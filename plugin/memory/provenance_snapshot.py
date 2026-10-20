"""The pre-migration backup: where it goes, and when it is a copy at all (MIG-6, MIG-7).

Split out of ``provenance.py``, which sat at its size-ratchet ceiling; the façade re-imports
every public name here, so ``provenance.snapshot_corpus`` keeps resolving. Imports only
``provenance_env`` — never the façade (CONTRIBUTING.md "Code layout").

MIG-6. The copy used to land beside the corpus, in ``.claude/memory.pre-cite2-<stamp>/``.
The Claude desktop app lays down every worktree it creates with a copy of the checkout's whole
``.claude/`` directory, ignored files included, so each backup rode into every worktree made
after it. On 2026-10-19 that was 53 worktrees each carrying six spent backups (44 MB, 3,915
files apiece) on a disk that filled. The copy now goes to ``<project>/.hippo-snapshots/``:
beside ``.claude/``, not inside it, so no worktree carries it, and on the same path from every
surface (it is derived from the corpus path alone — the plugin data dir is not: the Bash tool
and a terminal never see ``CLAUDE_PLUGIN_DATA``, and Claude Code removes that dir when the
plugin is uninstalled, which would take a gitignored corpus's only undo with it).

MIG-7. A copy is only needed when git cannot undo the migration. A corpus whose memory files
are all committed and unchanged is restored by ``git checkout``, so the snapshot names that
commit instead of copying anything.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from typing import Dict, List, Optional

from .provenance_env import ensure_self_ignoring_dir

SNAPSHOT_DIRNAME = ".hippo-snapshots"
SNAPSHOT_PREFIX = "memory.pre-cite2-"

# A stamp names a directory, so it is a plain label: a `/` or `..` would put the copy
# somewhere else — back inside `.claude/`, say.
_STAMP_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def _check_stamp(stamp: str) -> None:
    if not _STAMP_RE.fullmatch(stamp or ""):
        raise ValueError(
            f"stamp {stamp!r} is not a plain label — use letters, digits, '.', '_' or '-' "
            "(e.g. '20260715-cite3')"
        )


def snapshot_root(memory_dir: str) -> str:
    """``<project>/.hippo-snapshots`` for the usual ``<project>/.claude/memory`` corpus.

    Beside ``.claude/``, never inside it. A corpus that does not sit in a ``.claude/`` dir
    (a ``HIPPO_MEMORY_DIR`` elsewhere) keeps the same-named dir beside itself.
    """
    parent = os.path.dirname(os.path.abspath(memory_dir))
    if os.path.basename(parent) == ".claude":
        parent = os.path.dirname(parent)
    return os.path.join(parent, SNAPSHOT_DIRNAME)


def _git(args: List[str], cwd: str) -> Optional[str]:
    """stdout of a git command run in ``cwd``, or None on ANY failure — never ''.

    ``provenance_env.run_git`` answers '' on failure, which a ``git status`` reader would
    take for a clean tree. Here a failure must read as "git cannot vouch", so it is None.
    ``--no-optional-locks``: a read must not take the index lock from a concurrent commit.
    """
    try:
        out = subprocess.run(
            ["git", "--no-optional-locks", "-C", cwd, *args],
            capture_output=True, text=True, check=False, timeout=20,
        )
    except Exception:
        return None
    return out.stdout if out.returncode == 0 else None


def git_restore_point(memory_dir: str) -> Optional[str]:
    """MIG-7: HEAD's sha when ``git checkout`` alone can undo a migration of this corpus.

    A migration rewrites the top-level memory files and ``.format``. Git can put them back
    when each one is tracked and ``git status`` shows nothing under the corpus (no edit,
    staged or not, and no untracked file). Anything else answers None, and the caller
    copies: no repo, no commit yet, an ignored or untracked memory, an uncommitted edit, a
    git failure. An ignored file that a migration never writes (``.DS_Store``, a
    self-ignoring ``.audit-reports/``) does not force a copy.
    """
    md = os.path.abspath(memory_dir)
    if not os.path.isdir(md):
        return None
    head = (_git(["rev-parse", "--verify", "--quiet", "HEAD"], md) or "").strip()
    if not _SHA_RE.fullmatch(head):
        return None
    status = _git(["status", "--porcelain", "-z", "--untracked-files=all", "--", "."], md)
    tracked = _git(["ls-files", "-z", "--", "."], md)  # paths relative to md
    if status is None or tracked is None or status.strip("\0"):
        return None
    try:
        written = [
            e.name for e in os.scandir(md)
            if e.is_file(follow_symlinks=False) and (e.name.endswith(".md") or e.name == ".format")
        ]
    except OSError:
        return None
    if not written or not set(written) <= set(tracked.split("\0")):
        return None
    return head


def snapshot_corpus(memory_dir: str, stamp: str) -> str:
    """Copy the corpus to ``<project>/.hippo-snapshots/memory.pre-cite2-<stamp>/``.

    The copy is the undo for a corpus git cannot restore (MIG-7 decides that — see
    ``take_snapshot``). hippo SHIPS expecting a committed corpus (``.claude/memory/`` is
    deliberately absent from init's GITIGNORE_ENTRIES, and the README says the corpus "stays
    committed in git"), so upstream a re-derivation is undoable with ``git checkout``. A
    corpus the user chose to gitignore has NO undo but this. Returns the snapshot path.
    Raises on failure: no snapshot, no migration.

    SELF-IGNORING (SEC-3), and this is load-bearing rather than tidy. The snapshot is a
    verbatim copy of the corpus, but it is NOT the corpus: a project that gitignores
    ``.claude/memory/`` does not thereby ignore a backup of it, so without a marker the
    copy lands as a fresh untracked directory holding every private memory — one
    ``git add -A`` from being committed, in a repo that may well be public. The ``*``
    marker sits in ``.hippo-snapshots/`` itself and lands BEFORE the payload, so the window
    never exists; a marker that no longer ignores everything refuses the copy. The copy
    itself carries no marker, so it is byte-identical to the corpus and copying it back is
    the whole restore. (The pre-MIG-6 marker sat inside the copy: a corpus with its own
    ``.gitignore`` overwrote it, which left the copy visible to git, and a restore would
    have dropped a ``*`` .gitignore into the corpus.)
    """
    _check_stamp(stamp)
    root = snapshot_root(memory_dir)
    dest = os.path.join(root, f"{SNAPSHOT_PREFIX}{stamp}")
    if os.path.exists(dest):
        raise FileExistsError(f"{dest} already exists — refusing to overwrite a snapshot")
    entries = os.listdir(memory_dir)  # MIG-5: read before anything exists — no debris on failure
    ensure_self_ignoring_dir(root)  # the `*` marker lands first — no unignored window
    try:
        with open(os.path.join(root, ".gitignore"), encoding="utf-8") as fh:
            ignores_all = "*" in (line.strip() for line in fh)
    except OSError:
        ignores_all = False
    if not ignores_all:
        raise PermissionError(
            f"{root}/.gitignore does not ignore everything (`*`) — refusing to copy the "
            "corpus where git could see it"
        )
    os.makedirs(dest)
    for entry in entries:
        src = os.path.join(memory_dir, entry)
        dst = os.path.join(dest, entry)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    return dest


def take_snapshot(memory_dir: str, stamp: str) -> Dict[str, str]:
    """MIG-7: the backup a migration needs — ``{"commit": sha}`` when git can undo it,
    else ``{"path": dest}`` from ``snapshot_corpus``. Raises as ``snapshot_corpus`` does;
    the stamp is checked either way, so a label that works today works tomorrow."""
    _check_stamp(stamp)
    head = git_restore_point(memory_dir)
    if head:
        return {"commit": head}
    return {"path": snapshot_corpus(memory_dir, stamp)}


def snapshot_lines(result: Dict[str, str], memory_dir: str) -> List[str]:
    """The ONE rendering of a ``take_snapshot`` result (CLI ``--snapshot``, the MCP
    ``rederive``/``tend`` action='snapshot'): where the undo is, and the command that uses it."""
    md = os.path.abspath(memory_dir)
    if result.get("commit"):
        sha = result["commit"][:12]
        return [
            f"snapshot: not copied — the corpus is committed and clean at {sha}, so git is "
            "the undo.",
            f"To undo the migration: git -C {shlex.quote(md)} checkout {sha} -- .",
            "(A copy is taken only for a gitignored or uncommitted corpus, where it is the "
            "only undo.)",
        ]
    dest = result["path"]
    return [
        f"snapshot: {dest}",
        f"A copy, because git cannot undo a migration of this corpus (gitignored, or memory "
        f"files not committed). It sits in {os.path.dirname(dest)}, outside .claude/, so new "
        "worktrees do not carry it, and that dir ignores itself (a `*` .gitignore lands "
        "before the copy), so the backup cannot publish a corpus its project keeps private.",
        f"To undo the migration: cp -Rp {shlex.quote(dest + '/.')} {shlex.quote(md + '/')}",
    ]
