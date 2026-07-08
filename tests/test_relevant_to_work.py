"""SIG-1: the relevant-to-current-work SessionStart producer (diff-seeded).

The FIRST positive producer — surfaces memories whose ``cited_paths`` intersect the
session's uncommitted working-tree diff, BEFORE the first prompt. Membership is the exact
cited_paths intersection (so a clean tree emits nothing and appearance is deterministic
regardless of the recall backend); recall is exercised only as an ordering signal. Uses a
real git repo for the diff, following the test_capture/test_staleness convention.
"""

from __future__ import annotations

import os

import memory.session_start as S
from memory.staleness import RunContext

from .conftest import git_commit, write_file


def _mem(md, name, cited, sc, desc="a note"):
    """Write a project memory citing ``cited`` (repo-relative paths), stamped at ``sc``."""
    cp = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    write_file(
        md,
        f"{name}.md",
        f'---\nname: {name}\ndescription: "{desc}"\ntype: project\n'
        f'cited_paths: {cp}\nsource_commit: "{sc}"\n---\nbody about {name}\n',
    )


def _setup(repo):
    """A repo with two committed source files + an empty corpus dir; returns (memory_dir, sha)."""
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md, exist_ok=True)
    write_file(repo, "src/app.py", "print('v1')\n")
    write_file(repo, "src/util.py", "x = 1\n")
    sc = git_commit(repo, "init", 1_700_000_000)
    return md, sc


def test_dirty_branch_surfaces_cited_memory(repo):
    """A modified tracked file whose path a memory cites -> that memory surfaces, path named."""
    md, sc = _setup(repo)
    _mem(md, "app-note", ["src/app.py"], sc, "how the app entrypoint works")
    _mem(md, "util-note", ["src/util.py"], sc, "util helpers")
    write_file(repo, "src/app.py", "print('v2')\n")  # uncommitted modification

    out = S.relevant_to_work_producer(md, repo)
    assert out is not None
    assert "app-note" in out and "src/app.py" in out
    assert "util-note" not in out  # its cited file was untouched
    assert out.startswith("🎯")  # the positive block, not a ⚠ warning


def test_clean_tree_is_silent(repo):
    """No working-tree changes -> no block (no false positive on a clean checkout)."""
    md, sc = _setup(repo)
    _mem(md, "app-note", ["src/app.py"], sc)
    assert S.relevant_to_work_producer(md, repo) is None


def test_untracked_new_file_is_matched(repo):
    """A NEW (untracked) file a memory cites is matched — _git_changed_paths unions untracked."""
    md, sc = _setup(repo)
    _mem(md, "new-note", ["src/brand_new.py"], sc, "the new module")
    write_file(repo, "src/brand_new.py", "# created this session\n")  # untracked, non-ignored

    out = S.relevant_to_work_producer(md, repo)
    assert out is not None
    assert "new-note" in out and "src/brand_new.py" in out


def test_changed_file_that_no_memory_cites_is_silent(repo):
    """A changed file with no citing memory -> silent (precise; only cited-path matches surface)."""
    md, sc = _setup(repo)
    _mem(md, "app-note", ["src/app.py"], sc)
    write_file(repo, "src/util.py", "x = 2\n")  # changed, but nothing cites it
    assert S.relevant_to_work_producer(md, repo) is None


def test_uses_run_context_changed_paths_when_present(repo):
    """The producer trusts ctx.changed_paths (computed once in _build_run_context)."""
    md, sc = _setup(repo)
    _mem(md, "app-note", ["src/app.py"], sc)
    # ctx claims app.py changed even though the tree is clean -> producer uses ctx, no git diff
    out = S.relevant_to_work_producer(md, repo, RunContext(changed_paths=["src/app.py"]))
    assert out is not None and "app-note" in out
    # ctx present but empty (clean tree) -> silent; it must NOT fall back to a live git diff
    assert S.relevant_to_work_producer(md, repo, RunContext(changed_paths=[])) is None


def test_orders_more_matches_first_when_recall_abstains(repo, monkeypatch):
    """When recall abstains, ordering falls back to match count (more cited-changed = higher)."""
    md, sc = _setup(repo)
    write_file(repo, "src/other.py", "y = 1\n")
    git_commit(repo, "add other", 1_700_000_100)
    _mem(md, "one-cite", ["src/app.py"], sc)
    _mem(md, "two-cite", ["src/app.py", "src/other.py"], sc)
    write_file(repo, "src/app.py", "print('v2')\n")
    write_file(repo, "src/other.py", "y = 2\n")

    monkeypatch.setattr("memory.recall.recall", lambda *a, **k: [])  # force abstention
    out = S.relevant_to_work_producer(md, repo)
    assert out is not None
    assert out.index("two-cite") < out.index("one-cite")


def test_cap_and_overflow_note(repo, monkeypatch):
    """More matches than the cap -> only the cap is listed, with an honest '…and N more'."""
    md, sc = _setup(repo)
    changed = []
    for i in range(S._MAX_RELEVANT_ITEMS + 3):
        rel = f"src/mod{i}.py"
        write_file(repo, rel, "x = 0\n")
        changed.append(rel)
    git_commit(repo, "many modules", 1_700_000_200)
    for i in range(S._MAX_RELEVANT_ITEMS + 3):
        _mem(md, f"note{i}", [f"src/mod{i}.py"], sc)
        write_file(repo, f"src/mod{i}.py", "x = 1\n")  # touch each

    monkeypatch.setattr("memory.recall.recall", lambda *a, **k: [])
    out = S.relevant_to_work_producer(md, repo)
    assert out is not None
    listed = out.count("  • ")
    assert listed == S._MAX_RELEVANT_ITEMS
    assert "…and 3 more." in out


def test_wired_into_producers_and_flows_through_build_context(repo):
    """End-to-end: the real PRODUCERS list + dispatcher emits the positive block."""
    md, sc = _setup(repo)
    _mem(md, "app-note", ["src/app.py"], sc, "entrypoint details")
    write_file(repo, "src/app.py", "print('v2')\n")

    ctx = S.build_context(md, repo)  # trust gate open via HIPPO_TRUST_ALL in conftest
    assert "🎯 Relevant to your current work" in ctx
    assert "app-note" in ctx
    assert any(label == "relevant_to_work" for label, _fn in S.PRODUCERS)


def test_non_git_or_bogus_dir_never_raises(tmp_path):
    """A nonexistent memory_dir / non-git repo yields None, never an exception (inv2 spirit)."""
    bogus = str(tmp_path / "nope")
    assert S.relevant_to_work_producer(bogus, bogus) is None
