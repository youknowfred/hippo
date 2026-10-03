"""OBS-9: rows the suite leaked into a live reconsolidation ledger are quarantined from every
KPI reader, without hiding a real memory that happens to share a fixture's name."""

from __future__ import annotations

import json
import os

from memory.eval_metrics import graduation_rate
from memory.telemetry import read_reconsolidation_events


def _ledger(root, rows, memories=()):
    md = os.path.join(root, "memory")
    td = os.path.join(root, ".memory-telemetry")
    os.makedirs(md, exist_ok=True)
    os.makedirs(td, exist_ok=True)
    for name in memories:
        with open(os.path.join(md, name + ".md"), "w", encoding="utf-8") as fh:
            fh.write(f"---\nname: {name}\ndescription: d\n---\nb\n")
    with open(os.path.join(td, "reconsolidation_events.jsonl"), "w", encoding="utf-8") as fh:
        for name, outcome in rows:
            fh.write(json.dumps({"ts": 1.0, "name": name, "outcome": outcome}) + "\n")
    return td


def test_leaked_fixture_rows_never_reach_the_graduation_rate(tmp_path):
    td = _ledger(
        str(tmp_path),
        [("real_memory", "graduate"), ("real_memory", "demote"),
         ("m_alpha", "graduate"), ("m_feature_design", "graduate"), ("reranker_voyage", "graduate")],
        memories=["real_memory"],
    )
    assert graduation_rate(td) == {"rate": 0.5, "n": 2, "graduate": 1, "fix": 0, "demote": 1}
    assert len(list(read_reconsolidation_events(td, include_quarantined=True))) == 5


def test_a_real_memory_with_a_fixture_name_is_never_hidden(tmp_path):
    td = _ledger(str(tmp_path), [("m_alpha", "graduate")], memories=["m_alpha"])
    assert [e["name"] for e in read_reconsolidation_events(td)] == ["m_alpha"]


def test_an_archived_memory_with_a_fixture_name_is_never_hidden(tmp_path):
    td = _ledger(str(tmp_path), [("m_alpha", "graduate")])
    os.makedirs(os.path.join(str(tmp_path), "memory", "archive"))
    open(os.path.join(str(tmp_path), "memory", "archive", "m_alpha.md"), "w").close()
    assert [e["name"] for e in read_reconsolidation_events(td)] == ["m_alpha"]


def test_a_ledger_with_no_sibling_corpus_is_not_judged(tmp_path):
    td = str(tmp_path / "custom-telemetry")
    os.makedirs(td)
    with open(os.path.join(td, "reconsolidation_events.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": 1.0, "name": "m_alpha", "outcome": "graduate"}) + "\n")
    assert [e["name"] for e in read_reconsolidation_events(td)] == ["m_alpha"]
