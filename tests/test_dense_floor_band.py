"""HOT-10: the soft band under the dense floor.

A description row whose cosine is under the floor by no more than the band joins the dense
lane only when the BM25 description lane corroborates it; otherwise it is dropped as
``dense_floor_band``, so the autopsy tells a near miss from a clear miss. A band of 0 is the
floor alone, byte for byte. The suite runs BM25-only, so every cosine here is injected: the
index has identity dense rows and the (patched) query embedder hands back the cosines.
"""

from __future__ import annotations

import random

import numpy as np
import pytest

from memory import recall_rank as rr
from memory.build_index import LoadedIndex, bm25_terms, compute_bm25_stats
from memory.recall import recall
from memory.recall_query import tokenize

BGE = "BAAI/bge-small-en-v1.5"
FLOOR = 0.60  # the calibrated bge-small floor in rr._DENSE_FLOOR_BY_MODEL

DOCS = [
    ("rules-bridge", "governance rules bridge between memory and the always loaded files"),
    ("release-chain", "release chain tagging guarded content checks before the tag"),
    ("dense-floor", "dense cosine floor calibration for the embedding model"),
    ("trust-spine", "trust spine consent fingerprint quarantine drift"),
]


def _index(n_body: int = 0, docs=DOCS):
    entries = []
    for row, (name, desc) in enumerate(docs):
        entries.append(
            {"name": name, "file": f"{name}.md", "description": desc,
             "tokens": bm25_terms(tokenize(desc)), "row": row}
        )
    chunks = [
        {"entry": j % len(docs), "text": "chunk", "tokens": ["chunk"], "row": len(docs) + j}
        for j in range(n_body)
    ]
    manifest = {
        "entries": entries,
        "body_chunks": chunks,
        "dense_ready": True,
        "model": BGE,
        "bm25": compute_bm25_stats([e["tokens"] for e in entries]),
    }
    return LoadedIndex(manifest, np.eye(len(docs) + n_body, dtype=np.float32))


@pytest.fixture
def cosines(monkeypatch):
    """Set each dense row's cosine to the query: ``cosines([0.8, 0.597, 0.1, 0.1])``."""
    monkeypatch.delenv("HIPPO_DISABLE_ABSTAIN_GATE", raising=False)
    monkeypatch.delenv("HIPPO_DENSE_FLOOR", raising=False)
    monkeypatch.setenv("HIPPO_DENSE_FLOOR_BAND", "0.005")  # wide enough for the 0.003 cases
    monkeypatch.setattr(rr, "DEFAULT_MODEL", BGE)

    def _set(values):
        vec = np.asarray(values, dtype=np.float32)
        monkeypatch.setattr(rr, "run_bounded", lambda fn, timeout: vec)

    return _set


def _rows(index, corroborated, *, watch=None):
    sub: list = []
    above = rr._dense_rank_rows("q", index, subfloor_out=sub, corroborated=corroborated,
                                watch_rows=watch)
    return above, sub


# --------------------------------------------------------------------------- #
# The knob
# --------------------------------------------------------------------------- #
def test_band_default_and_env_override(monkeypatch):
    monkeypatch.delenv("HIPPO_DENSE_FLOOR_BAND", raising=False)
    assert rr._dense_floor_band() == rr._DENSE_FLOOR_BAND_DEFAULT
    monkeypatch.setenv("HIPPO_DENSE_FLOOR_BAND", "0.01")
    assert rr._dense_floor_band() == 0.01
    monkeypatch.setenv("HIPPO_DENSE_FLOOR_BAND", "0")
    assert rr._dense_floor_band() == 0.0
    assert rr._band_corroborators([0, 1]) is None  # off -> no band at all


@pytest.mark.parametrize("raw", ["nope", "-0.01", "inf", "  "])
def test_malformed_band_falls_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv("HIPPO_DENSE_FLOOR_BAND", raw)
    assert rr._dense_floor_band() == rr._DENSE_FLOOR_BAND_DEFAULT


