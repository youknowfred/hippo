---
description: Create a new, recall-ready memory file the right way (correct frontmatter, backfilled citation provenance, refreshed index, and a floor pointer if applicable). Use whenever the agent decides to save something to memory — a user preference, a corrected mistake, a project fact, or an external reference — instead of hand-writing a markdown file. Triggers include "remember this", "save this to memory", "/hippo:new".
---

# /hippo:new — create a memory right-by-construction

Never hand-write a memory file directly — `memory.new_memory` does six things atomically that
are easy to get wrong by hand: correct frontmatter schema, a near-duplicate/conflict check
against the existing corpus (warn-only — see the decision flow below), citation-provenance
backfill (`cited_paths` / `source_commit`, so staleness detection works from day one), an
index refresh (so it's recallable in THIS session, not just the next one), and — for
`user`/`feedback` types only — a floor pointer inserted into `MEMORY.md` at its sorted
position within the right section (lexicographic by memory name, not always the section
tail — this is what keeps two teammates' concurrent floor writes from merge-conflicting on
the same line), with the outcome reported explicitly (never a silent no-op — see the
floor-outcome section below).

## Preflight (shared across all hippo skills)

```bash
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ CLAUDE_PLUGIN_DATA is unset/empty — this Claude Code version is too old for hippo's self-provisioning. Update Claude Code, or export CLAUDE_PLUGIN_DATA to a writable dir (e.g. ~/.claude/hippo-data) and re-run."; exit 1; }
```

## Usage

```bash
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
hippo_resolve_py
```

CLI synopsis (`<required>` / `{choice-a|choice-b}` / `[optional]` — standard usage notation,
not literal shell; fill in the placeholders before running):

```
"$PY" -m memory.new_memory \
  <name> "<one-line description — this is the recall hook, be specific>" \
  --type {user|feedback|project|reference} \
  [--tier {project|user}] \
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
- `--tier` (TEA-1) — where the memory lives:
  - `project` (default) — the git-native in-repo corpus teammates share on clone.
  - `user` — the **machine-local user tier** (`~/.claude/hippo-memory`, or
    `HIPPO_USER_MEMORY_DIR`). It is recalled ALONGSIDE every project's corpus, so a
    person-scoped lesson learned here propagates to all your projects — but it is NEVER
    committed to any repo, and its floor pointer lands in the user tier's OWN `MEMORY.md`,
    never the shared project one. Use it for `user`/`feedback` memories that are about YOU,
    not this codebase (e.g. a personal workflow, a cross-project preference). A fused hit is
    labelled `(user memory)` in the injected block and `user tier` in `/hippo:recall`.
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

## Near-duplicate / conflict check (LIF-2) — the tool reports, YOU decide

At creation the tool scores the new memory's name+description against the **existing** index
(dense cosine when the dense index is warm, normalized BM25 otherwise; each threshold is
calibrated separately, `HIPPO_DUP_THRESHOLD` overrides). Existing memories above the
threshold print as a warning block and ride out on the result's `neighbors` list:

```
warning : 1 existing memory looks near-duplicate/conflicting:
  • old-memory-name (similarity 0.91) — its one-line description
  decide  : add (keep both) / update-existing / supersede / skip — see /hippo:new
```

**Creation is never blocked** — the file is already written when the warning prints. The
warning routes a decision to YOU (Mem0's ADD/UPDATE/DELETE/NOOP pattern; every outcome is a
reviewable, per-item git diff — never a bulk sweep). **Read the flagged neighbor(s) first**
— similarity is vocabulary overlap, not judgment — then pick exactly one:

- **add** — the two are genuinely distinct (similar vocabulary, different fact). Keep both
  as-is; optionally add a `[[wikilink]]` between them if they are meaningfully related.
- **update-existing** — same fact, and the EXISTING memory is the right home (it's just
  stale or incomplete). Fold the new content into the existing memory's body/description,
  then delete the just-created file (it is uncommitted — plus the floor pointer line the
  tool added to `MEMORY.md`, if the type was `user`/`feedback`; `git diff` shows it) and
  re-run `"$PY" -m memory.build_index` so the index drops the deleted entry.
- **supersede** — the new memory REPLACES the flagged one's claim (old asserts X, reality
  is now not-X). Keep the new file and record the typed edge + demotion verdict in one
  per-item step:

  ```bash
  "$PY" -m memory.reconsolidate --reverify "<old-name>" --outcome demote --superseded-by "<new-name>"
  "$PY" -m memory.build_index   # refresh links.json so the demotion is live THIS session
  ```

  This appends `supersedes: ["<old-name>"]` to the NEW memory's frontmatter (the GRA-4
  edge) and logs the verdict; recall then demotes the loser automatically (halved rank,
  `[superseded by <new-name>]` annotation) — the old file stays in the corpus as history
  and can be archived later via `/hippo:audit` once it ages out.
- **skip** — the flagged memory already covers it and needs no update (the new file should
  not exist). Delete the just-created file (+ its floor pointer, as in update-existing)
  and re-run `"$PY" -m memory.build_index`.

When the check could not run at all, the result carries a machine-readable
`note` (e.g. `duplicate check skipped: no index` — a first-ever memory, or a never-indexed
corpus with `--links`/`--no-links`). No warning ≠ no duplicates in that case — apply the
search-first judgment below yourself.

## Floor-pointer outcome (LIF-5) — read the `floor` line, SURFACE anything unusual

For `user`/`feedback` types the always-load pointer append used to silently no-op when
`MEMORY.md` was missing or a section header had been hand-renamed — the memory quietly lost
its floor presence. Now the outcome is an explicit result field
(`floor: {status, reason}`) and a `floor   :` CLI line. **Never absorb a non-`appended`
outcome silently — report it to the user and act on it:**

- `appended` — the normal case: the pointer landed at its deterministic sorted position
  (lexicographic by memory name) within the type's canonical section — not necessarily the
  end (TEA-4). Nothing to surface.
- `created-section — section not found: ## <Header> …` — `MEMORY.md` exists but the
  canonical section header was renamed or deleted (the floor drifted from
  `assets/MEMORY.skeleton.md`). Rather than dropping the pointer, the tool re-created the
  canonical section at the END of `MEMORY.md` (skeleton format) with the new pointer as its
  first entry. Tell the user, then reconcile the drift yourself — per-item, in the visible
  diff: if the old section still exists under a renamed header, fold its pointers into the
  re-created canonical section and remove the stray header (the SessionStart floor lint
  flags any leftovers).
- `skipped — MEMORY.md missing …` — the floor file itself does not exist, so the pointer was
  **not recorded anywhere** (the memory file + index are fine). The tool never fabricates
  `MEMORY.md` — floor creation is `/hippo:init`'s job (skeleton + starter packs). Run
  `/hippo:init`, then add the pointer line to the canonical section by hand
  (`- [Title](name.md) — hook`, the skeleton's pointer style). Do NOT re-run this tool for
  that — it refuses to overwrite the already-created memory file.
- `skipped — pointer already present` — idempotence: the floor already links `<name>.md`;
  nothing was duplicated. Worth a one-line mention only if you didn't expect it.
- `skipped — type '…' is never floor-linked` — `project`/`reference`; recalled on demand by
  design, not an error.

## What NOT to save (skip even if it seems tempting)

- Anything directly re-derivable from reading the current code or `git log`/`git blame`.
- Transient state relevant only to the current session.
- Anything already covered by this project's own docs/README.
- A duplicate of an existing memory — the write-time check above catches near-dupes when an
  index exists, but it is a similarity heuristic, not judgment: still search/recall first
  (`/hippo:doctor` or a direct `memory.recall` call) when you suspect the topic is covered;
  update the existing file instead if it's just stale, not wrong.

## After creating

The tool refreshes the index automatically — the new memory is recallable in the SAME session,
not just future ones. It does NOT commit anything; that's the user's call, same as
`/hippo:init`'s nudge-not-commit policy.
