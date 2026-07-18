"""Recall-triggered SEMANTIC reconsolidation worklist (Tier 2, memory-organism
instrument-immunize roadmap — the immune keystone).

Neutralizes two failure modes the architecture doc identified:
  - FM1: a birth-defect WRONG claim passes the SYNTACTIC reverify gate — ``reverify_file``
    only checks "does the file's cited code still match the baseline", never "is the
    CONTENT actually correct."
  - FM2: a frequently-recalled WRONG memory grows its soak/strength score and is the LAST
    thing curated — recall frequency measures USE, not correctness.

``recalled_stale_worklist()`` intersects the names RECENTLY RECALLED (from the Tier-1
recall-event ledger, over the last N sessions) with ``staleness.find_stale()``'s STALE set —
the "labile-on-recall" set: memories ACTIVELY shaping recent agent behavior (just retrieved)
AND whose cited code has since drifted (a concrete reason to doubt them). This is exactly
the shipped ``claude_is_memory_master`` re-grounding flow (read body + git diff
``source_commit``..HEAD → reverify / fix body + reverify / archive), just TRIGGERED BY
RECALL instead of only by calendar SessionStart.

The per-item JUDGMENT stays the memory-master AGENT's job — this module ships the
MECHANISM (the worklist + the write primitive + the outcome log), never a judgment loop in
a hook. ``semantic_reverify()`` is a thin wrapper around the EXISTING
``provenance.reverify_file()`` (per-item, verification-gated, body byte-identical, refuses
unparseable frontmatter) — there is NO new re-baseline primitive and therefore no new bulk
re-baseline path (mirrors ``reverify_head_only_no_bulk``). GRA-4's opt-in ``superseded_by``
routes through ``links.add_typed_relation`` (the one typed-edge write primitive), equally
per-item and agent-gated.

GRA-9 (consolidation propagates along edges): each worklist item carries an OPTIONAL
``"linked"`` column — its 1-hop graph neighborhood (untyped ``[[wikilinks]]`` + GRA-4
typed edges, both directions) — because when a memory drifts, its linked neighbors are
statistically the next most likely to be wrong. REPORT-ONLY: the column introduces zero
write paths; the producer renders it as ``X (+2 linked: Y, Z)`` and the agent decides
what (if anything) to re-check. When the graph is unavailable the column is simply
absent — the worklist is exactly its pre-GRA-9 self.

LIF-1 (close the loop — demote gets a terminal state): a ``demote`` verdict CHAINS
``staleness.set_invalid_after`` onto the memory — per-item and verdict-gated, the
one-command path from "confirmed wrong" to recall's EXISTING pre-cut penalty — records
the chain in the ledger event (``invalidated``) for audit, and refreshes the index so the
demotion is live this session. The worklist then stops re-nagging what's already settled:
items whose file carries ``invalid_after`` are excluded (terminal — the SessionStart
staleness producer still COUNTS them, so nothing silently disappears), and ``--snooze``
records an explicit per-item ack that silences an item for the next
``_SNOOZE_WINDOW_SESSIONS`` new sessions (a deferral, not a verdict — it expires).

LIF-6 (de-duplicate SessionStart staleness vs reconsolidation reporting): the worklist is
BY CONSTRUCTION a subset of ``staleness.find_stale()``'s full stale set, so the two
SessionStart producers used to each independently re-derive it into the same 9000-char
budget. ``recalled_stale_worklist`` now accepts a precomputed ``stale`` list (the
dispatcher's single ``find_stale`` call, via ``staleness.RunContext``), and
``session_start.staleness_producer`` excludes any name already on this worklist from its
own per-item lines (worklist first) — each memory appears exactly once per SessionStart.

Read-mostly; never raises.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Set

from .provenance import (
    build_repo_file_index,
    git_last_commit_with_time,
    reverify_file,
    run_git,  # unused here since the watermark-lane move; kept importable (tests reach it as R.run_git)
)
from .staleness import RunContext, find_stale, invalid_after_map, read_provenance, set_invalid_after
from .staleness_policy import note_suppressed, split_volatile_only, suppressed_count_note, volatile_set

# GRW-5 (re-export): the commit-precise [since-watermark] lane moved to its sibling when
# VOL-1 tripped the module-size ratchet — every dotted path (`memory.reconsolidate.<name>`)
# keeps resolving, per CONTRIBUTING.md "Code layout".
from .reconsolidate_watermark import (  # noqa: F401
    _MAX_WATERMARK_PATHS,
    _last_session_watermark,
    watermark_stale_candidates,
)
from .telemetry import (
    read_events,
    read_reconsolidation_events,
    record_reconsolidation_outcome,
)

_DEFAULT_WINDOW_SESSIONS = 10
_MAX_WORKLIST_ITEMS = 20
# LIF-1: how many NEW ledger sessions an explicit --snooze ack holds for. A snooze is a
# DEFERRAL, not a verdict — it must expire and re-nag (only demote's chained invalid_after
# is terminal). A plain module constant like _DEFAULT_WINDOW_SESSIONS above (no env knob —
# same convention), on the same 5-session rhythm as soak.SOAK_GATE_SESSIONS.
_SNOOZE_WINDOW_SESSIONS = 5
# GRA-9: per-item cap on the "linked" review-adjacent column. Deliberately tiny — the
# producer renders every item into session_start's shared 9000-char budget alongside all
# the other producers, and the column is a whose-neighborhood-to-eyeball-next HINT, not a
# graph dump (3 names ≈ the same width as the 4-path changed_paths render above it).
_MAX_LINKED_NEIGHBORS = 3
_VALID_OUTCOMES = frozenset({"graduate", "fix", "demote"})
# Outcomes that legitimately clear the staleness flag -- a "demote" must NEVER re-baseline
# source_commit (that would hide a CONFIRMED-WRONG memory from future staleness detection,
# exactly the FM2 hole this tier exists to close). Tier 3's invalid_after is the actual
# demotion primitive, not this module.
_OUTCOMES_THAT_CLEAR_STALENESS = frozenset({"graduate", "fix"})
# Outcomes that may OPTIONALLY record a supersedes edge (GRA-4's superseded_by opt-in):
# the memory was confirmed wrong/replaced and a SUCCESSOR carries the current claim.
# "graduate" is excluded — confirmed-correct and superseded are contradictory verdicts —
# and so is "fix" since GRW-7: a supersede now STAMPS the loser's invalid_after at the
# successor's commit date, and a memory reverify_file just re-baselined as current must
# never be stamped invalid in the same verdict. Demote is the one coherent supersede arm.
_EDGE_WRITING_OUTCOMES = frozenset({"demote"})


# --------------------------------------------------------------------------- #
# Worklist (read-only)
# --------------------------------------------------------------------------- #
def _recently_recalled_names(telemetry_dir: Optional[str], window_sessions: int) -> Set[str]:
    """Memory names surfaced in the last ``window_sessions`` DISTINCT sessions.

    Ledger events are append-only in chronological order, so the order a session_id is
    FIRST SEEN is its chronological position; the most-recently-STARTED sessions are the
    LAST ones to first-appear. Read-only over the ledger; never raises.
    """
    session_order: List[str] = []
    names_by_session: Dict[str, Set[str]] = {}
    try:
        for e in read_events(telemetry_dir):
            sid = e.get("session_id")
            if not sid:
                continue
            if sid not in names_by_session:
                names_by_session[sid] = set()
                session_order.append(sid)
            for name in e.get("names") or []:
                if name:
                    names_by_session[sid].add(name)
    except Exception:
        return set()
    recent_sessions = session_order[-window_sessions:] if window_sessions > 0 else session_order
    out: Set[str] = set()
    for sid in recent_sessions:
        out |= names_by_session.get(sid, set())
    return out


def _snoozed_names(telemetry_dir: Optional[str]) -> Set[str]:
    """Names whose latest ``"snooze"`` ack is younger than ``_SNOOZE_WINDOW_SESSIONS`` sessions.

    A snooze ages by SESSIONS, not wall-clock: each recall-ledger session whose FIRST
    (ts-carrying) event lands after the ack's timestamp counts once, and the ack expires —
    the item re-nags — once ``_SNOOZE_WINDOW_SESSIONS`` such sessions have started.
    Anchored on timestamps rather than session ids because the ledger legitimately mixes
    id provenances (harness-provided vs file-token, COR-6) and the snooze CLI has no
    harness id to record. Degrades toward RE-NAGGING, never toward silence: an unreadable
    ledger, a ts-less ack, or rotation dropping a post-ack session's earliest event can
    only shorten a snooze, never extend it. Read-only; never raises; ``set()`` on failure.
    """
    try:
        latest: Dict[str, float] = {}
        for e in read_reconsolidation_events(telemetry_dir):
            ts = e.get("ts")
            if (
                e.get("outcome") == "snooze"
                and e.get("name")
                and isinstance(ts, (int, float))
                and not isinstance(ts, bool)
            ):
                latest[e["name"]] = max(latest.get(e["name"], 0.0), float(ts))
        if not latest:
            return set()
        first_ts: Dict[str, float] = {}
        for e in read_events(telemetry_dir):
            sid, ts = e.get("session_id"), e.get("ts")
            if (
                sid
                and sid not in first_ts
                and isinstance(ts, (int, float))
                and not isinstance(ts, bool)
            ):
                first_ts[sid] = float(ts)
        starts = list(first_ts.values())
        return {
            name
            for name, acked in latest.items()
            if sum(1 for s in starts if s > acked) < _SNOOZE_WINDOW_SESSIONS
        }
    except Exception:
        return set()


def _attach_linked_neighbors(worklist: List[dict], memory_dir: str) -> None:
    """Annotate each worklist item with its 1-hop graph neighborhood (GRA-9, report-only).

    Adds a ``"linked"`` column: up to ``_MAX_LINKED_NEIGHBORS`` stems (sorted, JSON-safe
    list) adjacent to the item in EITHER direction — untyped ``[[wikilinks]]`` (inbound +
    outbound) unioned with GRA-4 typed edges (all ``TYPED_RELATIONS``, both directions) —
    deduped, EXCLUDING names already independently on the worklist (they get their own
    line; re-listing them as neighbors would double-report). Routed through the ONE
    canonical cache-aware graph API (``links.build_graph``): the SessionStart dispatcher's
    ``refresh_index`` has usually just re-persisted ``links.json``, so this is a stat
    sweep, not a corpus re-read. Degrades to NO column — the worklist is byte-for-byte its
    pre-GRA-9 self — when the graph is unavailable (no corpus, no cache, any graph
    failure). Mutates ``worklist`` in place; zero writes anywhere else; never raises.
    """
    try:
        from .links import TYPED_RELATIONS, build_graph

        try:
            from .build_index import default_index_dir

            index_dir: Optional[str] = default_index_dir(memory_dir)
        except Exception:
            index_dir = None
        graph = build_graph(memory_dir, index_dir=index_dir)
        if graph is None:
            return
        on_worklist = {item["name"] for item in worklist}
        for item in worklist:
            neighbors: Set[str] = graph.inbound(item["name"]) | graph.outbound(item["name"])
            for rel in TYPED_RELATIONS:
                neighbors |= graph.typed_inbound(item["name"], rel)
                neighbors |= graph.typed_outbound(item["name"], rel)
            item["linked"] = sorted(neighbors - on_worklist)[:_MAX_LINKED_NEIGHBORS]
    except Exception:
        return  # graph trouble must never cost the caller the worklist itself


def recalled_stale_worklist(
    memory_dir: str,
    repo_root: str,
    telemetry_dir: Optional[str] = None,
    window_sessions: int = _DEFAULT_WINDOW_SESSIONS,
    *,
    since: Optional[str] = None,
    stale: Optional[List[dict]] = None,
    watermark_stale: Optional[List[dict]] = None,
    diagnostics: Optional[dict] = None,
) -> List[dict]:
    """``[{"name", "changed_paths"[, "linked"]}]`` — recently-recalled names ∩ ``find_stale()``'s stale set.

    Most-recently-drifted first (the order ``find_stale()`` already returns; the
    intersection preserves it). ``since`` passes through to ``find_stale`` (its own default
    when omitted) — exposed so hermetic tests can widen the wall-clock-relative window
    (mirrors ``test_staleness.py``'s ``_ALL`` override pattern for pinned-epoch fixtures).
    ``"linked"`` is GRA-9's optional review-adjacent column (see
    ``_attach_linked_neighbors``): present on every item when the link graph is buildable,
    absent everywhere when it is not. LIF-1 thins the intersection to ACTIONABLE-ONLY:
    items whose file already carries ``invalid_after`` are dropped (demote's terminal
    state — recall is already penalizing them, and the SessionStart staleness producer
    still counts them, so nothing silently disappears), as are items with a live
    ``--snooze`` ack (``_snoozed_names`` — expires after ``_SNOOZE_WINDOW_SESSIONS`` new
    sessions). Read-only; never raises; ``[]`` when the ledger is empty or nothing
    intersects.

    LIF-6: ``stale`` accepts a PRECOMPUTED ``find_stale()`` result — the SessionStart
    dispatcher computes it once and hands it to both the staleness and reconsolidation
    producers, instead of each independently re-scanning the same corpus into the same
    git-log window. When omitted (the default — the ``reconsolidate`` CLI and any
    standalone caller), ``find_stale`` is still called here exactly as before. When given,
    it is trusted as-is and ``find_stale`` is never called — ``since`` is then ignored
    (the caller who computed ``stale`` already chose its window).

    GRW-5: ``watermark_stale`` accepts the precomputed ``watermark_stale_candidates()``
    result — commit-precise hits since the last session's episode watermark — and UNIONS
    it in AFTER the recency intersection (precision beats recency: a memory whose cited
    file a fresh commit touched belongs on the worklist whether or not it was recently
    recalled). Dedup is by name with the ``stale``-derived item winning (it carries
    recency/source_commit); the LIF-1 exclusions (``invalid_after``, snooze) apply to the
    UNION, so everything still routes through the ONE ``semantic_reverify`` gate. When
    omitted or empty, behavior is byte-identical to the pre-GRW-5 worklist.

    VOL-1: the stale lane partitions through ``staleness_policy.split_volatile_only`` —
    an item whose EVERY drifted path is registry-listed (``.format volatile_paths``)
    never arms the worklist; an armed item keeps its full path listing. Suppressed names
    union into caller-owned ``diagnostics`` (``DIAG_KEY``, shared with the watermark
    producer's own filter) so rendering surfaces print the count — never silent. The
    ``watermark_stale`` union is NOT re-filtered here: its producer already applied the
    policy, and CLB-3 evidence items must arm regardless of the file they quote.
    """
    try:
        recent = _recently_recalled_names(telemetry_dir, window_sessions)
        if not recent and not watermark_stale:
            return []
        worklist: List[dict] = []
        if recent:
            if stale is None:
                stale = find_stale(memory_dir, repo_root, **({"since": since} if since else {}))
            # LIF-6: shallow-copy each item -- `stale` may now be a CALLER-OWNED list shared
            # with something else (session_start.RunContext.stale), not always a freshly-
            # derived one this function alone holds. `_attach_linked_neighbors` below mutates
            # worklist items in place (adds "linked"); without the copy that mutation would
            # leak back into the caller's `stale` list for any item that's on both, corrupting
            # a shape callers were promised mirrors find_stale's own contract exactly.
            worklist = [dict(item) for item in stale if item["name"] in recent]
            worklist, vol_suppressed = split_volatile_only(worklist, volatile_set(memory_dir))
            note_suppressed(diagnostics, [item["name"] for item in vol_suppressed])
        if watermark_stale:
            on_worklist = {item["name"] for item in worklist}
            for item in watermark_stale:
                if item.get("name") and item["name"] not in on_worklist:
                    worklist.append(dict(item))  # same caller-owned-list discipline as stale
                    on_worklist.add(item["name"])
        if worklist:
            # LIF-1: drop terminal (invalid_after set — demote's chain, or a manual
            # --invalidate) and explicitly-snoozed items BEFORE the linked-column pass,
            # so GRA-9's on-worklist exclusion set matches what actually renders.
            invalidated = invalid_after_map([item["name"] for item in worklist], memory_dir)
            snoozed = _snoozed_names(telemetry_dir)
            worklist = [
                item
                for item in worklist
                if item["name"] not in invalidated and item["name"] not in snoozed
            ]
        if worklist:
            _attach_linked_neighbors(worklist, memory_dir)
        return worklist
    except Exception:
        return []


from .reconsolidate_replay import succession_replay, succession_replay_lines  # TMB-5 (re-export)

# --------------------------------------------------------------------------- #
# Write primitive (per-item, verification-gated — reuses provenance.reverify_file)
# --------------------------------------------------------------------------- #
def _successor_commit_iso(successor_path: str, repo_root: str) -> Optional[str]:
    """The SUCCESSOR file's last-commit time as UTC ISO-8601 — GRW-7's validity boundary.

    ``git_last_commit_with_time`` on the successor ``.md`` itself (its authorship/last-edit
    moment in history), deliberately NOT ``read_source_commit_time`` — that field is the
    successor's cited-CODE baseline, a different fact. ``None`` when the successor is
    uncommitted (just written this session) or outside the repo — the caller's
    ``set_invalid_after`` then falls back to its own now-UTC default. Never raises.
    """
    try:
        rel = os.path.relpath(successor_path, repo_root)
        _sha, epoch = git_last_commit_with_time(rel, repo_root)
        if not epoch:
            return None
        from datetime import datetime, timezone

        return datetime.fromtimestamp(int(epoch), timezone.utc).isoformat()
    except Exception:
        return None



def semantic_reverify(
    name: str,
    outcome: str,
    memory_dir: str,
    repo_root: str,
    *,
    telemetry_dir: Optional[str] = None,
    dry_run: bool = False,
    superseded_by: Optional[str] = None,
) -> dict:
    """Re-ground ONE memory after the memory-master agent has re-verified it, and LOG the verdict.

    ``outcome`` is one of ``{"graduate", "fix", "demote"}``:
      - ``"graduate"`` — content re-read and confirmed still correct as of HEAD. Clears the
        staleness flag via ``provenance.reverify_file`` (body byte-identical, re-baselines
        ``source_commit`` to HEAD).
      - ``"fix"`` — content was wrong, the memory-master EDITED the body to correct it, and
        the corrected content is confirmed current. Also clears the flag (the edit is a
        separate, prior step this function does not perform — it only re-baselines provenance
        once the fix is already in place).
      - ``"demote"`` — content is confirmed WRONG / not worth fixing. Does **NOT** call
        ``reverify_file`` — the staleness flag stays SET (clearing it would hide a
        confirmed-wrong memory from future detection, the FM2 hole this tier closes) —
        and instead CHAINS ``staleness.set_invalid_after`` onto the memory (LIF-1): the
        validity window closes, recall's EXISTING pre-cut penalty demotes it with no
        second manual command, and a best-effort ``refresh_index`` makes that live for
        this session's next recall (mirrors ``archive_memory``'s same-session refresh).
        Verdict-gated and per-item by construction — the chain fires only inside this
        one-memory verdict; the ledger event records ``invalidated`` so the chained
        action is auditable.

    ``superseded_by`` (GRA-4, opt-in, ``demote`` only): the name of the SUCCESSOR memory
    that replaces this one's claim. When given, the ``supersedes`` relation is appended to
    the SUCCESSOR's frontmatter (``links.add_typed_relation`` — additive, body-preserving,
    idempotent), so recall demotes+annotates the loser from the next index refresh on —
    AND (GRW-7) the loser's ``invalid_after`` is stamped at the SUCCESSOR's last-commit
    date (``git_last_commit_with_time`` on the successor file — its authorship moment,
    NOT ``read_source_commit_time``, which is the successor's cited-CODE baseline),
    falling back to now-UTC when the successor is uncommitted (just written this
    session). The succession moment becomes an explicit, auditable validity boundary
    instead of a silent score nudge; the ledger event records both the boundary and the
    successor. One write, not a second stamp — the demote arm's single
    ``set_invalid_after`` call receives the successor timestamp. Explicitly per-item and
    agent-gated: the agent names ONE successor for ONE re-verified memory; there is no
    batch form and nothing here ever fires autonomously. Refused (no writes at all) for
    ``graduate`` AND ``fix`` — a memory just confirmed/re-baselined as CURRENT cannot
    simultaneously be superseded (GRW-7 closed the old fix+superseded_by combination for
    exactly this reason) — and when either endpoint's file is missing (a dangling edge
    must not be born from the engine's own write path).

    The staleness-flag write (when one happens) routes ENTIRELY through the existing
    ``provenance.reverify_file()``, the edge write through the ONE typed-edge primitive,
    and the demote chain through the ONE soft-invalidation primitive
    (``staleness.set_invalid_after``) — no new bulk path can exist. Always logs the
    outcome via ``telemetry.record_reconsolidation_outcome`` (even on ``"demote"``, even
    when ``reverify_file`` is never called) UNLESS a write was refused. Never raises.

    ``cited``/``dropped_citations`` (LIF-3) are passed straight through from
    ``reverify_file``'s result on the graduate/fix outcomes: a re-derivation that DROPPED
    citations (a cited file was renamed/deleted) must ride out to the caller — this is
    the one write path where a memory can silently shrink to zero citations (becoming
    staleness-exempt) with nobody watching. Both stay ``[]`` on demote (nothing is
    re-derived) and on any refusal.

    ``dropped_gone``/``dropped_not_derived`` (LIF-4) ride along the same way — the WHY
    behind each drop, partitioned by ``reverify_file`` where the repo index is in scope.
    This function has no ``repo_files`` outside the ``reverify_file`` call, so the split
    cannot be recomputed downstream; dropping it here is what left the shared renderer
    asserting "no longer in the repo" about files that were never missing.
    ``preserved_not_derived`` (CUR-1) rides along too — the renderer's keep-line needs it.
    """
    result = {
        "name": name,
        "outcome": outcome,
        "cleared": False,
        "cited": [],
        "dropped_citations": [],
        "dropped_gone": [],
        "dropped_not_derived": [],
        "preserved_not_derived": [],
        "invalidated": False,
        "invalid_after": None,
        "edge_written": False,
        "succession_replay": None,
        "logged": False,
        "error": None,
    }
    try:
        if outcome not in _VALID_OUTCOMES:
            result["error"] = f"invalid outcome: {outcome!r}"
            return result
        fname = name if name.endswith(".md") else f"{name}.md"
        successor_path = None
        if superseded_by is not None:
            # Validate BEFORE any write so a refused edge never leaves a half-applied
            # verdict (reverify done, edge missing) behind.
            if outcome not in _EDGE_WRITING_OUTCOMES:
                result["error"] = (
                    f"superseded_by is only valid with outcome demote, not {outcome!r} — "
                    "graduate/fix assert the memory is CURRENT, which contradicts naming a "
                    "successor that replaces it (a supersede stamps the loser's "
                    "invalid_after at the successor's commit date, GRW-7)"
                )
                return result
            sfname = superseded_by if superseded_by.endswith(".md") else f"{superseded_by}.md"
            successor_path = os.path.join(memory_dir, sfname)
            if not os.path.isfile(successor_path):
                result["error"] = f"successor memory not found: {sfname}"
                return result
            if not os.path.isfile(os.path.join(memory_dir, fname)):
                result["error"] = f"memory not found: {fname}"
                return result
        if outcome in _OUTCOMES_THAT_CLEAR_STALENESS:
            repo_files, basename_index = build_repo_file_index(repo_root)
            path = os.path.join(memory_dir, fname)
            rv = reverify_file(path, repo_root, repo_files, basename_index, dry_run=dry_run)
            if rv["error"]:
                result["error"] = rv["error"]
                return result
            result["cleared"] = rv["changed"]
            # LIF-4 + CUR-1: carry the producer's drop partition AND kept set through —
            # this surface has no repo_files of its own to recompute either from.
            for k in ("cited", "dropped_citations", "dropped_gone",
                      "dropped_not_derived", "preserved_not_derived"):
                result[k] = rv.get(k, [])
        demoted_before = None  # COR-16: bytes-before capture for the two-write rollback
        if outcome == "demote":
            # LIF-1: the demote verdict OWNS its terminal state — close the validity
            # window on the memory itself so recall's pre-cut penalty (the EXISTING
            # demotion mechanism) engages with no second manual command. A refusal here
            # (missing file, unparseable frontmatter) aborts before anything is logged,
            # mirroring every other refused write above.
            path = os.path.join(memory_dir, fname)
            if not os.path.isfile(path):
                result["error"] = f"memory not found: {fname}"
                return result
            # GRW-7: when a successor is named, the validity boundary IS the succession
            # moment — the successor's commit date — not the time somebody got around to
            # rendering the verdict. One write: the same set_invalid_after call receives
            # the timestamp (None → its own now-UTC default for an uncommitted successor).
            successor_ts = (
                _successor_commit_iso(successor_path, repo_root)
                if successor_path is not None
                else None
            )
            # COR-16: with a successor named, this is write #1 of a TWO-write verdict
            # (loser's window, then the successor's supersedes edge). Capture the bytes
            # so a failed edge write rolls the invalidation back out — a verdict the
            # caller renders as "refused" must not have half-landed.
            if successor_path is not None and not dry_run:
                with open(path, "r", encoding="utf-8") as fh:
                    demoted_before = fh.read()
            ia = set_invalid_after(path, successor_ts, dry_run=dry_run)
            if ia["error"]:
                result["error"] = ia["error"]
                return result
            result["invalidated"] = bool(ia["changed"])
            result["invalid_after"] = ia.get("invalid_after")
            if result["invalidated"] and not dry_run:
                # Same-session immediacy (mirrors archive_memory): the penalty reads the
                # INDEX's invalid_after, so refresh now instead of waiting for the next
                # SessionStart. Best-effort — a refresh failure never voids the verdict
                # (the next SessionStart refresh_index heals it).
                try:
                    from .build_index import refresh_index

                    refresh_index(memory_dir)
                except Exception:
                    pass
        if successor_path is not None:
            from .links import add_typed_relation

            edge = add_typed_relation(
                successor_path, "supersedes", os.path.splitext(fname)[0], dry_run=dry_run
            )
            if edge["error"]:
                result["error"] = edge["error"]
                if result["invalidated"] and demoted_before is not None:
                    from .provenance import restore_file_bytes

                    undo_err = restore_file_bytes(
                        os.path.join(memory_dir, fname), demoted_before, memory_dir, repo_root
                    )
                    if undo_err:
                        result["error"] += (
                            f" — AND the invalid_after rollback failed ({undo_err}): "
                            f"{fname} is now soft-invalidated without its supersedes "
                            "edge; restore it from git"
                        )
                    else:
                        result["error"] += " — the invalid_after write was rolled back"
                        result["invalidated"] = False
                        result["invalid_after"] = None
                        try:
                            from .build_index import refresh_index

                            refresh_index(memory_dir)
                        except Exception:
                            pass
                return result
            result["edge_written"] = edge["changed"]
            if not dry_run:
                # TMB-5: fires only here, inside the single-item demote+superseded_by
                # verdict (no replay_all verb); see reconsolidate_replay.
                result["succession_replay"] = succession_replay(
                    os.path.splitext(fname)[0],
                    os.path.splitext(os.path.basename(successor_path))[0],
                    memory_dir,
                    telemetry_dir=telemetry_dir,
                )
        replay = result["succession_replay"]
        result["logged"] = record_reconsolidation_outcome(
            name,
            outcome,
            telemetry_dir=telemetry_dir,
            # LIF-1: stamp the chained action onto the demote event so the ledger is an
            # audit trail of what the verdict actually DID, not just that it was rendered.
            invalidated=result["invalidated"] if outcome == "demote" else None,
            # GRW-7: the boundary + the successor make the supersession itself auditable.
            invalid_after=result["invalid_after"],
            superseded_by=superseded_by if successor_path is not None else None,
            # TMB-5: counts only (the ledger's no-sensitive-content contract holds).
            succession_replay=(
                {"harvested": replay["harvested"], **replay["counts"]} if replay else None
            ),
        )
    except Exception as exc:
        result["error"] = str(exc)
    return result


def snooze(
    name: str,
    memory_dir: str,
    *,
    telemetry_dir: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Ack ONE worklist item WITHOUT rendering a verdict (LIF-1's explicit deferral).

    Records ``{"outcome": "snooze"}`` in the reconsolidation ledger — no memory-file
    write at all — and ``recalled_stale_worklist`` skips the name until
    ``_SNOOZE_WINDOW_SESSIONS`` new ledger sessions have started (``_snoozed_names``).
    A snooze EXPIRES by design: only demote's chained ``invalid_after`` is terminal.
    Refuses a name with no memory file (a typo must not silently ack nothing). Per-item
    and agent-gated like every other action here — no batch form exists. A ledger append
    failure is surfaced as an error, not swallowed: the ledger line IS the snooze, so a
    silent no-op would leave the user believing an inert ack. Never raises.
    """
    result = {"name": name, "logged": False, "error": None}
    try:
        fname = name if name.endswith(".md") else f"{name}.md"
        if not os.path.isfile(os.path.join(memory_dir, fname)):
            result["error"] = f"memory not found: {fname}"
            return result
        if dry_run:
            return result
        result["logged"] = record_reconsolidation_outcome(
            os.path.splitext(fname)[0], "snooze", telemetry_dir=telemetry_dir
        )
        if not result["logged"]:
            result["error"] = "reconsolidation ledger append failed — snooze not recorded"
    except Exception as exc:
        result["error"] = str(exc)
    return result


# --------------------------------------------------------------------------- #
# SessionStart producer — registered into session_start.PRODUCERS, never a parallel hook
# --------------------------------------------------------------------------- #
def _linked_note(item: dict) -> str:
    """Render GRA-9's review-adjacent annotation — ``" (+2 linked: y, z)"`` — or ``""``.

    The roadmap's exact form (``X (+2 linked: Y, Z)``); N is the number of neighbors SHOWN
    (the column is already capped at ``_MAX_LINKED_NEIGHBORS``). Empty both for an item
    with no neighbors and for a worklist built without the graph (degraded, no column).
    """
    linked = item.get("linked") or []
    if not linked:
        return ""
    return f" (+{len(linked)} linked: {', '.join(linked)})"


def reconsolidation_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """SILENT (``None``) unless a recently-recalled memory is currently stale.

    Surfaces a bounded, prioritized worklist otherwise — most-recently-drifted first,
    capped at ``_MAX_WORKLIST_ITEMS``. Never raises.

    LIF-6: ``ctx`` (the dispatcher's shared ``RunContext`` — every registered producer's
    signature carries this same trailing parameter, whether or not it reads it) already
    carries the worklist computed ONCE by ``session_start.build_context`` from a single
    ``find_stale`` call; this producer just renders it. When ``ctx`` is ``None`` (any
    standalone call — tests, or this module invoked outside the dispatcher), it falls
    back to deriving the worklist itself exactly as before.
    """
    try:
        worklist = ctx.worklist if ctx is not None else recalled_stale_worklist(memory_dir, repo_root)
    except Exception:
        worklist = []
    if not worklist:
        return None
    shown = worklist[:_MAX_WORKLIST_ITEMS]
    header = (
        f"🧠 Reconsolidation worklist — {len(worklist)} recently-recalled memories cite code "
        "that has since drifted (most-recently-drifted first). Re-ground each against current "
        "code, then render the verdict per item with the reconsolidate MCP tool "
        "(action='reverify', name=…, outcome=graduate|fix|demote) — /hippo:consolidate "
        "Step 2 drives the same flow in a terminal"
        # INT-18 (DOC-16's lesson): the old text said `provenance --reverify <name>` —
        # not runnable as written (no such command), the wrong verb (the cross-surface
        # path is reconsolidate/INT-13), and /hippo:-token-free, so the Desktop surface
        # note never attached and Desktop users dead-ended.
    )
    if any(item.get("linked") for item in shown):
        # GRA-9: one line of legend, and ONLY when a (+N linked: …) annotation actually
        # renders below — a linkless worklist keeps its pre-GRA-9 header verbatim.
        header += (
            "; (+N linked: …) names an item's 1-hop graph neighbors — review-adjacent, "
            "the next most likely to be wrong once it drifted"
        )
    if any(item.get("watermark") for item in shown):
        # GRW-5: same only-when-it-renders legend discipline — [since-watermark] items are
        # commit-precise hits (commits since the last session touched their cited files),
        # on the list whether or not they were recently recalled.
        header += (
            "; [since-watermark] items were flagged by commits landed since your last "
            "session, recalled recently or not"
        )
    lines = [header + ":"]
    for item in shown:
        paths = ", ".join(item["changed_paths"][:4])
        more = "" if len(item["changed_paths"]) <= 4 else f" (+{len(item['changed_paths']) - 4} more)"
        wm_tag = " [since-watermark]" if item.get("watermark") else ""
        lines.append(f"  • {item['name']}{wm_tag}{_linked_note(item)}: {paths}{more}")
    if len(worklist) > _MAX_WORKLIST_ITEMS:
        lines.append(f"  …and {len(worklist) - _MAX_WORKLIST_ITEMS} more.")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    from .provenance import resolve_dirs

    parser = argparse.ArgumentParser(description="Recall-triggered reconsolidation worklist (read-only).")
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--telemetry-dir", default=None)
    parser.add_argument("--window-sessions", type=int, default=_DEFAULT_WINDOW_SESSIONS)
    parser.add_argument(
        "--reverify",
        metavar="NAME",
        default=None,
        help="apply ONE semantic_reverify verdict (requires --outcome). Per-memory and "
        "verification-gated by design — the agent re-reads the memory first; there is no "
        "bulk form. NAME is the slug, with or without .md",
    )
    parser.add_argument(
        "--outcome",
        choices=sorted(_VALID_OUTCOMES),
        default=None,
        help="the re-verification verdict for --reverify (graduate/fix clear the staleness "
        "flag; demote never does — it chains invalid_after instead, so recall demotes the "
        "memory with no second command)",
    )
    parser.add_argument(
        "--snooze",
        metavar="NAME",
        default=None,
        help="ack ONE worklist item without a verdict: logged in the reconsolidation "
        f"ledger, and the worklist skips it until {_SNOOZE_WINDOW_SESSIONS} new sessions "
        "have started (then re-nags — a snooze expires; only demote is terminal). "
        "Per-memory by design — there is no bulk form. NAME is the slug, with or "
        "without .md",
    )
    parser.add_argument(
        "--superseded-by",
        metavar="SUCCESSOR",
        default=None,
        help="GRA-4 opt-in (demote only): name the SUCCESSOR memory that replaces this "
        "one's claim — appends `supersedes: [NAME]` to the successor's frontmatter AND "
        "stamps the loser's invalid_after at the successor's commit date (GRW-7), so the "
        "supersession is an auditable boundary. One successor, one memory, never autonomous.",
    )
    parser.add_argument("--dry-run", action="store_true", help="report only; do not write")
    args = parser.parse_args(argv)

    memory_dir, repo_root = resolve_dirs()
    memory_dir = args.memory_dir or memory_dir
    repo_root = args.repo_root or repo_root

    if args.snooze and args.reverify:
        print("snooze: --snooze and --reverify are mutually exclusive (one item, one action per call)")
        return 1
    if args.snooze:
        r = snooze(args.snooze, memory_dir, telemetry_dir=args.telemetry_dir, dry_run=args.dry_run)
        base = args.snooze if args.snooze.endswith(".md") else f"{args.snooze}.md"
        if r["error"]:
            print(f"snooze {base}: refused — {r['error']}")
            return 1
        verb = "would be " if args.dry_run else ""
        print(
            f"snooze {base}: ack {verb}logged — the worklist skips it until "
            f"{_SNOOZE_WINDOW_SESSIONS} new sessions have started"
        )
        return 0

    if args.reverify:
        if not args.outcome:
            print("reverify: --outcome is required (graduate|fix|demote)")
            return 1
        r = semantic_reverify(
            args.reverify,
            args.outcome,
            memory_dir,
            repo_root,
            telemetry_dir=args.telemetry_dir,
            dry_run=args.dry_run,
            superseded_by=args.superseded_by,
        )
        base = args.reverify if args.reverify.endswith(".md") else f"{args.reverify}.md"
        if r["error"]:
            print(f"reverify {base}: refused — {r['error']}")
            return 1
        verb = "would be " if args.dry_run else ""
        bits = [f"outcome={r['outcome']}"]
        bits.append(f"staleness flag {verb}cleared" if r["cleared"] else "staleness flag unchanged")
        if args.outcome == "demote":
            # LIF-1: name the chained action so the one-command demote is legible.
            boundary = (
                f" to {r['invalid_after']} (the successor's commit date)"
                if args.superseded_by and r.get("invalid_after")
                else ""
            )
            bits.append(
                f"invalid_after {verb}set{boundary} — recall's pre-cut penalty engages with no second command"
                if r["invalidated"]
                else "invalid_after unchanged"
            )
        if args.superseded_by:
            bits.append(
                f"supersedes edge {verb}written to {args.superseded_by}"
                if r["edge_written"]
                else "supersedes edge already present"
            )
        bits.append("logged" if r["logged"] else "not logged")
        print(f"reverify {base}: " + "; ".join(bits))
        # TMB-5: per-query PASS/FAIL/INCONCLUSIVE lines, printed at verdict time.
        for ln in succession_replay_lines(
            os.path.splitext(base)[0], args.superseded_by or "", r.get("succession_replay")
        ):
            print(ln)
        # LIF-3: the ONE shared rot rendering (provenance.citation_rot_lines) — a graduate/fix
        # re-derivation that dropped citations must be as loud here as on the provenance CLI.
        from .provenance import citation_rot_lines

        for ln in citation_rot_lines(base, r, dry_run=args.dry_run):
            print(ln)
        return 0

    # GRW-5: the CLI listing carries the same commit-precise watermark lane as the
    # SessionStart dispatcher — the drain and the producer must describe the SAME worklist.
    diagnostics: dict = {}
    worklist = recalled_stale_worklist(
        memory_dir,
        repo_root,
        telemetry_dir=args.telemetry_dir,
        window_sessions=args.window_sessions,
        watermark_stale=watermark_stale_candidates(
            memory_dir, repo_root, telemetry_dir=args.telemetry_dir, diagnostics=diagnostics
        ),
        diagnostics=diagnostics,
    )
    # VOL-1: what policy suppressed is printed with the listing it was suppressed FROM —
    # both lanes report into the one diagnostics dict; suppression is never silent.
    suppressed = diagnostics.get("volatile_suppressed") or []
    if not worklist:
        print("No recently-recalled memory is currently stale.")
        if suppressed:
            print(suppressed_count_note(len(suppressed)))
        return 0
    print(
        f"{len(worklist)} memories need re-grounding (recently recalled + stale, or "
        "[since-watermark] commit-precise hits):"
    )
    for item in worklist:
        # Same "X (+2 linked: Y, Z)" review-adjacent render as the SessionStart producer
        # (GRA-9) — the CLI and the producer must describe the same worklist identically.
        wm_tag = " [since-watermark]" if item.get("watermark") else ""
        print(f"  • {item['name']}{wm_tag}{_linked_note(item)}: {', '.join(item['changed_paths'][:6])}")
    if suppressed:
        print("  " + suppressed_count_note(len(suppressed)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
