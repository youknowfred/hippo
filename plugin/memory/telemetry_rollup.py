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
    was dropped or cut by the budget.

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
    }


def _finalize(acc: dict) -> dict:
    """Replace today's per-session sums with their histogram; ids never leave the day."""
    row = json.loads(json.dumps(acc))
    sessions = row["hook"].pop("session_chars", {}) or {}
    hist: Dict[str, int] = {}
    for chars in sessions.values():
        _bump(hist, _bucket(float(chars), _SESSION_CHAR_BUCKETS))
    row["hook"]["sessions"] = len(sessions)
    row["hook"]["session_chars_hist"] = hist
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
                acc = _empty(today)
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
) -> bool:
    """Fold one UserPromptSubmit hook run into today's row. Machine turns count by trigger
    and nothing else (they never recall). Fire-and-forget: never raises."""

    def fold(acc: dict) -> None:
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
            _bump(hook["wall_ms_hist"], _bucket(float(wall_ms), _MS_BUCKETS))

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
        "latency_p50": _hist_percentile(_merge(rows, "hook", "latency_ms_hist"), 50),
        "latency_p95": _hist_percentile(_merge(rows, "hook", "latency_ms_hist"), 95),
        "ss_runs": ss_total("runs"),
        "ss_chars_p50": _hist_percentile(_merge(rows, "session_start", "chars_hist"), 50),
        "ss_at_cap": ss_total("at_cap"),
        "ss_dropped": _merge(rows, "session_start", "dropped"),
        "ss_cut": _merge(rows, "session_start", "cut"),
    }
