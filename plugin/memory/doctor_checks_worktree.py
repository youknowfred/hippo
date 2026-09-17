"""SHP-7 doctor check: a linked git worktree's dead ``.claude/.memory-*`` copies, named.

Sibling of ``doctor_checks_env`` (CONTRIBUTING.md "Code layout": the env module sat at the
900-line cap, so the worktree check lands in its own prefix-named module; ``doctor`` imports
it, it never imports the façade). Pairs with ``check_corpus_resolution`` (env), which names
WHICH tree resolution started from — this check names what the redirect left behind.
"""

from __future__ import annotations

import os
from typing import Dict, List

from .doctor_checks_env import DoctorContext
from .provenance import resolve_corpus_start


# The derived, gitignored siblings of ``.claude/memory`` that move WITH the corpus (SHP-7).
_DERIVED_SIBLINGS = (
    (".memory-pending", "pending queue"),
    (".memory-index", "index"),
    (".memory-telemetry", "telemetry"),
)


def _seed_count(pending_dir: str) -> int:
    try:
        return sum(
            1 for e in os.scandir(pending_dir) if e.name.endswith(".json") and not e.name.startswith(".")
        )
    except OSError:
        return 0


def check_worktree_copies(ctx: DoctorContext) -> Dict[str, str]:
    """SHP-7: a linked worktree's OWN ``.claude/.memory-*`` dirs are dead copies — name them.

    Before the redirect, a worktree-launched MCP server drained and rebuilt siblings of the
    worktree's committed snapshot: a ``.memory-pending`` queue with the same filenames as
    the live one but different inodes, an index nobody recalls through, a telemetry ledger
    that vanished with the worktree. Resolution now skips them, but anything already there
    is exactly the stale state this check makes visible: a dead pending queue WITH seeds
    warns (they will never be drained from here — copies or orphans of the queue that lives
    in the main tree); empty dead dirs are noted. The worktree's ``.claude/memory`` itself
    is the branch's committed snapshot, which is normal, so it is named, never flagged.
    """
    try:
        info = resolve_corpus_start()
        if info.get("tree") != "main-tree":
            return {
                "status": "ok",
                "message": "worktree copies: n/a — resolution did not redirect from a linked worktree.",
            }
        wt = info.get("linked_worktree") or ""
        live_claude = os.path.dirname(os.path.abspath(ctx.memory_dir))
        dead: List[str] = []
        stranded = 0
        for dirname, label in _DERIVED_SIBLINGS:
            cand = os.path.join(wt, ".claude", dirname)
            live = os.path.join(live_claude, dirname)
            if not os.path.isdir(cand) or os.path.realpath(cand) == os.path.realpath(live):
                continue
            if dirname == ".memory-pending":
                n = _seed_count(cand)
                stranded += n
                dead.append(f"{label} {cand} ({n} seed(s); the live queue {live} has {_seed_count(live)})")
            else:
                dead.append(f"{label} {cand}")
        snapshot = os.path.join(wt, ".claude", "memory")
        snap_note = (
            f" This worktree's {snapshot} is the branch's committed snapshot, not the live corpus."
            if os.path.isdir(snapshot) and os.path.realpath(snapshot) != os.path.realpath(ctx.memory_dir)
            else ""
        )
        if not dead:
            return {
                "status": "ok",
                "message": f"worktree copies: none — linked worktree {wt} carries no dead derived "
                f"dirs.{snap_note}",
            }
        verdict = (
            f"{stranded} capture seed(s) sit in a DEAD pending queue and will never be drained from here"
            if stranded
            else "stale copies nothing reads"
        )
        return {
            "status": "warn" if stranded else "ok",
            "message": f"worktree copies: {len(dead)} dead derived dir(s) in linked worktree {wt} — "
            f"{verdict}: " + "; ".join(dead) + ". Resolution now targets the main tree; delete "
            f"these copies (rm -rf) or retire the worktree.{snap_note}",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"worktree-copies check failed: {exc}."}
