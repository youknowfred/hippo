"""Tests for memory/capture.py — the CAP-2 SessionEnd draft-capture pass.

The sharpest invariant in this release: capture is automated UP TO the approval gate, never
past it. These tests pin that structurally — a full capture pass over a session lands seeds
ONLY in the gitignored pending queue and leaves the corpus byte-identical — plus the seed's
provenance (session, commits, queries) and the queue's gitignored-ness.
"""

from __future__ import annotations

import inspect
import json
import os

import pytest

from memory import capture as C
from memory import session_start as SS
from memory import telemetry as T
from memory.telemetry import default_telemetry_dir

from .conftest import git_commit, write_file


def _seed_episode(md, repo, session_id, names, query):
    """Log one real episode for ``session_id`` (records the current HEAD as its watermark)."""
    td = default_telemetry_dir(md)
    T.log_episode(names, query=query, repo_root=repo, telemetry_dir=td, session_id=session_id)


def _corpus_snapshot(md):
    snap = {}
    for dirpath, _dn, files in os.walk(md):
        for f in files:
            p = os.path.join(dirpath, f)
            with open(p, "rb") as fh:
                snap[os.path.relpath(p, md)] = fh.read()
    return snap


# --------------------------------------------------------------------------- #
# The structural approval gate — capture NEVER writes the corpus
# --------------------------------------------------------------------------- #
def test_capture_never_writes_to_the_corpus(repo):
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    # A real corpus with a real memory + floor — the thing that must stay untouched.
    write_file(md, "existing.md", '---\nname: existing\ndescription: "x"\nmetadata:\n  type: project\n---\nbody\n')
    write_file(md, "MEMORY.md", "# Memory Index\n\n## User\n")
    write_file(repo, "src/app.py", "print('v1')\n")
    git_commit(repo, "init", 1_700_000_000)
    _seed_episode(md, repo, "sessABC", ["existing"], "how do we deploy the web service")
    write_file(repo, "src/app.py", "print('v2')\n")  # uncommitted change after the watermark

    before = _corpus_snapshot(md)
    path = C.write_session_capture("sessABC", reason="clear", memory_dir=md, repo_root=repo)
    after = _corpus_snapshot(md)

    assert path is not None
    # The corpus is byte-identical: capture added/modified/deleted NOTHING under .claude/memory/.
    assert before == after, "capture pass mutated the corpus — approval gate breached"
    # The seed landed in the SEPARATE pending queue, not the corpus.
    assert path.startswith(C.default_pending_dir(md) + os.sep)
    assert not path.startswith(md + os.sep)


def test_capture_module_imports_no_corpus_writer():
    """Belt to the runtime test: no IMPORT of the corpus writer, no CALL to it (AST, not prose).

    Checks the real import graph and call graph — the docstrings deliberately NAME new_memory /
    write_memory to explain the gate, so a naive string match would false-positive on the very
    explanation of the guarantee.
    """
    import ast

    tree = ast.parse(inspect.getsource(C))
    imported = set()
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute):
                called.add(fn.attr)
            elif isinstance(fn, ast.Name):
                called.add(fn.id)
    assert not any("new_memory" in m for m in imported), "capture imports the corpus-writing module"
    assert "write_memory" not in called, "capture calls the corpus writer"
    assert not hasattr(C, "write_memory"), "capture must not expose a corpus writer in its namespace"


def test_pending_dir_is_gitignored(repo):
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    git_commit(repo, "init", 1_700_000_000)
    _seed_episode(md, repo, "s1", ["m"], "q")
    C.write_session_capture("s1", memory_dir=md, repo_root=repo)
    gi = os.path.join(C.default_pending_dir(md), ".gitignore")
    assert os.path.exists(gi)
    with open(gi) as fh:
        assert fh.read().strip() == "*", "pending queue must self-ignore (SEC-3)"


# --------------------------------------------------------------------------- #
# Provenance: session, commits, queries
# --------------------------------------------------------------------------- #
def test_seed_carries_full_provenance(repo):
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    write_file(repo, "src/app.py", "print('v1')\n")
    wm = git_commit(repo, "init", 1_700_000_000)
    _seed_episode(md, repo, "sess-XYZ", ["deploy_runbook", "canary_steps"], "how do we deploy")
    _seed_episode(md, repo, "sess-XYZ", ["deploy_runbook"], "what is the rollback plan")
    write_file(repo, "src/app.py", "print('v2')\n")
    write_file(repo, "docs/deploy.md", "steps\n")

    path = C.write_session_capture("sess-XYZ", reason="logout", memory_dir=md, repo_root=repo)
    seed = json.load(open(path))

    assert seed["kind"] == "session-capture"
    assert seed["session_id"] == "sess-XYZ"            # session
    assert seed["head_commit"] == wm                    # commit watermark
    assert seed["head"] and len(seed["head"]) >= 7      # HEAD at capture (commit range end)
    assert "src/app.py" in seed["changed_paths"]        # diff since the watermark
    assert "docs/deploy.md" in seed["changed_paths"]
    # queries (deduped, order-preserving) + recalled names (deduped)
    assert seed["query_previews"] == ["how do we deploy", "what is the rollback plan"]
    assert seed["recalled_names"] == ["deploy_runbook", "canary_steps"]
    assert seed["reason"] == "logout"
    assert seed["episode_count"] == 2
    # GRW-1/GRW-4 seed schema 2: verbatim evidence + value label + the decisions field.
    assert seed["schema"] == 2
    assert "+print('v2')" in seed["diff_hunks"]         # the tracked change, VERBATIM
    assert seed["hunks_secret_flagged"] is False
    assert seed["decisions"] == []
    assert isinstance(seed["salience"], dict) and "score" in seed["salience"]


