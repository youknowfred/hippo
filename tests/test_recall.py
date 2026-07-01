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
