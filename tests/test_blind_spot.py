"""SIG-3: recall blind-spot mining — silent abstention -> a curation backlog.

Clusters recurring backend='none' (abstained) recall events into a legible, low-frequency
doctor/SessionStart signal. Ships the backend='none' arm ONLY (sub-floor near-miss scores are
never logged). Read-only over the gitignored recall ledger.
"""

from __future__ import annotations

import os

import memory.doctor as D
import memory.session_start as S
from memory import telemetry as T
from memory.telemetry import abstention_backlog, default_telemetry_dir


def _abstain(td, query, sid="s"):
    """Log ONE abstention: empty results -> backend='none'."""
    T.log_recall_event([], query=query, k=6, latency_ms=1.0, telemetry_dir=td, session_id=sid)


def _hit(td, query, sid="s"):
    """Log ONE successful recall (a real backend) -> NOT an abstention."""
    T.log_recall_event(
        [{"name": "m", "backend": "dense+bm25", "score": 0.9, "rank": 1}],
        query=query,
        k=6,
        latency_ms=1.0,
        telemetry_dir=td,
        session_id=sid,
    )


# ---- the analysis (telemetry.abstention_backlog) ------------------------------------------ #
def test_recurring_abstentions_cluster(memory_dir):
    td = default_telemetry_dir(memory_dir)
    _abstain(td, "how do I roll back a deploy")
    _abstain(td, "roll back a deploy safely")
    _abstain(td, "deploy roll back steps")

    backlog = abstention_backlog(td)
    assert len(backlog) == 1
    assert backlog[0]["count"] == 3
    assert {"roll", "back", "deploy"} <= set(backlog[0]["terms"])


def test_one_off_diverse_abstentions_do_not_cluster(memory_dir):
    td = default_telemetry_dir(memory_dir)
    _abstain(td, "how does recall fusion work")
    _abstain(td, "where is the trust gate applied")
    _abstain(td, "what is the corpus format version")

    assert abstention_backlog(td) == []  # three distinct one-offs, none recurring


def test_below_min_count_not_surfaced(memory_dir):
    td = default_telemetry_dir(memory_dir)
    _abstain(td, "roll back a deploy")
    _abstain(td, "roll back the deploy")  # only 2 < min_count
    assert abstention_backlog(td) == []


def test_successful_recalls_are_not_abstentions(memory_dir):
    td = default_telemetry_dir(memory_dir)
    _hit(td, "roll back a deploy")
    _hit(td, "roll back a deploy")
    _hit(td, "roll back a deploy")
    assert abstention_backlog(td) == []  # backend != 'none' -> not a blind spot


def test_deterministic_ordering_by_count(memory_dir):
    td = default_telemetry_dir(memory_dir)
    for _ in range(4):
        _abstain(td, "deploy rollback procedure")
    for _ in range(3):
        _abstain(td, "database migration ordering steps")
    backlog = abstention_backlog(td)
    assert [c["count"] for c in backlog] == [4, 3]  # most-asked first


# ---- the SessionStart producer (blind_spot_producer) -------------------------------------- #
def test_producer_shows_backlog(memory_dir, repo):
    td = default_telemetry_dir(memory_dir)
    for _ in range(3):
        _abstain(td, "how do I roll back a deploy")
    out = S.blind_spot_producer(memory_dir, repo)  # CLAUDE_PLUGIN_DATA stripped -> fires
    assert out is not None
    assert out.startswith("🔎 Recall blind spots")
    assert "roll back a deploy" in out and "asked 3×" in out


def test_producer_silent_without_recurring_backlog(memory_dir, repo):
    td = default_telemetry_dir(memory_dir)
    _abstain(td, "a one-off question about something")
    assert S.blind_spot_producer(memory_dir, repo) is None


