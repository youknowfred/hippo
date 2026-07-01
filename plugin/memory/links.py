"""Wikilink graph for agent-memory files (Tier 3 of the activation roadmap).

Parses ``[[name]]`` markers across the corpus into an adjacency graph and resolves each
target to a memory file — slug-normalizing so ``_``/``-`` variants and a dropped category
prefix resolve (e.g. ``[[151-avenue-a-is-standard-size]]`` →
``feedback_151_avenue_a_is_standard_size.md``) WITHOUT falsely resolving genuinely-absent
targets (``[[ship-roadmap]]``, ``[[decision-question-coverage]]``).

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
from typing import Dict, List, Optional, Set, Tuple

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
    """Resolved wikilink adjacency over the memory corpus."""

    def __init__(self, memory_dir: str):
        self.memory_dir = memory_dir
        self.files: List[str] = []  # basenames
        self._alias_to_file: Dict[str, str] = {}
        self._ambiguous: Set[str] = set()
        self.raw_targets: Dict[str, List[str]] = {}  # file -> raw [[targets]]
        self.adjacency: Dict[str, Set[str]] = {}  # file -> resolved target files
        self.unresolved: Dict[str, List[str]] = {}  # file -> raw targets that didn't resolve
        self._build()

    # -- construction ----------------------------------------------------- #
    def _register_alias(self, alias: str, fname: str, *, allow_collision: bool) -> None:
        if not alias:
            return
        if alias in self._alias_to_file:
            if self._alias_to_file[alias] != fname and not allow_collision:
                # Two files claim this alias -> ambiguous; drop it so it can't false-resolve.
                self._ambiguous.add(alias)
            return
        self._alias_to_file[alias] = fname

    def _build(self) -> None:
        texts: Dict[str, str] = {}
        stems: Dict[str, str] = {}  # fname -> stem
        for path in _iter_memory_files(self.memory_dir):
            fname = os.path.basename(path)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    texts[fname] = fh.read()
            except Exception:
                continue
            self.files.append(fname)
            stems[fname] = os.path.splitext(fname)[0]

        # Pass 1: full-stem aliases (unique by construction) — highest-confidence.
        for fname, stem in stems.items():
            self._register_alias(normalize_slug(stem), fname, allow_collision=False)

        # Pass 2: prefix-stripped + name-slug aliases — register only when globally unique.
        for fname, stem in stems.items():
            stripped = _strip_first_segment(stem)
            if stripped and stripped not in self._alias_to_file:
                self._register_alias(stripped, fname, allow_collision=False)
            fm = parse_frontmatter(texts.get(fname, ""))
            name = fm.get("name") if isinstance(fm, dict) else None
            if isinstance(name, str):
                slug = normalize_slug(name)
                if slug and slug not in self._alias_to_file:
                    self._register_alias(slug, fname, allow_collision=False)

        for amb in self._ambiguous:
            self._alias_to_file.pop(amb, None)

        # Edges
        for fname in self.files:
            targets = parse_wikilinks(texts.get(fname, ""))
            self.raw_targets[fname] = targets
            resolved: Set[str] = set()
            missed: List[str] = []
            for t in targets:
                f = self.resolve(t)
                if f and f != fname:
                    resolved.add(f)
                elif f is None:
                    missed.append(t)
            self.adjacency[fname] = resolved
            if missed:
                self.unresolved[fname] = missed

    # -- queries ---------------------------------------------------------- #
    def resolve(self, target: str) -> Optional[str]:
        """Resolve a ``[[target]]`` (or filename) to a corpus basename, or None."""
        slug = normalize_slug(target)
        if slug in self._ambiguous:
            return None
        return self._alias_to_file.get(slug)

    def resolved_via_stem(self, target: str) -> bool:
        """True when ``target`` matches a file's canonical full stem (not a soft alias)."""
        f = self.resolve(target)
        return bool(f) and normalize_slug(os.path.splitext(f)[0]) == normalize_slug(target)

    def outbound(self, name: str) -> Set[str]:
        f = self.resolve(name) or (name if name in self.adjacency else None)
        return set(self.adjacency.get(f, set())) if f else set()

    def traverse(self, name: str, hops: int = 1) -> Set[str]:
        """Files reachable from ``name`` within ``hops`` outbound edges (excludes ``name``)."""
        start = self.resolve(name) or (name if name in self.adjacency else None)
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
        """Files with zero OUTBOUND resolved links (newest-name-last; sorted)."""
        return sorted(f for f in self.files if not self.adjacency.get(f))


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
    print(f"files={len(g.files)} edges={total_edges} orphans={len(g.orphans())}")
    if args.traverse:
        reach = g.traverse(args.traverse, hops=args.hops)
        print(f"reachable from {args.traverse} within {args.hops} hops ({len(reach)}):")
        for f in sorted(reach):
            print(f"  - {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
