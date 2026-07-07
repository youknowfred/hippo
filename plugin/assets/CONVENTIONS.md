# Memory corpus conventions

This file documents the conventions the hippo memory engine actually implements — how a
memory file is shaped, what its typed fields mean, and how recall/reconsolidation reads them
— so the rules live in the corpus where memories get written, not only in the plugin bundle a
teammate may never open. `/hippo:init` seeds this file into `.claude/memory/` (idempotent —
it never overwrites an existing copy).

This is reference material, not a memory: it is deliberately excluded from indexing,
recall, and floor scanning by the same corpus-membership filter
(`memory.provenance._is_memory_filename`) that already excludes `MEMORY.md` itself, so it is
never injected into a prompt and never counted in corpus stats. Editing this copy does not
change plugin behavior — it only documents behavior implemented in `plugin/memory/*.py`; if
the two disagree, the code wins and this file is stale.

## Frontmatter schema

Every memory is a `.md` file whose frontmatter block (between the opening/closing `---`
fences) carries the fields recall and provenance depend on:

```
---
name: my-memory-slug
description: "one-line recall hook — matched against prompts, be specific"
metadata:
  type: project
  cited_paths: ["src/auth.py", "src/session.py"]
  source_commit: "a1b2c3d4e5f6..."
  source_commit_time: 1751500000
  last_verified: "2026-06-01T12:00:00+00:00"
  invalid_after: "2026-06-15T09:00:00+00:00"
  supersedes: ["old-memory-slug"]
  contradicts: ["some-other-slug"]
  refines: ["parent-topic-slug"]
---
```

- **`name`** / **`description`** — the two fields every writer renders top-level (never
  nested). `description` is what hybrid recall matches queries against — write it the way a
  future prompt would actually be phrased.
- **`metadata.type`** — one of `user` / `feedback` / `project` / `reference` (see the type
  taxonomy below). Required by `memory.new_memory` (`VALID_TYPES`); a memory with no type
  still recalls, but never gets a floor pointer and never routes reconsolidation's type-aware
  behavior.
- **`metadata.cited_paths`** — the repo-relative code/config files this memory's BODY cites
  (`path:line` tokens, `.md` excluded — memory-to-memory references are `[[wikilinks]]`,
  never staleness citations). Auto-derived by `memory.provenance` at write time; the set a
  memory watches for drift. An empty list makes a memory staleness-EXEMPT — nothing to watch.
- **`metadata.source_commit`** / **`metadata.source_commit_time`** — the staleness baseline:
  the commit (and its epoch) the cited paths were last known to match. Re-baselined to HEAD
  only by a human-confirmed re-verification (`reconsolidate --reverify ... --outcome
  graduate|fix`), never by a mechanical refresh — a `--refresh` re-derives `cited_paths` but
  always PRESERVES the existing baseline, so it can never silently clear a real staleness flag.
- **`metadata.last_verified`** — write-once: the timestamp of the FIRST human confirmation
  this memory ever received. Supplementary audit provenance only — the signal that actually
  clears a staleness flag is `source_commit`, not this field.
- **`metadata.invalid_after`** — soft-invalidation: present only after a confirmed-wrong
  verdict (`reconsolidate --reverify ... --outcome demote`, or a direct `--invalidate`).
  Terminal — nothing re-baselines it except a fresh, explicit re-verification. Its presence
  alone halves the memory's recall rank immediately (see "demote/snooze lifecycle" below).
- **`metadata.supersedes` / `metadata.contradicts` / `metadata.refines`** — typed relations
  (see the next section). Lists of memory names/stems.

Both schema shapes exist in real corpora and every reader tolerates both: the `metadata:`
nested style above (what every current writer renders), and a flat top-level style (`type:`,
`cited_paths:`, etc. as siblings of `name:`/`description:`, no `metadata:` block) from older
files. A reader always checks top-level FIRST, then falls back to `metadata.<key>` — never
the other way — so a value present in either place is honored.

## Typed relations: `supersedes` / `contradicts` / `refines`

Three typed edges, additive frontmatter lists, read top-level or under `metadata:` (same
fallback as `cited_paths`). Unlike `[[wikilinks]]` (untyped — "see also"), each typed
relation carries specific recall consequences:

