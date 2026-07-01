"""Agent-memory activation tooling (Option B from the TrustGraph-inward exploration).

Local, no-server tooling over the markdown memory corpus at ``.claude/memory/``.
Markdown-in-git is the single source of authority; everything here is derived,
rebuildable, and side-effect-light:

- ``provenance``    — extract ``path:line`` citations from a memory body into additive
                       ``cited_paths`` / ``source_commit`` frontmatter (idempotent;
                       never edits the body).
- ``staleness``     — flag memories whose cited code changed since their ``source_commit``
                       (git-drift signal, not calendar age).
- ``session_start`` — the SessionStart hook dispatcher: one corpus load, one merged
                       ``additionalContext`` (staleness now; git-recent + link-health
                       added by later tiers).

See ``memory/README.md`` and
``docs/plans/active/agent-memory-activation-layer-2026-06-23.yaml``.
"""
