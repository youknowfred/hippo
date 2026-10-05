"""INV-1: the verb-surface registry — every user-facing verb's surface story, declared ONCE.

The INT class (INT-13 consolidate, INT-14/15 repair, INT-16 pack, INT-17/18/19 the QA
sweep) is one recurring bug: a verb or nudge ships terminal-first, the Desktop surface
dead-ends or the advice names a non-runnable command, a field report arrives, an INT-id
patches the instance. The class recurred because no single artifact declared each verb's
surface story — the intent lived in seven separately-worded skill preflights, one hook
note (retired by CLM-8), and whichever nudge strings happened to name a command.

This module is that artifact. It declares, for every ``/hippo:*`` verb:

  - which MCP tools serve it (must exist in ``mcp_server._DISPATCH``),
  - its Desktop story: ``"tool"`` (the typed command's Desktop equivalent is the tool
    directly), ``"skill_tools"`` (the skill runs on both surfaces and drives the named
    per-item MCP tools where its bash blocks can't run), or ``"terminal_only"`` (an
    honest preflight says so — never a dead-end promise),

plus the tools that serve NO typed verb (mid-turn/subagent reads, the corpus-repair
verbs) and the ``hippo <verb>`` list (SRF-1; declared in ``cli_verbs``, the frozen rows
stated in STABILITY.md).

BUILD-TIME ARTIFACT ONLY. ``tests/test_surface_registry.py`` is the parity lint that
cross-checks every declaration here against reality — ``_DISPATCH``, the skills dir,
each SKILL.md's Desktop routing, and every nudge/advice string that names a runnable
command. Nothing on the hot path (hooks, the MCP server, recall) imports this module —
the lint asserts that too. Editing rules:

  - a NEW MCP tool must be claimed here (a verb row's ``mcp_tools`` or
    ``VERBLESS_TOOLS``) or the lint fails naming this file;
  - a NEW skill must gain a row (and a row must have a skill dir);
  - flipping a verb ``terminal_only`` -> routed means giving its SKILL.md a 'Surface
    routing' section that names the tools, and dropping the honest marker, in the same
    change — the lint holds the row and the skill together.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

from .cli_verbs import CLI_VERBS
from .mcp_schemas_v2 import DEPRECATED

# The honest-preflight marker every terminal-only skill must carry verbatim (the
# INT-19 wording): the lint greps SKILL.md for it, and its PRESENCE in a routed
# skill is as much a failure as its absence in a terminal-only one.
TERMINAL_ONLY_MARKER = "no Desktop-safe MCP-tool equivalent yet"

# SRF-1: every ``hippo <verb>`` the door dispatches, read from the one table in
# ``cli_verbs`` (pure data, which ``memory.cli`` reads at runtime; this registry only
# re-exports it). Advice naming ``hippo <verb>`` is checked against this list, and
# STABILITY.md's frozen CLI surface against the ``frozen`` rows.
BIN_HIPPO_SUBCOMMANDS: Tuple[str, ...] = tuple(v.verb for v in CLI_VERBS)
FROZEN_BIN_HIPPO_SUBCOMMANDS: Tuple[str, ...] = tuple(v.verb for v in CLI_VERBS if v.frozen)


@dataclass(frozen=True)
class VerbSurface:
    """One ``/hippo:<verb>``'s surface story. ``verb`` == its ``plugin/skills/`` dir."""

    verb: str
    desktop: str  # "tool" | "skill_tools" | "terminal_only" | "route"
    mcp_tools: Tuple[str, ...]  # every tool serving this verb; () iff terminal_only or route
    note: str  # one-line human story (documentation, not linted prose)
    routes_to: str = ""  # "route" rows only: the v2 verb this retired name now opens


# SRF-3: the nine v2 verbs. Each absorbed one or more v1 skills as a section of its own
# SKILL.md (doctor's content audit is a supporting file, read on demand).
V2_VERBS: Tuple[str, ...] = (
    "setup", "new", "recall", "tend", "doctor", "share", "review", "dream", "remove",
)

