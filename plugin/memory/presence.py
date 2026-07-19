"""T18 FLT-1: session presence — same-clone sessions become visible to each other.

Concurrent sessions sharing ONE working tree are the normal case on a busy machine, and
they collided in four documented events on 2026-07-16 alone: a pytest run polluted by a
sibling session's in-progress lint (the qa-sweep capstone), a branch pointer repositioned
under a live session by a concurrent release (the t16 capstone), "the third shared-clone
collision today" (PR #62), and a 14:21 checkout switch under a live T8 session (the
branch-cleanup capstone's Finding 2). Until now, sessions discovered each other by reflog
forensics. This module is the fleet keystone:

Each session writes ONE per-session doc ``<telemetry_dir>/presence/<safe(session_id)>.json``
= ``{session_id, branch, head, ts}`` at SessionStart (``write_presence``, wired in
``session_start.main``'s telemetry block, right after the session token rotates). Docs are
per-session, NOT a shared file — telemetry.py documents the single-writer assumption a
shared mutable file would violate; the shape mirrors ``jit.py``'s per-session state dir
and adds the mtime-TTL half its count-only prune lacks: ``_prune`` deletes ANY session's
expired doc (``PRESENCE_TTL_SECONDS`` — the crash-aging path) and count-caps the dir
(``MAX_PRESENCE_FILES``, oldest first). A graceful SessionEnd clears the session's OWN doc
(``clear_presence``, wired into ``capture --from-hook`` — with SubagentStop explicitly
excluded there: a subagent ending must not clear its still-live parent's doc).

The SessionStart producer (``presence_producer``, registered as ``"presence"``) is
EMPTY-NORM: with no OTHER fresh doc in the dir it emits nothing, ever; otherwise exactly
ONE bounded line naming the other sessions' branches and ages. The doc's ``branch``/
``head`` fields double as FLT-2's moved-tripwire baseline; the presence dir is FLT-3's
worktree-nudge read — both ride the PostToolUse spawn (``observe_fleet``).

ED4R-3 binds permanently: no lock, no daemon, no mutual exclusion — presence is a file
only ever READ for one line of legibility; recovery stays human. SCOPE is per-WORKING-
TREE: a worktree-opened-as-project session resolves its OWN telemetry dir and therefore
its own presence dir; spanning worktrees via the git common dir is out of scope v1 (all
four documented collisions had the main clone as project root, so per-tree suffices).

Artifact class (the ground Q1 was decided on, owner YES 2026-07-18): identical standing
to the episode buffer and the jit state — gitignored (self-ignoring dir), bounded (TTL +
count cap), self-aging, absence-emits-nothing. Kill switch: ``HIPPO_DISABLE_PRESENCE``
silences the whole lane — no write, no producer line, no clear (docs already on disk
simply age out via TTL); registered in STABILITY.md alongside ``HIPPO_DISABLE_JIT``.
Every entry point is hook-safe: never raises, fresh defaults on corruption (inv2), and
the per-prompt hot path is untouched (inv6 — SessionStart/PostToolUse/SessionEnd only).
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import List, Optional, Tuple

# --------------------------------------------------------------------------- #
# The stated bounds (acceptance criteria carry these numbers; tests import them)
# --------------------------------------------------------------------------- #
PRESENCE_TTL_SECONDS = 6 * 3600  # a doc idle this long reads as gone AND gets pruned (crash aging)
MAX_PRESENCE_FILES = 32  # count-cap, oldest-first (the jit.MAX_STATE_FILES precedent)
_PRESENCE_DIRNAME = "presence"
_MAX_LINE_CHARS = 300  # hard cap per fleet line
_MAX_FLEET_NAMES = 3  # branches named on the producer line before "(+N more)"

# The PRODUCERS loop calls every producer with the fixed (memory_dir, repo_root, ctx)
# shape, which carries no harness session id — ``write_presence`` (which DOES receive it,
# earlier in the same SessionStart process) parks it here so ``presence_producer`` can
# tell the session's own doc from the fleet's. Process-local by nature (each hook is one
# short-lived Python spawn); falls back to the shared file token when unset.
_SESSION_ID: Optional[str] = None


def presence_disabled() -> bool:
    """True when the T18 fleet lane is killed (``HIPPO_DISABLE_PRESENCE``).

    Killed means the lane contributes NOTHING — no presence write, no producer line, no
    SessionEnd clear (a doc already on disk just ages out via TTL): pre-T18 hook behavior.
    Same convention as ``jit.jit_disabled`` / ``build_index.dense_disabled``.
    """
    return os.environ.get("HIPPO_DISABLE_PRESENCE", "").strip() not in ("", "0", "false", "False")


def _presence_dir(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, _PRESENCE_DIRNAME)


def _presence_path(telemetry_dir: str, session_id: Optional[str]) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id or "anon"))[:80] or "anon"
    return os.path.join(_presence_dir(telemetry_dir), f"{safe}.json")


def _read_doc(path: str) -> dict:
    """One presence doc — fresh defaults on absence/corruption, never a raise. Optional
    fields (``nudged``, ``checked_ts``, ``moved_note``) survive verbatim when present."""
    doc: dict = {"session_id": "", "branch": "", "head": "", "ts": 0.0}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict):
            doc["session_id"] = str(raw.get("session_id") or "")
            doc["branch"] = str(raw.get("branch") or "")
            doc["head"] = str(raw.get("head") or "")
            ts = raw.get("ts")
            doc["ts"] = float(ts) if isinstance(ts, (int, float)) else 0.0
            for key in ("checked_ts", "nudged", "moved_note"):
                if key in raw:
                    doc[key] = raw[key]
    except Exception:
        pass
    return doc


def _write_doc(telemetry_dir: str, session_id: Optional[str], doc: dict) -> None:
    """Persist one presence doc (atomic, under a self-ignoring dir — SEC-3), then prune.

    Silent by design (the ``jit._write_state`` posture): a lost write costs one stale
    fleet line at worst — bookkeeping never outranks the session it describes.
    """
    try:
        pd = _presence_dir(telemetry_dir)
        from .atomic import write_json_atomic
        from .provenance import ensure_self_ignoring_dir

        ensure_self_ignoring_dir(pd)
        write_json_atomic(_presence_path(telemetry_dir, session_id), doc)
        _prune(pd)
    except Exception:
        pass


def _prune(presence_dir: str) -> None:
    """TTL sweep + count cap, in that order: delete ANY session's expired doc (mtime
    older than ``PRESENCE_TTL_SECONDS`` — crashed sessions age out here), then keep the
    newest ``MAX_PRESENCE_FILES`` of what remains, oldest deleted first. Opportunistic
    (runs after a successful write) and never raising — fleet state itself rots, so TTL
    discipline is mandatory, not optional."""
    try:
        now = time.time()
        entries = []
        with os.scandir(presence_dir) as it:
            for e in it:
                if not e.name.endswith(".json"):
                    continue
                try:
                    mtime = e.stat().st_mtime
                except OSError:
                    continue
                if now - mtime > PRESENCE_TTL_SECONDS:
                    try:
                        os.remove(e.path)
                    except OSError:
                        pass
                    continue
                entries.append((mtime, e.path))
        for _mtime, path in sorted(entries)[: max(0, len(entries) - MAX_PRESENCE_FILES)]:
            try:
                os.remove(path)
            except OSError:
                pass
    except Exception:
        pass


def _git_position(repo_root: Optional[str]) -> Tuple[str, str]:
    """Live ``(branch, head)``: branch ``""`` when detached, head ``""`` when there is no
    resolvable HEAD at all (not a git tree, or a repo with no commits — no collision
    story, so callers write no doc). Two bounded git reads via ``provenance.run_git``."""
    try:
        from .provenance import run_git

        root = repo_root or "."
        head = run_git(["rev-parse", "HEAD"], root).strip()
        if not re.fullmatch(r"[0-9a-f]{4,64}", head):
            return ("", "")
        branch = run_git(["symbolic-ref", "--short", "-q", "HEAD"], root).strip()
        return (branch, head)
    except Exception:
        return ("", "")


def _age_str(seconds: float) -> str:
    """Coarse human age for the fleet line: minutes under an hour, hours under two days,
    days beyond — presence is a liveness hint, not a clock."""
    s = max(0, int(seconds))
    if s < 3600:
        return f"{max(1, s // 60)}m"
    if s < 48 * 3600:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def _clip(line: str) -> str:
    return line if len(line) <= _MAX_LINE_CHARS else line[: _MAX_LINE_CHARS - 1].rstrip() + "…"


# --------------------------------------------------------------------------- #
# FLT-1 entry points: SessionStart write, SessionEnd clear, the producer
# --------------------------------------------------------------------------- #
def write_presence(
    memory_dir: str,
    repo_root: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    """Write/refresh THIS session's presence doc (the SessionStart moment).

    The id resolves exactly as telemetry's does (COR-6): a harness ``session_id`` keys
    the doc directly and the shared file token is never touched; without one, the file
    token (which ``mark_session`` just rotated for a genuinely-new session) is read. The
    session's own ``nudged`` dedup flag survives rewrites — a resume re-runs SessionStart
    and must not re-arm FLT-3. Never raises; a silent no-op when the lane is killed, when
    ``memory_dir`` is not a dir, or when ``repo_root`` has no resolvable HEAD.
    """
    global _SESSION_ID
    try:
        if presence_disabled() or not os.path.isdir(memory_dir):
            return
        from .telemetry import current_session_id, default_telemetry_dir

        td = default_telemetry_dir(memory_dir)
        sid = current_session_id(td, session_id=session_id)
        if not sid:
            return
        _SESSION_ID = sid
        branch, head = _git_position(repo_root)
        if not head:
            return
        doc: dict = {"session_id": sid, "branch": branch, "head": head, "ts": time.time()}
        path = _presence_path(td, sid)
        if os.path.exists(path) and _read_doc(path).get("nudged"):
            doc["nudged"] = True
        _write_doc(td, sid, doc)
    except Exception:
        pass


def clear_presence(memory_dir: Optional[str] = None, session_id: Optional[str] = None) -> None:
    """Remove THIS session's own doc (the SessionEnd moment; a crashed session ages out
    via TTL instead). Only ever the session's own file — never a sweep of the dir
    (ED4R-3: visibility, not coordination). Never raises."""
    try:
        if presence_disabled():
            return
        if memory_dir is None:
            from .provenance import resolve_dirs

            memory_dir, _ = resolve_dirs()
        if not memory_dir or not os.path.isdir(memory_dir):
            return
        from .telemetry import current_session_id, default_telemetry_dir

        td = default_telemetry_dir(memory_dir)
        sid = current_session_id(td, session_id=session_id)
        if not sid:
            return
        path = _presence_path(td, sid)
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _fresh_others(telemetry_dir: str, own_sid: Optional[str]) -> List[dict]:
    """Every OTHER session's FRESH doc, newest first: ``[{branch, age_s}]``.

    Freshness is mtime within ``PRESENCE_TTL_SECONDS`` — the same oracle ``_prune`` uses,
    so a doc is fresh iff it would survive a prune. Bounded work: one scandir plus one
    tiny JSON read per fresh doc (the dir is TTL- and count-capped)."""
    out: List[Tuple[float, dict]] = []
    try:
        own_name = os.path.basename(_presence_path(telemetry_dir, own_sid))
        now = time.time()
        with os.scandir(_presence_dir(telemetry_dir)) as it:
            for e in it:
                if not e.name.endswith(".json") or e.name == own_name:
                    continue
                try:
                    mtime = e.stat().st_mtime
                except OSError:
                    continue
                age = now - mtime
                if age > PRESENCE_TTL_SECONDS:
                    continue
                doc = _read_doc(e.path)
                out.append((mtime, {"branch": doc.get("branch") or "(detached)", "age_s": age}))
    except Exception:
        return []
    return [d for _mtime, d in sorted(out, reverse=True)]


def presence_producer(memory_dir: str, repo_root: str, ctx=None) -> Optional[str]:
    """FLT-1: the SessionStart fleet line (registered as ``"presence"``) — EMPTY-NORM.

    No other fresh session: ``None``, forever. Otherwise exactly ONE bounded line naming
    the other sessions' branches and ages (newest first, ``_MAX_FLEET_NAMES`` named, the
    rest counted). Detection and legibility only — the line prescribes nothing; FLT-3
    owns the worktree recipe at the actual mutating moment. ``ctx`` (LIF-6) unused.
    Read-only; never raises.
    """
    try:
        if presence_disabled() or not os.path.isdir(memory_dir):
            return None
        from .telemetry import current_session_id, default_telemetry_dir

        td = default_telemetry_dir(memory_dir)
        if not os.path.isdir(_presence_dir(td)):
            return None
        sid = _SESSION_ID or current_session_id(td)
        others = _fresh_others(td, sid)
        if not others:
            return None
        shown = ", ".join(
            f"{d['branch']} ({_age_str(d['age_s'])} ago)" for d in others[:_MAX_FLEET_NAMES]
        )
        more = f" (+{len(others) - _MAX_FLEET_NAMES} more)" if len(others) > _MAX_FLEET_NAMES else ""
        plural = "s" if len(others) != 1 else ""
        return _clip(
            f"🚦 fleet: {len(others)} other session{plural} active in this working tree — "
            f"{shown}{more}. Presence is per-working-tree; a session that ended without "
            "its SessionEnd hook ages out within "
            f"{PRESENCE_TTL_SECONDS // 3600}h."
        )
    except Exception:
        return None
