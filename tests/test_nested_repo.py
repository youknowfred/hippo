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
from memory.provenance import (
    ancestor_corpus_owner,
    rederive_worklist,
    resolve_dirs,
    walk_up_for_memory_dir,
)

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


def _submodule(tmp_path, sup) -> str:
    upstream = _git_init(tmp_path / "upstream")
    with open(os.path.join(upstream, "README"), "w", encoding="utf-8") as fh:
        fh.write("sub\n")
    _git(["add", "README"], upstream)
    _git(["commit", "-q", "-m", "sub"], upstream)
    _git(["-c", "protocol.file.allow=always", "submodule", "add", "-q", upstream, "libs/sub"], sup)
    return os.path.join(sup, "libs", "sub")


def _linked_worktree(main) -> str:
    """A corpus-less main checkout with a linked worktree under ``.claude/worktrees/``."""
    with open(os.path.join(main, "README"), "w", encoding="utf-8") as fh:
        fh.write("main\n")
    _git(["add", "README"], main)
    _git(["commit", "-q", "-m", "seed"], main)
    wt = os.path.join(main, ".claude", "worktrees", "wt-1")
    _git(["worktree", "add", "-q", "-b", "feature", wt], main)
    return wt


# --------------------------------------------------------------------------- #
# CLM-4/SHP-8: a corpus-less nested repo is told the ancestor's corpus exists
# --------------------------------------------------------------------------- #
def test_a_corpus_less_nested_repo_names_the_ancestors_corpus(nested, monkeypatch):
    parent, _parent_md, child = nested
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", child)
    md, rr = resolve_dirs()
    assert ancestor_corpus_owner(child) == parent

    r = D.check_corpus_resolution(D.DoctorContext(md, rr))
    assert r["status"] == "warn"
    assert r["message"] == (
        f"this repo has no corpus of its own; {parent} has one — init here, or pin "
        "HIPPO_CORPUS_ROOT to share it."
    )


def test_a_submodule_names_the_superprojects_corpus(tmp_path):
    sup = _git_init(tmp_path / "super")
    _corpus(sup)
    assert ancestor_corpus_owner(_submodule(tmp_path, sup)) == sup


def test_no_ancestor_line_for_the_repo_that_owns_the_corpus(nested, monkeypatch):
    parent, _md, _child = nested
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", parent)
    md, rr = resolve_dirs()
    assert ancestor_corpus_owner(parent) is None
    assert "no corpus of its own" not in D.check_corpus_resolution(D.DoctorContext(md, rr))["message"]


def test_no_ancestor_line_once_the_nested_repo_has_its_own_corpus(nested, monkeypatch):
    _parent, _md, child = nested
    own = _corpus(child)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", child)
    md, rr = resolve_dirs()
    assert os.path.realpath(md) == os.path.realpath(own)
    assert ancestor_corpus_owner(child) is None
    assert "no corpus of its own" not in D.check_corpus_resolution(D.DoctorContext(md, rr))["message"]


def test_no_ancestor_line_for_a_subdir_launch_or_a_plain_dir(nested):
    """A toplevel launch only — the SessionStart hook can only tell a toplevel by its .git."""
    parent, _md, child = nested
    sub = os.path.join(child, "src")
    os.makedirs(sub)
    plain = os.path.join(parent, "..", "notes")
    os.makedirs(plain)
    assert ancestor_corpus_owner(sub) is None
    assert ancestor_corpus_owner(plain) is None


def test_no_ancestor_line_for_a_linked_worktree(tmp_path):
    """SHP-7 resolves a linked worktree through its main tree, and ``init here`` in one
    would make a branch-only corpus that leaves with the worktree."""
    _corpus(str(tmp_path))
    main = _git_init(tmp_path / "main")
    assert ancestor_corpus_owner(main) == os.path.realpath(str(tmp_path))
    assert ancestor_corpus_owner(_linked_worktree(main)) is None


@pytest.mark.parametrize("var", ["HIPPO_CORPUS_ROOT", "HIPPO_MEMORY_DIR"])
def test_no_ancestor_line_once_the_corpus_is_pinned(nested, monkeypatch, var):
    parent, parent_md, child = nested
    monkeypatch.setenv(var, parent if var == "HIPPO_CORPUS_ROOT" else parent_md)
    assert ancestor_corpus_owner(child) is None


def test_pinning_the_parent_shares_its_corpus_under_the_parents_root(nested, monkeypatch):
    """The line's second choice: HIPPO_CORPUS_ROOT=<parent> from the child resolves the
    parent's corpus AND the parent as repo_root, so citations check against its tree."""
    parent, parent_md, child = nested
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", child)
    monkeypatch.setenv("HIPPO_CORPUS_ROOT", parent)
    md, rr = resolve_dirs()
    assert os.path.realpath(md) == os.path.realpath(parent_md)
    assert os.path.realpath(rr) == parent


