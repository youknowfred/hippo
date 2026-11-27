"""IDX-1: a manifest is bound to the one dense matrix its build wrote.

Measured on a 1,315-memory field corpus (2026-11-03): 1,016 entries (77%) were scored
against another memory's vector. ``build_index`` wrote ``dense.npy`` and then
``manifest.json``, each atomic (COR-12), but nothing ordered two concurrent builds, so
the last manifest could land over another build's matrix. QUA-4's load-time guard checks
shape and row bounds only, which a same-size pair passes, and every later incremental
build copied the wrong rows forward by hash. Each build now writes its own
``dense.<id>.npy`` and its manifest names it, so no interleaving can pair them wrongly;
a legacy (unbound) prior is spot-checked before its rows are reused.
"""

from __future__ import annotations

import json
import os
import threading
import time

import numpy as np
import pytest

from memory import build_index as B
from memory import build_index_dense as D


def _mem(name: str, description: str) -> str:
    return f'---\nname: {name}\ndescription: "{description}"\ntype: project\n---\nbody text\n'


def _write(md: str, items: dict) -> None:
    os.makedirs(md, exist_ok=True)
    for fname, desc in items.items():
        with open(os.path.join(md, fname), "w", encoding="utf-8") as fh:
            fh.write(_mem(fname[:-3], desc))


def _vec(text: str, dim: int = 32):
    v = np.zeros(dim, dtype="float32")
    for tok in B.tokenize(text):
        v[hash(tok) % dim] += 1.0
    n = np.linalg.norm(v)
    return v / n if n else v


@pytest.fixture
def embed(monkeypatch):
    calls = []

    def embed_documents(texts, allow_download=True):
        calls.append(len(texts))
        return np.vstack([_vec(t) for t in texts]).astype("float32")

    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.delenv("HIPPO_DISABLE", raising=False)
    monkeypatch.setattr(B, "embed_documents", embed_documents)
    return calls


_CORPUS = {
    "b.md": "voyage reranker cross encoder",
    "c.md": "budget timeout phase envelope",
    "d.md": "trust registry consent fingerprint",
}


def _misaligned(idx: str) -> list:
    loaded = B.load_index(idx)
    assert loaded is not None and loaded.dense is not None, "no dense view to check"
    return [e["name"] for e in loaded.entries
            if float(loaded.dense[e["row"]] @ _vec(e["doc_text"])) < 0.99]


def _make_legacy(idx: str, roll: int) -> None:
    """Rewrite a bound index into the pre-IDX-1 shape: ``dense.npy`` and a manifest that
    names no file — rolled by ``roll`` rows to model a torn pair."""
    with open(os.path.join(idx, B._MANIFEST_NAME), encoding="utf-8") as fh:
        manifest = json.load(fh)
    bound = os.path.join(idx, manifest.pop("dense_file"))
    np.save(os.path.join(idx, D.LEGACY_DENSE_NAME), np.roll(np.load(bound), roll, axis=0))
    os.remove(bound)
    with open(os.path.join(idx, B._MANIFEST_NAME), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)


def test_interleaved_builds_never_pair_one_manifest_with_another_matrix(tmp_path, embed, monkeypatch):
    md, idx = str(tmp_path / "memory"), str(tmp_path / "index")
    _write(md, _CORPUS)
    real_replace = os.replace
    a_at_manifest, b_done = threading.Event(), threading.Event()

    def replace(src, dst):
        if dst.endswith(B._MANIFEST_NAME) and threading.current_thread().name == "build-a":
            a_at_manifest.set()
            assert b_done.wait(10)
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    real_getpid = os.getpid  # the field race is two PROCESSES: give each thread its own pid
    monkeypatch.setattr(os, "getpid", lambda: real_getpid() + (threading.current_thread().name == "build-a"))
    a = threading.Thread(target=B.build_index, args=(md, idx), name="build-a")
    a.start()
    assert a_at_manifest.wait(10)
    # Same count, new order: build B's rows shift against build A's entries.
    os.rename(os.path.join(md, "b.md"), os.path.join(md, "z.md"))
    B.build_index(md, idx)
    b_done.set()
    a.join(10)
    assert _misaligned(idx) == []


def test_a_torn_legacy_index_heals_on_the_next_refresh(tmp_path, embed):
    md, idx = str(tmp_path / "memory"), str(tmp_path / "index")
    _write(md, _CORPUS)
    B.build_index(md, idx)
    _make_legacy(idx, roll=1)
    assert len(_misaligned(idx)) == 3  # the torn state the field corpus was in
    B.refresh_index(md, idx)
    assert _misaligned(idx) == []
    manifest = B._load_manifest(idx)
    assert manifest["dense_ready"] and manifest.get("dense_file")
    assert not os.path.exists(os.path.join(idx, D.LEGACY_DENSE_NAME))


def test_an_aligned_legacy_index_is_adopted_by_a_sample_not_a_re_embed(tmp_path, embed):
    md, idx = str(tmp_path / "memory"), str(tmp_path / "index")
    _write(md, {f"m{i:02d}.md": f"memory {i} topic{i} word{i}" for i in range(20)})
    B.build_index(md, idx)
    _make_legacy(idx, roll=0)
    embed.clear()
    B.refresh_index(md, idx)
    assert sum(embed) <= D.VERIFY_SAMPLE, embed  # spot check only, rows reused
    assert _misaligned(idx) == [] and B._load_manifest(idx).get("dense_file")


def test_a_legacy_manifest_still_serves_its_matrix(tmp_path, embed):
    """A session still running the previous plugin writes the unbound shape; it loads."""
    md, idx = str(tmp_path / "memory"), str(tmp_path / "index")
    _write(md, _CORPUS)
    B.build_index(md, idx)
    _make_legacy(idx, roll=0)
    loaded = B.load_index(idx)
    assert loaded.dense_ready and loaded.dense is not None


