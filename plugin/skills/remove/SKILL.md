---
description: Uninstall/offboarding for THIS project — removes the cross-machine symlink so native memory stops injecting the floor, offers to delete the derived index/telemetry dirs, and reports (never deletes) the shared venv/model-cache paths. The committed .claude/memory/ corpus is always left alone, inert in git. Use for "remove hippo from this project", "uninstall memory", "/hippo:remove".
---

# /hippo:remove — uninstall and offboarding path

The teardown counterpart to `/hippo:init`. Scoped to **this one project** — it never touches
another project's symlink, another project's corpus, or the shared per-machine venv/model cache
(those are reported for reclamation, never deleted, since other projects may still depend on
them).

## Preflight (shared across all hippo skills)

```bash
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ CLAUDE_PLUGIN_DATA is unset/empty — this Claude Code version is too old for hippo's self-provisioning. Update Claude Code, or export CLAUDE_PLUGIN_DATA to a writable dir (e.g. ~/.claude/hippo-data) and re-run."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
hippo_resolve_py
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
```

## What this does, in order

1. **Remove the cross-machine symlink.** This is the one step that actually stops native memory
   from injecting the floor — as long as `~/.claude/projects/<encoded>/memory` resolves to this
   project's `.claude/memory`, Claude Code keeps reading it every session regardless of anything
   else in this skill. Use the ONE tested Python helper
   (`memory.provenance.remove_project_symlink`, ONB-6 — the exact inverse of ONB-5's
   `create_project_symlink`, same SHP-5 encoding formula) — never hand-rolled `rm` in bash:
   ```bash
   "$PY" -c \
     "import sys, json; from memory.provenance import remove_project_symlink; \
      r = remove_project_symlink(sys.argv[1], sys.argv[1] + '/.claude/memory'); \
      print(json.dumps(r)); sys.exit(1 if r['status'] == 'conflict' else 0)" \
     "$REPO_ROOT"
   ```
   - `status: "removed"` — done; report the path that was unlinked.
   - `status: "absent"` — nothing was there; report "no symlink found for this project (already
     removed, or never initialized here)" rather than treating it as an error.
   - `status: "conflict"` — the symlink at that path points at a DIFFERENT directory than this
     project's `.claude/memory`. **Stop and report it rather than forcing removal** — this
     usually means either a prior manual setup or that `$REPO_ROOT` doesn't match what you
     expect; forcing it risks unlinking a symlink that belongs to a different project's corpus.

2. **Offer to delete the derived, gitignored dirs — agent-gated, never unconditional.** Ask the
   user (in this skill's own conversational turn) before deleting anything here; a "yes" to step
   1 is not consent for step 2. If confirmed, remove:
   - `.claude/.memory-index/` (the recall index — rebuildable any time via
     `/hippo:init` or `memory.build_index`)
   - `.claude/.memory-telemetry/` (recall/episode/reconsolidation ledgers —
     `recall_events.jsonl`, `episode_buffer.jsonl`, `reconsolidation_events.jsonl` all live here;
     deleting the directory takes all three with it)

   Both are already gitignored derived state — deleting them loses no git history and nothing
   committed. If the user declines, leave them exactly as they are and say so plainly (they're
   inert without the symlink from step 1 anyway — nothing reads them once native memory has
   nothing pointing at this corpus).

3. **Report (never delete) the shared, per-machine paths.** These are NOT per-project — removing
   them affects every other project on this machine still using the plugin, so this skill only
   ever prints them for the user's own manual reclamation:
   - venv: `${CLAUDE_PLUGIN_DATA}/venv`
   - fastembed model cache: the path `memory.build_index.durable_fastembed_cache_dir()` returns
     (print it via `"$PY" -c "from memory.build_index import durable_fastembed_cache_dir; print(durable_fastembed_cache_dir())"`)

   State explicitly: "these are shared across every project using hippo on this machine — only
   delete them yourself if you're removing hippo everywhere, not just from this project."

4. **Explain what was left alone.** End every run with this, regardless of what steps 2-3 did:
   `.claude/memory/` itself — the git-tracked corpus — is untouched and stays committed in git,
   inert, until someone runs `/hippo:init` again (in this repo or a fresh clone of it). Removal
   never edits, deletes, or archives a single memory file.

## Hard rules

- **Never delete `.claude/memory/` itself.** That is the git-tracked corpus, entirely out of
  scope for this skill — only the symlink pointing AT it, and the derived caches beside it, are
  ever candidates for removal here.
- **Step 2 is agent-gated, not automatic.** No `--all`, no silent sweep — a confirmed step in
  this skill's own conversation, every time.
- **Never delete the venv or model cache.** Report their paths only; a human decides whether
  removing hippo everywhere on this machine is actually what they want.
- **A `conflict` status on the symlink removal is a hard stop, not a force-through.** A symlink
  pointing somewhere else means this call doesn't know enough to safely act — report it and let
  the user resolve it by hand.

## After removal

No hook still fires for this project in a way that matters — `SessionStart` and
`UserPromptSubmit` still run (they're global to the plugin, not per-project), but with the
symlink gone, Claude Code's native memory has nothing to read for this repo, so there is no more
floor injection and no more recall output tied to this corpus. Re-running `/hippo:init` here
later picks the existing `.claude/memory/` corpus back up exactly where it was left (ONB-5's
existing-corpus path) — nothing was lost.
