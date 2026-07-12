#!/usr/bin/env bash
# memory_session_start.sh — SessionStart dispatcher for agent memory (plugin-packaged).
#
# Emits ONE merged additionalContext for the dynamic memory signals (staleness,
# git-recent, reconsolidation worklist, link-health, floor). Self-suppresses when
# there is nothing to say. ALWAYS exits 0 — never blocks session start.
#
# Runs the plugin's OWN self-provisioned venv (built by the /hippo:bootstrap skill
# into ${CLAUDE_PLUGIN_DATA}/venv), with PYTHONPATH pointed at ${CLAUDE_PLUGIN_ROOT}
# so `import memory` resolves to the bundled package — code from PLUGIN_ROOT
# (read-only, swapped on update), deps from PLUGIN_DATA (persistent across updates).
# Falls back to a bare `python3` if bootstrap hasn't run yet (BM25-only / degraded,
# never a hard failure). PY resolution itself is the ONE shared hippo_resolve_py()
# in _resolve_py.sh (OSP-6) — every hook/skill/bin surface sources the same file
# instead of re-deriving this logic.
#
# Wired as a SessionStart hook via plugin/hooks/hooks.json.
set -uo pipefail

# SessionStart delivers the event as JSON on stdin — ``source`` (startup/resume/clear/compact)
# and ``session_id`` (COR-6: read by memory.session_start so resume/compact don't rotate the
# telemetry session, and so concurrent sessions key telemetry by the harness's own id instead
# of a shared mutable file). Captured here (before the nudge branch's own reads) and piped to
# the python dispatcher below; parsing happens in Python, mirroring memory_user_prompt.sh.
PAYLOAD="$(cat 2>/dev/null || true)"

# Operate against the CONSUMING project's root, not the plugin's own directory —
# .claude/memory/ lives in the project, not in the plugin bundle.
cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}" 2>/dev/null || exit 0

# shellcheck disable=SC1091  # dynamic path via CLAUDE_PLUGIN_ROOT; see hooks/_resolve_py.sh
. "${CLAUDE_PLUGIN_ROOT:-.}/hooks/_resolve_py.sh"
hippo_resolve_py

# --- First-run nudge (ONB-1) — cheap pre-Python branch, pure stats -----------
# After install the plugin is otherwise silently inert: hooks fall back to bare
# python3 and every error is swallowed. Tell the user the ONE next step, at most
# once per NUDGE_EVERY nudge-eligible sessions, permanently silenceable via a
# dismissal marker. Emits a single well-formed hookSpecificOutput JSON and exits
# 0 (the Python dispatcher would be inert in both nudge states anyway).
NUDGE_EVERY=5
if [ -n "${CLAUDE_PLUGIN_DATA:-}" ] && [ ! -f "${CLAUDE_PLUGIN_DATA}/.nudge-dismissed" ]; then
  NUDGE=""
  SILENCE="(To silence this nudge permanently: touch '${CLAUDE_PLUGIN_DATA}/.nudge-dismissed')"
  # Typed /hippo:* commands exist only in the terminal CLI. The Claude Desktop app
  # (CLAUDE_CODE_ENTRYPOINT=claude-desktop in the hook env) runs the same hooks/skills/
  # MCP server but REJECTS typed plugin commands — there the same flows are the hippo
  # MCP setup tools (bootstrap/init, shipped in v1.10.0), so the nudge must name THOSE
  # or it dead-ends the exact user it is onboarding.
  if [ "${CLAUDE_CODE_ENTRYPOINT:-}" = "claude-desktop" ]; then
    if [ ! -x "${CLAUDE_PLUGIN_DATA}/venv/bin/python" ] || [ ! -f "${CLAUDE_PLUGIN_DATA}/.bootstrap-sentinel" ]; then
      NUDGE="hippo memory is installed but not bootstrapped — recall is inert. Set it up via the hippo MCP setup tools: run bootstrap once per machine, then init once per project (just ask for it — typed /hippo:* commands are terminal-only and do not work in this app). ${SILENCE}"
    elif [ ! -f ".claude/memory/MEMORY.md" ]; then
      NUDGE="hippo memory is bootstrapped but this project has no memory corpus — run the hippo init MCP tool to seed .claude/memory/ (just ask for it — typed /hippo:* commands are terminal-only and do not work in this app). ${SILENCE}"
    fi
  elif [ ! -x "${CLAUDE_PLUGIN_DATA}/venv/bin/python" ] || [ ! -f "${CLAUDE_PLUGIN_DATA}/.bootstrap-sentinel" ]; then
    NUDGE="hippo memory is installed but not bootstrapped — recall is inert. Run /hippo:bootstrap once per machine, then /hippo:init once per project. ${SILENCE}"
  elif [ ! -f ".claude/memory/MEMORY.md" ]; then
    NUDGE="hippo memory is bootstrapped but this project has no memory corpus — run /hippo:init to seed .claude/memory/. ${SILENCE}"
  fi
  if [ -n "$NUDGE" ]; then
    COUNTER_FILE="${CLAUDE_PLUGIN_DATA}/.nudge-counter"
    COUNT="$(cat "$COUNTER_FILE" 2>/dev/null || printf '0')"
    case "$COUNT" in '' | *[!0-9]*) COUNT=0 ;; esac
    printf '%s' "$((COUNT + 1))" > "$COUNTER_FILE" 2>/dev/null || true
    if [ "$((COUNT % NUDGE_EVERY))" -eq 0 ]; then
      # JSON-escape (backslash, double quote) with pure bash — the PLUGIN_DATA
      # path is the only variable content. No jq dependency on this path.
      ESCAPED="${NUDGE//\\/\\\\}"
      ESCAPED="${ESCAPED//\"/\\\"}"
      printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"%s"}}\n' "$ESCAPED" 2>/dev/null || true
    fi
    exit 0
  fi
fi

# COR-10: a never-opted-in repo has no .claude/memory at all — bail before the
# Python dispatcher, which would otherwise mkdir a real .memory-index directory
# via build_index.refresh_index even though there's no corpus to index. The nudge
# block above still fires (and exits) in this exact case until dismissed; this
# guard only matters once it's been silenced.
[ -d ".claude/memory" ] || exit 0

# Pin fastembed's ONNX model cache to a durable dir. UNSET, fastembed uses
# $TMPDIR/fastembed_cache (macOS /var/folders, purged on a schedule) — the OFFLINE
# SessionStart refresh can't re-fetch a wiped model, silently degrading recall to
# BM25. Precedence (must match memory/build_index.py::durable_fastembed_cache_dir):
# explicit env wins; else ${CLAUDE_PLUGIN_DATA}/fastembed (the update-surviving data
# dir every installed plugin gets); else a platform-conventional home cache dir for
# non-plugin/dev runs (OSP-2: macOS Library/Caches vs Linux XDG-or-~/.cache).
export FASTEMBED_CACHE_PATH="${FASTEMBED_CACHE_PATH:-${CLAUDE_PLUGIN_DATA:+$CLAUDE_PLUGIN_DATA/fastembed}}"
if [ "$(uname)" = "Darwin" ]; then
  export FASTEMBED_CACHE_PATH="${FASTEMBED_CACHE_PATH:-$HOME/Library/Caches/hippo-memory/fastembed}"
else
  export FASTEMBED_CACHE_PATH="${FASTEMBED_CACHE_PATH:-${XDG_CACHE_HOME:-$HOME/.cache}/hippo-memory/fastembed}"
fi

printf '%s' "$PAYLOAD" | "$PY" -m memory.session_start 2>/dev/null || true
exit 0
