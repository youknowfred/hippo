"""TND-7: ``hippo tend`` offers a report-only scheduled sleep once the backlog is real.

hippo installs nothing: ``hippo sleep --print-schedule`` prints cron, launchd and scheduled
task recipes and the human installs one. Until now nothing said so to a corpus whose queue
was growing. This adds ONE line to the tend summary (the CLI's ``hippo tend`` list and the
tend tool's ``list``) when all three hold:

  - the backlog is at least ``BACKLOG_MIN`` pending items. Measured on the three reference
    corpora's queue files (2026-11-12): 36 on the largest, 1 and 0 on the other two. Ten is
    more than one sitting clears (the list shows 15), well under the largest's 36, and far
    above the drained-by-hand 0 and 1. Item count, not age: the queue's items carry no
    common age (a capture has one, a contradiction does not), and the count is what a
    sitting has to get through;
  - no sleep run is recorded for this corpus in the last ``RECENT_DAYS`` days (the sleep
    state's ``last_run_at``; a schedule runs on weekdays, so a week without one means no
    schedule, or a machine that was away);
  - it has not been shown today. The mechanism v1.42.1 gave the not-bootstrapped line: the
    local date the line last showed is recorded, written only when it shows, so the first
    summary of each local day carries it and the rest do not. Here the date lives in this
    corpus's sleep state (``schedule_offer_shown_on``), so the day is per corpus.

The line only ever names the print command. The schedule it prints is report-only: the
write-capable dream tier is a separate opt-in the line never mentions.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Optional

BACKLOG_MIN = 10
RECENT_DAYS = 7
_KEY = "schedule_offer_shown_on"


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _recent_run(state: dict, now: datetime) -> bool:
    raw = state.get("last_run_at")
    if not raw:
        return False
    try:
        last = datetime.fromisoformat(str(raw))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except Exception:
        return False
    return now - last < timedelta(days=RECENT_DAYS)


def offer_line(memory_dir: str, pending: int, *, now: Optional[datetime] = None,
               today: Optional[str] = None) -> Optional[str]:
    """The offer, or None. Showing it records today's date for this corpus. Never raises."""
    try:
        if pending < BACKLOG_MIN:
            return None
        from .sleep import _read_state, _write_state
        from .telemetry import default_telemetry_dir

        td = default_telemetry_dir(memory_dir)
        state = _read_state(td)
        if _recent_run(state, now or datetime.now(timezone.utc)):
            return None
        day = today or _today()
        if state.get(_KEY) == day:
            return None
        state[_KEY] = day
        _write_state(td, state)  # a failed write shows it again next time, never never
        return (f"{pending} items wait here and no scheduled sleep has run for this corpus in "
                f"{RECENT_DAYS} days. `hippo sleep --print-schedule` prints a report-only "
                "schedule (cron, launchd or a scheduled task) for you to install: each run "
                "leaves a morning report of this queue and changes no memory. hippo installs "
                "nothing. (Shown once a day.)")
    except Exception:
        return None
