"""Tests for memory/soak.py — the soak ledger + curation analyzer (read-only).

Hermetic: synthesizes a ledger of N sessions in a tmp dir and a tiny tmp corpus; nothing
touches the real ~/.claude or the repo ledger.
"""

from __future__ import annotations

import json
import os

import memory.soak as soak


def _seed(td, events):
    """Write raw recall events (JSON lines) into a tmp telemetry dir."""
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "recall_events.jsonl"), "w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")


def _corpus(md, names):
    os.makedirs(md, exist_ok=True)
    for n in names:
        with open(os.path.join(md, f"{n}.md"), "w", encoding="utf-8") as fh:
            fh.write(f'---\nname: {n}\ndescription: "{n} description"\ntype: project\n---\nbody\n')


def _sessions(n, name="a", backend="bm25"):
    return [{"session_id": f"s{i}", "names": [name], "backend": backend} for i in range(n)]


# --------------------------------------------------------------------------- #
# soak_status — the >=5-session curation-soak bar
# --------------------------------------------------------------------------- #
def test_gate_not_met_below_five(tmp_path):
    td = str(tmp_path / "tele")
    _seed(td, _sessions(4))
    st = soak.soak_status(td)
    assert st["distinct_sessions"] == 4
    assert st["gate_met"] is False


def test_gate_flips_exactly_at_five(tmp_path):
    td = str(tmp_path / "tele")
    _seed(td, _sessions(5))
    st = soak.soak_status(td)
    assert st["distinct_sessions"] == 5
    assert st["gate_threshold"] == 5
    assert st["gate_met"] is True


def test_distinct_sessions_dedups_by_session_id(tmp_path):
    td = str(tmp_path / "tele")
    _seed(
        td,
        [
            {"session_id": "s1", "names": ["a"], "backend": "bm25"},
            {"session_id": "s1", "names": ["b"], "backend": "bm25"},  # same session
            {"session_id": "s2", "names": ["a"], "backend": "dense"},
        ],
    )
    st = soak.soak_status(td)
    assert st["distinct_sessions"] == 2
    assert st["total_events"] == 3


def test_soak_status_empty_ledger_never_raises(tmp_path):
    st = soak.soak_status(str(tmp_path / "missing"))
    assert st["distinct_sessions"] == 0
    assert st["gate_met"] is False


# --------------------------------------------------------------------------- #
# curation_report — dead weight + bm25-fallback rate
# --------------------------------------------------------------------------- #
def test_curation_never_recalled_and_bm25_rate(tmp_path):
    md = str(tmp_path / ".claude" / "memory")
    _corpus(md, ["a", "b", "c"])
    td = str(tmp_path / "tele")
    _seed(
        td,
        [
            {"session_id": "s1", "names": ["a", "b"], "backend": "dense+bm25"},
            {"session_id": "s2", "names": ["a"], "backend": "bm25"},  # bm25-only (fallback)
            {"session_id": "s3", "names": [], "backend": "none"},  # empty recall (not serving)
        ],
    )
    rep = soak.curation_report(md, td)
    assert rep["per_memory_hits"] == {"a": 2, "b": 1}
    assert rep["never_recalled"] == ["c"]  # c never surfaced -> dead weight
    assert rep["corpus_count"] == 3
    assert rep["recalled_count"] == 2
    # serving events: s1 (dense+bm25) + s2 (bm25) = 2; bm25-only = 1 -> rate 0.5
    assert rep["serving_events"] == 2
    assert rep["bm25_fallback_events"] == 1
    assert rep["bm25_fallback_rate"] == 0.5


def test_curation_all_recalled_no_dead_weight(tmp_path):
    md = str(tmp_path / ".claude" / "memory")
    _corpus(md, ["a", "b"])
    td = str(tmp_path / "tele")
    _seed(
        td,
        [
            {"session_id": "s1", "names": ["a"], "backend": "dense+bm25"},
            {"session_id": "s2", "names": ["b"], "backend": "dense"},
        ],
    )
    rep = soak.curation_report(md, td)
    assert rep["never_recalled"] == []
    assert rep["bm25_fallback_rate"] == 0.0  # no bm25-only events


def test_curation_empty_ledger_never_raises(tmp_path):
    md = str(tmp_path / ".claude" / "memory")
    _corpus(md, ["a"])
    rep = soak.curation_report(md, str(tmp_path / "missing"))
    assert rep["total_events"] == 0
    assert rep["never_recalled"] == ["a"]  # nothing recalled -> all corpus is dead weight
    assert rep["bm25_fallback_rate"] == 0.0


