---
description: Work hippo's maintenance queue one item at a time — captures to draft or discard, memories whose cited code moved, contradictions, merged-in duplicates, broken baselines and links, floor overflow, citation re-derivation, and changes waiting for re-consent — each with its evidence and your yes. Triggers include "tend memory", "consolidate memory", "drain the capture queue", "resolve contradictions", "/hippo:tend".
---

# /hippo:tend — the one maintenance queue

hippo derives every kind of memory upkeep into one ranked queue. This skill works it with
the user: show the top item and its evidence, say the likely verdict, ask, and apply ONE
verdict at a time. Nothing is applied without the user's yes, and there is no apply-all.
Integrity items (changes withheld from recall, invisible baselines) come first.

## Surface routing — decide first, then act silently

- **On Claude Desktop** (your context says you are in the Claude desktop app, `CLAUDE_CODE_ENTRYPOINT` is `claude-desktop`, or the preflight below stops on an unset `CLAUDE_PLUGIN_DATA`): run this same flow through the `tend` MCP tool — action='list', then 'next' (or 'show' with id=…), then per item action='apply' with id= and verdict=, or 'snooze' / 'skip' / 'hold'. Drafting a memory from a capture uses the `new_memory` tool (check:true first), then tend apply verdict='done' drains the seed as drafted (an expired seed comes back with tend action='restore', seed=… or all=true); a verbatim hunk goes through the `doctor` tool's action='secrets_scan' before it is fenced into a body. The consolidate steps below map the same way: the graph refresh is the `setup` tool's action='build_index'; co-recall link suggestions are tend action='link_proposals'; the blind-spot fixtures are tend action='fixtures' (step='draft', then 'confirm' per item); the interview is tend action='interview' (step='questions' or 'respond'); a decision the user stated is tend action='add_decision'. Just start driving the tool; don't explain why the shell flow isn't used here.
- **In a terminal Claude Code session**: run the bash flow below, guard first.

## Preflight (shared across all hippo skills)

```bash
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # Claude Code fills both in when it loads this skill
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] && [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] || { echo "✘ hippo's plugin paths are empty in this shell. Claude Code fills them into this skill's text when it loads the skill (the Bash tool does not inherit them), so run these blocks from the loaded /hippo:tend skill, not from a copy of its SKILL.md. If the loaded skill stops here too, take the MCP-tool route in 'Surface routing' above."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # the shared interpreter resolver
hippo_resolve_py
hippo_note_usage skill tend  # count this skill's use (one spool line, no Python)
```

Each Bash call is a fresh shell, so nothing set here reaches the next call. An inline
`hippo …` command runs as written (`hippo` is on the Bash tool's PATH and finds its own
venv).

## 1. See the queue

```bash
hippo tend
```

Tell the user the count by kind in one line, and anything held or failed. An empty queue is
a fine answer.

## 2. One item at a time

```bash
hippo tend next
```

`next` prints the item's id, why it is queued, the likely verdict, its gate, the evidence
(a capture's session summary, the code that moved under a memory, both sides of a pair, a
citation diff) and the exact verdicts it accepts. Read the evidence before proposing —
the likely verdict is a starting point, not the answer. Then ask the user, and only on a
yes:

```
hippo tend apply <id> --verdict <verdict>
```

What each kind needs before its verdict:

- **capture** — draft only what is durable and not re-derivable from code or git. Run
  `hippo new --check <name> "<description>" --type <type>` first, write each approved
  memory with `hippo new`, then `--verdict done`. Nothing worth keeping: `--verdict discard`.
- **reverify** — re-read the memory against the code that moved. `graduate` if it still
  holds; edit the body and `fix` if it was wrong; `demote` (optionally
  `--superseded-by <name>`) if it is wrong and not worth fixing; `archive` to retire it.