def test_corroborators_are_the_bm25_description_top_depth(monkeypatch):
    monkeypatch.delenv("HIPPO_DENSE_FLOOR_BAND", raising=False)
    ranked = list(range(25))
    assert rr._band_corroborators(ranked) == set(range(rr._BAND_CORROBORATION_DEPTH))
    assert rr._band_corroborators([]) == set()  # on, but nothing can corroborate


# --------------------------------------------------------------------------- #
# The walk (``_dense_rank_rows``)
# --------------------------------------------------------------------------- #
def test_a_corroborated_band_row_joins_after_every_above_floor_row(cosines):
    cosines([0.80, 0.597, 0.62, 0.40])
    above, sub = _rows(_index(), {1})
    assert above == [0, 2, 1]  # above-floor order untouched; the band row follows
    assert sub == [(3, pytest.approx(0.40))]  # the clear miss is still recorded


def test_an_uncorroborated_band_row_is_recorded_not_admitted(cosines):
    cosines([0.80, 0.597, 0.598, 0.40])
    above, sub = _rows(_index(), {3})  # only the clear miss is "corroborated"
    assert above == [0]
    assert [r for r, _ in sub] == [2, 1, 3]
    assert rr._subfloor_reason(0.598, FLOOR, {3}) == "dense_floor_band"
    assert rr._subfloor_reason(0.40, FLOOR, {3}) == "dense_floor"


def test_the_band_never_creates_a_dense_lane(cosines):
    # Nothing clears the floor: a corroborated band row must not make one up.
    cosines([0.597, 0.596, 0.20, 0.10])
    above, sub = _rows(_index(), {0, 1})
    assert above == []
    assert [r for r, _ in sub][:2] == [0, 1]


def test_a_body_chunk_above_the_floor_does_not_open_the_band(cosines):
    # Only a body chunk (row 4) clears the floor; a description lane still does not exist.
    cosines([0.597, 0.10, 0.10, 0.10, 0.70])
    above, _ = _rows(_index(n_body=1), {0})
    assert above == [4]


def test_body_chunk_rows_are_never_admitted_from_the_band(cosines):
    cosines([0.80, 0.10, 0.10, 0.10, 0.598])
    above, _ = _rows(_index(n_body=1), {0, 1, 2, 3, 4})
    assert above == [0]


def test_below_the_band_is_the_floor_exactly(cosines, monkeypatch):
    monkeypatch.setenv("HIPPO_DENSE_FLOOR_BAND", "0.005")
    cosines([0.80, 0.5949, 0.10, 0.10])
    above, _ = _rows(_index(), {1})
    assert above == [0]


def test_band_zero_is_byte_identical_to_the_floor_alone(cosines, monkeypatch):
    """Random cosines, random corroborator sets: with the band at 0 the walk's output and its
    drop records equal the band-less walk's, for every subfloor/watch combination."""
    rng = random.Random(1010)
    n = 40
    idx = _index(docs=[(f"m{i}", f"memory number {i}") for i in range(n)])
    monkeypatch.setenv("HIPPO_DENSE_FLOOR_BAND", "0")
    for trial in range(60):
        vals = [round(rng.uniform(0.50, 0.70), 4) for _ in range(n)]
        cosines(vals)
        corr = rr._band_corroborators(list(range(n)))
        assert corr is None
        watch = {rng.randrange(n)} if trial % 3 == 0 else None
        a_none, s_none = _rows(idx, None, watch=watch)
        a_band, s_band = _rows(idx, corr, watch=watch)
        assert (a_none, s_none) == (a_band, s_band)
        assert rr._dense_rank_rows("q", idx) == a_none  # the no-drop-log caller too
        for _, sim in s_none:
            assert rr._subfloor_reason(sim, FLOOR, corr) == "dense_floor"


