---
description: Review a memory diff like a PR — a zero-LLM packet that op-classifies each touched memory (ADD/UPDATE/SUPERSEDE/ARCHIVE/EDGE), runs the shipped lints scoped to the touched files, and (local only) previews how recall shifts by replaying recent episode queries against base-vs-head shadow indexes. Use for "review this memory PR", "review the memory diff", "what changed in memory", "memory review packet", "/hippo:review". The human still merges — this renders review material, never an approval.
---

# /hippo:review — the corpus review packet

A memory PR deserves the same review ergonomics as a code PR: what OPERATION each
touched memory represents, whether the shipped lints flag it, and how the change would
shift recall. `memory review` builds that packet with zero LLM and zero network — every
classification derives from git name-status, frontmatter edges, and `archive/` moves.

## Preflight (shared across all hippo skills)

```bash
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ CLAUDE_PLUGIN_DATA is unset/empty in this shell — this does NOT necessarily mean Claude Code is too old: on some surfaces (e.g. Claude Desktop) the agent's Bash tool never inherits plugin-scoped env vars even on a fully current, correctly-bootstrapped install, since only hippo's MCP server and hooks (not the general Bash tool) receive them. This skill has no Desktop-safe MCP-tool equivalent yet — re-run it from a terminal Claude Code session. If this IS a genuine terminal Claude Code session and you still see this, Claude Code likely is too old for hippo's self-provisioning — update it, or export CLAUDE_PLUGIN_DATA to a writable dir (e.g. ~/.claude/hippo-data) and re-run."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
hippo_resolve_py
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
MEMORY_DIR="$REPO_ROOT/.claude/memory"
```

## What this does, in order

1. **Pick the range.** No argument reviews the working tree against HEAD (the solo
   default — "what am I about to commit?"). Reviewing a branch or PR takes a git
   range: `main..feature`, `origin/main...HEAD`, or a single ref (meaning
   `ref..HEAD`).

2. **Build the packet (read-only):**

   ```bash
   "$PY" -m memory.review --memory-dir "$MEMORY_DIR" --repo-root "$REPO_ROOT"
   ```

   or, for a committed range:

   ```bash
   "$PY" -m memory.review origin/main...HEAD --memory-dir "$MEMORY_DIR" --repo-root "$REPO_ROOT"
   ```

   Present the packet to the user as-is (it is pasteable markdown — a PR comment a
   human posts, if they choose to). Walk them through it concretely:
   - **operations** — each touched memory as ADD / UPDATE / SUPERSEDE / ARCHIVE /
     EDGE, derived purely from frontmatter, typed edges, and archive/ moves. A
     `DELETE` row is a convention break worth calling out: hippo retires memories
     via `archive/` (reversible), never by deleting.
   - **lints** — GATE findings (secrets, threat Tier-A) are what `--ci` fails on;
     advisory findings (portability, dangling edges, conflict radar) are reviewer
     context, deliberately never a gate.
   - **recall-impact preview** — which memories would newly recall or stop
     recalling for this machine's recent real queries. Local only: the replay
     reads this machine's episode buffer and builds temp shadow indexes; it
     never runs in CI, and it states its 80-char query-preview bound inline.

3. **The human decides.** Auto-approve was removed outright — never coming back —
   as hippo's review-gated-writes identity pillar. The packet informs the merge;
   a person makes it. (Auto-posting the packet as a PR comment is not built either —
   it is future, trust-spine-gated scope, and would still never approve anything.)

## CI wiring (the one canonical memory-diff gate)

`--ci` is the SINGLE sanctioned CI scan for memory-file diffs — SEC-8's memory-diff
gate half, and the SEN-2 threat-lint CI leg rides the same vehicle:

```bash
"$PY" -m memory.review --ci origin/main...HEAD --memory-dir "$MEMORY_DIR" --repo-root "$REPO_ROOT"
```

Exit 1 iff a gate finding (secret / threat Tier-A) exists on a touched memory file;
exit 0 otherwise — including when the range touches no memory files at all. The
repo's `memory-review` job in `.github/workflows/ci.yml` runs exactly this against
each PR; do not add a second memory-scanning CI surface. (The `secret-scan` job in
the same file is SEC-8's other half — release hygiene over the whole shipped tree,
a different scope.)

## Hard rules

- **Review material, never an approval.** No mode of this skill or its CLI can
  accept, merge, or post anything. The human merges everywhere.
- **Zero LLM, zero network.** Op classification is mechanical; a packet that needed
  a model to explain itself would not be reviewable evidence.
- **The preview is local-only.** Never in CI (`--ci` omits it; `HIPPO_DISABLE_DENSE=1`
  and CI environments skip it with an explicit line) — a fresh clone has no episode
  buffer, and an honest "no local episodes to replay" beats a fabricated preview.
- **Advisory lints never gate.** Cited paths ARE repo coupling (portability would
  flag nearly every project memory), and an unresolved contradiction is a human
  judgment for /hippo:resolve — failing CI on either would automate a decision
  hippo deliberately leaves to people.
