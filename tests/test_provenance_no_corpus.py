"""MIG-3 — the re-derivation verbs in a git repo with no corpus.

A git repo nobody ran setup in (including a nested repo or submodule that resolves its own
corpus rather than its parent's) died on ``os.listdir`` of the missing dir:
``--rederive-worklist`` and ``--stamp-derivation`` printed a traceback. Kept out of ``test_provenance.py``, which sits
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
