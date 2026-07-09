"""Tests for memory/build_index.py — incremental hybrid index build.

Hermetic: every test writes a throwaway memory dir under tmp_path and points the builder
at a tmp index dir. The dense path is exercised with a deterministic FAKE embedder (no
fastembed, no network); a single importorskip test covers the real backend when present.
"""

from __future__ import annotations

import os
import sys
import threading
import time

import numpy as np
import pytest

from memory import build_index as B


def _mem(name: str, description: str, body: str = "body text") -> str:
    return f'---\nname: {name}\ndescription: "{description}"\ntype: project\n---\n{body}\n'


def _write_corpus(memory_dir: str, items: dict) -> None:
    os.makedirs(memory_dir, exist_ok=True)
    for fname, desc in items.items():
        with open(os.path.join(memory_dir, fname), "w", encoding="utf-8") as fh:
            fh.write(_mem(fname[:-3], desc))


def _fake_embedder(dim: int = 16):
    """Deterministic bag-of-token-hash embedder — no fastembed, no network."""

    def _vec(text: str):
        v = np.zeros(dim, dtype="float32")
        for tok in B.tokenize(text):
            v[hash(tok) % dim] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_documents(texts, allow_download=True):
        return np.vstack([_vec(t) for t in texts]).astype("float32")

    return embed_documents


# --------------------------------------------------------------------------- #
# BM25-only build (no dense)
# --------------------------------------------------------------------------- #
def test_build_bm25_only_when_dense_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "voyage reranker cross encoder", "b.md": "budget timeout phase envelope"})

    manifest = B.build_index(md, idx)
    assert manifest["dense_ready"] is False
    assert manifest["count"] == 2
    assert manifest["model"] is None
    # BM25 inputs persisted (tokens per entry), no dense file written.
    assert all(e["tokens"] for e in manifest["entries"])
    assert not os.path.exists(os.path.join(idx, "dense.npy"))
    assert os.path.exists(os.path.join(idx, "manifest.json"))


def test_degrade_to_bm25_when_model_unavailable(tmp_path, monkeypatch):
    # fastembed "present" but model construction blows up (e.g. offline cache miss).
    def boom(*a, **k):
        raise RuntimeError("no cached model")

    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", boom)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha", "b.md": "beta"})

    manifest = B.build_index(md, idx)  # must NOT raise
    assert manifest["dense_ready"] is False and manifest["count"] == 2


# --------------------------------------------------------------------------- #
# OSP-4: pure-stat cold-cache pre-check (no fastembed import, no SIGALRM needed)
# --------------------------------------------------------------------------- #
def test_fastembed_model_cached_false_on_empty_dir(tmp_path):
    assert B._fastembed_model_cached(str(tmp_path / "empty-cache")) is False


def test_fastembed_model_cached_true_when_onnx_present(tmp_path):
    cache = str(tmp_path / "cache")
    snapshot = os.path.join(
        cache, "models--qdrant--bge-small-en-v1.5-onnx-q", "snapshots", "abc123"
    )
    os.makedirs(snapshot)
    open(os.path.join(snapshot, "model_optimized.onnx"), "w").close()
    assert B._fastembed_model_cached(cache) is True


def test_cold_cache_degrades_to_bm25_fast_with_no_fastembed_import(tmp_path, monkeypatch):
    """OSP-4 acceptance: with NO fastembed model cache present, the dense path bails in
    well under the hook's timeout budget WITHOUT importing fastembed at all -- a pure stat,
    not a bounded-but-attempted load. Numpy/yaml/rank_bm25 are pre-warmed (their FIRST-ever
    import in a process is ~1-2s, unrelated to this item) so the timing isolates the cost of
    OSP-4's own pre-check rather than incidental interpreter warm-up."""
    import rank_bm25  # noqa: F401
    import yaml  # noqa: F401

    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    empty_cache = str(tmp_path / "empty-fastembed-cache")
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", empty_cache)
    sys.modules.pop("fastembed", None)

    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha", "b.md": "beta"})
    B.build_index(md, idx, allow_download=False)  # warm-up call (imports, filesystem caches)

    t0 = time.monotonic()
    manifest = B.build_index(md, idx, allow_download=False, force=True)
    elapsed = time.monotonic() - t0

    assert manifest["dense_ready"] is False
    assert elapsed < 0.1  # generous CI-safe bound; a real fastembed import alone is ~0.5s+
    assert "fastembed" not in sys.modules


# --------------------------------------------------------------------------- #
# RCL-5: the cross-encoder's OWN pure-stat cold-cache pre-check, mirroring OSP-4 exactly
# --------------------------------------------------------------------------- #
def test_cross_encoder_cached_false_on_empty_dir(tmp_path):
    assert B._cross_encoder_cached(str(tmp_path / "empty-cache")) is False


def test_cross_encoder_cached_true_when_onnx_present(tmp_path):
    cache = str(tmp_path / "cache")
    snapshot = os.path.join(cache, "models--Xenova--ms-marco-MiniLM-L-6-v2", "snapshots", "abc123")
    os.makedirs(snapshot)
    open(os.path.join(snapshot, "model.onnx"), "w").close()
    assert B._cross_encoder_cached(cache) is True


def test_get_cross_encoder_offline_raises_in_microseconds_on_cold_cache(tmp_path, monkeypatch):
    """The load-bearing reason for the pre-check: fastembed's OWN model loader wraps a cache
    MISS in a retry-with-backoff sleep loop regardless of why the load failed (confirmed
    empirically -- HF_HUB_OFFLINE=1 correctly blocks the network reach but does nothing to
    skip fastembed's retry wrapper, ~40s of sleep-and-retry). This pre-check must raise
    BEFORE fastembed is ever imported, so a cold cache degrades in microseconds instead."""
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "empty-cross-encoder-cache"))
    B._CROSS_ENCODER_CACHE.clear()
    sys.modules.pop("fastembed", None)

    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="not cached offline"):
        B._get_cross_encoder(allow_download=False)
    elapsed = time.monotonic() - t0

    assert elapsed < 0.1  # microseconds in practice; nowhere near fastembed's ~40s retry loop
    assert "fastembed" not in sys.modules


def test_get_cross_encoder_caches_by_allow_download_key(monkeypatch):
    """Same (model, allow_download) key -> the SAME cached instance; a different
    allow_download value is a distinct cache slot (mirrors _get_model's key shape)."""
    sentinel_offline = object()
    sentinel_online = object()
    B._CROSS_ENCODER_CACHE.clear()
    B._CROSS_ENCODER_CACHE[(B._CROSS_ENCODER_MODEL, False)] = sentinel_offline
    B._CROSS_ENCODER_CACHE[(B._CROSS_ENCODER_MODEL, True)] = sentinel_online
    assert B._get_cross_encoder(allow_download=False) is sentinel_offline
    assert B._get_cross_encoder(allow_download=True) is sentinel_online
    B._CROSS_ENCODER_CACHE.clear()


# --------------------------------------------------------------------------- #
# OSP-4: run_bounded holds off the main thread (SIGALRM never worked there)
# --------------------------------------------------------------------------- #
def test_run_bounded_raises_dense_timeout_from_worker_thread():
    result = {}

    def _target():
        try:
            B.run_bounded(lambda: time.sleep(2.0), 0.1)
            result["outcome"] = "no-timeout"
        except B.DenseTimeout:
            result["outcome"] = "timeout"
        except Exception as exc:  # pragma: no cover - would indicate a real bug
            result["outcome"] = f"error: {exc!r}"

    t = threading.Thread(target=_target)
    t.start()
    t.join(timeout=5.0)

    assert not t.is_alive()
    assert result.get("outcome") == "timeout"


def test_run_bounded_returns_value_from_worker_thread():
    result = {}

    def _target():
        result["value"] = B.run_bounded(lambda: 42, 5.0)

    t = threading.Thread(target=_target)
    t.start()
    t.join(timeout=5.0)

    assert not t.is_alive()
    assert result.get("value") == 42


# --------------------------------------------------------------------------- #
# Dense build with a fake embedder (deterministic, offline)
# --------------------------------------------------------------------------- #
def test_dense_build_writes_matrix_and_rows(tmp_path, monkeypatch):
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", _fake_embedder(16))
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha one", "b.md": "beta two", "c.md": "gamma three"})

    manifest = B.build_index(md, idx)
    assert manifest["dense_ready"] is True
    assert manifest["dim"] == 16 and manifest["count"] == 3
    dense = np.load(os.path.join(idx, "dense.npy"))
    assert dense.shape == (3, 16)
    # rows are L2-normalized
    norms = np.linalg.norm(dense, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)
    assert [e["row"] for e in manifest["entries"]] == [0, 1, 2]


