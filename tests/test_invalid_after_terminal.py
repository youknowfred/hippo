"""TMB-2: invalid_after terminal-state surfacing + the archive-admission retirement leg.

A memory retired via supersede/merge (invalid_after stamped, NO cited-code drift) never
enters ``find_stale``'s set, so pre-TMB-2 it signaled NOWHERE: recall display-filters it
("old" state), the staleness producer's invalid_after map is stale-scoped, and
archive_candidates was stale-gated 4-way. These tests pin the two additive fixes: the
corpus-wide count (producer line + doctor check) and the 5th admission leg into the
shipped GRA-5-guarded archive flow. No new verb anywhere — reinstatement stays
``semantic_reverify(name, outcome='graduate'|'fix')`` (``reverify_file`` strips the
stamp), and nothing routes through ``recalled_stale_worklist`` (its LIF-1 exclusion of
invalidated items is deliberate).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import memory.archive as A
import memory.doctor as D
import memory.session_start as S
import memory.staleness as ST
from memory import telemetry as T

_OLD_TS = "2020-01-01T00:00:00+00:00"  # decades past the 30-day horizon


def _mem(md, name, *, invalid_after=None, cited=None, source_commit=None, body="Body.", link=None):
    os.makedirs(md, exist_ok=True)
    lines = ["---", f"name: {name}", f'description: "claim of {name}"', "metadata:", "  type: project"]
    if cited:
        lines.append(f"  cited_paths: {json.dumps(cited)}")
    if source_commit:
        lines.append(f'  source_commit: "{source_commit}"')
    if invalid_after:
        lines.append(f'invalid_after: "{invalid_after}"')
    lines += ["---", "", body]
    if link:
        lines.append(f"see [[{link}]]")
    lines.append("")
    with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


# --------------------------------------------------------------------------- #
# the corpus-wide read (staleness helpers)
# --------------------------------------------------------------------------- #
def test_nondrift_old_excludes_stale_recent_and_unstamped(memory_dir):
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _mem(memory_dir, "retired-old", invalid_after=_OLD_TS)
    _mem(memory_dir, "retired-recent", invalid_after=recent)
    _mem(memory_dir, "plain")
    _mem(memory_dir, "stale-and-stamped", invalid_after=_OLD_TS)
    assert ST.invalid_after_all(memory_dir) == {
        "retired-old": _OLD_TS,
        "retired-recent": recent,
        "stale-and-stamped": _OLD_TS,
    }
    # stale_names subtraction: the drift signal already owns stale-and-stamped's story
    got = ST.nondrift_old_invalidated(memory_dir, {"stale-and-stamped"})
    assert got == {"retired-old": _OLD_TS}


def test_nondrift_old_horizon_boundary(memory_dir):
    """The horizon is recall's own classifier — one implementation, tested at the seam."""
    from memory.recall import _INVALIDATION_RECENT_DAYS

    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    just_recent = (now - timedelta(days=_INVALIDATION_RECENT_DAYS - 1)).isoformat()
    just_old = (now - timedelta(days=_INVALIDATION_RECENT_DAYS + 1)).isoformat()
    _mem(memory_dir, "edge-recent", invalid_after=just_recent)
    _mem(memory_dir, "edge-old", invalid_after=just_old)
    got = ST.nondrift_old_invalidated(memory_dir, set(), now=now.timestamp())
    assert set(got) == {"edge-old"}


# --------------------------------------------------------------------------- #
# the SessionStart producer line (the AC's "signals nowhere" fixture)
# --------------------------------------------------------------------------- #
def test_producer_surfaces_retirement_with_no_drift(memory_dir, repo):
    """The exact TMB-2 fixture: invalid_after but no cited_paths/drift — pre-TMB-2 this
    memory produced NOTHING here (find_stale skips it, so the producer returned None)."""
    _mem(memory_dir, "retired-quietly", invalid_after=_OLD_TS)
    out = S.staleness_producer(memory_dir, repo, None)
    assert out is not None
    assert "retired OUTSIDE the drift signal" in out
    assert "retired-quietly" in out
    assert "archive" in out  # points at the shipped flow
    assert "graduate|fix" in out  # reinstatement is the existing verbs, no new one


def test_producer_byte_identical_when_no_retirements(memory_dir, repo):
    """Cheap-at-zero: no such memories -> no extra output (None on a clean corpus)."""
    _mem(memory_dir, "plain")
    assert S.staleness_producer(memory_dir, repo, None) is None
    # a RECENT invalidation is recall's penalty-phase story, not a retirement line
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _mem(memory_dir, "fresh-demote", invalid_after=recent)
    assert S.staleness_producer(memory_dir, repo, None) is None


