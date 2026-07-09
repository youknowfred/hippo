"""Tests for memory/telemetry.py — the recall-event ledger.

Hermetic: every test points ``HIPPO_TELEMETRY_DIR`` at a tmp dir (nothing touches the real
``.claude/.memory-telemetry``). The recall-isolation test builds a throwaway BM25-only index.
"""

from __future__ import annotations

import os

import memory.telemetry as T

from .conftest import git_commit


def _events(td):
    return list(T.read_events(td))


def _episodes(td):
    return list(T.read_episodes(td))


# --------------------------------------------------------------------------- #
# append schema + never-raises
# --------------------------------------------------------------------------- #
def test_append_writes_one_event_with_schema(tmp_path):
    td = str(tmp_path / "tele")
    results = [
        {"name": "alpha", "backend": "dense+bm25"},
        {"name": "beta", "backend": "dense+bm25"},
    ]
    ok = T.log_recall_event(
        results, query="how do we avoid timeouts", k=10, latency_ms=42.5, telemetry_dir=td
    )
    assert ok is True
    evs = _events(td)
    assert len(evs) == 1
    e = evs[0]
    assert e["names"] == ["alpha", "beta"]
    assert e["backend"] == "dense+bm25"
    assert e["latency_ms"] == 42.5
    assert e["k"] == 10
    assert e["query_preview"] == "how do we avoid timeouts"
    assert isinstance(e["ts"], (int, float))
    assert e["session_id"]  # a session id was stamped


def test_empty_results_logged_with_none_backend(tmp_path):
    td = str(tmp_path / "tele")
    assert (
        T.log_recall_event([], query="nothing matches", k=10, latency_ms=3.0, telemetry_dir=td)
        is True
    )
    evs = _events(td)
    assert len(evs) == 1
    assert evs[0]["names"] == []
    # a recall that surfaced nothing STILL counts as a session for the soak gate
    assert evs[0]["backend"] == "none"


def test_query_is_truncated_no_full_prompt(tmp_path):
    td = str(tmp_path / "tele")
    secret = "SENSITIVE " + "x" * 500
    T.log_recall_event(
        [{"name": "a", "backend": "bm25"}], query=secret, k=5, latency_ms=1.0, telemetry_dir=td
    )
    e = _events(td)[0]
    assert len(e["query_preview"]) <= T._QUERY_PREVIEW_CHARS
    assert "x" * 500 not in e["query_preview"]  # the full prompt is NOT stored


def test_missing_dir_is_created(tmp_path):
    td = str(tmp_path / "deep" / "nested" / "tele")
    assert not os.path.exists(td)
    assert (
        T.log_recall_event(
            [{"name": "a", "backend": "bm25"}], query="q", k=1, latency_ms=1.0, telemetry_dir=td
        )
        is True
    )
    assert os.path.isdir(td)


def test_append_never_raises_on_unwritable_dir(tmp_path):
    # A FILE where the telemetry dir's parent should be -> makedirs fails -> degrade to False.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file")
    td = str(blocker / "sub" / "tele")
    # must NOT raise; returns False and the caller's recall is unaffected
    assert (
        T.log_recall_event(
            [{"name": "a", "backend": "bm25"}], query="q", k=1, latency_ms=1.0, telemetry_dir=td
        )
        is False
    )


# --------------------------------------------------------------------------- #
# rotation / bound
# --------------------------------------------------------------------------- #
def test_ledger_rotates_under_byte_cap(tmp_path, monkeypatch):
    td = str(tmp_path / "tele")
    monkeypatch.setenv("HIPPO_TELEMETRY_MAX_BYTES", "800")
    for i in range(200):
        T.log_recall_event(
            [{"name": f"m{i}", "backend": "bm25"}],
            query=f"query number {i}",
            k=10,
            latency_ms=float(i),
            telemetry_dir=td,
        )
    ledger = T._ledger_path(td)
    # we rotate immediately whenever size exceeds the cap, so the final file is <= cap
    assert os.path.getsize(ledger) <= 800
    evs = _events(td)
    assert 0 < len(evs) < 200  # rotation dropped older events
    assert evs[-1]["names"] == ["m199"]  # newest retained


# --------------------------------------------------------------------------- #
# LIF-4: usage aggregates — rotation-surviving per-memory recall history
# --------------------------------------------------------------------------- #
def _log(td, names, sid, query="q"):
    return T.log_recall_event(
        [{"name": n, "backend": "bm25"} for n in names],
        query=query,
        k=5,
        latency_ms=1.0,
        telemetry_dir=td,
        session_id=sid,
    )


