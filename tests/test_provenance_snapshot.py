"""The pre-migration backup (MIG-6, MIG-7, MIG-12) and doctor's line for the old ones (MIG-8).

MIG-6: the copy lives outside ``.claude/``, which the Claude desktop app copies into every
worktree it creates; MIG-12: in ``<project>/.hippo/snapshots/``, the container the derived
dirs use, not v1.42.3's ``<project>/.hippo-snapshots/``. MIG-7: a corpus git can restore
gets its commit, not a copy. MIG-8: doctor names the backups older plugins left in
``.claude/``, with their size, and calls them spent once the migration is done.

The MIG-1 snapshot tests moved here from test_provenance.py (its size ratchet).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess

import pytest

from memory import provenance as P
from memory.derived_dirs import derived_home
from memory.doctor_checks_env import DoctorContext
from memory.doctor_checks_worktree import check_snapshots
from memory.provenance_snapshot import snapshot_root

from .conftest import git_commit, write_file


def _corpus(repo, *, ignored):
    """One memory whose citations a re-derivation changes (it cites package.json). With
    ``ignored`` the corpus is gitignored, as this repo's own is: git has no undo for it."""
    if ignored:
        write_file(repo, ".gitignore", ".claude/memory/\n")
    write_file(repo, "package.json", '{"name":"x"}\n')
    write_file(repo, "src/keep.py", "k = 1\n")
    c1 = git_commit(repo, "code", 1_700_000_000)
    target = write_file(
        repo,
        ".claude/memory/m.md",
        f'---\nname: M\ncited_paths: ["src/keep.py"]\nsource_commit: "{c1}"\n'
        "---\nbody cites src/keep.py and package.json\n",
    )
    git_commit(repo, "memory", 1_700_000_001)
    return target


def _tree(path):
    out = {}
    for d, _dirs, files in os.walk(path):
        for f in files:
            with open(os.path.join(d, f), "rb") as fh:
                out[os.path.relpath(os.path.join(d, f), path)] = fh.read()
    return out


def _migrate(repo, target):
    files, bn = P.build_repo_file_index(repo)
    assert P.rederive_file(target, repo, files, bn)["changed"] is True


def _run_undo(lines):
    """Run the undo command the snapshot printed, exactly as printed."""
    cmd = next(l for l in lines if l.startswith("To undo the migration: ")).split(": ", 1)[1]
    subprocess.run(cmd, shell=True, check=True, capture_output=True)


def _head(repo):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                          text=True, check=True).stdout.strip()


# --------------------------------------------------------------------------- #
# MIG-6: the copy, outside .claude/
# --------------------------------------------------------------------------- #
def test_a_gitignored_corpus_is_copied_outside_claude_and_restores_byte_for_byte(repo, memory_dir):
    """The copy used to land in .claude/memory.pre-cite2-<stamp>/, and the desktop app lays
    every new worktree down with a copy of .claude/ — on 2026-10-19, 53 worktrees each carried
    six spent backups. The copy carries no marker of its own, so copying it back is the restore."""
    target = _corpus(repo, ignored=True)
    before = _tree(memory_dir)
    r = P.take_snapshot(memory_dir, "20260714")
    assert r == {"path": os.path.join(repo, ".hippo", "snapshots", "memory.pre-cite2-20260714")}
    assert not [n for n in os.listdir(os.path.dirname(memory_dir)) if n.startswith("memory.pre-cite")]
    assert _tree(r["path"]) == before
    lines = P.snapshot_lines(r, memory_dir)
    assert lines[0] == f"snapshot: {r['path']}"
    _migrate(repo, target)
    assert _tree(memory_dir) != before
    _run_undo(lines)
    assert _tree(memory_dir) == before


