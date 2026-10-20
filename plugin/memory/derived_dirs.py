"""Where hippo's derived per-clone dirs live: ``<project>/.hippo/`` (MIG-9, MIG-10, MIG-11).

The recall index, the telemetry ledgers and the capture pending queue used to sit beside the
corpus, in ``.claude/.memory-index``, ``.claude/.memory-telemetry`` and
``.claude/.memory-pending``. The Claude desktop app lays down every worktree it creates with a
copy of the checkout's whole ``.claude/`` directory, ignored files included, so each worktree
carried all three. SHP-7 already resolves a worktree session to the main tree's dirs, so the
copies were never read: on 2026-10-20 em-growth-labs had 50 desktop worktrees carrying 1.66 GB
of them (15 MB index, 13 MB telemetry, 8 MB pending queue apiece).

MIG-9. The dirs now live in ``<project>/.hippo/index``, ``.hippo/telemetry`` and
``.hippo/pending``: beside ``.claude/``, not inside it, on a path derived from the corpus path
alone (the same rule as MIG-6's ``.hippo-snapshots/``, for the same reasons: every surface,
the bash fast paths included, computes it without the plugin data dir, and it lives and dies
with the clone, so the pending queue survives a plugin uninstall). The ``.hippo/`` container
self-ignores (SEC-3). A corpus that is not ``<project>/.claude/memory`` (a ``HIPPO_MEMORY_DIR``
elsewhere, the user tier's ``~/.claude/hippo-memory``) keeps the old sibling dirs: there is no
``.claude/`` for them to ride in. Until SessionStart moves an old dir, ``derived_dir`` reads it
where it is.

MIG-10. SessionStart moves each old dir with one ``rename``. Hooks are pinned to the version
a session launched with, so a session that was open across the update keeps writing the old
paths; every SessionStart folds what it wrote into the live dir (``migrate``). A scheduled
``hippo sleep`` installed from an older recipe appends to ``.claude/.memory-telemetry/
sleep.log`` from the shell, before hippo runs, and that redirect fails if the dir is gone, so
a dir that held a ``sleep.log`` is left behind as an empty stub with a ``MOVED`` note until the
schedule is reinstalled.

Imports only ``provenance_env`` (CONTRIBUTING.md "Code layout"). Nothing here raises.
"""

from __future__ import annotations

import os
import shutil
from typing import Dict, Optional, Tuple

from .provenance_env import DERIVED_HOME_DIRNAME, ensure_self_ignoring_dir

KINDS = ("index", "telemetry", "pending")
LEGACY_DIRNAMES = {
    "index": ".memory-index",
    "telemetry": ".memory-telemetry",
    "pending": ".memory-pending",
}
_ENV_OVERRIDES = {
    "index": "HIPPO_INDEX_DIR",
    "telemetry": "HIPPO_TELEMETRY_DIR",
    "pending": "HIPPO_PENDING_DIR",
}

# The note a telemetry stub carries (MIG-10). Its own ``.gitignore`` keeps it out of git.
STUB_NOTE = "MOVED"
_STUB_TEXT = (
    "hippo's telemetry moved to {new} (v1.42.4).\n"
    "This directory stays only because a scheduled `hippo sleep` appends its log here\n"
    "(the `>> .../sleep.log` in its command). Reinstall the schedule from\n"
    "`hippo sleep --print-schedule`, then delete this directory.\n"
)
_SLEEP_LOG = "sleep.log"
# Fold rules for a name present on both sides. Ledgers are append-only, so the old side's
# lines are appended. Locks, the session token and atomic-write leftovers carry nothing
# the live side lacks. Anything else stays where it is, for doctor to name.
_APPEND_SUFFIXES = {"telemetry": (".jsonl", ".log"), "pending": ()}
_DROP_NAMES = {
    "telemetry": {".rollup.lock", "session"},
    "pending": {".capture-snooze.json"},
}


def derived_home(memory_dir: str) -> Optional[str]:
    """``<project>/.hippo`` when the corpus is ``<project>/.claude/memory``; else None."""
    md = os.path.abspath(memory_dir)
    parent = os.path.dirname(md)
    if os.path.basename(md) != "memory" or os.path.basename(parent) != ".claude":
        return None
    return os.path.join(os.path.dirname(parent), DERIVED_HOME_DIRNAME)


