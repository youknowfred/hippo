"""Recall evaluation harness — the 5 merge gates for Tier 2.

Gates (all must hold to merge / keep the recall path trustworthy):
  1. synthetic self-recall@10  >= 0.90  — each memory is retrievable by a query DERIVED
                                          from its own ``description`` (zero-maintenance
                                          backbone; catches a broken index).
  2. curated hard-set recall@10 >= 0.80 — hand-written cross-vocabulary PARAPHRASE queries
                                          (``recall_hard_set.yaml``) find the right memory.
  3. MRR@10                     >= 0.60 — the right memory ranks near the top, not just in
                                          the top-10, on the hard set.
  4. net token reduction        >  0    — trimmed floor + per-query recall injection costs
                                          fewer tokens than always-loading the full index.
  5. recall p95 (warm)          <  300ms — fast enough to run on every prompt.

Gate 5 is measured WARM (one in-process model reused across the loop). ``cold_latency``
reports the REAL per-process model-load cost every freshly-spawned hook pays — surfaced
alongside the warm p95 but NOT gated (a cold OS cache must not redden a healthy run; with
dense unavailable, cold ≈ warm).

RET-2: ``body_probe`` is a REPORT-ONLY (never-gated) addition proving body-chunk indexing
actually helps — probe queries are derived from body tokens ABSENT from a memory's own
description, so passing this metric proves something self_recall (description-derived
queries) cannot: that content living ONLY in the body is retrievable. The 5 gates above are
unchanged in number/semantics.

Pure / dependency-light: dense is used when the index has it, otherwise the gates are
computed on BM25 alone (so they run in CI without fastembed). ``main`` exits non-zero if
any gate fails (use it as a pre-merge check).
"""

from __future__ import annotations

import os
import time
from typing import Dict, List, Optional

from .build_index import (
    LoadedIndex,
    build_index,
    default_index_dir,
    entry_description,
    load_index,
    tokenize,
)
from .provenance import resolve_dirs
from .recall import format_results, recall

# Gate thresholds (the locked decisions from the roadmap).
GATE_SELF_RECALL = 0.90
GATE_HARD_RECALL = 0.80
GATE_MRR = 0.60
GATE_P95_MS = 300.0

_SELF_QUERY_TOKENS = 12
# RET-2: body_probe queries keep the first N tokens that are BOTH in a memory's body chunks
# AND absent from its description -- the same "derived, zero-maintenance" spirit as
# derive_self_query, but proving the NEW thing this item adds (body content is retrievable)
# rather than the thing self_recall already proves (description content is retrievable).
_BODY_PROBE_TOKENS = 12


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _estimate_tokens(text: str) -> int:
    """Rough token estimate (chars/4, the conventional heuristic)."""
    return max(0, round(len(text or "") / 4))


def _description_of(entry: dict) -> str:
    return entry_description(entry)


def derive_self_query(entry: dict) -> str:
    """A query DERIVED from a memory's description (not the indexed string verbatim).

    Tokenizes the description (drops the name + stopwords) and keeps the first N content
    tokens — a fair "can the index find this memory from its own words" probe.
    """
    toks = tokenize(_description_of(entry))
    return " ".join(toks[:_SELF_QUERY_TOKENS])


# --------------------------------------------------------------------------- #
# Gates
# --------------------------------------------------------------------------- #
def self_recall_at_k(index: LoadedIndex, k: int = 10) -> float:
    entries = index.entries
    if not entries:
        return 0.0
    hits = 0
    considered = 0
    for e in entries:
        q = derive_self_query(e)
        if not q:
            continue
        considered += 1
        names = {r["name"] for r in recall(q, k=k, index=index)}
        if e["name"] in names:
            hits += 1
    return hits / considered if considered else 0.0


