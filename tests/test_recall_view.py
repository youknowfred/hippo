"""Tests for memory/recall_view.py — the /hippo:recall read-side verb (INT-1).

Hermetic: tmp memory dir + tmp index dir, dense disabled (BM25-only). The presentation
layer (type / staleness flag / graph neighbors) is exercised both end-to-end over a real
built index and, for the staleness marker, against a synthetic hit so the flag path is
covered without standing up git drift.
"""

from __future__ import annotations

import os

import pytest

from memory import build_index as B
from memory import recall_view as V


def _mem(name: str, description: str, mtype: str = "project", body: str = "body") -> str:
    return f'---\nname: {name}\ndescription: "{description}"\nmetadata:\n  type: {mtype}\n---\n{body}\n'


def _seed(md: str, idx: str, items: dict) -> None:
    os.makedirs(md, exist_ok=True)
    for fname, text in items.items():
        with open(os.path.join(md, fname), "w", encoding="utf-8") as fh:
            fh.write(text)
    B.build_index(md, idx)


@pytest.fixture(autouse=True)
def _bm25_only(monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")


# --------------------------------------------------------------------------- #
# describe() — query the corpus
# --------------------------------------------------------------------------- #
def test_describe_answers_from_corpus(tmp_path):
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _seed(
        md,
        idx,
        {
            "deploy_runbook.md": _mem(
                "deploy_runbook", "how the web service is deployed via the canary lane"
            ),
            "unrelated.md": _mem("unrelated", "the excel parser header rescue heuristics"),
        },
    )
    out = V.describe("how do we deploy the web service", memory_dir=md, index_dir=idx)
    assert "deploy_runbook" in out
    assert "match(es)" in out
    # enriched with type + description
    assert "project" in out
    assert "canary" in out


def test_describe_abstains_on_off_topic_query(tmp_path):
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _seed(md, idx, {"deploy.md": _mem("deploy", "how the web service is deployed")})
    out = V.describe("zzqq xyzzy nonexistent qwertyuiop", memory_dir=md, index_dir=idx)
    assert "Abstention" in out or "cleared the relevance floor" in out
    assert "--list-by-type" in out  # points the reader at the fallback


def test_describe_shows_graph_neighbors(tmp_path):
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _seed(
        md,
        idx,
        {
            "alpha.md": _mem("alpha", "the alpha deploy rollout ordering runbook", body="see [[beta]]"),
            "beta.md": _mem("beta", "the beta canary staging checklist"),
        },
    )
    out_a = V.describe("alpha deploy rollout ordering", memory_dir=md, index_dir=idx)
    assert "→ links to: beta" in out_a
    out_b = V.describe("beta canary staging checklist", memory_dir=md, index_dir=idx)
    assert "← linked from: alpha" in out_b


def test_describe_marks_stale_and_graph_via(monkeypatch, tmp_path):
    md = str(tmp_path / "memory")
    os.makedirs(md)
    (tmp_path / "memory" / "x.md").write_text(_mem("x", "a drifted memory"), encoding="utf-8")
    synthetic = [
        {
            "name": "x",
            "file": os.path.join(md, "x.md"),
            "description": "a drifted memory",
            "score": 0.42,
            "via": "graph",
            "stale_banner": "anchored to abc123; 1 cited file changed since — verify",
        }
    ]
    monkeypatch.setattr(V, "recall", lambda *a, **k: synthetic)
    out = V.describe("anything", memory_dir=md)
    assert "⚠ stale" in out
    assert "via 1-hop link" in out
    assert "relevance 0.420" in out


# --------------------------------------------------------------------------- #
# list_by_type() — the whole corpus grouped by type
# --------------------------------------------------------------------------- #
def test_list_by_type_groups_and_orders(tmp_path):
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _seed(
        md,
        idx,
        {
            "role.md": _mem("role", "the operator role", mtype="user"),
            "pref.md": _mem("pref", "a working preference", mtype="feedback"),
            "proj.md": _mem("proj", "a project fact", mtype="project"),
        },
    )
    out = V.list_by_type(memory_dir=md)
    assert "3 memories across 3 type(s)" in out
    # canonical order: user before feedback before project
    assert out.index("## user") < out.index("## feedback") < out.index("## project")
    assert "role — the operator role" in out


def test_list_by_type_empty_corpus_nudges_init(tmp_path):
    md = str(tmp_path / "memory")
    os.makedirs(md)
    out = V.list_by_type(memory_dir=md)
    assert "/hippo:init" in out


# --------------------------------------------------------------------------- #
# main() — the CLI the skill wraps
# --------------------------------------------------------------------------- #
def test_main_query_mode(tmp_path, capsys):
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _seed(md, idx, {"deploy.md": _mem("deploy", "how the web service is deployed via canary")})
    rc = V.main(["how", "do", "we", "deploy", "--memory-dir", md, "--index-dir", idx])
    assert rc == 0
    assert "deploy" in capsys.readouterr().out


def test_main_list_by_type(tmp_path, capsys):
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _seed(md, idx, {"proj.md": _mem("proj", "a project fact")})
    rc = V.main(["--list-by-type", "--memory-dir", md])
    assert rc == 0
    assert "## project" in capsys.readouterr().out


def test_main_empty_query_is_usage_error(tmp_path, capsys):
    md = str(tmp_path / "memory")
    os.makedirs(md)
    rc = V.main(["--memory-dir", md])
    assert rc == 2
    assert "usage" in capsys.readouterr().out.lower()


def test_main_never_raises(tmp_path):
    # A bogus memory dir must degrade to rc 0 with a message, never raise.
    rc = V.main(["something", "--memory-dir", str(tmp_path / "does-not-exist")])
    assert rc == 0


# --------------------------------------------------------------------------- #
# RCL-5: offline cross-encoder rerank — explicit surfaces only
# --------------------------------------------------------------------------- #
def test_cross_encoder_rerank_degrades_to_unreranked_order_without_model():
    """No cached cross-encoder model (the common case, hermetically) -> the ORIGINAL
    order, never an error -- the hermetic suite never downloads anything."""
    hits = [
        {"name": "a", "description": "alpha", "corpus": None},
        {"name": "b", "description": "beta", "corpus": None},
    ]
    assert V._cross_encoder_rerank("some query", hits) == hits


def test_cross_encoder_rerank_noop_with_fewer_than_two_corpus_hits():
    assert V._cross_encoder_rerank("q", []) == []
    one = [{"name": "a", "description": "alpha", "corpus": None}]
    assert V._cross_encoder_rerank("q", one) == one
    only_rule = [{"name": "Deploys", "description": "rule section", "corpus": "rule"}]
    assert V._cross_encoder_rerank("q", only_rule) == only_rule


def test_cross_encoder_rerank_excludes_and_reattaches_rule_pointers(monkeypatch):
    """T2 guard: a rule pointer is excluded from the rerank math and re-attached at the
    tail in its original relative order -- never reordered among corpus hits."""
    hits = [
        {"name": "a", "description": "alpha topic", "corpus": None},
        {"name": "b", "description": "beta topic", "corpus": None},
        {"name": "Deploys", "description": "rule section", "corpus": "rule"},
    ]

    class _FakeCrossEncoder:
        def rerank(self, query, documents, **kwargs):
            # Reverse the natural order: "beta" scores higher than "alpha".
            return [0.9 if "beta" in d else 0.1 for d in documents]

    monkeypatch.setattr(B, "_get_cross_encoder", lambda allow_download: _FakeCrossEncoder())
    out = V._cross_encoder_rerank("beta", hits)
    assert [h["name"] for h in out] == ["b", "a", "Deploys"]


def test_cross_encoder_rerank_never_mutates_score_or_rank(monkeypatch):
    """COR-8: reordering must never fabricate/overwrite a hit's own score -- the cross-
    encoder's output is on a different scale and is never displayed as "relevance"."""
    hits = [
        {"name": "a", "description": "alpha", "corpus": None, "score": 0.111, "rank": 1},
        {"name": "b", "description": "beta", "corpus": None, "score": 0.099, "rank": 2},
    ]

    class _FakeCrossEncoder:
        def rerank(self, query, documents, **kwargs):
            return [0.1, 0.9]  # flips the order

    monkeypatch.setattr(B, "_get_cross_encoder", lambda allow_download: _FakeCrossEncoder())
    out = V._cross_encoder_rerank("q", hits)
    assert [h["name"] for h in out] == ["b", "a"]
    by_name = {h["name"]: h for h in out}
    assert by_name["a"]["score"] == 0.111  # untouched, still the true fused score
    assert by_name["b"]["score"] == 0.099


def test_cross_encoder_rerank_degrades_on_model_exception(monkeypatch):
    def _boom(allow_download):
        raise RuntimeError("cross-encoder model not cached offline")

    monkeypatch.setattr(B, "_get_cross_encoder", _boom)
    hits = [
        {"name": "a", "description": "alpha", "corpus": None},
        {"name": "b", "description": "beta", "corpus": None},
    ]
    assert V._cross_encoder_rerank("q", hits) == hits


def test_describe_invokes_cross_encoder_rerank(tmp_path, monkeypatch):
    """Integration: describe() actually calls the rerank step, not just defines it."""
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _seed(md, idx, {"deploy.md": _mem("deploy", "how the web service is deployed via canary")})
    calls = []

    def _spy(query, hits):
        calls.append((query, [h["name"] for h in hits]))
        return hits

    monkeypatch.setattr(V, "_cross_encoder_rerank", _spy)
    V.describe("how do we deploy the web service", memory_dir=md, index_dir=idx)
    assert calls and calls[0][0] == "how do we deploy the web service"


def test_describe_never_reranks_on_abstention(tmp_path, monkeypatch):
    """Abstention (no hits) must never even attempt a rerank call."""
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _seed(md, idx, {"deploy.md": _mem("deploy", "how the web service is deployed")})
    calls = []
    monkeypatch.setattr(V, "_cross_encoder_rerank", lambda q, h: calls.append(1) or h)
    V.describe("zzqq xyzzy nonexistent qwertyuiop", memory_dir=md, index_dir=idx)
    assert not calls


@pytest.mark.network
def test_real_cross_encoder_reranks_by_actual_relevance(tmp_path_factory, monkeypatch):
    """The REAL model, network-marked (downloads ~80MB on a cold cache; the hermetic CI
    lane deselects this, the dense lane opts in and restores a cache)."""
    pytest.importorskip("fastembed")
    cache = os.environ.get("FASTEMBED_CACHE_PATH") or str(
        tmp_path_factory.getbasetemp() / "fastembed-cache"
    )
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", cache)
    B._get_cross_encoder(allow_download=True)  # warm (downloads only on a cold cache)

    hits = [
        {"name": "a", "description": "how to deploy a web service via canary rollout", "corpus": None},
        {"name": "b", "description": "quarterly budget spreadsheet for the finance team", "corpus": None},
    ]
    out = V._cross_encoder_rerank("canary deployment rollout steps", hits)
    assert out[0]["name"] == "a"  # the genuinely relevant document must win
