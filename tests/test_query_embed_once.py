"""HOT-15 (a): explicit recall embeds the query ONCE when a section sub-index exists.

The section pass (``recall_sections.extra``) ranks the same query over the sub-index by
calling ``recall`` again; before this item that inner call embedded the query a second time
(about one model call per explicit recall on the field corpus). The vector the memory pass
computed is now reused for that one inner call, and only there: every other recall still
embeds its own query, and the rows are the same either way. Fixtures written from scratch.
"""

from __future__ import annotations

import json
import os
from contextlib import nullcontext

import numpy as np
import pytest

from memory import build_index as B
from memory import recall as R
from memory import recall_rank as RR
from memory import recall_sections as RS

_DIM = 8


def _vec(text: str) -> np.ndarray:
    """A deterministic unit vector per text (a stand-in model; no network, no fastembed)."""
    v = np.zeros(_DIM, dtype="float32")
    for w in text.lower().split():
        v[sum(map(ord, w)) % _DIM] += 1.0
    n = float(np.linalg.norm(v)) or 1.0
    return v / n


def _embed_documents(texts, allow_download=True):
    return np.stack([_vec(t) for t in texts]) if texts else np.zeros((0, _DIM), dtype="float32")


_DIGEST = """---
name: folded-garden
description: "Folded digest of old garden notes"
metadata:
  type: project
---
### orchard-pruning

Orchard pruning schedule for the winter apple trees

### compost-ratio

Compost ratio of browns to greens for the hot pile in the garden
"""


def _mem(name, desc):
    return f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: project\n---\n{desc}\n'


@pytest.fixture
def dense_corpus(tmp_path, monkeypatch):
    for k in ("HIPPO_DISABLE_DENSE", "HIPPO_DISABLE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "tel"))
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", str(tmp_path / "no-user"))
    monkeypatch.setenv("HIPPO_LOCAL_MEMORY_DIR", str(tmp_path / "no-local"))
    monkeypatch.setenv("HIPPO_TRUST_ALL", "1")
    monkeypatch.setattr(B, "embed_documents", _embed_documents)
    md, idx = str(tmp_path / "memory"), str(tmp_path / "idx")
    os.makedirs(md)
    files = {
        "watering.md": _mem("watering", "Watering cadence for the garden beds in summer"),
        "compost.md": _mem("compost", "Compost pile turning for the garden in autumn"),
        "billing.md": _mem("billing", "Billing ledger cutover checklist for invoices"),
        "folded-garden.md": _DIGEST,
        "MEMORY.md": "# Memory Index\n\n## User\n",
    }
    for name, text in files.items():
        with open(os.path.join(md, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    with open(os.path.join(md, "hippo.json"), "w", encoding="utf-8") as fh:
        json.dump({"fold_digests": ["folded-*.md"]}, fh)
    m = B.build_index(md, idx)
    assert m["dense_ready"] is True
    calls: list = []

    def embed_query(text, allow_download=False):
        calls.append(text)
        return _vec(text)

    monkeypatch.setattr(RR, "embed_query", embed_query)
    monkeypatch.setattr(RR, "_dense_floor", lambda model: 0.0)  # the stand-in model is uncalibrated
    return md, idx, calls


def _rows(rows):
    return [(r["name"], round(float(r["score"]), 9), bool(r.get("section"))) for r in rows]


def test_explicit_recall_with_sections_embeds_the_query_once(dense_corpus):
    md, idx, calls = dense_corpus
    rows = R.recall("garden compost", k=5, memory_dir=md, index_dir=idx)
    assert any(r.get("section") for r in rows), "the fixture must reach the section pass"
    assert calls == ["garden compost"]


def test_reusing_the_vector_changes_no_row(dense_corpus, monkeypatch):
    md, idx, calls = dense_corpus
    once = R.recall("garden compost", k=5, memory_dir=md, index_dir=idx)
    monkeypatch.setattr(RS, "_reuse_query_vector", lambda query: nullcontext())  # the old double embed
    calls.clear()
    twice = R.recall("garden compost", k=5, memory_dir=md, index_dir=idx)
    assert calls == ["garden compost", "garden compost"]
    assert _rows(once) == _rows(twice)


def test_each_recall_still_embeds_its_own_query(dense_corpus):
    md, idx, calls = dense_corpus
    R.recall("garden compost", k=5, memory_dir=md, index_dir=idx)
    R.recall("garden compost", k=5, memory_dir=md, index_dir=idx)
    R.recall("billing ledger", k=5, memory_dir=md, index_dir=idx)
    # one embed per top-level recall: the reuse never outlives the section pass it serves
    assert calls == ["garden compost", "garden compost", "billing ledger"]


def test_a_failed_first_embed_is_not_reused(dense_corpus, monkeypatch):
    md, idx, calls = dense_corpus
    R.recall("garden compost", k=5, memory_dir=md, index_dir=idx)
    real = RR.embed_query
    state = {"n": 0}

    def flaky(text, allow_download=False):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("model unavailable")
        return real(text, allow_download=allow_download)

    monkeypatch.setattr(RR, "embed_query", flaky)
    R.recall("garden compost", k=5, memory_dir=md, index_dir=idx)
    # the memory pass failed (BM25 carried it), so the section pass embeds for itself
    # rather than reusing the previous recall's vector
    assert state["n"] == 2
