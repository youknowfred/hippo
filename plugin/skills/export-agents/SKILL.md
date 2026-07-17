---
description: Render this project's memory floor as a PROPOSED AGENTS.md diff — one ranked, drift-checked source for the cross-tool rule plane (Codex/Cursor/Copilot all read AGENTS.md). Glob scoping is derived from each memory's cited_paths; propose-only, never an authoritative overwrite; once applied, the exported file is drift-checked by doctor/SessionStart. Use for "export to AGENTS.md", "sync my agent rules", "fan out the floor", "/hippo:export-agents".
---

# /hippo:export-agents — the floor as a proposed AGENTS.md diff

Every agent tool reads its own hand-maintained rules file; they all drift. hippo's floor
(the memories pinned in the corpus `MEMORY.md`) is the ranked, staleness-tracked version
of the same content. This skill renders that floor as ONE proposed `AGENTS.md` — the
Linux-Foundation cross-tool standard — as a **reviewable diff, never a write**. The
corpus stays the authority: to change `AGENTS.md`, edit the memories and re-export.

Scope is deliberately narrow (reach, not core): `AGENTS.md` only, one shot per run, no
auto-sync cadence. Only the PROJECT tier exports — user/private memories never enter a
repo-committed file (the no-git-leak invariant).

## Preflight (shared across all hippo skills)

```bash
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ CLAUDE_PLUGIN_DATA is unset/empty in this shell — this does NOT necessarily mean Claude Code is too old: on some surfaces (e.g. Claude Desktop) the agent's Bash tool never inherits plugin-scoped env vars even on a fully current, correctly-bootstrapped install, since only hippo's MCP server and hooks (not the general Bash tool) receive them. This skill has no Desktop-safe MCP-tool equivalent yet — re-run it from a terminal Claude Code session. If this IS a genuine terminal Claude Code session and you still see this, Claude Code likely is too old for hippo's self-provisioning — update it, or export CLAUDE_PLUGIN_DATA to a writable dir (e.g. ~/.claude/hippo-data) and re-run."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
hippo_resolve_py
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
MEMORY_DIR="$REPO_ROOT/.claude/memory"
```

## What this does, in order

1. **Render the proposal (read-only).** Nothing is written — the output IS the decision
   surface:

   ```bash
   "$PY" -c \
     "import sys; from memory.export_agents import export_agents, describe; \
      print(describe(export_agents(memory_dir=sys.argv[1], repo_root=sys.argv[2])))" \
     "$MEMORY_DIR" "$REPO_ROOT"
   ```

   Read the report to the user, concretely:
   - each section's scope (`Applies to:` globs derived from the memory's `cited_paths`;
     a single citation stays a literal path — exact drift detection — and a collapsed
     glob is capped so it can never balloon into a near-unscoped always-load);
   - every `⚑` flag (over-scoped globs kept as literals, cited paths missing from the
     tree, a preserved foreign frontmatter) and every skipped memory (retired ones do
     not fan out);
   - the unified diff. Hand-maintained content outside the managed markers is preserved
     byte-verbatim — the export proposes a diff, it never regenerates the file.

2. **Show the curation receipt (read-only) — WHY each floor line earned export.** The
   counter-story to "LLM-generated AGENTS.md hurts": this export is curated, and the
   receipt is the evidence, per floor line — recall strength under the soak-maturity
   gate (a thin corpus honestly reads *insufficient evidence*, never a false-clean
   0.0), staleness from the last scan (an absent cache reads *unknown*, never
   *fresh*), graduation stamps (type / confidence / last_verified), conflict-radar
   hits (authority gaps, superseded/contradicted floor lines), what was excluded and
   why, and rot already present in the prior AGENTS.md block:

   ```bash
   "$PY" -c \
     "import sys; from memory.export_receipts import curation_receipt, describe_receipt; \
      print(describe_receipt(curation_receipt(memory_dir=sys.argv[1], repo_root=sys.argv[2])))" \
     "$MEMORY_DIR" "$REPO_ROOT"
   ```

   Evidence is DISPLAY-ONLY: it never selects, filters, or ranks what exports (the
   proposed AGENTS.md is byte-identical with or without this step). A `⚑` here is a
   reason to go fix the memory (reverify, resolve, retire) and re-run step 1 — not a
   knob this skill turns for you.

3. **Review with the user.** This is the inv4 gate: the user (or the agent with the
   user's explicit go-ahead) approves the diff as a whole, or edits memories / floor
   pins and re-runs step 1. A refusal (`✘ export-agents refused: …`) means nothing to
   decide — relay the reason (empty floor, corrupt managed block) and stop.

4. **Apply only on explicit approval.** This re-renders from the current floor and
   writes the proposed file — run it ONLY after step 3's yes:

   ```bash
   "$PY" -c \
     "import sys; from memory.export_agents import export_agents; \
      r = export_agents(memory_dir=sys.argv[1], repo_root=sys.argv[2]); \
      r['proposed'] or sys.exit('✘ refused: ' + str(r.get('reason'))); \
      f = open(sys.argv[3], 'w', encoding='utf-8'); f.write(r['proposed']); f.close(); \
      print('wrote', sys.argv[3], '-', r['bytes'], 'bytes')" \
     "$MEMORY_DIR" "$REPO_ROOT" "$REPO_ROOT/AGENTS.md"
   ```

   Committing the file is the user's call, like any other working-tree change.

5. **Tell the user what stays true afterwards.**
   - The exported file is now DRIFT-CHECKED: a cited path that later moves flags loud in
     `/hippo:doctor` and the SessionStart rules-rot card (dead `paths:` globs in the
     frontmatter; rotten backtick refs in `Applies to:` lines).
   - Exported memories are governance-cited (backtick stems in the section headings), so
     they are archive-protected and visible to the conflict radar.
   - Re-running the skill later refreshes ONLY the managed block; anything the team
     hand-wrote around it survives.

## Hard rules

- **Propose, never overwrite.** The render step writes nothing; the apply step runs only
  after an explicit yes on the shown diff. No flag skips the review.
- **The corpus is the one authority (inv1).** Never hand-edit inside the managed block —
  edit the memory and re-export. Hand edits there are replaced by the next export.
- **AGENTS.md only.** No other tool's rules file, no auto-sync cadence, no watcher —
  re-export is always a deliberate, per-run decision.
- **Project tier only.** User/private-tier memories never render into a committed file;
  promote/demote between tiers is `/hippo:promote` territory.
- **A refusal means nothing changed.** Corrupt markers are repaired by hand, never
  guessed around.
