"""HCR-6: the retrieval-profile A/B — lean against high (and anything between) in one run.

``ab_runner.run_flag_arms`` compares one env flag off and on over one index. A profile is
more than a flag: an embedding space with its own index, a reranker, how deep the reranker
reads, and whichever flags ride along. So this is the same instrument with the arm
generalized: an ``Arm`` names those four things, ``run_profile_ab`` runs the BASELINE arm,
every other arm, then the baseline again, and refuses to report unless the two baseline
runs are byte-identical (the rig leaked nothing, so each delta belongs to its arm).

Per arm, from ONE pass over the hard set:

* QUA-6's recall@k and MRR by category — judged by ``eval_metrics.hard_set_metrics``, the
  production judgment, through its ``ranked_source`` seam (no arm decides what a hit is);
* the injection cost: estimated tokens of the block ``format_results`` would inject;
* in-process recall latency (the models already loaded — what a warm server pays).

Each arm recalls the way the warm server serves it: the arm's own ``LoadedIndex`` over the
LEAN index dir, which is where links, rules and sidecars are read on every profile.

``pool`` is the one thing here production does not do yet: recall the top ``pool`` fused
hits, rerank those, then cut to ``k``. It measures whether a reranker reading past the
top-k cut recovers hits the cut dropped, before anyone builds that into recall.

MEASURES ONLY (ED-2): nothing here flips a default, writes a file or touches a gate. The
caller (``bench/profile_ab.py``) loads the models, builds the indexes and stamps the
machine facts (load time, resident memory, cold start).
"""

from __future__ import annotations

import json
import os
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple

from .ab_runner import LOW_N_FLOOR


@dataclass(frozen=True)
class Arm:
    name: str
    space: Optional[str] = None  # a high embedding space id; None is lean's model
    reranker: Optional[str] = None  # a rerank_mlx model id; None with rerank is the MiniLM
    rerank: bool = False
    pool: int = 0  # > k: rerank the top ``pool`` fused hits, then cut to k
    env: Tuple[Tuple[str, str], ...] = ()  # flags held for the arm, restored exactly


@contextmanager
def _env(pairs: Dict[str, Optional[str]]) -> Iterator[None]:
    prev = {name: os.environ.get(name) for name in pairs}
    try:
        for name, value in pairs.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield
    finally:
        for name, value in prev.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _summary(xs: List[float]) -> Dict[str, float]:
    xs = sorted(xs)
    if not xs:
        return {"n": 0}

    def at(q: float) -> float:
        return round(xs[min(len(xs) - 1, max(0, int(round(q * len(xs))) - 1))], 1)

    return {"n": len(xs), "p50": at(0.50), "p95": at(0.95), "max": round(xs[-1], 1)}


def run_arm(arm: Arm, *, memory_dir: str, lean_index_dir: str, index, hard_set: List[dict],
            k: int) -> dict:
    """One pass of ``hard_set`` through ``arm``. ``index`` is the arm's space's index."""
    from .eval_metrics import _estimate_tokens, hard_set_metrics_by_category
    from .recall import format_results, recall
    from .recall_rank import _cross_encoder_rerank

    millis: List[float] = []
    tokens: List[int] = []
    returned: List[int] = []
    deep = arm.pool > k

    def ranked(query: str, k_: int) -> List[dict]:
        t0 = time.perf_counter()
        rows = recall(query, k=arm.pool if deep else k_, index=index,
                      index_dir=lean_index_dir, memory_dir=memory_dir)
        if deep:
            rows = _cross_encoder_rerank(query, rows)
            corpus = [r for r in rows if r.get("corpus") != "rule"][:k_]
            rows = corpus + [r for r in rows if r.get("corpus") == "rule"]
        millis.append((time.perf_counter() - t0) * 1000.0)
        tokens.append(_estimate_tokens(format_results(rows)))
        returned.append(len(rows))
        return rows

    flags: Dict[str, Optional[str]] = {"HIPPO_RERANK": "1" if arm.rerank and not deep else "0"}
    flags.update(dict(arm.env))
    with ExitStack() as stack:
        stack.enter_context(_env(flags))
        if arm.space:
            from .embed_mlx import using_space

            stack.enter_context(using_space(arm.space))
        if arm.reranker:
            from .rerank_mlx import using_reranker

            stack.enter_context(using_reranker(arm.reranker))
        by_category = hard_set_metrics_by_category(index, hard_set, k=k, ranked_source=ranked)
    for cat in by_category.values():
        cat["hits"] = int(round(cat["recall"] * cat["n"]))
        cat["recall"], cat["mrr"] = round(cat["recall"], 4), round(cat["mrr"], 4)
    n = sum(c["n"] for c in by_category.values())
    return {
        "by_category": by_category,
        "overall": {
            "n": n,
            "hits": sum(c["hits"] for c in by_category.values()),
            "mrr": round(sum(c["mrr"] * c["n"] for c in by_category.values()) / n, 4) if n else 0.0,
        },
        "injected_tokens": {"mean": round(sum(tokens) / len(tokens), 1) if tokens else 0.0,
                            "max": max(tokens, default=0)},
        "rows_returned": {"mean": round(sum(returned) / len(returned), 2) if returned else 0.0},
        "latency_ms": _summary(millis),
    }


