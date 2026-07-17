"""TMB-5: succession replay — verify a supersede's recall surface transfers.

On ``semantic_reverify --outcome demote --superseded-by``, the queries that historically
recalled the OLD name (recall-event ``query_preview``s — the LIVE fields, never the dark
scores/ranks arrays) are re-run offline against the post-verdict corpus and classified
PASS / FAIL / INCONCLUSIVE. Read-only beyond the verdict's own writes; fires only inside
the existing single-item demote path (the no-bulk signature pin in test_reconsolidate
still covers it — no replay_all verb exists); outcomes append ONLY to the existing
reconsolidation ledger (counts, never query text) — no new ledger, no new outcome value.
"""

from __future__ import annotations

import json
import os

import memory.doctor as D
import memory.reconsolidate as R
from memory import telemetry as T


def _mem(md, name, description, body="Body."):
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write(f'---\nname: {name}\ndescription: "{description}"\n---\n{body}\n')


def _seed_preview_events(td, rows):
    """rows: [(names, query_preview)] appended in order."""
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "recall_events.jsonl"), "a", encoding="utf-8") as fh:
        for names, preview in rows:
            fh.write(
                json.dumps(
                    {"session_id": "s1", "names": names, "query_preview": preview, "backend": "bm25"}
                )
                + "\n"
            )


