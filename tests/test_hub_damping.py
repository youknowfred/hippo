"""HOT-9: hub damping in 1-hop neighbor expansion, and at most one expansion entry per session."""

from __future__ import annotations

import io
import json
import math
import os

import pytest

from memory import build_index as B
from memory import recall as R
from memory import recall_graph as RG


def _edges(in_degrees):
    """An edge list where node ``name`` has ``n`` inbound wikilinks from filler stems."""
    edges = {}
    for name, n in in_degrees.items():
        edges.setdefault(name, {"out": set(), "in": set(), "typed_out": {}, "typed_in": {}})
        for i in range(n):
            src = f"{name}-src{i}"
            edges.setdefault(src, {"out": set(), "in": set(), "typed_out": {}, "typed_in": {}})
            edges[src]["out"].add(name)
            edges[name]["in"].add(src)
    return edges


def test_median_in_degree_is_at_least_one():
    assert RG._median_in_degree({"a": {"in": set()}, "b": {"in": set()}}) == 1.0
    assert RG._median_in_degree({}) == 1.0
    edges = {"a": {"in": {"x", "y", "z"}}, "b": {"in": {"x"}, "typed_in": {"refines": {"q"}}},
             "c": {"in": {"x", "y", "z", "w"}}}
    assert RG._median_in_degree(edges) == 3.0  # 3, 2 (wikilink + refines), 4


def test_the_divisor_is_one_at_or_below_the_median_and_sublinear_above():
    assert RG._hub_divisor(0, 2.0) == 1.0
    assert RG._hub_divisor(2, 2.0) == 1.0
    assert RG._hub_divisor(8, 2.0) == pytest.approx(2.0)  # sqrt(4)
    assert RG._hub_divisor(200, 2.0) == pytest.approx(10.0)
    assert RG._hub_divisor(200, 2.0) < 200 / 2.0


def _scene(seed_links_to):
    """seed (rank 1) links to ``seed_links_to``; hub has 40 more in-links, plain none."""
    edges = _edges({"hub": 40, "plain": 0, "seed": 0})
    for stem in seed_links_to:
        edges["seed"]["out"].add(stem)
        edges[stem]["in"].add("seed")
    entries = [{"name": "seed"}, {"name": "hub"}, {"name": "plain"}]
    penalized = [(0, 0.040, None), (1, 0.001, None), (2, 0.001, None)]
    return penalized, entries, edges


def test_a_hub_neighbor_is_damped_and_a_typical_one_is_not(monkeypatch):
    monkeypatch.setenv("HIPPO_GRAPH_SEEDS", "1")
    penalized, entries, edges = _scene(["hub", "plain"])
    median = RG._median_in_degree(edges)
    notes: dict = {}
    out, injected, endorsed = RG._expand_neighbors(penalized, entries, edges, notes=notes)
    score = {i: s for i, s, _ in out}
    assert injected == {1, 2} and endorsed == {1, 2}
    assert score[2] == 0.040 * 0.5  # plain: at the median -> undamped
    want = 0.040 * 0.5 / math.sqrt(41 / median)
    assert score[1] == pytest.approx(want) and score[1] < score[2]
    assert notes["damped"] == {1: (0.040 * 0.5, pytest.approx(want))}


def test_damping_off_is_byte_identical(monkeypatch):
    monkeypatch.setenv("HIPPO_GRAPH_SEEDS", "1")
    penalized, entries, edges = _scene(["hub", "plain"])
    monkeypatch.setenv("HIPPO_HUB_DAMPING", "0")
    out, injected, endorsed = RG._expand_neighbors(penalized, entries, edges)
    assert out == [(0, 0.040, None), (1, 0.040 * 0.5, None), (2, 0.040 * 0.5, None)]
    assert injected == {1, 2} and endorsed == {1, 2}


def test_an_already_expanded_memory_is_neither_injected_nor_endorsed(monkeypatch):
    monkeypatch.setenv("HIPPO_GRAPH_SEEDS", "1")
    penalized, entries, edges = _scene(["hub", "plain"])
    notes: dict = {}
    out, injected, endorsed = RG._expand_neighbors(
        penalized, entries, edges, expanded_before={"plain"}, notes=notes)
    assert injected == {1} and endorsed == {1}
    assert (2, 0.001, None) in out  # its organic tuple rides through untouched
    assert notes["expanded_before"] == {2: 0.040 * 0.5}


def test_the_session_rule_never_touches_a_seed(monkeypatch):
    monkeypatch.setenv("HIPPO_GRAPH_SEEDS", "2")
    entries = [{"name": "a"}, {"name": "b"}]
    edges = _edges({"a": 0, "b": 0})
    edges["a"]["out"].add("b")
    edges["b"]["in"].add("a")
    penalized = [(0, 0.04, None), (1, 0.03, None)]
    out, _inj, endorsed = RG._expand_neighbors(penalized, entries, edges, expanded_before={"b"})
    assert out == penalized and endorsed == {0, 1}  # both seeds: GRF-2 endorsement unchanged


