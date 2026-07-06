"""Tests for memory/build_index.py — incremental hybrid index build.

Hermetic: every test writes a throwaway memory dir under tmp_path and points the builder
at a tmp index dir. The dense path is exercised with a deterministic FAKE embedder (no
fastembed, no network); a single importorskip test covers the real backend when present.
"""

from __future__ import annotations

import os

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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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

    monkeypatch.delenv("MEMOBOT_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", boom)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha", "b.md": "beta"})

    manifest = B.build_index(md, idx)  # must NOT raise
    assert manifest["dense_ready"] is False and manifest["count"] == 2


# --------------------------------------------------------------------------- #
# Dense build with a fake embedder (deterministic, offline)
# --------------------------------------------------------------------------- #
def test_dense_build_writes_matrix_and_rows(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMOBOT_DISABLE_DENSE", raising=False)
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


def test_incremental_rebuild_only_embeds_changed(tmp_path, monkeypatch):
    embedded_batches = []

    base = _fake_embedder(16)

    def counting_embed(texts, allow_download=True):
        embedded_batches.append(list(texts))
        return base(texts)

    monkeypatch.delenv("MEMOBOT_DISABLE_DENSE", raising=False)
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

    monkeypatch.delenv("MEMOBOT_DISABLE_DENSE", raising=False)
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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

    monkeypatch.delenv("MEMOBOT_DISABLE_DENSE", raising=False)
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


def test_load_index_tolerates_older_index_missing_invalid_after(tmp_path):
    """An index written before this field existed (no invalid_after key on any entry) must
    load fine -- entry.get("invalid_after") degrades to None via dict .get(), no KeyError."""
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    os.makedirs(idx, exist_ok=True)
    import json as _json

    old_manifest = {
        "schema_version": 1,  # pre-Tier-3 schema
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

    monkeypatch.delenv("MEMOBOT_DISABLE_DENSE", raising=False)
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
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "alpha", "b.md": "beta"})
    monkeypatch.setattr(B, "embed_documents", _fake_embedder(16))
    B.build_index(md, idx)
    assert os.path.exists(os.path.join(idx, "dense.npy"))
    # Rebuild with dense disabled -> the stale dense.npy must be removed.
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    m2 = B.build_index(md, idx)
    assert m2["dense_ready"] is False
    assert not os.path.exists(os.path.join(idx, "dense.npy"))


def test_allow_download_false_threads_to_embedder(tmp_path, monkeypatch):
    seen = {}

    def recording_embed(texts, allow_download=True):
        seen["allow_download"] = allow_download
        return _fake_embedder(16)(texts)

    monkeypatch.delenv("MEMOBOT_DISABLE_DENSE", raising=False)
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
    monkeypatch.delenv("MEMOBOT_DISABLE_DENSE", raising=False)
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

    monkeypatch.delenv("MEMOBOT_DISABLE_DENSE", raising=False)
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
    monkeypatch.delenv("MEMOBOT_DISABLE_DENSE", raising=False)
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


def test_load_index_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"a.md": "voyage reranker", "b.md": "budget envelope"})
    B.build_index(md, idx)
    loaded = B.load_index(idx)
    assert loaded is not None and len(loaded) == 2
    assert loaded.dense_ready is False
    assert B.load_index(str(tmp_path / "nope")) is None


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
    monkeypatch.delenv("MEMOBOT_DISABLE_DENSE", raising=False)
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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

    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    repo = str(tmp_path / "repo")
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    with open(os.path.join(md, "m.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: m\ndescription: "zebra canary deploys"\n---\nbody\n')
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    monkeypatch.setenv("MEMOBOT_MEMORY_DIR", md)
    monkeypatch.chdir(repo)
    R.main(["zebra", "canary", "deploys"])  # builds the index + writes telemetry

    assert os.path.isdir(os.path.join(repo, ".claude", ".memory-index"))
    assert os.path.isdir(os.path.join(repo, ".claude", ".memory-telemetry"))
    porcelain = subprocess.run(
        ["git", "status", "--porcelain", "-uall"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert ".memory-index" not in porcelain and ".memory-telemetry" not in porcelain
    assert ".claude/memory/m.md" in porcelain  # the CORPUS stays visible/committable
