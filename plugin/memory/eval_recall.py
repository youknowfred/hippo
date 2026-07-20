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

Decomposed (pure code motion): metric/fixture primitives → ``eval_metrics``; MSR-2/MSR-4
arms, latency probes, GRF-4 → ``eval_arms``; MSR-1 fingerprints/diff → ``eval_ledger``;
SEN-4 adversarial probe → ``eval_adversarial`` (ED5R-3, split BEFORE MEA-1/MEA-5 needed lines);
GRF-3 dense-floor calibration sweep → ``eval_floor``, and the default-fixture resolvers it
needs → ``eval_fixtures`` (ED5R-3 again, split BEFORE round 6's next feature needed lines).
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

# --------------------------------------------------------------------------- #
# Decomposition re-imports (pure code motion): every moved name stays importable
# as ``memory.eval_recall.<name>`` and stays patchable HERE for the façade's own
# call sites (evaluate/main/_handle_run_outputs look these up in this module's
# globals). Siblings never import this façade.
# --------------------------------------------------------------------------- #
from .eval_arms import (
    _COLD_PROBE,
    _REACHABILITY_MIN_ROWS,
    _REACHABILITY_SEEDS,
    _dense_disabled_env,
    _grep_baseline_docs,
    _grep_rank,
    cold_latency,
    latency,
    miss_autopsy,
    null_hypothesis_arms,
    reachability_audit,
    token_reduction,
)
from .eval_floor import (  # GRF-3 dense-floor calibration sweep (façade re-exports)
    _FLOOR_SWEEP_NAME,
    _FLOOR_SWEEP_SCHEMA,
    _raw_max_cosines,
    default_floor_sweep_path,
    floor_sweep,
    read_floor_sweep,
    recommend_floor,
    write_floor_sweep,
)
from .eval_ledger import (
    _BASELINE_FILENAME,
    _BASELINE_N_FLOOR,
    _BASELINE_SCHEMA,
    _RUN_LEDGER_NAME,
    _VOLATILE_GATES,
    _VOLATILE_KEYS,
    _fmt_delta,
    _git_head,
    baseline_metrics,
    canonical_json,
    corpus_fingerprint,
    default_run_ledger_path,
    deterministic_view,
    diff_baseline,
    fixture_fingerprint,
)
from .eval_metrics import (
    CATEGORIES,
    _BODY_PROBE_TOKENS,
    _DEFAULT_CATEGORY,
    _SELF_QUERY_TOKENS,
    _description_of,
    _estimate_tokens,
    _load_fixture_docs,
    abstention_rate,
    body_probe_recall_at_k,
    derive_body_probe_query,
    derive_self_query,
    graduation_rate,
    hard_set_metrics,
    hard_set_metrics_by_category,
    load_abstention_set,
    load_hard_set,
    load_hard_set_metadata,
    load_relevance_set,
    precision_at_k,
    self_recall_at_k,
    staleness_half_life,
)

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
from .eval_fixtures import (  # drafts-queue plumbing + the T11 synthesizers (façade re-exports)
    _DRAFTS_FILENAME,
    _DRAFTS_NOTE,
    _default_abstention_set_path,
    _default_fixture_path,
    _default_hard_set_path,
    _default_relevance_set_path,
    _parseable_yaml,
    _project_fixture_path,
    default_drafts_path,
    draft_forgetting_fixtures,
    is_project_local_fixture,
    promoted_gate,
    draft_livedin_fixtures,
    draft_update_fixtures,
    run_draft_forgetting_cli,
    run_draft_livedin_cli,
    run_draft_update_cli,
    validate_confirm_row_kind,
)
from .eval_adversarial import (  # SEN-4 adversarial probe (façade re-exports; ED5R-3 split)
    _ADVERSARIAL_DIRNAME,
    _ADVERSARIAL_MANIFEST,
    _adversarial_fixture_dir,
    _render_clean_fixture,
    _sec6_withheld_via_real_recall,
    adversarial_report,
)
from .eval_ledger import read_run_ledger
from .eval_metrics import (
    absence_polarity_metrics,
    hard_set_resolvability,
    load_absence_rows,
    resolvable_row,
    t11_category_lines,
    update_category_metrics,
)


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
    absent: Optional[List[str]] = None,
    superseded: Optional[str] = None,
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

    T11 additive arms (gates in ``eval_fixtures.validate_confirm_row_kind``): ``absent``
    (TMB-3, mutually exclusive with ``expected``) admits an ABSENCE-polarity forgetting
    row — every named stem must actually be in ``archive/`` (fail closed; only
    ``load_absence_rows``/``absence_polarity_metrics`` consume these, report-only);
    ``superseded`` (TMB-4, presence rows) records the still-live corpse stem so
    ``update_category_metrics`` can bucket by its GRW-7 stamp state.
    """
    if memory_dir is None:
        memory_dir, _repo = resolve_dirs()
    q = (query or "").strip()
    if not q:
        return {"ok": False, "reason": "empty query"}

    def _norm(vals) -> List[str]:
        out: List[str] = []
        for s in vals if isinstance(vals, (list, tuple)) else [vals]:
            s = str(s or "").strip()
            if s.endswith(".md"):
                s = s[:-3]
            if s and s not in out:
                out.append(s)
        return out

    stems = _norm(expected) if expected else []
    absent_stems = _norm(absent) if absent else []
    corpse = _norm([superseded])[0] if superseded else None
    kind_error = validate_confirm_row_kind(memory_dir, stems, absent_stems, corpse)
    if kind_error:
        return {"ok": False, "reason": kind_error}
    if not stems and not absent_stems:
        return {
            "ok": False,
            "reason": "expected is empty — a cluster no existing memory answers is a "
            "capture gap (capture the memory first), not a fixture row",
        }
    bad = [s for s in stems + absent_stems if "/" in s or os.sep in s or s.startswith(".")]
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
    tracked_queries = {row["query"] for row in load_hard_set(fp)}
    tracked_queries |= {row["query"] for row in load_absence_rows(fp)}
    if q in tracked_queries:
        return {"ok": False, "reason": "query is already a tracked fixture row"}

    cat = str(category or "").strip() or "abstention"
    if absent_stems and cat == "abstention":
        cat = "forgetting"  # the absence rows' own data-driven default tag
    if absent_stems:
        row_text = (
            f"- query: {json.dumps(q, ensure_ascii=False)}\n"
            f"  absent: [{', '.join(json.dumps(s, ensure_ascii=False) for s in absent_stems)}]\n"
            f"  category: {json.dumps(cat, ensure_ascii=False)}\n"
        )
    else:
        row_text = (
            f"- query: {json.dumps(q, ensure_ascii=False)}\n"
            f"  expected: [{', '.join(json.dumps(s, ensure_ascii=False) for s in stems)}]\n"
            + (f"  superseded: {json.dumps(corpse, ensure_ascii=False)}\n" if corpse else "")
            + f"  category: {json.dumps(cat, ensure_ascii=False)}\n"
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
        **({"absent": absent_stems} if absent_stems else {}),
        **({"superseded": corpse} if corpse else {}),
        "category": cat,
        "removed_from_drafts": removed,
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
    # T11 (TMB-3/TMB-4): absence-polarity forgetting rows + stamp-state-bucketed update
    # scoring — both absence-emits-nothing (no rows -> no key -> reports, fingerprints
    # and committed MSR-1 baselines stay byte-identical), both report-only forever
    # unless a dated owner decision promotes a gate.
    absence_rows = load_absence_rows(hard_set_path) if hard_set_path else []
    forgetting = (
        absence_polarity_metrics(index, absence_rows, memory_dir, k=k, index_dir=index_dir)
        if absence_rows else {}
    )
    update_knowledge = update_category_metrics(
        index, hard_set, memory_dir, k=k, index_dir=index_dir
    )
    if not update_knowledge["n"]:
        update_knowledge = {}
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
    # ABS-3: both bind only on the pack-corpus pairing they were calibrated for; a project's
    # own fixture REPORTS instead (see promoted_gate) — scoping one twin and not the other
    # would leave the same category error live under another name.
    gates["precision@10"] = promoted_gate(
        precision["precision"], GATE_PRECISION_AT_K,
        precision["n"] > 0 and precision["precision"] >= GATE_PRECISION_AT_K,
        relevance_set_path, "relevance set",
    )
    gates["abstention_rate"] = promoted_gate(
        abstention["rate"], GATE_ABSTENTION,
        abstention["n"] > 0 and abstention["rate"] >= GATE_ABSTENTION,
        abstention_set_path, "off-topic fixture",
    )
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
    # MEA-1 (ED5R-2): the instrument states its own sensitivity — per-category
    # resolvable_n vs n, REPORTED never applied (no row skipped, no gate moved).
    # Absence-emits-nothing (ED-4): the key appears only when some row is UNresolvable,
    # so a fully-resolvable run (the CI/golden pack fixtures) stays byte-identical.
    resolvability = hard_set_resolvability(index, hard_set) if hard_set else {}
    if all(r["resolvable_n"] == r["n"] for r in resolvability.values()):
        resolvability = {}
    return {
        "ok": all(g["pass"] for g in gates.values() if g.get("pass") is not None),
        "dense_ready": index.dense_ready,
        "model": index.model,
        "count": len(index),
        "hard_set_n": hs["n"],
        "by_category": by_category,
        **({"resolvability": resolvability} if resolvability else {}),
        **({"forgetting": forgetting} if forgetting else {}),
        **({"update_knowledge": update_knowledge} if update_knowledge else {}),
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
    parser.add_argument(
        "--adversarial",
        action="store_true",
        help="SEN-4: the poisoned-memory coverage report — acceptance-test the shipped trust "
        "spine (SEC-5/6/7) against fixtures under .audit-fixtures/adversarial/. Per poisoned "
        "fixture, five deterministic booleans (payload crossed into format_results, SEC-6 "
        "quarantine withheld a drifted file, SEC-5 consent shows it byte-equal, threat-lint "
        "flagged it, knee/floor/MMR admitted it) by driving the shipped code — no LLM. "
        "Report-only; skips when no fixture corpus exists; never gates CI.",
    )
    parser.add_argument(
        "--draft-forgetting",
        action="store_true",
        help="TMB-3: enumerate archive/*.md into archive-absence DRAFT rows in the SIG-6 "
        "drafts queue (zero LLM); confirm each per item via confirm_hard_set_row("
        "absent=[stem], category='forgetting')",
    )
    parser.add_argument(
        "--draft-update",
        action="store_true",
        help="TMB-4: walk supersedes chains into category:update DRAFT rows (verbatim "
        "spans of the superseded file; zero LLM, fail closed); confirm each per item via "
        "confirm_hard_set_row(query, [tip], category='update', superseded=<corpse>)",
    )
    parser.add_argument(
        "--draft-livedin",
        action="store_true",
        help="MEA-2: queue outcome-confirmed lived-in retrievals (verbatim episode query "
        "x session-grain injection hit) as DRAFT rows — the fourth lane (zero LLM, "
        "deterministic noise filters, volume-capped); confirm each per item via "
        "confirm_hard_set_row(query, [stems], category='single-hop')",
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

        def _ab_forward() -> List[str]:
            # Forward the eval-level corpus/fixture args (they are parsed HERE, so
            # they never appear in ab_extra) — shared by the flag-context harnesses.
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
            return fwd

        if args.ab == "HIPPO_SALIENCE":
            # MSR-5: the ED-2 salience-revisit rig (memory.salience_eval) — measures
            # only, never flips the default.
            from .salience_eval import main as _salience_ab_main

            return _salience_ab_main(_ab_forward())
        if args.ab == "HIPPO_OUTCOME_PRIOR":
            # MEA-5: the EVD-4 Arm B rig (memory.outcome_prior_eval) — measures the
            # EXISTING RET-14 flag only; nothing flips (ED-2/LIF-7).
            from .outcome_prior_eval import main as _outcome_ab_main

            return _outcome_ab_main(_ab_forward())
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

    if args.draft_forgetting:
        return run_draft_forgetting_cli(args.memory_dir)
    if args.draft_update:
        return run_draft_update_cli(args.memory_dir, args.index_dir)
    if args.draft_livedin:
        return run_draft_livedin_cli(args.memory_dir)

    if args.adversarial:
        rep = adversarial_report(_adversarial_fixture_dir(args.memory_dir))
        if rep.get("skipped"):
            print(f"adversarial: SKIPPED — {rep['skipped']}")
            return 0
        t = rep["totals"]
        print(
            f"adversarial coverage ({t['n']} poisoned fixture(s)) — acceptance-testing the "
            f"shipped spine (SEC-5/6/7). Booleans are ADMISSION/COVERAGE, not 'injection success':"
        )
        def _m(v):
            return "?" if v is None else ("yes" if v else "no ")

        for r in rep["rows"]:
            print(
                f"  {r['name']:<28} crossed={_m(r['payload_crossed_raw'])} "
                f"sec6-withheld={_m(r['sec6_quarantine_withheld'])} "
                f"sec5-byte-equal={_m(r['sec5_consent_byte_equal'])} "
                f"threat-flagged={_m(r['threat_lint_flagged'])} "
                f"admitted={_m(r['admitted'])}  — \"{r['query']}\""
            )
        print(
            f"  totals: {t['crossed']}/{t['n']} crossed raw, {t['sec6_withheld']} SEC-6-withheld, "
            f"{t['sec5_byte_equal']} SEC-5 byte-equal, {t['threat_flagged']} threat-flagged, "
            f"{t['admitted']} admitted (report-only — never gates CI)"
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
            # ABS-3: a gate that RAN but does not bind carries its own reason — the generic
            # table would say "no ... fixture" for one that exists and measured a real number.
            why = g.get("reported_only")
            label = "reported only" if why else "skipped"
            why = why or _SKIP_REASONS.get(name, "input absent")
            extra += f" — {label} ({why}; excluded from RESULT)"
        print(f"  {mark} {name:18s} = {g['value']} (threshold {g['threshold']}){extra}")
    # RET-8: the per-category breakdown — the line that makes a regression attributable.
    # One line per category present in the hard set; single-category (all-default) fixtures
    # print it too, so the output shape doesn't shift when the first tagged row arrives.
    for cat, m in (report.get("by_category") or {}).items():
        print(
            f"  category {cat:11s} recall@{args.k}={m['recall']:.4f} mrr@{args.k}={m['mrr']:.4f} "
            f"n={m['n']} (RET-8)"
        )
    # MEA-1 (ED5R-2): the sensitivity line — present only when some fixture row cannot
    # resolve against this corpus (absence-emits-nothing keeps healthy output unchanged).
    resv = report.get("resolvability") or {}
    if resv:
        tot_r = sum(v["resolvable_n"] for v in resv.values())
        tot_n = sum(v["n"] for v in resv.values())
        cats = ", ".join(f"{c} {v['resolvable_n']}/{v['n']}" for c, v in resv.items())
        print(
            f"  SENSITIVITY (ED5R-2): {tot_r}/{tot_n} fixture row(s) resolvable against this "
            f"corpus ({cats}) — unresolvable rows can only score as misses; reported, never "
            "skipped, and no gate moves on it"
        )
    for line in t11_category_lines(report, args.k):  # TMB-3/TMB-4, report-only
        print(line)
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
