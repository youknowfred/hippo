"""OWN-1 — a git home for the user tier (closes #158).

``hippo setup --user-tier`` previews, then (``--yes``) writes the tier's ``.gitignore`` block
and runs ``git init`` (no commit, no remote); machine-local memories carry
``machine_local: true`` and stay out of git while recall still reads them; doctor reports the
tier's posture from local state; a project link to a machine-local memory reads "machine-local
on another machine" in a clone that lacks it, never "dangling".

Hermetic: every tier is a tmp dir (conftest points ``HIPPO_USER_MEMORY_DIR`` at an absent one
by default), ``GIT_CEILING_DIRECTORIES`` stops git from finding a repository above tmp, and
the "upstream" is a local bare repository. Nothing here reads the real home directory.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from memory import doctor as D
from memory import lint_links as L
from memory import setup_cli as S
from memory import user_tier_git as U
from memory.doctor_checks_user_tier import check_user_tier

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True,
        env={**os.environ, **_GIT_ENV},
    ).stdout


def _mem(name, desc, *, machine_local=False, body="Body.", mtype="feedback"):
    flag = "  machine_local: true\n" if machine_local else ""
    return f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: {mtype}\n{flag}---\n{body}\n'


@pytest.fixture
def tier(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    d = tmp_path / "user-tier"
    d.mkdir()
    (d / "MEMORY.md").write_text("# Agent Memory (user tier)\n")
    (d / "tabs.md").write_text(_mem("tabs", "indent with tabs"))
    (d / "this-laptop.md").write_text(_mem("this-laptop", "the laptop's VPN quirk", machine_local=True))
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", str(d))
    return d


def _setup(*flags):
    return S.main(["--user-tier", *flags])


# --------------------------------------------------------------------------- #
# The managed block
# --------------------------------------------------------------------------- #
def test_with_block_keeps_every_outside_line_and_is_idempotent():
    text = "mine.txt\n# a comment\n"
    once = U.with_block(text, ["b.md", "a.md"])
    assert once.startswith("mine.txt\n# a comment\n\n" + U.BLOCK_OPEN)
    assert once.count(U.BLOCK_OPEN) == 1 and "/a.md\n/b.md\n" in once
    assert U.with_block(once, ["a.md", "b.md"]) == once
    shrunk = U.with_block(once + "after.txt\n", ["a.md"])
    assert "/b.md" not in shrunk and shrunk.startswith("mine.txt\n") and shrunk.endswith("after.txt\n")
    assert U._block_entries(shrunk) == ["a.md"]


def test_block_ignores_hippo_transients():
    block = U.render_block([])
    assert "*.tmp.*" in block and ".*.staging-*" in block


# --------------------------------------------------------------------------- #
# hippo setup --user-tier
# --------------------------------------------------------------------------- #
def test_preview_writes_nothing_and_says_what_it_would_do(tier, capsys):
    assert _setup() == 0
    out = capsys.readouterr().out
    assert "Nothing was written." in out
    assert "git init" in out and "makes no commit and adds no remote" in out
    assert "/this-laptop.md" in out and "1 marked machine-local" in out
    assert not (tier / ".git").exists() and not (tier / ".gitignore").exists()


def test_yes_makes_a_local_repository_with_no_commit_and_no_remote(tier, capsys):
    assert _setup("--yes") == 0
    out = capsys.readouterr().out
    assert "now a local git repository (no commit yet, no remote)" in out
    assert (tier / ".git").is_dir()
    assert _git(tier, "remote").strip() == ""
    assert subprocess.run(["git", "-C", str(tier), "rev-parse", "--verify", "-q", "HEAD"],
                          capture_output=True).returncode != 0, "no commit is made"
    status = _git(tier, "status", "--porcelain", "--untracked-files=all")
    assert "tabs.md" in status and ".gitignore" in status
    assert "this-laptop.md" not in status, "a machine-local memory is never visible to git"


def test_a_second_run_on_its_own_repository_does_nothing(tier, capsys):
    _setup("--yes")
    capsys.readouterr()
    before = (tier / ".gitignore").read_bytes()
    assert _setup("--yes") == 0
    assert "already a git repository" in capsys.readouterr().out
    assert (tier / ".gitignore").read_bytes() == before


def test_an_existing_repository_only_gets_its_ignore_list_refreshed(tier, capsys):
    _setup("--yes")
    (tier / "second.md").write_text(_mem("second", "another local fact", machine_local=True))
    capsys.readouterr()
    assert _setup() == 0
    out = capsys.readouterr().out
    assert "already a git repository" in out and "/second.md" in out and "Nothing was written." in out
    assert "/second.md" not in (tier / ".gitignore").read_text()
    assert _setup("--yes") == 0
    assert "/second.md" in (tier / ".gitignore").read_text()
    assert "second.md" not in _git(tier, "status", "--porcelain", "--untracked-files=all")


def test_a_tier_inside_another_repository_is_left_alone(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    dots = tmp_path / "dotfiles"
    d = dots / "hippo-memory"
    d.mkdir(parents=True)
    (d / "a.md").write_text(_mem("a", "alpha"))
    _git(tmp_path, "init", "-q", str(dots))
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", str(d))
    assert _setup("--yes") == 0
    out = capsys.readouterr().out
    assert "sits inside the git repository" in out and "Nothing was written." in out
    assert not (d / ".git").exists() and not (d / ".gitignore").exists()


def test_no_user_tier_means_nothing_to_do(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", str(tmp_path / "none"))
    assert _setup("--yes") == 0
    assert "No user tier on this machine yet" in capsys.readouterr().out
    assert not (tmp_path / "none").exists()


def test_off_is_refused(tier, capsys):
    assert _setup("--off", "--yes") == 2
    assert not (tier / ".git").exists()


def test_status_prints_the_posture_and_writes_nothing(tier, capsys):
    assert _setup("--status") == 0
    assert "is not a git repository" in capsys.readouterr().out
    assert not (tier / ".gitignore").exists()


def test_bare_setup_lists_the_new_setting(capsys):
    assert S.main([]) == 2
    assert "--user-tier" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Doctor's posture line, in every state
# --------------------------------------------------------------------------- #
def _line(tmp_path):
    return check_user_tier(D.DoctorContext(str(tmp_path / "proj"), str(tmp_path)))


def test_no_user_tier_prints_no_doctor_line(tmp_path):
    assert _line(tmp_path) is None


def test_posture_not_a_repository(tier, tmp_path):
    r = _line(tmp_path)
    assert r["status"] == "ok"
    assert "holds 2 memories and is not a git repository" in r["message"]
    assert "never pushes or pulls" in r["message"]


def test_posture_uncommitted_then_clean_then_behind_and_ahead(tier, tmp_path):
    _setup("--yes")
    r = _line(tmp_path)
    assert r["status"] == "ok"
    assert "is a git repository; no commits yet; 3 uncommitted changes" in r["message"]

    _git(tier, "add", "-A")
    _git(tier, "commit", "-qm", "user tier")
    r = _line(tmp_path)
    assert r["status"] == "ok" and "clean; no upstream configured" in r["message"]

    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    _git(tier, "remote", "add", "origin", str(bare))
    branch = _git(tier, "rev-parse", "--abbrev-ref", "HEAD").strip()
    _git(tier, "push", "-q", "-u", "origin", branch)
    assert "up to date with origin/" in _line(tmp_path)["message"]

    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(bare), str(other))
    (other / "new.md").write_text(_mem("new", "from the other machine"))
    _git(other, "add", "-A")
    _git(other, "commit", "-qm", "elsewhere")
    _git(other, "push", "-q")
    assert "behind" not in _line(tmp_path)["message"], "no fetch: only what the user fetched"
    _git(tier, "fetch", "-q")
    r = _line(tmp_path)
    assert r["status"] == "warn"
    assert f"behind origin/{branch} by 1 commit as of your last fetch" in r["message"]

    _git(tier, "merge", "-q", "--ff-only")
    (tier / "local.md").write_text(_mem("local", "made here"))
    _git(tier, "add", "-A")
    _git(tier, "commit", "-qm", "here")
    r = _line(tmp_path)
    assert r["status"] == "ok" and "1 local commit not pushed" in r["message"]


def test_a_tracked_machine_local_memory_is_named_with_its_remedy(tier, tmp_path):
    _setup("--yes")
    _git(tier, "add", "-A")
    _git(tier, "commit", "-qm", "user tier")
    (tier / "tabs.md").write_text(_mem("tabs", "indent with tabs", machine_local=True))
    r = _line(tmp_path)
    assert r["status"] == "warn"
    assert "1 machine-local memory is already committed: tabs" in r["message"]
    assert "rm --cached" in r["message"] and "keeps the file" in r["message"]
    assert (tier / "tabs.md").exists(), "hippo never untracks or deletes"
    assert "tabs.md" in _git(tier, "ls-files")


def test_a_hand_flagged_memory_not_yet_ignored_is_named(tier, tmp_path):
    _setup("--yes")
    (tier / "fresh.md").write_text(_mem("fresh", "hand made", machine_local=True))
    r = _line(tmp_path)
    assert r["status"] == "warn"
    assert "not in the ignore list yet: fresh" in r["message"]
    assert "hippo setup --user-tier --yes" in r["message"]


def test_doctor_renders_one_line_per_check_when_a_user_tier_exists(tier, tmp_path):
    ctx = D.DoctorContext(str(tmp_path / "proj"), str(tmp_path), plugin_data="", plugin_root="")
    labels = [label for label, _ in D.run_checks(ctx)]
    assert labels == [label for label, _ in D.CHECKS if label != "floor_budget"]  # NAT-3: no budget, lean floor
    assert "user tier:" in D.render(ctx)


# --------------------------------------------------------------------------- #
# Saving a machine-local memory (CLI + MCP); recall still reads it
# --------------------------------------------------------------------------- #
def test_cli_machine_local_save_lands_in_the_user_tier_ignored(tier, capsys, monkeypatch):
    from memory import new_memory as N

    monkeypatch.setenv("HIPPO_DISABLE", "dense")
    _setup("--yes")
    rc = N.main(["home-printer", "the printer at home needs the duplex driver",
                 "--type", "user", "--machine-local", "--no-links"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "machine-local" in out
    text = (tier / "home-printer.md").read_text()
    assert "machine_local: true" in text
    assert "/home-printer.md" in (tier / ".gitignore").read_text()
    assert "home-printer" not in _git(tier, "status", "--porcelain", "--untracked-files=all")


def test_machine_local_outside_the_user_tier_is_refused(tmp_path):
    from memory.new_memory import write_memory

    res = write_memory("x", "d", "project", "b", memory_dir=str(tmp_path / "m"),
                       tier="project", machine_local=True)
    assert res["created"] is False and "user tier only" in res["error"]


def test_mcp_new_memory_machine_local(tier, monkeypatch):
    from memory.mcp_tools_core import _tool_new_memory

    monkeypatch.setenv("HIPPO_DISABLE", "dense")
    proj = tier.parent / "proj" / ".claude" / "memory"
    proj.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tier.parent / "proj"))
    out = _tool_new_memory({"name": "desk-lamp", "description": "the desk lamp's smart plug id",
                            "type": "user", "machine_local": True})
    assert "machine-local" in out, out
    assert "machine_local: true" in (tier / "desk-lamp.md").read_text()
    assert "/desk-lamp.md" in (tier / ".gitignore").read_text()
    assert not (proj / "desk-lamp.md").exists()


def test_both_mcp_schemas_offer_machine_local():
    from memory.mcp_schemas import _TOOLS as V1
    from memory.mcp_schemas_v2 import V2_TOOLS as V2

    for tools in (V1, V2):
        tool = next(t for t in tools if t["name"] == "new_memory")
        assert tool["inputSchema"]["properties"]["machine_local"]["type"] == "boolean"
        assert "machine_local" not in tool["inputSchema"]["required"]


def test_recall_returns_a_machine_local_memory(tier, tmp_path, monkeypatch):
    from memory import build_index as B
    from memory import recall as R

    monkeypatch.setenv("HIPPO_DISABLE", "dense")
    _setup("--yes")
    proj = tmp_path / "proj" / ".claude" / "memory"
    proj.mkdir(parents=True)
    (proj / "build.md").write_text(_mem("build", "the build pipeline", mtype="project"))
    idx = str(tmp_path / "proj" / ".hippo" / "index")
    B.build_index(str(proj), idx)
    B.build_index(str(tier), B.default_index_dir(str(tier)))
    hits = R.recall("laptop VPN quirk", k=5, memory_dir=str(proj), index_dir=idx)
    hit = next(h for h in hits if h["name"] == "this-laptop")
    assert hit["corpus"] == "user"
    assert "this-laptop" not in _git(tier, "status", "--porcelain", "--untracked-files=all")


# --------------------------------------------------------------------------- #
# Cross-tier links on a clone that lacks the machine-local memory
# --------------------------------------------------------------------------- #
def _project(tmp_path, links):
    proj = tmp_path / "proj" / ".claude" / "memory"
    proj.mkdir(parents=True)
    body = "See " + ", ".join(f"[[{t}]]" for t in links) + "."
    (proj / "src.md").write_text(_mem("src", "the source memory", mtype="project", body=body))
    return proj


def test_a_clone_names_a_machine_local_target_instead_of_dangling(tmp_path, monkeypatch, capsys):
    clone = tmp_path / "clone-of-user-tier"
    clone.mkdir()
    (clone / "tabs.md").write_text(_mem("tabs", "indent with tabs"))
    (clone / ".gitignore").write_text(U.render_block(["this-laptop.md"]))
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", str(clone))
    proj = _project(tmp_path, ["tabs", "this-laptop", "nowhere"])

    report = L.lint(str(proj))
    assert [d["target"] for d in report["dangling"]] == ["nowhere"]
    tiers = {d["target"]: d["tier"] for d in report["cross_tier"]}
    assert tiers == {"tabs": "user", "this-laptop": L.MACHINE_LOCAL_TIER}

    assert L.main(["--memory-dir", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "(user tier, machine-local on another machine)" in out
    assert "[[tabs]] (user tier)" in out
    line = L.health_line(report) or ""
    assert "nowhere" in line and "this-laptop" not in line and "1 dangling" in line
    from memory.links import graph_audit

    rot = {r["target"] for r in graph_audit(str(proj))["rot"]}
    assert rot == {"nowhere"}


def test_a_present_machine_local_memory_reads_as_plain_user_tier(tier, tmp_path):
    _setup("--yes")
    proj = _project(tmp_path, ["this-laptop"])
    report = L.lint(str(proj))
    assert report["dangling"] == []
    assert [d["tier"] for d in report["cross_tier"]] == ["user"]


def test_no_user_tier_leaves_lint_unchanged(tmp_path, capsys):
    proj = _project(tmp_path, ["this-laptop"])
    report = L.lint(str(proj))
    assert [d["target"] for d in report["dangling"]] == ["this-laptop"]
    assert report["cross_tier"] == []
