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
# COR-9: soft-alias collisions are ambiguous, never first-claimant-wins
# --------------------------------------------------------------------------- #
def _write(md: str, fname: str, content: str) -> None:
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, fname), "w", encoding="utf-8") as fh:
        fh.write(content)


def test_two_files_sharing_stripped_alias_resolve_to_neither(tmp_path):
    """The COR-9 census case: feedback_api_keys + project_api_keys both strip to
    "api-keys". Pre-fix, sorted iteration let feedback_* silently claim it and
    [[api-keys]] resolved to the WRONG memory. Now resolve() refuses."""
    md = str(tmp_path / "memory")
    _write(md, "feedback_api_keys.md", _mem("feedback api keys", "body"))
    _write(md, "project_api_keys.md", _mem("project api keys", "body"))
    g = LinkGraph(md)
    assert g.resolve("api-keys") is None
    assert g.ambiguous_claimants("api-keys") == ["feedback_api_keys", "project_api_keys"]
    # both files stay reachable by their canonical full stems — only the SOFT alias died
    assert g.resolve("feedback-api-keys") == "feedback_api_keys"
    assert g.resolve("project-api-keys") == "project_api_keys"


def test_nameslug_vs_nameslug_collision_is_ambiguous(tmp_path):
    md = str(tmp_path / "memory")
    _write(md, "one.md", _mem("Shared Name", "body"))
    _write(md, "two.md", _mem("Shared Name", "body"))
    g = LinkGraph(md)
    assert g.resolve("shared-name") is None
    assert g.ambiguous_claimants("shared-name") == ["one", "two"]


def test_stripped_vs_nameslug_collision_across_files_is_ambiguous(tmp_path):
    """Cross-KIND soft collision: file A's stripped stem == file B's name slug.
    Both are soft tier, different files -> ambiguous."""
    md = str(tmp_path / "memory")
    _write(md, "feedback_api_keys.md", _mem("feedback api keys", "body"))
    _write(md, "vault.md", _mem("api keys", "body"))
    g = LinkGraph(md)
    assert g.resolve("api-keys") is None
    assert g.ambiguous_claimants("api-keys") == ["feedback_api_keys", "vault"]


def test_full_stem_still_beats_soft_alias(tmp_path):
    """Tier rule preserved: a full-stem claim wins outright — the losing soft claim is
    not registered and must NOT poison the full-stem alias, even with MULTIPLE soft
    losers that would otherwise collide with each other."""
    md = str(tmp_path / "memory")
    _write(md, "api-keys.md", _mem("canonical", "body"))
    _write(md, "feedback_api_keys.md", _mem("feedback api keys", "body"))
    _write(md, "project_api_keys.md", _mem("project api keys", "body"))
    g = LinkGraph(md)
    assert g.resolve("api-keys") == "api-keys"  # full stem, unpoisoned
    assert g.ambiguous_claimants("api-keys") == []


def test_same_file_claiming_alias_twice_is_not_a_collision(tmp_path):
    """A file whose stripped stem equals its own name slug re-claims ONE alias from
    both soft kinds — same file, so no ambiguity, and it still resolves."""
    md = str(tmp_path / "memory")
    _write(md, "feedback_gamma_thing.md", _mem("Gamma Thing", "body"))
    g = LinkGraph(md)
    assert g.resolve("gamma-thing") == "feedback_gamma_thing"
    assert g.ambiguous_claimants("gamma-thing") == []


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


def test_lint_reports_ambiguous_with_both_claimants_not_dangling(tmp_path):
    """COR-9 acceptance: a [[target]] hitting an ambiguous soft alias lints as
    AMBIGUOUS — naming the alias and both claimant files — and is NOT mislabeled
    dangling (the files exist; the user must disambiguate, not create)."""
    md = str(tmp_path / "memory")
    _write(md, "feedback_api_keys.md", _mem("feedback api keys", "body"))
    _write(md, "project_api_keys.md", _mem("project api keys", "body"))
    _write(md, "linker.md", _mem("linker", "see [[api-keys]]"))
    report = L.lint(md)
    assert report["ambiguous"] == [
        {
            "file": "linker",
            "target": "api-keys",
            "claimants": ["feedback_api_keys", "project_api_keys"],
        }
    ]
    assert not any(d["target"] == "api-keys" for d in report["dangling"])
    # legible degradation: ambiguity is LOUD at SessionStart, like dangling
    line = L.health_line(report)
    assert line and "ambiguous" in line and "api-keys" in line


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


