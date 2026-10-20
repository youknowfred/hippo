"""Doctor checks for what ``.claude/`` carries into worktrees.

SHP-7: a linked git worktree's dead ``.claude/.memory-*`` copies, named. MIG-8: pre-MIG-6
corpus backups still in ``.claude/``, which the Claude desktop app copies into every worktree
it creates, named with their size. MIG-9: the derived dirs moved to ``<project>/.hippo/``;
what is still in ``.claude/``, and the old copies existing worktrees carry, named.

Sibling of ``doctor_checks_env`` (CONTRIBUTING.md "Code layout": the env module sat at the
900-line cap, so the worktree check lands in its own prefix-named module; ``doctor`` imports
it, it never imports the façade). Pairs with ``check_corpus_resolution`` (env), which names
WHICH tree resolution started from — this check names what the redirect left behind.
"""

from __future__ import annotations

import os
import shlex
from typing import Dict, List, Tuple

from .doctor_checks_env import DoctorContext
from .provenance import resolve_corpus_start
from .provenance_snapshot import dir_size, legacy_snapshot_dirs, snapshot_root


# The derived, gitignored dirs that move WITH the corpus (SHP-7), by their pre-MIG-9 names:
# the ones a worktree's copied ``.claude/`` can still hold.
_DERIVED_SIBLINGS = (
    (".memory-pending", "pending queue", "pending"),
    (".memory-index", "index", "index"),
    (".memory-telemetry", "telemetry", "telemetry"),
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
        from .derived_dirs import derived_dir

        wt = info.get("linked_worktree") or ""
        dead: List[str] = []
        stranded = 0
        for dirname, label, kind in _DERIVED_SIBLINGS:
            cand = os.path.join(wt, ".claude", dirname)
            live = derived_dir(ctx.memory_dir, kind)
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


def _mb(nbytes: int) -> str:
    return f"{nbytes / 1e6:.1f} MB" if nbytes >= 100_000 else f"{max(1, round(nbytes / 1e3))} KB"


def _migration_unfinished(ctx: DoctorContext) -> str:
    """'' when the citation migration is done (stamped current, worklist empty), else why not.

    The worklist re-derives every memory, so it is computed only here — when backups exist
    to be called spent — and only once the stamp says the migration finished."""
    from .provenance import CITATION_DERIVATION_VERSION, read_cite_derivation, rederive_worklist

    cite = read_cite_derivation(ctx.memory_dir)
    if cite < CITATION_DERIVATION_VERSION:
        return (f"the citation derivation is not stamped yet (v{cite}; this plugin derives "
                f"v{CITATION_DERIVATION_VERSION})")
    left = len(rederive_worklist(ctx.memory_dir, ctx.repo_root))
    return f"the re-derivation worklist still lists {left} memory(ies)" if left else ""


def check_snapshots(ctx: DoctorContext) -> Dict[str, str]:
    """MIG-8: pre-MIG-6 backups still in ``.claude/`` (``memory.pre-cite*/``), with their size.

    Until MIG-6 every ``rederive``/``tend`` snapshot copied the corpus to
    ``.claude/memory.pre-cite2-<stamp>/``, and the desktop app copies ``.claude/`` (ignored
    files included) into every worktree it creates — 53 worktrees x 44 MB of spent backups on
    the disk that filled on 2026-10-19. Once the citation derivation is stamped and the
    worklist is empty they are spent: warn, so they get moved or deleted. Before that they are
    still the undo: named, ok, with where to move them so they stop riding along. Read-only.
    """
    try:
        legacy = legacy_snapshot_dirs(ctx.memory_dir)
        root = snapshot_root(ctx.memory_dir)
        if not legacy:
            kept = [e.path for e in os.scandir(root) if e.is_dir()] if os.path.isdir(root) else []
            where = (
                f"; {len(kept)} in {root} ({_mb(sum(dir_size(p)[0] for p in kept))}), outside "
                ".claude/, so worktrees do not carry them" if kept else ""
            )
            return {"status": "ok", "message": f"snapshots: none in .claude/{where}."}
        sizes = [dir_size(p) for p in legacy]
        nbytes, nfiles = sum(b for b, _ in sizes), sum(f for _, f in sizes)
        names = [os.path.basename(p) for p in legacy]
        shown = ", ".join(names[:4]) + (f" (+{len(names) - 4} more)" if len(names) > 4 else "")
        parent = os.path.dirname(legacy[0])
        head = (f"snapshots: {len(legacy)} pre-migration backup(s) in {parent} "
                f"({_mb(nbytes)}, {nfiles:,} files): {shown}")
        rides = (
            "The Claude desktop app copies .claude/ into every worktree it creates, so each new "
            "worktree carries them. " if os.path.basename(parent) == ".claude" else ""
        )
        unfinished = _migration_unfinished(ctx)
        if unfinished:
            return {
                "status": "ok",
                "message": f"{head} — kept: still the undo, as {unfinished}. {rides}Move them "
                f"into {root}/ to keep them there instead.",
            }
        return {
            "status": "warn",
            "message": f"{head} — spent: the citation derivation is stamped and the "
            f"re-derivation worklist is empty. {rides}Delete them, or move them into {root}/ "
            "to keep them.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"snapshots check failed: {exc}."}


_KIND_LABELS = {"index": "index", "telemetry": "telemetry", "pending": "pending queue"}


def _worktree_copies_total(main_root: str) -> Tuple[int, int]:
    """``(worktrees, bytes)``: old ``.claude/.memory-*`` copies the desktop app put in the
    worktrees under ``<main>/.claude/worktrees/``. Nothing reads them (SHP-7)."""
    from .derived_dirs import LEGACY_DIRNAMES

    trees = total = 0
    base = os.path.join(main_root, ".claude", "worktrees")
    try:
        entries = sorted(os.scandir(base), key=lambda e: e.name)
    except OSError:
        return 0, 0
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        sizes = [
            dir_size(p)[0]
            for p in (os.path.join(entry.path, ".claude", n) for n in LEGACY_DIRNAMES.values())
            if os.path.isdir(p) and not os.path.islink(p)
        ]
        if sizes:
            trees += 1
            total += sum(sizes)
    return trees, total


def check_derived_dirs(ctx: DoctorContext) -> Dict[str, str]:
    """MIG-9/MIG-10: the index, telemetry and pending queue live in ``<project>/.hippo/``.

    Names what is still in ``.claude/``, where the Claude desktop app copies it into every
    worktree it creates: an old dir SessionStart has not moved yet (warn), entries an older
    hippo wrote after the move that the fold would not overwrite (warn), or a telemetry stub
    kept for a sleep schedule's log (ok, with the reinstall step). In the main tree it also
    totals the old copies existing worktrees carry, with the command that deletes them.
    Read-only.
    """
    try:
        from .derived_dirs import legacy_dir, leftovers

        home, found = leftovers(ctx.memory_dir)
        if home is None:
            return {
                "status": "ok",
                "message": f"derived dirs: beside {ctx.memory_dir} (not a <project>/.claude/memory "
                "corpus, so nothing rides in .claude/).",
            }
        claude = os.path.dirname(os.path.abspath(ctx.memory_dir))
        parts: List[str] = []
        warn = False
        unmoved = [k for k, (state, _n) in found.items() if state == "unmoved"]
        if unmoved:
            warn = True
            paths = [legacy_dir(ctx.memory_dir, k) for k in unmoved]
            size = sum(dir_size(p)[0] for p in paths)
            parts.append(
                f"{', '.join(_KIND_LABELS[k] for k in unmoved)} still in {claude} ({_mb(size)}) — "
                f"the next session start moves {'it' if len(unmoved) == 1 else 'them'} into {home}; "
                "until then the Claude desktop app copies them into every worktree it creates. "
                "If this line stays after a new session, move by hand: "
                + "; ".join(f"mv {p} {os.path.join(home, k)}" for p, k in zip(paths, unmoved))
            )
        for kind, (state, n) in found.items():
            legacy = legacy_dir(ctx.memory_dir, kind)
            if state == "leftover":
                warn = True
                parts.append(
                    f"{legacy} holds {n} entr{'y' if n == 1 else 'ies'} that {home}/{kind} also "
                    "has — an older hippo wrote them after the move, and hippo never overwrites "
                    f"the live copy. Compare, then delete {legacy}"
                )
            elif state == "stub":
                parts.append(
                    f"{legacy} stays as an empty stub because a scheduled `hippo sleep` logs into "
                    "it. Reinstall the schedule from `hippo sleep --print-schedule`, then delete "
                    "the stub"
                )
        if not parts:
            parts.append(f"in {home}, outside .claude/, so worktrees do not carry them")
        if resolve_corpus_start().get("tree") != "main-tree":
            trees, nbytes = _worktree_copies_total(os.path.dirname(claude))
            if trees:
                parts.append(
                    f"{trees} worktree(s) under {claude}/worktrees still carry old copies "
                    f"({_mb(nbytes)}) that nothing reads. Delete them: find "
                    f"{shlex.quote(os.path.join(claude, 'worktrees'))} -mindepth 3 -maxdepth 3 "
                    "-type d -path '*/.claude/.memory-*' -exec rm -rf {} +"
                )
        return {"status": "warn" if warn else "ok", "message": "derived dirs: " + ". ".join(parts) + "."}
    except Exception as exc:
        return {"status": "warn", "message": f"derived-dirs check failed: {exc}."}