def test_no_episodes_writes_no_seed(repo):
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    git_commit(repo, "init", 1_700_000_000)
    # A session with a DIFFERENT id has episodes, but our session has none.
    _seed_episode(md, repo, "other", ["m"], "q")
    path = C.write_session_capture("target-session", memory_dir=md, repo_root=repo)
    assert path is None, "no episodes for this session → no seed, not an empty one"
    assert C.pending_count(memory_dir=md) == 0


def test_non_git_repo_still_captures_queries(tmp_path):
    # No git → no watermark/diff, but the episode replay (names + queries) is still worth a seed.
    md = str(tmp_path / ".claude" / "memory")
    os.makedirs(md)
    _seed_episode(md, None, "s", ["m1"], "a non-git query")
    path = C.write_session_capture("s", memory_dir=md, repo_root=str(tmp_path))
    assert path is not None
    seed = json.load(open(path))
    assert seed["changed_paths"] == []
    assert seed["diff_hunks"] == ""                     # hunks degrade like changed_paths
    assert seed["hunks_secret_flagged"] is False
    assert "a non-git query" in seed["query_previews"]


# --------------------------------------------------------------------------- #
# GRW-1: verbatim diff-hunk evidence + seed salience
# --------------------------------------------------------------------------- #
def test_hunks_capture_tracked_and_untracked_verbatim(repo):
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    write_file(repo, "src/app.py", "print('v1')\n")
    git_commit(repo, "init", 1_700_000_000)
    _seed_episode(md, repo, "s-hunks", ["m"], "q")
    write_file(repo, "src/app.py", "print('v2')\n")          # tracked, modified
    write_file(repo, "src/brand_new.py", "NEW_CONSTANT = 7\n")  # untracked — the high-value case

    seed = json.load(open(C.write_session_capture("s-hunks", memory_dir=md, repo_root=repo)))
    hunks = seed["diff_hunks"]
    assert "-print('v1')" in hunks and "+print('v2')" in hunks   # tracked change, verbatim
    assert "+NEW_CONSTANT = 7" in hunks                          # untracked file, verbatim
    assert "brand_new.py" in hunks


def test_hunks_byte_cap_cuts_on_a_line_boundary(repo):
    write_file(repo, "big.txt", "start\n")
    wm = git_commit(repo, "init", 1_700_000_000)
    write_file(repo, "big.txt", "start\n" + ("x" * 60 + "\n") * 200)  # ~12KB tracked diff

    hunks = C._git_diff_hunks(wm, repo, [], max_bytes=1_000)
    assert len(hunks.encode("utf-8")) <= 1_000 + 64, "cap must bound the evidence"
    assert "… (diff truncated at 1000 bytes)" in hunks, "truncation must be legible"
    body = hunks.rsplit("\n… (diff truncated", 1)[0]
    # The cut lands on a line boundary: the last kept line is a COMPLETE diff line, not a
    # mid-line fragment of the 60-char x-run.
    assert body.splitlines()[-1] == "+" + "x" * 60


def test_hunks_skip_binary_sections(repo):
    write_file(repo, "a.txt", "one\n")
    wm = git_commit(repo, "init", 1_700_000_000)
    write_file(repo, "a.txt", "one\ntwo\n")
    with open(os.path.join(repo, "blob.bin"), "wb") as fh:
        fh.write(b"\x00\x01\x02binary-payload")

    hunks = C._git_diff_hunks(wm, repo, ["blob.bin"])
    assert "+two" in hunks, "the text change survives"
    assert "blob.bin" not in hunks, "binary sections carry no reviewable hunk — dropped"
    assert "Binary files" not in hunks and "GIT binary patch" not in hunks


def test_hunks_secret_lint_flags_the_seed(repo):
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    git_commit(repo, "init", 1_700_000_000)
    _seed_episode(md, repo, "s-secret", ["m"], "q")
    # An AWS-shaped key in an untracked file — exactly the evidence a seed would quote.
    write_file(repo, "oops.env", "AWS_KEY=AKIA" + "A" * 16 + "\n")

    seed = json.load(open(C.write_session_capture("s-secret", memory_dir=md, repo_root=repo)))
    assert "AKIA" in seed["diff_hunks"], "the hunk itself stays in the gitignored seed"
    assert seed["hunks_secret_flagged"] is True, "…but the seed is loudly flagged for the drain"