| relation      | meaning                              | recall effect on the TARGET                                   |
|---------------|---------------------------------------|-----------------------------------------------------------------|
| `supersedes`  | this memory REPLACES the target's claim | target's score halved; pointer annotated `superseded by <this>` |
| `contradicts` | this memory conflicts with the target | pointer annotated `contradicts <this> — verify`; **no** score change |
| `refines`     | this memory narrows/elaborates the target | no ranking effect, no annotation — a graph edge only        |

A typed target resolves through the exact same alias tiers as a `[[wikilink]]` (full stem,
prefix-stripped stem, `name:` slug) — an ambiguous or dangling typed target is refused the
same way, and surfaces as a `typed_dangling` finding (`memory.lint_links`) rather than
silently pointing nowhere. Typed edges never enter the untyped 1-hop graph expansion
(`memory.links`'s `adjacency`) — they live in their own structure, so a `supersedes` edge
can never become a side door around demotion via ordinary graph-neighbor injection (the same
penalty applies to a superseded memory whether it's found organically or via graph
expansion).

**Never hand-write a typed relation to mean "related."** That's what an untyped
`[[wikilink]]` is for — see "link conventions" below. Use a typed relation only when you
actually mean the specific consequence: `supersedes` when the OLD memory should visibly lose
rank, `contradicts` when a reader needs a verify-before-trusting flag, `refines` when you
want the graph edge with zero ranking side effects.

The one write primitive is `memory.links.add_typed_relation(path, relation, target)` —
additive, body-preserving, idempotent, per-item (there is no bulk-relation writer, per the
no-bulk-autonomous-sweeps invariant). In practice this is invoked via
`reconsolidate --reverify <old-name> --outcome {fix|demote} --superseded-by <new-name>`,
which writes the `supersedes` edge onto the NEW (successor) memory's frontmatter — never onto
the old one — so recall demotes/annotates the loser automatically from the next index
refresh on. `contradicts`/`refines` currently have no CLI writer; author them by hand in the
frontmatter.

## Type taxonomy

Four types, each with a different floor/recall treatment. Examples below show the SAME
questions asked of three different kinds of projects, to make clear a type is about the
KIND of fact, not the kind of project.

- **`user`** — a fact about the operator's role, responsibilities, or perspective.
  Floor-eligible (`## User`).
  - web app: "I'm the on-call engineer for the payments service this sprint."
  - CLI tool: "I'm the sole maintainer — optimize suggestions for solo velocity, not
    process ceremony."
  - data pipeline: "I own the ingestion DAGs; the modeling layer is a different team."
- **`feedback`** — a correction or confirmed-good approach the agent should keep doing (or
  stop doing). Always states WHY, not just the rule. Floor-eligible
  (`## Working Style & Process Feedback`).
  - web app: "Always run new migrations against staging before prod — a past migration
    locked a production table for 40 minutes."
  - CLI tool: "Never pass `--force` to the release script without asking first — it force-
    pushes the release tag."
  - data pipeline: "Backfill jobs must run with `--dry-run` first — an un-previewed backfill
    once double-counted a week of events."
- **`project`** — ongoing work state, decisions, non-obvious constraints discovered in code.
  NOT floor-eligible — recalled on demand only.
  - web app: "Auth uses short-lived JWTs (15 min) refreshed via `/token/refresh`; the mobile
    client does NOT implement refresh yet."
  - CLI tool: "Config resolution order is flag > `./toolname.toml` > `~/.config/toolname/
    config.toml` — deliberately NOT `$XDG_CONFIG_HOME`, to match the original tool's habit."
  - data pipeline: "The dedup step assumes upstream timestamps are UTC; one source (legacy
    exporter) emits local time and is normalized in `ingest/normalize.py`."
- **`reference`** — a pointer to an external system (tracker, dashboard, channel) plus what
  it's for. NOT floor-eligible — recalled on demand only.
  - web app: "Staging error dashboard: <internal URL> — filter by `service:payments`."
  - CLI tool: "Package registry listing: <registry URL> — release notes live there, not in
    this repo."
  - data pipeline: "Airflow UI: <internal URL> — the ingestion DAG is `ingest_v2`."

## The floor rule

`MEMORY.md` is the ONLY always-loaded memory context — everything else is recalled on demand,
per-prompt, by the hybrid recall hook. Keeping it lean is the whole point of the type split
above: memory pointer links (`](file.md)`) may appear ONLY under two sections,

```
## User
## Working Style & Process Feedback
```

A `project`/`reference` pointer anywhere else — including the "Recalled on demand" section —
is re-bloat: it re-grows the trimmed always-load, and `memory.lint_floor` flags it (the
SessionStart `floor` producer's one-line summary). Two restore/snapshot pointers,
`MEMORY.full.md` and `MEMORY.md` itself, are allow-listed everywhere — they aren't memory
entries. `memory.lint_floor` also flags floor link rot: a floor pointer whose target file no
longer exists.

Only `user`/`feedback` memories are floor-eligible; `memory.new_memory` appends a pointer
line automatically for those two types and reports the outcome explicitly rather than ever
silently no-op'ing — `appended`, `created-section` (the canonical header was renamed/deleted
by hand, so it's re-created at the end of the file in skeleton format), or `skipped` with a
machine-readable reason (`MEMORY.md missing`, `pointer already present`, or a write failure).
A `project`/`reference` write is `skipped` by design — recalled on demand, never floor-linked.

New pointers are inserted at their deterministic SORTED position within the section — before
the first existing pointer line whose memory name sorts lexicographically greater — rather
than always at the tail. Two teammates each adding a DIFFERENT name to the same section touch
a diff hunk at their OWN name's position instead of both appending to the section's single
highest-churn shared line, so git merges the two insertions cleanly instead of colliding.
When no existing pointer sorts greater than the new name (a brand-new section, or the name is
alphabetically last), it lands at the end of the block — the same place every insertion used
to land before sorting existed. This never reorders an unsorted legacy section's existing
lines; only the new line lands at its locally-correct slot.

## Evidence-block convention

Extraction is lossy: a distilled prose claim ("the retry logic caps at 3 attempts") loses the
exact error text, command output, or quoted decision that made it trustworthy in the first
place. When a memory's value depends on an exact quote, keep it verbatim in a fenced code
block placed directly under the distilled claim it supports, with a one-line source
annotation naming where it came from:

```
The deploy script fails fast on a missing `DATABASE_URL` rather than falling back to a
default — confirmed by re-running it locally:

    $ ./deploy.sh
    ✘ DATABASE_URL is unset — refusing to deploy with an implicit default

