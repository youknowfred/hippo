"""OBS-1: rotation-proof daily rollups — the durable numbers every v2 KPI reads.

The recall ledger rotates at ~2 MB, which on the busiest corpus kept about 2.3 days of
rows, so nothing could be measured over 30 days. Each hook run and each SessionStart now
also folds a few counters into today's accumulator (``rollup_today.json``, a few KB). When
the local date changes, the finished day is appended as one line to
``daily_rollups.jsonl`` (capped at 365 days) and a fresh accumulator starts. Nothing here is
rebuilt from the ledgers, so trimming or deleting them leaves the rollups intact.

A day row holds:
  - hook: prompts by trigger class (HOT-1's human + machine classes), the backend mix and
    abstentions of human turns, hygiene skips, injected chars (total, by trigger, and a
    per-session histogram), and histograms of the shell-measured wall (OBS-4) and the
    in-process latency;
  - session_start: runs, a chars histogram, runs at the cap, and how often each producer
    was dropped or cut by the budget;
  - surface (OBS-2): uses per ``surface:verb[:action]`` — every MCP tool call, every
    ``hippo <verb>``, every skill preflight, every hook spawn (and a failed one) — plus
    tallies of the client and the plugin version that served them. The bash-only surfaces
    append a line to ``usage_spool.jsonl`` (``hippo_note_usage`` in ``_resolve_py.sh``,
    no Python spawn) and the next fold drains it into the open day.

Latency and char values are kept as fixed-bucket histograms so 30-day percentiles merge
across days; a percentile reads as the bucket's upper bound ("≤ X"). Per-corpus (it lives in
the corpus's telemetry dir), gitignored, never raises, and every update holds an ``flock``
so concurrent sessions on one host never lose an increment.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional

_TODAY_NAME = "rollup_today.json"
_SPOOL_NAME = "usage_spool.jsonl"
# A spool line is short and written by our own scripts; anything longer is not ours.
_MAX_SPOOL_LINE = 512
_ROLLUP_NAME = "daily_rollups.jsonl"
_LOCK_NAME = ".rollup.lock"
ROLLUP_VERSION = 1
MAX_DAYS = 365

# Upper bounds (ms / chars); anything above the last lands in "inf".
_MS_BUCKETS = (10, 25, 50, 75, 100, 150, 200, 300, 400, 500, 650, 800, 1000, 1250, 1500,
               2000, 2500, 3000, 4000, 5000, 7500, 10000, 15000)
_SS_CHAR_BUCKETS = (250, 500, 1000, 1500, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 8500,
                    8800, 9000)
_SESSION_CHAR_BUCKETS = (0, 500, 1000, 2000, 3000, 5000, 8000, 12000, 16000, 24000, 32000,
                         48000, 64000, 96000)
# A SessionStart within 2% of its cap counts as at-cap (the roadmap's saturation measure).
_AT_CAP_FRACTION = 0.98


def _bucket(value: float, bounds) -> str:
    for b in bounds:
        if value <= b:
            return str(b)
    return "inf"


def _bump(counter: dict, key: str, by: int = 1) -> None:
    counter[key] = int(counter.get(key, 0)) + by


def _today(now: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))


def _empty(date: str) -> dict:
    return {
        "v": ROLLUP_VERSION,
        "date": date,
        "hook": {
            "prompts": 0,
            "trigger": {},
            "backend": {},
            "abstained": 0,
            "skipped": 0,
            "injected_prompts": 0,
            "injected_chars": 0,
            "injected_by_trigger": {},
            "session_chars": {},
            "wall_ms_hist": {},
            "latency_ms_hist": {},
        },
        "session_start": {
            "runs": 0,
            "chars_total": 0,
            "chars_hist": {},
            "at_cap": 0,
            "dropped": {},
            "cut": {},
        },
        "surface": {},
        "client": {},
        "version": {},
    }


def _usage_key(surface: str, verb: str, action: Optional[str] = None) -> str:
    key = f"{surface}:{verb}"
    return f"{key}:{action}" if action else key


def _client() -> str:
    """Which harness surface is running: Claude Code's own entrypoint label when it set one
    (``cli``, ``claude-desktop``, an SDK name), else ``unknown``."""
    return (os.environ.get("CLAUDE_CODE_ENTRYPOINT") or "").strip() or "unknown"


def _stamp(acc: dict, client: Optional[str] = None) -> None:
    try:
        from .telemetry import _producer_version

        _bump(acc.setdefault("version", {}), _producer_version() or "unknown")
    except Exception:
        pass
    _bump(acc.setdefault("client", {}), client or _client())


def _drain_spool(td: str, acc: dict) -> None:
    """Fold the bash surfaces' spooled uses into the open day, then empty the spool (the
    caller holds the lock). A malformed line is skipped, never fatal."""
    path = os.path.join(td, _SPOOL_NAME)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception:
        return
    if not lines:
        return
    surface = acc.setdefault("surface", {})
    for line in lines:
        if len(line) > _MAX_SPOOL_LINE:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if not isinstance(rec, dict) or not rec.get("surface") or not rec.get("verb"):
            continue
        _bump(surface, _usage_key(str(rec["surface"]), str(rec["verb"]), str(rec.get("action") or "") or None))
        _bump(acc.setdefault("client", {}), str(rec.get("client") or "unknown"))
    from .atomic import write_text_atomic

    write_text_atomic(path, "")


def _finalize(acc: dict) -> dict:
    """Replace today's per-session sums with their histogram; ids never leave the day."""
    row = json.loads(json.dumps(acc))
    sessions = row["hook"].pop("session_chars", {}) or {}
    hist: Dict[str, int] = {}
    for chars in sessions.values():
        _bump(hist, _bucket(float(chars), _SESSION_CHAR_BUCKETS))
    row["hook"]["sessions"] = len(sessions)
    row["hook"]["session_chars_hist"] = hist
    row.pop("legacy_sessions", None)  # SRF-4's once-per-session guard: ids, never a KPI
    return row


