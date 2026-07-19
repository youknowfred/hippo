"""The CAP-2 pending QUEUE surface — reading, bounding, snoozing, and listing seeds.

Split out of ``capture.py`` along its queue-maintenance section banner (ED5R-3 runway
discipline; see CONTRIBUTING.md "Code layout"): ``capture.py`` stays the façade — it keeps
the ``python -m memory.capture`` entry point, the seed BUILD path (episode replay, git
evidence, salience, the SessionEnd write), and re-imports every name here — while this
sibling owns everything about the queue as a directory of already-written seeds: where it
lives (``default_pending_dir``), reading it back (``read_pending``/``corrupt_pending``/
``pending_count``), bounding it (CAP-6 ``prune_pending``), deferring its nudge
(``snooze_queue``/``queue_snoozed``), and rendering the drain listing
(``_format_listing``). Same contract as the façade: everything here is read/maintenance
over GITIGNORED ephemera — nothing in this module writes ``.claude/memory/`` (the
approval-gate firewall test covers this sibling too).
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

from .provenance import ensure_self_ignoring_dir, resolve_dirs
from .telemetry import default_telemetry_dir

# The gitignored pending queue — a sibling of ``.claude/memory`` and of the index/telemetry
# dirs, following the same self-ignoring-cache convention (SEC-3). It is NOT the corpus and is
# NOT git-tracked: a draft here is a proposal awaiting explicit per-item approval, never memory.
_PENDING_DIRNAME = ".memory-pending"
# CAP-6: hard bound on the pending queue. One seed lands per session that recalls anything, so
# an un-drained queue would grow WITHOUT LIMIT — the exact "soaks forever" footgun the LIF
# workstream goal ("nothing nags forever") already closed for reconsolidation. When a fresh
# capture pushes the queue past this cap, the LOWEST-value then OLDEST seeds are pruned so the
# queue keeps the sessions a drain would lead with (highest salience, most recent). Ephemera in
# a gitignored dir: pruning a stale trivial seed loses nothing a re-capture couldn't redraft.
_MAX_PENDING_SEEDS = 50
# CAP-6: how many NEW recall-ledger sessions an explicit queue --snooze holds the SessionStart
# nudge for before it re-nags. Parity with ``reconsolidate._SNOOZE_WINDOW_SESSIONS`` (same value,
# same session-aging rhythm): a snooze is a DEFERRAL, never a dismissal — it must expire so a
# growing backlog resurfaces. The nudge is the only thing snoozed; the seeds are untouched.
_SNOOZE_WINDOW_SESSIONS = 5
# The queue-snooze marker: a tiny sibling of the seeds inside the gitignored pending dir (queue
# state lives with the queue). Dotfile so ``read_pending``/``pending_count`` (``*.json`` only)
# never mistake it for a seed.
_SNOOZE_MARKER = ".capture-snooze.json"


def default_pending_dir(memory_dir: str) -> str:
    """``.claude/.memory-pending`` — a sibling of ``.claude/memory`` (its own gitignored dir).

    Mirrors ``build_index.default_index_dir`` / ``telemetry.default_telemetry_dir`` so the
    queue lands beside the index and ledgers. ``HIPPO_PENDING_DIR`` overrides (hermetic tests).
    """
    override = os.environ.get("HIPPO_PENDING_DIR")
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(memory_dir)), _PENDING_DIRNAME)


def _resolve_pending_dir(pending_dir: Optional[str], memory_dir: Optional[str]) -> str:
    if pending_dir:
        return pending_dir
    if memory_dir:
        return default_pending_dir(memory_dir)
    md, _ = resolve_dirs()
    return default_pending_dir(md)


def _seed_score(seed: Dict) -> int:
    """The stored salience score of a seed; 0 for pre-GRW-1 (schema 1) seeds. Never raises."""
    try:
        return int((seed.get("salience") or {}).get("score", 0))
    except Exception:
        return 0


def read_pending(pending_dir: Optional[str] = None, *, memory_dir: Optional[str] = None) -> List[Dict]:
    """Every pending capture seed, HIGH-VALUE FIRST (GRW-1), then by filename for stability.

    The salience score only ORDERS the review queue so a deep backlog leads with the sessions
    most worth drafting — a low score never drops a seed (label, not gate). Skips corrupt
    files. Never raises.
    """
    out: List[Dict] = []
    try:
        pd = _resolve_pending_dir(pending_dir, memory_dir)
        if not os.path.isdir(pd):
            return []
        for name in sorted(os.listdir(pd)):
            # Seeds are ``capture-*.json``; skip dotfiles (the ``.gitignore`` and the CAP-6
            # ``.capture-snooze.json`` marker are queue state, never seeds).
            if name.startswith(".") or not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(pd, name), "r", encoding="utf-8") as fh:
                    obj = json.load(fh)
                if isinstance(obj, dict):
                    obj["_path"] = os.path.join(pd, name)
                    out.append(obj)
            except Exception:
                continue
        out.sort(key=lambda s: (-_seed_score(s), os.path.basename(s.get("_path", ""))))
    except Exception:
        return out
    return out


def corrupt_pending(
    pending_dir: Optional[str] = None, *, memory_dir: Optional[str] = None
) -> List[str]:
    """Seed FILENAMES in the queue that ``read_pending`` cannot parse. Never raises.

    RCH-9: a corrupt seed silently vanished from the drain listing while the bare
    file count (``pending_count``, the SessionStart nudge) still included it — the
    queue said "2 pending", the listing showed one, and a captured session was lost
    without a trace. The listing names what it cannot read; deleting or inspecting
    the file is the human's call (the queue is gitignored ephemera).
    """
    out: List[str] = []
    try:
        pd = _resolve_pending_dir(pending_dir, memory_dir)
        if not os.path.isdir(pd):
            return []
        for name in sorted(os.listdir(pd)):
            if name.startswith(".") or not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(pd, name), "r", encoding="utf-8") as fh:
                    obj = json.load(fh)
                if not isinstance(obj, dict):
                    out.append(name)
            except Exception:
                out.append(name)
    except Exception:
        return out
    return out


def pending_count(pending_dir: Optional[str] = None, *, memory_dir: Optional[str] = None) -> int:
    """Number of pending capture seeds (cheap listdir). Never raises."""
    try:
        pd = _resolve_pending_dir(pending_dir, memory_dir)
        if not os.path.isdir(pd):
            return 0
        return sum(1 for n in os.listdir(pd) if n.endswith(".json") and not n.startswith("."))
    except Exception:
        return 0


def discard_pending(path: str) -> bool:
    """Remove one drained/approved/dismissed seed from the queue. True on success. Never raises."""
    try:
        os.remove(path)
        return True
    except Exception:
        return False


def _seed_captured_at(seed: Dict) -> float:
    """A seed's capture timestamp for recency ordering; falls back to earliest_ts, then 0.0."""
    for key in ("captured_at", "earliest_ts"):
        val = seed.get(key)
        try:
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                return float(val)
        except Exception:
            pass
    return 0.0


