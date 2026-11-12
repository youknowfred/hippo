"""OBS-10: opened-after-recall — was an injected memory's own file opened afterwards?

The recall hook asks the agent to read a memory's file before relying on it. PostToolUse
already logs Reads, so the direct usefulness signal is a join away: of the (session,
memory) pairs the hook injected, how many had that file opened later in the same session.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import time

import memory.doctor as D
from memory import outcome as O
from memory import outcome_opened as OO
from memory import telemetry as T
from memory import telemetry_rollup as TR
from memory.telemetry import default_telemetry_dir

from .conftest import write_file


def _mem(md, name, mtype="project"):
    write_file(md, f"{name}.md", f'---\nname: {name}\ndescription: "a note"\ntype: {mtype}\n---\nbody\n')


def _read(md, repo, path, sid="s", tool="Read"):
    payload = {"tool_name": tool, "tool_input": {"file_path": path}, "session_id": sid}
    return O.record_from_payload(payload, memory_dir=md, repo_root=repo)


def _inject(md, repo, names, sid="s"):
    td = default_telemetry_dir(md)
    T.log_episode(names, query="q", repo_root=repo, telemetry_dir=td, session_id=sid)
    return td


def _rows(md):
    return list(T.read_outcomes(default_telemetry_dir(md)))


def _append(td, name, row):
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, name), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


# ---- record time: which touches are a memory open ---------------------------------------- #
def test_a_read_of_a_memory_file_is_stamped_opened(repo, memory_dir):
    _mem(memory_dir, "app-note")
    assert _read(memory_dir, repo, os.path.join(memory_dir, "app-note.md")) is True
    [row] = _rows(memory_dir)
    assert row["path"] == ".claude/memory/app-note.md"
    assert row["opened"] == "app-note"
    assert "tier" not in row  # the project corpus is the unmarked case


def test_an_edit_or_an_index_read_is_not_an_open(repo, memory_dir):
    _mem(memory_dir, "app-note")
    write_file(memory_dir, "MEMORY.md", "# Memory\n")
    write_file(memory_dir, "CONVENTIONS.md", "conventions\n")
    write_file(repo, "src/app.py", "x = 1\n")
    _read(memory_dir, repo, os.path.join(memory_dir, "app-note.md"), tool="Edit")
    _read(memory_dir, repo, os.path.join(memory_dir, "MEMORY.md"))
    _read(memory_dir, repo, os.path.join(memory_dir, "CONVENTIONS.md"))
    _read(memory_dir, repo, os.path.join(repo, "src", "app.py"))
    assert len(_rows(memory_dir)) == 4
    assert all("opened" not in r for r in _rows(memory_dir))


def test_a_read_through_the_native_memory_symlink_is_recorded(repo, memory_dir, tmp_path):
    """The harness reads the floor from ``~/.claude/projects/<slug>/memory``, a symlink onto
    the corpus that sits outside the repository. A Read through it used to log nothing."""
    _mem(memory_dir, "app-note")
    native = tmp_path / "home" / ".claude" / "projects" / "-slug"
    native.mkdir(parents=True)
    os.symlink(memory_dir, native / "memory")
    assert _read(memory_dir, repo, str(native / "memory" / "app-note.md")) is True
    [row] = _rows(memory_dir)
    assert row["opened"] == "app-note"
    assert row["path"] == ".claude/memory/app-note.md"  # the real file, repo-relative


def test_a_read_of_a_worktrees_copy_counts_as_the_same_memory(repo, memory_dir):
    _mem(memory_dir, "app-note")
    copy = write_file(repo, ".claude/worktrees/wt1/.claude/memory/app-note.md", "copy\n")
    assert _read(memory_dir, repo, copy) is True
    [row] = _rows(memory_dir)
    assert row["opened"] == "app-note"
    assert row["tree_path"] == ".claude/memory/app-note.md"


def test_a_user_tier_read_is_recorded_with_its_tier(repo, memory_dir, tmp_path, monkeypatch):
    user = tmp_path / "user-tier"
    user.mkdir()
    (user / "portable.md").write_text("---\nname: portable\ntype: feedback\n---\nbody\n", encoding="utf-8")
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", str(user))
    assert _read(memory_dir, repo, str(user / "portable.md")) is True
    [row] = _rows(memory_dir)
    assert row["opened"] == "portable" and row["tier"] == "user"
    assert row["path"] == "<user-tier>/portable.md"  # never an absolute home path


def test_a_file_outside_every_tier_is_still_dropped(repo, memory_dir, tmp_path):
    other = tmp_path / "elsewhere" / "memory"
    other.mkdir(parents=True)
    (other / "x.md").write_text("x\n", encoding="utf-8")
    assert _read(memory_dir, repo, str(other / "x.md")) is False
    assert _rows(memory_dir) == []


# ---- the join ------------------------------------------------------------------------------ #
def test_injected_then_opened_is_a_hit(repo, memory_dir):
    _mem(memory_dir, "app-note", "feedback")
    _mem(memory_dir, "other-note", "project")
    _inject(memory_dir, repo, ["app-note", "other-note"])
    _read(memory_dir, repo, os.path.join(memory_dir, "app-note.md"))
    r = OO.opened_after_recall(memory_dir)
    assert (r["pairs"], r["opened"], r["rate"]) == (2, 1, 0.5)
    assert r["by_kind"] == {"feedback": {"pairs": 1, "opened": 1}, "project": {"pairs": 1, "opened": 0}}
    assert (r["opens"], r["opens_after_recall"], r["sessions"]) == (1, 1, 1)


def test_an_open_before_the_injection_is_not_a_hit(repo, memory_dir):
    _mem(memory_dir, "app-note")
    _mem(memory_dir, "seed")
    _inject(memory_dir, repo, ["seed"])  # opens the shared window
    _read(memory_dir, repo, os.path.join(memory_dir, "app-note.md"))
    time.sleep(0.01)
    _inject(memory_dir, repo, ["app-note"])
    r = OO.opened_after_recall(memory_dir)
    assert r["opened"] == 0
    assert (r["opens"], r["opens_after_recall"]) == (1, 0)  # the agent got there first


def test_an_open_in_another_session_is_not_a_hit(repo, memory_dir):
    _mem(memory_dir, "app-note")
    _inject(memory_dir, repo, ["app-note"], sid="s1")
    _read(memory_dir, repo, os.path.join(memory_dir, "app-note.md"), sid="s2")
    r = OO.opened_after_recall(memory_dir)
    assert (r["pairs"], r["opened"]) == (1, 0)


def test_only_the_window_both_ledgers_cover_is_judged(repo, memory_dir, monkeypatch):
    """The episode buffer and the outcome ledger rotate on their own. Once the outcome
    ledger has rotated, a pair injected before its first row could never be seen being
    opened; it is left out, not counted as unopened. A ledger that has not rotated still
    holds its first row, so nothing is left out."""
    _mem(memory_dir, "old-note")
    _mem(memory_dir, "new-note")
    td = default_telemetry_dir(memory_dir)
    now = time.time()
    monkeypatch.setenv("HIPPO_TELEMETRY_MAX_BYTES", "400")  # the two rows below pass 45% of it
    _append(td, "episode_buffer.jsonl", {"ts": now - 5000, "session_id": "s", "recalled_names": ["old-note"]})
    _append(td, "episode_buffer.jsonl", {"ts": now - 50, "session_id": "s", "recalled_names": ["new-note"]})
    _append(td, "outcome_events.jsonl", {"ts": now - 100, "session_id": "s", "tool": "Read", "path": "src/a.py"})
    _append(td, "outcome_events.jsonl",
            {"ts": now - 10, "session_id": "s", "tool": "Read", "path": ".claude/memory/new-note.md"})
    r = OO.opened_after_recall(memory_dir)
    assert (r["pairs"], r["opened"]) == (1, 1)
    monkeypatch.delenv("HIPPO_TELEMETRY_MAX_BYTES")
    r = OO.opened_after_recall(memory_dir)
    assert (r["pairs"], r["opened"]) == (2, 1)  # never rotated: the old pair is judged too


def test_rows_without_the_field_are_read_from_their_path(repo, memory_dir):
    """The bash fast path and every row from before this field carry only ``path``."""
    _mem(memory_dir, "app-note")
    td = _inject(memory_dir, repo, ["app-note"])
    later = time.time() + 1
    _append(td, "outcome_events.jsonl",
            {"ts": later, "session_id": "s", "tool": "Read",
             "path": ".claude/worktrees/wt/.claude/memory/app-note.md",
             "tree_path": ".claude/memory/app-note.md"})
    _append(td, "outcome_events.jsonl",
            {"ts": later, "session_id": "s", "tool": "Edit", "path": ".claude/memory/app-note.md"})
    _append(td, "outcome_events.jsonl",
            {"ts": later, "session_id": "s", "tool": "Read", "path": ".claude/memory/MEMORY.md"})
    r = OO.opened_after_recall(memory_dir)
    assert (r["opened"], r["opens"]) == (1, 1)


def test_fold_digests_and_missing_files_get_their_own_kind(repo, memory_dir):
    write_file(memory_dir, "hippo.json", json.dumps({"fold_digests": ["folded-*.md"]}))
    _mem(memory_dir, "folded-2026-q3")
    _mem(memory_dir, "app-note", "reference")
    _inject(memory_dir, repo, ["folded-2026-q3", "app-note", "since-archived"])
    _read(memory_dir, repo, os.path.join(memory_dir, "app-note.md"))
    kinds = OO.opened_after_recall(memory_dir)["by_kind"]
    assert kinds == {
        "digest": {"pairs": 1, "opened": 0},
        "reference": {"pairs": 1, "opened": 1},
        "gone": {"pairs": 1, "opened": 0},
    }


def test_a_user_tier_memory_keeps_its_type_in_the_split(repo, memory_dir, tmp_path, monkeypatch):
    user = tmp_path / "user-tier"
    user.mkdir()
    (user / "portable.md").write_text("---\nname: portable\ntype: feedback\n---\nbody\n", encoding="utf-8")
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", str(user))
    _inject(memory_dir, repo, ["portable"])
    _read(memory_dir, repo, str(user / "portable.md"))
    r = OO.opened_after_recall(memory_dir)
    assert r["by_kind"] == {"feedback": {"pairs": 1, "opened": 1}}


def test_no_signal_and_bogus_dirs_never_raise(repo, memory_dir, tmp_path):
    assert OO.opened_after_recall(memory_dir)["rate"] is None
    assert "no signal yet" in OO.format_opened_report(memory_dir)
    bogus = str(tmp_path / "nope")
    assert OO.opened_after_recall(bogus)["pairs"] == 0
    assert OO.opened_memory("", None, bogus) is None
    assert OO.served_before(bogus, "s", "x") is False


# ---- the rollup: a rate that outlives rotation ------------------------------------------ #
def test_an_open_folds_into_todays_rollup(repo, memory_dir):
    _mem(memory_dir, "app-note")
    _mem(memory_dir, "cold-note")
    td = _inject(memory_dir, repo, ["app-note"])
    TR.record_prompt(td, trigger="human", session_id="s", ran_recall=True, backend="bm25",
                     injected_chars=900, injected_memories=1)
    _read(memory_dir, repo, os.path.join(memory_dir, "app-note.md"))  # recall named it first
    _read(memory_dir, repo, os.path.join(memory_dir, "cold-note.md"))  # the agent went looking
    k = TR.summarize(TR.read_rollups(td))
    assert (k["injected_memories"], k["opens"], k["opens_after_recall"]) == (1, 2, 1)


def test_rows_from_before_the_counters_summarize_to_zero(tmp_path):
    td = str(tmp_path)
    TR.record_prompt(td, trigger="human", session_id="s", ran_recall=True, backend="bm25", injected_chars=10)
    k = TR.summarize(TR.read_rollups(td))
    assert (k["injected_memories"], k["opens"], k["opens_after_recall"]) == (0, 0, 0)


# ---- surfaces ------------------------------------------------------------------------------ #
def test_doctor_reports_the_rate_and_its_blind_spot(repo, memory_dir):
    _mem(memory_dir, "app-note", "feedback")
    td = _inject(memory_dir, repo, ["app-note"])
    TR.record_prompt(td, trigger="human", session_id="s", ran_recall=True, backend="bm25",
                     injected_chars=900, injected_memories=1)
    _read(memory_dir, repo, os.path.join(memory_dir, "app-note.md"))
    r = D.check_opened_after_recall(D.DoctorContext(memory_dir, repo))
    assert r["status"] == "ok"
    assert "1 of 1 injected memories (100.00%)" in r["message"]
    assert "feedback 1/1" in r["message"]
    assert "not shell reads" in r["message"]
    assert "30 days of rollups: 1 of 1" in r["message"]


def test_doctor_is_ok_without_signal_and_the_check_is_wired(repo, memory_dir):
    r = D.check_opened_after_recall(D.DoctorContext(memory_dir, repo))
    assert r["status"] == "ok" and "no signal yet" in r["message"]
    assert "opened_after_recall" in [label for label, _ in D.CHECKS]


def test_the_cli_prints_the_report(repo, memory_dir, capsys):
    _mem(memory_dir, "app-note")
    _inject(memory_dir, repo, ["app-note"])
    _read(memory_dir, repo, os.path.join(memory_dir, "app-note.md"))
    assert O.main(["--opened", "--memory-dir", memory_dir]) == 0
    assert "opened after recall: 1 of 1" in capsys.readouterr().out


def test_the_module_reaches_no_ranking_or_corpus_writer():
    """Measurement only: like ``outcome``, it may not import recall or a corpus writer, and
    it may not import ``outcome`` (which imports it)."""
    tree = ast.parse(inspect.getsource(OO))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    assert not names & {"recall", "new_memory", "outcome", "memory.recall", "memory.new_memory", "memory.outcome"}