# --------------------------------------------------------------------------- #
# COR-12: atomic manifest/dense writes (no torn reads, no tmp litter)
# --------------------------------------------------------------------------- #
def test_no_stray_tmp_files_after_build(tmp_path, monkeypatch):
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", _fake_embedder(16))
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha one", "b.md": "beta two"})

    B.build_index(md, idx)
    leftovers = [f for f in os.listdir(idx) if f.endswith(".tmp") or f.endswith(".tmp.npy")]
    assert leftovers == []

    # Also true for a BM25-only (no-dense) build and a switch-back-to-dense rebuild.
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    B.build_index(md, idx)
    leftovers = [f for f in os.listdir(idx) if f.endswith(".tmp") or f.endswith(".tmp.npy")]
    assert leftovers == []


def test_dense_replace_happens_before_manifest_replace(tmp_path, monkeypatch):
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", _fake_embedder(16))
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha one", "b.md": "beta two"})

    replaced = []
    real_replace = os.replace

    def spy_replace(src, dst):
        replaced.append(dst)
        return real_replace(src, dst)

    monkeypatch.setattr(B.os, "replace", spy_replace)
    B.build_index(md, idx)

    dense_path = os.path.join(idx, "dense.npy")
    manifest_path = os.path.join(idx, "manifest.json")
    assert dense_path in replaced and manifest_path in replaced
    assert replaced.index(dense_path) < replaced.index(manifest_path)


def test_manifest_never_visible_with_missing_or_stale_dense(tmp_path, monkeypatch):
    # A reader that observes the manifest mid-build (simulated by inspecting os.replace call
    # order + on-disk state right after build_index returns) must never see dense_ready=true
    # with dense.npy absent or shaped differently than what the manifest expects.
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", _fake_embedder(16))
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha one", "b.md": "beta two", "c.md": "gamma three"})

    manifest = B.build_index(md, idx)
    assert manifest["dense_ready"] is True
    dense_path = os.path.join(idx, "dense.npy")
    assert os.path.exists(dense_path)
    dense = np.load(dense_path)
    assert dense.shape == (manifest["count"], manifest["dim"])


def test_incremental_rebuild_only_embeds_changed(tmp_path, monkeypatch):
    embedded_batches = []

    base = _fake_embedder(16)

    def counting_embed(texts, allow_download=True):
        embedded_batches.append(list(texts))
        return base(texts)

    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", counting_embed)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha", "b.md": "beta", "c.md": "gamma"})

    B.build_index(md, idx)
    assert len(embedded_batches[0]) == 3  # first build embeds everything

    # Edit ONE memory's description -> only it should re-embed.
    embedded_batches.clear()
    with open(os.path.join(md, "b.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem("b", "beta CHANGED now"))
    B.build_index(md, idx)
    assert len(embedded_batches) == 1
    assert len(embedded_batches[0]) == 1  # exactly the changed doc
    assert "changed" in B.tokenize(embedded_batches[0][0])  # the re-embedded doc is the edited one


def test_rebuild_with_no_changes_reembeds_nothing(tmp_path, monkeypatch):
    # 100% cache-hit path: every entry's hash is unchanged -> embed_documents not called.
    embedded_batches = []
    base = _fake_embedder(16)

    def counting_embed(texts, allow_download=True):
        embedded_batches.append(list(texts))
        return base(texts)

    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", counting_embed)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha", "b.md": "beta"})
    B.build_index(md, idx)
    embedded_batches.clear()
    m2 = B.build_index(md, idx)  # no changes
    assert embedded_batches == []  # nothing re-embedded
    assert m2["dense_ready"] is True and m2["count"] == 2  # index still complete + valid


# --------------------------------------------------------------------------- #
# invalid_after ingestion (Tier 3, soft-invalidation) — metadata refresh decoupled from re-embed
# --------------------------------------------------------------------------- #
def test_compute_corpus_invalid_after_absent_by_default(tmp_path):
    md = str(tmp_path / "memory")
    _write_corpus(md, {"a.md": "alpha"})
    entries = B.compute_corpus(md)
    assert entries[0]["invalid_after"] is None


def test_compute_corpus_reads_invalid_after_top_level(tmp_path):
    md = str(tmp_path / "memory")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: a\ndescription: "alpha"\ninvalid_after: "2026-01-01T00:00:00+00:00"\n---\nbody\n')
    entries = B.compute_corpus(md)
    assert entries[0]["invalid_after"] == "2026-01-01T00:00:00+00:00"


def test_compute_corpus_reads_invalid_after_nested_under_metadata(tmp_path):
    """The load-bearing fix: cited_paths/source_commit nest under metadata: in this corpus's
    established convention -- invalid_after must be readable there too, or the field is
    PERMANENTLY inert the moment staleness.set_invalid_after follows that same convention."""
    md = str(tmp_path / "memory")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write(
            '---\nname: a\ndescription: "alpha"\nmetadata:\n'
            '  invalid_after: "2026-01-01T00:00:00+00:00"\n---\nbody\n'
        )
    entries = B.compute_corpus(md)
    assert entries[0]["invalid_after"] == "2026-01-01T00:00:00+00:00"


def test_compute_corpus_coerces_unquoted_yaml_date(tmp_path):
    """yaml.safe_load auto-types an UNQUOTED invalid_after (the natural hand-authored form)
    into a native datetime.date -- it must be coerced to an ISO string, not silently
    dropped, and must never reach json.dump as a non-serializable object."""
    md = str(tmp_path / "memory")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: a\ndescription: \"alpha\"\ninvalid_after: 2026-06-01\n---\nbody\n")
    entries = B.compute_corpus(md)
    assert entries[0]["invalid_after"] == "2026-06-01"  # date.isoformat()