def test_expansion_drops_name_both_reasons():
    notes = {"damped": {1: (0.02, 0.004), 3: (0.001, 0.0005)}, "expanded_before": {2: 0.02, 4: 0.01}}
    drops = RG._expansion_drops(notes, final={0, 4}, cut=0.01)
    assert drops == [(1, "hub_damped", 0.004), (2, "expanded_this_session", 0.02)]
    # results short of k: every absent damped neighbor counts
    assert (3, "hub_damped", 0.0005) in RG._expansion_drops(notes, final={0}, cut=None)


# --- end to end: a real index, a hub, and the hook's session state ------------------------

def _corpus(md, n_fillers=12):
    os.makedirs(md, exist_ok=True)

    def w(name, desc, body="details\n"):
        with open(os.path.join(md, name + ".md"), "w", encoding="utf-8") as fh:
            fh.write(f"---\nname: {name}\ndescription: {desc}\nmetadata:\n  type: project\n---\n{body}")
    w("zebra_deploy", "zebra canary deploy rollout checklist", "See [[glossary_hub]] and [[quokka_notes]].\n")
    w("glossary_hub", "terms used across the project")
    w("quokka_notes", "quokka staging notes")
    for i in range(n_fillers):  # every filler links the hub: it has n_fillers + 1 in-links
        w(f"filler_{i}", f"filler topic number {i} about walrus things", "See [[glossary_hub]].\n")


def test_recall_records_the_hub_drop_and_the_hook_applies_the_session_rule(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_GRAPH_SEEDS", "1")
    td = str(tmp_path / "telemetry")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / "idx")
    _corpus(md)
    B.build_index(md, idx)

    monkeypatch.setenv("HIPPO_HUB_DAMPING", "0")
    off = {r["name"]: r for r in R.recall("zebra canary deploy rollout", k=10, memory_dir=md, index_dir=idx)}
    monkeypatch.delenv("HIPPO_HUB_DAMPING")
    log: dict = {}
    on = {r["name"]: r for r in R.recall("zebra canary deploy rollout", k=10, memory_dir=md,
                                         index_dir=idx, drop_log=log)}
    assert off["glossary_hub"]["via"] == "graph" and on["quokka_notes"]["via"] == "graph"
    assert on.get("glossary_hub", {}).get("score", 0) < off["glossary_hub"]["score"]

    log2: dict = {}
    again = R.recall("zebra canary deploy rollout", k=10, memory_dir=md, index_dir=idx,
                     drop_log=log2, expanded_before={"quokka_notes"})
    assert "quokka_notes" not in [r["name"] for r in again]
    assert {"name": "quokka_notes", "reason": "expanded_this_session"} in [
        {k: d[k] for k in ("name", "reason")} for d in log2["drops"]]

    def hook(sid):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
            {"prompt": "zebra canary deploy rollout", "session_id": sid})))
        assert R.main(["--stdin-json", "--memory-dir", md, "--index-dir", idx]) == 0
        capsys.readouterr()
        with open(os.path.join(td, "recall_events.jsonl"), encoding="utf-8") as fh:
            return [json.loads(x) for x in fh if x.strip()][-1]

    first = hook("s1")
    assert "quokka_notes" in first.get("expanded", [])
    second = hook("s1")  # same session: no second expansion entry
    assert "quokka_notes" not in second.get("expanded", [])
    assert "quokka_notes" not in second["names"]
    assert any(d["name"] == "quokka_notes" and d["reason"] == "expanded_this_session"
               for d in second.get("drops", []))
    other = hook("s2")  # another session starts fresh
    assert "quokka_notes" in other.get("expanded", [])
    with open(os.path.join(td, "episode_buffer.jsonl"), encoding="utf-8") as fh:
        eps = [json.loads(x) for x in fh if x.strip()]
    assert "quokka_notes" in eps[0]["expanded"]
    assert "quokka_notes" not in eps[1].get("expanded", [])


def test_a_memory_that_qualifies_on_its_own_score_is_never_suppressed(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_GRAPH_SEEDS", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / "idx")
    _corpus(md)
    B.build_index(md, idx)
    # "quokka staging" matches quokka_notes lexically: it ranks on its own score.
    res = R.recall("quokka staging notes", k=10, memory_dir=md, index_dir=idx,
                   expanded_before={"quokka_notes"})
    assert res and res[0]["name"] == "quokka_notes" and res[0]["via"] == "rank"
