"""CAP-7: one end-to-end integration test for the capture → approval loop.

The pieces are unit-tested apart (test_capture.py pins the SessionEnd write + the structural
approval gate; test_new_memory.py pins --check routing + write_memory); nothing exercised the
WHOLE loop the way a real session does. This walks it once, through the actual functions the
hooks and the /hippo:consolidate skill call, in order:

    SessionEnd capture  →  SessionStart nudge  →  --check routing  →  approved write  →  drain

and asserts the invariant that gives capture its safety story: NOTHING lands in the corpus until
the agent explicitly approves a candidate, and once it does, the approved memory is a real,
recall-ready file and the queue drains.
"""

from __future__ import annotations

import os

from memory import capture as C
from memory import new_memory as N
from memory import session_start as SS
from memory.telemetry import default_telemetry_dir

from .conftest import git_commit, write_file


def _corpus_snapshot(md):
    snap = {}
    for dirpath, _dn, files in os.walk(md):
        for f in files:
            p = os.path.join(dirpath, f)
            with open(p, "rb") as fh:
                snap[os.path.relpath(p, md)] = fh.read()
    return snap


def test_capture_to_approval_end_to_end(repo, monkeypatch):
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    write_file(md, "MEMORY.md", "# Memory Index\n\n## Project\n")
    # A real prior session: it recalled nothing useful and left a durable code change.
    write_file(repo, "src/pipeline.py", "def run():\n    return 'v1'\n")
    git_commit(repo, "init", 1_700_000_000)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)
    monkeypatch.chdir(repo)

    td = default_telemetry_dir(md)
    from memory import telemetry as T

    T.log_episode(
        ["nothing-relevant"],
        query="how does the pipeline batch its writes",
        repo_root=repo,
        telemetry_dir=td,
        session_id="sessE2E",
    )
    # The session then changed the code — the durable fact worth capturing.
    write_file(repo, "src/pipeline.py", "def run():\n    # batch writes at end, never per-item\n    return 'v2'\n")
    git_commit(repo, "batch writes", 1_700_000_100)

    corpus_before = _corpus_snapshot(md)

    # 1) SessionEnd capture — one seed lands in the gitignored pending queue, corpus untouched.
    seed_path = C.write_session_capture("sessE2E", reason="clear", memory_dir=md, repo_root=repo)
    assert seed_path is not None
    assert _corpus_snapshot(md) == corpus_before, "capture must not touch the corpus"
    assert C.pending_count(memory_dir=md) == 1

    # 2) SessionStart nudge — the next session is told the queue awaits review.
    nudge = SS.pending_capture_producer(md, repo)
    assert nudge and "1 pending" in nudge and "/hippo:consolidate" in nudge

    # 3) The drain reads the seed and drafts a candidate fact from its provenance.
    seed = C.read_pending(memory_dir=md)[0]
    assert seed["session_id"] == "sessE2E"
    assert any("pipeline.py" in p for p in seed.get("changed_paths", []))
    candidate = "pipeline-batches-writes-at-end"
    desc = "the pipeline batches its writes at end-of-run, never per-item"

    # 4) --check routing (CAP-3, dry run) — the candidate is novel, so it routes to `add`.
    decision = N.check_candidate(candidate, desc, "project", memory_dir=md, repo_root=repo)
    assert decision["route"] == "add", "a novel candidate must route to add, not review"
    assert _corpus_snapshot(md) == corpus_before, "the --check dry run must write nothing"

    # 5) The agent approves → write_memory lands a real recall-ready file in .claude/memory/.
    result = N.write_memory(
        candidate, desc, "project",
        body="Confirmed at src/pipeline.py: run() batches writes at end.",
        memory_dir=md, repo_root=repo,
        rationale="from session sessE2E; as of HEAD",
    )
    assert result.get("error") is None
    landed = os.path.join(md, f"{candidate}.md")
    assert os.path.isfile(landed), "the approved candidate must land in the corpus"
    assert candidate not in corpus_before  # it genuinely did not exist before approval

    # 6) Drain the seed — the queue empties and the nudge self-clears.
    assert C.discard_pending(seed_path)
    assert C.pending_count(memory_dir=md) == 0
    assert SS.pending_capture_producer(md, repo) is None

    # The loop closed: exactly ONE new memory (the approved one), written only after approval.
    added = set(_corpus_snapshot(md)) - set(corpus_before)
    assert added == {f"{candidate}.md"}
