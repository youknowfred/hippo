"""HCR-1's tear scenarios for the crash-fault lane (``tests/test_crash_faults.py``).

Kept beside the lane, which sits at its module-size cap. The lane passes in its own tear
machinery and lists what ``scenarios`` returns; ``CRASH_CONTRACT`` declares the same sites.
"""

from __future__ import annotations

import json
import os


def scenarios(arm, snap, assert_unchanged):
    """``[((module, function), class, scenario)]`` for the machine model preset's two writers."""

    def _preset(tmp_path, monkeypatch):
        from memory import retrieval_profile as RP

        data = tmp_path / "plugin-data"
        data.mkdir()
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data))
        monkeypatch.delenv(RP.ENV_VAR, raising=False)
        return os.path.join(str(data), "model.json")

    def write_detected(tmp_path, monkeypatch):
        """A torn preset write is NAMED by setup_cli (`refused`, rc 1) and the prior preset
        keeps its bytes — the model choice and old profile both survive."""
        from memory import retrieval_profile as RP
        from memory import setup_cli

        path = _preset(tmp_path, monkeypatch)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"embed_model": "some/model", "retrieval_profile": "lean"}, fh)
        before = snap(path)
        arm(monkeypatch, "retrieval_profile", "write_retrieval_profile")
        assert setup_cli.main(["--profile", "high", "--yes"]) == 1  # named, never a traceback
        assert_unchanged(before)
        assert RP.profile_source() == ("lean", "preset")

    def remove_detected(tmp_path, monkeypatch):
        """A torn key removal is NAMED (`refused`, rc 1); the persisted profile stays."""
        from memory import retrieval_profile as RP
        from memory import setup_cli

        _preset(tmp_path, monkeypatch)
        RP.write_retrieval_profile("floor")
        arm(monkeypatch, "retrieval_profile", "remove_retrieval_profile")
        assert setup_cli.main(["--profile", "--off", "--yes"]) == 1
        assert RP.profile_source() == ("floor", "preset")

    return [
        (("retrieval_profile", "write_retrieval_profile"), "detected", write_detected),
        (("retrieval_profile", "remove_retrieval_profile"), "detected", remove_detected),
    ]
