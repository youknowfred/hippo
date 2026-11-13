"""RUL-10: a floor line that restates an always-loaded rule — reported, never enforced.

Pins:
  - a floor pointer line whose own words an always-loaded rule (CLAUDE.md, an unscoped
    ``.claude/rules`` file) already carries is reported; a path-scoped rule, a different
    line, a too-short line and a line under a non-floor section are not;
  - rules are scored one list item at a time: words scattered over a long list do not
    make a restatement (the write-time check, which scores blocks, is unchanged);
  - informational: ``hippo lint-floor`` exits as before, the write/commit gate's floor
    judgement ignores it, doctor's status does not move;
  - TND-13: ``MEMORY.md`` + ``restates-rule`` + the floor's sha256 acknowledges it; one
    byte re-arms it and says so;
  - tend: one ``floor:rule:<name>.md`` item, ``done`` refused until the line leaves.
"""

from __future__ import annotations

import hashlib
import json
import os

from memory import floor_rules as FR
from memory import tend_queue as Q
from memory.doctor import DoctorContext
from memory.doctor_checks_corpus import check_floor_governance
from memory.rules_plane import rule_dup_candidates
from memory.tend import apply as tend_apply

from .conftest import write_file

RULE_LINE = "Never deploy the billing service on a Friday afternoon without a rollback plan written down."
CLAUDE_MD = f"""# Project rules

- Keep functions small and name them for what they return.
- {RULE_LINE}
- Prefer plain sentences in commit messages.
"""
RESTATING = ("- [Friday deploys](friday-deploys.md) — never deploy the billing service on a Friday "
             "afternoon without a rollback plan")
OTHER = "- [Tabs](tabs.md) — the formatter uses four spaces for indentation in every Python module"
FLOOR = f"""# Memory

## User
{OTHER}

## Working Style & Process Feedback
{RESTATING}
- [Short](short.md) — billing Friday
"""


def _setup(repo, memory_dir, claude=CLAUDE_MD, floor=FLOOR):
    if claude is not None:
        write_file(repo, "CLAUDE.md", claude)
    write_file(memory_dir, "MEMORY.md", floor)
    for n in ("friday-deploys", "tabs", "short", "elsewhere"):
        write_file(memory_dir, f"{n}.md", f"---\nname: {n}\ndescription: about {n}\n---\n\nbody\n")


def test_a_floor_line_an_always_loaded_rule_carries_is_reported(repo, memory_dir):
    _setup(repo, memory_dir)
    rep = FR.floor_restatements(memory_dir, repo)
    assert [(r["name"], r["file"]) for r in rep["rows"]] == [("friday-deploys", "CLAUDE.md")]
    row = rep["rows"][0]
    assert row["line"] == 7 and row["score"] >= 0.6 and "Friday" in row["preview"]


def test_no_always_loaded_file_reports_nothing(repo, memory_dir):
    _setup(repo, memory_dir, claude=None)
    rep = FR.floor_restatements(memory_dir, repo)
    assert rep["rows"] == [] and rep["files"] == 0


def test_a_path_scoped_rule_is_not_always_loaded_and_an_unscoped_one_is(repo, memory_dir):
    _setup(repo, memory_dir, claude=None)
    write_file(repo, ".claude/rules/deploys.md", f"---\npaths:\n  - deploy/**\n---\n\n- {RULE_LINE}\n")
    assert FR.floor_restatements(memory_dir, repo)["rows"] == []
    write_file(repo, ".claude/rules/always.md", f"# Always\n\n- {RULE_LINE}\n")
    rows = FR.floor_restatements(memory_dir, repo)["rows"]
    assert [(r["name"], r["file"]) for r in rows] == [("friday-deploys", os.path.join(".claude", "rules", "always.md"))]


def test_words_scattered_over_a_long_list_are_not_a_restatement(repo, memory_dir):
    # One blank-line block of many items: block scoring would contain the floor line's words,
    # item scoring sees no single rule that says it.
    scattered = "\n".join([
        "- Never touch the billing tables by hand.",
        "- Deploy through the pipeline only.",
        "- The service owner signs off each Friday.",
        "- An afternoon freeze applies before holidays.",
        "- Write a rollback note for schema changes.",
        "- Every plan names its owner.",
    ])
    _setup(repo, memory_dir, claude=f"# Rules\n\n{scattered}\n")
    assert FR.floor_restatements(memory_dir, repo)["rows"] == []
    # The write-time check (RUL-3) keeps scoring whole blocks.
    assert rule_dup_candidates(FR.line_text(RESTATING), "", repo)


def test_only_lines_under_a_floor_section_are_pointer_lines():
    text = FLOOR + "\n## Recalled on demand\n" + RESTATING.replace("friday-deploys", "elsewhere") + "\n"
    names = [p["name"] for p in FR.pointer_lines(text, ("User", "Working Style & Process Feedback"))]
    assert names == ["tabs", "friday-deploys", "short"]


