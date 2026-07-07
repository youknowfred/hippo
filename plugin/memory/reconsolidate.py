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

Read-mostly; never raises.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Set

from .provenance import build_repo_file_index, reverify_file
from .staleness import find_stale, invalid_after_map, set_invalid_after
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
# "graduate" is excluded — confirmed-correct and superseded are contradictory verdicts.
_EDGE_WRITING_OUTCOMES = frozenset({"fix", "demote"})


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
    """
    try:
        recent = _recently_recalled_names(telemetry_dir, window_sessions)
        if not recent:
            return []
        stale = find_stale(memory_dir, repo_root, **({"since": since} if since else {}))
        worklist = [item for item in stale if item["name"] in recent]
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


# --------------------------------------------------------------------------- #
# Write primitive (per-item, verification-gated — reuses provenance.reverify_file)
# --------------------------------------------------------------------------- #
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

    ``superseded_by`` (GRA-4, opt-in, ``demote``/``fix`` outcomes only): the name of the
    SUCCESSOR memory that replaces this one's claim. When given, the ``supersedes``
    relation is appended to the SUCCESSOR's frontmatter (``links.add_typed_relation`` —
    additive, body-preserving, idempotent), so recall demotes+annotates the loser from the
    next index refresh on. Explicitly per-item and agent-gated: the agent names ONE
    successor for ONE re-verified memory; there is no batch form and nothing here ever
    fires autonomously. Refused (no writes at all) for ``graduate`` — a memory just
    confirmed CORRECT cannot simultaneously be superseded — and when either endpoint's
    file is missing (a dangling edge must not be born from the engine's own write path).

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
    """
    result = {
        "name": name,
        "outcome": outcome,
        "cleared": False,
        "cited": [],
        "dropped_citations": [],
        "invalidated": False,
        "edge_written": False,
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
                    f"superseded_by is only valid with outcomes "
                    f"{sorted(_EDGE_WRITING_OUTCOMES)}, not {outcome!r}"
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
            result["cited"] = rv["cited"]
            result["dropped_citations"] = rv["dropped_citations"]
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
            ia = set_invalid_after(path, dry_run=dry_run)
            if ia["error"]:
                result["error"] = ia["error"]
                return result
            result["invalidated"] = bool(ia["changed"])
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
                return result
            result["edge_written"] = edge["changed"]
        result["logged"] = record_reconsolidation_outcome(
            name,
            outcome,
            telemetry_dir=telemetry_dir,
            # LIF-1: stamp the chained action onto the demote event so the ledger is an
            # audit trail of what the verdict actually DID, not just that it was rendered.
            invalidated=result["invalidated"] if outcome == "demote" else None,
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


def reconsolidation_producer(memory_dir: str, repo_root: str) -> Optional[str]:
    """SILENT (``None``) unless a recently-recalled memory is currently stale.

    Surfaces a bounded, prioritized worklist otherwise — most-recently-drifted first,
    capped at ``_MAX_WORKLIST_ITEMS``. Never raises.
    """
    try:
        worklist = recalled_stale_worklist(memory_dir, repo_root)
    except Exception:
        worklist = []
    if not worklist:
        return None
    shown = worklist[:_MAX_WORKLIST_ITEMS]
    header = (
        f"🧠 Reconsolidation worklist — {len(worklist)} recently-recalled memories cite code "
        "that has since drifted (most-recently-drifted first). Re-ground each against current "
        "code, then `provenance --reverify <name>` once confirmed correct"
    )
    if any(item.get("linked") for item in shown):
        # GRA-9: one line of legend, and ONLY when a (+N linked: …) annotation actually
        # renders below — a linkless worklist keeps its pre-GRA-9 header verbatim.
        header += (
            "; (+N linked: …) names an item's 1-hop graph neighbors — review-adjacent, "
            "the next most likely to be wrong once it drifted"
        )
    lines = [header + ":"]
    for item in shown:
        paths = ", ".join(item["changed_paths"][:4])
        more = "" if len(item["changed_paths"]) <= 4 else f" (+{len(item['changed_paths']) - 4} more)"
        lines.append(f"  • {item['name']}{_linked_note(item)}: {paths}{more}")
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
        help="GRA-4 opt-in (demote/fix only): name the SUCCESSOR memory that replaces this "
        "one's claim — appends `supersedes: [NAME]` to the successor's frontmatter so "
        "recall demotes+annotates the loser. One successor, one memory, never autonomous.",
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
            bits.append(
                f"invalid_after {verb}set — recall's pre-cut penalty engages with no second command"
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
        # LIF-3: the ONE shared rot rendering (provenance.citation_rot_lines) — a graduate/fix
        # re-derivation that dropped citations must be as loud here as on the provenance CLI.
        from .provenance import citation_rot_lines

        for ln in citation_rot_lines(base, r["cited"], r["dropped_citations"], dry_run=args.dry_run):
            print(ln)
        return 0

    worklist = recalled_stale_worklist(
        memory_dir, repo_root, telemetry_dir=args.telemetry_dir, window_sessions=args.window_sessions
    )
    if not worklist:
        print("No recently-recalled memory is currently stale.")
        return 0
    print(f"{len(worklist)} recently-recalled memories cite code that changed since they were written:")
    for item in worklist:
        # Same "X (+2 linked: Y, Z)" review-adjacent render as the SessionStart producer
        # (GRA-9) — the CLI and the producer must describe the same worklist identically.
        print(f"  • {item['name']}{_linked_note(item)}: {', '.join(item['changed_paths'][:6])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
