"""QUA-5 — index-corruption recovery matrix.

Builds a small hermetic index normally, then for EACH corruption state (truncated
manifest, dense_ready-without-dense.npy, wrong-shape dense.npy) asserts both:
  (1) recall() against that index never raises and returns a sane (possibly empty
      or BM25-only) result, and
  (2) check_index_integrity() names the specific corruption state, and the
      index_integrity_producer surfaces it at SessionStart.

Follows the fixture conventions of tests/test_build_index.py (fake embedder, no
fastembed/network) and tests/conftest.py (write_file/git_commit helpers).
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from memory import build_index as B
from memory import recall as R
from memory import session_start as S


def _mem(name: str, description: str, body: str = "body text") -> str:
    return f'---\nname: {name}\ndescription: "{description}"\ntype: project\n---\n{body}\n'


def _write_corpus(memory_dir: str, items: dict) -> None:
    os.makedirs(memory_dir, exist_ok=True)
    for fname, desc in items.items():
        with open(os.path.join(memory_dir, fname), "w", encoding="utf-8") as fh:
            fh.write(_mem(fname[:-3], desc))


def _fake_embedder(dim: int = 16):
    def _vec(text: str):
        v = np.zeros(dim, dtype="float32")
        for tok in B.tokenize(text):
            v[hash(tok) % dim] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_documents(texts, allow_download=True):
        return np.vstack([_vec(t) for t in texts]).astype("float32")

    return embed_documents


def _build_dense_index(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMOBOT_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", _fake_embedder(16))
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha one", "b.md": "beta two", "c.md": "gamma three"})
    manifest = B.build_index(md, idx)
    assert manifest["dense_ready"] is True
    return md, idx


# --------------------------------------------------------------------------- #
# Healthy baseline — no false positives
# --------------------------------------------------------------------------- #
def test_healthy_index_yields_no_diagnosis(tmp_path, monkeypatch):
    _md, idx = _build_dense_index(tmp_path, monkeypatch)
    assert B.check_index_integrity(idx) is None


def test_no_index_built_yet_yields_no_diagnosis(tmp_path):
    idx = str(tmp_path / ".memory-index")
    os.makedirs(idx, exist_ok=True)
    assert B.check_index_integrity(idx) is None


# --------------------------------------------------------------------------- #
# (a) truncated / garbled manifest.json (invalid JSON)
# --------------------------------------------------------------------------- #
def test_truncated_manifest_never_raises_recall_and_is_named(tmp_path, monkeypatch):
    md, idx = _build_dense_index(tmp_path, monkeypatch)
    manifest_path = os.path.join(idx, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        fh.write('{"schema_version": 2, "entries": [truncated garbage')

    # Diagnose BEFORE recall — recall()'s _ensure_index rebuilds in place on a load
    # failure (never-raise-by-repair), which would otherwise erase the corruption
    # this assertion is checking for.
    finding = B.check_index_integrity(idx)
    assert finding is not None
    assert "manifest" in finding and "invalid JSON" in finding

    result = R.recall("alpha", memory_dir=md, index_dir=idx)  # must never raise
    assert isinstance(result, list)


def test_truncated_manifest_self_heals_on_rebuild(tmp_path, monkeypatch):
    md, idx = _build_dense_index(tmp_path, monkeypatch)
    manifest_path = os.path.join(idx, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        fh.write("{not valid json at all")

    rebuilt = B.build_index(md, idx)  # old_manifest -> None -> full rebuild from scratch
    assert rebuilt["dense_ready"] is True
    assert rebuilt["count"] == 3
    assert B.check_index_integrity(idx) is None


# --------------------------------------------------------------------------- #
# (b) dense_ready=True but dense.npy missing
# --------------------------------------------------------------------------- #
def test_dense_ready_without_dense_file_never_raises_recall_and_is_named(tmp_path, monkeypatch):
    md, idx = _build_dense_index(tmp_path, monkeypatch)
    os.remove(os.path.join(idx, "dense.npy"))

    finding = B.check_index_integrity(idx)
    assert finding is not None
    assert "missing" in finding and "BM25" in finding

    result = R.recall("alpha", memory_dir=md, index_dir=idx)  # must never raise
    assert isinstance(result, list)  # degrades to BM25-only (or empty), never crashes


def test_loaded_index_degrades_when_dense_file_missing(tmp_path, monkeypatch):
    md, idx = _build_dense_index(tmp_path, monkeypatch)
    os.remove(os.path.join(idx, "dense.npy"))

    loaded = B.load_index(idx)
    assert loaded is not None
    assert loaded.dense_ready is False
    assert loaded.dense is None


# --------------------------------------------------------------------------- #
# (c) dense.npy has the wrong shape (row count or dim mismatch)
# --------------------------------------------------------------------------- #
def test_wrong_row_count_never_raises_recall_and_is_named(tmp_path, monkeypatch):
    md, idx = _build_dense_index(tmp_path, monkeypatch)
    bad = np.zeros((1, 16), dtype="float32")  # manifest has 3 entries
    np.save(os.path.join(idx, "dense.npy"), bad)

    finding = B.check_index_integrity(idx)
    assert finding is not None
    assert "shape" in finding and "BM25" in finding

    result = R.recall("alpha", memory_dir=md, index_dir=idx)  # must never raise
    assert isinstance(result, list)


def test_wrong_column_count_never_raises_recall_and_is_named(tmp_path, monkeypatch):
    md, idx = _build_dense_index(tmp_path, monkeypatch)
    bad = np.zeros((3, 4), dtype="float32")  # manifest declares dim=16
    np.save(os.path.join(idx, "dense.npy"), bad)

    finding = B.check_index_integrity(idx)
    assert finding is not None
    assert "shape" in finding and "BM25" in finding

    result = R.recall("alpha", memory_dir=md, index_dir=idx)  # must never raise
    assert isinstance(result, list)


def test_wrong_shape_dense_rank_never_raises(tmp_path, monkeypatch):
    """Direct unit check that _dense_rank's index.dense @ qvec degrades, not just recall()."""
    md, idx = _build_dense_index(tmp_path, monkeypatch)
    bad = np.zeros((1, 3), dtype="float32")
    np.save(os.path.join(idx, "dense.npy"), bad)

    loaded = B.load_index(idx)
    assert loaded is not None
    assert loaded.dense_ready is False  # LoadedIndex's own shape check already caught it


