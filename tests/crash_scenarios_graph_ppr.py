"""HCR-5's tear scenario for the crash-fault lane (``tests/test_crash_faults.py``).

Kept beside the lane, which sits at its module-size cap. The lane passes in its own tear
machinery and lists what ``scenarios`` returns; ``CRASH_CONTRACT`` declares the same site.
"""

from __future__ import annotations


def scenarios(arm, snap, assert_unchanged):
    """``[((module, function), class, scenario)]`` for the graph-walk A/B evidence writer."""

    def write_report_detected(tmp_path, monkeypatch):
        """A torn evidence write is NAMED in the result and leaves no torn report — an
        absent report stays absent."""
        from memory.graph_ppr_eval import write_report

        path = str(tmp_path / "telemetry" / "graph_ppr_ab.json")
        before = snap(path)
        arm(monkeypatch, "graph_ppr_eval", "write_report")
        res = write_report({"ok": True, "schema": 1, "deltas": {}}, path)
        assert_unchanged(before)
        assert res.get("ok") is False and "report write failed" in res.get("error", "")

    return [(("graph_ppr_eval", "write_report"), "detected", write_report_detected)]
