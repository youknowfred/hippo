"""GRF-7 — folded-section awareness: a link into a slug that survives inside a declared
fold digest is not edge rot.

Field shape (em-growth-labs, 2026-10-01): the corpus's fold ritual moves an idle lane's
memory into a digest — a ``folded-*-rounds.md`` family digest (the slug survives as a
``### <slug>`` heading) or a ``live-lanes-YYYY-MM-DD.md`` day file (a ``- [<slug>](…)``
row) — and deletes the file, so 533 of 605 audited rot edges were folds. Pinned here:

  - the corpus DECLARES its digests in ``.format`` ``fold_digests``; an undeclared
    corpus is byte-identical to before (every such link stays dangling);
  - a dangling wikilink whose target is a declared digest's heading/row reclassifies to
    the informational ``folded`` class, naming the digest — silent in ``health_line``,
    out of doctor's rot count (noted, never counted), listed by both CLIs;
  - a truly dangling link and an ARCHIVED target stay rot (GRF-6's never-mask rule), a
    heading in an UNDECLARED file folds nothing, typed relations stay loud;
  - folded links are NEVER edges: the graph's adjacency, links.json's edge lists, and
    recall's 1-hop expansion are identical with and without the declaration, so no
    digest becomes a hub;
  - the fold surface rides links.json (schema v6): the cached producer path classifies
    with zero memory-file reads, and a changed declaration reads as a cache miss.
"""

from __future__ import annotations

import builtins
import json
import os

import pytest

from memory import build_index as B
from memory import lint_links as L
from memory import links as LK
from memory import links_graph as LKG
from memory.doctor_checks_env import DoctorContext
from memory.doctor_checks_recall import check_edge_rot
from memory.links import LinkGraph, build_graph, graph_audit, links_cache_fresh, load_edges
from memory.links import main as links_main
from memory.links_graph import parse_fold_surface
from memory.lint_links import boundary_lint, health_line, lint
from memory.lint_links import main as lint_main
from memory.provenance import read_fold_digests
from memory.recall_graph import _expand_neighbors

from .conftest import git_commit, write_file

_GLOBS = ["folded-*.md", "live-lanes-*.md"]


def _mem(name, body, extra_fm=""):
    return f'---\nname: {name}\ndescription: "{name} description"\n{extra_fm}---\n{body}\n'


def _declare(md, globs=_GLOBS, **extra):
    with open(os.path.join(md, ".format"), "w", encoding="utf-8") as fh:
        json.dump({"corpus_format": 5, "fold_digests": globs, **extra}, fh)


def _seed(md):
    """The fixture corpus: a family digest with headings, a day file with rows, a source
    linking into both plus a truly-dangling slug and an archived one, and a NON-digest
    memory carrying a lookalike heading."""
    write_file(
        md,
        "folded-ads-rounds.md",
        _mem(
            "folded-ads-rounds",
            "Members (2): ads-pivot-round · budget-split-round.\n\n"
            "## Members\n\n"
            "### ads-pivot-round\n\nThe pivot's ☠ units.\n\n"
            "### budget_split_round ###\n\nSpelled with underscores and closing hashes.\n\n"
            "### attic-round\n\nThe archived lane also kept a heading here.\n\n"
            "```\n### fenced-round\n```\n",
        ),
    )
    write_file(
        md,
        "live-lanes-2026-09-14.md",
        _mem(
            "live-lanes-2026-09-14",
            "Moved at the read cap.\n\n"
            "- [lane-closed-round](lane-closed-round.md) — 09-14 CLOSED: the pointer as it stood\n"
            "- [lane-moved-round](folded-ads-rounds.md) — the row points at the digest now\n",
        ),
    )
    write_file(
        md,
        "hub.md",
        _mem(
            "hub",
            "See [[ads-pivot-round]], [[budget-split-round]], [[lane-closed-round]], "
            "[[lane-moved-round]], [[truly-missing]], [[attic-round]], [[fenced-round]], "
            "[[notes-only-round]] and [[live-one]].",
        ),
    )
    write_file(md, "live-one.md", _mem("live-one", "a live neighbor, links [[hub]]"))
    write_file(md, "notes.md", _mem("notes", "### notes-only-round\n\nnot a declared digest"))
    write_file(md, os.path.join("archive", "attic-round.md"), _mem("attic-round", "retired"))


