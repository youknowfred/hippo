"""HCR-4: the high profile's reranker — Qwen3-Reranker through MLX, behind the one rerank seam.

``recall_rank._cross_encoder_rerank`` is the only function that reorders hits by a joint
query/document read. It has two backends: fastembed's 2022 ms-marco MiniLM (every profile,
today's behavior) and, when a reranker is ACTIVE in the calling context, the Qwen3 model
named here. A reranker is active only inside ``using_reranker`` (a ContextVar, carried into
``run_bounded``'s worker by ``contextvars.copy_context``), and only two callers enter one:

* **The warm server's served call** (``recall_warm_high.serving``), on the high profile,
  once ``WARM_MODEL`` (Qwen3-Reranker-0.6B) has finished loading in the background. There
  ``HIPPO_RERANK`` defaults ON (``default_on``; an explicit falsy value still turns it off),
  and the call is bounded by ``WARM_TIMEOUT_SECS`` so a slow rerank degrades to the fused
  order well inside the warm server's serve budget.
* **The explicit surfaces** (``recall_view.describe``: ``/hippo:recall`` and the MCP
  ``recall`` tool), on the high profile, when ``EXPLICIT_MODEL`` (Qwen3-Reranker-4B) is
  servable here. Nothing turns rerank on there that was not already on (the explicit
  surface always reranked); only the model changes. The first call in a process loads it,
  inside ``EXPLICIT_TIMEOUT_SECS``.

The spawned hook enters neither, and lean and floor never resolve a model, so on every
path but those two ``active_reranker()`` is None and the seam runs exactly as before.

Offline posture, HCR-2's: ``unavailable_reason`` answers from the platform, a stat of the
pinned snapshot and ``find_spec`` — never an import of MLX, never a download. ``fetch`` is
the one download; only ``hippo setup --profile high --fetch --yes`` calls it.

Scoring is the model card's, exactly: a causal LM reads
``<prefix><Instruct>: …\\n<Query>: …\\n<Document>: …<suffix>`` and the score is
``P(yes)`` from a softmax over the final position's ``yes``/``no`` logits. Rows are
right-padded and the last real position gathered per row (exact for a causal model, as in
``embed_mlx``), so only those rows go through the tied output head. All MLX work runs on
``embed_mlx.mlx_executor()``, the process's one MLX thread.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Dict, Iterator, List, Optional

# id -> its pinned MLX conversion (mlx-community's mxfp8, the embedder's tier). Revisions
# are pinned so the weights scored with are the weights reviewed (SEC-11).
RERANK_MODELS: Dict[str, Dict] = {
    "Qwen/Qwen3-Reranker-0.6B": {
        "repo": "mlx-community/Qwen3-Reranker-0.6B-mxfp8",
        "revision": "a82f9f1c5dcb88c1156492f4dafb22366c1178da",
        "download_bytes": 628_763_002,
    },
    "Qwen/Qwen3-Reranker-4B": {
        "repo": "mlx-community/Qwen3-Reranker-4B-mxfp8",
        "revision": "25f203a237b822a90f38763843562b93a5baf82f",
        "download_bytes": 4_161_975_208,
    },
}
WARM_MODEL = "Qwen/Qwen3-Reranker-0.6B"  # the warm hot path: default ON there
EXPLICIT_MODEL = "Qwen/Qwen3-Reranker-4B"  # /hippo:recall and the MCP recall tool

# The warm path's own bound. The warm server's breaker trips one served call over 1500 ms
# (recall_warm.SERVE_BUDGET_MS); the rest of a served call measured ~114 ms median, so a
# rerank that has not answered in 800 ms degrades to the fused order and the call still
# lands inside the budget. HIPPO_RERANK_TIMEOUT, when set, overrides it (as it does the
# shared 5 s bound everywhere else).
WARM_TIMEOUT_SECS = 0.8
# The explicit 4B's bound. Measured on the owner's 128 GB Mac: load ~3.5 s, then ~3.3 s to
# score a /hippo:recall's hits, so the shared 5 s bound abandoned every first call. An
# abandoned scoring keeps the process's one MLX thread busy (run_bounded cannot cancel it),
# and the next warm prompt's query embed waited up to ~1.1 s behind it. The explicit
# surface has no per-prompt budget to protect, so it waits for the answer instead.
EXPLICIT_TIMEOUT_SECS = 15.0

# The task line every pair carries (the model card's <Instruct>). Kept beside, not shared
# with, embed_mlx.QUERY_INSTRUCTION: HCR-3's floor is calibrated on that one.
RERANK_INSTRUCTION = (
    "Given a developer's message to a coding agent, retrieve the project memories "
    "(decisions, conventions, facts and past fixes) that are relevant to it"
)
PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based on the "
    'Query and the Instruct provided. Note that the answer can only be "yes" or "no".'
    "<|im_end|>\n<|im_start|>user\n"
)
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
_MAX_TOKENS = 1024  # one pair, prefix and suffix included; descriptions sit far below it
_SLICE_TOKENS = 2048  # padded tokens per forward pass

_ACTIVE: "ContextVar[Optional[str]]" = ContextVar("hippo_rerank_model", default=None)


def pair_text(query: str, doc: str) -> str:
    return f"<Instruct>: {RERANK_INSTRUCTION}\n<Query>: {query}\n<Document>: {doc}"


# --------------------------------------------------------------------------- #
# Which reranker, where
# --------------------------------------------------------------------------- #
def active_reranker() -> Optional[str]:
    """The Qwen3 reranker this context scores with, or None (the fastembed MiniLM)."""
    return _ACTIVE.get()


def default_on() -> bool:
    """``HIPPO_RERANK`` unset means ON only inside the warm server's high-profile call."""
    return _ACTIVE.get() == WARM_MODEL