def prune_pending(
    pending_dir: Optional[str] = None,
    *,
    memory_dir: Optional[str] = None,
    max_seeds: int = _MAX_PENDING_SEEDS,
) -> int:
    """Bound the queue at ``max_seeds`` — drop the LOWEST-value, then OLDEST, seeds past the cap.

    A pending seed is gitignored ephemera awaiting review; a queue that grows one-seed-per-session
    without limit is itself a soak the LIF goal forbids. Keeps the ``max_seeds`` a drain would
    lead with — ranked by ``(salience score desc, captured_at desc)`` — so a just-written seed
    (the newest ``captured_at``) always survives a same-score tie, giving a rolling window rather
    than a hard stop that would silently swallow new captures. Returns the number pruned. A
    label/order operation on the queue, NEVER on a seed's fate in the corpus. Never raises.
    """
    try:
        pd = _resolve_pending_dir(pending_dir, memory_dir)
        if not os.path.isdir(pd):
            return 0
        seeds = read_pending(pd)
        if len(seeds) <= max_seeds:
            return 0
        ranked = sorted(seeds, key=lambda s: (-_seed_score(s), -_seed_captured_at(s)))
        pruned = 0
        for seed in ranked[max_seeds:]:
            if discard_pending(seed.get("_path", "")):
                pruned += 1
        return pruned
    except Exception:
        return 0


def _snooze_marker_path(pending_dir: str) -> str:
    return os.path.join(pending_dir, _SNOOZE_MARKER)


def snooze_queue(
    pending_dir: Optional[str] = None, *, memory_dir: Optional[str] = None
) -> bool:
    """Defer the SessionStart pending-capture nudge for ``_SNOOZE_WINDOW_SESSIONS`` sessions.

    Writes a timestamp marker inside the gitignored pending dir. The seeds are UNTOUCHED — this
    quiets only the nudge, and only until it ages out (parity with the reconsolidation snooze:
    a deferral, never a dismissal). Returns True on success. Never raises.
    """
    try:
        pd = _resolve_pending_dir(pending_dir, memory_dir)
        ensure_self_ignoring_dir(pd)
        marker = _snooze_marker_path(pd)
        tmp = marker + f".tmp.{os.getpid()}"  # COR-17: unique per writer — concurrent processes must not share a tmp
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"ts": round(time.time(), 3)}, fh)
        os.replace(tmp, marker)
        return True
    except Exception:
        return False