— from a local `./deploy.sh` run, 2026-06-02
```

When reconsolidating a memory (rewriting or trimming its prose on a `graduate`/`fix`
verdict), never delete an evidence block in the process — trim or update the surrounding
prose, keep the verbatim block, and update its source annotation if the source changed
(e.g. a fresher command run). A rewrite that keeps only the summary and drops the quote has
thrown away the part of the memory that was actually load-bearing.

This is a documented authoring/reconsolidation DISCIPLINE, not (yet) a code-enforced
invariant — nothing in `memory.reconsolidate` currently parses or protects fenced blocks
mechanically. Follow it by hand until it is.

## Link conventions

- **`[[name]]`** — the untyped wikilink, for a plain "see also" association with no ranking
  or annotation consequence. `memory.new_memory` suggests these automatically at write time
  (a trailing `Related: [[a]], [[b]]` body line, via a BM25 search against the existing
  corpus) — curate that suggestion before it lands: drop hits that share vocabulary but not
  a real relationship, add ones the automatic search missed.
- **Stem resolution** — a link target resolves against a corpus of file STEMS (filename
  without `.md`), trying, in order: (1) the full canonical stem — always wins; (2) the stem
  with its first `_`/`-` segment stripped (e.g. `feedback_foo` also resolves via `foo`); (3)
  the frontmatter `name:` slug. A target that resolves ONLY via tier 2/3 still works but is
  flagged `slug-mismatch` by `memory.lint_links` — non-canonical form, fix it to the full
  stem when convenient.
- **Ambiguous aliases refuse, they never guess.** If two different files claim the same
  soft alias (tier 2/3), that alias resolves to NEITHER — `memory.lint_links` names both
  claimants so the fix (link the full stem of the one you meant) is unambiguous. A dangling
  target (nobody claims it) and an ambiguous one (two files claim it) are different findings
  with different fixes.
- **When to use a typed relation instead of `[[wikilink]]`**: only when you mean the specific
  `supersedes`/`contradicts`/`refines` consequence described above. If you just mean "these
  two are related, worth reading together," that's an untyped `[[wikilink]]` — a typed
  relation is a claim with a ranking or annotation effect, not a stronger way to say "related."

## Duplicate-decision flow at write time

`memory.new_memory` scores a new memory's name+description against the EXISTING persisted
index before writing — dense cosine (threshold 0.80) when a warm dense index is available,
normalized BM25 (threshold 0.45) otherwise; both overridable via `HIPPO_DUP_THRESHOLD`. This
is warn-only: creation is NEVER blocked, and the above-threshold neighbors (top 3) ride out
on the result as a warning block. The write already happened — what to do about the overlap
is an agent decision, routed to exactly one of four branches:

- **add** — the two memories are genuinely distinct (similar vocabulary, different fact).
  Keep both; optionally add a `[[wikilink]]` between them if related.
- **update-existing** — same fact, and the EXISTING memory is the right home (just stale or
  incomplete). Fold the new content into it, then delete the just-created file (and its floor
  pointer, if any) and rebuild the index.
- **supersede** — the new memory replaces the flagged one's claim. Keep the new file; record
  it as a typed edge + demotion verdict in one step:
  `reconsolidate --reverify <old-name> --outcome demote --superseded-by <new-name>`.
- **skip** — the flagged memory already covers it; delete the just-created file (and its
  floor pointer, if any) and rebuild the index.

When the check could not run at all (no index yet, or an unscorable corpus), the result
carries a machine-readable `note` (e.g. `duplicate check skipped: no index`) — the absence of
a warning there does NOT mean "no duplicates," just "not checked."

## Stale banners + reinforcement

Recall renders a one-line banner on any injected pointer whose memory is currently flagged
stale (its `cited_paths` have changed since `source_commit`):

```
anchored to <sha>; N cited files changed since — verify before relying
```

This is UNGATED — unlike the optional salience-ranking signals, a correctness banner always
shows when applicable; it is not a ranking knob to opt out of. The banner clears itself with
no new machinery: a human-confirmed re-verification (`reconsolidate --reverify <name>
--outcome graduate|fix`) re-baselines `source_commit` to HEAD, so the memory drops out of the
next staleness scan entirely. That same re-verification also stamps `last_verified` — but
only the FIRST time a memory is ever reverified; a second reverification keeps the original
timestamp rather than logging a running history.

## Demote/snooze lifecycle

`reconsolidate --reverify <name> --outcome {graduate|fix|demote}` is the human-confirmed
verdict on one flagged memory:

- **`graduate`** — content re-read and confirmed still correct as of HEAD. Re-baselines
  `source_commit`, clearing the staleness flag.
- **`fix`** — content was edited and reconfirmed. Same re-baseline as `graduate`.
- **`demote`** — content is confirmed WRONG or not worth fixing. Does **not** re-baseline;
  instead it CHAINS `staleness.set_invalid_after` onto the memory in the same command — a
  terminal soft-invalidation. Recall's existing pre-cut penalty (score halved) engages
  immediately, with no second command required. A demoted memory drops out of the
  SessionStart staleness worklist (it's terminal, not pending) but is still COUNTED there
  (`"(+N already demoted)"`) so nothing silently disappears. Once a demotion ages past 30
  days, the staleness report names it as an archive candidate for `/hippo:audit` —
  report-only, never automatic.

`--superseded-by <successor>` is an opt-in addition to `fix`/`demote` (never `graduate` — a
confirmed-correct memory cannot simultaneously be superseded): it writes the `supersedes`
typed edge described above onto the SUCCESSOR's frontmatter in the same call.

`reconsolidate --snooze <name>` is a DIFFERENT, lighter action: it acks a flagged memory
without a verdict, excluding it from the next 5 new reconsolidation-ledger sessions' worklist
— then it re-nags. A snooze EXPIRES by design; only `demote`'s `invalid_after` is terminal.
Use `--snooze` for "I've seen this, not dealing with it today" and `--reverify ... --outcome
demote` for "I've confirmed this is wrong."
