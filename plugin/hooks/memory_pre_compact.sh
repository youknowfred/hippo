#!/usr/bin/env bash
# memory_pre_compact.sh — PreCompact capture nudge (CAP-1) for agent memory (plugin-packaged).
#
# Compaction is where session knowledge dies: the transcript is summarized and durable facts
# the agent learned this session (a user preference, a corrected mistake, a project decision)
# are lost unless they were already written to the corpus. This hook fires just BEFORE
# compaction and injects a one-line additionalContext nudge telling the model to persist any
# durable facts via /hippo:new first — a capture OPPORTUNITY, not a capture. Zero write
# machinery, prompt-level only: it never touches the corpus, never spawns Python, never
# downloads. The Stop/SessionEnd draft-capture pass (CAP-2) is the machinery; this is the
# cheap, synchronous, hot-path-safe reminder that precedes it.
#
# CRITICAL CONTRACT (same as the other hooks):
#   - ALWAYS exits 0 — never blocks or interferes with compaction.
#   - stdout is EMPTY or a single valid hookSpecificOutput JSON object.
#   - No Python spawn, no network, no corpus writes (nothing to get wrong offline).
#
# Wired as a PreCompact hook via plugin/hooks/hooks.json.
set -uo pipefail

# Operate against the CONSUMING project's root, not the plugin's own directory.
cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}" 2>/dev/null || exit 0

# PreCompact delivers the event as JSON on stdin ({"session_id": "...", "trigger":
# "manual"|"auto", ...}). GRW-4 reads ONE field — session_id, via pure-bash sed (still no
# jq/Python on this path) — so the decision-capture command below can key its ledger entries
# to THIS session; the rest of the payload is drained and ignored as before.
PAYLOAD="$(cat 2>/dev/null || true)"
SID="$(printf '%s' "$PAYLOAD" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' 2>/dev/null | head -1)"

# Only nudge in a project that has opted into hippo memory — a corpus present means /hippo:new
# has somewhere to write. In a never-opted-in repo (COR-10) the nudge would dead-end at a
# missing bootstrap/init, so stay silent, consistent with the other hooks' corpus guard.
[ -d ".claude/memory" ] || exit 0

# Static, self-contained message — no double quotes or backslashes, so it embeds into the JSON
# string verbatim with no escaping (no jq/Python dependency on this path).
MSG="hippo: compaction is about to summarize and discard session detail. Before it proceeds, persist any DURABLE facts worth keeping past this session — a user preference, a mistake you were corrected on, a project decision or non-obvious constraint discovered this session — by running /hippo:new once per fact. Skip anything re-derivable from the code or git history. This is the last point before the transcript is compacted."

# GRW-4: also nudge the WHY into the SessionEnd capture seed. The decision ledger is written
# by the AGENT (capture-from-evidence: quote/paraphrase what the user actually said — the
# tooling never synthesizes an entry), so the nudge must hand it a runnable command — the
# plugin venv python + PYTHONPATH, values sanitized of the two characters the JSON embed
# forbids. Appended only when the plugin env is present (pre-bootstrap keeps the base nudge).
if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ]; then
  PY_FOR_NUDGE="${CLAUDE_PLUGIN_DATA:-}/venv/bin/python"
  [ -n "${CLAUDE_PLUGIN_DATA:-}" ] && [ -x "$PY_FOR_NUDGE" ] || PY_FOR_NUDGE="python3"
  PY_SAFE="$(printf '%s' "$PY_FOR_NUDGE" | tr -d '"\\')"
  ROOT_SAFE="$(printf '%s' "${CLAUDE_PLUGIN_ROOT}" | tr -d '"\\')"
  SID_SAFE="$(printf '%s' "$SID" | tr -d '"\\')"
  SID_FLAG=""
  [ -n "$SID_SAFE" ] && SID_FLAG=" --session-id '$SID_SAFE'"
  MSG="$MSG Separately, record the WHY that cannot be re-derived from the diff — each decision the user explicitly made or confirmed this session (a tradeoff taken, an approach chosen, a constraint stated) — one command per decision, quoting or faithfully paraphrasing the user, never inferring: PYTHONPATH='$ROOT_SAFE' '$PY_SAFE' -m memory.capture --add-decision 'the decision, in one sentence'$SID_FLAG — these land in this session's capture seed for the next /hippo:consolidate drain."
fi

printf '{"hookSpecificOutput":{"hookEventName":"PreCompact","additionalContext":"%s"}}\n' "$MSG" 2>/dev/null || true
exit 0