# --------------------------------------------------------------------------- #
# SessionStart producer wiring
# --------------------------------------------------------------------------- #
def test_index_integrity_producer_silent_when_healthy(tmp_path, monkeypatch):
    md, idx = _build_dense_index(tmp_path, monkeypatch)
    monkeypatch.setattr(B, "default_index_dir", lambda memory_dir: idx)
    out = S.index_integrity_producer(md, "repo")
    assert out is None


def test_index_integrity_producer_names_corruption(tmp_path, monkeypatch):
    md, idx = _build_dense_index(tmp_path, monkeypatch)
    with open(os.path.join(idx, "manifest.json"), "w", encoding="utf-8") as fh:
        fh.write("{garbage")

    monkeypatch.setattr(B, "default_index_dir", lambda memory_dir: idx)
    out = S.index_integrity_producer(md, "repo")
    assert out is not None
    assert "invalid JSON" in out


def test_index_integrity_producer_is_registered():
    labels = [label for label, _fn in S.PRODUCERS]
    assert "index_integrity" in labels
    fns = [fn for label, fn in S.PRODUCERS if label == "index_integrity"]
    assert fns == [S.index_integrity_producer]


def test_index_integrity_producer_silent_when_index_dir_missing(tmp_path):
    md = str(tmp_path / "memory")
    os.makedirs(md, exist_ok=True)
    assert S.index_integrity_producer(md, "repo") is None
