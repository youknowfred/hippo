#!/usr/bin/env bash
# _resolve_py.sh — OSP-6 canonical PY-resolution snippet, sourced (never executed
# directly) by every bash-invoked surface that needs a Python interpreter: both
# hooks, plugin/bin/hippo, and the 5 SKILL.md preflight blocks.
#
# Before this file existed, the same three lines were hand-copied into eight
# places — two hooks, bin/hippo, and five skills — a drift-prone duplication.
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
