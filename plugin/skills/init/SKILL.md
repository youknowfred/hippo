---
description: Run this once per NEW project to seed .claude/memory/ from the starter packs (core by default, themed packs opt-in), a skeleton MEMORY.md floor, the cross-machine symlink, and a built recall index. Use when a user says "init memory here", "set up memory for this project", "/hippo:init", or opens a fresh repo with no .claude/memory/ directory yet. Idempotent — never overwrites an existing memory file.
---

# /hippo:init — seed a project's memory corpus

Builds the two seams a copy-isolated plugin cannot reach on its own: the consuming project's
own `.claude/memory/` corpus, and the machine-local `~/.claude/projects/<encoded>/memory`
symlink Claude Code's native memory system reads from.

## Preflight

- **Guard `CLAUDE_PLUGIN_DATA` first** (shared across all hippo skills — step 4 expands it):
  ```bash
  [ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ CLAUDE_PLUGIN_DATA is unset/empty — this Claude Code version is too old for hippo's self-provisioning. Update Claude Code, or export CLAUDE_PLUGIN_DATA to a writable dir (e.g. ~/.claude/hippo-data) and re-run."; exit 1; }
  ```
- If `.claude/memory/MEMORY.md` already exists, **STOP** and report it — do not touch an
  existing corpus. Suggest `/hippo:doctor` instead if the user wants a health check.
- Confirm `${CLAUDE_PROJECT_DIR}` (or `git rev-parse --show-toplevel`) resolves to a real git
  repo. `/hippo:init` outside a git repo has nowhere durable to seed — halt with a clear
  message rather than writing into an ungit'd directory that a future `git init` won't pick up.

## What this does, in order

1. **Offer the starter packs — default is core only.** The packs live in
   `${CLAUDE_PLUGIN_ROOT}/assets/packs/` (one directory per pack, each with a `manifest.json`;
   see `assets/packs/README.md` for the inclusion criteria). Ask the user which packs to seed
   (AskUserQuestion where available, otherwise a plain listed question), presenting each
   optional pack's title + one-line description from its manifest. Rules:
   - `core` (the `user_role.md` template + `claude_is_memory_master.md`) is offered by
     default; every OTHER pack defaults to NOT seeded — an unanswered/skipped menu means
     core only. These files are committed to the repo and steer behavior for every teammate;
     each extra policy must be an explicit choice.
   - Manifest entries carrying `"confirm": "individual"` (the attribution and CI-bypass
     policies) require their OWN yes even when their pack was selected — present the
     manifest's `reason` and ask separately; a pack-level yes is not consent for these.
   - Copy only the chosen packs' `*.md` memory files into `.claude/memory/` verbatim
     (manifests and the pack README stay in the plugin, never copied).
2. **Seed `MEMORY.md`** from `${CLAUDE_PLUGIN_ROOT}/assets/MEMORY.skeleton.md` — the skeleton
   ships with floor pointers for the core pack only. For every ADDITIONAL `user`/`feedback`
   memory actually copied in step 1, append a floor pointer line under the matching section
   (`## User` for `user` types, `## Working Style & Process Feedback` for `feedback`),
   `- [Title](file.md) — one-line hook` — mirror the skeleton's existing pointer style.
   `project`/`reference` memories never get floor pointers. `user_role.md` ships as an
   editable `<FILL-ME>` template — tell the user to fill it in with their own role/context
   before their first real session (skip this reminder only if they explicitly say they'll
   do it later).
3. **Create the cross-machine symlink**:
   ```bash
   REPO_ROOT="$(git rev-parse --show-toplevel)"
   ENCODED="-$(echo "$REPO_ROOT" | tr '/' '-' | sed 's/^-//')"
   SYMLINK_DIR="$HOME/.claude/projects/${ENCODED}"
   mkdir -p "$SYMLINK_DIR"
   [ -L "$SYMLINK_DIR/memory" ] || ln -s "$REPO_ROOT/.claude/memory" "$SYMLINK_DIR/memory"
   ```
   If the symlink already exists and points somewhere ELSE, stop and report the conflict rather
   than silently overwriting it — a pre-existing symlink to a different target is a sign of a
   prior manual setup that shouldn't be clobbered.
4. **Build the index**: `PYTHONPATH="${CLAUDE_PLUGIN_ROOT}" "${CLAUDE_PLUGIN_DATA}/venv/bin/python"
   -m memory.build_index --memory-dir .claude/memory --index-dir .claude/.memory-index` (fall back
   to bare `python3` if bootstrap hasn't run yet — BM25-only index still builds and works).
5. **Patch `.gitignore`**: append `.claude/.memory-index/` and `.claude/.memory-telemetry/` if not
   already present (derived, rebuildable — never commit them). Do NOT create `.gitignore` from
   scratch if the project doesn't have one without asking first — a repo with zero `.gitignore`
   may be intentional (e.g. a throwaway test repo).
6. **Nudge, don't commit.** Print the exact `git add .claude/memory .gitignore && git commit -m
   "seed agent memory"` command and STOP there. Never auto-commit the user's repo — memory
   corpus content is exactly the kind of thing a user should look at before it enters their
   history. If `user_role.md` still contains `<FILL-ME` at this point, END the report with an
   explicit warning: "⚠ user_role.md is still the unfilled template — recall will index its
   placeholder text until you edit it (/hippo:doctor flags this too)."

## Hard rules

- **Never write outside `.claude/memory/`, `.claude/.memory-index/`, `.claude/.memory-telemetry/`,
  `.gitignore`, and the one symlink.** No other files, no other directories.
- **Never overwrite an existing memory file.** If a name collision occurs (unlikely for a fresh
  project, but check), skip that one file and report it rather than silently clobbering.
- **Never auto-commit.** The nudge in step 6 is the end of this skill's responsibility.
- Re-running on an already-initialized project is a no-op per the preflight check — this skill
  does not have an "update" mode; that's `/hippo:doctor`'s job to detect drift, not this one's
  job to silently patch.
