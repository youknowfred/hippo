---
description: Publish ONE local-only memory INTO this repo's committed subset — the per-item preflight (mechanical refusals, the review gate with entropy ON, advisory receipt) prints the exact git add -f + commit line and stops; the human runs it. Use for "publish this memory", "commit this memory to the repo", "add this memory to the public subset", "/hippo:publish".
---

# /hippo:publish — per-item entry INTO the committed subset

The two-audience corpus has a curated committed subset (PR #67's ratified posture:
"every future memory stays local-only until deliberately reviewed in") and, until this
verb, the review-in was hand tooling — two scans, an eyeball, a hand-typed `git add -f`.
This skill is that ritual as ONE preflight per memory. **Print-only pending owner
decision Q3:** the preflight prints the exact commands and stops — the human executes
them. The consent moments are the printed command, the PR review, and the CLB-1
memory-review CI gate on the resulting PR.

## The naming triangle (which verb moves a memory where)

| verb | movement |
| --- | --- |
| `/hippo:promote` | per-item lift **ACROSS projects** — out of this repo's corpus into the machine-local user tier (`~/.claude/hippo-memory`), origin-stamped |
| `/hippo:pack` | per-item share **OUT of the repo** — `pack_extract` strips provenance, stamps pack metadata, emits a manifest into a shareable dir |
| `/hippo:publish` | per-item entry **INTO this repo's own committed subset** — byte-identical file, in place; only its git tracking changes |

A fourth shipped movement to keep distinct: **init's fresh-mode whole-dir nudge**
(`git add .claude/memory && git commit`) is the share-everything posture for a corpus
that is public from birth. Publish is the opposite posture: the dir stays gitignored,
and each memory enters one at a time via `git add -f`, deliberately reviewed.

Publish **never transforms content** — if it ever needs to rewrite a file it is the
wrong verb (that's pack territory). One name per invocation; there is deliberately no
"publish all".

## Preflight (shared across all hippo skills)

```bash
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ CLAUDE_PLUGIN_DATA is unset/empty in this shell — this does NOT necessarily mean Claude Code is too old: on some surfaces (e.g. Claude Desktop) the agent's Bash tool never inherits plugin-scoped env vars even on a fully current, correctly-bootstrapped install, since only hippo's MCP server and hooks (not the general Bash tool) receive them. This skill has no Desktop-safe MCP-tool equivalent yet — re-run it from a terminal Claude Code session. If this IS a genuine terminal Claude Code session and you still see this, Claude Code likely is too old for hippo's self-provisioning — update it, or export CLAUDE_PLUGIN_DATA to a writable dir (e.g. ~/.claude/hippo-data) and re-run."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
hippo_resolve_py
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
MEMORY_DIR="$REPO_ROOT/.claude/memory"
```

## What this does, in order

1. **Pick the memory — one, by name.** If the user already named it, skip ahead.
   Otherwise render the candidates report for a recent range (the encode-side twin of
   the PR diff comment — local-only memories citing the range's files, with readiness
   evidence):

   ```bash
   "$PY" -m memory.recall_diff --range origin/main~5..origin/main --candidates
   ```

   The boundary view names which candidates heal fresh-checkout link rot
   (`"$PY" -m memory.lint_links --boundary`); publishing the top heals-N candidate
   repairs the most dangling links a stranger's clone sees.

2. **Run the preflight** (read-only; nothing is staged, nothing is edited):

   ```bash
   NAME="<the one memory name the user picked>"
   "$PY" -m memory.publish "$NAME"
   ```

   - **Mechanical refusals only:** docs (`MEMORY.md`, `CONVENTIONS.md`) and
     already-tracked memories (an UPDATE to a committed memory rides plain git —
     edit + commit, no publish step).
   - **The gate** reuses the review packet's lint (`review.lint_touched`) on the
     file's text with **entropy ON** — a strict superset of the CI gate's
     entropy-off scan, in one run. Gate findings (secrets, Tier-A threats) mean
     NOT ready; nothing prints.
   - **Advisories never refuse:** an expired `invalid_after` or an unresolved
     `contradicts` pair renders as a receipt warning — the committed subset IS dev
     history, and #67's bar was secrets + eyeball only.
   - **The receipt** cross-references the shipped surfaces display-only: heals-N
     (boundary view), soak strength, `verified_by`, staleness, and the citation
     derivation state (disclosed, not blocking).

3. **Show the user the preflight output and STOP.** When it prints the ready block:

   ```
   git add -f ".claude/memory/<name>.md"
   git commit -m "memory: publish <name>"
   ```

   the HUMAN runs those commands (or asks you to run them — that instruction is the
   per-item consent this skill must not assume). The PR that follows gets the CLB-1
   memory-review CI gate automatically; boundary honesty updates on the next doctor
   run.

## What this deliberately does NOT do

- No `--stage` flag, no git writes of any kind — a future flip to staging is owner
  decision Q3 and would be hippo's FIRST production write to a user repo's git index.
- No bulk form. `pack_extract`'s `names='all'` does not carry over.
- No content edits ever — byte-identical in place is publish's defining property.
