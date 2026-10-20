# Upgrading hippo

hippo upgrades are **forward-only** (marketplace = latest-only; see [STABILITY.md](STABILITY.md))
and, where your committed data is involved, **agent-gated and per-item** — hippo never rewrites your
corpus autonomously. `/hippo:doctor` detects when something is behind and names the exact next step.
There are three kinds of "upgrade", in increasing order of involvement.

## 1. Update the plugin (usually nothing else)

Update via the marketplace (`/plugin`). Most releases need nothing more. When a release changes the
Python dependencies, the CHANGELOG entry says **re-bootstrap: yes** — run `/hippo:bootstrap` again to
rebuild the venv. `/hippo:doctor`'s `plugin_version` / `bootstrap` checks flag a stale bootstrap.

### v1.42.6: purged memories in the backups, and the worktree copies

**If you purged a memory while backups existed.** A pre-migration backup is a full copy of the
corpus. Before v1.42.6 the purge procedure (*Purging a memory* in `plugin/memory/README.md`)
did not cover the backups, so a memory purged by it, a leaked secret included, is still in every
backup taken before the purge. Run the procedure's step 5 for that memory: a `find` lists every
copy, then delete `<name>.md` and its `MEMORY.md` pointer line from each backup, or delete a
spent backup outright.

**doctor's `snapshots` line is longer.** It counts the backups in `.hippo/snapshots/` even when
some are still in `.claude/`, and it counts the copies of old `.claude/` backups that
desktop-app worktrees carry, with a `find` that lists them. It does not delete them or warn about
them. When it names a backup that exists only in worktrees, move that copy into
`.hippo/snapshots/` before you retire those worktrees, or it goes with them.

### v1.42.5: backups move into `.hippo/snapshots/`

v1.42.3 put pre-migration backups in `.hippo-snapshots/` at the project root, and v1.42.4 put
the index, telemetry and pending queue in `.hippo/`. New backups now go to `.hippo/snapshots/`,
so a project has one hippo dir. Where the v1.42.3 notes below say `.hippo-snapshots/`, read
`.hippo/snapshots/`.

**Nothing to do in most projects.** Only a project where you took a snapshot on v1.42.3 or
v1.42.4 has a `.hippo-snapshots/`. hippo leaves it where it is: a backup is the only undo a
gitignored corpus has, and that dir is outside `.claude/`, so worktrees do not carry it.
`/hippo:doctor`'s `snapshots` line names it and prints a command that moves it. When
`.hippo/snapshots/` does not exist yet, that is one rename. Otherwise it moves each backup the
new dir lacks, then deletes the old dir once only its `.gitignore` is left. A backup with the
same name in both stays in the old dir: compare the two, then delete one.

**Backups still in `.claude/`.** The same line now prints the command that moves
`.claude/memory.pre-cite*/` into `.hippo/snapshots/`. It creates the dir with its `*`
`.gitignore` first. Before, the line said to move them into a dir that usually did not exist.

**`/hippo:remove` keeps the backups.** It deletes `.hippo/index`, `.hippo/telemetry` and
`.hippo/pending` by name and lists each backup with its size, for you to delete yourself.

### v1.42.4: the index, telemetry and pending queue move out of `.claude/`

hippo kept three derived, gitignored dirs beside the corpus: `.claude/.memory-index`,
`.claude/.memory-telemetry` and `.claude/.memory-pending`. The Claude desktop app copies a
checkout's whole `.claude/` directory, ignored files included, into every worktree it creates,
so each worktree carried all three, about 39 MB on a large corpus. Since v1.34.0 a worktree
session reads the main checkout's dirs, so nothing ever read those copies. The dirs now live in
`.hippo/` at the project root: `.hippo/index`, `.hippo/telemetry`, `.hippo/pending`. `.hippo/`
ignores itself, and `/hippo:setup` adds `.hippo/` to `.gitignore`.

**Nothing to do in most projects.** The first session on v1.42.4 moves the three dirs with one
rename each; until then hippo reads them where they are. Sessions that were already open keep
running the hippo they started with and keep writing the old paths. Each new session moves
whatever they wrote: ledger lines are appended, new seeds and files are moved, and the old index
is dropped (it is rebuilt from the corpus). An entry the new dir already has is never
overwritten; `/hippo:doctor`'s `derived dirs:` line names it, and you compare and delete.