@contextmanager
def using_reranker(model: Optional[str]) -> Iterator[None]:
    """Score with ``model`` inside the block; ``None`` is a no-op (today's behavior)."""
    if model is None:
        yield
        return
    if model not in RERANK_MODELS:
        raise ValueError(f"not a high reranker: {model!r}")
    token = _ACTIVE.set(model)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def explicit_model() -> Optional[str]:
    """The explicit surfaces' reranker: ``EXPLICIT_MODEL`` on the high profile when this
    machine can serve it, else None (the MiniLM, exactly as before). Stat checks only."""
    try:
        from .retrieval_profile import resolve_retrieval_profile

        if resolve_retrieval_profile() != "high":
            return None
        return None if unavailable_reason(EXPLICIT_MODEL) else EXPLICIT_MODEL
    except Exception:
        return None


def bound_secs(model: str, shared: float) -> float:
    """The run_bounded bound for ``model``: ``HIPPO_RERANK_TIMEOUT`` when set, else the
    model's own bound (the warm model's tight one, the explicit model's generous one)."""
    if (os.environ.get("HIPPO_RERANK_TIMEOUT") or "").strip():
        return shared
    return WARM_TIMEOUT_SECS if model == WARM_MODEL else EXPLICIT_TIMEOUT_SECS


# --------------------------------------------------------------------------- #
# Offline posture
# --------------------------------------------------------------------------- #
def snapshot_dir(model: str, root: Optional[str] = None) -> str:
    from .embed_mlx import spec_snapshot_dir

    return spec_snapshot_dir(RERANK_MODELS[model], root)


def model_cached(model: str, root: Optional[str] = None) -> bool:
    from .embed_mlx import snapshot_complete

    return snapshot_complete(snapshot_dir(model, root))