def _deterministic(result: dict) -> str:
    """What must repeat exactly between two runs of one arm (latency never does)."""
    view = {key: result[key] for key in ("by_category", "overall", "injected_tokens", "rows_returned")}
    return json.dumps(view, sort_keys=True, separators=(",", ":"))


def _delta(arm: dict, base: dict) -> dict:
    out: Dict[str, dict] = {}
    for cat in sorted(set(arm["by_category"]) | set(base["by_category"])):
        a, b = arm["by_category"].get(cat) or {}, base["by_category"].get(cat) or {}
        n = int(min(a.get("n") or 0, b.get("n") or 0) or max(a.get("n") or 0, b.get("n") or 0))
        out[cat] = {
            "hits": int((a.get("hits") or 0) - (b.get("hits") or 0)),
            "recall": round((a.get("recall") or 0.0) - (b.get("recall") or 0.0), 4),
            "mrr": round((a.get("mrr") or 0.0) - (b.get("mrr") or 0.0), 4),
            "n": n,
            **({"low_n": True} if n <= LOW_N_FLOOR else {}),
        }
    return {
        "by_category": out,
        "overall": {"hits": arm["overall"]["hits"] - base["overall"]["hits"],
                    "mrr": round(arm["overall"]["mrr"] - base["overall"]["mrr"], 4)},
        "injected_tokens_mean": round(arm["injected_tokens"]["mean"] - base["injected_tokens"]["mean"], 1),
        "latency_p50_ms": round(arm["latency_ms"].get("p50", 0.0) - base["latency_ms"].get("p50", 0.0), 1),
    }


def run_profile_ab(arms: List[Arm], *, memory_dir: str, lean_index_dir: str,
                   indexes: Dict[Optional[str], object], hard_set_path: str, k: int = 10) -> dict:
    """``arms[0]`` is the baseline: run first and last, and every other arm is reported as
    a delta against it. ``indexes`` maps each arm's ``space`` (``None`` for lean) to its
    ``LoadedIndex``. ``{"ok": False, "error"}`` when there is nothing to measure or the
    baseline did not repeat."""
    from .eval_metrics import hard_set_resolvability, load_hard_set

    hard_set = load_hard_set(hard_set_path)
    if not arms or not hard_set:
        return {"ok": False, "error": "nothing to measure: no arms, or an empty hard set"}
    if len({a.name for a in arms}) != len(arms):
        return {"ok": False, "error": "arm names must be unique"}
    missing = sorted({str(a.space) for a in arms if indexes.get(a.space) is None})
    if missing:
        return {"ok": False, "error": f"no index for: {', '.join(missing)}"}

    def run(arm: Arm) -> dict:
        return run_arm(arm, memory_dir=memory_dir, lean_index_dir=lean_index_dir,
                       index=indexes[arm.space], hard_set=hard_set, k=k)

    base = arms[0]
    first = run(base)
    results = {arm.name: run(arm) for arm in arms[1:]}
    again = run(base)
    if _deterministic(first) != _deterministic(again):
        return {
            "ok": False,
            "error": "baseline self-check FAILED: the two baseline runs bracketing the other "
            "arms are not byte-identical, so an arm leaked state and no delta is "
            "attributable. Nothing recorded.",
        }
    results = {base.name: first, **results}
    spaces = {a.space for a in arms}
    return {
        "ok": True,
        "k": k,
        "baseline": base.name,
        "baseline_self_check": "pass — baseline runs byte-identical before and after the other arms",
        "condition": {
            "hard_set_n": len(hard_set),
            "corpus_n": len(getattr(indexes[base.space], "entries", []) or []),
            "resolvable_by_category": {
                str(space or "lean"): hard_set_resolvability(indexes[space], hard_set) for space in
                sorted(spaces, key=lambda s: s or "")
            },
        },
        "arms": {
            arm.name: {
                "space": arm.space or "lean", "reranker": arm.reranker if arm.rerank or arm.pool else None,
                "rerank": bool(arm.rerank or arm.pool > k), "pool": arm.pool if arm.pool > k else k,
                "env": dict(arm.env), **results[arm.name],
            }
            for arm in arms
        },
        "deltas": {arm.name: _delta(results[arm.name], first) for arm in arms[1:]},
    }
