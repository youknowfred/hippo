"""The CAP-2 pending QUEUE surface — reading, bounding, snoozing, and listing seeds.

Split out of ``capture.py`` along its queue-maintenance section banner (ED5R-3 runway
discipline; see CONTRIBUTING.md "Code layout"): ``capture.py`` stays the façade — it keeps
the ``python -m memory.capture`` entry point, the seed BUILD path (episode replay, git
evidence, salience, the SessionEnd write), and re-imports every name here — while this
sibling owns everything about the queue as a directory of already-written seeds: where it
lives (``default_pending_dir``), reading it back (``read_pending``/``corrupt_pending``/
``pending_count``), bounding it (CAP-6 ``prune_pending``), deferring its nudge
(``snooze_queue``/``queue_snoozed``), and rendering the drain listing
(``_format_listing``). TND-5 adds the hygiene: a re-capture of a session FOLDS into its
seed (``fold_seed``), seeds past 14 days / 20 sessions / the cap move to ``expired/``
(``expire_pending``, ``prune_pending``) and come back on request (``restore_pending``),
and every inflow and drain is counted in the daily rollups. Same contract as the façade:
everything here is read/maintenance over GITIGNORED ephemera — nothing in this module
writes ``.claude/memory/`` (the approval-gate firewall test covers this sibling too), and
nothing here deletes a seed except ``discard_pending``, a human's per-item call.
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
# TND-5: a seed nobody drained moves to ``<pending>/expired/`` once it is older than
# ``_EXPIRE_AGE_DAYS`` or ``_EXPIRE_SESSIONS`` later sessions have started — and so does a
# seed the CAP-6 cap pushes out. It leaves the listing and the nudge, and it is never
# deleted: ``hippo capture --restore`` moves it back, so nothing captured is destroyed
# without a human. The age and session clocks run from the seed's LAST refresh
# (``captured_at``, or ``restored_at`` after a restore), so a session still being folded
# into never expires underneath itself.
_EXPIRED_DIRNAME = "expired"
_EXPIRE_AGE_DAYS = 14
_EXPIRE_SESSIONS = 20
# Seed fields a fold UNIONS (order kept, prior first) — evidence replayed from the episode
# buffer, which rotates under a byte cap, so a later capture can hold LESS than an earlier
# one did. Everything else is the newest snapshot's.
_FOLD_UNION_FIELDS = (
    ("query_previews", 40),
    ("recalled_names", 60),
    ("decisions", 20),
    ("window_decisions", 20),
)
_SUBAGENT_STOP = "subagent-stop"


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


def _queue_telemetry_dir(
    pending_dir: Optional[str], memory_dir: Optional[str], telemetry_dir: Optional[str]
) -> Optional[str]:
    """Where the queue's inflow/drain counters land: the explicit dir, the corpus's ledger
    dir, ``HIPPO_TELEMETRY_DIR``, or the default queue's sibling ledger dir. ``None`` (no
    counting) when none of those exists. Never raises."""
    try:
        if telemetry_dir:
            return telemetry_dir
        if memory_dir:
            return default_telemetry_dir(memory_dir)
        override = os.environ.get("HIPPO_TELEMETRY_DIR")
        if override:
            return override
        if pending_dir and os.path.basename(os.path.normpath(pending_dir)) == _PENDING_DIRNAME:
            sibling = os.path.join(os.path.dirname(os.path.normpath(pending_dir)), ".memory-telemetry")
            return sibling if os.path.isdir(sibling) else None
    except Exception:
        return None
    return None


def _count(telemetry_dir: Optional[str], event: str, n: int = 1) -> None:
    """TND-5: one inflow/drain counter into the daily rollup. Never raises."""
    if not telemetry_dir or n <= 0:
        return
    try:
        from .telemetry_rollup import record_queue

        record_queue(telemetry_dir, event, n)
    except Exception:
        pass


def discard_pending(
    path: str,
    *,
    drafted: bool = False,
    telemetry_dir: Optional[str] = None,
    memory_dir: Optional[str] = None,
) -> bool:
    """Remove one drained/approved/dismissed seed from the queue. True on success. Never raises.

    The one place a seed is destroyed, and only ever on a human's per-item call. TND-5
    counts it: ``drafted=True`` when the seed became a memory, else ``discarded``.
    """
    try:
        os.remove(path)
    except Exception:
        return False
    td = _queue_telemetry_dir(os.path.dirname(path), memory_dir, telemetry_dir)
    _count(td, "drafted" if drafted else "discarded")
    return True


def expired_dir(pending_dir: str) -> str:
    """``<pending>/expired`` — where expired and over-cap seeds wait, recoverable."""
    return os.path.join(pending_dir, _EXPIRED_DIRNAME)


def _seed_files(directory: str) -> List[str]:
    try:
        return sorted(
            n for n in os.listdir(directory) if n.endswith(".json") and not n.startswith(".")
        )
    except Exception:
        return []


def expired_count(pending_dir: Optional[str] = None, *, memory_dir: Optional[str] = None) -> int:
    """How many seeds sit in ``expired/``. Never raises."""
    try:
        return len(_seed_files(expired_dir(_resolve_pending_dir(pending_dir, memory_dir))))
    except Exception:
        return 0


def _write_seed_json(path: str, seed: Dict) -> None:
    """Write one seed through a unique tmp + ``os.replace`` (COR-17); raises on failure."""
    doc = {k: v for k, v in seed.items() if k != "_path"}
    tmp = path + f".tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _free_name(directory: str, name: str) -> str:
    """``name`` in ``directory``, or ``<stem>.<n>.json`` when it is taken — a move into
    ``expired/`` must never overwrite an earlier expired copy of the same session."""
    if not os.path.exists(os.path.join(directory, name)):
        return name
    stem = name[:-5] if name.endswith(".json") else name
    n = 2
    while os.path.exists(os.path.join(directory, f"{stem}.{n}.json")):
        n += 1
    return f"{stem}.{n}.json"


def _canonical_name(name: str) -> str:
    """Strip ``_free_name``'s ``.<n>`` suffix: ``capture-x.2.json`` -> ``capture-x.json``."""
    stem = name[:-5] if name.endswith(".json") else name
    head, dot, tail = stem.rpartition(".")
    if dot and tail.isdigit() and head:
        stem = head
    return stem + ".json"


