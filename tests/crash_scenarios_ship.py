"""The ship verb's tear scenarios for the crash-fault lane (``tests/test_crash_faults.py``).

Kept beside the lane, like the outbox's. ``CRASH_CONTRACT`` there declares the same sites:
the floor copy's recorded base text (create-only while absent) and the ship's one manifest
compare-and-swap (the shipped stamp, ``--drop``'s row removal, ``--mine``'s new base).
"""

from __future__ import annotations

import json
import os
import subprocess


def _repo(tmp_path, monkeypatch):
    root = str(tmp_path / "repo")
    md = os.path.join(root, ".claude", "memory")
    os.makedirs(md)
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# Memory\n\n## User\n\n## Working Style & Process Feedback\n")
    subprocess.run(["git", "init", "-q", root], check=True)
    monkeypatch.setenv("HIPPO_OUTBOX_DIR", os.path.join(root, ".claude", "memory.outbox"))
    return root, md


def scenarios(arm, snap, assert_unchanged):
    """``[((module, function), class, scenario)]`` for the ship write sites."""

    def floor_base_detected(tmp_path, monkeypatch):
        """A torn base-text write raises before the floor copy exists: no copy without its
        base, and the committed floor is untouched."""
        from memory import outbox as OB

        root, md = _repo(tmp_path, monkeypatch)
        committed = os.path.join(md, "MEMORY.md")
        before = snap(committed)
        arm(monkeypatch, "outbox", "_record_floor_base")
        try:
            OB.copy_up(md, "MEMORY", root)
            raise AssertionError("a torn floor base must be loud")
        except OSError:
            pass
        assert_unchanged(before)
        d = OB.outbox_dir(md)
        assert not os.path.exists(os.path.join(d, "MEMORY.md"))
        assert not os.path.exists(os.path.join(d, OB.FLOOR_BASE_NAME))

    def manifest_detected(tmp_path, monkeypatch):
        """A torn shipped stamp keeps the prior manifest bytes and returns False (the verb
        names it); the next run reads the file as in flight off the branch itself."""
        from memory import outbox as OB
        from memory import ship as S

        root, md = _repo(tmp_path, monkeypatch)
        d = OB.ensure(md)
        p = os.path.join(d, "alpha.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('---\nname: alpha\ndescription: "a"\nmetadata:\n  type: project\n---\nA\n')
        assert OB.record_write(md, p, root) is None
        before = snap(OB.manifest_path(md))
        arm(monkeypatch, "ship", "_edit_manifest")
        ok = S.stamp_shipped(md, [{"name": "alpha.md", "sha256": "x"}], "hippo/ship-x", "0" * 40, None)
        assert ok is False
        assert_unchanged(before)
        assert "shipped" not in json.loads(open(OB.manifest_path(md)).read())["files"]["alpha.md"]

    return [
        (("outbox", "_record_floor_base"), "detected", floor_base_detected),
        (("ship", "_edit_manifest"), "detected", manifest_detected),
    ]
