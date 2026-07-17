"""Metric + fixture-loading primitives for the recall eval — decomposed out of
``eval_recall`` (the CLI front door, which re-exports every name here unchanged).

Carries the Helpers/Gates sections (self-recall, hard-set recall/MRR), RET-2's
body_probe, RET-1's abstention_rate, the graded precision@k, the report-only
scorecard metrics, and the RET-7/RET-8 fixture loaders with their category tags.
"""

from __future__ import annotations

import os
import time
from typing import Dict, List, Optional

from .build_index import LoadedIndex, bm25_terms, entry_description, tokenize
from .recall import recall


# RET-8: the canonical category tags. Data-driven everywhere (an unknown tag forms its own
# bucket rather than erroring) — this tuple is documentation + the default, not an enum wall.
# SEN-4 adds "adversarial": if adversarial-tagged hard_set rows exist they bucket in
# by_category with ZERO loader change (this tuple is documentation only); the poisoned-corpus
# COVERAGE report is the separate report-only `--adversarial` mode (adversarial_report), whose
# five-boolean rows come from the shipped spine directly, not from by_category recall numbers.
CATEGORIES = ("single-hop", "multi-hop", "temporal", "update", "abstention", "adversarial")
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
            sup = item.get("superseded")
            out.append(
                {
                    "query": q,
                    "expected": [str(x) for x in exp],
                    "category": str(cat) if isinstance(cat, str) and cat.strip() else _DEFAULT_CATEGORY,
                    # TMB-4: the corpse stem rides along for update_category_metrics'
                    # stamp-state bucketing; every pre-TMB-4 consumer reads only
                    # query/expected/category, so the extra key is purely additive.
                    **({"superseded": str(sup)} if isinstance(sup, str) and sup.strip() else {}),
                }
            )
    return out


def load_hard_set_metadata(path: str) -> Dict[str, str]:
    """The optional provenance header (``generated_with_backend``/``generated_at``) of a
    hard-set fixture, or ``{}`` when the fixture has none / doesn't exist / fails to parse.
    """
    meta, _rows = _load_fixture_docs(path)
    return meta if isinstance(meta, dict) else {}


def load_absence_rows(path: str) -> List[dict]:
    """TMB-3: load ``[{query, absent: [stem, ...], category}]`` — the forgetting rows.

    The ABSENCE-polarity sibling of ``load_hard_set``: rows whose gold is that the named
    (archived) stems must NOT surface. They live in the SAME tracked fixture file, keyed
    by an ``absent`` list instead of ``expected`` — ``load_hard_set`` drops them (no
    ``expected``), so every presence metric and gate is byte-identical whether or not
    forgetting rows exist. Category defaults to ``forgetting``. ``[]`` if missing.
    """
    _meta, data = _load_fixture_docs(path)
    out: List[dict] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        q = item.get("query")
        absent = item.get("absent")
        if isinstance(absent, str):
            absent = [absent]
        if isinstance(q, str) and isinstance(absent, list) and absent:
            cat = item.get("category")
            out.append(
                {
                    "query": q,
                    "absent": [str(x) for x in absent],
                    "category": str(cat) if isinstance(cat, str) and cat.strip() else "forgetting",
                }
            )
    return out


def absence_polarity_metrics(
    index: LoadedIndex,
    rows: List[dict],
    memory_dir: str,
    k: int = 10,
    *,
    index_dir: Optional[str] = None,
) -> Dict[str, float]:
    """TMB-3: the forgetting category's scorer — ABSENCE polarity, distinct from
    ``hard_set_metrics``.

    Presence metrics reward a stem for ranking; this one rewards it for STAYING GONE:
    a row HOLDS when none of its still-archived ``absent`` stems surface in the top-k
    for its query, and FAILS when one leaks (the archived-shadow / stale-index /
    regression class the archive-exclusion contract promises can't happen). A row whose
    targets are no longer in ``archive/`` (restored or deleted since confirm) is
    SKIPPED, not scored — the absence expectation ended with the archival (fail-closed:
    absent-from-archive = skip). ``{"n": scored, "skipped", "held", "absence"}`` where
    ``absence`` = held/scored (1.0 = perfect forgetting). Report-only forever unless a
    dated owner decision promotes a gate.
    """
    scored = skipped = held = 0
    archive_dir = os.path.join(memory_dir, "archive")
    for row in rows:
        still_archived = [
            s for s in row.get("absent") or []
            if os.path.isfile(os.path.join(archive_dir, f"{s}.md"))
        ]
        if not still_archived:
            skipped += 1
            continue
        scored += 1
        names = {
            r["name"]
            for r in recall(
                row["query"], k=k, index=index, index_dir=index_dir, memory_dir=memory_dir
            )
        }
        if not (set(still_archived) & names):
            held += 1
    return {
        "n": scored,
        "skipped": skipped,
        "held": held,
        "absence": (held / scored) if scored else 0.0,
    }


