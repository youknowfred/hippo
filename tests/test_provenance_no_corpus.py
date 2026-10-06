"""MIG-3/4/5 — the provenance verbs in a git repo with no corpus.

A git repo nobody ran setup in (including a nested repo or submodule that resolves its own
corpus rather than its parent's) died on ``os.listdir`` of the missing dir:
``--rederive-worklist`` and ``--stamp-derivation`` (MIG-3) and the backfill itself, bare or
with ``--refresh`` / ``--dry-run`` (MIG-4), printed a traceback, and ``--snapshot`` (MIG-5)
left a half-made snapshot dir behind. Kept out of ``test_provenance.py``, which sits
at its module-size pin. The MCP and tend-queue halves live beside their own tools
(``test_mcp_setup_tools.py``, ``test_tend_queue.py``).
"""

from __future__ import annotations

import os

import pytest

from memory import provenance as P


def test_rederive_worklist_of_a_repo_with_no_corpus_is_empty(repo):
    md = os.path.join(repo, ".claude", "memory")
    assert P.rederive_worklist(md, repo) == []
    assert not os.path.exists(os.path.join(repo, ".claude"))  # read-only: nothing created


def test_rederive_verbs_in_a_repo_with_no_corpus_say_so_in_one_line(repo, monkeypatch, capsys):
    """The reported repro: CLAUDE_PROJECT_DIR at a corpus-less git repo. Both verbs answer
    in one plain line naming the next step — doctor's "no corpus at …" wording — and the
    stamp writes no `.format` into a directory that does not exist."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    md, _root = P.resolve_dirs()
    assert os.path.realpath(md) == os.path.realpath(os.path.join(repo, ".claude", "memory"))
    for verb in ("--rederive-worklist", "--stamp-derivation"):
        assert P.main([verb]) == 0, verb
        out = capsys.readouterr().out.strip()
        assert "\n" not in out, f"{verb}: {out!r}"
        assert "no corpus" in out and "/hippo:setup" in out, f"{verb}: {out!r}"
    assert not os.path.exists(os.path.join(repo, ".claude"))


def test_rederive_worklist_still_raises_a_real_io_error_on_an_existing_corpus(repo, memory_dir):
    """The no-corpus answer is for a corpus that is ABSENT. One that exists but cannot be
    read is a real failure and must stay loud, not pass for an empty worklist."""
    try:
        os.chmod(memory_dir, 0o000)
        with pytest.raises(PermissionError):
            P.rederive_worklist(memory_dir, repo)
    finally:
        os.chmod(memory_dir, 0o755)


def test_backfill_corpus_of_a_repo_with_no_corpus_is_empty(repo):
    """MIG-4: the backfill reached ``os.listdir`` of the missing dir through
    ``_iter_memory_files``. An ABSENT corpus is nothing to backfill, and nothing is created."""
    md = os.path.join(repo, ".claude", "memory")
    assert P.backfill_corpus(md, repo) == []
    assert P.backfill_corpus(md, repo, dry_run=True, refresh=True) == []
    assert not os.path.exists(os.path.join(repo, ".claude"))


def test_backfill_verbs_in_a_repo_with_no_corpus_say_so_in_one_line(repo, monkeypatch, capsys):
    """MIG-4, the reported repro: no flags, ``--refresh`` and ``--dry-run`` each printed a
    traceback. Each now answers in MIG-3's one line, naming what there is nothing to do."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    for argv in ([], ["--refresh"], ["--dry-run"]):
        assert P.main(argv) == 0, argv
        out = capsys.readouterr().out.strip()
        assert "\n" not in out, f"{argv}: {out!r}"
        assert "no corpus" in out and "nothing to backfill" in out and "/hippo:setup" in out, out
    assert not os.path.exists(os.path.join(repo, ".claude"))


def test_backfill_corpus_still_raises_a_real_io_error_on_an_existing_corpus(repo, memory_dir):
    """MIG-4: an unreadable corpus is a real failure, not an empty backfill."""
    try:
        os.chmod(memory_dir, 0o000)
        with pytest.raises(PermissionError):
            P.backfill_corpus(memory_dir, repo)
    finally:
        os.chmod(memory_dir, 0o755)


def test_snapshot_of_a_repo_with_no_corpus_refuses_and_creates_nothing(repo, monkeypatch, capsys):
    """MIG-5: the snapshot wrote its self-ignoring ``.gitignore`` BEFORE reading the corpus,
    so in a repo with no corpus it left ``.claude/memory.pre-cite2-<stamp>/`` behind and then
    reported "snapshot FAILED". It now refuses in MIG-3's one line and creates nothing."""
    md = os.path.join(repo, ".claude", "memory")
    with pytest.raises(FileNotFoundError):
        P.snapshot_corpus(md, "t1")
    assert not os.path.exists(os.path.join(repo, ".claude"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    assert P.main(["--snapshot", "t1"]) == 1  # no snapshot was taken, so `&& migrate` stops
    out = capsys.readouterr().out.strip()
    assert "\n" not in out, out
    assert "no corpus" in out and "nothing to snapshot" in out and "/hippo:setup" in out, out
    assert "FAILED" not in out
    assert not os.path.exists(os.path.join(repo, ".claude"))


def test_snapshot_of_an_unreadable_corpus_still_fails_and_leaves_no_debris(repo, memory_dir):
    """MIG-5: an existing corpus that cannot be read still raises. Because the corpus is now
    read before anything is created, that failure leaves no half-made snapshot dir either."""
    try:
        os.chmod(memory_dir, 0o000)
        with pytest.raises(PermissionError):
            P.snapshot_corpus(memory_dir, "t1")
    finally:
        os.chmod(memory_dir, 0o755)
    assert not os.path.exists(os.path.join(os.path.dirname(memory_dir), "memory.pre-cite2-t1"))