@pytest.fixture
def corpus(tmp_path):
    md = str(tmp_path / "proj" / ".claude" / "memory")
    os.makedirs(md)
    _seed(md)
    _declare(md)
    return md


_FOLDED = {
    ("hub", "ads-pivot-round", "folded-ads-rounds"),
    ("hub", "budget-split-round", "folded-ads-rounds"),
    ("hub", "lane-closed-round", "live-lanes-2026-09-14"),
    ("hub", "lane-moved-round", "live-lanes-2026-09-14"),
}
_STILL_DANGLING = {"truly-missing", "attic-round", "fenced-round", "notes-only-round"}


# --------------------------------------------------------------------------- #
# the declaration
# --------------------------------------------------------------------------- #
def test_read_fold_digests_normalizes_and_degrades(tmp_path):
    md = str(tmp_path)
    assert read_fold_digests(md) == []  # no marker at all
    _declare(md, ["./folded-*.md", "folded-*.md", "", 7, "sub/dir-*.md", " live-*.md "])
    assert read_fold_digests(md) == ["folded-*.md", "live-*.md"]
    _declare(md, "folded-*.md")  # a bare string is a one-element list
    assert read_fold_digests(md) == ["folded-*.md"]
    _declare(md, {"not": "a list"})
    assert read_fold_digests(md) == []


def test_parse_fold_surface_reads_h3_headings_and_rows_only():
    text = (
        "## Members\n### one-round\n#### deeper-round\n### two_round ##\n"
        "- [three-round](x.md) — row\n  * [four-round](y.md)\n"
        "see [inline-round](z.md) mid-sentence\n```\n### fenced-round\n```\n### one-round\n"
    )
    assert parse_fold_surface(text) == ["one-round", "two_round", "three-round", "four-round"]


# --------------------------------------------------------------------------- #
# the split
# --------------------------------------------------------------------------- #
def test_declared_folds_classify_folded_rest_stays_dangling(corpus):
    report = lint(corpus)
    assert {(d["file"], d["target"], d["digest"]) for d in report["folded"]} == _FOLDED
    assert {d["target"] for d in report["dangling"]} == _STILL_DANGLING
    line = health_line(report)
    assert line is not None and "truly-missing" in line and "4 dangling" in line
    assert "ads-pivot-round" not in line and "lane-closed-round" not in line


def test_undeclared_corpus_is_unchanged(tmp_path):
    md = str(tmp_path / "proj" / ".claude" / "memory")
    os.makedirs(md)
    _seed(md)
    report = lint(md)
    assert report["folded"] == []
    assert {d["target"] for d in report["dangling"]} == _STILL_DANGLING | {
        t for _s, t, _d in _FOLDED
    }


def test_archived_target_is_never_masked_by_a_fold_heading(corpus):
    audit = graph_audit(corpus)
    rot = {(r["class"], r["target"]) for r in audit["rot"]}
    assert ("archived", "attic-round") in rot
    assert "attic-round" not in {r["target"] for r in audit["folded"]}


def test_typed_relation_into_a_folded_slug_stays_loud(corpus):
    write_file(
        corpus,
        "successor.md",
        _mem("successor", "links [[hub]]", extra_fm="supersedes: [ads-pivot-round]\n"),
    )
    report = lint(corpus)
    assert [(d["relation"], d["target"]) for d in report["typed_dangling"]] == [
        ("supersedes", "ads-pivot-round")
    ]