def test_init_in_a_nested_repo_creates_its_own_corpus_not_the_ancestors(nested, monkeypatch):
    from memory.init_project import init_project

    _parent, parent_md, child = nested
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", child)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    before = sorted(os.listdir(parent_md))
    r = init_project()
    assert os.path.realpath(r["memory_dir"]) == os.path.join(child, ".claude", "memory")
    assert r["mode"] == "fresh" and "MEMORY.md" in r["seeded"]
    assert "nested_owner" not in r
    assert sorted(os.listdir(parent_md)) == before  # the ancestor's corpus untouched
    md, _rr = resolve_dirs()
    assert os.path.realpath(md) == os.path.join(child, ".claude", "memory")
    assert ancestor_corpus_owner(child) is None


# --------------------------------------------------------------------------- #
# SessionStart: the same line from bash, before any Python runs
# --------------------------------------------------------------------------- #
def _ctx(proc) -> str:
    out = proc.stdout.strip()
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def _hook_in(tmp_path, project_dir, extra_env=None, **kw):
    # _run_hook's own project (tmp_path/project) carries the ancestor corpus; the session
    # launches in project_dir under it.
    return _run_hook(
        _SESSION_START_HOOK, "", tmp_path, with_corpus=True, venv_python=True, sentinel=True,
        extra_env={"CLAUDE_PROJECT_DIR": project_dir, **(extra_env or {})}, **kw,
    )


def test_session_start_names_a_corpus_less_nested_repo_in_one_line(tmp_path):
    child = _git_init(tmp_path / "project" / "vendor" / "child")
    proc, project, _ = _hook_in(tmp_path, child)
    _assert_contract(proc, "SessionStart")
    ctx = _ctx(proc)
    assert ctx.startswith(
        f"This repo has no corpus of its own; {os.path.realpath(project)} has one — run "
        "/hippo:setup here, or pin HIPPO_CORPUS_ROOT to share it. "
    )
    assert "\n" not in ctx
    assert not os.path.isdir(os.path.join(child, ".claude"))  # wrote nothing


def test_session_start_ancestor_line_on_desktop_names_the_init_tool(tmp_path):
    child = _git_init(tmp_path / "project" / "child")
    proc, _, _ = _hook_in(tmp_path, child, entrypoint="claude-desktop")
    ctx = _ctx(proc)
    assert "no corpus of its own" in ctx and "setup MCP tool's init step" in ctx
    assert "/hippo:setup" not in ctx


def test_session_start_has_no_ancestor_line_for_a_subdir_or_a_plain_dir(tmp_path):
    child = _git_init(tmp_path / "project" / "child")
    sub = os.path.join(child, "src")
    os.makedirs(sub)
    plain = os.path.join(str(tmp_path / "project"), "notes")  # no .git: not a repo toplevel
    os.makedirs(plain)
    for d in (sub, plain):
        proc, _, _ = _hook_in(tmp_path, d)
        _assert_contract(proc, "SessionStart")
        assert "no corpus of its own" not in proc.stdout


def test_session_start_has_no_ancestor_line_once_the_corpus_is_pinned(tmp_path):
    child = _git_init(tmp_path / "project" / "child")
    project = os.path.realpath(str(tmp_path / "project"))
    proc, _, _ = _hook_in(tmp_path, child, extra_env={"HIPPO_CORPUS_ROOT": project})
    _assert_contract(proc, "SessionStart")
    assert "no corpus of its own" not in proc.stdout


def test_session_start_ancestor_line_is_dismissable_per_repo(tmp_path):
    child = _git_init(tmp_path / "project" / "child")
    _proc, _project, data_dir = _hook_in(tmp_path, child)
    with open(os.path.join(data_dir, "nudge-dismissed-repos"), "w", encoding="utf-8") as fh:
        fh.write(child + "\n")
    proc, _, _ = _hook_in(tmp_path, child)
    _assert_contract(proc, "SessionStart")
    assert proc.stdout.strip() == ""


@pytest.mark.parametrize("layout", ["child", "subdir", "plain", "submodule", "worktree"])
def test_bash_and_python_name_the_same_ancestor(tmp_path, layout):
    """The hook's ``hippo_ancestor_corpus`` and ``ancestor_corpus_owner`` agree on every
    layout: the same owner, or both silent. The hook gets a real PATH (the harness's minimal
    one has no git, which a linked worktree's probe needs)."""
    project = tmp_path / "project"
    if layout == "child":
        d = _git_init(project / "libs" / "child")
    elif layout == "subdir":
        d = os.path.join(_git_init(project / "libs" / "child"), "src")
        os.makedirs(d)
    elif layout == "plain":
        d = str(project / "notes")
        os.makedirs(d)
    elif layout == "submodule":
        d = _submodule(tmp_path, _git_init(project))
    else:
        d = _linked_worktree(_git_init(project / "main"))
    path = f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"
    proc, _, _ = _hook_in(tmp_path, d, extra_env={"PATH": path})  # seeds project's corpus first
    _assert_contract(proc, "SessionStart")
    owner = ancestor_corpus_owner(d)
    expected = {"child": os.path.realpath(str(project)), "submodule": os.path.realpath(str(project))}
    assert owner == expected.get(layout)
    if owner:
        assert _ctx(proc).startswith(f"This repo has no corpus of its own; {owner} has one")
    else:
        assert "no corpus of its own" not in proc.stdout
