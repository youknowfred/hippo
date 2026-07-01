---
description: Fast health check for the memory plugin's own install/environment — is it bootstrapped, is the venv healthy, is the corpus symlinked and indexed correctly. Use for "is memory working", "check memory setup", "/memory:doctor", or when recall seems to be silently returning nothing. This is a QUICK sanity check, not a deep corpus audit — for the latter use /memory:audit.
---

# /memory:doctor — fast environment sanity check

A few-second diagnostic over the PLUGIN'S OWN install health — venv, model cache, symlink,
index freshness. This is deliberately NOT `/memory:audit`: doctor answers "is the plumbing
working," audit answers "is the corpus content still trustworthy" (a much heavier, judgment-based
pass). Don't reach for audit when doctor's quick checks are what's actually being asked.

## Checks, in order (stop at the first hard failure and report it — don't cascade confusing
downstream errors from a root cause already identified)

1. **Bootstrap state.** Does `${CLAUDE_PLUGIN_DATA}/.bootstrap-sentinel` exist and does its
   `requirements_hash` match the current `${CLAUDE_PLUGIN_ROOT}/requirements.txt`? Report
   "not bootstrapped — run /memory:bootstrap" or "bootstrapped `<date>`, deps current" or
   "bootstrapped but STALE — deps changed since, run /memory:bootstrap again."
2. **Venv health.** If bootstrapped, do all 4 deps actually import cleanly in
   `${CLAUDE_PLUGIN_DATA}/venv`? (`fastembed`, `numpy`, `yaml`, `rank_bm25`.) A missing import
   here despite a sentinel claiming success means a corrupted/partial venv — recommend deleting
   `${CLAUDE_PLUGIN_DATA}/venv` + `.bootstrap-sentinel` and re-running bootstrap, don't try to
   patch it in place.
3. **Model cache.** Does `${CLAUDE_PLUGIN_DATA}/fastembed` contain the warmed
   `bge-small-en-v1.5` model files? If bootstrapped but this is empty/missing, dense recall is
   silently degrading to BM25 — flag it explicitly (this is exactly the failure mode
   [[hippo_plugin_schema_gotchas]]'s sibling durable-cache-pin fix exists to prevent).
4. **Project corpus.** Does `.claude/memory/MEMORY.md` exist in the current project? If not,
   suggest `/memory:init`. If it exists, does the `~/.claude/projects/<encoded>/memory` symlink
   resolve to it correctly (not broken, not pointing elsewhere)?
5. **Index freshness.** Does `.claude/.memory-index/manifest.json` exist, and does its recorded
   memory count match the actual `.claude/memory/*.md` file count? A mismatch means the index is
   stale (a memory was added/removed since the last build) — recommend
   `memory.build_index --memory-dir .claude/memory --index-dir .claude/.memory-index`
   (SessionStart's own refresh should have caught this already; a persistent mismatch across
   sessions is itself worth flagging as a possible SessionStart hook problem).
6. **Live recall probe.** Run one real `memory.recall` call with a trivial query and confirm it
   returns without raising and within a few seconds. This is the actual end-to-end proof the
   other 5 checks are trying to predict — always run it even if 1-5 all look healthy.

## Report format

One line per check: `✔`/`✘`/`⚠` + the specific finding, not a generic pass/fail. End with ONE
concrete next action if anything failed (the single most useful thing to run next), not a list
of every possible remediation.

## When NOT to use

- A deep "is my corpus content still accurate" pass — that's `/memory:audit`.
- Routine curiosity when nothing seems wrong — SessionStart's own staleness/link-health
  producers already surface real problems for free every session; don't re-run this reflexively.
