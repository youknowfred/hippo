---
description: Set hippo up — once per machine, build the plugin's venv and offline model cache; once per project, seed or connect the memory corpus (a teammate's clone, a second machine or a worktree just gets wired). Triggers include "set up hippo", "bootstrap memory", "init memory here", "set up memory for this project", "/hippo:setup". Idempotent — never overwrites a memory file.
---

# /hippo:setup — machine and project setup

Two setup steps, each safe to re-run:

- **Bootstrap**, once per machine: the plugin's own venv and the offline embedding model.
  It is the one online step in hippo's lifecycle. See "Bootstrap — once per machine" below.
- **Init**, once per project: seed a new corpus, or wire an existing one (a teammate's clone,
  a second machine) to this machine. See "Init — once per project" below.

Run bootstrap first when the plugin is freshly installed, then init in each project.

## Surface routing — decide first, then act silently

- **On Claude Desktop** (your context says you are in the Claude desktop app, `CLAUDE_CODE_ENTRYPOINT` is `claude-desktop`, or the preflight below stops on an unset `CLAUDE_PLUGIN_DATA`): drive setup through the `setup` MCP tool — action='bootstrap' with step='start' (then step='status' until it finishes), and action='init' for this project. An existing corpus that init does not trust goes through the `trust` tool (action='review', then 'grant' with its digest once the user agrees). Call the tools with no preamble; don't explain why the shell flow isn't used here.
- **In a terminal Claude Code session**: run the bash flow below, guard first.

## Preflight (shared across all hippo skills)

```bash
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # Claude Code fills both in when it loads this skill
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] && [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] || { echo "✘ hippo's plugin paths are empty in this shell. Claude Code fills them into this skill's text when it loads the skill (the Bash tool does not inherit them), so run these blocks from the loaded /hippo:setup skill, not from a copy of its SKILL.md. If the loaded skill stops here too, take the MCP-tool route in 'Surface routing' above."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # the shared interpreter resolver
hippo_resolve_py
hippo_note_usage skill setup  # count this skill's use (one spool line, no Python)
```

