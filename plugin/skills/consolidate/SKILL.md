---
description: Sleep-time consolidation — one deliberate turn for the write-side maintenance the hot path defers. Drains the CAP-2 pending-capture queue into memory (check-first, per-item approval), works the reconsolidation worklist (reverify/fix/demote/snooze), and refreshes the link graph. Triggers include "consolidate memory", "drain the capture queue", "process pending captures", "/hippo:consolidate". Suggested when the pending queue or stale worklist is deep. Not a content audit — that's /hippo:audit.
---

# /hippo:consolidate — sleep-time consolidation

The recall hook stays pure retrieval forever: no LLM work, no writes, no consolidation per
prompt. That deferred work has to land *somewhere* — here. This is one deliberate turn where
latency doesn't matter and you do the write-side maintenance in a batch: drain the captured
drafts, close the stale-memory loops, and refresh the graph. Run it when the SessionStart nudge
says the pending queue or worklist is deep, or on demand.

Every write in this skill is per-item and agent-gated — the same approval gate as everywhere
else. Nothing here is a bulk sweep.

## Preflight (shared across all hippo skills)

```bash
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ CLAUDE_PLUGIN_DATA is unset/empty — this Claude Code version is too old for hippo's self-provisioning. Update Claude Code, or export CLAUDE_PLUGIN_DATA to a writable dir (e.g. ~/.claude/hippo-data) and re-run."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
hippo_resolve_py
```

## Step 1 — Drain the pending capture queue (CAP-2 → CAP-3)

The SessionEnd/SubagentStop capture pass leaves gitignored `session-capture` seeds — a
prior session's episode replay (queries, recalled names) + `git diff` — in
`.claude/.memory-pending/`. Nothing in there is in the corpus; you approve it here, per item.

List what's queued:

```
"$PY" -m memory.capture --list
```

For EACH seed, read its provenance (changed/new files, query previews, recalled names) and
decide what — if anything — is a durable fact worth keeping. Skip anything re-derivable from
the code or git history. For each candidate fact you draft, **check it against the corpus
BEFORE writing** so a near-duplicate never becomes a new file (CAP-3, a dry run — writes
nothing):

```
"$PY" -m memory.new_memory --check <candidate-name> "<one-line description>" --type {user|feedback|project|reference}
```

Then, for each candidate that survives the check, **render its RATIONALE before asking for
approval** (GOV-3) — the evidence a teammate reviewing the eventual MEMORY.md diff needs,
fused from what is already in hand (the seed listing + the `--check` block):

```
proposal <candidate-name>: from session <sid> (queries: "<q1>", "<q2>", …)
  evidence : changed <paths…> across <head_commit>..<head> (the seed's commit range)
  restates/replaces : <neighbor> (similarity 0.9x)     [--check neighbors, when any]
  governance echo   : <file> (overlap 0.7x)            [--check warning block, when any]
  baseline : as of HEAD <sha>                          [--check's baseline line]
```

The baseline is the `--check` output's own `baseline:` line — HEAD at PROPOSAL time
(`source_commit` does not exist yet; provenance backfill happens only on the real write).
If a seed ever carries verbatim diff hunks (a future capture upgrade), include the relevant
hunk lines in the `evidence` block too.

- **route `add`** → the candidate is novel; create it, fencing the rationale into the body
  so the WHY is git-committed with the memory (not a one-time drain display):
  ```
  "$PY" -m memory.new_memory <candidate-name> "<description>" --type {user|feedback|project|reference} --body "<the WHY>" --rationale "from session <sid>; as of HEAD <sha>"
  ```
- **route `review`** → a near-duplicate/conflict was named. Read it, then pick one, naming the
  target explicitly (Mem0's ADD/UPDATE/SUPERSEDE/NOOP): **update-existing** (fold the fact into
  the named memory's body/description, don't create a file), **supersede** (the new fact
  replaces the old claim — create it with
  `--rationale "replaces <old-name> (similarity 0.9x); from session <sid>; as of HEAD <sha>"`, then
  `"$PY" -m memory.reconsolidate --reverify <old-name> --outcome demote --superseded-by <new-name>`),
  or **skip** (already covered).

When a seed is fully processed (whatever you decided — including "nothing worth keeping"),
discard it so the queue drains and the nudge clears:

```
"$PY" -m memory.capture --discard <seed-path-from-the-list>
```

## Step 2 — Work the reconsolidation worklist (LIF-1)

Recently-recalled memories whose cited code has drifted are the worklist. Address them per
item — LIF-1 gave `demote` a terminal state (it chains straight to soft-invalidation, no second
command) and an ack/snooze so a deferred item stops re-nagging:

```
"$PY" -m memory.reconsolidate --dry-run
```

Then, for each memory you re-ground, render exactly one verdict (per item):

```
"$PY" -m memory.reconsolidate --reverify <name> --outcome {graduate|fix|demote|snooze}
```

`graduate` (re-verified current), `fix` (you corrected the content), `demote` (confirmed wrong
— auto-invalidates so recall stops surfacing it at full rank), `snooze` (explicitly defer — it
drops off the next N worklists instead of re-nagging).

## Step 3 — Refresh the graph

Refresh the index + persisted edge list so the session's writes are live and staleness is
recomputed:

```
"$PY" -m memory.build_index
```

For link densification on the existing corpus (GRA-3 — suggest edges between high-similarity
pairs, agent-gated, never an autonomous body edit), use `/hippo:audit`'s densification pass;
this skill's job is to drain and close loops, not to re-audit content.

> A future auto-maintained map-of-content note (CAP-5) will also be refreshed here once it
> ships; today consolidation ends at a drained queue, an addressed worklist, and a current graph.

## When NOT to use

- "Is my corpus content still accurate" — a deep, judgment-based scorecard is `/hippo:audit`.
- "Is the plumbing working" — `/hippo:doctor`.
- Saving one specific fact you already have in hand — just `/hippo:new`; you don't need the
  whole drain for a single write.
