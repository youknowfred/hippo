"""OWN-1's tear scenario for the crash-fault lane (``tests/test_crash_faults.py``).

Kept beside the lane, which sits near its module-size cap (the HOT-6 precedent,
``crash_scenarios_warm``). The one write site is the user tier's ``.gitignore`` block.
"""

from __future__ import annotations

import os


def scenarios(arm, snap, assert_unchanged):
    """``[((module, function), class, scenario)]`` for the user-tier git-home write site."""

    def ignore_block_detected(tmp_path, monkeypatch):
        """A torn ignore-list write keeps the prior ``.gitignore`` bytes and names itself,
        and a machine-local save that needed it is refused before its file exists."""
        from memory import user_tier_git as U
        from memory.new_memory import write_memory

        tier = str(tmp_path / "user-tier")
        os.makedirs(tier)
        monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", tier)
        gi = os.path.join(tier, ".gitignore")
        with open(gi, "w", encoding="utf-8") as fh:
            fh.write("mine.txt\n")
        before = snap(gi)
        arm(monkeypatch, "user_tier_git", "sync_ignore_block")
        res = U.sync_ignore_block(tier, also=["x.md"])
        assert res["error"] and "could not update" in res["error"]
        assert_unchanged(before)
        made = write_memory("kept-here", "a machine-local fact", "user", "Body.",
                            tier="user", machine_local=True, no_links=True)
        assert made["created"] is False and "not written" in made["error"]
        assert not os.path.exists(os.path.join(tier, "kept-here.md"))
        assert_unchanged(before)

    return [(("user_tier_git", "sync_ignore_block"), "detected", ignore_block_detected)]
