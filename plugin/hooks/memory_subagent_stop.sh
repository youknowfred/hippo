#!/usr/bin/env bash
# memory_subagent_stop.sh — SubagentStop draft-capture pass (INT-3) for agent memory.
#
# A Task subagent gets no UserPromptSubmit and no SessionEnd of its own, so anything it
# discovered — a fix, a decision, a non-obvious constraint — would vanish when it returns. This
# hook fires when a subagent finishes and runs the SAME CAP-2 capture pass the SessionEnd hook
# does: it snapshots the session's episode buffer + `git diff` since the HEAD watermark (now
# including the subagent's changes) into the GITIGNORED pending queue for later per-item
# approval. Idempotent per session (the seed filename is keyed on the session id, so a
# multi-subagent turn refreshes ONE seed rather than piling up duplicates), and a safety net if
# the session is later killed before its own SessionEnd fires.
#
# CRITICAL CONTRACT (identical to memory_session_end.sh):
#   - ALWAYS exits 0.
#   - Writes ONLY to the gitignored pending queue — NEVER .claude/memory/ (the capture module
#     has no corpus writer; nothing reaches the corpus without an explicit /hippo:new).
#   - No network, no model download. Off the hot path.
#
# Wired as a SubagentStop hook via plugin/hooks/hooks.json.
set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}" 2>/dev/null || exit 0

# COR-10: no corpus → nothing to capture for, nowhere to approve into.
[ -d ".claude/memory" ] || exit 0

# SubagentStop delivers JSON on stdin (session_id, ...). Parse it inside the capture module
# (one Python spawn, INT-5 discipline). --reason subagent-stop labels the seed's origin.
PAYLOAD="$(cat 2>/dev/null || true)"

# shellcheck disable=SC1091  # dynamic path via CLAUDE_PLUGIN_ROOT; see hooks/_resolve_py.sh
. "${CLAUDE_PLUGIN_ROOT:-.}/hooks/_resolve_py.sh"
hippo_resolve_py

printf '%s' "$PAYLOAD" | "$PY" -m memory.capture --from-hook --reason subagent-stop 2>/dev/null || true
exit 0
