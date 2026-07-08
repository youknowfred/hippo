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
