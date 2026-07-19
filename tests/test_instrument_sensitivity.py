"""MEA-1 (ED5R-2): instrument-sensitivity accounting — resolvable_n REPORTED, never applied.

The round-5 headline finding: the recorded Arm A salience evidence was measured through a
hard set with 1 of 32 rows resolvable against the lived-in corpus (~3% sensitivity) and no
surface said so. These pins hold the fix to its own laws:

  AC1  evaluate() carries per-category ``resolvable_n`` alongside ``n`` — but ONLY when
       some row is unresolvable (absence-emits-nothing, ED-4): a fully-resolvable run's
       report carries no new key, which is what keeps the CI/golden report byte-identical.
  AC2  REPORT-ONLY: no row is skipped — ``hard_set_n`` and every ``by_category`` ``n``
       count all rows whether or not their stems resolve; no gate constant moves.
  AC3  ONE shared helper (inv5): ``floor_sweep`` routes through
       ``eval_metrics.resolvable_row`` — the stem-existence predicate is re-implemented
       nowhere (inspect pin).
  AC4  ``salience_eval.run_ab``'s condition stamp records ``resolvable_by_category``
       (evidence files state their instrument's sensitivity unconditionally, ED5R-2).
  AC5  ``check_salience_evidence`` QUALIFIES a recorded A/B when the CURRENT
       fixture-vs-corpus resolvable share sits below the module floor — stated as derived
       from current state — and NEVER recommends a flip (the ED-2 razor). The recorded
       salience_ab.json is never rewritten by anything here.
"""

from __future__ import annotations

import inspect
import json
import os

from memory import eval_recall as E
from memory.build_index import build_index, default_index_dir, load_index
from memory.eval_metrics import hard_set_resolvability, load_hard_set, resolvable_row


def _write_memory(md: str, name: str, description: str) -> None:
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write(
            f"---\nname: {name}\ndescription: {json.dumps(description)}\n"
            f"metadata:\n  type: project\n---\nBody of {name}.\n"
        )


def _corpus(tmp_path, names):
    md = str(tmp_path / ".claude" / "memory")
    for n in names:
        _write_memory(md, n, f"notes about {n.replace('-', ' ')} handling")
    idx = str(tmp_path / ".claude" / ".memory-index")
    build_index(md, idx)
    return md, idx


def _hard_set(tmp_path, rows) -> str:
    p = str(tmp_path / "hard_set.yaml")
    with open(p, "w", encoding="utf-8") as fh:
        for r in rows:
            exp = ", ".join(json.dumps(s) for s in r["expected"])
            fh.write(f"- query: {json.dumps(r['query'])}\n  expected: [{exp}]\n")
            if r.get("category"):
                fh.write(f"  category: {r['category']}\n")
    return p


def test_hard_set_resolvability_counts_per_category(tmp_path):
    _md, idx = _corpus(tmp_path, ["alpha-notes", "beta-notes"])
    index = load_index(idx)
    rows = load_hard_set(
        _hard_set(
            tmp_path,
            [
                {"query": "alpha handling", "expected": ["alpha-notes"]},
                {"query": "ghost one", "expected": ["missing-stem"]},
                {"query": "beta deep dive", "expected": ["beta-notes"], "category": "multi-hop"},
                {"query": "ghost two", "expected": ["also-missing"], "category": "multi-hop"},
            ],
        )
    )
    r = hard_set_resolvability(index, rows)
    assert r == {
        "multi-hop": {"resolvable_n": 1, "n": 2},
        "single-hop": {"resolvable_n": 1, "n": 2},
    }
    # the predicate itself: ANY expected stem existing resolves the row
    names = {e.get("name") for e in index.entries}
    assert resolvable_row(names, {"expected": ["missing", "beta-notes"]})
    assert not resolvable_row(names, {"expected": ["missing"]})
    assert not resolvable_row(names, {"expected": []})


def test_evaluate_reports_resolvable_n_without_skipping_rows(tmp_path):
    md, idx = _corpus(tmp_path, ["alpha-notes"])
    hs = _hard_set(
        tmp_path,
        [
            {"query": "alpha handling", "expected": ["alpha-notes"]},
            {"query": "ghost", "expected": ["missing-stem"]},
            {"query": "ghost multi", "expected": ["gone"], "category": "multi-hop"},
        ],
    )
    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs)
    # AC1: the sensitivity block is present (a row is unresolvable) with full counts…
    assert report["resolvability"] == {
        "multi-hop": {"resolvable_n": 0, "n": 1},
        "single-hop": {"resolvable_n": 1, "n": 2},
    }
    # AC2: …and REPORT-ONLY — nothing was skipped or filtered.
    assert report["hard_set_n"] == 3
    assert report["by_category"]["single-hop"]["n"] == 2
    assert report["by_category"]["multi-hop"]["n"] == 1


