---
description: The recall receipt (glass-box) — explain WHY hippo surfaced a memory for a query (winning backend, typed edges, steering, salience) or why it did NOT (the best candidate's sub-floor near-miss score and the floor it missed, an untrusted corpus, or a no-shared-token BM25 miss). Triggers include "why did you recall that", "why was that injected", "why didn't you remember", "recall receipt", "/hippo:why". Read-only; runs the same ranking the hook uses.
---

# /hippo:why — the recall receipt

Two questions erode trust the most: "why did you surface that?" and "why did you NOT
surface the thing I know we wrote down?". The recall hook is invisible by design, so this
skill answers both deliberately — it re-runs the SAME ranking the hook would apply to the
query and prints a per-hit breakdown, or the honest abstention reason. Read-only: nothing
is written, logged, or reordered for future sessions.

## Surface routing — decide first, then act silently

- **On Claude Desktop** (your context says you are in the Claude desktop app, `CLAUDE_CODE_ENTRYPOINT` is `claude-desktop`, or the preflight below stops on an unset `CLAUDE_PLUGIN_DATA`): the Desktop path for this verb IS the `why` MCP tool — call it directly and present the receipt. Skip the bash preflight and the shell blocks below; those run only in a terminal. Call the tool with no preamble — don't explain that the shell flow doesn't run on this surface, or why you're reaching for a tool instead of bash. That surface-plumbing narration is exactly the repeated noise this routing removes.
- **In a terminal Claude Code session**: run the bash flow below, guard first.

## Preflight (shared across all hippo skills)

```bash
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # Claude Code fills both in when it loads this skill
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] && [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] || { echo "✘ hippo's plugin paths are empty in this shell. Claude Code fills them into this skill's text when it loads the skill (the Bash tool does not inherit them), so run these blocks from the loaded /hippo:why skill, not from a copy of its SKILL.md. If the loaded skill stops here too, take the MCP-tool route in 'Surface routing' above."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
hippo_resolve_py
hippo_note_usage skill why  # OBS-2: count this skill's use (one spool line, no Python)
```

Each Bash call is a fresh shell, so nothing set here reaches the next call. Every block below
opens by pinning what it needs; give an inline `"$PY" …` command from the text the same pin
and resolver lines, in the same call.

## Get the receipt

Use the user's own words as the query — the receipt explains what the hook would do for
*that* prompt, so paraphrasing it changes the answer:

```bash
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # a fresh shell: pin again
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"; hippo_resolve_py
"$PY" -m memory.recall_view --why "<the query, ideally the user's own phrasing>"
```

## Reading it

- **Hits** carry `[type · relevance N · won via <backend> · …]` tags: the backend(s) whose
  ranking produced the hit, `via 1-hop link` for a graph expansion, `pinned ×1.2` when a
  steer:pin lifted it, the typed-edge note (`superseded by X` / `contradicts X — verify`),
  and the salience components when that flag is on.
- **A `(rule)` pointer's** receipt names its query **containment** score and the rules
  floor — governance sections are matched by containment, not cosine.
- **Abstention** names the reason, honestly ranked: an UNTRUSTED corpus (recall withheld,
  nothing scored — trust it via /hippo:doctor), a BM25-only corpus where no memory shares
  a token, or the near-miss: "best candidate `X` scored 0.42, below the floor 0.60". A
  near-miss that SHOULD have answered the query is a capture/description problem — enrich
  it via /hippo:consolidate, or pin it (`steer: pin`).

## When NOT to use

- "What do you remember about X" (the answer, not the explanation) — `/hippo:recall`.
- "Is recall broken / empty for everything" (plumbing, not ranking) — `/hippo:doctor`.
