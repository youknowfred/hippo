"""RWY-6: the old derived-dir copies worktrees carry are removed one directory per yes.

Doctor counts ``.claude/.memory-*`` copies under ``<main>/.claude/worktrees/*`` that nothing
reads. The offer removes them, and only them: never a corpus, never the snapshots, never
through a symlink, never more than the one directory the caller named.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from memory import derived_cleanup as DC
from memory import setup_cli

from .conftest import write_file

_NAMES = (".memory-index", ".memory-telemetry", ".memory-pending")


def _main_tree(tmp_path, trees=("wt-a", "wt-b")):
    main = tmp_path / "proj"
    md = main / ".claude" / "memory"
    md.mkdir(parents=True)
    write_file(str(md), "m_a.md", '---\nname: m_a\ndescription: "d"\n---\nbody\n')
    subprocess.run(["git", "init", "-q", str(main)], check=True)
    for name in trees:
        claude = main / ".claude" / "worktrees" / name / ".claude"
        for d in _NAMES:
            (claude / d).mkdir(parents=True)
            (claude / d / "blob").write_bytes(b"x" * 2048)
        (claude / "memory").mkdir()
        (claude / "memory" / "keep.md").write_text("a corpus copy\n")
        (claude / "settings.json").write_text("{}")
    return main, md


def _doctor_line(md, main, monkeypatch):
    monkeypatch.chdir(main)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", str(md))
    from memory.doctor_checks_env import DoctorContext
    from memory.doctor_checks_worktree import check_derived_dirs

    return check_derived_dirs(DoctorContext(memory_dir=str(md), repo_root=str(main)))["message"]


def test_lists_exactly_the_old_copies(tmp_path):
    main, _md = _main_tree(tmp_path)
    found = DC.list_copies(str(main))
    assert len(found) == 6 and all(n == 2048 for _p, n in found)
    assert {os.path.basename(p) for p, _n in found} == set(_NAMES)
    assert DC.list_copies(str(tmp_path / "nowhere")) == []


def test_removes_one_directory_and_nothing_else(tmp_path):
    main, md = _main_tree(tmp_path)
    target = main / ".claude" / "worktrees" / "wt-a" / ".claude" / ".memory-index"
    res = DC.remove_copy(str(main), str(target))
    assert res["removed"] and res["bytes"] == 2048 and not target.exists()
    assert len(DC.list_copies(str(main))) == 5
    claude = main / ".claude" / "worktrees" / "wt-a" / ".claude"
    assert (claude / "memory" / "keep.md").exists() and (claude / "settings.json").exists()
    assert (md / "m_a.md").exists()
    again = DC.remove_copy(str(main), str(target))
    assert not again["removed"] and "not a directory" in again["error"]


@pytest.mark.parametrize(
    "rel",
    [
        ".claude/memory",  # the corpus itself
        ".claude/worktrees/wt-a/.claude/memory",  # a worktree's corpus copy
        ".claude/worktrees/wt-a/.claude",  # too shallow
        ".claude/worktrees/wt-a",  # the worktree
        ".claude/worktrees/wt-a/.claude/.memory-index/blob",  # too deep
        ".claude/worktrees/wt-a/.claude/settings.json",
        ".hippo/snapshots",
        ".hippo/index",  # the LIVE index
        ".claude/worktrees/wt-a/.hippo/snapshots",
        ".claude/worktrees/wt-a/.claude/.memory-index/../memory",  # dot-dot back out
    ],
)
def test_refuses_everything_that_is_not_an_old_copy(tmp_path, rel):
    main, _md = _main_tree(tmp_path)
    (main / ".hippo" / "snapshots").mkdir(parents=True)
    (main / ".hippo" / "index").mkdir(parents=True)
    (main / ".claude" / "worktrees" / "wt-a" / ".hippo" / "snapshots").mkdir(parents=True)
    path = str(main / rel)
    assert DC.check_candidate(str(main), path) is not None
    res = DC.remove_copy(str(main), path)
    assert res["removed"] is False and res["error"]
    assert os.path.lexists(os.path.normpath(path))


def test_refuses_a_symlink_at_every_level(tmp_path):
    main, md = _main_tree(tmp_path, trees=("wt-a",))
    wt = main / ".claude" / "worktrees"
    # 1. the derived dir itself is a link to the live corpus
    link = wt / "wt-a" / ".claude" / ".memory-pending"
    os.rename(link, tmp_path / "moved-aside")
    os.symlink(md, link)
    assert "symlink" in DC.remove_copy(str(main), str(link))["error"]
    assert (md / "m_a.md").exists()
    # 2. the worktree's .claude is a link to the main tree's .claude
    os.makedirs(wt / "wt-l")
    os.symlink(main / ".claude", wt / "wt-l" / ".claude")
    (main / ".claude" / ".memory-index").mkdir()
    via = wt / "wt-l" / ".claude" / ".memory-index"
    assert "symlink" in DC.remove_copy(str(main), str(via))["error"]
    assert (main / ".claude" / ".memory-index").is_dir()
    # 3. the worktree entry is a link
    real = tmp_path / "elsewhere"
    (real / ".claude" / ".memory-index").mkdir(parents=True)
    os.symlink(real, wt / "wt-s")
    assert DC.remove_copy(str(main), str(wt / "wt-s" / ".claude" / ".memory-index"))["removed"] is False
    assert (real / ".claude" / ".memory-index").is_dir()
    listed = {p for p, _n in DC.list_copies(str(main))}
    assert str(link) not in listed and str(via) not in listed
    assert not any("wt-s" in p or "wt-l" in p for p in listed)


def test_cli_previews_then_removes_one_per_yes_until_doctor_counts_none(tmp_path, monkeypatch, capsys):
    main, md = _main_tree(tmp_path)
    assert "2 worktree(s) under" in _doctor_line(md, main, monkeypatch)
    assert setup_cli.main(["--worktree-copies"]) == 0
    out = capsys.readouterr().out
    assert "6 old copies" in out and "Nothing was removed." in out
    paths = [p for p, _n in DC.list_copies(str(main))]
    assert all(p in out for p in paths)
    # naming a directory without --yes removes nothing
    assert setup_cli.main(["--worktree-copies", "--remove", paths[0]]) == 0
    assert "Nothing was removed." in capsys.readouterr().out and os.path.isdir(paths[0])
    # a refusal says why, exits 1, removes nothing
    assert setup_cli.main(["--worktree-copies", "--remove", str(md), "--yes"]) == 1
    assert "refused" in capsys.readouterr().out and (md / "m_a.md").exists()
    for i, p in enumerate(paths):
        assert setup_cli.main(["--worktree-copies", "--remove", p, "--yes"]) == 0
        assert f"{len(paths) - i - 1} left." in capsys.readouterr().out
    assert "worktree(s) under" not in _doctor_line(md, main, monkeypatch)
    assert setup_cli.main(["--worktree-copies"]) == 0
    assert "no old copies" in capsys.readouterr().out
    for name in ("wt-a", "wt-b"):
        assert (main / ".claude" / "worktrees" / name / ".claude" / "memory" / "keep.md").exists()


def test_from_inside_a_worktree_the_offer_still_targets_the_main_tree(tmp_path, monkeypatch, capsys):
    main, md = _main_tree(tmp_path, trees=())
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    subprocess.run(["git", "-C", str(main), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(main), "commit", "-qm", "c1"], check=True, env=env)
    wt = main / ".claude" / "worktrees" / "wt-live"
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", str(wt)], check=True, env=env)
    (wt / ".claude" / ".memory-telemetry").mkdir(parents=True)
    monkeypatch.delenv("HIPPO_MEMORY_DIR", raising=False)
    monkeypatch.chdir(wt)
    assert setup_cli.main(["--worktree-copies"]) == 0
    out = capsys.readouterr().out
    target = os.path.join(os.path.realpath(main), ".claude", "worktrees", "wt-live", ".claude", ".memory-telemetry")
    assert "1 old copy" in out and target in out
    assert setup_cli.main(["--worktree-copies", "--remove", target, "--yes"]) == 0
    assert not os.path.exists(target) and (wt / ".claude" / "memory" / "m_a.md").exists()


def test_remove_needs_the_setting_flag():
    assert setup_cli.main(["--remove", "/nowhere/x", "--yes"]) == 2  # no setting chosen
    with pytest.raises(SystemExit):
        setup_cli.main(["--warm", "--remove", "/nowhere/x"])
