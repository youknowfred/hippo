"""HCR-5: the personalized-PageRank graph lane (``recall_graph._ppr_expand``).

Opt-in beside GRA-1 behind HIPPO_GRAPH_PPR. The pins:

  - the flag follows the falsy-set convention and OFF is byte-identical: recall never
    calls the lane, and its output equals the explicitly-off spellings';
  - the walk reaches two hops (GRA-1 cannot) and rewards a node several seeds point at;
  - no reserved slots: the strongest graph candidate enters at GRA-1's ceiling (half the
    top seed) and an organic score that is already as good wins;
  - the supersede and recent-invalidation penalties apply to injected candidates, "old"
    rides through as state, and supersedes / contradicts edges are never walked;
  - RET-13's draft guard: a draft's own refines / derives-from are not walkable, the
    reverse direction is;
  - GRF-2 seed endorsement, HOT-9 hub damping and once-per-session suppression hold;
  - every degrade path returns the input untouched;
  - the walk runs in under 1 ms on a 5k-node graph.
"""

from __future__ import annotations

import os
import random
import time

import pytest

from memory import build_index as B
from memory import recall as R
from memory import recall_graph as RG


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("HIPPO_GRAPH_PPR", "HIPPO_GRAPH_SEEDS", "HIPPO_HUB_DAMPING"):
        monkeypatch.delenv(name, raising=False)
    RG._PPR_MEMO.clear()
    yield
    RG._PPR_MEMO.clear()


def _rec():
    return {"out": set(), "in": set(), "typed_out": {}, "typed_in": {}}


def _link(edges, a, b):
    edges.setdefault(a, _rec())
    edges.setdefault(b, _rec())
    edges[a]["out"].add(b)
    edges[b]["in"].add(a)


def _typed(edges, a, rel, b):
    edges.setdefault(a, _rec())
    edges.setdefault(b, _rec())
    edges[a]["typed_out"].setdefault(rel, set()).add(b)
    edges[b]["typed_in"].setdefault(rel, set()).add(a)


def _entries(*names):
    return [{"name": n} for n in names]


def _scores(out):
    return {i: s for i, s, _st in out}


# --- the flag ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,on", [
    (None, False), ("", False), ("0", False), ("false", False), ("False", False),
    ("1", True), ("true", True), ("yes", True),
])
def test_flag_is_default_off_with_the_falsy_set(monkeypatch, raw, on):
    if raw is not None:
        monkeypatch.setenv("HIPPO_GRAPH_PPR", raw)
    assert RG._graph_ppr_enabled() is on


def _corpus(md):
    os.makedirs(md, exist_ok=True)

    def w(name, desc, body="details\n"):
        with open(os.path.join(md, name + ".md"), "w", encoding="utf-8") as fh:
            fh.write(f"---\nname: {name}\ndescription: {desc}\nmetadata:\n  type: project\n---\n{body}")
    w("zebra_deploy", "zebra canary deploy rollout checklist", "See [[bridge_notes]].\n")
    w("bridge_notes", "notes that connect things", "See [[far_answer]].\n")
    w("far_answer", "the quokka staging answer")
    for i in range(6):
        w(f"filler_{i}", f"filler topic number {i} about walrus things")


