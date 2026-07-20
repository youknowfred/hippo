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
``session_start.main``'s telemetry block, right after the session token rotates), plus an
OPTIONAL additive ``plugin_version`` (OPS-1): the RUNNING plugin's manifest version via
``telemetry._producer_version`` — the launch-pinned hook version, stamped only when the
manifest is readable (ED-4: absent when not, and old docs simply lack it). The producer
renders versions ONLY when fresh docs actually differ, so launch-pin skew across sessions
becomes visible fleet-wide while single-version fleets render byte-identically to before. Docs are
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

FLT-2, the moved-under-me tripwire (``observe_fleet``, riding the existing PostToolUse
spawn via ``outcome.record_from_payload``): compare live ``git symbolic-ref --short
HEAD`` + ``git rev-parse HEAD`` against the session's OWN doc, debounced to at most one
git read pair per ``_RECHECK_SECONDS`` (each PostToolUse is a fresh short-lived process,
so the debounce state is the doc's ``checked_ts`` — the common path is one tiny JSON
read, ZERO subprocesses: the tests/test_scale.py budget). On a mismatch it separates the
boring shape from the reportable one: a fast-forward advance (the old head is an
ancestor of the new — this session's own commits landing) refreshes the doc SILENTLY;
a branch switch or a non-fast-forward reposition — exactly the two documented collision
signatures (the t16 branch reposition, the t8 14:21 checkout switch) — emits ONE neutral
line, quoting the reflog's checkout entry when it matches, then updates the doc so the
wire fires ONCE per move. Detection, not accusation: hooks cannot see Bash-mediated git,
so this session's own checkout looks identical to a concurrent session's — the line
states facts and prescribes nothing; recovery stays human. Touchless sessions get the
same compare at their NEXT SessionStart: ``write_presence`` stashes the line as the
doc's ``moved_note``; the producer emits it exactly once and clears it.

FLT-3, the worktree-first nudge (same spawn, same call): the FIRST time this session
MUTATES the shared tree — the caller passes ``mutating`` (tool in
``outcome.MUTATING_FILE_TOOLS``, the one canonical subset; Read excluded) and
``shared_tree`` (the touched rel path is NOT under ``outcome._WORKTREE_PREFIX`` — a
worktree-prefixed mutation is already isolated and self-exempts) — while >=1 OTHER
fresh presence doc exists, ONE nudge names the proven recipe verbatim:
``git worktree add .claude/worktrees/<branch>`` (every tier session since T8 used it;
the capstones call it the law). Once per session, deduped via the doc's ``nudged`` flag
(surviving SessionStart rewrites on resume). HONEST COVERAGE BOUNDARY: PostToolUse sees
FILE-TOOL acts only — Bash-mediated mutations (git commit/checkout, pytest, scripts)
never reach this hook, so the 5-session cwd-trap class is only PARTIALLY covered: a
file-tool edit landing on the shared tree (T10's swept memory-file mods) is the covered
shape; a git-add misfire from a wrong cwd is not. Also stated in STABILITY.md's
``HIPPO_DISABLE_PRESENCE`` entry and memory_post_tool.sh.

LINE BUDGET, stated (the QUA-2 / JIT-1 accounting): the PostToolUse surface's joint
budget is EXPLICITLY RE-BOUNDED, not shared — JIT-1 keeps its 3 reminder lines per
session (``jit.MAX_LINES_PER_SESSION``); this lane adds at most 2 more per spawn (one
tripwire line per detected move + one worktree nudge per session), each ``_clip``-capped.
Every line rides the same ``context_out``; the hook still prints exactly ONE
hookSpecificOutput.

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
import subprocess
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
_RECHECK_SECONDS = 60.0  # FLT-2 debounce: at most one live-git compare per this window
# FLT-3: the recipe every tier session since T8 proved out — named VERBATIM in the nudge.
WORKTREE_RECIPE = "git worktree add .claude/worktrees/<branch>"

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
    fields (``nudged``, ``checked_ts``, ``moved_note``, ``plugin_version``) survive
    verbatim when present — the passthrough is what keeps the OPS-1 stamp alive across
    the producer's note-clearing rewrite and ``observe_fleet``'s doc refreshes."""
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
            for key in ("checked_ts", "nudged", "moved_note", "plugin_version"):
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


def _is_ancestor(repo_root: Optional[str], old: str, new: str) -> bool:
    """True iff ``old`` is an ancestor of ``new`` — a linear fast-forward advance, the
    shape of this session's own commits landing. ``False`` on ANY failure, including a
    vanished old sha: an unresolvable baseline is exactly a move worth naming. One
    bounded rc-only git call, paid ONLY when the head actually changed (rare)."""
    try:
        proc = subprocess.run(
            ["git", "-C", repo_root or ".", "merge-base", "--is-ancestor", old, new],
            capture_output=True,
            timeout=20,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _reflog_checkout_quote(repo_root: Optional[str]) -> str:
    """The most recent reflog checkout subject ("checkout: moving from A to B"), or
    ``""`` — the ``session_start._recent_merge_signals`` probe family, paid only on the
    fire path."""
    try:
        from .provenance import run_git

        for ln in run_git(["reflog", "-n", "8", "--format=%gs"], repo_root or ".").splitlines():
            if ln.strip().startswith("checkout: moving from"):
                return ln.strip()
    except Exception:
        pass
    return ""


def _sha7(sha: str) -> str:
    return (sha or "")[:7]


def _moved_line(
    old_branch: str, old_head: str, branch: str, head: str, repo_root: Optional[str]
) -> str:
    """FLT-2's ONE neutral line — detection, not accusation: this session's own
    Bash-mediated git and a concurrent session's are indistinguishable to hooks, so the
    line states the move and prescribes nothing. The reflog checkout entry is quoted
    only when its destination matches where the tree actually is now."""
    quote = _reflog_checkout_quote(repo_root)
    if quote and branch and not quote.endswith(f" to {branch}"):
        quote = ""
    src = f"{old_branch or '(detached)'}@{_sha7(old_head)}"
    dst = f"{branch or '(detached)'}@{_sha7(head)}"
    tail = f' (reflog: "{quote}")' if quote else ""
    return _clip(
        f"🚦 fleet: this working tree moved — was {src}, now {dst}{tail}. A concurrent "
        "session's git and this session's own look identical to hooks; noted once per move."
    )


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
    and must not re-arm FLT-3. FLT-2's fallback for touchless sessions lives here too:
    a reportable move since the doc was last written (branch switch or non-fast-forward
    reposition; a linear advance is this session's own shape and stays silent) is
    stashed as ``moved_note`` for the producer to emit ONCE and clear — an unemitted
    note is simply superseded by the next fresh compare. Never raises; a silent no-op
    when the lane is killed, when ``memory_dir`` is not a dir, or when ``repo_root``
    has no resolvable HEAD.
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
        # OPS-1: stamp which plugin version this session's hooks ACTUALLY run — the ONE
        # canonical resolver (MEA-4's, CLAUDE_PLUGIN_ROOT first, module root as the dev
        # fallback, NEVER the operated-on tree). Additive and absent when unreadable
        # (ED-4); a SessionStart-only read, not the inv6 per-prompt path.
        from .telemetry import _producer_version

        pv = _producer_version()
        if pv:
            doc["plugin_version"] = pv
        path = _presence_path(td, sid)
        if os.path.exists(path):
            old = _read_doc(path)
            if old.get("nudged"):
                doc["nudged"] = True
            o_branch, o_head = old.get("branch") or "", old.get("head") or ""
            if o_head and (o_branch != branch or o_head != head):
                if o_branch != branch or not _is_ancestor(repo_root, o_head, head):
                    doc["moved_note"] = _moved_line(o_branch, o_head, branch, head, repo_root)
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
    """Every OTHER session's FRESH doc, newest first: ``[{branch, age_s, plugin_version}]``.

    Freshness is mtime within ``PRESENCE_TTL_SECONDS`` — the same oracle ``_prune`` uses,
    so a doc is fresh iff it would survive a prune. ``plugin_version`` is the doc's OPS-1
    stamp when present and a str (None otherwise — old docs lack it and render exactly as
    before). Bounded work: one scandir plus one tiny JSON read per fresh doc (the dir is
    TTL- and count-capped)."""
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
                pv = doc.get("plugin_version")
                out.append(
                    (
                        mtime,
                        {
                            "branch": doc.get("branch") or "(detached)",
                            "age_s": age,
                            "plugin_version": pv if isinstance(pv, str) and pv else None,
                        },
                    )
                )
    except Exception:
        return []
    return [d for _mtime, d in sorted(out, reverse=True)]


def presence_producer(memory_dir: str, repo_root: str, ctx=None) -> Optional[str]:
    """FLT-1/FLT-2: the SessionStart fleet block (registered as ``"presence"``) — EMPTY-NORM.

    At most two bounded lines, each self-silencing: the session's own stashed
    ``moved_note`` (FLT-2's fallback for touchless sessions — emitted exactly once, then
    cleared from the doc) and ONE line naming the other fresh sessions' branches and ages
    (newest first, ``_MAX_FLEET_NAMES`` named, the rest counted). No other session and no
    note: ``None``, forever. Detection and legibility only — the fleet line prescribes
    nothing; FLT-3 owns the worktree recipe at the actual mutating moment. ``ctx``
    (LIF-6) unused. Read-mostly (the one write is clearing an emitted note); never raises.
    """
    try:
        if presence_disabled() or not os.path.isdir(memory_dir):
            return None
        from .telemetry import current_session_id, default_telemetry_dir

        td = default_telemetry_dir(memory_dir)
        if not os.path.isdir(_presence_dir(td)):
            return None
        sid = _SESSION_ID or current_session_id(td)
        lines: List[str] = []
        own_version: Optional[str] = None
        own_path = _presence_path(td, sid)
        if os.path.exists(own_path):
            own = _read_doc(own_path)
            pv = own.get("plugin_version")
            own_version = pv if isinstance(pv, str) and pv else None
            note = own.pop("moved_note", None)
            if isinstance(note, str) and note.strip():
                lines.append(_clip(note))
                _write_doc(td, sid, own)  # emitted once — the note never renders twice
        others = _fresh_others(td, sid)
        if others:
            # OPS-1: launch-pin skew — append per-session hook versions ONLY when the
            # fresh docs (own included) actually carry more than one version. A uniform
            # fleet and any doc lacking the stamp render byte-identically to pre-OPS-1
            # (the acceptance byte-identity pin); versions are facts, never advice.
            versions = {d["plugin_version"] for d in others} | {own_version}
            versions.discard(None)
            skew = len(versions) > 1
            shown = ", ".join(
                f"{d['branch']} ({_age_str(d['age_s'])} ago, v{d['plugin_version']} hooks)"
                if skew and d["plugin_version"]
                else f"{d['branch']} ({_age_str(d['age_s'])} ago)"
                for d in others[:_MAX_FLEET_NAMES]
            )
            more = (
                f" (+{len(others) - _MAX_FLEET_NAMES} more)"
                if len(others) > _MAX_FLEET_NAMES
                else ""
            )
            plural = "s" if len(others) != 1 else ""
            lines.append(
                _clip(
                    f"🚦 fleet: {len(others)} other session{plural} active in this working "
                    f"tree — {shown}{more}. Presence is per-working-tree; a session that "
                    "ended without its SessionEnd hook ages out within "
                    f"{PRESENCE_TTL_SECONDS // 3600}h."
                )
            )
        return "\n".join(lines) if lines else None
    except Exception:
        return None


def observe_fleet(
    rel_path: str,
    *,
    memory_dir: str,
    repo_root: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    session_id: Optional[str] = None,
    mutating: bool = False,
    shared_tree: bool = False,
) -> Optional[List[str]]:
    """FLT-2 + FLT-3: the whole fleet decision for ONE PostToolUse file touch.

    Returns bounded line(s) for the caller's ``context_out`` (QUA-2: the hook still
    prints exactly ONE hookSpecificOutput), or ``None`` — the overwhelming norm. The
    live-git compare is debounced via the doc's ``checked_ts`` (each PostToolUse is a
    fresh process, so the doc IS the cross-spawn state): the common path is one tiny
    JSON read and ZERO subprocesses (the tests/test_scale.py budget); at most once per
    ``_RECHECK_SECONDS`` it pays two bounded git reads, and only on an actual head
    change the rc-only ancestor probe. A reportable move (branch switch / non-fast-
    forward reposition) emits ONE neutral line and updates the doc — the wire fires
    ONCE per move; a linear advance refreshes silently. A missing doc self-heals
    silently (a session predating the lane gets its baseline on first touch).

    The FLT-3 nudge fires at most once per SESSION (the ``nudged`` flag), only when
    ``mutating`` (the caller checked ``outcome.MUTATING_FILE_TOOLS``) AND ``shared_tree``
    (not worktree-prefixed — the caller checked ``outcome._WORKTREE_PREFIX``) AND >=1
    other fresh presence doc exists; it names ``WORKTREE_RECIPE`` verbatim. Guidance
    only — no lock, no block (ED4R-3). Doc rewrites double as the session's liveness
    beacon (mtime refresh). Never raises.
    """
    try:
        if presence_disabled() or not os.path.isdir(memory_dir):
            return None
        from .telemetry import current_session_id, default_telemetry_dir

        td = telemetry_dir or default_telemetry_dir(memory_dir)
        sid = current_session_id(td, session_id=session_id)
        if not sid:
            return None
        now = time.time()
        path = _presence_path(td, sid)
        lines: List[str] = []
        dirty = False
        if not os.path.exists(path):
            branch, head = _git_position(repo_root)
            if not head:
                return None
            doc: dict = {"session_id": sid, "branch": branch, "head": head, "ts": now}
            dirty = True
        else:
            doc = _read_doc(path)
            doc["session_id"] = sid
            checked = doc.get("checked_ts")
            base = checked if isinstance(checked, (int, float)) else doc.get("ts") or 0.0
            if now - float(base) >= _RECHECK_SECONDS:
                branch, head = _git_position(repo_root)
                if head:
                    o_branch, o_head = doc.get("branch") or "", doc.get("head") or ""
                    if o_head and (o_branch != branch or o_head != head):
                        if o_branch != branch or not _is_ancestor(repo_root, o_head, head):
                            lines.append(_moved_line(o_branch, o_head, branch, head, repo_root))
                        doc["branch"], doc["head"] = branch, head
                    elif not o_head:
                        doc["branch"], doc["head"] = branch, head
                    doc["ts"] = now
                doc["checked_ts"] = now
                dirty = True
        if mutating and shared_tree and not doc.get("nudged") and _fresh_others(td, sid):
            lines.append(
                _clip(
                    "🚦 fleet: another session is active in this working tree and this "
                    f"one just modified it ({rel_path}). Worktrees isolate concurrent "
                    f"sessions: {WORKTREE_RECIPE}"
                )
            )
            doc["nudged"] = True
            dirty = True
        if dirty:
            _write_doc(td, sid, doc)
        return lines or None
    except Exception:
        return None
