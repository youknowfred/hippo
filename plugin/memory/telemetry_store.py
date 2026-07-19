"""Telemetry ledger substrate — decomposed out of ``telemetry.py`` (ED5R-3, pure code
motion; the façade re-imports every name here, so ``memory.telemetry.<name>`` keeps
resolving).

This sibling owns WHERE the ledgers live (dir resolution + per-ledger paths), the
session-identity token, byte-bounded rotation, and the two read iterators other siblings
consume (``read_events``, ``read_episodes``). The writers stay in the façade — their
WRITE_OPEN_ALLOWLIST keys and never-raise contracts are documented there.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Iterator, Optional

from .provenance import ensure_self_ignoring_dir

_TELEMETRY_DIRNAME = ".memory-telemetry"
_LEDGER_NAME = "recall_events.jsonl"
_EPISODE_LEDGER_NAME = "episode_buffer.jsonl"
_RECONSOLIDATION_LEDGER_NAME = "reconsolidation_events.jsonl"
_OUTCOME_LEDGER_NAME = "outcome_events.jsonl"  # SIG-4: PostToolUse read-signal (KPI-2)
_USAGE_AGGREGATES_NAME = "usage_aggregates.json"
_THREAT_LEDGER_NAME = "threat_findings.jsonl"  # SEN-2 Tier-B: measured, never surfaced
_SESSION_NAME = "session"

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


def _outcome_ledger_path(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, _OUTCOME_LEDGER_NAME)


def _threat_ledger_path(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, _THREAT_LEDGER_NAME)


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
        tmp = path + f".tmp.{os.getpid()}"  # COR-17: unique per writer — concurrent processes must not share a tmp
        with open(tmp, "wb") as fh:
            fh.write(tail)
        os.replace(tmp, path)
    except Exception:
        pass


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
