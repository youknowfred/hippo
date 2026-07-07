"""Tests for GRA-6 — persisted wikilink edge cache (``links.json`` in the index dir).

Hermetic: dense is disabled everywhere (BM25-only builds), tmp corpora only. The
load-bearing claims pinned here:
  - a cache hit reproduces an IDENTICAL graph to a cold build (adjacency, aliases,
    ambiguous, unresolved, raw_targets, inbound) with ZERO memory-file reads;
  - a BODY edit (doc_text unchanged — the manifest's hash check cannot see it) is
    picked up via the per-file stat signature;
  - corrupt/missing/wrong-schema caches fall back to the full re-read, never raise;
  - ``load_edges`` returns both directions from links.json only, and None on absence;
  - ``refresh_index``'s no-op short-circuit backfills/refreshes the cache so a quiet
    corpus (or a pre-GRA-6 index) cannot starve it forever.
"""

from __future__ import annotations

import builtins
import json
import os

import memory.links as LK
from memory import build_index as B
from memory import lint_links as L
from memory.links import LinkGraph, build_graph, load_edges


def _mem(name: str, body: str) -> str:
    return f'---\nname: {name}\ndescription: "d for {name}"\ntype: project\n---\n{body}\n'


def _write(md: str, fname: str, content: str) -> None:
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, fname), "w", encoding="utf-8") as fh:
        fh.write(content)


def _corpus(md: str) -> None:
    """Same shape as test_links's census corpus: full-stem + soft-alias + dangling +
    an ambiguous soft collision, so every persisted field is non-trivially exercised."""
    _write(
        md,
        "alpha.md",
        _mem("alpha", "see [[beta]] and [[gamma-thing]] and [[ship-roadmap]] and [[api-keys]]"),
    )
    _write(md, "beta.md", _mem("beta", "back to [[alpha]]"))
    _write(md, "feedback_gamma_thing.md", _mem("Gamma Thing", "onward to [[delta]]"))
    _write(md, "delta.md", _mem("delta", "no outbound links here"))
    # COR-9 ambiguity: both strip to the soft alias "api-keys" -> resolve() refuses.
    _write(md, "feedback_api_keys.md", _mem("feedback api keys", "body"))
    _write(md, "project_api_keys.md", _mem("project api keys", "body"))


def _build(md: str, idx: str, monkeypatch) -> None:
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    B.build_index(md, idx)


def _graph_state(g: LinkGraph) -> tuple:
    """Every externally-observable graph facet, for exact cold-vs-cached comparison."""
    return (
        list(g.files),
        {s: set(v) for s, v in g.adjacency.items()},
        {s: set(g.inbound(s)) for s in g.files},
        dict(g._alias_to_stem),
        {a: set(c) for a, c in g._ambiguous.items()},
        {s: list(t) for s, t in g.raw_targets.items()},
        {s: list(t) for s, t in g.unresolved.items()},
        g.orphans(),
        g.isolates(),
    )


def _poison_corpus_reads(monkeypatch):
    """Make any full-corpus re-read inside memory.links blow up — so a non-None graph
    from build_graph PROVES the cached path served it (the fallback would raise ->
    build_graph would return None)."""

    def boom(_md):
        raise AssertionError("cached path must not iterate memory files")

    monkeypatch.setattr(LK, "_iter_memory_files", boom)