Each Bash call is a fresh shell, so nothing set here reaches the next call. Every block below
opens by pinning what it needs; an inline `hippo …` command runs as written (`hippo` is on
the Bash tool's PATH and finds its own venv).

## Bootstrap — once per machine

This is the **one online step** in this plugin's entire lifecycle. Every other operation
(recall, staleness, reconsolidation, archive) is offline-only by hard contract. Bootstrap
exists precisely so those hooks never have to be.

### Before you start

Every code block below expands the plugin data dir variable — unset, `uv venv "/venv"`
would provision into a root-owned path. Run this guard FIRST and stop if it fails:

```bash
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # Claude Code fills both in when it loads this skill
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] && [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] || { echo "✘ hippo's plugin paths are empty in this shell. Claude Code fills them into this skill's text when it loads the skill (the Bash tool does not inherit them), so run these blocks from the loaded /hippo:setup skill, not from a copy of its SKILL.md. If the loaded skill stops here too, take the MCP-tool route in 'Surface routing' above."; exit 1; }
```

### What this does

1. **Idempotency check first.** Read `${CLAUDE_PLUGIN_DATA}/.bootstrap-sentinel` (a small
   JSON file: `{"requirements_hash": "<sha256 of plugin/requirements.txt>", "bootstrapped_at": "..."}`).
   If it exists AND its `requirements_hash` matches the CURRENT `${CLAUDE_PLUGIN_ROOT}/requirements.txt`
   hash, report "already bootstrapped, nothing to do" and STOP. A stale hash (plugin updated,
   deps changed) means re-provision, not skip.
2. **Build the venv.** First check whether the system `python3` is inside the supported
   window — **3.9 through 3.13** — this plugin's pinned deps (`numpy>=1.26,<3`, matched to
   `fastembed`'s numpy-2 support) target:
   ```bash
   PYVER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "")"
   PYOK=1
   case "$PYVER" in
     3.9|3.10|3.11|3.12|3.13) PYOK=1 ;;
     "") PYOK=0 ;;
     *) PYOK=0 ;;
   esac
   ```
   - If `PYOK=1` (or version detection failed but `python3` exists — best effort, don't block
     on a detection quirk): `uv venv "${CLAUDE_PLUGIN_DATA}/venv"` if `uv` is on PATH, else
     `python3 -m venv "${CLAUDE_PLUGIN_DATA}/venv"` as a fallback.
   - If `PYOK=0` (system `python3` is outside 3.9–3.13, e.g. a brand-new 3.14+ default on a
     fresh machine) **and `uv` is on PATH**: prefer a pinned interpreter instead of the
     unsupported system one — `uv venv --python 3.12 "${CLAUDE_PLUGIN_DATA}/venv"` (uv fetches
     3.12 itself if it isn't already installed).
   - If `PYOK=0` **and `uv` is NOT on PATH**: don't attempt `python3 -m venv` — it would fail
     deep inside a numpy source build with an opaque traceback. Fail loudly and actionably
     instead: `echo "✘ system python3 is $PYVER, outside hippo's supported window (3.9–3.13),
     and uv is not on PATH to fetch a supported interpreter. Install uv
     (https://docs.astral.sh/uv/) and re-run bootstrap, or install a 3.9–3.13 python3 and
     re-run."; exit 1`
   Then install:
   ```bash
   export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # a fresh shell: pin again
   [ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ hippo's plugin paths are empty in this shell; see the preflight above."; exit 1; }
   uv pip install -r "${CLAUDE_PLUGIN_ROOT}/requirements.txt" --python "${CLAUDE_PLUGIN_DATA}/venv/bin/python"
   # fallback if uv is absent:
   "${CLAUDE_PLUGIN_DATA}/venv/bin/pip" install -q -r "${CLAUDE_PLUGIN_ROOT}/requirements.txt"
   ```
3. **Warm the model cache OFFLINE-SAFE.** This is the actual online step — it downloads the
   ~130MB `bge-small-en-v1.5` ONNX model via `fastembed` the FIRST time only. Run:
   ```bash
   export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # a fresh shell: pin again
   [ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ hippo's plugin paths are empty in this shell; see the preflight above."; exit 1; }
   PYTHONPATH="${CLAUDE_PLUGIN_ROOT}" "${CLAUDE_PLUGIN_DATA}/venv/bin/python" -c \
     "from memory.build_index import ensure_fastembed_cache_path; ensure_fastembed_cache_path(); from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"
   ```
   `ensure_fastembed_cache_path()` pins the cache under `${CLAUDE_PLUGIN_DATA}/fastembed` —
   never `$TMPDIR`. The lesson behind that pin: unset, fastembed caches under
   `$TMPDIR/fastembed_cache`, which macOS purges on a schedule — and the hooks are offline by
   hard contract, so they can never re-download a wiped model. Recall would silently degrade
   to BM25 until someone re-ran bootstrap. This step MUST land the model somewhere durable.

   Also warm the RCL-5 cross-encoder (a small ~80MB model, `/hippo:recall` and the MCP recall
   tool's offline rerank — never the hot path). Best-effort: a failure here must NOT fail
   bootstrap (the rerank already degrades to the un-reranked order on any cache miss):
   ```bash
   export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # a fresh shell: pin again
   [ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ hippo's plugin paths are empty in this shell; see the preflight above."; exit 1; }
   PYTHONPATH="${CLAUDE_PLUGIN_ROOT}" "${CLAUDE_PLUGIN_DATA}/venv/bin/python" -c \
     "from memory.build_index import ensure_fastembed_cache_path; ensure_fastembed_cache_path(); from fastembed.rerank.cross_encoder import TextCrossEncoder; TextCrossEncoder('Xenova/ms-marco-MiniLM-L-6-v2')" \
     || true
   ```
4. **Write the sentinel** on success: `{"requirements_hash": "<hash>", "bootstrapped_at": "<ISO
   timestamp>", "plugin_version": "<version field from ${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json>"}`
   to `${CLAUDE_PLUGIN_DATA}/.bootstrap-sentinel`. This is what step 1 checks — without it, every
   session would re-attempt a multi-second venv build. `plugin_version` records WHICH plugin
   version this venv was provisioned for, so `/hippo:doctor` can flag an installed-vs-bootstrapped
   version delta after an update (DOC-7); an older sentinel that predates this field simply reads
   as "unknown" and prompts a re-bootstrap to record it.
5. **Report** what happened: fresh bootstrap vs. re-provision (dep change detected) vs. already
   current. If `uv` was unavailable and the `venv` fallback was used, say so (slower but works).
   If the system `python3` was outside the supported window and `uv --python 3.12` was used
   instead, say that too — the venv's interpreter deliberately differs from `python3` on PATH.

### `--multilingual` — opt-in multilingual embedding preset (RET-3 / OQ-4)

The default dense model (`BAAI/bge-small-en-v1.5`) is English-only — trained and evaluated on
English text. This plugin's Unicode tokenization (BM25 side) works correctly for ANY language
unconditionally, but the DENSE half only understands what its model was trained on. If your
corpus is mostly written in a non-English language (Japanese, Russian, etc. — `/hippo:doctor`
will flag this for you if it notices), switch to a multilingual model instead:

1. Run the SAME venv-build steps above first (`--multilingual` doesn't skip provisioning), then
   **write the model preset** so the choice persists across sessions without an env var:
   ```bash
   export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # a fresh shell: pin again
   [ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ hippo's plugin paths are empty in this shell; see the preflight above."; exit 1; }
   PYTHONPATH="${CLAUDE_PLUGIN_ROOT}" "${CLAUDE_PLUGIN_DATA}/venv/bin/python" -c \
     "import json, os; os.makedirs(os.environ['CLAUDE_PLUGIN_DATA'], exist_ok=True); \
      json.dump({'embed_model': 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'}, \
      open(os.path.join(os.environ['CLAUDE_PLUGIN_DATA'], 'model.json'), 'w'))"
   ```
   `resolve_embed_model()` (in `memory/build_index.py`) reads this file — `HIPPO_EMBED_MODEL`
   still overrides it if set, otherwise every subsequent build/recall picks up the multilingual
   model automatically. This is the SAME preset file `/hippo:doctor`'s non-English-corpus check
   points users at.
2. **Warm THAT model** (mirrors step 3 above, but for the multilingual id — a separate ~220MB
   ONNX download the first time):
   ```bash
   export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # a fresh shell: pin again
   [ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ hippo's plugin paths are empty in this shell; see the preflight above."; exit 1; }
   PYTHONPATH="${CLAUDE_PLUGIN_ROOT}" "${CLAUDE_PLUGIN_DATA}/venv/bin/python" -c \
     "from memory.build_index import ensure_fastembed_cache_path; ensure_fastembed_cache_path(); from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"
   ```
3. **Rebuild the index — this is a FULL re-embed, not incremental.** The index manifest records
   which model embedded it (`manifest["model"]`); `build_index`'s cache-reuse check only trusts
   a prior row when `old_manifest["model"] == DEFAULT_MODEL`, so switching models makes EVERY
   existing row a cache miss — every memory gets re-embedded from scratch, once, under the new
   model. Expect this to take noticeably longer than an incremental rebuild on a large corpus:
   ```bash
   export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # a fresh shell: pin again
   [ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ hippo's plugin paths are empty in this shell; see the preflight above."; exit 1; }
   hippo build-index \
     --memory-dir <memory_dir> --index-dir <index_dir> --force
   ```

**When to use this**: your corpus (memory descriptions) is visibly written in a non-English
language, OR `/hippo:doctor` warns "corpus is N% non-Latin-alphabetic but is served by the
English default embedding model." **When NOT to**: an English (or mostly-English) corpus —
the multilingual model trades some English-specific accuracy for broad language coverage, so
switching without a real multilingual corpus is a pure downgrade. Switching back to English
later is the same procedure in reverse (rewrite `model.json` to the English id, or delete it
to fall back to the default, then `--force` rebuild again).

### Hard rules (do not violate)

- **Never run this from a hook.** Model warm is an online step; the `SessionStart` and
  `UserPromptSubmit` hooks are offline-only by contract (never download, always exit 0). This
  skill is the ONLY place network access for the model cache is allowed.
- **Never skip the hash check.** A dep bump without a re-provision leaves a venv missing a
  newly-added package, and every recall silently degrades to whatever the OLD deps support
  (e.g. dense recall breaking silently if a `fastembed` major bump changes its cache format).
- **Never write the sentinel before both the venv AND the model warm actually succeed.** A
  partial bootstrap that gets marked complete means the next real session trusts a broken
  install and gets no retry.
- If `uv` and `python3` are BOTH unavailable, fail loudly with a clear message — don't silently
  produce a broken venv path that later hooks will treat as "bootstrapped."
- If system `python3` is outside the supported window (3.9–3.13) and `uv` is ALSO unavailable,
  fail loudly with an actionable message (install `uv`, or install a supported `python3`) —
  don't let `python3 -m venv` limp forward into an opaque numpy source-build traceback.

### After bootstrap

Recall works in full hybrid (dense+BM25) mode from the next session onward. Before bootstrap,
recall already works in BM25-only mode — the plugin vendors a dependency-free BM25 scorer and
frontmatter parser (`memory/_vendor/`) so a bare `python3` with none of the pinned deps still
serves real lexical recall. Bootstrap unlocks the dense half (and the full pinned deps).

## Init — once per project

Builds the two seams a copy-isolated plugin cannot reach on its own: the consuming project's
own `.claude/memory/` corpus, and the machine-local `~/.claude/projects/<encoded>/memory`
symlink Claude Code's native memory system reads from.

### Before you start

- **Pin and guard the plugin paths first** (shared across all hippo skills — step 4 expands them):
  ```bash
  export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # Claude Code fills both in when it loads this skill
  [ -n "${CLAUDE_PLUGIN_DATA:-}" ] && [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] || { echo "✘ hippo's plugin paths are empty in this shell. Claude Code fills them into this skill's text when it loads the skill (the Bash tool does not inherit them), so run these blocks from the loaded /hippo:setup skill, not from a copy of its SKILL.md. If the loaded skill stops here too, take the MCP-tool route in 'Surface routing' above."; exit 1; }
  ```

  Each Bash call is a fresh shell, so nothing set here reaches the next call. Every block below
  opens by pinning what it needs; an inline `hippo …` command runs
  as written (`hippo` is on the Bash tool's PATH and finds its own venv).
- **If `.claude/memory/MEMORY.md` already exists (ONB-5), this is an EXISTING CORPUS, not a
  fresh project** — the flagship case here is a teammate cloning the repo, or opening a new
  `git worktree` of a repo already using hippo: the corpus is already in git, but THIS machine
  has never had its symlink or index built (a linked worktree itself needs neither — SHP-7
  resolves it to the main checkout, whose symlink and index are the ones that count). Do **NOT** hard-stop and do **NOT** touch any existing memory file. Instead, **skip
  steps 1-2b** (starter-pack selection, `MEMORY.md` skeleton, format marker — there is
  nothing to seed, and stamping a format marker onto an unmigrated corpus is doctor's call,
  not init's) and run
  **step 2c plus the machine-local setup, steps 3-5** (including 4b): `CONVENTIONS.md`
  backfill, symlink, index build, trust-mark, `.gitignore` check. Step 2c is deliberately NOT
  grouped with the skipped 1-2b range — see step 2c itself for why. Re-running init against
  an existing corpus is the user explicitly reviewing it, so 4b marks it trusted (SEC-1) even
  on this path. This
  makes re-running `/hippo:setup` on an already-initialized project safe and useful — it is how
  `/hippo:doctor` tells a user to repair a missing/broken symlink, instead of routing them back
  to a hard stop.
- Check `${CLAUDE_PROJECT_DIR}` (or `git rev-parse --show-toplevel`) resolves to a real git
  repo (`git rev-parse --show-toplevel` exits non-zero when it isn't). Do **NOT** halt when it
  isn't one — seed the corpus anyway (everything in steps 1-4 below works without git: the
  skeleton MEMORY.md, starter packs, index build, and cross-machine symlink). Skip step 5
  (the `.gitignore` patch — there's no git to ignore anything from) and replace step 6's
  commit nudge with the degradation notice (SHP-4): git init later still finds these files on
  disk and `git add`s them fine, so there was never a real reason to refuse.

### Scenarios this skill handles

- **Fresh project, no corpus yet.** Runs all steps 1-6 below.
- **Teammate clones the repo.** `.claude/memory/MEMORY.md` already exists (it's in git), but
  this machine's `~/.claude/projects/<encoded>/memory` symlink and `.claude/.memory-index/`
  don't exist yet (both are gitignored, so cloning never brings them along). Preflight detects
  the existing corpus and runs steps 2c-5 only.
- **New worktree of an existing repo.** Needs NOTHING (SHP-7, v1.34.0): a session launched in
  a linked `git worktree` resolves the MAIN checkout's corpus, symlink, index, telemetry, and
  capture queue — the worktree's own git-checked-out `.claude/memory/` is the branch's
  committed snapshot and is never read. Running init from a worktree therefore acts on the
  main checkout (`resolve_dirs` already points there); it will report the existing corpus and
  take the steps 2c-5 path against the MAIN tree. Do NOT create a symlink or index under the
  worktree's own `~/.claude/projects/<encoded>` entry — Claude Code keys a worktree session's
  native memory on the main checkout too. Only a main checkout WITHOUT a corpus (a branch-only
  corpus) keeps the worktree-local one.
- **Second machine, same repo.** Identical shape to the teammate-clone case — the corpus
  travels via git, the symlink and index are machine-local and never do.

### What this does, in order

Steps 1-2b are SKIPPED entirely on an existing corpus (see preflight) — jump straight to step
2c; steps 2c, 3, 4, 4b (trust-mark), and 5 all still run.

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
   editable `<FILL-ME>` template; step 2a offers to fill it interactively so the newcomer
   isn't left staring at a wall of placeholders (skip 2a only if they say they'll do it later).
2a. **Optionally fill `user_role.md` interactively — from the USER's own words (ONB-10).**
   Fresh-corpus path only (where core was just seeded and `user_role.md` is still the
   `<FILL-ME>` template). The single unavoidable manual step is filling this file, and the
   shipped template is a deliberately thorough `<FILL-ME>` scaffold — great for depth, but a lot
   to face on a first run. If `AskUserQuestion` is available, OFFER to fill it now instead of
   handing them the raw template: ask a few short questions and write **only their verbatim
   answers** into the file. Reasonable questions (keep it to ~3–4, all optional):
   - their name;
   - their role and what they're building (one line);
   - solo, or a small team (and if a team, roughly how many / who Claude is among);
   - how they want Claude to collaborate (technical depth, decision authority) — optional.

   Then rewrite `user_role.md`, replacing the matching `<FILL-ME>` spans with the user's answers
   **verbatim** and deleting the scaffolding/checkboxes for the branch they chose (e.g. the
   solo-vs-team options). Leave any span the user didn't answer as `<FILL-ME>` — `/hippo:doctor`
   flags what's still unfilled.

   > **HARD RULE — user-supplied content ONLY.** Never infer, synthesize, guess, or "helpfully"
   > draft the user's role from the repo, the git history, or the conversation. Every word in
   > `user_role.md` must be text the user gave you **in answer to these questions** — not quoted
   > from earlier chat, not paraphrased from what you've observed. If they decline the offer, or
   > `AskUserQuestion` isn't available, do NOT write the file — fall back to the step-2 reminder
   > to edit the template themselves. A fabricated user identity is worse than an unfilled one.
2b. **Stamp the corpus format marker** — `.claude/memory/.format`, committed WITH the corpus
   (it describes the corpus's on-disk conventions; it is NOT a derived cache, so it is never
   gitignored):
   ```bash
   printf '{"corpus_format": 5}\n' > .claude/memory/.format
   ```
   The number is `memory.provenance.CORPUS_FORMAT_VERSION` (a parity test pins this snippet
   to that constant so the two can't drift). Fresh-corpus path ONLY, like the rest of steps
   1-2: a corpus with NO marker already reads as format 1 (every pre-marker corpus), and an
   EXISTING corpus must never be stamped with a newer format it hasn't been migrated to —
   `/hippo:doctor`'s format check owns that comparison (COR-7).
2c. **Seed `CONVENTIONS.md`** (DOC-6) — copy `${CLAUDE_PLUGIN_ROOT}/assets/CONVENTIONS.md`
   into `.claude/memory/CONVENTIONS.md` verbatim, skipping (never overwriting) if the
   destination already exists:
   ```bash
   [ -f .claude/memory/CONVENTIONS.md ] || cp "${CLAUDE_PLUGIN_ROOT}/assets/CONVENTIONS.md" .claude/memory/CONVENTIONS.md
   ```
   Unlike steps 1-2b, this step is **NOT** fresh-corpus-only — it also runs on the
   existing-corpus preflight path: a corpus created before this file existed still needs it,
   and the idempotent skip-if-present check makes re-running it on an already-seeded corpus a
   no-op, exactly like the symlink/index steps that follow. `CONVENTIONS.md` documents the
   corpus's own frontmatter schema, type taxonomy, floor rule, evidence-block convention, and
   link conventions where memories actually get written — not only in the plugin bundle. It
   is deliberately NOT a memory itself: `memory.provenance._is_memory_filename` excludes it
   from every corpus-membership scan (indexing, floor lint, staleness, archive) the same
   canonical way `MEMORY.md` is already excluded, so it is never indexed or recalled.
3. **Create the cross-machine symlink**. The encoding is the harness's actual rule (SHP-5:
   every non-alphanumeric character becomes a literal `-`, one-for-one, no collapsing, no
   stripping), and the create-or-confirm logic itself is ONE tested Python helper
   (`memory.provenance.create_project_symlink`, ONB-5) — never hand-rolled `ln -s` in bash:
   ```bash
   export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # a fresh shell: pin again
   REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
   . "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
   hippo_resolve_py
   hippo_note_usage skill setup init  # OBS-2: count this skill's use (one spool line, no Python)
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
4. **Build the index**: `hippo build-index --memory-dir .claude/memory --index-dir
   .claude/.memory-index` — run it in the same Bash call as step 3's pin line, so `hippo`
   uses the plugin's venv (it falls back to bare `python3` if bootstrap hasn't run yet — a
   BM25-only index still builds and works).
4b. **Mark this corpus TRUSTED (SEC-1) + register it for cross-project recall (RCH-4).**
   Recall is gated: until this machine's user trusts a corpus, recall injects nothing from it
   (a cloned repo's memories are otherwise an unreviewed prompt-injection channel). Running
   `/hippo:setup` here IS the user's explicit review — whether they just created the corpus
   (steps 1-2) or re-ran init against an existing one (ONB-5) — so mark it trusted now, and
   register it in the machine-local project registry so `/hippo:recall --all-projects` can
   find it from other projects (registration is a LIST, not a grant — every registered corpus
   is still trust-gated per-source at query time). The block resolves its own `$PY` + `REPO_ROOT`:
   ```bash
   export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # a fresh shell: pin again
   . "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"; hippo_resolve_py
   REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
   "$PY" -c \
     "import sys, json; from memory.trust import mark_trusted; \
      from memory.registry import register_project; \
      print(json.dumps({'trusted': mark_trusted(sys.argv[1], memory_dir=sys.argv[1] + '/.claude/memory', origin='init'), \
                        'registered': register_project(sys.argv[1], sys.argv[1] + '/.claude/memory')}))" \
     "$REPO_ROOT"
   ```
   `memory_dir` stamps the SEC-6 content FINGERPRINT (the consent-time per-file baseline —
   recall withholds files that later drift from it until re-review) and `origin='init'`
   records that this trust came from the user creating/owning the corpus, not from
   reviewing a foreign one (SEC-7's provenance banner keys on that distinction). Both
   markers live machine-local under `~/.claude/` (`hippo-trust.json` /
   `hippo-projects.json` — OUTSIDE the project, so a foreign repo can't commit its own "trust
   me" or self-register). A `false` on either means that registry write failed — report it
   (recall stays gated / the project stays unlisted until it succeeds), don't pretend
   otherwise.
5. **Patch `.gitignore`** — SKIP entirely when not a git repo (there's nothing for git to
   ignore yet; a future `git init` + this same nudge in step 6, once repeated after init, is
   how it gets patched). In a git repo, append `.claude/.memory-index/`,
   `.claude/.memory-telemetry/`, and `.claude/memory.local/` if not already present (the first
   two are derived, rebuildable caches; `memory.local/` is the private tier from step 5b — never
   commit any of them). Do NOT create `.gitignore` from scratch if the project doesn't have one
   without asking first — a repo with zero `.gitignore` may be intentional (e.g. a throwaway test repo).
5b. **Create the private memory tier (TEA-3)** — git repo only. `.claude/memory.local/` is a
   gitignored sibling of `.claude/memory/` for memories you want recall over on THIS clone but
   never published to teammates (a local scratchpad, a machine-specific pointer, a personal
   note). Create it and make it self-ignoring so it can never be committed even without the
   step-5 patch (SEC-3 pattern):
   ```bash
   export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # a fresh shell: pin again
   . "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"; hippo_resolve_py
   "$PY" -c \
     "import sys; from memory.provenance import ensure_self_ignoring_dir; \
      ensure_self_ignoring_dir(sys.argv[1])" \
     ".claude/memory.local"
   ```
   Write to it with `/hippo:new --tier private`. It is recalled locally alongside the project
   corpus (labelled "private memory") and delivered on the floor by the SessionStart
   portable-floor producer; a teammate who lacks the dir simply sees nothing from it. On an
   existing corpus (ONB-5) this runs too — it is idempotent (never overwrites).
### Memories can reference each other

`.claude/memory/` files support `[[wikilinks]]` — an outbound `[[some-other-memory]]` in a
memory's body is a real edge the recall engine's 1-hop graph expansion uses to surface a
closely-related memory even when the query itself didn't match it directly. `/hippo:new`
suggests these automatically at write time (a "Related: [[...]]" line, curated by the agent —
GRA-3); on a corpus that's grown past a handful of memories with zero links so far,
`/hippo:doctor` also surfaces a one-time hint. There is nothing to do here at init time — this
is purely FYI so a new project's memories don't accidentally stay isolated from each other for
months the way a hand-authored corpus without this feature would.

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

   Then — on EVERY path (fresh, existing, git or not), after the `user_role.md` warning if any
   — close the report with the ONB-9 **try-it-now nudge** so the user reaches an *observable*
   first recall instead of a finished-but-silent setup. Name the exact next move, e.g.:

   > ▶ **Try it now** — once `user_role.md` has your real role, ask me *"what do you remember
   > about my role?"* (or run `/hippo:recall "my role"`) and watch hippo surface it inline.
   > That returned memory is the whole point of the setup you just ran.

   Recall is the payoff the entire init exists for; never end on the setup report alone.

### Hard rules

- **Never write outside `.claude/memory/`, `.claude/memory.local/`, `.claude/.memory-index/`,
  `.claude/.memory-telemetry/`, `.gitignore`, and the one symlink.** No other files, no other
  directories.
- **Never overwrite an existing memory file.** If a name collision occurs (unlikely for a fresh
  project, but check), skip that one file and report it rather than silently clobbering.
- **Never auto-commit.** The nudge in step 6 is the end of this skill's responsibility.
- Re-running on an already-initialized project (ONB-5) is safe and idempotent, NOT a hard
  stop: it skips seeding (steps 1-2, memory files untouched) and repeats only the
  machine-local setup (symlink, index, `.gitignore` check) — steps 3-5 are naturally
  idempotent (an already-correct symlink is a no-op, a fresh `build_index` call is
  content-hashed so an unchanged corpus re-embeds nothing, an already-patched `.gitignore` is
  left alone). This skill still does not have a corpus-editing "update" mode — detecting
  content DRIFT (stale memories, broken links) is the job of `/hippo:doctor`'s content audit and of `/hippo:tend`, not
  this one's.
