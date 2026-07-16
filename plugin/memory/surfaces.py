"""INV-1: the verb-surface registry — every user-facing verb's surface story, declared ONCE.

The INT class (INT-13 consolidate, INT-14/15 repair, INT-16 pack, INT-17/18/19 the QA
sweep) is one recurring bug: a verb or nudge ships terminal-first, the Desktop surface
dead-ends or the advice names a non-runnable command, a field report arrives, an INT-id
patches the instance. The class recurred because no single artifact declared each verb's
surface story — the intent lived in seven separately-worded skill preflights, one hook
note, and whichever nudge strings happened to name a command.

This module is that artifact. It declares, for every ``/hippo:*`` verb:

  - which MCP tools serve it (must exist in ``mcp_server._DISPATCH``),
  - its Desktop story: ``"tool"`` (the typed command's Desktop equivalent is the tool
    directly), ``"skill_tools"`` (the skill runs on both surfaces and drives the named
    per-item MCP tools where its bash blocks can't run), or ``"terminal_only"`` (an
    honest preflight says so — never a dead-end promise),

plus the tools that serve NO typed verb (mid-turn/subagent reads, the corpus-repair
verbs) and the ``bin/hippo`` subcommand list (frozen in STABILITY.md).

BUILD-TIME ARTIFACT ONLY. ``tests/test_surface_registry.py`` is the parity lint that
cross-checks every declaration here against reality — ``_DISPATCH``, the skills dir,
each SKILL.md's Desktop routing, the Desktop surface note in ``session_start``, and
every nudge/advice string that names a runnable command. Nothing on the hot path (hooks,
the MCP server, recall) imports this module — the lint asserts that too. Editing rules:

  - a NEW MCP tool must be claimed here (a verb row's ``mcp_tools`` or
    ``VERBLESS_TOOLS``) or the lint fails naming this file;
  - a NEW skill must gain a row (and a row must have a skill dir);
  - flipping a verb ``terminal_only`` -> routed means updating its SKILL.md preflight
    AND the Desktop surface note in the same change — the lint holds all three together.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

# The honest-preflight marker every terminal-only skill must carry verbatim (the
# INT-19 wording): the lint greps SKILL.md for it, and its PRESENCE in a routed
# skill is as much a failure as its absence in a terminal-only one.
TERMINAL_ONLY_MARKER = "no Desktop-safe MCP-tool equivalent yet"

# bin/hippo's dispatching subcommands (STABILITY.md's frozen CLI surface). The lint
# parses the script's exec-ing case arms and asserts equality, so advice naming
# ``hippo <sub>`` can be checked against a list that cannot drift from the script.
BIN_HIPPO_SUBCOMMANDS: Tuple[str, ...] = ("recall", "new", "build-index", "staleness", "mcp")


@dataclass(frozen=True)
class VerbSurface:
    """One ``/hippo:<verb>``'s surface story. ``verb`` == its ``plugin/skills/`` dir."""

    verb: str
    desktop: str  # "tool" | "skill_tools" | "terminal_only"
    mcp_tools: Tuple[str, ...]  # every tool serving this verb; () iff terminal_only
    note: str  # one-line human story (documentation, not linted prose)


