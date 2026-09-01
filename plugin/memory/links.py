"""Wikilink graph for agent-memory files (Tier 3 of the activation roadmap) — the façade.

Decomposed at the module-size ratchet (GRF-6 round) into prefix-named siblings per
CONTRIBUTING "Code layout": the parse/resolve core (``normalize_slug``,
``parse_wikilinks``, ``parse_typed_relations``, ``parse_planned``, the DRM-2 dream-block
grammar, ``LinkGraph``) lives in ``links_graph.py``; the GRA-6 persisted-cache layer (the
``links.json`` schema, ``write_links_cache``/``load_edges``) lives in ``links_cache.py``.
Every moved name is re-imported below, so ``memory.links`` remains the one import path
consumers use and ``python -m memory.links`` keeps its CLI. This façade keeps what
orchestrates, writes, or audits:

  - ``build_graph`` — cache-or-cold graph construction (the one constructor callers use);
  - ``add_typed_relation``/``remove_typed_relation`` — the per-item, agent-gated
    typed-edge write primitives (mirroring ``staleness.set_invalid_after``'s
    frontmatter-write discipline; AST-pinned to this file by the crash contract);
  - the tier/audit read layer — ``extra_tier_stems``/``resolve_cross_tier`` (GRF-1 tier
    awareness), ``archived_target``, ``_edge_origin_map``, and ``graph_audit`` (the
    one-call audit report doctor's ``check_edge_rot`` consumes).

Read-only with the two write-primitive exceptions; never raises into a caller.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional

from .provenance import _is_memory_filename, _iter_memory_files, parse_frontmatter

# Decomposition façade (CONTRIBUTING "Code layout"): every moved name re-imported
# explicitly so existing dotted paths keep resolving; siblings never import this façade.
from .links_graph import (  # noqa: F401  (re-exports are this façade's contract)
    DREAM_BLOCK_CLOSE,
    DREAM_BLOCK_OPEN,
    TYPED_RELATIONS,
    LinkGraph,
    _DREAM_BLOCK_RE,
    _DREAM_REFINES_STAMP_RE,
    _WIKILINK_RE,
    _strip_first_segment,
    dream_edges_admitted,
    normalize_slug,
    parse_planned,
    parse_typed_relations,
    parse_wikilinks,
    strip_dream_edges,
)
from .links_cache import (  # noqa: F401  (re-exports are this façade's contract)
    LINKS_SCHEMA_VERSION,
    _LINKS_CACHE_NAME,
    _graph_from_payload,
    _load_links_payload,
    _stat_signatures,
    links_cache_fresh,
    load_edges,
    write_links_cache,
)


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


def component_count(memory_dir: str, index_dir: Optional[str] = None) -> Optional[int]:
    """Number of weakly-connected components in the corpus graph, or ``None`` on failure.

    GRA-8's one-number rollup for the GOV-6 trust scorecard — a guarded convenience over
    ``build_graph(...).connected_components()`` that never raises (the scorecard tolerates an
    absent signal, never an exception).
    """
    try:
        graph = build_graph(memory_dir, index_dir)
        return len(graph.connected_components()) if graph is not None else None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# GRA-4: the ONE typed-edge write primitive (per-item, agent-gated)
# --------------------------------------------------------------------------- #
_FENCE = "---"


def add_typed_relation(path: str, relation: str, target: str, *, dry_run: bool = False) -> dict:
    """Append ``target`` to ONE memory's ``relation:`` frontmatter list (additive, body verbatim).

    The write primitive behind reconsolidation's ``superseded_by`` outcome (and any future
    agent-gated typed-edge write). Mirrors ``staleness.set_invalid_after``'s frontmatter-write
    discipline exactly: same ``metadata:``-nesting awareness as ``cited_paths`` (so
    ``parse_typed_relations`` finds the key regardless of which schema the file uses), body
    left byte-identical, refuses (no write) on missing/unparseable frontmatter. Idempotent:
    a target already in the list (compared slug-normalized, the same equivalence
    ``resolve()`` applies) is a no-op. An EXISTING ``relation:`` key is merged — its current
    targets (flow or block style, read via the YAML parse) are preserved, the key rewritten
    as one canonical flow list. Deliberately per-item with no batch parameter — a bulk
    supersede sweep must not be expressible. Never raises.
    """
    result = {"path": path, "relation": relation, "target": target, "changed": False, "error": None}
    try:
        if relation not in TYPED_RELATIONS:
            result["error"] = f"unknown relation: {relation!r} (must be one of {', '.join(TYPED_RELATIONS)})"
            return result
        if not isinstance(target, str) or not target.strip():
            result["error"] = "empty target"
            return result
        target = target.strip()
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        fm = parse_frontmatter(text)
        if not text.startswith(_FENCE):
            result["error"] = "no frontmatter -- cannot write a typed relation"
            return result
        if not fm:
            result["error"] = "unparseable frontmatter -- refusing to write (fix the YAML)"
            return result

        existing = parse_typed_relations(fm).get(relation, [])
        if normalize_slug(target) in {normalize_slug(t) for t in existing}:
            return result  # idempotent: the edge is already declared
        merged = existing + [target]

        lines = text.split("\n")
        close = next((i for i in range(1, len(lines)) if lines[i].strip() == _FENCE), None)
        if close is None:
            result["error"] = "no frontmatter -- cannot write a typed relation"
            return result
        fm_lines = lines[1:close]
        value = "[" + ", ".join(json.dumps(t) for t in merged) + "]"

        key_re = re.compile(rf"^(\s*){relation}\s*:")
        key_idx = next((i for i, ln in enumerate(fm_lines) if key_re.match(ln)), None)
        if key_idx is not None:
            # Rewrite the existing key in place (merged flow list), dropping any block-style
            # `- item` continuation lines that belonged to it — their values are already in
            # ``merged`` via the YAML parse above, so nothing is lost.
            indent = key_re.match(fm_lines[key_idx]).group(1)
            end = key_idx + 1
            while end < len(fm_lines) and re.match(r"^\s+-\s", fm_lines[end]):
                end += 1
            fm2 = fm_lines[:key_idx] + [f"{indent}{relation}: {value}"] + fm_lines[end:]
        else:
            # Fresh key: nest under an existing `metadata:` block when present, else append
            # top-level. COR-9: this was a hand-copy of backfill_text/set_invalid_after's
            # walk and shared its indent bug; all four now call the one primitive.
            from .provenance import insert_frontmatter_keys

            fm2 = insert_frontmatter_keys(fm_lines, [f"{relation}: {value}"])

        new_text = "\n".join([lines[0]] + fm2 + lines[close:])
        from .provenance import _frontmatter_damage

        # COR-9: a typed-edge write owns exactly the relation key it was asked to set.
        damage = _frontmatter_damage(text, new_text, {relation})
        if damage:
            result["error"] = f"refusing to write: {damage} — this is a hippo bug, please report it"
            return result
        result["changed"] = new_text != text
        if result["changed"] and not dry_run:
            from .atomic import write_text_atomic

            write_text_atomic(path, new_text)  # COR-18: never a torn corpus file
            # SEC-6: per-item, agent-gated typed-edge write — fold the new bytes into the
            # trusted-corpus consent baseline (review = consent; no-op on legacy
            # fingerprint-less records / ungated corpora; never fatal).
            # BND-3: an anomalous fold failure rides the result additively.
            try:
                from .trust import record_authored_write_disclosing

                note = record_authored_write_disclosing(os.path.dirname(path), path)
                if note:
                    result["consent_note"] = note
            except Exception:
                pass
    except Exception as exc:
        result["error"] = str(exc)
    return result


def remove_typed_relation(path: str, relation: str, target: str, *, dry_run: bool = False) -> dict:
    """Drop ``target`` from ONE memory's ``relation:`` frontmatter list (body verbatim) —
    ``add_typed_relation``'s inverse, and the settled-edge cleanup INV-4's resolve
    verdicts perform (a keep-one/scope-both verdict retires the pair's ``contradicts:``
    declaration; the supersedes edge or the scoped wording carries the story from here).

    Same discipline as its sibling: refuses (no write) on missing/unparseable
    frontmatter, idempotent when the target is not in the list (slug-normalized, the
    ``resolve()`` equivalence), rewrites a surviving list as one canonical flow list at
    the key's own indent, STRIPS the key entirely when the list empties (via the COR-9
    ``strip_frontmatter_keys`` walk, so block-style values lose their continuation
    lines too), guards with ``_frontmatter_damage``, and writes atomically with the
    SEC-6 consent fold. Deliberately per-item; never raises.
    """
    result = {"path": path, "relation": relation, "target": target, "changed": False, "error": None}
    try:
        if relation not in TYPED_RELATIONS:
            result["error"] = f"unknown relation: {relation!r} (must be one of {', '.join(TYPED_RELATIONS)})"
            return result
        if not isinstance(target, str) or not target.strip():
            result["error"] = "empty target"
            return result
        target = target.strip()
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        if not text.startswith(_FENCE):
            result["error"] = "no frontmatter -- cannot edit a typed relation"
            return result
        fm = parse_frontmatter(text)
        if not fm:
            result["error"] = "unparseable frontmatter -- refusing to write (fix the YAML)"
            return result

        existing = parse_typed_relations(fm).get(relation, [])
        kept = [t for t in existing if normalize_slug(t) != normalize_slug(target)]
        if len(kept) == len(existing):
            return result  # idempotent: the edge is not declared here

        from .provenance import _frontmatter_damage, strip_frontmatter_keys

        key_re = re.compile(rf"^(\s*){relation}\s*:")
        if kept:
            # Rewrite the key in place as a flow list of the survivors (dropping any
            # block-style continuation lines — their values are in `existing` already).
            lines = text.split("\n")
            close = next((i for i in range(1, len(lines)) if lines[i].strip() == _FENCE), None)
            fm_lines = lines[1:close]
            key_idx = next((i for i, ln in enumerate(fm_lines) if key_re.match(ln)), None)
            if key_idx is None:
                result["error"] = "relation key not found in frontmatter (parse/lines disagree)"
                return result
            indent = key_re.match(fm_lines[key_idx]).group(1)
            end = key_idx + 1
            while end < len(fm_lines) and re.match(r"^\s+-\s", fm_lines[end]):
                end += 1
            value = "[" + ", ".join(json.dumps(t) for t in kept) + "]"
            fm2 = fm_lines[:key_idx] + [f"{indent}{relation}: {value}"] + fm_lines[end:]
            new_text = "\n".join([lines[0]] + fm2 + lines[close:])
        else:
            new_text = strip_frontmatter_keys(text, key_re)

        damage = _frontmatter_damage(text, new_text, {relation})
        if damage:
            result["error"] = f"refusing to write: {damage} — this is a hippo bug, please report it"
            return result
        result["changed"] = new_text != text
        if result["changed"] and not dry_run:
            from .atomic import write_text_atomic

            write_text_atomic(path, new_text)  # COR-18: never a torn corpus file
            # BND-3: same disclosure contract as add_typed_relation.
            try:
                from .trust import record_authored_write_disclosing

                note = record_authored_write_disclosing(os.path.dirname(path), path)
                if note:
                    result["consent_note"] = note
            except Exception:
                pass
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _edge_origin_map(memory_dir: str) -> Dict[str, Dict[str, str]]:
    """``{src stem: {target: origin}}`` from each memory's ``edge_origin`` frontmatter.

    GRF-1: the consolidate skill stamps a newly-approved co-recall wikilink with
    ``edge_origin: {<target>: co-recall}`` (top-level or under ``metadata:`` — the same
    two-schema read convention as ``parse_typed_relations``; the key is deliberately
    ``edge_origin``, NOT ``origin``, which is RCH-1's memory-level provenance stamp).
    Absence-emits-nothing: an unstamped corpus returns ``{}`` and nothing downstream
    changes (no corpus_format bump). Read LIVE from frontmatter by the audit only —
    the annotation never enters links.json (ED-4: no cache schema change). Pure;
    never raises; skips unreadable/unparseable files.
    """
    out: Dict[str, Dict[str, str]] = {}
    try:
        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    fm = parse_frontmatter(fh.read())
            except Exception:
                continue
            if not isinstance(fm, dict):
                continue
            meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
            eo = fm.get("edge_origin")
            if eo is None:
                eo = (meta or {}).get("edge_origin")
            if not isinstance(eo, dict):
                continue
            tagged = {
                str(t): str(o) for t, o in eo.items() if isinstance(t, str) and isinstance(o, str)
            }
            if tagged:
                out[os.path.splitext(os.path.basename(path))[0]] = tagged
    except Exception:
        return {}
    return out


def extra_tier_stems(memory_dir: str) -> Dict[str, str]:
    """Stem → tier label over the NON-project recall tiers (TEA-3 private first, then the
    machine-local user tier) — the SAME tier set recall fuses (``recall_tiers``), read via
    the low-level dir helpers so the link plane stays import-cycle-free. First-wins on a
    cross-tier stem collision, mirroring the fusion merge's precedence. A promoted memory
    (project → user tier) is the canonical resident: recall resolves a ``[[wikilink]]`` to
    it every session, so the link plane must not report that link as rot forever. ``{}``
    when no extra tier exists (an unconfigured machine pays nothing). Never raises."""
    out: Dict[str, str] = {}
    try:
        from .provenance_env import local_memory_dir, user_memory_dir

        project_abs = os.path.abspath(memory_dir)
        for tier_dir, label in (
            (local_memory_dir(memory_dir), "private"),
            (user_memory_dir(), "user"),
        ):
            try:
                if not tier_dir or os.path.abspath(tier_dir) == project_abs:
                    continue
                if not os.path.isdir(tier_dir):
                    continue
                for name in sorted(os.listdir(tier_dir)):
                    if _is_memory_filename(name):
                        out.setdefault(name[:-3], label)
            except Exception:
                continue
    except Exception:
        return out
    return out


def resolve_cross_tier(target: str, tier_stems: Dict[str, str]) -> Optional[str]:
    """The tier label ``target`` resolves to OUTSIDE the project corpus, else None — the
    same candidate normalization ``graph_audit``'s archive classifier applies."""
    for cand in (target, normalize_slug(target), normalize_slug(target).replace("-", "_")):
        if cand and cand in tier_stems:
            return tier_stems[cand]
    return None


def archived_target(memory_dir: str, target: str) -> bool:
    """True when ``target`` matches a file retired to ``<memory_dir>/archive/`` — the
    candidate normalization ``graph_audit``'s rot classifier has always applied,
    extracted (GRF-6) so the planned-marker split shares ONE archive test with it: a
    ``planned:`` declaration must never quiet a link whose target was archived (the
    edge outlived a real retirement — exactly the rot the class exists to report)."""
    archive_dir = os.path.join(memory_dir, "archive")
    for cand in (target, normalize_slug(target).replace("-", "_"), normalize_slug(target)):
        if cand and os.path.isfile(os.path.join(archive_dir, cand + ".md")):
            return True
    return False


def graph_audit(memory_dir: str) -> Optional[dict]:
    """GRF-1 (absorbs & closes GRA-8's remainder): the one-call graph-audit report.

    Computed LIVE from the corpus + ``archive/`` state — a diagnostic reads the source
    of authority directly (the ``lint_links`` CLI convention), never the links.json
    cache, and changes no schema anywhere. Returns ``None`` only when the graph cannot
    be built at all. Report keys:

      files/edges/typed_edges  — node count, resolved untyped edge count, and a
                                 per-relation resolved-edge count ({} when none).
      components/orphans/isolates/max_degree — the GRA-8 structural stats.
      edge_origin              — {origin: count} over stamped edges ({} when none) —
                                 the consolidate co-recall stamp made visible.
      rot                      — [{src, target, via, class}] where class is:
          archived    the target's file lives in ``archive/`` (it WAS a memory; the
                      edge outlived its retirement — ``lint_links`` reports it as
                      dangling, this classifies it),
          dangling    the target resolves to nothing anywhere (rot only in the sense
                      that the edge points at nothing — lint_links' finding, carried
                      here so the audit is one complete view; an UNMARKED absent
                      target, typo and undeclared forward reference alike — advisory
                      by contract, the class never gates),
          superseded  the target IS a live memory but some other memory supersedes
                      it — the edge points at retired knowledge. The supersession
                      marker itself (the ``supersedes`` edge into the target) is NOT
                      rot; every other edge kind into it is.
      cross_tier               — [{src, target, via, tier}] — targets that resolve in
                                 ANOTHER recall tier (promoted → user, or TEA-3 private).
                                 A DISTINCT NON-ROT category: recall serves these links
                                 every session, so counting them as rot made doctor's
                                 headline number a standing lie (21 of 24 in the live
                                 2026-09-01 finding). ``lint()`` classifies; this report
                                 carries them beside ``rot``, never inside it.
      planned                  — [{src, target, via}] — dangling wikilinks whose SOURCE
                                 declares the target in ``planned:`` frontmatter (GRF-6,
                                 the deliberate-forward-reference idiom). Reclassified
                                 out of ``rot`` by ``lint_links._classify_planned``
                                 (archived/cross-tier targets are never maskable there);
                                 carried beside ``cross_tier`` so the audit still names
                                 every declared ref instead of silently dropping it.
      planned_stale            — [{src, target, reason}] — ``planned:`` declarations
                                 whose target now RESOLVES (in-project or cross-tier) or
                                 was archived: the marker outlived its purpose, so the
                                 CLI tells the author to retire the line. Advisory and
                                 audit-only; never a health_line nag.

    Cost: two corpus reads (``lint`` + ``build_graph``) + one frontmatter sweep —
    CLI/doctor-only, never any hook path.
    """
    from .lint_links import lint

    g = build_graph(memory_dir)
    if g is None:
        return None
    report = lint(memory_dir)

    typed_counts: Dict[str, int] = {}
    for _src, rels in g.typed.items():
        for rel, targets in rels.items():
            if targets:
                typed_counts[rel] = typed_counts.get(rel, 0) + len(targets)

    def _rot_class(target: str) -> str:
        return "archived" if archived_target(memory_dir, target) else "dangling"

    cross_tier: List[dict] = [
        {"src": i["file"], "target": i["target"], "via": "wikilink", "tier": i["tier"]}
        for i in report.get("cross_tier", [])
    ] + [
        {"src": i["file"], "target": i["target"], "via": i["relation"], "tier": i["tier"]}
        for i in report.get("cross_tier_typed", [])
    ]

    planned: List[dict] = [
        {"src": i["file"], "target": i["target"], "via": "wikilink"}
        for i in report.get("planned", [])
    ]

    # GRF-6: stale planned markers — a ``planned:`` declaration whose target now EXISTS
    # (in-project, in another recall tier, or retired to archive/). The marker did its
    # job or was wrong; either way the CLI names it so the author retires the line.
    # Deliberately not a rot class and never a health_line nag — the edge itself is
    # healthy (or already reported as archived rot below).
    try:
        tier_stems = extra_tier_stems(memory_dir)
    except Exception:
        tier_stems = {}
    planned_stale: List[dict] = []
    for src in sorted(g.planned_raw):
        for t in g.planned_raw[src]:
            if g.resolve(t):
                reason = "resolves-in-project"
            elif resolve_cross_tier(t, tier_stems):
                reason = "resolves-cross-tier"
            elif archived_target(memory_dir, t):
                reason = "archived"
            else:
                continue
            planned_stale.append({"src": src, "target": t, "reason": reason})

    rot: List[dict] = []
    for item in report.get("dangling", []):
        rot.append(
            {
                "src": item["file"],
                "target": item["target"],
                "via": "wikilink",
                "class": _rot_class(item["target"]),
            }
        )
    for item in report.get("typed_dangling", []):
        rot.append(
            {
                "src": item["file"],
                "target": item["target"],
                "via": item["relation"],
                "class": _rot_class(item["target"]),
            }
        )
    superseded_stems = {tgt for _src, tgt in g.all_typed_edges("supersedes")}
    if superseded_stems:
        for src, outs in sorted(g.adjacency.items()):
            for tgt in sorted(outs):
                if tgt in superseded_stems:
                    rot.append({"src": src, "target": tgt, "via": "wikilink", "class": "superseded"})
        for rel in sorted(TYPED_RELATIONS):
            if rel == "supersedes":
                continue  # the supersession marker itself is the one legitimate edge in
            for src, tgt in g.all_typed_edges(rel):
                if tgt in superseded_stems:
                    rot.append({"src": src, "target": tgt, "via": rel, "class": "superseded"})

    origin_counts: Dict[str, int] = {}
    for _src, tagged in _edge_origin_map(memory_dir).items():
        for _tgt, origin in tagged.items():
            origin_counts[origin] = origin_counts.get(origin, 0) + 1

    comps = g.connected_components()
    degrees = g.degrees()
    return {
        "files": len(g.files),
        "edges": report.get("edges", 0),
        "typed_edges": typed_counts,
        "components": len(comps),
        "largest_component": len(comps[0]) if comps else 0,
        "orphans": len(g.orphans()),
        "isolates": len(g.isolates()),
        "max_degree": degrees[0][3] if degrees else 0,
        "edge_origin": origin_counts,
        "rot": rot,
        "cross_tier": cross_tier,
        "planned": planned,
        "planned_stale": planned_stale,
    }


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    from .provenance import resolve_dirs

    parser = argparse.ArgumentParser(description="Inspect the memory wikilink graph.")
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--traverse", default=None, help="show files reachable from NAME")
    parser.add_argument("--hops", type=int, default=2)
    parser.add_argument(
        "--components",
        action="store_true",
        help="GRA-8: list weakly-connected components (fragmentation of the memory graph)",
    )
    parser.add_argument(
        "--degree",
        action="store_true",
        help="GRA-8: per-memory in/out/total degree, most-connected first",
    )
    parser.add_argument(
        "--export",
        choices=["json", "dot", "mermaid"],
        default=None,
        help="GRA-8: serialize the whole graph (json | Graphviz dot | mermaid)",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="GRF-1: the one-call graph audit — edge classes, structure stats, "
        "edge_origin tags, edge rot (archived/superseded/dangling targets), and the "
        "GRF-6 planned forward-reference classes",
    )
    args = parser.parse_args(argv)

    md, _ = resolve_dirs()
    md = args.memory_dir or md
    g = build_graph(md)
    if g is None:
        print("could not build link graph")
        return 1
    if args.export:
        print(g.export(args.export))
        return 0
    if args.audit:
        report = graph_audit(md)
        if report is None:
            print("could not build link graph")
            return 1
        typed = (
            ", ".join(f"{rel}={n}" for rel, n in sorted(report["typed_edges"].items())) or "none"
        )
        print(
            f"graph audit: files={report['files']} edges={report['edges']} "
            f"typed[{typed}] components={report['components']} "
            f"(largest {report['largest_component']}) orphans={report['orphans']} "
            f"isolates={report['isolates']} max_degree={report['max_degree']}"
        )
        if report["edge_origin"]:
            origins = ", ".join(f"{o}={n}" for o, n in sorted(report["edge_origin"].items()))
            print(f"edge origins (stamped): {origins}")
        rot = report["rot"]
        print(f"edge rot ({len(rot)}):" if rot else "edge rot (0): none")
        for r in rot:
            print(f"  {r['class']:<10} {r['src']} -> {r['target']} (via {r['via']})")
        cross = report.get("cross_tier") or []
        if cross:
            print(f"cross-tier links ({len(cross)}) — resolve in another recall tier, NOT rot:")
            for r in cross:
                print(f"  {r['tier']:<10} {r['src']} -> {r['target']} (via {r['via']})")
        planned = report.get("planned") or []
        if planned:
            print(f"planned forward refs ({len(planned)}) — declared deliberate (GRF-6), NOT rot:")
            for r in planned:
                print(f"  {'planned':<10} {r['src']} -> {r['target']} (via {r['via']})")
        stale = report.get("planned_stale") or []
        if stale:
            print(f"stale planned marker(s) ({len(stale)}) — the target now exists; retire the declaration:")
            for r in stale:
                print(f"  {r['reason']:<19} {r['src']} planned: {r['target']}")
        return 0
    total_edges = sum(len(v) for v in g.adjacency.values())
    typed_edges = sum(len(t) for m in g.typed.values() for t in m.values())
    comps = g.connected_components()
    print(
        f"files={len(g.files)} edges={total_edges} typed={typed_edges} "
        f"components={len(comps)} orphans={len(g.orphans())} isolates={len(g.isolates())}"
    )
    if args.components:
        print(f"connected components ({len(comps)}, largest first):")
        for i, comp in enumerate(comps):
            head = ", ".join(comp[:8]) + (f", +{len(comp) - 8} more" if len(comp) > 8 else "")
            print(f"  [{i}] {len(comp)} node(s): {head}")
    if args.degree:
        print("degree (out/in/total, most-connected first):")
        for stem, out_d, in_d, total_d in g.degrees():
            print(f"  {stem}: out={out_d} in={in_d} total={total_d}")
    if args.traverse:
        reach = g.traverse(args.traverse, hops=args.hops)
        print(f"reachable from {args.traverse} within {args.hops} hops ({len(reach)}):")
        for s in sorted(reach):
            print(f"  - {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
