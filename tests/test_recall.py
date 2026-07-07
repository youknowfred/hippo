"""Tests for memory/recall.py — fused recall + hook formatting + git-recent.

Hermetic: tmp memory dir + tmp index dir; dense exercised with a deterministic FAKE
embedder. The git-recent producer test uses the conftest git repo fixtures.
"""

from __future__ import annotations

import os

import numpy as np

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
    def _vec(text: str):
        v = np.zeros(dim, dtype="float32")
        for tok in B.tokenize(text):
            v[hash(tok) % dim] += 1.0
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _CORPUS)
    B.build_index(md, idx)

    res = R.recall("which reranker do we use for search results", k=5, memory_dir=md, index_dir=idx)
    names = [r["name"] for r in res]
    assert "reranker_voyage" in names
    assert all(r["backend"] == "bm25" for r in res)


def test_recall_empty_query_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _CORPUS)
    B.build_index(md, idx)
    assert R.recall("zzqq xyzzy nonexistent qwertyuiop", k=5, memory_dir=md, index_dir=idx) == []


def test_recall_uses_stored_description_not_doc_text_split(tmp_path, monkeypatch):
    # The recalled description comes from the stored field, robust to any '. ' in the name.
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {"excel_header.md": _CORPUS["excel_header.md"]})
    B.build_index(md, idx)
    res = R.recall("excel header inference", k=1, memory_dir=md, index_dir=idx)
    assert res and res[0]["description"] == _CORPUS["excel_header.md"]


def test_recall_builds_index_on_demand_bm25(tmp_path, monkeypatch):
    # Index not pre-built; recall should build a BM25 view implicitly (no dense download).
    monkeypatch.delenv("MEMOBOT_DISABLE_DENSE", raising=False)
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.delenv("MEMOBOT_DISABLE_DENSE", raising=False)
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
    monkeypatch.delenv("MEMOBOT_DISABLE_DENSE", raising=False)
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    force-disabled (MEMOBOT_GRAPH_SEEDS=0) — the expansion path may only ever ADD."""
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, _CORPUS)  # bodies are plain "body" — no [[links]], so links.json is edge-free
    B.build_index(md, idx)

    query = "which reranker do we use for search results"
    monkeypatch.setenv("MEMOBOT_GRAPH_SEEDS", "0")
    disabled = R.recall(query, k=5, memory_dir=md, index_dir=idx)
    monkeypatch.delenv("MEMOBOT_GRAPH_SEEDS")
    enabled = R.recall(query, k=5, memory_dir=md, index_dir=idx)
    assert enabled == disabled  # full dict equality: names, order, scores, via labels
    assert enabled and all(r["via"] == "rank" for r in enabled)
    assert "(linked)" not in R.format_results(enabled)


def test_graph_expansion_never_resurrects_old_invalidated(tmp_path, monkeypatch):
    """'old'-invalidated neighbors stay display-filtered — the graph must not become a
    side door around soft-invalidation."""
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    """MEMOBOT_GRAPH_SEEDS changes how deep the seed window reaches: a neighbor linked only
    from the #3-ranked hit appears at the default (3) but not at 1."""
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_GRAPH_SEEDS", "1")
    narrow = [r["name"] for r in R.recall(query, k=5, memory_dir=md, index_dir=idx)]
    assert "hidden_gem" not in narrow  # z is outside the 1-seed window

    monkeypatch.delenv("MEMOBOT_GRAPH_SEEDS")  # default 3 seeds reaches z
    wide = [r["name"] for r in R.recall(query, k=5, memory_dir=md, index_dir=idx)]
    assert "hidden_gem" in wide


def test_graph_expansion_skipped_for_in_memory_index_without_dirs(tmp_path, monkeypatch):
    """A caller-supplied LoadedIndex with no dirs (eval self_recall probes, hermetic tests)
    gets NO expansion — no index_dir is resolvable, so the edge cache is never consulted."""
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.delenv("MEMOBOT_DISABLE_DENSE", raising=False)
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
    monkeypatch.delenv("MEMOBOT_DISABLE_DENSE", raising=False)
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_RECENT_DAYS", "14")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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

    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
