"""FMT-2's tear scenario for the crash-fault lane (``tests/test_crash_faults.py``).

Kept beside the lane, which sits near its module-size cap (the HOT-6 precedent,
``crash_scenarios_warm``). The one write site is the index's citation-binding sidecar.
"""

from __future__ import annotations

import os
import subprocess


def scenarios(arm, snap, assert_unchanged):
    """``[((module, function), class, scenario)]`` for the binding sidecar write site."""

    def binding_intact(tmp_path, monkeypatch):
        """A torn sidecar write costs nothing: the index build still returns its manifest,
        the prior sidecar keeps its bytes, and readers keep serving it row by row."""
        from memory import build_index as BI
        from memory import citation_binding as IB

        monkeypatch.setenv("HIPPO_DISABLE", "dense")
        root = str(tmp_path / "repo")
        md = os.path.join(root, ".claude", "memory")
        os.makedirs(md)
        with open(os.path.join(root, "app.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        mem = os.path.join(md, "one.md")
        with open(mem, "w", encoding="utf-8") as fh:
            fh.write('---\nname: one\ndescription: "d"\nmetadata:\n  type: project\n---\nSee `app.py`.\n')
        subprocess.run(["git", "init", "-q", root], check=True)
        subprocess.run(["git", "-C", root, "add", "-A"], check=True)
        idx = str(tmp_path / "idx")
        BI.build_index(md, idx)
        path = IB.binding_path(idx)
        before = snap(path)
        assert before[path] is not None
        with open(mem, "a", encoding="utf-8") as fh:
            fh.write("More.\n")
        arm(monkeypatch, "citation_binding", "_write_doc")
        manifest = BI.build_index(md, idx)
        assert manifest["count"] == 1
        assert_unchanged(before)
        assert IB.read_binding(idx)["memories"]["one"]["cited_paths"] == ["app.py"]

    return [(("citation_binding", "_write_doc"), "intact", binding_intact)]
