"""Tests for memory/recall.py — fused recall + hook formatting + git-recent.

Hermetic: tmp memory dir + tmp index dir; dense exercised with a deterministic FAKE
embedder. The git-recent producer test uses the conftest git repo fixtures.
"""

from __future__ import annotations

import math
import os
import zlib

import numpy as np
import pytest

from memory import build_index as B
from memory import recall as R

from .conftest import git_commit, write_file


def _mem(name: str, description: str, body: str = "body") -> str:
    return f'---\nname: {name}\ndescription: "{description}"\ntype: project\n---\n{body}\n'


def _write_corpus(memory_dir: str, items: dict) -> None:
    os.makedirs(memory_dir, exist_ok=True)
    for fname, desc in items.items():
        with open(os.path.join(memory_dir, fname), "w", encoding="utf-8") as fh:
            fh.write(_mem(fname[:-3], desc))


def _fake_embedder(dim: int = 16):
    """Deterministic bag-of-hashed-tokens fake embedder for hermetic dense-path tests.

    RET-1: uses ``zlib.crc32`` (a fixed, unsalted hash) instead of Python's builtin
    ``hash()`` for the token->bucket mapping. ``hash(str)`` is SALTED per-process by
    default (``PYTHONHASHSEED=random``) -- this module's docstring always claimed the fake
    embedder was "deterministic", but that was only true WITHIN one process/run, not
    ACROSS runs. Every prior use of this fixture only ever checked "did dense contribute at
    all" (any nonzero similarity counted), so the run-to-run hash-seed variance was
    invisible. RET-1's calibrated floor made it visible: whether a given fake-embedder
    similarity clears the floor now depends on which hash bucket a token happens to land
    in THIS process, which could flip a test between pass/fail across runs with no code
    change -- a real flakiness bug this item's floor exposed rather than introduced. crc32
    is stable across processes/interpreters/platforms, restoring genuine determinism.
    """

    def _vec(text: str):
        v = np.zeros(dim, dtype="float32")
        for tok in B.tokenize(text):
            v[zlib.crc32(tok.encode("utf-8")) % dim] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_documents(texts, allow_download=True):
        return np.vstack([_vec(t) for t in texts]).astype("float32")

    def embed_query(text, allow_download=False):
        return _vec(text)

    return embed_documents, embed_query


_CORPUS = {
    "reranker_voyage.md": "voyage rerank cross encoder is the primary reranker bm25 hybrid fallback",
    "budget_envelope.md": "phase envelope budget authority guards the synthesis tail reservation",
    "excel_header.md": "excel parser llm header rescue for non canonical column layouts capped at three calls",
    "canvas_pdf.md": "canvas pdf export two pass gotenberg pypdf footnote marker",
    "formula_graph.md": "formula graph columnar parquet csr rebuild trace dependencies stay python",
}


# --------------------------------------------------------------------------- #
# BM25-only recall (no dense) — runs everywhere
# --------------------------------------------------------------------------- #
def test_recall_bm25_returns_expected_topk(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _CORPUS)
    B.build_index(md, idx)

    res = R.recall("which reranker do we use for search results", k=5, memory_dir=md, index_dir=idx)
    names = [r["name"] for r in res]
    assert "reranker_voyage" in names
    assert all(r["backend"] == "bm25" for r in res)


def test_recall_empty_query_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _CORPUS)
    B.build_index(md, idx)
    assert R.recall("", memory_dir=md, index_dir=idx) == []
    assert R.recall("   ", memory_dir=md, index_dir=idx) == []


def test_recall_never_raises_on_missing_index(tmp_path):
    # No corpus, no index — recall must return [] not raise.
    md = str(tmp_path / "empty")
    os.makedirs(md)
    assert R.recall("anything", memory_dir=md, index_dir=str(tmp_path / "noidx")) == []


def test_recall_returns_empty_when_no_ranker_matches(tmp_path, monkeypatch):
    # query whose tokens appear in NO memory -> BM25 empty + dense disabled -> [] (not a crash)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _CORPUS)
    B.build_index(md, idx)
    assert R.recall("zzqq xyzzy nonexistent qwertyuiop", k=5, memory_dir=md, index_dir=idx) == []


def test_recall_uses_stored_description_not_doc_text_split(tmp_path, monkeypatch):
    # The recalled description comes from the stored field, robust to any '. ' in the name.
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"excel_header.md": _CORPUS["excel_header.md"]})
    B.build_index(md, idx)
    res = R.recall("excel header inference", k=1, memory_dir=md, index_dir=idx)
    assert res and res[0]["description"] == _CORPUS["excel_header.md"]


def test_recall_builds_index_on_demand_bm25(tmp_path, monkeypatch):
    # Index not pre-built; recall should build a BM25 view implicitly (no dense download).
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _CORPUS)
    res = R.recall("excel header inference for weird columns", k=5, memory_dir=md, index_dir=idx)
    assert any(r["name"] == "excel_header" for r in res)


# --------------------------------------------------------------------------- #
# Soft-invalidation (Tier 3, graceful decay) — _invalidation_state + recall()'s pre-cut penalty
# --------------------------------------------------------------------------- #
def _iso_days_ago(days):
    import datetime

    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()


def test_invalidation_state_classifies_recent_old_absent_unparseable():
    import datetime

    ref = datetime.datetime(2026, 6, 30, tzinfo=datetime.timezone.utc)
    now = ref.timestamp()
    recent = (ref - datetime.timedelta(days=5)).isoformat()
    old = (ref - datetime.timedelta(days=45)).isoformat()
    boundary = (ref - datetime.timedelta(days=30)).isoformat()  # exactly 30d -> "old" (< is strict)

    assert R._invalidation_state({}, now=now) is None
    assert R._invalidation_state({"invalid_after": None}, now=now) is None
    assert R._invalidation_state({"invalid_after": ""}, now=now) is None
    assert R._invalidation_state({"invalid_after": recent}, now=now) == "recent"
    assert R._invalidation_state({"invalid_after": old}, now=now) == "old"
    assert R._invalidation_state({"invalid_after": boundary}, now=now) == "old"
    assert R._invalidation_state({"invalid_after": "not-a-date"}, now=now) is None  # fails open
    assert R._invalidation_state({"invalid_after": 12345}, now=now) is None  # fails open, never raises


def test_recall_recently_invalidated_demotes_and_can_drop_out_of_topk(tmp_path, monkeypatch):
    """The exact contract the roadmap's open question asked for: a recent invalidation must
    be able to move the top-k CUT, not just relabel a result after the cut already happened."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx_dir = str(tmp_path / ".memory-index")
    # 5 entries, strictly nested token overlap with the query -> a clean, discoverable ranking.
    words = ["alpha", "beta", "gamma", "delta", "epsilon"]
    _write_corpus(md, {f"e{i}.md": " ".join(words[:i]) for i in range(1, 6)})
    B.build_index(md, idx_dir)
    index = B.load_index(idx_dir)

    query = " ".join(words)
    k = 3
    full_ranking = [r["name"] for r in R.recall(query, k=10, index=index)]
    assert len(full_ranking) >= k + 1  # need a boundary entry + a successor to demonstrate the swap
    boundary_name = full_ranking[k - 1]
    successor_name = full_ranking[k]

    before = [r["name"] for r in R.recall(query, k=k, index=index)]
    assert boundary_name in before and successor_name not in before

    for e in index.entries:
        if e["name"] == boundary_name:
            e["invalid_after"] = _iso_days_ago(5)  # "recent" -> x0.5 penalty

    after_names = [r["name"] for r in R.recall(query, k=k, index=index)]
    assert boundary_name not in after_names  # demoted out of top-k by the penalty
    assert successor_name in after_names  # moved up to take its place
    assert len(after_names) == k  # no under-fill

    # still RECALLABLE at a larger k -- soft penalty, never a hard exclude
    wide = [r["name"] for r in R.recall(query, k=10, index=index)]
    assert boundary_name in wide


def test_recall_old_invalidated_dropped_from_display_not_corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx_dir = str(tmp_path / ".memory-index")
    _write_corpus(md, {"e1.md": "alpha one", "e2.md": "alpha two", "e3.md": "alpha three"})
    B.build_index(md, idx_dir)
    index = B.load_index(idx_dir)

    query = "alpha"
    before = [r["name"] for r in R.recall(query, k=10, index=index)]
    assert "e1" in before

    for e in index.entries:
        if e["name"] == "e1":
            e["invalid_after"] = _iso_days_ago(400)  # "old" -> display-only drop

    after = [r["name"] for r in R.recall(query, k=10, index=index)]
    assert "e1" not in after  # NEVER in display, at any k
    assert "e2" in after and "e3" in after

    # the corpus/index itself is untouched -- still 3 entries, e1 still present
    assert len(index.entries) == 3
    assert any(e["name"] == "e1" for e in index.entries)
    # e1 still fully participates in the raw ranking machinery (BM25 candidate generation)
    bm25_indices = R._bm25_rank(R.tokenize(query), index.entries)
    e1_idx = next(i for i, e in enumerate(index.entries) if e["name"] == "e1")
    assert e1_idx in bm25_indices


def test_recall_walk_and_break_avoids_underfill(tmp_path, monkeypatch):
    """If old-invalidated entries occupy slots that would otherwise land in the naive top-k,
    the walk-and-break loop must still fill k results from the eligible tail -- a fixed
    `[:k]` slice-then-filter would silently under-fill."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx_dir = str(tmp_path / ".memory-index")
    _write_corpus(md, {f"e{i}.md": "alpha" for i in range(6)})  # 6 equally-matching entries
    B.build_index(md, idx_dir)
    index = B.load_index(idx_dir)

    query = "alpha"
    k = 4
    ranking = [r["name"] for r in R.recall(query, k=10, index=index)]
    assert len(ranking) == 6

    to_invalidate = set(ranking[:2])  # invalidate (old) the first two in the natural ranking
    for e in index.entries:
        if e["name"] in to_invalidate:
            e["invalid_after"] = _iso_days_ago(400)

    after = R.recall(query, k=k, index=index)
    assert len(after) == k  # still fills k, even though 2 of the original top-4 were dropped
    after_names = {r["name"] for r in after}
    assert not (after_names & to_invalidate)


