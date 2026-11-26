# The hippo plugin

Local, git-native agent memory: a markdown-in-git corpus with offline dense+BM25 hybrid recall,
git-drift staleness/provenance tracking, a review-gated capture path and one maintenance queue.
This directory is what Claude Code installs: the hooks, the skills, the MCP server and the
engine they all call.

> [!NOTE]
> For what hippo is and how to install it, start at the [front README](../README.md). For the
> engine's internals (recall, staleness, reconsolidation, archive), see
> [`memory/README.md`](memory/README.md).

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="../assets/readme/session-timeline-dark.svg">
    <img src="../assets/readme/session-timeline.svg" alt="One Claude Code session and where hippo acts. Session starts: SessionStart refreshes the index and gives one report. You send a prompt: UserPromptSubmit recalls into the prompt. Claude edits files: PreToolUse runs the floor write gate and PostToolUse shows a first-touch reminder. A subagent ends: SubagentStop captures a seed. Context compacts: PreCompact nudges to save durable facts first. Session ends: SessionEnd captures a seed for /hippo:tend. All session long the MCP server answers its ten tools; warm recall and the high profile's Qwen3 models run there." width="820">
  </picture>
</p>

| | Contents |
|---|---|
| **What ships** | [The parts](#the-parts) · [Hooks](#hooks) · [Skills](#skills) · [Plugin options](#plugin-options) |
| **MCP server** | [The ten tools](#the-ten-tools) · [Resources](#resources) · [When to reach for it](#when-the-agent-reaches-for-it) · [Setup from a model](#setup-from-a-model-invoked-tool) · [v1 tool names](#v1-tool-names-until-v20) |
| **Retrieval** | [Profiles and Qwen3](#retrieval-profiles-where-qwen3-runs) · [Unicode and multilingual](#unicode-and-multilingual-retrieval-ret-3) |
| **Principles** | [The agent is the memory master](#operating-principle-the-agent-is-the-memory-master) · [Why bootstrap is explicit](#design-note-why-bootstrap-is-explicit-not-automatic) |

## The parts

| Part | Where | What it is |
|---|---|---|
| 7 hooks | [`hooks/hooks.json`](hooks/hooks.json) | Bash entry points Claude Code fires on its own events |
| 9 skills | [`skills/`](skills/) | The `/hippo:*` verbs, plus 12 v1 names routing to them |
| 1 MCP server | `bin/hippo mcp` | Ten tools, one internal tool, three resources |
| 1 CLI | [`bin/hippo`](bin/hippo) | `hippo <verb>`: the same engine without Claude (`hippo help`) |
| The engine | [`memory/`](memory/README.md) | The Python package every part above calls |
| Starter packs | [`assets/packs/`](assets/packs/) | Seed memories; `core` by default, the rest opt-in |
| 6 options | [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json) | Per-machine settings Claude Code asks for ([below](#plugin-options)) |

## Hooks

| Event | Matcher | What it does | Timeout |
|---|---|---|---|
| `SessionStart` | | Refreshes the index offline, then one merged report: staleness, captures, link health, the floor | 30 s |
| `UserPromptSubmit` | | Ranks the corpus against the prompt and injects the memories that earn it | 15 s |
| `PreToolUse` | `Write\|Edit\|MultiEdit` | The write gate: warns (or, by committed policy, refuses) a write that leaves the floor over budget and worse | 10 s |
| `PostToolUse` | `Read\|Edit\|Write\|MultiEdit\|NotebookEdit` | Records the touch, and on the first touch of a file a pinned or feedback memory cites, shows that memory's one line | 10 s |
| `SubagentStop` | | Folds the subagent's work into the session's one pending capture seed | 30 s |
| `PreCompact` | | Reminds the model to save durable facts before the transcript is summarized | 10 s |
| `SessionEnd` | | Captures a seed of the session (queries, recalled memories, changed files) into the gitignored queue | 30 s |

> [!IMPORTANT]
> Every hook always exits 0, never downloads, and runs no LLM on the prompt path. A hook blocks
> nothing unless the corpus's own committed policy asks it to (the write gate's `deny`).
> Nothing a hook captures reaches the corpus until you approve it in `/hippo:tend`.

## Skills

The nine `/hippo:*` verbs (the root [README's Commands section](../README.md#commands) has the
one-line "which one do I want?" guide for recall-vs-doctor, tend-vs-dream and the rest):

| Skill | Run when |
|---|---|
| `/hippo:setup` | Once per machine: builds the shared venv and warms the offline model cache. Once per project (or a fresh clone): seeds `.claude/memory/`, the cross-machine symlink and the index; a linked git worktree needs nothing (it resolves the main checkout's corpus) |
| `/hippo:new` | Whenever the agent decides to save something to memory |
| `/hippo:recall` | Deliberately recall the corpus ("what do you remember about X"), list it by type, or get the receipt for why a memory surfaced (or didn't) for a query |
| `/hippo:tend` | The one maintenance queue: pending captures, memories whose cited code moved, contradictions, duplicates, broken baselines and links, floor overflow, rule proposals, changes waiting for re-consent; one verdict at a time (a long floor diet can be reviewed as one list and applied by its digest) |
| `/hippo:doctor` | Fast health check of the plugin's install and the corpus's wiring, plus the consent step; on request, the deep judgment-based audit of the corpus's content |
| `/hippo:dream` | The offline link pass: replay recall over each memory's self-query and surface the latent edges |
| `/hippo:review` | Review a memory diff like a pull request: what each change is, the lints on the touched files, how recall would shift |
| `/hippo:share` | Memory beyond this project: packs, promotion to the user tier, a memory as a glob-scoped `.claude/rules/` file, publishing one memory, an `AGENTS.md` export, imports from other tools (Cursor `.mdc` first) |
| `/hippo:remove` | Uninstall/offboard THIS project: removes the symlink, offers to delete index/telemetry, reports (never deletes) shared venv/cache paths |

The twelve v1 skill names (`bootstrap`, `init`, `why`, `consolidate`, `resolve`, `audit`, `pack`,
`promote`, `promote-rule`, `publish`, `export-agents`, `import`) still load until v2.0: each is a
one-line route into the verb above that absorbed it.

## Plugin options

Claude Code asks for these when the plugin is enabled and lists the switches in `/config`. Each
is per machine, and an environment variable overrides it. Hooks receive them as
`CLAUDE_PLUGIN_OPTION_<KEY>` and the MCP server through its `env`; a shell command never does.

| Option | Default | What it does |
|---|---|---|
| `calm_session_start` | off | Open each session with a short digest (integrity warnings, where you left off, one next step, a count of queued maintenance) instead of the full report. `HIPPO_ATTENTION=calm\|full` overrides it |
| `capture_llm` | off | One small-model call when a session ends, suggesting a type and description for its pending capture. Suggestions only. `HIPPO_CAPTURE_LLM` overrides it |
| `dream_contradictions` | off | A small-model check of strongly related memory pairs during a dream pass run through the MCP tool; conflicts go to the inbox, never applied. `HIPPO_DREAM_CONTRADICTIONS` overrides it |
| `dream_generative` | off | Let dream passes stage draft schema memories (flagged, self-expiring, no LLM). The generative tier is deprecated and v2.0 removes it. `HIPPO_DREAM_GENERATIVE` overrides it |
| `llm_model` | hippo's default small model | Model id for the opt-in LLM passes. `HIPPO_LLM_MODEL` overrides it |
| `llm_api_key` | none | Anthropic API key for the opt-in LLM passes, kept in your system keychain. It reaches the hooks and the MCP server only; a scheduled `hippo sleep` needs `HIPPO_LLM_API_KEY` in its own environment |

The retrieval profile is not a plugin option: it is a per-machine choice that
`hippo setup --profile` writes into `${CLAUDE_PLUGIN_DATA}/model.json`
([below](#retrieval-profiles-where-qwen3-runs)).

## MCP server: mid-turn and subagent memory (INT-2)

The plugin declares a stdio MCP server (`plugin.json` → `bin/hippo mcp` → the PLUGIN_DATA venv
python, falling back to `python3` pre-bootstrap). It closes the two gaps the once-per-prompt
recall hook can't: mid-turn retrieval (after the agent discovers what it's working on) and
subagent memory (Task turns get no `UserPromptSubmit`). It is offline (bootstrap's model download
excepted) and corpus-local, and reuses the exact hook ranking (no fork).

It is a dependency-free JSON-RPC 2.0 server (stdlib only — no `mcp` package), bounded at a 1 MiB
per-message cap (SEC-13, `HIPPO_MCP_MAX_MESSAGE_CHARS`). The hook path never imports it, so recall
keeps working with the server absent, and `/hippo:doctor`'s `mcp_launch` check confirms it starts.

### The ten tools

The v2 toolset (v1.42.0) carries the whole surface. Each tool is annotated, and `recall`,
`doctor` and `tend` return structured content to clients that negotiate protocol 2025-06-18 or
later.

| Tool | What it does | Writes |
|---|---|---|
| `recall` | Hybrid recall with graph and staleness annotations, the same engine the hook uses; abstains on an off-topic query. On the `high` profile it reranks the top 30 with Qwen3-Reranker-0.6B | no |
| `new_memory` | One per-item, agent-gated corpus write (near-duplicates reported, trust-gated); `check: true` is a dry run that writes nothing | yes |
| `inspect` | Why a memory surfaced or didn't, its linked neighbors, its decision history, its blast radius | no |
| `tend` | The maintenance queue and the capture-drain steps, one verdict at a time | yes, per item |
| `doctor` | The `/hippo:doctor` checks, audit material and the secrets scan | no |
| `setup` | Bootstrap (kick-off and poll), init, and the index build | yes, idempotent |
| `trust` | Consent status, the review, the grant and the revoke | yes |
| `share` | Packs: extract one, or plan an install or update and apply it one item at a time | yes, per item |
| `dream` | The offline link pass | yes, reversible |
| `review` | A memory diff rendered as a review packet | no |

One more tool, `recall_hook`, is internal and unfrozen: it is how the opt-in
[warm recall](../README.md#warm-recall-opt-in) hook asks this server to answer a prompt with
the model it already holds.

### Resources

| Resource | Purpose |
|---|---|
| `hippo://floor` | The always-on floor (project MEMORY.md + user/private tier) as one document — **read this at subagent start** to get the baseline a main session gets natively |
| `hippo://rules-view` | Governance files (CLAUDE.md/AGENTS.md/rules) citing memories the corpus disputes, plus rules-plane rot |
| `hippo://scorecard` | The GOV-6 one-line corpus-health rollup a lead scans before trusting the corpus |

### When the agent reaches for it

The `UserPromptSubmit` hook fires once, on the *first* prompt of a main-session turn, before the
agent knows what it's working on. Reach for the MCP tools in the two cases the hook structurally
cannot serve:

- **Mid-turn**, once the task is concrete: after grepping the codebase and discovering you're
  touching (say) the auth flow, call `recall("auth session invalidation")` — the hook's
  opening-prompt recall never saw that query. `inspect` answers "why did/didn't you surface
  that?" and reconstructs why the current approach replaced an older one.
- **In a subagent** (a `Task` turn): subagents get **no** `UserPromptSubmit`, so they start with
  zero injected memory. Read `hippo://floor` first for the baseline, then `recall(...)` for the
  task at hand. `new_memory(...)` lets a subagent persist a durable finding (trust-gated).

> [!TIP]
> In the Claude Desktop app's Code tab the skills route themselves to these tools, because the
> Bash tool there gets no plugin environment. Install is the one terminal step hippo has
> verified; everything after it (setup, consent, diagnostics, daily recall) works from either
> surface.

### Setup from a model-invoked tool

The setup, doctor and trust tools re-serve the `/hippo:*` setup flows, so a surface with **no
typed-command input can still set hippo up end to end**. The **Claude Desktop app**'s local
sessions run installed plugins' hooks, skills, and MCP servers through the same engine as the
terminal CLI. They rejected *typed* `/hippo:*` commands until 2026-10 and run them now, but their
Bash tool still gets no plugin env, so each skill routes to these tools there.

> [!WARNING]
> One consent rule differs from the terminal skill, deliberately (SEC-1): typing
> `/hippo:setup` is the user's own explicit review of an existing corpus, so the *skill* may
> trust it on re-run. A *model-invoked* init must not — it auto-trusts only a corpus it just
> **created** (whose entire content is the plugin's own starter files) and otherwise routes
> consent through the trust review→confirm, so a prompt-injected session can never silently
> trust a foreign corpus by "helpfully" running setup.

### v1 tool names (until v2.0)

The 24 v1 tool names still answer until v2.0; each says so in its description and in every
answer. [UPGRADING.md](../UPGRADING.md) maps each old permission id to its new one.

<details>
<summary>The v1 tools, by the v2 tool that absorbed them</summary>

The five core v1 tools (the frozen v1.0 surface):

| Tool | Purpose |
|---|---|
| `recall(query, k)` | Hybrid recall + graph/staleness annotations — the same engine the hook uses; abstains on an off-topic query |
| `new_memory(name, description, type, body, links, confidence)` | Per-item, agent-gated corpus write (LIF-2 dup neighbors reported; SEC-13 trust-gated) |
| `traverse(name, hops)` | Outbound (≤N-hop) + inbound + typed (supersedes/contradicts/refines) neighbors |
| `why(query, k)` | The recall receipt (GOV-5): re-runs the ranking and explains each hit — or, on abstention, the near-miss and the floor it missed |
| `decision_history(name)` | Replays the supersedes/refines lineage around a memory into a dated "chose X → refined to Y → Z superseded it" narrative |

The four v1 setup tools (INT-9..12), now behind `setup`, `doctor` and `trust`:

| Tool | Purpose |
|---|---|
| `doctor()` | The `/hippo:doctor` engine verbatim (deterministic, read-only), plus a mapping from each named fix to the tool that runs it on this surface |
| `bootstrap(action, multilingual)` | Per-machine-surface provisioning, kick-off-and-poll: `start` detaches the venv-build + ~130MB model-warm worker, `status` polls it (sentinel-last; log tail included). Bootstrap is once per plugin-data dir. In 2026-07 the terminal and the desktop app got **different** ones from the harness, so each surface bootstrapped once (on Claude Code 2.1.286 they share one). `status` names a sibling install when it detects one |
| `init()` | The mechanical project half of `/hippo:setup`: seed a fresh corpus (core pack + floor + format marker) or wire an existing one (symlink, index, CONVENTIONS backfill, `.gitignore`, private tier). Idempotent, never overwrites, never commits |
| `trust_corpus(confirm_digest)` | The SEC-1 consent flow, two-step: a review call **never trusts** — it returns the memory count, the exact description strings recall would inject (quoted as untrusted data), and a consent digest; the confirm call requires that digest, binding consent to the reviewed bytes (a corpus that changed in between refuses). Also the SEC-6 drift re-consent path |

The `/hippo:dream` generative sleep pass is likewise a verb tool — `dream(action, …)`
(pass / undo / log / deparasite / dedup_merge / generate / sweep_drafts / archive_draft /
prospective; see the dream skill for the doctrine).

The six consolidate-flow tools (INT-13), now behind `tend` — the five steps of `/hippo:tend`'s
capture drain as thin, per-item primitives, so the sleep-time drain runs from either surface too
(they were built because the desktop app's Bash tool never inherits `CLAUDE_PLUGIN_DATA`, and they
stay the desktop route). The skill remains the doctrine; deliberately **not** one monolithic
"consolidate" tool — nothing batches writes past the per-item approval gate:

| Tool | Purpose |
|---|---|
| `capture(action, path, text)` | The CAP-2 pending queue: `list` (default — highest-value first, with provenance and the queue dir), `discard` ONE processed seed, `snooze` the SessionStart nudge, `add_decision` ONE user-confirmed WHY (GRW-4, transcription never synthesis) |
| `secrets_scan(text)` | The drain's hard gate (GRW-1): lint the exact lines before ANY verbatim hunk is fenced into a committed body — any finding means scrub and re-scan, never fence |
| `reconsolidate(action, name, outcome, superseded_by)` | The LIF-1 worklist (`worklist`, watermark lane included) + the per-item verdict (`reverify`: graduate / fix / demote [+`superseded_by`] / snooze) |
| `build_index()` | Refresh the recall index + persisted link graph — full build under the freshly-bootstrapped venv when one exists, never-downgrade in-process refresh otherwise |
| `co_recall_proposals()` | GRW-2: pairs that co-recalled across many distinct sessions (floor names excluded, already-linked pairs dropped) — read-only; an approved append is a per-item agent edit + `build_index` |
| `abstention_fixtures(action, query, expected)` | The SIG-6 blind-spot loop: `draft` recurring abstained queries into the gitignored queue; `confirm` ONE judged row into the tracked eval fixture (refuses stems that don't exist) |

`new_memory` grew a `check: true` flag for the same flow — the CAP-3 dry-run (near-duplicate
routing + the proposal-time baseline, writes nothing), so the drain checks **before** it writes.

</details>

## Retrieval

### Retrieval profiles: where Qwen3 runs

A machine's retrieval profile says how much model recall spends: `floor` (keyword only), `lean`
(the default, bge-small-en-v1.5 on CPU) or `high` (Qwen3-Embedding-4B and Qwen3-Reranker-0.6B
through MLX, on an Apple Silicon Mac). Every profile runs on the machine, with no API call. In
the plugin, the profile decides which process loads which model:

| Surface | `floor` | `lean` | `high` |
|---|---|---|---|
| `UserPromptSubmit` hook (spawned per prompt) | BM25 | bge-small-en-v1.5 | bge-small-en-v1.5: a fresh process cannot afford a multi-GB load |
| Warm recall (the MCP server answering the prompt hook) | BM25 | bge-small-en-v1.5, kept loaded | Qwen3-Embedding-4B over the `high/` index, once it has caught up |
| `/hippo:recall` and the MCP `recall` tool | BM25 | bge-small-en-v1.5 | Qwen3-Embedding-4B, then Qwen3-Reranker-0.6B over the top 30 |

Whenever `high` cannot serve (not Apple Silicon, MLX absent, weights not fetched, the `high/`
index still catching up, warm recall off), the same surface serves `lean`, and
`/hippo:doctor`'s retrieval-profile line says why. Turning it on is four steps of yours,
listed in the front README's [Retrieval profiles](../README.md#retrieval-profiles-opt-in); the
engine side, with the per-space calibration, is in
[`memory/README.md`](memory/README.md#retrieval-profiles-and-the-qwen3-high-space).

### Unicode and multilingual retrieval (RET-3)

BM25 tokenization is Unicode-aware unconditionally — word tokens for Latin/Cyrillic/etc.
(case-folded, accents preserved: "café" tokenizes whole) and character bigrams for CJK
(Chinese/Japanese/Korean) runs that lack whitespace segmentation. This works out of the box for
any corpus language, no configuration needed.

The DENSE embedding model, by contrast, stays **English by default** (`bge-small-en-v1.5`) —
switching it is an explicit opt-in: `/hippo:setup`'s `--multilingual` persists a multilingual
model choice (`${CLAUDE_PLUGIN_DATA}/model.json`) and warms it. `/hippo:doctor` proactively
flags a visibly non-English corpus still served by the English model. See the setup skill's
`--multilingual` section for the full procedure and tradeoffs.

## Operating principle: the agent is the memory master

> [!IMPORTANT]
> By default, this plugin operates on the assumption that **the agent owns memory upkeep
> autonomously** — not the human. The default is seeded by the core starter pack and can be
> changed by editing one memory (below).

When staleness or curation surfaces (a signal at session start, or an explicit "run memory
maintenance" ask), the agent should run the resolution pass itself: read the flagged memory,
check it against current reality, then resolve — still-accurate → re-verify it; drifted → fix
the body, then re-verify; obsolete → archive. The agent acts, then reports; git is the
audit/revert path, since the corpus is markdown-in-git. Verification is the agent's judgment,
never a human pre-approval checkpoint, and there is deliberately no bulk "reverify everything at
once" primitive anywhere in this engine — every resolution is a single, deliberate,
individually-justified action (a blind bulk re-baseline would anchor to the mechanical backfill
touch and silence real drift; that rationale carried forward into `/hippo:doctor`'s content
audit and its no-bulk hard rule).

This is a **default assumption seeded by the core starter pack** (`assets/packs/core/claude_is_memory_master.md`),
not a hardcoded behavior — if an operator prefers to review corpus maintenance themselves before
it happens, they should say so explicitly and delete or edit that memory; absent that
instruction, the agent should act autonomously on corpus upkeep.

## Design note: why bootstrap is explicit, not automatic

See the [repo root README](../README.md#bootstrap-vs-auto-provision-design-decision) for the
full reasoning — in short: the one online step in this plugin's whole lifecycle (venv + model
warm) is deliberately never triggered from a hook, so hooks stay simple, offline, and
always-exit-0.