def unavailable_reason(model: str) -> Optional[str]:
    """Why ``model`` cannot score here, or None — platform, cache stat, find_spec, and a
    load that already failed in this process. Never an import, never a download."""
    from . import embed_mlx as E

    if not E.apple_silicon():
        return "not an Apple Silicon Mac (the high profile's reranker runs on MLX)"
    if not model_cached(model):
        size = RERANK_MODELS[model]["download_bytes"] / 1e9
        return (f"{model} weights not downloaded (~{size:.1f} GB) — "
                f"`hippo setup --profile high --fetch --yes`")
    if not E.mlx_installed():
        return f"MLX is not installed ({E.INSTALL_HINT})"
    failed = _STATUS.get(model, {})
    if failed.get("status") == "failed":
        return failed.get("reason") or "model load failed"
    return None


def fetch(model: str) -> str:
    """Download ``model``'s pinned MLX weights. Setup only."""
    from .embed_mlx import fetch_spec

    return fetch_spec(RERANK_MODELS[model])


# --------------------------------------------------------------------------- #
# The reranker
# --------------------------------------------------------------------------- #
class _MLXReranker:
    """``score(query, docs)`` -> one ``P(yes)`` in [0, 1] per doc, in input order."""

    def __init__(self, model: str, snap: str) -> None:
        from .embed_mlx import mlx_executor

        self.model_id = model
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
        active = getattr(mx, "get_active_memory", None) or mx.metal.get_active_memory
        base = int(active())
        self._net, _cfg = load_model(Path(snap))
        self._net.eval()
        mx.eval(self._net.parameters())
        self.resident_bytes = int(active()) - base
        self._tok = Tokenizer.from_file(os.path.join(snap, "tokenizer.json"))
        self._yes, self._no = self._tok.token_to_id("yes"), self._tok.token_to_id("no")
        if self._yes is None or self._no is None:
            raise RuntimeError("the reranker's tokenizer has no 'yes'/'no' tokens")
        self._pad = self._tok.token_to_id("<|endoftext|>") or 0
        self._prefix = self._tok.encode(PREFIX, add_special_tokens=False).ids
        self._suffix = self._tok.encode(SUFFIX, add_special_tokens=False).ids

    def _ids(self, query: str, docs: List[str]) -> List[List[int]]:
        room = max(1, _MAX_TOKENS - len(self._prefix) - len(self._suffix))
        encs = self._tok.encode_batch([pair_text(query, d) for d in docs], add_special_tokens=False)
        return [self._prefix + list(e.ids)[:room] + self._suffix for e in encs]

    def _head(self, h):
        net = self._net
        if getattr(getattr(net, "args", None), "tie_word_embeddings", True) or not hasattr(net, "lm_head"):
            return net.model.embed_tokens.as_linear(h)
        return net.lm_head(h)

    def _forward(self, batch: List[List[int]]) -> List[float]:
        mx = self._mx
        width = max(len(ids) for ids in batch)
        grid = mx.array([ids + [self._pad] * (width - len(ids)) for ids in batch])
        hidden = self._net.model(grid)
        last = hidden[mx.arange(len(batch)), mx.array([len(ids) - 1 for ids in batch])]
        logits = self._head(last).astype(mx.float32)
        pair = mx.stack([logits[:, self._no], logits[:, self._yes]], axis=1)
        p_yes = mx.softmax(pair, axis=1)[:, 1]
        mx.eval(p_yes)
        return [float(x) for x in p_yes.tolist()]

    def score(self, query: str, docs: List[str]) -> List[float]:
        ids = self._ids(query, list(docs))
        order = sorted(range(len(ids)), key=lambda i: len(ids[i]))
        out: List[float] = [0.0] * len(ids)
        start = 0
        while start < len(order):
            end = start + 1
            while end < len(order) and len(ids[order[end]]) * (end - start + 1) <= _SLICE_TOKENS:
                end += 1
            part = order[start:end]
            got = self._pool.submit(self._forward, [ids[i] for i in part]).result()
            for pos, i in enumerate(part):
                out[i] = got[pos]
            start = end
        return out


