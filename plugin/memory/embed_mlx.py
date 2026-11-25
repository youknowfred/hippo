"""HCR-2: the high profile's embedder — Qwen3-Embedding (4B default, 8B knob) through MLX.

The dense lane picks its model by id (``build_index._get_model``). Lean and floor never
leave ``DEFAULT_MODEL``. The high profile adds a second id, an *embedding space*: the
Qwen3 model id, suffixed ``@<dim>`` when its Matryoshka (MRL) output is truncated below
the native width, so a 1024-dim index and a 2560-dim index of the same model are never
cosine-compared (they are different ids everywhere ids are compared: the manifest's
``model``, the build's row reuse, recall's model cross-check, the floor table).

A space is ACTIVE only inside ``using_space`` (a ContextVar, carried into ``run_bounded``'s
worker thread by ``contextvars.copy_context``). Nothing outside the HOT-6 warm server's
high half (``recall_warm_high``) enters one, so the spawned hook, the CLI, SessionStart and
every lean/floor caller see ``active_space() is None`` and run exactly as before. This
module imports only the standard library at load time; MLX (and ``mlx_lm``, which brings
the Qwen3 architecture and the quantized loader) are imported by ``_MLXEmbedder`` alone.

Offline posture, OSP-4's: ``unavailable_reason`` answers from ``sys.platform``, a stat walk
of the pinned snapshot and ``importlib.util.find_spec`` — the cache is checked BEFORE
anything imports MLX, and nothing here ever downloads. ``fetch`` is the one download, and
only ``hippo setup --profile high --fetch --yes`` calls it.

Qwen3-Embedding is instruction-aware: a query is embedded as
``Instruct: <QUERY_INSTRUCTION>\\nQuery:<text>``; a document is embedded bare. Pooling is the
model's own: the hidden state of the final token (the tokenizer appends ``<|endoftext|>``).
Batches are right-padded, which is exact for a causal model — a real token never attends to
a later pad, and every row's positions start at 0 — so the last real token is gathered per
row with no attention mask. All MLX work runs on ONE worker thread the embedder owns (MLX
arrays and streams are not shared across threads); a document batch is fed to it in small
slices so a query waiting behind a background build waits one slice, not the whole batch.
"""

from __future__ import annotations

import concurrent.futures
import importlib.util
import json
import os
import platform
import sys
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Dict, Iterator, List, Optional, Tuple

# The one task instruction every high-profile query carries (Qwen3-Embedding's query
# format). HCR-3 calibrates the floor against THIS string — change it and the floor moves.
QUERY_INSTRUCTION = (
    "Given a developer's message to a coding agent, retrieve the project memories "
    "(decisions, conventions, facts and past fixes) that are relevant to it"
)

# id -> the MLX conversion served for it. mxfp8 (8-bit microscaling) is the roadmap's
# "8-bit" tier; the revision is pinned so the weights a build embedded with are the
# weights recall queries with (SEC-11: a moved branch never silently changes the space).
HIGH_MODELS: Dict[str, Dict] = {
    "Qwen/Qwen3-Embedding-4B": {
        "repo": "mlx-community/Qwen3-Embedding-4B-mxfp8",
        "revision": "c738b4ebab1e09812a7fe6800cf216c7b1ab2bfd",
        "native_dim": 2560,
        "download_bytes": 4_160_000_000,
    },
    "Qwen/Qwen3-Embedding-8B": {
        "repo": "mlx-community/Qwen3-Embedding-8B-mxfp8",
        "revision": "51c773b7464b630a6c67b4f75dbd796b658d6236",
        "native_dim": 4096,
        "download_bytes": 7_820_000_000,
    },
}
DEFAULT_HIGH_MODEL = "Qwen/Qwen3-Embedding-4B"
MODEL_ENV = "HIPPO_HIGH_EMBED_MODEL"
DIM_ENV = "HIPPO_HIGH_EMBED_DIM"
MODEL_PRESET_KEY = "high_embed_model"
DIM_PRESET_KEY = "high_embed_dim"
_MIN_DIM = 32  # Qwen3-Embedding's documented MRL floor
_MAX_TOKENS = 1024  # longest input embedded; hippo's docs and body chunks sit far below it
_SLICE_TOKENS = 4096  # padded tokens per forward pass: bounds a waiting query's wait

_ACTIVE: "ContextVar[Optional[str]]" = ContextVar("hippo_embed_space", default=None)


# --------------------------------------------------------------------------- #
# Which space: model knob, MRL knob, the active space
# --------------------------------------------------------------------------- #
def _preset() -> Dict:
    from .retrieval_profile import _read_preset  # the shared model.json, never raises

    return _read_preset()


def resolve_high_model() -> str:
    """env > preset > 4B. An id outside ``HIGH_MODELS`` falls through (doctor names it)."""
    env = (os.environ.get(MODEL_ENV) or "").strip()
    if env in HIGH_MODELS:
        return env
    raw = _preset().get(MODEL_PRESET_KEY)
    if isinstance(raw, str) and raw.strip() in HIGH_MODELS:
        return raw.strip()
    return DEFAULT_HIGH_MODEL


