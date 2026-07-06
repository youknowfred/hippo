---
description: Run this once per NEW project to seed .claude/memory/ from the starter packs, a skeleton MEMORY.md floor, the cross-machine symlink, and a built recall index. Also safe on an EXISTING corpus (teammate clone, new worktree, second machine) — skips seeding, just wires up this machine's symlink+index. Use when a user says "init memory here", "set up memory for this project", "/hippo:init", or opens a fresh/cloned repo. Idempotent — never overwrites an existing memory file.
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
- **If `.claude/memory/MEMORY.md` already exists (ONB-5), this is an EXISTING CORPUS, not a
  fresh project** — the flagship case here is a teammate cloning the repo, or opening a new
  `git worktree` of a repo already using hippo: the corpus is already in git, but THIS machine
  (or this worktree's `~/.claude/projects/<encoded>` entry) has never had its symlink or index
  built. Do **NOT** hard-stop and do **NOT** touch any existing memory file. Instead, **skip
  steps 1-2** (starter-pack selection, `MEMORY.md` skeleton — there is nothing to seed) and run
  **only the machine-local setup, steps 3-5** (including 4b): symlink, index build, trust-mark,
  `.gitignore` check. Re-running init against an existing corpus is the user explicitly
  reviewing it, so 4b marks it trusted (SEC-1) even on this path. This
  makes re-running `/hippo:init` on an already-initialized project safe and useful — it is how
  `/hippo:doctor` tells a user to repair a missing/broken symlink, instead of routing them back
  to a hard stop.
- Check `${CLAUDE_PROJECT_DIR}` (or `git rev-parse --show-toplevel`) resolves to a real git
  repo (`git rev-parse --show-toplevel` exits non-zero when it isn't). Do **NOT** halt when it
  isn't one — seed the corpus anyway (everything in steps 1-4 below works without git: the
  skeleton MEMORY.md, starter packs, index build, and cross-machine symlink). Skip step 5
  (the `.gitignore` patch — there's no git to ignore anything from) and replace step 6's
  commit nudge with the degradation notice (SHP-4): git init later still finds these files on
  disk and `git add`s them fine, so there was never a real reason to refuse.

## Scenarios this skill handles

- **Fresh project, no corpus yet.** Runs all steps 1-6 below.
- **Teammate clones the repo.** `.claude/memory/MEMORY.md` already exists (it's in git), but
  this machine's `~/.claude/projects/<encoded>/memory` symlink and `.claude/.memory-index/`
  don't exist yet (both are gitignored, so cloning never brings them along). Preflight detects
  the existing corpus and runs steps 3-5 only.
- **New worktree of an existing repo.** `git worktree add` gives the worktree its own working
  directory (and its own `${CLAUDE_PROJECT_DIR}`), so it needs its OWN symlink and index even
  though `.claude/memory/` is the same tracked content as the main worktree. Same preflight
  path as the teammate-clone case: steps 3-5 only.
- **Second machine, same repo.** Identical shape to the teammate-clone case — the corpus
  travels via git, the symlink and index are machine-local and never do.

## What this does, in order

Steps 1-2 are SKIPPED entirely on an existing corpus (see preflight) — jump straight to step 3;
steps 3, 4, 4b (trust-mark), and 5 all still run.

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
3. **Create the cross-machine symlink**. The encoding is the harness's actual rule (SHP-5:
   every non-alphanumeric character becomes a literal `-`, one-for-one, no collapsing, no
   stripping), and the create-or-confirm logic itself is ONE tested Python helper
   (`memory.provenance.create_project_symlink`, ONB-5) — never hand-rolled `ln -s` in bash:
   ```bash
   REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
   . "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
   hippo_resolve_py
   "$PY" -c \
     "import sys, json; from memory.provenance import create_project_symlink; \
      r = create_project_symlink(sys.argv[1], sys.argv[1] + '/.claude/memory'); \
      print(json.dumps(r)); sys.exit(1 if r['status'] == 'conflict' else 0)" \
     "$REPO_ROOT"
   ```
   Non-git dir: `REPO_ROOT` falls back to the current directory (`pwd`) — there is no git
   toplevel to ask for. `status: "already_correct"` is the idempotent no-op path (teammate
   clone / new worktree / re-running init) — nothing to report beyond the health-check line in
   step 6. `status: "conflict"` means the symlink already exists and points somewhere ELSE —
   stop and report the conflict rather than silently overwriting it; a pre-existing symlink to
   a different target is a sign of a prior manual setup that shouldn't be clobbered.
4. **Build the index**: `"$PY" -m memory.build_index --memory-dir .claude/memory --index-dir
   .claude/.memory-index` — reuse the `$PY`/`PYTHONPATH` already resolved by `hippo_resolve_py`
   in step 3 (falls back to bare `python3` if bootstrap hasn't run yet — BM25-only index still
   builds and works).
4b. **Mark this corpus TRUSTED (SEC-1).** Recall is gated: until this machine's user trusts a
   corpus, recall injects nothing from it (a cloned repo's memories are otherwise an unreviewed
   prompt-injection channel). Running `/hippo:init` here IS the user's explicit review — whether
   they just created the corpus (steps 1-2) or re-ran init against an existing one (ONB-5) —
   so mark it trusted now. Reuse the `$PY` + `REPO_ROOT` from step 3:
   ```bash
   "$PY" -c \
     "import sys, json; from memory.trust import mark_trusted; \
      print(json.dumps({'trusted': mark_trusted(sys.argv[1])}))" \
     "$REPO_ROOT"
   ```
   The marker lives in the machine-local `~/.claude/hippo-trust.json` (OUTSIDE the project, so
   a foreign repo can't commit its own "trust me"). A `false` result means the registry write
   failed — report it (recall will stay gated until it succeeds), don't pretend it's trusted.
5. **Patch `.gitignore`** — SKIP entirely when not a git repo (there's nothing for git to
   ignore yet; a future `git init` + this same nudge in step 6, once repeated after init, is
   how it gets patched). In a git repo, append `.claude/.memory-index/` and
   `.claude/.memory-telemetry/` if not already present (derived, rebuildable — never commit
   them). Do NOT create `.gitignore` from scratch if the project doesn't have one without
   asking first — a repo with zero `.gitignore` may be intentional (e.g. a throwaway test repo).
6. **Nudge, don't commit — or, in a non-git dir, name the degradation. On an existing corpus
   (ONB-5), report machine-local setup instead of a seeding nudge.** On a FRESH project in a
   git repo: print the exact `git add .claude/memory .gitignore && git commit -m "seed agent
   memory"` command and STOP there. Never auto-commit the user's repo — memory corpus content
   is exactly the kind of thing a user should look at before it enters their history. In a
   NON-git dir (SHP-4), skip that nudge (there's no git to commit to) and print this notice
   instead: "Not a git repository — hippo is running in DEGRADED mode: staleness tracking,
   provenance backfill, and archive's git-mv path are all INACTIVE until you `git init` and
   commit. Recall, indexing, links, and floor loading all work normally." On an EXISTING
   corpus (steps 1-2 skipped): there is nothing to commit — report what steps 3-4 actually did
   instead, e.g. "✔ symlink created → ~/.claude/projects/<encoded>/memory" or "✔ symlink
   already correct" plus the index build result, so the user sees this machine is now wired up
   without re-reading the whole corpus. If `user_role.md` still contains `<FILL-ME` at this
   point (any path, fresh or existing), END the report with an explicit warning: "⚠
   user_role.md is still the unfilled template — recall will index its placeholder text until
   you edit it (/hippo:doctor flags this too)."

## Hard rules

- **Never write outside `.claude/memory/`, `.claude/.memory-index/`, `.claude/.memory-telemetry/`,
  `.gitignore`, and the one symlink.** No other files, no other directories.
- **Never overwrite an existing memory file.** If a name collision occurs (unlikely for a fresh
  project, but check), skip that one file and report it rather than silently clobbering.
- **Never auto-commit.** The nudge in step 6 is the end of this skill's responsibility.
- Re-running on an already-initialized project (ONB-5) is safe and idempotent, NOT a hard
  stop: it skips seeding (steps 1-2, memory files untouched) and repeats only the
  machine-local setup (symlink, index, `.gitignore` check) — steps 3-5 are naturally
  idempotent (an already-correct symlink is a no-op, a fresh `build_index` call is
  content-hashed so an unchanged corpus re-embeds nothing, an already-patched `.gitignore` is
  left alone). This skill still does not have a corpus-editing "update" mode — detecting
  content DRIFT (stale memories, broken links) is `/hippo:doctor` and `/hippo:audit`'s job, not
  this one's.