# --------------------------------------------------------------------------- #
# persistence + identical reconstruction
# --------------------------------------------------------------------------- #
def test_build_index_persists_links_cache(tmp_path, monkeypatch):
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _corpus(md)
    _build(md, idx, monkeypatch)

    p = os.path.join(idx, "links.json")
    assert os.path.exists(p)
    with open(p, encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["schema_version"] == LK.LINKS_SCHEMA_VERSION
    assert set(payload["files"]) == {
        "alpha", "beta", "delta", "feedback_gamma_thing",
        "feedback_api_keys", "project_api_keys",
    }
    for rec in payload["files"].values():
        mtime_ns, size = rec["sig"]
        assert mtime_ns > 0 and size > 0  # real stat signatures, not placeholders
    assert payload["files"]["alpha"]["outbound"] == ["beta", "feedback_gamma_thing"]
    # the COR-9 ambiguity survives the round trip with BOTH claimants
    assert payload["ambiguous"]["api-keys"] == ["feedback_api_keys", "project_api_keys"]


def test_cache_hit_reproduces_identical_graph(tmp_path, monkeypatch):
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _corpus(md)
    _build(md, idx, monkeypatch)

    cold = LinkGraph(md)  # authoritative full read, BEFORE the poison
    _poison_corpus_reads(monkeypatch)
    cached = build_graph(md, index_dir=idx)
    assert cached is not None  # None would mean the fallback (poisoned) path ran
    assert _graph_state(cached) == _graph_state(cold)
    # query surface behaves identically too — incl. refusing the ambiguous alias
    assert cached.resolve("gamma-thing") == "feedback_gamma_thing"
    assert cached.resolve("api-keys") is None
    assert cached.ambiguous_claimants("api-keys") == ["feedback_api_keys", "project_api_keys"]
    assert cached.traverse("alpha", hops=2) == cold.traverse("alpha", hops=2)


def test_cached_path_performs_zero_memory_file_reads(tmp_path, monkeypatch):
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _corpus(md)
    _build(md, idx, monkeypatch)

    _poison_corpus_reads(monkeypatch)
    opened: list = []
    real_open = builtins.open

    def spy(file, *a, **k):
        try:
            p = os.fspath(file)
        except TypeError:
            p = ""
        if isinstance(p, str) and p.startswith(md):
            opened.append(p)
        return real_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    g = build_graph(md, index_dir=idx)
    assert g is not None
    assert opened == []  # links.json (under idx) is the ONLY file touched


# --------------------------------------------------------------------------- #
# invalidation: BODY edits (doc_text unchanged) must be picked up via stat sig
# --------------------------------------------------------------------------- #
def test_body_edit_invalidates_via_stat_signature(tmp_path, monkeypatch):
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _corpus(md)
    _build(md, idx, monkeypatch)
    hashes_before = [e["hash"] for e in B.compute_corpus(md)]

    # BODY-only edit: name + description untouched, a new wikilink appears.
    _write(md, "delta.md", _mem("delta", "now links out: [[alpha]] and [[beta]]"))

    # Load-bearing premise of the stat-sig design: doc_text (name+description) hashes are
    # UNCHANGED — a doc_text-keyed cache could never see this edit.
    assert [e["hash"] for e in B.compute_corpus(md)] == hashes_before

    g = build_graph(md, index_dir=idx)  # sig mismatch -> falls back to the full re-read
    assert g is not None
    assert g.adjacency["delta"] == {"alpha", "beta"}


def test_refresh_noop_repersists_cache_after_body_edit(tmp_path, monkeypatch):
    """refresh_index's corpus-unchanged short-circuit (doc_text hashes) fires on a
    body-only edit — it must still re-persist links.json so the NEXT cached read
    (zero file reads) serves the new edge."""
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _corpus(md)
    _build(md, idx, monkeypatch)

    _write(md, "delta.md", _mem("delta", "now links out: [[alpha]]"))
    out = B.refresh_index(md, idx)
    assert out is not None  # the no-op path returned the unchanged manifest

    _poison_corpus_reads(monkeypatch)
    g = build_graph(md, index_dir=idx)
    assert g is not None  # cache is fresh again -> served with zero corpus reads
    assert g.adjacency["delta"] == {"alpha"}


def test_refresh_noop_backfills_missing_cache(tmp_path, monkeypatch):
    """A pre-GRA-6 index (manifest present, links.json absent) on a quiet corpus: the
    no-op path must create the cache rather than starve it until a corpus edit."""
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _corpus(md)
    _build(md, idx, monkeypatch)
    os.remove(os.path.join(idx, "links.json"))

    assert B.refresh_index(md, idx) is not None
    assert os.path.exists(os.path.join(idx, "links.json"))
    _poison_corpus_reads(monkeypatch)
    assert build_graph(md, index_dir=idx) is not None


# --------------------------------------------------------------------------- #
# fallback: corrupt / wrong-schema / absent caches degrade, never raise
# --------------------------------------------------------------------------- #
def test_corrupt_links_json_falls_back_cleanly(tmp_path, monkeypatch):
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _corpus(md)
    _build(md, idx, monkeypatch)
    cold = LinkGraph(md)

    with open(os.path.join(idx, "links.json"), "w", encoding="utf-8") as fh:
        fh.write("{ truncated garbage")
    g = build_graph(md, index_dir=idx)  # must NOT raise, must NOT be None
    assert g is not None
    assert _graph_state(g) == _graph_state(cold)


def test_valid_json_wrong_shape_falls_back(tmp_path, monkeypatch):
    """Valid JSON whose ``outbound`` is a STRING must not iterate as characters into a
    garbage adjacency — it's a cache miss like any other corruption."""
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _corpus(md)
    _build(md, idx, monkeypatch)
    cold = LinkGraph(md)

    p = os.path.join(idx, "links.json")
    with open(p, encoding="utf-8") as fh:
        payload = json.load(fh)
    payload["files"]["alpha"]["outbound"] = "beta"  # wrong shape, sigs still fresh
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)

    g = build_graph(md, index_dir=idx)
    assert g is not None and _graph_state(g) == _graph_state(cold)
    assert load_edges(idx) is None


