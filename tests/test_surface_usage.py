"""OBS-2: every hippo surface use is counted — MCP tool calls, `hippo <verb>`, skill
preflights, hook spawns and failed hook spawns — durably, in the daily rollup."""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess

import pytest

from memory import mcp_server as M
from memory import telemetry_rollup as TR
from memory.doctor_checks_env import DoctorContext
from memory.doctor_checks_kpi import check_surface_usage

_PLUGIN = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugin"))
_RESOLVE = os.path.join(_PLUGIN, "hooks", "_resolve_py.sh")
_BASH = shutil.which("bash")


def _surface(td):
    rows = TR.read_rollups(td)
    return rows[-1].get("surface", {}) if rows else {}


@pytest.mark.skipif(_BASH is None, reason="no bash")
def test_bash_note_spools_one_line_and_never_creates_the_dir(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".claude" / "memory").mkdir(parents=True)
    note = f'. "{_RESOLVE}"; hippo_note_usage skill consolidate'
    env = {"PATH": os.environ.get("PATH", ""), "CLAUDE_CODE_ENTRYPOINT": "cli"}
    subprocess.run([_BASH, "-c", note], cwd=proj, env=env, check=True)
    assert not (proj / ".claude" / ".memory-telemetry").exists()  # bash never mints it

    td = proj / ".claude" / ".memory-telemetry"
    td.mkdir()
    subprocess.run([_BASH, "-c", note], cwd=proj, env=env, check=True)
    lines = (td / "usage_spool.jsonl").read_text().splitlines()
    assert [json.loads(line) for line in lines] == [
        {"surface": "skill", "verb": "consolidate", "action": "", "client": "cli"}
    ]


def test_the_spool_drains_into_the_day_on_the_next_fold(tmp_path):
    td = str(tmp_path)
    with open(os.path.join(td, TR._SPOOL_NAME), "w", encoding="utf-8") as fh:
        fh.write('{"surface":"cli","verb":"recall","action":"","client":"cli"}\n')
        fh.write('{"surface":"hook","verb":"user_prompt","action":"failed","client":"cli"}\n')
        fh.write("not json\n")
        fh.write('{"surface":"skill"}\n')  # no verb: skipped
    TR.record_usage(td, surface="mcp", verb="recall", client="claude-desktop")
    assert _surface(td) == {"cli:recall": 1, "hook:user_prompt:failed": 1, "mcp:recall": 1}
    assert open(os.path.join(td, TR._SPOOL_NAME)).read() == ""
    assert TR.read_rollups(td)[-1]["client"] == {"cli": 2, "claude-desktop": 1}


def _mcp_corpus(tmp_path, monkeypatch):
    md = tmp_path / "memory"
    md.mkdir()
    (md / "zebra.md").write_text(
        "---\nname: zebra\ndescription: zebra canary deploy\nmetadata:\n  type: project\n---\nb\n"
    )
    td = tmp_path / ".memory-telemetry"
    monkeypatch.setenv("HIPPO_MEMORY_DIR", str(md))
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(td))
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    return str(td)


def _call(tool, arguments):
    return M.handle_request(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    )


def test_mcp_tool_calls_count_with_action_and_client(tmp_path, monkeypatch):
    td = _mcp_corpus(tmp_path, monkeypatch)
    M.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"clientInfo": {"name": "claude-code", "version": "2.1"}}})
    _call("recall", {"query": "zebra canary"})
    _call("rederive", {"action": "worklist"})
    usage = _surface(td)
    assert usage.get("mcp:recall") == 1 and usage.get("mcp:rederive:worklist") == 1
    assert TR.read_rollups(td)[-1]["client"].get("claude-code") == 2


def test_an_untrusted_corpus_counts_nothing(tmp_path, monkeypatch):
    td = _mcp_corpus(tmp_path, monkeypatch)
    from memory import trust

    monkeypatch.setattr(trust, "gate_repo_root", lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(trust, "is_trusted", lambda root: False)
    _call("recall", {"query": "zebra canary"})
    assert _surface(td) == {}


def test_every_skill_preflight_counts_its_own_verb():
    missing = []
    for path in sorted(glob.glob(os.path.join(_PLUGIN, "skills", "*", "SKILL.md"))):
        verb = os.path.basename(os.path.dirname(path))
        text = open(path, encoding="utf-8").read()
        if not re.search(r"^\s*hippo_resolve_py\s*$", text, re.M):
            continue  # bootstrap provisions the venv itself; no shared preflight to count
        if not re.search(rf"^\s*hippo_note_usage skill {re.escape(verb)}\b", text, re.M):
            missing.append(verb)
    assert not missing, f"skill preflights that never count their use: {missing}"


def test_bin_hippo_and_the_recall_hook_count_their_uses():
    hippo = open(os.path.join(_PLUGIN, "bin", "hippo"), encoding="utf-8").read()
    assert 'hippo_note_usage cli "$cmd"' in hippo
    hook = open(os.path.join(_PLUGIN, "hooks", "memory_user_prompt.sh"), encoding="utf-8").read()
    assert "|| hippo_note_usage hook user_prompt failed" in hook


def test_hook_folds_count_their_spawn(tmp_path):
    td = str(tmp_path)
    TR.record_prompt(td, trigger="human")
    TR.record_session_start(td, total=100, cap=9000)
    assert _surface(td) == {"hook:user_prompt:spawn": 1, "hook:session_start:spawn": 1}


def test_doctor_prints_per_verb_counts(tmp_path, monkeypatch):
    td = str(tmp_path / "tel")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    md = str(tmp_path / "memory")
    os.makedirs(md)
    ctx = DoctorContext(memory_dir=md, repo_root=str(tmp_path))
    assert "nothing counted yet" in check_surface_usage(ctx)["message"]
    for _ in range(3):
        TR.record_usage(td, surface="mcp", verb="new_memory", client="cli")
    TR.record_usage(td, surface="skill", verb="consolidate", client="cli")
    msg = check_surface_usage(ctx)["message"]
    assert "4 uses" in msg and "mcp:new_memory 3, skill:consolidate 1" in msg and "clients: cli 4" in msg