**If you installed a scheduled `hippo sleep`:** its command ends in
`>> <repo>/.claude/.memory-telemetry/sleep.log`, and the shell needs that dir to exist. When the
moved dir held a `sleep.log`, hippo leaves `.claude/.memory-telemetry/` behind as an empty stub
with a `MOVED` note, and folds the log into `.hippo/telemetry/sleep.log` at each session start.
Reinstall the schedule from `hippo sleep --print-schedule` (it now logs to `.hippo/telemetry/`),
then delete the stub. If you delete the stub first, `hippo census` and doctor's machine-state
line report the schedule as `stale-path`.

**Reclaiming the copies existing worktrees carry.** Worktrees made before the move still hold
their own `.claude/.memory-*` copies. Run `/hippo:doctor` in the main checkout: its `derived dirs:`
line totals them and prints a `find <repo>/.claude/worktrees -mindepth 3 -maxdepth 3 -type d
-path '*/.claude/.memory-*' -exec rm -rf {} +` that deletes those dirs and nothing else. On the
repo that filled the disk, that was 50 worktrees and 1.66 GB.

`HIPPO_INDEX_DIR`, `HIPPO_TELEMETRY_DIR` and `HIPPO_PENDING_DIR` still override each location,
and hippo leaves an overridden dir where it is. A corpus that is not `<project>/.claude/memory`
(a `HIPPO_MEMORY_DIR` elsewhere, the user tier in `~/.claude/hippo-memory`) keeps its dirs beside
itself, as before.

### v1.42.3: pre-migration backups move out of `.claude/`

`hippo provenance --snapshot <label>` and the `tend` / `rederive` snapshot action used to copy the
corpus to `.claude/memory.pre-cite2-<label>/`. The Claude desktop app copies a checkout's whole
`.claude/` directory, ignored files included, into every worktree it creates, so each backup
rode into every later worktree. A new copy now goes to `.hippo-snapshots/` at the project root,
which ignores itself. A corpus that is committed and clean gets no copy: the result names the
commit that `git checkout` restores.

Backups already in `.claude/` stay where they are. `/hippo:doctor` names them on its
`snapshots` line, with their size:

- **spent** (a warning): the citation derivation is stamped and the re-derivation worklist is
  empty. Delete them, or move them into `.hippo-snapshots/` if you want to keep them.
- **kept**: the migration is unfinished, so they are still your undo. Move them into
  `.hippo-snapshots/` to keep them without new worktrees carrying them.

If you restore from one of the old backups, leave out the `.gitignore` inside it. It holds `*`,
and in the corpus it would hide every memory from git. (New copies carry no marker of their own.)
Worktrees made before you move them still hold their own copies. Those go away with the
worktree.

### v1.42.1: nested repos and submodules stop seeing the parent's corpus

A session started at the top of a git repo that sits inside another one (a nested checkout or a
git submodule) used to climb into the parent's `.claude/memory/` when it had none of its own. The
MCP tools and the CLI then read and wrote the parent's corpus against the nested repo's files,
while the hooks did nothing. Corpus resolution now stops at the session's own git toplevel, so a
nested repo has no corpus until it gets one. Subdirectory launches inside one repo, and linked
worktrees, resolve as before.

SessionStart and `/hippo:doctor` name the parent in one line: *this repo has no corpus of its own;
\<parent\> has one — init here, or pin HIPPO_CORPUS_ROOT to share it*. Pick one:

- **Its own corpus:** run `/hippo:setup` in the nested repo.
- **The parent's, on purpose:** set `HIPPO_CORPUS_ROOT` to the parent's directory for sessions
  in the nested repo. The hooks, the MCP tools and the CLI then all use the parent's corpus, and
  citations and trust are checked against the parent's repo.

### v1.42.0: new MCP tool names (the old ones work until v2.0)

v1.42.0 adds the v2 MCP toolset: `recall`, `new_memory`, `inspect`, `tend`, `doctor`, `setup`,
`trust`, `share`, `dream` and `review`. Every other tool name keeps working through v1.43 and is
removed in v2.0. Each still appears in the tool list with a description that names its
replacement, and each call's result carries a one-line notice. If a permission rule in your
settings names one of the old tools, add the new id beside it; `/hippo:doctor` lists any it
finds (it never edits your settings).

