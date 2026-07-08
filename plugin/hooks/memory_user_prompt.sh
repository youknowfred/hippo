#!/usr/bin/env bash
# memory_user_prompt.sh — UserPromptSubmit recall hook for agent memory (plugin-packaged).
#
# Reads the user's prompt from stdin, runs hybrid (dense+BM25) recall over the memory
# corpus, and injects the top-K matches as additionalContext so the relevant memory is
# pulled on demand instead of always-loading the whole index.
#
# CRITICAL CONTRACT (verified harness facts):
#   - ALWAYS exits 0. On UserPromptSubmit, exit 2 BLOCKS *and ERASES* the user's prompt —
#     a recall failure must degrade silently, NEVER eat the user's input.
#   - NEVER triggers a synchronous model download: recall loads the embedding model OFFLINE
#     from the cache /hippo:bootstrap warmed; a cache miss falls back to BM25 (a pinned dep).
#   - Output is bounded < 10,000 chars by recall.py.
#
# Runs the plugin's OWN self-provisioned venv (${CLAUDE_PLUGIN_DATA}/venv), PYTHONPATH
# pointed at ${CLAUDE_PLUGIN_ROOT} so `import memory` resolves to the bundled package.
# Falls back to a bare `python3` if bootstrap hasn't run yet. PY resolution itself is
# the ONE shared hippo_resolve_py() in _resolve_py.sh (OSP-6) — every hook/skill/bin
# surface sources the same file instead of re-deriving this logic.
#
# Wired as a UserPromptSubmit hook via plugin/hooks/hooks.json. The SessionStart dynamic
# memory context is emitted by the separate memory_session_start.sh dispatcher.
set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}" 2>/dev/null || exit 0

# COR-10: a never-opted-in repo has no .claude/memory at all — bail before paying
# for stdin capture or a Python spawn. A stat is ~free; recall.py's own SEC-3
# guard would return the same nothing, but only after the interpreter+import cost.
[ -d ".claude/memory" ] || exit 0

# UserPromptSubmit delivers the event as JSON on stdin; ".prompt" is the user's text.
PAYLOAD="$(cat 2>/dev/null || true)"

# shellcheck disable=SC1091  # dynamic path via CLAUDE_PLUGIN_ROOT; see hooks/_resolve_py.sh
. "${CLAUDE_PLUGIN_ROOT:-.}/hooks/_resolve_py.sh"
hippo_resolve_py

# Force the dense model OFFLINE for the hook path (belt — recall.py also guards this).
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
# Pin fastembed's ONNX model cache to a durable, machine-shared dir (see
# memory_session_start.sh for the full rationale — precedence must match
# memory/build_index.py::durable_fastembed_cache_dir). This export runs BEFORE
# Python and WINS over its setdefault, so it must encode the same order
# (OSP-2: macOS Library/Caches vs Linux XDG-or-~/.cache).
export FASTEMBED_CACHE_PATH="${FASTEMBED_CACHE_PATH:-${CLAUDE_PLUGIN_DATA:+$CLAUDE_PLUGIN_DATA/fastembed}}"
if [ "$(uname)" = "Darwin" ]; then
  export FASTEMBED_CACHE_PATH="${FASTEMBED_CACHE_PATH:-$HOME/Library/Caches/hippo-memory/fastembed}"
else
  export FASTEMBED_CACHE_PATH="${FASTEMBED_CACHE_PATH:-${XDG_CACHE_HOME:-$HOME/.cache}/hippo-memory/fastembed}"
fi

# INT-5: ONE Python spawn for the whole hook. memory.recall --stdin-json reads the hook JSON
# payload (prompt + session_id, COR-6) directly off stdin and emits the hookSpecificOutput JSON
# itself — replacing the previous three launches (parse .prompt, parse .session_id, recall) plus
# the jq/python emission wrap. An empty/unparseable prompt or an empty result prints nothing.
printf '%s' "$PAYLOAD" | "$PY" -m memory.recall --stdin-json 2>/dev/null || true
exit 0
