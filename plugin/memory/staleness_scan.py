"""HOT-15: the git half of ``staleness.find_stale`` — one history walk per scan, memoized.

Before this module the path-change scan ran ``git log --name-only -- <cited paths>`` once per
8 KB chunk of the corpus's cited-path set (SHP-6). Every chunk walks the WHOLE window of
history and matches each commit's changes against its pathspecs, so the cost grew with the
number of chunks times the length of history. On the largest field corpus (723 memories with
a baseline, 2,566 cited paths, 12 chunks, 5,690 commits in the window) that was 300 s of wall
time under load, and with a 20 s budget per chunk half the chunks were dropped: 570 memories
flagged instead of 659, with only a "may be incomplete" note to show for it.

Now every scan is ONE ``git log``, in one of two shapes:

  - **scoped** (the cited set fits in one pathspec chunk, ``max_pathspec_bytes``): SHP-6's own
    call, unchanged — ``git log --since=<window> --name-only -- :/<path>...``. A small corpus
    inside a very large repository keeps SHP-6's bound: git prints only the cited paths.
  - **unscoped** (the cited set needs more than one chunk): ``git log --since=<window>
    --name-only`` with no pathspec, filtered to the cited set in memory. On the field corpus
    that walk is about a second of git CPU and a few MB of output, and its verdicts are the
    same 659, record for record, as the old 12-chunk scan given unlimited time. Its output is
    streamed and capped at ``OUTPUT_CAP_BYTES``, so a huge repository can never make it hold
    more than that in memory.

In both shapes ``git log`` lists newest first, so every complete line read is a true
newest-change time. A walk cut short — by the ``GIT_LOG_TIMEOUT`` budget, or (unscoped) by the
output cap — keeps what it read and says why; ``staleness.find_stale`` marks the result
partial and counts the memories it could not decide (``diagnostics["unchecked"]``).

A completed walk is memoized for the process, keyed by the repo, its ``HEAD`` sha, the window,
the UTC day and the shape (the scoped shape also by the digest of the cited set, so a scoped
result is never served to another set): a second ``find_stale`` at one HEAD costs one ``git
rev-parse``. Commit times are memoized per sha (a sha's commit time cannot change); an
unresolvable sha is never memoized, so a later fetch can still resolve it. A cut-short walk is
never memoized.

Never raises. Leaf module: subprocess and ``provenance_env.run_git`` only.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional, Tuple

CHANGE_MARKER = "__C__"
GIT_LOG_TIMEOUT = 20  # seconds for the ONE history walk (the old per-chunk budget)
# The unscoped walk's memory bound. The field corpus's whole two-year window is 3.5 MB
# (5,690 commits), so 64 MB is ~18x that: room for a repository with ~100,000 commits in the
# window, while a pathological one costs at most 64 MB of buffered output, never "whatever
# git prints in 20 s". Past it the walk stops and reports partial, like a timeout.
OUTPUT_CAP_BYTES = 64 * 1024 * 1024
TIMEOUT, OUTPUT_CAP = "timeout", "output_cap"  # why a walk was cut short
_MEMO_REPOS = 4  # completed scans kept per process (one per repo HEAD in practice)
_MEMO_MAX_PATHS = 500_000  # a scan listing more distinct paths than this is not kept

_SCANS: Dict[tuple, Dict[str, int]] = {}
_SHA_TIMES: Dict[Tuple[str, str], int] = {}


def _head(repo_root: str, run_git: Callable) -> str:
    return (run_git(["rev-parse", "HEAD"], repo_root) or "").strip()


def _complete_lines(text: str) -> str:
    cut = text.rfind("\n")
    return text[: cut + 1] if cut >= 0 else ""


def _scoped_log(repo_root: str, since: str, paths: List[str]) -> Tuple[str, Optional[str]]:
    """SHP-6's one scoped call, exactly as before: ``(stdout, cut_reason)``. The ``:/`` magic
    anchors each pathspec to the repo top whatever ``-C`` dir is given (SHP-1)."""
    pathspecs = [f":/{p}" for p in paths]
    try:
        out = subprocess.run(
            ["git", "-C", repo_root, "log", f"--since={since}", "--name-only",
             f"--format={CHANGE_MARKER}%ct", "--", *pathspecs],
            capture_output=True,
            text=True,
            errors="surrogateescape",  # a non-UTF-8 path must not cost the whole scan
            check=False,
            timeout=GIT_LOG_TIMEOUT,
        )
        return out.stdout or "", None
    except subprocess.TimeoutExpired as exc:
        part = exc.stdout or ""
        if isinstance(part, bytes):
            part = part.decode("utf-8", "surrogateescape")
        return _complete_lines(part), TIMEOUT
    except Exception:
        return "", None


def _unscoped_log(repo_root: str, since: str) -> Tuple[str, Optional[str]]:
    """The window walk with no pathspec, streamed: ``(complete lines read, cut_reason)``.
    Stops at ``GIT_LOG_TIMEOUT`` (a timer kills git) or past ``OUTPUT_CAP_BYTES``."""
    try:
        proc = subprocess.Popen(
            ["git", "-C", repo_root, "log", f"--since={since}", "--name-only",
             f"--format={CHANGE_MARKER}%ct"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return "", None
    fired = threading.Event()

    def _expire():
        fired.set()
        try:
            proc.kill()
        except Exception:
            pass

    timer = threading.Timer(GIT_LOG_TIMEOUT, _expire)
    timer.daemon = True
    timer.start()
    parts: List[bytes] = []
    size = 0
    reason: Optional[str] = None
    try:
        for line in proc.stdout:
            size += len(line)
            if size > OUTPUT_CAP_BYTES:
                reason = OUTPUT_CAP
                break
            parts.append(line)
    except Exception:
        pass
    finally:
        timer.cancel()
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            proc.stdout.close()
        except Exception:
            pass
    if reason is None and fired.is_set():
        reason = TIMEOUT
    text = b"".join(parts).decode("utf-8", "surrogateescape")
    return (_complete_lines(text) if reason else text), reason


def _parse(log: str) -> Dict[str, int]:
    """path -> newest commit time. ``git log`` lists newest first, so a path's first
    appearance is its newest change in the window."""
    out: Dict[str, int] = {}
    cur: Optional[int] = None
    for line in log.split("\n"):
        if line.startswith(CHANGE_MARKER):
            try:
                cur = int(line[len(CHANGE_MARKER):] or 0)
            except ValueError:
                cur = None
        elif line.strip() and cur is not None and line not in out:
            out[line] = cur
    return out


def fits_one_chunk(paths: List[str], max_pathspec_bytes: int) -> bool:
    """True when ``staleness._chunk_paths`` would make ONE chunk of ``paths``."""
    return len(paths) <= 1 or sum(len(p.encode("utf-8")) + 1 for p in paths) <= max_pathspec_bytes


def scan(
    repo_root: str, since: str, paths: Iterable[str], run_git: Callable, max_pathspec_bytes: int
) -> Tuple[Dict[str, int], Optional[str]]:
    """``({path: newest change time} for the cited ``paths`` changed in the window,
    cut_reason)``; ``cut_reason`` is None for a complete walk, else ``TIMEOUT`` or
    ``OUTPUT_CAP`` and the map holds what was read before it.

    Paths are toplevel-relative on both sides (``--name-only`` prints them so, whatever
    ``-C`` dir is given; ``cited_paths`` are stored so, SHP-1)."""
    want = list(paths)
    if not want:
        return {}, None
    scoped = fits_one_chunk(want, max_pathspec_bytes)
    shape = ("scoped", hashlib.sha256("\n".join(sorted(want)).encode("utf-8", "surrogatepass")).hexdigest()) \
        if scoped else ("all",)
    head = _head(repo_root, run_git)
    key = (os.path.realpath(repo_root), head, since, datetime.now(timezone.utc).date().isoformat(), shape)
    full = _SCANS.get(key) if head else None
    reason: Optional[str] = None
    if full is None:
        log, reason = _scoped_log(repo_root, since, want) if scoped else _unscoped_log(repo_root, since)
        full = _parse(log)
        if head and log and reason is None and len(full) <= _MEMO_MAX_PATHS:
            while len(_SCANS) >= _MEMO_REPOS:
                _SCANS.pop(next(iter(_SCANS)))
            _SCANS[key] = full
    return {p: full[p] for p in want if p in full}, reason


def commit_times(shas: List[str], repo_root: str, run_git: Callable) -> Dict[str, int]:
    """sha -> commit time, one ``git show`` for the shas not already known. ``--ignore-missing``
    (SHP-3): one unresolvable sha must not blank the lookup for the rest of the batch."""
    shas = [s for s in dict.fromkeys(shas) if s]
    if not shas:
        return {}
    root = os.path.realpath(repo_root)
    out: Dict[str, int] = {s: _SHA_TIMES[(root, s)] for s in shas if (root, s) in _SHA_TIMES}
    todo = [s for s in shas if s not in out]
    if todo:
        res = run_git(["show", "-s", "--format=%H %ct", "--ignore-missing", *todo], repo_root)
        asked = set(todo)
        for line in res.split("\n"):
            parts = line.split()
            if len(parts) == 2:
                try:
                    t = int(parts[1])
                except ValueError:
                    continue
                out[parts[0]] = t
                if parts[0] in asked:  # a full sha asked by its full name: immutable
                    _SHA_TIMES[(root, parts[0])] = t
    return out
