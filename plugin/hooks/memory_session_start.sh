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
# never a hard failure). The hook reaches Python only through bin/hippo (SRF-1), which
# runs the ONE shared hippo_resolve_py() in _resolve_py.sh (OSP-6).
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

# --- First-run nudge (ONB-1; scoped per repo by CLM-4) — pre-Python, pure bash -------
# After install the plugin is otherwise silently inert: hooks fall back to bare
# python3 and every error is swallowed. Tell the user the ONE next step, in one line:
#   - not bootstrapped: about the machine, so the machine sets its cadence and
#     dismissal (owner ruling 2026-10-05). It shows in any repo, but only in the first
#     session of each local day: PLUGIN_DATA/.bootstrap-nudge-day holds the date it
#     last showed, written only when it shows. Its own hint names a bootstrap-only
#     machine-wide marker, PLUGIN_DATA/.bootstrap-nudge-dismissed, so dismissing it
#     never hides a repo's later nested or no-corpus line;
#   - a repo nested inside another corpus: hippo's resolver climbs past this repo's own
#     toplevel into the ancestor's corpus, so its tools read and write THAT corpus;
#   - no corpus here: only in a repo that opted in — hippo enabled in its own
#     .claude/settings*.json (a project or local install), or init recorded in the
#     machine's projects registry. Not on a native memory dir alone: native auto memory
#     is on by default, so that dir exists in most repos a user opens and keying on it
#     would bring back the machine-wide nag. When one exists, the line mentions adoption.
# The nested and no-corpus lines show every session and are dismissed per repo: a line
# per repo root in PLUGIN_DATA/nudge-dismissed-repos. That list still silences every
# line in its repos (it was the bootstrap line's hint before the ruling). The old
# machine-wide .nudge-dismissed marker is still honored for every line (an explicit
# "permanently" from before), but nothing tells anyone to create it any more.
hippo_repo_key() {  # the repo this session works in: git toplevel (main tree of a linked worktree)
  local d main
  d="$(pwd -P)"
  while [ -n "$d" ]; do
    if [ -e "$d/.git" ]; then
      if [ -f "$d/.git" ] && main="$(cd "$d" && hippo_main_tree)" && [ -n "$main" ]; then
        printf '%s' "$main"
      else
        printf '%s' "$d"
      fi
      return 0
    fi
    [ "$d" = "/" ] && break
    d="${d%/*}"
    [ -n "$d" ] || d="/"
  done
  pwd -P
}
hippo_nudge_dismissed() {  # <repo key>
  local f="${CLAUDE_PLUGIN_DATA}/nudge-dismissed-repos" c
  [ -f "$f" ] || return 1
  c="$(<"$f")"
  case $'\n'"$c"$'\n' in *$'\n'"$1"$'\n'*) return 0 ;; esac
  return 1
}
hippo_repo_opted_in() {  # <repo key>
  local f c reg
  for f in "$1/.claude/settings.json" "$1/.claude/settings.local.json" \
           "$PWD/.claude/settings.json" "$PWD/.claude/settings.local.json"; do
    [ -f "$f" ] || continue
    c="$(<"$f")"
    [[ $c =~ \"hippo@[^\"]*\"[[:space:]]*:[[:space:]]*true ]] && return 0
  done
  reg="${HIPPO_PROJECTS_FILE:-${HOME:-}/.claude/hippo-projects.json}"
  [ -f "$reg" ] || return 1
  c="$(<"$reg")"
  case "$c" in *"\"$1\":"*) return 0 ;; esac
  return 1
}
hippo_today() {  # the local date, YYYY-MM-DD: bash's own clock (4.2+), else date(1); empty if neither
  local d=""
  printf -v d '%(%F)T' -1 2>/dev/null || d="$(date +%F 2>/dev/null)" || d=""
  case "$d" in [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) printf '%s' "$d" ;; esac
}
hippo_nested_owner() {  # prints the ancestor whose corpus a repo toplevel without one resolves
  local d home="${HOME:-}"
  [ -d ".claude/memory" ] && return 1
  [ -e ".git" ] || return 1  # only a toplevel launch climbs past itself (walk_up_for_memory_dir)
  if [ -f ".git" ] && hippo_main_tree >/dev/null; then
    return 1  # a linked worktree resolves its main tree's corpus, by design
  fi
  d="$PWD"
  while [ "$d" != "/" ] && [ -n "$d" ]; do
    d="${d%/*}"
    [ -n "$d" ] || d="/"
    if [ -d "${d%/}/.claude/memory" ]; then
      printf '%s' "$d"
      return 0
    fi
    [ "$d" = "$home" ] && return 1
  done
  return 1
}
hippo_native_memory_dir() {  # <repo key>: Claude Code's own memory dir for it, when it holds files
  local LC_ALL=C nd f
  nd="${HIPPO_CLAUDE_PROJECTS_DIR:-${HOME:-}/.claude/projects}/${1//[!A-Za-z0-9]/-}/memory"
  [ -d "$nd" ] && [ ! -L "$nd" ] || return 1
  for f in "$nd"/*.md; do
    if [ -f "$f" ]; then
      printf '%s' "$nd"
      return 0
    fi
  done
  return 1
}
if [ -n "${CLAUDE_PLUGIN_DATA:-}" ] && [ ! -f "${CLAUDE_PLUGIN_DATA}/.nudge-dismissed" ]; then
  REPO_KEY="$(hippo_repo_key)"
  if ! hippo_nudge_dismissed "$REPO_KEY"; then
    NUDGE=""
    SILENCE="(To stop this note in this repo: printf '%s\n' '${REPO_KEY}' >> '${CLAUDE_PLUGIN_DATA}/nudge-dismissed-repos')"
    # The Claude Desktop app (CLAUDE_CODE_ENTRYPOINT=claude-desktop in the hook env) runs
    # the same hooks/skills/MCP server; the setup flow there is the hippo setup MCP tool
    # (bootstrap/init steps), so the nudge names it. Typed /hippo:* runs there too (CLM-8).
    if [ "${CLAUDE_CODE_ENTRYPOINT:-}" = "claude-desktop" ]; then
      INIT_STEP="run the hippo setup MCP tool's init step (just ask for it)"
    else
      INIT_STEP="run /hippo:setup"
    fi
    if [ ! -x "${CLAUDE_PLUGIN_DATA}/venv/bin/python" ] || [ ! -f "${CLAUDE_PLUGIN_DATA}/.bootstrap-sentinel" ]; then
      DAY_FILE="${CLAUDE_PLUGIN_DATA}/.bootstrap-nudge-day"
      TODAY="$(hippo_today)"
      SHOWN_ON=""
      [ -f "$DAY_FILE" ] && SHOWN_ON="$(<"$DAY_FILE")"
      # No readable clock: show it every session rather than never, and say no cadence.
      if [ ! -f "${CLAUDE_PLUGIN_DATA}/.bootstrap-nudge-dismissed" ] && { [ -z "$TODAY" ] || [ "$SHOWN_ON" != "$TODAY" ]; }; then
        BOOT_SILENCE="(${TODAY:+Shown once a day. }To stop it on this machine: touch '${CLAUDE_PLUGIN_DATA}/.bootstrap-nudge-dismissed')"
        if [ "${CLAUDE_CODE_ENTRYPOINT:-}" = "claude-desktop" ]; then
          NUDGE="hippo memory is installed but not bootstrapped — recall is inert. Set it up with the hippo setup MCP tool: bootstrap once per machine, then init once per project (just ask for it). ${BOOT_SILENCE}"
        else
          NUDGE="hippo memory is installed but not bootstrapped — recall is inert. Run /hippo:setup once per machine, then once in each project. ${BOOT_SILENCE}"
        fi
        if [ -n "$TODAY" ]; then
          { printf '%s\n' "$TODAY" > "$DAY_FILE"; } 2>/dev/null || true
        fi
      fi
    elif OWNER="$(hippo_nested_owner)"; then
      NUDGE="This repo is nested inside ${OWNER}, so hippo resolves ${OWNER}'s memory corpus here, not one of this repo's own. To give this repo its own corpus, ${INIT_STEP} here. ${SILENCE}"
    elif ! hippo_floor_present && hippo_repo_opted_in "$REPO_KEY"; then
      NUDGE="hippo memory is enabled for this repo but it has no memory corpus yet — ${INIT_STEP} to seed .claude/memory/."
      if ND="$(hippo_native_memory_dir "$REPO_KEY")"; then
        NUDGE="${NUDGE} Claude Code's own memory for this repo (${ND}) can be adopted into it; setup previews that first."
      fi
      NUDGE="${NUDGE} ${SILENCE}"
    fi
    if [ -n "$NUDGE" ]; then
      # JSON-escape (backslash, double quote) with pure bash — paths are the only
      # variable content. No jq dependency on this path.
      ESCAPED="${NUDGE//\\/\\\\}"
      ESCAPED="${ESCAPED//\"/\\\"}"
      printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"%s"}}\n' "$ESCAPED" 2>/dev/null || true
      exit 0
    fi
  fi
fi

# COR-10: a never-opted-in repo has no .claude/memory at all — bail before the
# Python dispatcher, which would otherwise mkdir a real .memory-index directory
# via build_index.refresh_index even though there's no corpus to index. The nudge
# block above fires (and exits) only where it has something to say; every other
# corpus-less repo stops here.
hippo_corpus_present || exit 0  # SHP-7: a linked worktree whose MAIN tree has the corpus passes

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

printf '%s' "$PAYLOAD" | HIPPO_SURFACE=hook "$BASH" "${CLAUDE_PLUGIN_ROOT:-.}/bin/hippo" session-start 2>/dev/null || true
exit 0
