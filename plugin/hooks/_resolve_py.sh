#!/usr/bin/env bash
# _resolve_py.sh — OSP-6 canonical PY-resolution snippet, sourced (never executed
# directly) by every bash-invoked surface that needs a Python interpreter: the
# recall + SessionStart hooks, plugin/bin/hippo, and every SKILL.md preflight
# block (the PreCompact nudge hook is pure bash and needs no interpreter).
#
# Before this file existed, the same three lines were hand-copied into every
# such surface — a drift-prone duplication.
# Now there is ONE definition; every surface sources this file and calls
# hippo_resolve_py instead of inlining the resolution logic.
#
# hippo_resolve_py() sets PY to the plugin's self-provisioned venv python
# (${CLAUDE_PLUGIN_DATA}/venv/bin/python) when it exists and is executable,
# else falls back to a bare `python3` (pre-bootstrap / BM25-only degraded mode,
# never a hard failure). It also exports PYTHONPATH so `import memory` resolves
# to the bundled package at ${CLAUDE_PLUGIN_ROOT}.
hippo_resolve_py() {
  PY="${CLAUDE_PLUGIN_DATA:-}/venv/bin/python"
  [ -n "${CLAUDE_PLUGIN_DATA:-}" ] && [ -x "$PY" ] || PY="python3"
  export PYTHONPATH="${CLAUDE_PLUGIN_ROOT:-}${PYTHONPATH:+:$PYTHONPATH}"
}

# SHP-7 — the hooks' pre-Python probes, worktree-aware. hippo_main_tree() prints the MAIN
# working tree when the cwd is a LINKED git worktree (its .git is a FILE, so the common
# path — a plain checkout — costs one stat and no subprocess; only that shape pays one
# `git rev-parse`), and fails otherwise. hippo_corpus_present() is the COR-10 guard every
# hook runs after cd'ing to the project: true when THIS dir carries .claude/memory, or
# when its main tree does — the Python resolver then targets the main tree's corpus, so
# the hook must not bail. hippo_floor_present() is the same probe on MEMORY.md, the
# SessionStart first-run nudge's "has this project been seeded" question.
hippo_main_tree() {
  [ -f ".git" ] || return 1
  common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || return 1
  [ -n "$common" ] && [ "$(basename "$common")" = ".git" ] || return 1
  printf '%s' "$(dirname "$common")"
}
hippo_corpus_present() {
  [ -d ".claude/memory" ] && return 0
  main="$(hippo_main_tree)" && [ -d "$main/.claude/memory" ]
}
hippo_floor_present() {
  [ -f ".claude/memory/MEMORY.md" ] && return 0
  main="$(hippo_main_tree)" && [ -f "$main/.claude/memory/MEMORY.md" ]
}