def test_line_text_drops_the_link_target_and_bullet():
    assert FR.line_text(RESTATING).startswith("Friday deploys — never deploy")
    assert "friday-deploys.md" not in FR.line_text(RESTATING)


def _ack(memory_dir, sha):
    with open(os.path.join(memory_dir, "hippo.json"), "w", encoding="utf-8") as fh:
        json.dump({"acknowledged_findings": [{"file": "MEMORY.md", "rule": "restates-rule",
                   "sha256": sha, "reason": "kept on purpose", "date": "2026-11-12"}]}, fh)


def test_an_acknowledgement_counts_it_and_one_byte_re_arms_it(repo, memory_dir):
    _setup(repo, memory_dir)
    sha = FR.floor_restatements(memory_dir, repo)["sha256"]
    with open(os.path.join(memory_dir, "MEMORY.md"), "rb") as fh:
        assert sha == hashlib.sha256(fh.read()).hexdigest()
    _ack(memory_dir, sha)
    rep = FR.floor_restatements(memory_dir, repo)
    assert rep["rows"] == [] and rep["acknowledged"] == 1 and not rep["stale"]
    assert FR.tend_entries(memory_dir, repo) == []
    with open(os.path.join(memory_dir, "MEMORY.md"), "a", encoding="utf-8") as fh:
        fh.write(" ")
    rep = FR.floor_restatements(memory_dir, repo)
    assert len(rep["rows"]) == 1 and rep["stale"]
    assert any("no longer matches" in ln for ln in FR.render_lines(rep))


def test_an_acknowledgement_for_another_rule_or_file_does_not_count(repo, memory_dir):
    _setup(repo, memory_dir)
    sha = FR.floor_restatements(memory_dir, repo)["sha256"]
    with open(os.path.join(memory_dir, "hippo.json"), "w", encoding="utf-8") as fh:
        json.dump({"acknowledged_findings": [{"file": "MEMORY.md", "rule": "html-comment",
                   "sha256": sha, "reason": "r", "date": "2026-11-12"}]}, fh)
    assert len(FR.floor_restatements(memory_dir, repo)["rows"]) == 1


def test_lint_floor_lists_it_and_keeps_its_exit_status(repo, memory_dir, capsys):
    from memory.lint_floor import main

    _setup(repo, memory_dir)
    assert main(["--memory-dir", memory_dir]) == 0
    out = capsys.readouterr().out
    assert "invariant holds" in out
    assert "restate a rule (1, informational)" in out and "friday-deploys" in out
    assert FR.floor_restatements(memory_dir, repo)["sha256"] in out


def test_the_write_and_commit_gates_never_judge_it(repo, memory_dir):
    from memory.gate import judge_floor
    from memory.provenance_format import read_floor_lint

    _setup(repo, memory_dir)
    before = FLOOR.replace(RESTATING + "\n", "")
    assert judge_floor(before, FLOOR, read_floor_lint(memory_dir)) == []


def test_doctor_names_it_without_moving_the_status(repo, memory_dir):
    _setup(repo, memory_dir)
    r = check_floor_governance(DoctorContext(memory_dir, repo))
    assert r["status"] == "ok"
    assert "1 floor line(s) restate an always-loaded rule" in r["message"]
    _setup(repo, memory_dir, claude="# Rules\n\n- Unrelated words about something else entirely here.\n")
    r = check_floor_governance(DoctorContext(memory_dir, repo))
    assert r["status"] == "ok" and "restate" not in r["message"]


def test_tend_queues_one_floor_item_and_done_waits_for_the_line_to_leave(repo, memory_dir):
    _setup(repo, memory_dir)
    res = Q.build_queue(memory_dir, repo, kinds=("floor",), write_cache=False)
    ids = [e["id"] for e in res["pending"]]
    assert ids == ["floor:rule:friday-deploys.md"]
    assert "CLAUDE.md" in res["pending"][0]["evidence"]
    r = tend_apply("floor:rule:friday-deploys.md", "done", memory_dir=memory_dir, repo_root=repo)
    assert not r["ok"] and "still reported" in r["message"]
    write_file(memory_dir, "MEMORY.md", FLOOR.replace(RESTATING + "\n", ""))
    # The item leaves the queue with the line.
    assert Q.build_queue(memory_dir, repo, kinds=("floor",), write_cache=False)["pending"] == []


def test_never_raises_on_a_missing_floor(repo, memory_dir):
    write_file(repo, "CLAUDE.md", CLAUDE_MD)
    assert FR.floor_restatements(memory_dir, repo)["rows"] == []
    assert FR.tend_entries(memory_dir, repo) == []
