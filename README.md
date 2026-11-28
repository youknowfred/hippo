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

**Local, git-native memory for Claude Code.** Your repo is the store: a corpus of small
markdown files. hippo recalls the right ones on every prompt, with models that run on your
machine, and tells you when the code a memory cites has moved. Distributed as a Claude Code
plugin.

| Git is the store | Recall is local | Staleness is semantic | Team memory is reviewed |
|---|---|---|---|
| Plain markdown in your repo. Diff it, review it, revert it. There is no opaque database beside it. | No LLM call and no network per prompt. The models run on your machine, from keyword-only up to Qwen3-Embedding-4B on Apple Silicon. | A memory is flagged when the code it cites moved, whatever its age, and the flag is on the recalled row before Claude reads it. | A memory lands the way code does: as a diff, through review. Capture is automatic and every write waits for you. |

What recall injects does take context, and `/hippo:doctor` counts it per prompt and per session.
In daily use since 2026-06 across three production corpora, the largest over 1,000 memories.

**New here?** [How hippo thinks](CONCEPTS.md) is the five-minute mental model: what a memory
is, the always-on floor against on-demand recall, the four types, why markdown in git.

> [!TIP]
> **New in v1.48: retrieval profiles.** A machine now chooses how much model recall uses. The
> opt-in `high` profile runs Qwen3-Embedding-4B and Qwen3-Reranker-0.6B through MLX on an Apple
> Silicon Mac, fully offline. See [Retrieval profiles](#retrieval-profiles-opt-in).

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/readme/recall-flow-dark.svg">
    <img src="assets/readme/recall-flow.svg" alt="How hippo recalls on every prompt. The prompt runs through a keyword lane (BM25) and a dense lane (a local embedding model), the two rankings are fused, and the result is checked: enough evidence or stay silent, only files you have reviewed, and a flag when cited code moved. The surviving memories are shown to Claude beside the prompt as quoted data." width="820">
  </picture>
</p>

| | On this page |
|---|---|
| **Start** | [Quickstart](#quickstart) · [What you get](#what-you-get) · [How a memory lives](#how-a-memory-lives) |
| **Recall** | [Retrieval profiles and Qwen3](#retrieval-profiles-opt-in) · [Warm recall](#warm-recall-opt-in) |
| **Curate** | [Automatic capture](#automatic-capture--memory-that-writes-itself-gated-by-your-review) · [Commands](#commands) · [Settings and gates](#settings-and-gates) |
| **Fit** | [Compared to other tools](#compared-to-other-memory-tools) · [Native memory and worktrees](#hippo-and-claude-codes-native-memory) · [Linear and other trackers](#hippo-and-linear-or-any-issue-tracker) |
| **Reference** | [Troubleshooting](#troubleshooting) · [Support matrix](#support-matrix) · [Repo layout](#repo-layout) · [All the docs](#all-the-docs) |

## Quickstart

> [!NOTE]
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
     model (the ONE online step in the plugin's whole lifecycle). A Mac with memory to spare
     can later opt in to a much larger one ([Retrieval profiles](#retrieval-profiles-opt-in)).
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

**Recall**

- **Recall on every prompt.** A hook ranks the corpus against your prompt (dense + BM25,
  fused) and injects the memories that earn it: up to ten rows when the match is strong,
  fewer when it is not, none when nothing matches. No model call, no network.
- **Your choice of local model.** Keyword-only, a small CPU embedder (the default), or
  Qwen3-Embedding-4B with a Qwen3 reranker on an Apple Silicon Mac. All three run on your
  machine ([Retrieval profiles](#retrieval-profiles-opt-in)).
- **A reminder at the file.** The first time a session touches a file that a pinned or
  feedback memory cites, that memory's one-line description is shown once, at the moment it
  applies (`HIPPO_DISABLE=jit` turns it off).
- **An always-loaded floor.** `MEMORY.md` holds pointers to the few memories worth knowing in
  every session. It reaches Claude through Claude Code's own memory, so hippo adds no second
  always-on channel ([below](#hippo-and-claude-codes-native-memory)).
- **Mid-turn and subagent memory.** The plugin's MCP server gives the model the same recall
  as a tool, for the moments the per-prompt hook cannot cover.

**Upkeep**

- **Staleness by git drift.** A memory records the files it cites and the commit it was
  verified at. When those files move, the memory is flagged on the recalled row and queued
  for a re-check, whatever its age.
- **Capture behind a review.** A session's end leaves a seed of what happened in a queue;
  nothing is written to the corpus until you approve it
  ([below](#automatic-capture--memory-that-writes-itself-gated-by-your-review)).
- **One maintenance queue.** `/hippo:tend` lists every kind of upkeep and works it one
  verdict at a time.

**Trust and team**

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
  ([Linear and others](#hippo-and-linear-or-any-issue-tracker)).
- **A terminal door.** `hippo <verb>` runs the same engine without Claude (`hippo help`),
  which is what CI and scheduled maintenance use.

## How a memory lives

```mermaid
flowchart LR
    S["A session ends"] -->|"a seed of what happened"| Q[("Pending queue<br/>gitignored")]
    N["'remember this: …'"] -->|"/hippo:new"| C
    Q -->|"/hippo:tend<br/>you approve each one"| C[("Corpus<br/>markdown in git")]
    C -->|"pull request"| T["Teammates review it<br/>like code"]
    C -->|"indexed at session start"| R["Recalled when a<br/>prompt matches"]
    G["Cited code changes"] -->|"git drift"| D["Flagged stale,<br/>queued for a re-check"]
    D -->|"/hippo:tend<br/>graduate, fix or demote"| C
```

A memory enters the corpus two ways: you say *"remember this"*, or a session's end leaves a
seed that you approve later. Either way it is a markdown file in git. From there it is recalled
when a prompt matches it, reviewed by teammates like any other diff, and re-checked when the
files it cites change. Nothing in this loop writes to the corpus without your yes.

### Why it's called hippo

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

## Retrieval profiles (opt-in)

Every model hippo uses for recall runs on your machine. A **retrieval profile** is how much
model a machine spends on it: none, a small CPU embedder, or, on an Apple Silicon Mac,
**Qwen3-Embedding-4B and Qwen3-Reranker-0.6B through MLX**. No API key, no network once the
weights are on disk, and nothing about a memory or how it is stored changes with the profile.
A machine is on `lean` until you say otherwise.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/readme/profiles-dark.svg">
    <img src="assets/readme/profiles.svg" alt="The three retrieval profiles as rising steps. floor: keyword recall (BM25), no model, any machine. lean, the default: bge-small-en-v1.5, 384 dimensions, ONNX on CPU, about 130 MB. high, opt-in: Qwen3-Embedding-4B at 2,560 dimensions, 8-bit through MLX, with Qwen3-Reranker-0.6B over the top 30 candidates on deliberate recall, on Apple Silicon through warm recall, about 5 GB on disk and 4.1 GB in memory. A profile that cannot serve falls back to the one below it." width="820">
  </picture>
</p>

| Profile | Dense model | Rerank | Runs on |
|---|---|---|---|
| `floor` | none: keyword (BM25) only | none | anything |
| `lean` (default) | bge-small-en-v1.5 (384-dim, ONNX) | off on the hot path | anything Python runs on |
| `high` | Qwen3-Embedding-4B through MLX | off on warm recall (Qwen3-Reranker-0.6B with `HIPPO_RERANK=1`); Qwen3-Reranker-0.6B over the top 30 candidates on `/hippo:recall` and the MCP `recall` tool | Apple Silicon Macs, through [warm recall](#warm-recall-opt-in) |

### Turning `high` on

Four steps, each one yours to run:

1. **Install the MLX extra** into hippo's venv: `pip install -r requirements-high.txt`. The
   full command is at the top of [plugin/requirements-high.txt](plugin/requirements-high.txt).
2. **Fetch the weights**: `hippo setup --profile high --fetch --yes` (about 5 GB, pinned to
   one revision).
3. **Write the profile**: `hippo setup --profile high --yes`.
4. **Turn on [warm recall](#warm-recall-opt-in)**: `hippo setup --warm --yes`. The session's
   warm server is the only path that serves `high`.

Then check it: `hippo setup --profile` prints the active profile, where it was set and what it
serves here, and `/hippo:doctor`'s retrieval-profile line names the reason whenever `high` is
not serving. Every command previews when run without `--yes`:

```
hippo setup --profile                      # the active profile, where it was set, and what it serves here
hippo setup --profile high                 # preview; --yes writes it
hippo setup --profile high --fetch         # preview the downloads; --yes fetches them (about 5 GB)
hippo setup --profile --off --yes          # back to the default
```

`HIPPO_RETRIEVAL_PROFILE` overrides the saved choice for one process.

**The offer.** `hippo setup --profile` and `/hippo:doctor` mention `high` only on an arm64 Mac
with at least 64 GiB of memory. It is an offer and nothing more: no profile is ever changed for
you, and nothing downloads until you run the fetch with `--yes`.

### What `high` buys, measured

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/readme/profile-bench-dark.svg">
    <img src="assets/readme/profile-bench.svg" alt="Right memory in the top 10, out of 347 held-out questions over three corpora. lean in v1.48: 286, MRR 0.67. lean in v1.49: 302, MRR 0.68. high in v1.48: 303, MRR 0.71. high in v1.49, with whole-memory rows: 336, MRR 0.78." width="820">
  </picture>
</p>

On 347 held-out questions over three corpora (hippo's own, and two private project corpora of
113 and 1,070 memories; each question written from one memory, with no retriever in the loop),
`high` finds the right memory in the top 10 for 336 and `lean` for 302. That is 9.8 points, and
between 6.6 and 13.3 at 95% confidence. The gap is on questions that paraphrase the memory: when
a question uses the memory's own words, every setup tried finds 96 to 99 of 100.

In v1.48 the two read 303 and 286. Two changes moved them:

- **`high` now embeds each whole memory**, where it embedded the memory's one-line description
  (the body sections are indexed beside it on both profiles, as before). This was the largest
  single lever measured. `lean` keeps description rows: whole rows help its plain recall too, but
  not what it serves per prompt, and they would triple its index build on every machine.
- **The diversity re-cut was rescaled, on both profiles.** After ranking, hippo re-cuts the top
  rows so near-duplicates do not crowd out other memories. Its relevance term was on a far smaller
  scale than its diversity term, so it pushed right answers out: 16 of 347 on `lean`, 15 on
  `high`. Rescaled, it loses none that the set can resolve and still trims duplicate rows.

In the per-prompt hook, over 539 questions on five corpora, `high` puts the right memory on a
rendered line for 369 and `lean` for 324. It injects on about as many prompts as `lean` (225 and
219 of two corpora's 300 replayed prompts, against 227 and 211), and puts more rows in each block:
8.7 against 5.4. Off-topic prompts are turned away about equally; `high` is behind on technical
questions that sit next to a corpus's subject (8 of 12 turned away against 11 on hippo's corpus).

`/hippo:recall` and the MCP `recall` tool on `high` still rerank the top 30 candidates. That was
measured before these changes, and on whole-memory rows it added nothing the set could resolve,
for about a second per call.

The numbers, their intervals and what was not measured are in
[planning/HCR-9.release.evidence.2026-11-27.json](planning/HCR-9.release.evidence.2026-11-27.json)
and [planning/HCR-9.calibration.evidence.2026-11-27.json](planning/HCR-9.calibration.evidence.2026-11-27.json);
the question set's method is in
[planning/HCR-8.evidence.2026-11-26.json](planning/HCR-8.evidence.2026-11-26.json). One machine,
three to five corpora. To compare the profiles on your own corpus, run `python -m bench.profile_ab`
(the command is at the top of [bench/profile_ab.py](bench/profile_ab.py)).

### How `high` behaves

**What `high` changes, and what it does not.** Only the session's warm server loads the big
models. It keeps a second index, `high/`, beside the usual one and serves from it once that index
has caught up. The spawned recall hook is untouched: on every profile it serves the `lean` index
with the `lean` model, inside the same budget as before. Ranking, filtering, trust checks and the
injection budget are the same code on all three profiles.

**When `high` cannot serve, recall serves `lean`.** That covers a machine that is not an Apple
Silicon Mac, MLX not installed, weights not fetched, a model that failed to load, a `high/` index
still catching up after the corpus changed, and warm recall being off. A rerank that runs past its
time limit keeps the order it was given. None of these raises or blocks a prompt, recall never
downloads, and `/hippo:doctor`'s retrieval-profile line says which of them applies.

**Cost.** No money and no network once the weights are on disk. Memory is the cost: each session's
warm server holds its own copy of the embedder (4.1 GB of unified memory, measured), plus the
warm reranker (0.6 GB) only if you turn it on with `HIPPO_RERANK=1`. A process that serves
`/hippo:recall` or the MCP `recall` tool loads the same reranker the first time one is asked.
The embedder loads in about 2 seconds from a warm disk cache (about 5 seconds for the first load
after a reboot) and the reranker in under half a second. While a warm server builds or
refreshes its `high/` index it holds 2 to 2.5 GB more (6.0 to 6.5 GB, peaking at 6.8, measured
on a 1,070-memory corpus), and MLX's cache of freed buffers is capped at 512 MB
(`HIPPO_MLX_CACHE_MB`); uncapped, that cache grew by gigabytes during a build. A first build
costs about a second per memory. One session builds a project's index at a time, in passes that
are kept, so closing a session mid-build loses little.

**The 8B.** `HIPPO_HIGH_EMBED_MODEL=Qwen/Qwen3-Embedding-8B` (or `high_embed_model` in the
plugin's `model.json`) selects Qwen3-Embedding-8B instead; set it before the fetch step so the
right weights come down (7.8 GB). It holds 7.8 GB per warm server and recalls about half as
fast. On hippo's corpus it finds more in plain recall (34 of 38) and no more than the 4B on the
paths `high` serves, which is why the 4B is the default
([planning/HCR-6.8b.evidence.2026-11-25.json](planning/HCR-6.8b.evidence.2026-11-25.json)).

**Why not a retrieval platform.** hippo has looked twice at adopting TrustGraph and said no both
times: it brings a message bus, three databases and a monitoring stack to run, and it moves the
source of truth off markdown in git. The ideas worth having (hybrid recall, provenance, typed
edges, graph expansion) are built in. The reasoning, and the models that were considered for
`high`, are in [planning/EXPLORATIONS.retrieval.md](planning/EXPLORATIONS.retrieval.md).

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
| **Runs** | local, offline; your choice of local model, up to Qwen3-Embedding-4B on Apple Silicon | local | local (memsearch embeds Milvus Lite by default) | in-model + local files | cloud; self-hosting on paid tiers |

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
session. The one online step in hippo's default lifecycle is `/hippo:setup`
downloading the embedding model once (the opt-in `high` profile adds one fetch of its own, which you
run by hand); after that, recall is fully offline. For a privacy- or
cost-sensitive team, "memory that calls no model per prompt and never phones home" is a hard
requirement a hosted-by-default or LLM-in-the-loop memory can't meet without extra self-hosting work.

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
  `tend` MCP tool on Desktop. A long floor diet can go as one list instead: `hippo tend diet`
  shows every line it would move out, and one verdict applies exactly the lines it showed.
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
  ([hippo and Linear](#hippo-and-linear-or-any-issue-tracker)). Read-only like `--for-diff`.
  Without a `ledgers` declaration it says so and exits 0; an id that matches no declared `id_re` is refused (exit 2) with the pattern named.

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

## Settings and gates

Settings live in three places, and the first one that answers wins: an environment variable,
hippo's plugin options (your machine: the calm SessionStart switch, the LLM opt-ins and key),
and `.claude/memory/hippo.json` (the corpus's committed policy, shared with your team).
[SETTINGS.md](SETTINGS.md) is the full reference. What a corpus can ask for there:

- **Recall lanes and journals**: keep long work journals out of the prompt hook's rows unless
  the prompt names them ([recall lanes](SETTINGS.md#recall-lanes),
  [folded sections](SETTINGS.md#folded-sections)).
- **Tracker statuses**: a committed tracker mirror whose statuses recall renders
  ([above](#hippo-and-linear-or-any-issue-tracker)).
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

### A git home for the user tier (opt-in)

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

### Keeping CONVENTIONS.md current

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

## hippo and Linear (or any issue tracker)

hippo does not connect to Linear. There is no integration to install and no API key to hold,
and hippo never reads or writes an issue. The two work together through the repo: **Linear
keeps the state of the work, hippo keeps what was learned doing it, and a file in the repo
carries the one to the other.**

```
 Linear ──(your export script, on your schedule)──▶ a mirror file inside the repo
                                                            │
                                    read at SessionStart and `hippo build-index`
                                                            ▼
 a memory cites ENG-901 ───────▶ recalled as  "… · ENG-901 In Progress (snapshot 2026-08-26)"
```

**Who holds what.** The split is the whole design:

| In Linear | In hippo |
|---|---|
| Status, assignee, priority, the linked pull request | Why the approach was chosen, what broke, what you corrected |
| Changes many times a day | Changes when the lesson changes |
| One issue per piece of work | One memory per durable fact, citing the issues it came from |

So a memory about tracked work carries the issue id and the lesson, and nothing a tracker
already records: no status word, no PR number, no commit SHA. A status typed into a memory is
wrong the day the issue moves, and nothing in the repo changes to say so.

<details>
<summary><b>How a status reaches the session</b>: three steps, the first one yours</summary>

1. **Keep a mirror of the tracker in the repo.** hippo ships no exporter. A script of your own
   (Linear's GraphQL API in a scheduled job is the usual shape) writes a text file with one
   entry per issue:

   ```yaml
   issues:
     - id: ENG-901
       title: Rotate the signing key
       state: In Progress
   ```

   hippo reads two lines of each entry, the id and the state, and nothing else in the file.
   The file can be committed, so every clone and CI sees the same statuses, as fresh as its
   last commit. Or it can be an ignored, machine-local file that a job on your machine
   rewrites, so statuses stay current with no commit per refresh. Declare both and the first
   that knows an id answers, with the committed one as the fallback where the local one is
   absent.
2. **Declare it** in `.claude/memory/hippo.json`:

   ```json
   {"ledgers": [{"name": "linear", "path": "LINEAR-SNAPSHOT.yaml", "id_re": "\\bENG-\\d+\\b",
                 "entry_key": "id", "status_key": "state"}]}
   ```

3. **Recall does the rest.** From the next session, a recalled memory whose name or
   description cites an id ends with that id's status and the mirror's date, in the prompt
   hook's rows, `hippo recall` and `hippo inspect`:

   ```
   • signing-key-rotation — ENG-901 signing key rotation: the mobile client caches the old
     key for 24h · ENG-901 In Progress (snapshot 2026-08-26)
   ```

   And you can ask by issue, from a terminal or in CI:

   ```
   $ hippo recall --for-issue ENG-901
   ENG-901 In Progress (snapshot 2026-08-26) — 1 memory(ies) cite it:
   - signing-key-rotation [project] — ENG-901 signing key rotation: …  (cites it in: description, line 8)
   ```

The date is the mirror's last commit (for a file git does not track, the day it was last
written), so a status is as fresh as your last refresh and says so. For live state a session still asks Linear (its MCP server, or your own script); what
hippo adds is that the session knows which issues bear on the prompt, and what was learned
on them, before it asks.

</details>

**How status stays out of memory.** A corpus can make the split a rule rather than a habit.
`floor_lint.banned_re` in `hippo.json` names the words the floor may not carry:

```json
{"floor_lint": {"banned_re": "MERGED|SHIPPED|In Progress|#\\d{3,}"}}
```

The write gate then warns when a write adds a matching line to the floor or a matching word
to a memory body, `hippo lint-floor` lists what is already there, and the commit gate fails
a commit that makes the floor worse ([the gates](SETTINGS.md#the-write-gate)).

**What hippo does not do.** It makes no network call to a tracker, holds no tracker
credential, and writes nothing back: no comment, no status change, no issue. It does not yet
re-queue a memory for review when an issue it cites is closed; that is planned. And
`--for-issue` is a terminal command, with no MCP tool input yet.

**Other trackers.** Nothing above is specific to Linear except the example. Jira, GitHub
Issues or a hand-kept file work the same way once a mirror with an id line and a status line
per entry is in the repo; up to four ledgers can be declared. The declaration's limits, the
cache and the journal rule are in [SETTINGS.md](SETTINGS.md#tracker-statuses-at-read-time).

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

<details>
<summary><b>Escape hatches, where the derived dirs live, and nested repos</b></summary>

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

</details>

## Troubleshooting

**When in doubt, run `/hippo:doctor`.** It checks the native symlink, the recall index,
corpus trust and drift, the corpus format version, link density and unfilled templates, and
prints the exact repair command for whatever it finds.

<details>
<summary><code>/hippo:setup</code> (or any <code>/hippo:*</code> command) "isn't a recognized command" / "only works in the Claude Code terminal."</summary>

Desktop app versions before 2026-10 rejected typed plugin commands;
current ones run them (see [`PLATFORM.md`](PLATFORM.md)). Either way the **desktop app**'s
local sessions run hippo's hook, skills, and MCP tools, so where typed commands are still
rejected, just ask in plain words (*"set up hippo memory here"*, *"run hippo doctor"*,
*"tend memory"*): the agent drives the same flows through the `setup`, `doctor`, `trust`
and `tend` MCP tools, per item, with the same approval gates. **Cloud / remote** sessions
are the real limit: no local filesystem, no hippo. (Nothing's wrong with your install.)

</details>

<details>
<summary>Recall is BM25-only in the desktop app even though I already ran setup</summary>

The machine half
of setup is once per plugin-data dir, and some Claude Code versions gave the terminal CLI and
the desktop app **different plugin-data dirs** (`…/data/hippo-<marketplace>` vs
`…/data/hippo-inline`, seen in 2026-07; on 2.1.286 both use the first), so the terminal's
venv and model cache weren't where a desktop session looked. Ask the desktop session to run
hippo setup once: its status output names the sibling install so you can see exactly this
situation, and `/hippo:doctor` (or the `doctor` tool) flags it too.

</details>

<details>
<summary>Some recalls run BM25-only and I don't know why</summary>

The prompt hook records a
reason beside every BM25-only recall (the model not warmed, an index without vectors, another
model, the dense timeout, an error, dense turned off, a tier without vectors, or simply no
memory clearing the dense floor), and `/hippo:doctor`'s BM25-only line names the most common
one over 30 days with what to do about it.

</details>

<details>
<summary>Recall comes back empty</summary>

Almost always one of three things: **(a) setup's machine half
never ran** on this machine, so recall is BM25-only until `/hippo:setup` finishes;
**(b) the corpus isn't trusted yet**: a freshly cloned or pulled corpus injects *nothing*
until you review it. `/hippo:doctor` walks the consent step (`hippo trust review`, then
`hippo trust grant`), and its withheld line counts memories by how they arrived. A file
that changed outside hippo after you consented is withheld the same way until you review it;
**(c) `user_role.md` is still the `<FILL-ME>` template**, so the only thing to recall is
placeholder text. Edit it with your real role and context.

</details>

<details>
<summary>A memory I wrote never resurfaces</summary>

Recall is on-demand and ranked, not always-on: only
the always-load *floor* (the `user`/`feedback` pointers) is loaded in every session;
everything else surfaces when a prompt actually matches it. Phrase your question closer to
the memory's own wording, or confirm it's indexed with `/hippo:doctor`.

</details>

<details>
<summary>Recall surfaces something off-topic</summary>

Since v1.41.0, recall injects only when the dense
and lexical lanes agree on a memory, or when one of them is strong on its own (a very close
meaning, or a description sharing several of the prompt's terms). A prompt that shares one
coincidental word with a memory abstains. The gate needs the dense model, so on a BM25-only
install (setup's machine half not run, or the model cache cold) any shared token still admits.
Ask `/hippo:recall` why a memory surfaced for the prompt to see which lane matched it;
`HIPPO_DISABLE=abstain-gate` turns the gate off. `/hippo:doctor` reports the measured
per-corpus rate when you supply an off-topic fixture.

</details>

<details>
<summary>Recall shows fewer memories per prompt than it used to</summary>

The prompt hook used to serve
ten rows whenever it served any. Now the count follows the evidence. When the prompt's best
match clears the dense floor by a wide margin, up to ten rows are served as before.
Otherwise at most five are, and each has to have evidence of its own: a cosine close to the
prompt's best, a place in the top ten of both the dense and a lexical lane, or a description
that shares several of the prompt's terms. The best-ranked row is always served. A row cut
this way is not named on the overflow line; `hippo recall` and the recall tool are
unchanged and still return the full ranking. The rule needs the dense model, so a BM25-only
install serves as before. `HIPPO_SIGNAL_COUNT=0` turns it off, and `/hippo:doctor` reports
the median rows per injecting prompt.

</details>

<details>
<summary>The recalled rows in my prompt look shorter than <code>hippo recall</code> shows them</summary>

In the prompt
hook only, the first row keeps its description as written (up to 220 characters) and each
later row's description is cut at a word boundary at 90. The "already in floor" line names
its three best-ranked memories instead of five, the "already surfaced this thread" line
gives its count alone, and a session's second and later blocks carry a shorter header that
still says the lines are quoted data and to read the file before relying on it. Which rows
are served, their order and their names do not change, and a row that does not fit is
still named on the overflow line. `hippo recall` and the recall tool render as before.
`HIPPO_HOOK_COMPACT=0` restores the longer block.

</details>

<details>
<summary>I'm in a git worktree and doctor / recall / capture seem to be looking at the wrong corpus</summary>

They aren't, since v1.34.0: a linked worktree resolves the main checkout's live corpus, and
the `resolved corpus:` doctor line says which tree it used. If that line reads `tree: LINKED
worktree …`, the main checkout has no `.claude/memory/` (a branch-only corpus — expected). If
it reads `tree: OVERRIDE via HIPPO_CORPUS_ROOT=…`, an env var is pinning it. A `worktree
copies:` warning means the worktree still carries a dead `.claude/.memory-pending/` queue from
before v1.34.0 — nothing drains it; delete the copy or retire the worktree. An `APPLY REFUSED
— corpus untrusted` from a worktree on a repo you already trusted was the same bug (trust rows
key on the repo root) and no longer happens; do not "fix" it by trusting the worktree.

</details>

<details>
<summary>"Not a git repository" / staleness looks inactive</summary>

Outside a git repo hippo runs in a
degraded mode: recall, indexing, links, and the floor all work, but staleness tracking and
provenance backfill need git — `git init` and commit to activate them.

</details>

## Support matrix

| Platform | Status |
|---|---|
| macOS | **Fully supported** — the primary development platform; CI runs the full suite on macOS |
| Linux | **Fully supported** — CI runs the full suite on Ubuntu. Without `CLAUDE_PLUGIN_DATA`, the fallback cache dir is XDG-aware: `${XDG_CACHE_HOME:-~/.cache}/hippo-memory` ([planning/ROADMAP.yaml](planning/ROADMAP.yaml), OSP-2) |
| Windows | **Out of scope** — a decision, not an omission ([planning/ROADMAP.yaml](planning/ROADMAP.yaml), decision OQ-2 + non_goals): the hooks are bash and the engine is untested there. Revisit only on concrete adoption evidence |

**Claude Code 2.1.269 or newer.** That is the first version that sets plugin options from
`/config` (the attention setting); `/hippo:doctor` prints the running version against this
floor, and SessionStart names an older one. CI runs the full suite on Python 3.10 and 3.12 and
a dependency-install lane on 3.11, 3.13 and 3.14. Setup's machine half runs once per machine;
its project half runs once per project.

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
planning/                         # roadmaps, exploration notes and design specs (planning/README.md)
bench/                            # the reproducible recall benchmark (bench/run.sh)
demo/                             # git_drift.sh: staleness in five seconds
assets/                           # the logo, and the README's diagrams with their generator
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
an implicit SessionStart auto-provision. Until bootstrap runs, the SessionStart hook names the
next step instead of staying silent: in the first session of each day, in any repo, with a
one-line command that dismisses it on this machine.

<details>
<summary><b>The reasoning</b></summary>

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

</details>

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