def test_log_recall_event_updates_usage_aggregates_schema(tmp_path):
    td = str(tmp_path / "tele")
    _log(td, ["alpha", "beta"], "s1")
    _log(td, ["alpha"], "s1")  # same session -- distinct count must not advance
    _log(td, ["alpha"], "s2")

    agg = T.read_usage_aggregates(td)
    evs = _events(td)
    alpha = agg["memories"]["alpha"]
    assert alpha["sessions"] == 2  # s1 + s2, not 3 events
    assert alpha["first_ts"] == evs[0]["ts"]  # stamped from the FIRST event
    assert alpha["last_ts"] == evs[-1]["ts"]  # advanced by the latest
    assert agg["memories"]["beta"]["sessions"] == 1
    assert agg["sessions"]["count"] == 2  # global distinct sessions
    assert agg["sessions"]["first_ts"] == evs[0]["ts"]  # observation-span start


def test_usage_aggregates_survive_ledger_rotation(tmp_path, monkeypatch):
    """AC (LIF-4): forced ledger rotation does NOT change per-memory recall history —
    the oldest evidence survives in the aggregates after the byte-capped tail drops it."""
    td = str(tmp_path / "tele")
    monkeypatch.setenv("HIPPO_TELEMETRY_MAX_BYTES", "800")
    _log(td, ["keystone"], "s0", query="the very first recall")
    before = T.read_usage_aggregates(td)["memories"]["keystone"]

    for i in range(200):
        _log(td, [f"m{i}"], f"s{i + 1}", query=f"query number {i}")

    evs = _events(td)
    assert 0 < len(evs) < 201  # rotation really dropped the oldest events...
    assert all(e["names"] != ["keystone"] for e in evs)  # ...including keystone's only recall

    agg = T.read_usage_aggregates(td)
    assert agg["memories"]["keystone"] == before  # history byte-identical across rotation
    assert agg["sessions"]["count"] == 201  # every distinct session still counted
    assert len(agg["memories"]) == 201  # no per-memory record was lost


def test_usage_aggregates_count_distinct_sessions_not_events(tmp_path):
    td = str(tmp_path / "tele")
    for _ in range(3):
        _log(td, ["a"], "same-session")
    assert T.read_usage_aggregates(td)["memories"]["a"]["sessions"] == 1
    assert T.read_usage_aggregates(td)["sessions"]["count"] == 1
    _log(td, ["a"], "new-session")
    assert T.read_usage_aggregates(td)["memories"]["a"]["sessions"] == 2
    assert T.read_usage_aggregates(td)["sessions"]["count"] == 2


def test_empty_recall_still_counts_session_in_aggregates(tmp_path):
    """An empty recall counts as a session for the soak gate (mirrors soak_status), but
    creates no per-memory record."""
    td = str(tmp_path / "tele")
    _log(td, [], "sX")
    agg = T.read_usage_aggregates(td)
    assert agg["sessions"]["count"] == 1
    assert agg["memories"] == {}


def test_corrupt_aggregates_start_fresh_never_raise(tmp_path):
    td = str(tmp_path / "tele")
    os.makedirs(td)
    with open(T._usage_aggregates_path(td), "w", encoding="utf-8") as fh:
        fh.write("{ not json at all")
    assert _log(td, ["a"], "s1") is True  # never raises on the hot path
    agg = T.read_usage_aggregates(td)
    assert agg["sessions"]["count"] == 1  # started fresh, then counted the new event
    assert agg["memories"]["a"]["sessions"] == 1


def test_read_usage_aggregates_missing_file_returns_empty_shape(tmp_path):
    agg = T.read_usage_aggregates(str(tmp_path / "nope"))
    assert agg["sessions"] == {"count": 0, "first_ts": None, "last_session_id": None}
    assert agg["memories"] == {}


def test_aggregates_write_failure_never_breaks_ledger_append(tmp_path):
    """usage_aggregates.json replaced by a DIRECTORY -> os.replace fails -> the aggregate
    update degrades silently while the ledger append still succeeds (and no stray .tmp
    file is left behind)."""
    td = str(tmp_path / "tele")
    os.makedirs(os.path.join(td, "usage_aggregates.json"))  # a dir where the file goes
    assert _log(td, ["a"], "s1") is True
    assert [e["names"] for e in _events(td)] == [["a"]]
    assert not [f for f in os.listdir(td) if ".tmp." in f]  # tmp cleaned up on failure