def test_the_copy_is_invisible_to_git_even_when_the_corpus_has_its_own_gitignore(repo, memory_dir):
    """SEC-3 at the new location: the backup must not publish a corpus its project keeps
    private. The `*` marker sits in .hippo/snapshots/ itself, so a corpus .gitignore copied
    with the payload cannot replace it — before MIG-6 the marker sat inside the copy, where the
    corpus's own .gitignore overwrote it and the copy showed up in `git status`."""
    _corpus(repo, ignored=True)
    write_file(repo, ".claude/memory/.gitignore", "scratch/\n")
    dest = P.take_snapshot(memory_dir, "t1")["path"]
    for marker in (".hippo/.gitignore", ".hippo/snapshots/.gitignore"):
        with open(os.path.join(repo, marker), encoding="utf-8") as fh:
            assert fh.read().strip() == "*", marker
    porcelain = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
                               cwd=repo, capture_output=True, text=True, check=True).stdout
    assert ".hippo" not in porcelain and "pre-cite" not in porcelain, porcelain
    assert subprocess.run(["git", "check-ignore", "-q", os.path.join(dest, "m.md")],
                          cwd=repo).returncode == 0


def test_a_snapshot_dir_whose_marker_stopped_ignoring_everything_refuses_the_copy(repo, memory_dir):
    _corpus(repo, ignored=True)
    write_file(repo, ".hippo/snapshots/.gitignore", "*.tmp\n")
    with pytest.raises(PermissionError):
        P.take_snapshot(memory_dir, "t1")
    assert os.listdir(os.path.join(repo, ".hippo", "snapshots")) == [".gitignore"]


def test_a_corpus_outside_git_and_outside_claude_is_copied_beside_itself(tmp_path):
    md = tmp_path / "corpus"
    md.mkdir()
    (md / "m.md").write_text("---\nname: m\n---\nb\n", encoding="utf-8")
    r = P.take_snapshot(str(md), "t1")
    assert r == {"path": str(tmp_path / ".hippo" / "snapshots" / "memory.pre-cite2-t1")}


def test_snapshots_share_the_hippo_dir_the_derived_dirs_use(repo, memory_dir):
    """MIG-12: one hippo dir per project. v1.42.4 put the index, telemetry and pending queue
    in <project>/.hippo/; the backups join them there instead of a second .hippo-snapshots/."""
    _corpus(repo, ignored=True)
    root = snapshot_root(memory_dir)
    assert root == os.path.join(repo, ".hippo", "snapshots")
    assert os.path.dirname(root) == derived_home(memory_dir)
    P.take_snapshot(memory_dir, "t1")
    assert not os.path.exists(os.path.join(repo, ".hippo-snapshots"))
    assert sorted(os.listdir(os.path.join(repo, ".hippo"))) == [".gitignore", "snapshots"]


def test_a_new_snapshot_leaves_v1423_backups_where_they_are(repo, memory_dir):
    """Backups in .hippo-snapshots/ are a gitignored corpus's only undo: taking a new one never
    moves, merges or deletes them. Doctor names them with the mv (MIG-13)."""
    _corpus(repo, ignored=True)
    old = os.path.join(repo, ".hippo-snapshots")
    write_file(old, ".gitignore", "*\n")
    write_file(old, "memory.pre-cite2-t1/m.md", "old\n")
    before = _tree(old)
    dest = P.take_snapshot(memory_dir, "t1")["path"]  # the same stamp is free at the new root
    assert dest == os.path.join(repo, ".hippo", "snapshots", "memory.pre-cite2-t1")
    assert _tree(old) == before


def test_snapshot_refuses_to_overwrite_an_existing_one(repo, memory_dir):
    """The snapshot is the only undo a gitignored corpus has — never clobber one."""
    _corpus(repo, ignored=True)
    P.snapshot_corpus(memory_dir, "20260714")
    with pytest.raises(FileExistsError):
        P.snapshot_corpus(memory_dir, "20260714")


@pytest.mark.parametrize("stamp", ["", "a/../../.claude/x", "a/b", "..", ".hidden"])
def test_a_stamp_is_a_plain_label_so_the_copy_cannot_land_anywhere_else(repo, memory_dir, stamp):
    """The stamp names a directory: `a/../../.claude/x` would put the copy back in .claude/."""
    _corpus(repo, ignored=True)
    with pytest.raises(ValueError):
        P.take_snapshot(memory_dir, stamp)
    assert not os.path.exists(os.path.join(repo, ".hippo"))
    assert not os.path.exists(os.path.join(repo, ".claude", "x"))


