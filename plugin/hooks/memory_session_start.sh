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
# never a hard failure).
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

PY="${CLAUDE_PLUGIN_DATA:-}/venv/bin/python"
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] && [ -x "$PY" ] || PY="python3"
export PYTHONPATH="${CLAUDE_PLUGIN_ROOT:-}${PYTHONPATH:+:$PYTHONPATH}"

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
  if [ ! -x "${CLAUDE_PLUGIN_DATA}/venv/bin/python" ] || [ ! -f "${CLAUDE_PLUGIN_DATA}/.bootstrap-sentinel" ]; then
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

# Pin fastembed's ONNX model cache to a durable dir. UNSET, fastembed uses
# $TMPDIR/fastembed_cache (macOS /var/folders, purged on a schedule) — the OFFLINE
# SessionStart refresh can't re-fetch a wiped model, silently degrading recall to
# BM25. Precedence (must match memory/build_index.py::durable_fastembed_cache_dir):
# explicit env wins; else ${CLAUDE_PLUGIN_DATA}/fastembed (the update-surviving data
# dir every installed plugin gets); else a home cache dir for non-plugin/dev runs.
export FASTEMBED_CACHE_PATH="${FASTEMBED_CACHE_PATH:-${CLAUDE_PLUGIN_DATA:+$CLAUDE_PLUGIN_DATA/fastembed}}"
export FASTEMBED_CACHE_PATH="${FASTEMBED_CACHE_PATH:-$HOME/Library/Caches/hippo-memory/fastembed}"

printf '%s' "$PAYLOAD" | "$PY" -m memory.session_start 2>/dev/null || true
exit 0
