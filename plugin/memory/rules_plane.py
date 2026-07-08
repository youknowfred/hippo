"""The Claude-rules governance plane, read by hippo as a first-class surface (RUL, tier T2).

The rules plane — ``CLAUDE.md``, ``AGENTS.md``, ``.claude/rules/``, ``.claude/agents/``,
``.claude/skills/`` — is always-loaded, unranked, un-staled, and monotonically growing;
hippo's corpus is ranked, staleness-tracked, and review-gated. This module is the bridge:
the ONE canonical enumeration of the governance surface (the audit skill's ``GOV_GLOBS``
convention, promoted to importable API) plus the read-only joins built over it:

  - RUL-1 ``conflict_radar`` — governance files citing memories the corpus disagrees with:
    the authority-evidence gap (cited but never recalled, strength < 0.15) and the
    typed-edge leg (cited but another memory ``supersedes``/``contradicts`` it).

Relationship to the two pre-existing scan surfaces (deliberately NOT merged, inv5):
``archive._SCAN_TARGETS`` is the ARCHIVE-PROTECTION surface (adds ``docs/prompts``, omits
``AGENTS.md``, fails CLOSED to "cited" because an unreadable file must never unlock an
archive gate). The audit skill's inline ``GOV_GLOBS`` is the prototype this module
generalizes — same globs, same citation regex. A WARNING surface like this one fails the
OTHER way: an unreadable governance file yields no findings (never cry wolf from a read
error); the archive gate keeps its own fail-closed copy.

Everything here is read-only over user-owned files (inv1), off the UserPromptSubmit hot
path (inv6), surfaces loud at doctor/SessionStart (inv3), and proposes per-item decisions
without ever auto-resolving one (inv4). Never raises.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

# The governance-plane surface, verbatim from the audit skill's working prototype
# (plugin/skills/audit/SKILL.md GOV_GLOBS): the common Claude Code conventions plus
# AGENTS.md, the Linux-Foundation cross-tool standard. Glob patterns over repo_root.
GOV_GLOBS = (
    "CLAUDE.md",
    "AGENTS.md",
    ".claude/rules/*.md",
    ".claude/agents/*.md",
    ".claude/skills/**/*.md",
)

# A backtick-quoted memory-name-shaped token, ``.md`` optional — the same pattern the
# archive scanner and the audit skill both match (see archive._BACKTICK_TOKEN_RE for the
# empirical false-negative note that motivates the optional suffix).
_BACKTICK_TOKEN_RE = re.compile(r"`([A-Za-z0-9_-]+(?:\.md)?)`")

# The authority-evidence threshold, verbatim from the audit skill's join: a governance-cited
# memory whose recall strength (distinct-session share, soak.compute_strength_scores) sits
# below this is "governance says do X, telemetry says nobody uses it."
STRENGTH_GAP_THRESHOLD = 0.15

# The two typed relations that make a governance citation a live CONFLICT (a rule pointing
# at a memory the corpus itself has moved past). ``refines`` is deliberately absent — a
# refined memory is still authoritative.
_CONFLICT_RELATIONS = ("supersedes", "contradicts")


def gov_files(repo_root: str) -> List[str]:
    """Absolute paths of every governance-plane file under ``repo_root`` (GOV_GLOBS order,
    de-duplicated, sorted within each glob). Never raises; ``[]`` when nothing matches."""
    out: List[str] = []
    seen: Set[str] = set()
    try:
        root = Path(repo_root)
        for pattern in GOV_GLOBS:
            try:
                matches = sorted(str(p) for p in root.glob(pattern) if p.is_file())
            except Exception:
                continue
            for m in matches:
                if m not in seen:
                    seen.add(m)
                    out.append(m)
    except Exception:
        return []
    return out


def _rel(repo_root: str, path: str) -> str:
    """``path`` relative to ``repo_root`` for display; the absolute path on any failure."""
    try:
        return os.path.relpath(path, repo_root)
    except Exception:
        return path


def gov_citations(repo_root: str, corpus_names: Set[str]) -> Dict[str, List[str]]:
    """Which governance file cites which corpus memory: ``{stem: [repo-relative files]}``.

    A token counts only when it resolves to a REAL corpus stem (the precision gate — a
    backtick token like ``README.md`` that is not a memory never joins). Unreadable files
    are skipped: this feeds WARNING surfaces, so a read error must yield silence, not a
    fabricated finding (the archive gate's fail-closed copy covers the opposite need).
    Never raises; ``{}`` on any failure.
    """
    cited: Dict[str, List[str]] = {}
    try:
        if not corpus_names:
            return {}
        for path in gov_files(repo_root):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            rel = _rel(repo_root, path)
            for tok in _BACKTICK_TOKEN_RE.findall(text):
                stem = tok[:-3] if tok.endswith(".md") else tok
                if stem in corpus_names and rel not in cited.setdefault(stem, []):
                    cited[stem].append(rel)  # GOV_GLOBS encounter order: CLAUDE.md first
    except Exception:
        return {}
    return cited


def conflict_radar(
    memory_dir: str, repo_root: str, *, telemetry_dir: Optional[str] = None
) -> dict:
    """RUL-1: the rule↔memory conflict radar — the audit skill's authority-gap join as a
    standing, importable query.

    Returns::

        {
          "authority_gaps":  [{"name", "strength", "cited_by"}],  # strength leg
          "edge_conflicts":  [{"name", "relation", "by", "cited_by"}],  # typed leg
          "gate_met": bool,          # soak maturity — strength leg fires only when True
          "distinct_sessions": int,
        }

    The STRENGTH leg ("governance cites ``name`` but telemetry says nobody retrieves it,
    strength < 0.15") is gated on the soak maturity bar (``soak.soak_status()['gate_met']``,
    >= 5 distinct sessions): on a fresh clone EVERY cited memory scores 0.0, so an ungated
    standing producer would nag from day one — the explicit ``/hippo:audit`` run keeps its
    ungated join for deliberate curation sessions. The TYPED-EDGE leg ("governance cites
    ``name`` but ``by`` supersedes/contradicts it") rests on authored facts, not telemetry,
    so it fires regardless of soak maturity.

    Read-only; proposes per-item decisions (route: /hippo:consolidate), never resolves one.
    Never raises; empty findings on any failure.
    """
    empty = {
        "authority_gaps": [],
        "edge_conflicts": [],
        "gate_met": False,
        "distinct_sessions": 0,
    }
    try:
        from .links import build_graph
        from .provenance import _iter_memory_files
        from .telemetry import default_telemetry_dir
        from . import soak

        names = {
            os.path.splitext(os.path.basename(p))[0] for p in _iter_memory_files(memory_dir)
        }
        cited = gov_citations(repo_root, names)
        if not cited:
            return empty

        td = telemetry_dir or default_telemetry_dir(memory_dir)
        status = soak.soak_status(td, memory_dir=memory_dir)
        gate_met = bool(status.get("gate_met"))
        distinct = int(status.get("distinct_sessions") or 0)

        gaps: List[dict] = []
        if gate_met:
            strength = soak.compute_strength_scores(td)
            for name in sorted(cited):
                s = strength.get(name, 0.0)
                if s < STRENGTH_GAP_THRESHOLD:
                    gaps.append(
                        {"name": name, "strength": round(s, 4), "cited_by": cited[name]}
                    )
            gaps.sort(key=lambda g: (g["strength"], g["name"]))

        conflicts: List[dict] = []
        graph = build_graph(memory_dir)
        if graph is not None:
            for name in sorted(cited):
                for rel in _CONFLICT_RELATIONS:
                    for by in sorted(graph.typed_inbound(name, rel)):
                        conflicts.append(
                            {
                                "name": name,
                                "relation": rel,
                                "by": by,
                                "cited_by": cited[name],
                            }
                        )

        return {
            "authority_gaps": gaps,
            "edge_conflicts": conflicts,
            "gate_met": gate_met,
            "distinct_sessions": distinct,
        }
    except Exception:
        return empty
