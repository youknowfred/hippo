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

RET-1: ``abstention_rate`` is the mirror image of the 5 gates above — where
self_recall/hard_recall/mrr all measure "does recall() find the RIGHT memory",
abstention_rate measures "does recall() correctly find NOTHING for a query with no right
answer at all". Fed by an optional ``--abstention-set`` fixture of clearly off-topic
queries (``recall_abstention_set.yaml`` / the golden corpus's ``abstention_set.yaml``);
``rate`` = fraction of those queries for which recall() returned zero results. Shipped
report-only by RET-1; PROMOTED to a tracked, fixture-gated entry by RET-8 (below) — the
"depends on which probes someone wrote down" concern is handled the same way the hard-set
gates handle it: no fixture → the gate SKIPS rather than fails, and the threshold is a
regression tripwire calibrated against the shipped fixture, not an absolute quality claim.

RET-8: the category-tagged eval suite — the measurement keystone (KPI-4). Three additions:
  1. Hard-set rows may carry a ``category`` tag (canonical values: ``single-hop``,
     ``multi-hop``, ``temporal``, ``update``, ``abstention``; absent → ``single-hop``,
     which is what every pre-RET-8 row measured). Unknown strings pass through data-driven
     rather than erroring — SIG-6's self-populating fixtures extend the set without a
     loader change.
  2. ``report["by_category"]`` emits recall@k/MRR@k PER CATEGORY (printed per line by
     ``main``), so a regression is attributable to the question class that regressed —
     multi-hop (validates GRA-1 expansion), temporal (validates GRA-4 invalidation),
     update (post-reconsolidation truth), not just one aggregate.
  3. ``precision@10`` and ``abstention_rate`` are PROMOTED from report-only to tracked
     entries in the gates dict, with the hard-set gates' exact skip semantics (fixture
     absent → ``pass: None`` + ``skipped``, excluded from ``ok``; fixture provided but
     empty → loud FAIL). ROADMAP.v1 names this promotion as RET-8's license.

RET-7: every report records the SERVING BACKEND (``report["backend"]`` = ``"dense+bm25"``
when ``index.dense_ready`` else ``"bm25-only"``), printed on the gate-header line AND the
RESULT line, so a BM25-only pass can never be mistaken for verified hybrid recall health —
this matters because gates 2/3 (hard-set recall/MRR) can genuinely PASS on lexical overlap
alone in a small/favorable corpus even with dense entirely unavailable. A hard-set fixture
MAY additionally carry a ``generated_with_backend`` provenance header (see
``_load_fixture_docs``); when the fixture claims ``dense+bm25`` but this run only served
``bm25-only``, ``report["backend_mismatch"]`` is set and a loud warning prints — the
`/hippo:audit` skill surfaces this flag rather than reporting a bare pass/fail.

Pure / dependency-light: dense is used when the index has it, otherwise the gates are
computed on BM25 alone (so they run in CI without fastembed). ``main`` exits non-zero if
any gate fails (use it as a pre-merge check).
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from typing import Dict, List, Optional

from .build_index import (
    LoadedIndex,
    bm25_terms,
    build_index,
    default_index_dir,
    entry_description,
    load_index,
    tokenize,
)
from .provenance import ensure_self_ignoring_dir, resolve_dirs
from .recall import format_results, recall

# Gate thresholds (the locked decisions from the roadmap).
GATE_SELF_RECALL = 0.90
GATE_HARD_RECALL = 0.80
GATE_MRR = 0.60
GATE_P95_MS = 300.0
# PRF-2/PRF-5: the honest per-prompt budget for cold_latency, gated at p95 — the TAIL, not the
# p50 median (PRF-5 aligned this to the KPI-3/doctor statistic so a slow worst-case can't hide
# behind a healthy median). Fresh-subprocess-per-sample (see cold_latency()'s docstring); gate 5
# above is measured WARM and ~10x under the real per-prompt cost, so this is the number that
# reflects what a freshly-spawned hook actually pays. Report-only by default (opt in via
# --gate-cold / evaluate()'s gate_cold=True) so a cold OS cache on an ungated hermetic run never
# reddens CI; the dense CI lane (warm fastembed cache) passes --gate-cold so a REAL cold-path
# regression (a heavier model, a new per-import cost) fails the build.
GATE_COLD_P95_MS = 1500.0
# RET-8: the two promoted fixture-gated thresholds — REGRESSION TRIPWIRES calibrated against
# the shipped fixtures on the pack-seeded corpus, NOT absolute quality claims. Measured at
# promotion time (2026-07-09, 22-memory pack corpus): precision@10 0.1375 dense / 0.15
# bm25-only; abstention_rate 0.3333 on BOTH backends (BM25's match-set filter admits an
# off-topic query on a single coincidental token overlap, and the dense floor never
# overrides a BM25 match — the RET-1 design). precision@10's ceiling is structurally low
# (|relevant| is 1-3 per query, so a perfect run scores ~0.1-0.3). The thresholds sit just
# under the min measured value: a change that breaks the floor/knee/hard-skip trio (rate
# → 0.0) or tanks graded ranking trips them; normal jitter does not. Both gates SKIP
# (never fail) when their fixture is absent.
GATE_PRECISION_AT_K = 0.12
GATE_ABSTENTION = 0.30

# RET-8: the canonical category tags. Data-driven everywhere (an unknown tag forms its own
# bucket rather than erroring) — this tuple is documentation + the default, not an enum wall.
CATEGORIES = ("single-hop", "multi-hop", "temporal", "update", "abstention")
_DEFAULT_CATEGORY = "single-hop"  # what every pre-RET-8 row measured

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
def self_recall_at_k(
    index: LoadedIndex, k: int = 10, *, index_dir: Optional[str] = None,
    memory_dir: Optional[str] = None,
) -> float:
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
        names = {r["name"] for r in recall(q, k=k, index=index, index_dir=index_dir, memory_dir=memory_dir)}
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
    # RET-12: body-chunk tokens are stemmed (build_index.bm25_terms); stem the description
    # side the same way so "absent from description" compares like-for-like term-space,
    # rather than treating a merely-inflected description word as novel body content.
    desc_tokens = set(bm25_terms(tokenize(_description_of(entries[entry_idx]))))
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


def body_probe_recall_at_k(
    index: LoadedIndex, k: int = 10, *, index_dir: Optional[str] = None,
    memory_dir: Optional[str] = None,
) -> Dict[str, float]:
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
        names = {r["name"] for r in recall(q, k=k, index=index, index_dir=index_dir, memory_dir=memory_dir)}
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


def load_abstention_set(path: str) -> List[str]:
    """Load a bare list of CLEARLY off-topic query strings from a YAML fixture. [] if missing.

    RET-1: distinct schema from ``load_hard_set``/``load_relevance_set`` -- there is no
    ``expected``/``relevant`` field, because there is nothing these queries SHOULD retrieve;
    the fixture is just ``- query: "..."`` rows. Reuses ``_load_fixture_docs`` so an optional
    provenance header (unused today, but kept available for parity with the other two
    fixtures) is tolerated rather than mis-parsed as a query row.
    """
    _meta, data = _load_fixture_docs(path)
    out: List[str] = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and isinstance(item.get("query"), str):
            out.append(item["query"])
        elif isinstance(item, str):  # tolerate a bare string row too, not just {query: ...}
            out.append(item)
    return out


def abstention_rate(
    index: LoadedIndex, abstention_set: List[str], k: int = 10, *, index_dir: Optional[str] = None,
    memory_dir: Optional[str] = None,
) -> Dict[str, float]:
    """Fraction of ``abstention_set`` queries for which recall() returned ZERO results.

    Proves the NEW thing RET-1 adds (a clearly off-topic prompt can abstain, injecting
    nothing) the way ``body_probe`` proves RET-2's new capability: this is a metric no
    PRE-RET-1 index could ever score above 0 on (there was no floor/knee/hard-skip to
    abstain with). The realistic ceiling is well under 1.0 — BM25's match-set filter
    admits an off-topic query on a single coincidental token overlap and the dense floor
    never overrides a BM25 match (measured 0.3333 on the pack corpus, both backends) —
    which is why ``GATE_ABSTENTION`` is a just-under-measured tripwire, not a "near 1.0"
    target. Shipped report-only by RET-1; PROMOTED to a tracked, fixture-gated entry by
    RET-8 (hard-set skip semantics — see ``evaluate``).

    RET-11 (2026-07-10): a BM25-only abstention FLOOR was designed and empirically rejected,
    not skipped. On the golden fixture the off-topic and on-topic classes overlap in EVERY
    BM25-observable signal — summed matched-token IDF mass (off-topic 4.19 vs an on-topic
    minimum of 3.67), matched-token count (real queries match as few as 1 token), and
    single-token IDF all interleave — so no lexical threshold rejects the off-topic queries
    without also dropping real single-keyword hits. Only the dense semantic floor separates
    them. Abstention is therefore DENSE-GATED by decision, surfaced by
    ``doctor.check_abstention_cold_start`` + a warm-the-model nudge, rather than faked with a
    false-precision BM25 floor.
    ``n=0`` (rate 0.0) when the fixture is empty/missing -- a deliberately-absent input at
    THIS layer; ``evaluate`` decides skip-vs-fail from whether a path was provided.
    """
    if not abstention_set:
        return {"rate": 0.0, "n": 0}
    zero = 0
    for q in abstention_set:
        if not recall(q, k=k, index=index, index_dir=index_dir, memory_dir=memory_dir):
            zero += 1
    n = len(abstention_set)
    return {"rate": round(zero / n, 4), "n": n}