def test_the_cli_prints_where_the_undo_is(repo, memory_dir, capsys):
    _corpus(repo, ignored=True)
    argv = ["--snapshot", "t1", "--memory-dir", memory_dir, "--repo-root", repo]
    assert P.main(argv) == 0
    out = capsys.readouterr().out
    dest = os.path.join(repo, ".hippo", "snapshots", "memory.pre-cite2-t1")
    assert out.startswith(f"snapshot: {dest}\n") and "To undo the migration: cp -Rp" in out, out
    assert P.main(argv) == 1  # a second take of the same stamp is refused
    assert "already exists" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# MIG-7: git is the undo for a committed, clean corpus
# --------------------------------------------------------------------------- #
def test_a_committed_clean_corpus_gets_its_commit_not_a_copy_and_git_restores_it(repo, memory_dir):
    target = _corpus(repo, ignored=False)
    before = _tree(memory_dir)
    r = P.take_snapshot(memory_dir, "t1")
    assert r == {"commit": _head(repo)}
    assert not os.path.exists(os.path.join(repo, ".hippo"))
    lines = P.snapshot_lines(r, memory_dir)
    assert lines[0].startswith("snapshot: not copied") and _head(repo)[:12] in lines[0]
    _migrate(repo, target)
    assert _tree(memory_dir) != before
    _run_undo(lines)
    assert _tree(memory_dir) == before


@pytest.mark.parametrize("state", ["edited", "staged", "untracked", "ignored"])
def test_a_corpus_git_cannot_fully_restore_is_still_copied(repo, memory_dir, state):
    """An uncommitted edit, a new memory, or a memory git ignores has no git undo."""
    target = _corpus(repo, ignored=False)
    if state in ("edited", "staged"):
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("more\n")
        if state == "staged":
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    elif state == "untracked":
        write_file(repo, ".claude/memory/new.md", "---\nname: new\n---\nb\n")
    else:
        write_file(repo, ".git/info/exclude", "secret.md\n")
        write_file(repo, ".claude/memory/secret.md", "---\nname: s\n---\nb\n")
    assert "path" in P.take_snapshot(memory_dir, "t1")


def test_ignored_files_a_migration_never_writes_do_not_force_a_copy(repo, memory_dir):
    _corpus(repo, ignored=False)
    write_file(repo, ".git/info/exclude", ".DS_Store\n.audit-reports/\n")
    write_file(repo, ".claude/memory/.DS_Store", "x")
    write_file(repo, ".claude/memory/.audit-reports/audit.md", "report\n")
    assert P.take_snapshot(memory_dir, "t1") == {"commit": _head(repo)}


# --------------------------------------------------------------------------- #
# MIG-8: doctor names the backups older plugins left in .claude/
# --------------------------------------------------------------------------- #
def _legacy(memory_dir, stamp):
    d = os.path.join(os.path.dirname(memory_dir), f"memory.pre-cite2-{stamp}")
    write_file(d, "m.md", "x" * 2000)
    return d


def test_doctor_reports_no_backups_in_claude(repo, memory_dir):
    _corpus(repo, ignored=True)
    r = check_snapshots(DoctorContext(memory_dir, repo))
    assert r == {"status": "ok", "message": "snapshots: none in .claude/."}
    P.take_snapshot(memory_dir, "t1")
    m = check_snapshots(DoctorContext(memory_dir, repo))["message"]
    assert "none in .claude/; 1 in" in m and os.path.join(repo, ".hippo", "snapshots") in m, m


def test_doctor_keeps_backups_while_the_derivation_is_unstamped(repo, memory_dir):
    _corpus(repo, ignored=True)
    _legacy(memory_dir, "a")
    _legacy(memory_dir, "b")
    r = check_snapshots(DoctorContext(memory_dir, repo))
    assert r["status"] == "ok", r
    m = r["message"]
    assert "2 pre-migration backup(s)" in m and "(4 KB, 2 files)" in m, m
    assert "memory.pre-cite2-a, memory.pre-cite2-b" in m and "kept" in m and "not stamped" in m
    assert os.path.join(repo, ".hippo", "snapshots") in m


