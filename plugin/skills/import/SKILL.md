---
description: Migration on-ramp — import existing rules/notes from other tools into ranked, staleness-tracked, deduped hippo memories, per-item confirmed and secret-linted. First adapter is Cursor (.cursor/rules/*.mdc, globs become cited paths). Use for "import my cursor rules", "migrate from cursor", "bring in my existing rules", "/hippo:import".
---

# /hippo:import — migrate existing rules into the corpus

Adopters don't start from zero. This skill ingests foreign rule files — starting with
Cursor's `.cursor/rules/*.mdc`, whose `globs:` map near-perfectly onto hippo's cited-path
provenance — as ordinary corpus memories: recall-ranked, staleness-tracked from birth, and
deduped against what the corpus (and the rules plane) already says. **All foreign input is
UNTRUSTED**: every candidate is secret-linted BEFORE it can be written, and a flagged
candidate is held, never imported.

## Preflight (shared across all hippo skills)

```bash
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # Claude Code fills both in when it loads this skill
[ "${CLAUDE_CODE_ENTRYPOINT:-}" != "claude-desktop" ] || { echo "✘ /hippo:import has no Desktop-safe MCP-tool equivalent yet. Run it from a terminal Claude Code session in this repo (claude, then /hippo:import)."; exit 1; }
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] && [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] || { echo "✘ hippo's plugin paths are empty in this shell. Claude Code fills them into this skill's text when it loads the skill (the Bash tool does not inherit them), so run these blocks from the loaded /hippo:import skill, not from a copy of its SKILL.md. If the loaded skill stops here too, this Claude Code does not fill them in: update it and run /hippo:import again."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
hippo_resolve_py
hippo_note_usage skill import  # OBS-2: count this skill's use (one spool line, no Python)
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
```

Each Bash call is a fresh shell, so nothing set here reaches the next call. Every block below
opens by pinning what it needs; give an inline `"$PY" …` command from the text the same pin
and resolver lines, in the same call.

## What this does, in order

1. **Discover + report every candidate (read-only).** One entry per `.mdc`, with
   everything that stands between it and the corpus:

   ```bash
   export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # a fresh shell: pin again
   . "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"; hippo_resolve_py
   REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
   "$PY" -c \
     "import sys, json; from memory.import_mdc import import_candidates; \
      print(json.dumps(import_candidates(repo_root=sys.argv[1]), indent=1))" \
     "$REPO_ROOT"
   ```

   Read each entry to the user before touching anything:
   - `secret_warnings` non-empty — the import WILL be held; the source `.mdc` must be
     cleaned first (foreign input is never written flagged).
   - `route: "review"` + `neighbors` — the corpus already has something close; the user
     decides import-anyway / skip / update-the-existing-memory instead.
   - `rule_neighbors` — the `.mdc` restates CLAUDE.md/.claude/rules content ("link,
     don't copy"): usually skip, and cite the rule from an existing memory if needed.
   - `exists: true` — already imported (a re-run refuses idempotently).
   - `paths_matched` — how many concrete repo files its globs resolve to today (they
     land in the body as an `Applies to:` line and become `cited_paths`; the source
     `.mdc`'s own path lands as a `Source:` line on the same route — see step 3).
   - `always_apply: true` — Cursor kept this rule always-in-context; if the user wants
     the same here, import it, then suggest `steer: pin` or a `feedback`-type rewrite
     via `/hippo:new` — do not silently change its type.

2. **Import per-item — one file, one explicit yes, one run.** For EACH candidate the
   user approves (never a loop over the whole list in one go):

   ```bash
   export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # a fresh shell: pin again
   . "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"; hippo_resolve_py
   REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
   "$PY" -c \
     "import sys, json; from memory.import_mdc import import_mdc_file; \
      r = import_mdc_file(sys.argv[1], repo_root=sys.argv[2], \
                          allow_duplicate=(sys.argv[3] == 'yes')); \
      print(json.dumps(r, indent=1)); sys.exit(0 if r['imported'] else 1)" \
     "$REPO_ROOT/.cursor/rules/<file>.mdc" "$REPO_ROOT" "no"
   ```

   Pass `yes` as the last argument ONLY when step 1 showed `route: "review"` and the
   user explicitly said import-anyway after seeing the neighbors. Outcomes:
   - `imported: true` — done; report the path. The write ran the full shipped pipeline:
     link discovery, provenance backfill (the `Applies to:` paths became `cited_paths`,
     so staleness tracking works from day one), index refresh, floor rules by type.
   - `held ... secret-looking content` — nothing was written. Show the warnings (they
     name the KIND, never the secret), have the user clean the source, re-run step 1.
   - `held ... near-duplicate` — show `neighbors`, ask, re-run with `yes` if confirmed.
   - `already exists` — idempotence, not an error; say so and move on.

3. **Close the loop.** After the confirmed imports: remind the user the new memories are
   ordinary markdown-in-git (commit them), and that the source `.mdc` files are now
   redundant with the corpus — deleting them is THEIR call, in their own editor, not
   this skill's. Either way the memory stays honest: a TRACKED source `.mdc` is in the
   imported memory's `cited_paths` (the `Source:` line), so a later upstream edit — or
   the deletion itself — flags the memory stale at SessionStart/doctor and the RET-6
   verify-at-use banner names it on recall; re-import stays a manual decision. (An
   uncommitted `.mdc` can't be tracked — the fingerprint activates once it's committed.)

## claude-mem migration audit (`--from claude-mem`, v1 AUDIT-ONLY)

Migrating FROM claude-mem (the auto-write-everything incumbent)? v1 is a read-only
audit of its store — what a migration WOULD bring over, with **zero writes** to the
corpus, rules.json, or the pending queue (there is deliberately no write leg yet):

```bash
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # a fresh shell: pin again
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"; hippo_resolve_py
"$PY" -m memory.import_mdc --from claude-mem
```

(`--store <path>` overrides the default `~/.claude-mem/claude-mem.db`; `--project
<name>` scopes candidate scoring to one store project.) Read the JSON to the user:

- `candidates` / `projects` — observation rows (claude-mem's memory-shaped unit) and
  where they came from; `session_summaries` and `user_prompts` are COUNTED only —
  raw prompt text is a privacy surface this audit never reads into a report.
- `dedupe_rate` — the share of candidates whose substance the governance plane
  already carries (`rule_dup_candidates`): high means most of the store is already
  said in CLAUDE.md/AGENTS.md ("link, don't copy" — little to migrate).
- `secret_hits` / `portability_hits` / `threat_hits` — the untrusted-foreign-content
  posture, applied before anything could ever be written: findings name KINDS, never
  values.
- `schema_versions` — claude-mem migrates its store fast; an `error` naming ED-3
  means the format drifted past this adapter and needs a fresh probe, not a guess.

Then STOP: relay the counts and the graduation story. When the user wants rows
actually imported, that is a FUTURE per-item write leg (one observation, one shown
report, one yes — `import_mdc_file`'s pattern with the pack-install
refuse-on-secret posture); do not improvise one from this audit.

## Hard rules

- **Foreign input is untrusted.** The secret-lint hold is not overridable from this
  skill — there is no "import it flagged" path; clean the source instead.
- **Per-item confirm, never bulk.** One `.mdc`, one shown report, one yes, one
  `import_mdc_file` call (inv4). Never loop the write over the candidate list.
- **A duplicate never becomes a file.** `route: "review"` holds unless the user
  explicitly confirms after seeing the neighbors.
- **Type defaults to `project`.** Only the user upgrades an import to `user`/`feedback`
  (floor-linked types) — say why it matters (always-loaded floor space) when they ask.
