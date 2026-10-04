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

# OBS-4: stamp the hook's start in epoch milliseconds (HIPPO_HOOK_T0_MS) so the Python
# side can log the SHELL-measured wall — interpreter start and imports included, which the
# in-process latency_ms never saw. bash 5's $EPOCHREALTIME costs no spawn; GNU date's %N
# (Linux) costs one cheap one. Neither available (stock macOS bash 3.2 + BSD date): no
# stamp at all, and the row simply omits wall_ms — never a guessed or second-grained value.
hippo_stamp_t0() {
  local t=""
  if [ -n "${EPOCHREALTIME:-}" ]; then
    t="${EPOCHREALTIME//[.,]/}"
    t="${t:0:13}"
  else
    t="$(date +%s%3N 2>/dev/null)" || t=""
  fi
  case "$t" in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) export HIPPO_HOOK_T0_MS="$t" ;;
  esac
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

# OBS-2: count one use of a bash-only hippo surface (`hippo <verb>`, a skill preflight, a
# hook whose Python failed) without spawning Python: append one line to the corpus's
# usage spool, which the next Python fold drains into the day's rollup. Resolves the
# telemetry dir the way the engine does (HIPPO_TELEMETRY_DIR, this tree, or — SHP-7 — the
# main tree of a linked worktree) and writes nothing unless that dir already exists, so a
# never-opted-in project gains no ledger from bash. Never fails the caller.
hippo_note_usage() {  # <surface> <verb> [action]
  local td="" main=""
  if [ -n "${HIPPO_TELEMETRY_DIR:-}" ]; then
    td="$HIPPO_TELEMETRY_DIR"
  elif [ -d ".claude/memory" ]; then
    td=".claude/.memory-telemetry"
  elif main="$(hippo_main_tree)" && [ -d "$main/.claude/memory" ]; then
    td="$main/.claude/.memory-telemetry"
  fi
  [ -n "$td" ] && [ -d "$td" ] || return 0
  printf '{"surface":"%s","verb":"%s","action":"%s","client":"%s"}\n' \
    "${1:-}" "${2:-}" "${3:-}" "${CLAUDE_CODE_ENTRYPOINT:-unknown}" >> "$td/usage_spool.jsonl" 2>/dev/null || true
  return 0
}