def test_band_on_never_moves_an_above_floor_row(cosines, monkeypatch):
    rng = random.Random(56)
    n = 40
    idx = _index(docs=[(f"m{i}", f"memory number {i}") for i in range(n)])
    for _ in range(60):
        vals = [round(rng.uniform(0.55, 0.65), 4) for _ in range(n)]
        cosines(vals)
        vals = [float(np.float32(v)) for v in vals]  # the walk compares float32 cosines
        lo = FLOOR - 0.01
        corr = set(rng.sample(range(n), 10))
        monkeypatch.setenv("HIPPO_DENSE_FLOOR_BAND", "0")
        base, _ = _rows(idx, None)
        monkeypatch.setenv("HIPPO_DENSE_FLOOR_BAND", "0.01")
        banded, _ = _rows(idx, corr)
        assert banded[: len(base)] == base
        extra = banded[len(base):]
        assert all(i in corr and lo <= vals[i] < FLOOR for i in extra)
        if base:
            want = sorted((i for i in corr if lo <= vals[i] < FLOOR),
                          key=lambda i: -vals[i])
            assert sorted(extra) == sorted(want)


# --------------------------------------------------------------------------- #
# Through recall()
# --------------------------------------------------------------------------- #
def _recall(query, monkeypatch, band):
    monkeypatch.setenv("HIPPO_DENSE_FLOOR_BAND", band)
    log: dict = {}
    hits = recall(query, k=5, index=_index(), drop_log=log)
    return hits, log


def test_recall_admits_a_lexically_corroborated_near_miss(cosines, monkeypatch):
    # rules-bridge clears the floor strongly (the HOT-2 gate admits on dense alone);
    # release-chain sits 0.003 under the floor and BM25 ranks it first.
    cosines([0.80, 0.597, 0.598, 0.40])
    off, off_log = _recall("release chain tagging", monkeypatch, "0")
    on, on_log = _recall("release chain tagging", monkeypatch, "0.005")
    s_off = {h["name"]: h["score"] for h in off}
    s_on = {h["name"]: h["score"] for h in on}
    assert s_on["release-chain"] > s_off["release-chain"]  # now carries dense evidence too
    off_reasons = {d["name"]: d["reason"] for d in off_log["drops"]}
    on_reasons = {d["name"]: d["reason"] for d in on_log["drops"]}
    assert off_reasons["release-chain"] == "dense_floor"
    assert "release-chain" not in on_reasons  # admitted, not dropped
    assert off_reasons["dense-floor"] == "dense_floor"
    assert on_reasons["dense-floor"] == "dense_floor_band"  # near miss, uncorroborated
    assert on_reasons["trust-spine"] == "dense_floor"  # clear miss keeps its reason
    # near_miss (the abstention arm's evidence) still lists every recorded sub-floor row
    assert {m["name"] for m in on_log["near_miss"]} == {"dense-floor", "trust-spine"}
    assert on_log["dense_floor"] == off_log["dense_floor"] == FLOOR


def test_band_zero_recall_is_identical(cosines, monkeypatch):
    cosines([0.80, 0.597, 0.598, 0.40])
    a, la = _recall("release chain tagging", monkeypatch, "0")
    import memory.recall as recall_mod

    monkeypatch.setattr(recall_mod, "_band_corroborators", lambda bm25: None)
    b, lb = _recall("release chain tagging", monkeypatch, "0.005")
    assert a == b and la == lb


def test_an_off_topic_prompt_still_abstains(cosines, monkeypatch):
    # One coincidental shared term ("rules"), nothing above the floor, a near miss in the
    # band: HOT-2's gate still abstains, and nothing is admitted.
    cosines([0.598, 0.597, 0.30, 0.20])
    off, off_log = _recall("explain the offside rules in football", monkeypatch, "0")
    on, on_log = _recall("explain the offside rules in football", monkeypatch, "0.02")
    assert off == [] and on == []
    assert on_log["abstained"]["why"] == off_log["abstained"]["why"]


def test_nothing_above_the_floor_ranks_exactly_as_before(cosines, monkeypatch):
    # BM25 carries this prompt; the band has no dense lane to extend.
    cosines([0.10, 0.598, 0.10, 0.10])
    monkeypatch.setenv("HIPPO_DISABLE_ABSTAIN_GATE", "1")
    off, _ = _recall("release chain tagging", monkeypatch, "0")
    on, on_log = _recall("release chain tagging", monkeypatch, "0.005")
    assert off == on
    assert {d["name"]: d["reason"] for d in on_log["drops"]}.get("release-chain") == (
        "dense_floor_band"
    )