def _expire_one(seed: Dict, pending_dir: str, reason: str, now: float) -> bool:
    """Move one seed into ``expired/`` with ``expired_at`` and ``expired_reason`` stamped.
    The destination lands before the source is removed, so a crash between the two
    leaves a duplicate, never a loss. Never raises."""
    src = seed.get("_path") or ""
    try:
        ed = expired_dir(pending_dir)
        os.makedirs(ed, exist_ok=True)
        doc = dict(seed)
        doc["expired_at"] = round(now, 3)
        doc["expired_reason"] = reason
        _write_seed_json(os.path.join(ed, _free_name(ed, os.path.basename(src))), doc)
        os.remove(src)
        return True
    except Exception:
        return False


def _seed_clock(seed: Dict) -> float:
    """When a seed was last refreshed: the later of ``captured_at`` and ``restored_at``,
    else ``_seed_captured_at``'s fallback. The expiry clocks run from here."""
    vals = []
    for key in ("captured_at", "restored_at"):
        v = seed.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            vals.append(float(v))
    return max(vals) if vals else _seed_captured_at(seed)


def _session_starts(telemetry_dir: Optional[str]) -> List[float]:
    """Sorted first-event timestamps, one per recall-ledger session (``queue_snoozed``'s
    session clock). ``[]`` on any trouble — the session clock then never fires, and the
    age clock still does."""
    if not telemetry_dir:
        return []
    try:
        from .telemetry import read_events

        first: Dict[str, float] = {}
        for e in read_events(telemetry_dir):
            sid, ts = e.get("session_id"), e.get("ts")
            if sid and sid not in first and isinstance(ts, (int, float)) and not isinstance(ts, bool):
                first[sid] = float(ts)
        return sorted(first.values())
    except Exception:
        return []


