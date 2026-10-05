"""TND-5: capture-queue hygiene — subagent stops fold into the parent's seed, old seeds move
to ``expired/`` (by age, by session count, or past the cap) and come back on request, and the
queue's inflow and drain are counted in the daily rollups. Nothing here ever deletes a seed
that a human did not discard."""

from __future__ import annotations

import io
import json
import os
import time

from memory import capture as C
from memory import mcp_tools_consolidate as MC
from memory import session_start_signals as SSS
from memory import telemetry as T
from memory import telemetry_rollup as TR
from memory.doctor_checks_env import DoctorContext
from memory.doctor_checks_kpi import check_capture_queue
from memory.telemetry import default_telemetry_dir

from .conftest import git_commit


def _corpus(repo):
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    git_commit(repo, "init", 1_700_000_000)
    return md


def _episode(md, repo, sid, query, names=("m",)):
    T.log_episode(list(names), query=query, repo_root=repo,
                  telemetry_dir=default_telemetry_dir(md), session_id=sid)


def _queue_counts(md):
    return TR.summarize(TR.read_rollups(default_telemetry_dir(md)))["queue"]


def _raw_seed(pd, name, *, captured_at, session_id=None, score=0, **extra):
    os.makedirs(pd, exist_ok=True)
    seed = {
        "schema": C._SEED_SCHEMA, "kind": "session-capture",
        "session_id": session_id or name, "salience": {"score": score, "trivial": score == 0},
        "captured_at": captured_at, **extra,
    }
    path = os.path.join(pd, f"capture-{name}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(seed, fh)
    return path


def _sessions(td, n, after, prefix="later"):
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "recall_events.jsonl"), "a", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({"session_id": f"{prefix}-{i}", "names": [], "ts": after + 1 + i}) + "\n")


# --------------------------------------------------------------------------- #
# Fold: a SubagentStop lands on the parent session's one seed
# --------------------------------------------------------------------------- #
def test_subagent_stop_folds_into_the_parent_seed(repo):
    md = _corpus(repo)
    _episode(md, repo, "parent", "how does the deploy retry")
    p1 = C.write_session_capture("parent", reason="subagent-stop", memory_dir=md, repo_root=repo)
    p2 = C.write_session_capture("parent", reason="subagent-stop", memory_dir=md, repo_root=repo)
    p3 = C.write_session_capture("parent", reason="clear", memory_dir=md, repo_root=repo)
    assert p1 == p2 == p3
    assert C.pending_count(memory_dir=md) == 1
    seed = json.load(open(p3))
    assert seed["subagent_stops"] == 2
    assert seed["reason"] == "clear"
    assert seed["folds"] == 2
    # A subagent stop after SessionEnd never relabels the session's own reason.
    C.write_session_capture("parent", reason="subagent-stop", memory_dir=md, repo_root=repo)
    seed = json.load(open(p3))
    assert seed["reason"] == "clear" and seed["subagent_stops"] == 3
    counts = _queue_counts(md)
    assert counts.get("captured") == 1 and counts.get("folded") == 3


def test_fold_keeps_evidence_the_rotated_buffer_lost(repo):
    md = _corpus(repo)
    _episode(md, repo, "s", "first question about caching")
    path = C.write_session_capture("s", reason="subagent-stop", memory_dir=md, repo_root=repo)
    # The episode buffer rotates under its byte cap: the first episode is gone.
    buf = os.path.join(default_telemetry_dir(md), "episode_buffer.jsonl")
    open(buf, "w").close()
    _episode(md, repo, "s", "second question about eviction", names=("n",))
    C.write_session_capture("s", reason="clear", memory_dir=md, repo_root=repo)
    seed = json.load(open(path))
    assert seed["query_previews"] == [
        "first question about caching", "second question about eviction"
    ]
    assert seed["recalled_names"] == ["m", "n"]
    assert seed["episode_count"] == 1
    assert "first_captured_at" in seed


def test_fresh_seed_shape_is_unchanged_by_the_fold(repo):
    md = _corpus(repo)
    _episode(md, repo, "one", "q")
    seed = json.load(open(C.write_session_capture("one", memory_dir=md, repo_root=repo)))
    for key in ("subagent_stops", "folds", "first_captured_at"):
        assert key not in seed


