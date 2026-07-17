"""Eval condition arms + probes — decomposed out of ``eval_recall`` (which
re-exports every name here unchanged; ``evaluate()`` and the CLI stay there).

Carries MSR-2's null-hypothesis arm matrix, MSR-4's per-category miss autopsy, the
token-reduction/warm-latency/cold-latency probes, and GRF-4's typed-2-hop
reachability audit (GRA-7's measurable baseline arm).
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Dict, List, Optional

from .build_index import LoadedIndex, build_index, load_index, tokenize
from .eval_metrics import (
    _DEFAULT_CATEGORY,
    _estimate_tokens,
    derive_self_query,
    hard_set_metrics_by_category,
)
from .recall import format_results, recall


# --------------------------------------------------------------------------- #
# MSR-2: null-hypothesis eval arms over an index-mode x query-mode condition matrix.
#
# The eval reported absolute recall but never what the ranking STACK adds over trivial
# baselines, and it could not distinguish the production dense path from the
# production-REACHABLE mixed mode (dense index resident, bm25 ranking at query time —
# the embed-timeout / cold-cache degradation). Three report-only arms, each feeding the
# UNMODIFIED hard_set_metrics_by_category via the parameterized ranked-list source
# above (one hit-judgment code path — no arm can disagree about what a hit is):
#
#   grep   — a pure-stdlib token-overlap null. This measures RANKING-STACK LIFT over
#            the curated corpus: how much the fusion/floor/knee/graph stack adds over
#            the dumbest possible ranking of the same files. It is NOT the
#            Letta/Hidden-Layer "adopt memory at all" threshold — these fixtures cannot
#            answer that question, and no >=10-point adoption gate ships anywhere;
#            the only gate is report-only.
#   bm25   — TRUE bm25-only: a SECOND index built dense-disabled into a scratch
#            index_dir (never the real index_dir, never an in-process flag flip
#            against a resident dense matrix — that is mixed mode, not bm25-only).
#   mixed  — the explicitly-labeled degraded condition: the RESIDENT dense index with
#            HIPPO_DISABLE_DENSE at query time only, so dense ranking drops out while
#            the dense matrix stays loaded (MMR diversity still runs against it).
#            Mechanism note per the round-2 re-measurement: production dense+bm25
#            multi-hop is FIXED (GRA-1 knee suppression, 4d16022's graph_endorsed
#            exemption + cliff latch); the residual defect lives in exactly THIS mode,
#            where MMR's diversity penalty can drop a wikilink neighbor (definitionally
#            similar to its seed) — the leg GRF-2 exists to close. This arm is what
#            makes that leg measurable.
# --------------------------------------------------------------------------- #
@contextmanager
def _dense_disabled_env():
    """Set ``HIPPO_DISABLE_DENSE=1`` for a bounded scope, restoring the prior value
    exactly (the ``_ensure_index`` save/restore pattern). Eval-side only — never used
    on any hook path."""
    prev = os.environ.get("HIPPO_DISABLE_DENSE")
    os.environ["HIPPO_DISABLE_DENSE"] = "1"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("HIPPO_DISABLE_DENSE", None)
        else:
            os.environ["HIPPO_DISABLE_DENSE"] = prev


def _grep_baseline_docs(memory_dir: str) -> List[tuple]:
    """``[(name, token_set)]`` over every memory file's FULL text — the null corpus.

    Deliberately dumb: ``tokenize`` (the shared query-side normalization) with NO
    stemming, no fields, no weighting — stemming and description/body structure are part
    of the ranking stack this null exists to measure the lift OF. Read-only, stdlib.
    """
    from .provenance import _iter_memory_files

    docs: List[tuple] = []
    try:
        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            name = os.path.splitext(os.path.basename(path))[0]
            docs.append((name, set(tokenize(text))))
    except Exception:
        return []
    return docs


def _grep_rank(query: str, k: int, docs: List[tuple]) -> List[dict]:
    """Top-``k`` docs by raw query-token overlap count; zero-overlap docs never rank.

    Ties break by name so the null is exactly as deterministic as the stack it
    baselines (the pass^k probe must hold with --arms too).
    """
    q = set(tokenize(query))
    if not q:
        return []
    scored = sorted(
        ((len(q & toks), name) for name, toks in docs if q & toks),
        key=lambda t: (-t[0], t[1]),
    )
    return [{"name": name} for _score, name in scored[:k]]


# --------------------------------------------------------------------------- #
# MSR-4 (eval half): the per-category miss autopsy. The recall pipeline threw away
# WHY a memory did not surface; ``recall(..., drop_log=...)`` now records it, and this
# attributes every expected-but-missed hard-set stem to the mechanism and margin that
# cut it — the difference between "multi-hop regressed" and "multi-hop regressed
# because the knee cut the wikilink neighbor 0.02 under the cliff threshold".
# --------------------------------------------------------------------------- #
def miss_autopsy(
    index: LoadedIndex, hard_set: List[dict], k: int = 10, *, index_dir: Optional[str] = None,
    memory_dir: Optional[str] = None,
) -> Dict[str, List[dict]]:
    """``{category: [{query, stem, reason, score, margin}]}`` for every MISSED row.

    A row misses when no expected stem reaches the top-``k`` (the same binary judgment
    ``hard_set_metrics`` scores — this autopsies that exact verdict, it never re-judges).
    Each missed row is re-run ONCE with a drop-log watching its expected stems, so the
    cut record is exact regardless of the ledger caps. ``reason`` is the recall()
    drop-code that cut the stem; ``no_signal`` means the stem never entered any ranking
    at all (no BM25 token overlap, and dense unavailable or never scoring it) — on a
    bm25-only lane that is the honest "nothing to autopsy" answer, not a mechanism.
    ``margin`` = threshold - score where the mechanism has a threshold (dense_floor,
    knee_cliff); None otherwise. Eval-side only — never the hot path.
    """
    out: Dict[str, List[dict]] = {}
    for item in hard_set:
        expected = [str(s) for s in item["expected"]]
        ranked = {
            r["name"]
            for r in recall(
                item["query"], k=k, index=index, index_dir=index_dir, memory_dir=memory_dir
            )
        }
        if ranked.intersection(expected):
            continue  # the row HIT — nothing to autopsy
        dl: dict = {"watch": set(expected)}
        recall(item["query"], k=k, index=index, index_dir=index_dir, drop_log=dl, memory_dir=memory_dir)
        by_name: Dict[str, dict] = {}
        for d in dl.get("drops") or []:
            if d.get("name") in expected and d["name"] not in by_name:
                by_name[d["name"]] = d
        for stem in expected:
            d = by_name.get(stem)
            margin = None
            if d and isinstance(d.get("threshold"), (int, float)) and isinstance(
                d.get("score"), (int, float)
            ):
                margin = round(d["threshold"] - d["score"], 6)
            out.setdefault(item.get("category") or _DEFAULT_CATEGORY, []).append(
                {
                    "query": item["query"][:80],
                    "stem": stem,
                    "reason": d["reason"] if d else "no_signal",
                    "score": d.get("score") if d else None,
                    "margin": margin,
                }
            )
    return out


def null_hypothesis_arms(
    memory_dir: str,
    index: LoadedIndex,
    index_dir: Optional[str],
    hard_set: List[dict],
    k: int = 10,
    *,
    full_by_category: Optional[Dict[str, Dict[str, float]]] = None,
) -> dict:
    """The MSR-2 arm matrix: ``{"arms": {key: {label, by_category, ...}}, "deltas": ...}``.

    Report-only, eval-side, stdlib+offline (inv6 untouched). ``full_by_category`` reuses
    the report's already-computed production numbers rather than re-running them.
    Deltas are per-category vs the full pipeline; a category with n=0 on either side is
    SKIPPED (never zero-emitted) — a degenerate delta is no measurement at all.
    """
    import shutil
    import tempfile

    if not hard_set:
        return {}
    full = full_by_category or hard_set_metrics_by_category(
        index, hard_set, k=k, index_dir=index_dir, memory_dir=memory_dir
    )
    arms: Dict[str, dict] = {
        "full": {"label": "full pipeline (production ranking stack)", "by_category": full}
    }

    docs = _grep_baseline_docs(memory_dir)
    arms["grep"] = {
        "label": (
            "grep/token-overlap null — a ranking-stack-lift measure over this curated "
            "corpus, NOT an adopt-memory-at-all threshold"
        ),
        "by_category": hard_set_metrics_by_category(
            index, hard_set, k=k, ranked_source=lambda q, kk: _grep_rank(q, kk, docs)
        ),
    }

    # TRUE bm25-only: a second index built dense-disabled in a scratch dir. The real
    # index_dir is never written; the resident dense matrix is never flag-flipped.
    scratch = tempfile.mkdtemp(prefix="hippo-bm25-arm-")
    try:
        with _dense_disabled_env():
            build_index(memory_dir, scratch)
            idx2 = load_index(scratch)
        if idx2 is not None and len(idx2):
            arm = {
                "label": "true bm25-only (second index built dense-disabled in a scratch index_dir)",
                "by_category": hard_set_metrics_by_category(
                    idx2, hard_set, k=k, index_dir=scratch, memory_dir=memory_dir
                ),
            }
            if not index.dense_ready:
                arm["note"] = (
                    "degenerate: production is already bm25-only on this run, so this arm "
                    "mirrors the full pipeline"
                )
            arms["bm25"] = arm
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    if index.dense_ready:
        with _dense_disabled_env():
            arms["mixed"] = {
                "label": (
                    "mixed/degraded — dense index RESIDENT, bm25 ranking at query time "
                    "(the embed-timeout/cold-cache path; NOT bm25-only: MMR still runs "
                    "against the loaded matrix)"
                ),
                "by_category": hard_set_metrics_by_category(
                    index, hard_set, k=k, index_dir=index_dir, memory_dir=memory_dir
                ),
            }
    else:
        arms["mixed"] = {
            "label": (
                "mixed/degraded — dense index RESIDENT, bm25 ranking at query time "
                "(NOT bm25-only)"
            ),
            "skipped": "no resident dense index on this run — mixed mode is unreachable here",
            "by_category": {},
        }

    deltas: Dict[str, dict] = {}
    for arm_key, arm in arms.items():
        if arm_key == "full":
            continue
        d: Dict[str, dict] = {}
        for cat, m in (arm.get("by_category") or {}).items():
            f = full.get(cat)
            if not f or not f.get("n") or not m.get("n"):
                continue  # degenerate: skip, never zero-emit
            d[cat] = {
                "recall": round(m["recall"] - f["recall"], 4),
                "mrr": round(m["mrr"] - f["mrr"], 4),
                "n": int(m["n"]),
            }
        if d:
            deltas[arm_key] = d
    return {"arms": arms, "deltas": deltas}


def token_reduction(
    memory_dir: str, index: LoadedIndex, hard_set: List[dict], k: int = 10,
    *, index_dir: Optional[str] = None
) -> Dict[str, float]:
    """Tokens for the always-loaded full index vs (trimmed floor + per-prompt recall).

    full   = MEMORY.full.md if present (pre-trim snapshot), else current MEMORY.md
    floor  = current MEMORY.md (the trimmed always-load)
    recall = average per-query recall-injection size over the hard set (or a self sample)
    """
    full_path = os.path.join(memory_dir, "MEMORY.full.md")
    if not os.path.exists(full_path):
        full_path = os.path.join(memory_dir, "MEMORY.md")
    floor_path = os.path.join(memory_dir, "MEMORY.md")

    def _read(p: str) -> str:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                return fh.read()
        except Exception:
            return ""

    full_tokens = _estimate_tokens(_read(full_path))
    floor_tokens = _estimate_tokens(_read(floor_path))

    sample = hard_set or [{"query": derive_self_query(e)} for e in index.entries[:20]]
    inj = [
        _estimate_tokens(
            format_results(
                recall(s["query"], k=k, index=index, index_dir=index_dir, memory_dir=memory_dir)
            )
        )
        for s in sample
        if s.get("query")
    ]
    recall_tokens = round(sum(inj) / len(inj)) if inj else 0

    net = full_tokens - (floor_tokens + recall_tokens)
    pct = (net / full_tokens) if full_tokens else 0.0
    return {
        "full": full_tokens,
        "floor": floor_tokens,
        "recall_avg": recall_tokens,
        "net": net,
        "pct": round(pct, 4),
    }


def latency(
    index: LoadedIndex, queries: List[str], k: int = 10, *, index_dir: Optional[str] = None,
    memory_dir: Optional[str] = None,
) -> Dict[str, float]:
    """Warm recall latency (index preloaded) — p50/p95 in ms over ``queries``."""
    samples: List[float] = []
    for q in queries:
        if not q:
            continue
        t0 = time.perf_counter()
        recall(q, k=k, index=index, index_dir=index_dir, memory_dir=memory_dir)
        samples.append((time.perf_counter() - t0) * 1000.0)
    if not samples:
        return {"p50": 0.0, "p95": 0.0, "n": 0}
    samples.sort()
    p50 = samples[len(samples) // 2]
    p95 = samples[min(len(samples) - 1, int(round(0.95 * (len(samples) - 1))))]
    return {"p50": round(p50, 2), "p95": round(p95, 2), "n": len(samples)}


# A fresh-process recall timer (run via ``python -c``). Times ``recall()`` directly — NOT the
# CLI — so the cold measure never writes the telemetry ledger. The lazy ``fastembed`` import +
# ONNX model instantiation are paid INSIDE this fresh interpreter, exactly as every hook pays
# them; timing starts before the first recall() so the load is captured.
_COLD_PROBE = (
    "import time,sys;"
    "from memory.recall import recall;"
    "q,md,idx,k=sys.argv[1],sys.argv[2],sys.argv[3],int(sys.argv[4]);"
    "t=time.perf_counter();"
    "recall(q,k=k,memory_dir=md,index_dir=idx);"
    "print((time.perf_counter()-t)*1000.0)"
)


def cold_latency(
    memory_dir: str, index_dir: str, queries: List[str], k: int = 10, samples: int = 5
) -> Dict[str, float]:
    """COLD recall latency — the honest per-prompt number the WARM ``latency`` gate hides.

    Every real UserPromptSubmit recall spawns a FRESH process that pays the lazy ``fastembed``
    import + ONNX model load INSIDE ``recall()``; the warm gate reuses one in-process model and
    reports ~10x lower than production. This spawns a fresh interpreter per sample so the cost is
    measured the way the hook pays it. Times ``recall()`` (not the CLI) so it never writes the
    telemetry ledger. REPORT-ONLY (not a gate): a cold OS cache must not redden a healthy run, and
    with dense unavailable (CI / BM25-only) cold ≈ warm. Never raises; zeros if no sample succeeds.
    """
    import subprocess
    import sys

    # Self-locate the `memory` package's parent dir rather than trusting cwd/inherited
    # PYTHONPATH: this module may be nested arbitrarily deep (e.g. plugin/memory/ in the
    # packaged plugin, vs. a repo-root-adjacent scripts/memory/ pre-packaging) — a fresh
    # `-c` subprocess only gets "" (its own cwd) on sys.path by default, which resolves
    # `import memory.recall` only when the caller's cwd happens to equal this package's
    # parent. Pin it explicitly so cold_latency works regardless of caller cwd.
    _pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    env["PYTHONPATH"] = _pkg_parent + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    samples_ms: List[float] = []
    for q in [x for x in queries if x][:samples]:
        try:
            proc = subprocess.run(
                [sys.executable, "-c", _COLD_PROBE, q, memory_dir, index_dir, str(k)],
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )
            line = (proc.stdout or "").strip().splitlines()
            if line:
                samples_ms.append(float(line[-1]))
        except Exception:
            continue  # a failed/slow probe is dropped — cold latency must never break eval
    if not samples_ms:
        return {"p50": 0.0, "p95": 0.0, "max": 0.0, "n": 0}
    samples_ms.sort()
    n = len(samples_ms)
    return {
        "p50": round(samples_ms[n // 2], 2),
        # PRF-5: p95 is the TAIL statistic the cold gate now keys on (same nearest-rank
        # formula as the warm ``latency`` above). With a handful of cold samples it coincides
        # with the worst sample — which is exactly the honest worst-case a freshly-spawned hook
        # can pay, and the number a p50-median gate would let hide.
        "p95": round(samples_ms[min(n - 1, int(round(0.95 * (n - 1))))], 2),
        "max": round(samples_ms[-1], 2),
        "n": n,
    }


# --------------------------------------------------------------------------- #
# GRF-4: the typed-2-hop reachability audit — GRA-7's measurable baseline arm.
#
# GRA-7 (personalized PageRank) is gated on "beats GRA-1 on multi-hop", but 1-hop
# expansion is a special case — there was no typed-2-hop baseline to compare a PPR
# stage against. This reports, per multi-hop hard-set row, the MINIMUM hop depth
# (0 = the stem ranked as a seed itself, 1, 2, or unreachable) at which each expected
# stem becomes reachable from the row's top-N recall seeds over links.json adjacency,
# and which edge kind (wikilink / typed relation) the first-reaching hop used. A PURE
# OFFLINE WALK over the already-persisted edge list: zero recall.py change, no env
# flag, no telemetry schema change, nothing hot-path — and explicitly NOT authorizing
# any shipped depth-2/PPR mechanism (see the roadmap's not_pursuing: that needs its
# own gate). Gated skip-if-fixture-too-small: a reachability report over the old n=2
# multi-hop set is vacuous; it activates at the GRF-2-grown n>=10.
# --------------------------------------------------------------------------- #
_REACHABILITY_MIN_ROWS = 10
_REACHABILITY_SEEDS = 3  # mirrors recall._GRAPH_SEEDS — the expansion seam this baselines


def reachability_audit(
    index: LoadedIndex,
    hard_set: List[dict],
    index_dir: Optional[str],
    k: int = 10,
    *,
    memory_dir: Optional[str] = None,
) -> dict:
    """``{"rows": [{query, stem, depth, via}], "summary": {...}}`` — or ``{"skipped"}``.

    Seeds per row are the top-``_REACHABILITY_SEEDS`` of the PRODUCTION ranking (the
    same eval-side ``recall()`` every metric here scores — the walk itself is what
    stays pure-graph). ``depth`` 0 means the expected stem itself ranked as a seed
    (no graph needed); ``via`` names the edge kind of the first-reaching hop
    (``wikilink`` or the typed relation name), ``"-"`` at depth 0, ``None``
    unreachable. Undirected traversal over out/in/typed_out/typed_in — the same
    adjacency ``links.load_edges`` serves the hot path, read once."""
    from .links import load_edges

    multi = [r for r in hard_set if (r.get("category") or _DEFAULT_CATEGORY) == "multi-hop"]
    if len(multi) < _REACHABILITY_MIN_ROWS:
        return {
            "skipped": f"multi-hop n={len(multi)} < {_REACHABILITY_MIN_ROWS} — a "
            "reachability baseline over the ungrown fixture is vacuous (GRF-2 grows it)"
        }
    edges = load_edges(index_dir) if index_dir else None
    if not edges:
        return {"skipped": "no links.json edge list — build the index first"}

    def _neighbors(stem: str):
        # Sorted iteration everywhere: a stem reachable via two edge kinds at the same
        # depth must report a DETERMINISTIC `via` (str-set order is per-process).
        rec = edges.get(stem)
        if not rec:
            return
        for tgt in sorted(rec.get("out", ())):
            yield tgt, "wikilink"
        for tgt in sorted(rec.get("in", ())):
            yield tgt, "wikilink"
        for rel in sorted(rec.get("typed_out") or {}):
            for tgt in sorted((rec.get("typed_out") or {})[rel]):
                yield tgt, rel
        for rel in sorted(rec.get("typed_in") or {}):
            for tgt in sorted((rec.get("typed_in") or {})[rel]):
                yield tgt, rel

    rows: List[dict] = []
    counts = {0: 0, 1: 0, 2: 0, None: 0}
    for item in multi:
        ranked = recall(item["query"], k=k, index=index, index_dir=index_dir, memory_dir=memory_dir)
        seeds = [r["name"] for r in ranked[:_REACHABILITY_SEEDS]]
        for stem in item.get("expected") or ():
            depth: Optional[int] = None
            via: Optional[str] = None
            if stem in seeds:
                depth, via = 0, "-"
            else:
                frontier = {s: "-" for s in seeds}
                seen = set(seeds)
                for d in (1, 2):
                    nxt: Dict[str, str] = {}
                    for node, _how in frontier.items():
                        for tgt, kind in _neighbors(node):
                            if tgt in seen:
                                continue
                            nxt.setdefault(tgt, kind)
                    if stem in nxt:
                        depth, via = d, nxt[stem]
                        break
                    seen |= set(nxt)
                    frontier = nxt
            counts[depth] = counts.get(depth, 0) + 1
            rows.append(
                {"query": item["query"][:60], "stem": stem, "depth": depth, "via": via}
            )
    total = len(rows)
    return {
        "rows": rows,
        "summary": {
            "expected_stems": total,
            "seed_rank_0": counts[0],
            "reachable_at_1": counts[1],
            "reachable_at_2": counts[2],
            "unreachable": counts[None],
            "seeds_per_row": _REACHABILITY_SEEDS,
        },
    }
