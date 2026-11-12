"""GAT-2's tear scenario for the crash-fault lane (``tests/test_crash_faults.py``).

Kept beside the lane, which sits near its module-size cap; the lane passes in its tear
machinery and lists what ``scenarios`` returns in ``_SCENARIOS``.
"""

from __future__ import annotations

import os
import subprocess


def scenarios(arm, snap, assert_unchanged):
    def install_detected(tmp_path, monkeypatch, capsys):
        """A torn pre-commit hook write exits 1, says nothing was changed, and leaves the
        hook hippo wrote earlier byte-identical (never a half-written hook git would run)."""
        from memory import gate_cli

        repo = str(tmp_path / "repo")
        md = os.path.join(repo, ".claude", "memory")
        os.makedirs(md)
        subprocess.run(["git", "init", "-q", repo], check=True)
        hooks = os.path.join(repo, ".git", "hooks")
        os.makedirs(hooks, exist_ok=True)
        target = os.path.join(hooks, "pre-commit")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(f"#!/bin/sh\n{gate_cli.HOOK_MARKER}. An older copy.\nexit 0\n")
        before = snap(target)
        arm(monkeypatch, "gate_cli", "install")
        assert gate_cli.main(["--install", "--yes", "--memory-dir", md]) == 1
        assert_unchanged(before)
        assert "nothing was changed" in capsys.readouterr().err

    return [(("gate_cli", "install"), "detected", install_detected)]
