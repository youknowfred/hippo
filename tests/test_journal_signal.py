"""HOT-13: a journal admitted by its own signal.

On a corpus that declares ``journals`` the prompt hook withholds every journal the prompt
does not name. One more admission: the journal that is the FIRST of all the fused
candidates, when the count rule finds evidence of its own for it. At most one per prompt,
and it is served beside the prompt's rows, never in place of one. A tiny in-memory index
with identity dense rows hands each memory the cosine the test chooses; the hook tests
drive ``run_recall`` over canned rows. Fixtures are written from scratch.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from memory import lanes as L
from memory import recall as R
from memory import recall_rank as rr
from memory import telemetry_rollup as TR
from memory.build_index import LoadedIndex, bm25_terms, compute_bm25_stats
from memory.recall_hook import run_recall
from memory.recall_query import tokenize

BGE = "BAAI/bge-small-en-v1.5"

DOCS = [
    # name, description, lane
    ("deploy-round", "deploy canary rollout round notes staging production", L.JOURNAL),
    ("deploy-runbook", "deploy runbook canary rollout staging", L.FACT),
    ("deploy-rollback", "rollback steps canary deploy fails revert release", L.FACT),
    ("canary-round", "canary metrics round notes error rate deploy", L.JOURNAL),
    ("billing-ledger", "billing ledger cutover checklist invoices", L.FACT),
    ("pager-feedback", "page the on call before a production deploy", L.BEHAVIOR),
]
QUERY = "deploy the canary rollout to production"
# The journal leads both lanes: best cosine, and the most shared terms.
JOURNAL_FIRST = [0.74, 0.70, 0.66, 0.65, 0.10, 0.64]
# A fact leads; the journals trail it by a hair.
FACT_FIRST = [0.70, 0.74, 0.66, 0.73, 0.10, 0.64]


def _index(declared=True, model=BGE):
    entries = [
        {"name": name, "file": f"{name}.md", "description": desc, "lane": lane,
         "tokens": bm25_terms(tokenize(desc)), "row": row}
        for row, (name, desc, lane) in enumerate(DOCS)
    ]
    manifest = {
        "entries": entries, "body_chunks": [], "dense_ready": True, "model": model,
        "bm25": compute_bm25_stats([e["tokens"] for e in entries]),
        "lanes": {"declared": declared, "globs": ["*-round.md"] if declared else []},
    }
    return LoadedIndex(manifest, np.eye(len(DOCS), dtype=np.float32))


@pytest.fixture
def cosines(monkeypatch):
    for name in ("HIPPO_SIGNAL_COUNT", "HIPPO_COUNT_BAND", "HIPPO_COUNT_WIDE", "HIPPO_DENSE_FLOOR",
                 "HIPPO_DISABLE", "HIPPO_DISABLE_ABSTAIN_GATE", "HIPPO_JOURNAL_SIGNAL",
                 "HIPPO_LANE_BEHAVIOR_SHARE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(rr, "DEFAULT_MODEL", BGE)

    def _set(values):
        vec = np.asarray(values, dtype=np.float32)
        monkeypatch.setattr(rr, "run_bounded", lambda fn, timeout: vec)

    return _set


def _recall(prompt=QUERY, declared=True, floor=(), model=BGE):
    req = L.LaneRequest(prompt, 6, floor=floor)
    rows = R.recall(prompt, k=6, index=_index(declared, model), lanes=req)
    return req, [r["name"] for r in rows]


# --------------------------------------------------------------------------- #
# recall(): which journal gets past the lane
# --------------------------------------------------------------------------- #
def test_the_first_fused_candidate_is_admitted_when_it_is_a_journal(cosines):
    cosines(JOURNAL_FIRST)
    req, names = _recall()
    assert req.active and names[0] == "deploy-round"
    assert req.by_signal() == frozenset({"deploy-round"})
    assert req.admitted_by == {"deploy-round": "signal"}
    assert "deploy-round" not in req.withheld
    assert "canary-round" not in names and "canary-round" in req.withheld  # one, not every journal


def test_every_other_row_is_what_recall_returns_without_the_admission(cosines, monkeypatch):
    cosines(JOURNAL_FIRST)
    _req, names = _recall()
    rows = R.recall(QUERY, k=6, index=_index(), lanes=L.LaneRequest(QUERY, 6))
    monkeypatch.setenv("HIPPO_JOURNAL_SIGNAL", "0")
    off = R.recall(QUERY, k=6, index=_index(), lanes=L.LaneRequest(QUERY, 6))
    assert names[0] == "deploy-round" and [r["name"] for r in off] == names[1:]
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    for with_j, without in zip(rows[1:], off):  # same row, one rank later
        assert {**with_j, "rank": without["rank"]} == without


def test_a_journal_that_does_not_lead_the_candidates_stays_withheld(cosines):
    cosines(FACT_FIRST)
    req, names = _recall()
    assert names and not ({"deploy-round", "canary-round"} & set(names))
    assert req.by_signal() == frozenset() and req.admitted_by == {}
    assert {"deploy-round", "canary-round"} <= req.withheld


def test_the_switch_turns_the_admission_off(cosines, monkeypatch):
    cosines(JOURNAL_FIRST)
    monkeypatch.setenv("HIPPO_JOURNAL_SIGNAL", "0")
    req, names = _recall()
    assert "deploy-round" not in names and req.by_signal() == frozenset()
    assert L.journal_signal() is False
    monkeypatch.setenv("HIPPO_JOURNAL_SIGNAL", "junk")  # junk reads as on
    assert L.journal_signal() is True


@pytest.mark.parametrize("why", ["count rule off", "uncalibrated model"])
def test_without_per_row_evidence_nothing_is_admitted(cosines, monkeypatch, why):
    cosines(JOURNAL_FIRST)
    if why == "count rule off":
        monkeypatch.setenv("HIPPO_SIGNAL_COUNT", "0")
        req, names = _recall()
    else:
        req, names = _recall(model="some/other-model")
    assert "deploy-round" not in names and req.by_signal() == frozenset()


def test_an_undeclared_corpus_is_untouched(cosines):
    cosines(JOURNAL_FIRST)
    req, names = _recall(declared=False)
    assert not req.active and req.by_signal() == frozenset() and req.admitted_by == {}
    assert {"deploy-round", "canary-round"} <= set(names)  # nothing withheld, as before


def test_a_journal_the_prompt_names_or_the_floor_holds_is_not_counted_as_signal(cosines):
    cosines(JOURNAL_FIRST)
    req, names = _recall(prompt=QUERY + " per [[deploy-round]]")
    assert "deploy-round" in names and req.admitted_by == {"deploy-round": "named"}
    assert req.by_signal() == frozenset()
    req, names = _recall(floor={"deploy-round"})
    assert "deploy-round" in names and req.admitted_by == {} and req.by_signal() == frozenset()


def test_explicit_recall_is_unchanged(cosines):
    cosines(JOURNAL_FIRST)
    rows = R.recall(QUERY, k=6, index=_index())
    assert {"deploy-round", "canary-round"} <= {r["name"] for r in rows}


# --------------------------------------------------------------------------- #
# LaneRequest.withhold(): the decision itself
# --------------------------------------------------------------------------- #
class _Signals:
    def __init__(self, earned):
        self.earned = earned

    def row(self, i):
        got = self.earned.get(i)
        return None if got is None else {"earned": got}


def _entries():
    return [{"name": name, "lane": lane} for name, _desc, lane in DOCS]


def _request():
    req = L.LaneRequest("an unrelated prompt", 6)
    req.active = True
    return req


def _beside(req, servable=lambda i: True):
    return [i for i, _ in req.beside(_entries(), servable)]


def test_the_picked_journal_stays_out_of_the_walk_and_comes_back_beside_it():
    req = _request()
    kept = req.withhold([(0, 0.05), (1, 0.04)], _entries(), None, _Signals({0: True}))
    assert [i for i, _ in kept] == [1]            # withheld from the walk like any journal
    assert req.by_signal() == frozenset()         # not admitted until recall serves it
    assert _beside(req) == [0] and req.admitted_by == {"deploy-round": "signal"}
    assert req.by_signal() == frozenset({"deploy-round"}) and "deploy-round" not in req.withheld


def test_a_journal_recall_cannot_serve_is_not_admitted():
    req = _request()
    req.withhold([(0, 0.05), (1, 0.04)], _entries(), None, _Signals({0: True}))
    assert _beside(req, servable=lambda i: False) == []
    assert req.admitted_by == {} and "deploy-round" in req.withheld


def test_a_leading_journal_with_no_evidence_of_its_own_is_withheld():
    req = _request()
    kept = req.withhold([(0, 0.05), (1, 0.04)], _entries(), None, _Signals({0: False}))
    assert [i for i, _ in kept] == [1] and _beside(req) == []
    req = _request()  # a row the count rule cannot judge (no cosine) is not admitted either
    req.withhold([(0, 0.05), (1, 0.04)], _entries(), None, _Signals({}))
    assert _beside(req) == [] and req.by_signal() == frozenset()


def test_only_the_first_candidate_is_ever_considered():
    req = _request()
    rows = [(1, 0.05), (0, 0.04), (3, 0.03)]
    kept = req.withhold(rows, _entries(), None, _Signals({0: True, 3: True}))
    assert [i for i, _ in kept] == [1] and _beside(req) == []


def test_the_decision_is_made_once_so_expansion_cannot_bring_a_second_journal():
    req = _request()
    sig = _Signals({0: True, 3: True})
    assert [i for i, _ in req.withhold([(0, 0.05), (1, 0.04)], _entries(), None, sig)] == [1]
    # The second call is the post-expansion list: another journal now leads it.
    assert [i for i, _ in req.withhold([(3, 0.06), (0, 0.05), (1, 0.04)], _entries(), None, sig)] == [1]
    assert _beside(req) == [0]
    # A first call with no evidence to judge by (a BM25-only index) decides nothing.
    req = _request()
    assert req.withhold([(0, 0.05)], _entries(), None, None) == [] and _beside(req) == []


def test_a_broken_signal_object_admits_nothing_and_raises_nothing():
    class Broken:
        def row(self, i):
            raise RuntimeError("no")

    req = _request()
    assert req.withhold([(0, 0.05), (1, 0.04)], _entries(), None, Broken()) == [(1, 0.04)]
    assert _beside(req) == []

    def boom(i):
        raise RuntimeError("no")

    req = _request()
    req.withhold([(0, 0.05)], _entries(), None, _Signals({0: True}))
    assert req.beside(_entries(), boom) == [] and req.admitted_by == {}


# --------------------------------------------------------------------------- #
# select(): the admitted journal rides beside the k rows
# --------------------------------------------------------------------------- #
def _r(name, lane):
    return {"name": name, "file": f"{name}.md", "description": f"{name} desc", "score": 0.01, "lane": lane}


def test_select_serves_the_extra_row_on_top_of_k():
    rows = [_r("j1", L.JOURNAL), _r("f1", L.FACT), _r("f2", L.FACT), _r("b1", L.BEHAVIOR),
            _r("f3", L.FACT), _r("f4", L.FACT)]
    without, cut_without = L.select(rows[1:], 3, 0.4)
    picked, cut = L.select(rows, 3, 0.4, extra={"j1"})
    assert [r["name"] for r in picked] == ["b1", "j1", "f1", "f2"]  # recall order kept
    assert [r["name"] for r in picked if r["name"] != "j1"] == [r["name"] for r in without]
    assert [r["name"] for r in cut] == [r["name"] for r in cut_without] == ["f3", "f4"]


def test_select_without_extra_is_as_before():
    rows = [_r("j1", L.JOURNAL), _r("f1", L.FACT), _r("f2", L.FACT), _r("f3", L.FACT)]
    assert L.select(rows, 2, 0.4) == L.select(rows, 2, 0.4, extra=())
    assert [r["name"] for r in L.select(rows, 2, 0.4)[0]] == ["j1", "f1"]
    assert L.select(rows, 2, 0.4, extra={"not-a-row"}) == L.select(rows, 2, 0.4)


# --------------------------------------------------------------------------- #
# the prompt hook
# --------------------------------------------------------------------------- #
def _canned(name, lane, score, earned=True):
    return {
        "name": name, "file": f"{name}.md", "description": f"about {name}", "score": score,
        "rank": 0, "backend": "dense+bm25", "via": "rank", "note": "", "stale_banner": "",
        "lane": lane, "signal": {"cos": 0.7, "gap": 0.0, "earned": earned},
    }


@pytest.fixture
def hook(tmp_path, monkeypatch):
    """``hook(rows, admitted)`` runs the prompt hook over canned recall rows on a corpus whose
    lanes are active; ``admitted`` is the journal recall admitted by signal, if any."""
    md = str(tmp_path / "memory")
    os.makedirs(md)
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# Memory Index\n\n## User\n")
    td = str(tmp_path / ".memory-telemetry")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.delenv("HIPPO_LANE_BEHAVIOR_SHARE", raising=False)

    def run(rows, admitted=None, k=10, session="s1"):
        def fake(*_a, **kw):
            req = kw["lanes"]
            req.active = True
            if admitted:
                req.admitted.add(admitted)
                req.admitted_by[admitted] = "signal"
            return [dict(r) for r in rows]

        monkeypatch.setattr(R, "recall", fake)
        block = run_recall("how do we deploy the canary rollout", session_id=session, k=k,
                           memory_dir=md, index_dir=str(tmp_path / "idx"), hook=True)
        with open(os.path.join(td, "recall_events.jsonl"), encoding="utf-8") as fh:
            event = json.loads(fh.read().splitlines()[-1])
        with open(os.path.join(td, "rollup_today.json"), encoding="utf-8") as fh:
            today = json.load(fh)
        return block, event, today["hook"]

    return run


def test_the_admitted_journal_is_an_extra_row_not_a_replacement(hook):
    facts = [_canned(f"f{i}", L.FACT, 0.05 - i * 0.001) for i in range(7)]
    _block, before, _ = hook(facts)
    assert before["names"] == ["f0", "f1", "f2", "f3", "f4"]  # a narrow prompt: five rows
    rows = [_canned("deploy-round", L.JOURNAL, 0.06)] + facts
    block, event, counters = hook(rows, admitted="deploy-round", session="s2")  # no cooldown
    assert event["names"] == ["deploy-round", "f0", "f1", "f2", "f3", "f4"]
    assert "• deploy-round " in block and "top 6 by hybrid recall" in block
    assert [d["name"] for d in event["drops"] if d["reason"] == "signal_cap"] == ["f5", "f6"]
    assert counters["journal_admitted"] == {"signal": 1}


def test_a_journal_recall_did_not_admit_by_signal_still_takes_a_slot(hook):
    # A prompt-named journal keeps HOT-7's behavior: it is one of the k rows.
    facts = [_canned(f"f{i}", L.FACT, 0.05 - i * 0.001) for i in range(7)]
    rows = [_canned("deploy-round", L.JOURNAL, 0.06)] + facts
    _block, event, _ = hook(rows)
    assert event["names"] == ["deploy-round", "f0", "f1", "f2", "f3"]


def test_the_rollup_merges_the_signal_admissions(tmp_path):
    td = str(tmp_path / "tel")
    for _ in range(2):
        TR.record_prompt(td, trigger="human", ran_recall=True, backend="dense+bm25",
                         injected_chars=100, journal_admitted={"signal": 1})
    TR.record_prompt(td, trigger="human", ran_recall=True, backend="dense+bm25",
                     injected_chars=100, journal_admitted={"named": 1})
    with open(os.path.join(td, "rollup_today.json"), encoding="utf-8") as fh:
        today = json.load(fh)
    assert today["hook"]["journal_admitted"] == {"signal": 2, "named": 1}
