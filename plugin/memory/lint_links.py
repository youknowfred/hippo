"""Wikilink integrity linter for agent-memory files (Tier 3 of the activation roadmap).

Reports five classes of link rot the corpus census found, READ-ONLY and idempotent —
it NEVER edits a memory file:
  - dangling      : a ``[[target]]`` that resolves to NO file (after slug normalization).
  - ambiguous     : a ``[[target]]`` whose soft alias is claimed by TWO OR MORE files
                    (COR-9) — resolve() refuses it rather than guess, and the lint line
                    names every claimant so the fix (link the full stem) is obvious.
  - slug-mismatch : a ``[[target]]`` that DOES resolve, but only via a soft alias
                    (prefix-strip / ``name:`` slug) rather than the canonical filename stem —
                    i.e. it works today but is written in a non-canonical form.
  - typed-dangling: a typed relation target (GRA-4 — ``supersedes``/``contradicts``/
                    ``refines`` frontmatter) that resolves to NO memory. Worse than a
                    dangling wikilink: a dangling ``supersedes`` silently disables the
                    demotion/annotation it was written to cause. Ambiguous typed targets
                    land here too, with the claimants named (same COR-9 refusal).
  - orphan        : a memory with zero OUTBOUND wikilinks (nothing points out of it).

Surfaces a one-line health note for the SessionStart dispatcher (``session_start.py``).
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from .links import TYPED_RELATIONS, LinkGraph, build_graph
from .staleness import RunContext


def lint(memory_dir: str, index_dir: Optional[str] = None) -> dict:
    """Return a report dict. Pure / never raises / never writes.

    ``index_dir`` (GRA-6) opts into ``build_graph``'s persisted-cache fast path — the
    SessionStart producer passes it so the dispatcher's ONE corpus read stays the index
    refresh's own; a fresh cache makes this a stat sweep with zero file reads. The CLI
    keeps the full re-read (a diagnostic should read the source of authority directly).
    """
    g = build_graph(memory_dir, index_dir=index_dir)
    if g is None:
        return {
            "ok": False,
            "dangling": [],
            "ambiguous": [],
            "slug_mismatch": [],
            "typed_dangling": [],
            "orphans": [],
            "files": 0,
        }
    return _graph_report(g)


def _graph_report(g: LinkGraph) -> dict:
    """Assemble the finding classes from a built graph — shared by ``lint()`` (the full
    corpus) and ``boundary_lint()`` (the committed-subset view), so the two surfaces can
    never diverge on what counts as rot (PUB-3: no new parser, no new lint classes)."""
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

    # Typed relations (GRA-4) whose target resolves to no memory. `claimants` is non-empty
    # when the target is an AMBIGUOUS alias (two files claim it — the fix is disambiguating,
    # not creating) and empty when it is genuinely dangling — one finding class, but the
    # line still tells the user which repair applies.
    typed_dangling: List[dict] = []
    for fname, rels in sorted(g.typed_unresolved.items()):
        for rel, targets in sorted(rels.items()):
            for t in targets:
                typed_dangling.append(
                    {
                        "file": fname,
                        "relation": rel,
                        "target": t,
                        "claimants": g.ambiguous_claimants(t),
                    }
                )

    return {
        "ok": True,
        "files": len(g.files),
        "edges": sum(len(v) for v in g.adjacency.values()),
        "typed_edges": sum(len(t) for m in g.typed.values() for t in m.values()),
        "dangling": dangling,
        "ambiguous": ambiguous,
        "slug_mismatch": slug_mismatch,
        "typed_dangling": typed_dangling,
        "orphans": g.orphans(),
    }


def boundary_lint(memory_dir: str, repo_root: str) -> dict:
    """PUB-3: the committed-subset boundary view — the graph a fresh checkout sees.

    Committed memories wikilink local-only memories: those links resolve for the owner
    and dangle in every stranger's clone. This is a VIEW, not a detector — membership
    comes from ``provenance.build_repo_file_index`` (imported; the pub lane's
    single-homed oracle, the SHP-1 precedent — never a fresh membership listing) and
    the machinery is ``LinkGraph``/``_graph_report`` over the committed files' texts.
    ``heals_by`` counts, per LOCAL-ONLY memory, how many boundary danglings publishing
    it would heal — the per-candidate column PUB-2's report composes. ``introduces_by``
    (BND-1) is its twin, computed in the SAME walk: per local-only memory, how many of
    its own outbound targets (plain wikilinks + typed relations) resolve to OTHER
    local-only memories — each a NEW boundary dangling the moment it publishes. The
    measured treadmill (20 -> 19 across two real publishes) was invisible without this
    half. NEVER a gate on either number (PR #67 called the class expected-not-error;
    CLB-1 keeps edge findings advisory; the publish-hard-gate kill re-affirms
    introduces/net as receipt vocabulary FOREVER); empty norms: no git / no committed
    subset -> ``ok`` False and the callers render their quiet line. Read-only; never
    raises.
    """
    empty = {
        "ok": False,
        "files": 0,
        "dangling": [],
        "typed_dangling": [],
        "heals_by": {},
        "introduces_by": {},
        "local_only": [],
    }
    try:
        from .provenance import _iter_memory_files, build_repo_file_index, run_git

        top = run_git(["rev-parse", "--show-toplevel"], repo_root).strip() or repo_root
        repo_files, _basenames = build_repo_file_index(repo_root)
        if not repo_files:
            return empty
        mem_rel = os.path.relpath(os.path.realpath(memory_dir), os.path.realpath(top))
        committed_texts: Dict[str, str] = {}
        local_only: List[str] = []
        for path in _iter_memory_files(memory_dir):
            base = os.path.basename(path)
            stem = os.path.splitext(base)[0]
            rel = base if mem_rel == "." else f"{mem_rel}/{base}"
            if rel not in repo_files:
                local_only.append(stem)
                continue
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    committed_texts[stem] = fh.read()
            except Exception:
                continue
        if not committed_texts:
            return empty
        report = _graph_report(LinkGraph(memory_dir, texts=committed_texts))
        # Join each boundary dangling against the FULL graph: a target that resolves
        # there points at a local-only memory — the candidate whose publication heals it.
        full = build_graph(memory_dir)
        heals_by: Dict[str, int] = {}
        for d in report["dangling"] + report["typed_dangling"]:
            full_stem = full.resolve(d["target"]) if full else None
            if full_stem and full_stem not in committed_texts:
                heals_by[full_stem] = heals_by.get(full_stem, 0) + 1
        # BND-1: the introduces twin, same walk — a candidate's own outbound edges
        # (plain + typed, mirroring the two dangling classes above) that resolve to
        # OTHER local-only memories each become a boundary dangling when it lands.
        introduces_by: Dict[str, int] = {}
        if full:
            for stem in local_only:
                n = sum(
                    1 for t in full.outbound(stem) if t != stem and t not in committed_texts
                ) + sum(
                    1
                    for rel in TYPED_RELATIONS
                    for t in full.typed_outbound(stem, rel)
                    if t != stem and t not in committed_texts
                )
                if n:
                    introduces_by[stem] = n
        return {
            "ok": True,
            "files": report["files"],
            "dangling": report["dangling"],
            "typed_dangling": report["typed_dangling"],
            "heals_by": heals_by,
            "introduces_by": introduces_by,
            "local_only": sorted(local_only),
        }
    except Exception:
        return empty


def health_line(report: dict) -> Optional[str]:
    """One-line link-health summary for the SessionStart producer; None when clean."""
    if not report.get("ok"):
        return None
    n_dangling = len(report.get("dangling", []))
    n_ambiguous = len(report.get("ambiguous", []))
    n_mismatch = len(report.get("slug_mismatch", []))
    n_typed = len(report.get("typed_dangling", []))
    n_orphans = len(report.get("orphans", []))
    if n_dangling == 0 and n_ambiguous == 0 and n_mismatch == 0 and n_typed == 0:
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
    if n_typed:
        # A dangling typed relation is REAL rot with TEETH — a supersedes edge that
        # resolves to nothing silently disables the demotion it exists to cause (GRA-4),
        # so it is loud at SessionStart like dangling/ambiguous wikilinks.
        examples = ", ".join(f"{d['relation']}: {d['target']}" for d in report["typed_dangling"][:3])
        more = "" if n_typed <= 3 else f" (+{n_typed - 3} more)"
        bits.append(f"{n_typed} dangling typed relation target(s): {examples}{more}")
    # GRA-7: carry the qualifier. Bare "3 orphan memo(s)" reads as rot beside the genuine rot
    # it is appended to, and it is not — this module's own header defines an orphan as "a
    # memory with zero OUTBOUND wikilinks (nothing points out of it)", which is an ordinary
    # state for a leaf memory and is why orphans alone deliberately do not fire this line at
    # all (see the early return above). Say which thing it is.
    tail = f"; {n_orphans} orphan memo(s) (no outbound links)" if n_orphans else ""
    return "🔗 Memory link health — " + "; ".join(bits) + tail + " (run `memory.lint_links`)."


def lint_links_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """SessionStart producer (signature matches the dispatcher). Self-suppresses when clean.

    GRA-6: routes through the persisted edge cache so SessionStart performs ONE corpus
    read total — the dispatcher's ``refresh_index`` side effect reads the corpus and
    (re)persists ``links.json`` before producers run, so this lint reconstructs the graph
    from the cache with zero file reads. A missing/stale cache silently falls back to the
    full re-read inside ``build_graph`` (correctness never depends on the cache). ``ctx``
    (LIF-6's shared per-run ``RunContext``) is unused here — declared only so every
    producer in ``PRODUCERS`` shares ONE call shape.
    """
    try:
        from .build_index import default_index_dir

        index_dir: Optional[str] = default_index_dir(memory_dir)
    except Exception:
        index_dir = None
    return health_line(lint(memory_dir, index_dir=index_dir))


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    from .provenance import resolve_dirs

    parser = argparse.ArgumentParser(description="Lint memory wikilinks (read-only).")
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--show-orphans", action="store_true")
    parser.add_argument(
        "--boundary",
        action="store_true",
        help="PUB-3: evaluate the COMMITTED-subset view — the links a fresh checkout "
        "sees dangle, with per-candidate heals-N (never a gate)",
    )
    args = parser.parse_args(argv)

    md, rr = resolve_dirs()
    md = args.memory_dir or md

    if args.boundary:
        view = boundary_lint(md, rr)
        if not view["ok"] or not view["files"]:
            print("no committed memory subset — nothing to check.")
            return 0
        n_d, n_t = len(view["dangling"]), len(view["typed_dangling"])
        print(f"committed files={view['files']} boundary-dangling={n_d} typed={n_t}")
        for d in view["dangling"]:
            print(f"  ✗ {d['file']} -> [[{d['target']}]]")
        for d in view["typed_dangling"]:
            print(f"  ✗ {d['file']} -> {d['relation']}: {d['target']}")
        heals, intro = view["heals_by"], view.get("introduces_by") or {}
        if heals or intro:
            print("heals-N (publishing the local-only memory heals N boundary danglings):")
            # BND-1: rows carry the introduces twin + net when nonzero; a corpus with
            # zero introduces renders byte-identically to the pre-BND-1 block (pinned).
            for stem in sorted(set(heals) | set(intro), key=lambda s: (-heals.get(s, 0), s)):
                n, m = heals.get(stem, 0), intro.get(stem, 0)
                suffix = f"   introduces {m} (net {m - n:+d})" if m else ""
                print(f"  {n:3d}  {stem}{suffix}")
        # READ-ONLY and NEVER a gate: the boundary class is expected-not-error (PR #67);
        # introduces/net is receipt vocabulary FOREVER (the publish-hard-gate kill).
        return 0

    report = lint(md)
    if not report["ok"]:
        print("could not build link graph")
        return 1

    print(f"files={report['files']} edges={report['edges']} typed={report.get('typed_edges', 0)}")
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
    print(f"typed dangling   : {len(report.get('typed_dangling', []))}")
    for d in report.get("typed_dangling", []):
        # Ambiguous typed targets carry claimants (disambiguate); genuinely-dangling don't.
        who = f" (claimed by {', '.join(d['claimants'])})" if d.get("claimants") else ""
        print(f"  ✗ {d['file']} -> {d['relation']}: {d['target']}{who}")
    print(f"orphans (no outbound links): {len(report['orphans'])}")
    if args.show_orphans:
        for o in report["orphans"]:
            print(f"  · {o}")
    # READ-ONLY: a linter never fails a workflow on findings.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