def resolve_dim(model: str) -> int:
    """The MRL output width: env > preset > the model's native width (the default until
    HCR-6's bench settles it). Outside ``[32, native]`` or not an int falls through."""
    native = HIGH_MODELS[model]["native_dim"]
    for raw in (os.environ.get(DIM_ENV), _preset().get(DIM_PRESET_KEY)):
        try:
            dim = int(str(raw).strip()) if raw is not None and str(raw).strip() else None
        except ValueError:
            dim = None
        if dim is not None and _MIN_DIM <= dim <= native:
            return dim
    return native


def space_id(model: str, dim: Optional[int] = None) -> str:
    """``model``, or ``model@dim`` for an MRL-truncated space."""
    if dim is None or dim == HIGH_MODELS[model]["native_dim"]:
        return model
    return f"{model}@{dim}"


def parse_space(space: Optional[str]) -> Optional[Tuple[str, int]]:
    """``(model, dim)`` for a high space id, else None (a lean id, junk, None)."""
    if not isinstance(space, str):
        return None
    model, _, dim_s = space.partition("@")
    spec = HIGH_MODELS.get(model)
    if spec is None:
        return None
    if not dim_s:
        return model, spec["native_dim"]
    try:
        dim = int(dim_s)
    except ValueError:
        return None
    return (model, dim) if _MIN_DIM <= dim < spec["native_dim"] else None


def resolve_high_space() -> str:
    model = resolve_high_model()
    return space_id(model, resolve_dim(model))


def active_space() -> Optional[str]:
    """The high space this context embeds in, or None (every lean/floor/spawn caller)."""
    return _ACTIVE.get()


@contextmanager
def using_space(space: str) -> Iterator[None]:
    if parse_space(space) is None:
        raise ValueError(f"not a high embedding space: {space!r}")
    token = _ACTIVE.set(space)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def manifest_fields(space: str) -> Dict:
    """Extra manifest keys a build in ``space`` records; ``{}`` for a lean id, so a lean
    manifest is byte-identical to one written before this module existed."""
    parsed = parse_space(space)
    if parsed is None:
        return {}
    model, dim = parsed
    spec = HIGH_MODELS[model]
    return {
        "embed_backend": "mlx",
        "embed_weights": f"{spec['repo']}@{spec['revision'][:12]}",
        "native_dim": spec["native_dim"],
        "mrl_dim": dim,
    }


# --------------------------------------------------------------------------- #
# Offline posture: where the weights live, whether they (and MLX) are here
# --------------------------------------------------------------------------- #
def cache_dir() -> str:
    """``$CLAUDE_PLUGIN_DATA/mlx`` (update-surviving), else ``<platform cache>/mlx`` —
    the fastembed cache's sibling, never under ``$TMPDIR``."""
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        return os.path.join(plugin_data, "mlx")
    from .build_index import platform_cache_dir

    return os.path.join(platform_cache_dir(), "mlx")


def snapshot_dir(model: str, root: Optional[str] = None) -> str:
    spec = HIGH_MODELS[model]
    repo_dir = "models--" + spec["repo"].replace("/", "--")
    return os.path.join(root or cache_dir(), repo_dir, "snapshots", spec["revision"])


def model_cached(model: str, root: Optional[str] = None) -> bool:
    """Pure stat (plus one small JSON read): the pinned snapshot holds a config, a
    tokenizer and every weight shard its index names. A partial download is not cached."""
    snap = snapshot_dir(model, root)
    if not (os.path.isfile(os.path.join(snap, "config.json"))
            and os.path.isfile(os.path.join(snap, "tokenizer.json"))):
        return False
    try:
        with open(os.path.join(snap, "model.safetensors.index.json"), encoding="utf-8") as fh:
            shards = set((json.load(fh).get("weight_map") or {}).values())
    except Exception:
        shards = set()
    if shards:
        return all(os.path.isfile(os.path.join(snap, s)) for s in shards)
    try:
        return any(n.endswith(".safetensors") and os.path.isfile(os.path.join(snap, n))
                   for n in os.listdir(snap))
    except OSError:
        return False


def apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def mlx_installed() -> bool:
    """Both runtime packages are findable — ``find_spec`` locates, it never imports."""
    try:
        return all(importlib.util.find_spec(m) is not None for m in ("mlx", "mlx_lm"))
    except (ImportError, ValueError):
        return False


INSTALL_HINT = "pip install -r plugin/requirements-high.txt into hippo's venv"


def unavailable_reason(model: str) -> Optional[str]:
    """Why ``model`` cannot be served on this machine right now, or None. Platform, then
    the stat-checked cache, then a find_spec — never an import, never a download."""
    if not apple_silicon():
        return "not an Apple Silicon Mac (the high profile's embedder runs on MLX)"
    if not model_cached(model):
        size = HIGH_MODELS[model]["download_bytes"] / 1e9
        return (f"{model} weights not downloaded (~{size:.1f} GB) — "
                f"`hippo setup --profile high --fetch --yes`")
    if not mlx_installed():
        return f"MLX is not installed ({INSTALL_HINT})"
    return None


