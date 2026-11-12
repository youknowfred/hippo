#!/usr/bin/env bash
# memory_pre_tool.sh — PreToolUse write gate on the memory corpus (GAT-1).
#
# Before a Write/Edit/MultiEdit (scoped by the hooks.json matcher) lands in the corpus, the
# write's effect is simulated and judged: does it leave a floor or body budget over AND make
# it worse? If so, by default one hookSpecificOutput with additionalContext names each budget
# with its before and after numbers and the way out; the write goes ahead. It refuses the
# write (permissionDecision "deny") only where the corpus's own COMMITTED hippo.json asks for
# that and this machine accepted it (`hippo trust policy --accept`). Standing debt never
# objects: an edit that holds or shrinks a measure always passes.
#
# Fast path: almost every write is not a corpus write, so the payload is read by the shell
# and rejected by pattern before any interpreter starts — a write whose file_path
# does not name a .md file in a `memory` directory costs no Python spawn and no git call.
#
# CRITICAL CONTRACT (identical to the other memory hooks):
#   - ALWAYS exits 0 — a refusal is the JSON decision, never an exit code.
#   - stdout is EMPTY or exactly one hookSpecificOutput JSON object.
#   - Writes nothing but the corpus's usage spool line (gitignored telemetry), and only when
#     Python was started. No network, no model download.
set -uo pipefail

# The payload, read by the shell itself ($(<file) execs no `cat`).
PAYLOAD="$(</dev/stdin)" 2>/dev/null || PAYLOAD=""

# Fast path, builtins only. Every corpus path (the project's, a worktree copy's, the
# native-memory symlink's) has a `/memory/` segment, so a payload without one is never a
# corpus write. The tool must be a file write, and its file_path must name a .md file
# directly inside a `memory` directory. Inside a JSON string every quote is escaped, so the
# unescaped `"file_path"` key can never be matched inside the content of the write.
case "$PAYLOAD" in */memory/*) ;; *) exit 0 ;; esac
[[ $PAYLOAD =~ \"tool_name\"[[:space:]]*:[[:space:]]*\"(Write|Edit|MultiEdit)\" ]] || exit 0
[[ $PAYLOAD =~ \"file_path\"[[:space:]]*:[[:space:]]*\"([^\"]*)\" ]] || exit 0
GATE_PATH="${BASH_REMATCH[1]}"
case "$GATE_PATH" in
  *\\*) ;;                                  # an escape in the path: let Python parse it
  */memory/*.md) case "${GATE_PATH##*/memory/}" in */*) exit 0 ;; esac ;;
  *) exit 0 ;;
esac

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}" 2>/dev/null || exit 0
# shellcheck disable=SC1091  # dynamic path via CLAUDE_PLUGIN_ROOT; see hooks/_resolve_py.sh
. "${CLAUDE_PLUGIN_ROOT:-.}/hooks/_resolve_py.sh"
hippo_corpus_present || exit 0  # a project without a corpus has nothing to gate

OUT="$(printf '%s' "$PAYLOAD" | HIPPO_SURFACE=hook "$BASH" "${CLAUDE_PLUGIN_ROOT:-.}/bin/hippo" pre-tool --from-hook 2>/dev/null)" || OUT=""
case "$OUT" in
  \{*permissionDecision*) hippo_note_usage hook pre_tool deny ;;
  \{*) hippo_note_usage hook pre_tool warn ;;
  *) OUT=""; hippo_note_usage hook pre_tool pass ;;
esac
[ -n "$OUT" ] && printf '%s\n' "$OUT"
exit 0
