"""RWY-6: the old derived-dir copies worktrees carry, removed one directory per explicit yes.

Before MIG-9 the index, telemetry and pending queue lived in ``.claude/.memory-*``, and the
Claude desktop app copies ``.claude/`` into every worktree it creates under
``<main>/.claude/worktrees/``. A session in such a worktree resolves to the MAIN tree's corpus
(SHP-7), so its own copies are read by nothing, whether or not the worktree is still in use.
Doctor counts them (``doctor_checks_worktree.check_derived_dirs``); this is the offer that
removes them.

The removal is deliberately narrow. A candidate is exactly
``<main>/.claude/worktrees/<one name>/.claude/<one of hippo's old derived dir names>``, a real
directory reached through no symlink. Everything else is refused: a corpus (``.claude/memory``),
the snapshots dir, a path that is one of those shapes only after resolving a symlink, anything
deeper or shallower. Nothing is removed without the caller naming that one directory.
"""

from __future__ import annotations

import os
import shutil
from typing import Dict, List, Optional, Tuple

from .derived_dirs import LEGACY_DIRNAMES

_DERIVED_NAMES = frozenset(LEGACY_DIRNAMES.values())


def _base(main_root: str) -> str:
    return os.path.join(os.path.abspath(main_root), ".claude", "worktrees")


def check_candidate(main_root: str, path: str) -> Optional[str]:
    """None when ``path`` is one of the old copies this offer covers, else why it is refused."""
    try:
        base = _base(main_root)
        p = os.path.abspath(path)
        rel = os.path.relpath(p, base)
        parts = rel.split(os.sep)
        if rel.startswith(os.pardir) or len(parts) != 3 or parts[1] != ".claude":
            return f"not a directory of the form {base}/<worktree>/.claude/<old derived dir>"
        if parts[2] not in _DERIVED_NAMES:
            return (
                f"{parts[2]} is not one of hippo's old derived dirs "
                f"({', '.join(sorted(_DERIVED_NAMES))})"
            )
        # No symlink anywhere on the way down: the worktrees dir, the worktree, its .claude,
        # or the dir itself. A link could point the removal at a live index or a corpus.
        walk = base
        for part in [None, *parts]:
            if part is not None:
                walk = os.path.join(walk, part)
            if os.path.islink(walk):
                return f"{walk} is a symlink; hippo removes only real directories it can see into"
        if not os.path.isdir(p):
            return "not a directory (already removed?)"
        if os.path.realpath(p) != os.path.join(os.path.realpath(base), *parts):
            return "resolves somewhere else through a symlink"
        return None
    except Exception as exc:
        return f"could not be checked: {exc}"


def _size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def list_copies(main_root: str) -> List[Tuple[str, int]]:
    """``[(path, bytes)]`` for every old copy under ``<main>/.claude/worktrees``, sorted.
    The same set doctor counts, minus anything ``check_candidate`` refuses. Never raises."""
    out: List[Tuple[str, int]] = []
    base = _base(main_root)
    try:
        entries = sorted(os.scandir(base), key=lambda e: e.name)
    except OSError:
        return out
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        for name in sorted(_DERIVED_NAMES):
            p = os.path.join(entry.path, ".claude", name)
            if os.path.lexists(p) and check_candidate(main_root, p) is None:
                out.append((p, _size(p)))
    return out


def remove_copy(main_root: str, path: str) -> Dict[str, object]:
    """Remove ONE old copy. ``{"removed", "path", "bytes", "error"}``; the check runs again
    here, so a path that changed shape since the preview is refused, not followed."""
    res: Dict[str, object] = {"removed": False, "path": os.path.abspath(path), "bytes": 0, "error": None}
    why = check_candidate(main_root, path)
    if why:
        res["error"] = why
        return res
    try:
        res["bytes"] = _size(res["path"])
        shutil.rmtree(res["path"])  # raises on a symlink target; never follows links inside
        res["removed"] = True
    except Exception as exc:
        res["error"] = str(exc)
    return res
