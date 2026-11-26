"""HCR-3: the high profile's embedding space has its own calibrated floor and gate.

The numbers come from planning/HCR-3.evidence.2026-11-25.json (a real-model run, not
reproducible in CI: the test venv has no MLX on purpose). These tests pin the table entries
to that file, key them by the space id the high index stamps, and check that a space the
evidence did not measure (an MRL-truncated width, the 8B) stays uncalibrated.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from memory import embed_mlx, recall_abstain, recall_count
from memory import recall_rank as rr
from memory.build_index import LoadedIndex, bm25_terms, compute_bm25_stats
from memory.recall_query import tokenize

_EVIDENCE = os.path.join(
    os.path.dirname(__file__), "..", "planning", "HCR-3.evidence.2026-11-25.json"
)
_COUNT_EVIDENCE = os.path.join(
    os.path.dirname(__file__), "..", "planning", "HCR-3.count.evidence.2026-11-25.json"
)
FOUR_B = embed_mlx.space_id(embed_mlx.DEFAULT_HIGH_MODEL)


@pytest.fixture(autouse=True)
def _no_overrides(monkeypatch):
    for var in ("HIPPO_DENSE_FLOOR", "HIPPO_DENSE_FLOOR_BAND", "HIPPO_DISABLE_ABSTAIN_GATE", "HIPPO_DISABLE",
                "HIPPO_COUNT_BAND", "HIPPO_COUNT_WIDE", "HIPPO_SIGNAL_COUNT"):
        monkeypatch.delenv(var, raising=False)


def _chosen():
    with open(_EVIDENCE, encoding="utf-8") as fh:
        return json.load(fh)["chosen"]


def test_the_full_width_4b_space_is_the_plain_model_id():
    assert FOUR_B == "Qwen/Qwen3-Embedding-4B"


def test_floor_and_gate_entries_match_the_dated_evidence():
    chosen = _chosen()
    assert rr._DENSE_FLOOR_BY_MODEL[FOUR_B] == chosen["_DENSE_FLOOR_BY_MODEL"][FOUR_B]
    assert recall_abstain._COSINE_BY_MODEL[FOUR_B] == chosen["_COSINE_BY_MODEL"][FOUR_B]
    assert rr._dense_floor(FOUR_B) == 0.42


def test_gate_thresholds_sit_above_the_floor_and_in_order():
    th = recall_abstain._COSINE_BY_MODEL[FOUR_B]
    assert rr._dense_floor(FOUR_B) < th["support"] < th["strong"]


def test_the_band_default_is_unchanged_by_the_new_space():
    assert rr._dense_floor_band() == 0.002


@pytest.mark.parametrize(
    "space",
    [embed_mlx.space_id("Qwen/Qwen3-Embedding-4B", 1024), "Qwen/Qwen3-Embedding-8B"],
)
def test_spaces_the_evidence_did_not_measure_stay_uncalibrated(space):
    assert space not in rr._DENSE_FLOOR_BY_MODEL
    assert rr._dense_floor(space) == rr._DENSE_FLOOR_DEFAULT
    assert recall_abstain._thresholds(space) is None
    assert recall_count.thresholds(space) is None


def test_the_count_entry_matches_its_dated_evidence_and_cost_no_hard_set_row():
    with open(_COUNT_EVIDENCE, encoding="utf-8") as fh:
        ev = json.load(fh)
    assert recall_count._COUNT_BY_MODEL[FOUR_B] == ev["chosen"]["_COUNT_BY_MODEL"][FOUR_B]
    assert recall_count.thresholds(FOUR_B) == {"band": 0.12, "wide": 0.14}
    # measured against the floor and gate this file's other tests pin
    assert ev["configured"]["dense_floor"] == rr._DENSE_FLOOR_BY_MODEL[FOUR_B]
    assert ev["configured"]["gate"] == recall_abstain._COSINE_BY_MODEL[FOUR_B]
    # the rule the entry was admitted under: no hard-set row lost on any corpus, abstention equal
    runs = ev["shipped_vs_off_hook_mode_high"]
    assert set(runs["shipped"]) == {"golden", "packs", "hippo"}
    for corpus, on in runs["shipped"].items():
        off = runs["off"][corpus]
        assert on["rows_lost_vs_off"] == 0, corpus
        assert on["hard_set_hits"] == off["hard_set_hits"] and on["abstained"] == off["abstained"]
        assert on["served_rows_per_injecting_prompt"]["mean"] <= off["served_rows_per_injecting_prompt"]["mean"]


def test_the_count_band_covers_what_each_measured_corpus_asks_for():
    with open(_COUNT_EVIDENCE, encoding="utf-8") as fh:
        ev = json.load(fh)
    band = recall_count._COUNT_BY_MODEL[FOUR_B]["band"]
    for corpus, dist in ev["distributions_high"].items():
        assert band + 0.01 >= dist["expected_memory_gap_under_best"]["p75"], corpus  # doctor's tolerance


def test_the_private_corpus_evidence_carries_no_text():
    with open(_COUNT_EVIDENCE, encoding="utf-8") as fh:
        raw = fh.read()
    assert '"query"' not in raw and '"expected"' not in raw and '"name"' not in raw


def test_env_override_still_wins_in_the_high_space(monkeypatch):
    monkeypatch.setenv("HIPPO_DENSE_FLOOR", "0.3")
    assert rr._dense_floor(FOUR_B) == 0.3


_DOCS = [
    ("rules-bridge", "governance rules bridge between memory and the always loaded files"),
    ("release-chain", "release chain tagging guarded content checks before the tag"),
    ("dense-floor", "dense cosine floor calibration for the embedding model"),
]


def _index():
    entries = [
        {"name": n, "file": f"{n}.md", "description": d, "tokens": bm25_terms(tokenize(d)), "row": i}
        for i, (n, d) in enumerate(_DOCS)
    ]
    manifest = {"entries": entries, "body_chunks": [], "dense_ready": True, "model": FOUR_B,
                "bm25": compute_bm25_stats([e["tokens"] for e in entries])}
    return LoadedIndex(manifest, np.eye(len(_DOCS), dtype=np.float32))


def _verdict(cosines, query, bm25_desc):
    vec = np.asarray(cosines, dtype=np.float32)
    return recall_abstain.corroboration(bm25_terms(tokenize(query)), _index(), vec, bm25_desc, [])


def test_the_gate_now_applies_to_a_4b_index():
    # The best off-topic cosine measured anywhere was 0.4545; one coincidental shared term.
    verdict = _verdict([0.4545, 0.30, 0.20], "explain the offside rules", [0])
    assert verdict is not None and verdict["admit"] is False


def test_a_4b_cosine_over_strong_admits_on_dense_alone():
    # The lowest on-topic best cosine measured was 0.4949, over ``strong``.
    verdict = _verdict([0.4949, 0.30, 0.20], "something with no shared words at all", [])
    assert verdict["admit"] is True and verdict["why"] == "strong"