def test_ledger_rotation_never_touches_aggregates_file(tmp_path, monkeypatch):
    """_rotate_if_needed operates on the ledger path only — across a rotation-heavy burst
    that adds no new history (same name, same session), everything HISTORIC in the
    aggregates is intact (only last_ts legitimately advances: real recalls happened)."""
    td = str(tmp_path / "tele")
    _log(td, ["pinned"], "s0")
    before = T.read_usage_aggregates(td)

    monkeypatch.setenv("HIPPO_TELEMETRY_MAX_BYTES", "600")
    for i in range(100):
        _log(td, ["pinned"], "s0", query=f"padding {i}")

    after = T.read_usage_aggregates(td)
    assert after["memories"]["pinned"]["first_ts"] == before["memories"]["pinned"]["first_ts"]
    assert after["memories"]["pinned"]["sessions"] == before["memories"]["pinned"]["sessions"]
    assert after["sessions"] == before["sessions"]
    assert os.path.getsize(T._ledger_path(td)) <= 600  # rotation really happened


# --------------------------------------------------------------------------- #
# session tagging
# --------------------------------------------------------------------------- #
def test_mark_session_rotates_token(tmp_path):
    td = str(tmp_path / "tele")
    s1 = T.mark_session(td)
    s2 = T.mark_session(td)
    assert s1 and s2 and s1 != s2
    assert T.current_session_id(td) == s2  # current == the latest mark


def test_two_sessionstarts_produce_two_distinct_session_ids(tmp_path):
    td = str(tmp_path / "tele")
    sid_a = T.mark_session(td)
    T.log_recall_event(
        [{"name": "a", "backend": "bm25"}], query="one", k=1, latency_ms=1.0, telemetry_dir=td
    )
    sid_b = T.mark_session(td)
    T.log_recall_event(
        [{"name": "b", "backend": "bm25"}], query="two", k=1, latency_ms=1.0, telemetry_dir=td
    )
    evs = _events(td)
    assert evs[0]["session_id"] == sid_a
    assert evs[1]["session_id"] == sid_b
    assert sid_a != sid_b
    assert len({e["session_id"] for e in evs}) == 2


def test_current_session_id_mints_when_absent(tmp_path):
    td = str(tmp_path / "tele")
    assert not os.path.exists(T._session_path(td))
    sid = T.current_session_id(td)
    assert sid
    assert T.current_session_id(td) == sid  # stable on re-read


# --------------------------------------------------------------------------- #
# COR-6: harness-keyed telemetry sessions (bypass the shared file-based token)
# --------------------------------------------------------------------------- #
def test_current_session_id_override_bypasses_file_token(tmp_path):
    td = str(tmp_path / "tele")
    assert T.current_session_id(td, session_id="harness-abc") == "harness-abc"
    assert not os.path.exists(T._session_path(td))  # never read or written


def test_current_session_id_override_wins_over_existing_file_token(tmp_path):
    td = str(tmp_path / "tele")
    file_sid = T.mark_session(td)
    assert T.current_session_id(td, session_id="harness-xyz") == "harness-xyz"
    assert file_sid != "harness-xyz"


def test_log_recall_event_uses_harness_session_id(tmp_path):
    td = str(tmp_path / "tele")
    T.log_recall_event(
        [{"name": "a", "backend": "bm25"}],
        query="q",
        k=1,
        latency_ms=1.0,
        telemetry_dir=td,
        session_id="harness-sid-1",
    )
    assert _events(td)[0]["session_id"] == "harness-sid-1"
    assert not os.path.exists(T._session_path(td))  # file token never touched


def test_log_episode_uses_harness_session_id(tmp_path):
    td = str(tmp_path / "tele")
    T.log_episode(["a"], query="q", telemetry_dir=td, session_id="harness-sid-2")
    assert _episodes(td)[0]["session_id"] == "harness-sid-2"