def test_build_index_does_not_crash_on_unquoted_yaml_date(tmp_path, monkeypatch):
    """End-to-end: the exact crash scenario the coercion fix prevents -- json.dump(manifest)
    on a raw datetime.date would raise TypeError without it."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: a\ndescription: \"alpha\"\ninvalid_after: 2026-06-01\n---\nbody\n")
    manifest = B.build_index(md, idx)  # must NOT raise
    assert manifest["entries"][0]["invalid_after"] == "2026-06-01"
    # the manifest really did serialize to disk (json.dump succeeded)
    assert os.path.exists(os.path.join(idx, "manifest.json"))


def test_invalid_after_refreshed_on_embedding_cache_hit(tmp_path, monkeypatch):
    """The metadata-refresh-decoupled-from-re-embed guarantee: changing ONLY invalid_after
    (not the description) must NOT trigger a re-embed, yet the new value must still land in
    the manifest on the next build -- because compute_corpus re-reads every file from disk
    on every call; only the embedding ROW is cache-reused, keyed by hash(doc_text), and
    doc_text is name+description only (invalid_after never enters the hash)."""
    embedded_batches = []
    base = _fake_embedder(16)

    def counting_embed(texts, allow_download=True):
        embedded_batches.append(list(texts))
        return base(texts)

    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", counting_embed)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha", "b.md": "beta"})
    m1 = B.build_index(md, idx)
    assert m1["entries"][1]["invalid_after"] is None  # "b" entry, no invalid_after yet

    embedded_batches.clear()
    # Add invalid_after to b.md WITHOUT touching its description/doc_text.
    with open(os.path.join(md, "b.md"), "w", encoding="utf-8") as fh:
        fh.write(
            '---\nname: b\ndescription: "beta"\ninvalid_after: "2026-01-01T00:00:00+00:00"\n---\nbody text\n'
        )
    m2 = B.build_index(md, idx)

    assert embedded_batches == []  # zero re-embed calls -- 100% cache-hit on the row
    b_entry = next(e for e in m2["entries"] if e["name"] == "b")
    assert b_entry["invalid_after"] == "2026-01-01T00:00:00+00:00"  # metadata DID refresh
    a_entry = next(e for e in m2["entries"] if e["name"] == "a")
    assert a_entry["invalid_after"] is None  # untouched entry stays untouched


def test_load_index_tolerates_entries_missing_invalid_after(tmp_path):
    """A CURRENT-schema manifest whose entries carry no invalid_after key must load fine --
    entry.get("invalid_after") degrades to None via dict .get(), no KeyError. (Pre-COR-7
    this test used a schema_version-1 manifest to model "older index"; a genuinely older
    manifest no longer loads AT ALL -- the schema gate treats it as absent and the next
    refresh rebuilds -- so the missing-key tolerance is pinned on a current-version shape.)"""
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    os.makedirs(idx, exist_ok=True)
    import json as _json

    old_manifest = {
        "schema_version": B.SCHEMA_VERSION,
        "model": None,
        "dense_ready": False,
        "dim": None,
        "count": 1,
        "entries": [{"name": "a", "file": "a.md", "doc_text": "a. alpha", "description": "alpha", "hash": "x", "tokens": ["a"]}],
    }
    with open(os.path.join(idx, "manifest.json"), "w", encoding="utf-8") as fh:
        _json.dump(old_manifest, fh)
    loaded = B.load_index(idx)
    assert loaded is not None
    assert loaded.entries[0].get("invalid_after") is None  # no KeyError, degrades cleanly


def test_force_reembeds_everything(tmp_path, monkeypatch):
    embedded_batches = []
    base = _fake_embedder(16)

    def counting_embed(texts, allow_download=True):
        embedded_batches.append(list(texts))
        return base(texts)

    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", counting_embed)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha", "b.md": "beta"})
    B.build_index(md, idx)
    embedded_batches.clear()
    B.build_index(md, idx, force=True)
    assert len(embedded_batches[0]) == 2  # force ignores the cache


# --------------------------------------------------------------------------- #
# Index location + load + stale-dense cleanup
# --------------------------------------------------------------------------- #
def test_default_index_dir_is_gitignored_sibling(tmp_path):
    md = str(tmp_path / ".claude" / "memory")
    got = B.default_index_dir(md)
    assert got.endswith(os.path.join(".claude", ".memory-index"))


def test_switching_to_bm25_removes_stale_dense_file(tmp_path, monkeypatch):
    # Explicit delenv: the CI hermetic lane exports HIPPO_DISABLE_DENSE=1 job-wide,
    # and this test's FIRST build must be a dense one for the scenario to exist.
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha", "b.md": "beta"})
    monkeypatch.setattr(B, "embed_documents", _fake_embedder(16))
    B.build_index(md, idx)
    assert os.path.exists(os.path.join(idx, "dense.npy"))
    # Rebuild with dense disabled -> the stale dense.npy must be removed.
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    m2 = B.build_index(md, idx)
    assert m2["dense_ready"] is False
    assert not os.path.exists(os.path.join(idx, "dense.npy"))


def test_allow_download_false_threads_to_embedder(tmp_path, monkeypatch):
    seen = {}

    def recording_embed(texts, allow_download=True):
        seen["allow_download"] = allow_download
        return _fake_embedder(16)(texts)

    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", recording_embed)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha"})
    B.build_index(md, idx, allow_download=False)
    assert seen["allow_download"] is False  # offline embed path used


# --------------------------------------------------------------------------- #
# refresh_index — the SessionStart incremental, offline, never-downgrade rebuild
# --------------------------------------------------------------------------- #
def test_refresh_index_picks_up_new_memory(tmp_path, monkeypatch):
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", _fake_embedder(16))
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha"})
    B.build_index(md, idx)
    assert B.load_index(idx).manifest["count"] == 1

    # A memory written AFTER the build is indexed by the refresh.
    with open(os.path.join(md, "b.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem("b", "beta brand new"))
    m = B.refresh_index(md, idx)
    assert m["count"] == 2 and m["dense_ready"] is True
    names = {e["name"] for e in B.load_index(idx).entries}
    assert "b" in names


def test_refresh_index_noop_when_unchanged(tmp_path, monkeypatch):
    embedded = []

    def counting(texts, allow_download=True):
        embedded.append(list(texts))
        return _fake_embedder(16)(texts)

    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", counting)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha", "b.md": "beta"})
    B.build_index(md, idx)
    mtime_before = os.path.getmtime(os.path.join(idx, "manifest.json"))
    embedded.clear()

    B.refresh_index(md, idx)  # corpus unchanged
    assert embedded == []  # no embedding
    assert os.path.getmtime(os.path.join(idx, "manifest.json")) == mtime_before  # no rewrite


def test_refresh_preserves_dense_when_offline_embed_fails(tmp_path, monkeypatch):
    # Build a complete dense index, then add a memory but make the offline embed FAIL
    # (simulates a cold/wiped cache). The refresh must NOT downgrade the index to BM25-only.
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", _fake_embedder(16))
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha", "b.md": "beta"})
    B.build_index(md, idx)
    dense_before = np.load(os.path.join(idx, "dense.npy")).copy()

    with open(os.path.join(md, "c.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem("c", "gamma needs embedding but cache is cold"))

    def boom(texts, allow_download=True):
        raise RuntimeError("offline cache miss")

    monkeypatch.setattr(B, "embed_documents", boom)
    m = B.refresh_index(md, idx)
    # Never-worse: the old complete dense index is preserved, the new memory NOT added.
    assert m["dense_ready"] is True and m["count"] == 2
    loaded = B.load_index(idx)
    assert loaded.dense_ready is True and len(loaded) == 2
    assert np.array_equal(np.load(os.path.join(idx, "dense.npy")), dense_before)


def test_refresh_index_missing_dir_is_none(tmp_path):
    assert B.refresh_index(str(tmp_path / "nope"), str(tmp_path / "idx")) is None


def test_refresh_index_picks_up_metadata_only_invalid_after_change(tmp_path, monkeypatch):
    """LIF-1: invalid_after never perturbs a doc_text/body hash, so the unchanged-corpus
    short-circuit must compare it explicitly — otherwise a soft-invalidation (set by
    demote's chain or --invalidate, or STRIPPED by a genuine reverify) would be starved
    out of the index forever on an otherwise-quiet corpus, and recall's pre-cut penalty
    would never see the one field it acts on."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha", "b.md": "beta"})
    B.build_index(md, idx)
    assert all(e.get("invalid_after") is None for e in B.load_index(idx).entries)

    from memory.staleness import set_invalid_after

    set_invalid_after(os.path.join(md, "a.md"), "2026-01-01T00:00:00+00:00")
    B.refresh_index(md, idx)
    by_name = {e["name"]: e.get("invalid_after") for e in B.load_index(idx).entries}
    assert by_name["a"] == "2026-01-01T00:00:00+00:00"  # set -> propagated

    # the reverse direction too: clearing the window must not be no-op'd away either
    text = open(os.path.join(md, "a.md"), encoding="utf-8").read()
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(ln for ln in text.split("\n") if not ln.startswith("invalid_after")))
    B.refresh_index(md, idx)
    assert all(e.get("invalid_after") is None for e in B.load_index(idx).entries)


# --------------------------------------------------------------------------- #
# GOV-2: steer ingestion — the manifest-level half of steer:pin. Mirrors the
# invalid_after family above exactly: same top-level/nested-under-metadata read
# contract (_extract_steer), same "metadata never perturbs doc_text hash"
# starvation-proofing in refresh_index. Plus the closed-enum guard: an unknown
# steer value reads as None (unsteered), never a passthrough ranking knob.
# --------------------------------------------------------------------------- #
def test_compute_corpus_steer_absent_by_default(tmp_path):
    md = str(tmp_path / "memory")
    _write_corpus(md, {"a.md": "alpha"})
    assert B.compute_corpus(md)[0]["steer"] is None