| Old permission id | New permission id | Call it with |
|---|---|---|
| `mcp__plugin_hippo_hippo__traverse` | `mcp__plugin_hippo_hippo__inspect` | action='traverse' |
| `mcp__plugin_hippo_hippo__why` | `mcp__plugin_hippo_hippo__inspect` | action='why' |
| `mcp__plugin_hippo_hippo__decision_history` | `mcp__plugin_hippo_hippo__inspect` | action='history' |
| `mcp__plugin_hippo_hippo__blast_radius` | `mcp__plugin_hippo_hippo__inspect` | action='blast_radius' |
| `mcp__plugin_hippo_hippo__bootstrap` | `mcp__plugin_hippo_hippo__setup` | action='bootstrap' |
| `mcp__plugin_hippo_hippo__init` | `mcp__plugin_hippo_hippo__setup` | action='init' |
| `mcp__plugin_hippo_hippo__build_index` | `mcp__plugin_hippo_hippo__setup` | action='build_index' |
| `mcp__plugin_hippo_hippo__trust_corpus` | `mcp__plugin_hippo_hippo__trust` | action='review', then 'grant' |
| `mcp__plugin_hippo_hippo__untrust` | `mcp__plugin_hippo_hippo__trust` | action='revoke' |
| `mcp__plugin_hippo_hippo__secrets_scan` | `mcp__plugin_hippo_hippo__doctor` | action='secrets_scan' |
| `mcp__plugin_hippo_hippo__audit` | `mcp__plugin_hippo_hippo__doctor` | action='audit' |
| `mcp__plugin_hippo_hippo__capture` | `mcp__plugin_hippo_hippo__tend` | kind='capture' (add_decision is action='add_decision') |
| `mcp__plugin_hippo_hippo__reconsolidate` | `mcp__plugin_hippo_hippo__tend` | kind='reverify' |
| `mcp__plugin_hippo_hippo__resolve` | `mcp__plugin_hippo_hippo__tend` | kind='contradiction' |
| `mcp__plugin_hippo_hippo__rederive` | `mcp__plugin_hippo_hippo__tend` | kind='derivation' (snapshot is action='snapshot') |
| `mcp__plugin_hippo_hippo__heal_baselines` | `mcp__plugin_hippo_hippo__tend` | kind='baseline' |
| `mcp__plugin_hippo_hippo__co_recall_proposals` | `mcp__plugin_hippo_hippo__tend` | action='link_proposals' |
| `mcp__plugin_hippo_hippo__abstention_fixtures` | `mcp__plugin_hippo_hippo__tend` | action='fixtures' |
| `mcp__plugin_hippo_hippo__interview` | `mcp__plugin_hippo_hippo__tend` | action='interview' |
| `mcp__plugin_hippo_hippo__pack_extract` | `mcp__plugin_hippo_hippo__share` | action='pack_extract' |
| `mcp__plugin_hippo_hippo__pack_install_plan` | `mcp__plugin_hippo_hippo__share` | action='pack_install_plan' |
| `mcp__plugin_hippo_hippo__pack_install_item` | `mcp__plugin_hippo_hippo__share` | action='pack_install_item' |
| `mcp__plugin_hippo_hippo__pack_update_plan` | `mcp__plugin_hippo_hippo__share` | action='pack_update_plan' |
| `mcp__plugin_hippo_hippo__pack_update_item` | `mcp__plugin_hippo_hippo__share` | action='pack_update_item' |

### Settings moved in v1.42

Nothing breaks: every old name keeps working through v1.43, and `/hippo:doctor` lists each one it
sees with its new spelling. Move them before v2.0, when the old names stop being read:

| Old | New |
|---|---|
| `HIPPO_DISABLE_DENSE`, `HIPPO_DISABLE_JIT`, `HIPPO_DISABLE_PRESENCE`, `HIPPO_DISABLE_FLOOR_NAG`, `HIPPO_DISABLE_ABSTAIN_GATE`, `HIPPO_DISABLE_TOUCH_FASTPATH` | one list: `HIPPO_DISABLE=dense,jit,presence,floor-nag,abstain-gate,touch-fastpath` (name only what you turn off) |
| `DREAM_CONTRA_MAX_PAIRS`, `DREAM_CONTRA_MIN_COFIRE` | `HIPPO_DREAM_CONTRA_MAX_PAIRS`, `HIPPO_DREAM_CONTRA_MIN_COFIRE` |
| `volatile_paths`, `floor_lint`, `fold_digests` in `.claude/memory/.format` | the same keys in `.claude/memory/hippo.json` (commit it; `corpus_format` and `cite_derivation` stay in `.format`) |
| `HIPPO_MUTE` as your only mute list | `"mute"` in `hippo.json` for the team; `HIPPO_MUTE` still overrides it |
| `~/.claude/hippo-llm.json` `capture_triage`, `dream_contradictions`, `model` | hippo's plugin options: LLM triage of captured sessions, LLM contradiction check in dream, LLM model |
| `~/.claude/hippo-llm.json` `api_key` | hippo's LLM API key option (masked, kept in your system keychain); then delete the key from the file |