def fetch(model: str) -> str:
    """Download ``model``'s pinned MLX weights into ``cache_dir()``. Setup only."""
    from huggingface_hub import snapshot_download

    spec = HIGH_MODELS[model]
    return snapshot_download(spec["repo"], revision=spec["revision"], cache_dir=cache_dir())


# --------------------------------------------------------------------------- #
# The embedder
# --------------------------------------------------------------------------- #
def query_text(text: str) -> str:
    return f"Instruct: {QUERY_INSTRUCTION}\nQuery:{text}"


class _MLXEmbedder:
    """fastembed's ``TextEmbedding`` shape — ``embed`` and ``query_embed`` yield one
    float32 vector per text — so ``build_index.embed_documents``/``embed_query`` serve
    both backends unchanged. Vectors come back MRL-truncated; build_index normalizes."""

    def __init__(self, model: str, dim: int, snap: str) -> None:
        self.model_id, self.dim = model, dim
        self._pool = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="hippo-mlx")
        t0 = time.monotonic()
        self._pool.submit(self._load, snap).result()
        self.load_ms = (time.monotonic() - t0) * 1000.0

    def _load(self, snap: str) -> None:
        from pathlib import Path

        import mlx.core as mx
        from mlx_lm.utils import load_model
        from tokenizers import Tokenizer

        self._mx = mx
        self._net, _cfg = load_model(Path(snap))
        self._net.eval()
        mx.eval(self._net.parameters())  # resident now, not on the first query
        active = getattr(mx, "get_active_memory", None) or mx.metal.get_active_memory
        self.resident_bytes = int(active())  # the weights' unified-memory footprint
        self._tok = Tokenizer.from_file(os.path.join(snap, "tokenizer.json"))
        self._pad = self._tok.token_to_id("<|endoftext|>") or 0
        self._eos = self._pad

    def _ids(self, texts: List[str]) -> List[List[int]]:
        out = []
        for enc in self._tok.encode_batch(texts):
            ids = list(enc.ids)
            if len(ids) > _MAX_TOKENS:  # keep the pooled final token
                ids = ids[: _MAX_TOKENS - 1] + [self._eos]
            out.append(ids)
        return out

    def _forward(self, batch: List[List[int]]):
        import numpy as np

        mx = self._mx
        width = max(len(ids) for ids in batch)
        grid = mx.array([ids + [self._pad] * (width - len(ids)) for ids in batch])
        hidden = self._net.model(grid)  # final-norm hidden states, [B, L, D]
        last = mx.array([len(ids) - 1 for ids in batch])
        pooled = hidden[mx.arange(len(batch)), last][:, : self.dim].astype(mx.float32)
        mx.eval(pooled)
        return np.array(pooled)

    def _encode(self, texts: List[str]):
        import numpy as np

        ids = self._ids(texts)
        order = sorted(range(len(ids)), key=lambda i: len(ids[i]))
        out: List = [None] * len(ids)
        start = 0
        while start < len(order):
            end = start + 1
            while end < len(order) and len(ids[order[end]]) * (end - start + 1) <= _SLICE_TOKENS:
                end += 1
            part = order[start:end]
            vecs = self._pool.submit(self._forward, [ids[i] for i in part]).result()
            for pos, i in enumerate(part):
                out[i] = vecs[pos]
            start = end
        return np.stack(out) if out else np.zeros((0, self.dim), dtype="float32")

    def embed(self, texts: List[str]):
        return iter(self._encode(list(texts)))

    def query_embed(self, texts: List[str]):
        return iter(self._encode([query_text(t) for t in texts]))


# Tests replace this with a deterministic fake: (model, dim, snapshot_dir) -> embedder.
BACKEND: Callable[[str, int, str], object] = _MLXEmbedder
_LOADED: Dict[str, object] = {}
_LOAD_LOCK = threading.Lock()


def high_embedder(space: str):
    """The loaded embedder for ``space`` — offline only; raises ``DenseUnavailable`` (a
    recall_fallback token) when this machine cannot serve it, never downloads."""
    from .build_index import DenseUnavailable

    parsed = parse_space(space)
    if parsed is None:
        raise DenseUnavailable(f"unknown embedding space {space!r}", "model_mismatch")
    with _LOAD_LOCK:
        hit = _LOADED.get(space)
        if hit is not None:
            return hit
        model, dim = parsed
        reason = unavailable_reason(model)
        if reason:
            raise DenseUnavailable(reason, "model_not_warmed")
        emb = BACKEND(model, dim, snapshot_dir(model))
        _LOADED[space] = emb
        return emb


def loaded(space: str):
    """The embedder already in memory for ``space``, or None — never loads."""
    return _LOADED.get(space)