def test_recall_no_invalid_after_is_unaffected(tmp_path, monkeypatch):
    """Sanity: with no entry carrying invalid_after, behavior is exactly what it was before
    this tier (same names, same order) -- the no-op path."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx_dir = str(tmp_path / ".memory-index")
    _write_corpus(md, _CORPUS)
    B.build_index(md, idx_dir)
    index = B.load_index(idx_dir)
    res = R.recall("voyage reranker cross encoder", k=5, index=index)
    assert any(r["name"] == "reranker_voyage" for r in res)
    assert all(e.get("invalid_after") is None for e in index.entries)


# --------------------------------------------------------------------------- #
# COR-8: true fused scores (not fabricated 1/rank) + explicit emission rank
# --------------------------------------------------------------------------- #
def test_recall_emits_monotone_scores_and_sequential_ranks(tmp_path, monkeypatch):
    """Baseline sanity (no invalidation): emitted scores are monotone non-increasing in
    emission order, and `rank` is exactly the 1-based emission position."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx_dir = str(tmp_path / ".memory-index")
    _write_corpus(md, _CORPUS)
    B.build_index(md, idx_dir)
    index = B.load_index(idx_dir)

    res = R.recall("reranker budget excel canvas formula", k=5, index=index)
    assert len(res) >= 2
    scores = [r["score"] for r in res]
    assert scores == sorted(scores, reverse=True)  # monotone non-increasing
    assert [r["rank"] for r in res] == list(range(1, len(res) + 1))  # 1-based, no gaps


def test_recall_emitted_score_equals_true_penalized_fused_score(tmp_path, monkeypatch):
    """The exact acceptance case: construct a corpus where the invalidation penalty REORDERS
    results (rank-derived 1/rank and fused-derived scores visibly diverge), then assert the
    emitted `score` is the REAL internal penalized/fused score, not 1/(emission_rank+1)."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx_dir = str(tmp_path / ".memory-index")
    words = ["alpha", "beta", "gamma", "delta", "epsilon"]
    _write_corpus(md, {f"e{i}.md": " ".join(words[:i]) for i in range(1, 6)})
    B.build_index(md, idx_dir)
    index = B.load_index(idx_dir)

    query = " ".join(words)
    k = 3
    full_ranking = [r["name"] for r in R.recall(query, k=10, index=index)]
    boundary_name = full_ranking[k - 1]
    successor_name = full_ranking[k]

    # Recompute the TRUE internal fused+penalized score for every entry the same way
    # recall() does, independently of recall()'s own emission -- this is the oracle the
    # emitted `score` must match exactly (not merely be consistent-with-itself).
    q_tokens = R.tokenize(query)
    bm25 = R._bm25_rank(q_tokens, index.entries)
    fused = dict(R._rrf_fuse([bm25]))

    for e in index.entries:
        if e["name"] == boundary_name:
            e["invalid_after"] = _iso_days_ago(5)  # "recent" -> x0.5 penalty, reorders

    name_to_idx = {e["name"]: i for i, e in enumerate(index.entries)}
    expected_penalized = {}
    for name, i in name_to_idx.items():
        state = R._invalidation_state(index.entries[i])
        raw = fused.get(i, 0.0)
        expected_penalized[name] = raw * R._INVALIDATION_PENALTY if state == "recent" else raw

    after = R.recall(query, k=k, index=index)
    after_names = [r["name"] for r in after]
    # The reorder actually happened -- the exact contract this test must exercise.
    assert boundary_name not in after_names
    assert successor_name in after_names

    for r in after:
        expected = round(expected_penalized[r["name"]], 6)
        assert r["score"] == expected  # the TRUE fused/penalized score, not 1/rank
        # And explicitly NOT the fabricated-1/rank value this item removes (except by the
        # coincidence rank==1, where 1/(1+1)==0.5 could theoretically collide with a real
        # score -- guard against that false-negative by only asserting the divergence for
        # entries where the two formulas provably differ).
        rank_derived = round(1.0 / (r["rank"] + 1), 4)
        if round(expected, 4) != rank_derived:
            assert round(r["score"], 4) != rank_derived

    scores = [r["score"] for r in after]
    assert scores == sorted(scores, reverse=True)  # still monotone in emission order


# --------------------------------------------------------------------------- #
# COR-8: manifest-vs-query embedding model cross-check
# --------------------------------------------------------------------------- #
def test_dense_rank_skips_when_manifest_model_mismatches_configured_model(tmp_path, monkeypatch):
    """A manifest embedded under model X, scored against a query embedded under the
    CURRENTLY configured model Y, is comparing two different embedding spaces -- garbage
    similarity. _dense_rank must refuse and degrade to [] (BM25 carries recall)."""
    emb_docs, emb_query = _fake_embedder(16)
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", emb_docs)
    monkeypatch.setattr(R, "embed_query", emb_query)
    md = str(tmp_path / "memory")
    idx_dir = str(tmp_path / ".memory-index")
    _write_corpus(md, _CORPUS)
    B.build_index(md, idx_dir)

    index = B.load_index(idx_dir)
    assert index.dense_ready is True
    assert index.model == B.DEFAULT_MODEL

    # Simulate a stale index built under a DIFFERENT model than the one now configured.
    index.model = "some/other-model-v2"
    assert R._dense_rank("formula dependency graph columnar storage", index) == []


def test_recall_degrades_to_bm25_with_doctor_visible_reason_on_model_mismatch(
    tmp_path, monkeypatch
):
    """Full acceptance case: build a real dense index, rewrite the on-disk manifest's
    `model` to a different string, and assert BOTH recall()'s backend degrades to bm25
    AND check_index_integrity names the mismatch (doctor-visible, both models + remediation)."""
    emb_docs, emb_query = _fake_embedder(16)
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", emb_docs)
    monkeypatch.setattr(R, "embed_query", emb_query)
    md = str(tmp_path / "memory")
    idx_dir = str(tmp_path / ".memory-index")
    _write_corpus(md, _CORPUS)
    manifest = B.build_index(md, idx_dir)
    assert manifest["dense_ready"] is True
    real_model = manifest["model"]

    # Sanity: BEFORE the rewrite, dense participates and integrity is clean.
    assert B.check_index_integrity(idx_dir) is None
    res_before = R.recall("formula dependency graph columnar storage", k=5, memory_dir=md, index_dir=idx_dir)
    assert res_before and res_before[0]["backend"] == "dense+bm25"

    # Rewrite the manifest's model in place (simulating a stale index surviving a model
    # change) -- everything else (entries/dense.npy/dim) stays internally consistent.
    import json

    manifest_path = os.path.join(idx_dir, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        on_disk = json.load(fh)
    on_disk["model"] = "some/other-model-v2"
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(on_disk, fh)

    res_after = R.recall("formula dependency graph columnar storage", k=5, memory_dir=md, index_dir=idx_dir)
    assert res_after  # BM25 still carries it -- degrades, does not go empty
    assert all(r["backend"] == "bm25" for r in res_after)

    finding = B.check_index_integrity(idx_dir)
    assert finding is not None
    assert "some/other-model-v2" in finding  # names the STALE model
    assert real_model in finding  # names the CONFIGURED model
    assert "rebuild" in finding.lower()  # remediation

    # Doctor-visible wiring: session_start's index_integrity_producer surfaces the same
    # finding verbatim (already-wired producer; verify it holds for this NEW case too).
    from memory import session_start as S

    monkeypatch.setattr(B, "default_index_dir", lambda memory_dir: idx_dir)
    out = S.index_integrity_producer(md, "repo")
    assert out is not None
    assert "some/other-model-v2" in out


# --------------------------------------------------------------------------- #
# 1-hop graph expansion (GRA-1) — BM25-only, links.json persisted by build_index
# --------------------------------------------------------------------------- #
def _write_linked_corpus(memory_dir: str, items: dict) -> None:
    """items: fname -> (description, body). Bodies may carry [[wikilinks]]."""
    os.makedirs(memory_dir, exist_ok=True)
    for fname, (desc, body) in items.items():
        with open(os.path.join(memory_dir, fname), "w", encoding="utf-8") as fh:
            fh.write(_mem(fname[:-3], desc, body))


# auth_flow links [[deploy_runbook]]; deploy_runbook shares ZERO tokens with the oauth
# query — only the graph edge can surface it. Fillers keep BM25 IDF sane on a tiny corpus.
_LINKED_CORPUS = {
    "auth_flow.md": (
        "oauth token refresh flow for the api gateway",
        "details\n\nRelated: [[deploy_runbook]]\n",
    ),
    "deploy_runbook.md": ("kubernetes helm chart rollout steps", "body"),
    "excel_header.md": (_CORPUS["excel_header.md"], "body"),
    "canvas_pdf.md": (_CORPUS["canvas_pdf.md"], "body"),
    "formula_graph.md": (_CORPUS["formula_graph.md"], "body"),
}
_OAUTH_QUERY = "how does the oauth token refresh flow work"


def test_graph_expansion_surfaces_lexically_distant_neighbor(tmp_path, monkeypatch):
    """The acceptance case: A links [[B]], B shares no tokens with a query hitting A ->
    B still lands in top-k, marked via=graph, and format_results renders ' (linked)'."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_linked_corpus(md, _LINKED_CORPUS)
    B.build_index(md, idx)

    res = R.recall(_OAUTH_QUERY, k=5, memory_dir=md, index_dir=idx)
    by_name = {r["name"]: r for r in res}
    assert "auth_flow" in by_name and by_name["auth_flow"]["via"] == "rank"
    assert "deploy_runbook" in by_name  # lexically distant — only the edge got it here
    assert by_name["deploy_runbook"]["via"] == "graph"
    # the seed outranks its 0.5x-discounted neighbor
    names = [r["name"] for r in res]
    assert names.index("auth_flow") < names.index("deploy_runbook")

    out = R.format_results(res)
    for line in out.splitlines():
        if "deploy_runbook" in line:
            assert line.endswith(" (linked)")
        elif "auth_flow" in line:
            assert "(linked)" not in line


