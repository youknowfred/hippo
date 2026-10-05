"""CLM-4: a git repo nested inside another corpus is named, in doctor and at SessionStart.

``walk_up_for_memory_dir`` checks a toplevel's own corpus, but a session launched AT a
toplevel with none keeps climbing into the nearest ancestor's ``.claude/memory``. Every
tool then reads and writes that corpus. These tests build REAL nested ``git init`` repos
in tmp dirs and pin: the resolution as it is, the one line that names it (doctor, the
SessionStart hook), its absence everywhere else, and init giving the nested repo its own.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from memory import doctor as D
from memory.provenance import foreign_corpus_owner, resolve_dirs

from .test_hooks_contract import _SESSION_START_HOOK, _assert_contract, _run_hook


def _git_init(path) -> str:
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    return os.path.realpath(str(path))


def _corpus(root) -> str:
    md = os.path.join(root, ".claude", "memory")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# Memory Index\n")
    return md


@pytest.fixture
def nested(tmp_path):
    parent = _git_init(tmp_path / "parent")
    parent_md = _corpus(parent)
    child = _git_init(os.path.join(parent, "libs", "child"))
    return parent, parent_md, child


def test_a_nested_repo_resolves_the_ancestors_corpus_and_is_named(nested, monkeypatch):
    parent, parent_md, child = nested
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", child)
    md, rr = resolve_dirs()
    assert os.path.realpath(md) == os.path.realpath(parent_md)  # the walk climbed past child
    assert os.path.realpath(rr) == child
    assert foreign_corpus_owner(md, rr) == parent

    r = D.check_corpus_resolution(D.DoctorContext(md, rr))
    assert r["status"] == "warn"
    assert f"nested inside {parent}" in r["message"]
    assert "Run init here to give this repo its own corpus" in r["message"]


def test_no_nested_line_for_the_repo_that_owns_the_corpus(nested, monkeypatch):
    parent, _md, _child = nested
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", parent)
    md, rr = resolve_dirs()
    assert foreign_corpus_owner(md, rr) is None
    assert "nested inside" not in D.check_corpus_resolution(D.DoctorContext(md, rr))["message"]


def test_no_nested_line_once_the_nested_repo_has_its_own_corpus(nested, monkeypatch):
    _parent, _md, child = nested
    own = _corpus(child)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", child)
    md, rr = resolve_dirs()
    assert os.path.realpath(md) == os.path.realpath(own)
    assert foreign_corpus_owner(md, rr) is None


def test_a_subdir_launch_in_the_nested_repo_does_not_climb_so_no_line(nested, monkeypatch):
    _parent, _md, child = nested
    sub = os.path.join(child, "src")
    os.makedirs(sub)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", sub)
    md, rr = resolve_dirs()
    assert foreign_corpus_owner(md, rr) is None


def test_a_per_package_corpus_inside_the_same_repo_is_not_foreign(nested, monkeypatch):
    parent, _md, _child = nested
    pkg = os.path.join(parent, "packages", "web")
    pkg_md = _corpus(pkg)
    assert foreign_corpus_owner(pkg_md, parent) is None


def test_an_explicit_memory_dir_is_never_called_nested(nested, monkeypatch):
    _parent, parent_md, child = nested
    monkeypatch.setenv("HIPPO_MEMORY_DIR", parent_md)
    assert foreign_corpus_owner(parent_md, child) is None


def test_init_in_a_nested_repo_creates_its_own_corpus_not_the_ancestors(nested, monkeypatch):
    from memory.init_project import init_project

    parent, parent_md, child = nested
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", child)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    before = sorted(os.listdir(parent_md))
    r = init_project()
    assert r["nested_owner"] == parent
    assert os.path.realpath(r["memory_dir"]) == os.path.join(child, ".claude", "memory")
    assert r["mode"] == "fresh" and "MEMORY.md" in r["seeded"]
    assert sorted(os.listdir(parent_md)) == before  # the ancestor's corpus untouched
    md, rr = resolve_dirs()
    assert foreign_corpus_owner(md, rr) is None  # resolution now lands on its own


# --------------------------------------------------------------------------- #
# SessionStart: the same line from bash, before any Python runs
# --------------------------------------------------------------------------- #
def _ctx(proc) -> str:
    out = proc.stdout.strip()
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def _hook_in(tmp_path, project_dir, **kw):
    # _run_hook's own project (tmp_path/project) carries the ancestor corpus; the session
    # launches in project_dir under it.
    return _run_hook(
        _SESSION_START_HOOK, "", tmp_path, with_corpus=True, venv_python=True, sentinel=True,
        extra_env={"CLAUDE_PROJECT_DIR": project_dir}, **kw,
    )


def test_session_start_names_a_nested_repo_in_one_line(tmp_path):
    child = _git_init(tmp_path / "project" / "vendor" / "child")
    proc, project, _ = _hook_in(tmp_path, child)
    _assert_contract(proc, "SessionStart")
    ctx = _ctx(proc)
    assert ctx.startswith(f"This repo is nested inside {os.path.realpath(project)}")
    assert "run /hippo:setup here" in ctx and "\n" not in ctx
    assert not os.path.isdir(os.path.join(child, ".claude"))  # wrote nothing


def test_session_start_nested_line_on_desktop_names_the_init_tool(tmp_path):
    child = _git_init(tmp_path / "project" / "child")
    proc, _, _ = _hook_in(tmp_path, child, entrypoint="claude-desktop")
    ctx = _ctx(proc)
    assert "nested inside" in ctx and "setup MCP tool's init step" in ctx and "/hippo:setup" not in ctx


def test_session_start_has_no_nested_line_for_a_subdir_or_a_plain_dir(tmp_path):
    child = _git_init(tmp_path / "project" / "child")
    sub = os.path.join(child, "src")
    os.makedirs(sub)
    plain = os.path.join(str(tmp_path / "project"), "notes")  # no .git: not a repo toplevel
    os.makedirs(plain)
    for d in (sub, plain):
        proc, _, _ = _hook_in(tmp_path, d)
        _assert_contract(proc, "SessionStart")
        assert "nested inside" not in proc.stdout


def test_session_start_nested_line_is_dismissable_per_repo(tmp_path):
    child = _git_init(tmp_path / "project" / "child")
    _proc, _project, data_dir = _hook_in(tmp_path, child)
    with open(os.path.join(data_dir, "nudge-dismissed-repos"), "w", encoding="utf-8") as fh:
        fh.write(child + "\n")
    proc, _, _ = _hook_in(tmp_path, child)
    _assert_contract(proc, "SessionStart")
    assert proc.stdout.strip() == ""
