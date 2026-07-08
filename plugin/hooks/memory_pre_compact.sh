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

# PreCompact delivers the event as JSON on stdin ({"trigger":"manual"|"auto", ...}); we do not
# parse it (the nudge is identical either way) but drain it so the writer never sees EPIPE.
cat >/dev/null 2>&1 || true

# Only nudge in a project that has opted into hippo memory — a corpus present means /hippo:new
# has somewhere to write. In a never-opted-in repo (COR-10) the nudge would dead-end at a
# missing bootstrap/init, so stay silent, consistent with the other hooks' corpus guard.
[ -d ".claude/memory" ] || exit 0

# Static, self-contained message — no double quotes or backslashes, so it embeds into the JSON
# string verbatim with no escaping (no jq/Python dependency on this path).
MSG="hippo: compaction is about to summarize and discard session detail. Before it proceeds, persist any DURABLE facts worth keeping past this session — a user preference, a mistake you were corrected on, a project decision or non-obvious constraint discovered this session — by running /hippo:new once per fact. Skip anything re-derivable from the code or git history. This is the last point before the transcript is compacted."

printf '{"hookSpecificOutput":{"hookEventName":"PreCompact","additionalContext":"%s"}}\n' "$MSG" 2>/dev/null || true
exit 0