def update_category_metrics(
    index: LoadedIndex,
    hard_set: List[dict],
    memory_dir: str,
    k: int = 10,
    *,
    index_dir: Optional[str] = None,
) -> Dict[str, object]:
    """TMB-4: the update rows' STAMP-STATE-BUCKETED scoring — report-only.

    Rows: ``category == 'update'`` carrying a ``superseded`` corpse stem. Each row's
    bucket is the corpse's CURRENT GRW-7 ``invalid_after`` state, read live at scoring
    time (recall's own classifier — one horizon):

      - ``unstamped``/``recent`` → successor-must-OUTRANK-corpse: the tip must rank in
        the top-k AND above the corpse if the corpse ranks at all (a recent invalidation
        only score-halves the corpse — it can legitimately still surface, but current
        truth must beat retired truth).
      - ``old`` (or the corpse file gone) → successor-PRESENCE-only: recall
        display-filters old-stamped corpses, so "outrank" is vacuous — the tip ranking
        at all is the whole test.

    ``{"n", "outrank": {"n", "pass"}, "presence": {"n", "pass"}, "outrank_failures"}``
    — ``outrank_failures`` is the number the TMB-4 doctor line names. Never a gate:
    GATE_UPDATE_* promotion stays a dated owner decision and no such constant exists.
    """
    rows = [
        r for r in hard_set if r.get("category") == "update" and r.get("superseded")
    ]
    out = {
        "n": len(rows),
        "outrank": {"n": 0, "pass": 0},
        "presence": {"n": 0, "pass": 0},
        "outrank_failures": 0,
    }
    if not rows:
        return out
    from .staleness import read_invalid_after

    for row in rows:
        corpse = row["superseded"]
        state = "old"  # a vanished corpse can't rank — presence-only, the honest bucket
        corpse_path = os.path.join(memory_dir, f"{corpse}.md")
        if os.path.isfile(corpse_path):
            try:
                with open(corpse_path, "r", encoding="utf-8") as fh:
                    ia = read_invalid_after(fh.read())
                from .recall import _invalidation_state

                state = _invalidation_state({"invalid_after": ia}) or "unstamped"
            except Exception:
                state = "unstamped"
        names = [
            r["name"]
            for r in recall(
                row["query"], k=k, index=index, index_dir=index_dir, memory_dir=memory_dir
            )
        ]
        tips = row["expected"]
        tip_rank = next((names.index(t) + 1 for t in tips if t in names), None)
        corpse_rank = names.index(corpse) + 1 if corpse in names else None
        if state in ("unstamped", "recent"):
            out["outrank"]["n"] += 1
            passed = tip_rank is not None and (corpse_rank is None or tip_rank < corpse_rank)
            if passed:
                out["outrank"]["pass"] += 1
            else:
                out["outrank_failures"] += 1
        else:
            out["presence"]["n"] += 1
            if tip_rank is not None:
                out["presence"]["pass"] += 1
            # a presence miss is visible as presence.n - presence.pass; it is NOT an
            # outrank failure (the corpse never competed) — the doctor line stays honest
    return out


def t11_category_lines(report: dict, k: int) -> List[str]:
    """The T11 report lines (forgetting + update knowledge) — rendered here so the
    façade's ``main`` stays a one-call print site. Empty when neither key is present
    (absence-emits-nothing keeps flag-off output byte-identical)."""
    lines: List[str] = []
    f = report.get("forgetting")
    if f:
        lines.append(
            f"  category {'forgetting':11s} absence@{k}={f['absence']:.4f} "
            f"held={f['held']}/{f['n']} skipped={f['skipped']} "
            "(TMB-3, report-only; absence POLARITY — an archived stem surfacing is the failure)"
        )
    u = report.get("update_knowledge")
    if u:
        lines.append(
            f"  update knowledge: outrank {u['outrank']['pass']}/{u['outrank']['n']} "
            f"(unstamped/recent — successor must beat the corpse), presence "
            f"{u['presence']['pass']}/{u['presence']['n']} (old-stamped — corpse is "
            f"display-filtered), {u['outrank_failures']} outrank failure(s) "
            "(TMB-4, report-only; GATE_UPDATE_* stays a dated owner decision)"
        )
    return lines


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
