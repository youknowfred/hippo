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
unparseable frontmatter) — there is NO new write primitive and therefore no new bulk
re-baseline path (mirrors ``reverify_head_only_no_bulk``).

Read-mostly; never raises.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Set

from .provenance import build_repo_file_index, reverify_file
from .staleness import find_stale
from .telemetry import read_events, record_reconsolidation_outcome

_DEFAULT_WINDOW_SESSIONS = 10
_MAX_WORKLIST_ITEMS = 20
_VALID_OUTCOMES = frozenset({"graduate", "fix", "demote"})
# Outcomes that legitimately clear the staleness flag -- a "demote" must NEVER re-baseline
# source_commit (that would hide a CONFIRMED-WRONG memory from future staleness detection,
# exactly the FM2 hole this tier exists to close). Tier 3's invalid_after is the actual
# demotion primitive, not this module.
_OUTCOMES_THAT_CLEAR_STALENESS = frozenset({"graduate", "fix"})


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


def recalled_stale_worklist(
    memory_dir: str,
    repo_root: str,
    telemetry_dir: Optional[str] = None,
    window_sessions: int = _DEFAULT_WINDOW_SESSIONS,
    *,
    since: Optional[str] = None,
) -> List[dict]:
    """``[{"name", "changed_paths"}]`` — recently-recalled names ∩ ``find_stale()``'s stale set.

    Most-recently-drifted first (the order ``find_stale()`` already returns; the
    intersection preserves it). ``since`` passes through to ``find_stale`` (its own default
    when omitted) — exposed so hermetic tests can widen the wall-clock-relative window
    (mirrors ``test_staleness.py``'s ``_ALL`` override pattern for pinned-epoch fixtures).
    Read-only; never raises; ``[]`` when the ledger is empty or nothing intersects.
    """
    try:
        recent = _recently_recalled_names(telemetry_dir, window_sessions)
        if not recent:
            return []
        stale = find_stale(memory_dir, repo_root, **({"since": since} if since else {}))
        return [item for item in stale if item["name"] in recent]
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
        confirmed-wrong memory from future detection, the FM2 hole this tier closes). The
        caller is responsible for any further action (e.g. Tier 3's ``invalid_after``, or
        archiving) — this function only logs the verdict.

    The frontmatter write (when one happens) routes ENTIRELY through the existing
    ``provenance.reverify_file()`` — no new write primitive, so no new bulk path can exist.
    Always logs the outcome via ``telemetry.record_reconsolidation_outcome`` (even on
    ``"demote"``, even when ``reverify_file`` is never called). Never raises.
    """
    result = {"name": name, "outcome": outcome, "cleared": False, "logged": False, "error": None}
    try:
        if outcome not in _VALID_OUTCOMES:
            result["error"] = f"invalid outcome: {outcome!r}"
            return result
        if outcome in _OUTCOMES_THAT_CLEAR_STALENESS:
            repo_files, basename_index = build_repo_file_index(repo_root)
            fname = name if name.endswith(".md") else f"{name}.md"
            path = os.path.join(memory_dir, fname)
            rv = reverify_file(path, repo_root, repo_files, basename_index, dry_run=dry_run)
            if rv["error"]:
                result["error"] = rv["error"]
                return result
            result["cleared"] = rv["changed"]
        result["logged"] = record_reconsolidation_outcome(name, outcome, telemetry_dir=telemetry_dir)
    except Exception as exc:
        result["error"] = str(exc)
    return result


# --------------------------------------------------------------------------- #
# SessionStart producer — registered into session_start.PRODUCERS, never a parallel hook
# --------------------------------------------------------------------------- #
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
    lines = [
        f"🧠 Reconsolidation worklist — {len(worklist)} recently-recalled memories cite code "
        "that has since drifted (most-recently-drifted first). Re-ground each against current "
        "code, then `provenance --reverify <name>` once confirmed correct:"
    ]
    for item in worklist[:_MAX_WORKLIST_ITEMS]:
        paths = ", ".join(item["changed_paths"][:4])
        more = "" if len(item["changed_paths"]) <= 4 else f" (+{len(item['changed_paths']) - 4} more)"
        lines.append(f"  • {item['name']}: {paths}{more}")
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
    args = parser.parse_args(argv)

    memory_dir, repo_root = resolve_dirs()
    memory_dir = args.memory_dir or memory_dir
    repo_root = args.repo_root or repo_root

    worklist = recalled_stale_worklist(
        memory_dir, repo_root, telemetry_dir=args.telemetry_dir, window_sessions=args.window_sessions
    )
    if not worklist:
        print("No recently-recalled memory is currently stale.")
        return 0
    print(f"{len(worklist)} recently-recalled memories cite code that changed since they were written:")
    for item in worklist:
        print(f"  • {item['name']}: {', '.join(item['changed_paths'][:6])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