def test_flag_off_is_byte_identical_and_never_walks(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md, idx = str(tmp_path / "memory"), str(tmp_path / "idx")
    _corpus(md)
    B.build_index(md, idx)

    def _boom(*_a, **_k):
        raise AssertionError("the PPR lane ran with the flag off")

    monkeypatch.setattr(R, "_ppr_expand", _boom)
    base = R.recall("zebra canary deploy rollout", k=10, memory_dir=md, index_dir=idx)
    for raw in ("0", "false", ""):
        monkeypatch.setenv("HIPPO_GRAPH_PPR", raw)
        assert R.recall("zebra canary deploy rollout", k=10, memory_dir=md, index_dir=idx) == base
    assert base and base[0]["name"] == "zebra_deploy"


def test_flag_on_routes_recall_through_the_walk(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md, idx = str(tmp_path / "memory"), str(tmp_path / "idx")
    _corpus(md)
    B.build_index(md, idx)
    calls = []
    real = RG._ppr_expand

    def _spy(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(R, "_ppr_expand", _spy)
    monkeypatch.setenv("HIPPO_GRAPH_PPR", "1")
    res = R.recall("zebra canary deploy rollout", k=10, memory_dir=md, index_dir=idx)
    assert calls
    names = [r["name"] for r in res]
    # two hops out: only the walk reaches it (no lexical overlap with the query)
    assert "far_answer" in names and "bridge_notes" in names
    monkeypatch.delenv("HIPPO_GRAPH_PPR")
    off = [r["name"] for r in R.recall("zebra canary deploy rollout", k=10, memory_dir=md, index_dir=idx)]
    assert "far_answer" not in off


# --- the walk -------------------------------------------------------------------------------

def test_the_walk_reaches_two_hops_where_one_hop_cannot(monkeypatch):
    monkeypatch.setenv("HIPPO_GRAPH_SEEDS", "1")
    edges: dict = {}
    _link(edges, "seed", "mid")
    _link(edges, "mid", "far")
    entries = _entries("seed", "mid", "far")
    penalized = [(0, 0.04, None)]
    _o, _i, one_hop = RG._expand_neighbors(penalized, entries, edges)
    out, injected, endorsed = RG._ppr_expand(penalized, entries, edges)
    assert one_hop == {1}
    assert injected == {1, 2} and endorsed == {1, 2}
    s = _scores(out)
    assert s[1] == pytest.approx(0.04 * RG._NEIGHBOR_DISCOUNT)  # the strongest: GRA-1's ceiling
    assert 0 < s[2] < s[1]


def test_a_two_hop_node_several_seeds_reach_outranks_one_only_one_reaches(monkeypatch):
    monkeypatch.setenv("HIPPO_GRAPH_SEEDS", "2")
    edges: dict = {}
    _link(edges, "s1", "m1")
    _link(edges, "s2", "m2")
    _link(edges, "m1", "shared")
    _link(edges, "m2", "shared")
    _link(edges, "m1", "solo")
    entries = _entries("s1", "s2", "m1", "m2", "shared", "solo")
    penalized = [(0, 0.04, None), (1, 0.039, None)]
    out, injected, _e = RG._ppr_expand(penalized, entries, edges)
    s = _scores(out)
    assert {4, 5} <= injected and s[4] > s[5]  # the walk votes; the 1-hop pull sees neither
    _o, one_hop, _e2 = RG._expand_neighbors(penalized, entries, edges)
    assert one_hop == {2, 3}


@pytest.mark.parametrize("seed", range(12))
def test_the_walk_is_a_superset_of_the_one_hop_pull(monkeypatch, seed):
    """Every node GRA-1 endorses stays endorsed, at no less than GRA-1's score — the walk
    may add MMR/knee protection, never remove it (the first live A/B's failure mode)."""
    monkeypatch.setenv("HIPPO_GRAPH_SEEDS", "3")
    rnd = random.Random(seed)
    names = [f"n{i:02d}" for i in range(40)]
    edges: dict = {n: _rec() for n in names}
    for _ in range(70):
        a, b = rnd.sample(names, 2)
        if rnd.random() < 0.8:
            _link(edges, a, b)
        else:
            _typed(edges, a, rnd.choice(["refines", "derives-from"]), b)
    entries = _entries(*names)
    order = rnd.sample(range(40), 40)
    penalized = [(i, 0.05 - r * 0.001, None) for r, i in enumerate(order)]
    drafts = {order[1]} if seed % 2 else set()
    seen = {names[order[5]]} if seed % 3 == 0 else None
    one = RG._expand_neighbors(penalized, entries, edges, None, drafts, expanded_before=seen)
    walk = RG._ppr_expand(penalized, entries, edges, None, drafts, expanded_before=seen)
    assert one[2] <= walk[2]
    s1, s2 = _scores(one[0]), _scores(walk[0])
    assert all(s2[j] >= s1[j] for j in s1)


def test_no_reserved_slots_an_organic_score_that_is_as_good_wins(monkeypatch):
    monkeypatch.setenv("HIPPO_GRAPH_SEEDS", "1")
    edges: dict = {}
    _link(edges, "seed", "mid")
    entries = _entries("seed", "mid", "other")
    penalized = [(0, 0.04, None), (2, 0.03, None), (1, 0.025, None)]
    out, injected, endorsed = RG._ppr_expand(penalized, entries, edges)
    assert out == penalized  # 0.025 organic beats the 0.02 ceiling: nothing replaced
    assert injected == set() and endorsed == {1}


def test_penalties_apply_to_injected_candidates(monkeypatch):
    monkeypatch.setenv("HIPPO_GRAPH_SEEDS", "1")
    edges: dict = {}
    for n in ("sup", "rec", "old", "plain"):
        _link(edges, "seed", n)
    entries = [{"name": "seed"}, {"name": "sup"}, {"name": "rec", "invalid_after": "2000-01-01"},
               {"name": "old"}, {"name": "plain"}]
    monkeypatch.setattr(RG, "_invalidation_state",
                        lambda e: {"rec": "recent", "old": "old"}.get(e["name"]))
    out, injected, _e = RG._ppr_expand([(0, 0.04, None)], entries, edges, {1: ["newer"]})
    s = {i: (sc, st) for i, sc, st in out}
    assert injected == {1, 2, 3, 4}
    assert s[1][0] == pytest.approx(s[4][0] * RG._SUPERSEDED_PENALTY)
    assert s[2] == (pytest.approx(s[4][0] * RG._INVALIDATION_PENALTY), "recent")
    assert s[3] == (pytest.approx(s[4][0]), "old")  # rides through as state; display drops it


def test_supersedes_and_contradicts_are_never_walked(monkeypatch):
    monkeypatch.setenv("HIPPO_GRAPH_SEEDS", "1")
    edges: dict = {}
    _typed(edges, "seed", "supersedes", "gone")
    _typed(edges, "seed", "contradicts", "rival")
    _typed(edges, "seed", "refines", "parent")
    _typed(edges, "child", "derives-from", "seed")
    entries = _entries("seed", "gone", "rival", "parent", "child")
    out, injected, endorsed = RG._ppr_expand([(0, 0.04, None)], entries, edges)
    assert injected == {3, 4} and endorsed == {3, 4}  # RET-13's typed relations, both ways


def test_a_drafts_own_lineage_is_not_walkable_but_the_reverse_is(monkeypatch):
    monkeypatch.setenv("HIPPO_GRAPH_SEEDS", "1")
    edges: dict = {}
    _typed(edges, "draft", "derives-from", "kid")
    _typed(edges, "draft", "refines", "base")
    entries = _entries("draft", "kid", "base")
    _o, injected, endorsed = RG._ppr_expand([(0, 0.04, None)], entries, edges, draft_seeds={0})
    assert injected == set() and endorsed == set()
    # a non-draft seed still pulls in the draft that derives from it
    entries2 = _entries("kid", "draft", "base")
    _o, injected2, _e = RG._ppr_expand([(0, 0.04, None)], entries2, edges, draft_seeds={1})
    assert 1 in injected2
    # ...and the walk does not continue through the draft's own lineage to "base"
    assert 2 not in injected2


def test_a_seed_next_to_another_seed_is_endorsed_not_injected(monkeypatch):
    monkeypatch.setenv("HIPPO_GRAPH_SEEDS", "2")
    edges: dict = {}
    _link(edges, "a", "b")
    entries = _entries("a", "b")
    penalized = [(0, 0.04, None), (1, 0.03, None)]
    out, injected, endorsed = RG._ppr_expand(penalized, entries, edges)
    assert out == penalized and injected == set() and endorsed == {0, 1}


def test_hub_damping_and_the_session_rule_hold(monkeypatch):
    monkeypatch.setenv("HIPPO_GRAPH_SEEDS", "1")
    edges: dict = {}
    _link(edges, "seed", "hub")
    _link(edges, "seed", "plain")
    _link(edges, "seed", "seen")
    for i in range(40):
        _link(edges, f"src{i}", "hub")
    entries = _entries("seed", "hub", "plain", "seen")
    notes: dict = {}
    out, injected, endorsed = RG._ppr_expand(
        [(0, 0.04, None)], entries, edges, expanded_before={"seen"}, notes=notes)
    assert injected == {1, 2} and endorsed == {1, 2}
    assert 1 in notes["damped"] and 3 in notes["expanded_before"]
    s = _scores(out)
    assert s[1] < s[2]
    monkeypatch.setenv("HIPPO_HUB_DAMPING", "0")
    s_off = _scores(RG._ppr_expand([(0, 0.04, None)], entries, edges)[0])
    assert s_off[1] > s[1]


@pytest.mark.parametrize("case", ["no_edges", "no_penalized", "seeds_zero", "seed_not_in_graph", "junk"])
def test_every_degrade_returns_the_input_untouched(monkeypatch, case):
    edges: dict = {}
    _link(edges, "seed", "mid")
    entries = _entries("seed", "mid", "loner")
    penalized = [(0, 0.04, None)]
    if case == "no_edges":
        edges = {}
    elif case == "no_penalized":
        penalized = []
    elif case == "seeds_zero":
        monkeypatch.setenv("HIPPO_GRAPH_SEEDS", "0")
    elif case == "seed_not_in_graph":
        penalized = [(2, 0.04, None)]
        monkeypatch.setenv("HIPPO_GRAPH_SEEDS", "1")
    else:
        edges = {"seed": {"out": 5}}  # malformed record: never raises
    assert RG._ppr_expand(penalized, entries, edges) == (penalized, set(), set())


def test_ties_are_deterministic_by_stem(monkeypatch):
    monkeypatch.setenv("HIPPO_GRAPH_SEEDS", "1")
    edges: dict = {}
    for n in ("zeta", "alpha", "mu"):
        _link(edges, "seed", n)
    entries = _entries("seed", "zeta", "alpha", "mu")
    out, _i, _e = RG._ppr_expand([(0, 0.04, None)], entries, edges)
    assert [i for i, _s, _st in out] == [0, 2, 3, 1]


# --- the budget -----------------------------------------------------------------------------

def _graph_5k():
    """5,000 memories, three wikilinks each to random targets: a mean degree near the live
    corpus's (about 6.6 walkable neighbors), with no clustering to keep the ball small."""
    rnd = random.Random(7)
    edges: dict = {}
    n = 5000
    for i in range(n):
        for _ in range(3):
            j = rnd.randrange(n)
            if j != i:
                _link(edges, f"m{i:04d}", f"m{j:04d}")
    return edges


def test_the_walk_runs_in_under_a_millisecond_on_5k_nodes():
    graph = RG._ppr_graph(_graph_5k())
    assert len(graph["names"]) == 5000
    seeds = [graph["node"][s] for s in ("m0000", "m0001", "m0002")]
    RG._ppr_mass(graph, seeds, [0.4, 0.3, 0.3], set())  # warm numpy
    times = []
    for _ in range(40):
        t0 = time.perf_counter()
        ball, mass = RG._ppr_mass(graph, seeds, [0.4, 0.3, 0.3], set())
        times.append((time.perf_counter() - t0) * 1000.0)
    times.sort()
    median = times[len(times) // 2]
    assert ball.size > 100 and float(mass.sum()) > 0.5
    assert median < 1.0, f"PPR walk median {median:.3f} ms >= 1 ms at 5k nodes"
