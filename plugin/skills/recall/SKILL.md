---
description: Deliberately recall what memory knows — answer "what do you remember about X", "what do you know here", or "why was that injected", and list the corpus by type. Wraps hybrid recall + the link graph into a human-readable listing (name, type, staleness, inbound/outbound links). Triggers include "what do you remember about", "what do you know here", "recall", "/hippo:recall". The read verb between /hippo:new (write) and /hippo:audit (maintenance); not plumbing (that's /hippo:doctor).
---

# /hippo:recall — the read-side verb

The recall HOOK is invisible by design: it fires once per user prompt and silently injects
matches. This skill is the DELIBERATE read entry point for the questions the hook can't answer
— "what do you remember about deploys", "list what you know here", "why did that get injected".

It reuses the exact same engine the hook does (`memory.recall` → the same fusion, relevance
floor, knee cutoff, 1-hop graph expansion, and salience) — it never forks the ranking, so what
you see here is what the hook would inject. On top of that it shows each match's **type**, a
**staleness flag**, and its **inbound/outbound graph neighbors**, so the answer is browsable,
not just a raw injection block.

## Surface routing — decide first, then act silently

- **On Claude Desktop** (you have the `⌨ Surface note` in your context, or `CLAUDE_CODE_ENTRYPOINT` is `claude-desktop`): drive this verb through the `recall` MCP tool for query-recall — memory lineage → the `decision_history` tool, graph hops → the `traverse` tool. The `--list-by-type` corpus map and `--all-projects` cross-project modes below have no tool form yet — say they are terminal-only plainly rather than improvising one. Skip the bash preflight and the shell blocks below; those run only in a terminal. Call the tool with no preamble — don't explain that typed commands or the shell flow don't work on this surface, or why you're reaching for a tool instead of bash. That surface-plumbing narration is exactly the repeated noise this routing removes.
- **In a terminal Claude Code session**: run the bash flow below, guard first.

## Preflight (shared across all hippo skills)

```bash
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ CLAUDE_PLUGIN_DATA is unset/empty in this shell. On Claude Desktop this is expected — take the MCP-tool route in 'Surface routing' above instead of this bash flow. In a genuine terminal Claude Code session it means Claude Code is likely too old for hippo's self-provisioning — update it, or export CLAUDE_PLUGIN_DATA to a writable dir (e.g. ~/.claude/hippo-data) and re-run."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
hippo_resolve_py
```

## Usage

Query the corpus (natural language — phrase it as the underlying question, the way the recall
index matches):

```
"$PY" -m memory.recall_view "<what to recall — e.g. how do we deploy the web service>" [-k <max matches>]
```

List everything this project knows, grouped by type (a map of the corpus — no query):

```
"$PY" -m memory.recall_view --list-by-type
```

Replay how a decision evolved (RCH-3 — walks the authored supersedes/refines chain around
a memory into an ordered narrative, with retirement boundaries and contradiction branch
points; answers "why did we decide X" / "what replaced Y"):

```
"$PY" -m memory.recall_view --history "<memory-name>"
```

Search EVERY registered project on this machine, not just this one (RCH-4 — explicit
command only, never the hook; each source passes the SEC-1 trust gate at query time,
cross-project hits are labeled `from <repo>`, and a trailer names every corpus searched
or skipped):

```
"$PY" -m memory.recall_view --all-projects "<what to recall>"
```

## Reading the output

Each match prints as:

```
  • <memory-name>  [<type> · relevance <score> · via 1-hop link · ⚠ stale — verify before relying]
      <the memory's one-line description>
      → links to: <outbound wikilink targets>
      ← linked from: <inbound referrers>
```

- **`<type>`** — `user` / `feedback` / `project` / `reference` (the floor taxonomy).
- **`relevance <score>`** — the true fused+penalized score (COR-8), NOT a rank proxy — higher
  is a stronger match. This is the honest answer to "why was this injected".
- **`via 1-hop link`** — present only when the memory entered top-k through GRA-1 graph
  expansion (a linked neighbor of a lexical/dense hit), not by matching the query directly.
- **`⚠ stale`** — the memory is anchored to a commit whose cited files have since changed
  (RET-6). Treat its content as needing a re-check before you rely on it.
- **`→ links to` / `← linked from`** — the memory's outbound and inbound `[[wikilink]]`
  neighbors, so you can traverse related memory by hand.

**Abstention is a feature.** If nothing clears the relevance floor the skill says so rather
than padding out low-signal matches (RET-1) — an unrelated or too-thin query correctly
surfaces nothing. Reach for `--list-by-type` to see what *is* known.

## Agents and subagents

The recall hook fires only on a top-level user prompt, so mid-turn retrieval and subagents
(launched via Task, which get no `UserPromptSubmit` at all) need another path. Two exist:

- **The MCP server (INT-2), preferred.** The plugin declares a stdio MCP server exposing
  first-class `recall(query, k)`, `new_memory(...)`, and `traverse(name, hops)` tools that
  subagents inherit automatically — call them mid-turn, no user prompt required.
- **`bin/hippo recall`, the fallback** (pre-bootstrap, or where MCP is unavailable): run
  `"${CLAUDE_PLUGIN_ROOT}/bin/hippo" recall "<focused query>"` for the raw injection block, or
  this skill's `memory.recall_view` for the browsable listing.

**Task-prompt injection for policy-critical delegations.** When you delegate work whose
correctness depends on remembered policy (a user's feedback rule, a project constraint), don't
rely on the subagent discovering it — INJECT it. Run `bin/hippo recall "<the policy topic>"`
yourself and paste the relevant memory into the Task prompt so the subagent is grounded from
its first token, before it calls any tool. This is the deterministic path where "the subagent
might query memory" isn't good enough.

**Subagent discoveries are captured (INT-3).** When a subagent finishes, a `SubagentStop` hook
runs the same draft-capture pass as `SessionEnd` (see `memory.capture`): what the subagent
changed is snapshotted into the gitignored pending queue for later per-item approval, so a
delegated discovery doesn't vanish when the subagent returns. Nothing it found reaches the
corpus without an explicit approval, same gate as everywhere else.

## When NOT to use

- "Is memory working / why is recall empty" — that's plumbing: use `/hippo:doctor`.
- "Is the corpus content still accurate" (a judgment-based maintenance pass) — use `/hippo:audit`.
- Saving something — use `/hippo:new`. This verb is read-only; it never writes the corpus.