def expire_pending(
    pending_dir: Optional[str] = None,
    *,
    memory_dir: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    now: Optional[float] = None,
    max_age_days: float = _EXPIRE_AGE_DAYS,
    max_sessions: int = _EXPIRE_SESSIONS,
) -> int:
    """Move every seed older than ``max_age_days`` — or with ``max_sessions`` sessions
    started since its last refresh — into ``expired/``. Returns how many moved and counts
    them (``expired``). Recoverable with ``restore_pending``; nothing is deleted. Never
    raises."""
    try:
        pd = _resolve_pending_dir(pending_dir, memory_dir)
        if not os.path.isdir(pd):
            return 0
        seeds = read_pending(pd)
        if not seeds:
            return 0
        now = time.time() if now is None else float(now)
        td = _queue_telemetry_dir(pd, memory_dir, telemetry_dir)
        starts = _session_starts(td)
        import bisect

        moved = 0
        for seed in seeds:
            clock = _seed_clock(seed)
            reason = None
            if now - clock > max_age_days * 86400.0:
                reason = "age"
            elif starts and len(starts) - bisect.bisect_right(starts, clock) >= max_sessions:
                reason = "sessions"
            if reason and _expire_one(seed, pd, reason, now):
                moved += 1
        _count(td, "expired", moved)
        return moved
    except Exception:
        return 0


def read_expired(pending_dir: Optional[str] = None, *, memory_dir: Optional[str] = None) -> List[Dict]:
    """The seeds in ``expired/``, newest expiry first. Skips unreadable files. Never raises."""
    out: List[Dict] = []
    try:
        ed = expired_dir(_resolve_pending_dir(pending_dir, memory_dir))
        for name in _seed_files(ed):
            try:
                with open(os.path.join(ed, name), "r", encoding="utf-8") as fh:
                    obj = json.load(fh)
                if isinstance(obj, dict):
                    obj["_path"] = os.path.join(ed, name)
                    out.append(obj)
            except Exception:
                continue
        out.sort(key=lambda s: (-float(s.get("expired_at") or 0.0), os.path.basename(s["_path"])))
    except Exception:
        return out
    return out


def restore_pending(
    target: Optional[str] = None,
    *,
    restore_all: bool = False,
    pending_dir: Optional[str] = None,
    memory_dir: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    now: Optional[float] = None,
) -> List[str]:
    """Move expired seed(s) back into the queue; returns the restored queue paths.

    ``target`` names one expired seed (its filename, its path, or the session id it was
    captured under); ``restore_all`` takes every one. The restored seed gets a fresh
    ``restored_at``, so it does not expire again on the next capture. When the session
    already has a live seed (it kept capturing after this copy expired), the two are
    FOLDED into that one file. Counts each as ``restored``. Never raises.
    """
    restored: List[str] = []
    try:
        pd = _resolve_pending_dir(pending_dir, memory_dir)
        candidates = read_expired(pd)
        if not restore_all:
            want = (target or "").strip()
            if not want:
                return []
            base = os.path.basename(want)
            candidates = [
                s for s in candidates
                if os.path.basename(s["_path"]) in (base, base + ".json")
                or _canonical_name(os.path.basename(s["_path"])) in (base, base + ".json")
                or str(s.get("session_id") or "") == want
            ]
        now = time.time() if now is None else float(now)
        ensure_self_ignoring_dir(pd)
        # Oldest expiry first, so a fold of several copies of one session keeps the
        # newest evidence last.
        for seed in sorted(candidates, key=lambda s: float(s.get("expired_at") or 0.0)):
            src = seed["_path"]
            doc = {k: v for k, v in seed.items() if k not in ("expired_at", "expired_reason")}
            doc["restored_at"] = round(now, 3)
            dest = os.path.join(pd, _canonical_name(os.path.basename(src)))
            live = None
            if os.path.isfile(dest):
                try:
                    with open(dest, "r", encoding="utf-8") as fh:
                        live = json.load(fh)
                except Exception:
                    live = None
            if isinstance(live, dict):
                # Order by when each copy was CAPTURED (the restore stamp is not evidence).
                older, newer = sorted((doc, live), key=_seed_captured_at)
                doc = fold_seed(older, newer)
                doc["restored_at"] = round(now, 3)
            try:
                _write_seed_json(dest, doc)
                os.remove(src)
            except Exception:
                continue
            restored.append(dest)
        _count(_queue_telemetry_dir(pd, memory_dir, telemetry_dir), "restored", len(restored))
    except Exception:
        return restored
    return restored


