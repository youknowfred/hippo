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
prior session's episode replay (queries, recalled names) + `git diff`, including bounded
VERBATIM diff hunks (GRW-1) — in `.claude/.memory-pending/`. Nothing in there is in the
corpus; you approve it here, per item.

List what's queued (highest-value first — each seed carries a `value:` score and trivial
sessions are labelled; the score orders your review, it never gates a seed):

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
  decisions : "<d1>"; "<d2>"                           [the seed's user-confirmed WHY, when any]
  restates/replaces : <neighbor> (similarity 0.9x)     [--check neighbors, when any]
  governance echo   : <file> (overlap 0.7x)            [--check warning block, when any]
  baseline : as of HEAD <sha>                          [--check's baseline line]
```

A seed's `decisions` entries (GRW-4) are the session's recorded WHY — text the agent captured
in-session, quoting or paraphrasing what the USER stated or confirmed. Fold the relevant ones
into the drafted `--body` (they are exactly the durable rationale a memory needs and the one
thing git cannot re-derive). TRANSCRIPTION, NOT SYNTHESIS: never invent a WHY the seed does
not carry — if the seed has no decisions and the diff alone doesn't justify the fact, ask, or
write the WHAT without a fabricated WHY. When you are draining the SAME session you are still
in (the context is live), you may also record decisions the user confirmed just now — one per
command — before drafting:

```
"$PY" -m memory.capture --add-decision "<the decision, in the user's own terms>"
```

The baseline is the `--check` output's own `baseline:` line — HEAD at PROPOSAL time
(`source_commit` does not exist yet; provenance backfill happens only on the real write).

When the seed carries `diff_hunks` (schema 2 — the listing shows an `evidence: … bytes`
line), include the RELEVANT hunk lines in the `evidence` block and quote them verbatim in
the drafted body where they ground the fact — verbatim beats extraction; a memory that
quotes its diff never paraphrases its own evidence wrong.

**HARD GATE — secret-lint any hunk before it lands in a body.** Verbatim hunks widen the
secret-exposure surface (the seed is gitignored; a memory body is committed and recalled
forever). Before fencing ANY hunk lines into a `--body`, run the shipped lint over the exact
lines you intend to fence, and REFUSE the fence if it reports anything — drop or scrub the
flagged lines instead (`write_memory`'s own write-time lint is the backstop, not the gate):

```
"$PY" - <<'PYEOF'
from memory.secrets import scan_with_remediation
hunk_lines = """<paste the exact hunk lines you intend to fence>"""
for w in scan_with_remediation(hunk_lines):
    print(w)
PYEOF
```

Seeds already flagged at capture (`⚠ secret lint flagged these hunks` in the listing) get
the same treatment: their hunks NEVER reach a body verbatim — summarize around the secret,
or scrub it and lint again until the scan is clean.

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
discard it so the queue drains and the nudge clears (`--dismiss` is the same op — use it when
a capture isn't worth keeping at all):

```
"$PY" -m memory.capture --discard <seed-path-from-the-list>
```

The queue is BOUNDED (CAP-6): each capture self-prunes to the highest-value, most-recent
seeds, so an un-drained backlog can never grow without limit — a gitignored trivial seed a
prune drops is nothing a future session couldn't re-capture. If you can't drain now, defer the
SessionStart nudge for a few sessions instead of ignoring it (it re-nags after — a snooze is a
deferral, not a dismissal):

```
"$PY" -m memory.capture --snooze
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

Two SessionStart signals route extra items through this same per-item gate:

- `[since-watermark]` worklist items (GRW-5) were flagged by COMMITS landed since your last
  session touching their cited files — commit-precise, on the list whether or not they were
  recently recalled. Same verdicts as above.
- **Squash-merge healing (GRW-6):** when SessionStart reported a recent merge broke
  staleness baselines (`🩹 … baselines no longer resolve`), re-ground each NAMED memory
  against the post-merge code, and once you confirm it still holds, render `--outcome
  graduate` — the reverify re-baselines its `source_commit` to the current HEAD and
  re-derives its citations, healing the break (detection is automatic; the rebaseline is
  only ever this per-item, confirmed verdict — never bulk).

## Step 3 — Refresh the graph

Refresh the index + persisted edge list so the session's writes are live and staleness is
recomputed:

```
"$PY" -m memory.build_index
```

For link densification on the existing corpus (GRA-3 — suggest edges between high-similarity
pairs, agent-gated, never an autonomous body edit), use `/hippo:audit`'s densification pass;
this skill's job is to drain and close loops, not to re-audit content.

## Step 4 — Propose co-recall edges (GRW-2)

Similarity can never link a bug to its unrelated-looking workaround — but the episode buffer
records which memories actually SURFACE TOGETHER. Tally pairs that co-recalled across many
distinct sessions (the threshold is deliberately high — on a sparse or noisy map this
proposes NOTHING, and that empty result is the designed outcome, not a failure):

```bash
"$PY" - <<'PYEOF'
from memory.lint_floor import floor_memory_names
from memory.links import build_graph
from memory.provenance import resolve_dirs
from memory.telemetry import co_recall_pairs, default_telemetry_dir

memory_dir, repo_root = resolve_dirs()
pairs = co_recall_pairs(
    default_telemetry_dir(memory_dir),
    exclude_names=floor_memory_names(memory_dir),  # floor names would dominate every pair
)
adjacent = set()
graph = build_graph(memory_dir)
if graph:
    for src, outs in graph.adjacency.items():
        adjacent.update(frozenset((src, tgt)) for tgt in outs)
    for src, rels in graph.typed.items():
        for tgts in rels.values():
            adjacent.update(frozenset((src, tgt)) for tgt in tgts)
fresh = [p for p in pairs if frozenset(p["pair"]) not in adjacent]
if not fresh:
    print("no co-recall pairs above threshold — the sparse map stays empty (by design)")
for p in fresh:
    a, b = p["pair"]
    print(f"{a} <-> {b}   (co-recalled in {p['sessions']} distinct sessions)")
PYEOF
```

For EACH printed pair (already-linked pairs are dropped above), read both memories and judge
whether the association is real — would someone recalling one genuinely need the other? If
yes, ask for approval, then append a `[[the-other-name]]` reference into ONE side's body
(its `Related:` line if present — an untyped wikilink, the GRA-3 convention; no new edge
type, no schema change). Per item, agent-gated — never append the whole list in bulk. If no,
skip it; the tally will keep its count and you can dismiss it again next drain.

After any approved append, re-run `"$PY" -m memory.build_index` so `links.json` carries the
new edge — GRA-1's 1-hop expansion picks it up on the very next recall, no ranking change
involved.

## Step 5 — Close the blind-spot loop (SIG-6)

The SessionStart blind-spot nudge routes HERE: a recurring abstained query means the corpus
kept being asked something it couldn't answer, and Step 1's drain may have just captured
exactly the memory that closes such a gap. Record that as an eval fixture so KPI-4 measures
the gap-closing loop end to end — first refresh the drafts queue:

```bash
"$PY" - <<'PYEOF'
import json
from memory.eval_recall import draft_abstention_fixtures
print(json.dumps(draft_abstention_fixtures(), indent=2))
PYEOF
```

Each recurring abstention cluster becomes an UNCONFIRMED row (`expected: []`) in the
gitignored drafts queue the summary's `path` names (`.claude/.memory-pending/` — queue
state, the same trust domain as the capture seeds; existing rows are preserved verbatim).
For each unconfirmed row: if a memory you JUST captured — or an existing one — genuinely
answers the query, propose the pair and, on explicit approval, admit it per item:

```bash
"$PY" - <<'PYEOF'
import json
from memory.eval_recall import confirm_hard_set_row
print(json.dumps(confirm_hard_set_row("<the query>", ["<stem>"]), indent=2))
PYEOF
```

The row lands in `.claude/memory/.audit-fixtures/recall_hard_set.yaml` tagged
`category: abstention`, and the drafts row drains. If NO memory answers a row, it stays a
capture gap — future drains are where it gets a memory on its own merits; **never fabricate
a memory to make a fixture pass** (the primitive refuses stems that don't exist — a refusal
is a verdict, not a thing to work around). Delete rows that are noise. Per item,
agent-gated — never admit the whole queue in bulk.

> A future auto-maintained map-of-content note (CAP-5) will also be refreshed here once it
> ships; today consolidation ends at a drained queue, an addressed worklist, a current graph,
> and a blind-spot queue that is judged rather than silently growing.

## When NOT to use

- "Is my corpus content still accurate" — a deep, judgment-based scorecard is `/hippo:audit`.
- "Is the plumbing working" — `/hippo:doctor`.
- Saving one specific fact you already have in hand — just `/hippo:new`; you don't need the
  whole drain for a single write.