def test_compute_corpus_reads_steer_top_level_and_nested(tmp_path):
    md = str(tmp_path / "memory")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: a\ndescription: "alpha"\nsteer: pin\n---\nbody\n')
    with open(os.path.join(md, "b.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: b\ndescription: "beta"\nmetadata:\n  steer: pin\n---\nbody\n')
    by_name = {e["name"]: e["steer"] for e in B.compute_corpus(md)}
    assert by_name == {"a": "pin", "b": "pin"}


def test_compute_corpus_steer_is_a_closed_enum(tmp_path):
    """A junk/unknown steer value reads as None (unsteered, fail-open) — a typo can never
    become an accidental ranking knob, and no user-supplied float ever reaches recall."""
    md = str(tmp_path / "memory")
    os.makedirs(md, exist_ok=True)
    for name, val in (("a", "mute"), ("b", "2.5"), ("c", "[pin]"), ("d", '"PIN "')):
        with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
            fh.write(f'---\nname: {name}\ndescription: "d"\nsteer: {val}\n---\nbody\n')
    by_name = {e["name"]: e["steer"] for e in B.compute_corpus(md)}
    # "PIN " normalizes (case/whitespace) to the shipped mode; everything else drops.
    assert by_name == {"a": None, "b": None, "c": None, "d": "pin"}


def test_build_index_persists_steer_into_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: a\ndescription: "alpha"\nsteer: pin\n---\nbody\n')
    manifest = B.build_index(md, idx)
    assert manifest["entries"][0]["steer"] == "pin"
    assert manifest["schema_version"] == B.SCHEMA_VERSION


def test_refresh_index_picks_up_metadata_only_steer_change(tmp_path, monkeypatch):
    """GOV-2: steer never perturbs a doc_text/body hash, so the unchanged-corpus
    short-circuit must compare it explicitly — otherwise pinning (or UNpinning) a memory
    on an otherwise-quiet corpus would be starved out of the index forever and the boost
    would never engage (or never release)."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha", "b.md": "beta"})
    B.build_index(md, idx)
    assert all(e.get("steer") is None for e in B.load_index(idx).entries)

    # Pin a.md WITHOUT touching its description or body (metadata-only edit — neither the
    # doc_text hash nor any body-chunk hash moves, so ONLY the steer compare can rescue it).
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: a\ndescription: "alpha"\ntype: project\nsteer: pin\n---\nbody text\n')
    B.refresh_index(md, idx)
    by_name = {e["name"]: e.get("steer") for e in B.load_index(idx).entries}
    assert by_name["a"] == "pin"  # pinned -> propagated

    # the reverse direction too: unpinning must not be no-op'd away either
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem("a", "alpha"))
    B.refresh_index(md, idx)
    assert all(e.get("steer") is None for e in B.load_index(idx).entries)


# --------------------------------------------------------------------------- #
# GOV-7: confidence ingestion — same both-schema read + starvation-proofing family
# as invalid_after/source_commit_time/steer above. Display-only downstream.
# --------------------------------------------------------------------------- #
def test_compute_corpus_confidence_absent_by_default(tmp_path):
    md = str(tmp_path / "memory")
    _write_corpus(md, {"a.md": "alpha"})
    assert B.compute_corpus(md)[0]["confidence"] is None


def test_compute_corpus_reads_confidence_top_level_and_nested(tmp_path):
    md = str(tmp_path / "memory")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: a\ndescription: "alpha"\nconfidence: draft\n---\nbody\n')
    with open(os.path.join(md, "b.md"), "w", encoding="utf-8") as fh:
        fh.write(
            '---\nname: b\ndescription: "beta"\nmetadata:\n  confidence: authoritative\n---\nbody\n'
        )
    by_name = {e["name"]: e["confidence"] for e in B.compute_corpus(md)}
    assert by_name == {"a": "draft", "b": "authoritative"}


def test_compute_corpus_confidence_is_a_closed_enum(tmp_path):
    """An unknown tier reads as unset (today's default) — never a passthrough label."""
    md = str(tmp_path / "memory")
    os.makedirs(md, exist_ok=True)
    for name, val in (("a", "canon"), ("b", "0.9"), ("c", '"  VERIFIED "')):
        with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
            fh.write(f'---\nname: {name}\ndescription: "d"\nconfidence: {val}\n---\nbody\n')
    by_name = {e["name"]: e["confidence"] for e in B.compute_corpus(md)}
    assert by_name == {"a": None, "b": None, "c": "verified"}


def test_refresh_index_picks_up_metadata_only_confidence_change(tmp_path, monkeypatch):
    """An author re-grading draft→(unset) on a quiet corpus must reach the manifest —
    confidence never perturbs a doc_text/body hash, so only the explicit compare can."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha", "b.md": "beta"})
    B.build_index(md, idx)
    assert all(e.get("confidence") is None for e in B.load_index(idx).entries)

    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: a\ndescription: "alpha"\ntype: project\nconfidence: draft\n---\nbody text\n')
    B.refresh_index(md, idx)
    by_name = {e["name"]: e.get("confidence") for e in B.load_index(idx).entries}
    assert by_name["a"] == "draft"

    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem("a", "alpha"))
    B.refresh_index(md, idx)
    assert all(e.get("confidence") is None for e in B.load_index(idx).entries)


# --------------------------------------------------------------------------- #
# RET-5: source_commit_time ingestion — the manifest-level half of the recency prior.
# Mirrors the invalid_after tests above exactly: same top-level/nested-under-metadata
# read contract (staleness.read_source_commit_time), same "metadata never perturbs
# doc_text hash" starvation-proofing in refresh_index.
# --------------------------------------------------------------------------- #
def test_compute_corpus_source_commit_time_absent_by_default(tmp_path):
    md = str(tmp_path / "memory")
    _write_corpus(md, {"a.md": "alpha"})
    entries = B.compute_corpus(md)
    assert entries[0]["source_commit_time"] is None


def test_compute_corpus_reads_source_commit_time_top_level(tmp_path):
    md = str(tmp_path / "memory")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: a\ndescription: "alpha"\nsource_commit_time: 1750000000\n---\nbody\n')
    entries = B.compute_corpus(md)
    assert entries[0]["source_commit_time"] == 1750000000


def test_compute_corpus_reads_source_commit_time_nested_under_metadata(tmp_path):
    md = str(tmp_path / "memory")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write(
            '---\nname: a\ndescription: "alpha"\nmetadata:\n'
            "  source_commit_time: 1750000000\n---\nbody\n"
        )
    entries = B.compute_corpus(md)
    assert entries[0]["source_commit_time"] == 1750000000


def test_build_index_manifest_carries_source_commit_time(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: a\ndescription: "alpha"\nsource_commit_time: 1750000000\n---\nbody\n')
    manifest = B.build_index(md, idx)
    assert manifest["entries"][0]["source_commit_time"] == 1750000000
    assert B.load_index(idx).entries[0]["source_commit_time"] == 1750000000


def test_refresh_index_picks_up_metadata_only_source_commit_time_change(tmp_path, monkeypatch):
    """RET-5: source_commit_time never perturbs doc_text (name+description only), so the
    unchanged-corpus short-circuit must compare it explicitly — otherwise a reverify/
    backfill that only bumps this field would be starved out of the index forever on an
    otherwise-quiet corpus, and recall's optional recency prior would keep reading a stale
    baseline (or none at all)."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha", "b.md": "beta"})
    B.build_index(md, idx)
    assert all(e.get("source_commit_time") is None for e in B.load_index(idx).entries)

    text = open(os.path.join(md, "a.md"), encoding="utf-8").read()
    text = text.replace("---\n", "---\nsource_commit_time: 1750000000\n", 1)
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    B.refresh_index(md, idx)
    by_name = {e["name"]: e.get("source_commit_time") for e in B.load_index(idx).entries}
    assert by_name["a"] == 1750000000  # set -> propagated despite doc_text being unchanged
    assert by_name["b"] is None


# --------------------------------------------------------------------------- #
# COR-3: a degraded (BM25-only) index must upgrade to dense on the next refresh_index
# call once the corpus is unchanged but the model cache is warm again — the hash
# short-circuit must NOT ignore dense_ready.
# --------------------------------------------------------------------------- #
def test_refresh_index_upgrades_degraded_index_when_corpus_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha one", "b.md": "beta two"})
    m1 = B.build_index(md, idx)
    assert m1["dense_ready"] is False  # persisted degraded (dense disabled at build time)

    # The corpus is UNCHANGED across "sessions", but dense is now available (mocked/fast
    # embed path) -- the next refresh_index must upgrade the index in place.
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", _fake_embedder(16))
    m2 = B.refresh_index(md, idx)

    assert m2["dense_ready"] is True
    assert m2["count"] == 2
    loaded = B.load_index(idx)
    assert loaded.dense_ready is True and len(loaded) == 2


def test_refresh_index_still_noops_when_already_dense_and_unchanged(tmp_path, monkeypatch):
    # The short-circuit must still fire (no embedding call) when the index is ALREADY dense
    # and the corpus hasn't changed -- COR-3 must not regress the fast no-op path.
    embedded = []

    def counting(texts, allow_download=True):
        embedded.append(list(texts))
        return _fake_embedder(16)(texts)

    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", counting)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha", "b.md": "beta"})
    B.build_index(md, idx)
    embedded.clear()

    m = B.refresh_index(md, idx)
    assert embedded == []  # no embedding -- true no-op
    assert m["dense_ready"] is True