@contextmanager
def _locked(td: str) -> Iterator[None]:
    """Hold an exclusive ``flock`` on the rollup lock file; a platform without ``fcntl``
    runs unlocked rather than not at all."""
    fh = None
    try:
        try:
            import fcntl

            fh = open(os.path.join(td, _LOCK_NAME), "a")
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        yield
    finally:
        if fh is not None:
            fh.close()


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def _append_finalized(td: str, row: dict) -> None:
    """Append one finished day, then trim to ``MAX_DAYS``. The trim is best-effort: a failed
    rewrite keeps the file's prior bytes plus the new line (one extra row until the next
    roll trims it), and never costs the day its fold."""
    path = os.path.join(td, _ROLLUP_NAME)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = [ln for ln in fh if ln.strip()]
        if len(lines) > MAX_DAYS:
            from .atomic import write_text_atomic

            write_text_atomic(path, "".join(lines[-MAX_DAYS:]))
    except Exception:
        pass


def _update(td: str, now: Optional[float], fold) -> bool:
    """Load today's accumulator (rolling the previous day out first), apply ``fold``, save."""
    try:
        from .atomic import write_json_atomic
        from .provenance import ensure_self_ignoring_dir

        ensure_self_ignoring_dir(td)
        today = _today(now)
        with _locked(td):
            path = os.path.join(td, _TODAY_NAME)
            acc = _read_json(path)
            if not acc or acc.get("v") != ROLLUP_VERSION or not isinstance(acc.get("date"), str):
                acc = _empty(today)
            elif acc["date"] != today:
                _append_finalized(td, _finalize(acc))
                carry = acc.get("legacy_sessions")
                acc = _empty(today)
                if isinstance(carry, list) and carry:
                    acc["legacy_sessions"] = carry  # a session spanning midnight counts once
            _drain_spool(td, acc)
            fold(acc)
            write_json_atomic(path, acc, indent=None, sort_keys=True)
        return True
    except Exception:
        return False


