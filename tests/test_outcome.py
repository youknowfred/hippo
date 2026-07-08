"""SIG-4: PostToolUse read-signal — the KPI-2 injection-precision MEASUREMENT.

A PostToolUse hook records file touches into a gitignored outcome ledger; injection_precision
later JOINS that against the episode buffer's injected memories via their cited_paths — "was an
injected memory's cited file subsequently touched in the same session?" MEASUREMENT ONLY: this
module influences no ranking (pinned by a negative-capability import test).
"""

from __future__ import annotations

import ast
import inspect
import json
import os

import memory.doctor as D
from memory import outcome as O
from memory import telemetry as T
from memory.telemetry import default_telemetry_dir

from .conftest import write_file


def _mem(md, name, cited, desc="a note"):
    cp = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    write_file(
        md,
        f"{name}.md",
        f'---\nname: {name}\ndescription: "{desc}"\ntype: project\n'
        f'cited_paths: {cp}\nsource_commit: "abc"\n---\nbody\n',
    )


def _touch(md, repo, path_rel, sid="s", tool="Read"):
    """Simulate the PostToolUse payload for a file-touching tool inside the repo."""
    payload = {"tool_name": tool, "tool_input": {"file_path": os.path.join(repo, path_rel)}, "session_id": sid}
    return O.record_from_payload(payload, memory_dir=md, repo_root=repo)


# ---- the hook write path (record_from_payload) -------------------------------------------- #
def test_records_file_touch_repo_relative(repo, memory_dir):
    assert _touch(memory_dir, repo, "src/app.py", sid="s") is True
    events = list(T.read_outcomes(default_telemetry_dir(memory_dir)))
    assert len(events) == 1
    assert events[0]["path"] == "src/app.py"  # stored repo-relative
    assert events[0]["tool"] == "Read"


def test_ignores_non_file_tools(repo, memory_dir):
    payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "session_id": "s"}
    assert O.record_from_payload(payload, memory_dir=memory_dir, repo_root=repo) is False
    assert list(T.read_outcomes(default_telemetry_dir(memory_dir))) == []


def test_ignores_touch_outside_repo(repo, memory_dir, tmp_path):
    outside = str(tmp_path / "elsewhere" / "x.py")
    payload = {"tool_name": "Edit", "tool_input": {"file_path": outside}, "session_id": "s"}
    assert O.record_from_payload(payload, memory_dir=memory_dir, repo_root=repo) is False
    assert list(T.read_outcomes(default_telemetry_dir(memory_dir))) == []


def test_notebook_path_is_recorded(repo, memory_dir):
    payload = {
        "tool_name": "NotebookEdit",
        "tool_input": {"notebook_path": os.path.join(repo, "nb/analysis.ipynb")},
        "session_id": "s",
    }
    assert O.record_from_payload(payload, memory_dir=memory_dir, repo_root=repo) is True
    assert list(T.read_outcomes(default_telemetry_dir(memory_dir)))[0]["path"] == "nb/analysis.ipynb"


# ---- the KPI-2 join (injection_precision) ------------------------------------------------- #
def test_injection_hit(repo, memory_dir):
    _mem(memory_dir, "app-note", ["src/app.py"])
    td = default_telemetry_dir(memory_dir)
    T.log_episode(["app-note"], query="app entrypoint", repo_root=repo, telemetry_dir=td, session_id="s")
    _touch(memory_dir, repo, "src/app.py", sid="s")  # touched after injection

    r = O.injection_precision(memory_dir)
    assert r["injected_with_cites"] == 1
    assert r["hits"] == 1
    assert r["precision"] == 1.0


def test_injection_miss(repo, memory_dir):
    _mem(memory_dir, "app-note", ["src/app.py"])
    td = default_telemetry_dir(memory_dir)
    T.log_episode(["app-note"], query="app entrypoint", repo_root=repo, telemetry_dir=td, session_id="s")
    _touch(memory_dir, repo, "src/other.py", sid="s")  # touched a DIFFERENT file

    r = O.injection_precision(memory_dir)
    assert r["injected_with_cites"] == 1 and r["hits"] == 0 and r["precision"] == 0.0


