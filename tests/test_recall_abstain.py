"""HOT-2: the corroboration gate — inject only when the dense and lexical lanes agree.

A tiny in-memory index with identity dense rows, so each memory's cosine to the query is
exactly the value the test hands the (patched) query embedder.
"""

from __future__ import annotations

import numpy as np
import pytest

from memory import recall_abstain
from memory import recall_rank as rr
from memory.build_index import LoadedIndex, bm25_terms, compute_bm25_stats
from memory.recall import recall
from memory.recall_query import tokenize

BGE = "BAAI/bge-small-en-v1.5"

DOCS = [
    ("rules-bridge", "governance rules bridge between memory and the always loaded files"),
    ("release-chain", "release chain tagging guarded content checks before the tag"),
    ("dense-floor", "dense cosine floor calibration for the embedding model"),
    ("trust-spine", "trust spine consent fingerprint quarantine drift"),
]


def _index(model=BGE):
    entries = []
    for row, (name, desc) in enumerate(DOCS):
        entries.append(
            {"name": name, "file": f"{name}.md", "description": desc,
             "tokens": bm25_terms(tokenize(desc)), "row": row}
        )
    manifest = {
        "entries": entries,
        "body_chunks": [],
        "dense_ready": True,
        "model": model,
        "bm25": compute_bm25_stats([e["tokens"] for e in entries]),
    }
    return LoadedIndex(manifest, np.eye(len(DOCS), dtype=np.float32))


@pytest.fixture
def cosines(monkeypatch):
    """Set each memory's cosine to the query: ``cosines([0.6, 0.7, 0.1, 0.1])``."""
    monkeypatch.delenv("HIPPO_DISABLE_ABSTAIN_GATE", raising=False)
    monkeypatch.delenv("HIPPO_DENSE_FLOOR", raising=False)
    monkeypatch.setattr(rr, "DEFAULT_MODEL", BGE)

    def _set(values):
        vec = np.asarray(values, dtype=np.float32)
        monkeypatch.setattr(rr, "run_bounded", lambda fn, timeout: vec)

    return _set


def _recall(query):
    log: dict = {}
    hits = recall(query, k=5, index=_index(), drop_log=log)
    return [h["name"] for h in hits], log


def test_one_coincidental_shared_term_abstains(cosines):
    # "rules" is the only overlap, and the dense lane prefers a different memory.
    cosines([0.65, 0.70, 0.30, 0.20])
    names, log = _recall("explain the offside rules in football")
    assert names == []
    assert log["abstained"]["why"] == "uncorroborated"
    assert any(d["reason"] == "uncorroborated" for d in log["drops"])


def test_two_shared_terms_on_a_corroborated_memory_admit(cosines):
    cosines([0.20, 0.62, 0.30, 0.20])
    names, log = _recall("how do we guard the release tag")
    assert names and names[0] == "release-chain"
    assert "abstained" not in log


def test_a_strong_cosine_admits_on_dense_alone(cosines):
    cosines([0.10, 0.10, 0.81, 0.10])
    names, _ = _recall("what threshold separates relevant neighbors")
    assert names and names[0] == "dense-floor"


def test_one_shared_term_with_dense_support_admits(cosines):
    cosines([0.10, 0.10, 0.10, 0.73])
    names, _ = _recall("is the quarantine on")
    assert "trust-spine" in names


def test_a_strong_description_match_admits_without_dense(cosines):
    cosines([0.05, 0.05, 0.30, 0.05])
    names, _ = _recall("dense cosine floor calibration")
    assert names and names[0] == "dense-floor"


def test_an_uncalibrated_model_has_no_gate():
    vec = np.asarray([0.65, 0.70, 0.30, 0.20], dtype=np.float32)
    verdict = recall_abstain.corroboration(
        bm25_terms(tokenize("explain the offside rules")), _index(model="some/other-model"),
        vec, [0], [],
    )
    assert verdict is None


def test_no_cosines_means_no_gate():
    assert recall_abstain.corroboration(["rule"], _index(), None, [0], []) is None


def test_the_kill_switch_turns_the_gate_off(cosines, monkeypatch):
    cosines([0.65, 0.70, 0.30, 0.20])
    monkeypatch.setenv("HIPPO_DISABLE_ABSTAIN_GATE", "1")
    names, log = _recall("explain the offside rules in football")
    assert names  # the pre-HOT-2 behavior: one shared token admits
    assert "abstained" not in log


def test_receipt_names_the_closest_miss_and_the_thresholds(cosines):
    cosines([0.65, 0.70, 0.30, 0.20])
    _names, log = _recall("explain the offside rules in football")
    line = recall_abstain.receipt_line(log["abstained"])
    assert line.startswith("abstained: best ")
    assert "cosine" in line and "strong 0.76" in line and "0.72" in line


def test_why_prints_the_gate_receipt(cosines, monkeypatch, tmp_path):
    from memory import recall as recall_mod
    from memory.recall_view import _abstention_receipt

    cosines([0.65, 0.70, 0.30, 0.20])
    idx = _index()
    monkeypatch.setattr(recall_mod, "_ensure_index", lambda *a, **k: idx)
    monkeypatch.setattr(recall_mod, "_fuse_recall_tiers", lambda idx, *a, **k: idx)
    out = _abstention_receipt("explain the offside rules in football", str(tmp_path), None, None)
    assert "Reason: abstained: best " in out
