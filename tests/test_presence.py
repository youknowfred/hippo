"""T18 FLT: the fleet lane — presence docs (FLT-1), later the moved tripwire (FLT-2) and
the worktree-first nudge (FLT-3). Hermetic throughout: every test runs against tmp_path
fixtures; nothing touches the real ~/.claude or this repo's own telemetry.

The lane's contract under test here (FLT-1):
  - per-session doc <telemetry_dir>/presence/<safe(session_id)>.json = {session_id,
    branch, head, ts}, written atomically under a self-ignoring dir;
  - mtime-TTL aging (any session's expired doc is pruned — the crash path) plus a
    count-cap oldest-first prune (the jit.MAX_STATE_FILES precedent);
  - SessionEnd clears the session's OWN doc only; SubagentStop must not;
  - the SessionStart producer is empty-norm: no OTHER fresh doc -> None, forever;
  - HIPPO_DISABLE_PRESENCE kills the whole lane; nothing here ever raises.
"""

import json
import os
import time

import pytest

from memory import presence as P
from memory.telemetry import default_telemetry_dir

from .conftest import git_commit, write_file


@pytest.fixture(autouse=True)
def _fresh_lane(monkeypatch):
    """Presence parks the harness session id in module state for the producer; tests must
    never see a previous test's id (one process runs many 'sessions' here)."""
    monkeypatch.setattr(P, "_SESSION_ID", None)
    monkeypatch.delenv("HIPPO_DISABLE_PRESENCE", raising=False)


def _seed_commit(repo):
    write_file(repo, "src/app.py", "x = 1\n")
    return git_commit(repo, "init", 1_700_000_000)


def _plant(td, sid, branch="other-branch", age_s=60.0, head="a" * 40, **extra):
    """Handcraft another session's presence doc, aged via mtime (the freshness oracle)."""
    doc = {"session_id": sid, "branch": branch, "head": head, "ts": time.time() - age_s}
    doc.update(extra)
    pd = P._presence_dir(td)
    os.makedirs(pd, exist_ok=True)
    path = P._presence_path(td, sid)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    when = time.time() - age_s
    os.utime(path, (when, when))
    return path


# --------------------------------------------------------------------------- #
# FLT-1: write_presence — the SessionStart doc
# --------------------------------------------------------------------------- #
def test_write_presence_creates_doc_with_live_position(repo, memory_dir):
    sha = _seed_commit(repo)
    P.write_presence(memory_dir, repo, session_id="sess-1")
    td = default_telemetry_dir(memory_dir)
    path = P._presence_path(td, "sess-1")
    assert os.path.isfile(path)
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    assert doc["session_id"] == "sess-1"
    assert doc["head"] == sha
    assert doc["branch"]  # whatever init named it (master/main) — a real branch name
    assert isinstance(doc["ts"], float) and doc["ts"] > 0


def test_presence_dir_is_self_ignoring(repo, memory_dir):
    _seed_commit(repo)
    P.write_presence(memory_dir, repo, session_id="sess-1")
    gi = os.path.join(P._presence_dir(default_telemetry_dir(memory_dir)), ".gitignore")
    with open(gi, "r", encoding="utf-8") as fh:
        assert fh.read().strip() == "*"


def test_harness_session_id_never_touches_the_shared_token(repo, memory_dir):
    """COR-6 parity: a harness id keys the doc DIRECTLY — the shared file token is
    neither read nor minted (concurrent sessions must never share it)."""
    _seed_commit(repo)
    td = default_telemetry_dir(memory_dir)
    P.write_presence(memory_dir, repo, session_id="harness-abc")
    assert os.path.isfile(P._presence_path(td, "harness-abc"))
    assert not os.path.exists(os.path.join(td, "session"))


def test_session_id_is_sanitized_for_the_filename(repo, memory_dir):
    _seed_commit(repo)
    P.write_presence(memory_dir, repo, session_id="a/b c:d")
    td = default_telemetry_dir(memory_dir)
    assert os.path.isfile(os.path.join(P._presence_dir(td), "a_b_c_d.json"))


def test_write_without_head_writes_nothing(repo, memory_dir):
    """A git tree with no commits has no HEAD and no collision story — no doc."""
    P.write_presence(memory_dir, repo, session_id="sess-1")
    assert not os.path.exists(P._presence_dir(default_telemetry_dir(memory_dir)))


def test_write_outside_git_writes_nothing(tmp_path):
    md = str(tmp_path / "proj" / ".claude" / "memory")
    os.makedirs(md)
    P.write_presence(md, str(tmp_path / "proj"), session_id="sess-1")
    assert not os.path.exists(P._presence_dir(default_telemetry_dir(md)))


