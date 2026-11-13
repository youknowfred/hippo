"""LDG-1's tear scenario for the crash-fault lane (``tests/test_crash_faults.py``).

Kept beside the lane, which sits near its module-size cap; the lane passes in its tear
machinery and lists what ``scenarios`` returns in ``_SCENARIOS``.
"""

from __future__ import annotations

import json
import os


def scenarios(arm, snap, assert_unchanged):
    def cache_detected(tmp_path, monkeypatch):
        """A torn ledger-cache write keeps the prior cache whole and returns False, so the
        SessionStart caller never assumes the join is fresh."""
        from memory import ledger

        top = str(tmp_path / "plain")
        md = os.path.join(top, ".claude", "memory")
        os.makedirs(md)
        snap_path = os.path.join(top, "T.yaml")
        with open(snap_path, "w", encoding="utf-8") as fh:
            fh.write("- id: ABC-1\n  status: Open\n")
        with open(os.path.join(md, "hippo.json"), "w", encoding="utf-8") as fh:
            json.dump({"ledgers": [{"name": "t", "path": "T.yaml", "id_re": r"ABC-\d+"}]}, fh)
        idx = str(tmp_path / "idx")
        assert ledger.refresh_ledger_cache(md, idx) is True  # a healthy prior cache on disk
        before = snap(ledger.cache_path(idx))
        with open(snap_path, "w", encoding="utf-8") as fh:
            fh.write("- id: ABC-1\n  status: Closed\n- id: ABC-2\n  status: Open\n")
        arm(monkeypatch, "ledger", "write_cache")
        assert ledger.refresh_ledger_cache(md, idx) is False
        assert_unchanged(before)

    return [(("ledger", "write_cache"), "detected", cache_detected)]
