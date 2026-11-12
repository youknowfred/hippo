"""The outbox's tear scenarios for the crash-fault lane (``tests/test_crash_faults.py``).

Kept beside the lane, which sits near its module-size cap, the way the warm-recall scenarios
are. The lane passes in its tear machinery and lists what ``scenarios`` returns in
``_SCENARIOS``; ``CRASH_CONTRACT`` there declares the same two sites.
"""

from __future__ import annotations

import os
import subprocess


def _corpus(tmp_path, monkeypatch):
    root = str(tmp_path / "repo")
    md = os.path.join(root, ".claude", "memory")
    os.makedirs(md)
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# Memory\n\n## User\n\n## Working Style & Process Feedback\n")
    subprocess.run(["git", "init", "-q", root], check=True)
    monkeypatch.setenv("HIPPO_OUTBOX_DIR", str(tmp_path / "repo" / ".claude" / "memory.outbox"))
    return root, md


def scenarios(arm, snap, assert_unchanged):
    """``[((module, function), class, scenario)]`` for the outbox write sites."""

    def manifest_detected(tmp_path, monkeypatch):
        """A torn manifest write keeps the prior manifest bytes and the save names what did
        not land; the memory itself is already whole."""
        from memory import outbox as OB

        root, md = _corpus(tmp_path, monkeypatch)
        d = OB.ensure(md)
        first = os.path.join(d, "alpha.md")
        with open(first, "w", encoding="utf-8") as fh:
            fh.write('---\nname: alpha\ndescription: "a"\nmetadata:\n  type: project\n---\nA\n')
        assert OB.record_write(md, first, root) is None
        before = snap(OB.manifest_path(md))
        second = os.path.join(d, "beta.md")
        with open(second, "w", encoding="utf-8") as fh:
            fh.write('---\nname: beta\ndescription: "b"\nmetadata:\n  type: project\n---\nB\n')
        arm(monkeypatch, "outbox", "_update_manifest")
        note = OB.record_write(md, second, root)
        assert_unchanged(before)
        assert note and "outbox manifest" in note
        assert sorted(OB.read_manifest(md)) == ["alpha.md"]

    def copy_up_detected(tmp_path, monkeypatch):
        """A torn copy-up raises, leaves the committed file alone and no partial copy behind;
        a floor edit through it then reports the missing floor instead of writing the
        working tree."""
        from memory import outbox as OB
        from memory.new_memory_floor import _append_floor_pointer

        root, md = _corpus(tmp_path, monkeypatch)
        committed = os.path.join(md, "MEMORY.md")
        before = snap(committed)
        arm(monkeypatch, "outbox", "copy_up")
        try:
            OB.copy_up(md, "MEMORY", root)
            raise AssertionError("a torn copy-up must be loud")
        except OSError:
            pass
        assert_unchanged(before)
        assert not os.path.exists(os.path.join(OB.outbox_dir(md), "MEMORY.md"))
        out = _append_floor_pointer(
            md, "## User", "alpha", "Alpha", "hook", floor_dir=OB.floor_target(md, root)
        )
        assert out["status"] == "skipped" and "MEMORY.md missing" in out["reason"]
        assert_unchanged(before)

    return [
        (("outbox", "_update_manifest"), "detected", manifest_detected),
        (("outbox", "copy_up"), "detected", copy_up_detected),
    ]