def test_write_missing_memory_dir_never_raises(tmp_path):
    P.write_presence(str(tmp_path / "nope"), str(tmp_path), session_id="s")


def test_ttl_prune_removes_any_sessions_expired_doc(repo, memory_dir):
    _seed_commit(repo)
    td = default_telemetry_dir(memory_dir)
    dead = _plant(td, "crashed-sess", age_s=P.PRESENCE_TTL_SECONDS + 60)
    live = _plant(td, "live-sess", age_s=30)
    P.write_presence(memory_dir, repo, session_id="sess-1")
    assert not os.path.exists(dead)  # the crash path: expired docs age out on any write
    assert os.path.exists(live)


def test_count_cap_prunes_oldest_first(repo, memory_dir):
    _seed_commit(repo)
    td = default_telemetry_dir(memory_dir)
    for i in range(P.MAX_PRESENCE_FILES + 5):
        _plant(td, f"s{i:03d}", age_s=600 - i)  # s000 oldest … s036 newest
    P.write_presence(memory_dir, repo, session_id="sess-new")
    pd = P._presence_dir(td)
    kept = [n for n in os.listdir(pd) if n.endswith(".json")]
    assert len(kept) == P.MAX_PRESENCE_FILES
    assert "sess-new.json" in kept  # the just-written doc always survives
    assert "s000.json" not in kept and "s005.json" not in kept  # oldest gone first


def test_corrupt_own_doc_gets_fresh_defaults(repo, memory_dir):
    _seed_commit(repo)
    td = default_telemetry_dir(memory_dir)
    pd = P._presence_dir(td)
    os.makedirs(pd, exist_ok=True)
    with open(P._presence_path(td, "sess-1"), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    P.write_presence(memory_dir, repo, session_id="sess-1")
    with open(P._presence_path(td, "sess-1"), "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    assert doc["session_id"] == "sess-1" and doc["head"]


def test_rewrite_preserves_the_nudged_flag(repo, memory_dir):
    """A resume re-runs SessionStart; the FLT-3 once-per-session dedup must survive it."""
    _seed_commit(repo)
    td = default_telemetry_dir(memory_dir)
    P.write_presence(memory_dir, repo, session_id="sess-1")
    path = P._presence_path(td, "sess-1")
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    doc["nudged"] = True
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    P.write_presence(memory_dir, repo, session_id="sess-1")
    with open(path, "r", encoding="utf-8") as fh:
        assert json.load(fh).get("nudged") is True


def test_kill_switch_silences_the_whole_lane(repo, memory_dir, monkeypatch):
    _seed_commit(repo)
    monkeypatch.setenv("HIPPO_DISABLE_PRESENCE", "1")
    td = default_telemetry_dir(memory_dir)
    _plant(td, "other-sess", age_s=30)
    P.write_presence(memory_dir, repo, session_id="sess-1")
    assert not os.path.exists(P._presence_path(td, "sess-1"))
    assert P.presence_producer(memory_dir, repo) is None


# --------------------------------------------------------------------------- #
# FLT-1: clear_presence — the SessionEnd moment
# --------------------------------------------------------------------------- #
def test_clear_removes_only_the_own_doc(repo, memory_dir):
    _seed_commit(repo)
    td = default_telemetry_dir(memory_dir)
    other = _plant(td, "other-sess", age_s=30)
    P.write_presence(memory_dir, repo, session_id="sess-1")
    P.clear_presence(memory_dir, session_id="sess-1")
    assert not os.path.exists(P._presence_path(td, "sess-1"))
    assert os.path.exists(other)  # never a sweep (ED4R-3)


def test_clear_missing_doc_never_raises(repo, memory_dir):
    P.clear_presence(memory_dir, session_id="never-wrote")
    P.clear_presence(str(repo) + "/nope", session_id="x")


def test_capture_from_hook_clears_presence_on_session_end(repo, memory_dir, monkeypatch):
    """The SessionEnd wiring: capture --from-hook clears the ending session's doc."""
    import io

    from memory import capture as C

    _seed_commit(repo)
    P.write_presence(memory_dir, repo, session_id="sess-end")
    td = default_telemetry_dir(memory_dir)
    assert os.path.exists(P._presence_path(td, "sess-end"))
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"session_id": "sess-end", "reason": "exit"}))
    )
    C.main(["--from-hook", "--memory-dir", memory_dir, "--repo-root", repo])
    assert not os.path.exists(P._presence_path(td, "sess-end"))


