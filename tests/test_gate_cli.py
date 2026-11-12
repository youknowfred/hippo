"""GAT-2: ``hippo gate`` — the write gate's checks on the staged tree and on one commit, the
declared ``budgets``, and the pre-commit hook it installs.

Fixtures are synthetic. The commit-time behaviours of a hand-built floor checker are
reproduced by name (a numeric cap fails only when the change leaves it over AND grew it;
untracked files never count; the first commit is judged against nothing; a cap raised in the
same change is judged by the new cap).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

from memory import gate_cli as C

from .conftest import git_commit, write_file

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

_PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugin"))
FLOOR = "# Memory\n\n## User\n- [Short](short.md) — keep it tight\n"


def note(name, body="Plain body."):
    return f'---\nname: {name}\ndescription: "d"\ntype: project\n---\n{body}\n'


def _seed(repo, memory_dir, policy, n_notes=3, rounds=1):
    write_file(memory_dir, "MEMORY.md", FLOOR)
    write_file(memory_dir, "hippo.json", json.dumps(policy))
    for i in range(n_notes):
        write_file(memory_dir, f"n{i}.md", note(f"n{i}"))
    for i in range(rounds):
        write_file(memory_dir, f"r{i}-round.md", note(f"r{i}-round"))
    return git_commit(repo, "seed", 1_700_000_000)


def _stage(repo, *paths):
    subprocess.run(["git", "-C", repo, "add", "-A", *paths], check=True)


def _staged(memory_dir, capsys):
    rc = C.main(["--staged", "--memory-dir", memory_dir])
    return rc, capsys.readouterr().out


# --- budgets ------------------------------------------------------------------------------
def test_read_budgets_keeps_positive_ints_only():
    assert C.read_budgets({"files": 3, "floor_bytes": 0, "journals": True, "floor_lines": "9", "x": 4}) == {"files": 3}
    assert C.read_budgets(["files"]) == {}


def test_nothing_declared_is_not_checked(repo, memory_dir, capsys):
    _seed(repo, memory_dir, {"volatile_paths": []})
    write_file(memory_dir, "MEMORY.md", FLOOR + "- x\n" * 300)
    _stage(repo)
    rc, out = _staged(memory_dir, capsys)
    assert rc == 0 and "nothing to check" in out and out.count("\n") == 1


# --- estate behaviour: a numeric cap fails only over AND grown ----------------------------
def test_files_budget_over_and_grown_fails(repo, memory_dir, capsys):
    _seed(repo, memory_dir, {"budgets": {"files": 4}})
    write_file(memory_dir, "n9.md", note("n9"))
    _stage(repo)
    rc, out = _staged(memory_dir, capsys)
    assert rc == 1 and "memory files 4 -> 5, over the budget of 4" in out and "budgets.files" in out


def test_files_budget_standing_overage_held_passes(repo, memory_dir, capsys):
    _seed(repo, memory_dir, {"budgets": {"files": 2}})  # already 4 > 2
    write_file(memory_dir, "n0.md", note("n0", "edited"))
    _stage(repo)
    assert _staged(memory_dir, capsys)[0] == 0
    os.remove(os.path.join(memory_dir, "n1.md"))
    write_file(memory_dir, "n7.md", note("n7"))  # a swap holds the count
    _stage(repo)
    assert _staged(memory_dir, capsys)[0] == 0


def test_journals_budget_counts_declared_globs(repo, memory_dir, capsys):
    _seed(repo, memory_dir, {"budgets": {"journals": 2}, "journals": ["*-round.md"], "fold_digests": ["folded-*.md"]})
    write_file(memory_dir, "folded-2026.md", note("folded-2026"))
    _stage(repo)
    rc, out = _staged(memory_dir, capsys)
    assert rc == 0  # 1 round + 1 digest = 2, at the budget
    git_commit(repo, "digest", 1_700_000_100)
    write_file(memory_dir, "r5-round.md", note("r5-round"))
    _stage(repo)
    rc, out = _staged(memory_dir, capsys)
    assert rc == 1 and "journal files 2 -> 3" in out


def test_floor_bytes_and_lines_budgets(repo, memory_dir, capsys):
    _seed(repo, memory_dir, {"budgets": {"floor_bytes": 60, "floor_lines": 4}})
    write_file(memory_dir, "MEMORY.md", FLOOR + "- [More](more.md) — another\n")
    _stage(repo)
    rc, out = _staged(memory_dir, capsys)
    assert rc == 1 and "floor bytes" in out and "floor lines 4 -> 5, over the budget of 4" in out


def test_a_budget_raised_in_the_same_change_is_judged_by_the_new_budget(repo, memory_dir, capsys):
    _seed(repo, memory_dir, {"budgets": {"files": 4}})
    write_file(memory_dir, "n9.md", note("n9"))
    write_file(memory_dir, "hippo.json", json.dumps({"budgets": {"files": 5}}))
    _stage(repo)
    assert _staged(memory_dir, capsys)[0] == 0


# --- estate behaviour: only the index counts ----------------------------------------------
def test_untracked_files_are_never_counted(repo, memory_dir, capsys):
    _seed(repo, memory_dir, {"budgets": {"files": 4}})
    write_file(memory_dir, "untracked.md", note("u"))
    rc, out = _staged(memory_dir, capsys)
    assert rc == 0 and "0 corpus file(s) changed" in out


def test_the_first_commit_is_judged_against_nothing(repo, memory_dir, capsys):
    write_file(memory_dir, "MEMORY.md", FLOOR)
    write_file(memory_dir, "hippo.json", json.dumps({"budgets": {"files": 1}}))
    write_file(memory_dir, "a.md", note("a"))
    write_file(memory_dir, "b.md", note("b"))
    _stage(repo)
    rc, out = _staged(memory_dir, capsys)
    assert rc == 1 and "memory files 0 -> 2" in out


# --- the same checks as the write gate ----------------------------------------------------
def test_floor_lint_and_body_checks_run_at_commit(repo, memory_dir, capsys):
    _seed(repo, memory_dir, {"floor_lint": {"banned_re": r"DONE-NOW|#\d{3,}"}})
    write_file(memory_dir, "n0.md", note("n0", "Closed in #4521."))
    _stage(repo)
    rc, out = _staged(memory_dir, capsys)
    assert rc == 1 and "n0.md: body lines matching the banned pattern 0 -> 1" in out
    git_commit(repo, "debt", 1_700_000_200)
    write_file(memory_dir, "n0.md", note("n0", "Closed in #4521, see the tracker."))
    _stage(repo)
    assert _staged(memory_dir, capsys)[0] == 0  # standing debt, reworded


def test_commit_time_size_edge_is_the_read_limit_not_the_warn_line(repo, memory_dir, capsys):
    _seed(repo, memory_dir, {"floor_lint": {"max_line": 300}})
    row = "- [P](p.md) — " + "pointer row padding " * 6 + "\n"  # ~135 B
    write_file(memory_dir, "MEMORY.md", FLOOR + row * 150)  # ~20 KB, 155 lines
    _stage(repo)
    assert _staged(memory_dir, capsys)[0] == 0  # over 17,500 but under 25,000
    git_commit(repo, "big", 1_700_000_300)
    write_file(memory_dir, "MEMORY.md", FLOOR + row * 190)  # past 25,000 B, under 200 lines
    _stage(repo)
    rc, out = _staged(memory_dir, capsys)
    assert rc == 1 and "read limit" in out


def test_commit_replays_one_commit_against_its_first_parent(repo, memory_dir, capsys):
    _seed(repo, memory_dir, {"budgets": {"files": 4}})
    write_file(memory_dir, "n9.md", note("n9"))
    grow = git_commit(repo, "grow", 1_700_000_400)
    os.remove(os.path.join(memory_dir, "n9.md"))
    shrink = git_commit(repo, "shrink", 1_700_000_500)
    assert C.main(["--commit", grow, "--memory-dir", memory_dir]) == 1
    assert f"commit {grow[:9]}" in capsys.readouterr().out
    assert C.main(["--commit", shrink, "--memory-dir", memory_dir]) == 0
    assert C.main(["--commit", "no-such-rev", "--memory-dir", memory_dir]) == 2


def test_usage_errors_exit_two(memory_dir):
    with pytest.raises(SystemExit) as e:
        C.main(["--memory-dir", memory_dir])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        C.main(["--staged", "--yes", "--memory-dir", memory_dir])
    assert e.value.code == 2


def test_outside_git_is_a_quiet_pass(tmp_path, capsys):
    md = tmp_path / "loose" / ".claude" / "memory"
    md.mkdir(parents=True)
    assert C.main(["--staged", "--memory-dir", str(md)]) == 0
    assert "nothing to check" in capsys.readouterr().out


# --- the installer --------------------------------------------------------------------------
def _hook_path(repo):
    out = subprocess.run(["git", "-C", repo, "rev-parse", "--git-path", "hooks"], capture_output=True, text=True)
    p = out.stdout.strip()
    return os.path.join(p if os.path.isabs(p) else os.path.join(repo, p), "pre-commit")


def test_install_previews_then_writes_and_is_idempotent(repo, memory_dir, capsys):
    _seed(repo, memory_dir, {"budgets": {"files": 4}})
    target = _hook_path(repo)
    assert C.main(["--install", "--memory-dir", memory_dir]) == 0
    out = capsys.readouterr().out
    assert target in out and "gate --staged" in out and "Nothing was written" in out
    assert not os.path.exists(target)
    assert C.main(["--install", "--yes", "--memory-dir", memory_dir]) == 0
    assert os.access(target, os.X_OK) and C.HOOK_MARKER in open(target).read()
    assert C.main(["--install", "--yes", "--memory-dir", memory_dir]) == 0
    assert "already current" in capsys.readouterr().out


def test_install_refuses_a_foreign_hook(repo, memory_dir, capsys):
    _seed(repo, memory_dir, {"budgets": {"files": 4}})
    target = _hook_path(repo)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w") as fh:
        fh.write("#!/bin/sh\necho mine\n")
    assert C.main(["--install", "--yes", "--memory-dir", memory_dir]) == 1
    assert "left alone" in capsys.readouterr().out and open(target).read() == "#!/bin/sh\necho mine\n"


def test_install_follows_core_hooks_path(repo, memory_dir):
    _seed(repo, memory_dir, {"budgets": {"files": 4}})
    subprocess.run(["git", "-C", repo, "config", "core.hooksPath", ".githooks"], check=True)
    assert C.main(["--install", "--yes", "--memory-dir", memory_dir]) == 0
    assert os.path.exists(os.path.join(repo, ".githooks", "pre-commit"))


def _commit_env(tmp_path, with_hippo=True):
    b = tmp_path / "hookbin"
    b.mkdir(exist_ok=True)
    if with_hippo and not (b / "hippo").exists():
        (b / "hippo").write_text(
            f"#!/bin/sh\nPYTHONPATH={_PLUGIN_ROOT} exec {sys.executable} -m memory.cli \"$@\"\n"
        )
        os.chmod(b / "hippo", 0o755)
    env = {k: v for k, v in os.environ.items()}
    env["PATH"] = f"{b}:/usr/bin:/bin"
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e.x", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e.x")
    return env


def test_the_installed_hook_fails_only_a_worsening_commit(repo, memory_dir, tmp_path):
    _seed(repo, memory_dir, {"budgets": {"files": 4}})
    assert C.main(["--install", "--yes", "--memory-dir", memory_dir]) == 0
    env = _commit_env(tmp_path)
    write_file(repo, "app.py", "x = 1\n")
    _stage(repo)
    ok = subprocess.run(["git", "-C", repo, "commit", "-qm", "code only"], env=env, capture_output=True, text=True)
    assert ok.returncode == 0
    write_file(memory_dir, "n9.md", note("n9"))
    _stage(repo)
    bad = subprocess.run(["git", "-C", repo, "commit", "-qm", "grow"], env=env, capture_output=True, text=True)
    assert bad.returncode != 0 and "memory files 4 -> 5" in bad.stdout + bad.stderr


def test_the_installed_hook_never_bricks_commits_without_hippo(repo, memory_dir, tmp_path):
    _seed(repo, memory_dir, {"budgets": {"files": 4}})
    hook = C.hook_text(str(tmp_path / "gone" / "hippo"), ".claude/memory")
    target = _hook_path(repo)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w") as fh:
        fh.write(hook)
    os.chmod(target, 0o755)
    write_file(memory_dir, "n9.md", note("n9"))
    _stage(repo)
    p = subprocess.run(["git", "-C", repo, "commit", "-qm", "grow"], env=_commit_env(tmp_path, with_hippo=False),
                       capture_output=True, text=True)
    assert p.returncode == 0
