"""Resources (RUL-5) for the stdio MCP server — ``hippo://floor``, ``hippo://rules-view``,
and ``hippo://scorecard`` (GOV-6): the agent-PULLED baseline-memory channel, SEC-1-gated,
never an implicit always-load. Decomposed out of ``mcp_server.py`` as pure code motion;
the façade re-imports every name, so ``memory.mcp_server.<name>`` stays importable."""

from __future__ import annotations

import os

from .mcp_tools_core import _UNTRUSTED_REMEDY


# --------------------------------------------------------------------------- #
# Resources (RUL-5) — agent-PULLED baseline memory; never an implicit always-load.
# --------------------------------------------------------------------------- #
_RESOURCES = [
    {
        "uri": "hippo://floor",
        "name": "hippo memory floor",
        "description": (
            "The always-on memory floor (project MEMORY.md + the portable user/private-tier "
            "floor) as one markdown document. Read this at SUBAGENT start to obtain the "
            "baseline memory a main session gets natively — a Task subagent receives none of "
            "it automatically. Agent-pulled on demand; never auto-loaded."
        ),
        "mimeType": "text/markdown",
    },
    {
        "uri": "hippo://rules-view",
        "name": "hippo rules-view",
        "description": (
            "The rules↔memory reconciliation: governance files (CLAUDE.md/AGENTS.md/"
            ".claude/rules|agents|skills) citing memories the corpus disputes (superseded/"
            "contradicted/never-recalled), plus rules-plane rot (dead code references and "
            "paths: globs matching nothing). Read-only; findings route to per-item decisions."
        ),
        "mimeType": "text/markdown",
    },
    {
        "uri": "hippo://scorecard",
        "name": "hippo trust scorecard",
        "description": (
            "GOV-6: the one-line corpus-health rollup a lead scans before trusting the "
            "corpus — contested-unresolved contradictions, rule↔memory conflicts, rules-"
            "plane rot, blind spots, orphans, pinned/muted/draft counts, and the floor/"
            "corpus delta since this clone's last session. Each number names the skill "
            "that resolves it. Read-only; agent-pulled, never auto-loaded."
        ),
        "mimeType": "text/markdown",
    },
]


def _resource_floor() -> str:
    """``hippo://floor`` — the always-on floor as one pulled document. Never raises upstream
    (the resources/read handler wraps it); SEC-1: an untrusted corpus withholds BOTH in-repo
    parts (project floor and private tier ride the same clone) — the exact posture
    ``build_context``'s short-circuit gives SessionStart, made explicit instead of silent."""
    from . import trust
    from .provenance import resolve_dirs
    from .recall import portable_floor_producer

    memory_dir, repo_root = resolve_dirs()
    header = (
        "# hippo memory floor\n\n"
        "Always-on memory, agent-pulled (a Task subagent receives none of this "
        "automatically)."
    )
    gate_root = trust.gate_repo_root(memory_dir, repo_root)
    if gate_root is not None and not trust.is_trusted(gate_root):
        return (
            header + "\n\nFloor WITHHELD — this project's memory corpus is untrusted "
            "(SEC-1: a cloned corpus is an unreviewed prompt-injection channel). "
            + _UNTRUSTED_REMEDY
        )
    parts = []
    try:
        with open(os.path.join(memory_dir, "MEMORY.md"), encoding="utf-8") as fh:
            floor_md = fh.read().strip()
        if floor_md:
            parts.append("## Project floor (MEMORY.md)\n\n" + floor_md)
    except Exception:
        pass
    portable = None
    try:
        portable = portable_floor_producer(memory_dir, repo_root, None)
    except Exception:
        portable = None
    if portable:
        parts.append("## Portable floor (user & private tiers)\n\n" + portable)
    if not parts:
        return header + "\n\nFloor empty — no always-on memory configured yet (/hippo:init)."
    return header + "\n\n" + "\n\n".join(parts)


def _resource_scorecard() -> str:
    """``hippo://scorecard`` — GOV-6's rollup as one pulled document. SEC-1-gated like the
    floor/rules-view; delegates to doctor's ``_scorecard_message`` (one implementation)."""
    from . import trust
    from .doctor import _scorecard_message
    from .provenance import resolve_dirs

    memory_dir, repo_root = resolve_dirs()
    header = "# hippo trust scorecard — corpus-health rollup"
    gate_root = trust.gate_repo_root(memory_dir, repo_root)
    if gate_root is not None and not trust.is_trusted(gate_root):
        return (
            header + "\n\nScorecard WITHHELD — this project's corpus is untrusted (SEC-1). "
            + _UNTRUSTED_REMEDY
        )
    status, message = _scorecard_message(memory_dir, repo_root)
    glyph = "⚠" if status == "warn" else "✔"
    return (
        header + f"\n\n{glyph} {message}\n\nDrill down with /hippo:doctor (the point checks) "
        "and resolve via the named skill per number."
    )


def _resource_rules_view() -> str:
    """``hippo://rules-view`` — the RUL-1/RUL-2 reconciliation as one pulled document.
    SEC-1-gated like the floor: a foreign clone's governance files ARE the injection threat."""
    from . import trust
    from .provenance import resolve_dirs
    from .rules_plane import conflict_radar, rules_rot

    memory_dir, repo_root = resolve_dirs()
    header = "# hippo rules-view — governance plane ↔ memory corpus reconciliation"
    gate_root = trust.gate_repo_root(memory_dir, repo_root)
    if gate_root is not None and not trust.is_trusted(gate_root):
        return (
            header + "\n\nView WITHHELD — this project's corpus is untrusted (SEC-1). "
            + _UNTRUSTED_REMEDY
        )
    radar = conflict_radar(memory_dir, repo_root)
    rot = rules_rot(repo_root)
    lines = [header, ""]
    conflicts = radar["edge_conflicts"]
    gaps = radar["authority_gaps"]
    if conflicts or gaps:
        lines.append("## Conflicts (decide per item via /hippo:consolidate — nothing auto-resolves)")
        for c in conflicts:
            lines.append(
                f"- {c['cited_by'][0]} cites `{c['name']}` but `{c['by']}` {c['relation']} it"
            )
        for g in gaps:
            lines.append(
                f"- {g['cited_by'][0]} cites `{g['name']}` but no session recalls it "
                f"(strength {g['strength']:.2f})"
            )
    else:
        note = "" if radar["gate_met"] else " (strength leg pending the telemetry soak gate)"
        lines.append(f"## Conflicts: none — governance citations agree with the corpus{note}")
    code_rot = rot["code_ref_rot"]
    dead_globs = rot["dead_path_globs"]
    if code_rot or dead_globs:
        lines.append("")
        lines.append("## Rules-plane rot (fix per item — hippo names it, you edit the file)")
        for r in code_rot:
            what = "path gone" if r["kind"] == "path" else "symbol gone"
            lines.append(f"- {r['file']} references `{r['ref']}` — {what}")
        for d in dead_globs:
            lines.append(f"- {d['file']} scopes paths: '{d['glob']}' — matches nothing")
    else:
        lines.append("")
        lines.append("## Rules-plane rot: none — code references and paths: globs resolve")
    return "\n".join(lines)