def precision_at_k(
    index: LoadedIndex, relevance_set: List[dict], k: int = 10, *, index_dir: Optional[str] = None,
    memory_dir: Optional[str] = None,
) -> Dict[str, float]:
    """precision@k = |top-k ∩ relevant| / k, averaged over a hand-judged relevance set.

    A GRADED measure, distinct from ``hard_set_metrics``' binary recall@k (any one expected
    name in the top-k counts as a full hit): a query whose relevant set spans several
    memories is rewarded for surfacing MORE of them, not just one. Shipped report-only;
    PROMOTED to a tracked, fixture-gated entry by RET-8 (``GATE_PRECISION_AT_K``, hard-set
    skip semantics — see ``evaluate``). ``n=0`` (zero precision) when the relevance set is
    empty/missing; ``evaluate`` decides skip-vs-fail from whether a path was provided.
    """
    if not relevance_set or k <= 0:
        return {"precision": 0.0, "n": 0}
    total = 0.0
    for item in relevance_set:
        relevant = set(item["relevant"])
        ranked = [r["name"] for r in recall(item["query"], k=k, index=index, index_dir=index_dir, memory_dir=memory_dir)]
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
    *,
    index_dir: Optional[str] = None,
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

    MSR-6: events carrying ``injected_chars`` (the ledger-measured emitted payload)
    upgrade the per-query figure from ``token_reduction``'s ESTIMATE to a measured
    actual (chars/4, the same heuristic, over real payloads). The estimate remains the
    fallback for a ledger predating the field; ``measured_events`` (additive) says
    which one this report used. Report-only status and gate constants untouched.
    """
    from .telemetry import default_telemetry_dir, read_events

    td = telemetry_dir or default_telemetry_dir(memory_dir)
    sessions: Dict[str, int] = {}
    measured_chars: List[int] = []
    try:
        for e in read_events(td):
            sid = e.get("session_id")
            if sid:
                sessions[sid] = sessions.get(sid, 0) + 1
            ic = e.get("injected_chars")
            if isinstance(ic, int) and not isinstance(ic, bool) and ic >= 0:
                measured_chars.append(ic)
    except Exception:
        pass
    if not sessions:
        return {
            "avg_events_per_session": 0.0,
            "avg_session_tokens": 0.0,
            "n_sessions": 0,
            "measured_events": 0,
        }
    avg_events = sum(sessions.values()) / len(sessions)
    if measured_chars:
        # chars/4 — _estimate_tokens' exact heuristic, applied to the measured mean.
        per_query_tokens = max(0, round((sum(measured_chars) / len(measured_chars)) / 4))
    else:
        tok = token_reduction(memory_dir, index, hard_set, k=k, index_dir=index_dir)
        per_query_tokens = tok["recall_avg"]
    return {
        "avg_events_per_session": round(avg_events, 2),
        "avg_session_tokens": round(avg_events * per_query_tokens, 1),
        "n_sessions": len(sessions),
        "measured_events": len(measured_chars),
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


# RET-7: fixture provenance header. A hard-set (or relevance-set) fixture MAY carry an
# OPTIONAL leading YAML document recording how it was generated -- e.g.
#
#   generated_with_backend: dense+bm25
#   generated_at: 2026-07-06
#   ---
#   - query: ...
#     expected: [...]
#
# This is the thing that makes "did this fixture actually exercise the dense half of hybrid
# recall, or only BM25" checkable at eval time (see `evaluate()`'s backend_mismatch below).
# The bare-list schema (no leading doc at all) keeps loading UNCHANGED -- every fixture
# written before this item, and every hand-written one that never bothers with the header,
# is still a valid fixture with metadata == {}.
def _load_fixture_docs(path: str) -> tuple:
    """Parse a hard-/relevance-set YAML file into (metadata: dict, rows: list).

    Uses ``yaml.safe_load_all`` so BOTH shapes are read with one code path:
      - bare list only               -> one document, a list          -> ({}, list)
      - mapping header + `---` + list -> two documents, mapping + list -> (mapping, list)
    A single lone mapping document (no second doc) is treated as metadata-only with no
    rows, rather than mis-parsed as a "list" of one dict -- symmetrical with the two-doc
    case rather than a special error path.
    ``([], [])``-shaped failures (missing file, unparseable YAML) return ``({}, [])`` --
    the caller's existing "arrive at an empty list" degradation, now paired with empty
    metadata rather than raising.
    """
    if not path or not os.path.exists(path):
        return {}, []
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as fh:
            docs = [d for d in yaml.safe_load_all(fh) if d is not None]
    except Exception:
        return {}, []
    if not docs:
        return {}, []
    if len(docs) == 1:
        doc = docs[0]
        if isinstance(doc, list):
            return {}, doc
        if isinstance(doc, dict):
            return doc, []
        return {}, []
    # Two+ documents: first is the metadata header, second is the row list (anything past
    # the second is ignored -- the schema only ever defines these two documents).
    meta = docs[0] if isinstance(docs[0], dict) else {}
    rows = docs[1] if isinstance(docs[1], list) else []
    return meta, rows


def load_hard_set(path: str) -> List[dict]:
    """Load ``[{query, expected: [name, ...], category}]`` from a YAML fixture. [] if missing.

    RET-8: each row may carry a ``category`` tag (canonical set in ``CATEGORIES``); a row
    without one loads as ``single-hop`` — the class every pre-RET-8 row measured — so every
    existing fixture keeps loading unchanged. The tag is data-driven, not validated against
    an enum: an unknown string forms its own ``by_category`` bucket (SIG-6's confirmed
    abstention-cluster fixtures extend the set without touching this loader).

    Ignores an optional leading metadata document (see ``_load_fixture_docs``) -- callers
    that need the provenance header use ``load_hard_set_metadata`` instead.
    """
    _meta, data = _load_fixture_docs(path)
    out: List[dict] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        q = item.get("query")
        exp = item.get("expected")
        if isinstance(exp, str):
            exp = [exp]
        if isinstance(q, str) and isinstance(exp, list) and exp:
            cat = item.get("category")
            out.append(
                {
                    "query": q,
                    "expected": [str(x) for x in exp],
                    "category": str(cat) if isinstance(cat, str) and cat.strip() else _DEFAULT_CATEGORY,
                }
            )
    return out


def load_hard_set_metadata(path: str) -> Dict[str, str]:
    """The optional provenance header (``generated_with_backend``/``generated_at``) of a
    hard-set fixture, or ``{}`` when the fixture has none / doesn't exist / fails to parse.
    """
    meta, _rows = _load_fixture_docs(path)
    return meta if isinstance(meta, dict) else {}


# --------------------------------------------------------------------------- #
# SIG-6: abstention → self-populating eval fixtures (KPI-4).
#
# RET-7 fixtures are hand-seeded, so KPI-4 measures what someone thought to test, not what
# users actually ASK. The SIG-3 abstention backlog is exactly the missing demand signal:
# recurring queries recall answered with NOTHING. Two primitives close the loop:
#
#   draft_abstention_fixtures() — at audit/consolidate time, turn each recurring cluster
#       into a CANDIDATE row in a gitignored drafts queue. A draft row's ``expected`` is
#       ALWAYS written empty: which existing memory should answer the query is a JUDGMENT
#       (the abstention has no answer by definition) — the agent proposes, a human
#       confirms. Never fabricate a memory to make a fixture pass (the killed
#       demand-gap-auto-draft); a cluster no existing memory answers is a CAPTURE gap
#       (SIG-3's own nudge), not fixture material.
#   confirm_hard_set_row()      — the per-item admission gate (inv4): validates the
#       judgment (real stems only, no duplicates) and appends ONE row, tagged
#       ``category: abstention`` (RET-8's data-driven tag), to the TRACKED project
#       fixture ``.claude/memory/.audit-fixtures/recall_hard_set.yaml`` — so the
#       per-category eval measures the gap-closing loop end-to-end.
#
# The drafts queue lives in the PENDING dir (``.claude/.memory-pending/``), NOT in
# ``.audit-fixtures/``: draft rows carry raw ``query_preview`` text from the gitignored
# telemetry ledger, and the pending queue is the shipped home for exactly that kind of
# unreviewed session-derived text (self-ignoring ``.gitignore``, SEC-3 — the capture-seed
# precedent). The tracked fixture dir stays committable because every row in it passed
# the per-item confirm step. Nothing consumes the drafts file automatically:
# ``_default_fixture_path`` probes only the canonical filenames, and an unfilled draft
# row (``expected: []``) is not even loadable by ``load_hard_set``.
# --------------------------------------------------------------------------- #
_DRAFTS_FILENAME = "recall_hard_set.drafts.yaml"
_DRAFTS_NOTE = (
    "SIG-6 candidate eval fixtures drafted from recurring recall abstentions — UNCONFIRMED. "
    "For each row: if a REAL existing memory should answer the query, put its stem in "
    "'expected' and admit the row via eval_recall.confirm_hard_set_row (per item); if no "
    "memory answers it, that is a capture gap — capture the memory first (never invent a "
    "stem to make a fixture pass), or delete the row if it is noise."
)


def _project_fixture_path(memory_dir: str, filename: str = "recall_hard_set.yaml") -> str:
    """The project-local TRACKED-fixture path (``.audit-fixtures/``, the RET-7 convention)."""
    return os.path.join(memory_dir, ".audit-fixtures", filename)


def default_drafts_path(memory_dir: str) -> str:
    """The SIG-6 drafts-queue path — inside the gitignored pending dir (see block comment)."""
    from .capture import default_pending_dir

    return os.path.join(default_pending_dir(memory_dir), _DRAFTS_FILENAME)


def _parseable_yaml(path: str) -> bool:
    """False when ``path`` exists but is not loadable YAML — the append guards below refuse
    to grow a file an agent hand-edit broke (appending after a parse error only buries it)."""
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as fh:
            list(yaml.safe_load_all(fh))
        return True
    except Exception:
        return False


def draft_abstention_fixtures(
    memory_dir: Optional[str] = None,
    *,
    telemetry_dir: Optional[str] = None,
    drafts_path: Optional[str] = None,
    index_dir: Optional[str] = None,
    k: int = 10,
    probe: bool = True,
) -> dict:
    """Turn recurring abstention clusters into CANDIDATE fixture rows in the drafts queue.

    Reads ``telemetry.abstention_backlog`` (the SIG-3 arm: recurring ``backend='none'``
    clusters) and appends one draft row per NEW cluster to the gitignored drafts file:
    ``{query, count, terms, current_hits, expected: []}``. ``current_hits`` records what
    ``recall()`` surfaces for the query NOW (the same edge-aware supplied-index call shape
    as the eval metrics) — judgment MATERIAL for the reviewing agent, never a verdict;
    ``expected`` is always written empty (the judgment is deliberately not automated — see
    the block comment above). ``probe=False`` skips the recall probes entirely (e.g. the
    audit skill's ``--skip-eval`` fast path, where a cold dense model must not be paid
    for): ``current_hits`` stays ``[]`` and no backend is claimed in the header.

    Skips clusters whose query is already a TRACKED fixture row (that loop is closed) or
    already drafted (existing draft rows — including any agent-filled ``expected`` still
    awaiting confirmation — are preserved byte-verbatim; new rows only APPEND). No new
    rows → nothing is created or touched. Refuses (``error`` key, no write) when the
    drafts file exists but no longer parses — fix or delete a hand-edit typo first.

    ``memory_dir=None`` resolves the ambient corpus; an EXPLICIT memory_dir derives the
    telemetry dir as its sibling (the ``session_token_cost`` hermeticity pattern) rather
    than re-resolving ambient state. Returns a summary dict:
    ``{path, clusters, added, kept, skipped_tracked}``.
    """
    from .telemetry import abstention_backlog, default_telemetry_dir

    if memory_dir is None:
        memory_dir, _repo = resolve_dirs()
    td = telemetry_dir or default_telemetry_dir(memory_dir)
    dp = drafts_path or default_drafts_path(memory_dir)

    clusters = abstention_backlog(td)
    tracked = {row["query"] for row in load_hard_set(_project_fixture_path(memory_dir))}
    _meta, existing_rows = _load_fixture_docs(dp)
    drafted = {(r.get("query") or "").strip() for r in existing_rows if isinstance(r, dict)}

    resolved_index_dir = index_dir or default_index_dir(memory_dir)
    idx = load_index(resolved_index_dir) if probe else None
    backend = None
    if idx is not None and len(idx):
        backend = "dense+bm25" if idx.dense_ready else "bm25-only"

    added: List[dict] = []
    skipped_tracked: List[str] = []
    for c in clusters:
        q = (c.get("sample_query") or "").strip()
        if not q:
            continue
        if q in tracked:
            skipped_tracked.append(q)
            continue
        if q in drafted:
            continue
        hits: List[str] = []
        if backend is not None:
            hits = [r["name"] for r in recall(q, k=k, index=idx, index_dir=resolved_index_dir)]
        added.append(
            {
                "query": q,
                "count": int(c.get("count") or 0),
                "terms": [str(t) for t in (c.get("terms") or [])],
                "hits": hits,
            }
        )

    summary = {
        "path": dp,
        "clusters": len(clusters),
        "added": [r["query"] for r in added],
        "kept": len(existing_rows),
        "skipped_tracked": skipped_tracked,
    }
    if not added:
        return summary
    if os.path.exists(dp) and not _parseable_yaml(dp):
        summary["added"] = []
        summary["error"] = (
            "drafts file exists but is not parseable YAML — fix or delete it before "
            "drafting more rows"
        )
        return summary

    def _row_text(r: dict) -> str:
        terms = ", ".join(json.dumps(t, ensure_ascii=False) for t in r["terms"])
        hits = ", ".join(json.dumps(h, ensure_ascii=False) for h in r["hits"])
        return (
            f"- query: {json.dumps(r['query'], ensure_ascii=False)}\n"
            f"  count: {r['count']}\n"
            f"  terms: [{terms}]\n"
            f"  current_hits: [{hits}]\n"
            f"  expected: []\n"
        )

    rows_text = "".join(_row_text(r) for r in added)
    if os.path.exists(dp):
        with open(dp, "r", encoding="utf-8") as fh:
            text = fh.read()
        if text and not text.endswith("\n"):
            text += "\n"
        text += rows_text
    else:
        # First write: SEC-3 self-ignoring dir (raw ledger queries must never be a
        # `git add .` away from a commit) + the unconfirmed-marking provenance header.
        ensure_self_ignoring_dir(os.path.dirname(dp))
        header_lines = ["draft: true", f"note: {json.dumps(_DRAFTS_NOTE, ensure_ascii=False)}"]
        if backend is not None:
            header_lines.append(f"generated_with_backend: {backend}")
        header_lines.append(f"generated_at: {time.strftime('%Y-%m-%d')}")
        text = "\n".join(header_lines) + "\n---\n" + rows_text
    from .atomic import write_text_atomic

    # INV-2: the drafts queue accumulates human judgments that re-drafting promises to
    # preserve verbatim — a torn rewrite would clobber exactly what it must keep.
    write_text_atomic(dp, text)
    return summary


def confirm_hard_set_row(
    query: str,
    expected: List[str],
    memory_dir: Optional[str] = None,
    *,
    fixture_path: Optional[str] = None,
    drafts_path: Optional[str] = None,
    category: str = "abstention",
) -> dict:
    """Admit ONE confirmed row into the TRACKED project fixture — the SIG-6 confirm gate.

    The write half of the draft→confirm loop, per-item and agent-gated (inv4): a human (or
    an operator-approved agent turn) has judged that ``expected`` — real, existing
    memories — SHOULD answer ``query``. Appends the row (tagged ``category`` — default
    ``abstention``, RET-8's data-driven tag, so unknown future tags need no loader change)
    to ``.claude/memory/.audit-fixtures/recall_hard_set.yaml`` TEXTUALLY, preserving the
    existing fixture bytes verbatim above the append (never a regenerate); creates the
    fixture (minimal ``generated_at`` header, deliberately NO backend claim — these rows
    come from traffic, not query synthesis) when the project has none yet.

    REFUSES — ``{"ok": False, "reason": ...}``, nothing written — when: the query or
    ``expected`` is empty (a no-answer cluster is a CAPTURE gap, not a fixture); any stem
    does not exist in THIS corpus (never fabricate a memory to make a fixture pass); the
    query is already tracked (dup guard); or the existing fixture no longer parses.

    On success the matching drafts-queue row (if any) is dropped, so the queue drains.
    The admitted row is deliberately NOT pre-verified against ``recall()`` — a
    currently-FAILING row is legitimate signal (the fixture documents a recall gap the
    corpus should close), and whether to admit one anyway is exactly the judgment the
    human makes at confirm time.
    """
    if memory_dir is None:
        memory_dir, _repo = resolve_dirs()
    q = (query or "").strip()
    if not q:
        return {"ok": False, "reason": "empty query"}
    stems: List[str] = []
    for s in expected if isinstance(expected, (list, tuple)) else [expected]:
        s = str(s or "").strip()
        if s.endswith(".md"):
            s = s[:-3]
        if s and s not in stems:
            stems.append(s)
    if not stems:
        return {
            "ok": False,
            "reason": "expected is empty — a cluster no existing memory answers is a "
            "capture gap (capture the memory first), not a fixture row",
        }
    bad = [s for s in stems if "/" in s or os.sep in s or s.startswith(".")]
    if bad:
        return {"ok": False, "reason": f"expected entries must be bare memory stems: {bad}"}
    missing = [s for s in stems if not os.path.exists(os.path.join(memory_dir, f"{s}.md"))]
    if missing:
        return {
            "ok": False,
            "reason": f"expected cites memories that do not exist in this corpus: {missing} "
            "— never fabricate a memory to make a fixture pass",
        }
    fp = fixture_path or _project_fixture_path(memory_dir)
    if os.path.exists(fp) and not _parseable_yaml(fp):
        return {
            "ok": False,
            "reason": "tracked fixture exists but is not parseable YAML — fix it before "
            "admitting rows",
        }
    if any(row["query"] == q for row in load_hard_set(fp)):
        return {"ok": False, "reason": "query is already a tracked fixture row"}

    cat = str(category or "").strip() or "abstention"
    row_text = (
        f"- query: {json.dumps(q, ensure_ascii=False)}\n"
        f"  expected: [{', '.join(json.dumps(s, ensure_ascii=False) for s in stems)}]\n"
        f"  category: {json.dumps(cat, ensure_ascii=False)}\n"
    )
    if os.path.exists(fp):
        with open(fp, "r", encoding="utf-8") as fh:
            text = fh.read()
        if text and not text.endswith("\n"):
            text += "\n"
        text += row_text
    else:
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        created_note = (
            "project-local recall eval fixture — rows admitted per-item via "
            "eval_recall.confirm_hard_set_row (SIG-6)"
        )
        text = (
            f"note: {json.dumps(created_note)}\n"
            f"generated_at: {time.strftime('%Y-%m-%d')}\n---\n" + row_text
        )
    from .atomic import write_text_atomic

    # INV-2: the tracked fixture is COMMITTED calibration truth (its existing bytes are
    # preserved verbatim above the append) — never leave it torn.
    write_text_atomic(fp, text)

    removed = False
    dp = drafts_path or default_drafts_path(memory_dir)
    if os.path.exists(dp):
        meta, rows = _load_fixture_docs(dp)
        keep = [
            r for r in rows if not (isinstance(r, dict) and (r.get("query") or "").strip() == q)
        ]
        if len(keep) != len(rows):
            import yaml

            parts = []
            if meta:
                parts.append(yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).rstrip("\n") + "\n---\n")
            parts.append(
                yaml.safe_dump(keep, sort_keys=False, allow_unicode=True) if keep else "[]\n"
            )
            write_text_atomic(dp, "".join(parts))  # INV-2: same drafts-queue guarantee
            removed = True
    return {
        "ok": True,
        "path": fp,
        "query": q,
        "expected": stems,
        "category": cat,
        "removed_from_drafts": removed,
    }


def hard_set_metrics(
    index: LoadedIndex,
    hard_set: List[dict],
    k: int = 10,
    *,
    index_dir: Optional[str] = None,
    ranked_source=None,
    memory_dir: Optional[str] = None,
) -> Dict[str, float]:
    """recall@k (any expected in top-k) + MRR@k (1/rank of first expected) over the set.

    MSR-2: ``ranked_source`` parameterizes WHERE the ranked list comes from — a callable
    ``(query, k) -> [{"name": ...}, ...]`` an eval arm supplies (the grep null, a scratch
    bm25-only index, the mixed/degraded condition). The HIT JUDGMENT (expected ∩ ranked,
    first-hit reciprocal rank) stays right here for every arm — no arm can reimplement
    what counts as a hit and quietly disagree with the production gates. ``None`` (every
    pre-MSR-2 caller) is the production ``recall()`` path, unchanged.
    """
    if not hard_set:
        return {"recall": 0.0, "mrr": 0.0, "n": 0}
    hit = 0
    rr_sum = 0.0
    for item in hard_set:
        expected = set(item["expected"])
        rows = (
            ranked_source(item["query"], k)
            if ranked_source is not None
            else recall(item["query"], k=k, index=index, index_dir=index_dir, memory_dir=memory_dir)
        )
        ranked = [r["name"] for r in rows]
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


def hard_set_metrics_by_category(
    index: LoadedIndex,
    hard_set: List[dict],
    k: int = 10,
    *,
    index_dir: Optional[str] = None,
    ranked_source=None,
    memory_dir: Optional[str] = None,
) -> Dict[str, Dict[str, float]]:
    """RET-8: ``hard_set_metrics`` bucketed by each row's ``category`` tag.

    ``{category: {recall, mrr, n}}``, categories sorted; a row without a tag (a hand-rolled
    list passed directly, bypassing ``load_hard_set``'s default) buckets as ``single-hop``.
    Scoring DELEGATES to ``hard_set_metrics`` per bucket — one scoring code path, so the
    per-category numbers can never disagree with the aggregate gates about what a hit is.
    This is what makes a regression ATTRIBUTABLE: the aggregate can hide a multi-hop
    collapse behind twenty healthy single-hop rows; these buckets cannot.
    ``ranked_source`` threads through verbatim (MSR-2's arms) — the bucketing and the
    judgment are arm-independent by construction.
    """
    buckets: Dict[str, List[dict]] = {}
    for item in hard_set:
        cat = item.get("category") or _DEFAULT_CATEGORY
        buckets.setdefault(cat, []).append(item)
    return {
        cat: hard_set_metrics(
            index, items, k=k, index_dir=index_dir, ranked_source=ranked_source,
            memory_dir=memory_dir,
        )
        for cat, items in sorted(buckets.items())
    }


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
    abstention_set_path: Optional[str] = None,
    gate_cold: bool = False,
    arms: bool = False,
) -> dict:
    """Run all 5 gates; return a report dict with per-gate values + pass flags.

    ``arms`` (MSR-2) opts INTO the null-hypothesis condition matrix (grep null, true
    bm25-only in a scratch index_dir, labeled mixed/degraded) — report-only per-category
    deltas under ``report["null_arms"]``. Default False: the key is ABSENT and the report
    is byte-identical to before this item (absence-emits-nothing, ED-4), and no caller
    pays the second index build unasked.

    ``repo_root``/``telemetry_dir`` feed REPORT-ONLY scorecard additions (staleness
    half-life, per-session token cost). ``relevance_set_path``/``abstention_set_path``
    feed the two RET-8-PROMOTED tracked gates (``precision@10``, ``abstention_rate``):
    omit a path and its gate SKIPS (``pass: None`` + ``skipped``, excluded from ``ok``)
    exactly like the hard-set gates on an absent fixture — so omitting all optional
    inputs reproduces the prior ``ok`` semantics; pass a path that loads EMPTY and the
    gate fails loudly, same as a truncated hard set.

    RET-8 (the premise correction this item shipped on): ``index_dir`` now threads into
    EVERY metric's ``recall()`` call. Before this, eval passed a bare preloaded index —
    the shape ``_expand_neighbors`` documents as "no edges loaded" — so the eval
    structurally measured an EDGE-BLIND variant of recall: GRA-1 expansion and GRA-4
    typed-edge penalties never ran, and a multi-hop category (or GRA-7's
    beats-GRA-1-on-multi-hop gate) could never measure anything. With the thread, the
    eval scores the production ranking path; a helper called directly with a bare index
    (hermetic tests) keeps the old edge-free behavior via ``index_dir=None``.

    MSR-5 (the RET-8-pattern repeat, usage-prior edition): ``memory_dir`` now threads
    into every metric's ``recall()`` call too. Before this, eval was USAGE-PRIOR-BLIND —
    ``_apply_salience``'s ``_usage_boost_map`` keys on recall's ``memory_dir`` argument,
    which the supplied-index helpers never passed, so a salience-ON eval could never see
    ``usage_aggregates.json`` and the A/B rig's ON arm would have measured nothing.
    Because the helpers also pass ``index=``, the SEC-1/SEC-6 trust gate and the
    user/private tier fusion stay skipped (both live inside recall()'s ``index is
    None`` branch) — the thread adds usage visibility, the COR-4 drift patch, and the
    dangling-file check, all no-ops on the unchanged corpora eval runs over (the
    salience_eval OFF-arm byte-identity self-check asserts exactly this).

    ``gate_cold`` (PRF-2/PRF-5) opts INTO gating ``cold_latency``'s p95 against
    ``GATE_COLD_P95_MS`` -- default False so cold_latency stays the report-only honesty
    signal it always was on every hermetic/ungated caller. Even when requested, the gate is
    skipped (not failed) on a BM25-only run: without dense, cold ~= warm (no per-process
    model load to amortize), so a hermetic machine gating this would be gating nothing
    real and could redden CI on a cache-less runner that never claimed to serve dense.
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
    abstention_set = load_abstention_set(abstention_set_path) if abstention_set_path else []

    # RET-7: the SERVING backend for this run, recorded so a BM25-only pass can never
    # masquerade as hybrid (dense+bm25) health -- ``index.dense_ready`` is the same
    # torn-pair-verified signal build_index.LoadedIndex already exposes (COR-3), not a
    # re-derivation, so this can never disagree with what recall() itself actually used.
    backend = "dense+bm25" if index.dense_ready else "bm25-only"
    # Fixture provenance mismatch: the hard-set fixture SAYS it was generated against a
    # dense+bm25 run (see _load_fixture_docs' metadata header), but THIS run is serving
    # bm25-only -- e.g. a cold model cache, HIPPO_DISABLE_DENSE, or fastembed missing.
    # A bm25-only pass against dense-calibrated paraphrase queries is systematically WEAKER
    # than what the fixture was tuned for (BM25 alone can't catch the cross-vocabulary
    # paraphrases dense embeddings were curated to test) -- silently reporting "PASS" here
    # would be exactly the "BM25-only masquerading as hybrid health" this item exists to
    # prevent. Only fires for a fixture that explicitly claims dense+bm25 provenance; a
    # fixture with no header (or one generated bm25-only, or one whose header claims
    # something else) never trips this -- an honest bm25-only fixture is a valid input, not
    # a mismatch.
    fixture_meta = load_hard_set_metadata(hard_set_path) if hard_set_path else {}
    backend_mismatch = (
        fixture_meta.get("generated_with_backend") == "dense+bm25" and backend != "dense+bm25"
    )

    self_recall = self_recall_at_k(index, k=k, index_dir=index_dir, memory_dir=memory_dir)
    hs = hard_set_metrics(index, hard_set, k=k, index_dir=index_dir, memory_dir=memory_dir)
    # RET-8: the same rows bucketed by category tag — regressions attributable to the
    # question class (multi-hop/temporal/update/...) instead of hidden in the aggregate.
    by_category = (
        hard_set_metrics_by_category(
            index, hard_set, k=k, index_dir=index_dir, memory_dir=memory_dir
        )
        if hard_set else {}
    )
    tok = token_reduction(memory_dir, index, hard_set, k=k, index_dir=index_dir)
    lat_queries = [item["query"] for item in hard_set] or [
        derive_self_query(e) for e in index.entries[:30]
    ]
    lat = latency(index, lat_queries, k=k, index_dir=index_dir, memory_dir=memory_dir)
    cold = cold_latency(memory_dir, index_dir, lat_queries, k=k)

    # Report-only scorecard additions (Tier 1 + Tier 2) — never feed a gate threshold above.
    # Resolve telemetry_dir ONCE here (sibling of memory_dir) and pass the SAME resolved value
    # to every consumer below -- each independently re-deriving it from None would re-resolve
    # via the ambient resolve_dirs(), which can leak onto the real repo's ledger when an
    # explicit memory_dir was passed (the same class of leak the repo_root guard above closes).
    from .telemetry import default_telemetry_dir

    resolved_telemetry_dir = telemetry_dir or default_telemetry_dir(memory_dir)
    precision = precision_at_k(index, relevance_set, k=k, index_dir=index_dir, memory_dir=memory_dir)
    half_life = staleness_half_life(memory_dir, repo_root) if repo_root else {"median_days": 0.0, "n": 0}
    sess_cost = session_token_cost(
        memory_dir, resolved_telemetry_dir, index, hard_set, k=k, index_dir=index_dir
    )
    grad = graduation_rate(resolved_telemetry_dir)
    body_probe = body_probe_recall_at_k(index, k=k, index_dir=index_dir, memory_dir=memory_dir)
    abstention = abstention_rate(index, abstention_set, k=k, index_dir=index_dir, memory_dir=memory_dir)

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
    # RET-8: the two promoted fixture-gated entries. Same skip-vs-fail split as the
    # hard-set gates above: no path provided → skipped (pass=None, excluded from `ok`);
    # a provided path that loads empty → loud FAIL (a truncated/malformed fixture is a
    # real problem, not a deliberately-absent input).
    relevance_provided = bool(relevance_set_path)
    abstention_provided = bool(abstention_set_path)
    gates["precision@10"] = {
        "value": precision["precision"], "threshold": GATE_PRECISION_AT_K,
        "pass": (precision["n"] > 0 and precision["precision"] >= GATE_PRECISION_AT_K)
        if relevance_provided else None,
        **({"skipped": True} if not relevance_provided else {}),
    }
    gates["abstention_rate"] = {
        "value": abstention["rate"], "threshold": GATE_ABSTENTION,
        "pass": (abstention["n"] > 0 and abstention["rate"] >= GATE_ABSTENTION)
        if abstention_provided else None,
        **({"skipped": True} if not abstention_provided else {}),
    }
    # PRF-2: cold_p95_ms follows the SAME skip-vs-gate shape as the hard-set/token-reduction
    # gates above (pass=None + skipped=True + a reason string, excluded from `ok`) rather than
    # a bespoke boolean -- one pattern for "this gate wasn't asked to run" across the module.
    # Two independent reasons a caller ends up skipped here:
    #   1. not requested at all (gate_cold=False, the default) -- every existing caller
    #      (hermetic suite, bare `eval_recall` invocations, doctor/audit) keeps reporting
    #      cold_latency exactly as before with zero behavior change.
    #   2. requested but serving bm25-only -- cold_latency's own docstring says cold ~= warm
    #      with dense unavailable (no per-process model load to amortize), so gating it on a
    #      hermetic/cache-less machine would be enforcing a budget against a cost that isn't
    #      actually being paid -- exactly the kind of false-negative-prone gate the hard-set
    #      skip semantics above already exist to avoid.
    if gate_cold and index.dense_ready:
        gates["cold_p95_ms"] = {
            "value": cold["p95"], "threshold": GATE_COLD_P95_MS,
            "pass": cold["n"] > 0 and cold["p95"] < GATE_COLD_P95_MS,
        }
    else:
        gates["cold_p95_ms"] = {
            "value": cold["p95"], "threshold": GATE_COLD_P95_MS,
            "pass": None,
            "skipped": True,
        }
    # MSR-2: the opt-in null-hypothesis arms — computed LAST (they re-score the same
    # hard set under other conditions; nothing above depends on them) and emitted only
    # when requested, so a flag-off report stays byte-identical.
    null_arms = (
        null_hypothesis_arms(
            memory_dir, index, index_dir, hard_set, k=k, full_by_category=by_category
        )
        if arms and hard_set
        else {}
    )
    # MSR-4: every expected-but-missed stem attributed to the mechanism + margin that
    # cut it, per category. Only missed rows are re-run (with a watched drop-log), so
    # a healthy fixture pays ~nothing; deterministic, so it rides the pass^k view.
    autopsy = (
        miss_autopsy(index, hard_set, k=k, index_dir=index_dir, memory_dir=memory_dir)
        if hard_set
        else {}
    )
    return {
        "ok": all(g["pass"] for g in gates.values() if g.get("pass") is not None),
        "dense_ready": index.dense_ready,
        "model": index.model,
        "count": len(index),
        "hard_set_n": hs["n"],
        "by_category": by_category,
        **({"miss_autopsy": autopsy} if autopsy else {}),
        **({"null_arms": null_arms} if null_arms else {}),
        "gates": gates,
        "tokens": tok,
        "latency": lat,
        "cold_latency": cold,
        "precision_at_k": precision,
        "staleness_half_life": half_life,
        "session_token_cost": sess_cost,
        "graduation_rate": grad,
        "body_probe": body_probe,
        "abstention_rate": abstention,
        # RET-7: serving backend + fixture-provenance mismatch flag (see comments above) --
        # consumed by /hippo:audit and printed on the RESULT line by main() below.
        "backend": backend,
        "backend_mismatch": backend_mismatch,
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


def _default_abstention_set_path() -> Optional[str]:
    return _default_fixture_path("recall_abstention_set.yaml")


# --------------------------------------------------------------------------- #
# MSR-1: the eval run ledger + fingerprint-keyed baseline diff + pass^k probe.
#
# RET-8 gave hippo category-tagged eval with tracked gates, but every gate is an
# ABSOLUTE frozen threshold — a regression that stays above it is invisible, no run
# persists, and nothing ever proved the deterministic metrics are actually
# deterministic. Three additions, all REPORT-ONLY (no gate constant moves, no new
# CI-failing check — the fail ratchet is explicitly deferred behind a dated owner
# blessing of the first baseline, never a metric-proxied gate):
#
#   --json / --out    serialize the full evaluate() report; --out appends it (with
#                     git-HEAD + fixture + corpus fingerprints) to a gitignored,
#                     byte-rotated run ledger in the derived telemetry dir (inv1).
#   --baseline        report-only per-gate/per-category drift vs a COMMITTED baseline
#                     file (the recall_hard_set.yaml fixture-class precedent, written
#                     via --write-baseline). Comparability is fingerprint-KEYED:
#                     a fixture/corpus fingerprint mismatch SKIPS with a loud note
#                     (different inputs are not drift); a HEAD difference is the
#                     attribution context and prints, never skips.
#   --repeat k        the pass^k determinism probe: k FRESH processes on the hermetic
#                     (HIPPO_DISABLE_DENSE=1) lane must produce byte-identical
#                     deterministic metrics (epsilon=0). Latency and every other
#                     wall-clock-derived value is excluded (see _VOLATILE_KEYS);
#                     any nonzero delta is a bug to fix, not jitter to tolerate.
# --------------------------------------------------------------------------- #
_RUN_LEDGER_NAME = "eval_runs.jsonl"
_BASELINE_FILENAME = "recall_eval_baseline.json"
_BASELINE_SCHEMA = 1
# Categories at/below this n are structurally too thin for their delta to mean much
# (today's multi-hop fixture is n=2 until GRF-2 grows it) — their drift lines carry an
# explicit low-n marker and are ALWAYS report-only, like everything else here.
_BASELINE_N_FLOOR = 3

# Report keys derived from wall-clock or ledger-external state — excluded from the
# determinism view so the pass^k claim is about the METRICS, not the machine:
#   latency/cold_latency + their two gate entries — timing;
#   staleness_half_life — ages are computed against *now*;
#   ok — folds the latency gates' pass flags in, so it inherits their volatility.
_VOLATILE_KEYS = ("latency", "cold_latency", "staleness_half_life", "ok")
_VOLATILE_GATES = ("recall_p95_ms", "cold_p95_ms")


def deterministic_view(report: dict) -> dict:
    """The report minus every wall-clock-derived value — the pass^k comparison surface.

    A deep-enough copy (top level + gates) that the caller's report is never mutated.
    """
    view = {k: v for k, v in report.items() if k not in _VOLATILE_KEYS}
    gates = report.get("gates")
    if isinstance(gates, dict):
        view["gates"] = {k: v for k, v in gates.items() if k not in _VOLATILE_GATES}
    return view


def canonical_json(view: dict) -> str:
    """One canonical byte form (sorted keys, no whitespace variance) for byte-identity."""
    return json.dumps(view, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _git_head(memory_dir: Optional[str], repo_root: Optional[str]) -> Optional[str]:
    """The corpus repo's HEAD sha, or None (non-git corpus). CLI-only — never the hot path."""
    from .provenance import run_git

    try:
        root = repo_root
        if not root and memory_dir:
            root = run_git(
                ["rev-parse", "--show-toplevel"], os.path.dirname(os.path.abspath(memory_dir))
            ).strip()
        if not root:
            return None
        return run_git(["rev-parse", "HEAD"], root).strip() or None
    except Exception:
        return None


def corpus_fingerprint(index: LoadedIndex) -> str:
    """sha256 over exactly the compare-field lists ``build_index.refresh_index`` uses to
    decide "corpus unchanged" (entry hashes, body-chunk hashes, invalid_after,
    source_commit_time, steer, confidence) — one definition of corpus identity, reused,
    so the baseline diff and the index refresh can never disagree about what "the same
    corpus" means."""
    import hashlib

    entries = index.manifest.get("entries", []) or []
    chunks = index.manifest.get("body_chunks", []) or []
    material = [
        [e.get("hash") for e in entries],
        [c.get("hash") for c in chunks],
        [e.get("invalid_after") for e in entries],
        [e.get("source_commit_time") for e in entries],
        [e.get("steer") for e in entries],
        [e.get("confidence") for e in entries],
    ]
    return hashlib.sha256(canonical_json({"corpus": material}).encode("utf-8")).hexdigest()


def fixture_fingerprint(*paths: Optional[str]) -> str:
    """sha256 over the raw bytes of every provided fixture file, position-stable.

    An absent/None path contributes a marker (not silence) so "hard set present, no
    abstention set" and "abstention set present, no hard set" can never collide."""
    import hashlib

    h = hashlib.sha256()
    for p in paths:
        if p and os.path.exists(p):
            try:
                with open(p, "rb") as fh:
                    h.update(hashlib.sha256(fh.read()).hexdigest().encode("ascii"))
            except Exception:
                h.update(b"<unreadable>")
        else:
            h.update(b"<absent>")
        h.update(b"|")
    return h.hexdigest()


def default_run_ledger_path(memory_dir: str, telemetry_dir: Optional[str] = None) -> str:
    """``<telemetry_dir>/eval_runs.jsonl`` — beside the recall ledger (derived, gitignored)."""
    from .telemetry import default_telemetry_dir

    return os.path.join(telemetry_dir or default_telemetry_dir(memory_dir), _RUN_LEDGER_NAME)


def append_run_ledger(
    report: dict,
    memory_dir: str,
    *,
    telemetry_dir: Optional[str] = None,
    head: Optional[str] = None,
    fixture_fp: Optional[str] = None,
    corpus_fp: Optional[str] = None,
    out_path: Optional[str] = None,
) -> Optional[str]:
    """Append ONE eval run (full report + fingerprints) to the gitignored run ledger.

    Same contract as the telemetry ledgers it lives beside: never raises, append-only,
    byte-rotated (``telemetry._rotate_if_needed``), SEC-3 self-ignoring dir. Returns the
    path written, or None on failure. inv1: derived telemetry, never a second authority.
    """
    from .telemetry import _rotate_if_needed

    try:
        path = out_path or default_run_ledger_path(memory_dir, telemetry_dir)
        ensure_self_ignoring_dir(os.path.dirname(path))
        row = {
            "ts": round(time.time(), 3),
            "head": head,
            "fixture_fingerprint": fixture_fp,
            "corpus_fingerprint": corpus_fp,
            "report": report,
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        _rotate_if_needed(path)
        return path
    except Exception:
        return None


def _default_baseline_path() -> Optional[str]:
    return _default_fixture_path(_BASELINE_FILENAME)


def baseline_metrics(report: dict) -> dict:
    """The comparable (deterministic, per-metric/per-category) subset a baseline pins."""
    view = deterministic_view(report)
    gates = {k: g.get("value") for k, g in (view.get("gates") or {}).items()}
    tokens = view.get("tokens") or {}
    return {
        "gates": gates,
        "by_category": view.get("by_category") or {},
        "tokens": {k: tokens.get(k) for k in ("full", "floor", "recall_avg", "net")},
        "body_probe": view.get("body_probe") or {},
        "count": view.get("count"),
        "hard_set_n": view.get("hard_set_n"),
        "backend": view.get("backend"),
    }


def write_baseline(
    report: dict,
    path: str,
    *,
    head: Optional[str],
    fixture_fp: str,
    corpus_fp: str,
) -> dict:
    """Write the committed baseline file (``--write-baseline``). ``{ok, path}`` or
    ``{ok: False, error}`` — a torn write is DETECTED (named), never a half-written pin.
    """
    from .atomic import write_json_atomic

    doc = {
        "schema": _BASELINE_SCHEMA,
        "head": head,
        "fixture_fingerprint": fixture_fp,
        "corpus_fingerprint": corpus_fp,
        "generated_at": time.strftime("%Y-%m-%d"),
        "metrics": baseline_metrics(report),
    }
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # INV-2/INV-3: the baseline is a COMMITTED fixture-class artifact — a torn pin
        # would silently re-key every future drift comparison, so the write is atomic.
        write_json_atomic(path, doc, indent=2)
        return {"ok": True, "path": path}
    except Exception as exc:
        return {"ok": False, "error": f"baseline write failed: {exc}"}


def _fmt_delta(new, old) -> str:
    try:
        d = float(new) - float(old)
    except (TypeError, ValueError):
        return f"{old!r} -> {new!r}"
    return f"{old} -> {new} ({'+' if d >= 0 else ''}{round(d, 4)})"


def diff_baseline(
    report: dict,
    baseline: dict,
    *,
    head: Optional[str],
    fixture_fp: str,
    corpus_fp: str,
) -> List[str]:
    """Report-only drift lines vs a committed baseline. NEVER affects the exit code.

    Comparability is keyed on the fixture + corpus fingerprints: a mismatch means the
    INPUTS changed (different corpus / different fixtures), so per-metric deltas would
    compare apples to oranges — the diff SKIPS, loudly naming which key moved. A HEAD
    difference is the whole point (code drift between two runs of the same inputs) and
    prints as attribution context.
    """
    lines: List[str] = []
    if not isinstance(baseline, dict) or baseline.get("schema") != _BASELINE_SCHEMA:
        return [
            "baseline: SKIPPED — unrecognized baseline schema "
            f"(want {_BASELINE_SCHEMA}, got {baseline.get('schema') if isinstance(baseline, dict) else type(baseline).__name__})"
        ]
    mismatched = [
        key
        for key, current in (
            ("fixture_fingerprint", fixture_fp),
            ("corpus_fingerprint", corpus_fp),
        )
        if baseline.get(key) != current
    ]
    if mismatched:
        return [
            "baseline: SKIPPED — "
            + " and ".join(mismatched)
            + " changed since the baseline was written; the numbers are not comparable "
            "(different inputs, not drift). Re-pin with --write-baseline after reviewing.",
        ]
    b_head = baseline.get("head")
    lines.append(
        f"baseline: comparing HEAD {(head or 'no-git')[:12]} against baseline "
        f"@ {(b_head or 'no-git')[:12]} (written {baseline.get('generated_at') or '?'})"
    )
    old = baseline.get("metrics") or {}
    new = baseline_metrics(report)
    drift = 0
    old_gates = old.get("gates") or {}
    new_gates = new.get("gates") or {}
    for gname in sorted(set(old_gates) | set(new_gates)):
        if old_gates.get(gname) != new_gates.get(gname):
            drift += 1
            lines.append(f"  gate {gname}: {_fmt_delta(new_gates.get(gname), old_gates.get(gname))}")
    old_cat = old.get("by_category") or {}
    new_cat = new.get("by_category") or {}
    for cat in sorted(set(old_cat) | set(new_cat)):
        o, n = old_cat.get(cat) or {}, new_cat.get(cat) or {}
        if o == n:
            continue
        drift += 1
        n_floor = min(x for x in (o.get("n"), n.get("n")) if isinstance(x, (int, float))) if (o.get("n") is not None or n.get("n") is not None) else 0
        low_n = " [low n — report-only]" if (n_floor or 0) <= _BASELINE_N_FLOOR else ""
        lines.append(
            f"  category {cat}: recall {_fmt_delta(n.get('recall'), o.get('recall'))}, "
            f"mrr {_fmt_delta(n.get('mrr'), o.get('mrr'))}, n {o.get('n')}->{n.get('n')}{low_n}"
        )
    for scalar in ("count", "hard_set_n", "backend"):
        if old.get(scalar) != new.get(scalar):
            drift += 1
            lines.append(f"  {scalar}: {old.get(scalar)!r} -> {new.get(scalar)!r}")
    if old.get("tokens") != new.get("tokens"):
        drift += 1
        lines.append(f"  tokens: {old.get('tokens')} -> {new.get('tokens')}")
    if old.get("body_probe") != new.get("body_probe"):
        drift += 1
        lines.append(f"  body_probe: {old.get('body_probe')} -> {new.get('body_probe')}")
    if not drift:
        lines.append("  no drift — deterministic metrics match the committed baseline.")
    lines.append(
        "  (report-only: baseline drift never fails a run; the CI ratchet stays deferred "
        "behind a dated owner blessing of the first baseline)"
    )
    return lines


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


# --------------------------------------------------------------------------- #
# GRF-3: the dense-floor calibration sweep — RET-9's missing calibration half.
#
# recall._DENSE_FLOOR_BY_MODEL is a static table calibrated on the maintainer's golden
# corpus; doctor.check_abstention_floor_sanity (RET-9's leak-detector half, shipped
# 2026-07-10) can SAY "the floor is too permissive for this corpus" but not what number
# to raise it to. This sweep automates RET-1's documented cosine-separation recipe:
# embed the corpus's own on-topic queries and off-topic probes with its configured/warm
# model, take each query's best DESCRIPTION-row cosine — the exact value the floor
# gates in recall._dense_rank_rows — and recommend a per-model/per-corpus floor from
# the separation of the two distributions. RAW cosine space throughout, never fused
# RET-8 metrics (the two logged fused-vs-cosine incommensurability corrections).
#
# Advisory-only by construction (inv4): the sweep recommends, one doctor line compares
# the recommendation to the configured entry, and a HUMAN edits the table (or sets
# HIPPO_DENSE_FLOOR). Nothing here writes a floor anywhere. The persisted report is
# derived/gitignored telemetry (inv1), keyed to the corpus fingerprint so doctor can
# tell a stale sweep from a fresh one. Ettin/Li-LSR reranker arms are explicitly out
# of scope (ED-3-blocked — see the roadmap's not_pursuing).
# --------------------------------------------------------------------------- #
_FLOOR_SWEEP_NAME = "floor_sweep.json"
_FLOOR_SWEEP_SCHEMA = 1


def default_floor_sweep_path(memory_dir: str, telemetry_dir: Optional[str] = None) -> str:
    """``<telemetry_dir>/floor_sweep.json`` — beside the run ledger (derived, gitignored)."""
    from .telemetry import default_telemetry_dir

    return os.path.join(telemetry_dir or default_telemetry_dir(memory_dir), _FLOOR_SWEEP_NAME)


def recommend_floor(on_scores: List[float], off_scores: List[float]) -> Optional[dict]:
    """Pure separation math over raw cosines. ``None`` when either side is empty.

    Clean separation (every on-topic max above every off-topic max): recommend the
    midpoint of the gap. Overlap: recommend the 10th-percentile on-topic score — the
    conservative "keep ~90% of real hits admitted" point — and report the leak/cut
    counts at that floor so the human sees exactly what the overlap costs. Either way
    ``safety_delta`` = recommendation − best off-topic cosine: positive means every
    off-topic probe stays below the recommended floor; negative names the leak margin.
    """
    if not on_scores or not off_scores:
        return None
    on = sorted(float(s) for s in on_scores)
    off = sorted(float(s) for s in off_scores)
    on_min, off_max = on[0], off[-1]
    overlap = on_min <= off_max
    if not overlap:
        recommended = round((on_min + off_max) / 2.0, 4)
    else:
        p10 = max(0, min(len(on) - 1, int(len(on) * 0.10)))
        recommended = round(on[p10], 4)
    return {
        "recommended": recommended,
        "overlap": overlap,
        "on_n": len(on),
        "off_n": len(off),
        "on_min": round(on_min, 4),
        "off_max": round(off_max, 4),
        "safety_delta": round(recommended - off_max, 4),
        "leaked_off": sum(1 for s in off if s >= recommended),
        "cut_on": sum(1 for s in on if s < recommended),
    }


def _raw_max_cosines(index: LoadedIndex, queries: List[str]) -> List[float]:
    """Best DESCRIPTION-row cosine per query — the exact quantity the dense floor gates.

    Embeds with the corpus's configured/warm model via ``recall.embed_query`` — resolved
    through the module attribute so hermetic tests' fake embedders apply (offline; the
    caller has already verified ``dense_ready``). A query that fails to embed is skipped
    (better a smaller honest sample than a fabricated zero)."""
    from . import recall as _recall_mod

    out: List[float] = []
    n_desc = len(index.entries)
    for q in queries:
        if not q:
            continue
        try:
            qvec = _recall_mod.embed_query(q, allow_download=False)
            sims = index.dense @ qvec
            out.append(round(float(sims[:n_desc].max()), 6))
        except Exception:
            continue
    return out


def floor_sweep(
    memory_dir: Optional[str] = None,
    index_dir: Optional[str] = None,
    hard_set_path: Optional[str] = None,
    abstention_set_path: Optional[str] = None,
    *,
    telemetry_dir: Optional[str] = None,
    write: bool = True,
) -> dict:
    """Run the calibration sweep; persist the report for doctor; return it.

    On-topic queries: the hard-set rows (non-abstention categories) whose expected
    stems actually exist in THIS corpus — a row whose answer the corpus lacks would
    drag the on-topic minimum down with an honest-but-irrelevant low cosine. Off-topic
    probes: the abstention set. Both resolve through the same default-fixture paths
    ``evaluate()`` uses, so a project's ``.audit-fixtures/`` rows take precedence when
    present. Loud, structured failure (``{"ok": False, "error": ...}``) when the dense
    model is unavailable — a sweep cannot calibrate a floor it cannot measure.
    """
    if memory_dir is None:
        memory_dir, _ = resolve_dirs()
    if index_dir is None:
        index_dir = default_index_dir(memory_dir)
    index = load_index(index_dir)
    if index is None or not len(index):
        return {"ok": False, "error": "no index / empty corpus — build the index first"}
    if not index.dense_ready or index.dense is None:
        return {
            "ok": False,
            "error": "dense model unavailable (bm25-only run) — the floor gates raw "
            "cosines, so the sweep needs the dense backend; run /hippo:bootstrap first",
        }

    hs_path = hard_set_path or _default_hard_set_path()
    ab_path = abstention_set_path or _default_abstention_set_path()
    hard_set = load_hard_set(hs_path) if hs_path else []
    probes = load_abstention_set(ab_path) if ab_path else []
    names = {e.get("name") for e in index.entries}
    on_queries = [
        row["query"]
        for row in hard_set
        if (row.get("category") or _DEFAULT_CATEGORY) != "abstention"
        and any(stem in names for stem in row.get("expected") or ())
    ]
    if not on_queries or not probes:
        return {
            "ok": False,
            "error": "need both on-topic hard-set rows resolvable against this corpus and "
            "off-topic abstention probes — "
            f"(on-topic {len(on_queries)}, off-topic {len(probes)}); draft fixtures via "
            "/hippo:audit or SIG-6's abstention_fixtures flow",
        }

    from .recall import _dense_floor

    on_scores = _raw_max_cosines(index, on_queries)
    off_scores = _raw_max_cosines(index, probes)
    rec = recommend_floor(on_scores, off_scores)
    if rec is None:
        return {"ok": False, "error": "embedding produced no usable scores — model failure?"}

    doc = {
        "ok": True,
        "schema": _FLOOR_SWEEP_SCHEMA,
        "model": index.model,
        "configured_floor": _dense_floor(index.model),
        "corpus_fingerprint": corpus_fingerprint(index),
        "generated_at": time.strftime("%Y-%m-%d"),
        **rec,
    }
    if write:
        path = default_floor_sweep_path(memory_dir, telemetry_dir)
        written = write_floor_sweep(doc, path)
        doc["path"] = written.get("path") if written.get("ok") else None
    return doc


def write_floor_sweep(doc: dict, path: str) -> dict:
    """Persist the sweep report (atomic — a torn report must never half-inform doctor).
    ``{ok, path}`` or ``{ok: False, error}``; never raises."""
    from .atomic import write_json_atomic

    try:
        ensure_self_ignoring_dir(os.path.dirname(path))  # SEC-3 self-ignoring pattern
        write_json_atomic(path, doc, indent=2)
        return {"ok": True, "path": path}
    except Exception as exc:
        return {"ok": False, "error": f"floor-sweep write failed: {exc}"}


def read_floor_sweep(memory_dir: str, telemetry_dir: Optional[str] = None) -> Optional[dict]:
    """The persisted sweep report, or None (absent/corrupt/wrong-schema). Never raises."""
    try:
        path = default_floor_sweep_path(memory_dir, telemetry_dir)
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict) or doc.get("schema") != _FLOOR_SWEEP_SCHEMA:
            return None
        return doc
    except Exception:
        return None


def _forwarded_eval_argv(args) -> List[str]:
    """The INPUT arguments a --repeat subprocess re-runs with — never the output flags
    (--json is added by the probe itself; --out/--baseline/--write-baseline would make
    the probe's fresh processes write ledgers/pins as a side effect)."""
    argv: List[str] = []
    for flag, value in (
        ("--memory-dir", args.memory_dir),
        ("--index-dir", args.index_dir),
        ("--hard-set", args.hard_set),
        ("--relevance-set", args.relevance_set),
        ("--abstention-set", args.abstention_set),
        ("--repo-root", args.repo_root),
        ("--telemetry-dir", args.telemetry_dir),
    ):
        if value:
            argv.extend([flag, value])
    if args.k != 10:
        argv.extend(["-k", str(args.k)])
    if getattr(args, "arms", False):
        argv.append("--arms")  # shapes the report deterministically — must repeat too
    return argv


def run_repeat_probe(args, repeat: int) -> int:
    """pass^k: ``repeat`` FRESH interpreters run the same eval on the hermetic lane;
    their deterministic metric views must be byte-identical. Exit 0 on pass, 1 on any
    delta (a nonzero delta is a bug to fix, not jitter to tolerate) or probe failure.
    """
    import subprocess
    import sys as _sys

    if repeat < 2:
        print("--repeat needs k >= 2 (one run has nothing to compare against)")
        return 2
    _pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {
        **os.environ,
        # The hermetic lane: byte-identity is only claimable where no model-load /
        # cache-warmth variance exists. Offline flags match cold_latency's probe.
        "HIPPO_DISABLE_DENSE": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    env["PYTHONPATH"] = _pkg_parent + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    argv = [_sys.executable, "-m", "memory.eval_recall", "--json"] + _forwarded_eval_argv(args)
    blobs: List[str] = []
    for i in range(repeat):
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=600, env=env)
        line = next((ln for ln in (proc.stdout or "").splitlines() if ln.strip()), "")
        try:
            report = json.loads(line)
        except Exception:
            print(
                f"--repeat: run {i + 1}/{repeat} produced no parseable --json report "
                f"(exit {proc.returncode}); stderr tail: {(proc.stderr or '')[-300:]}"
            )
            return 1
        blobs.append(canonical_json(deterministic_view(report)))
        print(f"  run {i + 1}/{repeat}: {len(blobs[-1])} canonical bytes")
    if all(b == blobs[0] for b in blobs):
        print(
            f"pass^{repeat}: deterministic metrics byte-identical across {repeat} fresh "
            "processes (hermetic lane; latency/staleness excluded by definition)"
        )
        return 0
    first_bad = next(i for i, b in enumerate(blobs) if b != blobs[0])
    a, b = json.loads(blobs[0]), json.loads(blobs[first_bad])
    diverged = sorted(
        k for k in set(a) | set(b) if a.get(k) != b.get(k)
    )
    print(
        f"pass^{repeat} FAILED: run {first_bad + 1} diverged from run 1 in deterministic "
        f"key(s): {', '.join(diverged)} — epsilon=0 is the contract; this is a bug to fix, "
        "not jitter to tolerate."
    )
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate the memory recall gates.")
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--index-dir", default=None)
    parser.add_argument("--hard-set", default=None)
    parser.add_argument("--relevance-set", default=None)
    parser.add_argument(
        "--abstention-set",
        default=None,
        help="RET-1/RET-8: fixture of clearly off-topic queries — measures the fraction "
        "recall() correctly abstains (returns []) on. Tracked gate when provided "
        "(GATE_ABSTENTION); skipped, never failed, when absent.",
    )
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--telemetry-dir", default=None)
    parser.add_argument(
        "--gate-cold",
        action="store_true",
        help="PRF-2/PRF-5: gate cold_latency's p95 tail (fresh-subprocess-per-sample, the honest "
        "per-prompt cost) against GATE_COLD_P95_MS. Off by default so cold_latency stays a "
        "report-only signal everywhere except CI's dense lane, which restores a warm model "
        "cache and passes this flag so a real cold-path regression fails the build. Skipped "
        "(not failed) on a bm25-only run -- without dense, cold ~= warm.",
    )
    parser.add_argument("-k", type=int, default=10)
    # MSR-1: the run-ledger / baseline / determinism surface — ALL report-only (the CI
    # fail ratchet is deferred behind a dated owner blessing; no gate constant moves).
    parser.add_argument(
        "--json",
        action="store_true",
        help="MSR-1: print the full evaluate() report as ONE JSON line instead of the "
        "human gate table (exit code semantics unchanged). Any --out/--baseline notes "
        "print on later lines — machine consumers parse the first line.",
    )
    parser.add_argument(
        "--out",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="MSR-1: append this run (full report + git-HEAD/fixture/corpus fingerprints) "
        "to the gitignored run ledger — default <telemetry-dir>/eval_runs.jsonl, or an "
        "explicit PATH. Append-only, byte-rotated, never affects the exit code.",
    )
    parser.add_argument(
        "--baseline",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="MSR-1: report-only drift vs a committed baseline (default: this corpus's "
        ".audit-fixtures/recall_eval_baseline.json, falling back to the repo fixture on "
        "an ambient run). Fingerprint mismatch skips loudly; drift NEVER fails the run.",
    )
    parser.add_argument(
        "--write-baseline",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="MSR-1: pin THIS run's deterministic metrics as the committed baseline file "
        "(atomic write). Committing it — and any CI ratchet over it — is a deliberate, "
        "dated owner decision, never automatic.",
    )
    parser.add_argument(
        "--arms",
        action="store_true",
        help="MSR-2: run the null-hypothesis condition matrix — a grep/token-overlap "
        "null (a ranking-stack-lift measure, NOT an adoption threshold), a TRUE "
        "bm25-only arm (second index in a scratch dir), and the explicitly-labeled "
        "mixed/degraded arm (dense resident, bm25 at query time). Report-only "
        "per-category deltas vs the full pipeline; no gate ships on any of them.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=None,
        metavar="K",
        help="MSR-1: the pass^k determinism probe — run the same eval K times in FRESH "
        "processes on the hermetic lane (HIPPO_DISABLE_DENSE=1) and assert byte-identity "
        "of the deterministic metrics (latency/staleness excluded). Exit 1 on any delta.",
    )
    parser.add_argument(
        "--ab",
        default=None,
        metavar="FLAG",
        help="run a paired A/B toggling ONLY the named flag. Whitelist: HIPPO_DREAM "
        "(DRM-3 — the /dream snapshot-diff harness, memory.dream_eval; extra args pass "
        "through, e.g. --ab HIPPO_DREAM --live) and HIPPO_SALIENCE (MSR-5 — the ED-2 "
        "salience-revisit rig, memory.salience_eval: OFF/ON/OFF over the live corpus, "
        "per-category deltas to the gitignored dir; MEASURES ONLY, the default stays "
        "owner-decided-OFF).",
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="RET-15: grid-search HIPPO_KNEE_RATIO/HIPPO_DENSE_FLOOR against this same "
        "--memory-dir/--hard-set/--abstention-set (memory.calibrate_thresholds) instead of "
        "running the normal gate report. Report-only — never mutates recall.py's default.",
    )
    parser.add_argument(
        "--reachability",
        action="store_true",
        help="GRF-4: the typed-2-hop reachability audit — per multi-hop row, the min "
        "hop depth (0/1/2/unreachable) at which each expected stem is reachable from "
        "the row's top-3 seeds over links.json, and the edge kind of the first hop. "
        "GRA-7's PPR gate must beat THIS baseline. Pure offline walk; print-only; "
        "skips below the grown n>=10 multi-hop fixture. Authorizes NO hot-path "
        "depth-2 mechanism.",
    )
    parser.add_argument(
        "--floor-sweep",
        action="store_true",
        help="GRF-3 (delivers RET-9's calibration half): recommend a per-model/per-corpus "
        "dense floor from the RAW-cosine separation of on-topic hard-set queries vs "
        "off-topic abstention probes (never fused metrics — complementary to --calibrate's "
        "end-to-end grid). Persists the report for doctor's advisory comparison line. "
        "Advisory only: a human edits recall._DENSE_FLOOR_BY_MODEL (or sets "
        "HIPPO_DENSE_FLOOR); nothing auto-writes.",
    )
    args, ab_extra = parser.parse_known_args(argv)
    if args.ab is None and ab_extra:
        # Extras are pass-through ONLY under --ab; the plain eval keeps strict parsing.
        parser.error(f"unrecognized arguments: {' '.join(ab_extra)}")

    # DRM-3 (owner decision 3, 2026-07-12): the --ab whitelist dispatch. Each flag owns a
    # self-contained harness module; eval_recall stays the one CLI front door.
    if args.ab is not None:
        from .dream_eval import AB_FLAGS
        from .dream_eval import main as _dream_ab_main

        if args.ab not in AB_FLAGS:
            print(f"eval --ab: unknown flag {args.ab!r} (whitelist: {', '.join(AB_FLAGS)}).")
            return 2
        if args.ab == "HIPPO_SALIENCE":
            # MSR-5: the ED-2 salience-revisit rig (memory.salience_eval) — measures
            # only, never flips the default. Forward the eval-level corpus/fixture
            # args (they are parsed HERE, so they never appear in ab_extra).
            from .salience_eval import main as _salience_ab_main

            fwd: List[str] = list(ab_extra or [])
            ambient = args.memory_dir is None
            if args.memory_dir:
                fwd += ["--memory-dir", args.memory_dir]
            if args.index_dir:
                fwd += ["--index-dir", args.index_dir]
            hs = args.hard_set or (_default_hard_set_path() if ambient else None)
            if hs:
                fwd += ["--hard-set", hs]
            if args.telemetry_dir:
                fwd += ["--telemetry-dir", args.telemetry_dir]
            if args.k != 10:
                fwd += ["-k", str(args.k)]
            return _salience_ab_main(fwd)
        return _dream_ab_main((ab_extra or []) + (["-k", str(args.k)] if args.k != 10 else []))

    # MSR-1: the pass^k probe is its own mode (like --calibrate) — it spawns fresh
    # processes that each run the plain eval with --json; output flags never forward.
    if args.repeat is not None:
        return run_repeat_probe(args, args.repeat)

    if args.calibrate:
        from .calibrate_thresholds import format_report as _calibrate_report

        ambient = args.memory_dir is None
        print(
            _calibrate_report(
                memory_dir=args.memory_dir,
                index_dir=args.index_dir,
                hard_set_path=args.hard_set or (_default_hard_set_path() if ambient else None),
                abstention_set_path=args.abstention_set
                or (_default_abstention_set_path() if ambient else None),
                k=args.k,
            )
        )
        return 0

    if args.reachability:
        ambient = args.memory_dir is None
        hs_path = args.hard_set or (_default_hard_set_path() if ambient else None)
        if args.memory_dir is None:
            _md, _ = resolve_dirs()
        else:
            _md = args.memory_dir
        _idx = args.index_dir or default_index_dir(_md)
        index = load_index(_idx)
        if index is None or not len(index):
            print("reachability: no index / empty corpus")
            return 1
        hard_set = load_hard_set(hs_path) if hs_path else []
        audit = reachability_audit(index, hard_set, _idx, k=args.k, memory_dir=_md)
        if audit.get("skipped"):
            print(f"reachability: SKIPPED — {audit['skipped']}")
            return 0
        s = audit["summary"]
        print(
            f"typed-2-hop reachability (GRA-7's baseline arm; seeds/row={s['seeds_per_row']}): "
            f"{s['expected_stems']} expected stem(s) — {s['seed_rank_0']} ranked as a seed, "
            f"{s['reachable_at_1']} reachable at 1 hop, {s['reachable_at_2']} at 2 hops, "
            f"{s['unreachable']} unreachable"
        )
        for r in audit["rows"]:
            d = "unreachable" if r["depth"] is None else f"depth {r['depth']}"
            via = f" via {r['via']}" if r["via"] not in (None, "-") else ""
            print(f"  {d:<12} {r['stem']}{via} — \"{r['query']}\"")
        print(
            "  (offline links.json walk — a baseline for GRA-7's gate, NOT a shipped "
            "depth-2 mechanism)"
        )
        return 0

    if args.floor_sweep:
        ambient = args.memory_dir is None
        doc = floor_sweep(
            memory_dir=args.memory_dir,
            index_dir=args.index_dir,
            hard_set_path=args.hard_set or (_default_hard_set_path() if ambient else None),
            abstention_set_path=args.abstention_set
            or (_default_abstention_set_path() if ambient else None),
            telemetry_dir=args.telemetry_dir,
        )
        if not doc.get("ok"):
            print(f"floor sweep: {doc.get('error')}")
            return 1
        sep = "OVERLAPPING" if doc["overlap"] else "clean"
        print(
            f"floor sweep [{doc['model']}]: recommended {doc['recommended']} "
            f"(configured {doc['configured_floor']}) — {sep} separation over "
            f"{doc['on_n']} on-topic / {doc['off_n']} off-topic cosines "
            f"(on-min {doc['on_min']}, off-max {doc['off_max']}, "
            f"safety Δ {doc['safety_delta']:+})"
        )
        if doc["overlap"]:
            print(
                f"  overlap cost at the recommendation: {doc['leaked_off']} off-topic "
                f"probe(s) would leak, {doc['cut_on']} on-topic quer(ies) would abstain"
            )
        if doc.get("path"):
            print(f"  persisted for doctor: {doc['path']}")
        print(
            "  advisory only — edit recall._DENSE_FLOOR_BY_MODEL (or set "
            "HIPPO_DENSE_FLOOR) yourself; nothing auto-writes (RET-9 closed by this sweep)"
        )
        return 0

    # RET-8 hermeticity guard, the CLI twin of evaluate()'s memory_dir guard: the ambient
    # default fixtures (this repo's tests/fixtures, or the resolved project's
    # .audit-fixtures) calibrate the AMBIENT corpus. Scoring them against an explicitly
    # overridden --memory-dir would judge one corpus by another corpus's fixtures —
    # harmless while precision/abstention were report-only, a false gate verdict now that
    # they (and the hard-set gates they sit beside) are tracked. Explicit --memory-dir →
    # only explicitly-passed fixtures run; the fixtureless gates skip, exactly as a
    # fixture-less fresh project skips them.
    ambient = args.memory_dir is None
    # MSR-1: the fixture paths are hoisted so the fingerprints below hash EXACTLY the
    # inputs evaluate() scored — a drifted copy of this resolution would let the baseline
    # key disagree with the run it claims to describe.
    hard_set_path = args.hard_set or (_default_hard_set_path() if ambient else None)
    relevance_set_path = args.relevance_set or (
        _default_relevance_set_path() if ambient else None
    )
    abstention_set_path = args.abstention_set or (
        _default_abstention_set_path() if ambient else None
    )
    report = evaluate(
        memory_dir=args.memory_dir,
        index_dir=args.index_dir,
        hard_set_path=hard_set_path,
        k=args.k,
        relevance_set_path=relevance_set_path,
        repo_root=args.repo_root,
        telemetry_dir=args.telemetry_dir,
        abstention_set_path=abstention_set_path,
        gate_cold=args.gate_cold,
        arms=args.arms,
    )
    if not report.get("ok") and "error" in report:
        if args.json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            print(f"eval error: {report['error']}")
        return 1

    # MSR-1: --json prints the full report as ONE machine-parseable line and skips the
    # human table; --out/--baseline/--write-baseline notes (below) print on later lines.
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
        return _handle_run_outputs(
            args, report, ambient, hard_set_path, relevance_set_path, abstention_set_path
        )
    # RET-7: `backend` is printed on the gate-header line itself (not just buried in the
    # dict) -- the whole point is that a BM25-only pass must be visibly labeled every time
    # someone actually reads the CLI output, not just discoverable by someone who thinks to
    # inspect the report dict.
    print(
        f"corpus={report['count']} dense={report['dense_ready']} model={report['model']} "
        f"hard_set={report['hard_set_n']} backend={report['backend']}"
    )
    if report.get("backend_mismatch"):
        # LOUD by design (see evaluate()'s comment) -- this fixture was generated_with_backend:
        # dense+bm25 but this run only served bm25-only, so ANY pass below is calibrated
        # against a stronger backend than what actually ran. Printed before the gate table so
        # it can't be missed/scrolled past.
        print(
            "  ⚠️  BACKEND MISMATCH: hard-set fixture was generated_with_backend=dense+bm25, "
            "but this run served bm25-only — a PASS here does NOT prove hybrid recall works, "
            "only that BM25 alone can pass a dense-calibrated fixture (or that dense degraded "
            "silently -- check the fastembed model cache / HIPPO_DISABLE_DENSE)."
        )
    _SKIP_REASONS = {
        "hard_recall@10": "no hard-set fixture",
        "mrr@10": "no hard-set fixture",
        "token_reduction": "no MEMORY.full.md pre-trim snapshot",
        "precision@10": "no relevance-set fixture",
        "abstention_rate": "no abstention-set fixture",
        "cold_p95_ms": (
            "not requested (--gate-cold)"
            if not args.gate_cold
            else "bm25-only — cold ~= warm without dense; hermetic machines must not redden"
        ),
    }
    for name, g in report["gates"].items():
        skipped = g.get("pass") is None
        mark = "➖" if skipped else ("✅" if g["pass"] else "❌")
        extra = f" ({g['pct']*100:.1f}% reduction)" if name == "token_reduction" else ""
        if skipped:
            extra += f" — skipped ({_SKIP_REASONS.get(name, 'input absent')}; excluded from RESULT)"
        print(f"  {mark} {name:18s} = {g['value']} (threshold {g['threshold']}){extra}")
    # RET-8: the per-category breakdown — the line that makes a regression attributable.
    # One line per category present in the hard set; single-category (all-default) fixtures
    # print it too, so the output shape doesn't shift when the first tagged row arrives.
    for cat, m in (report.get("by_category") or {}).items():
        print(
            f"  category {cat:11s} recall@{args.k}={m['recall']:.4f} mrr@{args.k}={m['mrr']:.4f} "
            f"n={m['n']} (RET-8)"
        )
    # MSR-4: the per-category miss autopsy — a missed stem names the mechanism and
    # margin that cut it, instead of just deflating a category average.
    for cat, misses in sorted((report.get("miss_autopsy") or {}).items()):
        for m in misses:
            margin = f", margin {m['margin']}" if m.get("margin") is not None else ""
            score = f" (score {m['score']}{margin})" if m.get("score") is not None else ""
            print(
                f"  miss {cat}: `{m['stem']}` cut by {m['reason']}{score} — "
                f"query \"{m['query']}\" (MSR-4)"
            )
    # MSR-2: the null-hypothesis arm deltas — every arm explicitly labeled, every
    # category's n printed, everything report-only (no arm feeds a gate).
    na = report.get("null_arms") or {}
    for arm_key in ("grep", "bm25", "mixed"):
        arm = (na.get("arms") or {}).get(arm_key)
        if not arm:
            continue
        if arm.get("skipped"):
            print(f"  arm {arm_key}: skipped — {arm['skipped']} (MSR-2)")
            continue
        note = f" [{arm['note']}]" if arm.get("note") else ""
        print(f"  arm {arm_key}: {arm['label']}{note} (MSR-2, report-only)")
        for cat, d in sorted((na.get("deltas") or {}).get(arm_key, {}).items()):
            print(
                f"    {cat}: Δrecall={d['recall']:+.4f} Δmrr={d['mrr']:+.4f} n={d['n']} "
                "(vs full pipeline)"
            )
    t = report["tokens"]
    print(f"  tokens: full={t['full']} floor={t['floor']} recall_avg={t['recall_avg']} net={t['net']}")
    print(f"  latency (warm): p50={report['latency']['p50']}ms p95={report['latency']['p95']}ms n={report['latency']['n']}")
    c = report.get("cold_latency") or {}
    if c.get("n"):
        print(
            f"  latency (cold, per-process model load): p50={c['p50']}ms p95={c.get('p95')}ms "
            f"max={c['max']}ms n={c['n']} — the REAL hook cost; the warm p95 above understates it "
            "(p95 is what --gate-cold gates, PRF-5)"
        )

    # Report-only scorecard additions (Tier 1, memory-organism-instrument-immunize) — none
    # of THESE feed a gate threshold; they exist to MEASURE, not to merge-block. (precision
    # and abstention_rate left this block for the gate table above — RET-8.)
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
    # RET-7: the RESULT line always names the serving backend -- e.g.
    #   RESULT: ALL GATES PASS ✅ [backend=bm25-only — dense path unverified]
    # so a bm25-only pass can never be skimmed as "hybrid recall verified" from this one
    # line alone, which is the line most CI logs / terminals actually surface.
    backend = report.get("backend", "unknown")
    if backend == "dense+bm25":
        backend_note = "[backend=dense+bm25]"
    else:
        backend_note = f"[backend={backend} — dense path unverified]"
    if report.get("backend_mismatch"):
        backend_note += " [FIXTURE/BACKEND MISMATCH]"
    print("RESULT:", ("ALL GATES PASS ✅" if report["ok"] else "GATE FAILURE ❌"), backend_note)
    return _handle_run_outputs(
        args, report, ambient, hard_set_path, relevance_set_path, abstention_set_path
    )


def _handle_run_outputs(
    args,
    report: dict,
    ambient: bool,
    hard_set_path: Optional[str],
    relevance_set_path: Optional[str],
    abstention_set_path: Optional[str],
) -> int:
    """MSR-1: the --out / --write-baseline / --baseline tail of a ``main()`` run.

    Returns the process exit code. The DEFAULT path is untouched semantics —
    ``0 if report["ok"] else 1`` — and baseline DRIFT never changes it (report-only);
    only a loud input/IO failure (an explicitly named baseline that is missing or
    unparseable, a failed pin write) overrides to 1.
    """
    wants_ledger = args.out is not None
    wants_baseline = args.baseline is not None
    wants_pin = args.write_baseline is not None
    default_exit = 0 if report["ok"] else 1
    if not (wants_ledger or wants_baseline or wants_pin):
        return default_exit

    resolved_md = args.memory_dir
    resolved_repo = args.repo_root
    if resolved_md is None:
        resolved_md, rr = resolve_dirs()
        if resolved_repo is None:
            resolved_repo = rr
    resolved_idx = args.index_dir or default_index_dir(resolved_md)
    idx = load_index(resolved_idx)
    corpus_fp = corpus_fingerprint(idx) if idx is not None else "<no-index>"
    fixture_fp = fixture_fingerprint(hard_set_path, relevance_set_path, abstention_set_path)
    head = _git_head(resolved_md, resolved_repo)

    if wants_ledger:
        written = append_run_ledger(
            report,
            resolved_md,
            telemetry_dir=args.telemetry_dir,
            head=head,
            fixture_fp=fixture_fp,
            corpus_fp=corpus_fp,
            out_path=args.out or None,
        )
        print(
            f"run ledger: appended to {written}"
            if written
            else "run ledger: write failed (report unaffected)"
        )

    if wants_pin:
        pin_path = args.write_baseline or _project_fixture_path(resolved_md, _BASELINE_FILENAME)
        res = write_baseline(
            report, pin_path, head=head, fixture_fp=fixture_fp, corpus_fp=corpus_fp
        )
        if not res.get("ok"):
            print(f"write-baseline: {res.get('error')}")
            return 1
        print(
            f"write-baseline: pinned deterministic metrics to {res['path']} — committing "
            "it (and any CI ratchet over it) is a dated owner decision"
        )

    if wants_baseline:
        bp = args.baseline or None
        if bp is None:
            candidate = _project_fixture_path(resolved_md, _BASELINE_FILENAME)
            if os.path.exists(candidate):
                bp = candidate
            elif ambient:
                bp = _default_baseline_path()
        if bp is None:
            # Skip-if-absent, loudly — mirrors the hard-set gates' absent-fixture skip.
            print(
                "baseline: none found (no committed "
                f"{_BASELINE_FILENAME}) — pin one with --write-baseline"
            )
            return default_exit
        try:
            with open(bp, "r", encoding="utf-8") as fh:
                baseline_doc = json.load(fh)
        except Exception as exc:
            # Provided-but-unreadable is the loud-fail arm (a truncated committed pin is
            # a real problem, not a deliberately-absent input) — RET-8's exact split.
            print(f"baseline: FAILED to read {bp}: {exc}")
            return 1
        for line in diff_baseline(
            report, baseline_doc, head=head, fixture_fp=fixture_fp, corpus_fp=corpus_fp
        ):
            print(line)
    return default_exit


if __name__ == "__main__":
    raise SystemExit(main())