# --------------------------------------------------------------------------- #
# the doctor check
# --------------------------------------------------------------------------- #
def test_doctor_invalid_after_terminal_ok_at_zero_and_warns_on_fixture(memory_dir, repo):
    ctx = D.DoctorContext(memory_dir, repo)
    _mem(memory_dir, "plain")
    r = D.check_invalid_after_terminal(ctx)
    assert r["status"] == "ok" and "no memories retired" in r["message"]
    _mem(memory_dir, "retired-quietly", invalid_after=_OLD_TS)
    r = D.check_invalid_after_terminal(ctx)
    assert r["status"] == "warn"
    assert "retired-quietly" in r["message"]
    assert "\n" not in r["message"]  # one check = one single-line message


def test_doctor_invalid_after_terminal_registered_before_trailing_env_check():
    labels = [label for label, _fn in D.CHECKS]
    assert "invalid_after_terminal" in labels
    assert labels[-1] == "stale_memobot_env"  # the pinned-last check still trails


# --------------------------------------------------------------------------- #
# the archive-admission leg (end-to-end into the shipped GRA-5-guarded flow)
# --------------------------------------------------------------------------- #
def _meet_soak_gate(td):
    for i in range(5):
        T.log_recall_event(
            [], query=f"q{i}", k=5, latency_ms=1.0, telemetry_dir=td, session_id=f"s{i}"
        )


def test_archive_candidates_admits_retired_memory_absent_from_find_stale(
    memory_dir, repo, tmp_path
):
    td = str(tmp_path / "telemetry")
    _meet_soak_gate(td)
    _mem(memory_dir, "retired-quietly", invalid_after=_OLD_TS)
    _mem(memory_dir, "plain")
    assert ST.find_stale(memory_dir, repo) == []  # truly absent from the drift set
    diagnostics = {}
    cands = A.archive_candidates(memory_dir, repo, telemetry_dir=td, diagnostics=diagnostics)
    assert [c["name"] for c in cands] == ["retired-quietly"]
    assert cands[0]["invalid_after_old"] is True
    assert cands[0]["invalid_after"] == _OLD_TS
    assert cands[0]["changed_paths"] == []  # a retirement, not a drift hit

    # end-to-end: the listed candidate reaches a JOURNALED archive move (report-then-
    # approve — the approve is this explicit per-item call, the GRA-5-guarded primitive)
    r = A.archive_memory("retired-quietly", memory_dir, repo)
    assert r["moved"] is True and r["error"] is None
    assert os.path.isfile(os.path.join(memory_dir, "archive", "retired-quietly.md"))
    journal = os.path.join(memory_dir, "archive", A._JOURNAL_NAME)
    assert os.path.isfile(journal)  # untracked file -> os.rename fallback, journaled
    with open(journal, encoding="utf-8") as fh:
        assert any(
            json.loads(ln)["name"] == "retired-quietly.md" for ln in fh if ln.strip()
        )


def test_retirement_leg_respects_the_zero_inbound_and_citation_gates(
    memory_dir, repo, tmp_path
):
    td = str(tmp_path / "telemetry")
    _meet_soak_gate(td)
    # inbound-referenced: another memory wikilinks it -> NOT a candidate (gate verbatim)
    _mem(memory_dir, "retired-linked", invalid_after=_OLD_TS)
    _mem(memory_dir, "referrer", link="retired-linked")
    import memory.build_index as B

    B.build_index(memory_dir, B.default_index_dir(memory_dir))
    cands = A.archive_candidates(memory_dir, repo, telemetry_dir=td)
    assert "retired-linked" not in [c["name"] for c in cands]
    # and even a FORCED listing attempt hits the GRA-5 guard at the move itself
    r = A.archive_memory("retired-linked", memory_dir, repo)
    assert r["refused"] is True and "referrer" in (r["error"] or "")


def test_retirement_leg_withheld_with_soak_gate_unmet(memory_dir, repo, tmp_path):
    td = str(tmp_path / "telemetry-empty")
    _mem(memory_dir, "retired-quietly", invalid_after=_OLD_TS)
    diagnostics = {}
    cands = A.archive_candidates(memory_dir, repo, telemetry_dir=td, diagnostics=diagnostics)
    assert cands == []
    assert diagnostics.get("reason") == "soak_gate_unmet"  # the soak gate reused verbatim


def test_retired_memories_never_route_through_the_worklist(memory_dir, repo, tmp_path):
    """The LIF-1 exclusion is deliberate: the worklist re-nags UNSETTLED items; a
    retirement is settled. TMB-2's surfacing must not undo that."""
    from memory.reconsolidate import recalled_stale_worklist

    td = str(tmp_path / "telemetry")
    _mem(memory_dir, "retired-quietly", invalid_after=_OLD_TS)
    T.log_recall_event(
        [{"name": "retired-quietly", "backend": "bm25", "score": 0.5, "rank": 1}],
        query="q", k=5, latency_ms=1.0, telemetry_dir=td, session_id="s1",
    )
    assert recalled_stale_worklist(memory_dir, repo, telemetry_dir=td) == []
