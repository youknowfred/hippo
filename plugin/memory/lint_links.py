"""Wikilink integrity linter for agent-memory files (Tier 3 of the activation roadmap).

Reports four classes of link rot the corpus census found, READ-ONLY and idempotent —
it NEVER edits a memory file:
  - dangling     : a ``[[target]]`` that resolves to NO file (after slug normalization).
  - ambiguous    : a ``[[target]]`` whose soft alias is claimed by TWO OR MORE files
                   (COR-9) — resolve() refuses it rather than guess, and the lint line
                   names every claimant so the fix (link the full stem) is obvious.
  - slug-mismatch: a ``[[target]]`` that DOES resolve, but only via a soft alias
                   (prefix-strip / ``name:`` slug) rather than the canonical filename stem —
                   i.e. it works today but is written in a non-canonical form.
  - orphan       : a memory with zero OUTBOUND wikilinks (nothing points out of it).

Surfaces a one-line health note for the SessionStart dispatcher (``session_start.py``).
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from .links import LinkGraph, build_graph


def lint(memory_dir: str) -> dict:
    """Return a report dict. Pure / never raises / never writes."""
    g = build_graph(memory_dir)
    if g is None:
        return {
            "ok": False,
            "dangling": [],
            "ambiguous": [],
            "slug_mismatch": [],
            "orphans": [],
            "files": 0,
        }

    # unresolved holds BOTH failure modes (resolve() returns None for each); split them
    # here because the remedies differ — dangling means "nothing claims this", ambiguous
    # means "two files claim it, refuse to guess" (COR-9). Reporting an ambiguous target
    # as dangling would send the user hunting for a file that already exists twice.
    dangling: List[dict] = []
    ambiguous: List[dict] = []
    for fname, missed in sorted(g.unresolved.items()):
        for t in missed:
            claimants = g.ambiguous_claimants(t)
            if claimants:
                ambiguous.append({"file": fname, "target": t, "claimants": claimants})
            else:
                dangling.append({"file": fname, "target": t})

    slug_mismatch: List[dict] = []
    for fname in g.files:
        for t in g.raw_targets.get(fname, []):
            resolved = g.resolve(t)
            if resolved and not g.resolved_via_stem(t):
                slug_mismatch.append(
                    {"file": fname, "target": t, "resolves_to": resolved}
                )

    return {
        "ok": True,
        "files": len(g.files),
        "edges": sum(len(v) for v in g.adjacency.values()),
        "dangling": dangling,
        "ambiguous": ambiguous,
        "slug_mismatch": slug_mismatch,
        "orphans": g.orphans(),
    }


def health_line(report: dict) -> Optional[str]:
    """One-line link-health summary for the SessionStart producer; None when clean."""
    if not report.get("ok"):
        return None
    n_dangling = len(report.get("dangling", []))
    n_ambiguous = len(report.get("ambiguous", []))
    n_mismatch = len(report.get("slug_mismatch", []))
    n_orphans = len(report.get("orphans", []))
    if n_dangling == 0 and n_ambiguous == 0 and n_mismatch == 0:
        return None  # orphans alone are informational, not rot — don't nag every session
    bits = []
    if n_dangling:
        examples = ", ".join(d["target"] for d in report["dangling"][:3])
        more = "" if n_dangling <= 3 else f" (+{n_dangling - 3} more)"
        bits.append(f"{n_dangling} dangling [[wikilink]] target(s): {examples}{more}")
    if n_ambiguous:
        # An ambiguous link is REAL rot — it resolves to nothing until disambiguated —
        # so it must be loud at SessionStart (legible-degradation invariant), same as
        # dangling.
        examples = ", ".join(d["target"] for d in report["ambiguous"][:3])
        more = "" if n_ambiguous <= 3 else f" (+{n_ambiguous - 3} more)"
        bits.append(f"{n_ambiguous} ambiguous [[wikilink]] target(s): {examples}{more}")
    if n_mismatch:
        bits.append(f"{n_mismatch} non-canonical (slug-mismatch) link(s)")
    tail = f"; {n_orphans} orphan memo(s)" if n_orphans else ""
    return "🔗 Memory link health — " + "; ".join(bits) + tail + " (run `memory.lint_links`)."


def lint_links_producer(memory_dir: str, repo_root: str) -> Optional[str]:
    """SessionStart producer (signature matches the dispatcher). Self-suppresses when clean."""
    return health_line(lint(memory_dir))


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    from .provenance import resolve_dirs

    parser = argparse.ArgumentParser(description="Lint memory wikilinks (read-only).")
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--show-orphans", action="store_true")
    args = parser.parse_args(argv)

    md, _ = resolve_dirs()
    md = args.memory_dir or md
    report = lint(md)
    if not report["ok"]:
        print("could not build link graph")
        return 1

    print(f"files={report['files']} edges={report['edges']}")
    print(f"dangling targets : {len(report['dangling'])}")
    for d in report["dangling"]:
        print(f"  ✗ {d['file']} -> [[{d['target']}]]")
    print(f"ambiguous targets: {len(report['ambiguous'])}")
    for d in report["ambiguous"]:
        # Name the alias AND every claimant — the fix is linking a full stem instead.
        print(f"  ? {d['file']} -> [[{d['target']}]] (claimed by {', '.join(d['claimants'])})")
    print(f"slug mismatches  : {len(report['slug_mismatch'])}")
    for d in report["slug_mismatch"][:50]:
        print(f"  ~ {d['file']} -> [[{d['target']}]] (resolves to {d['resolves_to']})")
    print(f"orphans (no outbound links): {len(report['orphans'])}")
    if args.show_orphans:
        for o in report["orphans"]:
            print(f"  · {o}")
    # READ-ONLY: a linter never fails a workflow on findings.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