def test_producer_low_frequency_gate(memory_dir, repo, tmp_path, monkeypatch):
    """With CLAUDE_PLUGIN_DATA set, the nudge fires on the 1st backlog session and every 5th."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    td = default_telemetry_dir(memory_dir)
    for _ in range(3):
        _abstain(td, "how do I roll back a deploy")
    fired = [bool(S.blind_spot_producer(memory_dir, repo)) for _ in range(6)]
    assert fired == [True, False, False, False, False, True]


# ---- the doctor check (always-available surface) ------------------------------------------ #
def test_doctor_reports_backlog(memory_dir, repo):
    td = default_telemetry_dir(memory_dir)
    for _ in range(3):
        _abstain(td, "how do I roll back a deploy")
    r = D.check_recall_blind_spots(D.DoctorContext(memory_dir, repo))
    assert r["status"] == "warn"
    assert "recall blind spots: 1 recurring" in r["message"]
    assert "roll back a deploy" in r["message"]


def test_doctor_ok_when_empty(memory_dir, repo):
    r = D.check_recall_blind_spots(D.DoctorContext(memory_dir, repo))
    assert r["status"] == "ok"
    assert "none" in r["message"]


def test_wired_into_producers_and_checks():
    assert any(label == "blind_spot" for label, _fn in S.PRODUCERS)
    assert "recall_blind_spots" in [label for label, _ in D.CHECKS]


def test_bogus_dir_never_raises(tmp_path):
    bogus = str(tmp_path / "nope")
    assert abstention_backlog(bogus) == []
    assert S.blind_spot_producer(bogus, bogus) is None


# ---- harness envelopes are not questions -------------------------------------------------- #
# A hook older than HOT-1 logged the raw prompt, so the ledger still holds abstained "queries"
# that are the harness talking: another local session's message, a subagent's hand-back, a
# background task's result, a `!`-bash turn. Same-shaped envelopes share their tag and sender
# tokens, so they cluster, and every surface reading the backlog asked the human about them.
_ENVELOPE_ABSTENTIONS = (
    [f'<cross-session-message from="uds:/tmp/cc-socks/9f2c.sock">\nPR {n} merged, branch gone' for n in range(7)]
    + [f'<agent-message from="a41c{n}">\nDone. The worktree sweep found no stale branches' for n in range(3)]
    + [f"<task-notification>\n<task-id>b7{n}</task-id>\n<status>completed</status>" for n in range(3)]
    + ["<bash-input>git status --short</bash-input>" for _ in range(3)]
)
_REAL = "how do I roll back a deploy"
_MENTIONS = "why does an agent-message hand-back draft as a fixture"  # names a tag, opens with none


def _seed_envelopes_and_real_queries(td):
    for q in _ENVELOPE_ABSTENTIONS:
        _abstain(td, q)
    for _ in range(3):
        _abstain(td, _REAL)
        _abstain(td, _MENTIONS)


def test_harness_envelopes_are_not_blind_spots(memory_dir):
    td = default_telemetry_dir(memory_dir)
    _seed_envelopes_and_real_queries(td)

    backlog = abstention_backlog(td)
    assert sorted((c["sample_query"], c["count"]) for c in backlog) == [
        (_REAL, 3),
        (_MENTIONS, 3),
    ]


def test_backlog_consumers_never_ask_about_envelopes(memory_dir, repo, monkeypatch):
    """SessionStart, doctor, the consolidate interview and the fixture drafts all read the
    backlog, so none of them may surface an envelope; the real recurring query still does."""
    from memory import eval_recall as E
    from memory import interview as IV

    monkeypatch.delenv("HIPPO_PENDING_DIR", raising=False)
    td = default_telemetry_dir(memory_dir)
    _seed_envelopes_and_real_queries(td)
    tags = ("<cross-session-message", "<agent-message", "<task-notification", "<bash-input")

    producer = S.blind_spot_producer(memory_dir, repo)
    doctor = D.check_recall_blind_spots(D.DoctorContext(memory_dir, repo))["message"]
    questions = [q["question"] for q in IV.gather_questions(memory_dir, repo_root=repo, telemetry_dir=td)]
    drafted = E.draft_abstention_fixtures(memory_dir, telemetry_dir=td, probe=False)["added"]

    for surface in [producer, doctor, *questions, *drafted]:
        assert not any(t in surface for t in tags), surface
    assert _REAL in producer
    assert any(_REAL in q for q in questions)
    assert sorted(drafted) == sorted([_REAL, _MENTIONS])
