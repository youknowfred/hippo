"""HOT-7: doctor's recall-lanes line (K14's instrument, report-only per V2Q-15).

Here rather than in ``test_doctor.py`` because that file is at its size pin
(``tests/test_module_size.py``); the helpers are that module's own.
"""

from __future__ import annotations

import json

from memory import build_index as B
from memory import doctor as D
from memory.telemetry_rollup import record_prompt

from .conftest import write_file
from .test_doctor import _ctx, _seed


def _m(name, kind):
    return f'---\nname: {name}\ndescription: "{name} note"\nmetadata:\n  type: {kind}\n---\nbody\n'


def _corpus(memory_dir, monkeypatch, policy=None):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _m("a", "feedback"))
    write_file(memory_dir, "b.md", _m("b", "project"))
    write_file(memory_dir, "c.md", _m("c", "reference"))
    write_file(memory_dir, "x-round.md", _m("x-round", "project"))
    if policy is not None:
        write_file(memory_dir, "hippo.json", json.dumps(policy))
    B.build_index(memory_dir, B.default_index_dir(memory_dir))


def test_the_check_is_registered_once():
    assert [label for label, _ in D.CHECKS].count("recall_lanes") == 1


def test_lanes_line_reports_corpus_shares_and_says_when_nothing_was_counted(repo, memory_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "tel"))
    _corpus(memory_dir, monkeypatch)
    r = D.check_recall_lanes(_ctx(memory_dir, repo))
    assert r["status"] == "ok"
    assert "not active" in r["message"]
    assert "behavior 25.0%, fact 75.0%, journal 0.0%" in r["message"]
    assert "nothing counted yet" in r["message"]
    assert "\n" not in r["message"]


def test_lanes_line_reports_hook_shares_and_stays_ok(repo, memory_dir, monkeypatch, tmp_path):
    td = str(tmp_path / "tel")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    _corpus(memory_dir, monkeypatch, {"journals": ["*-round.md"]})
    for _ in range(3):
        record_prompt(td, trigger="human", ran_recall=True, backend="bm25", injected_chars=500,
                      injected_memories=4, injected_by_lane={"behavior": 3, "fact": 1},
                      rendered_by_lane={"behavior": 2, "fact": 1})
    record_prompt(td, trigger="human", ran_recall=True, backend="bm25", injected_chars=500,
                  injected_memories=4, injected_by_lane={"fact": 3, "journal": 1})
    r = D.check_recall_lanes(_ctx(memory_dir, repo))
    assert r["status"] == "ok"  # report-only, whatever the numbers say
    msg = r["message"]
    assert "active (the corpus declares journals)" in msg
    assert "16 memories): behavior 56.2%, journal 6.2%" in msg
    assert "rendered in full (9): behavior 66.7%, journal 0.0%" in msg
    assert "behavior 25.0%, fact 50.0%, journal 25.0%" in msg  # the corpus, as the index labels it


def test_lanes_line_without_an_index(repo, memory_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "tel"))
    _seed(memory_dir)
    r = D.check_recall_lanes(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "no current index yet" in r["message"]
