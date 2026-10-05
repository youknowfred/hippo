"""CLM-4: the SessionStart onboarding nudge, scoped per repo.

The machine-wide every-5th-session counter is gone. What replaced it, pinned through the
real hook under the minimal hook PATH:

- the not-bootstrapped line is about the machine, so its cadence and dismissal are the
  machine's (owner ruling 2026-10-05): it shows in the first session of each day, in any
  repo, and its own hint dismisses it machine-wide with a bootstrap-only marker;
- the nested-repo and no-corpus lines stay per repo: every session, dismissed per repo;
- the no-corpus line shows only where the repo opted in (hippo enabled in its own
  ``.claude/settings*.json``, or init recorded in the projects registry), and names a
  native memory directory init could adopt when one exists — a native directory alone
  never opts a repo in (auto memory is on by default, so it exists almost everywhere).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from .test_hooks_contract import _SESSION_START_HOOK, _assert_contract, _opt_in, _run_hook
from .test_nested_repo import _git_init, _hook_in

_DAY_STAMP = ".bootstrap-nudge-day"
_BOOT_DISMISSED = ".bootstrap-nudge-dismissed"


def _ctx(proc) -> str:
    out = proc.stdout.strip()
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def _native_dir(tmp_path) -> str:
    """Claude Code's own memory dir for tmp_path/project, holding one topic file."""
    project = os.path.realpath(str(tmp_path / "project"))
    os.makedirs(project, exist_ok=True)
    encoded = "".join(c if c.isalnum() and c.isascii() else "-" for c in project)
    nd = tmp_path / "claude-projects" / encoded / "memory"
    os.makedirs(nd)
    (nd / "notes.md").write_text("# a native memory\n", encoding="utf-8")
    return str(nd)


def _day(offset: int = 0) -> str:
    """A UTC date: _boot pins the hook to TZ=UTC so the two clocks name the same day."""
    return (datetime.now(timezone.utc).date() + timedelta(days=offset)).isoformat()


def _boot(base, *, data_dir: str = "", entrypoint: str = "", with_corpus: bool = False):
    """A session on a machine that never bootstrapped, in a repo that never opted in."""
    extra_env = {"TZ": "UTC"}
    if data_dir:
        extra_env["CLAUDE_PLUGIN_DATA"] = data_dir
    return _run_hook(
        _SESSION_START_HOOK, "", base, with_corpus=with_corpus, entrypoint=entrypoint,
        extra_env=extra_env,
    )


def _stamp(data_dir: str) -> str:
    with open(os.path.join(data_dir, _DAY_STAMP), encoding="utf-8") as fh:
        return fh.read().strip()


def test_the_bootstrap_line_shows_in_the_first_session_of_the_day_in_any_repo(tmp_path):
    before = _day()
    proc, _, data_dir = _boot(tmp_path)
    _assert_contract(proc, "SessionStart")
    assert "not bootstrapped" in _ctx(proc)  # no opt-in needed: it is about the machine
    assert _stamp(data_dir) in {before, _day()}
    # The second session of the day stays quiet, in this repo or any other.
    proc, _, _ = _boot(tmp_path)
    _assert_contract(proc, "SessionStart")
    assert proc.stdout.strip() == ""
    other = tmp_path / "second"
    other.mkdir()
    proc, _, _ = _boot(other, data_dir=data_dir)
    _assert_contract(proc, "SessionStart")
    assert proc.stdout.strip() == ""
    # Nor does the Python dispatcher say it instead, where a corpus lets it run.
    third = tmp_path / "third"
    third.mkdir()
    proc, _, _ = _boot(third, data_dir=data_dir, with_corpus=True)
    _assert_contract(proc, "SessionStart")
    assert "not bootstrapped" not in proc.stdout


def test_a_stamp_from_yesterday_shows_the_line_again(tmp_path):
    data_dir = tmp_path / "plugin-data"
    data_dir.mkdir()
    (data_dir / _DAY_STAMP).write_text(_day(-1) + "\n", encoding="utf-8")
    proc, _, _ = _boot(tmp_path)
    _assert_contract(proc, "SessionStart")
    assert "not bootstrapped" in _ctx(proc)
    assert _stamp(str(data_dir)) != _day(-1)


@pytest.mark.parametrize("entrypoint", ["", "claude-desktop"])
def test_the_bootstrap_line_dismisses_machine_wide(tmp_path, entrypoint):
    proc, _, data_dir = _boot(tmp_path, entrypoint=entrypoint)
    _assert_contract(proc, "SessionStart")
    ctx = _ctx(proc)
    assert "once a day" in ctx and "nudge-dismissed-repos" not in ctx and "\n" not in ctx
    # The line's own hint is a runnable command naming the bootstrap-only marker.
    m = re.search(r"To stop it on this machine: (touch '([^']+)')", ctx)
    assert m and m.group(2) == os.path.join(data_dir, _BOOT_DISMISSED)
    subprocess.run(["/bin/sh", "-c", m.group(1)], check=True)
    assert os.path.isfile(m.group(2))
    os.remove(os.path.join(data_dir, _DAY_STAMP))  # a new day: only the dismissal is left
    other = tmp_path / "second"
    other.mkdir()
    for base, shared in ((tmp_path, ""), (other, data_dir)):
        proc, _, _ = _boot(base, data_dir=shared, entrypoint=entrypoint)
        _assert_contract(proc, "SessionStart")
        assert proc.stdout.strip() == ""
    assert not os.path.exists(os.path.join(data_dir, _DAY_STAMP))  # written only when shown


