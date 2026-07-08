"""TEA-5 — usage signals honest about their scope: "never recalled" means "never in THIS
clone" until teammates commit a per-user usage summary that curation UNIONS before judging
coldness. Plus current_user_slug derivation.
"""

from __future__ import annotations

import json
import os

from memory import build_index as B
from memory import provenance as P
from memory import soak as SK
from memory import telemetry as T


def _seed_events(td, events):
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "recall_events.jsonl"), "w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")


def _seed_corpus(memory_dir, names):
    os.makedirs(memory_dir, exist_ok=True)
    for n in names:
        with open(os.path.join(memory_dir, f"{n}.md"), "w", encoding="utf-8") as fh:
            fh.write(f'---\nname: {n}\ndescription: "d {n}"\nmetadata:\n  type: project\n---\nbody\n')


def _write_committed(memory_dir, user, memories, sessions=5):
    usage = os.path.join(memory_dir, ".usage")
    os.makedirs(usage, exist_ok=True)
    payload = {
        "version": 1,
        "user": user,
        "sessions": {"count": sessions, "first_ts": 1.0, "last_ts": 2.0},
        "memories": {m: {"first_ts": 1.0, "last_ts": 2.0, "sessions": 1} for m in memories},
    }
    with open(os.path.join(usage, f"{user}.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


# --------------------------------------------------------------------------- #
# current_user_slug
# --------------------------------------------------------------------------- #
def test_user_slug_env_override_and_slugify(monkeypatch, tmp_path):
    monkeypatch.setenv("HIPPO_USAGE_USER", "Alice O'Brien <a@b.co>")
    # lowercased, non [a-z0-9_.-] -> _, leading/trailing separators trimmed
    assert P.current_user_slug(str(tmp_path)) == "alice_o_brien__a_b.co"


def test_user_slug_falls_back_to_unknown(monkeypatch, tmp_path):
    monkeypatch.delenv("HIPPO_USAGE_USER", raising=False)
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("LOGNAME", raising=False)
    # Isolate git config so the runner's real global user.email can't leak in (git config
    # reads the global ~/.gitconfig even outside a repo — correct in production, isolated here).
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "nonexistent-global"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(tmp_path / "nonexistent-system"))
    assert P.current_user_slug(str(tmp_path)) == "unknown"


def test_user_slug_prefers_git_email(monkeypatch, tmp_path):
    import subprocess

    monkeypatch.delenv("HIPPO_USAGE_USER", raising=False)
    repo = str(tmp_path / "r")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "Dev.Person@Example.com"], cwd=repo, check=True)
    assert P.current_user_slug(repo) == "dev.person_example.com"


# --------------------------------------------------------------------------- #
# committed usage read/write
# --------------------------------------------------------------------------- #
def test_read_committed_usage_empty_when_absent(tmp_path):
    got = T.read_committed_usage(str(tmp_path / "memory"))
    assert got == {"memories": set(), "sessions": 0}


def test_read_committed_usage_unions_multiple_users(tmp_path):
    md = str(tmp_path / "memory")
    _write_committed(md, "alice", ["shared", "alice-only"], sessions=6)
    _write_committed(md, "bob", ["shared", "bob-only"], sessions=4)
    got = T.read_committed_usage(md)
    assert got["memories"] == {"shared", "alice-only", "bob-only"}
    assert got["sessions"] == 10  # summed across users


def test_write_user_usage_summary_folds_aggregates(tmp_path, monkeypatch):
    md = str(tmp_path / "memory")
    td = str(tmp_path / ".telemetry")
    os.makedirs(td, exist_ok=True)
    # Seed local aggregates via the real updater.
    T._update_usage_aggregates(td, names=["frequently-used"], session_id="s1", ts=100.0)
    T._update_usage_aggregates(td, names=["frequently-used"], session_id="s2", ts=200.0)

    path = T.write_user_usage_summary(md, "carol", td)
    assert path and os.path.isfile(path)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["user"] == "carol"
    assert "frequently-used" in data["memories"]
    assert data["sessions"]["count"] >= 2
    # No session ids leak into the committed file.
    blob = json.dumps(data)
    assert "s1" not in blob and "s2" not in blob and "last_session_id" not in blob