def test_two_concurrent_harness_session_ids_stay_distinct_and_stable(tmp_path):
    """Two concurrent sessions on the SAME telemetry_dir, each with its own harness
    session_id, must log under distinct stable ids — neither clobbers the other's,
    unlike the shared mutable session-token file."""
    td = str(tmp_path / "tele")
    T.log_recall_event(
        [{"name": "a", "backend": "bm25"}],
        query="one", k=1, latency_ms=1.0, telemetry_dir=td, session_id="session-A",
    )
    T.log_recall_event(
        [{"name": "b", "backend": "bm25"}],
        query="two", k=1, latency_ms=1.0, telemetry_dir=td, session_id="session-B",
    )
    T.log_recall_event(
        [{"name": "c", "backend": "bm25"}],
        query="three", k=1, latency_ms=1.0, telemetry_dir=td, session_id="session-A",
    )
    evs = _events(td)
    assert [e["session_id"] for e in evs] == ["session-A", "session-B", "session-A"]
    assert not os.path.exists(T._session_path(td))


# --------------------------------------------------------------------------- #
# read_events robustness
# --------------------------------------------------------------------------- #
def test_read_events_skips_corrupt_lines(tmp_path):
    td = str(tmp_path / "tele")
    os.makedirs(td)
    with open(T._ledger_path(td), "w", encoding="utf-8") as fh:
        fh.write('{"names": ["good1"], "backend": "bm25"}\n')
        fh.write("this is not json\n")
        fh.write("\n")
        fh.write('{"names": ["good2"], "backend": "dense"}\n')
        fh.write('"a string, not a dict"\n')
    evs = _events(td)
    assert [e["names"] for e in evs] == [["good1"], ["good2"]]


def test_read_events_empty_when_missing(tmp_path):
    assert _events(str(tmp_path / "nope")) == []


def test_append_after_corrupt_line_does_not_raise(tmp_path):
    td = str(tmp_path / "tele")
    os.makedirs(td)
    with open(T._ledger_path(td), "w", encoding="utf-8") as fh:
        fh.write("garbage line\n")
    assert (
        T.log_recall_event(
            [{"name": "a", "backend": "bm25"}], query="q", k=1, latency_ms=1.0, telemetry_dir=td
        )
        is True
    )
    assert [e["names"] for e in _events(td)] == [["a"]]  # corrupt skipped, good read


# --------------------------------------------------------------------------- #
# episode buffer (log_episode / read_episodes) — Tier 1 instrumentation
# --------------------------------------------------------------------------- #
def test_log_episode_writes_one_entry_with_schema(tmp_path, repo):
    td = str(tmp_path / "tele")
    git_commit(repo, "init", 1_700_000_000)
    assert (
        T.log_episode(["alpha", "beta"], query="how do recall ties resolve", repo_root=repo, telemetry_dir=td)
        is True
    )
    eps = _episodes(td)
    assert len(eps) == 1
    e = eps[0]
    assert set(e.keys()) >= {"ts", "session_id", "query_preview", "recalled_names", "head_commit"}
    assert e["recalled_names"] == ["alpha", "beta"]
    assert e["query_preview"] == "how do recall ties resolve"
    assert len(e["head_commit"]) == 40  # full sha from the hermetic repo's real commit


def test_log_episode_query_is_truncated_no_full_prompt(tmp_path):
    td = str(tmp_path / "tele")
    secret = "x" * 5000
    T.log_episode(["a"], query=secret, telemetry_dir=td)
    assert len(_episodes(td)[0]["query_preview"]) == T._QUERY_PREVIEW_CHARS


def test_log_episode_drops_falsy_names(tmp_path):
    td = str(tmp_path / "tele")
    T.log_episode(["alpha", "", None, "beta"], query="q", telemetry_dir=td)
    assert _episodes(td)[0]["recalled_names"] == ["alpha", "beta"]


def test_log_episode_no_repo_root_means_no_head_commit(tmp_path):
    """repo_root omitted -> head_commit is None, never resolved against the real repo
    (hermeticity: log_episode must not silently fall back to the real working tree)."""
    td = str(tmp_path / "tele")
    assert T.log_episode(["a"], query="q", telemetry_dir=td) is True
    assert _episodes(td)[0]["head_commit"] is None


def test_log_episode_non_git_repo_root_degrades_to_none_head_commit(tmp_path):
    td = str(tmp_path / "tele")
    not_a_repo = str(tmp_path / "not_a_repo")
    os.makedirs(not_a_repo)
    assert T.log_episode(["a"], query="q", repo_root=not_a_repo, telemetry_dir=td) is True
    assert _episodes(td)[0]["head_commit"] is None


