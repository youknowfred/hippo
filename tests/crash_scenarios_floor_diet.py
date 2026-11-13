"""NAT-3's tear scenario for the crash-fault lane (``tests/test_crash_faults.py``).

Kept beside the lane, which sits at its module-size cap. The lane passes in its own tear
machinery and lists what ``scenarios`` returns; ``CRASH_CONTRACT`` declares the same site.
"""

from __future__ import annotations

import json
import os


def scenarios(arm, snap, assert_unchanged):
    """``[((module, function), class, scenario)]`` for the floor diet's replay cache."""

    def replay_cache_intact(tmp_path, monkeypatch):
        """A torn replay-cache write is silent: the diet still proposes, and the prior cache
        keeps its bytes (the next call ranks the missing prompt again)."""
        from memory import build_index as B
        from memory import floor_diet as FD
        from memory.telemetry import log_episode

        monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
        monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "tel"))
        os.makedirs(str(tmp_path / "tel"))
        md = tmp_path / "repo" / ".claude" / "memory"
        md.mkdir(parents=True)
        floor = "# Agent Memory\n\n## User\n\n## Working Style & Process Feedback\n"
        for n in ("alpha", "bravo"):
            (md / f"{n}.md").write_text(
                f'---\nname: {n}\ndescription: "{n} deploy notes"\ntype: project\n---\n{n}\n', encoding="utf-8")
            floor += f"- [{n}]({n}.md) — {n} {'x' * 200}\n"
        (md / "MEMORY.md").write_text(floor, encoding="utf-8")
        (md / "hippo.json").write_text(json.dumps({"budgets": {"floor_bytes": 100}}), encoding="utf-8")
        B.build_index(str(md), B.default_index_dir(str(md)))
        log_episode(["alpha"], query="alpha deploy notes", telemetry_dir=str(tmp_path / "tel"), session_id="s")
        assert FD.plan(str(md))["proposals"]
        before = snap(FD._cache_path(str(md)))
        log_episode(["bravo"], query="bravo deploy notes", telemetry_dir=str(tmp_path / "tel"), session_id="s")
        arm(monkeypatch, "floor_diet", "_write_replay_cache")
        assert FD.plan(str(md))["proposals"]
        assert_unchanged(before)

    return [(("floor_diet", "_write_replay_cache"), "intact", replay_cache_intact)]