# --------------------------------------------------------------------------- #
# COR-3: chunked offline batch embed persists partial progress instead of
# all-or-nothing, so a large corpus converges to dense across sessions.
# --------------------------------------------------------------------------- #
def test_large_corpus_offline_embed_persists_partial_progress_across_sessions(tmp_path, monkeypatch):
    """Simulate a bounded budget that only allows a couple of chunks per session: assert
    partial rows persist after session 1, and session 2 continues from where it left off
    (already-embedded hashes are never re-submitted to embed_documents)."""
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "DENSE_EMBED_CHUNK_SIZE", 10)

    submitted_texts = []
    base = _fake_embedder(16)

    def counting_embed(texts, allow_download=False):
        submitted_texts.append(list(texts))
        return base(texts)

    monkeypatch.setattr(B, "embed_documents", counting_embed)

    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    items = {f"m{i:03d}.md": f"memory number {i} unique token m{i:03d}" for i in range(50)}
    _write_corpus(md, items)

    # Session 1: budget only covers ~2 chunks (20 of 50 docs) before time.monotonic()
    # reports the deadline has passed. Fake a clock that advances a lot per call so the
    # loop in build_index's offline branch stops early but has already embedded 2 chunks.
    real_monotonic = B.time.monotonic
    call_count = {"n": 0}

    def fake_monotonic():
        call_count["n"] += 1
        # First call establishes the deadline (t=0); subsequent calls (the per-slice
        # `remaining` checks) advance quickly so only 2 chunks fit before time is up.
        return real_monotonic() + call_count["n"] * 6.0

    monkeypatch.setattr(B.time, "monotonic", fake_monotonic)
    m1 = B.build_index(md, idx, allow_download=False, preserve_on_dense_fail=True)

    assert m1["dense_ready"] is False  # not everything embedded yet
    embedded_so_far = sum(1 for e in m1["entries"] if e["row"] is not None)
    assert 0 < embedded_so_far < 50  # partial progress, not all-or-nothing
    assert os.path.exists(os.path.join(idx, "dense.npy"))  # partial rows persisted to disk
    first_pass_submitted = sum(len(batch) for batch in submitted_texts)
    assert first_pass_submitted == embedded_so_far

    # Session 2: restore the real clock (full budget) -- the remaining docs finish, and the
    # already-embedded hashes from session 1 must NOT be resubmitted.
    monkeypatch.setattr(B.time, "monotonic", real_monotonic)
    submitted_texts.clear()
    m2 = B.build_index(md, idx, allow_download=False, preserve_on_dense_fail=True)

    assert m2["dense_ready"] is True
    assert all(e["row"] is not None for e in m2["entries"])
    resubmitted = sum(len(batch) for batch in submitted_texts)
    assert resubmitted == 50 - embedded_so_far  # only the remaining docs were embedded

    loaded = B.load_index(idx)
    assert loaded.dense_ready is True and len(loaded) == 50


def test_offline_embed_chunk_timeout_keeps_partial_progress(tmp_path, monkeypatch):
    """A slice that itself blows the (per-slice) wall-clock bound raises DenseTimeout inside
    run_bounded -- build_index must catch it, stop starting new slices, and still persist
    whatever prior slices completed rather than losing all progress."""
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "DENSE_EMBED_CHUNK_SIZE", 5)
    monkeypatch.setattr(B, "DENSE_REFRESH_TIMEOUT_SECS", 15.0)

    base = _fake_embedder(16)
    calls = {"n": 0}

    def flaky_embed(texts, allow_download=False):
        calls["n"] += 1
        if calls["n"] == 2:  # second slice "hangs" past its bound
            raise B.DenseTimeout()
        return base(texts)

    monkeypatch.setattr(B, "embed_documents", flaky_embed)

    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    items = {f"m{i:02d}.md": f"memory number {i}" for i in range(15)}
    _write_corpus(md, items)

    m = B.build_index(md, idx, allow_download=False, preserve_on_dense_fail=True)
    assert m["dense_ready"] is False
    embedded_rows = [e for e in m["entries"] if e["row"] is not None]
    assert len(embedded_rows) == 5  # only the first slice (5 docs) landed
    assert os.path.exists(os.path.join(idx, "dense.npy"))


def test_load_index_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "voyage reranker", "b.md": "budget envelope"})
    B.build_index(md, idx)
    loaded = B.load_index(idx)
    assert loaded is not None and len(loaded) == 2
    assert loaded.dense_ready is False
    assert B.load_index(str(tmp_path / "nope")) is None


# --------------------------------------------------------------------------- #
# PRF-1: precomputed BM25 stats (postings/doc_len/avgdl/idf) -- build-time unit tests.
# recall.py's test suite covers the query-time fast path + golden equivalence + drift
# interaction; these pin the build-time computation and its manifest wiring in isolation.
# --------------------------------------------------------------------------- #
def test_compute_bm25_stats_matches_rank_bm25_fields():
    """postings/doc_len/avgdl/idf/k1/b computed here must agree with a fresh rank_bm25
    construction over the SAME corpus (field-by-field, not just downstream scores)."""
    rank_bm25 = pytest.importorskip("rank_bm25")
    corpus = [
        "alpha beta gamma".split(),
        "alpha alpha delta".split(),
        "epsilon zeta".split(),
    ]
    stats = B.compute_bm25_stats(corpus)
    oracle = rank_bm25.BM25Okapi(corpus)

    assert stats["doc_len"] == oracle.doc_len
    assert stats["avgdl"] == pytest.approx(oracle.avgdl)
    assert stats["k1"] == oracle.k1
    assert stats["b"] == oracle.b
    assert set(stats["idf"].keys()) == set(oracle.idf.keys())
    for tok, val in oracle.idf.items():
        assert stats["idf"][tok] == pytest.approx(val, abs=1e-9)

    # postings[tok] lists exactly the docs (and their TF) that contain tok -- must reconstruct
    # the SAME per-doc frequency dict rank_bm25's doc_freqs holds internally.
    for tok in oracle.idf:
        expected_tf_by_doc = {
            i: freqs[tok] for i, freqs in enumerate(oracle.doc_freqs) if tok in freqs
        }
        got_tf_by_doc = {doc_i: tf for doc_i, tf in stats["postings"].get(tok, [])}
        assert got_tf_by_doc == expected_tf_by_doc


def test_compute_bm25_stats_empty_corpus():
    stats = B.compute_bm25_stats([])
    assert stats["postings"] == {}
    assert stats["doc_len"] == []
    assert stats["avgdl"] == 0.0
    assert stats["idf"] == {}


def test_build_index_manifest_carries_bm25_block(tmp_path, monkeypatch):
    """build_index() must persist the "bm25" stats block, and it must survive the JSON
    manifest round-trip byte-for-byte (all keys JSON-safe: no sets/tuples/non-str keys)."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "voyage reranker fallback", "b.md": "budget envelope authority"})
    manifest = B.build_index(md, idx)
    assert "bm25" in manifest
    bm25 = manifest["bm25"]
    assert set(bm25.keys()) == {"postings", "doc_len", "avgdl", "idf", "k1", "b"}
    assert len(bm25["doc_len"]) == manifest["count"] == 2

    # Round-trip through the actual manifest.json file on disk (not just the in-memory dict).
    import json

    with open(os.path.join(idx, "manifest.json"), "r", encoding="utf-8") as fh:
        on_disk = json.load(fh)
    assert on_disk["bm25"] == bm25

    loaded = B.load_index(idx)
    assert loaded.manifest["bm25"] == bm25


# --------------------------------------------------------------------------- #
# Real fastembed backend (network-marked: can download the ~130MB model on a
# cold cache, so it is deselected by default via addopts `-m "not network"` —
# the suite's hermeticity claim must stay true offline with an empty home cache)
# --------------------------------------------------------------------------- #
@pytest.mark.network
def test_real_fastembed_dense_build(tmp_path, monkeypatch, tmp_path_factory):
    pytest.importorskip("fastembed")
    # Pin the model cache: honor a caller-provided FASTEMBED_CACHE_PATH (CI's dense
    # lane points this at the actions-restored cache) else a session-scoped tmp dir —
    # NEVER the user's real home cache.
    cache = os.environ.get("FASTEMBED_CACHE_PATH") or str(
        tmp_path_factory.getbasetemp() / "fastembed-cache"
    )
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", cache)
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(
        md,
        {
            "reranker.md": "voyage cross encoder reranking is primary, bm25 hybrid fallback",
            "budget.md": "phase envelope budget authority guards the synthesis tail",
            "excel.md": "excel header rescue uses llm inference for non-canonical columns",
        },
    )
    manifest = B.build_index(md, idx)
    assert manifest["dense_ready"] is True
    assert manifest["dim"] and manifest["dim"] > 0
    assert os.path.exists(os.path.join(idx, "dense.npy"))


# --------------------------------------------------------------------------- #
# SEC-3: the index dir is self-ignoring; derived state invisible to git
# --------------------------------------------------------------------------- #
def test_index_dir_drops_self_ignoring_gitignore(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha"})
    B.build_index(md, idx)
    gi = os.path.join(idx, ".gitignore")
    assert os.path.exists(gi) and open(gi, encoding="utf-8").read() == "*\n"


def test_derived_dirs_invisible_to_git_without_init(tmp_path, monkeypatch):
    """Both derived dirs must be invisible to `git status` in a repo whose .gitignore
    was never patched by init — the self-ignoring pattern removes that dependency."""
    import subprocess

    from memory import recall as R

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo = str(tmp_path / "repo")
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    with open(os.path.join(md, "m.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: m\ndescription: "zebra canary deploys"\n---\nbody\n')
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)
    monkeypatch.chdir(repo)
    R.main(["zebra", "canary", "deploys"])  # builds the index + writes telemetry

    assert os.path.isdir(os.path.join(repo, ".claude", ".memory-index"))
    assert os.path.isdir(os.path.join(repo, ".claude", ".memory-telemetry"))
    porcelain = subprocess.run(
        ["git", "status", "--porcelain", "-uall"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert ".memory-index" not in porcelain and ".memory-telemetry" not in porcelain
    assert ".claude/memory/m.md" in porcelain  # the CORPUS stays visible/committable


# --------------------------------------------------------------------------- #
# RET-3: Unicode-aware tokenize() — word tokens for Latin/Cyrillic, CJK bigrams
# --------------------------------------------------------------------------- #
def test_tokenize_accented_latin_survives_whole():
    """'café' must tokenize whole ('café'), never truncate to 'caf' (the old ASCII-only
    regex's failure mode — accents aren't in [a-z0-9] so the match stopped mid-word)."""
    assert B.tokenize("café") == ["café"]
    assert B.tokenize("Café RÉSUMÉ naïve") == ["café", "résumé", "naïve"]


def test_tokenize_cyrillic_is_not_empty():
    """The old ASCII-only tokenizer produced ZERO tokens for any non-Latin text -- a Russian
    memory/query would trip the min-content skip and silently never recall. Must not regress."""
    toks = B.tokenize("Привет как дела сегодня")
    assert toks, "Cyrillic text must tokenize to a non-empty token list"
    assert "привет" in toks  # case-folded (Python str.lower() is Unicode-aware)


def test_tokenize_japanese_is_not_empty():
    toks = B.tokenize("東京の天気はどうですか")
    assert toks, "Japanese text must tokenize to a non-empty token list"


def test_tokenize_cjk_bigrams_basic():
    """CJK runs (no whitespace segmentation) split into overlapping character bigrams."""
    assert B.tokenize("東京都") == ["東京", "京都"]


def test_tokenize_cjk_single_char_run_yields_the_char_itself():
    """A length-1 CJK run can't form a bigram -- falls back to the single char, per spec."""
    assert B.tokenize("東") == ["東"]


