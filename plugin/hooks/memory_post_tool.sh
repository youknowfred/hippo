#!/usr/bin/env bash
# memory_post_tool.sh — PostToolUse read-signal capture (SIG-4 / KPI-2) + the T16 JIT lane.
#
# After a file-touching tool (Read/Edit/Write/MultiEdit/NotebookEdit — scoped by the hooks.json
# matcher), this records that the file was touched into a GITIGNORED outcome ledger. Off the hot
# path (the read-signal for KPI-2), the ledger is later JOINED against the episode buffer's
# injected memories + their cited_paths to measure injection precision — did an injected memory's
# cited file actually get used? Nothing here influences ranking.
#
# T16 JIT-1 rides the SAME single Python spawn: on the FIRST touch of a file cited by a
# steer:pin/feedback memory this session, stdout carries ONE bounded hookSpecificOutput JSON
# ("memory <name>: <description>" as additionalContext) — derived-cache reads only, empty on
# almost every touch, killed entirely by HIPPO_DISABLE_JIT. JIT-2 stamps the same lookup onto
# the outcome row as optional touch-grain provenance (cited_by).
#
# T18 FLT rides it too (killed by HIPPO_DISABLE_PRESENCE): the moved-tree tripwire compares
# live HEAD against this session's presence doc (debounced; one neutral line, once per move)
# and the worktree-first nudge fires at the first shared-tree mutation while another session
# is present. Coverage is honest: PostToolUse sees FILE-TOOL acts only — Bash-mediated
# mutations (git, pytest, scripts) are invisible to this hook.
#
# CRITICAL CONTRACT (identical to the other memory hooks):
#   - ALWAYS exits 0 — a read-signal failure must never disturb the tool loop.
#   - stdout is EMPTY or exactly one hookSpecificOutput JSON object (QUA-2).
#   - Writes ONLY to gitignored derived dirs (.claude/.memory-telemetry/). Never the corpus.
#   - No network, no model download. Fire-and-forget; a single cheap Python spawn.
#
# Wired as a PostToolUse hook (matcher-scoped to file tools) via plugin/hooks/hooks.json.
set -uo pipefail

# Operate against the CONSUMING project's root, not the plugin's own directory.
cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}" 2>/dev/null || exit 0

# COR-10: a never-opted-in repo has no corpus — nothing injects, so there is no signal to record.
[ -d ".claude/memory" ] || exit 0

# PostToolUse delivers the event as JSON on stdin ({tool_name, tool_input, session_id, ...});
# parse it INSIDE the outcome module (one Python spawn) rather than with a separate launch.
PAYLOAD="$(cat 2>/dev/null || true)"

# shellcheck disable=SC1091  # dynamic path via CLAUDE_PLUGIN_ROOT; see hooks/_resolve_py.sh
. "${CLAUDE_PLUGIN_ROOT:-.}/hooks/_resolve_py.sh"
hippo_resolve_py

printf '%s' "$PAYLOAD" | "$PY" -m memory.outcome --from-hook 2>/dev/null || true
exit 0