# --------------------------------------------------------------------------- #
# GRA-4: typed edges — supersedes / contradicts / refines
# --------------------------------------------------------------------------- #
def _typed_mem(name: str, fm_extra: str, body: str = "body") -> str:
    return f'---\nname: {name}\ndescription: "d for {name}"\ntype: project\n{fm_extra}---\n{body}\n'


def _typed_corpus(md: str) -> None:
    """new_way supersedes old_way + refines base; rival contradicts base (nested under
    metadata:); dangler carries an unresolvable supersedes target."""
    _write(md, "old_way.md", _mem("old_way", "body"))
    _write(md, "base.md", _mem("base", "body"))
    _write(md, "new_way.md", _typed_mem("new_way", "supersedes: [old_way]\nrefines: [base]\n"))
    _write(md, "rival.md", _typed_mem("rival", "metadata:\n  contradicts: [base]\n"))
    _write(md, "dangler.md", _typed_mem("dangler", 'supersedes: ["no-such-memory"]\n'))


def test_parse_typed_relations_top_level_and_metadata_and_scalar():
    from memory.links import parse_typed_relations

    # top-level list, metadata-nested list, and the natural scalar single-target form
    assert parse_typed_relations({"supersedes": ["a", "b", "a"]}) == {"supersedes": ["a", "b"]}
    assert parse_typed_relations({"metadata": {"contradicts": ["x"]}}) == {"contradicts": ["x"]}
    assert parse_typed_relations({"refines": "base"}) == {"refines": ["base"]}
    # top-level wins over metadata (the cited_paths read convention); junk shapes drop
    assert parse_typed_relations({"supersedes": ["t"], "metadata": {"supersedes": ["m"]}}) == {
        "supersedes": ["t"]
    }
    assert parse_typed_relations({"supersedes": [1, None, ""], "contradicts": 7}) == {}
    assert parse_typed_relations({}) == {}
    assert parse_typed_relations(None) == {}  # never raises


def test_typed_edges_resolve_through_the_same_alias_path(tmp_path):
    """A typed target enjoys the SAME soft-alias resolution and ambiguity refusal a
    [[wikilink]] does — one resolution path (the GRA-4 spec's own requirement)."""
    md = str(tmp_path / "memory")
    _write(md, "feedback_gamma_thing.md", _mem("Gamma Thing", "body"))
    # soft (prefix-stripped) alias target resolves...
    _write(md, "n1.md", _typed_mem("n1", "supersedes: [gamma-thing]\n"))
    # ...an ambiguous soft alias is refused (COR-9), landing in typed_unresolved
    _write(md, "feedback_api_keys.md", _mem("feedback api keys", "body"))
    _write(md, "project_api_keys.md", _mem("project api keys", "body"))
    _write(md, "n2.md", _typed_mem("n2", "supersedes: [api-keys]\n"))
    g = LinkGraph(md)
    assert g.typed["n1"]["supersedes"] == {"feedback_gamma_thing"}
    assert "n2" not in g.typed
    assert g.typed_unresolved["n2"] == {"supersedes": ["api-keys"]}


def test_typed_accessors_both_directions_and_self_target_dropped(tmp_path):
    md = str(tmp_path / "memory")
    _typed_corpus(md)
    # a self-referential typed edge must be dropped like a self wikilink
    _write(md, "selfy.md", _typed_mem("selfy", "supersedes: [selfy]\n"))
    g = LinkGraph(md)
    assert g.typed_outbound("new_way", "supersedes") == {"old_way"}
    assert g.typed_inbound("old_way", "supersedes") == {"new_way"}  # recall's direction
    assert g.typed_inbound("base", "contradicts") == {"rival"}
    assert g.typed_inbound("base", "refines") == {"new_way"}
    assert g.typed_outbound("old_way", "supersedes") == set()
    assert g.typed_inbound("unknown-name", "supersedes") == set()
    assert "selfy" not in g.typed
    # typed edges NEVER leak into the untyped adjacency (wikilinks stay the untyped edge)
    assert g.adjacency["new_way"] == set()
    assert g.inbound("old_way") == set()


