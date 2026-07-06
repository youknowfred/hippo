---
description: Create a new, recall-ready memory file the right way (correct frontmatter, backfilled citation provenance, refreshed index, and a floor pointer if applicable). Use whenever the agent decides to save something to memory — a user preference, a corrected mistake, a project fact, or an external reference — instead of hand-writing a markdown file. Triggers include "remember this", "save this to memory", "/hippo:new".
---

# /hippo:new — create a memory right-by-construction

Never hand-write a memory file directly — `memory.new_memory` does five things atomically that
are easy to get wrong by hand: correct frontmatter schema, citation-provenance backfill
(`cited_paths` / `source_commit`, so staleness detection works from day one), an index refresh
(so it's recallable in THIS session, not just the next one), and — for `user`/`feedback` types
only — a floor pointer appended to `MEMORY.md` under the right section.

## Preflight (shared across all hippo skills)

```bash
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ CLAUDE_PLUGIN_DATA is unset/empty — this Claude Code version is too old for hippo's self-provisioning. Update Claude Code, or export CLAUDE_PLUGIN_DATA to a writable dir (e.g. ~/.claude/hippo-data) and re-run."; exit 1; }
```

## Usage

```bash
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
hippo_resolve_py
"$PY" -m memory.new_memory \
  <name> "<one-line description — this is the recall hook, be specific>" \
  --type {user|feedback|project|reference} \
  --body "<full memory body — the WHY, not just the WHAT>" \
  [--title "<floor link text>"] [--hook "<floor trailing note>"] \
  [--memory-dir <path, defaults to resolve_dirs()>]
```

- `name` — kebab/snake slug, also becomes the filename stem (`<name>.md`).
- `description` — the single most important field: it's what hybrid recall matches queries
  against. Write it the way a future prompt would actually be phrased, not a dry summary.
- `--type`:
  - `user` — a fact about the operator's role, responsibilities, or perspective.
  - `feedback` — a correction or confirmed-good approach the agent should remember doing
    (or not doing) again. Always include *why* in the body so future edge-case judgment is
    possible, not just a bare rule.
  - `project` — ongoing work state, decisions, non-obvious constraints discovered in code.
    NOT added to the floor — recalled on demand only.
  - `reference` — a pointer to an external system (a tracker, a dashboard, a channel) plus
    what it's for.
- `--title` / `--hook` are floor-pointer cosmetics for `user`/`feedback` only — omit for
  `project`/`reference` (they're recalled on demand, never floor-linked).

## What NOT to save (skip even if it seems tempting)

- Anything directly re-derivable from reading the current code or `git log`/`git blame`.
- Transient state relevant only to the current session.
- Anything already covered by this project's own docs/README.
- A duplicate of an existing memory — search/recall first (`/hippo:doctor` or a direct
  `memory.recall` call) before creating a new one on the same topic; update the existing file
  instead if it's just stale, not wrong.

## After creating

The tool refreshes the index automatically — the new memory is recallable in the SAME session,
not just future ones. It does NOT commit anything; that's the user's call, same as
`/hippo:init`'s nudge-not-commit policy.
