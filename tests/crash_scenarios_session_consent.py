"""OUT-5's tear scenario for the crash-fault lane (``tests/test_crash_faults.py``).

Kept beside the lane, which sits near its module-size cap; the lane passes in its tear
machinery and lists what ``scenarios`` returns in ``_SCENARIOS``. The one new site is the
PreToolUse note write; the grant itself goes through the trust registry's existing writer.
"""

from __future__ import annotations

import json
import os
import subprocess


def scenarios(arm, snap, assert_unchanged):
    def note_intact(tmp_path, monkeypatch):
        """A torn note write is silent (the hook says nothing either way), leaves no partial
        note at the real path, and the post hook then grants nothing: the file waits for
        review, which is the state without the opt-in."""
        from memory import policy_consent as PC
        from memory import session_consent as SC
        from memory import trust as T

        monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
        td = str(tmp_path / "tele")
        monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
        repo = str(tmp_path / "repo")
        md = os.path.join(repo, ".claude", "memory")
        os.makedirs(md)
        with open(os.path.join(md, "hippo.json"), "w", encoding="utf-8") as fh:
            json.dump({"consent": {"session_writes": True}}, fh)
        subprocess.run(["git", "init", "-q", repo], check=True)
        root = T.gate_repo_root(md, repo)
        assert T.mark_trusted(root, memory_dir=md, origin="init")
        assert PC.acknowledge(md, repo)["ok"]
        path = os.path.join(md, "fresh.md")
        payload = {"tool_name": "Write", "session_id": "s1",
                   "tool_input": {"file_path": path, "content": "---\nname: fresh\n---\nx\n"}}
        np = SC.note_path(td, "s1", os.path.realpath(path))
        trust_file = os.environ["HIPPO_TRUST_FILE"]
        before = snap(np, trust_file)
        arm(monkeypatch, "session_consent", "_write_note")
        assert SC.note_before_write(payload, memory_dir=md, repo_root=repo, tree=repo) is False
        assert_unchanged(before)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(payload["tool_input"]["content"])
        assert SC.grant_after_write(payload, memory_dir=md, repo_root=repo, tree=repo) is False
        assert_unchanged({trust_file: before[trust_file]})

    return [(("session_consent", "_write_note"), "intact", note_intact)]