def test_wrong_schema_version_falls_back(tmp_path, monkeypatch):
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _corpus(md)
    _build(md, idx, monkeypatch)
    p = os.path.join(idx, "links.json")
    with open(p, encoding="utf-8") as fh:
        payload = json.load(fh)
    payload["schema_version"] = LK.LINKS_SCHEMA_VERSION + 1
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)

    _poison_corpus_reads(monkeypatch)
    # cached path refuses the foreign schema; the (poisoned) fallback then fails -> None.
    assert build_graph(md, index_dir=idx) is None


def test_file_added_or_removed_invalidates(tmp_path, monkeypatch):
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _corpus(md)
    _build(md, idx, monkeypatch)

    _write(md, "zeta.md", _mem("zeta", "links to [[alpha]]"))
    g = build_graph(md, index_dir=idx)  # stem-set mismatch -> full re-read
    assert g is not None and "zeta" in g.files and g.adjacency["zeta"] == {"alpha"}

    os.remove(os.path.join(md, "zeta.md"))
    os.remove(os.path.join(md, "beta.md"))
    g2 = build_graph(md, index_dir=idx)
    assert g2 is not None and "beta" not in g2.files


def test_build_graph_without_index_dir_keeps_full_read_behavior(tmp_path):
    md = str(tmp_path / "memory")
    _corpus(md)
    g = build_graph(md)  # no index_dir -> the original full-read path, no cache involved
    assert g is not None and g.resolve("beta") == "beta"
    assert build_graph("/no/such/dir/xyz", index_dir="/no/such/index") is None


# --------------------------------------------------------------------------- #
# load_edges — the O(1) loader for recall-time expansion (GRA-1)
# --------------------------------------------------------------------------- #
def test_load_edges_returns_both_directions(tmp_path, monkeypatch):
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _corpus(md)
    _build(md, idx, monkeypatch)

    edges = load_edges(idx)
    assert edges is not None
    assert edges["alpha"]["out"] == {"beta", "feedback_gamma_thing"}
    assert edges["alpha"]["in"] == {"beta"}
    assert edges["beta"] == {"out": {"alpha"}, "in": {"alpha"}}
    assert edges["delta"] == {"out": set(), "in": {"feedback_gamma_thing"}}
    # every corpus stem is present, even the fully unlinked ones
    assert edges["project_api_keys"] == {"out": set(), "in": set()}


def test_load_edges_reads_links_json_only(tmp_path, monkeypatch):
    """No corpus scan, no stat sweep — a deleted memory dir must not matter."""
    import shutil

    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _corpus(md)
    _build(md, idx, monkeypatch)
    shutil.rmtree(md)  # the corpus is GONE; links.json alone must suffice
    edges = load_edges(idx)
    assert edges is not None and edges["beta"]["in"] == {"alpha"}


def test_load_edges_none_on_absence_or_corruption(tmp_path):
    assert load_edges(str(tmp_path / "nowhere")) is None
    idx = str(tmp_path / "idx")
    os.makedirs(idx)
    with open(os.path.join(idx, "links.json"), "w", encoding="utf-8") as fh:
        fh.write("not json at all")
    assert load_edges(idx) is None


# --------------------------------------------------------------------------- #
# the acceptance criterion end-to-end: SessionStart = ONE corpus read
# --------------------------------------------------------------------------- #
def test_sessionstart_lint_producer_reads_no_memory_files_after_refresh(tmp_path, monkeypatch):
    """refresh_index's own read is THE one corpus read: after it runs (as the dispatcher
    does before producers), the link-health producer must serve from the cache — zero
    opens of memory files — and still report the same rot as a cold lint."""
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    _corpus(md)
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    # the producer derives the index dir itself (default_index_dir honors this override)
    monkeypatch.setenv("MEMOBOT_INDEX_DIR", idx)
    assert B.refresh_index(md, idx) is not None

    cold_line = L.health_line(L.lint(md))  # corpus has dangling links -> non-None

    opened: list = []
    real_open = builtins.open

    def spy(file, *a, **k):
        try:
            p = os.fspath(file)
        except TypeError:
            p = ""
        if isinstance(p, str) and p.startswith(md):
            opened.append(p)
        return real_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    line = L.lint_links_producer(md, md)
    assert opened == []  # zero memory-file reads on the producer's path
    assert line == cold_line and line is not None
