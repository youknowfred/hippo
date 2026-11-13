"""RUL-8: the text leg of the rule↔memory conflict radar.

A behavior memory and a governance section that share most of a sentence's content words
but differ in its directive become one tend ``contradiction`` item naming both sides; the
existing verdicts work it, nothing picks a winner, a dismissed candidate stays down until
either text changes, and doctor's rules line stops saying "none". Fixtures are written
from scratch.
"""

from __future__ import annotations

import json
import os

import pytest

from .conftest import git_commit, write_file
from memory import doctor as D
from memory import rules_conflict as RC
from memory import tend as T
from memory import tend_queue as Q
from memory.telemetry import default_telemetry_dir

_MEM = """---
name: {name}
description: "{description}"
metadata:
  type: {type}
  source_commit: "abc"
---

{body}
"""

# The shape the estate handed over, written from scratch: the rule now says the agent runs
# the shipping step in the same turn; an older memory still says ask the owner first.
_RULE = """# Project rules

## Saving notes

- Save notes only through the notebook tool, never by hand into the notes folder, then,
  in the same turn, run `tools/ship-notes --now` from the primary checkout.

## Tests

- Run the focused test file before every handoff.
"""
_OLD_PRACTICE = (
    "Save notes only through the notebook tool, then ask the owner to ship the notes "
    "folder in the same turn from the primary checkout"
)


def _mem(md, name, description, body="Plain body text that says nothing directive.", type_="feedback"):
    write_file(md, f"{name}.md", _MEM.format(name=name, description=description, type=type_, body=body))


@pytest.fixture
def estate(repo, memory_dir):
    write_file(repo, "CLAUDE.md", _RULE)
    _mem(memory_dir, "notes-ship-ask", _OLD_PRACTICE)
    _mem(memory_dir, "unrelated-habit", "Prefer short commit subjects that name the module touched")
    git_commit(repo, "corpus", 1_700_000_000)
    os.makedirs(default_telemetry_dir(memory_dir), exist_ok=True)
    return repo, memory_dir


def _target():
    return "notes-ship-ask|rule:CLAUDE.md#saving-notes"


# ---- the detector: each directive axis, and the false-positive shapes ----------------------- #
@pytest.mark.parametrize(
    "a,b,axis",
    [
        (  # ask first / just run
            "After a release build, ask the owner to publish the changelog page",
            "After a release build, run the publish script for the changelog page",
            "ask_run",
        ),
        (  # always / never on the shared predicate
            "Always rebuild the search index after editing the schema file",
            "Never rebuild the search index after editing the schema file by hand",
            "always_never",
        ),
        (  # the order of two shared steps
            "Rebase the feature branch before running the integration suite locally",
            "Run the integration suite locally after you rebase... no: rebase the feature branch after the integration suite runs locally",
            "order",
        ),
        (  # whose act: the agent's / the user's
            "Rotating the staging deploy token is the session's to run each Monday morning",
            "Rotating the staging deploy token each Monday morning is the owner's to run",
            "whose",
        ),
    ],
)
def test_each_axis_flags_an_opposed_directive(a, b, axis):
    assert RC.directive_conflict(a, b) == axis
    assert RC.directive_conflict(b, a) == axis


def test_negation_scope_never_ask_is_the_run_pole():
    # "never ask" and "without asking" both say: just do it. They agree.
    a = "Restart the preview server after a config edit without asking the owner first"
    b = "Never ask before you restart the preview server after a config edit"
    assert RC.directive_conflict(a, b) is None


def test_same_directive_is_not_a_conflict():
    a = "Ask the owner before deleting a published release tag from the remote"
    b = "Confirm with the owner before deleting a published release tag from the remote"
    assert RC.directive_conflict(a, b) is None


def test_low_overlap_is_not_a_candidate():
    a = "Always pin the formatter version in the lockfile"
    b = "Never paste secrets into the issue tracker"
    assert RC.directive_conflict(a, b) is None


def test_a_sentence_holding_both_poles_casts_no_vote():
    a = "Run the schema migration dry run and ask the owner about the real schema migration"
    b = "Ask the owner about the schema migration dry run and the real schema migration"
    assert RC.directive_conflict(a, b) is None