def test_log_episode_shares_rotation_with_its_own_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_TELEMETRY_MAX_BYTES", "400")
    td = str(tmp_path / "tele")
    for i in range(40):
        T.log_episode([f"m{i}"], query=f"q{i}", telemetry_dir=td)
    path = T._episode_ledger_path(td)
    assert os.path.getsize(path) <= 400
    # the recall-event ledger (a sibling file) is untouched by episode-buffer rotation
    assert not os.path.exists(T._ledger_path(td))


def test_log_episode_never_raises_on_unwritable_dir(tmp_path):
    blocker = tmp_path / "tele"
    blocker.write_text("not a directory")
    assert T.log_episode(["a"], query="q", telemetry_dir=str(blocker)) is False


def test_read_episodes_skips_corrupt_lines(tmp_path):
    td = str(tmp_path / "tele")
    os.makedirs(td)
    with open(T._episode_ledger_path(td), "w", encoding="utf-8") as fh:
        fh.write('{"recalled_names": ["a"]}\n')
        fh.write("not json\n")
        fh.write('{"recalled_names": ["b"]}\n')
    assert [e["recalled_names"] for e in _episodes(td)] == [["a"], ["b"]]


def test_read_episodes_empty_when_missing(tmp_path):
    assert _episodes(str(tmp_path / "tele")) == []


def test_recall_ledger_and_episode_buffer_are_distinct_files(tmp_path):
    td = str(tmp_path / "tele")
    T.log_recall_event([{"name": "a", "backend": "bm25"}], query="q", k=1, latency_ms=1.0, telemetry_dir=td)
    T.log_episode(["a"], query="q", telemetry_dir=td)
    assert os.path.exists(T._ledger_path(td))
    assert os.path.exists(T._episode_ledger_path(td))
    assert T._ledger_path(td) != T._episode_ledger_path(td)
    assert len(_events(td)) == 1
    assert len(_episodes(td)) == 1


# --------------------------------------------------------------------------- #
# reconsolidation outcomes (record_reconsolidation_outcome / read_reconsolidation_events)
# --------------------------------------------------------------------------- #
def _outcomes(td):
    return list(T.read_reconsolidation_events(td))


def test_record_reconsolidation_outcome_writes_one_entry_with_schema(tmp_path):
    td = str(tmp_path / "tele")
    assert T.record_reconsolidation_outcome("some_memory", "graduate", telemetry_dir=td) is True
    evs = _outcomes(td)
    assert len(evs) == 1
    assert set(evs[0].keys()) >= {"ts", "name", "outcome"}
    assert evs[0]["name"] == "some_memory"
    assert evs[0]["outcome"] == "graduate"


def test_record_reconsolidation_outcome_accepts_all_three_verdicts(tmp_path):
    td = str(tmp_path / "tele")
    for outcome in ("graduate", "fix", "demote"):
        assert T.record_reconsolidation_outcome("m", outcome, telemetry_dir=td) is True
    assert [e["outcome"] for e in _outcomes(td)] == ["graduate", "fix", "demote"]


def test_record_reconsolidation_outcome_rejects_invalid_verdict(tmp_path):
    td = str(tmp_path / "tele")
    assert T.record_reconsolidation_outcome("m", "approved", telemetry_dir=td) is False
    assert _outcomes(td) == []  # no garbage entry written, denominator stays clean


def test_record_reconsolidation_outcome_accepts_snooze_ack(tmp_path):
    """LIF-1: "snooze" (an explicit per-item deferral, not a verdict) is a valid ledger
    outcome — the worklist reads it back to stop re-nagging for a bounded window."""
    td = str(tmp_path / "tele")
    assert T.record_reconsolidation_outcome("m", "snooze", telemetry_dir=td) is True
    evs = _outcomes(td)
    assert len(evs) == 1 and evs[0]["outcome"] == "snooze"


def test_record_reconsolidation_outcome_invalidated_field_is_optional_and_auditable(tmp_path):
    """LIF-1: demote's chained soft-invalidation is stamped onto the event (audit trail);
    events recorded without the kwarg keep their exact pre-LIF-1 shape."""
    td = str(tmp_path / "tele")
    assert T.record_reconsolidation_outcome("m1", "demote", telemetry_dir=td, invalidated=True)
    assert T.record_reconsolidation_outcome("m2", "graduate", telemetry_dir=td)
    evs = _outcomes(td)
    assert evs[0]["invalidated"] is True
    assert "invalidated" not in evs[1]  # absent unless the caller stamped it


