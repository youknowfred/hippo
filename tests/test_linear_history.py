"""CON-5: a push to main that carries a merge commit fails a CI lane and names the commit.

main's ruleset requires linear history, but `gh pr merge --admin` goes around it, and
nothing looked at main's shape afterwards. The lane runs `.github/scripts/
check_linear_history.sh <before> <after>` on every push to main.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest
import yaml

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SCRIPT = os.path.join(_ROOT, ".github", "scripts", "check_linear_history.sh")
_ZERO = "0" * 40

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(repo, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


def _commit(repo, name):
    with open(os.path.join(repo, name), "w", encoding="utf-8") as fh:
        fh.write(name + "\n")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"add {name}")
    return _git(repo, "rev-parse", "HEAD")


def _check(repo, before, after):
    return subprocess.run(["bash", _SCRIPT, before, after], cwd=repo, capture_output=True, text=True)


@pytest.fixture
def history(tmp_path):
    """main: a - b - (merge of a side branch) - d."""
    repo = str(tmp_path / "r")
    os.makedirs(repo)
    _git(repo, "init", "-q", "-b", "main")
    a = _commit(repo, "a")
    b = _commit(repo, "b")
    _git(repo, "checkout", "-q", "-b", "side", a)
    _commit(repo, "s")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    merge = _git(repo, "rev-parse", "HEAD")
    d = _commit(repo, "d")
    return repo, a, b, merge, d


def test_a_linear_push_passes(history):
    repo, a, b, _merge, _d = history
    r = _check(repo, a, b)
    assert r.returncode == 0 and "no merge commits" in r.stdout


def test_a_push_carrying_a_merge_commit_fails_and_names_it(history):
    repo, _a, b, merge, d = history
    r = _check(repo, b, d)
    assert r.returncode == 1
    assert merge[:7] in r.stdout and "merge side" in r.stdout
    assert "squash-merge" in r.stdout


def test_commits_after_the_merge_pass_on_their_own(history):
    repo, _a, _b, merge, d = history
    assert _check(repo, merge, d).returncode == 0


def test_a_new_branch_or_a_force_push_checks_the_new_tip_alone(history):
    repo, _a, _b, merge, d = history
    assert _check(repo, _ZERO, d).returncode == 0
    assert _check(repo, _ZERO, merge).returncode == 1
    assert _check(repo, "1" * 40, d).returncode == 0  # the old tip no longer exists


def test_a_missing_tip_is_an_error_not_a_pass(history):
    repo = history[0]
    assert _check(repo, _ZERO, "2" * 40).returncode == 2
    assert _check(repo, "", "").returncode == 2


def test_the_lane_runs_the_script_on_pushes_to_main_only():
    with open(os.path.join(_ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8") as fh:
        job = yaml.safe_load(fh)["jobs"]["linear-history"]
    assert job["if"] == "github.event_name == 'push'"
    checkout, step = job["steps"]
    assert checkout["with"]["fetch-depth"] == 0  # the range needs history
    assert step["run"] == 'bash .github/scripts/check_linear_history.sh "$BEFORE" "$AFTER"'
    assert step["env"] == {"BEFORE": "${{ github.event.before }}", "AFTER": "${{ github.sha }}"}