def test_capture_from_hook_subagent_stop_does_not_clear(repo, memory_dir, monkeypatch):
    """SubagentStop rides the same capture entry with the PARENT's session id — the
    parent is still live, so its presence doc must survive."""
    import io

    from memory import capture as C

    _seed_commit(repo)
    P.write_presence(memory_dir, repo, session_id="parent-sess")
    td = default_telemetry_dir(memory_dir)
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"session_id": "parent-sess"}))
    )
    C.main(
        ["--from-hook", "--reason", "subagent-stop", "--memory-dir", memory_dir,
         "--repo-root", repo]
    )
    assert os.path.exists(P._presence_path(td, "parent-sess"))


# --------------------------------------------------------------------------- #
# FLT-1: presence_producer — the SessionStart line (empty-norm)
# --------------------------------------------------------------------------- #
def test_producer_silent_when_alone(repo, memory_dir):
    _seed_commit(repo)
    P.write_presence(memory_dir, repo, session_id="sess-1")
    assert P.presence_producer(memory_dir, repo) is None


def test_producer_silent_with_no_presence_dir(repo, memory_dir):
    assert P.presence_producer(memory_dir, repo) is None


def test_producer_names_other_sessions_branch_and_age(repo, memory_dir):
    _seed_commit(repo)
    td = default_telemetry_dir(memory_dir)
    _plant(td, "other-sess", branch="enh-x", age_s=7 * 60)
    P.write_presence(memory_dir, repo, session_id="sess-1")
    out = P.presence_producer(memory_dir, repo)
    assert out is not None and out.count("\n") == 0  # ONE bounded line
    assert "enh-x" in out and "7m ago" in out
    assert "1 other session" in out


def test_producer_excludes_the_own_doc_by_harness_id(repo, memory_dir):
    """write_presence parks the harness id; the producer (whose fixed call shape carries
    no id) must use it to exclude the session's own doc."""
    _seed_commit(repo)
    P.write_presence(memory_dir, repo, session_id="harness-77")
    out = P.presence_producer(memory_dir, repo)
    assert out is None


def test_producer_ignores_expired_docs(repo, memory_dir):
    _seed_commit(repo)
    td = default_telemetry_dir(memory_dir)
    _plant(td, "stale-sess", age_s=P.PRESENCE_TTL_SECONDS + 60)
    P.write_presence(memory_dir, repo, session_id="sess-1")
    # the write's prune already removed it; even a racing re-plant stays invisible
    _plant(td, "stale-sess", age_s=P.PRESENCE_TTL_SECONDS + 60)
    assert P.presence_producer(memory_dir, repo) is None


def test_producer_caps_named_branches(repo, memory_dir):
    _seed_commit(repo)
    td = default_telemetry_dir(memory_dir)
    for i in range(P._MAX_FLEET_NAMES + 2):
        _plant(td, f"o{i}", branch=f"branch-{i}", age_s=60 + i)
    P.write_presence(memory_dir, repo, session_id="sess-1")
    out = P.presence_producer(memory_dir, repo)
    assert out is not None
    assert f"{P._MAX_FLEET_NAMES + 2} other sessions" in out
    assert "(+2 more)" in out
    assert len(out) <= P._MAX_LINE_CHARS


def test_producer_orders_newest_first(repo, memory_dir):
    _seed_commit(repo)
    td = default_telemetry_dir(memory_dir)
    _plant(td, "older", branch="older-branch", age_s=50 * 60)
    _plant(td, "newer", branch="newer-branch", age_s=2 * 60)
    P.write_presence(memory_dir, repo, session_id="sess-1")
    out = P.presence_producer(memory_dir, repo)
    assert out.index("newer-branch") < out.index("older-branch")


def test_producer_never_raises_on_garbage_docs(repo, memory_dir):
    _seed_commit(repo)
    td = default_telemetry_dir(memory_dir)
    pd = P._presence_dir(td)
    os.makedirs(pd, exist_ok=True)
    with open(os.path.join(pd, "junk.json"), "w", encoding="utf-8") as fh:
        fh.write("{broken")
    P.write_presence(memory_dir, repo, session_id="sess-1")
    out = P.presence_producer(memory_dir, repo)
    assert out is None or "(detached)" in out  # a fresh-but-unreadable doc renders neutral


def test_age_rendering_grains():
    assert P._age_str(30) == "1m"
    assert P._age_str(7 * 60) == "7m"
    assert P._age_str(3 * 3600 + 60) == "3h"
    assert P._age_str(3 * 86400) == "3d"