def record_prompt(
    telemetry_dir: str,
    *,
    trigger: str,
    session_id: Optional[str] = None,
    ran_recall: bool = False,
    backend: Optional[str] = None,
    injected_chars: Optional[int] = None,
    latency_ms: Optional[float] = None,
    wall_ms: Optional[float] = None,
    now: Optional[float] = None,
    path: str = "spawn",
) -> bool:
    """Fold one UserPromptSubmit hook run into today's row. Machine turns count by trigger
    and nothing else (they never recall). Fire-and-forget: never raises.

    HOT-6 ``path``: which hook path ran the recall — ``spawn`` (a fresh Python per prompt)
    or ``warm`` (served by the session's own MCP server). It keys the surface count, and a
    warm run's wall lands in its own histogram so the spawn wall stays comparable."""

    def fold(acc: dict) -> None:
        _bump(acc.setdefault("surface", {}), _usage_key("hook", "user_prompt", path or "spawn"))
        _stamp(acc)
        hook = acc["hook"]
        hook["prompts"] += 1
        _bump(hook["trigger"], trigger or "human")
        if trigger == "human":
            if not ran_recall:
                hook["skipped"] += 1
            else:
                _bump(hook["backend"], backend or "none")
                if (backend or "none") == "none":
                    hook["abstained"] += 1
            if latency_ms is not None:
                _bump(hook["latency_ms_hist"], _bucket(float(latency_ms), _MS_BUCKETS))
        if injected_chars:
            hook["injected_prompts"] += 1
            hook["injected_chars"] += int(injected_chars)
            _bump(hook["injected_by_trigger"], trigger or "human", int(injected_chars))
        if session_id:
            _bump(hook["session_chars"], str(session_id), int(injected_chars or 0))
        if wall_ms is not None:
            key = "warm_wall_ms_hist" if path == "warm" else "wall_ms_hist"
            _bump(hook.setdefault(key, {}), _bucket(float(wall_ms), _MS_BUCKETS))

    return _update(telemetry_dir, now, fold)


def dropped_producers(producer_chars: Dict[str, int], ctx: str) -> Dict[str, List[str]]:
    """Which SessionStart producers the char budget dropped whole (``dropped``) or cut
    mid-block (``cut``). Producers merge in order, joined by a blank line; the bounded
    payload marks a cut with ``…(truncated)``. Never raises."""
    out: Dict[str, List[str]] = {"dropped": [], "cut": []}
    try:
        marker = ctx.find("\n…(truncated)")
        if marker < 0:
            return out
        offset = 0
        for label, chars in producer_chars.items():
            start, end = offset, offset + int(chars)
            if start >= marker:
                out["dropped"].append(label)
            elif end > marker:
                out["cut"].append(label)
            offset = end + 2
        return out
    except Exception:
        return {"dropped": [], "cut": []}


def record_session_start(
    telemetry_dir: str,
    *,
    total: int,
    cap: int,
    dropped: Optional[List[str]] = None,
    cut: Optional[List[str]] = None,
    now: Optional[float] = None,
) -> bool:
    """Fold one SessionStart emission into today's row. Never raises."""

    def fold(acc: dict) -> None:
        _bump(acc.setdefault("surface", {}), _usage_key("hook", "session_start", "spawn"))
        _stamp(acc)
        ss = acc["session_start"]
        ss["runs"] += 1
        ss["chars_total"] += int(total)
        _bump(ss["chars_hist"], _bucket(float(total), _SS_CHAR_BUCKETS))
        if cap and total >= _AT_CAP_FRACTION * cap:
            ss["at_cap"] += 1
        for label in dropped or ():
            _bump(ss["dropped"], label)
        for label in cut or ():
            _bump(ss["cut"], label)

    return _update(telemetry_dir, now, fold)