# --------------------------------------------------------------------------- #
# compute_strength_scores — topic-bias-resistant distinct-session fraction (report-only)
# --------------------------------------------------------------------------- #
def test_strength_is_distinct_sessions_not_raw_hits(tmp_path):
    """A memory hit 3x in ONE session must NOT outscore one hit once in each of 2 sessions."""
    td = str(tmp_path / "tele")
    _seed(
        td,
        [
            {"session_id": "s1", "names": ["chatty", "chatty", "chatty"], "backend": "bm25"},
            {"session_id": "s2", "names": ["spread"], "backend": "bm25"},
            {"session_id": "s3", "names": ["spread"], "backend": "bm25"},
        ],
    )
    scores = soak.compute_strength_scores(td)
    # "chatty" appears 3x in raw events but only within s1 -> 1 distinct session.
    assert scores["chatty"] == round(1 / 3, 4)
    assert scores["spread"] == round(2 / 3, 4)
    assert scores["spread"] > scores["chatty"]


def test_strength_denominator_is_full_session_pool(tmp_path):
    """Denominator is ALL distinct sessions logged, not just sessions that recalled something."""
    td = str(tmp_path / "tele")
    _seed(
        td,
        [
            {"session_id": "s1", "names": ["a"], "backend": "bm25"},
            {"session_id": "s2", "names": [], "backend": "none"},  # recalled nothing
            {"session_id": "s3", "names": [], "backend": "none"},
        ],
    )
    scores = soak.compute_strength_scores(td)
    assert scores["a"] == round(1 / 3, 4)  # 1 of 3 total sessions, not 1 of 1


def test_strength_memory_recalled_every_session_scores_one(tmp_path):
    td = str(tmp_path / "tele")
    _seed(td, _sessions(5, name="always"))
    scores = soak.compute_strength_scores(td)
    assert scores["always"] == 1.0


def test_strength_never_recalled_memory_is_absent_not_zero_entry(tmp_path):
    td = str(tmp_path / "tele")
    _seed(td, _sessions(5, name="a"))
    scores = soak.compute_strength_scores(td)
    assert "never_recalled_name" not in scores  # absent, read as 0.0 by convention


def test_strength_empty_ledger_returns_empty_dict_never_raises(tmp_path):
    assert soak.compute_strength_scores(str(tmp_path / "missing")) == {}


def test_strength_is_readonly_over_ledger(tmp_path):
    td = str(tmp_path / "tele")
    _seed(td, _sessions(5))
    ledger = os.path.join(td, "recall_events.jsonl")
    before = open(ledger, "rb").read()
    soak.compute_strength_scores(td)
    after = open(ledger, "rb").read()
    assert before == after


def test_strength_does_not_change_curation_report_ranking():
    """REPORT-ONLY guarantee: compute_strength_scores is a standalone function with no
    hook into curation_report's per_memory_hits / never_recalled ranking output."""
    import inspect

    src = inspect.getsource(soak.curation_report)
    assert "compute_strength_scores" not in src


# --------------------------------------------------------------------------- #
# no SessionStart producer — the Option-C soak announcer was removed
# --------------------------------------------------------------------------- #
def test_no_soak_producer_symbol():
    # The Option-C auto-extraction draft queue was killed; the soak announcer that advertised
    # it must NOT exist (a met gate must never resurrect-by-accident a killed feature).
    assert not hasattr(soak, "soak_producer")


def test_soak_not_registered_in_dispatcher():
    import memory.session_start as S

    assert all(label != "soak" for label, _fn in S.PRODUCERS)


# --------------------------------------------------------------------------- #
# read-only invariant — soak never mutates the ledger
# --------------------------------------------------------------------------- #
def test_soak_is_readonly_over_ledger(tmp_path, monkeypatch):
    monkeypatch.delenv("HIPPO_TELEMETRY_DIR", raising=False)
    md = str(tmp_path / ".claude" / "memory")
    _corpus(md, ["a", "b"])
    td = str(tmp_path / ".claude" / ".memory-telemetry")
    _seed(td, _sessions(5))
    ledger = os.path.join(td, "recall_events.jsonl")
    before = open(ledger, "rb").read()

    soak.soak_status(td)
    soak.curation_report(md, td)

    after = open(ledger, "rb").read()
    assert before == after  # the ledger is never mutated


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_main_empty_ledger_never_raises(tmp_path, capsys):
    md = str(tmp_path / ".claude" / "memory")
    os.makedirs(md)
    rc = soak.main(["--memory-dir", md, "--telemetry-dir", str(tmp_path / "missing")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "soak ledger" in out.lower()
    assert "pending" in out.lower()  # bar not met on an empty ledger


def test_main_prints_report_when_gate_met(tmp_path, capsys):
    md = str(tmp_path / ".claude" / "memory")
    _corpus(md, ["a", "b", "c"])
    td = str(tmp_path / "tele")
    _seed(td, _sessions(5, name="a"))
    rc = soak.main(["--memory-dir", md, "--telemetry-dir", td])
    assert rc == 0
    out = capsys.readouterr().out
    assert "MET" in out
    assert "never recalled" in out.lower()
    assert "strength scores" in out.lower()
    assert "a: 1.0" in out


def test_main_omits_strength_section_on_empty_ledger(tmp_path, capsys):
    md = str(tmp_path / ".claude" / "memory")
    os.makedirs(md)
    rc = soak.main(["--memory-dir", md, "--telemetry-dir", str(tmp_path / "missing")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "strength scores" not in out.lower()
