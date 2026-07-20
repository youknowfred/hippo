"""Usage aggregates — the two tiers that answer "was this memory ever actually used?".

Decomposed out of ``telemetry.py`` (ED5R-3, pure code motion). Both tiers exist for the
same reason: the recall ledger they sit beside is a byte-capped ROTATING buffer, so
"never recalled" is a claim it structurally cannot make. They differ in whose usage they
can see:

- **LIF-4 ``usage_aggregates.json``** — clone-local, gitignored, and never rotated. Folded
  from every ``log_recall_event`` append; its size is bounded by CORPUS size (~100 bytes
  per ever-recalled memory) rather than by history length, so a long-lived corpus keeps
  its oldest usage evidence after the ledger's byte-capped tail drops it.
- **TEA-5 ``.usage/<user>.json``** — the COMMITTED per-user summary teammates union,
  because the tier above still only knows THIS clone: a memory a teammate hits daily
  reads as archive-cold on your machine without it.

soak/curation/archive union both before judging coldness; RET-5 reads the first as a
ranking prior. Same robustness contract as the ledgers — never raises, fire-and-forget,
corrupt input self-heals to a fresh value. Both writers keep their own COR-17 unique-tmp +
``os.replace`` (pid-suffixed), which is why both hold entries in
``tests/test_write_discipline.py``'s allowlist rather than routing through ``atomic``.

The ``telemetry`` façade re-exports every name below, and is the only caller of
``_update_usage_aggregates`` (from ``log_recall_event``). Siblings never import it back.
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

from .telemetry_store import _resolve_dir, _usage_aggregates_path


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
# TEA-5: committed per-user usage summary — ``.claude/memory/.usage/<user>.json``.
# The rotating recall ledger and the ``usage_aggregates.json`` above are BOTH clone-local
# and gitignored, so "never recalled" means "never in THIS clone" — a memory a teammate hits
# daily reads as archive-cold on your machine. This tier is the opt-in fix: an append-only,
# COMMITTED (NOT self-ignored — the whole point is that teammates union it) per-user summary
# under the corpus tree, tiny and merge-friendly (memory names + counts + timestamps only, NO
# session ids — those are bookkeeping and would leak into git). Curation UNIONS every user's
# summary before judging coldness. It is NOT indexed/recalled/floor-scanned: ``.usage`` is a
# subdir, and ``_iter_memory_files`` only yields ``*.md`` files (never recurses), so it is
# skipped exactly like ``archive/``.
# --------------------------------------------------------------------------- #
_USAGE_DIRNAME = ".usage"
_COMMITTED_USAGE_VERSION = 1


def committed_usage_dir(memory_dir: str) -> str:
    """``<memory_dir>/.usage`` — the committed per-user usage summaries' one canonical home."""
    return os.path.join(memory_dir, _USAGE_DIRNAME)


def read_committed_usage(memory_dir: Optional[str]) -> dict:
    """Union of EVERY committed per-user summary under ``<memory_dir>/.usage/*.json`` (TEA-5).

    Returns ``{"memories": set(names), "sessions": int}`` — the set of memory stems ANY
    teammate has recalled, and the summed distinct-session count across users. Missing dir or a
    corrupt file contributes nothing; never raises. This is what ``soak.curation_report`` unions
    into its ``recalled`` set (and ``soak_status`` into its distinct count) so a teammate's daily
    hit is never miscounted as clone-local dead weight."""
    memories: set = set()
    sessions = 0
    try:
        if not memory_dir:
            return {"memories": memories, "sessions": 0}
        usage_dir = committed_usage_dir(memory_dir)
        if not os.path.isdir(usage_dir):
            return {"memories": memories, "sessions": 0}
        for fname in sorted(os.listdir(usage_dir)):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(usage_dir, fname), "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            mems = data.get("memories")
            if isinstance(mems, dict):
                memories |= {k for k in mems if isinstance(k, str)}
            sess = data.get("sessions")
            if isinstance(sess, dict):
                c = sess.get("count")
                if isinstance(c, int) and not isinstance(c, bool) and c > 0:
                    sessions += c
    except Exception:
        pass
    return {"memories": memories, "sessions": sessions}


def write_user_usage_summary(
    memory_dir: str, user_slug: str, telemetry_dir: Optional[str] = None
) -> Optional[str]:
    """Fold THIS clone's rotation-surviving aggregates into the COMMITTED per-user summary
    ``<memory_dir>/.usage/<user_slug>.json`` (TEA-5). Returns the path written, or None on
    failure.

    Append-only union — a re-run never loses ground: ``max`` on session/per-memory counts,
    ``min`` on ``first_ts``, ``max`` on ``last_ts``. Session ids are deliberately NOT written
    (bookkeeping, and they would leak into git). The file is COMMITTED, so — unlike the
    telemetry aggregates — it is NEVER self-ignored. Agent/user-gated (never on the hot path).
    Atomic write (tmp + os.replace, pid-suffixed). Never raises."""
    try:
        if not memory_dir or not user_slug:
            return None
        agg = read_usage_aggregates(telemetry_dir)
        usage_dir = committed_usage_dir(memory_dir)
        os.makedirs(usage_dir, exist_ok=True)
        path = os.path.join(usage_dir, f"{user_slug}.json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                prior = json.load(fh)
            if not isinstance(prior, dict):
                prior = {}
        except Exception:
            prior = {}
        prior_mems = prior.get("memories") if isinstance(prior.get("memories"), dict) else {}
        prior_sess = prior.get("sessions") if isinstance(prior.get("sessions"), dict) else {}

        def _mx(a, b):
            a = a if isinstance(a, (int, float)) and not isinstance(a, bool) else None
            b = b if isinstance(b, (int, float)) and not isinstance(b, bool) else None
            vals = [v for v in (a, b) if v is not None]
            return max(vals) if vals else None

        def _mn(a, b):
            a = a if isinstance(a, (int, float)) and not isinstance(a, bool) else None
            b = b if isinstance(b, (int, float)) and not isinstance(b, bool) else None
            vals = [v for v in (a, b) if v is not None]
            return min(vals) if vals else None

        merged_mems: dict = {k: v for k, v in prior_mems.items() if isinstance(v, dict)}
        for name, rec in agg.get("memories", {}).items():
            if not isinstance(name, str) or not isinstance(rec, dict):
                continue
            old = merged_mems.get(name) or {}
            merged_mems[name] = {
                "first_ts": _mn(old.get("first_ts"), rec.get("first_ts")),
                "last_ts": _mx(old.get("last_ts"), rec.get("last_ts")),
                "sessions": int(_mx(old.get("sessions"), rec.get("sessions")) or 0),
            }
        agg_sess = agg.get("sessions", {})
        out = {
            "version": _COMMITTED_USAGE_VERSION,
            "user": user_slug,
            "sessions": {
                "count": int(_mx(prior_sess.get("count"), agg_sess.get("count")) or 0),
                "first_ts": _mn(prior_sess.get("first_ts"), agg_sess.get("first_ts")),
                "last_ts": _mx(prior_sess.get("last_ts"), agg_sess.get("last_ts")),
            },
            "memories": merged_mems,
        }
        tmp = f"{path}.tmp.{os.getpid()}"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return None
        return path
    except Exception:
        return None
