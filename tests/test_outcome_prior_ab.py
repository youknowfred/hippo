"""MEA-5: the EVD-4 Arm B un-sever — HIPPO_OUTCOME_PRIOR gets its A/B harness.

Exactly the named minimal delta: AB_FLAGS entry + --ab dispatch + a GENERALIZED
flag-context arm runner extracted from the salience_eval pattern (inv5 — one core, not a
second copy) + an outcome-signal precondition. Measures the EXISTING RET-14 flag only;
nothing flips (ED-2/LIF-7); the touch-grain graduation arm stays severed. The pins:

  AC1  AB_FLAGS carries HIPPO_OUTCOME_PRIOR; eval --ab dispatches to the harness;
       unknown flags still refuse (whitelist).
  AC2  the arm runner is ONE generalized core (ab_runner.run_flag_arms): salience_eval
       routes through it (no second copy — source pin); OFF-arm byte-identity
       self-check, per-category n, low-n labels, dated footer all preserved.
  AC3  outcome-signal precondition: a signal-less ON arm reports as SELF-CHECK, not
       finding (ED-3 posture); the report says so explicitly.
  AC4  the flag-OFF cache problem: the harness writes outcome.json in-process for the
       ON arm and RESTORES prior state after (no cache left behind under flag-OFF; a
       pre-existing cache is preserved). HIPPO_OUTCOME_PRIOR restored exactly.
  AC5  the evidence file lands beside salience_ab.json (gitignored telemetry dir) with
       the ED-2-style footer AND MEA-1's resolvable_by_category stamp; its named
       standing reader is check_salience_evidence's sibling sentence (decided at build).
"""

from __future__ import annotations

import inspect
import json
import os

import pytest

from memory import ab_runner as AB
from memory import outcome_prior_eval as OP
from memory import salience_eval as S
from memory.build_index import build_index, default_index_dir
from memory.telemetry import default_telemetry_dir


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.delenv("HIPPO_OUTCOME_PRIOR", raising=False)


def _corpus(md, cited=False):
    for stem in ("alpha-notes", "beta-notes"):
        cp = '\n  cited_paths: ["src/a.py"]' if cited and stem == "alpha-notes" else ""
        with open(os.path.join(md, f"{stem}.md"), "w", encoding="utf-8") as fh:
            fh.write(
                f"---\nname: {stem}\ndescription: \"notes about {stem}\"\n"
                f"metadata:\n  type: project{cp}\n---\nbody\n"
            )
    idx = default_index_dir(md)
    build_index(md, idx)
    return idx


def test_ab_flags_whitelist_carries_the_flag():
    from memory.dream_eval import AB_FLAGS

    assert "HIPPO_OUTCOME_PRIOR" in AB_FLAGS
    from memory import eval_recall as E

    assert E.main(["--ab", "NOT_A_FLAG"]) == 2


def test_flag_context_is_generalized_not_copied():
    """inv5: one flag save/restore context; salience_eval routes through it."""
    prev = os.environ.get("SOME_TEST_FLAG")
    with AB.flag_context("SOME_TEST_FLAG", True):
        assert os.environ["SOME_TEST_FLAG"] == "1"
    assert os.environ.get("SOME_TEST_FLAG") == prev
    src = inspect.getsource(S)
    assert "flag_context(" in src            # salience delegates…
    assert "run_flag_arms(" in src           # …and uses the shared core
    # the harnesses own no private copy of the OFF->ON->OFF loop
    assert "_arm(False)" not in inspect.getsource(OP.run_ab)


def test_signal_less_run_reports_self_check_not_finding(memory_dir):
    idx = _corpus(memory_dir)
    td = default_telemetry_dir(memory_dir)
    report = OP.run_ab(memory_dir=memory_dir, index_dir=idx, telemetry_dir=td, write=False)
    assert report.get("ok"), report.get("error")
    assert report["signal"]["outcome_confirmed_memories"] == 0
    assert report["identical_arms"] is True
    assert "not a finding" in report["identical_arms_note"].lower()
    assert "self-check" in report["identical_arms_note"].lower()
    # ED-2-style footer + MEA-1 sensitivity stamp ride the report
    assert "default-OFF" in report["ed2"]
    assert "resolvable_by_category" in report["condition"]
    # and nothing leaked: the flag is not set, no cache exists
    assert "HIPPO_OUTCOME_PRIOR" not in os.environ
    assert not os.path.exists(os.path.join(idx, "outcome.json"))


def test_with_signal_cache_is_written_in_process_and_restored(memory_dir):
    from memory import telemetry as T
    from memory.outcome import read_outcome_cache

    idx = _corpus(memory_dir, cited=True)
    td = default_telemetry_dir(memory_dir)
    T.log_episode(["alpha-notes"], query="alpha handling", telemetry_dir=td, session_id="s1")
    T.log_outcome("Edit", "src/a.py", session_id="s1", telemetry_dir=td)

    report = OP.run_ab(memory_dir=memory_dir, index_dir=idx, telemetry_dir=td, write=False)
    assert report.get("ok"), report.get("error")
    assert report["signal"]["outcome_confirmed_memories"] == 1
    assert "written in-process" in report["signal"]["cache"]
    # restored: flag-OFF machines keep no outcome.json they did not write
    assert read_outcome_cache(idx) is None
    assert "HIPPO_OUTCOME_PRIOR" not in os.environ


def test_pre_existing_cache_is_preserved(memory_dir):
    from memory import telemetry as T
    from memory.outcome import injection_hits, read_outcome_cache, write_outcome_cache

    idx = _corpus(memory_dir, cited=True)
    td = default_telemetry_dir(memory_dir)
    T.log_episode(["alpha-notes"], query="alpha handling", telemetry_dir=td, session_id="s1")
    T.log_outcome("Edit", "src/a.py", session_id="s1", telemetry_dir=td)
    assert write_outcome_cache(idx, injection_hits(memory_dir, td))
    before = read_outcome_cache(idx)

    report = OP.run_ab(memory_dir=memory_dir, index_dir=idx, telemetry_dir=td, write=False)
    assert report.get("ok"), report.get("error")
    assert "pre-existing" in report["signal"]["cache"]
    assert read_outcome_cache(idx) == before


def test_report_writes_beside_salience_ab_and_doctor_reads_it(memory_dir, monkeypatch):
    from memory import doctor as D
    from memory import doctor_checks_recall as DR

    idx = _corpus(memory_dir)
    td = default_telemetry_dir(memory_dir)
    report = OP.run_ab(memory_dir=memory_dir, index_dir=idx, telemetry_dir=td, write=True)
    assert report.get("ok")
    assert report["path"] == os.path.join(td, "outcome_prior_ab.json")
    assert os.path.exists(report["path"])
    # the named standing reader (AC5): the salience-evidence doctor line's sibling sentence
    monkeypatch.setattr(
        S, "read_report", lambda md, telemetry_dir=None: {"deltas": {}, "identical_arms": True}
    )
    r = DR.check_salience_evidence(
        D.DoctorContext(memory_dir, os.path.dirname(os.path.dirname(memory_dir)))
    )
    assert "outcome-prior A/B recorded" in r["message"]
    assert "ED-2" in r["message"]


def test_flip_language_absent_everywhere():
    """ED-2/LIF-7: the harness measures; no surface recommends enabling the flag."""
    for mod in (OP, AB):
        src = inspect.getsource(mod).lower()
        assert "enable hippo_outcome_prior" not in src
        assert "turn the flag on" not in src