# HOT-5: the PostToolUse fast path — log a file touch WITHOUT a Python spawn when Python would
# do nothing but append the outcome row. That was ~7.5 interpreter starts per recall prompt.
# Python still runs (return 1) whenever it could add anything: a NotebookEdit, a path this
# parse cannot read exactly, MEMORY.md (the floor nag), a path the touchmap knows (JIT-1
# reminder / JIT-2 cited_by — the same exact-key lookup jit does — unless the session's
# cited_by quota is spent and no reminder can fire), a due fleet check whose HEAD moved, or
# a shared-tree mutation that could still owe the worktree nudge. Otherwise it appends exactly the row
# telemetry.log_outcome writes, so the KPI-2 join input is unchanged. Rotation stays with
# the next Python write. HIPPO_DISABLE_TOUCH_FASTPATH=1 restores the always-spawn path.
_hippo_mtime() {
  stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null
}
_hippo_epoch_ms_ts() {  # prints epoch seconds with 3 decimals, or fails
  local t=""
  if [ -n "${EPOCHREALTIME:-}" ]; then
    t="${EPOCHREALTIME/,/.}"
    printf '%s' "${t%???}"
    return 0
  fi
  t="$(date +%s.%3N 2>/dev/null)"
  case "$t" in *N*|"") ;; *) printf '%s' "$t"; return 0 ;; esac
  perl -MTime::HiRes=time -e 'printf "%.3f", time' 2>/dev/null
}
_hippo_off() {  # the engine's kill-switch reading: off unless "", 0, false or False
  case "${1//[[:space:]]/}" in ""|0|false|False) return 1 ;; *) return 0 ;; esac
}
# jit.MAX_PROVENANCE_ROWS_PER_SESSION — tests/test_touch_fastpath.py pins the two together.
HIPPO_JIT_CITED_ROWS_CAP=40
hippo_touch_fastpath() {  # <payload json>; 0 = logged here, 1 = spawn Python
  local p="$1" tool path sid corpus tree rel tree_rel td idx doc now m v ts row other rc
  local d old_head old_branch live_head live_branch st
  local LC_ALL=C
  _hippo_off "${HIPPO_DISABLE_TOUCH_FASTPATH:-}" && return 1
  [ -z "${HIPPO_INDEX_DIR:-}" ] || return 1
  # Each key exactly once, so a key-like string inside a value can never be read as one.
  [ "${p//\"tool_name\"/}" = "${p/\"tool_name\"/}" ] || return 1
  [ "${p//\"file_path\"/}" = "${p/\"file_path\"/}" ] || return 1
  [ "${p//\"session_id\"/}" = "${p/\"session_id\"/}" ] || return 1
  [[ $p =~ \"tool_name\"[[:space:]]*:[[:space:]]*\"(Read|Edit|Write|MultiEdit)\" ]] || return 1
  tool="${BASH_REMATCH[1]}"
  [[ $p =~ \"file_path\"[[:space:]]*:[[:space:]]*\"([^\"\\]+)\" ]] || return 1
  path="${BASH_REMATCH[1]}"
  [[ $p =~ \"session_id\"[[:space:]]*:[[:space:]]*\"([A-Za-z0-9._-]+)\" ]] || return 1
  sid="${BASH_REMATCH[1]}"
  case "$path" in /*) ;; *) return 1 ;; esac
  case "$path" in */./*|*/../*|*/.|*/..|*//*) return 1 ;; esac
  [[ $path =~ [[:cntrl:]] ]] && return 1
  tree="$PWD"
  if [ -d ".claude/memory" ]; then
    corpus="$PWD"
  else
    corpus="$(hippo_main_tree)" && [ -d "$corpus/.claude/memory" ] || return 1
  fi
  case "$path" in
    "$corpus"/*) rel="${path#"$corpus"/}" ;;
    "$tree"/*) rel="${path#"$tree"/}" ;;
    *) return 1 ;;  # outside the repo: Python drops it too, but let it decide
  esac
  tree_rel="$rel"
  case "$rel" in
    .claude/worktrees/*/?*) tree_rel="${rel#.claude/worktrees/}"; tree_rel="${tree_rel#*/}" ;;
  esac
  [ "${tree_rel##*/}" = "MEMORY.md" ] && return 1
  # Printable ASCII only: the touchmap may store anything else \u-escaped, and a key this
  # grep cannot see must never be read as "not cited".
  [[ $tree_rel =~ ^[\ -~]+$ ]] || return 1
  idx="$corpus/.claude/.memory-index/touchmap.json"
  [ -f "$idx" ] || return 1
  td="${HIPPO_TELEMETRY_DIR:-$corpus/.claude/.memory-telemetry}"
  [ -d "$td" ] || return 1
  # jit.observe_touch looks the in-tree path up as an exact key; so does this. A grep error
  # (or no grep) spawns.
  grep -qF -e "\"$tree_rel\"" "$idx" 2>/dev/null
  rc=$?
  if [ $rc -eq 0 ] && ! _hippo_off "${HIPPO_DISABLE_JIT:-}"; then
    # A cited path. Python adds cited_by until this session's quota is spent, and could
    # emit a first-touch reminder; with no reminder candidates at all and the quota spent,
    # it adds nothing.
    grep -qF '"reminders": {}' "$idx" 2>/dev/null || return 1
    st="$td/jit/$sid.json"
    [ -f "$st" ] || return 1
    d="$(<"$st")"
    [[ $d =~ \"cited_rows\"[[:space:]]*:[[:space:]]*([0-9]+) ]] || return 1
    [ "${BASH_REMATCH[1]}" -ge "$HIPPO_JIT_CITED_ROWS_CAP" ] || return 1
  elif [ $rc -ne 0 ] && [ $rc -ne 1 ]; then
    return 1
  fi
  if ! _hippo_off "${HIPPO_DISABLE_PRESENCE:-}"; then
      doc="$td/presence/$sid.json"
      [ -f "$doc" ] || return 1
      now="$(date +%s 2>/dev/null)" || return 1
      case "$now" in ""|*[!0-9]*) return 1 ;; esac
      m="$(_hippo_mtime "$doc")" || return 1
      case "$m" in ""|*[!0-9]*) return 1 ;; esac
      if [ $((now - m)) -ge 55 ]; then
        # The fleet check is due. It speaks only when HEAD moved, so verify that here (two
        # cheap git reads, presence._git_position's) and refresh the doc's mtime, the
        # debounce clock this path reads. Any doubt spawns.
        d="$(<"$doc")"
        [[ $d =~ \"head\"[[:space:]]*:[[:space:]]*\"([0-9a-f]+)\" ]] || return 1
        old_head="${BASH_REMATCH[1]}"
        old_branch=""
        [[ $d =~ \"branch\"[[:space:]]*:[[:space:]]*\"([^\"]*)\" ]] && old_branch="${BASH_REMATCH[1]}"
        live_head="$(git -C "$tree" rev-parse HEAD 2>/dev/null)" || return 1
        live_branch="$(git -C "$tree" symbolic-ref --short -q HEAD 2>/dev/null)"
        [ "$live_head" = "$old_head" ] && [ "$live_branch" = "$old_branch" ] || return 1
        touch "$doc" 2>/dev/null || return 1
      fi
      if [ "$tool" != "Read" ]; then
        case "$rel" in
          .claude/worktrees/*) ;;
          *)
            if ! grep -q '"nudged": *true' "$doc" 2>/dev/null; then
              for other in "$td"/presence/*.json; do
                [ -f "$other" ] && [ "$other" != "$doc" ] || continue
                m="$(_hippo_mtime "$other")" || return 1
                case "$m" in ""|*[!0-9]*) return 1 ;; esac
                [ $((now - m)) -le 21600 ] && return 1  # another live session: nudge may be owed
              done
            fi
            ;;
        esac
      fi
  fi
  ts="$(_hippo_epoch_ms_ts)" || return 1
  case "$ts" in [0-9]*.[0-9][0-9][0-9]) ;; *) return 1 ;; esac
  v=""
  if [ -f "${CLAUDE_PLUGIN_ROOT:-}/.claude-plugin/plugin.json" ]; then
    m="$(<"${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json")"
    [[ $m =~ \"version\"[[:space:]]*:[[:space:]]*\"([^\"]+)\" ]] && v="${BASH_REMATCH[1]}"
  fi
  row="{\"ts\": $ts, \"session_id\": \"$sid\", \"tool\": \"$tool\", \"path\": \"$rel\""
  [ "$tree_rel" != "$rel" ] && row="$row, \"tree_path\": \"$tree_rel\""
  [ -n "$v" ] && row="$row, \"v\": \"$v\""
  printf '%s}\n' "$row" >> "$td/outcome_events.jsonl" 2>/dev/null || return 1
  return 0
}
