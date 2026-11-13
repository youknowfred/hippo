"""HOT-8: an injection count that scales with signal.

The count rule itself (``recall_count``), the ``signal`` each hook row carries, and what
the prompt hook does with it: a servable row with no evidence of its own is not served.
A tiny in-memory index with identity dense rows hands each memory exactly the cosine the
test chooses; the hook tests drive ``run_recall`` over canned rows. Fixtures are written
from scratch.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from memory import recall as R
from memory import recall_count as C
from memory import recall_rank as rr
from memory import telemetry_rollup as TR
from memory.build_index import LoadedIndex, bm25_terms, compute_bm25_stats
from memory.lanes import LaneRequest
from memory.recall_hook import run_recall
from memory.recall_query import tokenize

BGE = "BAAI/bge-small-en-v1.5"

DOCS = [
    ("deploy-runbook", "deploy runbook canary rollout staging production"),
    ("deploy-rollback", "rollback steps canary deploy fails revert release"),
    ("canary-metrics", "canary metrics dashboard error rate deploy rollout"),
    ("billing-ledger", "billing ledger cutover checklist invoices"),
    ("orchard-notes", "orchard pruning schedule winter"),
    ("pager-policy", "pager policy on call escalation deploy"),
]
QUERY = "deploy the canary rollout"


def _index(model=BGE):
    entries = [
        {"name": name, "file": f"{name}.md", "description": desc,
         "tokens": bm25_terms(tokenize(desc)), "row": row}
        for row, (name, desc) in enumerate(DOCS)
    ]
    manifest = {
        "entries": entries, "body_chunks": [], "dense_ready": True, "model": model,
        "bm25": compute_bm25_stats([e["tokens"] for e in entries]),
    }
    return LoadedIndex(manifest, np.eye(len(DOCS), dtype=np.float32))


@pytest.fixture
def cosines(monkeypatch):
    """Set each memory's cosine to the query, in ``DOCS`` order."""
    for name in ("HIPPO_SIGNAL_COUNT", "HIPPO_COUNT_BAND", "HIPPO_COUNT_WIDE", "HIPPO_DENSE_FLOOR",
                 "HIPPO_DISABLE", "HIPPO_DISABLE_ABSTAIN_GATE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(rr, "DEFAULT_MODEL", BGE)

    def _set(values):
        vec = np.asarray(values, dtype=np.float32)
        monkeypatch.setattr(rr, "run_bounded", lambda fn, timeout: vec)

    return _set


def _hook_rows(model=BGE):
    rows = R.recall(QUERY, k=6, index=_index(model), lanes=LaneRequest(QUERY, 6))
    return {r["name"]: r for r in rows}


def _signal(values, name):
    """The signal ``recall_count`` gives ``name`` when the memories' cosines are ``values``."""
    idx = _index()
    q = bm25_terms(tokenize(QUERY))
    lexical = sorted(
        (i for i, e in enumerate(idx.entries) if set(q) & set(e["tokens"])),
        key=lambda i: -len(set(q) & set(idx.entries[i]["tokens"])),
    )
    sig = C.signals(q, idx, np.asarray(values, dtype=np.float32), lexical, [], 0.60)
    return sig.row([n for n, _ in DOCS].index(name))


# --------------------------------------------------------------------------- #
# the signal each hook row carries
# --------------------------------------------------------------------------- #
NARROW = [0.74, 0.70, 0.62, 0.61, 0.10, 0.66]  # best 0.74: 0.14 over the 0.60 floor


def test_hook_rows_carry_their_signal(cosines):
    cosines(NARROW)
    rows = _hook_rows()
    assert rows and all(isinstance(r["signal"], dict) for r in rows.values())
    top = rows["deploy-runbook"]["signal"]
    assert top["cos"] == pytest.approx(0.74, abs=1e-4) and top["gap"] == 0.0 and top["earned"]


def test_a_row_near_the_best_cosine_is_earned_and_a_far_one_is_not(cosines):
    near = _signal(NARROW, "deploy-rollback")
    assert near["gap"] == pytest.approx(0.04, abs=1e-4) and near["earned"] is True
    assert "wide" not in near
    far = _signal(NARROW, "billing-ledger")  # 0.13 under the best, and no shared term
    assert "lexical_rank" not in far and far["earned"] is False


def test_a_row_in_both_lanes_top_is_earned_even_far_from_the_best(cosines):
    sig = _signal(NARROW, "canary-metrics")
    assert sig["gap"] > 0.05 and sig["dense_rank"] <= 10 and sig["lexical_rank"] <= 10
    assert sig["earned"] is True


def test_a_description_that_names_the_subject_is_earned_without_dense_support(cosines):
    # The runbook shares three query terms (deploy, canary, rollout) and leads the lexical
    # lane, but its cosine is far under the best and outside the dense top 10 would be the
    # field case; here it is simply far under the best.
    far = [0.40, 0.74, 0.62, 0.61, 0.10, 0.66]
    sig = _signal(far, "deploy-runbook")
    assert sig["gap"] > 0.3 and sig["lexical_rank"] == 1
    assert sig["earned"] is True


def test_a_wide_margin_over_the_floor_earns_every_row(cosines):
    wide = [0.80, 0.70, 0.62, 0.61, 0.61, 0.66]  # 0.20 over the floor
    for name, _ in DOCS:
        sig = _signal(wide, name)
        assert sig["earned"] is True and sig["wide"] is True


def test_both_numbers_can_be_overridden(cosines, monkeypatch):
    assert _signal(NARROW, "billing-ledger")["earned"] is False
    monkeypatch.setenv("HIPPO_COUNT_WIDE", "0.10")
    assert _signal(NARROW, "billing-ledger")["earned"] is True
    monkeypatch.setenv("HIPPO_COUNT_WIDE", "junk")  # junk reads as the default
    assert _signal(NARROW, "billing-ledger")["earned"] is False
    monkeypatch.setenv("HIPPO_COUNT_BAND", "0.2")
    assert _signal(NARROW, "billing-ledger")["earned"] is True


def test_explicit_recall_carries_no_signal(cosines):
    cosines([0.74, 0.70, 0.62, 0.61, 0.10, 0.66])
    rows = R.recall(QUERY, k=6, index=_index())
    assert rows and all("signal" not in r for r in rows)


def test_the_switch_an_uncalibrated_model_and_no_cosines_leave_rows_unjudged(cosines, monkeypatch):
    cosines([0.74, 0.70, 0.62, 0.61, 0.10, 0.66])
    assert all("signal" not in r for r in _hook_rows("some/other-model").values())
    monkeypatch.setenv("HIPPO_SIGNAL_COUNT", "0")
    assert all("signal" not in r for r in _hook_rows().values())
    assert C.signals([], _index(), None, [], [], 0.6) is None
    assert C.thresholds("some/other-model") is None


# --------------------------------------------------------------------------- #
# cut(): which servable rows are not served
# --------------------------------------------------------------------------- #
def _row(name, earned=None):
    r = {"name": name}
    if earned is not None:
        r["signal"] = {"earned": earned}
    return r


def test_cut_keeps_the_first_row_and_every_earned_row():
    rows = [_row("a", False), _row("b", True), _row("c", False), _row("d", True)]
    assert C.cut(rows) == {"c"}


def test_cut_never_touches_a_row_without_a_signal():
    rows = [_row("a", False), _row("rule"), _row("b", False)]
    assert C.cut(rows) == {"b"}
    assert C.cut([_row("x"), _row("y")]) == set()
    assert C.cut([]) == set()


# --------------------------------------------------------------------------- #
# the prompt hook
# --------------------------------------------------------------------------- #
def _canned(name, earned, score, **extra):
    row = {
        "name": name, "file": f"{name}.md", "description": f"about {name}", "score": score,
        "rank": 0, "backend": "dense+bm25", "via": "rank", "note": "", "stale_banner": "",
        "lane": "fact", "signal": {"cos": 0.7, "gap": 0.0, "earned": earned},
    }
    row.update(extra)
    return row


@pytest.fixture
def hook(tmp_path, monkeypatch):
    """``hook(rows, k=...)`` runs the prompt hook over canned recall rows; returns
    ``(block, recall event, rollup hook counters)``."""
    md = str(tmp_path / "memory")
    os.makedirs(md)
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# Memory Index\n\n## User\n")
    td = str(tmp_path / ".memory-telemetry")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")

    def run(rows, k=10, session="s1", prompt="how do we deploy the canary rollout"):
        monkeypatch.setattr(R, "recall", lambda *a, **kw: [dict(r) for r in rows])
        block = run_recall(prompt, session_id=session, k=k,
                           memory_dir=md, index_dir=str(tmp_path / "idx"), hook=True)
        with open(os.path.join(td, "recall_events.jsonl"), encoding="utf-8") as fh:
            event = json.loads(fh.read().splitlines()[-1])
        counters = TR.read_today(td)["hook"] if hasattr(TR, "read_today") else {}
        return block, event, counters

    return run


def test_a_row_with_no_evidence_is_not_served_and_the_drop_says_why(hook):
    rows = [_canned("a", True, 0.05), _canned("b", False, 0.04), _canned("c", True, 0.03)]
    block, event, _ = hook(rows)
    assert event["names"] == ["a", "c"]
    assert "• a " in block and "• c " in block and "• b " not in block
    assert "top 2 by hybrid recall" in block
    assert {"name": "b", "reason": "low_signal", "score": 0.04} in event["drops"]


def test_the_best_ranked_row_is_served_whatever_its_evidence(hook):
    rows = [_canned("a", False, 0.05), _canned("b", False, 0.04)]
    _block, event, _ = hook(rows)
    assert event["names"] == ["a"]


def test_a_memory_the_prompt_names_is_served_without_evidence(hook):
    rows = [_canned("a", True, 0.05), _canned("deploy-notes", False, 0.04), _canned("c", False, 0.03)]
    _block, event, _ = hook(rows, prompt="what does [[deploy-notes]] say about the canary rollout")
    assert event["names"] == ["a", "deploy-notes"]
    assert [d["name"] for d in event["drops"] if d["reason"] == "low_signal"] == ["c"]


def test_a_freed_slot_goes_to_the_next_earned_row(hook):
    rows = [_canned("a", True, 0.05), _canned("b", False, 0.04), _canned("c", True, 0.03)]
    _block, event, _ = hook(rows, k=2)
    assert event["names"] == ["a", "c"]  # before the rule: a and b, with c past k


def test_rule_pointers_and_rows_without_a_signal_are_served_as_before(hook):
    plain = _canned("plain", False, 0.04)
    del plain["signal"]
    rule = _canned("CLAUDE.md § Deploys", False, 0.02, corpus=R._RULES_SOURCE)
    del rule["signal"]
    _block, event, _ = hook([_canned("a", True, 0.05), plain, rule])
    assert event["names"] == ["a", "plain", "CLAUDE.md § Deploys"]


def test_a_cooldown_collapsed_row_is_not_judged(hook):
    rows = [_canned("a", True, 0.05), _canned("b", False, 0.04)]
    hook([_canned("b", True, 0.05)], session="s2")  # turn 1 serves b
    _block, event, _ = hook(rows, session="s2")     # turn 2: b collapses, with no evidence
    assert event["names"] == ["a"] and event["collapsed"] == ["b"]
    assert not any(d["reason"] == "low_signal" for d in event.get("drops", []))


def test_the_rollup_counts_served_rows_per_injecting_prompt(tmp_path, monkeypatch):
    td = str(tmp_path / "tel")
    for n in (2, 2, 0, 11):
        TR.record_prompt(td, trigger="human", ran_recall=True, backend="dense+bm25",
                         injected_chars=100, served_rows=n)
    TR.record_prompt(td, trigger="human", ran_recall=True, backend="none")  # nothing injected
    with open(os.path.join(td, "rollup_today.json"), encoding="utf-8") as fh:
        today = json.load(fh)
    assert today["hook"]["served_rows_hist"] == {"2": 2, "0": 1, "10+": 1}


def test_row_budget_is_k_only_when_the_margin_is_wide_or_nothing_was_judged():
    narrow = [_row("a", True), _row("b", True)]
    assert C.row_budget(narrow, 10) == 5
    assert C.row_budget(narrow, 3) == 3          # never more than k
    wide = [dict(_row("a", True), signal={"earned": True, "wide": True}), _row("b", True)]
    assert C.row_budget(wide, 10) == 10
    assert C.row_budget([_row("plain")], 10) == 10  # an unjudged prompt (BM25-only) keeps k
    assert C.row_budget([], 10) == 10


def test_a_narrow_prompt_serves_at_most_five_rows_and_says_why_it_stopped(hook):
    rows = [_canned(f"m{i}", True, 0.05 - i * 0.001) for i in range(7)]
    _block, event, _ = hook(rows)
    assert event["names"] == ["m0", "m1", "m2", "m3", "m4"]
    assert [d["name"] for d in event["drops"] if d["reason"] == "signal_cap"] == ["m5", "m6"]


def test_a_wide_prompt_still_serves_k(hook):
    rows = [_canned(f"m{i}", True, 0.05 - i * 0.001) for i in range(7)]
    for r in rows:
        r["signal"]["wide"] = True
    _block, event, _ = hook(rows)
    assert len(event["names"]) == 7
    assert not any(d["reason"] in ("signal_cap", "low_signal") for d in event.get("drops", []))


# --------------------------------------------------------------------------- #
# the sweep: margin quantiles and the count rule's calibration target
# --------------------------------------------------------------------------- #
def test_quantiles_are_nearest_rank_and_empty_is_just_a_count():
    from memory.eval_floor import quantiles

    q = quantiles([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
    assert q == {"n": 10, "p10": 0.2, "p50": 0.6, "p75": 0.8, "p90": 1.0}
    assert quantiles([]) == {"n": 0}


def test_the_sweep_records_margin_quantiles_and_the_expected_memorys_gap(tmp_path, monkeypatch):
    import zlib

    import yaml

    from memory import build_index as B
    from memory import eval_floor as F

    def _vec(text, dim=64):
        v = np.zeros(dim, dtype="float32")
        for tok in B.tokenize(text):
            v[zlib.crc32(tok.encode("utf-8")) % dim] += 1.0
        norm = np.linalg.norm(v)
        return v / norm if norm else v

    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.delenv("HIPPO_DISABLE", raising=False)
    monkeypatch.setattr(B, "embed_documents",
                        lambda texts, allow_download=True: np.vstack([_vec(t) for t in texts]))
    monkeypatch.setattr(R, "embed_query", lambda text, allow_download=False: _vec(text))
    md = str(tmp_path / "memory")
    os.makedirs(md)
    for name, desc in DOCS:
        with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
            fh.write(f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: project\n---\n{desc}\n')
    idx = str(tmp_path / "idx")
    B.build_index(md, idx)
    hard = str(tmp_path / "hard.yaml")
    with open(hard, "w", encoding="utf-8") as fh:
        yaml.safe_dump([
            {"query": "deploy runbook canary rollout", "expected": ["deploy-runbook"]},
            # the expected memory is NOT this query's best match: a positive gap
            {"query": "canary deploy rollout metrics error", "expected": ["pager-policy"]},
        ], fh)
    off = str(tmp_path / "off.yaml")
    with open(off, "w", encoding="utf-8") as fh:
        yaml.safe_dump(["the offside rule in football", "sourdough starter hydration"], fh)

    doc = F.floor_sweep(memory_dir=md, index_dir=idx, hard_set_path=hard,
                        abstention_set_path=off, telemetry_dir=str(tmp_path / "tel"), write=False)
    assert doc["ok"] is True and doc["schema"] == 1
    assert doc["on_top"]["n"] == 2 and doc["off_top"]["n"] == 2
    assert doc["on_top"]["p90"] == pytest.approx(max(doc["on_top"]["p10"], doc["on_top"]["p90"]))
    gap = doc["expected_gap"]
    assert gap["n"] == 2 and gap["p10"] == pytest.approx(0.0, abs=1e-6) and gap["p90"] > 0.0
    assert doc["count"]["band"] == 0.05 and doc["count"]["wide"] == 0.18
    assert doc["count"]["band_recommended"] == gap["p75"]