def test_tokenize_cjk_bigram_overlap_enables_bm25_match():
    """The whole point of bigramming: a query sharing a 2-char SUBSTRING with an indexed
    memory must overlap on a token -- this is what makes BM25 retrieval work for CJK at all."""
    doc_tokens = set(B.tokenize("東京都渋谷区のカフェ"))  # "Shibuya-ku, Tokyo... cafe"
    query_tokens = set(B.tokenize("渋谷区のイベント"))  # "Shibuya-ku ... event"
    assert doc_tokens & query_tokens, "shared substring '渋谷区' must produce overlapping bigrams"


def test_tokenize_mixed_cjk_and_latin_run():
    """A token gluing CJK and ASCII digits/letters with no separator splits correctly: CJK
    part bigrammed, non-CJK part word-tokenized (each independently, not as one blob)."""
    toks = B.tokenize("東京2024test")
    assert "東京" in toks
    assert "2024test" in toks


def test_tokenize_english_stopwords_and_length_floor_unchanged():
    """RET-3 must not regress the existing English behavior: stopwords still dropped, 1-char
    tokens still dropped, multi-char content words still kept."""
    toks = B.tokenize("the quick fox is a test of tokenization")
    assert "the" not in toks and "is" not in toks and "a" not in toks and "of" not in toks
    assert "quick" in toks and "fox" in toks and "test" in toks and "tokenization" in toks


def test_tokenize_empty_and_whitespace():
    assert B.tokenize("") == []
    assert B.tokenize("   ") == []