def test_record_reconsolidation_outcome_never_raises_on_unwritable_dir(tmp_path):
    blocker = tmp_path / "tele"
    blocker.write_text("not a directory")
    assert T.record_reconsolidation_outcome("m", "graduate", telemetry_dir=str(blocker)) is False


def test_reconsolidation_ledger_shares_rotation(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_TELEMETRY_MAX_BYTES", "400")
    td = str(tmp_path / "tele")
    for i in range(40):
        T.record_reconsolidation_outcome(f"m{i}", "graduate", telemetry_dir=td)
    assert os.path.getsize(T._reconsolidation_ledger_path(td)) <= 400


def test_read_reconsolidation_events_skips_corrupt_lines(tmp_path):
    td = str(tmp_path / "tele")
    os.makedirs(td)
    with open(T._reconsolidation_ledger_path(td), "w", encoding="utf-8") as fh:
        fh.write('{"name": "a", "outcome": "graduate"}\n')
        fh.write("not json\n")
        fh.write('{"name": "b", "outcome": "demote"}\n')
    assert [e["name"] for e in _outcomes(td)] == ["a", "b"]


def test_read_reconsolidation_events_empty_when_missing(tmp_path):
    assert _outcomes(str(tmp_path / "tele")) == []


def test_three_ledgers_are_distinct_files(tmp_path):
    """recall ledger, episode buffer, reconsolidation events are THREE separate sibling
    files — none of the three write paths can collide or corrupt another."""
    td = str(tmp_path / "tele")
    T.log_recall_event([{"name": "a", "backend": "bm25"}], query="q", k=1, latency_ms=1.0, telemetry_dir=td)
    T.log_episode(["a"], query="q", telemetry_dir=td)
    T.record_reconsolidation_outcome("a", "graduate", telemetry_dir=td)
    paths = {T._ledger_path(td), T._episode_ledger_path(td), T._reconsolidation_ledger_path(td)}
    assert len(paths) == 3
    assert all(os.path.exists(p) for p in paths)
    assert len(_events(td)) == 1
    assert len(_episodes(td)) == 1
    assert len(_outcomes(td)) == 1


# --------------------------------------------------------------------------- #
# default_telemetry_dir derivation
# --------------------------------------------------------------------------- #
def test_default_telemetry_dir_is_index_sibling(tmp_path, monkeypatch):
    monkeypatch.delenv("HIPPO_TELEMETRY_DIR", raising=False)
    md = str(tmp_path / ".claude" / "memory")
    assert T.default_telemetry_dir(md) == str(tmp_path / ".claude" / ".memory-telemetry")


def test_default_telemetry_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", "/custom/tele")
    assert T.default_telemetry_dir(str(tmp_path / ".claude" / "memory")) == "/custom/tele"


# --------------------------------------------------------------------------- #
# recall.main() logs exactly ONE event; recall() direct logs NOTHING (eval isolation)
# --------------------------------------------------------------------------- #
def test_recall_main_logs_one_event_recall_direct_logs_nothing(tmp_path, monkeypatch):
    from memory import build_index as B
    from memory import recall as R

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    td = str(tmp_path / "tele")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)

    md = str(tmp_path / ".claude" / "memory")
    os.makedirs(md)
    with open(os.path.join(md, "alpha_note.md"), "w", encoding="utf-8") as fh:
        fh.write(
            '---\nname: alpha_note\ndescription: "alpha beta gamma timeout budget"\n'
            "type: project\n---\nbody\n"
        )
    B.build_index(md, B.default_index_dir(md))

    # (1) direct recall() — the eval path — logs NOTHING (neither the recall ledger nor the
    # episode buffer; eval_recall calls recall() directly so it must never pollute either)
    res = R.recall("alpha beta", memory_dir=md)
    assert res  # it found the memory
    assert _events(td) == []  # ledger untouched by direct recall()
    assert _episodes(td) == []  # episode buffer untouched by direct recall()

    # (2) recall.main() — the hook path — logs exactly ONE event in EACH ledger
    rc = R.main(["alpha", "beta", "--memory-dir", md])
    assert rc == 0
    evs = _events(td)
    assert len(evs) == 1
    assert "alpha_note" in evs[0]["names"]
    assert evs[0]["backend"] == "bm25"  # dense disabled

    eps = _episodes(td)
    assert len(eps) == 1
    assert eps[0]["recalled_names"] == ["alpha_note"]
    assert eps[0]["head_commit"] is None  # --repo-root not passed -> stays hermetic, no leak


