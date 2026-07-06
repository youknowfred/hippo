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

# Operate against the CONSUMING project's root, not the plugin's own directory —
# .claude/memory/ lives in the project, not in the plugin bundle.
cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}" 2>/dev/null || exit 0

PY="${CLAUDE_PLUGIN_DATA:-}/venv/bin/python"
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] && [ -x "$PY" ] || PY="python3"
export PYTHONPATH="${CLAUDE_PLUGIN_ROOT:-}${PYTHONPATH:+:$PYTHONPATH}"

# Pin fastembed's ONNX model cache to a durable dir. UNSET, fastembed uses
# $TMPDIR/fastembed_cache (macOS /var/folders, purged on a schedule) — the OFFLINE
# SessionStart refresh can't re-fetch a wiped model, silently degrading recall to
# BM25. Precedence (must match memory/build_index.py::durable_fastembed_cache_dir):
# explicit env wins; else ${CLAUDE_PLUGIN_DATA}/fastembed (the update-surviving data
# dir every installed plugin gets); else a home cache dir for non-plugin/dev runs.
export FASTEMBED_CACHE_PATH="${FASTEMBED_CACHE_PATH:-${CLAUDE_PLUGIN_DATA:+$CLAUDE_PLUGIN_DATA/fastembed}}"
export FASTEMBED_CACHE_PATH="${FASTEMBED_CACHE_PATH:-$HOME/Library/Caches/hippo-memory/fastembed}"

"$PY" -m memory.session_start 2>/dev/null || true
exit 0