# --------------------------------------------------------------------------- #
# RET-3: resolve_embed_model() precedence — env > model.json preset > English default
# --------------------------------------------------------------------------- #
def test_resolve_embed_model_defaults_to_english(tmp_path, monkeypatch):
    monkeypatch.delenv("HIPPO_EMBED_MODEL", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    assert B.resolve_embed_model() == B.ENGLISH_DEFAULT_MODEL


def test_resolve_embed_model_reads_persisted_preset(tmp_path, monkeypatch):
    monkeypatch.delenv("HIPPO_EMBED_MODEL", raising=False)
    plugin_data = str(tmp_path / "plugin-data")
    os.makedirs(plugin_data)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", plugin_data)
    with open(os.path.join(plugin_data, "model.json"), "w", encoding="utf-8") as fh:
        fh.write('{"embed_model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"}')
    assert B.resolve_embed_model() == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def test_resolve_embed_model_env_override_wins_over_preset(tmp_path, monkeypatch):
    """HIPPO_EMBED_MODEL must win even when a model.json preset is ALSO present -- the env
    override is the top precedence level, unconditionally (existing tests/hooks rely on this)."""
    plugin_data = str(tmp_path / "plugin-data")
    os.makedirs(plugin_data)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", plugin_data)
    with open(os.path.join(plugin_data, "model.json"), "w", encoding="utf-8") as fh:
        fh.write('{"embed_model": "some/preset-model"}')
    monkeypatch.setenv("HIPPO_EMBED_MODEL", "some/env-model")
    assert B.resolve_embed_model() == "some/env-model"


def test_resolve_embed_model_missing_preset_file_falls_through(tmp_path, monkeypatch):
    monkeypatch.delenv("HIPPO_EMBED_MODEL", raising=False)
    plugin_data = str(tmp_path / "plugin-data")
    os.makedirs(plugin_data)  # dir exists, but no model.json inside it
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", plugin_data)
    assert B.resolve_embed_model() == B.ENGLISH_DEFAULT_MODEL


def test_resolve_embed_model_corrupt_preset_never_raises(tmp_path, monkeypatch):
    """A corrupt/unreadable model.json must degrade to the default, never raise or crash --
    resolve_embed_model runs at MODULE IMPORT time, so a raise here would break every hook."""
    monkeypatch.delenv("HIPPO_EMBED_MODEL", raising=False)
    plugin_data = str(tmp_path / "plugin-data")
    os.makedirs(plugin_data)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", plugin_data)
    with open(os.path.join(plugin_data, "model.json"), "w", encoding="utf-8") as fh:
        fh.write("{not valid json")
    assert B.resolve_embed_model() == B.ENGLISH_DEFAULT_MODEL


def test_resolve_embed_model_non_dict_json_falls_through(tmp_path, monkeypatch):
    monkeypatch.delenv("HIPPO_EMBED_MODEL", raising=False)
    plugin_data = str(tmp_path / "plugin-data")
    os.makedirs(plugin_data)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", plugin_data)
    with open(os.path.join(plugin_data, "model.json"), "w", encoding="utf-8") as fh:
        fh.write("[1, 2, 3]")
    assert B.resolve_embed_model() == B.ENGLISH_DEFAULT_MODEL


# --------------------------------------------------------------------------- #
# RET-3: switching the embed model forces a full re-embed (never silently reuses stale rows)
# --------------------------------------------------------------------------- #
def test_model_switch_forces_full_reembed(tmp_path, monkeypatch):
    """build_index's cache-reuse check gates on `old_manifest["model"] == DEFAULT_MODEL` --
    switching HIPPO_EMBED_MODEL (what bootstrap's model.json preset ultimately becomes, via
    resolve_embed_model, at the next process start) must make EVERY existing row a cache miss,
    not just the changed ones -- the whole point being two different models' vectors are NOT
    comparable, so no row can be silently carried over."""
    # The CI hermetic lane exports HIPPO_DISABLE_DENSE=1 job-wide (see the sibling tests in
    # this file) -- without clearing it here, dense_disabled() short-circuits want_dense before
    # the monkeypatched embedder ever runs, so dense_ready is force-False regardless of this
    # test's own logic. Every other dense-path test in this file already delenv's this.
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha content", "b.md": "beta content"})

    monkeypatch.setattr(B, "embed_documents", _fake_embedder(16))
    monkeypatch.setattr(B, "DEFAULT_MODEL", "model-a")
    manifest_a = B.build_index(md, idx)
    assert manifest_a["dense_ready"] is True
    assert manifest_a["model"] == "model-a"
    rows_a = [e["row"] for e in manifest_a["entries"]]
    assert all(r is not None for r in rows_a)  # every entry embedded fresh under model-a

    embed_calls = {"count": 0}
    base_embedder = _fake_embedder(16)

    def _counting_embedder(texts, allow_download=True):
        embed_calls["count"] += len(texts)
        return base_embedder(texts, allow_download=allow_download)

    monkeypatch.setattr(B, "embed_documents", _counting_embedder)
    monkeypatch.setattr(B, "DEFAULT_MODEL", "model-b")  # simulate a switched model
    manifest_b = B.build_index(md, idx)  # no corpus change at all -- only the model changed
    assert manifest_b["model"] == "model-b"
    assert manifest_b["dense_ready"] is True
    # Nothing was cache-reused from model-a's rows: BOTH entries were re-embedded under model-b.
    assert embed_calls["count"] == 2


# --------------------------------------------------------------------------- #
# RET-2: body-aware indexing — compute_body_chunks bounds + build_index wiring
# --------------------------------------------------------------------------- #
def _mem_with_body(name: str, description: str, body: str) -> str:
    return f'---\nname: {name}\ndescription: "{description}"\ntype: project\n---\n{body}\n'


def _write_body_corpus(memory_dir: str, items: dict) -> None:
    """``items``: fname -> (description, body)."""
    os.makedirs(memory_dir, exist_ok=True)
    for fname, (desc, body) in items.items():
        with open(os.path.join(memory_dir, fname), "w", encoding="utf-8") as fh:
            fh.write(_mem_with_body(fname[:-3], desc, body))


def test_compute_body_chunks_heading_guided_split():
    """A body with >=1 '##'+ heading splits ONE chunk per section, heading line kept in it."""
    text = (
        "---\nname: a\ndescription: \"generic\"\n---\n"
        "## First Section\n"
        + ("alpha " * 20)
        + "\n\n## Second Section\n"
        + ("beta " * 20)
    )
    chunks = B.compute_body_chunks("a", text)
    assert len(chunks) == 2
    assert chunks[0]["text"].startswith("## First Section")
    assert chunks[1]["text"].startswith("## Second Section")


def test_compute_body_chunks_paragraph_guided_fallback():
    """No '##'+ heading anywhere -> falls back to blank-line-separated paragraphs (the shape
    most of this corpus's real memories take -- bolded labels, no markdown headings)."""
    text = (
        "---\nname: a\ndescription: \"generic\"\n---\n"
        + ("**Why:** " + "root cause discipline matters a lot here today. " * 4)
        + "\n\n"
        + ("**How to apply:** " + "always fix the actual bug not a symptom of it now. " * 4)
    )
    chunks = B.compute_body_chunks("a", text)
    assert len(chunks) == 2
    assert chunks[0]["text"].startswith("**Why:**")
    assert chunks[1]["text"].startswith("**How to apply:**")


def test_compute_body_chunks_caps_at_three():
    """MAX 3 chunks per memory even when the body has many qualifying paragraphs."""
    paras = "\n\n".join(f"paragraph number {i} has plenty of unique content words here today" * 2 for i in range(6))
    text = f'---\nname: a\ndescription: "generic"\n---\n{paras}\n'
    chunks = B.compute_body_chunks("a", text)
    assert len(chunks) <= B._BODY_CHUNK_MAX
    assert len(chunks) == 3


def test_compute_body_chunks_skips_trivia_below_min_chars():
    """A paragraph shorter than ~80 chars is trivia -- skipped, not indexed as noise."""
    text = '---\nname: a\ndescription: "generic"\n---\ntoo short\n\n' + ("substantial content " * 10)
    chunks = B.compute_body_chunks("a", text)
    assert all(len(c["text"]) >= B._BODY_CHUNK_MIN_CHARS for c in chunks)
    assert not any(c["text"] == "too short" for c in chunks)


def test_compute_body_chunks_only_considers_first_char_budget():
    """Content beyond ~1500 chars of body is never chunked at all."""
    filler = "z" * 2000  # one huge unbroken paragraph, well past the budget
    text = f'---\nname: a\ndescription: "generic"\n---\n{filler}\n'
    chunks = B.compute_body_chunks("a", text)
    for c in chunks:
        assert len(c["text"]) <= B._BODY_CHUNK_CHAR_BUDGET


def test_compute_body_chunks_empty_body_yields_no_chunks():
    text = '---\nname: a\ndescription: "generic"\n---\n\n'
    assert B.compute_body_chunks("a", text) == []


def test_compute_body_chunks_never_raises_on_no_frontmatter():
    assert B.compute_body_chunks("a", "just plain text, no frontmatter fence at all") != [] or True
    # (never raises is the actual contract; the exact split doesn't matter here)


# --------------------------------------------------------------------------- #
# RET-2: manifest wiring — body_chunks block, widened dense matrix, incremental reuse
# --------------------------------------------------------------------------- #
_DISTINCTIVE_BODY = (
    "## Error signature\n"
    "The exact failure is a zqxwyvutplaceholder timeout raised from the network layer "
    "when the retry budget is exhausted before the handshake completes successfully.\n\n"
    "## Root cause\n"
    "A misconfigured connection pool size caused exhaustion under load during peak traffic "
    "hours across every affected region consistently.\n"
)


def test_build_index_manifest_carries_body_chunks_block(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_body_corpus(md, {"a.md": ("a generic description", _DISTINCTIVE_BODY)})
    manifest = B.build_index(md, idx)
    assert "body_chunks" in manifest
    chunks = manifest["body_chunks"]
    assert chunks and all(c["entry"] == 0 for c in chunks)
    # RCL-6: "text" joined the persisted shape (the evidence-snippet render needs the
    # winning chunk's verbatim text with no read-at-emit).
    assert all(set(c.keys()) == {"entry", "hash", "tokens", "row", "text"} for c in chunks)

    # Round-trips through the actual manifest.json file on disk.
    import json

    with open(os.path.join(idx, "manifest.json"), "r", encoding="utf-8") as fh:
        on_disk = json.load(fh)
    assert on_disk["body_chunks"] == chunks

    # The entries list itself is EXACTLY the pre-RET-2 shape plus RET-5's
    # source_commit_time, GOV-2's steer, and GOV-7's confidence (no OTHER new keys
    # leaked onto it).
    assert set(manifest["entries"][0].keys()) == {
        "name", "file", "doc_text", "description", "hash", "tokens", "invalid_after",
        "source_commit_time", "steer", "confidence", "row",
    }


def test_build_index_manifest_carries_head_commit(repo, memory_dir, monkeypatch):
    """RCL-6: the manifest stamps the CURRENT HEAD at build time -- one git call per BUILD,
    never per query -- as the evidence-snippet's "indexed @sha" source."""
    from .conftest import git_commit

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    idx = os.path.join(os.path.dirname(memory_dir), ".memory-index")
    with open(os.path.join(memory_dir, "a.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: a\ndescription: "d"\ntype: project\n---\nbody\n')
    sha = git_commit(repo, "seed", 1_700_000_000)

    manifest = B.build_index(memory_dir, idx)
    assert manifest["head_commit"] == sha


def test_build_index_manifest_head_commit_none_outside_git_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_body_corpus(md, {"a.md": ("a generic description", _DISTINCTIVE_BODY)})
    manifest = B.build_index(md, idx)
    assert manifest["head_commit"] is None


def test_build_index_dense_matrix_widened_with_body_chunk_rows(tmp_path, monkeypatch):
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", _fake_embedder(16))
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_body_corpus(
        md,
        {
            "a.md": ("a generic description", _DISTINCTIVE_BODY),
            "b.md": ("another generic description", "short body, no heading here at all"),
        },
    )
    manifest = B.build_index(md, idx)
    assert manifest["dense_ready"] is True
    n_entries = len(manifest["entries"])
    n_chunks = len(manifest["body_chunks"])
    assert n_chunks >= 2  # a.md's two headed sections both clear the min-chars floor

    dense = np.load(os.path.join(idx, "dense.npy"))
    assert dense.shape[0] == n_entries + n_chunks  # widened: descriptions THEN chunks
    # Every entry row is in 0..n_entries-1, every chunk row is in n_entries..n_total-1.
    for e in manifest["entries"]:
        assert 0 <= e["row"] < n_entries
    for c in manifest["body_chunks"]:
        assert n_entries <= c["row"] < n_entries + n_chunks


def test_incremental_rebuild_reuses_unchanged_body_chunk_rows(tmp_path, monkeypatch):
    """Hash-keyed reuse for chunk rows, exactly like entry rows: editing ONE memory's body
    must not force a re-embed of an UNCHANGED memory's body chunks."""
    embedded_batches = []
    base = _fake_embedder(16)

    def counting_embed(texts, allow_download=True):
        embedded_batches.append(list(texts))
        return base(texts)

    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", counting_embed)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_body_corpus(
        md,
        {
            "a.md": ("a generic description", _DISTINCTIVE_BODY),
            "b.md": ("another generic description", "short body, no heading here at all " * 3),
        },
    )
    m1 = B.build_index(md, idx)
    assert m1["dense_ready"] is True
    first_total_embedded = sum(len(b) for b in embedded_batches)
    assert first_total_embedded == len(m1["entries"]) + len(m1["body_chunks"])

    # Edit ONLY b.md's body -- a's description+body chunks must all be cache-hit.
    embedded_batches.clear()
    with open(os.path.join(md, "b.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem_with_body("b", "another generic description", "## Changed\n" + "totally new body content here for b " * 4))
    m2 = B.build_index(md, idx)
    assert m2["dense_ready"] is True

    reembedded_texts = [t for batch in embedded_batches for t in batch]
    # None of a's original body-chunk text (from _DISTINCTIVE_BODY) was resubmitted.
    assert not any("zqxwyvutplaceholder" in t for t in reembedded_texts)
    # b's NEW body chunk text WAS resubmitted (its hash changed).
    assert any("changed" in t.lower() for t in reembedded_texts)


def test_loaded_index_exposes_body_chunks(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_body_corpus(md, {"a.md": ("a generic description", _DISTINCTIVE_BODY)})
    B.build_index(md, idx)
    loaded = B.load_index(idx)
    assert loaded.body_chunks  # non-empty, surfaced on the LoadedIndex view


def test_loaded_index_degrades_dense_when_widened_matrix_mismatches_body_chunks(tmp_path, monkeypatch):
    """A torn/corrupt dense.npy whose row count matches entries+OLD chunk count but not the
    manifest's CURRENT body_chunks count must degrade the WHOLE dense view (not just chunks)."""
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", _fake_embedder(16))
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_body_corpus(md, {"a.md": ("a generic description", _DISTINCTIVE_BODY)})
    B.build_index(md, idx)

    manifest = B._load_manifest(idx)
    # Corrupt the manifest in-memory: claim one MORE body chunk than the dense matrix has rows for.
    manifest["body_chunks"] = manifest["body_chunks"] + [
        {"entry": 0, "hash": "deadbeef", "tokens": ["x"], "row": 9999}
    ]
    dense = B._load_dense(idx)
    loaded = B.LoadedIndex(manifest, dense)
    assert loaded.dense_ready is False
    assert loaded.dense is None


def test_body_chunks_absent_key_degrades_cleanly_on_old_manifest(tmp_path):
    """A pre-RET-2 manifest (no 'body_chunks' key at all) must load fine -- body_chunks
    degrades to [] via .get(), no KeyError, and dense-matches-entries validation is
    unaffected (0 chunks -> same row-count check as before this item)."""
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    os.makedirs(idx, exist_ok=True)
    import json as _json

    old_manifest = {
        "schema_version": B.SCHEMA_VERSION,  # current — COR-7's gate would hide any other
        "model": None,
        "dense_ready": False,
        "dim": None,
        "count": 1,
        "entries": [{"name": "a", "file": "a.md", "doc_text": "a. alpha", "description": "alpha", "hash": "x", "tokens": ["a"], "row": None}],
        "bm25": {"postings": {}, "doc_len": [0], "avgdl": 0.0, "idf": {}, "k1": 1.5, "b": 0.75},
    }
    with open(os.path.join(idx, "manifest.json"), "w", encoding="utf-8") as fh:
        _json.dump(old_manifest, fh)
    loaded = B.load_index(idx)
    assert loaded is not None
    assert loaded.body_chunks == []


# --------------------------------------------------------------------------- #
# RET-2: refresh_index heals body-only drift (description hash unchanged, body changed)
# --------------------------------------------------------------------------- #
def test_refresh_index_heals_body_only_edit(tmp_path, monkeypatch):
    """A body edit that leaves the description BYTE-IDENTICAL changes no entry hash at all --
    refresh_index's corpus-unchanged short-circuit must still notice via body-chunk hashes
    and re-run the build so the NEW body content becomes indexed/retrievable."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_body_corpus(md, {"a.md": ("a generic description", "## Old\n" + "original body content here today " * 4)})
    m1 = B.build_index(md, idx)
    old_chunk_hashes = {c["hash"] for c in m1["body_chunks"]}

    # Edit ONLY the body (description string is byte-identical) -- entry hash is unchanged.
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem_with_body("a", "a generic description", "## New\n" + "brand new distinctive body content today " * 4))

    m2 = B.refresh_index(md, idx)
    assert m2 is not None
    new_chunk_hashes = {c["hash"] for c in m2["body_chunks"]}
    assert new_chunk_hashes != old_chunk_hashes  # the no-op short-circuit did NOT fire
    assert any("distinctive" in " ".join(c["tokens"]) for c in m2["body_chunks"])


# --------------------------------------------------------------------------- #
# COR-7: schema_version is ENFORCED on every manifest load path — a mismatched
# manifest reads as absent, so a plugin update that changes the manifest shape
# costs exactly ONE full rebuild instead of silently serving the stale shape.
# --------------------------------------------------------------------------- #
def test_load_index_treats_schema_mismatch_as_absent(tmp_path, monkeypatch):
    """The load-path half of the AC: the moment SCHEMA_VERSION moves, _load_manifest (and
    therefore load_index — recall's path) returns None for an index built at the old
    version, instead of serving it verbatim."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha"})
    B.build_index(md, idx)
    assert B.load_index(idx) is not None  # sanity: the current version loads

    monkeypatch.setattr(B, "SCHEMA_VERSION", B.SCHEMA_VERSION + 1)
    assert B._load_manifest(idx) is None  # the one gate every consumer passes through
    assert B.load_index(idx) is None
    # The RAW reader still sees it — that is doctor's format-check path, not a load path.
    assert B._read_manifest_json(idx) is not None


def test_schema_bump_triggers_exactly_one_full_rebuild_then_noop(tmp_path, monkeypatch):
    """THE acceptance test: monkeypatching SCHEMA_VERSION to a new value makes the next
    refresh perform exactly ONE full rebuild — every doc re-embedded (zero hash-keyed row
    reuse from the stale manifest) and the manifest rewritten with the NEW version — and a
    SECOND refresh with an unchanged corpus is back on the no-op fast path (no embeds, no
    rewrite)."""
    embedded = []

    def counting(texts, allow_download=True):
        embedded.append(list(texts))
        return _fake_embedder(16)(texts)

    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", counting)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha", "b.md": "beta"})
    B.build_index(md, idx)
    embedded.clear()

    bumped = B.SCHEMA_VERSION + 1
    monkeypatch.setattr(B, "SCHEMA_VERSION", bumped)
    refreshed = B.refresh_index(md, idx)
    assert refreshed is not None and refreshed["dense_ready"] is True
    # ONE full rebuild: both docs re-embedded (the old manifest contributed no cached rows).
    assert sum(len(batch) for batch in embedded) == 2
    import json as _json

    with open(os.path.join(idx, "manifest.json"), "r", encoding="utf-8") as fh:
        assert _json.load(fh)["schema_version"] == bumped  # rewritten at the NEW version

    embedded.clear()
    mtime_before = os.path.getmtime(os.path.join(idx, "manifest.json"))
    again = B.refresh_index(md, idx)  # unchanged corpus at the current version
    assert again is not None
    assert embedded == []  # NOT a second rebuild
    assert os.path.getmtime(os.path.join(idx, "manifest.json")) == mtime_before  # no rewrite


def test_build_index_discards_row_reuse_on_schema_mismatch(tmp_path, monkeypatch):
    """build_index's incremental path is a manifest LOAD path too: a version-mismatched old
    manifest must contribute ZERO cached embedding rows (its per-entry row indices may not
    mean what the current code thinks they mean)."""
    embedded = []

    def counting(texts, allow_download=True):
        embedded.append(list(texts))
        return _fake_embedder(16)(texts)

    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", counting)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha", "b.md": "beta"})
    B.build_index(md, idx)
    embedded.clear()

    monkeypatch.setattr(B, "SCHEMA_VERSION", B.SCHEMA_VERSION + 1)
    B.build_index(md, idx)  # unchanged corpus — but the old manifest is now invisible
    assert sum(len(batch) for batch in embedded) == 2  # full re-embed, no hash reuse


def test_check_index_integrity_distinguishes_schema_mismatch_from_corruption(tmp_path, monkeypatch):
    """A version-mismatched manifest is a routine plugin-update state, NOT damage: the
    integrity detector must return None for it (doctor's check_format_version owns naming
    it) rather than misdiagnose it as corrupt JSON via the gated loader."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha"})
    B.build_index(md, idx)
    assert B.check_index_integrity(idx) is None  # healthy at the current version

    monkeypatch.setattr(B, "SCHEMA_VERSION", B.SCHEMA_VERSION + 1)
    assert B.check_index_integrity(idx) is None  # mismatch != corruption