# Tests replace this with a deterministic fake: (model, snapshot_dir) -> reranker.
BACKEND: Callable[[str, str], object] = _MLXReranker
_LOADED: Dict[str, object] = {}
_STATUS: Dict[str, Dict] = {}  # model -> {"status": loading|ready|failed, "reason", ...}
_LOAD_LOCK = threading.Lock()


class RerankUnavailable(RuntimeError):
    """The active reranker cannot score right now; the seam keeps the fused order."""


def loaded(model: str):
    """The reranker already in memory, or None — never loads."""
    return _LOADED.get(model)


def reranker(model: str):
    """The loaded reranker for ``model``, loading it from the pinned snapshot if needed —
    offline only. Raises ``RerankUnavailable``; a failed load is remembered, so later
    calls in this process fall back to the MiniLM instead of retrying a broken load."""
    hit = _LOADED.get(model)
    if hit is not None:
        return hit
    with _LOAD_LOCK:
        hit = _LOADED.get(model)
        if hit is not None:
            return hit
        reason = unavailable_reason(model)
        if reason:
            raise RerankUnavailable(reason)
        _STATUS[model] = {"status": "loading", "reason": ""}
        try:
            rr = BACKEND(model, snapshot_dir(model))
        except Exception as exc:
            _STATUS[model] = {"status": "failed", "reason": f"model load failed: {exc}"}
            raise RerankUnavailable(str(exc)) from exc
        _LOADED[model] = rr
        _STATUS[model] = {
            "status": "ready",
            "reason": "",
            "load_ms": getattr(rr, "load_ms", None),
            "resident_bytes": getattr(rr, "resident_bytes", None),
        }
        return rr


def start_loading(model: str) -> None:
    """Load ``model`` on a daemon thread, once per process (the warm server's path: the
    cold load never lands on a served call). Unservable here: recorded, nothing started."""
    with _LOAD_LOCK:
        if model in _LOADED or model in _STATUS:
            return
        reason = unavailable_reason(model)
        if reason:
            _STATUS[model] = {"status": "unavailable", "reason": reason}
            return
        _STATUS[model] = {"status": "loading", "reason": ""}

    def _bg() -> None:
        try:
            reranker(model)
        except Exception:
            pass  # recorded in _STATUS; serving keeps today's behavior

    threading.Thread(target=_bg, name="hippo-rerank", daemon=True).start()


def status(model: str) -> Dict:
    """``{}`` until something asked for ``model`` in this process."""
    rec = dict(_STATUS.get(model) or {})
    if rec:
        rec["model"] = model
    return rec


_BUSY: Dict[str, threading.Lock] = {m: threading.Lock() for m in RERANK_MODELS}


def score(model: str, query: str, docs: List[str]) -> List[float]:
    """Scores from ``model`` — the seam's MLX call, run inside ``run_bounded``. The warm
    model scores only once loaded (its load is the background thread's); the explicit
    model loads here on first use.

    One scoring per model at a time, and a call that finds one still running fails at
    once (the seam keeps the fused order). ``run_bounded`` cannot cancel a scoring that
    outlived its bound: it keeps the one MLX thread busy, and without this every later
    call would queue behind it, time out in turn, and leave work behind for the next
    (measured on the 4B: every explicit call pinned at its 5 s bound)."""
    busy = _BUSY[model]
    if not busy.acquire(blocking=False):
        raise RerankUnavailable(f"{model} is still scoring an earlier call")
    try:
        rr = loaded(model) if model == WARM_MODEL else reranker(model)
        if rr is None:
            raise RerankUnavailable(f"{model} is still loading")
        scores = list(rr.score(query, docs))
    finally:
        busy.release()
    if len(scores) != len(docs):
        raise RerankUnavailable("the reranker returned the wrong number of scores")
    return scores


def _reset_for_tests() -> None:
    _LOADED.clear()
    _STATUS.clear()
    _BUSY.update({m: threading.Lock() for m in RERANK_MODELS})  # a leftover scoring keeps its old one