# --------------------------------------------------------------------------- #
# RET-2: body_probe — REPORT-ONLY metric proving body chunks are retrievable at all (not
# just "the index still finds descriptions", which self_recall already covers). A probe
# query is derived per-memory from BODY tokens ABSENT from the description -- if the query
# only used tokens the description ALSO carries, a description-only (pre-RET-2) index would
# already pass, so the probe wouldn't be testing anything new. This is a NEW gate-adjacent
# metric, but never a merge gate itself (per the roadmap: "the 5 gate semantics unchanged").
# --------------------------------------------------------------------------- #
def derive_body_probe_query(index: LoadedIndex, entry_idx: int) -> str:
    """A query from body tokens NOT in the entry's description, or "" when none qualify.

    Walks ``index.body_chunks`` (RET-2's persisted ``{entry, hash, tokens, row}`` list) for
    every chunk belonging to ``entry_idx``, collects tokens in body-chunk order (first chunk
    first, tokens in their original order) that are ABSENT from the description's own token
    set, dedupes while preserving that order, and keeps the first ``_BODY_PROBE_TOKENS``
    (~12). An entry with no qualifying body chunks (no chunks at all, or every body token
    already appears in the description) yields "" -- the caller excludes it from the
    denominator, exactly like ``self_recall_at_k`` excludes an empty ``derive_self_query``.
    """
    entries = index.entries
    if entry_idx < 0 or entry_idx >= len(entries):
        return ""
    desc_tokens = set(tokenize(_description_of(entries[entry_idx])))
    seen: set = set()
    out: List[str] = []
    for chunk in index.body_chunks:
        if chunk.get("entry") != entry_idx:
            continue
        for tok in chunk.get("tokens") or []:
            if tok in desc_tokens or tok in seen:
                continue
            seen.add(tok)
            out.append(tok)
            if len(out) >= _BODY_PROBE_TOKENS:
                break
        if len(out) >= _BODY_PROBE_TOKENS:
            break
    return " ".join(out)


def body_probe_recall_at_k(index: LoadedIndex, k: int = 10) -> Dict[str, float]:
    """recall@k of the PARENT entry for a body-derived probe query, over every entry that
    has a qualifying probe (see ``derive_body_probe_query``). REPORT-ONLY -- never a merge
    gate; ``n=0`` (and ``recall=0.0``) when no entry in the corpus has a body chunk carrying a
    token absent from its own description (e.g. a BM25-only index built before this item ever
    ran, or a corpus whose bodies are pure restatements of their descriptions)."""
    entries = index.entries
    if not entries:
        return {"recall": 0.0, "n": 0}
    hits = 0
    considered = 0
    for i, e in enumerate(entries):
        q = derive_body_probe_query(index, i)
        if not q:
            continue
        considered += 1
        names = {r["name"] for r in recall(q, k=k, index=index)}
        if e["name"] in names:
            hits += 1
    return {"recall": round(hits / considered, 4) if considered else 0.0, "n": considered}


def load_relevance_set(path: str) -> List[dict]:
    """Load ``[{query, relevant: [name, ...]}]`` from a hand-judged YAML fixture. [] if missing.

    Unlike ``load_hard_set``'s ``expected`` (any ONE counts as a binary hit), ``relevant``
    lists EVERY memory stem judged relevant to the query, feeding the graded ``precision_at_k``
    metric below. Mirrors ``load_hard_set``'s loader shape exactly.
    """
    if not path or not os.path.exists(path):
        return []
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or []
    except Exception:
        return []
    out: List[dict] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        q = item.get("query")
        rel = item.get("relevant")
        if isinstance(rel, str):
            rel = [rel]
        if isinstance(q, str) and isinstance(rel, list) and rel:
            out.append({"query": q, "relevant": [str(x) for x in rel]})
    return out


def precision_at_k(index: LoadedIndex, relevance_set: List[dict], k: int = 10) -> Dict[str, float]:
    """precision@k = |top-k ∩ relevant| / k, averaged over a hand-judged relevance set.

    A GRADED measure, distinct from ``hard_set_metrics``' binary recall@k (any one expected
    name in the top-k counts as a full hit): a query whose relevant set spans several
    memories is rewarded for surfacing MORE of them, not just one. REPORT-ONLY — never a
    merge gate. ``n=0`` (zero precision) when the relevance set is empty/missing.
    """
    if not relevance_set or k <= 0:
        return {"precision": 0.0, "n": 0}
    total = 0.0
    for item in relevance_set:
        relevant = set(item["relevant"])
        ranked = [r["name"] for r in recall(item["query"], k=k, index=index)]
        total += len(relevant.intersection(ranked)) / k
    n = len(relevance_set)
    return {"precision": round(total / n, 4), "n": n}


