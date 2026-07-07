"""Recall-event telemetry for the agent-memory hook (instrumentation tier).

An append-only, gitignored, LOCAL ledger of what the recall hook surfaced in the wild —
one JSON line per hook recall: timestamp, session id, the surfaced memory NAMES, the
backend that served them (``dense+bm25`` / ``dense`` / ``bm25`` / ``none``), latency, ``k``,
and a TRUNCATED query preview (privacy-conscious — never the full prompt).

Robustness contract (the UserPromptSubmit hook depends on it):
  - NEVER raises — every write is wrapped; a failure (unwritable dir, a race) degrades to a
    silent no-op, and the recall still returns its results.
  - It runs AFTER recall results are computed, so it can never delay or change a recall.
  - SIZE-BOUNDED — the ledger caps at a byte ceiling and rotates (keeps the recent tail), so
    it can never grow without bound.
  - No sensitive content — only memory names + backend + latency + a truncated query.

Markdown-in-git stays the single source of authority; this ledger is DERIVED, local,
gitignored, and append-only HISTORY (NOT rebuildable like the index — deleting it loses only
the history, nothing the corpus needs). It lives in its OWN sibling of the index
(``.claude/.memory-telemetry/``) precisely BECAUSE it is history, not a rebuildable cache.

``read_events`` is the read surface the Tier-2 soak/curation analyzer consumes.

LIF-4: beside the rotating ledgers sits ``usage_aggregates.json`` — a tiny per-memory
aggregate (first/last recalled ts, distinct-session count) updated on every
``log_recall_event`` append and NEVER rotated, so long-lived corpora keep their oldest
usage evidence after the ledger's byte-capped tail drops it. ``read_usage_aggregates``
is its read surface (soak/curation union it in; v0.5.0's RET-5 consumes it as a
ranking prior).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Iterator, List, Optional

from .provenance import ensure_self_ignoring_dir

_TELEMETRY_DIRNAME = ".memory-telemetry"
_LEDGER_NAME = "recall_events.jsonl"
_EPISODE_LEDGER_NAME = "episode_buffer.jsonl"
_RECONSOLIDATION_LEDGER_NAME = "reconsolidation_events.jsonl"
_USAGE_AGGREGATES_NAME = "usage_aggregates.json"
_SESSION_NAME = "session"

# Tier 2: the only valid reconsolidation verdicts -- "fix" is a distinct outcome (content was
# wrong, then corrected) from "graduate"/"demote" (a verdict on the ORIGINALLY flagged
# content), see eval_recall.graduation_rate()'s docstring for why it's excluded from that ratio.
_RECONSOLIDATION_OUTCOMES = frozenset({"graduate", "fix", "demote"})

# Privacy: store only a short prefix of the query, never the full prompt.
_QUERY_PREVIEW_CHARS = 80

_DEFAULT_MAX_BYTES = 2_000_000


def _max_bytes() -> int:
    """Byte ceiling before the ledger rotates. Env-overridable (tests use a tiny cap)."""
    try:
        return max(256, int(os.environ.get("HIPPO_TELEMETRY_MAX_BYTES") or _DEFAULT_MAX_BYTES))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_BYTES


# --------------------------------------------------------------------------- #
# Dir resolution (mirrors build_index.default_index_dir)
# --------------------------------------------------------------------------- #
def default_telemetry_dir(memory_dir: str) -> str:
    """``.claude/.memory-telemetry`` — a sibling of ``.claude/memory`` (its own gitignored dir).

    Mirrors ``build_index.default_index_dir`` so the ledger lands beside the index. It is a
    SEPARATE dir from the index because it is append-only history, not a rebuildable cache.
    ``HIPPO_TELEMETRY_DIR`` overrides (hermetic tests use this).
    """
    override = os.environ.get("HIPPO_TELEMETRY_DIR")
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(memory_dir)), _TELEMETRY_DIRNAME)


def _resolve_dir(telemetry_dir: Optional[str]) -> str:
    if telemetry_dir:
        return telemetry_dir
    # Lazy import: provenance is the package's dir oracle and never imports telemetry.
    from .provenance import resolve_dirs

    memory_dir, _ = resolve_dirs()
    return default_telemetry_dir(memory_dir)


def _ledger_path(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, _LEDGER_NAME)


def _episode_ledger_path(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, _EPISODE_LEDGER_NAME)


def _reconsolidation_ledger_path(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, _RECONSOLIDATION_LEDGER_NAME)


def _usage_aggregates_path(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, _USAGE_AGGREGATES_NAME)


def _session_path(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, _SESSION_NAME)


# --------------------------------------------------------------------------- #
# Session token (persisted: SessionStart and UserPromptSubmit are separate processes)
#
# COR-6: when the harness hands us a concrete session_id (from the SessionStart /
# UserPromptSubmit hook payload), that id is used DIRECTLY as the telemetry key instead of
# the file-based uuid token below. The file (``<telemetry_dir>/session``) is a SHARED,
# mutable fallback — fine for a single interactive session with no harness id (tests, bare
# CLI invocations), but two concurrent harness sessions on the same project both writing to
# it would clobber each other's id. Passing an explicit ``session_id`` bypasses the file
# entirely: nothing is read or written to it, so concurrent sessions never collide.
# --------------------------------------------------------------------------- #
def mark_session(telemetry_dir: Optional[str] = None) -> Optional[str]:
    """Stamp a FRESH session id (rotates the token). Called once per SessionStart.

    Returns the new id, or None on failure. Never raises.
    """
    try:
        td = _resolve_dir(telemetry_dir)
        ensure_self_ignoring_dir(td)  # derived dir: mkdir + self-ignoring .gitignore (SEC-3)
        sid = uuid.uuid4().hex
        with open(_session_path(td), "w", encoding="utf-8") as fh:
            fh.write(sid)
        return sid
    except Exception:
        return None


def current_session_id(
    telemetry_dir: Optional[str] = None, *, session_id: Optional[str] = None
) -> Optional[str]:
    """Read the current session id, minting + persisting one if none exists.

    So recall events are grouped per Claude-Code session even if a recall fires before the
    SessionStart mark (the first read establishes the id; the next SessionStart rotates it).
    When ``session_id`` is given (a harness-provided id), it is returned DIRECTLY — the
    file-based token is neither read nor written, so concurrent sessions never share it.
    Never raises.
    """
    if session_id:
        return session_id
    try:
        td = _resolve_dir(telemetry_dir)
        sp = _session_path(td)
        if os.path.exists(sp):
            with open(sp, "r", encoding="utf-8") as fh:
                sid = fh.read().strip()
            if sid:
                return sid
        return mark_session(td)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Append (never raises, bounded, rotates)
# --------------------------------------------------------------------------- #
def _rotate_if_needed(path: str) -> None:
    """Keep the ledger under the byte ceiling by retaining only the most-recent tail.

    Keeps the last ``<= max_bytes // 2`` bytes, aligned to a line boundary (the partial
    leading line is dropped). Called AFTER the new line is appended, so the newest event is
    always retained. A failed rotation leaves the file as-is — it never breaks logging.

    Single-writer assumption: interactive SessionStart/UserPromptSubmit hooks are effectively
    serialized per session, so this read-modify-write is not cross-process locked. The
    ``os.replace`` swap keeps each rotation atomic (no structurally-corrupt file); a rare
    concurrent-writer race costs at most a dropped telemetry line, never a crash.
    """
    try:
        if os.path.getsize(path) <= _max_bytes():
            return
    except OSError:
        return
    try:
        target = max(256, _max_bytes() // 2)
        with open(path, "rb") as fh:
            data = fh.read()
        tail = data[-target:]
        nl = tail.find(b"\n")
        if nl != -1:
            tail = tail[nl + 1:]  # drop the partial leading line
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(tail)
        os.replace(tmp, path)
    except Exception:
        pass


def log_recall_event(
    results: List[dict],
    *,
    query: str,
    k: int,
    latency_ms: float,
    telemetry_dir: Optional[str] = None,
    session_id: Optional[str] = None,
) -> bool:
    """Append ONE recall event to the ledger. Fire-and-forget: NEVER raises.

    Records the surfaced memory names, the serving backend, latency, ``k``, a TRUNCATED
    query preview (never the full prompt), and — COR-8 — each result's TRUE penalized fused
    score plus its 1-based emission rank (``recall()`` now emits the real ranking signal
    instead of fabricated 1/rank noise; this ledger just persists it verbatim so threshold
    calibration and any future feedback loop, e.g. v0.5.0's RET-5 salience fusion, inherit the
    real number). ``scores``/``ranks`` are parallel arrays aligned to ``names`` — kept
    separate rather than nesting `{name, score, rank}` objects so the existing `names`-shaped
    consumers (the soak/curation analyzer) are untouched; a `results` entry missing a `score`
    (a caller-constructed dict predating this field) contributes ``None`` at that position
    rather than dropping the row, so the arrays never lose alignment with `names`.
    ``session_id``, when given (the harness-provided id), keys the event directly instead of
    the file-based token — see ``current_session_id``. Returns True on a successful append,
    else False (a write failure degrades silently — the caller's recall is unaffected).
    LIF-4: a successful append also folds the event into ``usage_aggregates.json`` (the
    rotation-surviving per-memory summary — best-effort, never affects the return value).
    """
    try:
        td = _resolve_dir(telemetry_dir)
        ensure_self_ignoring_dir(td)  # derived dir: mkdir + self-ignoring .gitignore (SEC-3)
        backend = (results[0].get("backend") if results else None) or "none"
        named = [r for r in results if r.get("name")]
        event = {
            "ts": round(time.time(), 3),
            "session_id": current_session_id(td, session_id=session_id),
            "names": [r.get("name") for r in named],
            "scores": [r.get("score") for r in named],
            "ranks": [r.get("rank") for r in named],
            "backend": backend,
            "latency_ms": round(float(latency_ms), 2),
            "k": int(k),
            "query_preview": (query or "")[:_QUERY_PREVIEW_CHARS],
        }
        path = _ledger_path(td)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        _rotate_if_needed(path)
        # LIF-4: fold this event into the rotation-surviving usage aggregates. Runs AFTER
        # the append and is itself never-raising, so an aggregate failure can neither lose
        # the ledger line nor flip this function's return value.
        _update_usage_aggregates(
            td, names=event["names"], session_id=event["session_id"], ts=event["ts"]
        )
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Read (for the Tier-2 soak / curation analyzer)
# --------------------------------------------------------------------------- #
def read_events(telemetry_dir: Optional[str] = None) -> Iterator[dict]:
    """Yield parsed recall events, skipping corrupt/partial lines. Read-only; never raises."""
    try:
        td = _resolve_dir(telemetry_dir)
        path = _ledger_path(td)
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except Exception:
        return


# --------------------------------------------------------------------------- #
# LIF-4: rotation-surviving usage aggregates — a tiny JSON sidecar of the recall ledger.
#
# The ledger is a byte-capped rotating buffer, so a long-lived corpus loses its OLDEST
# usage evidence first and genuinely-used memories drift toward "never recalled" in the
# soak/curation/archive analyzers. This file keeps the per-memory summary those analyzers
# actually need — first-recalled ts, last-recalled ts, distinct-session count — updated on
# every ``log_recall_event`` append and NEVER rotated (its size is bounded by corpus size,
# ~100 bytes per ever-recalled memory, not by history length).
#
# Distinct-session counting is APPROXIMATE by design: each record keeps only the LAST
# session id it counted, and increments when a different id shows up. Consecutive events
# from one session count once; two sessions interleaving their prompts on the same project
# can each be counted more than once. The full-precision alternative (a session-id set per
# name) would grow without bound — the opposite of this file's contract.
#
# Same robustness contract as the ledgers: NEVER raises (corrupt/missing file -> start
# fresh), fire-and-forget, no sensitive content (memory names + timestamps + one session
# id per record). Writes are atomic (tmp + os.replace, pid-suffixed so concurrent writers
# never share a tmp path) under the same single-writer assumption as ``_rotate_if_needed``
# — a rare concurrent-writer race loses at most one increment, never the file.
# --------------------------------------------------------------------------- #
_AGGREGATES_VERSION = 1


def _empty_aggregates() -> dict:
    return {
        "version": _AGGREGATES_VERSION,
        "sessions": {"count": 0, "first_ts": None, "last_session_id": None},
        "memories": {},
    }


def _num(v) -> Optional[float]:
    """``v`` as a float when it is a real number (bool excluded), else None."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _load_usage_aggregates(telemetry_dir: str) -> dict:
    """Parse ``usage_aggregates.json`` into the canonical shape. Corrupt/missing -> fresh.

    Field-level tolerant: a wrong-typed ``sessions`` block or non-dict memory record is
    replaced with a fresh value rather than raising (the never-raise discipline) or
    poisoning the rest of the file.
    """
    fresh = _empty_aggregates()
    try:
        with open(_usage_aggregates_path(telemetry_dir), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return fresh
    if not isinstance(data, dict):
        return fresh
    sess = data.get("sessions")
    if isinstance(sess, dict):
        count = sess.get("count")
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            fresh["sessions"]["count"] = count
        fresh["sessions"]["first_ts"] = _num(sess.get("first_ts"))
        lsid = sess.get("last_session_id")
        if isinstance(lsid, str) and lsid:
            fresh["sessions"]["last_session_id"] = lsid
    mems = data.get("memories")
    if isinstance(mems, dict):
        fresh["memories"] = {
            name: rec for name, rec in mems.items() if isinstance(name, str) and isinstance(rec, dict)
        }
    return fresh


def _update_usage_aggregates(
    telemetry_dir: str, *, names: List[str], session_id: Optional[str], ts: float
) -> None:
    """Fold ONE recall event into the aggregates. Fire-and-forget: NEVER raises.

    The GLOBAL ``sessions`` record advances on every event (an empty recall still counts
    as a session for the soak gate, mirroring ``soak.soak_status``); per-memory records
    advance only for the recalled ``names``. Events without a session id still stamp
    first/last timestamps but cannot advance a distinct-session count. A malformed
    per-name record self-heals to a fresh one. The write is atomic (tmp + os.replace);
    on any failure the previous file is left intact and the caller is unaffected —
    ``_rotate_if_needed`` never sees, and can never truncate, this file.
    """
    try:
        agg = _load_usage_aggregates(telemetry_dir)
        sess = agg["sessions"]
        if sess["first_ts"] is None:
            sess["first_ts"] = ts
        if session_id and session_id != sess["last_session_id"]:
            sess["count"] += 1
            sess["last_session_id"] = session_id
        for name in names or []:
            if not name or not isinstance(name, str):
                continue
            rec = agg["memories"].get(name)
            if not isinstance(rec, dict):
                rec = {}
            first_ts = _num(rec.get("first_ts"))
            count = rec.get("sessions")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                count = 0
            last_sid = rec.get("last_session_id")
            if not isinstance(last_sid, str) or not last_sid:
                last_sid = None
            if session_id and session_id != last_sid:
                count += 1
                last_sid = session_id
            agg["memories"][name] = {
                "first_ts": first_ts if first_ts is not None else ts,
                "last_ts": ts,
                "sessions": count,
                "last_session_id": last_sid,
            }
        path = _usage_aggregates_path(telemetry_dir)
        tmp = f"{path}.tmp.{os.getpid()}"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(agg, ensure_ascii=False, separators=(",", ":")))
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except Exception:
        pass


def read_usage_aggregates(telemetry_dir: Optional[str] = None) -> dict:
    """The cheap read surface over the rotation-surviving usage aggregates.

    Always returns the canonical shape (missing/corrupt file -> the empty shape, never an
    error): ``{"version", "sessions": {"count", "first_ts", "last_session_id"},
    "memories": {name: {"first_ts", "last_ts", "sessions", "last_session_id"}}}``.
    ``sessions.count`` / per-record ``sessions`` are DISTINCT-session counts (approximate —
    see the section comment above); ``first_ts``/``last_ts`` are recall-event timestamps;
    ``sessions.first_ts`` is the start of the whole observation span. ``last_session_id``
    keys are counter bookkeeping, not signal. Consumers: soak/curation/archive union this
    with the rotating ledger; RET-5 reads it as a ranking prior. Read-only; never raises.
    """
    try:
        td = _resolve_dir(telemetry_dir)
        return _load_usage_aggregates(td)
    except Exception:
        return _empty_aggregates()


# --------------------------------------------------------------------------- #
# Episode buffer (instrumentation tier) — append-only, DISTINCT from the recall
# ledger above. The recall ledger records memory NAMES surfaced per query; the episode
# buffer additionally pins the repo HEAD commit at recall time, so a future (separately
# roadmapped, NOT shipped here) autonomous-capture pass has a watermark to diff
# ``git log <head_commit>..HEAD`` against. It must start soaking now even though nothing
# reads it yet, since it cannot be backfilled retroactively.
#
# Same robustness contract as the recall ledger above: NEVER raises, fire-and-forget,
# size-bounded (reuses ``_rotate_if_needed``), no sensitive content (a truncated query
# preview only, same ``_QUERY_PREVIEW_CHARS`` budget — never the full prompt).
# --------------------------------------------------------------------------- #
def log_episode(
    recalled_names: List[str],
    *,
    query: str,
    repo_root: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    session_id: Optional[str] = None,
) -> bool:
    """Append ONE episode to the gitignored ``episode_buffer.jsonl``. Fire-and-forget.

    Records the recalled memory NAMES (not content — the buffer has nothing else to
    "replay"), a TRUNCATED query preview, the current session id, and the repo's HEAD
    commit at logging time (``None`` when it cannot be determined — e.g. not a git repo —
    never raises on that failure). ``session_id``, when given (the harness-provided id),
    keys the event directly instead of the file-based token — see ``current_session_id``.
    Returns True on a successful append, else False (a write failure degrades silently,
    mirroring ``log_recall_event``).
    """
    try:
        td = _resolve_dir(telemetry_dir)
        ensure_self_ignoring_dir(td)  # derived dir: mkdir + self-ignoring .gitignore (SEC-3)
        head_commit = None
        if repo_root:
            try:
                from .provenance import run_git

                head_commit = run_git(["rev-parse", "HEAD"], repo_root).strip() or None
            except Exception:
                head_commit = None
        event = {
            "ts": round(time.time(), 3),
            "session_id": current_session_id(td, session_id=session_id),
            "query_preview": (query or "")[:_QUERY_PREVIEW_CHARS],
            "recalled_names": [n for n in (recalled_names or []) if n],
            "head_commit": head_commit,
        }
        path = _episode_ledger_path(td)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        _rotate_if_needed(path)
        return True
    except Exception:
        return False


def read_episodes(telemetry_dir: Optional[str] = None) -> Iterator[dict]:
    """Yield parsed episode-buffer entries, skipping corrupt/partial lines. Never raises."""
    try:
        td = _resolve_dir(telemetry_dir)
        path = _episode_ledger_path(td)
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except Exception:
        return


# --------------------------------------------------------------------------- #
# Reconsolidation outcomes (immunize tier) — a THIRD, distinct ledger. Logs the verdict each
# time the memory-master agent re-grounds a recall-flagged-stale memory (graduate / fix /
# demote), feeding eval_recall.graduation_rate() (the accuracy axis of the scorecard). This
# module only LOGS the outcome; the per-item judgment + the actual reverify/fix/archive
# action live in reconsolidate.py / the memory-master agent, never here.
# --------------------------------------------------------------------------- #
def record_reconsolidation_outcome(
    name: str,
    outcome: str,
    *,
    telemetry_dir: Optional[str] = None,
) -> bool:
    """Append ONE reconsolidation verdict to the gitignored ``reconsolidation_events.jsonl``.

    ``outcome`` must be one of ``{"graduate", "fix", "demote"}`` — an invalid outcome is a
    silent no-op (returns ``False``) rather than corrupting ``graduation_rate()``'s
    denominator with garbage. Fire-and-forget; NEVER raises; size-bounded (reuses
    ``_rotate_if_needed``); no sensitive content (only the memory name + the verdict).
    """
    if outcome not in _RECONSOLIDATION_OUTCOMES:
        return False
    try:
        td = _resolve_dir(telemetry_dir)
        ensure_self_ignoring_dir(td)  # derived dir: mkdir + self-ignoring .gitignore (SEC-3)
        event = {"ts": round(time.time(), 3), "name": name, "outcome": outcome}
        path = _reconsolidation_ledger_path(td)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        _rotate_if_needed(path)
        return True
    except Exception:
        return False


def read_reconsolidation_events(telemetry_dir: Optional[str] = None) -> Iterator[dict]:
    """Yield parsed reconsolidation-outcome events, skipping corrupt/partial lines. Never raises."""
    try:
        td = _resolve_dir(telemetry_dir)
        path = _reconsolidation_ledger_path(td)
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except Exception:
        return