- **contradiction** — read both. `keep_one` or `merge` take `--winner` and `--loser` (merge
  after you folded the loser's unique content into the winner); `scope_both` after you
  edited both to name their scopes; `not_conflicting` when they are both right.
- **merge** — a merged-in memory looks like a duplicate. `supersede --winner <name>`,
  `updated` after you folded it into the existing memory, or `distinct`.
- **baseline** — confirm the memory still holds, then `rebaseline`.
- **link**, **floor** — edit the file, then `done`; tend re-checks and refuses while the
  problem is still there.
- **derivation** — read the citation diff, then `apply` for that memory; when every memory
  is applied, the `corpus` item takes `stamp`. On a corpus that is not committed to git,
  take a snapshot first (`hippo provenance --snapshot <label>`).
- **trust** — re-consent is its own gate: `hippo trust review` shows each change against
  the version consented to, and `hippo trust grant` takes the digest it prints (all of it,
  or `--files` for part). On Desktop the `trust` MCP tool does the same (action='review',
  then 'grant'). Consent is never inferred.

## 3. Deferring

- `hippo tend snooze <id> --days N` — not now.
- `hippo tend skip <id>` — leave it until its evidence changes.
- `hippo tend hold <kind-or-id> --reason "<why>"` — an owner decision to keep something
  as it is on purpose; held items count as resolved. `hippo tend release <kind-or-id>`
  undoes it. Only hold when the user says so, with their reason.

Repeat step 2 until the queue is empty or the user stops. Finish with one line: what was
applied, what was deferred, and what is left.

The two sections below are the long-form doctrine for the two kinds that need the most
judgment: drafting memories from captured sessions (and the rest of the deliberate
consolidation turn), and settling contradictions.

## The capture drain and the consolidate steps

The recall hook stays pure retrieval forever: no LLM work, no writes, no consolidation per
prompt. That deferred work has to land *somewhere* — here. This is one deliberate turn where
latency doesn't matter and you do the write-side maintenance in a batch: drain the captured
drafts, close the stale-memory loops, and refresh the graph. Run it when the SessionStart nudge
says the pending queue or worklist is deep, or on demand.

Every write in this skill is per-item and agent-gated — the same approval gate as everywhere
else. Nothing here is a bulk sweep.

### Before you start

```bash
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # Claude Code fills both in when it loads this skill
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] && [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] || { echo "✘ hippo's plugin paths are empty in this shell. Claude Code fills them into this skill's text when it loads the skill (the Bash tool does not inherit them), so run these blocks from the loaded /hippo:tend skill, not from a copy of its SKILL.md. If the loaded skill stops here too, take the MCP-tool route in 'Surface routing' above."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
hippo_resolve_py
hippo_note_usage skill tend consolidate  # OBS-2: count this skill's use (one spool line, no Python)
```

Each Bash call is a fresh shell, so nothing set here reaches the next call. Every block below
opens by pinning what it needs; an inline `hippo …` command runs
as written (`hippo` is on the Bash tool's PATH and finds its own venv).

> **Desktop:** the tool-by-tool mapping is in 'Surface routing' at the top of this skill — drive the SAME flow through those MCP tools, same order, same per-item approval gates. Each capture seed is a plain JSON file: read it directly for the full evidence when drafting.

### Step 1 — Drain the pending capture queue (CAP-2 → CAP-3)

The SessionEnd/SubagentStop capture pass leaves gitignored `session-capture` seeds — a
prior session's episode replay (queries, recalled names) + `git diff`, including bounded
VERBATIM diff hunks (GRW-1) — in `.claude/.memory-pending/`. Nothing in there is in the
corpus; you approve it here, per item.

List what's queued (highest-value first — each seed carries a `value:` score and trivial
sessions are labelled; the score orders your review, it never gates a seed):

```
hippo capture --list
```

When CAP-LLM triage is enabled (`capture_triage: true` in `~/.claude/hippo-llm.json`, or
`HIPPO_CAPTURE_LLM=1`), a seed may also carry `triage (LLM suggestion …)` lines — a
suggested type + name, a drafted description, and possible duplicates (the model's semantic
second opinion, plus a pre-run of the same `--check` machinery you use below). These are
SUGGESTIONS to verify, never decisions: use them as your starting draft when they hold up
against the seed's own evidence, discard them when they don't, and still run `--check`
yourself — the triage dup flags ride BESIDE the calibrated thresholds, not instead of them.
A `⚠ secret lint flagged the triage text` line means scrub before any corpus use, exactly
like flagged hunks.

For EACH seed, read its provenance (changed/new files, query previews, recalled names) and
decide what — if anything — is a durable fact worth keeping. Skip anything re-derivable from
the code or git history. For each candidate fact you draft, **check it against the corpus
BEFORE writing** so a near-duplicate never becomes a new file (CAP-3, a dry run — writes
nothing):

```
hippo new --check <candidate-name> "<one-line description>" --type {user|feedback|project|reference}
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
hippo capture --add-decision "<the decision, in the user's own terms>"
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

**Evidence-fence marker (CLB-3 — future drains only).** When you fence hunk lines into a
body, attribute the fence machine-recognizably so the drift detector can re-verify the
quote against the live tree at every SessionStart: put `evidence: <path>:<start>-<end>`
at the end of the fence's info string — `<path>` is the toplevel-relative file the hunk
came from, `<start>-<end>` the post-image line region from the hunk's `@@` header:

    ```diff evidence: src/thing.py:120-138
    @@ -118,6 +120,8 @@
     context line
    +added line
    ```

Keep the diff prefixes verbatim — the matcher is diff-line-class aware (context/added
lines are checked exact-then-whitespace-normalized; removed lines are excluded), and the
line region is an informational anchor, not the oracle: content contiguity is what gets
matched, so upstream edits that merely shift line numbers never flag. NEVER backfill
markers onto quotes in pre-existing bodies you did not just drain — unmarked fences are
out of the detector's scope by contract (doctor counts them as unverifiable instead).

- **route `add`** → the candidate is novel; create it, fencing the rationale into the body
  so the WHY is git-committed with the memory (not a one-time drain display):
  ```
  hippo new <candidate-name> "<description>" --type {user|feedback|project|reference} --body "<the WHY>" --rationale "from session <sid>; as of HEAD <sha>"
  ```
- **route `review`** → a near-duplicate/conflict was named. Read it, then pick one, naming the
  target explicitly (Mem0's ADD/UPDATE/SUPERSEDE/NOOP): **update-existing** (fold the fact into
  the named memory's body/description, don't create a file), **supersede** (the new fact
  replaces the old claim — create it with
  `--rationale "replaces <old-name> (similarity 0.9x); from session <sid>; as of HEAD <sha>"`, then
  `hippo reconsolidate --reverify <old-name> --outcome demote --superseded-by <new-name>`),
  or **skip** (already covered).

When a seed is fully processed (whatever you decided — including "nothing worth keeping"),
discard it so the queue drains and the nudge clears (`--dismiss` is the same op — use it when
a capture isn't worth keeping at all):

```
hippo capture --discard <seed-path-from-the-list>            # skipped: nothing worth keeping
hippo capture --discard <seed-path-from-the-list> --drafted  # it became a memory
```

The queue is BOUNDED: each capture keeps the highest-value, most-recent seeds, and seeds older
than 14 days or 20 sessions move to the queue's `expired/` folder. Nothing there is deleted —
the listing counts it, and `hippo capture --restore <seed>` (or `--restore --all`) brings it
back. If you can't drain now, defer the SessionStart nudge for a few sessions instead of
ignoring it (it re-nags after — a snooze is a deferral, not a dismissal):

```
hippo capture --snooze
```

### Step 2 — Work the reconsolidation worklist (LIF-1)

Recently-recalled memories whose cited code has drifted are the worklist. Address them per
item — LIF-1 gave `demote` a terminal state (it chains straight to soft-invalidation, no second
command) and an ack/snooze so a deferred item stops re-nagging:

```
hippo reconsolidate --dry-run
```

For each item, render its evidence brief BEFORE the verdict (EVD-1 — read-only; retires
the hand-gathered `git diff`): diffstat + bounded hunk headers from the entry's OWN
`source_commit` baseline to HEAD, secret-linted hunk bodies when clean, plus
evidence-drift fences, `invalid_after` state, and linked neighbors. Read the memory
body itself alongside it — the brief carries the code-side half only:

```
hippo brief <name>
```

(Desktop: the `tend` tool, action='show' with id='reverify:<name>'.) Then, for each
memory you re-ground, render exactly one verdict (per item):

```
hippo reconsolidate --reverify <name> --outcome {graduate|fix|demote|snooze}
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

### Step 3 — Refresh the graph

Refresh the index + persisted edge list so the session's writes are live and staleness is
recomputed:

```
hippo build-index
```

For link densification on the existing corpus (GRA-3 — suggest edges between high-similarity
pairs, agent-gated, never an autonomous body edit), use `/hippo:doctor`'s densification pass;
this skill's job is to drain and close loops, not to re-audit content.

### Step 4 — Propose co-recall edges (GRW-2)

Similarity can never link a bug to its unrelated-looking workaround — but the episode buffer
records which memories actually SURFACE TOGETHER. Tally pairs that co-recalled across many
distinct sessions (the threshold is deliberately high — on a sparse or noisy map this
proposes NOTHING, and that empty result is the designed outcome, not a failure):

```bash
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # a fresh shell: pin again
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"; hippo_resolve_py
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
type, no schema change). In the SAME edit, stamp the edge's provenance in that file's
frontmatter (GRF-1 — the key is `edge_origin`, NOT `origin`, which is the memory-level
promote stamp):

```yaml
metadata:
  edge_origin:
    the-other-name: co-recall
```

(merge into an existing `edge_origin:` map if one is already there). The stamp is
absence-emits-nothing — corpora without it behave identically, nothing bumps
`corpus_format`, and it never enters links.json; `hippo links --audit`
displays stamped-edge counts so a co-recall-proposed edge stays distinguishable from a
hand-authored one. Per item, agent-gated — never append the whole list in bulk. If no,
skip it; the tally will keep its count and you can dismiss it again next drain.

After any approved append, re-run `hippo build-index` so `links.json` carries the
new edge — GRA-1's 1-hop expansion picks it up on the very next recall, no ranking change
involved.

### Step 5 — Close the blind-spot loop (SIG-6)

The SessionStart blind-spot nudge routes HERE: a recurring abstained query means the corpus
kept being asked something it couldn't answer, and Step 1's drain may have just captured
exactly the memory that closes such a gap. Record that as an eval fixture so KPI-4 measures
the gap-closing loop end to end — first refresh the drafts queue:

```bash
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # a fresh shell: pin again
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"; hippo_resolve_py
"$PY" - <<'PYEOF'
import json
from memory.eval_recall import draft_abstention_fixtures, draft_livedin_fixtures
print(json.dumps(draft_abstention_fixtures(), indent=2))
print(json.dumps(draft_livedin_fixtures(), indent=2))
PYEOF
```

MEA-2 refreshes the FOURTH lane in the same step: `draft_livedin_fixtures` queues
(verbatim query → outcome-confirmed memory) candidates from the session ledgers —
`derived_expected` names the evidence, `expected` stays empty, and admission is the same
per-item confirm below (`category='single-hop'` for these).

Each recurring abstention cluster becomes an UNCONFIRMED row (`expected: []`) in the
gitignored drafts queue the summary's `path` names (`.claude/.memory-pending/` — queue
state, the same trust domain as the capture seeds; existing rows are preserved verbatim).
For each unconfirmed row: if a memory you JUST captured — or an existing one — genuinely
answers the query, propose the pair and, on explicit approval, admit it per item:

```bash
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # a fresh shell: pin again
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"; hippo_resolve_py
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

### Step 6 — The interview: ask up to three grounded questions (EXT-3)

hippo tells, but never asked. Three gap signals are machine-detected with no encode-side
loop — the recurring abstentions Step 5 just triaged, the contradiction inbox, and generated
drafts at their decay horizon. Render the (at most three) questions and put each to the
USER, verbatim — never answer one yourself:

```bash
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # a fresh shell: pin again
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"; hippo_resolve_py
"$PY" - <<'PYEOF'
from memory.interview import gather_questions, render_questions
from memory.provenance import resolve_dirs

md, rr = resolve_dirs()
print(render_questions(gather_questions(md, repo_root=rr)))
PYEOF
```

(Desktop: the `tend` tool, action='interview', step='questions'.) Each question cites its evidence
verbatim — the abstained query and its count, the contradicting pair, the draft stem. Route
each answer through the EXISTING per-item verbs, exactly as the question's `route` line says:
a written-down abstention answer goes through `new_memory` with `check:true` first (Step 1's
discipline — author it from real knowledge, never fabricate); a contradiction verdict goes
through the contradiction items of this queue; a draft verification goes through its
reverify item (`hippo tend apply reverify:<name> --verdict …`). The asks step itself writes
nothing.

A "no" is recorded so it NEVER re-asks; a "later" snoozes for a few days — per item, via the
same tend action: action='interview', step='respond', `qid=<from the listing>`,
`outcome='decline'` or `'later'`. Zero questions is the designed norm, not a failure; if dogfooding ever says this
step nags, the cap and decline memory are the dials that exist to be turned down.

> A future auto-maintained map-of-content note (CAP-5) will also be refreshed here once it
> ships; today consolidation ends at a drained queue, an addressed worklist, a current graph,
> a blind-spot queue that is judged rather than silently growing, and at most three grounded
> questions the human actually wanted to be asked.

### When NOT to use

- "Is my corpus content still accurate" — a deep, judgment-based scorecard is `/hippo:doctor`.
- "Is the plumbing working" — `/hippo:doctor`.
- Saving one specific fact you already have in hand — just `/hippo:new`; you don't need the
  whole drain for a single write.

## Contradictions — one verdict per pair

A `contradicts` edge deliberately demotes neither side — it means "one of these is wrong,
VERIFY", and the verify step is a human call. Until someone renders it, recall keeps
injecting both sides of the dispute. This skill walks each unresolved pair, one verdict per
item. Every verdict that changes the corpus is an ordinary reviewable git commit; the ONLY
verdict that doesn't touch the corpus (mark-not-conflicting) lands in a per-clone ledger.

### Before you start

```bash
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # Claude Code fills both in when it loads this skill
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] && [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] || { echo "✘ hippo's plugin paths are empty in this shell. Claude Code fills them into this skill's text when it loads the skill (the Bash tool does not inherit them), so run these blocks from the loaded /hippo:tend skill, not from a copy of its SKILL.md. If the loaded skill stops here too, take the MCP-tool route in 'Surface routing' above."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
hippo_resolve_py
hippo_note_usage skill tend resolve  # OBS-2: count this skill's use (one spool line, no Python)
```

Each Bash call is a fresh shell, so nothing set here reaches the next call. Every block below
opens by pinning what it needs; an inline `hippo …` command runs
as written (`hippo` is on the Bash tool's PATH and finds its own venv).

### Step 1 — List the inbox

```bash
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # a fresh shell: pin again
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"; hippo_resolve_py
hippo resolve --list
```

Every unresolved `contradicts` pair in the corpus, whether or not the two memories ever
co-surfaced in a recall. `(declared by: …)` names which file carries the `contradicts:`
frontmatter — that is the file a corpus-mutating verdict edits.

Each pair carries a deterministic **evidence card** (TMB-1): conflict age in
commits-since-declaration (git-mined; "unknown" for uncommitted/rewritten history), the
git-newer side, cached cited-code drift per side, usage asymmetry (withheld below 5
recorded sessions), and a `suggested:` prefill expressed strictly in the four verdict
names below (or `abstain`). The suggestion is adjudication EVIDENCE, never a decision —
read both files regardless, and it is never auto-applied. When you render a verdict,
pass the suggestion you saw so agreement is auditable: `--prefill <name>` on `--dismiss`,
or `prefill=` on a verdict through the v1 contradiction route (it lands beside the dismiss
records in the per-clone ledger).

The inbox may also list `(PROPOSED by dream --contradictions …)` pairs — DRM-C candidates
an LLM flagged as substantive conflicts among dream's high-cofire pairs, shown with the
model's one-line rationale. No edge is declared yet, so there is no frontmatter to drop;
the same verdicts below apply, with the proposal clearing itself on any corpus outcome
(the listing repeats this inline): keep-one-supersede-other's `supersedes` edge clears it;
declaring the edge (`contradicts: [<other>]` on one side) turns it into an ordinary
declared pair if you want the dispute to stay visible instead; merging/retiring a side
clears it; and scope-both ends in `--dismiss` (after scoping, "both stand as written" —
exactly what dismiss records). A proposal is an LLM's opinion with a cofire receipt —
read both files before believing it.

### Step 2 — For EACH pair, read both files and render ONE verdict

Read both memories under `.claude/memory/` first — the descriptions in the listing are
hooks, not the full claims. Then pick exactly one, per item:

- **keep-A-supersede-B** — one side won (the other is outdated/wrong). Demote the loser and
  record the succession edge:
  ```
  hippo reconsolidate --reverify <loser-name> --outcome demote --superseded-by <winner-name>
  ```
  Then edit the declaring memory's frontmatter to drop the now-settled `contradicts:` entry
  (the supersedes edge carries the story from here). Commit both — an ordinary reviewable diff.

- **keep-both-as-scoped** — both are right in different scopes ("we use X *on the backend*",
  "we use Y *on the frontend*"). Edit each memory's description/body to name its scope, drop
  the `contradicts:` entry from the declaring file, and commit.

- **merge** — the two are one fact split awkwardly. Fold the surviving claim into ONE memory
  (update its body/description), then retire the other via the `/hippo:remove` flow so links
  and the floor stay consistent. Commit.

- **mark-not-conflicting** — the edge itself was wrong; both stand as written:
  ```
  hippo resolve --dismiss <name-a> <name-b>
  ```
  This is the ONLY verdict that does not edit the corpus — it lands in this clone's
  gitignored ledger (under `${CLAUDE_PLUGIN_DATA}`), so the pair stops appearing here while
  the files and the edge stay untouched for other readers to judge.

Never bulk-apply a verdict across pairs — each pair gets its own reading and its own commit.

### Step 3 — Confirm the inbox drained

```bash
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # a fresh shell: pin again
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"; hippo_resolve_py
hippo resolve --list
```

Pairs you dismissed stay gone on this clone; pairs you resolved in the corpus are gone
everywhere once the commit lands.

### When NOT to use

- "Is my corpus content still accurate" — that judgment-based sweep is `/hippo:doctor`.
- Draining captured session drafts / the stale-memory worklist — `/hippo:tend`.
- A conflict between a memory and CLAUDE.md/.claude/rules (the governance plane) — the
  SessionStart radar routes those to `/hippo:tend`; this inbox is memory ⇄ memory.