def staleness_half_life(memory_dir: str, repo_root: str, *, now: Optional[float] = None) -> Dict[str, float]:
    """Median age in days (vs ``now``) of the corpus's staleness baselines (``source_commit``).

    A half-life PROXY: the median splits the corpus's baseline-age distribution exactly in
    half, so half the corpus's content baselines are younger than this figure and half are
    older — a single report-only number for "how stale, on average, are this corpus's
    provenance baselines right now." Memories with no ``source_commit`` yet (not backfilled)
    are excluded from the sample rather than counted as age zero. REPORT-ONLY. Read-only over
    git history (reuses ``staleness._commit_times``); never raises; ``n=0`` when no memory has
    a resolvable baseline.
    """
    from .provenance import _iter_memory_files
    from .staleness import _commit_times, read_provenance

    ref = now if now is not None else time.time()
    ages_days: List[float] = []
    try:
        shas: List[str] = []
        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            _, sc = read_provenance(text)
            if sc:
                shas.append(sc)
        ctimes = _commit_times(shas, repo_root)
        ages_days = sorted((ref - t) / 86400.0 for t in ctimes.values())
    except Exception:
        ages_days = []
    if not ages_days:
        return {"median_days": 0.0, "n": 0}
    n = len(ages_days)
    median = ages_days[n // 2] if n % 2 == 1 else (ages_days[n // 2 - 1] + ages_days[n // 2]) / 2.0
    return {"median_days": round(median, 1), "n": n}


def session_token_cost(
    memory_dir: str,
    telemetry_dir: Optional[str],
    index: LoadedIndex,
    hard_set: List[dict],
    k: int = 10,
) -> Dict[str, float]:
    """Average recall-injection tokens PER SESSION (vs ``token_reduction``'s per-QUERY figure).

    = average recall events per session (from the REAL telemetry ledger) x the average
    per-query recall-injection token cost (reuses ``token_reduction``'s ``recall_avg`` rather
    than re-deriving it). REPORT-ONLY. Read-only over the telemetry ledger; never raises;
    zeros when no session has been logged yet (a fresh corpus / clean telemetry dir).

    ``telemetry_dir=None`` derives the SIBLING of ``memory_dir`` (mirrors
    ``recall.main()``'s ``default_telemetry_dir(args.memory_dir)`` pattern) rather than
    independently re-resolving via the ambient ``resolve_dirs()`` — an explicit
    ``memory_dir`` (a hermetic test corpus, or any non-default corpus) must never silently
    read a DIFFERENT corpus's telemetry ledger.
    """
    from .telemetry import default_telemetry_dir, read_events

    td = telemetry_dir or default_telemetry_dir(memory_dir)
    sessions: Dict[str, int] = {}
    try:
        for e in read_events(td):
            sid = e.get("session_id")
            if sid:
                sessions[sid] = sessions.get(sid, 0) + 1
    except Exception:
        pass
    if not sessions:
        return {"avg_events_per_session": 0.0, "avg_session_tokens": 0.0, "n_sessions": 0}
    avg_events = sum(sessions.values()) / len(sessions)
    tok = token_reduction(memory_dir, index, hard_set, k=k)
    return {
        "avg_events_per_session": round(avg_events, 2),
        "avg_session_tokens": round(avg_events * tok["recall_avg"], 1),
        "n_sessions": len(sessions),
    }


def graduation_rate(telemetry_dir: Optional[str] = None) -> Dict[str, float]:
    """graduate / (graduate + demote) over the reconsolidation outcome ledger.

    The ACCURACY axis of the scorecard: of the recently-recalled memories the immune system
    flagged for re-grounding, what fraction were confirmed CORRECT (graduate) vs WRONG
    (demote)? ``fix`` outcomes are EXCLUDED from this ratio by design (per the roadmap's
    pinned formula) — a fix is a distinct outcome (content was wrong, then corrected), not a
    verdict on whether the ORIGINALLY flagged content was right or wrong, which is what this
    ratio measures. REPORT-ONLY — never a merge gate. Read-only over the ledger; never raises;
    ``n=0`` when no graduate/demote outcome has been logged yet (a ``fix``-only ledger also
    yields ``n=0``).
    """
    from .telemetry import read_reconsolidation_events

    counts = {"graduate": 0, "fix": 0, "demote": 0}
    try:
        for e in read_reconsolidation_events(telemetry_dir):
            outcome = e.get("outcome")
            if outcome in counts:
                counts[outcome] += 1
    except Exception:
        pass
    denominator = counts["graduate"] + counts["demote"]
    if not denominator:
        return {"rate": 0.0, "n": 0, **counts}
    return {"rate": round(counts["graduate"] / denominator, 4), "n": denominator, **counts}


def load_hard_set(path: str) -> List[dict]:
    """Load ``[{query, expected: [name, ...]}]`` from a YAML fixture. [] if missing."""
    if not path or not os.path.exists(path):
        return []
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or []
    except Exception:
        return []
    out: List[dict] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        q = item.get("query")
        exp = item.get("expected")
        if isinstance(exp, str):
            exp = [exp]
        if isinstance(q, str) and isinstance(exp, list) and exp:
            out.append({"query": q, "expected": [str(x) for x in exp]})
    return out


def hard_set_metrics(index: LoadedIndex, hard_set: List[dict], k: int = 10) -> Dict[str, float]:
    """recall@k (any expected in top-k) + MRR@k (1/rank of first expected) over the set."""
    if not hard_set:
        return {"recall": 0.0, "mrr": 0.0, "n": 0}
    hit = 0
    rr_sum = 0.0
    for item in hard_set:
        expected = set(item["expected"])
        ranked = [r["name"] for r in recall(item["query"], k=k, index=index)]
        if expected.intersection(ranked):
            hit += 1
        rr = 0.0
        for rank, name in enumerate(ranked):
            if name in expected:
                rr = 1.0 / (rank + 1)
                break
        rr_sum += rr
    n = len(hard_set)
    return {"recall": hit / n, "mrr": rr_sum / n, "n": n}


def token_reduction(
    memory_dir: str, index: LoadedIndex, hard_set: List[dict], k: int = 10
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
        _estimate_tokens(format_results(recall(s["query"], k=k, index=index)))
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


def latency(index: LoadedIndex, queries: List[str], k: int = 10) -> Dict[str, float]:
    """Warm recall latency (index preloaded) — p50/p95 in ms over ``queries``."""
    samples: List[float] = []
    for q in queries:
        if not q:
            continue
        t0 = time.perf_counter()
        recall(q, k=k, index=index)
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
    memory_dir: str, index_dir: str, queries: List[str], k: int = 10, samples: int = 3
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
        return {"p50": 0.0, "max": 0.0, "n": 0}
    samples_ms.sort()
    return {
        "p50": round(samples_ms[len(samples_ms) // 2], 2),
        "max": round(samples_ms[-1], 2),
        "n": len(samples_ms),
    }


# --------------------------------------------------------------------------- #
# Top-level evaluation
# --------------------------------------------------------------------------- #
def evaluate(
    memory_dir: Optional[str] = None,
    index_dir: Optional[str] = None,
    hard_set_path: Optional[str] = None,
    k: int = 10,
    *,
    relevance_set_path: Optional[str] = None,
    repo_root: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
) -> dict:
    """Run all 5 gates; return a report dict with per-gate values + pass flags.

    ``relevance_set_path``/``repo_root``/``telemetry_dir`` feed three REPORT-ONLY scorecard
    additions (precision@k, staleness half-life, per-session token cost) — none of the 5
    gates above are affected by any of them; omitting all three reproduces the exact prior
    report shape plus three zeroed report blocks.
    """
    if memory_dir is None:
        # Only resolve_dirs() when memory_dir actually needs it -- mirrors recall.main()'s
        # hermeticity guard: never spend an EXTRA git call just to backfill repo_root when an
        # explicit memory_dir was already passed (keeps explicit-memory-dir test/CLI calls
        # fully hermetic instead of resolving repo_root against whatever cwd happens to be).
        resolved_memory_dir, resolved_repo_root = resolve_dirs()
        memory_dir = resolved_memory_dir
        if repo_root is None:
            repo_root = resolved_repo_root
    if index_dir is None:
        index_dir = default_index_dir(memory_dir)

    index = load_index(index_dir)
    if index is None:
        build_index(memory_dir, index_dir)
        index = load_index(index_dir)
    if index is None or not len(index):
        return {"ok": False, "error": "no index / empty corpus"}

    hard_set = load_hard_set(hard_set_path) if hard_set_path else []
    relevance_set = load_relevance_set(relevance_set_path) if relevance_set_path else []

    self_recall = self_recall_at_k(index, k=k)
    hs = hard_set_metrics(index, hard_set, k=k)
    tok = token_reduction(memory_dir, index, hard_set, k=k)
    lat_queries = [item["query"] for item in hard_set] or [
        derive_self_query(e) for e in index.entries[:30]
    ]
    lat = latency(index, lat_queries, k=k)
    cold = cold_latency(memory_dir, index_dir, lat_queries, k=k)

    # Report-only scorecard additions (Tier 1 + Tier 2) — never feed a gate threshold above.
    # Resolve telemetry_dir ONCE here (sibling of memory_dir) and pass the SAME resolved value
    # to every consumer below -- each independently re-deriving it from None would re-resolve
    # via the ambient resolve_dirs(), which can leak onto the real repo's ledger when an
    # explicit memory_dir was passed (the same class of leak the repo_root guard above closes).
    from .telemetry import default_telemetry_dir

    resolved_telemetry_dir = telemetry_dir or default_telemetry_dir(memory_dir)
    precision = precision_at_k(index, relevance_set, k=k)
    half_life = staleness_half_life(memory_dir, repo_root) if repo_root else {"median_days": 0.0, "n": 0}
    sess_cost = session_token_cost(memory_dir, resolved_telemetry_dir, index, hard_set, k=k)
    grad = graduation_rate(resolved_telemetry_dir)
    body_probe = body_probe_recall_at_k(index, k=k)

    # A caller with NO hard-set fixture (hard_set_path=None — e.g. a fresh install of the
    # packaged plugin with no hand-curated calibration data yet, see /hippo:audit) is a
    # deliberately-absent input, not a failure. Those two gates report "skipped" (pass=None,
    # excluded from `ok`) rather than a false FAIL against an empty set. A caller who DID pass
    # a hard_set_path that happens to load empty (a malformed/truncated fixture file) keeps the
    # original strict fail-on-empty behavior — that case is a real problem worth failing loudly.
    hard_set_provided = bool(hard_set_path)
    # token_reduction compares the TRIMMED floor + per-query recall against the pre-trim
    # MEMORY.full.md snapshot. A corpus that never had an untrimmed always-load (every fresh
    # install — MEMORY.full.md absent) has nothing to compare against: full == floor and the
    # gate would fail as net == -recall_avg in EVERY fresh project. Same skip semantics as
    # the absent hard set: deliberately-absent input, not a failure.
    has_full_snapshot = os.path.exists(os.path.join(memory_dir, "MEMORY.full.md"))
    gates = {
        "self_recall@10": {"value": round(self_recall, 4), "threshold": GATE_SELF_RECALL, "pass": self_recall >= GATE_SELF_RECALL},
        "hard_recall@10": {
            "value": round(hs["recall"], 4), "threshold": GATE_HARD_RECALL,
            "pass": (hs["n"] > 0 and hs["recall"] >= GATE_HARD_RECALL) if hard_set_provided else None,
            **({"skipped": True} if not hard_set_provided else {}),
        },
        "mrr@10": {
            "value": round(hs["mrr"], 4), "threshold": GATE_MRR,
            "pass": (hs["n"] > 0 and hs["mrr"] >= GATE_MRR) if hard_set_provided else None,
            **({"skipped": True} if not hard_set_provided else {}),
        },
        "token_reduction": {
            "value": tok["net"], "pct": tok["pct"], "threshold": 0,
            "pass": (tok["net"] > 0) if has_full_snapshot else None,
            **({} if has_full_snapshot else {"skipped": True}),
        },
        "recall_p95_ms": {"value": lat["p95"], "threshold": GATE_P95_MS, "pass": lat["p95"] < GATE_P95_MS},
    }
    return {
        "ok": all(g["pass"] for g in gates.values() if g.get("pass") is not None),
        "dense_ready": index.dense_ready,
        "model": index.model,
        "count": len(index),
        "hard_set_n": hs["n"],
        "gates": gates,
        "tokens": tok,
        "latency": lat,
        "cold_latency": cold,
        "precision_at_k": precision,
        "staleness_half_life": half_life,
        "session_token_cost": sess_cost,
        "graduation_rate": grad,
        "body_probe": body_probe,
    }


def _default_fixture_path(filename: str) -> Optional[str]:
    """Resolve a default eval fixture, or None when no fixture exists anywhere.

    Probe order:
      1. ``.claude/memory/.audit-fixtures/<filename>`` — the project-local convention
         the /hippo:audit skill writes to (any consuming project can carry its own
         calibration data).
      2. ``<repo>/tests/fixtures/<filename>`` — the engine repo's own checked-in set.

    ``None`` (nothing found) makes ``main()`` inherit ``evaluate()``'s skip semantics
    for the hard-set gates rather than failing them against a path that exists
    nowhere — an absent fixture is a deliberately-absent input, not a failure.
    """
    memory_dir, repo = resolve_dirs()
    for candidate in (
        os.path.join(memory_dir, ".audit-fixtures", filename),
        os.path.join(repo, "tests", "fixtures", filename),
    ):
        if os.path.exists(candidate):
            return candidate
    return None


def _default_hard_set_path() -> Optional[str]:
    return _default_fixture_path("recall_hard_set.yaml")


def _default_relevance_set_path() -> Optional[str]:
    return _default_fixture_path("recall_relevance_set.yaml")


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate the memory recall gates.")
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--index-dir", default=None)
    parser.add_argument("--hard-set", default=None)
    parser.add_argument("--relevance-set", default=None)
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--telemetry-dir", default=None)
    parser.add_argument("-k", type=int, default=10)
    args = parser.parse_args(argv)

    report = evaluate(
        memory_dir=args.memory_dir,
        index_dir=args.index_dir,
        hard_set_path=args.hard_set or _default_hard_set_path(),
        k=args.k,
        relevance_set_path=args.relevance_set or _default_relevance_set_path(),
        repo_root=args.repo_root,
        telemetry_dir=args.telemetry_dir,
    )
    if not report.get("ok") and "error" in report:
        print(f"eval error: {report['error']}")
        return 1

    print(f"corpus={report['count']} dense={report['dense_ready']} model={report['model']} hard_set={report['hard_set_n']}")
    _SKIP_REASONS = {
        "hard_recall@10": "no hard-set fixture",
        "mrr@10": "no hard-set fixture",
        "token_reduction": "no MEMORY.full.md pre-trim snapshot",
    }
    for name, g in report["gates"].items():
        skipped = g.get("pass") is None
        mark = "➖" if skipped else ("✅" if g["pass"] else "❌")
        extra = f" ({g['pct']*100:.1f}% reduction)" if name == "token_reduction" else ""
        if skipped:
            extra += f" — skipped ({_SKIP_REASONS.get(name, 'input absent')}; excluded from RESULT)"
        print(f"  {mark} {name:18s} = {g['value']} (threshold {g['threshold']}){extra}")
    t = report["tokens"]
    print(f"  tokens: full={t['full']} floor={t['floor']} recall_avg={t['recall_avg']} net={t['net']}")
    print(f"  latency (warm): p50={report['latency']['p50']}ms p95={report['latency']['p95']}ms n={report['latency']['n']}")
    c = report.get("cold_latency") or {}
    if c.get("n"):
        print(
            f"  latency (cold, per-process model load): p50={c['p50']}ms max={c['max']}ms n={c['n']} "
            "— the REAL hook cost; the warm p95 above understates it (report-only, not gated)"
        )

    # Report-only scorecard additions (Tier 1, memory-organism-instrument-immunize) — NONE of
    # these feed a gate threshold above; they exist to MEASURE, not to merge-block.
    p = report.get("precision_at_k") or {}
    if p.get("n"):
        print(f"  precision@{args.k} (graded, n={p['n']}): {p['precision']} (report-only)")
    hl = report.get("staleness_half_life") or {}
    if hl.get("n"):
        print(f"  staleness half-life: median {hl['median_days']}d across {hl['n']} baselined memories (report-only)")
    sc = report.get("session_token_cost") or {}
    if sc.get("n_sessions"):
        print(
            f"  session token cost: ~{sc['avg_session_tokens']} tokens/session "
            f"({sc['avg_events_per_session']} recalls/session over {sc['n_sessions']} sessions, report-only)"
        )
    gr = report.get("graduation_rate") or {}
    if gr.get("n"):
        print(
            f"  graduation rate: {gr['rate']} ({gr['graduate']} graduate / {gr['demote']} demote, "
            f"{gr['fix']} fix excluded from ratio, report-only)"
        )
    bp = report.get("body_probe") or {}
    if bp.get("n"):
        print(
            f"  body_probe@{args.k} (RET-2, n={bp['n']}): {bp['recall']} — parent recall for "
            "queries derived from body-only tokens (report-only)"
        )

    print("RESULT:", "ALL GATES PASS ✅" if report["ok"] else "GATE FAILURE ❌")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