def _union(prior, new, cap: int) -> list:
    out: list = []
    seen = set()
    for seq in (prior, new):
        if not isinstance(seq, list):
            continue
        for item in seq:
            key = json.dumps(item, sort_keys=True, ensure_ascii=False) if not isinstance(item, str) else item
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out[:cap]


def fold_seed(prior: Dict, new: Dict) -> Dict:
    """TND-5: merge a fresh capture of a session into that session's existing seed.

    A session's seed is keyed on its session id, and a SubagentStop payload carries the
    PARENT's id, so every subagent stop re-captures the parent's ONE seed. A plain
    overwrite let the newest capture erase what an earlier one held: the episode buffer
    rotates, so evidence replayed later can be thinner. The fold keeps it:

    - query previews, recalled names and decisions are unioned (order kept, prior first);
    - changed paths are unioned and re-sorted; the diff hunks are the newest non-empty;
    - the watermark (``head_commit``), ``earliest_ts`` and ``first_captured_at`` keep the
      earliest value, ``episode_count`` the largest, ``salience`` the higher score;
    - ``reason`` stays the session's own: a subagent stop never relabels a seed a real
      SessionEnd wrote, and ``subagent_stops`` counts how many stops folded in;
    - ``folds`` counts every capture merged into the seed after the first.

    Pure: returns a new dict, touches no file. Never raises (falls back to ``new``).
    """
    try:
        if not isinstance(prior, dict) or not prior:
            out = dict(new)
            if new.get("reason") == _SUBAGENT_STOP:
                out["subagent_stops"] = 1  # additive key, absent on an ordinary seed (ED-4)
            return out
        out = dict(new)
        for key, cap in _FOLD_UNION_FIELDS:
            merged = _union(prior.get(key), new.get(key), cap)
            if merged or key in new or key in prior:
                out[key] = merged
        if not out.get("window_decisions"):
            out.pop("window_decisions", None)
        paths = _union(prior.get("changed_paths"), new.get("changed_paths"), 10_000)
        out["changed_paths"] = sorted(p for p in paths if isinstance(p, str))[:200]
        if not new.get("diff_hunks") and prior.get("diff_hunks"):
            for key in ("diff_hunks", "hunks_secret_flagged", "hunks_threat_flagged"):
                if key in prior:
                    out[key] = prior[key]
        if prior.get("head_commit"):
            out["head_commit"] = prior["head_commit"]
        ts = [
            float(v) for v in (prior.get("earliest_ts"), new.get("earliest_ts"))
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        if ts:
            out["earliest_ts"] = min(ts)
        counts = [
            int(v) for v in (prior.get("episode_count"), new.get("episode_count"))
            if isinstance(v, int) and not isinstance(v, bool)
        ]
        if counts:
            out["episode_count"] = max(counts)
        if _seed_score(prior) > _seed_score(new) and prior.get("salience"):
            out["salience"] = prior["salience"]
        first = prior.get("first_captured_at", prior.get("captured_at"))
        if isinstance(first, (int, float)) and not isinstance(first, bool):
            out["first_captured_at"] = first
        if new.get("reason") == _SUBAGENT_STOP and prior.get("reason") not in (None, _SUBAGENT_STOP):
            out["reason"] = prior.get("reason")
        stops = prior.get("subagent_stops")
        stops = stops if isinstance(stops, int) and not isinstance(stops, bool) else (
            1 if prior.get("reason") == _SUBAGENT_STOP else 0
        )
        stops += 1 if new.get("reason") == _SUBAGENT_STOP else 0
        if stops:
            out["subagent_stops"] = stops
        folds = prior.get("folds")
        out["folds"] = (folds if isinstance(folds, int) and not isinstance(folds, bool) else 0) + 1
        if "restored_at" in prior and "restored_at" not in out:
            out["restored_at"] = prior["restored_at"]
        out.pop("_path", None)
        return out
    except Exception:
        return dict(new)


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
    telemetry_dir: Optional[str] = None,
) -> int:
    """Bound the queue at ``max_seeds`` — move the LOWEST-value, then OLDEST, seeds past the
    cap into ``expired/``.

    A pending seed is gitignored ephemera awaiting review; a queue that grows one-seed-per-session
    without limit is itself a soak the LIF goal forbids. Keeps the ``max_seeds`` a drain would
    lead with — ranked by ``(salience score desc, captured_at desc)`` — so a just-written seed
    (the newest ``captured_at``) always survives a same-score tie, giving a rolling window rather
    than a hard stop that would silently swallow new captures. Returns the number moved. TND-5:
    the overflow is MOVED, never deleted (it used to be) — ``restore_pending`` brings any of it
    back, and each move counts as ``expired``. A label/order operation on the queue, NEVER on
    a seed's fate in the corpus. Never raises.
    """
    try:
        pd = _resolve_pending_dir(pending_dir, memory_dir)
        if not os.path.isdir(pd):
            return 0
        seeds = read_pending(pd)
        if len(seeds) <= max_seeds:
            return 0
        ranked = sorted(seeds, key=lambda s: (-_seed_score(s), -_seed_captured_at(s)))
        now = time.time()
        pruned = 0
        for seed in ranked[max_seeds:]:
            if _expire_one(seed, pd, "cap", now):
                pruned += 1
        _count(_queue_telemetry_dir(pd, memory_dir, telemetry_dir), "expired", pruned)
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