def test_reordered_steps_around_an_ask_are_an_order_conflict():
    # The flip side of the case above: one says dry-run first then ask, the other ask first.
    a = "Run the migration dry run yourself, then ask the owner before the real migration run"
    b = "Ask the owner before the migration dry run and the real migration run"
    assert RC.directive_conflict(a, b) == "order"


def test_directive_off_the_shared_predicate_does_not_count():
    # "never" governs the unshared clause (the cache folder); the shared words carry none.
    a = "Regenerate the API client from the schema file; never touch the cache folder"
    b = "Always regenerate the API client from the schema file after a schema change"
    assert RC.directive_conflict(a, b) is None


def test_flag_and_possessive_words_are_not_directives():
    # "--title required" is a flag note, and "the session's own branch" is not whose-act.
    a = "Publish the report page with the publish script, title flag required, from the session's own branch"
    b = "Never publish the report page with the publish script from the session's own branch"
    assert RC.directive_conflict(a, b) is None


def test_a_run_on_list_line_is_not_a_sentence(repo, memory_dir):
    traps = " · ".join(f"☠ trap {i} always moves widget{i} gauge{i} meter{i}" for i in range(30))
    write_file(repo, "CLAUDE.md", f"# Traps\n\n{traps}\n")
    _mem(memory_dir, "widget-habit", "Never moves widget3 gauge3 meter3 trap without a review")
    rows = RC.text_conflicts(memory_dir, repo)
    # Each ☠ item is its own sentence now; none shares most of the memory's words.
    assert all(r["rule_says"].count("☠") <= 1 for r in rows)


# ---- corpus × governance -------------------------------------------------------------------- #
def test_text_leg_lists_the_pair_with_both_sentences(estate):
    repo, md = estate
    rows = RC.text_conflicts(md, repo)
    assert [(r["memory"], r["rule"], r["axis"]) for r in rows] == [
        ("notes-ship-ask", "rule:CLAUDE.md#saving-notes", "ask_run")
    ]
    assert "ask the owner" in rows[0]["memory_says"]
    assert "run `tools/ship-notes --now`" in rows[0]["rule_says"]


def test_only_the_behavior_lane_is_read(repo, memory_dir):
    write_file(repo, "CLAUDE.md", _RULE)
    _mem(memory_dir, "notes-ship-state", _OLD_PRACTICE, type_="project")
    _mem(memory_dir, "notes-ship-ref", _OLD_PRACTICE, type_="reference")
    assert RC.text_conflicts(memory_dir, repo) == []
    # The fact lane is measurable on request (the gate's "what would it add" number).
    assert len(RC.text_conflicts(memory_dir, repo, types=("project", "reference"))) == 2


def test_no_governance_plane_means_no_candidates(memory_dir, repo):
    _mem(memory_dir, "notes-ship-ask", _OLD_PRACTICE)
    assert RC.text_conflicts(memory_dir, repo) == []


def test_bogus_dirs_never_raise(tmp_path):
    bogus = str(tmp_path / "nope")
    assert RC.text_conflicts(bogus, bogus) == []
    assert RC.pending(bogus, bogus) == []


def test_repeated_headings_get_distinct_labels(repo, memory_dir):
    body = "Keep release notes short, dated and linked to the merged change."
    write_file(repo, "CLAUDE.md", f"# A\n\n## Notes\n\n{body}\n\n## Notes\n\n{body}\n")
    labels = [s["label"] for s in RC.rule_sections(repo)]
    assert labels == ["rule:CLAUDE.md#notes", "rule:CLAUDE.md#notes-2"]


# ---- the tend queue -------------------------------------------------------------------------- #
def test_candidate_is_one_contradiction_item_naming_both(estate):
    repo, md = estate
    r = Q.build_queue(md, repo, kinds=("contradiction",))
    items = [e for e in r["pending"] if RC.is_rule_target(e["target"])]
    assert [e["id"] for e in items] == [f"contradiction:{_target()}"]
    ev = items[0]["evidence"]
    assert "notes-ship-ask" in ev and "CLAUDE.md § Saving notes" in ev
    assert "ask the owner" in ev and "tools/ship-notes" in ev
    assert "Confirmed" not in ev


