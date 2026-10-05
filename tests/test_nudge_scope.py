"""CLM-4: the SessionStart onboarding nudge, scoped per repo.

The machine-wide every-5th-session counter and the machine-wide dismissal file are gone.
What replaced them, pinned through the real hook under the minimal hook PATH:

- the not-bootstrapped line stays machine-level (any repo) but is dismissable per repo;
- the no-corpus line shows only where the repo opted in (hippo enabled in its own
  ``.claude/settings*.json``, or init recorded in the projects registry), and names a
  native memory directory init could adopt when one exists — a native directory alone
  never opts a repo in (auto memory is on by default, so it exists almost everywhere).
"""

from __future__ import annotations

import json
import os

from .test_hooks_contract import _SESSION_START_HOOK, _assert_contract, _opt_in, _run_hook


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


def test_the_bootstrap_line_shows_in_any_repo_and_dismisses_per_repo(tmp_path):
    proc, project, data_dir = _run_hook(_SESSION_START_HOOK, "", tmp_path, with_corpus=False)
    _assert_contract(proc, "SessionStart")
    assert "not bootstrapped" in _ctx(proc)  # no opt-in needed: it is about the machine
    with open(os.path.join(data_dir, "nudge-dismissed-repos"), "w", encoding="utf-8") as fh:
        fh.write(os.path.realpath(project) + "\n")
    proc, _, _ = _run_hook(_SESSION_START_HOOK, "", tmp_path, with_corpus=False)
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