Which value wins: an environment variable, then hippo's plugin option, then `hippo.json`, then the
old location. The plugin options reach hippo's hooks and its MCP server but not commands run in a
shell, so the dream contradiction check runs through hippo's MCP `dream` tool. A scheduled
`hippo sleep` (cron or launchd) never sees them either: if your schedule relies on
`hippo-llm.json` for the check, put `HIPPO_DREAM_CONTRADICTIONS=1` and `HIPPO_LLM_API_KEY` in the
scheduled command's environment before v2.0 (`hippo sleep --print-schedule` shows where). A run
that has the check on but sees no key says so in its report.

## 2. Rebuild a derived cache (automatic)

The recall index carries its own `schema_version` (currently 7), separate from your corpus format.
When the plugin's schema is newer than the persisted index, **every load path treats the stale index
as absent** and the next SessionStart refresh does one full rebuild — no action needed. The same is
true of the link cache, staleness cache, and telemetry ledgers: they are gitignored, rebuildable
artifacts. A `schema_version` bump is a re-index, never a corpus migration.

## 3. Migrate the corpus format (agent-gated, per-item)

This is the only upgrade that touches your **committed markdown**. The corpus format is stamped in
`.claude/memory/.format` (`{"corpus_format": N}`, currently **5**) and describes the on-disk
conventions your memory files follow. It is committed with the corpus (not a cache), and it only ever
increases. `read_corpus_format()` reads it (a corpus with no marker reads as format 1);
`write_corpus_format()` stamps it.

When you open an older corpus with a newer plugin, `/hippo:doctor` reports, for example:

```
⚠ corpus format is v2, this plugin writes v5 — the corpus needs a MIGRATION before newer-format
features work; hippo never migrates automatically — follow the doctor-driven path in
plugin/memory/README.md ('Corpus format versioning')
```

(That "Corpus format versioning" section in the engine reference is the canonical short form; this
file is its top-level, worked-example expansion of the same doctor-driven path.) hippo does **not**
migrate for you: a bulk autonomous rewrite of your memories would violate the
guiding invariant that corrective writes are per-item and agent-gated. Instead, the migration is a
deliberate pass you run (ask Claude to "migrate my hippo corpus to the current format") that follows
this pattern:

### Worked example — a format 2 → 3 migration (the template every migration follows)

Suppose format 3 moved a frontmatter field out of the top level and under `metadata:` (an
illustrative convention change — real bumps are itemized in the CHANGELOG). The migration is:

1. **Detect and scope.** Confirm the gap and list the files to touch:
   ```bash
   PYTHONPATH=plugin python3 -c "from memory.provenance import read_corpus_format; print(read_corpus_format('.claude/memory'))"
   ```
   This prints `2`. The files to migrate are every `*.md` under `.claude/memory/` (never `MEMORY.md`
   floor, never the gitignored caches).

2. **Edit each memory, one at a time, agent-gated.** For each file, the agent reads it, applies the
   convention change (here: move the field under `metadata:`), and leaves the body verbatim. This is
   an ordinary markdown edit — reviewable as a git diff, per item, never a blind sweep. Skip a file
   that already conforms; stop and ask if one is ambiguous.

3. **Verify before stamping.** Re-read the migrated files (or run `/hippo:doctor` /
   `/hippo:audit`) to confirm the corpus is internally consistent and nothing was dropped. The index
   will rebuild itself on the next refresh, so you don't hand-edit any cache.

4. **Stamp the new format — only after every file is migrated and verified:**
   ```bash
   PYTHONPATH=plugin python3 -c "from memory.provenance import write_corpus_format; write_corpus_format('.claude/memory', 3)"
   ```
   Then commit the whole migration (the memory edits **and** the bumped `.format` marker) as one
   reviewable commit. Because the corpus is markdown-in-git, the entire migration is a diff you can
   inspect, revert, or cherry-pick — there is no opaque migration step.

The key properties, true of every corpus migration: **doctor detects it, you drive it per item, the
body is preserved, and the `.format` stamp lands last** — so a half-finished migration never claims a
format it hasn't reached. Migrate straight to the current version (v5) by applying each intermediate
step's convention change in order, then stamping the final version once. (Every bump so far — v2
typed relations, v3 `steer`, v4 `confidence`, v5 `derives-from` — has been purely additive, so in
practice the migration is: review that no existing frontmatter already uses the new key, then stamp.)
