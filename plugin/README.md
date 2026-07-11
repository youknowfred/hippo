# memory (plugin)

Local, git-native agent memory: a markdown-in-git corpus with offline dense+BM25 hybrid recall,
git-drift staleness/provenance tracking, and a self-audit skill. See
[`memory/README.md`](memory/README.md) for the full engine documentation (recall, staleness,
reconsolidation, archive internals).

## Skills

All 15 `/hippo:*` commands (the root [README's Commands section](../README.md#commands) has the
one-line "which one do I want?" guide for recall-vs-doctor, doctor-vs-audit, consolidate-vs-audit):

| Skill | Run when |
|---|---|
| `/hippo:bootstrap` | Once per machine — builds the shared venv + warms the offline model cache |
| `/hippo:init` | Once per new project (or a fresh clone/worktree) — seeds `.claude/memory/` + the cross-machine symlink + index |
| `/hippo:new` | Whenever the agent decides to save something to memory |
| `/hippo:recall` | Deliberately recall the corpus — "what do you remember about X", or list it by type |
| `/hippo:why` | The glass-box recall receipt — why a memory surfaced (or didn't) for a query |
| `/hippo:doctor` | Fast health check — is the plugin's own install/environment working |
| `/hippo:audit` | Deep, judgment-based self-audit of the corpus's content — staleness, drift, archive candidates |
| `/hippo:consolidate` | Sleep-time drain — approve pending captures, work the reconsolidation worklist, refresh the graph |
| `/hippo:resolve` | Drain the contradiction inbox — a per-item verdict on each unresolved `contradicts` pair |
| `/hippo:promote` | Lift one proven-portable memory into the machine-local user (or repo private) tier with an origin stamp |
| `/hippo:promote-rule` | Promote one reinforced procedural memory into a glob-scoped `.claude/rules/` file |
| `/hippo:pack` | Share or adopt memory packs — extract a portable pack, or install one per-item on the trust spine |
| `/hippo:export-agents` | Render the memory floor as a proposed `AGENTS.md` diff for the cross-tool rule plane |
| `/hippo:import` | Migration on-ramp — import other tools' rules/notes (Cursor `.mdc` first) into deduped hippo memories |
| `/hippo:remove` | Uninstall/offboard THIS project — removes the symlink, offers to delete index/telemetry, reports (never deletes) shared venv/cache paths |

## MCP server — mid-turn & subagent memory (INT-2)

The plugin declares a stdio MCP server (`plugin.json` → `bin/hippo mcp` → the PLUGIN_DATA venv
python, falling back to `python3` pre-bootstrap). It closes the two gaps the once-per-prompt
recall hook can't: mid-turn retrieval (after the agent discovers what it's working on) and
subagent memory (Task turns get no `UserPromptSubmit`). Five tools + three resources, offline and
corpus-local, reusing the exact hook ranking (no fork):

| Tool | Purpose |
|---|---|
| `recall(query, k)` | Hybrid recall + graph/staleness annotations — the same engine the hook uses; abstains on an off-topic query |
| `new_memory(name, description, type, body, links, confidence)` | Per-item, agent-gated corpus write (LIF-2 dup neighbors reported; SEC-13 trust-gated) |
| `traverse(name, hops)` | Outbound (≤N-hop) + inbound + typed (supersedes/contradicts/refines) neighbors |
| `why(query, k)` | The recall receipt (GOV-5): re-runs the ranking and explains each hit — or, on abstention, the near-miss and the floor it missed |
| `decision_history(name)` | Replays the supersedes/refines lineage around a memory into a dated "chose X → refined to Y → Z superseded it" narrative |

| Resource | Purpose |
|---|---|
| `hippo://floor` | The always-on floor (project MEMORY.md + user/private tier) as one document — **read this at subagent start** to get the baseline a main session gets natively |
| `hippo://rules-view` | Governance files (CLAUDE.md/AGENTS.md/rules) citing memories the corpus disputes, plus rules-plane rot |
| `hippo://scorecard` | The GOV-6 one-line corpus-health rollup a lead scans before trusting the corpus |

**When the agent reaches for it (discoverability).** The `UserPromptSubmit` hook fires once, on
the *first* prompt of a main-session turn, before the agent knows what it's working on. Reach for
the MCP tools in the two cases the hook structurally cannot serve:

- **Mid-turn**, once the task is concrete: after grepping the codebase and discovering you're
  touching (say) the auth flow, call `recall("auth session invalidation")` — the hook's
  opening-prompt recall never saw that query. `why(...)` answers "why did/didn't you surface
  that?", and `decision_history(name)` reconstructs why the current approach replaced an older one.
- **In a subagent** (a `Task` turn): subagents get **no** `UserPromptSubmit`, so they start with
  zero injected memory. Read `hippo://floor` first for the baseline, then `recall(...)` for the
  task at hand. `new_memory(...)` lets a subagent persist a durable finding (trust-gated).

It is a dependency-free JSON-RPC 2.0 server (stdlib only — no `mcp` package), bounded at a 1 MiB
per-message cap (SEC-13, `HIPPO_MCP_MAX_MESSAGE_CHARS`). The hook path never imports it, so recall
keeps working with the server absent, and `/hippo:doctor`'s `mcp_launch` check confirms it starts.

### Unicode and multilingual retrieval (RET-3)

BM25 tokenization is Unicode-aware unconditionally — word tokens for Latin/Cyrillic/etc.
(case-folded, accents preserved: "café" tokenizes whole) and character bigrams for CJK
(Chinese/Japanese/Korean) runs that lack whitespace segmentation. This works out of the box for
any corpus language, no configuration needed.

The DENSE embedding model, by contrast, stays **English by default** (`bge-small-en-v1.5`) —
switching it is an explicit opt-in: `/hippo:bootstrap --multilingual` persists a multilingual
model choice (`${CLAUDE_PLUGIN_DATA}/model.json`) and warms it. `/hippo:doctor` proactively
flags a visibly non-English corpus still served by the English model. See the bootstrap skill's
`--multilingual` section for the full procedure and tradeoffs.

## Operating principle: the agent is the memory master

By default, this plugin operates on the assumption that **the agent owns memory upkeep
autonomously** — not the human. When staleness or curation surfaces (a signal at session start,
or an explicit "run memory maintenance" ask), the agent should run the resolution pass itself:
read the flagged memory, check it against current reality, then resolve — still-accurate →
re-verify it; drifted → fix the body, then re-verify; obsolete → archive. The agent acts, then
reports; git is the audit/revert path, since the corpus is markdown-in-git. Verification is the
agent's judgment, never a human pre-approval checkpoint, and there is deliberately no bulk
"reverify everything at once" primitive anywhere in this engine — every resolution is a single,
deliberate, individually-justified action (a blind bulk re-baseline would anchor to the
mechanical backfill touch and silence real drift; that rationale carried forward into
`/hippo:audit`'s no-bulk hard rule).

This is a **default assumption seeded by the core starter pack** (`assets/packs/core/claude_is_memory_master.md`),
not a hardcoded behavior — if an operator prefers to review corpus maintenance themselves before
it happens, they should say so explicitly and delete or edit that memory; absent that
instruction, the agent should act autonomously on corpus upkeep.

## Design note: why bootstrap is explicit, not automatic

See the [repo root README](../README.md#bootstrap-vs-auto-provision-design-decision) for the
full reasoning — in short: the one online step in this plugin's whole lifecycle (venv + model
warm) is deliberately never triggered from a hook, so hooks stay simple, offline, and
always-exit-0.
