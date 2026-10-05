"""SHP-8: a git repo nested inside another repo (or a submodule) resolves its OWN corpus.

``walk_up_for_memory_dir`` stops at the session's own git toplevel. Before SHP-8 a session
launched AT a toplevel with no corpus kept climbing into the nearest ancestor's
``.claude/memory``: the MCP tools and the CLI then read and wrote the parent's corpus with
the CHILD as ``repo_root`` (so the rederive worklist proposed stripping citations the
parent's tree still has, and trust rows keyed the parent's corpus under the child), while
the hooks, which never climb, did nothing. A parent's corpus is shared deliberately through
``HIPPO_CORPUS_ROOT``. These tests build REAL nested ``git init`` repos and submodules in tmp
dirs and pin: the resolution, the CLM-4 line that names a corpus-less nested repo (doctor,
the SessionStart hook), its absence everywhere else, and init giving the nested repo its own.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from memory import doctor as D
from memory.provenance import foreign_corpus_owner, rederive_worklist, resolve_dirs, walk_up_for_memory_dir

from .test_hooks_contract import _SESSION_START_HOOK, _assert_contract, _run_hook


def _git_init(path) -> str:
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    return os.path.realpath(str(path))


def _git(args, cwd) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "commit.gpgsign=false",
         *args],
        cwd=cwd, check=True, capture_output=True,
    )


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


def test_a_nested_repo_resolves_its_own_corpus_not_the_ancestors(nested, monkeypatch):
    _parent, _parent_md, child = nested
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", child)
    md, rr = resolve_dirs()
    assert os.path.realpath(md) == os.path.join(child, ".claude", "memory")  # the walk stopped at child
    assert os.path.realpath(rr) == child
    assert walk_up_for_memory_dir(child) == ("", "none-found")


def test_a_nested_repo_cannot_rederive_its_parents_corpus(nested, monkeypatch):
    """The harm the climb did: the parent's corpus paired with the CHILD as repo_root. The
    child's ``git ls-files`` lacks the parent's files, so the worklist said the parent's
    memory "loses" a citation the parent still has — applying it strips a valid one."""
    parent, parent_md, child = nested
    os.makedirs(os.path.join(parent, "src"))
    with open(os.path.join(parent, "src", "engine.py"), "w", encoding="utf-8") as fh:
        fh.write("ENGINE = 1\n")
    _git(["add", "src/engine.py"], parent)
    _git(["commit", "-q", "-m", "code"], parent)
    with open(os.path.join(parent_md, "engine.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: engine\ncited_paths: ["src/engine.py"]\nsource_commit: "x"\n'
                 "---\nThe engine lives in src/engine.py.\n")
    _git(["add", ".claude/memory"], parent)
    _git(["commit", "-q", "-m", "memory"], parent)

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", parent)
    assert rederive_worklist(*resolve_dirs()) == []  # from the parent itself: nothing to change

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", child)
    md, rr = resolve_dirs()
    work = rederive_worklist(md, rr) if os.path.isdir(md) else []
    assert [w for w in work if w["name"] == "engine"] == []


def test_a_submodule_resolves_its_own_corpus_not_the_superprojects(tmp_path, monkeypatch):
    upstream = _git_init(tmp_path / "upstream")
    with open(os.path.join(upstream, "README"), "w", encoding="utf-8") as fh:
        fh.write("sub\n")
    _git(["add", "README"], upstream)
    _git(["commit", "-q", "-m", "sub"], upstream)
    sup = _git_init(tmp_path / "super")
    _corpus(sup)
    _git(["-c", "protocol.file.allow=always", "submodule", "add", "-q", upstream, "libs/sub"], sup)
    sub = os.path.join(sup, "libs", "sub")
    assert os.path.isfile(os.path.join(sub, ".git"))  # a submodule's .git is a pointer file

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", sub)
    md, rr = resolve_dirs()
    assert os.path.realpath(md) == os.path.join(sub, ".claude", "memory")
    assert os.path.realpath(rr) == sub


@pytest.mark.parametrize("rel", ["", "src"])
def test_a_launch_through_a_symlink_still_stops_at_the_toplevel(nested, monkeypatch, rel):
    """git names the toplevel by its real path; a launch dir reached through a symlink must
    still meet it, or the walk climbs on into the parent's corpus."""
    parent, _parent_md, child = nested
    os.makedirs(os.path.join(child, "src"))
    link = os.path.join(parent, "child-link")
    os.symlink(child, link)
    start = os.path.join(link, rel) if rel else link
    assert walk_up_for_memory_dir(start) == ("", "none-found")


def test_a_dir_outside_any_repo_still_climbs_to_the_corpus_above(tmp_path, monkeypatch):
    """No git toplevel bounds the walk, so it keeps climbing to $HOME as before SHP-8."""
    home = str(tmp_path / "home")
    md = _corpus(os.path.join(home, "work"))
    deep = os.path.join(home, "work", "notes", "deep")
    os.makedirs(deep)
    monkeypatch.setattr(os.path, "expanduser", lambda p: home if p == "~" else p)
    assert walk_up_for_memory_dir(deep) == (md, "root-fallthrough")


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
