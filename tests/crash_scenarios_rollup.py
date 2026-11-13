"""The daily rollup's tear scenarios for the crash-fault lane (``tests/test_crash_faults.py``).

Kept beside the lane, like the outbox's and the ship verb's, so the lane's registry file keeps
room under its size cap. ``CRASH_CONTRACT`` there declares the same sites: the open day's
accumulator write and the 365-day trim of the finished rows.
"""

from __future__ import annotations

import json
import os


def scenarios(arm, snap, assert_unchanged):
    """``[((module, function), class, scenario)]`` for two of the rollup's write sites."""

    def update_detected(tmp_path, monkeypatch):
        """OBS-1: a torn accumulator write is reported (the fold returns False) and the open
        day's row keeps its prior bytes."""
        from memory import telemetry_rollup as TR

        td = str(tmp_path / "tele")
        os.makedirs(td)
        assert TR.record_prompt(td, trigger="human")
        path = os.path.join(td, TR._TODAY_NAME)
        before = snap(path)
        arm(monkeypatch, "telemetry_rollup", "_update")
        assert TR.record_prompt(td, trigger="human") is False
        assert_unchanged(before)

    def trim_intact(tmp_path, monkeypatch):
        """OBS-1: a torn 365-day trim is silent and costs nothing: the finished day stays
        appended and the new day still folds."""
        from memory import telemetry_rollup as TR

        td = str(tmp_path / "tele")
        os.makedirs(td)
        monkeypatch.setattr(TR, "MAX_DAYS", 1)
        t0 = 1_790_000_000.0
        assert TR.record_prompt(td, trigger="human", now=t0)
        assert TR.record_prompt(td, trigger="human", now=t0 + 86400)  # roll 1: no trim needed
        arm(monkeypatch, "telemetry_rollup", "_append_finalized")
        assert TR.record_prompt(td, trigger="human", now=t0 + 2 * 86400)  # roll 2: the trim tears
        with open(os.path.join(td, TR._ROLLUP_NAME), encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        assert [r["date"] for r in rows] == [TR._today(t0), TR._today(t0 + 86400)]
        assert TR.read_rollups(td, now=t0 + 2 * 86400)[-1]["hook"]["prompts"] == 1

    return [
        (("telemetry_rollup", "_update"), "detected", update_detected),
        (("telemetry_rollup", "_append_finalized"), "intact", trim_intact),
    ]