def test_write_user_usage_summary_is_monotone(tmp_path):
    md = str(tmp_path / "memory")
    td = str(tmp_path / ".telemetry")
    os.makedirs(td, exist_ok=True)
    T._update_usage_aggregates(td, names=["a", "b"], session_id="s1", ts=100.0)
    T._update_usage_aggregates(td, names=["a"], session_id="s2", ts=200.0)
    T.write_user_usage_summary(md, "dave", td)
    # Aggregates shrink (file reset) — a re-run must NOT lose ground already committed.
    os.remove(os.path.join(td, "usage_aggregates.json"))
    T._update_usage_aggregates(td, names=["c"], session_id="s3", ts=300.0)
    T.write_user_usage_summary(md, "dave", td)
    got = T.read_committed_usage(md)
    assert {"a", "b", "c"} <= got["memories"]  # earlier a/b survive the fold


# --------------------------------------------------------------------------- #
# curation unions committed usage before judging coldness
# --------------------------------------------------------------------------- #
def test_curation_report_unions_committed_usage(tmp_path):
    md = str(tmp_path / "memory")
    td = str(tmp_path / ".telemetry")
    _seed_corpus(md, ["local-hit", "teammate-hit", "truly-cold"])
    # This clone only ever recalled "local-hit".
    _seed_events(td, [{"session_id": "s1", "names": ["local-hit"], "backend": "bm25"}])
    # A teammate's committed usage says THEY recall "teammate-hit" daily.
    _write_committed(md, "teammate", ["teammate-hit"])

    rep = SK.curation_report(md, td)
    assert rep["committed_usage_present"] is True
    assert "teammate-hit" not in rep["never_recalled"], "a teammate's daily hit must not be cold"
    assert rep["never_recalled"] == ["truly-cold"]


def test_curation_report_clone_local_without_committed_usage(tmp_path):
    md = str(tmp_path / "memory")
    td = str(tmp_path / ".telemetry")
    _seed_corpus(md, ["local-hit", "teammate-hit"])
    _seed_events(td, [{"session_id": "s1", "names": ["local-hit"], "backend": "bm25"}])
    rep = SK.curation_report(md, td)
    assert rep["committed_usage_present"] is False
    assert rep["never_recalled"] == ["teammate-hit"]  # clone-local: reads as cold here


def test_soak_status_counts_committed_sessions(tmp_path):
    md = str(tmp_path / "memory")
    td = str(tmp_path / ".telemetry")
    _seed_events(td, [{"session_id": "s1", "names": ["a"], "backend": "bm25"}])
    _write_committed(md, "teammate", ["a"], sessions=9)
    # Without memory_dir -> clone-local (1 session, gate unmet).
    assert SK.soak_status(td)["gate_met"] is False
    # With memory_dir -> unions 9 committed cross-clone sessions -> gate met.
    st = SK.soak_status(td, memory_dir=md)
    assert st["committed_sessions"] == 9 and st["gate_met"] is True


# --------------------------------------------------------------------------- #
# .usage/ is committed (not self-ignored) and never indexed
# --------------------------------------------------------------------------- #
def test_usage_dir_never_indexed(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _seed_corpus(md, ["real-memory"])
    _write_committed(md, "alice", ["real-memory"])
    manifest = B.build_index(md, idx)
    names = {e["name"] for e in manifest["entries"]}
    assert names == {"real-memory"}, ".usage/*.json must never be indexed as a memory"


def test_usage_dir_is_not_self_ignored(tmp_path):
    """.usage is COMMITTED — write_user_usage_summary must NOT drop a self-ignoring .gitignore."""
    md = str(tmp_path / "memory")
    td = str(tmp_path / ".telemetry")
    os.makedirs(td, exist_ok=True)
    T._update_usage_aggregates(td, names=["a"], session_id="s1", ts=1.0)
    T.write_user_usage_summary(md, "alice", td)
    assert not os.path.exists(os.path.join(md, ".usage", ".gitignore"))