def test_graph_expansion_pulls_inbound_neighbors_too(tmp_path, monkeypatch):
    """Expansion unions BOTH directions: a query hitting deploy_runbook (the link TARGET)
    surfaces auth_flow through its inbound edge."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_linked_corpus(md, _LINKED_CORPUS)
    B.build_index(md, idx)

    res = R.recall("kubernetes helm chart rollout steps", k=5, memory_dir=md, index_dir=idx)
    by_name = {r["name"]: r for r in res}
    assert "deploy_runbook" in by_name and by_name["deploy_runbook"]["via"] == "rank"
    assert "auth_flow" in by_name and by_name["auth_flow"]["via"] == "graph"


def test_graph_expansion_noop_when_no_edges(tmp_path, monkeypatch):
    """A corpus with zero wikilinks must rank byte-identically with expansion enabled vs
    force-disabled (HIPPO_GRAPH_SEEDS=0) — the expansion path may only ever ADD."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _CORPUS)  # bodies are plain "body" — no [[links]], so links.json is edge-free
    B.build_index(md, idx)

    query = "which reranker do we use for search results"
    monkeypatch.setenv("HIPPO_GRAPH_SEEDS", "0")
    disabled = R.recall(query, k=5, memory_dir=md, index_dir=idx)
    monkeypatch.delenv("HIPPO_GRAPH_SEEDS")
    enabled = R.recall(query, k=5, memory_dir=md, index_dir=idx)
    assert enabled == disabled  # full dict equality: names, order, scores, via labels
    assert enabled and all(r["via"] == "rank" for r in enabled)
    assert "(linked)" not in R.format_results(enabled)


def test_graph_expansion_never_resurrects_old_invalidated(tmp_path, monkeypatch):
    """'old'-invalidated neighbors stay display-filtered — the graph must not become a
    side door around soft-invalidation."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    items = dict(_LINKED_CORPUS)
    _write_linked_corpus(md, items)
    # Stamp deploy_runbook (the neighbor) old-invalidated via top-level frontmatter, the
    # same field build_index extracts for organic candidates.
    runbook = os.path.join(md, "deploy_runbook.md")
    with open(runbook, "r", encoding="utf-8") as fh:
        text = fh.read()
    text = text.replace("type: project\n", f"type: project\ninvalid_after: {_iso_days_ago(400)}\n")
    with open(runbook, "w", encoding="utf-8") as fh:
        fh.write(text)
    B.build_index(md, idx)

    res = R.recall(_OAUTH_QUERY, k=5, memory_dir=md, index_dir=idx)
    names = [r["name"] for r in res]
    assert "auth_flow" in names
    assert "deploy_runbook" not in names  # injected, then display-filtered like any organic "old"


def test_graph_expansion_keeps_higher_organic_score(tmp_path, monkeypatch):
    """A neighbor that ALREADY ranks organically above the discounted injection keeps its
    organic tuple — same position, via=rank, no downgrade to a graph label."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    # Both docs match the query hard -> the neighbor's organic RRF score (rank 1 or 2,
    # >= 1/62) strictly beats the 0.5x-discounted injection (<= 1/122).
    _write_linked_corpus(
        md,
        {
            "auth_flow.md": (
                "oauth token refresh flow for the api gateway",
                "details\n\nRelated: [[token_rotation]]\n",
            ),
            "token_rotation.md": ("oauth token refresh rotation policy", "body"),
            "excel_header.md": (_CORPUS["excel_header.md"], "body"),
            "canvas_pdf.md": (_CORPUS["canvas_pdf.md"], "body"),
        },
    )
    B.build_index(md, idx)

    res = R.recall(_OAUTH_QUERY, k=4, memory_dir=md, index_dir=idx)
    by_name = {r["name"]: r for r in res}
    assert "token_rotation" in by_name
    assert by_name["token_rotation"]["via"] == "rank"  # organic win — no graph relabel
    # organic top-2, ahead of everything the graph could have injected it at
    assert [r["name"] for r in res].index("token_rotation") < 2


def test_graph_expansion_seed_count_env_override(tmp_path, monkeypatch):
    """HIPPO_GRAPH_SEEDS changes how deep the seed window reaches: a neighbor linked only
    from the #3-ranked hit appears at the default (3) but not at 1."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    # Strictly nested token overlap -> deterministic BM25 order x > y > z for the query;
    # only z (rank 3) links to the lexically-distant neighbor.
    _write_linked_corpus(
        md,
        {
            "x.md": ("alpha beta gamma delta topic", "body"),
            "y.md": ("alpha beta gamma other topic", "body"),
            "z.md": ("alpha beta unrelated topic", "Related: [[hidden_gem]]\n"),
            "hidden_gem.md": ("kubernetes helm chart rollout steps", "body"),
            "filler.md": (_CORPUS["canvas_pdf.md"], "body"),
        },
    )
    B.build_index(md, idx)

    query = "alpha beta gamma delta"
    monkeypatch.setenv("HIPPO_GRAPH_SEEDS", "1")
    narrow = [r["name"] for r in R.recall(query, k=5, memory_dir=md, index_dir=idx)]
    assert "hidden_gem" not in narrow  # z is outside the 1-seed window

    monkeypatch.delenv("HIPPO_GRAPH_SEEDS")  # default 3 seeds reaches z
    wide = [r["name"] for r in R.recall(query, k=5, memory_dir=md, index_dir=idx)]
    assert "hidden_gem" in wide


def test_graph_expansion_skipped_for_in_memory_index_without_dirs(tmp_path, monkeypatch):
    """A caller-supplied LoadedIndex with no dirs (eval self_recall probes, hermetic tests)
    gets NO expansion — no index_dir is resolvable, so the edge cache is never consulted."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_linked_corpus(md, _LINKED_CORPUS)
    B.build_index(md, idx)
    index = B.load_index(idx)

    res = R.recall(_OAUTH_QUERY, k=5, index=index)
    names = [r["name"] for r in res]
    assert "auth_flow" in names
    assert "deploy_runbook" not in names  # edge exists on disk, but no dirs -> no expansion
    assert all(r["via"] == "rank" for r in res)


# --------------------------------------------------------------------------- #
# Fused dense+BM25 recall with a fake embedder
# --------------------------------------------------------------------------- #
def test_recall_fused_dense_and_bm25(tmp_path, monkeypatch):
    emb_docs, emb_query = _fake_embedder(16)
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", emb_docs)
    monkeypatch.setattr(R, "embed_query", emb_query)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _CORPUS)
    B.build_index(md, idx)

    res = R.recall("formula dependency graph columnar storage", k=5, memory_dir=md, index_dir=idx)
    names = [r["name"] for r in res]
    assert "formula_graph" in names
    assert res[0]["backend"] == "dense+bm25"  # both rankers contributed


def test_recall_falls_back_to_bm25_when_dense_query_fails(tmp_path, monkeypatch):
    emb_docs, _ = _fake_embedder(16)
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", emb_docs)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _CORPUS)
    B.build_index(md, idx)  # dense index built

    def boom(text, allow_download=False):
        raise RuntimeError("offline cache miss at query time")

    monkeypatch.setattr(R, "embed_query", boom)  # dense query path dies
    res = R.recall("phase envelope budget", k=5, memory_dir=md, index_dir=idx)
    assert res and all(r["backend"] == "bm25" for r in res)  # degraded, not crashed


# --------------------------------------------------------------------------- #
# Hook output formatting (bounded < 10K)
# --------------------------------------------------------------------------- #
def test_format_results_is_bounded(tmp_path):
    big = [
        {"name": f"m_{i}", "file": f"m_{i}.md", "description": "x" * 5000, "score": 0.1, "backend": "bm25"}
        for i in range(40)
    ]
    out = R.format_results(big, max_chars=9000)
    assert len(out) <= 9000
    assert out.endswith("(truncated)")


def test_format_results_empty_is_empty():
    assert R.format_results([]) == ""


def test_recall_output_bounded_under_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _CORPUS)
    B.build_index(md, idx)
    res = R.recall("reranker budget excel canvas formula", k=10, memory_dir=md, index_dir=idx)
    assert len(R.format_results(res)) <= R._MAX_RECALL_CHARS


# --------------------------------------------------------------------------- #
# BM25-only fallback asserted with fastembed present (importorskip)
# --------------------------------------------------------------------------- #
def test_bm25_fallback_path_with_fastembed_installed(tmp_path, monkeypatch):
    import pytest

    pytest.importorskip("fastembed")
    # Even with fastembed installed, forcing dense off must yield a working BM25 recall.
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _CORPUS)
    manifest = B.build_index(md, idx)
    assert manifest["dense_ready"] is False
    res = R.recall("voyage reranker", k=3, memory_dir=md, index_dir=idx)
    assert res and all(r["backend"] == "bm25" for r in res)


