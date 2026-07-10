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
from typing import Dict, List, Optional

from .build_index import (
    LoadedIndex,
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
# PRF-2: the honest per-prompt budget for cold_latency's p50 (fresh-subprocess-per-sample,
# see cold_latency()'s docstring). Gate 5 above is measured WARM and documents itself as
# ~10x under the real per-prompt cost -- this is the number that actually reflects what a
# freshly-spawned hook pays. Report-only by default (opt in via --gate-cold / evaluate()'s
# gate_cold=True) so a cold OS cache on an ungated hermetic run never reddens CI; the dense
# CI lane (which restores a warm fastembed model cache) passes --gate-cold so a REAL cold-path
# regression (e.g. a heavier model, a new per-import cost) fails the build.
GATE_COLD_P50_MS = 1500.0
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
def self_recall_at_k(index: LoadedIndex, k: int = 10, *, index_dir: Optional[str] = None) -> float:
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
        names = {r["name"] for r in recall(q, k=k, index=index, index_dir=index_dir)}
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


def body_probe_recall_at_k(
    index: LoadedIndex, k: int = 10, *, index_dir: Optional[str] = None
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
        names = {r["name"] for r in recall(q, k=k, index=index, index_dir=index_dir)}
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
    index: LoadedIndex, abstention_set: List[str], k: int = 10, *, index_dir: Optional[str] = None
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
    ``n=0`` (rate 0.0) when the fixture is empty/missing -- a deliberately-absent input at
    THIS layer; ``evaluate`` decides skip-vs-fail from whether a path was provided.
    """
    if not abstention_set:
        return {"rate": 0.0, "n": 0}
    zero = 0
    for q in abstention_set:
        if not recall(q, k=k, index=index, index_dir=index_dir):
            zero += 1
    n = len(abstention_set)
    return {"rate": round(zero / n, 4), "n": n}


def precision_at_k(
    index: LoadedIndex, relevance_set: List[dict], k: int = 10, *, index_dir: Optional[str] = None
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
        ranked = [r["name"] for r in recall(item["query"], k=k, index=index, index_dir=index_dir)]
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
    tok = token_reduction(memory_dir, index, hard_set, k=k, index_dir=index_dir)
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
    with open(dp, "w", encoding="utf-8") as fh:
        fh.write(text)
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
    with open(fp, "w", encoding="utf-8") as fh:
        fh.write(text)

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
            with open(dp, "w", encoding="utf-8") as fh:
                fh.write("".join(parts))
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
    index: LoadedIndex, hard_set: List[dict], k: int = 10, *, index_dir: Optional[str] = None
) -> Dict[str, float]:
    """recall@k (any expected in top-k) + MRR@k (1/rank of first expected) over the set."""
    if not hard_set:
        return {"recall": 0.0, "mrr": 0.0, "n": 0}
    hit = 0
    rr_sum = 0.0
    for item in hard_set:
        expected = set(item["expected"])
        ranked = [r["name"] for r in recall(item["query"], k=k, index=index, index_dir=index_dir)]
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
    index: LoadedIndex, hard_set: List[dict], k: int = 10, *, index_dir: Optional[str] = None
) -> Dict[str, Dict[str, float]]:
    """RET-8: ``hard_set_metrics`` bucketed by each row's ``category`` tag.

    ``{category: {recall, mrr, n}}``, categories sorted; a row without a tag (a hand-rolled
    list passed directly, bypassing ``load_hard_set``'s default) buckets as ``single-hop``.
    Scoring DELEGATES to ``hard_set_metrics`` per bucket — one scoring code path, so the
    per-category numbers can never disagree with the aggregate gates about what a hit is.
    This is what makes a regression ATTRIBUTABLE: the aggregate can hide a multi-hop
    collapse behind twenty healthy single-hop rows; these buckets cannot.
    """
    buckets: Dict[str, List[dict]] = {}
    for item in hard_set:
        cat = item.get("category") or _DEFAULT_CATEGORY
        buckets.setdefault(cat, []).append(item)
    return {
        cat: hard_set_metrics(index, items, k=k, index_dir=index_dir)
        for cat, items in sorted(buckets.items())
    }


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
        _estimate_tokens(format_results(recall(s["query"], k=k, index=index, index_dir=index_dir)))
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
    index: LoadedIndex, queries: List[str], k: int = 10, *, index_dir: Optional[str] = None
) -> Dict[str, float]:
    """Warm recall latency (index preloaded) — p50/p95 in ms over ``queries``."""
    samples: List[float] = []
    for q in queries:
        if not q:
            continue
        t0 = time.perf_counter()
        recall(q, k=k, index=index, index_dir=index_dir)
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
    abstention_set_path: Optional[str] = None,
    gate_cold: bool = False,
) -> dict:
    """Run all 5 gates; return a report dict with per-gate values + pass flags.

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

    ``gate_cold`` (PRF-2) opts INTO gating ``cold_latency``'s p50 against
    ``GATE_COLD_P50_MS`` -- default False so cold_latency stays the report-only honesty
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

    self_recall = self_recall_at_k(index, k=k, index_dir=index_dir)
    hs = hard_set_metrics(index, hard_set, k=k, index_dir=index_dir)
    # RET-8: the same rows bucketed by category tag — regressions attributable to the
    # question class (multi-hop/temporal/update/...) instead of hidden in the aggregate.
    by_category = (
        hard_set_metrics_by_category(index, hard_set, k=k, index_dir=index_dir)
        if hard_set else {}
    )
    tok = token_reduction(memory_dir, index, hard_set, k=k, index_dir=index_dir)
    lat_queries = [item["query"] for item in hard_set] or [
        derive_self_query(e) for e in index.entries[:30]
    ]
    lat = latency(index, lat_queries, k=k, index_dir=index_dir)
    cold = cold_latency(memory_dir, index_dir, lat_queries, k=k)

    # Report-only scorecard additions (Tier 1 + Tier 2) — never feed a gate threshold above.
    # Resolve telemetry_dir ONCE here (sibling of memory_dir) and pass the SAME resolved value
    # to every consumer below -- each independently re-deriving it from None would re-resolve
    # via the ambient resolve_dirs(), which can leak onto the real repo's ledger when an
    # explicit memory_dir was passed (the same class of leak the repo_root guard above closes).
    from .telemetry import default_telemetry_dir

    resolved_telemetry_dir = telemetry_dir or default_telemetry_dir(memory_dir)
    precision = precision_at_k(index, relevance_set, k=k, index_dir=index_dir)
    half_life = staleness_half_life(memory_dir, repo_root) if repo_root else {"median_days": 0.0, "n": 0}
    sess_cost = session_token_cost(
        memory_dir, resolved_telemetry_dir, index, hard_set, k=k, index_dir=index_dir
    )
    grad = graduation_rate(resolved_telemetry_dir)
    body_probe = body_probe_recall_at_k(index, k=k, index_dir=index_dir)
    abstention = abstention_rate(index, abstention_set, k=k, index_dir=index_dir)

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
    # PRF-2: cold_p50_ms follows the SAME skip-vs-gate shape as the hard-set/token-reduction
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
        gates["cold_p50_ms"] = {
            "value": cold["p50"], "threshold": GATE_COLD_P50_MS,
            "pass": cold["n"] > 0 and cold["p50"] < GATE_COLD_P50_MS,
        }
    else:
        gates["cold_p50_ms"] = {
            "value": cold["p50"], "threshold": GATE_COLD_P50_MS,
            "pass": None,
            "skipped": True,
        }
    return {
        "ok": all(g["pass"] for g in gates.values() if g.get("pass") is not None),
        "dense_ready": index.dense_ready,
        "model": index.model,
        "count": len(index),
        "hard_set_n": hs["n"],
        "by_category": by_category,
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
        help="PRF-2: gate cold_latency's p50 (fresh-subprocess-per-sample, the honest "
        "per-prompt cost) against GATE_COLD_P50_MS. Off by default so cold_latency stays a "
        "report-only signal everywhere except CI's dense lane, which restores a warm model "
        "cache and passes this flag so a real cold-path regression fails the build. Skipped "
        "(not failed) on a bm25-only run -- without dense, cold ~= warm.",
    )
    parser.add_argument("-k", type=int, default=10)
    args = parser.parse_args(argv)

    # RET-8 hermeticity guard, the CLI twin of evaluate()'s memory_dir guard: the ambient
    # default fixtures (this repo's tests/fixtures, or the resolved project's
    # .audit-fixtures) calibrate the AMBIENT corpus. Scoring them against an explicitly
    # overridden --memory-dir would judge one corpus by another corpus's fixtures —
    # harmless while precision/abstention were report-only, a false gate verdict now that
    # they (and the hard-set gates they sit beside) are tracked. Explicit --memory-dir →
    # only explicitly-passed fixtures run; the fixtureless gates skip, exactly as a
    # fixture-less fresh project skips them.
    ambient = args.memory_dir is None
    report = evaluate(
        memory_dir=args.memory_dir,
        index_dir=args.index_dir,
        hard_set_path=args.hard_set or (_default_hard_set_path() if ambient else None),
        k=args.k,
        relevance_set_path=args.relevance_set
        or (_default_relevance_set_path() if ambient else None),
        repo_root=args.repo_root,
        telemetry_dir=args.telemetry_dir,
        abstention_set_path=args.abstention_set
        or (_default_abstention_set_path() if ambient else None),
        gate_cold=args.gate_cold,
    )
    if not report.get("ok") and "error" in report:
        print(f"eval error: {report['error']}")
        return 1

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
        "cold_p50_ms": (
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
    t = report["tokens"]
    print(f"  tokens: full={t['full']} floor={t['floor']} recall_avg={t['recall_avg']} net={t['net']}")
    print(f"  latency (warm): p50={report['latency']['p50']}ms p95={report['latency']['p95']}ms n={report['latency']['n']}")
    c = report.get("cold_latency") or {}
    if c.get("n"):
        print(
            f"  latency (cold, per-process model load): p50={c['p50']}ms max={c['max']}ms n={c['n']} "
            "— the REAL hook cost; the warm p95 above understates it (report-only, not gated)"
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
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
