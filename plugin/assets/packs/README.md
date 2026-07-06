# Starter packs

Seed memories for a fresh corpus, organized as **opt-in packs**. `/hippo:init` offers this
menu; the default is **core only** — every additional policy a corpus starts with must be an
explicit choice, because these files are committed to the consuming repo, floor-loaded or
recalled for every teammate, and steer agent behavior from day one.

| Pack | Default | What it seeds |
|---|---|---|
| `core` | **yes** | The `user_role.md` template (who the operator is — a `<FILL-ME>` scaffold) + `claude_is_memory_master.md` (the agent owns corpus upkeep) |
| `git-workflow` | no | Git habits: no-stash fault attribution, two-dot diffs for lost work, single-PR batching, plus two **individually-confirmed** policies (below) |
| `debugging-discipline` | no | Root-cause-first diagnosis policies |
| `engineering-process` | no | One-correct-path engineering: no compat shims, no legacy fallbacks, no flag-gated refactors, batch tests at the end |
| `stack-specific` | no | Gotchas that only apply on a given stack (`metadata.stack` tags: `python-asyncio`, `serena-mcp`) |

## Individually-confirmed policies

Two memories are consequential enough that a pack-level "yes" is NOT consent — init must ask
about each one separately (their `manifest.json` entries carry `"confirm": "individual"` with
the reason):

- `git-workflow/oss-attribution-no-claude.md` — strips AI co-author trailers from every
  commit the agent makes, for the whole repo.
- `git-workflow/feedback_dont_poll_ci_on_hotfix_merges.md` — a CI-bypass policy (merge as
  soon as mergeable, don't wait for checks); many teams explicitly forbid this.

## Manifest format

Each pack directory carries a `manifest.json`:

```json
{
  "pack": "git-workflow",
  "version": "0.2.0",
  "title": "Git workflow",
  "description": "…",
  "seed_by_default": false,
  "memories": [
    {"file": "feedback_two_dot_diff_for_lost_work.md"},
    {"file": "oss-attribution-no-claude.md", "confirm": "individual", "reason": "…"}
  ]
}
```

Every pack memory's frontmatter carries `metadata.pack` and `metadata.pack_version` (and
`metadata.stack` where stack-specific), so `/hippo:audit` can flag never-recalled pack
members for pruning and future tooling can report pack drift. Manifests stay in the plugin —
init copies only the `.md` memory files into `.claude/memory/`.

## Inclusion criteria

A memory belongs in a pack only if ALL of these hold:

1. **Portable** — true for (nearly) any operator/project of the pack's theme; never coupled
   to one product, repo layout, or employer. Project-coupled knowledge belongs in the
   consuming repo's own corpus, written by `/hippo:new` as it's learned.
2. **Behavior-worthy** — it changes what the agent should DO, not trivia. Every `feedback`
   memory carries the *why*, so edge-case judgment stays possible.
3. **Safe as a default for its audience** — anything that would surprise a teammate reading
   `git log` or a reviewer (attribution, CI bypass) needs `"confirm": "individual"`, not
   just pack membership.
4. **Maintained** — a pack memory that stops being true gets fixed or removed here; consumers
   re-sync via their own corpus curation (audit flags cold pack members).
