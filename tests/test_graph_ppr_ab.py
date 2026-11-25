"""HCR-5: the PPR lane's A/B rig (``memory.graph_ppr_eval``, ``eval --ab HIPPO_GRAPH_PPR``).

The pins: the flag is on the --ab whitelist and dispatches to its harness; the harness
routes through the ONE shared OFF → ON → OFF core (inv5) and carries its byte-identity
self-check; the reachability audit and warm latency are read under each arm; the flag is
restored exactly; the report lands in the gitignored telemetry dir with the footer, the
condition stamp and the edge-list stamp; a graph-less corpus reports a self-check, not a
finding.
"""

from __future__ import annotations

import inspect
import json
import os

import pytest

from memory import graph_ppr_eval as G
from memory.build_index import build_index, default_index_dir
from memory.telemetry import default_telemetry_dir


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.delenv("HIPPO_GRAPH_PPR", raising=False)


def _write(md, name, desc, body="details\n"):
    with open(os.path.join(md, name + ".md"), "w", encoding="utf-8") as fh:
        fh.write(f"---\nname: {name}\ndescription: {desc}\nmetadata:\n  type: project\n---\n{body}")


def _corpus(md, linked=True):
    _write(md, "zebra_deploy", "zebra canary deploy rollout checklist",
           "See [[bridge_notes]].\n" if linked else "x\n")
    _write(md, "bridge_notes", "notes that connect things", "See [[far_answer]].\n" if linked else "x\n")
    _write(md, "far_answer", "the quokka staging answer")
    for i in range(4):
        _write(md, f"filler_{i}", f"filler topic number {i} about walrus things")
    idx = default_index_dir(md)
    build_index(md, idx)
    return idx


def _hard_set(path, multi_rows=10):
    rows = ['- query: "zebra canary deploy rollout"\n  expected: [zebra_deploy]\n  category: single-hop\n']
    rows += [
        f'- query: "zebra canary deploy rollout {i}"\n  expected: [far_answer]\n  category: multi-hop\n'
        for i in range(multi_rows)
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(rows))
    return path


def test_the_flag_is_whitelisted_and_dispatches(memory_dir, monkeypatch):
    from memory import eval_recall as E

    assert "HIPPO_GRAPH_PPR" in E.AB_FLAGS
    seen = {}
    monkeypatch.setattr(G, "main", lambda argv: seen.setdefault("argv", argv) and 0)
    assert E.main(["--ab", "HIPPO_GRAPH_PPR", "--memory-dir", memory_dir]) == 0
    assert "--memory-dir" in seen["argv"]


def test_the_harness_uses_the_shared_core():
    src = inspect.getsource(G)
    assert "run_flag_arms(" in src and "flag_context(" in src
    assert "_arm(False)" not in inspect.getsource(G.run_ab)


def test_a_linked_corpus_measures_both_arms_and_restores_the_flag(memory_dir, tmp_path):
    idx = _corpus(memory_dir)
    td = default_telemetry_dir(memory_dir)
    hs = _hard_set(str(tmp_path / "hard.yaml"))
    report = G.run_ab(memory_dir=memory_dir, index_dir=idx, hard_set_path=hs, telemetry_dir=td, write=False)
    assert report.get("ok"), report.get("error")
    assert "HIPPO_GRAPH_PPR" not in os.environ
    assert report["off_arm_self_check"].startswith("pass")
    assert report["identical_arms"] is False  # the walk moved the ranking view
    assert report["deltas"]["multi-hop"]["recall"] > 0  # two hops out: only the walk reaches it
    reach = report["reachability"]
    assert reach["on"]["seed_rank_0"] >= reach["off"]["seed_rank_0"]
    assert set(report["latency_ms"]) == {"off", "on"} and report["latency_ms"]["on"]["n"] == 55
    g = report["condition"]["graph"]
    assert g["nodes"] == 7 and g["walkable_pairs"] == 4
    assert "resolvable_by_category" in report["condition"]
    assert "default-OFF" in report["footer"]


def test_a_graph_less_corpus_is_a_self_check_not_a_finding(memory_dir, tmp_path):
    idx = _corpus(memory_dir, linked=False)
    hs = _hard_set(str(tmp_path / "hard.yaml"), multi_rows=0)
    report = G.run_ab(memory_dir=memory_dir, index_dir=idx, hard_set_path=hs, write=False)
    assert report.get("ok"), report.get("error")
    assert report["identical_arms"] is True
    assert "not a finding" in report["identical_arms_note"].lower()
    assert "skipped" in report["reachability"]


def test_the_report_lands_in_telemetry_and_reads_back(memory_dir, tmp_path, capsys):
    idx = _corpus(memory_dir)
    td = default_telemetry_dir(memory_dir)
    hs = _hard_set(str(tmp_path / "hard.yaml"))
    assert G.main(["--memory-dir", memory_dir, "--index-dir", idx, "--hard-set", hs,
                   "--telemetry-dir", td]) == 0
    out = capsys.readouterr().out
    assert "graph-walk A/B" in out and "OFF-arm self-check: pass" in out
    path = os.path.join(td, "graph_ppr_ab.json")
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh)["flag"] == "HIPPO_GRAPH_PPR"
    assert G.read_report(memory_dir, td)["schema"] == 1
    assert os.path.exists(os.path.join(td, ".gitignore"))


def test_read_report_is_none_when_absent_or_wrong_schema(memory_dir, tmp_path):
    td = str(tmp_path / "t")
    assert G.read_report(memory_dir, td) is None
    os.makedirs(td)
    with open(os.path.join(td, "graph_ppr_ab.json"), "w", encoding="utf-8") as fh:
        json.dump({"schema": 99}, fh)
    assert G.read_report(memory_dir, td) is None
