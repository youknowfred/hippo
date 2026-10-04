"""PLT-2: hippo declares the oldest Claude Code it supports and checks the running one."""

from __future__ import annotations

import os

from memory import platform_floor as PF
from memory.doctor_checks_env import DoctorContext
from memory.doctor_checks_platform import check_claude_code_version
from memory.session_start_health import harness_floor_producer


def test_version_is_read_from_ai_agent():
    assert PF.claude_code_version({"AI_AGENT": "claude-code_2-1-289_harness"}) == "2.1.289"
    assert PF.claude_code_version({"AI_AGENT": "claude-code_2-1-286_agent"}) == "2.1.286"
    assert PF.claude_code_version({"AI_AGENT": "claude-code_2-1-300"}) == "2.1.300"
    for junk in ("", "codex_1-2-3", "claude-code_2.1.289", "claude-code_2-1"):
        assert PF.claude_code_version({"AI_AGENT": junk}) is None
    assert PF.claude_code_version({}) is None


def test_only_an_older_known_version_is_too_old():
    assert PF.harness_too_old({"AI_AGENT": "claude-code_2-1-268_harness"}) == "2.1.268"
    assert PF.harness_too_old({"AI_AGENT": "claude-code_2-1-269_harness"}) is None
    assert PF.harness_too_old({"AI_AGENT": "claude-code_3-0-0_harness"}) is None
    assert PF.harness_too_old({}) is None  # unknown checks nothing
    line = PF.harness_floor_line({"AI_AGENT": "claude-code_2-1-200_agent"})
    assert line.startswith("⚠ Claude Code 2.1.200 is older than") and PF.MIN_CLAUDE_CODE in line


def test_doctor_and_sessionstart(monkeypatch, tmp_path):
    ctx = DoctorContext(memory_dir=str(tmp_path), repo_root=None)
    monkeypatch.delenv("AI_AGENT", raising=False)
    assert "unknown here" in check_claude_code_version(ctx)["message"]
    assert harness_floor_producer(str(tmp_path), str(tmp_path)) is None
    monkeypatch.setenv("AI_AGENT", "claude-code_2-1-100_harness")
    assert check_claude_code_version(ctx)["status"] == "warn"
    assert "older than hippo's supported floor" in harness_floor_producer(str(tmp_path), str(tmp_path))
    monkeypatch.setenv("AI_AGENT", "claude-code_2-1-289_harness")
    assert check_claude_code_version(ctx) == {"status": "ok", "message": "Claude Code 2.1.289 (floor 2.1.269)."}


def test_readme_states_the_same_floor():
    readme = os.path.join(os.path.dirname(__file__), "..", "README.md")
    assert f"**Claude Code {PF.MIN_CLAUDE_CODE} or newer.**" in open(readme, encoding="utf-8").read()
