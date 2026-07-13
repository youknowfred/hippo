#!/usr/bin/env bash
# memory_session_end.sh — SessionEnd draft-capture pass (CAP-2) for agent memory.
#
# When a session ends, the durable facts it learned die with the transcript unless they were
# written to the corpus. This hook runs the sleep-time capture pass: it snapshots the ending
# session's episode-buffer entries (query previews + recalled names + the HEAD watermark) plus
# `git diff` since that watermark into ONE seed in the GITIGNORED pending queue
# (.claude/.memory-pending/), for the agent to review and approve into memory NEXT session.
#
# CRITICAL CONTRACT:
#   - ALWAYS exits 0 — never interferes with session teardown.
#   - Writes ONLY to the gitignored pending queue. It NEVER writes .claude/memory/: the capture
#     module has no corpus writer at all (structural approval gate — see memory/capture.py and
#     its negative-capability test). Nothing reaches the corpus without an explicit /hippo:new.
#   - No network, no model download — UNLESS the owner explicitly opts in with
#     HIPPO_CAPTURE_LLM=1, which adds ONE bounded triage API call (suggestions annotated onto
#     the seed only; any failure falls back to the heuristic-only seed and this hook still
#     exits 0). Off the hot path (SessionEnd, once) — zero per-prompt cost.
#
# Wired as a SessionEnd hook via plugin/hooks/hooks.json.
set -uo pipefail

# Operate against the CONSUMING project's root, not the plugin's own directory.
cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}" 2>/dev/null || exit 0

# COR-10: a never-opted-in repo has no corpus — nothing to capture for, and nowhere the agent
# would approve into. Bail before the Python spawn (consistent with the other hooks' guard).
[ -d ".claude/memory" ] || exit 0

# SessionEnd delivers the event as JSON on stdin ({session_id, reason, ...}); parse it INSIDE
# the capture module (one Python spawn, INT-5 discipline) rather than with a separate launch.
PAYLOAD="$(cat 2>/dev/null || true)"

# shellcheck disable=SC1091  # dynamic path via CLAUDE_PLUGIN_ROOT; see hooks/_resolve_py.sh
. "${CLAUDE_PLUGIN_ROOT:-.}/hooks/_resolve_py.sh"
hippo_resolve_py

printf '%s' "$PAYLOAD" | "$PY" -m memory.capture --from-hook 2>/dev/null || true
exit 0
