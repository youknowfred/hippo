"""NAT-1: read-only detection of native auto memory beside the corpus — its settings, where
it reads, and what it left in the corpus — plus the doctor line."""

from __future__ import annotations

import json
import os

from memory.doctor_checks_env import DoctorContext
from memory.doctor_checks_native import check_native_auto_memory
from memory.native_memory import native_memory_state

from .conftest import git_commit, write_file


def _settings(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def test_default_is_on_with_nothing_redirected(repo, memory_dir):
    s = native_memory_state(memory_dir, repo)
    assert s["enabled"] is True and s["decided_by"] == "default"
    assert s["directory"] is None and s["stamped"] == 0 and s["untracked"] == []


def test_settings_precedence_local_project_user_and_env(repo, memory_dir, tmp_path, monkeypatch):
    user = str(tmp_path / "user-settings.json")
    monkeypatch.setenv("HIPPO_CLAUDE_SETTINGS", user)
    _settings(user, {"autoMemoryEnabled": False})
    assert native_memory_state(memory_dir, repo)["decided_by"] == "autoMemoryEnabled in user settings"

    _settings(os.path.join(repo, ".claude", "settings.json"), {"autoMemoryEnabled": True})
    s = native_memory_state(memory_dir, repo)
    assert s["enabled"] is True and s["decided_by"] == "autoMemoryEnabled in project settings"

    _settings(os.path.join(repo, ".claude", "settings.local.json"), {"autoMemoryEnabled": False})
    s = native_memory_state(memory_dir, repo)
    assert s["enabled"] is False and s["decided_by"] == "autoMemoryEnabled in local settings"

    monkeypatch.setenv("CLAUDE_CODE_DISABLE_AUTO_MEMORY", "1")
    _settings(os.path.join(repo, ".claude", "settings.local.json"), {"autoMemoryEnabled": True})
    s = native_memory_state(memory_dir, repo)
    assert s["enabled"] is False and s["decided_by"].startswith("CLAUDE_CODE_DISABLE_AUTO_MEMORY")


def test_auto_memory_directory_is_resolved_and_compared(repo, memory_dir, tmp_path):
    _settings(os.path.join(repo, ".claude", "settings.json"), {"autoMemoryDirectory": ".claude/memory"})
    s = native_memory_state(memory_dir, repo)
    assert s["directory_source"] == "project settings" and s["directory_is_corpus"] is True

    elsewhere = str(tmp_path / "elsewhere")
    _settings(os.path.join(repo, ".claude", "settings.json"), {"autoMemoryDirectory": elsewhere})
    s = native_memory_state(memory_dir, repo)
    assert s["directory"] == elsewhere and s["directory_is_corpus"] is False


def test_native_stamps_and_untracked_files_are_counted(repo, memory_dir):
    write_file(memory_dir, "hippo_written.md", "---\nname: a\ndescription: d\nmetadata:\n  type: user\n---\nb\n")
    write_file(
        memory_dir,
        "adopted.md",
        "---\nname: b\ndescription: d\nmetadata:\n  type: user\n  modified: 2026-10-03T00:00:00Z\n"
        "  source_commit: abc1234\n---\nb\n",
    )
    write_file(
        memory_dir,
        "native_only.md",
        "---\nname: c\ndescription: d\nmetadata:\n  node_type: memory\n  type: project\n"
        "  originSessionId: 0000\n  modified: 2026-10-03T00:00:00Z\n---\nb\n",
    )
    git_commit(repo, "corpus", 1_700_000_000)
    write_file(memory_dir, "never_committed.md", "---\nname: d\ndescription: d\n---\nb\n")
    s = native_memory_state(memory_dir, repo)
    assert s["stamped"] == 2 and s["stamped_without_provenance"] == ["native_only"]
    assert s["untracked"] == ["never_committed"]


def test_doctor_warns_when_the_floor_cannot_load(repo, memory_dir, tmp_path, monkeypatch):
    ctx = DoctorContext(memory_dir=memory_dir, repo_root=repo)
    assert check_native_auto_memory(ctx)["status"] == "ok"

    monkeypatch.setenv("CLAUDE_CODE_DISABLE_AUTO_MEMORY", "1")
    res = check_native_auto_memory(ctx)
    assert res["status"] == "warn" and "skips MEMORY.md" in res["message"]
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_AUTO_MEMORY")

    _settings(os.path.join(repo, ".claude", "settings.json"), {"autoMemoryDirectory": str(tmp_path / "x")})
    res = check_native_auto_memory(ctx)
    assert res["status"] == "warn" and "bypassing hippo's projects-dir symlink" in res["message"]


def test_doctor_reports_counts_as_information(repo, memory_dir):
    write_file(
        memory_dir,
        "native_only.md",
        "---\nname: c\ndescription: d\nmetadata:\n  modified: 2026-10-03T00:00:00Z\n---\nb\n",
    )
    res = check_native_auto_memory(DoctorContext(memory_dir=memory_dir, repo_root=repo))
    assert res["status"] == "ok"
    assert "1 corpus file(s) carry native auto memory's stamp, 1 with no hippo provenance: native_only" in res["message"]
    assert "1 corpus file(s) untracked by git: native_only" in res["message"]