def test_all_typed_edges_enumerates_corpus_wide_sorted(tmp_path):
    """GOV-1's enumerator: every resolved (src, tgt) pair for a relation, corpus-wide —
    unresolved targets stay in typed_unresolved (a lint concern), never in the edge list."""
    md = str(tmp_path / "memory")
    _typed_corpus(md)
    _write(md, "rival2.md", _typed_mem("rival2", "contradicts: [old_way]\n"))
    g = LinkGraph(md)
    assert g.all_typed_edges("contradicts") == [("rival", "base"), ("rival2", "old_way")]
    assert g.all_typed_edges("supersedes") == [("new_way", "old_way")]  # dangler's miss excluded
    assert g.all_typed_edges("refines") == [("new_way", "base")]
    assert g.all_typed_edges("no-such-relation") == []


def test_all_typed_edges_is_directional(tmp_path):
    """A mutual declaration yields BOTH tuples — collapsing to one conflict is the
    consumer's call (resolve_view canonicalizes), not the graph's."""
    md = str(tmp_path / "memory")
    _write(md, "a.md", _typed_mem("a", "contradicts: [b]\n"))
    _write(md, "b.md", _typed_mem("b", "contradicts: [a]\n"))
    g = LinkGraph(md)
    assert g.all_typed_edges("contradicts") == [("a", "b"), ("b", "a")]


def test_lint_flags_dangling_typed_targets(tmp_path):
    md = str(tmp_path / "memory")
    _typed_corpus(md)
    report = L.lint(md)
    assert report["typed_dangling"] == [
        {"file": "dangler", "relation": "supersedes", "target": "no-such-memory", "claimants": []}
    ]
    assert report["typed_edges"] == 3  # supersedes + refines + contradicts, all resolved
    line = L.health_line(report)
    assert line and "dangling typed relation" in line and "no-such-memory" in line


def test_lint_typed_ambiguous_target_names_claimants(tmp_path):
    md = str(tmp_path / "memory")
    _write(md, "feedback_api_keys.md", _mem("feedback api keys", "body"))
    _write(md, "project_api_keys.md", _mem("project api keys", "body"))
    _write(md, "n.md", _typed_mem("n", "contradicts: [api-keys]\n"))
    report = L.lint(md)
    assert report["typed_dangling"] == [
        {
            "file": "n",
            "relation": "contradicts",
            "target": "api-keys",
            "claimants": ["feedback_api_keys", "project_api_keys"],
        }
    ]


def test_lint_health_line_silent_when_typed_edges_are_clean(tmp_path):
    md = str(tmp_path / "memory")
    _write(md, "old_way.md", _mem("old_way", "body"))
    _write(md, "new_way.md", _typed_mem("new_way", "supersedes: [old_way]\n"))
    report = L.lint(md)
    assert report["typed_dangling"] == []
    assert L.health_line(report) is None  # resolved typed edges are healthy, not rot


# --------------------------------------------------------------------------- #
# GRA-4: add_typed_relation — the ONE typed-edge write primitive
# --------------------------------------------------------------------------- #
def test_add_typed_relation_appends_top_level_body_verbatim(tmp_path):
    from memory.links import add_typed_relation

    md = str(tmp_path / "memory")
    _write(md, "succ.md", _mem("succ", "the body\nstays byte-identical\n"))
    p = os.path.join(md, "succ.md")
    before_body = open(p, encoding="utf-8").read().split("---\n", 2)[-1]

    r = add_typed_relation(p, "supersedes", "old_way")
    assert r == {"path": p, "relation": "supersedes", "target": "old_way", "changed": True, "error": None}
    text = open(p, encoding="utf-8").read()
    assert 'supersedes: ["old_way"]' in text
    assert text.split("---\n", 2)[-1] == before_body  # body untouched

    # idempotent — a slug-equivalent target (underscore/hyphen variant) is the SAME edge
    assert add_typed_relation(p, "supersedes", "old-way")["changed"] is False
    # a second, different target APPENDS to the existing flow list
    assert add_typed_relation(p, "supersedes", "older_way")["changed"] is True
    assert 'supersedes: ["old_way", "older_way"]' in open(p, encoding="utf-8").read()