# --------------------------------------------------------------------------- #
# git-recent producer (uses the conftest git repo)
# --------------------------------------------------------------------------- #
def test_recent_memories_window(repo, memory_dir):
    write_file(repo, "src/x.py", "x=1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    # Two memories sharing the same (recent) source_commit baseline.
    write_file(
        repo,
        ".claude/memory/m_a.md",
        f'---\nname: m_a\ndescription: "alpha"\nsource_commit: "{c1}"\n---\nbody\n',
    )
    write_file(
        repo,
        ".claude/memory/m_b.md",
        f'---\nname: m_b\ndescription: "beta"\nsource_commit: "{c1}"\n---\nbody\n',
    )
    git_commit(repo, "c2", 1_700_000_100)

    # now just after c1 -> both within a 30-day window.
    recent = R.recent_memories(memory_dir, repo, now=1_700_000_000 + 100, window_days=30)
    names = {r["name"] for r in recent}
    assert {"m_a", "m_b"}.issubset(names)

    # now far in the future -> the 14-day window excludes the old commits (self-suppress).
    none_recent = R.recent_memories(memory_dir, repo, now=1_700_000_000 + 99 * 86400, window_days=14)
    assert none_recent == []


def test_git_recent_producer_self_suppresses(repo, memory_dir, monkeypatch):
    # No memories with a resolvable recent source_commit -> producer returns None.
    monkeypatch.setenv("HIPPO_RECENT_DAYS", "14")
    git_commit(repo, "c1", 1_700_000_000)
    assert R.git_recent_producer(memory_dir, repo) is None


# --------------------------------------------------------------------------- #
# Query hygiene (clean_query) — strip harness envelopes / skip near-empty prompts
# --------------------------------------------------------------------------- #
def test_clean_query_strips_task_notification_envelope():
    raw = (
        "<task-notification>\n<task-id>abc123</task-id>\n"
        "<tool-use-id>toolu_01XYZ</tool-use-id>\n<status>completed</status>\n"
        "</task-notification>"
    )
    assert R.clean_query(raw) == ""  # a pure tool-use envelope -> skip recall (no model load)


def test_clean_query_keeps_real_text_after_envelope():
    raw = "<system-reminder>injected noise</system-reminder> fix the reranker circuit breaker bug"
    out = R.clean_query(raw)
    assert "reranker" in out
    assert "system-reminder" not in out and "noise" not in out


def test_clean_query_skips_near_empty_and_continuations():
    for raw in ("?", "continue", "pls continue", "ok", "drop it", "   ", "option 2", "yes"):
        assert R.clean_query(raw) == "", raw


def test_clean_query_strips_fenced_code_blocks():
    raw = "look at this:\n```python\nimport os\nx = 1\n```\nwhy does excel header parsing fail"
    out = R.clean_query(raw)
    assert "excel" in out and "import os" not in out


def test_clean_query_passes_through_a_real_question():
    raw = "how do we keep the memo writer from timing out under latency"
    assert R.clean_query(raw) == raw


# --------------------------------------------------------------------------- #
# RET-4: fence mining + traceback mining — MINE identifiers instead of deleting them,
# and restrict tag stripping to KNOWN harness tag names.
# --------------------------------------------------------------------------- #
def test_clean_query_mines_symbol_and_file_tokens_from_a_fenced_traceback():
    raw = (
        "why does this keep failing:\n"
        "```\n"
        "Traceback (most recent call last):\n"
        '  File "plugin/memory/recall.py", line 42, in recall\n'
        "    task.add_done_callback(_BG_TASKS.discard)\n"
        "ValueError: bad state\n"
        "```\n"
        "seems related to asyncio.create_task somehow"
    )
    out = R.clean_query(raw)
    # The fence is no longer deleted wholesale -- its identifier-like tokens (symbol, file
    # path, error class) are mined out and appended so they still reach BM25/dense.
    assert "_BG_TASKS" in out or "_bg_tasks" in out.lower()
    assert "plugin/memory/recall.py" in out
    assert "ValueError" in out
    # The prose around the fence survives untouched, same as before this change.
    assert "asyncio.create_task" in out
    assert "Traceback (most recent call last)" not in out  # fence body itself still removed


def test_clean_query_mines_an_unfenced_traceback_line():
    # Roadmap: "give un-fenced traceback lines the same treatment if cheap" -- a pasted stack
    # trace often isn't triple-backtick'd; File "...", line N and a trailing SomeError: still
    # carry signal and must survive even with no fence present.
    raw = (
        'File "plugin/memory/build_index.py", line 88, in embed_query\n'
        "RuntimeError: model cache miss\n"
        "why does this happen on a cold machine"
    )
    out = R.clean_query(raw)
    assert "plugin/memory/build_index.py" in out
    assert "RuntimeError" in out
    assert "cold machine" in out


def test_clean_query_caps_mined_tokens():
    # _MAX_MINED_TOKENS (module constant) bounds how many mined identifiers get appended --
    # a huge fence must not blow the query out to an unbounded size.
    fence_lines = "\n".join(f"module_{i}.attr_name_{i}" for i in range(30))
    raw = f"why is this broken:\n```\n{fence_lines}\n```\nplease help"
    out = R.clean_query(raw)
    mined_count = sum(1 for i in range(30) if f"module_{i}.attr_name_{i}" in out)
    assert mined_count <= R._MAX_MINED_TOKENS


def test_clean_query_known_harness_tags_still_stripped():
    raw = (
        "<system-reminder>ignore this</system-reminder>"
        "<task-notification><tool-use-id>x</tool-use-id></task-notification>"
        "<local-command-stdout>noise</local-command-stdout>"
        "<local-command-caveat>careful</local-command-caveat>"
        "<command-name>foo</command-name><command-message>bar</command-message>"
        "<command-args/>"
        " fix the reranker circuit breaker bug"
    )
    out = R.clean_query(raw)
    assert "reranker" in out
    for marker in (
        "system-reminder", "task-notification", "local-command-stdout",
        "local-command-caveat", "command-name", "command-message", "command-args",
    ):
        assert marker not in out


def test_clean_query_unknown_tags_survive_as_signal():
    # RET-4: only KNOWN harness tag names get deleted -- <lambda>, Vec<String>, <module> are
    # real symbol-shaped signal in a debugging prompt and must NOT be silently discarded.
    raw = "why does <lambda> at Vec<String> break, also <module> level code"
    out = R.clean_query(raw)
    assert "<lambda>" in out
    assert "Vec<String>" in out
    assert "<module>" in out


def test_clean_query_continuation_skip_unchanged_with_mining():
    # A terse continuation must still skip recall entirely -- fence mining must not
    # accidentally manufacture enough "content" out of nothing to defeat the gate.
    for raw in ("?", "continue", "pls continue", "ok", "drop it", "   ", "option 2", "yes"):
        assert R.clean_query(raw) == "", raw


def test_recall_acceptance_traceback_prompt_recalls_memory_citing_same_symbol(
    tmp_path, monkeypatch
):
    # Acceptance criterion (RET-4): a traceback-bearing prompt recalls the memory citing the
    # same symbol/file. Hermetic corpus test THROUGH recall() (clean_query -> recall), not
    # just a clean_query unit test -- exercises the full hot-path contract end to end.
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    corpus = {
        "anchor_fire_and_forget_tasks.md": (
            "asyncio.create_task returns a Task the event loop only weakly references -- "
            "an unanchored fire and forget task can be garbage collected mid execution "
            "unless anchored on a module level _BG_TASKS set with a done callback"
        ),
        "excel_header.md": "excel parser llm header rescue for non canonical column layouts",
        "canvas_pdf.md": "canvas pdf export two pass gotenberg pypdf footnote marker",
    }
    _write_corpus(md, corpus)
    B.build_index(md, idx)

    raw = (
        "our background job keeps silently dying, here's the trace:\n"
        "```\n"
        "Traceback (most recent call last):\n"
        '  File "worker.py", line 12, in <module>\n'
        "    task.add_done_callback(_BG_TASKS.discard)\n"
        "```\n"
        "what's going on"
    )
    cleaned = R.clean_query(raw)
    assert cleaned  # not skipped -- carries real retrieval intent once mined
    res = R.recall(cleaned, k=5, memory_dir=md, index_dir=idx)
    names = [r["name"] for r in res]
    assert "anchor_fire_and_forget_tasks" in names


# --------------------------------------------------------------------------- #
# Floor-dedup (display layer) — drop always-loaded floor members from recall output
# --------------------------------------------------------------------------- #
def _write_floor(memory_dir: str, floor_names) -> None:
    lines = ["# Floor", "## User"]
    for n in floor_names:
        lines.append(f"- [{n}]({n}.md) — pinned in the always-loaded floor")
    lines.append("## Recalled on demand")
    with open(os.path.join(memory_dir, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def test_floor_memory_names_parses_floor_pointers(tmp_path):
    from memory.lint_floor import floor_memory_names

    md = str(tmp_path / "memory")
    os.makedirs(md)
    _write_floor(md, ["feedback_no_backward_compat", "user_role"])
    assert floor_memory_names(md) == {"feedback_no_backward_compat", "user_role"}


def test_main_floor_dedup_drops_always_loaded_members_and_tops_off(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(
        md,
        {
            "feedback_no_backward_compat.md": "no backward compat one path refactor single pr",
            "reranker_voyage.md": "voyage rerank cross encoder primary reranker bm25 hybrid fallback",
        },
    )
    _write_floor(md, ["feedback_no_backward_compat"])  # this memory is ALREADY always-loaded
    B.build_index(md, idx)

    rc = R.main(["no backward compat refactor voyage reranker", "--memory-dir", md, "--index-dir", idx])
    assert rc == 0
    out = capsys.readouterr().out
    assert "feedback_no_backward_compat" not in out  # dropped — it's in the floor already
    assert "reranker_voyage" in out  # non-floor memory still surfaces (topped off to k)


def test_main_skips_recall_entirely_on_envelope_query(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _CORPUS)
    B.build_index(md, idx)
    rc = R.main(
        [
            "<task-notification><tool-use-id>toolu_x</tool-use-id></task-notification>",
            "--memory-dir",
            md,
            "--index-dir",
            idx,
        ]
    )
    assert rc == 0
    assert capsys.readouterr().out.strip() == ""  # hygiene skipped recall — nothing injected


# --------------------------------------------------------------------------- #
# Mid-session corpus drift (COR-4) — edits/deletes invisible until next SessionStart
# --------------------------------------------------------------------------- #
def test_recall_drops_deleted_memory_same_session(tmp_path, monkeypatch):
    """A memory deleted from disk AFTER the index was built must never surface again,
    even though the persisted index still has a row for it."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _CORPUS)
    B.build_index(md, idx)

    query = "which reranker do we use for search results"
    before = [r["name"] for r in R.recall(query, k=5, memory_dir=md, index_dir=idx)]
    assert "reranker_voyage" in before

    os.remove(os.path.join(md, "reranker_voyage.md"))  # delete WITHOUT rebuilding the index

    after = [r["name"] for r in R.recall(query, k=5, memory_dir=md, index_dir=idx)]
    assert "reranker_voyage" not in after


def test_recall_patches_bm25_on_edited_description(tmp_path, monkeypatch):
    """A description edited on disk (index NOT rebuilt) must surface via BM25 for a query
    that only matches the NEW text — proving the live token patch, not just stale reuse."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"canvas_pdf.md": _CORPUS["canvas_pdf.md"]})
    B.build_index(md, idx)

    query = "kubernetes helm chart deployment rollout"
    assert R.recall(query, k=5, memory_dir=md, index_dir=idx) == []  # doesn't match yet

    with open(os.path.join(md, "canvas_pdf.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem("canvas_pdf", "kubernetes helm chart deployment rollout strategy"))

    res = R.recall(query, k=5, memory_dir=md, index_dir=idx)
    names = [r["name"] for r in res]
    assert "canvas_pdf" in names
    hit = next(r for r in res if r["name"] == "canvas_pdf")
    assert "kubernetes" in hit["description"]  # displayed text is the FRESH description too


def test_recall_drift_check_stays_fast_on_larger_corpus(tmp_path, monkeypatch):
    """Timing guard: the per-query stat+reread drift check must not blow up the hot path."""
    import time

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    corpus = {
        f"synthetic_{i:03d}.md": f"synthetic memory number {i} about topic area {i % 7} testing"
        for i in range(60)
    }
    _write_corpus(md, corpus)
    B.build_index(md, idx)

    start = time.monotonic()
    res = R.recall("synthetic memory topic area testing", k=10, memory_dir=md, index_dir=idx)
    elapsed = time.monotonic() - start

    assert res
    assert elapsed < 2.0  # generous bound; drift check is a handful of stats+reads, not ML


# --------------------------------------------------------------------------- #
# RET-3: Unicode/multilingual retrieval — BM25 acceptance + clean_query skip-gate
# --------------------------------------------------------------------------- #
_JAPANESE_CORPUS = {
    "tokyo_weather.md": "東京の天気予報は明日晴れです 週末も晴天が続く見込み",  # Tokyo weather forecast
    "osaka_food.md": "大阪のたこ焼きは観光客に人気の食べ物です 道頓堀で食べられる",  # Osaka takoyaki
    "kyoto_temple.md": "京都の清水寺は紅葉の名所として有名です 秋の観光シーズン",  # Kyoto temple
}

_RUSSIAN_CORPUS = {
    "quarterly_report.md": "квартальный отчёт по продажам показывает рост выручки в этом году",
    "server_migration.md": "миграция сервера на новую инфраструктуру завершена успешно вчера",
    "team_meeting.md": "еженедельная встреча команды назначена на вторник утром в офисе",
}


def test_recall_bm25_japanese_corpus_returns_relevant_hit(tmp_path, monkeypatch):
    """Acceptance: a Japanese corpus + same-language query must return the RELEVANT memory via
    BM25 -- pre-RET-3, the ASCII-only tokenizer produced zero tokens for this text entirely,
    so BM25 could never match anything (0 shared tokens with an empty corpus vocabulary)."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _JAPANESE_CORPUS)
    B.build_index(md, idx)

    res = R.recall("東京の天気はどうですか", k=5, memory_dir=md, index_dir=idx)  # "how's Tokyo weather"
    names = [r["name"] for r in res]
    assert "tokyo_weather" in names
    assert all(r["backend"] == "bm25" for r in res)


def test_recall_bm25_russian_corpus_returns_relevant_hit(tmp_path, monkeypatch):
    """Acceptance: same as the Japanese case, for Cyrillic (word-token, not bigram, path)."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _RUSSIAN_CORPUS)
    B.build_index(md, idx)

    res = R.recall("квартальный отчёт по продажам за год", k=5, memory_dir=md, index_dir=idx)
    names = [r["name"] for r in res]
    assert "quarterly_report" in names
    assert all(r["backend"] == "bm25" for r in res)


def test_recall_dense_japanese_corpus_with_fake_embedder(tmp_path, monkeypatch):
    """Dense-path MECHANICS for non-English text, hermetically (fake embedder, not the real
    English-only model -- covers the switch/plumbing, not real multilingual embedding quality,
    per the roadmap's scoping: a real multilingual dense download is explicitly NOT added to
    CI). Confirms the dense path doesn't choke/degrade on non-Latin text end to end."""
    emb_docs, emb_query = _fake_embedder(16)
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", emb_docs)
    monkeypatch.setattr(R, "embed_query", emb_query)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _JAPANESE_CORPUS)
    B.build_index(md, idx)

    res = R.recall("東京の天気はどうですか", k=5, memory_dir=md, index_dir=idx)
    names = [r["name"] for r in res]
    assert "tokyo_weather" in names
    assert any(r["backend"] in ("dense", "dense+bm25") for r in res)


def test_clean_query_never_skips_substantive_japanese_prompt():
    """Acceptance: a substantive non-English prompt must NEVER trip the continuation/min-content
    skip -- pre-RET-3 this ALWAYS returned "" for any Japanese/Russian text (zero ASCII tokens),
    silently disabling recall for an entire language regardless of prompt substance."""
    raw = "東京の天気はどうですか、明日は晴れますか"  # "how's Tokyo weather, will it be sunny tomorrow"
    out = R.clean_query(raw)
    assert out != ""
    assert out == raw  # nothing to strip/mine here -- passes through verbatim like English does


def test_clean_query_never_skips_substantive_russian_prompt():
    raw = "почему сервер падает при высокой нагрузке в пиковые часы"  # "why does the server crash under high load at peak hours"
    out = R.clean_query(raw)
    assert out != ""
    assert out == raw


def test_clean_query_cjk_three_char_prompt_passes_via_bigrams():
    """Edge case named explicitly in the roadmap: a 3-char CJK prompt must pass the min-content
    gate via bigrams (2 bigrams over 3 chars == 2 tokens == _MIN_CONTENT_TOKENS)."""
    assert R.clean_query("東京都") != ""  # "Tokyo" (3 chars) -> ["東京","京都"] -> 2 tokens -> passes


def test_clean_query_cjk_two_char_prompt_treated_like_a_single_english_word():
    """A 2-char CJK prompt yields only ONE bigram -- the same "too terse" treatment a single
    English word gets today (not a regression; applying the existing rule uniformly)."""
    assert R.clean_query("継続") == ""  # "continue" (2 chars) -> 1 bigram -> below the floor
    assert R.clean_query("continue") == ""  # the existing English precedent, for comparison


def test_clean_query_accented_latin_cafe_round_trip():
    """'café' must survive clean_query's min-content gate whole (not truncated to 'caf' by an
    ASCII-only tokenizer internally used for the gate check)."""
    raw = "where is the café located in the office building"
    assert R.clean_query(raw) == raw


# --------------------------------------------------------------------------- #
# PRF-1: persisted BM25 statistics — fast path (postings) vs from-scratch construction
# --------------------------------------------------------------------------- #
# Deliberately includes a NEGATIVE-IDF corner: "deploy" appears in 4 of 5 docs, so its
# Okapi idf = ln((5-4+0.5)/(4+0.5)) = ln(1.5/4.5) < 0 and must be floored to
# epsilon * average_idf (rank_bm25's exact behavior) rather than left negative.
_PRF1_CORPUS_TOKENS = [
    "zebra deploy canary rollout pager escalation".split(),
    "postgres catalog bucket warehouse lakehouse files".split(),
    "excel header rescue inference column layout".split(),
    "deploy deploy deploy repeated token document".split(),
    "the common token appears in every document deploy".split(),
]


def _prf1_entries() -> list:
    return [
        {"name": f"e{i}", "file": f"e{i}.md", "tokens": toks}
        for i, toks in enumerate(_PRF1_CORPUS_TOKENS)
    ]


def test_bm25_postings_stats_have_negative_idf_floored():
    """The corner case named in the roadmap: a token in most docs gets a NEGATIVE Okapi idf,
    floored to epsilon * average_idf — never left negative in the persisted stats, and
    matching rank_bm25's own floored value exactly (average_idf is computed from the
    PRE-floor sum, so it must not be re-derived from the already-floored dict)."""
    rank_bm25 = pytest.importorskip("rank_bm25")
    stats = B.compute_bm25_stats(_PRF1_CORPUS_TOKENS)
    df_deploy = 4  # appears in 4 of the 5 docs
    unfloored = math.log(len(_PRF1_CORPUS_TOKENS) - df_deploy + 0.5) - math.log(df_deploy + 0.5)
    assert unfloored < 0  # confirms this token really does hit the negative-idf corner
    assert stats["idf"]["deploy"] > 0  # floored, never left negative

    oracle = rank_bm25.BM25Okapi(_PRF1_CORPUS_TOKENS)
    assert stats["idf"]["deploy"] == pytest.approx(oracle.idf["deploy"], abs=1e-9)


@pytest.mark.parametrize(
    "query",
    [
        ["deploy", "canary"],
        ["postgres", "warehouse"],
        ["deploy"],  # high-df token — exercises the negative-idf epsilon floor at query time
        ["nonexistent"],
        ["excel", "deploy", "catalog"],
    ],
)
def test_bm25_rank_fast_path_matches_full_construction_golden(query):
    """Golden equivalence: IDENTICAL ordering AND scores from the postings fast path vs a
    fresh query-time BM25Okapi over the same hermetic corpus (incl. the negative-idf corner)."""
    rank_bm25 = pytest.importorskip("rank_bm25")
    entries = _prf1_entries()
    stats = B.compute_bm25_stats(_PRF1_CORPUS_TOKENS)

    fast = R._bm25_rank(query, entries, stats=stats)

    oracle = rank_bm25.BM25Okapi(_PRF1_CORPUS_TOKENS)
    oracle_scores = oracle.get_scores(query)
    qset = set(query)
    expected = [i for i in range(len(entries)) if qset.intersection(_PRF1_CORPUS_TOKENS[i])]
    expected.sort(key=lambda i: oracle_scores[i], reverse=True)

    assert fast == expected  # identical ORDERING (incl. tie behavior)

    fast_scores = R._bm25_score_via_postings(query, stats, fast)
    for i in fast:
        assert fast_scores[i] == pytest.approx(oracle_scores[i], abs=1e-9)  # identical SCORES


def test_bm25_rank_fast_path_never_constructs_bm25okapi(monkeypatch):
    """Probe: with stats supplied and nothing drift-patched, the fast path must NEVER import/
    construct BM25Okapi (rank_bm25 or the vendored fallback) — monkeypatch both constructors
    to raise, and assert recall still ranks correctly via postings alone."""
    import rank_bm25

    def _boom(*a, **k):
        raise AssertionError("fast path must not construct BM25Okapi")

    monkeypatch.setattr(rank_bm25, "BM25Okapi", _boom)
    from memory._vendor import bm25 as vendored_bm25

    monkeypatch.setattr(vendored_bm25, "BM25Okapi", _boom)

    entries = _prf1_entries()
    stats = B.compute_bm25_stats(_PRF1_CORPUS_TOKENS)
    result = R._bm25_rank(["deploy", "canary"], entries, stats=stats)
    assert result  # still ranks, despite both constructors being landmines
    assert 0 in result  # the zebra_deploy doc matches both query tokens


def test_bm25_rank_without_stats_falls_back_and_matches_golden():
    """The two existing direct-call test sites omit `stats` entirely — confirm that path
    (unchanged full-construction fallback) still matches the golden oracle."""
    rank_bm25 = pytest.importorskip("rank_bm25")
    entries = _prf1_entries()
    query = ["deploy", "canary"]
    fast = R._bm25_rank(query, entries)  # no stats kwarg -> fallback path

    oracle = rank_bm25.BM25Okapi(_PRF1_CORPUS_TOKENS)
    oracle_scores = oracle.get_scores(query)
    qset = set(query)
    expected = [i for i in range(len(entries)) if qset.intersection(_PRF1_CORPUS_TOKENS[i])]
    expected.sort(key=lambda i: oracle_scores[i], reverse=True)
    assert fast == expected


def test_bm25_stats_survive_manifest_round_trip(tmp_path, monkeypatch):
    """Build persists the "bm25" block; loading it back gives byte-for-byte-equivalent stats
    (JSON round-trip: int/float/list/dict only, no drift)."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _CORPUS)
    manifest = B.build_index(md, idx)
    assert "bm25" in manifest
    for key in ("postings", "doc_len", "avgdl", "idf", "k1", "b"):
        assert key in manifest["bm25"]

    loaded = B.load_index(idx)
    assert loaded.manifest["bm25"] == manifest["bm25"]

    # And recall() actually exercises the fast path end-to-end without error.
    res = R.recall("which reranker do we use for search results", k=5, memory_dir=md, index_dir=idx)
    names = [r["name"] for r in res]
    assert "reranker_voyage" in names


def test_recall_rankings_identical_with_and_without_persisted_stats(tmp_path, monkeypatch):
    """Same corpus/query: recall() with the persisted-stats fast path vs the manifest's "bm25"
    block stripped out (forcing the fallback) must produce IDENTICAL rankings and scores."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _CORPUS)
    B.build_index(md, idx)
    query = "excel header inference for weird canvas pdf formula graph reranker"

    with_stats = B.load_index(idx)
    fast_results = R.recall(query, k=10, index=with_stats)

    without_stats = B.load_index(idx)
    without_stats.manifest = dict(without_stats.manifest)
    without_stats.manifest.pop("bm25", None)
    fallback_results = R.recall(query, k=10, index=without_stats)

    assert [r["name"] for r in fast_results] == [r["name"] for r in fallback_results]
    assert [r["score"] for r in fast_results] == [r["score"] for r in fallback_results]


def test_bm25_rank_falls_back_when_drift_patched_entry_matches(tmp_path, monkeypatch):
    """A drift-patched entry (COR-4: fresh tokens the persisted postings don't know about)
    must force the FULL fallback construction for this query, not the stale fast path —
    this is exactly what makes `test_recall_patches_bm25_on_edited_description` keep passing."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"canvas_pdf.md": _CORPUS["canvas_pdf.md"]})
    B.build_index(md, idx)

    query = "kubernetes helm chart deployment rollout"
    # Before the edit: fresh tokens aren't in the corpus at all -> no match, either path.
    assert R.recall(query, k=5, memory_dir=md, index_dir=idx) == []

    with open(os.path.join(md, "canvas_pdf.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem("canvas_pdf", "kubernetes helm chart deployment rollout strategy"))

    # The persisted postings still reflect the OLD description -- only the drift-patch path
    # (which forces the fallback for THIS query) can find the new tokens.
    res = R.recall(query, k=5, memory_dir=md, index_dir=idx)
    assert any(r["name"] == "canvas_pdf" for r in res)


def test_bm25_rank_patched_indices_param_gates_fast_path():
    """Direct unit check on `_bm25_rank`'s new kwarg: a `patched_indices` set intersecting the
    match set must force the fallback path even though `stats` is supplied — proven by a
    scenario where trusting the STALE postings gives a DIFFERENT score than a fresh rebuild
    over the current (patched) tokens, for a doc that matches under BOTH old and new text."""
    entries = _prf1_entries()
    stats = B.compute_bm25_stats(_PRF1_CORPUS_TOKENS)
    query = ["canary"]

    # Entry 0's original tokens have exactly one "canary". Simulate a drift-patch that added
    # several MORE "canary" occurrences (still matches the query under both old and new
    # text, so the match-set filter can't be what distinguishes the two paths) -- the TF (and
    # hence the score) genuinely differs between the stale persisted postings and a fresh
    # rebuild over the patched tokens.
    patched_entries = list(entries)
    patched_entries[0] = dict(entries[0])
    patched_entries[0]["tokens"] = entries[0]["tokens"] + ["canary"] * 5

    # Fast path (no patched_indices) wrongly scores entry 0 from the STALE persisted TF=1
    # postings, ignoring the patched tokens' TF=6 entirely.
    stale_fast = R._bm25_rank(query, patched_entries, stats=stats, patched_indices=set())
    stale_scores = R._bm25_score_via_postings(query, stats, stale_fast)
    # Correctly gated: a full rebuild over the CURRENT (patched) tokens sees TF=6.
    correct = R._bm25_rank(query, patched_entries, stats=stats, patched_indices={0})

    assert 0 in stale_fast and 0 in correct  # matches under both -- score is what must differ
    rank_bm25 = pytest.importorskip("rank_bm25")
    fresh_corpus = [e["tokens"] for e in patched_entries]
    fresh_oracle_score = rank_bm25.BM25Okapi(fresh_corpus).get_scores(query)[0]
    correct_scores = R._bm25_score_via_postings(
        query, B.compute_bm25_stats(fresh_corpus), correct
    )
    # The stale fast path's score (frozen at the old TF=1) must NOT match a fresh rebuild's
    # score (TF=6, and a different avgdl/doc_len too) -- proving the gate is load-bearing.
    assert stale_scores[0] != pytest.approx(fresh_oracle_score, abs=1e-9)
    assert correct_scores[0] == pytest.approx(fresh_oracle_score, abs=1e-9)


# --------------------------------------------------------------------------- #
# RET-2: body-aware indexing — the body backstop actually surfaces body-only facts
# --------------------------------------------------------------------------- #
def _mem_with_body(name: str, description: str, body: str) -> str:
    return f'---\nname: {name}\ndescription: "{description}"\ntype: project\n---\n{body}\n'


def _write_body_corpus(memory_dir: str, items: dict) -> None:
    """``items``: fname -> (description, body)."""
    os.makedirs(memory_dir, exist_ok=True)
    for fname, (desc, body) in items.items():
        with open(os.path.join(memory_dir, fname), "w", encoding="utf-8") as fh:
            fh.write(_mem_with_body(fname[:-3], desc, body))


_DISTINCTIVE_BODY = (
    "## Error signature\n"
    "The exact failure is a zqxwyvutplaceholder timeout raised from the network layer "
    "when the retry budget is exhausted before the handshake completes successfully.\n\n"
    "## Root cause\n"
    "A misconfigured connection pool size caused exhaustion under load during peak traffic "
    "hours across every affected region consistently.\n"
)


def test_recall_acceptance_body_only_fact_retrievable_via_bm25(tmp_path, monkeypatch):
    """The headline RET-2 acceptance test: a GENERIC description gives BM25 nothing to match,
    but a query on a DISTINCTIVE body token (present ONLY in the body, absent from the
    description) must still surface the memory via the bm25_body backstop ranking."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_body_corpus(
        md,
        {
            "incident.md": ("a note about a past incident", _DISTINCTIVE_BODY),
            "other.md": ("an unrelated memory about something else", "unrelated body content here entirely today"),
        },
    )
    B.build_index(md, idx)

    # Sanity: the description alone truly does NOT carry this token (would trivially pass
    # via the description ranking otherwise, proving nothing about the body backstop).
    assert "zqxwyvutplaceholder" not in B.extract_description(
        open(os.path.join(md, "incident.md"), encoding="utf-8").read()
    )

    res = R.recall("zqxwyvutplaceholder timeout", k=5, memory_dir=md, index_dir=idx)
    names = [r["name"] for r in res]
    assert "incident" in names


def test_recall_body_backstop_never_beats_description_hit_at_equal_relevance(tmp_path, monkeypatch):
    """_BODY_RRF_WEIGHT keeps body rankings a BACKSTOP: a query matching one memory's
    DESCRIPTION must still outrank a same-query body-only match on another memory, all else
    equal -- proving description rows stay primary, per the roadmap's design."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_body_corpus(
        md,
        {
            "desc_hit.md": (
                "kubernetes helm chart deployment rollout strategy guidance",
                "unrelated filler content that has nothing to do with the query at all today",
            ),
            "body_hit.md": (
                "a totally unrelated generic memory description here",
                "## Details\nkubernetes helm chart deployment rollout strategy guidance lives here in the body only",
            ),
        },
    )
    B.build_index(md, idx)
    res = R.recall("kubernetes helm chart deployment rollout strategy", k=5, memory_dir=md, index_dir=idx)
    names = [r["name"] for r in res]
    assert names.index("desc_hit") < names.index("body_hit")


def test_recall_body_hit_carries_primary_backend_label(tmp_path, monkeypatch):
    """A body-only hit still reports the description-only backend label ('bm25', not a third
    backend) -- body rankings are a backstop at the display layer too, per the design."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_body_corpus(md, {"incident.md": ("a generic description", _DISTINCTIVE_BODY)})
    B.build_index(md, idx)
    res = R.recall("zqxwyvutplaceholder", k=5, memory_dir=md, index_dir=idx)
    assert res and res[0]["backend"] == "bm25"


def test_bm25_rank_body_maps_chunks_back_to_parent_and_dedupes(tmp_path, monkeypatch):
    """Direct unit check: a memory with MULTIPLE matching body chunks contributes exactly
    ONE entry to the ranking (its best-ranked chunk), never one entry per matching chunk."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    two_chunk_body = (
        "## First\n" + ("shared unique keyword appears here in the first section today " * 3)
        + "\n\n## Second\n" + ("shared unique keyword appears again in the second section too " * 3)
    )
    _write_body_corpus(md, {"a.md": ("generic description", two_chunk_body)})
    B.build_index(md, idx)
    loaded = B.load_index(idx)
    assert len(loaded.body_chunks) == 2  # both sections cleared the min-chars floor

    q_tokens = B.tokenize("shared unique keyword")
    result = R._bm25_rank_body(q_tokens, loaded)
    assert result.count(0) == 1  # entry 0 contributes exactly once, not twice


def test_dense_rank_body_maps_chunks_back_and_dedupes(tmp_path, monkeypatch):
    """Direct unit check on the dense half of the backstop: fake-embedder dense rank over the
    widened matrix, mapped back to parent entries, deduped to best rank."""
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", _fake_embedder(16)[0])
    monkeypatch.setattr(B, "embed_query", _fake_embedder(16)[1])
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_body_corpus(
        md,
        {
            "a.md": ("generic description", _DISTINCTIVE_BODY),
            "b.md": ("another generic description", "short unrelated body content here at all today"),
        },
    )
    B.build_index(md, idx)
    loaded = B.load_index(idx)
    assert loaded.dense_ready is True

    result = R._dense_rank_body("zqxwyvutplaceholder timeout network handshake", loaded)
    # No entry index appears more than once even though "a" may have 2 qualifying chunks.
    assert len(result) == len(set(result))


def test_rrf_fuse_weights_none_is_byte_identical_to_unweighted(tmp_path):
    """weights=None (every existing call site before this item) must reproduce the EXACT
    pre-RET-2 unweighted formula -- a golden byte-for-byte equivalence pin."""
    rankings = [[2, 0, 1], [1, 2, 0]]
    unweighted = R._rrf_fuse(rankings)
    explicit_ones = R._rrf_fuse(rankings, weights=[1.0, 1.0])
    assert unweighted == explicit_ones


def test_rrf_fuse_body_weight_discounts_but_does_not_zero():
    """A body-only ranking must still be ABLE to contribute (nonzero weight), just less than
    a full-weight description ranking for the same rank position."""
    desc_only = R._rrf_fuse([[0]], weights=[1.0])
    body_only = R._rrf_fuse([[0]], weights=[0.5])
    assert body_only[0][1] == pytest.approx(desc_only[0][1] * 0.5)
    assert 0.0 < body_only[0][1] < desc_only[0][1]


def test_body_rrf_weight_env_override(monkeypatch):
    monkeypatch.setenv("HIPPO_BODY_RRF_WEIGHT", "0.25")
    assert R._body_rrf_weight() == pytest.approx(0.25)
    monkeypatch.setenv("HIPPO_BODY_RRF_WEIGHT", "not-a-number")
    assert R._body_rrf_weight() == R._BODY_RRF_WEIGHT  # malformed -> module default
    monkeypatch.delenv("HIPPO_BODY_RRF_WEIGHT", raising=False)
    assert R._body_rrf_weight() == R._BODY_RRF_WEIGHT


def test_graph_expansion_still_operates_on_parent_entries_with_body_chunks_present(tmp_path, monkeypatch):
    """GRA-1 interplay (explicitly called out in the roadmap): 1-hop expansion must keep
    operating purely on parent ENTRY indices even when the corpus has body chunks -- the
    graph never needs to know body chunks exist at all."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_body_corpus(
        md,
        {
            "seed.md": ("seed memory about zzqqcanary widgets", "## Notes\n" + "extra body detail about the seed topic here today " * 3),
            "linked.md": ("a completely unrelated description", "## Notes\n" + "extra body detail about something else entirely today " * 3),
        },
    )
    # Wire an explicit link seed -> linked so expansion has something to pull.
    with open(os.path.join(md, "seed.md"), "a", encoding="utf-8") as fh:
        fh.write("\nSee also [[linked]].\n")
    B.build_index(md, idx)

    res = R.recall("zzqqcanary widgets", k=5, memory_dir=md, index_dir=idx)
    names = [r["name"] for r in res]
    assert "seed" in names
    assert "linked" in names  # pulled in via 1-hop expansion despite unrelated body/description
    linked_hit = next(r for r in res if r["name"] == "linked")
    assert linked_hit["via"] == "graph"


@pytest.mark.network
def test_recall_acceptance_body_only_fact_retrievable_via_dense(tmp_path, monkeypatch):
    """Network-marked dense equivalent of the BM25 body-backstop acceptance test: with the
    REAL fastembed model, a query semantically close to body-only content (paraphrased, not
    a literal token match) still surfaces the memory via the dense_body ranking."""
    pytest.importorskip("fastembed")
    # Honor a caller-provided FASTEMBED_CACHE_PATH (CI's dense lane points this at the
    # actions-restored cache) else this machine's own durable warm cache (mirrors
    # test_real_fastembed_dense_build's precedence, but falls back to the REAL durable dir
    # instead of an empty tmp one — this test needs an ALREADY-warm model, not a fresh
    # download, to stay fast and offline-safe in the dense CI lane and on a warm dev machine).
    cache = os.environ.get("FASTEMBED_CACHE_PATH") or B.durable_fastembed_cache_dir()
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", cache)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_body_corpus(
        md,
        {
            "incident.md": (
                "a short note",
                "## Details\nthe production database connection pool was exhausted because "
                "retries never backed off, causing a cascading outage across every service.",
            ),
            "other.md": ("an unrelated memory", "completely different unrelated topic content here today"),
        },
    )
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    B.build_index(md, idx)
    res = R.recall(
        "why did the database connections run out during retries", k=5, memory_dir=md, index_dir=idx
    )
    names = [r["name"] for r in res]
    assert "incident" in names


# --------------------------------------------------------------------------- #
# RET-1: relevance floor + knee cutoff — "earn every injected token"
# --------------------------------------------------------------------------- #
_RET1_CORPUS = {
    "oauth_refresh.md": "oauth token refresh flow rotates the access token before it expires using the refresh token grant",
    "helm_rollout.md": "kubernetes helm chart deployment rollout strategy for canary releases across regions",
    "bm25_fusion.md": "reciprocal rank fusion combines a lexical bm25 ranking with a dense embedding ranking",
    "git_bisect.md": "git bisect binary searches commit history to find the exact commit that introduced a regression",
    "unicode_nfc.md": "unicode normalization form nfc versus nfd affects whether visually identical strings compare equal",
}

_OFF_TOPIC_PROMPTS = [
    "what's the ideal hydration ratio for pizza dough and how long should it ferment",
    "explain quantum entanglement and Bell's inequality in a physics lecture",
    "which celebrity just announced their engagement this week",
]


def test_dense_floor_env_override_accepted_and_malformed_falls_back(monkeypatch):
    """``HIPPO_DENSE_FLOOR`` overrides the calibrated table for ANY model; a malformed
    value degrades to the table/default rather than raising (recall() must never break over
    a typo'd env var)."""
    monkeypatch.setenv("HIPPO_DENSE_FLOOR", "0.42")
    assert R._dense_floor("BAAI/bge-small-en-v1.5") == 0.42
    assert R._dense_floor("some/other-model") == 0.42  # override wins over EVERY model
    assert R._dense_floor(None) == 0.42

    monkeypatch.setenv("HIPPO_DENSE_FLOOR", "not-a-float")
    assert R._dense_floor("BAAI/bge-small-en-v1.5") == R._DENSE_FLOOR_BY_MODEL["BAAI/bge-small-en-v1.5"]

    monkeypatch.delenv("HIPPO_DENSE_FLOOR", raising=False)
    assert R._dense_floor("BAAI/bge-small-en-v1.5") == R._DENSE_FLOOR_BY_MODEL["BAAI/bge-small-en-v1.5"]
    assert R._dense_floor("an/unknown-model-id") == R._DENSE_FLOOR_DEFAULT  # uncalibrated -> conservative default


def test_dense_floor_zero_override_disables_floor_entirely(monkeypatch):
    """``HIPPO_DENSE_FLOOR=0`` admits every candidate regardless of similarity — the pre-
    RET-1 behavior, available as an explicit opt-out."""
    monkeypatch.setenv("HIPPO_DENSE_FLOOR", "0")
    assert R._dense_floor("BAAI/bge-small-en-v1.5") == 0.0


def test_knee_ratio_env_override_accepted_and_malformed_falls_back(monkeypatch):
    monkeypatch.setenv("HIPPO_KNEE_RATIO", "0.75")
    assert R._knee_ratio() == 0.75
    monkeypatch.setenv("HIPPO_KNEE_RATIO", "garbage")
    assert R._knee_ratio() == R._KNEE_RATIO
    monkeypatch.delenv("HIPPO_KNEE_RATIO", raising=False)
    assert R._knee_ratio() == R._KNEE_RATIO


def test_dense_rank_rows_drops_candidates_below_floor(tmp_path, monkeypatch):
    """Unit check on leg 1: ``_dense_rank_rows`` must never return a row whose cosine
    similarity to the query sits below the calibrated floor for the index's model."""
    emb_docs, emb_query = _fake_embedder(16)
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", emb_docs)
    monkeypatch.setattr(R, "embed_query", emb_query)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _RET1_CORPUS)
    B.build_index(md, idx)
    index = B.load_index(idx)

    # A floor of 1.01 is unreachable for any unit-normalized cosine similarity (max is 1.0)
    # -> every candidate must be dropped, proving the floor is actually applied per-row.
    monkeypatch.setenv("HIPPO_DENSE_FLOOR", "1.01")
    assert R._dense_rank_rows("oauth token refresh", index) == []

    # A floor of -1.0 is below every possible cosine similarity -> nothing is ever dropped;
    # the raw row count must equal the corpus size (a real ordering, not an empty one).
    monkeypatch.setenv("HIPPO_DENSE_FLOOR", "-1.0")
    rows = R._dense_rank_rows("oauth token refresh", index)
    assert len(rows) == len(index.entries)


def test_off_topic_prompt_injects_zero_pointers_bm25_only(tmp_path, monkeypatch):
    """ACCEPTANCE (hermetic): a query sharing NO token with any memory in the corpus, with
    dense disabled, must abstain completely — recall() returns [], not a wasted top-k."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _RET1_CORPUS)
    B.build_index(md, idx)

    for prompt in _OFF_TOPIC_PROMPTS:
        res = R.recall(prompt, k=10, memory_dir=md, index_dir=idx)
        assert res == [], f"expected abstention for {prompt!r}, got {[r['name'] for r in res]}"


def test_hard_skip_when_dense_below_floor_and_bm25_empty(tmp_path, monkeypatch):
    """Unit check on leg 3: force a dense hit that clears NOTHING (an unreachable floor) on a
    query with zero BM25 token overlap either -> recall() must return [], not fall through to
    some partial/garbage result."""
    emb_docs, emb_query = _fake_embedder(16)
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", emb_docs)
    monkeypatch.setattr(R, "embed_query", emb_query)
    monkeypatch.setenv("HIPPO_DENSE_FLOOR", "1.01")  # unreachable -> dense always empty
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _RET1_CORPUS)
    B.build_index(md, idx)

    res = R.recall("zzqqxx nonexistent placeholder gibberish", k=10, memory_dir=md, index_dir=idx)
    assert res == []


def test_knee_cutoff_stops_early_leaving_up_to_k(tmp_path, monkeypatch):
    """Leg 2 acceptance: a corpus with one strong hit and several much-weaker BM25-only
    matches must emit FEWER than k results once the score ratio falls below the knee -- "up
    to k", not always exactly k."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    # "alpha" alone overlaps every doc (weak, shared token); "beta gamma delta epsilon
    # zeta" all in doc 1 only -> doc1's BM25 score towers over the rest, which share just
    # the one common token "alpha" -- a textbook knee.
    _write_corpus(
        md,
        {
            "strong.md": "alpha beta gamma delta epsilon zeta",
            "weak1.md": "alpha unrelated topic one here today",
            "weak2.md": "alpha unrelated topic two here today",
            "weak3.md": "alpha unrelated topic three here today",
            "weak4.md": "alpha unrelated topic four here today",
        },
    )
    B.build_index(md, idx)
    res = R.recall("alpha beta gamma delta epsilon zeta", k=10, memory_dir=md, index_dir=idx)
    names = [r["name"] for r in res]
    assert names[0] == "strong"
    assert len(res) < 10  # knee stopped emission well short of k -- "up to k"


def test_knee_ratio_zero_disables_cutoff(tmp_path, monkeypatch):
    """``HIPPO_KNEE_RATIO=0`` must restore the pre-RET-1 "always fill to k" behavior on
    the SAME corpus the knee test above proves stops early with the default ratio."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_KNEE_RATIO", "0")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(
        md,
        {
            "strong.md": "alpha beta gamma delta epsilon zeta",
            "weak1.md": "alpha unrelated topic one here today",
            "weak2.md": "alpha unrelated topic two here today",
            "weak3.md": "alpha unrelated topic three here today",
            "weak4.md": "alpha unrelated topic four here today",
        },
    )
    B.build_index(md, idx)
    res = R.recall("alpha beta gamma delta epsilon zeta", k=10, memory_dir=md, index_dir=idx)
    assert len(res) == 5  # every match-set doc emitted -- knee disabled, "up to k" -> "= k"


def test_knee_cutoff_exempts_invalidation_demoted_entry_still_recallable(tmp_path, monkeypatch):
    """The knee must judge PRIMARY relevance, not the freshness-demotion multiplier -- a
    recently-invalidated memory can still legitimately drop out of a SMALL top-k (real
    demotion), but must remain reachable at a larger k rather than being knee-cut on the
    artificial score gap the x0.5 penalty alone manufactures (see recall()'s
    `primary_relevance` construction)."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx_dir = str(tmp_path / ".memory-index")
    words = ["alpha", "beta", "gamma", "delta", "epsilon"]
    _write_corpus(md, {f"e{i}.md": " ".join(words[:i]) for i in range(1, 6)})
    B.build_index(md, idx_dir)
    index = B.load_index(idx_dir)
    query = " ".join(words)

    for e in index.entries:
        if e["name"] == "e3":
            e["invalid_after"] = _iso_days_ago(5)

    wide = [r["name"] for r in R.recall(query, k=10, index=index)]
    assert "e3" in wide  # demoted, not knee-cut out of existence


@pytest.mark.network
def test_off_topic_prompt_injects_zero_pointers_dense(tmp_path, monkeypatch):
    """ACCEPTANCE (network): with the REAL fastembed model, a clearly off-topic prompt must
    clear NEITHER the dense floor NOR any BM25 token overlap -> recall() abstains."""
    pytest.importorskip("fastembed")
    cache = os.environ.get("FASTEMBED_CACHE_PATH") or B.durable_fastembed_cache_dir()
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", cache)
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _RET1_CORPUS)
    B.build_index(md, idx)

    for prompt in _OFF_TOPIC_PROMPTS:
        res = R.recall(prompt, k=10, memory_dir=md, index_dir=idx)
        assert res == [], f"expected abstention for {prompt!r}, got {[r['name'] for r in res]}"


