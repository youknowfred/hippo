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
  2. the stem with its FIRST ``_``/``-`` segment stripped   (registered only if globally
                                                             unique → no false positives)
  3. the frontmatter ``name:`` slug        (registered only if globally unique)

Pure / read-only; never raises into a caller.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Set

from .provenance import _iter_memory_files, parse_frontmatter

_WIKILINK_RE = re.compile(r"\[\[([^\]\[]+?)\]\]")


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

    def __init__(self, memory_dir: str):
        self.memory_dir = memory_dir
        self.files: List[str] = []  # stems (unique — one flat dir, one stem per file)
        self._alias_to_stem: Dict[str, str] = {}
        self._ambiguous: Set[str] = set()
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
                # Two files claim this alias -> ambiguous; drop it so it can't false-resolve.
                self._ambiguous.add(alias)
            return
        self._alias_to_stem[alias] = stem

    def _build(self) -> None:
        texts: Dict[str, str] = {}  # stem -> file text
        for path in _iter_memory_files(self.memory_dir):
            stem = os.path.splitext(os.path.basename(path))[0]
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    texts[stem] = fh.read()
            except Exception:
                continue
            self.files.append(stem)

        # Pass 1: full-stem aliases (unique by construction) — highest-confidence.
        for stem in self.files:
            self._register_alias(normalize_slug(stem), stem, allow_collision=False)

        # Pass 2: prefix-stripped + name-slug aliases — register only when globally unique.
        for stem in self.files:
            stripped = _strip_first_segment(stem)
            if stripped and stripped not in self._alias_to_stem:
                self._register_alias(stripped, stem, allow_collision=False)
            fm = parse_frontmatter(texts.get(stem, ""))
            name = fm.get("name") if isinstance(fm, dict) else None
            if isinstance(name, str):
                slug = normalize_slug(name)
                if slug and slug not in self._alias_to_stem:
                    self._register_alias(slug, stem, allow_collision=False)

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


def build_graph(memory_dir: str) -> Optional[LinkGraph]:
    try:
        return LinkGraph(memory_dir)
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
