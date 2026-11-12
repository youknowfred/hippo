"""OWN-1: a git home for the user tier (closes #158).

The user tier (``provenance_env.user_memory_dir()``, ``~/.claude/hippo-memory`` by default) is
the one corpus that follows the owner across projects, and until now it had no history and no
way to reach a second machine. This module gives it a git home that the owner controls:

- ``hippo setup --user-tier`` previews, and with ``--yes`` makes, a LOCAL repository there:
  it writes the ``.gitignore`` below, then runs ``git init``. It makes no commit (the owner
  decides what the first commit holds, typically after marking machine-local memories) and
  records no remote. hippo never pushes, pulls or fetches: adding a remote is the owner's
  move. A tier that already is a repository, or sits inside one (a dotfiles repo), is left
  alone and the offer says so.
- **Machine-local memories** carry ``machine_local: true`` in their frontmatter (top level or
  under ``metadata:``, the usual two shapes). hippo keeps the tier's ``.gitignore`` block
  listing exactly those files, so they stay out of the repository and recall still reads
  them (they are ordinary files in the tier dir; no reader changes). The block is rewritten
  whole by ``sync_ignore_block``; a flagged file that is not in it yet (a hand edit) and a
  flagged file git already tracks (ignoring does not untrack) are both named by doctor with
  their remedy. hippo never untracks or deletes anything.
- **The block is also the record that travels.** A clone of the tier on another machine has
  the ``.gitignore`` but not the machine-local files, so a project memory's ``[[link]]`` to one
  of them is classified "machine-local to another machine" (``lint_links``) instead of
  dangling. That is why the flag-plus-block design was chosen over a gitignored subdirectory:
  the subdirectory leaves no committed trace of what it held.
- ``posture`` / ``posture_line`` give doctor its one line, from LOCAL state only (no fetch):
  not a repository / inside another repository / N uncommitted changes / clean / behind and
  ahead of the upstream as of the owner's last fetch.

Nothing here runs on a hook. Every git call is a short local subprocess; never raises.
"""

from __future__ import annotations

import os
import subprocess
from typing import Dict, Iterable, List, Optional, Tuple

MACHINE_LOCAL_KEY = "machine_local"
GITIGNORE = ".gitignore"
BLOCK_OPEN = "# >>> hippo: kept out of git (rewritten by hippo; mark a memory machine_local instead of editing)"
BLOCK_CLOSE = "# <<< hippo"
# Transient files hippo itself may leave in the tier for a moment: atomic-write tmps
# (``<name>.tmp.<random>``) and new-memory staging files (``.<name>.md.staging-<pid>``).
_HYGIENE = ("*.tmp.*", ".*.staging-*")
_MACHINE_HEADER = "# machine-local memories (frontmatter machine_local: true):"
_GIT_TIMEOUT_S = 20


# --------------------------------------------------------------------------- #
# The machine-local flag and the managed ignore block
# --------------------------------------------------------------------------- #
def is_machine_local(fm) -> bool:
    """True when a parsed frontmatter marks the memory machine-local."""
    from .fm_access import fm_get

    value = fm_get(fm, MACHINE_LOCAL_KEY)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return value is True


