"""GAT-4's tear scenarios for the crash-fault lane (``tests/test_crash_faults.py``).

Kept beside the lane, which sits near its module-size cap; the lane passes in its tear
machinery and lists what ``scenarios`` returns in ``_SCENARIOS``.
"""

from __future__ import annotations

import os


def _edited_corpus(tmp_path):
    from memory import conventions as C

    md = str(tmp_path / "repo" / ".claude" / "memory")
    os.makedirs(md)
    text = C.asset_bytes().decode("utf-8") + "Our own rule: keep it short.\n"
    copy = os.path.join(md, "CONVENTIONS.md")
    with open(copy, "w", encoding="utf-8") as fh:
        fh.write(text)
    local = os.path.join(md, "CONVENTIONS.local.md")
    with open(local, "w", encoding="utf-8") as fh:
        fh.write("# Local rules\n")
    return md, copy, local


def scenarios(arm, snap, assert_unchanged):
    def append_local_detected(tmp_path, monkeypatch):
        """A torn append to the local file keeps its prior bytes and the copy untouched,
        and the refresh reports it did not finish (the copy is never replaced first)."""
        from memory import conventions as C

        md, copy, local = _edited_corpus(tmp_path)
        before = snap(copy, local)
        arm(monkeypatch, "conventions", "_append_local")
        r = C.apply(md)
        assert r["ok"] is False and r["did"] == []
        assert_unchanged(before)

    def write_copy_detected(tmp_path, monkeypatch):
        """A torn replace keeps the copy's prior bytes and says so; the moved lines are
        already in the local file, so a re-run does not add them twice."""
        from memory import conventions as C

        md, copy, _local = _edited_corpus(tmp_path)
        before = snap(copy)
        arm(monkeypatch, "conventions", "_write_copy")
        r = C.apply(md)
        assert r["ok"] is False and "moved 1 line(s)" in r["did"][0]
        assert_unchanged(before)
        assert C.plan(md)["write_local"] is False

    return [
        (("conventions", "_append_local"), "detected", append_local_detected),
        (("conventions", "_write_copy"), "detected", write_copy_detected),
    ]
