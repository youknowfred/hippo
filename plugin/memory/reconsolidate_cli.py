"""The ``python -m memory.reconsolidate`` CLI — split out of ``reconsolidate.py`` by RWY-1 as
pure code motion so the façade keeps runway. ``reconsolidate.main`` stays importable at its
old path. ``main()`` imports the ``reconsolidate`` names it uses at call time, so a test that
monkeypatches ``memory.reconsolidate.<name>`` still steers it.
"""

from __future__ import annotations

import os
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    from .provenance import resolve_dirs
    from .reconsolidate import (
        DIAG_TYPE_KEY,
        _DEFAULT_WINDOW_SESSIONS,
        _SNOOZE_WINDOW_SESSIONS,
        _VALID_OUTCOMES,
        _linked_note,
        recalled_stale_worklist,
        semantic_reverify,
        snooze,
        succession_replay_lines,
        suppressed_count_note,
        type_exempt_count_note,
        watermark_stale_candidates,
    )

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
        help="opt-in (demote only): name the SUCCESSOR memory that replaces this "
        "one's claim — appends `supersedes: [NAME]` to the successor's frontmatter AND "
        "stamps the loser's invalid_after at the successor's commit date, so the "
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
    # VOL-1 + TYPE-1: what policy suppressed is printed with the listing it was suppressed
    # FROM — both lanes report into the one diagnostics dict; suppression is never silent.
    suppressed = diagnostics.get("volatile_suppressed") or []
    type_exempt = diagnostics.get(DIAG_TYPE_KEY) or []
    if not worklist:
        print("No recently-recalled memory is currently stale.")
        if suppressed:
            print(suppressed_count_note(len(suppressed)))
        if type_exempt:
            print(type_exempt_count_note(len(type_exempt)))
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
    if type_exempt:
        print("  " + type_exempt_count_note(len(type_exempt)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
