"""HCR-2: the high profile's warm half — the one process that ever loads the big embedder.

How the two paths coexist (the roadmap's warm-first rule, made concrete):

* **The spawned hook never changes.** It runs in a fresh interpreter per prompt, sees no
  active embedding space, and serves the LEAN index at ``<index>`` with lean's model — on
  every profile. It cannot afford a multi-GB load, so it never attempts one.
* **The high index is a sibling, not a replacement.** ``<index>/high/`` is a complete index
  built by the same ``build_index`` inside ``embed_mlx.using_space``, so its manifest names
  the Qwen3 space (and records the MRL width and pinned weights) and IDX-1 binds its own
  matrix. The lean build never looks inside ``high/`` (``retire_dense`` matches only its
  own top-level files), and the high build never writes outside it: neither can tear the
  other. Concurrent sessions building the same ``high/`` are IDX-1's case, already safe.
* **Only this server builds or reads it.** On the first warm call of a high-profile
  session the server starts one daemon thread that loads the model (off the serving path,
  so the cold load never counts against ``COLD_BUDGET_MS``) and then keeps every recall
  tier's ``high/`` index caught up with its lean twin — offline (the high embedder never
  downloads) and incremental (rows reused by hash). Until the model is loaded and EVERY tier's high index
  holds exactly the lean index's entries and chunks (same hashes, dense-ready, same space),
  warm calls serve lean like any other warm call. The switch is all-or-nothing per call
  because ``recall_tiers`` drops dense entirely when merged tiers disagree on the model.
* **A served call on high** swaps only the ``LoadedIndex`` each tier resolves to (through
  ``INDEX_LOADER``, the warm path's existing seam) and enters the high space so the query
  embeds with the same model. Every side file — links, rules, sidecars, digest sections —
  is still read from the lean index dir. Digest sections are embedded in lean's space, so
  ``recall_sections`` skips them on a high call (its existing model guard) until a later
  item gives them a high twin.
* **Nothing degrades loudly.** Not Apple Silicon, MLX absent, weights not fetched, or a
  failed load: the status records why (the heartbeat carries it; doctor reports it) and the
  server keeps serving lean. Recall never downloads.

Each session's MCP server is its own process, so each loads its own copy of the model.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional, Tuple

HIGH_SUBDIR = "high"
REFRESH_EVERY_S = 30.0  # idle re-check; a served call on a changed lean index wakes it now
_MAX_PASSES = 8  # each pass embeds every missing row; more passes only chase a moving lean index


def high_index_dir(index_dir: str) -> str:
    return os.path.join(index_dir, HIGH_SUBDIR)


class _State:
    def __init__(self) -> None:
        self.rerank: Optional[str] = None  # HCR-4: the warm reranker, on the high profile
        self.status = "off"  # off | unavailable | loading | ready | failed
        self.reason = ""
        self.space: Optional[str] = None
        self.load_ms: Optional[float] = None
        self.resident_bytes: Optional[int] = None
        self.tiers: List[Tuple[str, str]] = []
        self.engaged = False
        self.thread: Optional[threading.Thread] = None
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.fresh_memo: Dict[Tuple[int, int], tuple] = {}


_ST = _State()
_HIGH_CACHE = None  # a recall_warm._IndexCache over the high/ dirs, made on first use


def _high_cache():
    global _HIGH_CACHE
    if _HIGH_CACHE is None:
        from .recall_warm import _IndexCache

        _HIGH_CACHE = _IndexCache()
    return _HIGH_CACHE


def _tiers(memory_dir: str) -> List[Tuple[str, str]]:
    from .recall_tiers import _recall_tier_dirs

    return [(corpus, idx) for corpus, idx, _label in _recall_tier_dirs(memory_dir, None)]


def _hashes(li) -> Tuple[List[str], List[str]]:
    return [e.get("hash") for e in li.entries], [c.get("hash") for c in li.body_chunks]


def _rows_done(li, space: str) -> int:
    """Rows ``li`` already holds in ``space`` (-1: no index, or another space's)."""
    if li is None or li.model != space:
        return -1
    return sum(x.get("row") is not None for x in li.entries + li.body_chunks)


def fresh(lean, high, space: str) -> bool:
    """``high`` serves in ``lean``'s place: dense-ready in ``space`` over the same entries
    and body chunks, in the same order (memoized per loaded pair)."""
    if lean is None or high is None:
        return False
    key = (id(lean), id(high))
    memo = _ST.fresh_memo.get(key)  # holds both objects, so their ids cannot be reused
    if memo is not None and memo[0] is lean and memo[1] is high and memo[2] == space:
        return memo[3]
    hit = bool(high.dense_ready and high.dense is not None and high.model == space
               and _hashes(high) == _hashes(lean))
    if len(_ST.fresh_memo) > 64:
        _ST.fresh_memo.clear()
    _ST.fresh_memo[key] = (lean, high, space, hit)
    return hit


def status() -> Dict:
    """The heartbeat's ``high`` record; ``{}`` when the profile is not high."""
    if _ST.rerank is None and _ST.status == "off":
        return {}
    out = {
        "status": _ST.status,
        "space": _ST.space,
        "reason": _ST.reason,
        "engaged": _ST.engaged,
        "load_ms": round(_ST.load_ms, 1) if _ST.load_ms is not None else None,
        "resident_bytes": _ST.resident_bytes,
    }
    if _ST.rerank is not None:
        from .rerank_mlx import status as rerank_status

        rec = rerank_status(_ST.rerank)
        if rec.get("load_ms") is not None:
            rec["load_ms"] = round(rec["load_ms"], 1)
        out["rerank"] = rec
    return out


def pin_blas_threads(environ=None) -> bool:
    """HCR-2's measured follow-up, folded into HCR-4: on the high profile, one OpenBLAS
    thread for this (the warm) server. OpenBLAS's thread pool beside Metal took warm recall
    from a 114 ms to a 309-464 ms median; a reranker adds Metal work to the same budget.
    OpenBLAS reads the variable when numpy loads, so ``mcp_server.main`` calls this first.
    An explicit value (any) is left alone; lean and floor are left alone. True if pinned."""
    env = os.environ if environ is None else environ
    if env.get("OPENBLAS_NUM_THREADS") is not None:
        return False
    try:
        from .retrieval_profile import resolve_retrieval_profile

        if resolve_retrieval_profile() != "high":
            return False
    except Exception:
        return False
    env["OPENBLAS_NUM_THREADS"] = "1"
    return True


def _kick_rerank() -> None:
    """High profile with ``HIPPO_RERANK`` on: the warm reranker loads once per process, in
    the background. Off (the default), nothing loads, so the 0.6 GB and its load stay unpaid."""
    from .recall_rank import _rerank_enabled

    if not _rerank_enabled():
        _ST.rerank = None
        return
    from .rerank_mlx import WARM_MODEL, start_loading

    _ST.rerank = WARM_MODEL
    start_loading(WARM_MODEL)


def kick(memory_dir) -> None:
    """Every served call: note the profile and tiers; start the loader thread once.
    ``memory_dir`` may be a zero-argument callable, resolved only on the high profile."""
    from .build_index import dense_disabled
    from .retrieval_profile import resolve_retrieval_profile

    if resolve_retrieval_profile() != "high":
        _ST.status, _ST.engaged, _ST.rerank = "off", False, None
        return
    _kick_rerank()  # independent of the embedder: a reranker reorders lean hits too
    if dense_disabled():
        _ST.status, _ST.engaged = "off", False
        return
    memory_dir = memory_dir() if callable(memory_dir) else memory_dir
    if not memory_dir:
        _ST.status, _ST.engaged = "off", False
        return
    from . import embed_mlx as E

    space = E.resolve_high_space()
    with _ST.lock:
        _ST.tiers = _tiers(memory_dir)
        if _ST.thread is not None and _ST.thread.is_alive() and _ST.space == space:
            _ST.wake.set()
            return
        if _ST.status == "failed" and _ST.space == space:
            return  # a load that failed stays failed for this process; doctor says why
        _ST.space = space
        reason = E.unavailable_reason(E.parse_space(space)[0])
        if reason:
            _ST.status, _ST.reason = "unavailable", reason
            return
        _ST.status, _ST.reason = "loading", ""
        _ST.thread = threading.Thread(target=_run, args=(space,), name="hippo-high", daemon=True)
        _ST.thread.start()


def _run(space: str) -> None:
    from . import embed_mlx as E

    try:
        emb = E.high_embedder(space)
        _ST.load_ms = getattr(emb, "load_ms", None)
        _ST.resident_bytes = getattr(emb, "resident_bytes", None)
        _ST.status = "ready"
    except Exception as exc:
        _ST.status, _ST.reason = "failed", f"model load failed: {exc}"
        return
    while _ST.space == space:
        for memory_dir, index_dir in list(_ST.tiers):
            try:
                refresh_tier(memory_dir, index_dir, space)
            except Exception:
                pass  # the next pass retries; serving stays lean meanwhile
        _ST.wake.wait(REFRESH_EVERY_S)
        _ST.wake.clear()


def refresh_tier(memory_dir: str, index_dir: str, space: str) -> bool:
    """Bring ``<index_dir>/high`` level with the lean index. True when it is fresh."""
    from .build_index import build_index, load_index
    from .embed_mlx import using_space

    hdir = high_index_dir(index_dir)
    for _ in range(_MAX_PASSES):
        lean, high = load_index(index_dir), load_index(hdir)
        if lean is None:
            return False  # nothing to mirror yet; the lean side builds it
        if fresh(lean, high, space):
            return True
        before = _rows_done(high, space)
        # Unbounded (``allow_download=True`` is build_index's one-batch online path): this
        # thread has no hook deadline, and the lean refresh's 15 s budget never fits a 64-doc
        # slice of a 4B model (measured: such a build never converged). The high embedder
        # itself never downloads, whatever this flag says, and feeds its worker in short
        # slices, so a served call is never queued behind the whole batch.
        with using_space(space):
            build_index(memory_dir, hdir, allow_download=True)
        after = load_index(hdir)
        if not fresh(lean, after, space) and _rows_done(after, space) <= before:
            return False  # no progress (the model is failing): the next wake retries
    return False


@contextmanager
def serving(memory_dir, lean_load) -> Iterator[None]:
    """Wrap one served call: on high, with the model loaded and every tier's high index
    fresh, each tier's index resolves to its high twin and the query embeds in the high
    space. Otherwise this is a no-op and the call serves lean, exactly as before."""
    plan: Optional[Dict[str, object]] = None
    try:
        kick(memory_dir)
        plan = _plan(lean_load)
    except Exception:
        plan = None
    _ST.engaged = bool(plan)
    with _warm_reranker():
        if not plan:
            yield
            return
        from .embed_mlx import using_space
        from .recall_tiers import INDEX_LOADER

        token = INDEX_LOADER.set(lambda d: plan.get(d) or lean_load(d))
        try:
            with using_space(_ST.space):
                yield
        finally:
            INDEX_LOADER.reset(token)


@contextmanager
def _warm_reranker() -> Iterator[None]:
    """HCR-4: with ``HIPPO_RERANK`` on and the warm reranker loaded, a served call scores
    with it. Otherwise — the default, before the load finishes, or off the high profile —
    the call runs exactly as before. Never raises."""
    model = None
    try:
        from .rerank_mlx import loaded

        if _ST.rerank is not None and loaded(_ST.rerank) is not None:
            model = _ST.rerank
    except Exception:
        model = None
    if model is None:
        yield
        return
    from .rerank_mlx import using_reranker

    with using_reranker(model):
        yield


def _plan(lean_load) -> Optional[Dict[str, object]]:
    from .embed_mlx import loaded

    space = _ST.space
    if _ST.status != "ready" or not space or loaded(space) is None or not _ST.tiers:
        return None
    plan: Dict[str, object] = {}
    for _memory_dir, index_dir in _ST.tiers:
        lean = lean_load(index_dir)
        if lean is None:
            continue  # recall skips this tier on either path
        high = _high_cache().load(high_index_dir(index_dir))
        if not fresh(lean, high, space):
            _ST.wake.set()  # lean moved on: catch the twin up now
            return None
        plan[index_dir] = high
    return plan or None


def _reset_for_tests() -> None:
    global _HIGH_CACHE
    from .rerank_mlx import _reset_for_tests as _reset_rerank

    _ST.space = None  # a running loop exits at its next wake
    _ST.wake.set()
    _ST.__init__()
    _HIGH_CACHE = None
    _reset_rerank()


def doctor_meaning(lean: str, profile: str) -> Tuple[str, str]:
    """Doctor's retrieval-profile line for this machine: ``(meaning, degrade note)``. The
    note is non-empty when high is selected but recall is (or will be) serving lean."""
    if profile != "high":
        return lean, ""
    from . import embed_mlx as E

    space = E.resolve_high_space()
    why = E.unavailable_reason(E.parse_space(space)[0])
    if why:
        return f"{lean}; high model {space} is not servable here", f"High degrades to lean: {why}."
    meaning = f"dense {space} via MLX in the warm server (the spawned hook serves lean: {lean})"
    from .recall_warm import server_states

    live = [r["high"] for r in server_states() if r.get("alive") and isinstance(r.get("high"), dict)]
    if not live:
        return meaning, ("No warm server has loaded the high model yet — high serves only "
                         "through warm recall (`hippo setup --warm`); until then recall is lean.")
    h = live[0]
    if h.get("status") in ("failed", "unavailable"):
        return meaning, f"The warm server degraded to lean: {h.get('reason') or h.get('status')}."
    bits = [f"warm server {h.get('status')}",
            "serving high" if h.get("engaged") else "serving lean while the high index catches up"]
    if h.get("load_ms") is not None:
        bits.append(f"model load {h['load_ms'] / 1000:.1f} s")
    if h.get("resident_bytes"):
        bits.append(f"{h['resident_bytes'] / 2**30:.1f} GiB resident")
    rr = h.get("rerank") if isinstance(h.get("rerank"), dict) else {}
    if rr.get("model"):
        bits.append(f"reranker {rr['model']} {rr.get('status') or 'idle'}"
                    + (f" ({rr['reason']})" if rr.get("reason") else ""))
    return f"{meaning}; {', '.join(bits)}", ""