def test_hook_payload_names_the_parent_session(repo, monkeypatch, capsys):
    md = _corpus(repo)
    _episode(md, repo, "parent-sid", "what broke the build")
    payload = {
        "session_id": "parent-sid", "hook_event_name": "SubagentStop",
        "agent_id": "agent-1", "agent_type": "Explore",
        "agent_transcript_path": "/x/parent-sid/subagents/agent-agent-1.jsonl",
    }
    for _ in range(2):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        C.main(["--from-hook", "--reason", "subagent-stop", "--memory-dir", md, "--repo-root", repo])
    seeds = C.read_pending(memory_dir=md)
    assert len(seeds) == 1 and seeds[0]["session_id"] == "parent-sid"
    assert seeds[0]["subagent_stops"] == 2


def test_payload_without_session_id_falls_back_to_the_transcript_stem():
    assert C._payload_session_id({"transcript_path": "/p/abc-123.jsonl"}) == "abc-123"
    assert C._payload_session_id({"session_id": " s1 ", "transcript_path": "/p/x.jsonl"}) == "s1"
    assert C._payload_session_id({}) is None


# --------------------------------------------------------------------------- #
# Expiry: by age, by session count, and the cap — moved, never deleted
# --------------------------------------------------------------------------- #
def test_expiry_by_age_moves_the_seed_and_counts_it(repo):
    md = _corpus(repo)
    pd = C.default_pending_dir(md)
    now = time.time()
    _raw_seed(pd, "old", captured_at=now - 15 * 86400)
    _raw_seed(pd, "young", captured_at=now - 13 * 86400)
    assert C.expire_pending(pd, memory_dir=md, now=now) == 1
    assert [s["session_id"] for s in C.read_pending(pd)] == ["young"]
    expired = C.read_expired(pd)
    assert [s["session_id"] for s in expired] == ["old"]
    assert expired[0]["expired_reason"] == "age"
    assert os.path.isfile(os.path.join(C.expired_dir(pd), "capture-old.json"))
    assert _queue_counts(md).get("expired") == 1


def test_expiry_by_session_count(repo):
    md = _corpus(repo)
    pd = C.default_pending_dir(md)
    td = default_telemetry_dir(md)
    now = time.time()
    _raw_seed(pd, "quiet", captured_at=now - 3600)
    _sessions(td, 19, now - 3600)
    assert C.expire_pending(pd, memory_dir=md, now=now) == 0
    _sessions(td, 1, now - 1000, prefix="one-more")
    assert C.expire_pending(pd, memory_dir=md, now=now) == 1
    assert C.read_expired(pd)[0]["expired_reason"] == "sessions"


def test_cap_prune_moves_to_expired_never_deletes(repo):
    md = _corpus(repo)
    pd = C.default_pending_dir(md)
    now = time.time()
    for i, score in enumerate((5, 1, 0)):
        _raw_seed(pd, f"s{i}", captured_at=now - 10 + i, score=score)
    assert C.prune_pending(pd, max_seeds=1, memory_dir=md) == 2
    assert [s["session_id"] for s in C.read_pending(pd)] == ["s0"]
    assert sorted(s["session_id"] for s in C.read_expired(pd)) == ["s1", "s2"]
    assert all(s["expired_reason"] == "cap" for s in C.read_expired(pd))


def test_a_second_expiry_of_the_same_session_keeps_both_copies(repo):
    md = _corpus(repo)
    pd = C.default_pending_dir(md)
    now = time.time()
    _raw_seed(pd, "same", captured_at=now - 20 * 86400, query_previews=["one"])
    C.expire_pending(pd, memory_dir=md, now=now)
    _raw_seed(pd, "same", captured_at=now - 20 * 86400, query_previews=["two"])
    C.expire_pending(pd, memory_dir=md, now=now)
    assert C.expired_count(pd) == 2
    names = sorted(os.listdir(C.expired_dir(pd)))
    assert names == ["capture-same.2.json", "capture-same.json"]


# --------------------------------------------------------------------------- #
# Restore
# --------------------------------------------------------------------------- #
def test_restore_one_by_name_then_by_session_then_all(repo):
    md = _corpus(repo)
    pd = C.default_pending_dir(md)
    now = time.time()
    for name in ("a", "b", "c"):
        _raw_seed(pd, name, captured_at=now - 30 * 86400)
    assert C.expire_pending(pd, memory_dir=md, now=now) == 3
    back = C.restore_pending("capture-a.json", pending_dir=pd, memory_dir=md, now=now)
    assert [os.path.basename(p) for p in back] == ["capture-a.json"]
    back = C.restore_pending("b", pending_dir=pd, memory_dir=md, now=now)
    assert [os.path.basename(p) for p in back] == ["capture-b.json"]
    back = C.restore_pending(None, restore_all=True, pending_dir=pd, memory_dir=md, now=now)
    assert [os.path.basename(p) for p in back] == ["capture-c.json"]
    assert C.expired_count(pd) == 0 and C.pending_count(pd) == 3
    restored = C.read_pending(pd)
    assert all("expired_at" not in s and s["restored_at"] == round(now, 3) for s in restored)
    # A restored seed's clock restarts: the next expiry pass leaves it alone.
    assert C.expire_pending(pd, memory_dir=md, now=now + 60) == 0
    assert _queue_counts(md).get("restored") == 3