def test_salience_orders_pending_best_first_and_labels_trivial(repo):
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    git_commit(repo, "init", 1_700_000_000)
    # Session A: trivial — one query, no changes, nothing landed.
    _seed_episode(md, repo, "a-trivial", [], "one lonely query")
    C.write_session_capture("a-trivial", memory_dir=md, repo_root=repo)
    # Session B: high value — a NEW file appears before its capture.
    _seed_episode(md, repo, "b-busy", ["m"], "how do we ship this")
    write_file(repo, "src/shipit.py", "ship = True\n")
    C.write_session_capture("b-busy", memory_dir=md, repo_root=repo)

    seeds = C.read_pending(memory_dir=md)
    assert [s["session_id"] for s in seeds] == ["b-busy", "a-trivial"], "best-first ordering"
    assert seeds[0]["salience"]["score"] > seeds[1]["salience"]["score"]
    assert seeds[0]["salience"]["new_files"] >= 1
    # Both seeds stayed in the queue — salience labels and orders, it NEVER gates/prunes.
    assert len(seeds) == 2
    assert seeds[1]["salience"]["trivial"] is True
    listing = C._format_listing(seeds)
    assert "(trivial session)" in listing
    assert "value:" in listing


def test_trivial_seed_scores_zero_and_producer_labels_it(repo):
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    git_commit(repo, "init", 1_700_000_000)
    _seed_episode(md, repo, "quiet", [], "just one question")
    C.write_session_capture("quiet", memory_dir=md, repo_root=repo)

    seeds = C.read_pending(memory_dir=md)
    assert seeds[0]["salience"] == {
        "score": 0,
        "new_files": 0,
        "commit_landed": False,
        "distinct_queries": 1,
        "abstained_queries": 0,
        "trivial": True,
    }
    out = SS.pending_capture_producer(md, repo)
    assert out and "1 pending" in out and "(1 trivial)" in out



def test_read_and_count_pending(repo):
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    git_commit(repo, "init", 1_700_000_000)
    _seed_episode(md, repo, "s1", ["a"], "q1")
    _seed_episode(md, repo, "s2", ["b"], "q2")
    C.write_session_capture("s1", memory_dir=md, repo_root=repo)
    C.write_session_capture("s2", memory_dir=md, repo_root=repo)
    assert C.pending_count(memory_dir=md) == 2
    seeds = C.read_pending(memory_dir=md)
    assert {s["session_id"] for s in seeds} == {"s1", "s2"}
    # discard one → count drops
    assert C.discard_pending(seeds[0]["_path"])
    assert C.pending_count(memory_dir=md) == 1


def test_refired_sessionend_overwrites_not_duplicates(repo):
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    git_commit(repo, "init", 1_700_000_000)
    _seed_episode(md, repo, "s1", ["a"], "q1")
    C.write_session_capture("s1", memory_dir=md, repo_root=repo)
    C.write_session_capture("s1", memory_dir=md, repo_root=repo)  # same session again
    assert C.pending_count(memory_dir=md) == 1, "one seed per session, not one per fire"


# --------------------------------------------------------------------------- #
# SessionStart producer legibility
# --------------------------------------------------------------------------- #
def test_pending_producer_surfaces_queue(repo):
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    git_commit(repo, "init", 1_700_000_000)
    assert SS.pending_capture_producer(md, repo) is None  # empty → silent
    _seed_episode(md, repo, "s1", ["a"], "q1")
    C.write_session_capture("s1", memory_dir=md, repo_root=repo)
    out = SS.pending_capture_producer(md, repo)
    assert out and "/hippo:consolidate" in out and "1 pending" in out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_main_from_hook_reads_stdin(repo, monkeypatch, capsys):
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    git_commit(repo, "init", 1_700_000_000)
    _seed_episode(md, repo, "hook-sess", ["a"], "q")
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)
    monkeypatch.chdir(repo)
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": "hook-sess", "reason": "clear"})))
    rc = C.main(["--from-hook"])
    assert rc == 0
    assert C.pending_count(memory_dir=md) == 1
    assert capsys.readouterr().out == ""  # silent on the hook path


def test_main_list(repo, monkeypatch, capsys):
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    git_commit(repo, "init", 1_700_000_000)
    _seed_episode(md, repo, "s1", ["a"], "q1")
    C.write_session_capture("s1", memory_dir=md, repo_root=repo)
    rc = C.main(["--list", "--memory-dir", md])
    assert rc == 0
    assert "1 pending capture" in capsys.readouterr().out


def test_main_discard_drains_one_seed(repo, capsys):
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    git_commit(repo, "init", 1_700_000_000)
    _seed_episode(md, repo, "s1", ["a"], "q1")
    path = C.write_session_capture("s1", memory_dir=md, repo_root=repo)
    rc = C.main(["--discard", path])
    assert rc == 0
    assert "discarded" in capsys.readouterr().out
    assert C.pending_count(memory_dir=md) == 0
