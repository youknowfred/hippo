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
row with no attention mask. All MLX work runs on the process's ONE MLX worker thread
(``mlx_executor``; MLX arrays and streams are not shared across threads, and the rerankers
submit there too); a document batch is fed to it in small
slices so a query waiting behind a background build waits one slice, not the whole batch.
"""

from __future__ import annotations

import concurrent.futures
import importlib.util
import json
import os
import platform
import re
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
# Longest input embedded. Body chunks sit far below it; a whole-memory row (HCR-9) is cut here,
# so a long memory is embedded by its first ~1,024 tokens (what HCR-8 measured).
_MAX_TOKENS = 1024
# Padded tokens per forward pass: bounds a waiting query's wait. Measured on the 4B (M-series,
# 128 GB): ~1,350 tok/s up to ~1.2k tokens a pass, falling to ~600 tok/s at ~4k.
_SLICE_TOKENS = 1024

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


class EmbedAbandoned(RuntimeError):
    """A queued embed dropped because its caller had already stopped waiting."""


_GIVEN_UP: ContextVar[Optional[threading.Event]] = ContextVar("hippo_embed_given_up", default=None)


@contextmanager
def abandon_on_timeout() -> Iterator[None]:
    """Wrap a bounded embed call. ``run_bounded`` can make its caller stop waiting but
    cannot cancel the work, so an embed still queued for the MLX thread would run anyway
    and the next prompt's query would wait behind it. Leaving this block by an exception
    (the bound's ``DenseTimeout``) marks every embed started inside it as abandoned; the
    embedder drops the parts that have not begun. One already running finishes. Outside a
    high space nothing reads the mark."""
    mark = threading.Event()
    token = _GIVEN_UP.set(mark)
    try:
        yield
    except BaseException:
        mark.set()
        raise
    finally:
        _GIVEN_UP.reset(token)


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


def spec_snapshot_dir(spec: Dict, root: Optional[str] = None) -> str:
    """The HF-hub snapshot dir of a ``{"repo", "revision"}`` spec under the MLX cache —
    shared with ``rerank_mlx``, whose pinned weights sit beside the embedder's."""
    repo_dir = "models--" + spec["repo"].replace("/", "--")
    return os.path.join(root or cache_dir(), repo_dir, "snapshots", spec["revision"])


def snapshot_dir(model: str, root: Optional[str] = None) -> str:
    return spec_snapshot_dir(HIGH_MODELS[model], root)


def model_cached(model: str, root: Optional[str] = None) -> bool:
    return snapshot_complete(snapshot_dir(model, root))


def snapshot_complete(snap: str) -> bool:
    """Pure stat: the pinned snapshot holds a config, a tokenizer and a complete set of
    weight files — an unsharded ``*.safetensors``, or every ``-NNNNN-of-MMMMM`` shard.
    The file names are the evidence, not ``model.safetensors.index.json``: the 4B mxfp8
    conversion ships a stale index naming two shards it does not have (mlx_lm globs the
    files too). A snapshot link appears only once its file finished downloading, so a
    partial download reads as not cached."""
    if not (os.path.isfile(os.path.join(snap, "config.json"))
            and os.path.isfile(os.path.join(snap, "tokenizer.json"))):
        return False
    try:
        weights = [n for n in os.listdir(snap)
                   if n.endswith(".safetensors") and os.path.isfile(os.path.join(snap, n))]
    except OSError:
        return False
    shards = [m for m in (_SHARD_RE.search(n) for n in weights) if m]
    if not shards:
        return bool(weights)
    want = {int(m.group(2)) for m in shards}
    return len(want) == 1 and {int(m.group(1)) for m in shards} == set(range(1, want.pop() + 1))


_SHARD_RE = re.compile(r"-(\d+)-of-(\d+)\.safetensors$")


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
    return fetch_spec(HIGH_MODELS[model])


def fetch_spec(spec: Dict) -> str:
    from huggingface_hub import snapshot_download

    return snapshot_download(spec["repo"], revision=spec["revision"], cache_dir=cache_dir())


# --------------------------------------------------------------------------- #
# MLX's buffer cache: capped
# --------------------------------------------------------------------------- #
# MLX keeps freed Metal buffers for reuse and, left alone, caps that cache near the whole
# machine (121.6 GB on a 128 GB Mac). A query reuses one shape, so the cache stays small; an
# index build runs slices of ever-changing shape and each one leaves its buffers behind.
# Measured 2026-11-26 on the 4B over a 1,000-memory corpus: 6.4 GB of cache after five slices
# and still climbing, in every warm server that builds. Capped at 256 MB or at 1 GB the
# build ran at the same speed (496 and 529 padded tok/s), a query still took 56-62 ms, and
# the process held 4.6 and 5.5 GB.
CACHE_ENV = "HIPPO_MLX_CACHE_MB"
DEFAULT_CACHE_MB = 512


def cache_limit_bytes() -> int:
    """The cap on MLX's buffer cache for this process: ``HIPPO_MLX_CACHE_MB`` when it is a
    whole number of megabytes (0 keeps no cache), else 512 MB."""
    raw = (os.environ.get(CACHE_ENV) or "").strip()
    try:
        mb = int(raw) if raw else DEFAULT_CACHE_MB
    except ValueError:
        mb = DEFAULT_CACHE_MB
    return (mb if mb >= 0 else DEFAULT_CACHE_MB) * 2**20


def cap_mlx_cache(mx) -> Optional[int]:
    """Cap MLX's buffer cache before a model loads; every loader in this process calls it
    (the embedder here, the rerankers in ``rerank_mlx``). Returns the cap in bytes, or None
    when this MLX has no such call — a load is never failed over the cap."""
    limit = cache_limit_bytes()
    try:
        setter = getattr(mx, "set_cache_limit", None) or mx.metal.set_cache_limit
        setter(limit)
    except Exception:
        return None
    return limit


# --------------------------------------------------------------------------- #
# The one MLX thread
# --------------------------------------------------------------------------- #
_POOL: Optional[concurrent.futures.ThreadPoolExecutor] = None
_POOL_LOCK = threading.Lock()


def mlx_executor() -> concurrent.futures.ThreadPoolExecutor:
    """The process's single MLX worker. MLX arrays and streams are not shared across
    threads, so every model this process serves through MLX — the embedder and the
    rerankers (``rerank_mlx``) — submits its work here, one task at a time."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="hippo-mlx")
        return _POOL


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
        self._pool = mlx_executor()
        t0 = time.monotonic()
        self._pool.submit(self._load, snap).result()
        self.load_ms = (time.monotonic() - t0) * 1000.0

    def _load(self, snap: str) -> None:
        from pathlib import Path

        import mlx.core as mx
        from mlx_lm.utils import load_model
        from tokenizers import Tokenizer

        self._mx = mx
        cap_mlx_cache(mx)  # before the first buffer: an index build must not grow the cache
        active = getattr(mx, "get_active_memory", None) or mx.metal.get_active_memory
        base = int(active())  # a reranker may already share this process's MLX memory
        # An embedder reads hidden states and never the LM head. The 8B conversion's config
        # says the head is untied yet ships no ``lm_head.weight`` (the 4B's is tied), so a
        # plain load refuses it: "Missing 1 parameters: lm_head.weight". Tied, mlx_lm builds
        # no head at all, which is also 2.5 GB the 8B never has to hold.
        self._net, _cfg = load_model(Path(snap), model_config={"tie_word_embeddings": True})
        self._net.eval()
        mx.eval(self._net.parameters())  # resident now, not on the first query
        self.resident_bytes = int(active()) - base  # the weights' unified-memory footprint
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

    def _slice(self, batch: List[List[int]], given_up: Optional[threading.Event]):
        """One queued forward pass, dropped unrun when its caller stopped waiting while it
        sat in the queue (behind a build slice or a model load): the worker goes straight
        to the next caller's query instead of first embedding one nobody will read."""
        if given_up is not None and given_up.is_set():
            raise EmbedAbandoned()
        return self._forward(batch)

    def _encode(self, texts: List[str]):
        import numpy as np

        given_up = _GIVEN_UP.get()
        ids = self._ids(texts)
        order = sorted(range(len(ids)), key=lambda i: len(ids[i]))
        out: List = [None] * len(ids)
        start = 0
        while start < len(order):
            end = start + 1
            while end < len(order) and len(ids[order[end]]) * (end - start + 1) <= _SLICE_TOKENS:
                end += 1
            part = order[start:end]
            vecs = self._pool.submit(self._slice, [ids[i] for i in part], given_up).result()
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