VERBS: Tuple[VerbSurface, ...] = (
    VerbSurface(
        "setup",
        desktop="skill_tools",
        mcp_tools=("setup", "trust"),
        note="bootstrap once per machine, init once per project; an existing corpus's "
        "consent goes through the trust tool",
    ),
    VerbSurface(
        "new",
        desktop="tool",
        mcp_tools=("new_memory",),
        note="the per-item corpus write, right-by-construction",
    ),
    VerbSurface(
        "recall",
        desktop="tool",
        mcp_tools=("recall", "inspect"),
        note="query recall plus the why receipt, lineage and graph hops (inspect); "
        "--list-by-type/--all-projects stay terminal-only",
    ),
    VerbSurface(
        "tend",
        desktop="skill_tools",
        mcp_tools=("tend", "new_memory"),
        note="the one maintenance queue and the consolidate steps; drafting a capture "
        "goes through new_memory",
    ),
    VerbSurface(
        "doctor",
        desktop="skill_tools",
        mcp_tools=("doctor", "trust"),
        note="health checks + the consent step; the content audit is a supporting file "
        "whose material the doctor tool serves (action='audit')",
    ),
    VerbSurface(
        "share",
        desktop="skill_tools",
        mcp_tools=("share",),
        note="packs through the share tool; promote, promote-rule, publish, export and "
        "import need a terminal (each flow's own guard says so)",
    ),
    VerbSurface(
        "review",
        desktop="tool",
        mcp_tools=("review",),
        note="the corpus review packet (op-classified diff + scoped lints + local recall "
        "preview); --ci is the single memory-diff CI gate",
    ),
    VerbSurface(
        "dream",
        desktop="tool",
        mcp_tools=("dream",),
        note="the offline link pass",
    ),
    VerbSurface(
        "remove",
        desktop="terminal_only",
        mcp_tools=(),
        note="project offboarding; terminal-only by intent",
    ),
    # The v1 skill names, through the deprecation window (v1.42-v1.43): each SKILL.md is a
    # one-line route into its v2 verb, and its preflight counts the old name (OBS-2).
    *(
        VerbSurface(old, desktop="route", mcp_tools=(), note=f"now /hippo:{new}", routes_to=new)
        for old, new in (
            ("bootstrap", "setup"), ("init", "setup"), ("why", "recall"),
            ("consolidate", "tend"), ("resolve", "tend"), ("audit", "doctor"),
            ("pack", "share"), ("promote", "share"), ("promote-rule", "share"),
            ("publish", "share"), ("export-agents", "share"), ("import", "share"),
        )
    ),
)

# MCP tools that serve NO /hippo:* verb. (The v1 repair and incident tools are deprecated
# names now, claimed below through the v2 mapping.)
VERBLESS_TOOLS: Dict[str, str] = {
    "recall_hook": "HOT-6 INTERNAL, unfrozen — the opt-in warm-recall hook's entry; the harness calls it, never a model or a skill",
}

# SRF-2: the v1 tool names, still served through the deprecation window, claimed through
# the v2 route each one names (``mcp_schemas_v2.DEPRECATED``, pure data).
DEPRECATED_TOOLS: Dict[str, str] = {old: new for old, (new, _how) in DEPRECATED.items()}


def verb_map() -> Dict[str, VerbSurface]:
    """``verb -> row`` for lookups; the tuple above stays the declaration of record."""
    return {v.verb: v for v in VERBS}


def route_verbs() -> Dict[str, str]:
    """``retired name -> v2 verb`` for the route rows."""
    return {v.verb: v.routes_to for v in VERBS if v.desktop == "route"}


def terminal_only_verbs() -> Tuple[str, ...]:
    """The verbs whose honest story is 'terminal-only for now', in declaration order."""
    return tuple(v.verb for v in VERBS if v.desktop == "terminal_only")


def claimed_tools() -> frozenset:
    """Every MCP tool name the registry accounts for — must equal ``_DISPATCH`` exactly."""
    tools = set(VERBLESS_TOOLS) | set(DEPRECATED_TOOLS)
    for v in VERBS:
        tools.update(v.mcp_tools)
    return frozenset(tools)