def expired_line(n: int) -> Optional[str]:
    """TND-5: the one counted line for the ``expired/`` shelf; ``None`` when it is empty."""
    if not n:
        return None
    return (
        f"{n} expired capture(s) kept in the queue's expired/ folder (older than "
        f"{_EXPIRE_AGE_DAYS} days, {_EXPIRE_SESSIONS} sessions, or past the "
        f"{_MAX_PENDING_SEEDS}-seed cap; never deleted) — bring one back with "
        "`hippo capture --restore <seed>`, or all with `hippo capture --restore --all`."
    )


def _format_listing(seeds: List[Dict], expired: int = 0) -> str:
    tail = expired_line(expired)
    if not seeds:
        head = "No pending captures — the queue is empty."
        return head + ("\n" + tail if tail else "")
    out = [f"{len(seeds)} pending capture(s) awaiting review (nothing is in the corpus yet):", ""]
    for s in seeds:
        sid = s.get("session_id") or "(no session id)"
        wm = (s.get("head_commit") or "?")[:12]
        head = (s.get("head") or "?")[:12]
        out.append(f"  • {os.path.basename(s.get('_path', ''))}  session={sid}")
        out.append(f"      commits: {wm}..{head}   episodes: {s.get('episode_count', 0)}")
        stops = s.get("subagent_stops")
        if isinstance(stops, int) and not isinstance(stops, bool) and stops > 0:
            out.append(f"      folded in: {stops} subagent stop(s) from this session")
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
        if tri.get("abstained"):
            # WRT-1: an honest "no durable fact" is a first-class triage outcome, rendered
            # as such — never a missing block the reviewer might mistake for a failure.
            out.append(
                "      triage (LLM): ABSTAINED — the model found no durable fact worth "
                "saving (the heuristic evidence above still stands for human review)"
            )
        elif tri:
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
            ung = tri.get("ungrounded_tokens") or []
            if ung:
                # WRT-1: mechanical doubt — these identifiers appear NOWHERE in the
                # evidence the triage prompt carried, so the model cannot have taken
                # them from this session.
                out.append(
                    "        ⚠ ungrounded identifiers in the draft (appear nowhere in the "
                    f"session evidence): {', '.join(str(u) for u in ung[:6])} — verify "
                    "against the diff/queries before trusting the description"
                )
    if tail:
        out += ["", tail]
    return "\n".join(out)
