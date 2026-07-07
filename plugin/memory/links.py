"""Wikilink graph for agent-memory files (Tier 3 of the activation roadmap).

Parses ``[[name]]`` markers across the corpus into an adjacency graph and resolves each
target to a memory file — slug-normalizing so ``_``/``-`` variants and a dropped category
prefix resolve (e.g. ``[[151-avenue-a-is-standard-size]]`` →
``feedback_151_avenue_a_is_standard_size``) WITHOUT falsely resolving genuinely-absent
targets (``[[ship-roadmap]]``, ``[[decision-question-coverage]]``).

Node identity is the filename STEM (``foo``, never ``foo.md``) — a deliberate clean break
(GRA-2, one-canonical-name invariant): every other module that joins against graph output
(staleness / soak / archive / telemetry) keys by stem, so basename output forced each
consumer to strip ``.md`` by hand before every join, and two of them got it subtly
different. ``files``, ``adjacency`` (keys AND values), ``raw_targets``, ``unresolved`` and
every query method speak stems; nothing in graph output carries a ``.md`` suffix.

Resolution aliases per file (all normalized to lower-hyphen):
  1. the full filename stem               (always registered — filenames are unique)
  2. the stem with its FIRST ``_``/``-`` segment stripped
  3. the frontmatter ``name:`` slug

Tiering (COR-9): a full-stem claim (tier 1) always beats a soft claim (tiers 2–3) — a
soft alias that collides with an existing full-stem alias is simply not registered and
never poisons the full-stem claim. WITHIN the soft tier, aliases are registered
UNCONDITIONALLY so that two DIFFERENT files claiming the same soft alias land in
``_ambiguous`` and ``resolve()`` refuses both (previously the alphabetically-first file
silently won and ``[[target]]`` resolved to the wrong memory with no signal). The same
file claiming one alias twice (its stripped stem == its ``name:`` slug) is NOT a
collision. Ambiguous claimants are tracked so the linter can name both files.

Persisted edge cache (GRA-6): ``build_index`` writes the fully-resolved graph to
``links.json`` inside the index dir, keyed by a per-file STAT signature
``[st_mtime_ns, st_size]`` — deliberately NOT the manifest's ``doc_text`` hash, because
wikilinks live in BODIES and a body edit does not change ``doc_text`` (name + description
only); a hash-keyed cache would go silently stale on exactly the edits that change edges.
``build_graph(memory_dir, index_dir=...)`` reconstructs the graph from that cache after ONE
stat sweep (zero file reads); any mismatch/corruption falls back to the full re-read.
``load_edges`` is the O(1) recall-time loader (GRA-1): links.json only, no corpus scan —
recall tolerates a slightly-stale edge list the same way it tolerates a stale index.

Pure / read-only; never raises into a caller.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional, Set

from .provenance import _is_memory_filename, _iter_memory_files, parse_frontmatter

_WIKILINK_RE = re.compile(r"\[\[([^\]\[]+?)\]\]")

# links.json schema — independent of the manifest's SCHEMA_VERSION (the two files evolve
# separately; a manifest bump must not silently invalidate a perfectly good edge cache).
LINKS_SCHEMA_VERSION = 1
_LINKS_CACHE_NAME = "links.json"


def normalize_slug(s: str) -> str:
    """Lowercase; unify ``_``, spaces and runs of separators to single ``-``; trim."""
    s = (s or "").strip().lower()
    if s.endswith(".md"):
        s = s[:-3]
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")


def _strip_first_segment(stem: str) -> Optional[str]:
    """``feedback_151_avenue...`` -> ``151-avenue...`` (normalized); None if no segment."""
    norm = normalize_slug(stem)
    parts = norm.split("-", 1)
    return parts[1] if len(parts) == 2 and parts[1] else None


def parse_wikilinks(text: str) -> List[str]:
    """Ordered, de-duped ``[[target]]`` targets in ``text`` (``|display`` + ``#anchor`` stripped)."""
    seen: Set[str] = set()
    out: List[str] = []
    for m in _WIKILINK_RE.finditer(text or ""):
        raw = m.group(1).strip()
        raw = raw.split("|", 1)[0].split("#", 1)[0].strip()
        if raw and raw not in seen:
            seen.add(raw)
            out.append(raw)
    return out


class LinkGraph:
    """Resolved wikilink adjacency over the memory corpus. All nodes are STEMS."""

    def __init__(self, memory_dir: str, texts: Optional[Dict[str, str]] = None):
        self.memory_dir = memory_dir
        # GRA-6: ``texts`` lets a caller that ALREADY read the corpus (build_index reads
        # every file's full text anyway) construct the graph with ZERO extra file reads.
        # Keys are stems or filenames ({filename-or-stem: text}); ``None`` keeps the
        # original read-from-disk behavior.
        self._texts_in = texts
        self.files: List[str] = []  # stems (unique — one flat dir, one stem per file)
        self._alias_to_stem: Dict[str, str] = {}
        # alias -> claimant stems, so lint can NAME the colliders (COR-9). Membership
        # checks ("alias in self._ambiguous") read identically to the old Set form.
        self._ambiguous: Dict[str, Set[str]] = {}
        # Aliases claimed at full-stem tier (pass 1). A soft claim colliding with one of
        # these is silently dropped — full-stem beats soft, and the losing soft claim
        # must never mark the winning full-stem alias ambiguous.
        self._stem_tier: Set[str] = set()
        self.raw_targets: Dict[str, List[str]] = {}  # stem -> raw [[targets]]
        self.adjacency: Dict[str, Set[str]] = {}  # stem -> resolved target stems
        self.unresolved: Dict[str, List[str]] = {}  # stem -> raw targets that didn't resolve
        # Reverse adjacency, built ONCE alongside the forward pass — the single place in
        # the codebase that inverts the graph (GRA-2 acceptance criterion). archive.py and
        # the audit skill consume it via inbound()/isolates() instead of re-inverting.
        self._inbound: Dict[str, Set[str]] = {}  # stem -> stems that link TO it
        self._build()

    # -- construction ----------------------------------------------------- #
    def _register_alias(self, alias: str, stem: str, *, allow_collision: bool) -> None:
        if not alias:
            return
        if alias in self._alias_to_stem:
            if self._alias_to_stem[alias] != stem and not allow_collision:
                # Two DIFFERENT files claim this alias -> ambiguous; record BOTH claimants
                # (linter names them) and drop the alias post-build so it can't
                # false-resolve. Same-file re-claims (stripped stem == name slug) fall
                # through the != check — not a collision.
                self._ambiguous.setdefault(alias, {self._alias_to_stem[alias]}).add(stem)
            return
        self._alias_to_stem[alias] = stem

    def _register_soft_alias(self, alias: str, stem: str) -> None:
        """Tier 2/3 registration (COR-9): unconditional WITHIN the soft tier.

        Full-stem beats soft: an alias already claimed at full-stem tier is skipped
        outright (no registration, no ambiguity — the soft loser must not poison the
        full-stem claim). Everything else registers unconditionally so same-tier
        collisions across different files land in ``_ambiguous`` instead of letting
        the alphabetically-first file silently win (the pre-COR-9 bug: the
        ``not in _alias_to_stem`` guard skipped registration entirely, so the
        collision was never even SEEN).
        """
        if not alias or alias in self._stem_tier:
            return
        self._register_alias(alias, stem, allow_collision=False)

    def _read_texts(self) -> Dict[str, str]:
        """Read every memory file into ``{stem: text}`` (unreadable files skipped)."""
        texts: Dict[str, str] = {}
        for path in _iter_memory_files(self.memory_dir):
            stem = os.path.splitext(os.path.basename(path))[0]
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    texts[stem] = fh.read()
            except Exception:
                continue
        return texts

    def _build(self) -> None:
        # GRA-6: alias/edge construction runs from an in-memory mapping so a caller that
        # already read the corpus (build_index) pays zero extra file reads. Cold path
        # (no texts supplied) reads from disk exactly as before.
        if self._texts_in is not None:
            # Normalize caller keys to stems ({filename-or-stem: text}); dict iteration
            # preserves the caller's insertion order, and build_index inserts in
            # _iter_memory_files order — so cached-vs-cold graphs carry the same node order.
            texts = {
                (k[:-3] if k.endswith(".md") else k): (v or "")
                for k, v in self._texts_in.items()
            }
        else:
            texts = self._read_texts()
        self.files = list(texts.keys())

        # Pass 1: full-stem aliases (unique by construction) — highest-confidence tier.
        # Every alias claimed here is immune to soft-tier interference below.
        for stem in self.files:
            slug = normalize_slug(stem)
            self._register_alias(slug, stem, allow_collision=False)
            self._stem_tier.add(slug)

        # Pass 2: prefix-stripped + name-slug aliases — soft tier, registered
        # UNCONDITIONALLY (COR-9) so same-tier collisions become ambiguous instead of
        # silently resolving to whichever file sorts first.
        for stem in self.files:
            stripped = _strip_first_segment(stem)
            if stripped:
                self._register_soft_alias(stripped, stem)
            fm = parse_frontmatter(texts.get(stem, ""))
            name = fm.get("name") if isinstance(fm, dict) else None
            if isinstance(name, str):
                self._register_soft_alias(normalize_slug(name), stem)

        for amb in self._ambiguous:
            self._alias_to_stem.pop(amb, None)

        # Edges — forward AND reverse in the same pass, so inbound-degree queries never
        # need a second O(V+E) inversion anywhere else.
        for stem in self.files:
            self._inbound.setdefault(stem, set())
        for stem in self.files:
            targets = parse_wikilinks(texts.get(stem, ""))
            self.raw_targets[stem] = targets
            resolved: Set[str] = set()
            missed: List[str] = []
            for t in targets:
                s = self.resolve(t)
                if s and s != stem:
                    resolved.add(s)
                    self._inbound[s].add(stem)
                elif s is None:
                    missed.append(t)
            self.adjacency[stem] = resolved
            if missed:
                self.unresolved[stem] = missed

    # -- queries ---------------------------------------------------------- #
    def resolve(self, target: str) -> Optional[str]:
        """Resolve a ``[[target]]`` (or filename/stem) to a corpus STEM, or None."""
        slug = normalize_slug(target)
        if slug in self._ambiguous:
            return None
        return self._alias_to_stem.get(slug)

    def ambiguous_claimants(self, target: str) -> List[str]:
        """Sorted claimant stems when ``target`` is an ambiguous alias; ``[]`` otherwise.

        The linter's window into WHY a target refused to resolve — an ambiguous
        ``[[target]]`` is a different failure from a dangling one (two files claim it
        vs. nobody does), and the fix is different (rename/disambiguate vs. create),
        so lint must be able to name both claimants.
        """
        return sorted(self._ambiguous.get(normalize_slug(target), set()))

    def resolved_via_stem(self, target: str) -> bool:
        """True when ``target`` matches a file's canonical full stem (not a soft alias)."""
        s = self.resolve(target)
        return bool(s) and normalize_slug(s) == normalize_slug(target)

    def _node(self, name: str) -> Optional[str]:
        """Resolve ``name`` (alias, stem, or filename) to a graph node stem, or None."""
        return self.resolve(name) or (name if name in self.adjacency else None)

    def outbound(self, name: str) -> Set[str]:
        """Stems ``name`` links TO. Accepts any resolvable alias; ``set()`` if unknown."""
        s = self._node(name)
        return set(self.adjacency.get(s, set())) if s else set()

    def inbound(self, name: str) -> Set[str]:
        """Stems that link TO ``name`` — "what refers to this memory?".

        Accepts any resolvable alias (like ``outbound()``); ``set()`` for an unknown name.
        Backed by the reverse adjacency built once in ``_build()`` — callers must never
        re-invert ``adjacency`` themselves.
        """
        s = self._node(name)
        return set(self._inbound.get(s, set())) if s else set()

    def traverse(self, name: str, hops: int = 1) -> Set[str]:
        """Stems reachable from ``name`` within ``hops`` outbound edges (excludes ``name``)."""
        start = self._node(name)
        if not start or hops < 1:
            return set()
        seen: Set[str] = {start}
        frontier: Set[str] = {start}
        for _ in range(hops):
            nxt: Set[str] = set()
            for node in frontier:
                for tgt in self.adjacency.get(node, set()):
                    if tgt not in seen:
                        seen.add(tgt)
                        nxt.add(tgt)
            if not nxt:
                break
            frontier = nxt
        seen.discard(start)
        return seen

    def orphans(self) -> List[str]:
        """Stems with zero OUTBOUND resolved links (sorted).

        Orphans-vs-isolates: an orphan points at nothing but may still be pointed AT
        (a well-cited leaf note is an orphan, and that's healthy); an isolate has no
        edges in EITHER direction — genuinely disconnected from the graph. Archive/audit
        gating cares about inbound degree, never orphan-hood alone.
        """
        return sorted(s for s in self.files if not self.adjacency.get(s))

    def isolates(self) -> List[str]:
        """Stems with zero inbound AND zero outbound edges (sorted) — fully disconnected.

        Strictly a subset of ``orphans()``; see the orphans() docstring for the
        distinction. This is the "graph-isolated watch-list" primitive the audit skill
        reports (informational only — never archive-eligible on its own).
        """
        return sorted(
            s for s in self.files if not self.adjacency.get(s) and not self._inbound.get(s)
        )


# --------------------------------------------------------------------------- #
# GRA-6: persisted edge cache (links.json in the index dir)
# --------------------------------------------------------------------------- #
def write_links_cache(index_dir: str, graph: LinkGraph, sigs: Dict[str, List[int]]) -> None:
    """Persist ``graph`` (+ per-file stat ``sigs``) to ``index_dir/links.json``. Never raises.

    ``sigs`` maps stem -> ``[st_mtime_ns, st_size]`` captured by the caller AT READ TIME
    (build_index stats each file just before reading it, so a write racing the build makes
    the sig look STALE on the next check — never fresh-but-wrong). The payload carries
    everything a ``LinkGraph`` view needs — including ``raw_targets``, which lint's
    slug-mismatch check reads and which is NOT derivable from the resolved adjacency —
    so a cache hit reproduces the full graph, not a lossy subset. Written atomically
    (tmp + ``os.replace``, the manifest's COR-12 pattern) so a reader never sees a torn
    file. A stem missing from ``sigs`` gets ``[0, 0]``, which can never match a real stat
    — it degrades to a cache miss, the safe direction.
    """
    try:
        payload = {
            "schema_version": LINKS_SCHEMA_VERSION,
            "files": {
                stem: {
                    "sig": list(sigs.get(stem) or (0, 0)),
                    "outbound": sorted(graph.adjacency.get(stem, ())),
                }
                for stem in graph.files
            },
            "alias_to_file": dict(graph._alias_to_stem),
            "ambiguous": {a: sorted(c) for a, c in graph._ambiguous.items()},
            "unresolved": {s: list(t) for s, t in graph.unresolved.items()},
            "raw_targets": {s: list(t) for s, t in graph.raw_targets.items()},
        }
        path = os.path.join(index_dir, _LINKS_CACHE_NAME)
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
    except Exception:
        pass  # a failed cache write must never break an index build


def _load_links_payload(index_dir: str) -> Optional[dict]:
    """Parse ``links.json`` -> payload dict, or None (missing / corrupt / wrong schema)."""
    try:
        with open(os.path.join(index_dir, _LINKS_CACHE_NAME), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict):
            return None
        if payload.get("schema_version") != LINKS_SCHEMA_VERSION:
            return None
        if not all(
            isinstance(payload.get(k), dict)
            for k in ("files", "alias_to_file", "ambiguous", "unresolved", "raw_targets")
        ):
            return None
        return payload
    except Exception:
        return None


def _stat_signatures(memory_dir: str) -> Optional[Dict[str, List[int]]]:
    """One ``os.scandir`` stat sweep -> ``{stem: [st_mtime_ns, st_size]}``; None on failure.

    NO file reads — this is the cached path's entire I/O cost. Deliberately NOT built on
    ``_iter_memory_files`` (which is the corpus READER's entry point and what the
    zero-reads test probes); the membership filter itself is still the one canonical
    ``_is_memory_filename``, so the sweep sees exactly the files the graph builder reads.
    """
    try:
        sigs: Dict[str, List[int]] = {}
        with os.scandir(memory_dir) as it:
            for entry in it:
                if not _is_memory_filename(entry.name):
                    continue
                st = entry.stat()
                sigs[entry.name[:-3]] = [st.st_mtime_ns, st.st_size]
        return sigs
    except Exception:
        return None


def links_cache_fresh(index_dir: str, sigs: Dict[str, List[int]]) -> bool:
    """True when ``links.json`` exists and its per-file sigs exactly match ``sigs``.

    Used by ``refresh_index``'s no-op short-circuit: the corpus-unchanged check compares
    ``doc_text`` hashes, which body edits do NOT perturb — so the short-circuit must
    independently verify the edge cache before skipping the rebuild, or a body-only edit
    (the exact kind that changes wikilinks) would leave a stale links.json in place forever.
    """
    payload = _load_links_payload(index_dir)
    if payload is None:
        return False
    try:
        cached = {s: list(rec.get("sig") or []) for s, rec in payload["files"].items()}
        return cached == {s: list(v) for s, v in sigs.items()}
    except Exception:
        return False


def _graph_from_payload(memory_dir: str, payload: dict) -> LinkGraph:
    """Reconstruct a full ``LinkGraph`` view from a validated cache payload (no I/O).

    Raises on any malformed field — ``build_graph`` treats that as a cache miss and falls
    back to the full re-read. The reverse adjacency is re-derived here (O(E), in-memory)
    rather than persisted: it is the exact transpose of ``outbound`` and storing both would
    just create a second copy that could disagree.
    """
    files_map = payload["files"]
    g = LinkGraph.__new__(LinkGraph)
    g.memory_dir = memory_dir
    g._texts_in = None
    g.files = list(files_map.keys())
    g._alias_to_stem = {str(a): str(s) for a, s in payload["alias_to_file"].items()}
    g._ambiguous = {str(a): set(c) for a, c in payload["ambiguous"].items()}
    # Only consulted during construction's soft-alias tiering, but kept coherent anyway —
    # it is trivially derivable and a half-initialized object invites subtle breakage.
    g._stem_tier = {normalize_slug(s) for s in g.files}
    g.raw_targets = {str(s): list(t) for s, t in payload["raw_targets"].items()}
    g.unresolved = {str(s): list(t) for s, t in payload["unresolved"].items()}
    g.adjacency = {}
    g._inbound = {stem: set() for stem in g.files}
    for stem, rec in files_map.items():
        outbound = rec["outbound"]
        if not isinstance(outbound, list):
            # Valid-JSON-but-wrong-shape corruption (e.g. a hand-edited string) would
            # otherwise iterate as CHARACTERS into a garbage adjacency — raise instead,
            # which build_graph treats as a cache miss.
            raise ValueError("outbound must be a list")
        out = set(outbound)
        g.adjacency[stem] = out
        for tgt in out:
            g._inbound.setdefault(tgt, set()).add(stem)
    return g


def build_graph(memory_dir: str, index_dir: Optional[str] = None) -> Optional[LinkGraph]:
    """Build the corpus link graph; with ``index_dir``, try the persisted cache first.

    Cached fast path (GRA-6): load ``links.json``, do ONE stat pass over the memory dir
    (zero file reads); if the stem set and every ``[st_mtime_ns, st_size]`` signature match,
    reconstruct the graph entirely from the cache. ANY discrepancy — missing/corrupt cache,
    added/removed file, any body edit — falls back to the full corpus re-read, so the cache
    can be wrong only in the cheap direction (a wasted rebuild), never the silent one
    (serving stale edges as fresh). Never raises; None only when even the fallback fails
    (e.g. the memory dir does not exist).
    """
    if index_dir:
        try:
            payload = _load_links_payload(index_dir)
            if payload is not None:
                sigs = _stat_signatures(memory_dir)
                if sigs is not None and sigs == {
                    s: list(rec.get("sig") or []) for s, rec in payload["files"].items()
                }:
                    return _graph_from_payload(memory_dir, payload)
        except Exception:
            pass  # any cache trouble -> full rebuild below
    try:
        return LinkGraph(memory_dir)
    except Exception:
        return None


def load_edges(index_dir: str) -> Optional[Dict[str, Dict[str, Set[str]]]]:
    """O(1)-load edge list for recall-time expansion (GRA-1): ``{stem: {"out", "in"}}``.

    Reads ``links.json`` ONLY — no corpus scan, no stat sweep. Recall-time expansion
    tolerates a slightly-stale edge list the same way it tolerates a stale index (the
    SessionStart refresh re-syncs both), and the hot path must not pay an O(N) stat sweep
    per prompt for freshness it does not need. Returns None (never raises) when the cache
    is absent/corrupt — the caller simply skips expansion.
    """
    try:
        payload = _load_links_payload(index_dir)
        if payload is None:
            return None
        files_map = payload["files"]
        edges: Dict[str, Dict[str, Set[str]]] = {
            str(stem): {"out": set(), "in": set()} for stem in files_map
        }
        for stem, rec in files_map.items():
            outbound = rec.get("outbound")
            if not isinstance(outbound, list):
                return None  # wrong-shape corruption -> absent, same as any other corruption
            for tgt in outbound:
                if tgt in edges:  # a resolved edge always targets a corpus stem
                    edges[stem]["out"].add(tgt)
                    edges[tgt]["in"].add(stem)
        return edges
    except Exception:
        return None


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    from .provenance import resolve_dirs

    parser = argparse.ArgumentParser(description="Inspect the memory wikilink graph.")
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--traverse", default=None, help="show files reachable from NAME")
    parser.add_argument("--hops", type=int, default=2)
    args = parser.parse_args(argv)

    md, _ = resolve_dirs()
    md = args.memory_dir or md
    g = build_graph(md)
    if g is None:
        print("could not build link graph")
        return 1
    total_edges = sum(len(v) for v in g.adjacency.values())
    print(
        f"files={len(g.files)} edges={total_edges} "
        f"orphans={len(g.orphans())} isolates={len(g.isolates())}"
    )
    if args.traverse:
        reach = g.traverse(args.traverse, hops=args.hops)
        print(f"reachable from {args.traverse} within {args.hops} hops ({len(reach)}):")
        for s in sorted(reach):
            print(f"  - {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