def test_graph_audit_and_doctor_count_only_true_rot(corpus):
    audit = graph_audit(corpus)
    assert {(r["src"], r["target"], r["digest"]) for r in audit["folded"]} == _FOLDED
    assert all(r["via"] == "wikilink" for r in audit["folded"])
    assert {r["target"] for r in audit["rot"]} == _STILL_DANGLING
    verdict = check_edge_rot(DoctorContext(corpus, os.path.dirname(corpus)))
    assert verdict["status"] == "warn"
    assert "edge rot: 4 edge(s)" in verdict["message"]
    assert "archived=1, dangling=3" in verdict["message"]
    assert "4 folded link(s)" in verdict["message"]  # noted, never counted


def test_doctor_ok_when_only_folds_remain(tmp_path):
    md = str(tmp_path / "proj" / ".claude" / "memory")
    os.makedirs(md)
    write_file(md, "folded-x-rounds.md", _mem("folded-x-rounds", "### gone-round\n\nbody"))
    write_file(md, "hub.md", _mem("hub", "See [[gone-round]] and [[other]]."))
    write_file(md, "other.md", _mem("other", "back to [[hub]]"))
    _declare(md)
    assert health_line(lint(md)) is None
    verdict = check_edge_rot(DoctorContext(md, os.path.dirname(md)))
    assert verdict["status"] == "ok"
    assert "edge rot: 0" in verdict["message"]
    assert "1 folded link(s)" in verdict["message"]


def test_clis_name_each_fold_with_its_digest(corpus, capsys):
    assert links_main(["--memory-dir", corpus, "--audit"]) == 0
    out = capsys.readouterr().out
    assert "folded links (4)" in out
    assert "hub -> lane-moved-round (in live-lanes-2026-09-14)" in out
    assert "hub -> ads-pivot-round (in folded-ads-rounds)" in out
    assert "edge rot (4):" in out
    assert lint_main(["--memory-dir", corpus]) == 0
    out = capsys.readouterr().out
    assert "folded links     : 4" in out
    assert "hub -> [[budget-split-round]] (in folded-ads-rounds)" in out
    assert "dangling targets : 4" in out


