<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo/hippo-lockup-dark.svg">
    <img src="assets/logo/hippo-lockup.svg" alt="hippo — a hippo surfacing at a waterline; the wordmark floats on the same line" width="360">
  </picture>
</p>

# hippo

[![CI](https://github.com/youknowfred/hippo/actions/workflows/ci.yml/badge.svg)](https://github.com/youknowfred/hippo/actions/workflows/ci.yml)
[![version](https://img.shields.io/github/v/tag/youknowfred/hippo?label=version&sort=semver)](https://github.com/youknowfred/hippo/releases)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Give Claude Code a memory that lives in your repo: a corpus of small markdown files it recalls
the right pieces of, on demand, every session. **New here? Start with
[How hippo thinks](CONCEPTS.md)**, the five-minute mental model (what a memory is, the
always-on floor vs. on-demand recall, the four types, why markdown-in-git).

**Local, git-native memory for Claude Code: your repo is the store, recall runs offline with no LLM
and no network per prompt (what it injects into context is counted, per prompt and per session, in
`/hippo:doctor`), staleness is git-drift verified before injection (the cited code moved, not
calendar age), and every team memory lands through code review.** Distributed as a Claude Code
plugin.

"A markdown corpus with hybrid recall" is a crowded shelf. hippo's line is narrower
and sharper: **git *is* the store** (diff it, review it, revert it; not an opaque local DB),
**staleness is semantic** (did the code a memory cites actually move?), the **hot path runs no
LLM** ($0 and nothing leaves your machine per prompt), and **team memory ships through review**,
never an autonomous write. See how it stacks up in [Compared to other memory tools](#compared-to-other-memory-tools).

In daily use since 2026-06 across three production corpora, the largest over 1,000 memories.

**On this page:** [Quickstart](#quickstart) · [What you get](#what-you-get) ·
[Automatic capture](#automatic-capture--memory-that-writes-itself-gated-by-your-review) ·
[Compared to other tools](#compared-to-other-memory-tools) · [Commands](#commands) ·
[Issue trackers (Linear)](#issue-trackers-linear-and-others) ·
[Opt-in settings](#warm-recall-opt-in) · [Native memory and worktrees](#hippo-and-claude-codes-native-memory) ·
[Troubleshooting](#troubleshooting) · [All the docs](#all-the-docs)

## Quickstart

> **Install from the Claude Code _terminal_ (the `claude` CLI); use hippo from the terminal
> _or_ the Claude Desktop app's Code tab.** The desktop app's **local sessions run the same
> plugin**: the auto-recall hook, the skills and the MCP tools. Typed `/hippo:*` commands run
> there too, and the skills route themselves to hippo's MCP tools. You can also just ask
> (*"set up hippo memory for this project"*). Two things still need a terminal, and each says
> so if you run it on the desktop app: `/hippo:remove`, and every `/hippo:share` flow except
> packs (promote, promote a rule, publish, export, import). Claude Code's docs say plugins
> also install from the desktop app; hippo has verified only the terminal install below (see
> [`PLATFORM.md`](PLATFORM.md)). **Cloud / remote sessions** can't run hippo at all (it needs
> local access to your files and git).

1. **Install** (inside the `claude` terminal):

   ```
   /plugin marketplace add youknowfred/hippo
   /plugin install hippo@hippo
   ```

2. **Set up.** One command, two halves, each safe to re-run:

   ```
   /hippo:setup
   ```

   - **Once per machine** it builds the plugin's own venv and downloads the ~130MB embedding
     model (the ONE online step in the plugin's whole lifecycle).
   - **Once per project** it seeds `.claude/memory/` from the starter packs (core by
     default: a `user_role.md` template and the memory-master policy; themed packs are
     opt-in), builds the recall index, and wires the always-loaded floor. Fill in
     `user_role.md` when it asks. On a teammate's clone or a second machine it wires the
     existing corpus and overwrites nothing.

3. **Use it.** Say *"remember this: …"* (the `/hippo:new` skill writes a recall-ready
   memory), then just work: every prompt is matched against the corpus by the
   UserPromptSubmit hook and the relevant memories are injected automatically. Session
   starts surface staleness, recent captures, and link health. If anything seems off, run
   `/hippo:doctor` (or see [Troubleshooting](#troubleshooting)).

4. **See it work.** Ask Claude *"what do you remember about my role?"* (or run
   `/hippo:recall "my role"` directly). hippo matches your prompt against the corpus and
   surfaces the relevant memory inline. That returned memory is the whole point: the right
   context on demand, retrieved with no model call and never leaving your machine. (Fill in
   `user_role.md` first, from step 2, so there's something real to recall.)

Before the machine half of setup has run, recall works immediately in BM25-only mode: the
plugin vendors a dependency-free BM25 scorer and frontmatter parser (`plugin/memory/_vendor/`)
for that window, so a bare `python3` with none of the pinned deps still serves real lexical
recall. Setup unlocks the dense half; until then dense recall degrades gracefully rather
than blocking or erroring.

## What you get

- **Recall on every prompt.** A hook ranks the corpus against your prompt (dense + BM25,
  fused) and injects the memories that earn it: up to ten rows when the match is strong,
  fewer when it is not, none when nothing matches. No model call, no network.
- **A reminder at the file.** The first time a session touches a file that a pinned or
  feedback memory cites, that memory's one-line description is shown once, at the moment it
  applies (`HIPPO_DISABLE=jit` turns it off).
- **An always-loaded floor.** `MEMORY.md` holds pointers to the few memories worth knowing in
  every session. It reaches Claude through Claude Code's own memory, so hippo adds no second
  always-on channel ([below](#hippo-and-claude-codes-native-memory)).
- **Staleness by git drift.** A memory records the files it cites and the commit it was
  verified at. When those files move, the memory is flagged on the recalled row and queued
  for a re-check, whatever its age.
- **Capture behind a review.** A session's end leaves a seed of what happened in a queue;
  nothing is written to the corpus until you approve it
  ([below](#automatic-capture--memory-that-writes-itself-gated-by-your-review)).
- **One maintenance queue.** `/hippo:tend` lists every kind of upkeep and works it one
  verdict at a time.
- **Consent before injection.** Memory files you have not reviewed on this machine (a clone,
  a pull, a merge) are withheld from recall until you do (`hippo trust review`). Saved memories are scanned for credential shapes and
  prompt-injection shapes, and recalled text is labelled as quoted data.
- **Three tiers.** The project corpus (committed, reviewed), a user tier in
  `~/.claude/hippo-memory` recalled in every project, and a private per-repo tier
  (`.claude/memory.local/`) that is never committed.
- **Team memory through review.** The corpus is plain files in git, so a memory lands the way
  code does. `/hippo:review` renders a memory diff as a review packet, `/hippo:share` moves
  packs between repos, and a CI recipe posts the memories a pull request's files touch.
- **Your tracker's statuses, not a copy of them.** A memory cites an issue id; recall shows
  the id's current status from a mirror the repo commits
  ([Linear and others](#issue-trackers-linear-and-others)).
- **Mid-turn and subagent memory.** The plugin's MCP server gives the model the same recall
  as a tool, for the moments the per-prompt hook cannot cover.
- **A terminal door.** `hippo <verb>` runs the same engine without Claude (`hippo help`),
  which is what CI and scheduled maintenance use.

## Automatic capture — memory that writes itself, gated by your review

hippo remembers more than what you explicitly save. When a session ends, a background hook
saves a seed of what actually happened (the queries you ran, the memories recalled, the files
that changed since the session began) in a gitignored pending queue, `.hippo/pending/`.
The hook drafts nothing and writes nothing to the corpus. **Nothing enters your corpus
automatically.**

The next session's SessionStart nudge tells you when that queue is worth draining. You run:

```
/hippo:tend
```

and hippo walks you through each seed one at a time, showing its evidence (which files
changed, the session's queries, any near-duplicate already in the corpus), drafts a memory
from it, and writes only the ones you approve, as an ordinary reviewable markdown diff.
This is the part native memory doesn't have: **capture is automatic, but every write waits for
a human.** You get the recall benefit of always-on capture without ever ceding control of what
your corpus says.

### Optional: a small model as second opinion (off by default)

If you give hippo an API key, two opt-in surfaces add one bounded small-model call each,
both **propose-only**, so the review gate above is untouched:

- **Capture triage.** Each captured seed arrives pre-annotated with a suggested type, a
  drafted description, and likely near-duplicates (judged semantically, alongside, never
  instead of, hippo's calibrated similarity check). You still ratify per item in
  `/hippo:tend`.
- **Contradiction discovery.** The `/hippo:dream` pass asks, for its strongest co-firing
  memory pairs, "do these actually *disagree*, or merely relate?", the one judgment
  similarity math can't make. Confirmed candidates join the `/hippo:tend` queue as
  contradictions, where you render the verdict as usual. Nothing auto-applies.

Turn either on in hippo's plugin options: Claude Code asks for them when you enable the
plugin, and lists the switches in `/config` (**LLM triage of captured sessions** and **LLM
contradiction check in dream**). Set the **LLM API key** option too (from hippo's entry in
`/plugin`): it is masked and kept in your system keychain, never in a file. The key reaches
hippo's hooks and its MCP server and never a shell command, so the contradiction check runs
through hippo's MCP `dream` tool. An `ANTHROPIC_API_KEY` in your environment overrides the
option. A scheduled `hippo sleep` (cron or launchd) and a shell command never see the options:
give them `HIPPO_LLM_API_KEY` and the switch's variable (`HIPPO_DREAM_CONTRADICTIONS=1`,
`HIPPO_CAPTURE_LLM=1`) in their own environment; `hippo sleep --print-schedule` shows where.

The default model is the `claude-haiku-5-5` alias, with thinking off (a heavy month of
captures costs well under a dollar); set the **LLM model** option to `claude-sonnet-5-5` to
upgrade the judgment at ~20× the (still small) per-call cost. Any failure (no key, no
network, a malformed reply) falls back to exactly the un-enriched behavior, and a run with a
switch on but no key says so in one line. Full knob reference:
[`plugin/memory/README.md`](plugin/memory/README.md#standalone-llm-enrichment-opt-in-default-off).

## Compared to other memory tools

Agent memory for Claude Code is a busy category — claude-mem, memsearch, memweave, supermemory,
and Anthropic's own native memory all solve "the agent forgets between sessions." Several are
excellent. hippo makes a specific set of trades the others don't:

| | **hippo** | claude-mem | memsearch / memweave | Anthropic native memory | supermemory |
|---|---|---|---|---|---|
| **Store** | your **git repo** (plain markdown, diffable) | local SQLite + vector store of AI-compressed session observations | markdown + a derived index (Milvus / SQLite-FTS) | client-managed files / an auto `MEMORY.md` | hosted service |
| **Recall** | hybrid dense+BM25, on-demand, ranked | AI-compressed context injected at session start, plus search | hybrid semantic + keyword | the file(s), always loaded | hosted semantic search |
| **Hot-path cost** | **$0 inference** — no LLM or network per prompt; injected context is counted | AI compression in the loop | local embeddings; write path may summarize | injected file = tokens | API calls to the service |
| **Staleness** | semantic **git-drift** (did the *cited code* move) | recency-based | recency / content-hash | — | — |
| **Team memory** | ships through **code review**; a foreign corpus is quarantined until you trust it | auto-captured, no review gate | shared index, no review gate | per-project / per-machine | shared via account |
| **Runs** | local, offline | local | local (memsearch embeds Milvus Lite by default) | in-model + local files | cloud; self-hosting on paid tiers |

Where hippo stands apart is two rows: **staleness is semantic and checked before injection** —
offline and deterministically, hippo asks whether the *code a memory cites* has moved since the
memory was written, and says so on the recalled line (GitHub Copilot Memory also checks citations
against the current branch; most tools decay by calendar age, by content hash, or not at all) —
and **every team memory lands through review** rather than an autonomous write. Underpinning both, **the whole store is plain, diffable git** (the history is the
audit/review/revert trail — not a byproduct of, or a sidecar to, a separate database). The top rows
— markdown, hybrid recall, local — are table stakes now; these are not.

**See the staleness difference in 5 seconds:** [`demo/git_drift.sh`](demo/git_drift.sh) builds a
throwaway repo, writes a memory that cites a function, edits that function, and shows hippo flag the
memory stale — because the code it points at moved, not because a timer expired. No download needed.

*(Reflects each tool's documented behavior, last checked 2026-11; these projects move fast, and
corrections are welcome via an issue.)*

**One reproducible number.** On the shipped 50-memory golden dev corpus, over 18 hand-written
cross-vocabulary paraphrase queries, hippo scores **recall@10 = 1.0** and **MRR@10 ≈ 0.91** — at
**$0 inference per prompt** (no model call, no network). It reproduces to the digit on any machine, with
or without the embedding model, via one command: `bench/run.sh`. Full methodology and the
principled *why we don't run LongMemEval / LoCoMo / BEAM* (they measure autonomous chat-history
extraction — a thing hippo deliberately gates behind human approval) are in
[`bench/README.md`](bench/README.md).

**Why the hot path is $0 and private.** Every prompt's recall is local lexical + cached-dense
ranking — hippo calls no model API to retrieve, so retrieval spends **no inference**, and **nothing
leaves your machine**. What recall injects does take context; doctor counts it per prompt and per
session. The one online step in hippo's entire lifecycle is `/hippo:setup`
downloading the embedding model once; after that, recall is fully offline. For a privacy- or
cost-sensitive team, "memory that calls no model per prompt and never phones home" is a hard
requirement a hosted-by-default or LLM-in-the-loop memory can't meet without extra self-hosting work.

## Why it's called hippo

Named for the **hippocampus**, the brain's memory-indexing structure, not just the animal at the
waterline. The analogy is loose and deliberately cheap: hippo is git + embeddings + staleness
scoring, not a model of neural tissue. What it *does* borrow is the *shape*: five operations
over one shared store (the markdown corpus plus its derived index, link graph, and
ledgers), the way memory is encoded, consolidated, and replayed:

- **recall**: hybrid BM25 + dense retrieval, per prompt, `$0` and offline. No LLM, no network.
- **consolidate**: *sleep-time compute*, a deliberate off-hot-path turn that drains pending
  captures and re-verifies stale memories. One agent turn, per-item, approval-gated; nothing
  autonomous or bulk. The command is `/hippo:tend`.
- **reconsolidate**: re-verify a memory when recall resurfaces it *and* its cited code has
  drifted. These items sit in the same `/hippo:tend` queue.
- **forget**: not calendar decay. A memory goes stale when a file it cites moved after its
  recorded commit. Semantic, and always graceful demotion, never deletion.
- **dream**: offline replay that re-runs recall over each memory's own self-query to surface
  latent graph edges. It proposes nothing on a young corpus, and on a soaked one it applies
  only the few links that clear a calibrated bar (five per pass at most), each stamped,
  reversible with one command, and left uncommitted in your working tree; `--dry-run`
  previews. **The empty pass is the norm.**

The loose analogy earns the name; every claim above resolves to a file you can `git diff`. The
sharpest ideas are the ones that *break* from biology (staleness is git-drift, not time), and the
full map, with where each analogy ends, is in
[How hippo thinks](CONCEPTS.md#one-more-idea-the-sleep-model).

## Commands

hippo ships as 9 `/hippo:*` skills. You rarely invoke most of them by hand — the agent runs the
maintenance ones when a session-start signal calls for it — but here is the whole surface. In a
terminal the same engine is one command, `hippo <verb>` (`hippo help` lists the verbs).

**Setup (you run these):**
- `/hippo:setup` — once per machine, builds the plugin's venv and warms the offline embedding
  model (the one online step in hippo's whole lifecycle); once per project, seeds
  `.claude/memory/`, wires the native-memory symlink and builds the recall index. Safe on a
  teammate's clone or a second machine: an existing corpus is wired, never overwritten. A linked
  `git worktree` needs nothing: it resolves the main checkout's corpus
  ([Git worktrees](#git-worktrees)).

**Everyday:**
- `/hippo:new` — save one memory the right way (correct frontmatter, provenance backfill, index
  refresh, floor pointer when applicable). This is what *"remember this: …"* routes to.
- `/hippo:recall` — deliberately pull from the corpus: *"what do you remember about X"*, list it
  by type, or ask why a memory was (or wasn't) recalled — the winning ranking lane, typed edges,
  steering, or the near-miss score and the floor it missed. (The prompt hook already recalls
  automatically every turn; this is for when you want to *see* it.)

**Curation & health:**
- `/hippo:tend` — the one maintenance queue: every kind of upkeep (captures, memories whose cited
  code moved, contradictions, merged-in duplicates, broken baselines and links, floor overflow,
  citation re-derivation, path-scoped rule proposals, changes waiting for re-consent) in one
  ranked list, worked one verdict at a time with your yes. `hippo tend` in a terminal; the
  `tend` MCP tool on Desktop.
  A lesson another trusted project on this machine also keeps shows up there too: promote it
  to the user tier, or, when the same rule reads differently there, adopt one text.
- `/hippo:doctor` — fast check of the *plumbing* (bootstrapped, venv healthy, corpus symlinked,
  indexed and trusted, format current) plus the consent step; on request, a deep, judgment-based
  audit of the *content* (staleness, drift, orphans, archive candidates).
- `/hippo:dream` — the offline link pass: it replays recall over each memory's own self-query,
  watches which memories co-fire, and diffs that against the link graph to surface latent edges.
  Gated on a soak bar and capped at five edges per pass; what it applies is reversible and
  never committed, and `--dry-run` previews. The empty pass is the designed norm.
- `/hippo:review` — review a memory diff like a pull request: what each touched memory's change
  *is* (added, updated, superseded, archived, relinked), the lints on the touched files, and how
  recall would shift.

**Sharing & portability:**
- `/hippo:share` — memory beyond this project, one item at a time: extract or install memory
  *packs*; promote a proven-portable memory into your machine-local user tier; turn a procedural
  memory into a glob-scoped `.claude/rules/` file; publish one memory into the repo's committed
  subset; export the floor as a proposed `AGENTS.md` diff; import rules from other tools (Cursor
  `.cursor/rules/*.mdc` first).

**Offboarding:**
- `/hippo:remove` — uninstall for this project: drop the symlink so native memory stops injecting
  the floor, offer to delete the derived index/telemetry, and report (never delete) the shared
  venv/cache.

The v1 skill names (`bootstrap`, `init`, `why`, `consolidate`, `resolve`, `audit`, `pack`,
`promote`, `promote-rule`, `publish`, `export-agents`, `import`) keep working until v2.0: each is
now a one-line route into the verb above that absorbed it.

**CI — memory for reviewers who don't run Claude:**
- `hippo recall --for-diff <range> [--json]` — the reviewer's recall: joins a git diff's changed
  files against the corpus's `cited_paths` and lists the citing memories (pins and feedback
  lessons first, a ⚠ flag on any whose cited code drifted since last verify). A pure read-only
  join — no index, no model, no telemetry — so it runs on a bare `python3` in CI. The shipped
  recipe ([.github/workflows/memory-on-diff.yml](.github/workflows/memory-on-diff.yml)) posts the
  result as one sticky PR comment and skips entirely when nothing matches. Disclosure boundary:
  the comment renders only names + descriptions already committed to the repo's own
  `.claude/memory/` corpus — nothing a reader of the repo couldn't already see. Same-repo PRs
  only by default (fork PRs get no write token).
- `hippo recall --for-issue <ID> [--json] [--cap N]` — the issue's recall: the memories whose
  description or body cites a tracker id as a whole token, each with where it cites it, under
  the id's status and snapshot date from a declared ledger
  ([Issue trackers](#issue-trackers-linear-and-others)). Read-only like `--for-diff`. Without a `ledgers` declaration it says so and
  exits 0; an id that matches no declared `id_re` is refused (exit 2) with the pattern named.

**Which one do I want?**
- **recall vs. doctor** — `recall` asks the *corpus* a question; `doctor` asks whether the *plugin*
  is healthy. Empty recall **and** a green doctor means you just haven't written that memory yet.
- **doctor vs. its content audit** — the checks are fast plumbing (seconds, deterministic); the
  audit is a slow, judgment-based read of whether the content is still *accurate*. The checks
  never tell you a memory is out of date; the audit does.
- **tend vs. the audit** — `tend` *drains and closes loops* (captures → memory, stale memories →
  verdicts, contradictions → decisions); the audit *diagnoses* content health but drains nothing.
- **tend vs. dream** — both run *off* the hot path, but `tend` works what is already queued;
  `dream` *discovers* what wasn't captured — latent links between existing memories.

## Issue trackers: Linear and others

Task state belongs in the tracker; a memory holds what the tracker does not. hippo keeps the
two apart and joins them at read time:

- **Cite the id, never the status.** A memory that says "ENG-901 is in progress" is wrong the
  day the issue moves. A memory that cites `ENG-901` stays right.
- **Recall shows the status for you.** If the repo commits a mirror of its tracker (a Linear
  snapshot file is the worked example), declare it under `ledgers` in `hippo.json` and a
  recalled row whose name or description cites an id ends with that id's status and the
  mirror's date:
  `· ENG-901 In Progress (snapshot 2026-08-26)`.
- **Ask by issue.** `hippo recall --for-issue ENG-901` lists the memories that cite one issue.
  A prompt that names an issue id also lets a journal citing it through the recall lanes.
- **Keep status words out of memory.** A corpus can ban them from the floor and warn on them
  in memory bodies (`floor_lint.banned_re`, in [the write gate](SETTINGS.md#the-write-gate)).

hippo reads only the committed file. It never calls Linear's API or any other tracker's,
holds no tracker credential, and writes nothing back, so Jira, GitHub Issues or a hand-kept
file work the same way. The declaration and its limits are in
[SETTINGS.md](SETTINGS.md#tracker-statuses-at-read-time).

## Warm recall (opt-in)

By default each prompt's recall runs in a fresh Python process: start the interpreter, load the
index and the embedding model, rank, exit. That is a few hundred milliseconds per prompt, more on a
busy machine. Warm recall hands the same work to the session's own hippo MCP server, which keeps
the model loaded between prompts.

```
hippo setup --warm               # preview: the exact hook and the file it goes in; writes nothing
hippo setup --warm --yes         # turn it on (the settings file is backed up first)
hippo setup --warm --status      # is it on, and which path served recent prompts
hippo setup --warm --off --yes   # turn it off again
```

**What it changes:** one `UserPromptSubmit` hook of type `mcp_tool` in your user settings
(`~/.claude/settings.json`), pointing at hippo's server. Nothing else is touched, and users who
don't opt in pay nothing. hippo's usual recall hook stays installed and decides, prompt by prompt,
which of the two answers, so recall never runs twice. The usual path takes over whenever the server
can't answer: the first prompt of some sessions, a session still running an older hippo after an
update (restart it), a server busy with another tool, or a session whose served recall failed or
ran slow.

**What stays the same:** the ranking, filtering, budgets and trust checks are the same code. The
server reads the index on disk and writes nothing to your corpus or index; it keeps a few small
handshake files in the plugin's data directory.

**Cost:** no money and no network, as before. Each session's server holds the embedding model in
memory once it has served a prompt. `/hippo:doctor` shows whether warm recall is on and how many
prompts in the last 30 days were served warm, by the usual path, or failed.

## A git home for the user tier (opt-in)

The user tier (`~/.claude/hippo-memory`) holds what you taught hippo about yourself, recalled in
every project. It is not a repository until you ask for one:

```
hippo setup --user-tier          # preview: the .gitignore it writes and the `git init`; writes nothing
hippo setup --user-tier --yes    # make it a local repository (no commit, no remote)
hippo setup --user-tier --status # its posture: not a repository, uncommitted changes, behind its upstream
```

hippo makes no commit (you choose what the first one holds) and adds no remote. It never pushes,
pulls or fetches; add a private remote yourself if you want the tier on a second machine. A user
tier that already sits inside a repository (a dotfiles repo) is left alone.

**Machine-local memories** stay out of that repository: save one with
`hippo new <name> "<description>" --type user --machine-local` (or add `machine_local: true` to
an existing memory's frontmatter and re-run `hippo setup --user-tier --yes`). hippo lists such
files in a block of the tier's `.gitignore`; recall on this machine still reads them. Ignoring does
not untrack: `/hippo:doctor` names a machine-local memory that is already committed, with the
`git rm --cached` that stops tracking it. On another machine's clone, a project memory's link to a
machine-local memory reads "machine-local on another machine" in `hippo lint-links`, not
"dangling".

`/hippo:doctor` prints the tier's posture in one line, from local state only: "as of your last
fetch" means exactly that.

## Keeping CONVENTIONS.md current

Setup seeds `.claude/memory/CONVENTIONS.md`, the reference for how a memory file is shaped. Its
first line is the text's version. `/hippo:doctor` says whether the corpus's copy is the
plugin's current version, an earlier one, or edited, and:

```
hippo setup --conventions        # preview: the diff, and any lines of your own it would move
hippo setup --conventions --yes  # move your own lines into CONVENTIONS.local.md, then refresh the copy
```

Keep a corpus's own rules in `CONVENTIONS.local.md` beside it. hippo never requires that file,
never overwrites it, and adds to it only on that `--yes` (the lines of an edited copy that are
in no version hippo shipped). Neither file is a memory: neither is indexed or recalled.

The refresh lands where the corpus's saves land. On a corpus whose `write_path` is `outbox`
(and this machine has accepted it) both files are written to the outbox and `hippo ship`
carries them into its branch; the working tree is not touched. Otherwise the corpus dir is
written, and from a linked git worktree that is the worktree's own `.claude/memory/`, never
the main checkout's tracked copy.

## Settings and gates

Settings live in three places, and the first one that answers wins: an environment variable,
hippo's plugin options (your machine: the calm SessionStart switch, the LLM opt-ins and key),
and `.claude/memory/hippo.json` (the corpus's committed policy, shared with your team).
[SETTINGS.md](SETTINGS.md) is the full reference. What a corpus can ask for there:

- **Recall lanes and journals**: keep long work journals out of the prompt hook's rows unless
  the prompt names them ([recall lanes](SETTINGS.md#recall-lanes),
  [folded sections](SETTINGS.md#folded-sections)).
- **Tracker statuses**: a committed tracker mirror whose statuses recall renders
  ([above](#issue-trackers-linear-and-others)).
- **An outbox**: saved memories go to a gitignored folder beside the corpus, so `git status`
  stays clean, and `hippo ship` lands them as one commit on one new branch, never pushed
  ([the outbox](SETTINGS.md#saving-without-touching-the-working-tree-opt-in)).
- **A write gate**: a `PreToolUse` hook warns (or, by committed policy, refuses) when a write
  leaves a floor budget over and makes it worse ([the write gate](SETTINGS.md#the-write-gate)).
- **Consent for a session's own writes**: skip the review for bytes this machine's own session
  wrote and that pass the lints
  ([session writes](SETTINGS.md#consent-for-a-sessions-own-memory-writes-opt-in)).
- **A commit gate and budgets**: `hippo gate --staged` as a pre-commit hook, with declared
  limits on files, floor size and journals, and a floor diet in `/hippo:tend`
  ([the commit gate](SETTINGS.md#at-commit-time-hippo-gate-and-declared-budgets)).

A setting that changes what a machine does with pulled bytes (the outbox, a refusing gate,
session-write consent) takes one `hippo trust policy --accept` on each machine before it acts.

## Removal / Uninstall

To stop hippo from acting on a project, run inside Claude Code:

```
/hippo:remove
```

This removes the cross-machine symlink under `~/.claude/projects/<encoded>/memory` — the one
thing that actually stops Claude Code's native memory from injecting the floor for this project.
It then offers (a confirmed step, never automatic) to delete the derived, gitignored dirs in
`.hippo/` (the index, telemetry and pending queue), and **reports** — never
deletes — the pre-migration corpus backups in `.hippo/snapshots/`, which are a gitignored
corpus's only undo, and the shared per-machine venv (`CLAUDE_PLUGIN_DATA/venv`) and fastembed
model cache paths, since those are shared across every project using the plugin on this machine.

It also reports, and never deletes, `.claude/memory.outbox/` when a corpus saves there: those
memories are not in git yet.

`.claude/memory/` itself — the git-tracked corpus — is always left alone: it stays committed in
git, inert, until someone runs `/hippo:setup` again (in this repo, a fresh clone, or a new
worktree).

## Support matrix

| Platform | Status |
|---|---|
| macOS | **Fully supported** — the primary development platform; CI runs the full suite on macOS |
| Linux | **Fully supported** — CI runs the full suite on Ubuntu. Without `CLAUDE_PLUGIN_DATA`, the fallback cache dir is XDG-aware: `${XDG_CACHE_HOME:-~/.cache}/hippo-memory` ([ROADMAP.yaml](ROADMAP.yaml), OSP-2) |
| Windows | **Out of scope** — a decision, not an omission ([ROADMAP.yaml](ROADMAP.yaml), decision OQ-2 + non_goals): the hooks are bash and the engine is untested there. Revisit only on concrete adoption evidence |

**Claude Code 2.1.269 or newer.** That is the first version that sets plugin options from
`/config` (the attention setting); `/hippo:doctor` prints the running version against this
floor, and SessionStart names an older one. CI runs the full suite on Python 3.10 and 3.12 and
a dependency-install lane on 3.11, 3.13 and 3.14. Setup's machine half runs once per machine;
its project half runs once per project.

## hippo and Claude Code's native memory

Anthropic now ships memory of its own: the GA `memory_20250818` tool (a client-managed
file-memory API, view/create/edit/delete) and Claude Code's **Auto Memory**, which quietly
maintains a project `MEMORY.md` — build commands, code style, architecture decisions, bugs it
solved with you. So the fair question is *"why not just use Anthropic's memory?"*

Because hippo is the **ranking + hygiene + review layer on top of it**, not a competitor to it.
hippo **composes** with native memory — it does not replace or fork it.

- **What native memory does.** At session start Claude Code loads `MEMORY.md` from a per-project
  memory directory (`~/.claude/projects/<encoded>/memory`, the first 200 lines or 25 KB), and with
  auto memory (on by default) Claude writes notes there as it works, keeping detail in per-topic
  files it opens on demand. It's per-machine and loaded every session — great for a small
  always-on note. The index it loads is **static**: the model reads the topic files it decides to
  open, with no ranking step choosing the memories that match your prompt. It is **not reviewable
  in git**, **auto-written (no approval gate)**, and **not shared with teammates** — and nothing
  tells you when an auto-captured fact went stale.
- **What hippo adds — the layer on top.** A **git-native, teammate-reviewable** corpus with
  **hybrid dense+BM25 recall** (the *right* memories on demand, not everything every prompt),
  **semantic git-drift staleness**, a typed **link graph**, **reconsolidation**, an
  **automatic-capture-behind-an-approval-gate** path, and an offline **generative-replay** pass
  (`/hippo:dream`) that surfaces latent edges between memories. It is exactly the ranking, staleness
  hygiene, and human review that Auto Memory's always-loaded, auto-written note lacks.
- **How they compose.** `/hippo:setup` points the native memory location at this repo's
  `.claude/memory/` via a symlink, so hippo's always-load **floor** (the `user`/`feedback`
  pointers) reaches context *through* native memory's own always-load — hippo adds no second
  always-load channel. Everything else is served on demand by the recall hook and the MCP tools.
  If native auto memory already wrote notes for the repo, setup previews adopting them into the
  corpus (`hippo adopt`) and asks first; adopted notes wait for `hippo trust review` like any
  bytes hippo did not write.

That symlink is the **only** native behavior hippo depends on. The full contract — every
assumption, how it can drift, and how `/hippo:doctor` detects a break — is documented in
[`plugin/memory/NATIVE_MEMORY.md`](plugin/memory/NATIVE_MEMORY.md).

### Git worktrees

A session launched inside a **linked worktree** (`git worktree add …`, including the
`.claude/worktrees/<name>/` trees Claude Code creates) resolves the **main checkout's** corpus,
not the worktree's own git-checked-out copy of `.claude/memory/`. That copy is the branch's
committed snapshot — a different file with a different inode — and everything derived from the
corpus moves with it: the recall index, the telemetry ledgers, and the capture queue all live
beside the main checkout's `.claude/memory/`, so a capture from a worktree session lands where
the next session (in any tree) will find it, and nothing vanishes when the worktree is retired.
This matches Claude Code's own native memory, which keys a worktree session's project on the
main checkout. Trust is keyed the same way — trusting the repo once trusts it from every
worktree.

Session-local facts stay session-local: the session-end capture diffs the **worktree** you
edited in, and the fleet-presence doc records the **worktree's** branch and HEAD.

Two escape hatches, both `HIPPO_*` env vars: `HIPPO_CORPUS_ROOT=<dir>` pins where resolution
starts (and disables the redirect — point it at the worktree to get the old behavior, or at any
other checkout), and `HIPPO_MEMORY_DIR` still names a corpus dir outright. A main checkout that
carries **no** corpus leaves a worktree's branch-only corpus alone. `/hippo:doctor` prints which
tree it resolved in one line (`tree: MAIN working tree … (redirected from linked worktree …)`)
and names any dead `.claude/.memory-*` copies a worktree still carries from before this behavior.

Since v1.42.4 the index, the telemetry ledgers and the capture pending queue live in
`<project>/.hippo/`, beside `.claude/` rather than inside it. The Claude desktop app copies a
checkout's whole `.claude/` dir, ignored files included, into every worktree it creates, and these
dirs used to ride along (about 39 MB per worktree on a large corpus). The first session after the
update moves them with one rename each. `/hippo:doctor`'s `derived dirs:` line names anything still
in `.claude/`, and in the main checkout it totals the old copies existing worktrees carry.
`hippo setup --worktree-copies` lists them, and `--remove <dir> --yes` removes one directory per
yes (it refuses anything that is not one of those copies, and any symlink); the doctor line also
prints a `find … -exec rm -rf` that deletes only those, all at once.

A repo nested inside another, or a git submodule, resolves only its own corpus: resolution stops
at the session's git toplevel. When it has none and a parent does, SessionStart and doctor say
so; point `HIPPO_CORPUS_ROOT` at the parent to share the parent's corpus.

## Repo layout

This repo is both a **plugin marketplace** and the **plugin itself**:

```
.claude-plugin/marketplace.json   # marketplace manifest (lists the `hippo` plugin)
plugin/
├── .claude-plugin/plugin.json    # plugin manifest (version, options, the MCP server)
├── memory/                       # the engine (Python package, imported as `memory`)
│   └── _vendor/                  # pre-bootstrap fallbacks (BM25 + frontmatter parser)
├── hooks/                        # UserPromptSubmit recall · SessionStart report · PreToolUse write gate ·
│                                 # PostToolUse first-touch reminder · PreCompact nudge · SessionEnd/SubagentStop capture
├── assets/packs/                 # starter packs (core seeded by default; rest opt-in)
├── bin/hippo                     # `hippo <verb>`: the one entry into the engine
├── requirements.txt              # fastembed, numpy, PyYAML, rank-bm25 (the venv path)
└── skills/                       # the nine verbs, plus 12 v1 names routing to them (see Commands above)
tests/                            # hermetic test suite (no network/model download by default)
bench/                            # the reproducible recall benchmark (bench/run.sh)
demo/                             # git_drift.sh: staleness in five seconds
.github/workflows/ci.yml          # hermetic matrix + dense/secret-scan/resolution lanes + shellcheck
.github/workflows/memory-on-diff.yml  # the CI recipe: memories a pull request's files touch
.github/workflows/release.yml     # on a version tag: manifests and CHANGELOG must agree, then the release is published
```

New to the ideas here? Start with [How hippo thinks](CONCEPTS.md). For the deep internals
(recall, staleness, reconsolidation, archive), see the engine reference at
[`plugin/memory/README.md`](plugin/memory/README.md); for the skills and the MCP server, see
[`plugin/README.md`](plugin/README.md).

## Bootstrap vs. auto-provision (design decision)

The plugin's Python dependencies (fastembed, numpy, PyYAML, rank-bm25) and the ~130MB
fastembed model cache are **not** installed automatically on enable. Bootstrapping is
**explicit** via `/hippo:setup`'s machine half, run once per machine, rather than
an implicit SessionStart auto-provision. Reasoning:

- The one-time venv build + model warm is an **online** step (the only one in this
  plugin's whole lifecycle) and can take tens of seconds — doing it silently inside a
  `SessionStart` hook risks tripping the hook timeout on a slow connection, whereas an
  explicit skill invocation can show real progress and isn't budget-constrained the way
  a hook is.
- Auto-provisioning on first `SessionStart` would mean the *first* recall a user ever
  sees is unpredictably slow (or silently degrades to BM25 if the hook times out
  mid-provision) with no clear signal why — a confusing first impression for a tool
  whose whole value proposition is instant, silent recall.
  Explicit bootstrap means degraded-to-BM25 only ever happens because bootstrap
  genuinely hasn't run yet, not because it raced a hook timeout.
- It keeps the hard hook contract (`exit 0`, never downloads, and no hook blocks unless the
  corpus's own committed policy asks it to) simple and
  auditable: hooks only ever *read* an already-warmed cache; they never *provision* one.

Until bootstrap runs, the SessionStart hook names the next step instead of staying
silent: in the first session of each day, in any repo, with a one-line command that
dismisses it on this machine.

## Troubleshooting

- **`/hippo:setup` (or any `/hippo:*` command) "isn't a recognized command" / "only works in the
  Claude Code terminal."** Desktop app versions before 2026-10 rejected typed plugin commands;
  current ones run them (see [`PLATFORM.md`](PLATFORM.md)). Either way the **desktop app**'s
  local sessions run hippo's hook, skills, and MCP tools, so where typed commands are still
  rejected, just ask in plain words (*"set up hippo memory here"*, *"run hippo doctor"*,
  *"tend memory"*): the agent drives the same flows through the `setup`, `doctor`, `trust`
  and `tend` MCP tools, per item, with the same approval gates. **Cloud / remote** sessions
  are the real limit: no local filesystem, no hippo. (Nothing's wrong with your install.)
- **Recall is BM25-only in the desktop app even though I already ran setup.** The machine half
  of setup is once per plugin-data dir, and some Claude Code versions gave the terminal CLI and
  the desktop app **different plugin-data dirs** (`…/data/hippo-<marketplace>` vs
  `…/data/hippo-inline`, seen in 2026-07; on 2.1.286 both use the first), so the terminal's
  venv and model cache weren't where a desktop session looked. Ask the desktop session to run
  hippo setup once: its status output names the sibling install so you can see exactly this
  situation, and `/hippo:doctor` (or the `doctor` tool) flags it too.
- **Some recalls run BM25-only and I don't know why.** The prompt hook records a
  reason beside every BM25-only recall (the model not warmed, an index without vectors, another
  model, the dense timeout, an error, dense turned off, a tier without vectors, or simply no
  memory clearing the dense floor), and `/hippo:doctor`'s BM25-only line names the most common
  one over 30 days with what to do about it.
- **Recall comes back empty.** Almost always one of three things: **(a) setup's machine half
  never ran** on this machine, so recall is BM25-only until `/hippo:setup` finishes;
  **(b) the corpus isn't trusted yet**: a freshly cloned or pulled corpus injects *nothing*
  until you review it. `/hippo:doctor` walks the consent step (`hippo trust review`, then
  `hippo trust grant`), and its withheld line counts memories by how they arrived. A file
  that changed outside hippo after you consented is withheld the same way until you review it;
  **(c) `user_role.md` is still the `<FILL-ME>` template**, so the only thing to recall is
  placeholder text. Edit it with your real role and context.
- **A memory I wrote never resurfaces.** Recall is on-demand and ranked, not always-on: only
  the always-load *floor* (the `user`/`feedback` pointers) is loaded in every session;
  everything else surfaces when a prompt actually matches it. Phrase your question closer to
  the memory's own wording, or confirm it's indexed with `/hippo:doctor`.
- **Recall surfaces something off-topic.** Since v1.41.0, recall injects only when the dense
  and lexical lanes agree on a memory, or when one of them is strong on its own (a very close
  meaning, or a description sharing several of the prompt's terms). A prompt that shares one
  coincidental word with a memory abstains. The gate needs the dense model, so on a BM25-only
  install (setup's machine half not run, or the model cache cold) any shared token still admits.
  Ask `/hippo:recall` why a memory surfaced for the prompt to see which lane matched it;
  `HIPPO_DISABLE=abstain-gate` turns the gate off. `/hippo:doctor` reports the measured
  per-corpus rate when you supply an off-topic fixture.
- **Recall shows fewer memories per prompt than it used to.** The prompt hook used to serve
  ten rows whenever it served any. Now the count follows the evidence. When the prompt's best
  match clears the dense floor by a wide margin, up to ten rows are served as before.
  Otherwise at most five are, and each has to have evidence of its own: a cosine close to the
  prompt's best, a place in the top ten of both the dense and a lexical lane, or a description
  that shares several of the prompt's terms. The best-ranked row is always served. A row cut
  this way is not named on the overflow line; `hippo recall` and the recall tool are
  unchanged and still return the full ranking. The rule needs the dense model, so a BM25-only
  install serves as before. `HIPPO_SIGNAL_COUNT=0` turns it off, and `/hippo:doctor` reports
  the median rows per injecting prompt.
- **The recalled rows in my prompt look shorter than `hippo recall` shows them.** In the prompt
  hook only, the first row keeps its description as written (up to 220 characters) and each
  later row's description is cut at a word boundary at 90. The "already in floor" line names
  its three best-ranked memories instead of five, the "already surfaced this thread" line
  gives its count alone, and a session's second and later blocks carry a shorter header that
  still says the lines are quoted data and to read the file before relying on it. Which rows
  are served, their order and their names do not change, and a row that does not fit is
  still named on the overflow line. `hippo recall` and the recall tool render as before.
  `HIPPO_HOOK_COMPACT=0` restores the longer block.
- **I'm in a git worktree and doctor / recall / capture seem to be looking at the wrong corpus.**
  They aren't, since v1.34.0: a linked worktree resolves the main checkout's live corpus, and
  the `resolved corpus:` doctor line says which tree it used. If that line reads `tree: LINKED
  worktree …`, the main checkout has no `.claude/memory/` (a branch-only corpus — expected). If
  it reads `tree: OVERRIDE via HIPPO_CORPUS_ROOT=…`, an env var is pinning it. A `worktree
  copies:` warning means the worktree still carries a dead `.claude/.memory-pending/` queue from
  before v1.34.0 — nothing drains it; delete the copy or retire the worktree. An `APPLY REFUSED
  — corpus untrusted` from a worktree on a repo you already trusted was the same bug (trust rows
  key on the repo root) and no longer happens; do not "fix" it by trusting the worktree.
- **"Not a git repository" / staleness looks inactive.** Outside a git repo hippo runs in a
  degraded mode: recall, indexing, links, and the floor all work, but staleness tracking and
  provenance backfill need git — `git init` and commit to activate them.
- **When in doubt, run `/hippo:doctor`.** It is the one-stop diagnostic — it checks the native
  symlink, the recall index, corpus trust and drift, the corpus format version, link density,
  and unfilled templates, and prints the exact repair command for whatever it finds.

## All the docs

| Read | For |
|---|---|
| [CONCEPTS.md](CONCEPTS.md) | The five-minute mental model: memories, the floor, the four types |
| [SETTINGS.md](SETTINGS.md) | `hippo.json`, the outbox and `hippo ship`, the write and commit gates |
| [plugin/README.md](plugin/README.md) | The skills and the MCP server |
| [plugin/memory/README.md](plugin/memory/README.md) | The engine: recall, staleness, reconsolidation, archive, purge |
| [plugin/memory/NATIVE_MEMORY.md](plugin/memory/NATIVE_MEMORY.md) | The contract with Claude Code's native memory |
| [UPGRADING.md](UPGRADING.md) | What each release asks of an existing install |
| [STABILITY.md](STABILITY.md) | What is frozen, and the deprecation window (the v2 draft: [STABILITY.v2.md](STABILITY.v2.md)) |
| [PLATFORM.md](PLATFORM.md) | Dated observations of the Claude Code behavior hippo depends on |
| [bench/README.md](bench/README.md) | The benchmark and its method |
| [CHANGELOG.md](CHANGELOG.md) | Every release |
| [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) | Working on hippo; reporting a vulnerability |

## Security

Found a vulnerability? Please report it privately — see [SECURITY.md](SECURITY.md)
for the disclosure channel, supported versions, and hippo's threat model (untrusted
shared corpora, credentials committed into memory, and prompt-injection via memory
text).

## License

MIT — see [LICENSE](LICENSE). A copy ships inside the plugin bundle
([plugin/LICENSE](plugin/LICENSE)) so installs carry the license text too. The engine code
was written by this repo's author for a private predecessor project and is relicensed here
under the same MIT terms; no third-party code was ported with it.
The pre-bootstrap fallbacks in `plugin/memory/_vendor/` (a BM25 scorer and a frontmatter
parser) are likewise original implementations, MIT like the rest — not copied vendor code.

hippo's own source ports no third-party code, but bootstrap installs a small set of
permissively-licensed Python packages (fastembed, numpy, PyYAML, rank-bm25 and their
dependencies) and downloads an embedding model. Those runtime components and their licenses
(all Apache-2.0 / MIT / BSD-3-Clause / MPL-2.0 / HPND) are inventoried in
[THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES).

## Author

Built and maintained by [youknowfred](https://youknowfred.com).