def test_a_dense_file_name_outside_the_bound_shape_is_not_followed(tmp_path, embed):
    md, idx = str(tmp_path / "memory"), str(tmp_path / "index")
    _write(md, _CORPUS)
    B.build_index(md, idx)
    path = os.path.join(idx, B._MANIFEST_NAME)
    with open(path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    for bad in ("../dense.npy", "/tmp/dense.0123456789abcdef.npy", "dense.ZZ.npy"):
        manifest["dense_file"] = bad
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
        loaded = B.load_index(idx)
        assert loaded.dense is None and not loaded.dense_ready, bad


def test_retired_matrices_are_collected_after_the_grace(tmp_path, embed):
    md, idx = str(tmp_path / "memory"), str(tmp_path / "index")
    _write(md, _CORPUS)
    B.build_index(md, idx)
    first = B._load_manifest(idx)["dense_file"]
    stale = os.path.join(idx, first)
    old = time.time() - D.RETIRE_GRACE_SECS - 5
    os.utime(stale, (old, old))
    _write(md, {"e.md": "a new memory arrives"})
    B.build_index(md, idx)
    live = B._load_manifest(idx)["dense_file"]
    assert live != first and os.path.exists(os.path.join(idx, live))
    assert not os.path.exists(stale)


def test_doctor_names_a_misaligned_index(tmp_path, embed):
    md, idx = str(tmp_path / "memory"), str(tmp_path / "index")
    _write(md, _CORPUS)
    B.build_index(md, idx)
    assert B.check_index_alignment(idx) is None
    _make_legacy(idx, roll=1)
    msg = B.check_index_alignment(idx)
    assert msg and "another memory's vector" in msg and "--force" in msg


def test_doctor_index_check_fails_on_a_torn_pair(tmp_path, embed, monkeypatch):
    from memory.doctor import DoctorContext
    from memory.doctor_checks_env import check_index_corruption

    md = str(tmp_path / "memory")
    _write(md, _CORPUS)
    idx = B.default_index_dir(md)
    B.build_index(md, idx)
    ctx = DoctorContext(memory_dir=md, repo_root=str(tmp_path))
    assert check_index_corruption(ctx)["status"] == "ok"
    _make_legacy(idx, roll=1)
    r = check_index_corruption(ctx)
    assert r["status"] == "fail" and "misaligned" in r["message"], r


# --------------------------------------------------------------------------- #
# HCR-9: a manifest says what its entry rows embed, and rows are reused only on that key
# --------------------------------------------------------------------------- #
_BODY = "The long form of the note, with enough words in its one paragraph to be indexed as a body chunk of its own."


def _write_long(md: str) -> None:
    os.makedirs(md, exist_ok=True)
    for fname, desc in _CORPUS.items():
        with open(os.path.join(md, fname), "w", encoding="utf-8") as fh:
            fh.write(_mem(fname[:-3], desc).replace("body text", f"{desc}. {_BODY}"))


def test_row_text_is_per_model_and_survives_an_mrl_suffix(monkeypatch):
    assert D.row_text("Qwen/Qwen3-Embedding-4B") == D.ROW_WHOLE
    assert D.row_text("Qwen/Qwen3-Embedding-4B@1024") == D.ROW_WHOLE
    assert D.row_text(None) == D.ROW_DESCRIPTION and D.row_text("some/other-model") == D.ROW_DESCRIPTION
    assert D.row_hash("x") != B._hash("x")  # never the doc_text hash of the same characters


def test_a_mode_change_re_embeds_the_entry_rows_and_keeps_the_chunk_rows(tmp_path, embed, monkeypatch):
    md, idx = str(tmp_path / "memory"), str(tmp_path / "index")
    _write_long(md)
    first = B.build_index(md, idx)
    assert "row_text" not in first and len(first["body_chunks"]) == 3
    assert sum(embed) == 6
    del embed[:]
    monkeypatch.setattr(D, "WHOLE_ROW_MODELS", frozenset({B.DEFAULT_MODEL}))
    second = B.build_index(md, idx)
    assert second["row_text"] == "whole" and sum(embed) == 3  # the three entries; chunk rows reused
    loaded = B.load_index(idx)
    for e in loaded.entries:  # each entry row is its whole file's vector now
        whole = open(os.path.join(md, e["file"]), encoding="utf-8").read()
        assert float(loaded.dense[e["row"]] @ _vec(whole)) > 0.99
    del embed[:]
    B.build_index(md, idx)
    assert embed == []  # and is reused on the whole-file key from here on
    monkeypatch.setattr(D, "WHOLE_ROW_MODELS", frozenset())
    third = B.build_index(md, idx)
    assert "row_text" not in third and sum(embed) == 3  # back: description rows are embedded again


def test_alignment_is_checked_on_chunk_rows_when_entry_rows_embed_whole_files(tmp_path, embed, monkeypatch):
    md, idx = str(tmp_path / "memory"), str(tmp_path / "index")
    _write_long(md)
    monkeypatch.setattr(D, "WHOLE_ROW_MODELS", frozenset({B.DEFAULT_MODEL}))
    manifest = B.build_index(md, idx)
    # the entry rows are not doc_text vectors, and that is not a torn index
    assert B.check_index_alignment(idx) is None
    dense = np.load(os.path.join(idx, manifest["dense_file"]))
    assert D.prior_rows_aligned(manifest, dense, lambda t: np.vstack([_vec(x) for x in t])) is True
    torn = np.roll(dense, 1, axis=0)
    assert D.prior_rows_aligned(manifest, torn, lambda t: np.vstack([_vec(x) for x in t])) is False