def test_doctor_keeps_backups_while_a_stamped_corpus_still_has_worklist_items(repo, memory_dir):
    _corpus(repo, ignored=True)
    assert P.write_cite_derivation(memory_dir)  # stamped, yet m.md still derives differently
    _legacy(memory_dir, "a")
    r = check_snapshots(DoctorContext(memory_dir, repo))
    assert r["status"] == "ok" and "worklist still lists 1 memory" in r["message"], r


def test_doctor_calls_backups_spent_once_stamped_with_an_empty_worklist(repo, memory_dir):
    write_file(repo, "src/keep.py", "k = 1\n")
    c1 = git_commit(repo, "code", 1_700_000_000)
    write_file(repo, ".claude/memory/m.md",
               f'---\nname: M\ncited_paths: ["src/keep.py"]\nsource_commit: "{c1}"\n'
               "---\nbody cites src/keep.py\n")
    git_commit(repo, "memory", 1_700_000_001)
    assert P.rederive_worklist(memory_dir, repo) == []
    assert P.write_cite_derivation(memory_dir)
    for stamp in "abcde":
        _legacy(memory_dir, stamp)
    r = check_snapshots(DoctorContext(memory_dir, repo))
    assert r["status"] == "warn", r
    m = r["message"]
    assert "5 pre-migration backup(s)" in m and "(10 KB, 5 files)" in m and "(+1 more)" in m, m
    assert "spent" in m and "every worktree" in m and "Delete them" in m


# --------------------------------------------------------------------------- #
# MIG-13: doctor names v1.42.3's .hippo-snapshots/ with the mv into .hippo/snapshots/
# --------------------------------------------------------------------------- #
_SHELLS = [s for s in ("sh", "zsh", "bash") if shutil.which(s)]


@pytest.fixture
def spaced_repo(tmp_path):
    """A repo whose path has a space, so the printed command's quoting is exercised."""
    r = str(tmp_path / "my repo")
    os.makedirs(os.path.join(r, ".claude", "memory"))
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    write_file(r, ".gitignore", ".claude/memory/\n")
    write_file(r, ".claude/memory/m.md", "---\nname: m\n---\nbody\n")
    git_commit(r, "ignore the corpus", 1_700_000_000)
    return r, os.path.join(r, ".claude", "memory")


def _v1423(repo, *stamps):
    """Backups where v1.42.3 put them: .hippo-snapshots/ with its `*` marker."""
    old = os.path.join(repo, ".hippo-snapshots")
    write_file(old, ".gitignore", "*\n")
    for stamp in stamps:
        write_file(old, f"memory.pre-cite2-{stamp}/m.md", f"backup {stamp}\n")
    return old


def _run_printed(message, marker, shell):
    cmd = message.split(marker, 1)[1]
    subprocess.run([shell, "-c", cmd], check=True, capture_output=True)


def _porcelain(repo):
    return subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo,
                          capture_output=True, text=True, check=True).stdout


@pytest.mark.parametrize("shell", _SHELLS)
def test_doctor_names_v1423_backups_and_its_mv_folds_them_into_one_dir(spaced_repo, shell):
    repo, md = spaced_repo
    old = _v1423(repo, "a", "b")
    before = _tree(old)
    r = check_snapshots(DoctorContext(md, repo))
    assert r["status"] == "ok", r  # they do not ride into worktrees: named, never a warning
    m = r["message"]
    assert m.startswith("snapshots: none in .claude/. 2 backup(s) in " + old), m
    assert "where v1.42.3 put them — kept" in m and "one hippo dir: " in m, m
    _run_printed(m, "one hippo dir: ", shell)
    root = os.path.join(repo, ".hippo", "snapshots")
    assert not os.path.exists(old)
    assert _tree(root) == before  # one rename: every byte, the `*` marker included
    assert _porcelain(repo) == "", _porcelain(repo)
    m = check_snapshots(DoctorContext(md, repo))["message"]
    assert m == f"snapshots: none in .claude/; 2 in {root} (1 KB), outside .claude/, so worktrees do not carry them.", m


