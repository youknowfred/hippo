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
  [--links name-a,name-b | --no-links] \
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
- `--links a,b,c` — explicit related-memory names, comma-separated. OVERRIDES the automatic
  discovery below entirely (no `recall()` call happens at all — your list wins verbatim).
- `--no-links` — suppress the Related line entirely (no discovery, no `--links`).

## Related: [[...]] — link creation at write time (GRA-3)

Unless suppressed, `new_memory` runs an in-process `recall()` against the **existing** corpus
(BM25-only, never blocks, never raises, silently skipped on an empty/unbuilt corpus) using the
new memory's own name + description as the query, and appends a final body line:

```
Related: [[some-existing-memory]], [[another-existing-memory]]
```

**This is a suggestion, not a fact — CURATE it before it lands.** The tool has no judgment
about whether a BM25-similar memory is actually a meaningful relationship; you do. After the
file is written:

- **Keep** a suggestion that's genuinely related.
- **Trim** the list down (or drop the whole line) if a hit is superficially similar but
  substantively unrelated — a shared word is not a shared concept.
- **Replace/add** — if you know of a better-related memory that recall missed (different
  wording, no shared vocabulary), edit the line yourself; `[[wikilink]]` targets resolve by
  filename stem (see `memory.links`).

Do not blindly accept the suggested line as final. This is the single point where the graph
gains an edge at all on a fresh project — a snap-in install starts at zero wikilinks, and this
is what seeds the first ones — so a lazily-accepted, actually-unrelated link is worse than no
link (it pollutes 1-hop graph expansion at recall time for everyone downstream).

Only `user`/`feedback`/`project`/`reference` memories written via THIS tool ever gain a
Related line — it is never retrofitted onto an existing memory by any automated process (see
`/hippo:audit`'s link-densification pass for the agent-gated equivalent on the existing corpus).

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