@pytest.mark.network
def test_on_topic_prompt_still_finds_hit_with_dense_floor_active(tmp_path, monkeypatch):
    """Companion to the off-topic acceptance test: the SAME floor that abstains on nonsense
    must not swallow a real on-topic paraphrase — the conservative "admit when in doubt"
    calibration."""
    pytest.importorskip("fastembed")
    cache = os.environ.get("FASTEMBED_CACHE_PATH") or B.durable_fastembed_cache_dir()
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", cache)
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _RET1_CORPUS)
    B.build_index(md, idx)

    res = R.recall(
        "how does the token get rotated before it expires without logging back in",
        k=10,
        memory_dir=md,
        index_dir=idx,
    )
    assert any(r["name"] == "oauth_refresh" for r in res)


# --------------------------------------------------------------------------- #
# COR-7: the hook path never serves a schema-stale index — load_index treats it
# as absent and the implicit BM25-only build replaces it in place.
# --------------------------------------------------------------------------- #
def test_recall_rebuilds_and_serves_results_on_schema_stale_index(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"reranker.md": "voyage reranker cross encoder ordering"})
    B.build_index(md, idx)

    monkeypatch.setattr(B, "SCHEMA_VERSION", B.SCHEMA_VERSION + 1)
    res = R.recall("voyage reranker cross encoder", memory_dir=md, index_dir=idx)
    assert any(r["name"] == "reranker" for r in res)  # rebuilt, not silently empty
    # The stale manifest was REPLACED at the current (bumped) version by _ensure_index's
    # implicit build — the next load serves it without another rebuild.
    import json as _json

    with open(os.path.join(idx, "manifest.json"), "r", encoding="utf-8") as fh:
        assert _json.load(fh)["schema_version"] == B.SCHEMA_VERSION
    assert B.load_index(idx) is not None