def test_restore_folds_into_a_live_seed_of_the_same_session(repo):
    md = _corpus(repo)
    pd = C.default_pending_dir(md)
    now = time.time()
    _raw_seed(pd, "s", captured_at=now - 30 * 86400, query_previews=["old question"])
    C.expire_pending(pd, memory_dir=md, now=now)
    _raw_seed(pd, "s", captured_at=now - 60, query_previews=["new question"])
    back = C.restore_pending("s", pending_dir=pd, memory_dir=md, now=now)
    assert len(back) == 1 and C.pending_count(pd) == 1 and C.expired_count(pd) == 0
    seed = C.read_pending(pd)[0]
    assert seed["query_previews"] == ["old question", "new question"]


def test_cli_restore_and_drafted_discard(repo, capsys):
    md = _corpus(repo)
    pd = C.default_pending_dir(md)
    now = time.time()
    _raw_seed(pd, "x", captured_at=now - 30 * 86400)
    _raw_seed(pd, "y", captured_at=now - 60)
    C.main(["--prune", "--memory-dir", md])
    assert "moved 1" in capsys.readouterr().out
    C.main(["--list", "--memory-dir", md])
    out = capsys.readouterr().out
    assert "1 expired capture(s)" in out and "hippo capture --restore --all" in out
    assert C.main(["--restore", "--memory-dir", md]) == 2
    capsys.readouterr()
    C.main(["--restore", "--all", "--memory-dir", md])
    assert "restored 1 seed(s)" in capsys.readouterr().out
    C.main(["--discard", os.path.join(pd, "capture-x.json"), "--drafted", "--memory-dir", md])
    C.main(["--discard", os.path.join(pd, "capture-y.json"), "--memory-dir", md])
    counts = _queue_counts(md)
    assert counts.get("drafted") == 1 and counts.get("discarded") == 1
    assert counts.get("expired") == 1 and counts.get("restored") == 1


def test_mcp_restore_and_drafted(repo, monkeypatch):
    md = _corpus(repo)
    monkeypatch.setattr("memory.provenance.resolve_dirs", lambda *a, **k: (md, repo))
    pd = C.default_pending_dir(md)
    now = time.time()
    _raw_seed(pd, "m1", captured_at=now - 30 * 86400)
    C.expire_pending(pd, memory_dir=md, now=now)
    listing = MC._tool_capture({"action": "list"})
    assert "1 expired capture(s)" in listing
    assert "pass path=" in MC._tool_capture({"action": "restore"})
    assert "restored 1 seed(s)" in MC._tool_capture({"action": "restore", "all": True})
    out = MC._tool_capture({"action": "discard", "path": "capture-m1.json", "drafted": True})
    assert out.startswith("discarded:")
    assert _queue_counts(md).get("drafted") == 1


# --------------------------------------------------------------------------- #
# The counted lines: SessionStart and doctor
# --------------------------------------------------------------------------- #
def test_session_start_and_doctor_lines_count_the_expired_shelf(repo):
    md = _corpus(repo)
    pd = C.default_pending_dir(md)
    now = time.time()
    _raw_seed(pd, "live", captured_at=now - 60, score=2)
    _raw_seed(pd, "gone", captured_at=now - 30 * 86400)
    C.expire_pending(pd, memory_dir=md, now=now)
    line = SSS.pending_capture_producer(md, repo)
    assert "1 pending capture(s)" in line
    assert "1 older capture(s) expired" in line and "hippo capture --restore --all" in line
    assert "hippo capture --snooze" in line and "python -m" not in line
    doc = check_capture_queue(DoctorContext(md, repo))
    assert doc["status"] == "ok"
    assert doc["message"].startswith("capture queue: 1 pending, 1 expired")
    assert "1 expired" in doc["message"].split("30 days:")[1]


def test_record_queue_rejects_unknown_events(tmp_path):
    td = str(tmp_path / "t")
    assert TR.record_queue(td, "captured") is True
    assert TR.record_queue(td, "bogus") is False
    assert TR.record_queue(td, "folded", 0) is False
    assert TR.summarize(TR.read_rollups(td))["queue"] == {"captured": 1}
