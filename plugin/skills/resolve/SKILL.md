---
description: Drain the contradiction inbox — walk every unresolved `contradicts` pair in the memory corpus and render a per-item human verdict (keep one side and supersede the other, scope both, merge, or mark not-conflicting). Nothing auto-picks a winner. Triggers include "resolve contradictions", "contradiction inbox", "resolve memory conflicts", "which memories conflict", "/hippo:resolve". An empty inbox is fine — it just says so.
---

# /hippo:resolve — drain the contradiction inbox

A `contradicts` edge deliberately demotes neither side — it means "one of these is wrong,
VERIFY", and the verify step is a human call. Until someone renders it, recall keeps
injecting both sides of the dispute. This skill walks each unresolved pair, one verdict per
item. Every verdict that changes the corpus is an ordinary reviewable git commit; the ONLY
verdict that doesn't touch the corpus (mark-not-conflicting) lands in a per-clone ledger.

## Preflight (shared across all hippo skills)

```bash
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ CLAUDE_PLUGIN_DATA is unset/empty in this shell — this does NOT necessarily mean Claude Code is too old: on some surfaces (e.g. Claude Desktop) the agent's Bash tool never inherits plugin-scoped env vars even on a fully current, correctly-bootstrapped install, since only hippo's MCP server and hooks (not the general Bash tool) receive them. This skill has no Desktop-safe MCP-tool equivalent yet — re-run it from a terminal Claude Code session. If this IS a genuine terminal Claude Code session and you still see this, Claude Code likely is too old for hippo's self-provisioning — update it, or export CLAUDE_PLUGIN_DATA to a writable dir (e.g. ~/.claude/hippo-data) and re-run."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
hippo_resolve_py
```

## Step 1 — List the inbox

```bash
"$PY" -m memory.resolve_view --list
```

Every unresolved `contradicts` pair in the corpus, whether or not the two memories ever
co-surfaced in a recall. `(declared by: …)` names which file carries the `contradicts:`
frontmatter — that is the file a corpus-mutating verdict edits.

The inbox may also list `(PROPOSED by dream --contradictions …)` pairs — DRM-C candidates
an LLM flagged as substantive conflicts among dream's high-cofire pairs, shown with the
model's one-line rationale. No edge is declared yet, so there is no frontmatter to drop;
the same verdicts below apply, with the proposal clearing itself on any corpus outcome
(the listing repeats this inline): keep-one-supersede-other's `supersedes` edge clears it;
declaring the edge (`contradicts: [<other>]` on one side) turns it into an ordinary
declared pair if you want the dispute to stay visible instead; merging/retiring a side
clears it; and scope-both ends in `--dismiss` (after scoping, "both stand as written" —
exactly what dismiss records). A proposal is an LLM's opinion with a cofire receipt —
read both files before believing it.

## Step 2 — For EACH pair, read both files and render ONE verdict

Read both memories under `.claude/memory/` first — the descriptions in the listing are
hooks, not the full claims. Then pick exactly one, per item:

- **keep-A-supersede-B** — one side won (the other is outdated/wrong). Demote the loser and
  record the succession edge:
  ```
  "$PY" -m memory.reconsolidate --reverify <loser-name> --outcome demote --superseded-by <winner-name>
  ```
  Then edit the declaring memory's frontmatter to drop the now-settled `contradicts:` entry
  (the supersedes edge carries the story from here). Commit both — an ordinary reviewable diff.

- **keep-both-as-scoped** — both are right in different scopes ("we use X *on the backend*",
  "we use Y *on the frontend*"). Edit each memory's description/body to name its scope, drop
  the `contradicts:` entry from the declaring file, and commit.

- **merge** — the two are one fact split awkwardly. Fold the surviving claim into ONE memory
  (update its body/description), then retire the other via the `/hippo:remove` flow so links
  and the floor stay consistent. Commit.

- **mark-not-conflicting** — the edge itself was wrong; both stand as written:
  ```
  "$PY" -m memory.resolve_view --dismiss <name-a> <name-b>
  ```
  This is the ONLY verdict that does not edit the corpus — it lands in this clone's
  gitignored ledger (under `${CLAUDE_PLUGIN_DATA}`), so the pair stops appearing here while
  the files and the edge stay untouched for other readers to judge.

Never bulk-apply a verdict across pairs — each pair gets its own reading and its own commit.

## Step 3 — Confirm the inbox drained

```bash
"$PY" -m memory.resolve_view --list
```

Pairs you dismissed stay gone on this clone; pairs you resolved in the corpus are gone
everywhere once the commit lands.

## When NOT to use

- "Is my corpus content still accurate" — that judgment-based sweep is `/hippo:audit`.
- Draining captured session drafts / the stale-memory worklist — `/hippo:consolidate`.
- A conflict between a memory and CLAUDE.md/.claude/rules (the governance plane) — the
  SessionStart radar routes those to `/hippo:consolidate`; this inbox is memory ⇄ memory.