def legacy_dir(memory_dir: str, kind: str) -> str:
    """The pre-MIG-9 location: ``.claude/.memory-<kind>``, beside the corpus."""
    return os.path.join(os.path.dirname(os.path.abspath(memory_dir)), LEGACY_DIRNAMES[kind])


def derived_dir(memory_dir: str, kind: str) -> str:
    """Where ``kind`` lives for this corpus. Callers check their own env override first.

    ``<project>/.hippo/<kind>``, unless only the old ``.claude/.memory-<kind>`` exists: that
    one has not been moved yet (SessionStart moves it), so it is read where the data is. At
    most two stats; this is on the recall hot path.
    """
    legacy = legacy_dir(memory_dir, kind)
    home = derived_home(memory_dir)
    if home is None:
        return legacy
    new = os.path.join(home, kind)
    if os.path.isdir(new) or not os.path.isdir(legacy):
        return new
    if os.path.isfile(os.path.join(legacy, STUB_NOTE)):
        return new  # a telemetry stub kept for a sleep schedule is not the live dir
    return legacy


def is_stub(path: str) -> bool:
    """True for an old telemetry dir kept only as an empty stub for a sleep schedule."""
    try:
        return set(os.listdir(path)) <= {".gitignore", STUB_NOTE} and os.path.isfile(
            os.path.join(path, STUB_NOTE)
        )
    except OSError:
        return False


def _write_stub(legacy: str, new: str) -> None:
    """The dir comes first, so a schedule's redirect works even if the note's write tears."""
    from .atomic import write_text_atomic

    ensure_self_ignoring_dir(legacy)
    try:
        write_text_atomic(os.path.join(legacy, STUB_NOTE), _STUB_TEXT.format(new=new))
    except OSError:
        pass


def _append_file(src: str, dst: str) -> None:
    """Append ``src``'s bytes to ``dst``, on a line boundary."""
    with open(src, "rb") as fh:
        data = fh.read()
    if not data:
        return
    with open(dst, "ab+") as out:
        out.seek(0, os.SEEK_END)
        if out.tell():
            out.seek(-1, os.SEEK_END)
            if out.read(1) != b"\n":
                out.write(b"\n")
        out.write(data)


def _fold_tree(src: str, dst: str, kind: str, seen: Dict[str, bool]) -> int:
    """Move ``src``'s entries into ``dst``; return how many had to stay in ``src``.

    An entry ``dst`` lacks is renamed over. A dir on both sides is folded entry by entry.
    A file on both sides is appended (ledgers), dropped (``_DROP_NAMES`` and ``*.tmp.*``
    write leftovers), or left in place: a seed or a state file is never overwritten.
    """
    left = 0
    for entry in os.scandir(src):
        if entry.name in (".gitignore", STUB_NOTE):
            continue  # an old dir keeps its own marker while anything is left in it (SEC-3)
        s, d = entry.path, os.path.join(dst, entry.name)
        try:
            if entry.name == _SLEEP_LOG:
                seen["sleep_log"] = True
            if not os.path.lexists(d):
                os.rename(s, d)
            elif entry.is_dir(follow_symlinks=False) and os.path.isdir(d) and not os.path.islink(d):
                sub = _fold_tree(s, d, kind, seen)
                left += sub
                if not sub:
                    shutil.rmtree(s)
            elif entry.is_file(follow_symlinks=False) and os.path.isfile(d):
                if entry.name in _DROP_NAMES[kind] or ".tmp." in entry.name:
                    os.unlink(s)
                elif entry.name.endswith(_APPEND_SUFFIXES[kind]):
                    _append_file(s, d)
                    os.unlink(s)
                else:
                    left += 1
            else:
                left += 1
        except OSError:
            left += 1
    return left