def test_add_typed_relation_nests_under_metadata_block(tmp_path):
    """Mirrors backfill_text/set_invalid_after's metadata:-nesting discipline so
    parse_typed_relations finds the key regardless of frontmatter schema."""
    from memory.links import add_typed_relation, parse_typed_relations
    from memory.provenance import parse_frontmatter

    md = str(tmp_path / "memory")
    _write(
        md,
        "nested.md",
        "---\nname: nested\ndescription: \"d\"\nmetadata:\n  originSessionId: abc\n---\nbody\n",
    )
    p = os.path.join(md, "nested.md")
    assert add_typed_relation(p, "contradicts", "rival")["changed"] is True
    text = open(p, encoding="utf-8").read()
    assert '  contradicts: ["rival"]' in text  # nested at the metadata block's indent
    assert parse_typed_relations(parse_frontmatter(text)) == {"contradicts": ["rival"]}


def test_add_typed_relation_merges_existing_block_style_list(tmp_path):
    from memory.links import add_typed_relation

    md = str(tmp_path / "memory")
    _write(
        md,
        "block.md",
        '---\nname: block\ndescription: "d"\nsupersedes:\n  - alpha\n  - beta\n---\nbody\n',
    )
    p = os.path.join(md, "block.md")
    assert add_typed_relation(p, "supersedes", "gamma")["changed"] is True
    text = open(p, encoding="utf-8").read()
    assert 'supersedes: ["alpha", "beta", "gamma"]' in text  # old values preserved, one canonical line
    assert "- alpha" not in text  # the block continuation lines were folded in, not duplicated


def test_add_typed_relation_refusals_and_dry_run(tmp_path):
    from memory.links import add_typed_relation

    md = str(tmp_path / "memory")
    _write(md, "ok.md", _mem("ok", "body"))
    _write(md, "nofm.md", "no frontmatter at all\n")
    _write(
        md,
        "bad.md",
        "---\nname: bad\ndescription: contains an unquoted colon: like this\n---\nbody\n",
    )
    assert add_typed_relation(os.path.join(md, "ok.md"), "bogus-rel", "x")["error"] is not None
    assert add_typed_relation(os.path.join(md, "ok.md"), "supersedes", "  ")["error"] is not None
    assert add_typed_relation(os.path.join(md, "nofm.md"), "supersedes", "x")["error"] is not None
    assert add_typed_relation(os.path.join(md, "bad.md"), "supersedes", "x")["error"] is not None
    missing = add_typed_relation(os.path.join(md, "gone.md"), "supersedes", "x")
    assert missing["error"] is not None and missing["changed"] is False  # never raises

    before = open(os.path.join(md, "ok.md"), encoding="utf-8").read()
    r = add_typed_relation(os.path.join(md, "ok.md"), "supersedes", "x", dry_run=True)
    assert r["changed"] is True and r["error"] is None
    assert open(os.path.join(md, "ok.md"), encoding="utf-8").read() == before  # nothing written


def test_add_typed_relation_is_single_item_only_no_bulk_path():
    """The no-bulk pin (mirrors test_semantic_reverify_is_single_item_only): one path, one
    relation, one target — a bulk supersede sweep must not be expressible."""
    import inspect

    from memory.links import add_typed_relation

    params = list(inspect.signature(add_typed_relation).parameters)
    assert params[:3] == ["path", "relation", "target"]
    assert "targets" not in params and "names" not in params and "bulk" not in params


