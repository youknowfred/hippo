"""Tests for memory/links.py + lint_links.py — wikilink graph + integrity.

Hermetic: a tmp corpus under tmp_path that reproduces the corpus census cases (a
prefix-stripped slug-mismatch that SHOULD resolve, and genuinely-absent dangling targets).
"""

from __future__ import annotations

import os

from memory import lint_links as L
from memory.links import LinkGraph, build_graph, normalize_slug, parse_wikilinks


def _mem(name: str, body: str) -> str:
    return f'---\nname: {name}\ndescription: "d for {name}"\ntype: project\n---\n{body}\n'


def _corpus(memory_dir: str) -> None:
    os.makedirs(memory_dir, exist_ok=True)
    files = {
        # alpha links out to beta (full stem), gamma-thing (prefix-stripped alias),
        # the exact census slug-mismatch, and two genuinely-absent targets.
        "alpha.md": _mem(
            "alpha",
            "see [[beta]] and [[gamma-thing]] and [[151-avenue-a-is-standard-size]] "
            "and [[ship-roadmap]] and [[totally-absent]]",
        ),
        "beta.md": _mem("beta", "back to [[alpha]]"),
        "feedback_gamma_thing.md": _mem("Gamma Thing", "onward to [[delta]]"),
        "feedback_151_avenue_a_is_standard_size.md": _mem("151-avenue-a", "body"),
        "delta.md": _mem("delta", "no outbound links here"),
    }
    for fname, content in files.items():
        with open(os.path.join(memory_dir, fname), "w", encoding="utf-8") as fh:
            fh.write(content)


# --------------------------------------------------------------------------- #
# parsing + normalization
# --------------------------------------------------------------------------- #
def test_parse_wikilinks_strips_display_and_anchor():
    assert parse_wikilinks("a [[foo]] b [[bar|Bar]] c [[baz#sec]] [[foo]]") == ["foo", "bar", "baz"]


def test_normalize_slug_unifies_separators():
    assert normalize_slug("Foo_Bar Baz.md") == "foo-bar-baz"
    assert normalize_slug("a__b--c") == "a-b-c"


# --------------------------------------------------------------------------- #
# resolution (the load-bearing slug behavior)
# --------------------------------------------------------------------------- #
def test_resolution_full_stem_prefix_strip_and_underscore(tmp_path):
    md = str(tmp_path / "memory")
    _corpus(md)
    g = LinkGraph(md)

    assert g.resolve("beta") == "beta.md"  # full stem
    assert g.resolve("gamma-thing") == "feedback_gamma_thing.md"  # prefix-stripped alias
    assert g.resolve("gamma_thing") == "feedback_gamma_thing.md"  # underscore variant normalizes
    # the exact corpus census slug-mismatch resolves:
    assert g.resolve("151-avenue-a-is-standard-size") == "feedback_151_avenue_a_is_standard_size.md"


def test_genuinely_absent_targets_do_not_resolve(tmp_path):
    md = str(tmp_path / "memory")
    _corpus(md)
    g = LinkGraph(md)
    assert g.resolve("ship-roadmap") is None
    assert g.resolve("totally-absent") is None
    assert g.resolve("gamma") is None  # partial-prefix must NOT false-resolve to gamma-thing


def test_resolved_via_stem_distinguishes_canonical_from_alias(tmp_path):
    md = str(tmp_path / "memory")
    _corpus(md)
    g = LinkGraph(md)
    assert g.resolved_via_stem("beta") is True  # canonical filename stem
    assert g.resolved_via_stem("gamma-thing") is False  # resolves only via soft alias


# --------------------------------------------------------------------------- #
# traversal
# --------------------------------------------------------------------------- #
def test_traverse_n_hops(tmp_path):
    md = str(tmp_path / "memory")
    _corpus(md)
    g = LinkGraph(md)

    one = g.traverse("alpha", hops=1)
    assert "beta.md" in one and "feedback_gamma_thing.md" in one
    assert "delta.md" not in one  # delta is 2 hops away (alpha -> gamma -> delta)

    two = g.traverse("alpha", hops=2)
    assert "delta.md" in two  # reached via feedback_gamma_thing -> delta
    assert "alpha.md" not in two  # start excluded even though beta links back to it


def test_traverse_unknown_or_zero_hops_is_empty(tmp_path):
    md = str(tmp_path / "memory")
    _corpus(md)
    g = LinkGraph(md)
    assert g.traverse("does-not-exist", hops=2) == set()
    assert g.traverse("alpha", hops=0) == set()


# --------------------------------------------------------------------------- #
# lint (dangling / slug-mismatch / orphan) — READ-ONLY
# --------------------------------------------------------------------------- #
def test_lint_flags_dangling_targets(tmp_path):
    md = str(tmp_path / "memory")
    _corpus(md)
    report = L.lint(md)
    dangling = {(d["file"], d["target"]) for d in report["dangling"]}
    assert ("alpha.md", "ship-roadmap") in dangling
    assert ("alpha.md", "totally-absent") in dangling
    # the resolvable ones are NOT dangling
    assert not any(t in ("beta", "gamma-thing", "151-avenue-a-is-standard-size") for _, t in dangling)


def test_lint_flags_slug_mismatch(tmp_path):
    md = str(tmp_path / "memory")
    _corpus(md)
    report = L.lint(md)
    mism = {(d["file"], d["target"]) for d in report["slug_mismatch"]}
    assert ("alpha.md", "gamma-thing") in mism
    assert ("alpha.md", "151-avenue-a-is-standard-size") in mism
    # a canonical-stem link is NOT a mismatch
    assert ("alpha.md", "beta") not in mism


def test_lint_flags_orphans(tmp_path):
    md = str(tmp_path / "memory")
    _corpus(md)
    report = L.lint(md)
    # delta has inbound (from gamma) but ZERO outbound -> orphan; 151 file has no links -> orphan
    assert "delta.md" in report["orphans"]
    assert "feedback_151_avenue_a_is_standard_size.md" in report["orphans"]
    assert "alpha.md" not in report["orphans"]


def test_lint_is_read_only(tmp_path):
    md = str(tmp_path / "memory")
    _corpus(md)
    before = {
        f: (os.path.getmtime(os.path.join(md, f)), open(os.path.join(md, f), encoding="utf-8").read())
        for f in os.listdir(md)
    }
    L.lint(md)
    L.lint(md)  # idempotent
    after = {
        f: (os.path.getmtime(os.path.join(md, f)), open(os.path.join(md, f), encoding="utf-8").read())
        for f in os.listdir(md)
    }
    assert before == after  # no file touched


# --------------------------------------------------------------------------- #
# producer + resilience
# --------------------------------------------------------------------------- #
def test_health_line_self_suppresses_when_clean(tmp_path):
    md = str(tmp_path / "memory")
    os.makedirs(md)
    # one file, no links of any kind -> no dangling, no mismatch -> producer is silent
    with open(os.path.join(md, "solo.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem("solo", "no links at all"))
    assert L.lint_links_producer(md, md) is None


def test_health_line_reports_dangling(tmp_path):
    md = str(tmp_path / "memory")
    _corpus(md)
    line = L.lint_links_producer(md, md)
    assert line and "dangling" in line and line.startswith("🔗")


def test_build_graph_missing_dir_is_none():
    assert build_graph("/no/such/memory/dir/xyz") is None