def record_usage(
    telemetry_dir: str,
    *,
    surface: str,
    verb: str,
    action: Optional[str] = None,
    client: Optional[str] = None,
    now: Optional[float] = None,
) -> bool:
    """OBS-2: count one use of a hippo surface (``mcp``, ``cli``, ``skill``, ``hook``).
    Never raises."""

    def fold(acc: dict) -> None:
        _bump(acc.setdefault("surface", {}), _usage_key(surface, verb, action))
        _stamp(acc, client)

    return _update(telemetry_dir, now, fold)


# TND-5: the capture queue's inflow and drain, one counter per event. Inflow: a seed
# ``captured`` (new file), ``folded`` (a re-capture merged into the session's existing
# seed, SubagentStop included), ``restored`` from ``expired/``. Drain: ``expired`` (moved
# to ``expired/`` by age, session count or the size cap), ``discarded`` (skipped by a
# human), ``drafted`` (discarded after it became a memory).
QUEUE_EVENTS = ("captured", "folded", "expired", "restored", "discarded", "drafted")


def record_queue(
    telemetry_dir: str, event: str, n: int = 1, *, now: Optional[float] = None
) -> bool:
    """Count ``n`` capture-queue events of kind ``event`` into today's row (a ``queue``
    section; absent until the first event, so older rows read unchanged). Unknown kinds
    and non-positive counts write nothing. Never raises."""
    if event not in QUEUE_EVENTS or not isinstance(n, int) or n <= 0 or not telemetry_dir:
        return False

    def fold(acc: dict) -> None:
        _bump(acc.setdefault("queue", {}), event, n)

    return _update(telemetry_dir, now, fold)


# SRF-4: how many recent session ids the legacy-name counter remembers, so a session's
# resume/compact SessionStarts do not count the same old names again.
_LEGACY_SESSIONS_KEPT = 50


def record_legacy_names(
    telemetry_dir: str,
    session_id: Optional[str],
    names: List[tuple],
    *,
    now: Optional[float] = None,
) -> int:
    """SRF-4: count each legacy env/config name in use ONCE for this session through the
    OBS-2 usage map (``surface:verb`` — e.g. ``env:HIPPO_DISABLE_DENSE``,
    ``config:format.volatile_paths``), so the deprecation window can see who still uses
    an old spelling before v2.0 removes it. ``names`` is ``[(surface, verb)]``. A session
    already counted (its SessionStart fired again on resume or compaction) counts nothing.
    Returns how many names were counted. Never raises."""
    if not telemetry_dir or not session_id or not names:
        return 0
    counted = [0]

    def fold(acc: dict) -> None:
        seen = acc.get("legacy_sessions")
        seen = seen if isinstance(seen, list) else []
        if session_id in seen:
            return
        usage = acc.setdefault("surface", {})
        for surface, verb in names:
            _bump(usage, _usage_key(str(surface), str(verb)))
            counted[0] += 1
        seen.append(session_id)
        acc["legacy_sessions"] = seen[-_LEGACY_SESSIONS_KEPT:]

    return counted[0] if _update(telemetry_dir, now, fold) else 0


