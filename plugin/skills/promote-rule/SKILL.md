---
description: Promote ONE reinforced procedural memory into a glob-SCOPED .claude/rules/<name>.md — the harness lazy-loads it only for edits under paths derived from the memory's cited_paths, instead of another always-load CLAUDE.md line. Propose-only reviewable diff, over-scoping-capped so a rule never becomes near-unscoped; never a write without your yes. Use for "promote this to a rule", "make this a scoped rule", "scope this lesson to its files", "/hippo:promote-rule".
---

# /hippo:promote-rule — a procedural memory as a glob-scoped rule diff

A reinforced procedural memory ("always run the linter before committing `src/*.py`") is
worth making load-bearing. Done as an unscoped `CLAUDE.md` line it costs every prompt; done
RIGHT it becomes a `.claude/rules/<name>.md` whose `paths:` globs — derived from the memory's
own `cited_paths` — make the harness lazy-load it ONLY when a matching path is edited. This
skill renders that promotion as a **reviewable diff, never a write**. The corpus stays the
authority: to change the rule, edit the memory and re-promote.

Scope is deliberately narrow: ONE memory per run, `.claude/rules/` only, no auto-sync. The
`paths:` derivation is capped (a single citation stays a literal for exact drift detection; a
same-directory/same-extension group may collapse to `<dir>/*<ext>` only within the
over-scoping factor; `**` is never emitted) — a promoted rule can never silently become a
near-unscoped always-load.

## Preflight (shared across all hippo skills)

```bash
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] || { echo "✘ CLAUDE_PLUGIN_DATA is unset/empty — this Claude Code version is too old for hippo's self-provisioning. Update Claude Code, or export CLAUDE_PLUGIN_DATA to a writable dir (e.g. ~/.claude/hippo-data) and re-run."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # canonical PY resolver, OSP-6
hippo_resolve_py
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
MEMORY_DIR="$REPO_ROOT/.claude/memory"
NAME="the_memory_stem_to_promote"   # e.g. lint_before_commit (fill from the user's request)
```

## What this does, in order

1. **Render the proposal (read-only).** Nothing is written — the output IS the decision
   surface:

   ```bash
   "$PY" -m memory.promote_rule --name "$NAME" --memory-dir "$MEMORY_DIR" --repo-root "$REPO_ROOT"
   ```

   Read the report to the user, concretely:
   - the derived `paths:` globs (a single citation stays a literal path — exact drift
     detection — and a collapsed glob is capped so it can never balloon into a
     near-unscoped rule);
   - every `⚠` flag: an over-scoped glob kept as literals, a cited path missing from the
     tree (excluded), or no git oracle;
   - the unified diff of the proposed `.claude/rules/<name>.md`.
   A refusal (`promote-rule REFUSED: …`) means nothing to decide — relay the reason (no
   cited_paths, every cited path missing, a hand-authored rule already at that path) and stop.

2. **Review with the user.** This is the inv4 gate: the user (or the agent with the user's
   explicit go-ahead) approves the diff, or edits the memory's `cited_paths` / body and
   re-runs step 1.

3. **Apply only on explicit approval.** Re-renders and writes the proposed file — run it
   ONLY after step 2's yes:

   ```bash
   "$PY" -m memory.promote_rule --name "$NAME" --memory-dir "$MEMORY_DIR" --repo-root "$REPO_ROOT" --apply
   ```

   Committing the file is the user's call, like any other working-tree change.

4. **Tell the user what stays true afterwards.**
   - The rule is now DRIFT-CHECKED: a cited path that later moves flags loud in
     `/hippo:doctor` and the SessionStart rules-rot card (dead `paths:` globs).
   - The memory stays the authority — re-promoting refreshes the rule from the current
     memory; hand-edits to the rule file are replaced by the next promotion.

## Hard rules

- **Propose, never overwrite.** The render step writes nothing; the apply step runs only
  after an explicit yes on the shown diff. No flag skips the review.
- **The corpus is the one authority (inv1).** Never hand-edit the promoted rule — edit the
  memory and re-promote.
- **A cited path is the scope.** No `cited_paths`, no scoped rule (this skill refuses rather
  than emit an unscoped always-load).
- **One memory, `.claude/rules/` only.** No auto-sync, no watcher — promotion is always a
  deliberate, per-run decision.
- **A refusal means nothing changed.** A hand-authored rule already at the target path is
  never clobbered — rename or remove it first.