# --------------------------------------------------------------------------- #
# DRM-6: derives-from — derivation provenance joins the closed typed-relation set
# --------------------------------------------------------------------------- #
def test_derives_from_is_a_typed_relation_with_a_version_bump(tmp_path):
    """DRM-6 acceptance (inv5, a clean schema addition + version bump): ``derives-from``
    is in TYPED_RELATIONS, ``add_typed_relation`` accepts it (it refused it before this
    item), ``parse_typed_relations`` round-trips it, the graph resolves it into the typed
    maps, and BOTH version dials moved — LINKS_SCHEMA_VERSION 2→3 (a v2 cache predates
    the relation and must read as a miss) and CORPUS_FORMAT_VERSION 4→5."""
    from memory.links import (
        LINKS_SCHEMA_VERSION,
        TYPED_RELATIONS,
        LinkGraph,
        add_typed_relation,
        parse_typed_relations,
    )
    from memory.provenance import CORPUS_FORMAT_VERSION, parse_frontmatter

    assert "derives-from" in TYPED_RELATIONS
    assert LINKS_SCHEMA_VERSION == 3
    assert CORPUS_FORMAT_VERSION == 5

    md = str(tmp_path / "memory")
    _write(md, "child-a.md", _mem("child-a", "body"))
    _write(md, "child-b.md", _mem("child-b", "body"))
    _write(md, "parent.md", _mem("parent", "an abstraction over the children"))
    p = os.path.join(md, "parent.md")

    r = add_typed_relation(p, "derives-from", "child-a")
    assert r["error"] is None and r["changed"] is True
    assert add_typed_relation(p, "derives-from", "child-b")["changed"] is True
    # idempotent on the slug-equivalent form, same as every other relation
    assert add_typed_relation(p, "derives-from", "child_a")["changed"] is False

    text = open(p, encoding="utf-8").read()
    assert 'derives-from: ["child-a", "child-b"]' in text
    assert parse_typed_relations(parse_frontmatter(text)) == {
        "derives-from": ["child-a", "child-b"]
    }
    graph = LinkGraph(md)
    assert graph.typed.get("parent", {}).get("derives-from") == {"child-a", "child-b"}


# --------------------------------------------------------------------------- #
# GRA-8: graph observability — components / degree / export
# --------------------------------------------------------------------------- #
def _observability_corpus(md: str) -> None:
    """Three components: {a<->b via wikilink}, {c -supersedes-> d}, and the isolate e."""
    _write(md, "a.md", _mem("a", "see [[b]]"))
    _write(md, "b.md", _mem("b", "back to [[a]]"))
    _write(md, "c.md", _typed_mem("c", "supersedes: [d]\n"))
    _write(md, "d.md", _mem("d", "no links"))
    _write(md, "e.md", _mem("e", "an isolate"))


def test_connected_components_weakly_over_all_edge_kinds(tmp_path):
    md = str(tmp_path / "memory")
    _observability_corpus(md)
    comps = LinkGraph(md).connected_components()
    # Largest-first, then lexical; a typed edge unites c/d just like a wikilink unites a/b.
    assert comps == [["a", "b"], ["c", "d"], ["e"]]


def test_degrees_count_both_directions_and_edge_kinds(tmp_path):
    md = str(tmp_path / "memory")
    _observability_corpus(md)
    deg = dict((stem, (out, ind, tot)) for stem, out, ind, tot in LinkGraph(md).degrees())
    assert deg["a"] == (1, 1, 1)  # a<->b reciprocal → one undirected neighbor
    assert deg["c"] == (1, 0, 1)  # c supersedes d (typed out)
    assert deg["d"] == (0, 1, 1)  # d is superseded (typed in)
    assert deg["e"] == (0, 0, 0)  # isolate


def test_export_json_dot_mermaid_are_deterministic_and_complete(tmp_path):
    import json as _json

    md = str(tmp_path / "memory")
    _observability_corpus(md)
    g = LinkGraph(md)

    payload = _json.loads(g.export("json"))
    assert payload["files"] == ["a", "b", "c", "d", "e"]
    assert payload["components"] == 3
    assert {"src": "c", "tgt": "d", "type": "supersedes"} in payload["edges"]
    assert {"src": "a", "tgt": "b", "type": "link"} in payload["edges"]

    dot = g.export("dot")
    assert dot.startswith("digraph hippo {") and dot.rstrip().endswith("}")
    assert '"c" -> "d" [label="supersedes"];' in dot

    mermaid = g.export("mermaid")
    assert mermaid.startswith("graph LR")
    assert "-->|supersedes|" in mermaid

    # Deterministic: a fresh graph over the same corpus → byte-identical export.
    assert LinkGraph(md).export("json") == g.export("json")


def test_export_rejects_unknown_format(tmp_path):
    import pytest

    md = str(tmp_path / "memory")
    _observability_corpus(md)
    with pytest.raises(ValueError):
        LinkGraph(md).export("svg")


def test_component_count_helper_guards_and_matches(tmp_path):
    from memory.links import component_count

    md = str(tmp_path / "memory")
    _observability_corpus(md)
    assert component_count(md) == 3
    assert component_count("/no/such/dir/xyz") is None  # guarded, never raises
