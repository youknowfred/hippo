"""IOP-1 — the foreign-dialect radar: census + divergence + rot over NON-hippo rule files.

A repo may carry Cursor ``.cursor/rules/*.mdc``, Copilot
``.github/instructions/*.instructions.md``, and hippo memories that say the same thing
and drift apart — and a scoped foreign rule may point at code that moved. hippo has the
only real drift machinery in that ecosystem; this module points it (report-only) at the
dialects hippo does NOT own:

  - CENSUS — which dialects have files present, by filesystem glob-presence alone.
    No foreign frontmatter is parsed beyond the shipped ``.mdc`` adapter (ED-3: every
    other dialect's format gets its own live probe before a parser is ever written —
    Copilot ``applyTo:`` support is explicitly deferred behind one).
  - CROSS-DIALECT DIVERGENCE — each discovered foreign file's content scored against
    the GOVERNANCE plane via ``rules_plane.rule_dup_candidates`` called verbatim (the
    same-rule-in-two-planes containment RUL-3 ships): a foreign rule whose substance
    already lives in CLAUDE.md/AGENTS.md is a same-rule-diverged pair in the making.
    Foreign×foreign pairwise comparison is deliberately NOT built (no new machinery).
  - ROT, ``.mdc`` only (the one dialect with a shipped parser): existence-only stale
    citations — body path references whose target left the tree, via the SAME
    ``provenance.extract_citations`` regex the import backfill uses — and zero-match
    ``globs:`` via the RCH-2 ``parse_mdc``/``resolve_globs`` pipeline. Deliberately NOT
    framed as git-log drift-since-written: a ``.mdc`` carries no ``source_commit``
    baseline to drift FROM (IOP-2 gives IMPORTED copies exactly that).

ISOLATION IS THE LOAD-BEARING INVARIANT (inv5): ``FOREIGN_GLOBS`` is a separate symbol
that must NEVER merge into ``rules_plane.GOV_GLOBS`` and never reach the RUL-1/3/4
authority paths (``gov_citations``/``conflict_radar``/``rule_dup_candidates``'s
governance scan/``load_rules_cache``) — un-owned Cursor/Copilot content must never be
mistaken for hippo authority, never become a recall pointer, never join the write-time
dedup surface. This module never imports those functions (structurally pinned); its one
``rules_plane`` reuse is calling ``rule_dup_candidates`` WITH foreign content as the
draft argument — the sanctioned direction (foreign content scored AGAINST governance,
never enumerated AS governance). inv6: audit-skill/doctor on-demand only — NOT a
SessionStart producer. inv1/inv4: read-only; writes nothing, imports nothing, proposes
no fixes beyond naming findings.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

# The foreign-dialect surface — SEPARATE from rules_plane.GOV_GLOBS by design (inv5);
# merging them would hand un-owned foreign content hippo's authority joins. ``.agents/
# rules/`` (#179) is an UNRATIFIED convention: censused watch-only, never parsed.
FOREIGN_GLOBS = (
    ".cursor/rules/*.mdc",
    ".github/instructions/*.instructions.md",
    ".agents/rules/*.md",
)

# glob -> (dialect label, watch_only). Presence reporting only — no per-dialect parsing
# is keyed off this table (only .mdc has a shipped parser, addressed explicitly below).
_DIALECTS = {
    ".cursor/rules/*.mdc": ("cursor", False),
    ".github/instructions/*.instructions.md": ("copilot", False),
    ".agents/rules/*.md": ("agents-rules", True),
}


def foreign_census(repo_root: str) -> Dict[str, List[str]]:
    """``{dialect: [repo-relative files]}`` by glob-presence alone — no file is opened,
    no frontmatter parsed. Every dialect key is always present (``[]`` when absent) so
    the renderer can say "no other dialects found" honestly. Never raises."""
    out: Dict[str, List[str]] = {label: [] for label, _w in _DIALECTS.values()}
    try:
        root = Path(repo_root)
        for pattern in FOREIGN_GLOBS:
            label = _DIALECTS[pattern][0]
            try:
                out[label] = sorted(
                    os.path.relpath(str(p), repo_root)
                    for p in root.glob(pattern)
                    if p.is_file()
                )
            except Exception:
                continue
    except Exception:
        pass
    return out


def _read(repo_root: str, rel: str) -> str:
    try:
        with open(os.path.join(repo_root, rel), "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return ""


def foreign_divergence(repo_root: str, census: Dict[str, List[str]]) -> List[dict]:
    """Same-rule-diverged pairs: foreign files whose content a governance block already
    contains, via ``rule_dup_candidates`` verbatim (containment ≥ its own threshold,
    its own ≤3 cap). ``.mdc`` content goes through the shipped ``parse_mdc``; every
    other dialect is fed RAW (no foreign-frontmatter parsing — ED-3). Returns
    ``[{"foreign", "dialect", "matches": [{"file", "score", "preview"}]}]`` for files
    with at least one match. Report-only: never written, never an import candidate."""
    out: List[dict] = []
    try:
        from .import_mdc import parse_mdc
        from .rules_plane import rule_dup_candidates

        for label, files in sorted(census.items()):
            for rel in files:
                text = _read(repo_root, rel)
                if not text:
                    continue
                if rel.endswith(".mdc"):
                    parsed = parse_mdc(text)
                    description, body = parsed["description"], parsed["body"]
                else:
                    description, body = "", text
                matches = rule_dup_candidates(description, body, repo_root)
                if matches:
                    out.append({"foreign": rel, "dialect": label, "matches": matches})
    except Exception:
        return out
    return out


def mdc_rot(repo_root: str, mdc_files: List[str]) -> dict:
    """The two ``.mdc`` rot legs — existence-only, never git-log framed.

    ``citation_rot``: body path references (``provenance.extract_citations`` — the same
    regex the import backfill derives cited_paths with) whose target is absent from the
    tree. A bare basename resolves through the basename index; an ambiguous or resolvable
    one is SILENCE (under-flag beats cry-wolf, ``rules_rot``'s own discipline).
    ``dead_globs``: frontmatter ``globs:`` entries matching nothing, via the RCH-2
    ``parse_mdc`` → ``resolve_globs`` pipeline verbatim.

    Returns ``{"citation_rot": [{"file", "missing": [...]}], "dead_globs": [{"file",
    "glob"}]}``. Never raises."""
    empty: dict = {"citation_rot": [], "dead_globs": []}
    try:
        from .import_mdc import parse_mdc, resolve_globs
        from .provenance import build_repo_file_index, extract_citations

        repo_files, basename_index = build_repo_file_index(repo_root)
        if not repo_files:
            return empty  # no git oracle: no findings (mirror rules_rot's silence)
        citation_rot: List[dict] = []
        dead_globs: List[dict] = []
        for rel in mdc_files:
            text = _read(repo_root, rel)
            if not text:
                continue
            parsed = parse_mdc(text)
            missing = []
            for tok in extract_citations(parsed["body"]):
                alive = tok in repo_files if "/" in tok else bool(basename_index.get(tok))
                if not alive and tok not in missing:
                    missing.append(tok)
            if missing:
                citation_rot.append({"file": rel, "missing": missing})
            for glob in parsed["globs"]:
                if not resolve_globs(repo_root, [glob]):
                    dead_globs.append({"file": rel, "glob": glob})
        return {"citation_rot": citation_rot, "dead_globs": dead_globs}
    except Exception:
        return empty


def foreign_radar(repo_root: str) -> dict:
    """The whole radar, read-only: census + divergence + ``.mdc`` rot. Never raises."""
    census = foreign_census(repo_root)
    rot = mdc_rot(repo_root, census.get("cursor") or [])
    return {
        "census": census,
        "divergence": foreign_divergence(repo_root, census),
        "mdc_citation_rot": rot["citation_rot"],
        "mdc_dead_globs": rot["dead_globs"],
    }


def describe_radar(radar: dict) -> str:
    """Human render for the audit skill: census first (degrading to the honest
    single-dialect line), then each finding NAMED per item — the fix is always a human
    edit of the named file, never this module's."""
    census = radar.get("census") or {}
    present = {d: fs for d, fs in sorted(census.items()) if fs}
    lines: List[str] = []
    if not present:
        return "Foreign-dialect radar: no other dialects found (cursor/copilot/agents-rules globs all empty)."
    counts = ", ".join(f"{d}: {len(fs)} file(s)" for d, fs in present.items())
    watch = {label for label, w in _DIALECTS.values() if w}
    watched = [d for d in present if d in watch]
    lines.append(
        "Foreign-dialect radar (report-only): " + counts
        + (f" — {', '.join(watched)} censused watch-only (unratified, never parsed)" if watched else "")
    )
    for d in radar.get("divergence") or []:
        top = d["matches"][0]
        lines.append(
            f"  ⚑ {d['foreign']} ({d['dialect']}) restates governance content — "
            f"{top['file']} at containment {top['score']} — same-rule-diverged pair; "
            "converge by hand (link, don't copy)"
        )
    for c in radar.get("mdc_citation_rot") or []:
        lines.append(
            f"  ⚑ {c['file']} cites missing path(s): {', '.join(c['missing'])} — "
            "existence check only (a .mdc has no drift baseline)"
        )
    for g in radar.get("mdc_dead_globs") or []:
        lines.append(f"  ⚑ {g['file']} scopes globs: '{g['glob']}' — matches nothing in the tree")
    if len(lines) == 1:
        lines.append("  no cross-dialect divergence, no .mdc rot.")
    return "\n".join(lines)