def test_listing_never_writes_the_rule_or_the_memory(estate):
    repo, md = estate
    paths = [os.path.join(repo, "CLAUDE.md"), os.path.join(md, "notes-ship-ask.md")]
    before = [open(p, encoding="utf-8").read() for p in paths]
    Q.build_queue(md, repo)
    T.main(["next", "--kind", "contradiction", "--memory-dir", md, "--repo-root", repo])
    D.check_rules_conflicts(D.DoctorContext(md, repo))
    assert [open(p, encoding="utf-8").read() for p in paths] == before


def test_show_renders_both_sentences(estate, capsys):
    repo, md = estate
    assert T.main(["show", f"contradiction:{_target()}", "--memory-dir", md, "--repo-root", repo]) == 0
    out = capsys.readouterr().out
    assert "the memory says:" in out and "the rule says:" in out
    assert "hippo never edits a rule file" in out


def test_not_conflicting_stays_down_until_a_text_changes(estate):
    repo, md = estate
    eid = f"contradiction:{_target()}"
    r = T.apply(eid, "not_conflicting", memory_dir=md, repo_root=repo)
    assert r["ok"], r
    again = Q.build_queue(md, repo, kinds=("contradiction",))
    assert eid not in [e["id"] for e in again["pending"]]
    assert eid in [e["id"] for e in again["skipped"]]
    # A second pass does not bring it back...
    assert eid not in [e["id"] for e in Q.build_queue(md, repo, kinds=("contradiction",))["pending"]]
    # ...an edit to the memory's disagreeing sentence does (the evidence moved).
    _mem(md, "notes-ship-ask", _OLD_PRACTICE + " every time")
    assert eid in [e["id"] for e in Q.build_queue(md, repo, kinds=("contradiction",))["pending"]]


def test_scope_both_is_recorded_like_a_dismissal(estate):
    repo, md = estate
    r = T.apply(f"contradiction:{_target()}", "scope_both", memory_dir=md, repo_root=repo)
    assert r["ok"] and "scoped" in r["message"]


def test_keep_one_with_the_rule_winning_demotes_the_memory(estate):
    repo, md = estate
    eid = f"contradiction:{_target()}"
    r = T.apply(eid, "keep_one", memory_dir=md, repo_root=repo, winner="rule", loser="notes-ship-ask")
    assert r["ok"], r
    with open(os.path.join(md, "notes-ship-ask.md"), encoding="utf-8") as fh:
        assert "invalid_after" in fh.read()
    with open(os.path.join(repo, "CLAUDE.md"), encoding="utf-8") as fh:
        assert fh.read() == _RULE


def test_keep_one_with_the_memory_winning_never_edits_the_rule(estate):
    repo, md = estate
    eid = f"contradiction:{_target()}"
    r = T.apply(eid, "keep_one", memory_dir=md, repo_root=repo,
                winner="notes-ship-ask", loser="rule:CLAUDE.md#saving-notes")
    assert not r["ok"] and "never edits a rule file" in r["message"]
    with open(os.path.join(repo, "CLAUDE.md"), encoding="utf-8") as fh:
        assert fh.read() == _RULE
    # Editing the rule is what clears it.
    write_file(repo, "CLAUDE.md", _RULE.replace("run `tools/ship-notes --now`", "ask the owner to ship them"))
    assert RC.text_conflicts(md, repo) == []


def test_keep_one_needs_both_sides_named(estate):
    repo, md = estate
    r = T.apply(f"contradiction:{_target()}", "merge", memory_dir=md, repo_root=repo,
                winner="rule", loser="someone-else")
    assert not r["ok"] and "--winner and --loser" in r["message"]


# ---- doctor ------------------------------------------------------------------------------- #
def test_doctor_rules_line_stops_saying_none(estate):
    repo, md = estate
    r = D.check_rules_conflicts(D.DoctorContext(md, repo))
    assert r["status"] == "warn"
    assert "1 text conflict(s)" in r["message"] and "none" not in r["message"]
    assert "`notes-ship-ask` disagrees with CLAUDE.md § Saving notes" in r["message"]
    T.apply(f"contradiction:{_target()}", "not_conflicting", memory_dir=md, repo_root=repo)
    r = D.check_rules_conflicts(D.DoctorContext(md, repo))
    assert r["status"] == "ok"  # a dismissed candidate stops counting


