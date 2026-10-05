"""HOT-6: ``hippo setup --warm`` — the opt-in writes the warm-recall hook into Claude Code's
user settings only after a preview and an explicit ``--yes``.

Every test points ``HIPPO_CLAUDE_SETTINGS`` at a tmp file (conftest already points it at an
absent one), so the real ``~/.claude/settings.json`` is never read or written.
"""

from __future__ import annotations

import glob
import json
import os

import pytest

from memory import cli
from memory import setup_cli as S
from memory import telemetry_rollup as TR
from memory.doctor_checks_env import DoctorContext
from memory.doctor_checks_kpi import check_warm_recall

_EXISTING = {
    "theme": "dark",
    "permissions": {"allow": ["Bash(ls:*)"], "deny": []},
    "hooks": {
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo mine", "timeout": 3}]}],
        "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "echo stop"}]}],
    },
    "env": {"FOO": "bar"},
}


@pytest.fixture
def settings(tmp_path, monkeypatch):
    path = str(tmp_path / "claude" / "settings.json")
    os.makedirs(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_EXISTING, fh, indent=2)
        fh.write("\n")
    monkeypatch.setenv("HIPPO_CLAUDE_SETTINGS", path)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    return path


def _bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _doc(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _backups(path):
    return sorted(glob.glob(path + ".hippo-backup-*"))


def test_the_entry_is_the_documented_mcp_tool_hook_shape():
    e = S.warm_hook_entry()
    assert e == {
        "type": "mcp_tool",
        "server": "plugin:hippo:hippo",
        "tool": "recall_hook",
        "input": {
            "prompt": "${prompt}",
            "session_id": "${session_id}",
            "prompt_id": "${prompt_id}",
            "cwd": "${cwd}",
        },
        "timeout": 5,
    }


def test_preview_prints_the_change_and_writes_nothing(settings, capsys):
    before = _bytes(settings)
    assert S.main(["--warm"]) == 0
    out = capsys.readouterr().out
    assert settings in out and '"recall_hook"' in out and "Nothing was written" in out
    assert "hippo setup --warm --off --yes" in out  # how to undo, before doing it
    assert _bytes(settings) == before
    assert _backups(settings) == []


def test_yes_writes_once_keeps_every_other_key_and_backs_up(settings, capsys, tmp_path):
    before = _bytes(settings)
    assert S.main(["--warm", "--yes"]) == 0
    assert os.path.isdir(tmp_path / "data" / "warm"), "a session's first handshake finds the dir"
    doc = _doc(settings)
    assert {k: v for k, v in doc.items() if k != "hooks"} == {k: v for k, v in _EXISTING.items() if k != "hooks"}
    assert doc["hooks"]["Stop"] == _EXISTING["hooks"]["Stop"]
    groups = doc["hooks"]["UserPromptSubmit"]
    assert groups[0] == _EXISTING["hooks"]["UserPromptSubmit"][0]  # the user's own hook, first
    assert groups[1] == {"hooks": [S.warm_hook_entry()]}
    backups = _backups(settings)
    assert len(backups) == 1 and _bytes(backups[0]) == before
    # Idempotent: a second --yes changes nothing and makes no second backup.
    after = _bytes(settings)
    capsys.readouterr()
    assert S.main(["--warm", "--yes"]) == 0
    assert "already on" in capsys.readouterr().out
    assert _bytes(settings) == after and len(_backups(settings)) == 1


def test_the_backup_and_the_file_keep_a_private_mode(settings):
    """Settings can hold secrets (env): a 0600 file must not gain a world-readable copy."""
    os.chmod(settings, 0o600)
    assert S.main(["--warm", "--yes"]) == 0
    (backup,) = _backups(settings)
    assert os.stat(backup).st_mode & 0o777 == 0o600
    assert os.stat(settings).st_mode & 0o777 == 0o600


def test_an_older_entry_is_updated_in_place_and_duplicates_collapse(settings):
    old = {"type": "mcp_tool", "server": "plugin:hippo:hippo", "tool": "recall_hook",
           "input": {"prompt": "${prompt}"}, "timeout": 30}
    doc = json.loads(json.dumps(_EXISTING))
    doc["hooks"]["UserPromptSubmit"][0]["hooks"].append(old)
    doc["hooks"]["UserPromptSubmit"].append({"hooks": [dict(old)]})
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    assert S.main(["--warm", "--yes"]) == 0
    hooks = [h for g in _doc(settings)["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
    assert [h for h in hooks if h.get("tool") == "recall_hook"] == [S.warm_hook_entry()]
    assert {"type": "command", "command": "echo mine", "timeout": 3} in hooks


def test_off_previews_then_removes_only_what_it_added(settings, tmp_path, capsys):
    original = _doc(settings)
    assert S.main(["--warm", "--yes"]) == 0
    warm = tmp_path / "data" / "warm"
    os.makedirs(warm, exist_ok=True)
    for name in ("s1.server.json", "s1.p1.claim", "s1.lost", "keep.txt"):
        (warm / name).write_text("x")
    on = _bytes(settings)
    capsys.readouterr()
    assert S.main(["--warm", "--off"]) == 0
    assert "Nothing was written" in capsys.readouterr().out
    assert _bytes(settings) == on
    assert S.main(["--warm", "--off", "--yes"]) == 0
    assert _doc(settings) == original
    assert sorted(os.listdir(warm)) == ["keep.txt"], "live sessions stop handing prompts over"
    capsys.readouterr()
    assert S.main(["--warm", "--off", "--yes"]) == 0
    assert "already off" in capsys.readouterr().out


def test_leftover_handshake_files_are_flagged_and_cleared(settings, tmp_path):
    """A hook removed by hand leaves the warm dir behind, and every prompt's hook keeps
    writing a claim there: doctor says so, and `--off --yes` clears it even when off."""
    warm = tmp_path / "data" / "warm"
    os.makedirs(warm)
    (warm / "s1.p1.claim").write_text("spawn")
    md = str(tmp_path / "proj" / ".claude" / "memory")
    os.makedirs(md)
    r = check_warm_recall(DoctorContext(memory_dir=md, repo_root=str(tmp_path / "proj")))
    assert r["status"] == "warn" and "hippo setup --warm --off --yes" in r["message"]
    assert S.main(["--warm", "--off"]) == 0
    assert os.path.exists(warm / "s1.p1.claim"), "a preview clears nothing"
    assert S.main(["--warm", "--off", "--yes"]) == 0
    assert not os.path.exists(warm), "no warm dir: every hook is back to one stat"
    r = check_warm_recall(DoctorContext(memory_dir=md, repo_root=str(tmp_path / "proj")))
    assert r["status"] == "ok"


def test_off_drops_only_the_containers_it_emptied(tmp_path, monkeypatch):
    path = str(tmp_path / "settings.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"model": "x"}, fh)
    monkeypatch.setenv("HIPPO_CLAUDE_SETTINGS", path)
    assert S.main(["--warm", "--yes"]) == 0
    assert S.main(["--warm", "--off", "--yes"]) == 0
    assert _doc(path) == {"model": "x"}
    assert S.without_warm_hook({"hooks": {"UserPromptSubmit": []}}) == {"hooks": {"UserPromptSubmit": []}}


def test_a_missing_settings_file_is_created(tmp_path, monkeypatch):
    path = str(tmp_path / "new-config" / "settings.json")
    monkeypatch.setenv("HIPPO_CLAUDE_SETTINGS", path)
    assert S.main(["--warm", "--yes"]) == 0
    assert _doc(path) == {"hooks": {"UserPromptSubmit": [{"hooks": [S.warm_hook_entry()]}]}}


def test_a_file_that_does_not_parse_is_never_touched(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "settings.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"theme": "dark",, }')
    monkeypatch.setenv("HIPPO_CLAUDE_SETTINGS", path)
    before = _bytes(path)
    assert S.main(["--warm", "--yes"]) == 1
    assert "not touching" in capsys.readouterr().err
    assert _bytes(path) == before and _backups(path) == []


def test_status_is_read_only_and_reports_both_states(settings, capsys, monkeypatch, tmp_path):
    project = tmp_path / "proj"
    os.makedirs(project / ".claude" / "memory")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    before = _bytes(settings)
    assert S.main(["--warm", "--status"]) == 0
    assert "warm recall: off" in capsys.readouterr().out
    assert _bytes(settings) == before
    assert S.main(["--warm", "--yes"]) == 0
    td = str(project / ".claude" / ".memory-telemetry")
    os.makedirs(td)
    TR.record_prompt(td, trigger="human", path="warm", wall_ms=40.0)
    TR.record_prompt(td, trigger="human", wall_ms=600.0)
    TR.record_usage(td, surface="hook", verb="user_prompt", action="failed")
    warm = tmp_path / "data" / "warm"
    os.makedirs(warm, exist_ok=True)
    (warm / "s1.server.json").write_text(json.dumps(
        {"pid": os.getpid(), "version": "1.0.0", "accept": True, "state": "idle",
         "last_path": "warm", "updated": 1.0}) + "\n")
    on = _bytes(settings)
    capsys.readouterr()
    assert S.main(["--warm", "--status"]) == 0
    out = capsys.readouterr().out
    assert "warm recall: on" in out and settings in out
    assert "1 live of 1" in out and "last took the warm path" in out
    assert "warm 1 · spawn 1 · failed 1" in out and "warm wall p50 ≤50ms" in out
    assert _bytes(settings) == on
    with pytest.raises(SystemExit):
        S.main(["--warm", "--status", "--yes"])


def test_bare_setup_lists_the_settings(capsys):
    assert S.main([]) == 2
    assert "--warm" in capsys.readouterr().out


def test_setup_is_a_hippo_verb(settings):
    """Through the one door, in a fresh process (the door runs the module as __main__)."""
    import subprocess
    import sys

    plugin = os.path.dirname(os.path.dirname(cli.__file__))
    env = {**os.environ, "PYTHONPATH": plugin}
    before = _bytes(settings)
    proc = subprocess.run([sys.executable, "-m", "memory.cli", "setup", "--warm"],
                          capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "Nothing was written" in proc.stdout
    assert _bytes(settings) == before


def test_doctor_line_reports_the_hook_and_the_paths(settings, tmp_path):
    md = str(tmp_path / "proj" / ".claude" / "memory")
    os.makedirs(md)
    ctx = DoctorContext(memory_dir=md, repo_root=str(tmp_path / "proj"))
    r = check_warm_recall(ctx)
    assert r["status"] == "ok" and r["message"].startswith("warm recall: off")
    assert S.main(["--warm", "--yes"]) == 0
    td = str(tmp_path / "proj" / ".claude" / ".memory-telemetry")
    os.makedirs(td)
    TR.record_prompt(td, trigger="human", path="warm", wall_ms=30.0)
    r = check_warm_recall(ctx)
    assert r["status"] == "ok" and "warm recall: on" in r["message"]
    assert "warm 1 · spawn 0 · failed 0" in r["message"]
    doc = _doc(settings)
    doc["hooks"]["UserPromptSubmit"][-1]["hooks"][0]["timeout"] = 99
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    r = check_warm_recall(ctx)
    assert r["status"] == "warn" and "hippo setup --warm --yes" in r["message"]


def test_project_settings_count_as_configured(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    os.makedirs(repo / ".claude")
    (repo / ".claude" / "settings.json").write_text(json.dumps(
        {"hooks": {"UserPromptSubmit": [{"hooks": [S.warm_hook_entry()]}]}}))
    monkeypatch.setenv("HIPPO_CLAUDE_SETTINGS", str(tmp_path / "absent.json"))
    st = S.warm_state(str(repo))
    assert st["configured"] and st["current"] and st["file"].endswith(os.path.join(".claude", "settings.json"))


def test_record_prompt_keys_the_path_and_keeps_the_walls_apart(tmp_path):
    td = str(tmp_path)
    TR.record_prompt(td, trigger="human", wall_ms=700.0)
    TR.record_prompt(td, trigger="human", wall_ms=45.0, path="warm")
    row = TR.read_rollups(td)[-1]
    assert row["surface"] == {"hook:user_prompt:spawn": 1, "hook:user_prompt:warm": 1}
    assert row["hook"]["wall_ms_hist"] == {"800": 1}
    assert row["hook"]["warm_wall_ms_hist"] == {"50": 1}
    k = TR.summarize([row])
    assert (k["wall_p95"], k["warm_wall_p95"]) == ("800", "50")