def read_rollups(telemetry_dir: str, *, days: int = 30, now: Optional[float] = None) -> List[dict]:
    """The finalized day rows inside the last ``days`` days plus today's open row,
    oldest first. ``[]`` when nothing has been recorded. Never raises."""
    ref = time.time() if now is None else now
    cutoff = _today(ref - days * 86400.0)
    rows: List[dict] = []
    try:
        with open(os.path.join(telemetry_dir, _ROLLUP_NAME), "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict) and str(row.get("date", "")) > cutoff:
                    rows.append(row)
    except Exception:
        rows = []
    # A day appended twice (a fold that failed after its roll, then retried) keeps its last row.
    by_date: Dict[str, dict] = {}
    for row in rows:
        by_date[str(row.get("date"))] = row
    rows = [by_date[d] for d in sorted(by_date)]
    acc = _read_json(os.path.join(telemetry_dir, _TODAY_NAME))
    if acc and acc.get("v") == ROLLUP_VERSION and str(acc.get("date", "")) > cutoff:
        if not rows or rows[-1].get("date") != acc.get("date"):
            rows.append(_finalize(acc))
    return rows


def _hist_percentile(hist: Dict[str, int], pct: float) -> Optional[str]:
    """Nearest-rank percentile over a bucket histogram, as the bucket's upper bound."""
    total = sum(hist.values())
    if not total:
        return None
    keys = sorted(hist, key=lambda k: float("inf") if k == "inf" else float(k))
    rank = max(1, -(-int(pct * total) // 100))
    seen = 0
    for k in keys:
        seen += hist[k]
        if seen >= rank:
            return k
    return keys[-1]


def _merge(rows: List[dict], section: str, key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for row in rows:
        for k, v in ((row.get(section) or {}).get(key) or {}).items():
            _bump(out, k, int(v))
    return out


def summarize(rows: List[dict]) -> dict:
    """The 30-day KPI view over ``read_rollups`` rows."""
    hook_total = lambda key: sum(int((r.get("hook") or {}).get(key) or 0) for r in rows)  # noqa: E731
    ss_total = lambda key: sum(int((r.get("session_start") or {}).get(key) or 0) for r in rows)  # noqa: E731
    trigger = _merge(rows, "hook", "trigger")
    by_trigger = _merge(rows, "hook", "injected_by_trigger")
    human = trigger.get("human", 0)
    ran = sum(_merge(rows, "hook", "backend").values())
    injected_prompts = hook_total("injected_prompts")
    return {
        "days": len(rows),
        "first": rows[0]["date"] if rows else None,
        "prompts": hook_total("prompts"),
        "trigger": trigger,
        "machine_prompts": hook_total("prompts") - human,
        "machine_injected_chars": sum(v for k, v in by_trigger.items() if k != "human"),
        "abstained": hook_total("abstained"),
        "ran_recall": ran,
        "skipped": hook_total("skipped"),
        "injected_prompts": injected_prompts,
        "injected_chars": hook_total("injected_chars"),
        "chars_per_injected_prompt": (hook_total("injected_chars") / injected_prompts) if injected_prompts else None,
        "sessions": hook_total("sessions"),
        "session_chars_p50": _hist_percentile(_merge(rows, "hook", "session_chars_hist"), 50),
        "session_chars_p95": _hist_percentile(_merge(rows, "hook", "session_chars_hist"), 95),
        "wall_p50": _hist_percentile(_merge(rows, "hook", "wall_ms_hist"), 50),
        "wall_p95": _hist_percentile(_merge(rows, "hook", "wall_ms_hist"), 95),
        "warm_wall_p50": _hist_percentile(_merge(rows, "hook", "warm_wall_ms_hist"), 50),
        "warm_wall_p95": _hist_percentile(_merge(rows, "hook", "warm_wall_ms_hist"), 95),
        "latency_p50": _hist_percentile(_merge(rows, "hook", "latency_ms_hist"), 50),
        "latency_p95": _hist_percentile(_merge(rows, "hook", "latency_ms_hist"), 95),
        "ss_runs": ss_total("runs"),
        "ss_chars_p50": _hist_percentile(_merge(rows, "session_start", "chars_hist"), 50),
        "ss_at_cap": ss_total("at_cap"),
        "ss_dropped": _merge(rows, "session_start", "dropped"),
        "ss_cut": _merge(rows, "session_start", "cut"),
        "surface": _merge_top(rows, "surface"),
        "queue": _merge_top(rows, "queue"),
        "client": _merge_top(rows, "client"),
        "version": _merge_top(rows, "version"),
    }


def _merge_top(rows: List[dict], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for row in rows:
        for k, v in (row.get(key) or {}).items():
            _bump(out, k, int(v))
    return out