# --------------------------------------------------------------------------- #
# never an edge: no digest becomes a hub
# --------------------------------------------------------------------------- #
def test_folded_links_are_never_edges_graph_cache_or_recall(corpus, tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    idx_on, idx_off = str(tmp_path / "idx-on"), str(tmp_path / "idx-off")
    on = LinkGraph(corpus)
    B.build_index(corpus, idx_on)
    edges_on = load_edges(idx_on)
    os.remove(os.path.join(corpus, ".format"))
    off = LinkGraph(corpus)
    B.build_index(corpus, idx_off)
    edges_off = load_edges(idx_off)

    assert on.folded_raw and not off.folded_raw  # the declaration was live on one side
    assert on.adjacency == off.adjacency
    assert on._inbound == off._inbound
    assert on.typed == off.typed
    assert edges_on == edges_off
    for digest in ("folded-ads-rounds", "live-lanes-2026-09-14"):
        assert on.inbound(digest) == set()
        assert edges_on[digest]["in"] == set()

    # Recall's 1-hop expansion seeded on the linking memory pulls in its one REAL
    # neighbor and never a digest.
    entries = [{"name": n} for n in ("hub", "live-one", "folded-ads-rounds", "live-lanes-2026-09-14")]
    penalized = [(0, 1.0, None), (2, 0.2, None), (3, 0.2, None)]
    _out, injected, endorsed = _expand_neighbors(penalized, entries, edges_on)
    assert {entries[i]["name"] for i in injected | endorsed} == {"live-one"}


# --------------------------------------------------------------------------- #
# the cache (links.json v6)
# --------------------------------------------------------------------------- #
def test_fold_surface_rides_links_json(corpus, tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    idx = str(tmp_path / "idx")
    B.build_index(corpus, idx)
    with open(os.path.join(idx, "links.json"), encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["schema_version"] == 6
    assert payload["fold_digests"] == _GLOBS
    assert payload["folded"] == {
        "folded-ads-rounds": ["ads-pivot-round", "budget_split_round", "attic-round"],
        "live-lanes-2026-09-14": ["lane-closed-round", "lane-moved-round"],
    }


def test_cached_producer_classifies_folds_with_zero_memory_file_reads(
    corpus, tmp_path, monkeypatch
):
    idx = str(tmp_path / "idx")
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    assert B.refresh_index(corpus, idx) is not None
    cold = lint(corpus)

    opened: list = []
    real_open = builtins.open

    def spy(file, *a, **k):
        try:
            p = os.fspath(file)
        except TypeError:
            p = ""
        if isinstance(p, str) and p.startswith(corpus):
            opened.append(os.path.basename(p))
        return real_open(file, *a, **k)

    def boom(_md):
        raise AssertionError("cached path must not iterate memory files")

    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(LK, "_iter_memory_files", boom)
    monkeypatch.setattr(LKG, "_iter_memory_files", boom)
    cached = lint(corpus, index_dir=idx)
    line = L.lint_links_producer(corpus, corpus)
    # The one corpus-dir open is the policy marker (the declaration check), never a memory.
    assert [p for p in opened if p.endswith(".md")] == []
    assert set(opened) <= {".format"}
    assert cached["folded"] == cold["folded"] and cached["dangling"] == cold["dangling"]
    assert line == health_line(cold)


def test_changed_declaration_reads_as_a_cache_miss(tmp_path, monkeypatch):
    md = str(tmp_path / "proj" / ".claude" / "memory")
    os.makedirs(md)
    _seed(md)
    idx = str(tmp_path / "idx")
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    B.build_index(md, idx)  # built undeclared
    assert lint(md, index_dir=idx)["folded"] == []
    sigs = LK._stat_signatures(md)
    assert links_cache_fresh(idx, sigs, md)

    _declare(md)  # .format is not a memory file — no stat sig moves
    assert not links_cache_fresh(idx, sigs, md)
    assert links_cache_fresh(idx, sigs)  # recall's edge loader never consults the policy
    assert {(d["file"], d["target"], d["digest"]) for d in lint(md, index_dir=idx)["folded"]} == _FOLDED
    # ...and the no-op refresh re-persists the cache under the new declaration.
    B.refresh_index(md, idx)
    assert links_cache_fresh(idx, LK._stat_signatures(md), md)
    assert build_graph(md, index_dir=idx).folded_raw


def test_v5_cache_reads_as_a_miss(corpus, tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    idx = str(tmp_path / "idx")
    B.build_index(corpus, idx)
    p = os.path.join(idx, "links.json")
    with open(p, encoding="utf-8") as fh:
        payload = json.load(fh)
    payload["schema_version"] = 5
    for k in ("folded", "fold_digests"):
        payload.pop(k)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    assert LK._load_links_payload(idx) is None
    assert lint(corpus, index_dir=idx)["folded"]  # the cold fallback still classifies


# --------------------------------------------------------------------------- #
# the boundary view and the failure shape
# --------------------------------------------------------------------------- #
def test_boundary_lint_stays_fold_blind(tmp_path):
    """PR #67 contract, as for planned/cross-tier: the committed-subset view reports what a
    fresh checkout sees, unclassified."""
    repo = str(tmp_path / "repo")
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    write_file(md, "folded-x-rounds.md", _mem("folded-x-rounds", "### gone-round\n\nbody"))
    write_file(md, "hub.md", _mem("hub", "See [[gone-round]]."))
    _declare(md)
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    git_commit(repo, "seed", 1_700_000_000)
    view = boundary_lint(md, repo)
    assert view["ok"]
    assert [d["target"] for d in view["dangling"]] == ["gone-round"]


def test_failure_shape_carries_the_folded_key(tmp_path):
    report = lint(str(tmp_path / "no" / "such" / "dir"))
    assert report["ok"] is False and report["folded"] == []