def _move_one(memory_dir: str, home: str, kind: str) -> str:
    """Move or fold one old dir into ``home``. Returns what happened, for receipts."""
    legacy = legacy_dir(memory_dir, kind)
    if not os.path.isdir(legacy) or os.path.islink(legacy):
        return ""
    new = os.path.join(home, kind)
    if kind == "telemetry" and is_stub(legacy):
        return "stub"
    if not os.path.lexists(new):
        keep_stub = kind == "telemetry" and any(
            os.path.isfile(os.path.join(legacy, n)) for n in (_SLEEP_LOG, STUB_NOTE)
        )
        try:
            os.rename(legacy, new)
        except OSError:
            if not os.path.isdir(new):
                return "blocked"
        else:
            if keep_stub:
                if os.path.isfile(os.path.join(new, STUB_NOTE)):
                    os.unlink(os.path.join(new, STUB_NOTE))
                _write_stub(legacy, new)
            return "moved"
    if not os.path.isdir(new):
        return "blocked"
    if kind == "index":
        # A cache: the live index is rebuilt from the corpus, so a second one is dropped.
        shutil.rmtree(legacy, ignore_errors=True)
        return "folded"
    seen: Dict[str, bool] = {}
    left = _fold_tree(legacy, new, kind, seen)
    if left:
        return "partial"
    if kind == "telemetry" and (seen.get("sleep_log") or os.path.isfile(os.path.join(legacy, STUB_NOTE))):
        _write_stub(legacy, new)
    else:
        shutil.rmtree(legacy, ignore_errors=True)
    return "folded"


def _try_lock(home: str):
    """A non-blocking ``flock`` on ``<home>/.migrate.lock``: the open file, or None when
    another session holds it. A platform without ``fcntl`` runs unlocked."""
    fh = open(os.path.join(home, ".migrate.lock"), "a")
    try:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        pass
    except OSError:
        fh.close()
        return None
    return fh


def migrate(memory_dir: str) -> Dict[str, str]:
    """MIG-10: move every old ``.claude/.memory-<kind>`` dir into ``<project>/.hippo/``.

    ``{kind: outcome}`` for each kind that had an old dir: ``moved`` (one rename), ``folded``
    (a dir an older hippo wrote after the move, merged in and removed), ``partial`` (merged,
    but entries the live dir also has stayed behind for doctor to name), ``stub`` (an empty
    telemetry dir kept for a sleep schedule's log), ``blocked`` (the rename failed; the old
    dir is still read in place), ``busy`` (another session is moving them). A kind whose env
    override is set is left alone. Three stats when there is nothing to move. Never raises.
    """
    out: Dict[str, str] = {}
    try:
        home = derived_home(memory_dir)
        if home is None or not os.path.isdir(memory_dir):
            return out
        todo = [
            k for k in KINDS
            if not os.environ.get(_ENV_OVERRIDES[k]) and os.path.isdir(legacy_dir(memory_dir, k))
        ]
        if not todo:
            return out
        if todo == ["telemetry"] and is_stub(legacy_dir(memory_dir, "telemetry")):
            return {"telemetry": "stub"}
        ensure_self_ignoring_dir(home)
        lock = _try_lock(home)
        if lock is None:
            return {k: "busy" for k in todo}
        try:
            for kind in todo:
                try:
                    result = _move_one(memory_dir, home, kind)
                except Exception:
                    result = "blocked"
                if result:
                    out[kind] = result
        finally:
            lock.close()
    except Exception:
        pass
    return out


def leftovers(memory_dir: str) -> Tuple[Optional[str], Dict[str, Tuple[str, int]]]:
    """For doctor: ``(home, {kind: (state, entries)})`` for each old dir still in ``.claude/``.

    ``state`` is ``unmoved`` (no live dir yet), ``stub`` (kept for a sleep schedule) or
    ``leftover`` (entries the fold would not overwrite). Read-only. Never raises.
    """
    found: Dict[str, Tuple[str, int]] = {}
    home = derived_home(memory_dir)
    if home is None:
        return None, found
    for kind in KINDS:
        legacy = legacy_dir(memory_dir, kind)
        try:
            if not os.path.isdir(legacy) or os.path.islink(legacy):
                continue
            names = [n for n in os.listdir(legacy) if n not in (".gitignore", STUB_NOTE)]
            if not os.path.isdir(os.path.join(home, kind)):
                found[kind] = ("unmoved", len(names))
            elif kind == "telemetry" and is_stub(legacy):
                found[kind] = ("stub", 0)
            else:
                found[kind] = ("leftover", len(names))
        except OSError:
            continue
    return home, found
