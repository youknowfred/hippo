"""CLM-7: the floor's hard edges — both native read limits, declared section budgets, a
refusal for hippo's own writes past them, and the trim-safety replay report."""

from __future__ import annotations

import json
import os

from memory import build_index as B
from memory import new_memory as NM
from memory.lint_floor import (
    floor_governance,
    format_governance_summary,
    render_trim_report,
    trim_safety_report,
)
from memory.provenance import read_floor_lint
from memory.telemetry import default_telemetry_dir, log_episode


def _corpus(tmp_path, floor_text, fmt=None):
    md = tmp_path / "memory"
    md.mkdir()
    (md / "MEMORY.md").write_text(floor_text, encoding="utf-8")
    if fmt is not None:
        (md / ".format").write_text(json.dumps(fmt), encoding="utf-8")
    return str(md)


def _floor(extra_lines=0, in_flight=""):
    body = "# Agent Memory\n\n## User\n- [Alpha](alpha.md) — a\n\n## Working Style & Process Feedback\n"
    body += "".join(f"note {i}\n" for i in range(extra_lines))
    if in_flight:
        body += "\n## In flight\n" + in_flight
    return body


def test_policy_reads_section_budgets_and_line_overrides(tmp_path):
    md = _corpus(tmp_path, _floor(), {"corpus_format": 5, "floor_lint": {
        "section_budgets": {"## In flight": 100, "no-hash": 5, "## Bad": "x"}, "cap_lines": 150,
        "warn_lines": 999}})
    p = read_floor_lint(md)
    assert p["section_budgets"] == {"## In flight": 100}
    assert p["cap_lines"] == 150 and p["warn_lines"] == 140  # downward-only override


def test_the_line_edge_is_measured_and_named(tmp_path):
    g = floor_governance(_corpus(tmp_path, _floor(extra_lines=210)))
    assert g["lines"] > 200 and g["over_line_cap"]
    assert "200-line read limit: lines 201+ never load" in format_governance_summary(g)


def test_line_warn_before_the_limit(tmp_path):
    g = floor_governance(_corpus(tmp_path, _floor(extra_lines=150)))
    assert g["over_line_warn"] and not g["over_line_cap"]
    assert "-line warn line" in format_governance_summary(g)


def test_a_section_over_its_budget_is_named(tmp_path):
    md = _corpus(tmp_path, _floor(in_flight="x" * 300 + "\n"),
                 {"floor_lint": {"section_budgets": {"## In flight": 100}}})
    g = floor_governance(md)
    assert g["sections_over"] and g["sections_over"][0]["section"] == "## In flight"
    assert "'## In flight'" in format_governance_summary(g)


def test_hippo_refuses_a_pointer_that_would_cross_the_line_limit(tmp_path):
    md = _corpus(tmp_path, _floor(extra_lines=194))  # 200 lines; one more crosses the limit
    before = open(os.path.join(md, "MEMORY.md"), encoding="utf-8").read()
    r = NM._append_floor_pointer(md, "## User", "bravo", "Bravo", "b")
    assert r["status"] == "skipped" and "200-line read limit" in r["reason"]
    assert open(os.path.join(md, "MEMORY.md"), encoding="utf-8").read() == before


def test_hippo_refuses_a_pointer_past_its_sections_budget(tmp_path):
    md = _corpus(tmp_path, _floor(), {"floor_lint": {"section_budgets": {"## User": 40}}})
    r = NM._append_floor_pointer(md, "## User", "bravo", "Bravo", "b")
    assert r["status"] == "skipped" and "'## User'" in r["reason"]


def test_another_sections_overrun_does_not_block_this_write(tmp_path):
    md = _corpus(tmp_path, _floor(in_flight="x" * 300 + "\n"),
                 {"floor_lint": {"section_budgets": {"## In flight": 100}}})
    r = NM._append_floor_pointer(md, "## User", "bravo", "Bravo", "b")
    assert r["status"] == "appended"


def test_trim_report_counts_prompts_recall_carries(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _corpus(tmp_path, _floor())
    with open(os.path.join(md, "alpha.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: alpha\ndescription: "deploy rollback steps"\ntype: feedback\n---\nbody\n')
    idx = str(tmp_path / "idx")
    B.build_index(md, idx)
    td = default_telemetry_dir(md)
    for q in ("how do we do a deploy rollback", "unrelated pizza question", "rollback steps again"):
        log_episode([], query=q, telemetry_dir=td, session_id="s1")
    rep = trim_safety_report(md, index_dir=idx, telemetry_dir=td)
    assert rep["queries"] == 3
    row = rep["rows"][0]
    assert row["name"] == "alpha" and row["carried"] == 2
    assert "carried on 2/3" in render_trim_report(rep)