VERBS: Tuple[VerbSurface, ...] = (
    VerbSurface(
        "audit",
        desktop="skill_tools",
        mcp_tools=("audit",),
        note="the skill drives judgment on both surfaces; the audit tool serves the "
        "read-only Phase-1 report material (INV-4, scope ratified 2026-07-16)",
    ),
    VerbSurface(
        "bootstrap",
        desktop="tool",
        mcp_tools=("bootstrap",),
        note="per-machine-surface provisioning (INT-10)",
    ),
    VerbSurface(
        "consolidate",
        desktop="skill_tools",
        mcp_tools=(
            "capture",
            "new_memory",
            "secrets_scan",
            "reconsolidate",
            "build_index",
            "co_recall_proposals",
            "abstention_fixtures",
        ),
        note="the skill is the doctrine on both surfaces; its steps are per-item tools (INT-13)",
    ),
    VerbSurface(
        "doctor",
        desktop="tool",
        mcp_tools=("doctor", "trust_corpus"),
        note="health check + the SEC-1 consent flow (INT-9/INT-12)",
    ),
    VerbSurface(
        "dream",
        desktop="tool",
        mcp_tools=("dream",),
        note="the generative sleep pass (DRM-2)",
    ),
    VerbSurface(
        "export-agents",
        desktop="terminal_only",
        mcp_tools=(),
        note="AGENTS.md fan-out; propose-only diffs in a terminal",
    ),
    VerbSurface(
        "import",
        desktop="terminal_only",
        mcp_tools=(),
        note="migration on-ramp (Cursor rules); per-item confirmed in a terminal",
    ),
    VerbSurface(
        "init",
        desktop="tool",
        mcp_tools=("init",),
        note="per-project wiring; pre-existing corpora route to trust_corpus (INT-11)",
    ),
    VerbSurface(
        "new",
        desktop="tool",
        mcp_tools=("new_memory",),
        note="the per-item corpus write, right-by-construction",
    ),
    VerbSurface(
        "pack",
        desktop="skill_tools",
        mcp_tools=(
            "pack_extract",
            "pack_install_plan",
            "pack_install_item",
            "pack_update_plan",
            "pack_update_item",
        ),
        note="share/adopt packs; the skill drives five per-item primitives (INT-16)",
    ),
    VerbSurface(
        "promote",
        desktop="terminal_only",
        mcp_tools=(),
        note="lift ONE memory to the user tier; per-item in a terminal",
    ),
    VerbSurface(
        "promote-rule",
        desktop="terminal_only",
        mcp_tools=(),
        note="promote ONE memory into a scoped rule; propose-only in a terminal",
    ),
    VerbSurface(
        "recall",
        desktop="tool",
        mcp_tools=("recall", "decision_history", "traverse"),
        note="query recall + lineage + graph hops; --list-by-type/--all-projects stay terminal-only",
    ),
    VerbSurface(
        "remove",
        desktop="terminal_only",
        mcp_tools=(),
        note="project offboarding; terminal-only by intent",
    ),
    VerbSurface(
        "resolve",
        desktop="tool",
        mcp_tools=("resolve",),
        note="the contradiction inbox + ONE per-pair verdict per call — the "
        "nudge-routed dead end closed (INV-4, scope ratified 2026-07-16)",
    ),
    VerbSurface(
        "why",
        desktop="tool",
        mcp_tools=("why",),
        note="the glass-box recall receipt (GOV-5)",
    ),
)

# MCP tools that serve NO /hippo:* verb: the corpus-repair verbs (INT-14/15 — they exist
# to undo defects hippo itself shipped, on both surfaces, with no typed form). The lint
# requires the Desktop surface note to name these so they are discoverable there.
VERBLESS_TOOLS: Dict[str, str] = {
    "rederive": "MIG-1 citation re-derivation — MCP tool on both surfaces, no /hippo:* form",
    "heal_baselines": "COR-10 empty-baseline heal — MCP tool on both surfaces, no /hippo:* form",
}


def verb_map() -> Dict[str, VerbSurface]:
    """``verb -> row`` for lookups; the tuple above stays the declaration of record."""
    return {v.verb: v for v in VERBS}


def terminal_only_verbs() -> Tuple[str, ...]:
    """The verbs whose honest story is 'terminal-only for now', in declaration order."""
    return tuple(v.verb for v in VERBS if v.desktop == "terminal_only")


def claimed_tools() -> frozenset:
    """Every MCP tool name the registry accounts for — must equal ``_DISPATCH`` exactly."""
    tools = set(VERBLESS_TOOLS)
    for v in VERBS:
        tools.update(v.mcp_tools)
    return frozenset(tools)
