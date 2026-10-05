"""SRF-1: the ``hippo <verb>`` table — every engine entry point, declared once.

``bin/hippo`` is the one door into the engine: skills, hooks and the hints hippo prints
all spell a call as ``hippo <verb> [args...]``. ``memory.cli`` turns the verb into the
module below and runs that module exactly as ``python -m memory.<module>`` would (same
argv, same exit code, same output), so moving a caller onto the door changes nothing
but its spelling.

Pure data with no imports, so the door pays nothing to read it, and the surface registry
(``surfaces.py``, a build-time artifact the hot path never imports) re-exports it as
``BIN_HIPPO_SUBCOMMANDS``, so the registry lints, the usage text and the STABILITY pins
all read this one list.

Rows:
  - ``frozen``: in STABILITY.md's v1.0 CLI surface. Renaming one is a major-version bump.
  - ``internal``: a hook's own entry. Callable, but left out of the usage text.
  - everything else: added after v1.0, so not frozen. STABILITY v2 decides which stay.

Editing rules: a new verb is one row here (``tests/test_cli.py`` imports every module
and checks that the frozen rows match STABILITY.md); a verb that a hint or skill names
must exist here (``tests/test_surface_registry.py`` checks every ``hippo <verb>`` hippo
prints).
"""

from __future__ import annotations

from typing import NamedTuple, Tuple


class CliVerb(NamedTuple):
    verb: str
    module: str  # the memory.<module> run as __main__
    summary: str  # one line for the usage text
    frozen: bool = False
    internal: bool = False


CLI_VERBS: Tuple[CliVerb, ...] = (
    # The v1.0 frozen surface (STABILITY.md), in its stated order.
    CliVerb("recall", "recall_hook", "recall memories for a query (the hook's ranking)", frozen=True),
    CliVerb("new", "new_memory", "write one memory, right by construction", frozen=True),
    CliVerb("build-index", "build_index", "rebuild the recall index and link graph", frozen=True),
    CliVerb("staleness", "staleness", "report memories whose cited code moved", frozen=True),
    CliVerb("mcp", "mcp_server", "serve the MCP tools over stdio", frozen=True),
    CliVerb("sleep", "sleep", "the headless maintenance report", frozen=True),
    CliVerb("review", "review", "review a memory diff like a pull request", frozen=True),
    # Added in v1.42 (SRF-1): every other entry the skills, hooks and hints call.
    CliVerb("inspect", "recall_view", "a readable recall listing, why receipts, decision history"),
    CliVerb("doctor", "doctor", "health check for the install and the corpus"),
    CliVerb("tend", "tend", "the maintenance queue: list, next, apply one verdict"),
    CliVerb("migrate", "migrate", "what the next corpus format will touch (--check, read-only)"),
    CliVerb("capture", "capture", "the pending-capture queue: list, discard, snooze"),
    CliVerb("reconsolidate", "reconsolidate", "the reverify worklist and per-item verdicts"),
    CliVerb("brief", "reconsolidate_brief", "the evidence brief for one stale memory"),
    CliVerb("resolve", "resolve_view", "the contradiction inbox and per-pair verdicts"),
    CliVerb("dream", "dream", "find latent links; undo, log and draft tiers"),
    CliVerb("links", "links", "the link graph: audit and repair"),
    CliVerb("lint-links", "lint_links", "dangling links, orphans, boundary candidates"),
    CliVerb("lint-floor", "lint_floor", "check MEMORY.md against its budgets"),
    CliVerb("publish", "publish", "preflight one memory for the committed subset"),
    CliVerb("promote-rule", "promote_rule", "propose one memory as a scoped rule"),
    CliVerb("promote-scan", "promote_scan", "list memories ready for the user tier"),
    CliVerb("import", "import_mdc", "import rules and notes from other tools"),
    CliVerb("recall-diff", "recall_diff", "how a commit range shifts recall"),
    CliVerb("blast-radius", "blast_radius", "which sessions touched what (read-only)"),
    CliVerb("archive", "archive", "archive and restore memories"),
    CliVerb("provenance", "provenance", "citation provenance: backfill, re-derive, stamp"),
    CliVerb("registry", "registry", "the machine's registered corpora"),
    CliVerb("secrets", "secrets", "scan text or files for secrets"),
    CliVerb("eval", "eval_recall", "recall quality against the pinned fixtures"),
    CliVerb("census", "machine_census", "every hippo corpus on this machine"),
    CliVerb("soak", "soak", "record usage for the soak report"),
    # Hook entries: the hooks call these through the door like everything else.
    CliVerb("session-start", "session_start", "the SessionStart report", internal=True),
    CliVerb("outcome", "outcome", "log a file touch (PostToolUse)", internal=True),
)


# Multi-step flows that live in a skill, not behind a verb: ``hippo <name>`` names the
# skill to run instead (exit 1), the v1.0 redirect behavior.
SKILL_REDIRECTS: Tuple[str, ...] = ("init", "bootstrap", "audit")


def verb_table() -> dict:
    """``verb -> CliVerb``; the tuple above stays the declaration of record."""
    return {row.verb: row for row in CLI_VERBS}
