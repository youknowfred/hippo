---
description: Fast health check for the memory plugin's own install/environment — is it bootstrapped, is the venv healthy, is the corpus symlinked and indexed correctly. Use for "is memory working", "check memory setup", "/hippo:doctor", or when recall seems to be silently returning nothing. This is a QUICK sanity check, not a deep corpus audit — for the latter use /hippo:audit.
---

# /hippo:doctor — fast environment sanity check

A few-second diagnostic over the PLUGIN'S OWN install health — venv, model cache, symlink,
index freshness. This is deliberately NOT `/hippo:audit`: doctor answers "is the plumbing
working," audit answers "is the corpus content still trustworthy" (a much heavier, judgment-based
pass). Don't reach for audit when doctor's quick checks are what's actually being asked.

## Preflight (shared across all hippo skills)

```bash
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ CLAUDE_PLUGIN_DATA is unset/empty — this Claude Code version is too old for hippo's self-provisioning. Update Claude Code, or export CLAUDE_PLUGIN_DATA to a writable dir (e.g. ~/.claude/hippo-data) and re-run."; exit 1; }
```

## Checks, in order (stop at the first hard failure and report it — don't cascade confusing
downstream errors from a root cause already identified)

1. **Bootstrap state.** Does `${CLAUDE_PLUGIN_DATA}/.bootstrap-sentinel` exist and does its
   `requirements_hash` match the current `${CLAUDE_PLUGIN_ROOT}/requirements.txt`? Report
   "not bootstrapped — run /hippo:bootstrap" or "bootstrapped `<date>`, deps current" or
   "bootstrapped but STALE — deps changed since, run /hippo:bootstrap again."
2. **Venv health.** If bootstrapped, do all 4 deps actually import cleanly in
   `${CLAUDE_PLUGIN_DATA}/venv`? (`fastembed`, `numpy`, `yaml`, `rank_bm25`.) A missing import
   here despite a sentinel claiming success means a corrupted/partial venv — recommend deleting
   `${CLAUDE_PLUGIN_DATA}/venv` + `.bootstrap-sentinel` and re-running bootstrap, don't try to
   patch it in place.
3. **Model cache.** Does `${CLAUDE_PLUGIN_DATA}/fastembed` contain the warmed
   `bge-small-en-v1.5` model files? If bootstrapped but this is empty/missing, dense recall is
   silently degrading to BM25 — flag it explicitly. (The cache is pinned to the durable
   plugin-data dir precisely because hooks are offline by contract and can never re-warm a
   purged `$TMPDIR` cache; an empty dir here means that pin failed or the warm step never ran.)
