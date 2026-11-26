"""HCR-6: the retrieval-profile A/B core (``memory.profile_ab``), on a BM25 corpus.

The real arms (the Qwen3 embedder and rerankers) run only in ``bench/profile_ab.py`` on a
machine that has them; everything the instrument itself decides is pinned here.
"""

from __future__ import annotations

import os

import pytest

from memory import build_index as B
from memory import profile_ab as P
from memory import recall as RC
from memory import recall_rank as RR

_MEMS = {
    "zebra_deploy_runbook": "How the zebra service is deployed: zebra canary lane, zebra pager path.",
    "zebra_rollback_notes": "Zebra rollback notes for a failed zebra deploy.",
    "zebra_oncall_rota": "Who carries the zebra pager this quarter.",
    "llama_billing_cutover": "Llama billing cutover checklist: ledger freeze, invoice replay.",
}


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    md = str(tmp_path / "project" / ".claude" / "memory")
    os.makedirs(md)
    for name, desc in _MEMS.items():
        with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
            fh.write(f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: project\n---\n{desc}\n')
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# Memory Index\n\n## User\n")
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    idx = B.default_index_dir(md)
    B.build_index(md, idx)
    order = [r["name"] for r in RC.recall("zebra", k=10, index_dir=idx, memory_dir=md)]
    assert len(order) == 3
    hard = str(tmp_path / "hard.yaml")
    with open(hard, "w", encoding="utf-8") as fh:
        fh.write(f"- query: zebra\n  expected: [{order[-1]}]\n"
                 f"- query: llama billing\n  expected: [llama_billing_cutover]\n  category: multi-hop\n")
    return {"md": md, "idx": idx, "hard": hard, "indexes": {None: B.load_index(idx)}, "last": order[-1]}


def _run(corpus, arms, **kw):
    return P.run_profile_ab(arms, memory_dir=corpus["md"], lean_index_dir=corpus["idx"],
                            indexes=corpus["indexes"], hard_set_path=corpus["hard"], **kw)


def test_an_arm_identical_to_the_baseline_shows_no_delta(corpus):
    rep = _run(corpus, [P.Arm("lean"), P.Arm("lean-again")], k=10)
    assert rep["ok"] and rep["baseline"] == "lean" and rep["baseline_self_check"].startswith("pass")
    d = rep["deltas"]["lean-again"]
    assert d["overall"] == {"hits": 0, "mrr": 0.0} and d["injected_tokens_mean"] == 0.0
    assert all(c["hits"] == 0 and c["recall"] == 0.0 and c["low_n"] for c in d["by_category"].values())
    base = rep["arms"]["lean"]
    assert base["overall"]["hits"] == 2 and base["overall"]["n"] == 2
    assert set(base["by_category"]) == {"single-hop", "multi-hop"}
    assert base["injected_tokens"]["mean"] > 0 and base["latency_ms"]["n"] == 2
    assert base["lexical_only_queries"] == 2  # this corpus has no dense lane at all
    assert rep["condition"]["hard_set_n"] == 2 and rep["condition"]["corpus_n"] == 4
    assert set(rep["condition"]["resolvable_by_category"]) == {"lean"}
    assert "lean" not in rep["deltas"]


def test_a_deep_pool_reranks_past_the_cut_then_cuts_to_k(corpus, monkeypatch):
    """At k=1 the expected memory is ranked third; a reranker reading the top 3 can lift it."""
    seen = []

    def reverse(query, hits):
        seen.append(len(hits))
        return list(reversed(hits))

    monkeypatch.setattr(RR, "_cross_encoder_rerank", reverse)
    rep = _run(corpus, [P.Arm("lean"), P.Arm("deep", rerank=True, pool=3)], k=1)
    assert rep["ok"], rep
    assert rep["arms"]["lean"]["by_category"]["single-hop"]["hits"] == 0
    deep = rep["arms"]["deep"]
    assert deep["by_category"]["single-hop"]["hits"] == 1 and deep["pool"] == 3 and deep["rerank"]
    assert deep["rows_returned"]["mean"] <= 1.0  # cut back to k
    assert rep["deltas"]["deep"]["by_category"]["single-hop"]["hits"] == 1
    assert max(seen) == 3


def test_each_arm_holds_its_flags_and_restores_the_environment(corpus, monkeypatch):
    held = []
    real = RC.recall

    def spy(*a, **kw):
        held.append((os.environ.get("HIPPO_RERANK"), os.environ.get("HIPPO_GRAPH_SEEDS")))
        return real(*a, **kw)

    monkeypatch.setattr(RC, "recall", spy)
    monkeypatch.setenv("HIPPO_RERANK", "kept")
    monkeypatch.delenv("HIPPO_GRAPH_SEEDS", raising=False)
    arms = [P.Arm("lean"), P.Arm("rerank", rerank=True), P.Arm("seeds", env=(("HIPPO_GRAPH_SEEDS", "1"),)),
            P.Arm("deep", rerank=True, pool=99)]
    assert _run(corpus, arms, k=10)["ok"]
    # three recalls an arm: one untimed to wake its models, then the two hard-set rows
    assert held == [("0", None)] * 3 + [("1", None)] * 3 + [("0", "1")] * 3 + [("0", None)] * 6
    assert os.environ["HIPPO_RERANK"] == "kept" and "HIPPO_GRAPH_SEEDS" not in os.environ


def test_a_baseline_that_does_not_repeat_reports_nothing(corpus, monkeypatch):
    real, calls = P.run_arm, []

    def drifting(arm, **kw):
        out = real(arm, **kw)
        calls.append(arm.name)
        if calls.count("lean") == 2:
            out["overall"]["hits"] -= 1
        return out

    monkeypatch.setattr(P, "run_arm", drifting)
    rep = _run(corpus, [P.Arm("lean"), P.Arm("other")])
    assert rep["ok"] is False and "self-check FAILED" in rep["error"] and "arms" not in rep


def test_latency_alone_never_fails_the_self_check():
    a = {"by_category": {}, "overall": {}, "injected_tokens": {}, "rows_returned": {},
         "lexical_only_queries": 0, "latency_ms": {"p50": 1.0}}
    assert P._deterministic(a) == P._deterministic({**a, "latency_ms": {"p50": 9.0}})


def test_nothing_to_measure_is_an_error_not_a_report(corpus, tmp_path):
    assert _run(corpus, [])["ok"] is False
    assert _run(corpus, [P.Arm("a"), P.Arm("a")])["error"] == "arm names must be unique"
    assert "no index for" in _run(corpus, [P.Arm("lean"), P.Arm("high", space="Qwen/Qwen3-Embedding-4B")])["error"]
    empty = str(tmp_path / "empty.yaml")
    open(empty, "w").close()
    assert P.run_profile_ab([P.Arm("lean")], memory_dir=corpus["md"], lean_index_dir=corpus["idx"],
                            indexes=corpus["indexes"], hard_set_path=empty)["ok"] is False