def test_touch_before_injection_is_not_a_hit(repo, memory_dir):
    """'injected THEN touched' — a touch earlier than the memory's recall ts does not count."""
    _mem(memory_dir, "app-note", ["src/app.py"])
    td = default_telemetry_dir(memory_dir)
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "episode_buffer.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": 100.0, "session_id": "s", "recalled_names": ["app-note"]}) + "\n")
    with open(os.path.join(td, "outcome_events.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": 50.0, "session_id": "s", "tool": "Read", "path": "src/app.py"}) + "\n")

    r = O.injection_precision(memory_dir)
    assert r["injected_with_cites"] == 1 and r["hits"] == 0


def test_cross_session_touch_is_not_a_hit(repo, memory_dir):
    _mem(memory_dir, "app-note", ["src/app.py"])
    td = default_telemetry_dir(memory_dir)
    T.log_episode(["app-note"], query="q", repo_root=repo, telemetry_dir=td, session_id="sA")
    _touch(memory_dir, repo, "src/app.py", sid="sB")  # different session
    r = O.injection_precision(memory_dir)
    assert r["hits"] == 0


def test_memory_without_cited_paths_excluded_from_denominator(repo, memory_dir):
    _mem(memory_dir, "with-cite", ["src/app.py"])
    _mem(memory_dir, "no-cite", [])  # no cited_paths -> no file signal
    td = default_telemetry_dir(memory_dir)
    T.log_episode(["with-cite", "no-cite"], query="q", repo_root=repo, telemetry_dir=td, session_id="s")
    _touch(memory_dir, repo, "src/app.py", sid="s")

    r = O.injection_precision(memory_dir)
    assert r["injected_with_cites"] == 1  # only with-cite counts
    assert r["hits"] == 1 and r["precision"] == 1.0


def test_no_signal_yet(repo, memory_dir):
    r = O.injection_precision(memory_dir)
    assert r["injected_with_cites"] == 0 and r["precision"] is None
    assert "no injected-then-touched signal yet" in O.format_report(memory_dir)


# ---- measurement-only guarantee ----------------------------------------------------------- #
def test_module_imports_no_ranking_or_corpus_writer():
    """MEASUREMENT ONLY: outcome.py must not import recall (ranking) or new_memory (corpus write)."""
    tree = ast.parse(inspect.getsource(O))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.lstrip("."))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.lstrip("."))
    assert "recall" not in imported, "SIG-4 is measurement-only — it must not touch ranking (recall)"
    assert "new_memory" not in imported, "SIG-4 must not import the corpus writer"


# ---- the doctor surface ------------------------------------------------------------------- #
def test_doctor_reports_precision(repo, memory_dir):
    _mem(memory_dir, "app-note", ["src/app.py"])
    td = default_telemetry_dir(memory_dir)
    T.log_episode(["app-note"], query="q", repo_root=repo, telemetry_dir=td, session_id="s")
    _touch(memory_dir, repo, "src/app.py", sid="s")

    r = D.check_injection_precision(D.DoctorContext(memory_dir, repo))
    assert r["status"] == "ok"
    assert "injection precision (KPI-2)" in r["message"]
    assert "100%" in r["message"]


def test_doctor_ok_without_signal(repo, memory_dir):
    r = D.check_injection_precision(D.DoctorContext(memory_dir, repo))
    assert r["status"] == "ok"
    assert "no injected-then-touched signal yet" in r["message"]


def test_wired_into_checks():
    assert "injection_precision" in [label for label, _ in D.CHECKS]


def test_bogus_dir_never_raises(tmp_path):
    bogus = str(tmp_path / "nope")
    assert O.injection_precision(bogus)["precision"] is None
    assert O.record_from_payload({"tool_name": "Read", "tool_input": {"file_path": "/x"}}, memory_dir=bogus, repo_root=bogus) is False
