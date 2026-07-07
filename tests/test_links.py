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

    # GRA-2: resolve() returns STEMS — no ".md" anywhere in graph output.
    assert g.resolve("beta") == "beta"  # full stem
    assert g.resolve("gamma-thing") == "feedback_gamma_thing"  # prefix-stripped alias
    assert g.resolve("gamma_thing") == "feedback_gamma_thing"  # underscore variant normalizes
    # the exact corpus census slug-mismatch resolves:
    assert g.resolve("151-avenue-a-is-standard-size") == "feedback_151_avenue_a_is_standard_size"


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
    assert "beta" in one and "feedback_gamma_thing" in one
    assert "delta" not in one  # delta is 2 hops away (alpha -> gamma -> delta)

    two = g.traverse("alpha", hops=2)
    assert "delta" in two  # reached via feedback_gamma_thing -> delta
    assert "alpha" not in two  # start excluded even though beta links back to it


def test_traverse_unknown_or_zero_hops_is_empty(tmp_path):
    md = str(tmp_path / "memory")
    _corpus(md)
    g = LinkGraph(md)
    assert g.traverse("does-not-exist", hops=2) == set()
    assert g.traverse("alpha", hops=0) == set()


# --------------------------------------------------------------------------- #
# GRA-2: stem identity + inbound()/isolates() backlink primitives
# --------------------------------------------------------------------------- #
def test_graph_output_is_stem_keyed_no_md_anywhere(tmp_path):
    """The clean break: files / adjacency (keys AND values) / raw_targets / unresolved /
    orphans / isolates all speak stems — no '.md' suffix survives into graph output."""
    md = str(tmp_path / "memory")
    _corpus(md)
    g = LinkGraph(md)

    every_node = (
        set(g.files)
        | set(g.adjacency)
        | {t for targets in g.adjacency.values() for t in targets}
        | set(g.raw_targets)
        | set(g.unresolved)
        | set(g.orphans())
        | set(g.isolates())
    )
    assert every_node  # sanity: the corpus actually produced nodes
    assert not any(n.endswith(".md") for n in every_node)


def test_stem_adjacency_joins_cleanly_against_plain_name_set(tmp_path):
    """The whole point of the stem break: graph output intersects a staleness/soak-style
    stem set directly — no Path(...).stem / '.md'-stripping conversion step."""
    md = str(tmp_path / "memory")
    _corpus(md)
    g = LinkGraph(md)

    # the shape staleness/soak/archive produce: plain stems.
    name_set = {"alpha", "beta", "delta", "feedback_gamma_thing"}
    assert set(g.files) & name_set == name_set
    has_inbound = {n for n in g.files if g.inbound(n)}
    assert has_inbound & name_set == {"alpha", "beta", "delta", "feedback_gamma_thing"}


def test_inbound_correctness_and_alias_resolution(tmp_path):
    md = str(tmp_path / "memory")
    _corpus(md)
    g = LinkGraph(md)

    assert g.inbound("beta") == {"alpha"}
    assert g.inbound("alpha") == {"beta"}
    assert g.inbound("delta") == {"feedback_gamma_thing"}
    # accepts any resolvable alias, like outbound(): prefix-stripped + underscore variants
    assert g.inbound("gamma-thing") == {"alpha"}
    assert g.inbound("gamma_thing") == {"alpha"}
    assert g.inbound("feedback_gamma_thing.md") == {"alpha"}  # filename form still resolves
    # unknown name -> empty set, never a raise
    assert g.inbound("no-such-memory") == set()


def test_inbound_and_outbound_are_consistent_views_of_one_graph(tmp_path):
    """Reverse adjacency is built once in _build(); it must be the exact transpose of
    adjacency — every forward edge appears backward, nothing extra."""
    md = str(tmp_path / "memory")
    _corpus(md)
    g = LinkGraph(md)
    forward = {(src, dst) for src, targets in g.adjacency.items() for dst in targets}
    backward = {(src, dst) for dst in g.files for src in g.inbound(dst)}
    assert forward == backward


def test_isolates_vs_orphans_distinction(tmp_path):
    """orphan = zero OUTBOUND (may still be pointed at); isolate = zero in AND zero out.
    delta: inbound from gamma, no outbound -> orphan but NOT isolate.
    the 151 file: zero outbound but alpha links to it -> orphan, NOT isolate.
    epsilon: no links either direction -> orphan AND isolate."""
    md = str(tmp_path / "memory")
    _corpus(md)
    with open(os.path.join(md, "epsilon.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem("epsilon", "fully disconnected — no links in or out"))
    g = LinkGraph(md)

    assert "delta" in g.orphans()
    assert "delta" not in g.isolates()  # inbound from gamma keeps it out of isolates
    assert "feedback_151_avenue_a_is_standard_size" in g.orphans()
    assert "feedback_151_avenue_a_is_standard_size" not in g.isolates()  # alpha points at it
    assert g.isolates() == ["epsilon"]
    # isolates is strictly a subset of orphans, both sorted
    assert set(g.isolates()) <= set(g.orphans())
    assert g.orphans() == sorted(g.orphans()) and g.isolates() == sorted(g.isolates())


# --------------------------------------------------------------------------- #
# lint (dangling / slug-mismatch / orphan) — READ-ONLY
# --------------------------------------------------------------------------- #
def test_lint_flags_dangling_targets(tmp_path):
    md = str(tmp_path / "memory")
    _corpus(md)
    report = L.lint(md)
    dangling = {(d["file"], d["target"]) for d in report["dangling"]}
    assert ("alpha", "ship-roadmap") in dangling
    assert ("alpha", "totally-absent") in dangling
    # the resolvable ones are NOT dangling
    assert not any(t in ("beta", "gamma-thing", "151-avenue-a-is-standard-size") for _, t in dangling)


def test_lint_flags_slug_mismatch(tmp_path):
    md = str(tmp_path / "memory")
    _corpus(md)
    report = L.lint(md)
    mism = {(d["file"], d["target"]) for d in report["slug_mismatch"]}
    assert ("alpha", "gamma-thing") in mism
    assert ("alpha", "151-avenue-a-is-standard-size") in mism
    # a canonical-stem link is NOT a mismatch
    assert ("alpha", "beta") not in mism


def test_lint_flags_orphans(tmp_path):
    md = str(tmp_path / "memory")
    _corpus(md)
    report = L.lint(md)
    # delta has inbound (from gamma) but ZERO outbound -> orphan; 151 file has no links -> orphan
    assert "delta" in report["orphans"]
    assert "feedback_151_avenue_a_is_standard_size" in report["orphans"]
    assert "alpha" not in report["orphans"]


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