# ---- the opt-in LLM check may confirm, never dismiss or decide ------------------------------ #
@pytest.fixture
def fake_llm(monkeypatch):
    from memory import llm_client

    answers = []
    monkeypatch.setattr(llm_client, "complete", lambda prompt, **kw: answers.pop(0) if answers else None)
    return answers


def test_llm_confirmation_marks_the_evidence(estate, fake_llm):
    repo, md = estate
    td = default_telemetry_dir(md)
    fake_llm.append(json.dumps({"conflict": True, "reason": "one asks, one runs"}))
    stats = RC.confirm_candidates(md, repo, td, pass_id="p1", max_pairs=5, timeout_s=1)
    assert stats["attempts"] == 1 and stats["confirmed"] == 1
    ev = [e for e in Q.build_queue(md, repo, kinds=("contradiction",))["pending"]
          if RC.is_rule_target(e["target"])][0]["evidence"]
    assert ev.endswith("Confirmed by the contradiction check.")
    # Judged once: a second pass makes no call.
    assert RC.confirm_candidates(md, repo, td, pass_id="p2", max_pairs=5, timeout_s=1)["attempts"] == 0


def test_llm_no_conflict_never_dismisses(estate, fake_llm):
    repo, md = estate
    td = default_telemetry_dir(md)
    before = Q.build_queue(md, repo, kinds=("contradiction",))["pending"]
    fake_llm.append(json.dumps({"conflict": False, "reason": "different cases"}))
    stats = RC.confirm_candidates(md, repo, td, pass_id="p1", max_pairs=5, timeout_s=1)
    assert stats["not_confirmed"] == 1
    after = Q.build_queue(md, repo, kinds=("contradiction",))["pending"]
    assert after == before  # same item, same evidence, same skip hash


def test_rule_rows_never_reach_the_memory_pair_inbox(estate, fake_llm):
    from memory.resolve_view import unresolved_contradictions

    repo, md = estate
    fake_llm.append(json.dumps({"conflict": True, "reason": "x"}))
    RC.confirm_candidates(md, repo, default_telemetry_dir(md), pass_id="p1", max_pairs=5, timeout_s=1)
    assert unresolved_contradictions(md, repo_root=repo) == []


def test_dream_pass_runs_the_confirmation_under_the_same_opt_in(estate, tmp_path, monkeypatch):
    from memory import dream

    from .test_dream_contradictions import _seed_sessions

    repo, md = estate
    _mem(md, "garden-almanac", "Heirloom tomato rotation beds for the spring garden", type_="reference")
    td, idx = str(tmp_path / "tele"), str(tmp_path / "idx")
    _seed_sessions(td)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_LLM_API_KEY", "test-key-not-real")
    prompts = []

    def fake(prompt, **kw):
        prompts.append(prompt)
        return json.dumps({"conflict": True, "reason": "x"})

    monkeypatch.setattr("memory.llm_client.complete", fake)
    monkeypatch.delenv("HIPPO_DREAM_CONTRADICTIONS", raising=False)
    dream.discover(md, idx, td)
    assert not any("project rule" in p for p in prompts)  # flag off: no call at all
    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "1")
    result = dream.discover(md, idx, td)
    assert result["status"] == "ok", result["status"]
    assert result["stats"]["contradictions"]["rule_text"]["confirmed"] == 1
    assert any("project rule" in p and "notes-ship-ask" in p for p in prompts)


def test_repo_root_of_never_falls_back_to_an_arbitrary_dir(tmp_path):
    assert RC.repo_root_of(str(tmp_path / "loose" / "mem")) == ""
    assert RC.repo_root_of(str(tmp_path / "proj" / ".claude" / "memory")) == str(tmp_path / "proj")


def test_llm_failure_records_nothing(estate, fake_llm):
    repo, md = estate
    td = default_telemetry_dir(md)
    stats = RC.confirm_candidates(md, repo, td, pass_id="p1", max_pairs=5, timeout_s=1)
    assert stats["llm_failures"] == 1
    assert not os.path.exists(os.path.join(td, "dream", "contradictions.jsonl"))