def flagged_machine_local(user_dir: str) -> List[str]:
    """Sorted filenames (``<stem>.md``) in the tier whose frontmatter says machine-local."""
    from .provenance import _iter_memory_files, parse_frontmatter

    out: List[str] = []
    try:
        for path in _iter_memory_files(user_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read(8192)  # frontmatter sits at the top
                if is_machine_local(parse_frontmatter(text)):
                    out.append(os.path.basename(path))
            except Exception:
                continue
    except Exception:
        return []
    return sorted(out)


def _block_entries(text: str) -> Optional[List[str]]:
    """The machine-local filenames listed in a ``.gitignore``'s hippo block; None if no block."""
    lines = text.splitlines()
    try:
        start = lines.index(BLOCK_OPEN)
    except ValueError:
        return None
    out: List[str] = []
    for line in lines[start + 1:]:
        if line == BLOCK_CLOSE:
            break
        s = line.strip()
        if s.startswith("/") and s.endswith(".md") and "/" not in s[1:]:
            out.append(s[1:])
    return out


def ignored_machine_local(user_dir: Optional[str]) -> List[str]:
    """Filenames the tier's committed ignore block lists as machine-local. One small read;
    ``[]`` when there is no tier, no ``.gitignore`` or no block. Never raises."""
    if not user_dir:
        return []
    try:
        with open(os.path.join(user_dir, GITIGNORE), "r", encoding="utf-8") as fh:
            return sorted(set(_block_entries(fh.read()) or []))
    except Exception:
        return []


def render_block(machine_local: Iterable[str]) -> str:
    """The managed block for ``machine_local`` filenames (sorted, deduplicated)."""
    names = sorted({n for n in machine_local if n})
    lines = [BLOCK_OPEN, *_HYGIENE, _MACHINE_HEADER, *(f"/{n}" for n in names), BLOCK_CLOSE]
    return "\n".join(lines) + "\n"


def with_block(text: str, machine_local: Iterable[str]) -> str:
    """``text`` (a ``.gitignore``) with its hippo block replaced, or appended when absent.
    Every line outside the block is kept as it was."""
    block = render_block(machine_local)
    lines = text.splitlines(keepends=True)
    stripped = [ln.rstrip("\r\n") for ln in lines]
    if BLOCK_OPEN in stripped:
        start = stripped.index(BLOCK_OPEN)
        end = next((i for i in range(start + 1, len(stripped)) if stripped[i] == BLOCK_CLOSE), None)
        end = len(lines) - 1 if end is None else end
        return "".join(lines[:start]) + block + "".join(lines[end + 1:])
    if text and not text.endswith("\n"):
        text += "\n"
    return text + ("\n" if text else "") + block


def sync_ignore_block(user_dir: str, also: Iterable[str] = ()) -> Dict[str, object]:
    """Rewrite the tier's ignore block to list every flagged file, plus ``also`` (a file
    about to be written). Creates ``.gitignore`` when absent. Returns
    ``{"changed", "listed", "error"}``; a failed write keeps the prior bytes and names
    itself in ``error``. Never raises."""
    result: Dict[str, object] = {"changed": False, "listed": [], "error": None}
    try:
        names = sorted(set(flagged_machine_local(user_dir)) | {n for n in also if n})
        result["listed"] = names
        path = os.path.join(user_dir, GITIGNORE)
        from .atomic import update_text_cas, write_text_cas

        if not os.path.exists(path):
            os.makedirs(user_dir, exist_ok=True)
            write_text_cas(path, render_block(names), None)
            result["changed"] = True
            return result
        with open(path, "r", encoding="utf-8") as fh:
            before = fh.read()
        after = update_text_cas(path, lambda text: with_block(text, names))
        result["changed"] = after != before
    except Exception as exc:
        result["error"] = f"could not update {os.path.join(user_dir, GITIGNORE)}: {exc}"
    return result


# --------------------------------------------------------------------------- #
# Local git state
# --------------------------------------------------------------------------- #
def _git(args: List[str], cwd: str) -> Tuple[int, str]:
    """``(returncode, stdout)`` of a local git call; ``(-1, "")`` when git cannot run."""
    try:
        out = subprocess.run(
            ["git", "-C", cwd, *args], capture_output=True, text=True, check=False,
            timeout=_GIT_TIMEOUT_S,
        )
        return out.returncode, out.stdout or ""
    except Exception:
        return -1, ""


def _count_lines(text: str) -> int:
    return sum(1 for ln in text.splitlines() if ln.strip())


def posture(user_dir: str) -> Optional[Dict[str, object]]:
    """The tier's git posture from local state, or None when the tier does not exist.

    Keys: ``dir``, ``memories`` (count), ``repo`` (``"none"`` | ``"own"`` | ``"inside"``),
    ``top`` (the repository's toplevel), ``head`` (False before the first commit),
    ``uncommitted`` (``git status --porcelain`` entries under the tier, ignored files
    excluded), ``upstream`` (name or None), ``behind``/``ahead`` (vs the upstream ref as of
    the last fetch), ``flagged`` / ``tracked_flagged`` / ``unignored_flagged`` (machine-local
    filenames: all, already tracked by git, not in the ignore block). Never raises."""
    try:
        if not user_dir or not os.path.isdir(user_dir):
            return None
        from .provenance import _iter_memory_files

        flagged = flagged_machine_local(user_dir)
        listed = set(ignored_machine_local(user_dir))
        p: Dict[str, object] = {
            "dir": user_dir,
            "memories": sum(1 for _ in _iter_memory_files(user_dir)),
            "repo": "none", "top": None, "head": False, "uncommitted": 0,
            "upstream": None, "behind": 0, "ahead": 0,
            "flagged": flagged,
            "tracked_flagged": [],
            "unignored_flagged": [n for n in flagged if n not in listed],
        }
        rc, top = _git(["rev-parse", "--show-toplevel"], user_dir)
        if rc != 0 or not top.strip():
            return p
        top = top.strip()
        own = os.path.realpath(top) == os.path.realpath(user_dir)
        p["repo"] = "own" if own else "inside"
        p["top"] = top
        p["head"] = _git(["rev-parse", "--verify", "-q", "HEAD"], user_dir)[0] == 0
        p["uncommitted"] = _count_lines(_git(["status", "--porcelain", "--", "."], user_dir)[1])
        if flagged:
            _rc, tracked = _git(["ls-files", "--", *flagged], user_dir)
            tracked_set = {os.path.basename(t) for t in tracked.splitlines() if t.strip()}
            p["tracked_flagged"] = [n for n in flagged if n in tracked_set]
        if p["head"]:
            rc, up = _git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], user_dir)
            if rc == 0 and up.strip():
                p["upstream"] = up.strip()
                rc, counts = _git(["rev-list", "--left-right", "--count", "HEAD...@{upstream}"], user_dir)
                parts = counts.split()
                if rc == 0 and len(parts) == 2:
                    p["ahead"], p["behind"] = int(parts[0]), int(parts[1])
        return p
    except Exception:
        return None