def _demote(md, repo, td, old, new, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    return R.semantic_reverify(old, "demote", md, repo, telemetry_dir=td, superseded_by=new)


# --------------------------------------------------------------------------- #
# the three verdicts
# --------------------------------------------------------------------------- #
def test_replay_pass_when_successor_ranks(memory_dir, repo, tmp_path, monkeypatch):
    td = str(tmp_path / "tele")
    _mem(memory_dir, "retry-v1", "retry policy waits a fixed three seconds")
    _mem(memory_dir, "retry-v2", "retry policy uses exponential backoff with jitter")
    _seed_preview_events(td, [(["retry-v1"], "retry policy")])
    r = _demote(memory_dir, repo, td, "retry-v1", "retry-v2", monkeypatch)
    assert r["error"] is None and r["invalidated"] and r["edge_written"]
    replay = r["succession_replay"]
    assert replay["harvested"] == 1
    assert [q["verdict"] for q in replay["queries"]] == ["PASS"]
    assert replay["queries"][0]["successor_rank"] is not None
    assert replay["counts"] == {"pass": 1, "fail": 0, "inconclusive": 0}


def test_replay_fail_when_tombstone_leaks_and_successor_absent(
    memory_dir, repo, tmp_path, monkeypatch
):
    td = str(tmp_path / "tele")
    _mem(memory_dir, "ingress-timeout", "kubernetes ingress timeout is ninety seconds")
    _mem(memory_dir, "unrelated-successor", "completely different vocabulary about billing")
    _seed_preview_events(td, [(["ingress-timeout"], "kubernetes ingress timeout")])
    r = _demote(memory_dir, repo, td, "ingress-timeout", "unrelated-successor", monkeypatch)
    replay = r["succession_replay"]
    assert [q["verdict"] for q in replay["queries"]] == ["FAIL"]
    assert replay["queries"][0]["old_rank"] is not None  # the tombstone still surfaces
    assert replay["queries"][0]["successor_rank"] is None
    assert replay["counts"]["fail"] == 1


def test_replay_inconclusive_when_neither_ranks(memory_dir, repo, tmp_path, monkeypatch):
    td = str(tmp_path / "tele")
    _mem(memory_dir, "old-fact", "the deploy pipeline caches artifacts")
    _mem(memory_dir, "new-fact", "the deploy pipeline streams artifacts")
    _seed_preview_events(td, [(["old-fact"], "zzz qqq xyzzy")])  # matches neither, post-truncation shape
    r = _demote(memory_dir, repo, td, "old-fact", "new-fact", monkeypatch)
    replay = r["succession_replay"]
    assert [q["verdict"] for q in replay["queries"]] == ["INCONCLUSIVE"]
    assert replay["counts"]["inconclusive"] == 1


def test_replay_nothing_to_replay_reports_never_fabricates(
    memory_dir, repo, tmp_path, monkeypatch, capsys
):
    """Zero prior hits -> 'nothing to replay', no error, no invented query; the CLI says so."""
    td = str(tmp_path / "tele")
    _mem(memory_dir, "quiet-old", "a memory nothing ever recalled")
    _mem(memory_dir, "quiet-new", "its successor")
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    rc = R.main([
        "--reverify", "quiet-old", "--outcome", "demote", "--superseded-by", "quiet-new",
        "--memory-dir", memory_dir, "--repo-root", repo, "--telemetry-dir", td,
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "nothing to replay" in out
    assert "no query is fabricated" in out


def test_cli_prints_one_line_per_harvested_query(memory_dir, repo, tmp_path, monkeypatch, capsys):
    td = str(tmp_path / "tele")
    _mem(memory_dir, "retry-v1", "retry policy waits a fixed three seconds")
    _mem(memory_dir, "retry-v2", "retry policy uses exponential backoff with jitter")
    _seed_preview_events(
        td,
        [(["retry-v1"], "retry policy"), (["retry-v1"], "zzz qqq xyzzy")],
    )
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    rc = R.main([
        "--reverify", "retry-v1", "--outcome", "demote", "--superseded-by", "retry-v2",
        "--memory-dir", memory_dir, "--repo-root", repo, "--telemetry-dir", td,
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "succession replay (2 historical queries" in out
    assert out.count("PASS") == 1 and out.count("INCONCLUSIVE") == 1
    assert "retry policy" in out  # the query text is shown at verdict time


# --------------------------------------------------------------------------- #
# ledger discipline
# --------------------------------------------------------------------------- #
def test_replay_summary_rides_the_existing_ledger_event_counts_only(
    memory_dir, repo, tmp_path, monkeypatch
):
    td = str(tmp_path / "tele")
    _mem(memory_dir, "retry-v1", "retry policy waits a fixed three seconds")
    _mem(memory_dir, "retry-v2", "retry policy uses exponential backoff with jitter")
    _seed_preview_events(td, [(["retry-v1"], "retry policy")])
    outcomes_before = set(T._RECONSOLIDATION_OUTCOMES)
    _demote(memory_dir, repo, td, "retry-v1", "retry-v2", monkeypatch)
    events = [e for e in T.read_reconsolidation_events(td) if e.get("outcome") == "demote"]
    assert len(events) == 1
    replay = events[0]["succession_replay"]
    assert replay == {"harvested": 1, "pass": 1, "fail": 0, "inconclusive": 0}
    assert "queries" not in replay  # counts only — never query text in this ledger
    assert set(T._RECONSOLIDATION_OUTCOMES) == outcomes_before  # no new outcome value
    ledgers = {f for f in os.listdir(td) if f.endswith(".jsonl")}
    assert ledgers == {"recall_events.jsonl", "reconsolidation_events.jsonl"}  # no new ledger


def test_dry_run_skips_the_replay(memory_dir, repo, tmp_path, monkeypatch):
    td = str(tmp_path / "tele")
    _mem(memory_dir, "retry-v1", "retry policy waits a fixed three seconds")
    _mem(memory_dir, "retry-v2", "retry policy uses exponential backoff with jitter")
    _seed_preview_events(td, [(["retry-v1"], "retry policy")])
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    r = R.semantic_reverify(
        "retry-v1", "demote", memory_dir, repo, telemetry_dir=td,
        superseded_by="retry-v2", dry_run=True,
    )
    assert r["error"] is None
    assert r["succession_replay"] is None  # a preview writes nothing and replays nothing


# --------------------------------------------------------------------------- #
# the doctor line
# --------------------------------------------------------------------------- #
def test_doctor_succession_replay_states(memory_dir, repo, tmp_path, monkeypatch):
    import memory.build_index as B

    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "tele-doctor"))
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    ctx = D.DoctorContext(memory_dir, repo)
    # no corpus/edges -> ok, single line
    _mem(memory_dir, "standalone", "no edges here")
    B.build_index(memory_dir, B.default_index_dir(memory_dir))
    r = D.check_succession_replay(ctx)
    assert r["status"] == "ok" and "no supersedes edges" in r["message"]
    # a hand-authored supersedes edge with no demote event -> unrun -> warn, one line
    with open(os.path.join(memory_dir, "hand-new.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: hand-new\ndescription: "newer"\nsupersedes: [hand-old]\n---\nB\n')
    _mem(memory_dir, "hand-old", "older")
    B.build_index(memory_dir, B.default_index_dir(memory_dir))
    r = D.check_succession_replay(ctx)
    assert r["status"] == "warn"
    assert "never replayed" in r["message"] and "hand-new→hand-old" in r["message"]
    assert "\n" not in r["message"]


def test_doctor_succession_replay_ok_after_a_passing_verdict(
    memory_dir, repo, tmp_path, monkeypatch
):
    td = str(tmp_path / "tele")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    _mem(memory_dir, "retry-v1", "retry policy waits a fixed three seconds")
    _mem(memory_dir, "retry-v2", "retry policy uses exponential backoff with jitter")
    _seed_preview_events(td, [(["retry-v1"], "retry policy")])
    r = _demote(memory_dir, repo, td, "retry-v1", "retry-v2", monkeypatch)
    assert r["succession_replay"]["counts"]["fail"] == 0
    ctx = D.DoctorContext(memory_dir, repo)
    res = D.check_succession_replay(ctx)
    assert res["status"] == "ok"
    assert "1 supersede pair(s), none failing or unreplayed" in res["message"]


def test_doctor_succession_replay_registered_before_trailing_env_check():
    labels = [label for label, _fn in D.CHECKS]
    assert "succession_replay" in labels
    assert labels[-1] == "stale_memobot_env"
