---
description: Lift ONE proven-portable memory from this project's corpus into the machine-local user tier (or this repo's private tier) with an origin stamp — "learned in <repo>@<sha>" — so the lesson recalls in every other project WITH provenance. Per-item and agent-gated, never bulk. Use for "promote this memory", "make this lesson global", "lift to the user tier", "/hippo:promote".
---

# /hippo:promote — lift a project lesson into the user tier

A lesson that outgrew its repo (a working-style preference, a corrected mistake that applies
everywhere) moves UP: out of the project corpus, into the machine-local user tier
(`~/.claude/hippo-memory`), where recall fuses it into every project on this machine. The
move stamps `metadata.origin: "<repo>@<sha>"` so a cross-project hit always answers "where
was this learned", and the recall view renders it as `learned in <repo>@<sha>`.
`memory.new_memory.promote_memory` does the whole move with every guard built in — never
hand-move the file.

## Preflight (shared across all hippo skills)

```bash
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ CLAUDE_PLUGIN_DATA is unset/empty in this shell — this does NOT necessarily mean Claude Code is too old: on some surfaces (e.g. Claude Desktop) the agent's Bash tool never inherits plugin-scoped env vars even on a fully current, correctly-bootstrapped install, since only hippo's MCP server and hooks (not the general Bash tool) receive them. This skill has no Desktop-safe MCP-tool equivalent yet — re-run it from a terminal Claude Code session. If this IS a genuine terminal Claude Code session and you still see this, Claude Code likely is too old for hippo's self-provisioning — update it, or export CLAUDE_PLUGIN_DATA to a writable dir (e.g. ~/.claude/hippo-data) and re-run."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
hippo_resolve_py
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
MEMORY_DIR="$REPO_ROOT/.claude/memory"
```

## What this does, in order

1. **Pick the memory — one, by name.** If the user already named it, skip ahead. Otherwise
   list the dry-run candidates (user/feedback memories that are repo-coupling-free; the
   `consequential` count warns how many per-item confirmations the lift will need):

   ```bash
   "$PY" -c \
     "import sys, json; from memory.new_memory import promote_candidates; \
      print(json.dumps(promote_candidates(memory_dir=sys.argv[1]), indent=1))" \
     "$MEMORY_DIR"
   ```

   This is a LISTING, not a queue — the lift below stays one memory per confirmed run.

2. **Portability report (read-only), before anything moves.** Show the user every finding:

   ```bash
   "$PY" -c \
     "import sys, json; from memory.portability import scan_portability; \
      text = open(sys.argv[1], encoding='utf-8').read(); \
      print(json.dumps(scan_portability(text), indent=1))" \
     "$MEMORY_DIR/<name>.md"
   ```

   - `severity: "warn"` (`repo_coupling`) — frontmatter provenance (`cited_paths`,
     `source_commit`) is stripped automatically by the lift, so those findings resolve
     themselves. A coupled BODY (an absolute `/Users/...` path, a `git@...` remote) does
     NOT auto-rewrite: offer to edit the memory body first (a normal file edit, then
     re-run this step), or proceed with the user knowingly accepting the coupled text.
   - `severity: "confirm"` (`consequential_default` — attribution/CI-bypass policies) —
     each finding needs its OWN explicit yes from the user before step 3 may pass
     `allow_consequential`. A blanket "yes to everything" is not that.

3. **The lift.** `user` tier = recalls in every project on this machine; `private` tier =
   decoupled from the shared corpus but stays with THIS repo (no cross-project spread).
   Pass `yes` as the last argument ONLY when step 2's confirm-findings (if any) each got
   an explicit yes:

   ```bash
   "$PY" -c \
     "import sys, json; from memory.new_memory import promote_memory; \
      r = promote_memory(sys.argv[1], memory_dir=sys.argv[2], repo_root=sys.argv[3], \
                         dest_tier=sys.argv[4], allow_consequential=(sys.argv[5] == 'yes')); \
      print(json.dumps(r, indent=1)); sys.exit(0 if r['promoted'] else 1)" \
     "<name>" "$MEMORY_DIR" "$REPO_ROOT" "user" "no"
   ```

   Every refusal is a zero-filesystem-change event — the project file is untouched:
   - `already exists ... in the destination tier` — the tier already has a `<name>.md`.
     NEVER silently shadow (inv5): ask the user for a new slug and re-run with
     `new_name='<new-slug>'` added to the call (keyword on `promote_memory`).
   - `inbound referrer(s) still link here` — other project memories would dangle. Offer
     to rewrite those references first (edit each referrer's `[[link]]`), or re-run with
     `force=True` only if the user explicitly accepts the dangling links.
   - `retired (invalid_after ...)` — a demoted/superseded memory does not promote. Stop;
     its lifecycle is `/hippo:resolve` territory.
   - `consequential-default finding(s) require an individual yes` — step 2 was skipped or
     incomplete; go back and confirm each finding, then re-run with `yes`.

4. **Report the outcome, concretely.** On `promoted: true` tell the user:
   - the origin stamp (`result["origin"]`) and destination path (`result["to"]`);
   - the project-side removal is STAGED when the file was tracked (`git rm`) — it lands
     with their next commit; `floor_removed` says whether a floor pointer was dropped;
   - the memory now recalls in every project (user tier) tagged `user tier ·
     learned in <repo>@<sha>` in `/hippo:recall`, and ` (user memory)` in hook injections.

## Hard rules

- **Per-item only.** No bulk lift, no list parameter — one memory per confirmed run (inv4).
- **Never silently shadow.** A destination collision is a refusal + rename conversation,
  never an overwrite (inv5).
- **Consequential findings are individually confirmed.** One finding, one explicit yes —
  that is what `allow_consequential=True` asserts on the user's behalf.
- **A refusal means nothing moved.** Do not "clean up" after a refusal — there is nothing
  to clean; the project file and floor are exactly as they were.
- **Steer/pins and staleness bookkeeping do not carry.** `steer: pin`, `last_verified`,
  and citation provenance are project-scoped by design; only name/description/type/
  confidence/body (verbatim) + the new origin stamp land in the destination tier.