def test_fully_resolvable_report_carries_no_sensitivity_key(tmp_path):
    md, idx = _corpus(tmp_path, ["alpha-notes", "beta-notes"])
    hs = _hard_set(
        tmp_path,
        [
            {"query": "alpha handling", "expected": ["alpha-notes"]},
            {"query": "beta handling", "expected": ["beta-notes"]},
        ],
    )
    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs)
    # ED-4 absence-emits-nothing — THIS is the CI/golden byte-identity mechanism.
    assert "resolvability" not in report
    # and with no hard set at all, nothing appears either
    report2 = E.evaluate(memory_dir=md, index_dir=idx)
    assert "resolvability" not in report2


def test_floor_sweep_reuses_the_shared_predicate():
    """inv5: floor_sweep's on-topic filter routes through eval_metrics.resolvable_row —
    the stem-existence logic exists exactly once."""
    src = inspect.getsource(E.floor_sweep)
    assert "resolvable_row(" in src
    assert "any(stem in names" not in src


def test_run_ab_condition_stamp_records_resolvability(tmp_path):
    from memory import salience_eval as S

    md, idx = _corpus(tmp_path, ["alpha-notes", "beta-notes"])
    hs = _hard_set(
        tmp_path,
        [
            {"query": "alpha handling", "expected": ["alpha-notes"]},
            {"query": "ghost", "expected": ["missing-stem"], "category": "multi-hop"},
        ],
    )
    td = str(tmp_path / "telemetry")
    report = S.run_ab(memory_dir=md, index_dir=idx, hard_set_path=hs, telemetry_dir=td, write=False)
    assert report.get("ok"), report.get("error")
    assert report["condition"]["resolvable_by_category"] == {
        "multi-hop": {"resolvable_n": 0, "n": 1},
        "single-hop": {"resolvable_n": 1, "n": 1},
    }


def _doctor_ctx(tmp_path, corpus_names, fixture_rows):
    """A DoctorContext over a tmp corpus + a project-local .audit-fixtures hard set."""
    from memory import doctor as D

    repo = str(tmp_path)
    md, _idx_unused = _corpus(tmp_path, corpus_names)
    build_index(md, default_index_dir(md))
    fx_dir = os.path.join(md, ".audit-fixtures")
    os.makedirs(fx_dir, exist_ok=True)
    with open(os.path.join(fx_dir, "recall_hard_set.yaml"), "w", encoding="utf-8") as fh:
        for r in fixture_rows:
            exp = ", ".join(json.dumps(s) for s in r["expected"])
            fh.write(f"- query: {json.dumps(r['query'])}\n  expected: [{exp}]\n")
    return D.DoctorContext(md, repo)


def test_doctor_qualifies_low_sensitivity_recorded_evidence(tmp_path, monkeypatch):
    from memory import doctor_checks_recall as DR
    from memory import salience_eval as S

    ctx = _doctor_ctx(
        tmp_path,
        ["alpha-notes"],
        [
            {"query": "one", "expected": ["gone-a"]},
            {"query": "two", "expected": ["gone-b"]},
            {"query": "three", "expected": ["gone-c"]},
            {"query": "alpha handling", "expected": ["alpha-notes"]},
        ],
    )
    monkeypatch.setattr(
        S, "read_report", lambda md, telemetry_dir=None: {"deltas": {"single-hop": {}}, "identical_arms": True}
    )
    r = DR.check_salience_evidence(ctx)
    assert r["status"] == "ok"
    # the qualification names the CURRENT-state derivation and the share
    assert "SENSITIVITY" in r["message"]
    assert "1/4" in r["message"]
    assert "current fixture-vs-corpus state" in r["message"]
    # the ED-2 razor: qualification language only — never a flip recommendation
    assert "dated owner decision" in r["message"]
    for banned in ("enable salience", "turn the flag on", "flip the default"):
        assert banned not in r["message"].lower()


def test_doctor_skips_qualification_when_instrument_is_sensitive(tmp_path, monkeypatch):
    from memory import doctor_checks_recall as DR
    from memory import salience_eval as S

    ctx = _doctor_ctx(
        tmp_path,
        ["alpha-notes", "beta-notes"],
        [
            {"query": "alpha handling", "expected": ["alpha-notes"]},
            {"query": "beta handling", "expected": ["beta-notes"]},
        ],
    )
    monkeypatch.setattr(
        S, "read_report", lambda md, telemetry_dir=None: {"deltas": {"single-hop": {}}, "identical_arms": True}
    )
    r = DR.check_salience_evidence(ctx)
    assert r["status"] == "ok"
    assert "A/B recorded" in r["message"]
    assert "SENSITIVITY" not in r["message"]


def test_doctor_qualification_absent_when_unmeasurable(tmp_path, monkeypatch):
    """No fixture / no index → the base message renders unchanged (absence-emits-nothing)."""
    from memory import doctor as D
    from memory import doctor_checks_recall as DR
    from memory import salience_eval as S

    md = str(tmp_path / ".claude" / "memory")
    os.makedirs(md, exist_ok=True)
    monkeypatch.setattr(
        S, "read_report", lambda md_, telemetry_dir=None: {"deltas": {}, "identical_arms": False}
    )
    r = DR.check_salience_evidence(D.DoctorContext(md, str(tmp_path)))
    assert r["status"] == "ok"
    assert "A/B recorded" in r["message"]
    assert "SENSITIVITY" not in r["message"]