def test_a_repo_already_dismissed_per_repo_stays_quiet_and_keeps_the_day(tmp_path):
    # Before the owner ruling the bootstrap line's hint was the per-repo list; a repo a user
    # put there keeps its silence, and a session there does not use up the day.
    data_dir = tmp_path / "plugin-data"
    data_dir.mkdir()
    project = os.path.realpath(str(tmp_path / "project"))
    os.makedirs(project)
    (data_dir / "nudge-dismissed-repos").write_text(project + "\n", encoding="utf-8")
    proc, _, _ = _boot(tmp_path)
    _assert_contract(proc, "SessionStart")
    assert proc.stdout.strip() == ""
    assert not (data_dir / _DAY_STAMP).exists()
    other = tmp_path / "second"
    other.mkdir()
    proc, _, _ = _boot(other, data_dir=str(data_dir))
    assert "not bootstrapped" in _ctx(proc)


@pytest.mark.parametrize("kind", ["no-corpus", "nested"])
def test_the_per_repo_lines_are_unchanged(tmp_path, kind):
    # Bootstrapped: every session, dismissed per repo, and untouched by the bootstrap
    # line's day stamp and machine-wide marker.
    if kind == "nested":
        repo = _git_init(tmp_path / "project" / "child")

        def run():
            return _hook_in(tmp_path, repo)
    else:
        repo = os.path.realpath(_opt_in(tmp_path))

        def run():
            return _run_hook(
                _SESSION_START_HOOK, "", tmp_path, with_corpus=False, venv_python=True,
                sentinel=True,
            )
    data_dir = tmp_path / "plugin-data"
    for _ in range(2):
        proc, _, _ = run()
        _assert_contract(proc, "SessionStart")
        ctx = _ctx(proc)
        assert "/hippo:setup" in ctx and "nudge-dismissed-repos" in ctx and repo in ctx
    assert not (data_dir / _DAY_STAMP).exists()
    (data_dir / _BOOT_DISMISSED).touch()
    proc, _, _ = run()
    assert "/hippo:setup" in _ctx(proc)
    with open(data_dir / "nudge-dismissed-repos", "a", encoding="utf-8") as fh:
        fh.write(repo + "\n")
    proc, _, _ = run()
    _assert_contract(proc, "SessionStart")
    assert proc.stdout.strip() == ""


def test_the_init_line_names_a_native_dir_init_can_adopt(tmp_path):
    nd = _native_dir(tmp_path)
    _opt_in(tmp_path)
    proc, _, _ = _run_hook(
        _SESSION_START_HOOK, "", tmp_path, with_corpus=False, venv_python=True, sentinel=True,
        extra_env={"HIPPO_CLAUDE_PROJECTS_DIR": str(tmp_path / "claude-projects")},
    )
    _assert_contract(proc, "SessionStart")
    ctx = _ctx(proc)
    assert "/hippo:setup" in ctx and f"({nd}) can be adopted" in ctx and "\n" not in ctx


def test_a_native_dir_alone_does_not_opt_a_repo_in(tmp_path):
    _native_dir(tmp_path)
    proc, _, _ = _run_hook(
        _SESSION_START_HOOK, "", tmp_path, with_corpus=False, venv_python=True, sentinel=True,
        extra_env={"HIPPO_CLAUDE_PROJECTS_DIR": str(tmp_path / "claude-projects")},
    )
    _assert_contract(proc, "SessionStart")
    assert proc.stdout.strip() == ""


def test_the_projects_registry_opts_a_repo_in(tmp_path):
    project = os.path.realpath(str(tmp_path / "project"))
    os.makedirs(project, exist_ok=True)
    reg = tmp_path / "projects.json"
    reg.write_text(json.dumps({"projects": {project: {"memory_dir": project + "/.claude/memory"}}}),
                   encoding="utf-8")
    proc, _, _ = _run_hook(
        _SESSION_START_HOOK, "", tmp_path, with_corpus=False, venv_python=True, sentinel=True,
        extra_env={"HIPPO_PROJECTS_FILE": str(reg)},
    )
    assert "/hippo:setup" in _ctx(proc)


def test_a_plugin_enabled_false_does_not_opt_in(tmp_path):
    claude = tmp_path / "project" / ".claude"
    os.makedirs(claude)
    (claude / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"hippo@hippo": False}}), encoding="utf-8"
    )
    proc, _, _ = _run_hook(
        _SESSION_START_HOOK, "", tmp_path, with_corpus=False, venv_python=True, sentinel=True,
    )
    assert proc.stdout.strip() == ""