def test_recall_main_empty_query_logs_nothing(tmp_path, monkeypatch):
    from memory import recall as R

    td = str(tmp_path / "tele")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    assert R.main([]) == 0  # no query
    assert _events(td) == []
    assert _episodes(td) == []


def test_recall_main_session_id_flag_keys_telemetry(tmp_path, monkeypatch):
    """COR-6: --session-id (as memory_user_prompt.sh threads the harness's session_id)
    keys both ledgers directly, bypassing the shared file-based token."""
    from memory import build_index as B
    from memory import recall as R

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    td = str(tmp_path / "tele")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)

    md = str(tmp_path / ".claude" / "memory")
    os.makedirs(md)
    with open(os.path.join(md, "alpha_note.md"), "w", encoding="utf-8") as fh:
        fh.write(
            '---\nname: alpha_note\ndescription: "alpha beta gamma timeout budget"\n'
            "type: project\n---\nbody\n"
        )
    B.build_index(md, B.default_index_dir(md))

    rc = R.main(["alpha", "beta", "--memory-dir", md, "--session-id", "harness-session-42"])
    assert rc == 0
    assert _events(td)[0]["session_id"] == "harness-session-42"
    assert _episodes(td)[0]["session_id"] == "harness-session-42"
    assert not os.path.exists(T._session_path(td))


# --------------------------------------------------------------------------- #
# session_start.main() opens a new telemetry session (side effect)
# --------------------------------------------------------------------------- #
def test_session_start_main_marks_a_session(tmp_path, monkeypatch):
    import memory.session_start as S

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / ".claude" / "memory")
    os.makedirs(md)
    td = str(tmp_path / ".claude" / ".memory-telemetry")
    monkeypatch.delenv("HIPPO_TELEMETRY_DIR", raising=False)

    monkeypatch.setattr(S, "resolve_dirs", lambda: (md, str(tmp_path)))
    monkeypatch.setattr(S, "build_context", lambda *a, **k: "")  # isolate the side effect
    assert S.main() == 0
    # a session token now exists at the index-sibling telemetry dir
    assert os.path.exists(T._session_path(td))
    assert T.current_session_id(td)


def test_session_start_main_no_stray_telemetry_for_missing_memory_dir(tmp_path, monkeypatch):
    """A bogus/nonexistent memory_dir must NOT create a stray telemetry dir — the dispatcher's
    mark_session side effect is guarded on a real corpus dir (mirrors refresh_index)."""
    import memory.session_start as S

    monkeypatch.delenv("HIPPO_TELEMETRY_DIR", raising=False)
    bogus = str(tmp_path / "does_not_exist" / "memory")
    monkeypatch.setattr(S, "resolve_dirs", lambda: (bogus, str(tmp_path)))
    monkeypatch.setattr(S, "build_context", lambda *a, **k: "")
    assert S.main() == 0
    assert not os.path.exists(T.default_telemetry_dir(bogus))  # no stray ledger dir


# --------------------------------------------------------------------------- #
# SEC-3: derived dirs are self-ignoring
# --------------------------------------------------------------------------- #
def test_telemetry_dir_drops_self_ignoring_gitignore(tmp_path):
    td = str(tmp_path / ".memory-telemetry")
    T.mark_session(td)
    gi = os.path.join(td, ".gitignore")
    assert os.path.exists(gi)
    assert open(gi, encoding="utf-8").read() == "*\n"


def test_existing_gitignore_never_overwritten(tmp_path):
    td = str(tmp_path / ".memory-telemetry")
    os.makedirs(td)
    with open(os.path.join(td, ".gitignore"), "w", encoding="utf-8") as fh:
        fh.write("# user-edited\n")
    T.mark_session(td)
    assert open(os.path.join(td, ".gitignore"), encoding="utf-8").read() == "# user-edited\n"


# --------------------------------------------------------------------------- #
# GRW-2: Hebbian co-recall tally (co_recall_pairs)
# --------------------------------------------------------------------------- #
def _co_session(td, sid, names):
    """One session recalling ``names`` (a single episode is enough — sessions are unioned)."""
    T.log_episode(names, query=f"q-{sid}", telemetry_dir=td, session_id=sid)