def queue_snoozed(
    pending_dir: Optional[str] = None,
    *,
    memory_dir: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
) -> bool:
    """True while an explicit queue snooze is younger than ``_SNOOZE_WINDOW_SESSIONS`` sessions.

    Ages by SESSIONS, not wall-clock, exactly like ``reconsolidate._snoozed_names``: each recall-
    ledger session whose first ts-carrying event lands after the ack counts once, and the snooze
    expires once ``_SNOOZE_WINDOW_SESSIONS`` such sessions have started. Degrades toward
    RE-NAGGING, never silence: a missing/corrupt marker, a ts-less ack, or an unreadable ledger
    all read as "not snoozed". Read-only; never raises.
    """
    try:
        pd = _resolve_pending_dir(pending_dir, memory_dir)
        marker = _snooze_marker_path(pd)
        if not os.path.isfile(marker):
            return False
        with open(marker, "r", encoding="utf-8") as fh:
            acked = float((json.load(fh) or {}).get("ts") or 0.0)
        if acked <= 0:
            return False
        if telemetry_dir is None and memory_dir is not None:
            telemetry_dir = default_telemetry_dir(memory_dir)
        from .telemetry import read_events

        first_ts: Dict[str, float] = {}
        for e in read_events(telemetry_dir):
            sid, ts = e.get("session_id"), e.get("ts")
            if (
                sid
                and sid not in first_ts
                and isinstance(ts, (int, float))
                and not isinstance(ts, bool)
            ):
                first_ts[sid] = float(ts)
        started_since = sum(1 for s in first_ts.values() if s > acked)
        return started_since < _SNOOZE_WINDOW_SESSIONS
    except Exception:
        return False


def _format_listing(seeds: List[Dict]) -> str:
    if not seeds:
        return "No pending captures — the queue is empty."
    out = [f"{len(seeds)} pending capture(s) awaiting review (nothing is in the corpus yet):", ""]
    for s in seeds:
        sid = s.get("session_id") or "(no session id)"
        wm = (s.get("head_commit") or "?")[:12]
        head = (s.get("head") or "?")[:12]
        out.append(f"  • {os.path.basename(s.get('_path', ''))}  session={sid}")
        out.append(f"      commits: {wm}..{head}   episodes: {s.get('episode_count', 0)}")
        sal = s.get("salience") or {}
        if sal:
            out.append(
                f"      value: {sal.get('score', 0)}"
                + (" (trivial session)" if sal.get("trivial") else "")
            )
        cp = s.get("changed_paths") or []
        if cp:
            shown = ", ".join(cp[:8]) + (f", +{len(cp) - 8} more" if len(cp) > 8 else "")
            out.append(f"      changed: {shown}")
        rn = s.get("recalled_names") or []
        if rn:
            out.append(f"      recalled: {', '.join(rn[:10])}")
        qp = s.get("query_previews") or []
        if qp:
            out.append(f"      queries: {'; '.join(qp[:5])}")
        hunks = s.get("diff_hunks") or ""
        if hunks:
            out.append(f"      evidence: {len(hunks.encode('utf-8'))} bytes of verbatim diff hunks")
            if s.get("hunks_secret_flagged"):
                out.append(
                    "      ⚠ secret lint flagged these hunks — do NOT fence them into a memory "
                    "body without scrubbing (run memory.secrets.scan_with_remediation first)"
                )
            if s.get("hunks_threat_flagged"):
                out.append(
                    "      ⚠ threat lint flagged these hunks (SEN-2 Tier-A: invisible Unicode / "
                    "confusable / exfil shape / HTML comment) — inspect before fencing into a "
                    "body (run memory.threat_lint.scan_tier_a on the exact lines)"
                )
        dec = s.get("decisions") or []
        if dec:
            out.append(f"      decisions: {'; '.join(str(d) for d in dec[:5])}")
        wdec = s.get("window_decisions") or []
        if wdec:
            # WRT-3: visibly distinct from the session-proven line above — this class was
            # recorded WITHOUT this session's id (MCP tool / bare --add-decision) and is
            # matched only by its ts falling inside the session's episode span.
            out.append(
                "      decisions (WINDOW-MATCHED, not session-proven — recorded without "
                f"this session's id; ts inside its episode span): "
                f"{'; '.join(str(d) for d in wdec[:5])}"
            )
        tri = s.get("llm_triage") or {}
        if tri:
            out.append(
                f"      triage (LLM suggestion — ratify or discard at drain): "
                f"type={tri.get('suggested_type') or '?'}  name={tri.get('suggested_name') or '?'}"
            )
            if tri.get("draft_description"):
                out.append(f"        draft description: {tri['draft_description']}")
            dups = tri.get("llm_duplicate_flags") or []
            if dups:
                out.append(f"        possible duplicates (LLM 2nd opinion): {', '.join(dups[:5])}")
            dc = tri.get("dup_check") or {}
            if dc.get("neighbors"):
                shown = ", ".join(
                    f"{n.get('name')} ({n.get('score')})" for n in dc["neighbors"][:3]
                )
                out.append(f"        index dup check: route={dc.get('route')} — {shown}")
            elif dc.get("route"):
                out.append(f"        index dup check: route={dc.get('route')}")
            if tri.get("secret_flagged"):
                out.append(
                    "        ⚠ secret lint flagged the triage text — scrub before any corpus use"
                )
    return "\n".join(out)