@pytest.mark.parametrize("shell", _SHELLS)
def test_a_backup_already_in_the_new_dir_by_name_stays_where_it_is(spaced_repo, shell):
    """After the update a new snapshot can take a stamp a v1.42.3 one already has. The printed
    command moves only what the new dir lacks, and keeps the old dir (and its marker) while a
    backup is left in it."""
    repo, md = spaced_repo
    old = _v1423(repo, "a", "b")
    P.take_snapshot(md, "b")
    root = os.path.join(repo, ".hippo", "snapshots")
    new_b = _tree(os.path.join(root, "memory.pre-cite2-b"))
    m = check_snapshots(DoctorContext(md, repo))["message"]
    assert f"memory.pre-cite2-b is also in {root} under the same name, so it stays" in m, m
    _run_printed(m, "one hippo dir: ", shell)
    assert _tree(os.path.join(root, "memory.pre-cite2-a")) == {"m.md": b"backup a\n"}
    assert _tree(os.path.join(root, "memory.pre-cite2-b")) == new_b
    assert sorted(os.listdir(old)) == [".gitignore", "memory.pre-cite2-b"]
    assert _porcelain(repo) == "", _porcelain(repo)
    m = check_snapshots(DoctorContext(md, repo))["message"]
    assert "1 backup(s) in" in m and "memory.pre-cite2-b is also in" in m, m
    assert "Move" not in m and "rm -r" not in m, m  # nothing left that the command could move


@pytest.mark.parametrize("shell", _SHELLS)
def test_an_empty_v1423_dir_is_named_and_its_command_removes_only_that(spaced_repo, shell):
    repo, md = spaced_repo
    old = _v1423(repo)
    m = check_snapshots(DoctorContext(md, repo))["message"]
    assert m == f"snapshots: none in .claude/. {old}, where v1.42.3 put them, holds no backups; delete it: " \
        + f"[ -z \"$(ls -A {shlex.quote(old)} | grep -vxF .gitignore)\" ] && rm -r {shlex.quote(old)}", m
    write_file(old, "notes.txt", "mine\n")  # anything but the marker blocks the rm
    with pytest.raises(subprocess.CalledProcessError):
        _run_printed(m, "delete it: ", shell)
    assert os.path.isfile(os.path.join(old, "notes.txt"))
    os.unlink(os.path.join(old, "notes.txt"))
    _run_printed(m, "delete it: ", shell)
    assert not os.path.exists(old)


def test_doctor_names_both_old_locations_on_one_line(repo, memory_dir):
    _corpus(repo, ignored=True)
    _legacy(memory_dir, "a")
    _v1423(repo, "b")
    r = check_snapshots(DoctorContext(memory_dir, repo))
    assert r["status"] == "ok", r
    m = r["message"]
    assert "1 pre-migration backup(s) in" in m and "kept: still the undo" in m, m
    assert "1 backup(s) in " + os.path.join(repo, ".hippo-snapshots") in m, m


# --------------------------------------------------------------------------- #
# MIG-14: /hippo:remove deletes the derived dirs by name, never the backups beside them
# --------------------------------------------------------------------------- #
def test_remove_deletes_the_derived_dirs_by_name_and_reports_the_backups():
    """v1.42.4's /hippo:remove said to remove `.hippo/` at the project root. With the backups
    in .hippo/snapshots/ that would delete a gitignored corpus's only undo."""
    skill = os.path.join(os.path.dirname(__file__), "..", "plugin", "skills", "remove", "SKILL.md")
    with open(skill, encoding="utf-8") as fh:
        text = fh.read()
    assert "remove `.hippo/` at the project root" not in text
    assert "never `.hippo/` as a whole" in text and "never `rm -rf .hippo/`" in text
    for kind in ("index", "telemetry", "pending"):
        assert f"`.hippo/{kind}/`" in text
    for backup in (".hippo/snapshots/", ".hippo-snapshots/", ".claude/memory.pre-cite*/"):
        assert backup in text, backup
    assert "report them, never delete them here" in text