def test_co_recall_pairs_needs_min_distinct_sessions(tmp_path):
    td = str(tmp_path / "tele")
    for sid in ("s1", "s2", "s3"):
        _co_session(td, sid, ["bug_workaround", "proxy_quirk"])
    pairs = T.co_recall_pairs(td)
    assert pairs == [{"pair": ["bug_workaround", "proxy_quirk"], "sessions": 3}]


def test_co_recall_below_threshold_proposes_nothing(tmp_path):
    # Two sessions < _CORECALL_MIN_SESSIONS (3): the sparse map STAYS EMPTY — by design.
    td = str(tmp_path / "tele")
    for sid in ("s1", "s2"):
        _co_session(td, sid, ["a", "b"])
    assert T.co_recall_pairs(td) == []


def test_chatty_single_session_counts_once(tmp_path):
    # 5 episodes in ONE session must credit the pair ONE distinct session, not five.
    td = str(tmp_path / "tele")
    for _ in range(5):
        _co_session(td, "one-long-session", ["a", "b"])
    assert T.co_recall_pairs(td, min_sessions=2) == []
    assert T.co_recall_pairs(td, min_sessions=1) == [{"pair": ["a", "b"], "sessions": 1}]


def test_co_recall_unions_names_within_a_session(tmp_path):
    # Names recalled in DIFFERENT episodes of the same session still pair (session-level union).
    td = str(tmp_path / "tele")
    for sid in ("s1", "s2", "s3"):
        _co_session(td, sid, ["a"])
        _co_session(td, sid, ["b"])
    assert T.co_recall_pairs(td) == [{"pair": ["a", "b"], "sessions": 3}]


def test_co_recall_excludes_floor_names(tmp_path):
    # Always-recalled floor memories would dominate every pair — the exclusion drops them
    # BEFORE pairing, so only the non-floor pair survives.
    td = str(tmp_path / "tele")
    for sid in ("s1", "s2", "s3"):
        _co_session(td, sid, ["floor_note", "a", "b"])
    pairs = T.co_recall_pairs(td, exclude_names={"floor_note"})
    assert pairs == [{"pair": ["a", "b"], "sessions": 3}]
    assert all("floor_note" not in p["pair"] for p in pairs)


def test_co_recall_orders_most_sessions_first_deterministically(tmp_path):
    td = str(tmp_path / "tele")
    for sid in ("h1", "h2", "h3", "h4"):
        _co_session(td, sid, ["hot_a", "hot_b"])
    for sid in ("w1", "w2", "w3"):
        _co_session(td, sid, ["warm_x", "warm_y"])
    pairs = T.co_recall_pairs(td)
    assert [p["pair"] for p in pairs] == [["hot_a", "hot_b"], ["warm_x", "warm_y"]]
    assert [p["sessions"] for p in pairs] == [4, 3]


def test_co_recall_pairs_never_raises_on_missing_dir(tmp_path):
    assert T.co_recall_pairs(str(tmp_path / "nope")) == []


# --------------------------------------------------------------------------- #
# GRW-4: the in-session decision ledger (log_decision / read_decisions)
# --------------------------------------------------------------------------- #
def test_log_decision_roundtrip_with_session_keying(tmp_path):
    td = str(tmp_path / "tele")
    assert T.log_decision("ship the v2 schema now, migrate v1 lazily", telemetry_dir=td, session_id="s-A")
    rows = list(T.read_decisions(td))
    assert len(rows) == 1
    assert rows[0]["text"] == "ship the v2 schema now, migrate v1 lazily"
    assert rows[0]["session_id"] == "s-A"
    assert rows[0]["ts"] > 0


def test_log_decision_truncates_and_refuses_empty(tmp_path):
    td = str(tmp_path / "tele")
    assert T.log_decision("   ", telemetry_dir=td) is False, "whitespace-only → nothing recorded"
    assert list(T.read_decisions(td)) == []
    long = "d" * (T._DECISION_MAX_CHARS + 200)
    assert T.log_decision(long, telemetry_dir=td, session_id="s")
    assert len(list(T.read_decisions(td))[0]["text"]) == T._DECISION_MAX_CHARS


def test_decision_ledger_is_self_ignoring_and_never_raises(tmp_path):
    td = str(tmp_path / "tele")
    T.log_decision("x", telemetry_dir=td, session_id="s")
    assert open(os.path.join(td, ".gitignore"), encoding="utf-8").read() == "*\n"
    assert list(T.read_decisions(str(tmp_path / "missing"))) == []
