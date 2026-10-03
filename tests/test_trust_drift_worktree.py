"""OBS-6: the dominant trust-drift path, repro first.

Classified on 2026-10-03 across three trusted corpora: em-growth-labs drifted 28 files,
every one of them arriving on the main checkout through a squash-merged PR from a linked
worktree after consent; hippo drifted 1 (an uncommitted hand edit); Skyline 0. Tracing the
writers: hippo's own verbs ran in the worktree sessions with the corpus pinned to the
worktree copy (``HIPPO_CORPUS_ROOT``), and ``record_authored_write`` folded each write into
the consent record keyed on THAT tree — which has none — so the fold was a silent no-op.
When the branch merged, the main tree's record had never seen the bytes hippo itself wrote.

Authorship is consent (SEC-6's fold), never git authorship: only bytes a hippo verb wrote
join the main tree's baseline. A hand edit in the worktree still drifts after the merge.
"""

from __future__ import annotations

import os
import subprocess

from memory import trust

from .conftest import git_commit, write_file

_MEM = "---\nname: m\ndescription: d\nmetadata:\n  type: feedback\n---\n{body}\n"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _trusted_main_with_worktree(tmp_path, repo, memory_dir, monkeypatch):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    write_file(memory_dir, "m.md", _MEM.format(body="original"))
    write_file(memory_dir, "other.md", _MEM.format(body="untouched"))
    git_commit(repo, "corpus", 1_700_000_000)
    assert trust.mark_trusted(repo, memory_dir, origin="init")
    wt = str(tmp_path / "wt")
    _git(repo, "worktree", "add", "-q", "-b", "feature", wt)
    return wt, os.path.join(wt, ".claude", "memory")


def _merge_feature(repo, wt, message):
    _git(wt, "add", "-A")
    _git(wt, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", message)
    _git(repo, "merge", "-q", "--ff-only", "feature")


def test_bytes_hippo_wrote_in_a_worktree_do_not_drift_after_the_merge(tmp_path, repo, memory_dir, monkeypatch):
    wt, wt_md = _trusted_main_with_worktree(tmp_path, repo, memory_dir, monkeypatch)
    path = os.path.join(wt_md, "m.md")
    write_file(wt_md, "m.md", _MEM.format(body="corrected by a reviewed verdict"))
    # A hippo verb run with the corpus pinned to the worktree folds with the worktree's
    # own root, exactly as the field runs did.
    trust.record_authored_write(wt_md, path, wt)
    _merge_feature(repo, wt, "verdict")

    drift = trust.untrusted_changes(repo, memory_dir)
    assert drift["baseline"] and drift["changed"] == [], drift


def test_a_hand_edit_in_a_worktree_still_drifts_after_the_merge(tmp_path, repo, memory_dir, monkeypatch):
    wt, wt_md = _trusted_main_with_worktree(tmp_path, repo, memory_dir, monkeypatch)
    write_file(wt_md, "m.md", _MEM.format(body="edited by hand, never through hippo"))
    _merge_feature(repo, wt, "hand edit")
    assert trust.untrusted_changes(repo, memory_dir)["changed"] == ["m"]


def test_bytes_that_differ_from_what_hippo_wrote_still_drift(tmp_path, repo, memory_dir, monkeypatch):
    wt, wt_md = _trusted_main_with_worktree(tmp_path, repo, memory_dir, monkeypatch)
    path = os.path.join(wt_md, "m.md")
    write_file(wt_md, "m.md", _MEM.format(body="what hippo wrote"))
    trust.record_authored_write(wt_md, path, wt)
    write_file(wt_md, "m.md", _MEM.format(body="then changed by hand"))
    _merge_feature(repo, wt, "mixed")
    assert trust.untrusted_changes(repo, memory_dir)["changed"] == ["m"]


def test_a_worktree_of_an_untrusted_main_tree_extends_nothing(tmp_path, repo, memory_dir, monkeypatch):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    write_file(memory_dir, "m.md", _MEM.format(body="original"))
    git_commit(repo, "corpus", 1_700_000_000)
    wt = str(tmp_path / "wt")
    _git(repo, "worktree", "add", "-q", "-b", "feature", wt)
    wt_md = os.path.join(wt, ".claude", "memory")
    write_file(wt_md, "m.md", _MEM.format(body="new"))
    assert trust.record_authored_write(wt_md, os.path.join(wt_md, "m.md"), wt) is False
    assert not trust.is_trusted(repo)
