"""HOT-6's tear scenarios for the crash-fault lane (``tests/test_crash_faults.py``).

Kept beside the lane rather than in it, which sits at its module-size cap. The lane passes in
its own tear machinery (``arm``/``snap``/``assert_unchanged``) and lists what ``scenarios``
returns in ``_SCENARIOS``; ``CRASH_CONTRACT`` there declares the same sites (HCR-7 added the
embedding daemon's record, which the warm servers' doctor line reads).
"""

from __future__ import annotations

import os
import threading


def scenarios(arm, snap, assert_unchanged):
    """``[((module, function), class, scenario)]`` for the warm-recall write sites."""

    def heartbeat_intact(tmp_path, monkeypatch):
        """A torn heartbeat write is SILENT: the served call still answers, and no partial
        heartbeat lands (the command hook reads a missing one as "spawn")."""
        from memory import recall_warm

        project = str(tmp_path / "proj")
        os.makedirs(os.path.join(project, ".claude", "memory"))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", project)
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
        monkeypatch.setattr(recall_warm, "CLAIM_WAIT_S", 0.05)
        recall_warm._SESSIONS.clear()
        arm(monkeypatch, "recall_warm", "_publish")
        try:
            assert recall_warm.serve({"session_id": "s1", "prompt_id": "p1", "prompt": "hi"}) == "{}"
        finally:
            recall_warm._SESSIONS.clear()
        d = os.path.join(str(tmp_path / "data"), "warm")
        assert not [f for f in os.listdir(d) if f.endswith(".server.json")], "absent, never partial"

    def setup_detected(func):
        def scenario(tmp_path, monkeypatch, capsys):
            """A torn settings write (or the backup before it) exits 1, says nothing was
            changed, and leaves the user's settings byte-identical."""
            from memory import setup_cli

            path = str(tmp_path / "settings.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{\n  "theme": "dark"\n}\n')
            monkeypatch.setenv("HIPPO_CLAUDE_SETTINGS", path)
            before = snap(path)
            arm(monkeypatch, "setup_cli", func)
            assert setup_cli.main(["--warm", "--yes"]) == 1
            assert_unchanged(before)
            assert "Nothing was changed" in capsys.readouterr().err

        return scenario

    def daemon_record_intact(tmp_path, monkeypatch):
        """HCR-7: a torn write of the embedding daemon's record is SILENT. The daemon still
        takes the socket and answers; no partial record lands, and doctor finds the daemon
        by asking its socket."""
        from memory import embed_daemon, embed_remote

        space = "Qwen/Qwen3-Embedding-4B"
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
        embed_remote._reset_for_tests()
        arm(monkeypatch, "embed_daemon", "_record")
        daemon = embed_daemon.Daemon(space, load_embedder=lambda: None, live=lambda: 1, tick_s=0.02)
        assert daemon.start()
        thread = threading.Thread(target=daemon.serve_forever, daemon=True)
        thread.start()
        try:
            assert not os.path.exists(embed_remote.paths(space).meta), "absent, never partial"
            assert embed_remote.hello(space, timeout=2.0)["pid"] == os.getpid()
            assert f"pid {os.getpid()}" in embed_remote.describe(space)
        finally:
            daemon.stop()
            thread.join(10)
            daemon.close()
            embed_remote._reset_for_tests()

    return [
        (("recall_warm", "_publish"), "intact", heartbeat_intact),
        (("embed_daemon", "_record"), "intact", daemon_record_intact),
        (("setup_cli", "_apply"), "detected", setup_detected("_apply")),
        (("setup_cli", "_backup"), "detected", setup_detected("_backup")),
    ]