def _names(names: List[str], limit: int = 5) -> str:
    shown = ", ".join(n[:-3] if n.endswith(".md") else n for n in names[:limit])
    return shown + (f" (+{len(names) - limit} more)" if len(names) > limit else "")


def posture_line(p: Dict[str, object]) -> Tuple[str, str]:
    """``(status, message)`` for doctor. ``warn`` only when something needs the owner: a
    machine-local memory git tracks or does not ignore yet, or new commits upstream."""
    d = p["dir"]
    warn = False
    n = p["memories"]
    if p["repo"] == "none":
        msg = (
            f"user tier: {d} holds {n} memor{'y' if n == 1 else 'ies'} and is not a git "
            "repository; `hippo setup --user-tier` previews making it one (local only: "
            "hippo never pushes or pulls)"
        )
    else:
        where = "a git repository" if p["repo"] == "own" else f"inside the git repository at {p['top']}"
        bits = [f"user tier: {d} is {where}"]
        if not p["head"]:
            bits.append("no commits yet")
        u = p["uncommitted"]
        bits.append(f"{u} uncommitted change{'' if u == 1 else 's'}" if u else "clean")
        if p["upstream"]:
            behind, ahead = p["behind"], p["ahead"]
            if behind:
                warn = True
                bits.append(
                    f"behind {p['upstream']} by {behind} commit{'' if behind == 1 else 's'} as of "
                    f"your last fetch (`git -C {d} pull` brings them in)"
                )
            else:
                bits.append(f"up to date with {p['upstream']} as of your last fetch")
            if ahead:
                bits.append(f"{ahead} local commit{'' if ahead == 1 else 's'} not pushed")
        elif p["head"]:
            bits.append("no upstream configured")
        msg = "; ".join(bits)
    tracked = list(p.get("tracked_flagged") or [])
    if tracked:
        warn = True
        msg += (
            f"; {len(tracked)} machine-local memor{'y is' if len(tracked) == 1 else 'ies are'} "
            f"already committed: {_names(tracked)} (ignoring does not untrack: "
            f"`git -C {d} rm --cached <file>` stops tracking it and keeps the file)"
        )
    # Outside a repository nothing can track a file, so an unlisted flag waits for setup.
    unignored = [x for x in (p.get("unignored_flagged") or []) if x not in tracked]
    if unignored and p["repo"] != "none":
        warn = True
        fix = (
            "`hippo setup --user-tier --yes` refreshes it" if p["repo"] == "own"
            else "add it to that repository's ignore rules"
        )
        msg += (
            f"; {len(unignored)} machine-local memor{'y is' if len(unignored) == 1 else 'ies are'} "
            f"not in the ignore list yet: {_names(unignored)} ({fix})"
        )
    return ("warn" if warn else "ok"), msg + "."


# --------------------------------------------------------------------------- #
# The setup offer
# --------------------------------------------------------------------------- #
def init_repo(user_dir: str) -> Dict[str, object]:
    """Write the ignore block, then ``git init`` the tier. The ignore file goes first, so a
    machine-local memory is ignored from the repository's first moment. No commit, no
    remote. Returns ``{"ok", "error", "gitignore_changed"}``. Never raises."""
    res: Dict[str, object] = {"ok": False, "error": None, "gitignore_changed": False}
    sync = sync_ignore_block(user_dir)
    if sync["error"]:
        res["error"] = sync["error"]
        return res
    res["gitignore_changed"] = sync["changed"]
    rc, _out = _git(["init", "-q"], user_dir)
    if rc != 0:
        res["error"] = f"`git init` failed in {user_dir}"
        return res
    res["ok"] = True
    return res