4. **Project corpus.** Does `.claude/memory/MEMORY.md` exist in the current project? If not,
   suggest `/hippo:init`. If it exists, call
   `memory.provenance.check_project_symlink(repo_root, memory_dir)` (SHP-5) — it verifies from
   the direction Claude Code actually reads (resolves `~/.claude/projects/<encoded>/memory` and
   compares its REAL target against this project's `.claude/memory`), never by recomputing and
   trusting the formula blind. Report:
   - `ok` — symlink resolves to this project's corpus, nothing to say.
   - `missing` — no symlink yet; print the returned `repair_command`.
   - `broken` — symlink exists but points elsewhere; print the returned `repair_command`.
   - `legacy_wrong_encoding` — a symlink exists under the OLD (pre-SHP-5) buggy encoding for
     this same repo root (only `/` transliterated) instead of the harness's real one; report it
     explicitly as a legacy artifact and print the returned `repair_command` (create the
     correctly-encoded symlink, then remove the stale legacy one).
5. **Corpus resolution (monorepo subdir launches, SHP-2).** Report WHICH corpus this session's
   `memory.provenance.resolve_dirs()` actually resolved, and why — a session started from a
   package subdirectory (`claude` launched from `packages/web`) walks UP toward the git
   toplevel looking for `.claude/memory` when the subdir has none of its own, and a session
   that silently fell through can otherwise look identical to a healthy nested one. Call
   `memory.provenance.walk_up_for_memory_dir(<CLAUDE_PROJECT_DIR-or-cwd>)` (or just inspect
   `resolve_dirs()`'s return) and report one of:
   - `resolved corpus: packages/web/.claude/memory (nested; found before reaching repo root)`
   - `resolved corpus: .claude/memory at repo root (no nested corpus found in packages/web)`
   - `resolved corpus: packages/web/.claude/memory (none found anywhere above — this is the
     CLAUDE_PROJECT_DIR default; run /hippo:init here or at the repo root)`
   Flag the root-fallthrough case explicitly — it is correct behavior, not a bug, but a subdir
   session silently inheriting the root corpus is worth surfacing so the user isn't confused
   about which `.claude/memory` their edits should land in.
6. **Unfilled templates.** Run `grep -rln '<FILL-ME' .claude/memory/` — any hit means a
   template memory (usually `user_role.md`) was never filled in: its placeholder text is
   being embedded into the recall index and (for `user` types) floor-loaded every session.
   Report each file BY NAME with "edit this file, then the next SessionStart re-indexes it
   automatically"; don't edit it yourself — its content is facts about the user only they
   can supply.
7. **Index freshness.** Does `.claude/.memory-index/manifest.json` exist, and does its recorded
   memory count match the actual `.claude/memory/*.md` file count? A mismatch means the index is
   stale (a memory was added/removed since the last build) — recommend
   `memory.build_index --memory-dir .claude/memory --index-dir .claude/.memory-index`
   (SessionStart's own refresh should have caught this already; a persistent mismatch across
   sessions is itself worth flagging as a possible SessionStart hook problem).
8. **Live recall probe.** Run one real `memory.recall` call with a trivial query and confirm it
   returns without raising and within a few seconds. This is the actual end-to-end proof the
   other checks are trying to predict — always run it even if the rest all look healthy.
9. **Stale plugin name (pre-0.2.0).** If the user's installed-plugin list still shows
   `memory@hippo`, that install predates the 0.2.0 rename to `hippo` and receives no updates —
   recommend `/plugin uninstall memory@hippo` followed by `/plugin install hippo@hippo`
   (a clean break; there is no alias shim).
10. **Non-git degraded mode (SHP-4).** Run `git -C "${CLAUDE_PROJECT_DIR:-.}" rev-parse
    --show-toplevel` (or reuse `memory.provenance.git_root()`). A non-zero exit / `None` means
    this project has no git repo — report it as a LABELED DEGRADATION, not an error, and name
    exactly which subsystems are inactive and why:
    ```
    ⚠ not a git repository — running in DEGRADED mode:
      - staleness tracking: INACTIVE (no commit history to diff cited files against)
      - provenance/backfill: INACTIVE (no commits, so source_commit has no baseline to record)
      - archive: DEGRADED, not inactive — falls back to os.rename instead of git mv (COR-5),
        so archived memories are still recoverable, just not via git history
      recall, indexing, links, and floor loading are all unaffected — run `git init` and
      commit to restore the rest.
    ```
    When it IS a git repo, report `✔ git repo detected — staleness, provenance, and archive's
    git-mv path are all active.` instead; don't print the degraded block on a healthy repo.
11. **Unresolvable staleness baselines (squash-merge / shallow clone, SHP-3).** Call
    `memory.staleness.count_unresolvable_baselines(<memory_dir>, <repo_root>)` — memories whose
    `source_commit` sha is NOT reachable in this repo's history (a squash-merge default rewrites
    branch commits away; a shallow/partial clone never fetched them). These fall back to each
    memory's own stored `source_commit_time` for drift detection rather than being silently
    exempted forever, but the fallback is a weaker signal than a git-cross-checked sha, so report
    it as a LABELED degradation when nonzero: `⚠ N memories have unresolvable staleness baselines
    (source_commit sha not in history — likely squash-merge or a shallow clone); falling back to
    time-based comparison.` Silent (no line) when the count is `0`.

## Report format

One line per check: `✔`/`✘`/`⚠` + the specific finding, not a generic pass/fail. End with ONE
concrete next action if anything failed (the single most useful thing to run next), not a list
of every possible remediation.

## When NOT to use

- A deep "is my corpus content still accurate" pass — that's `/hippo:audit`.
- Routine curiosity when nothing seems wrong — SessionStart's own staleness/link-health
  producers already surface real problems for free every session; don't re-run this reflexively.
