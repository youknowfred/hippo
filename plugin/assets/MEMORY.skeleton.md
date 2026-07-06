# <FILL-ME: project name> — Agent Memory Index (durable floor)
> Always-loaded floor: the **User** + **Working Style & Process Feedback** memories in full
> (below). Everything else is **recalled on demand** by the hybrid recall hook per-prompt — not
> listed here, to keep the always-loaded floor lean. As this corpus grows, keep new
> `project`/`reference` memories OFF this floor; only `user`/`feedback` memories get a pointer
> here (see `plugin/memory/README.md`'s floor-lint rule).

## User
- [User Role](user_role.md) — <FILL-ME once user_role.md is filled in — one-line hook>

## Working Style & Process Feedback
- [OSS attribution](oss-attribution-no-claude.md) — commit attribution preference
- [Fallback is still a bug](feedback_fallback_is_still_bug.md) — no "working as designed" on a degraded path
- [Fix pre-existing when encountered](feedback_fix_pre_existing_when_encountered.md) — same session, no deferring
- [No backward compat](feedback_no_backward_compat.md) — one correct path, no shims
- [No legacy read fallbacks](feedback_no_legacy_read_fallbacks.md) — one canonical name only
- [No feature-flagged refactor rollouts](feedback_no_feature_flagged_refactor_rollouts.md) — one PR, delete the old
- [No git stash for fault attribution](feedback_no_git_stash_for_fault_attribution.md) — `git diff`/`log`/`show` instead
- [Two-dot diff for lost work](feedback_two_dot_diff_for_lost_work.md) — never trust a lone three-dot diff on a merge/promote
- [Single PR, separate commits](feedback_single_pr_separate_commits.md) — bundle multi-issue fixes, one commit per issue
- [Additive not restrictive redirects](feedback_additive_not_restrictive_redirects.md) — a mid-task redirect doesn't drop other scoped work
- [Batch tests at the end](feedback_batch_tests_at_end.md) — don't interleave test runs mid-refactor
- [Don't poll CI on hotfix merges](feedback_dont_poll_ci_on_hotfix_merges.md) — merge as soon as mergeable
- [No hedging on confirmed paid services](feedback_no_hedging_on_confirmed_paid_services.md) — don't invent plan-limit risk already ruled out
- [Serena tools only](feedback_serena_tools_only.md) — code-nav MCP only, never for memory
- [No tactical shortcuts](feedback_no_tactical_shortcuts.md) — implement the sound fix, don't stub a plan doc
- [Root cause not symptom handling](feedback_root_cause_not_symptom_handling.md) — fix why, not add defense-in-depth
- [Don't blame vendor latency](feedback_dont_blame_vendor_latency.md) — hunt the code-side mechanism first
- [New logs mean recurrence](feedback_new_logs_mean_recurrence.md) — assume a prior fix didn't work
- [Anchor fire-and-forget tasks](feedback_anchor_fire_and_forget_tasks.md) — asyncio Task weak-ref GC gotcha
- [Claude is memory master](claude_is_memory_master.md) — agent owns memory upkeep autonomously
- [Adversarial design pinning before implementation](adversarial_design_pinning_before_implementation.md) — judge panel + adversarial-verify before risky code

## Recalled on demand
> Everything below the floor — project facts, external references, and anything not linked
> above — is surfaced per-prompt by the recall hook, not always-loaded. Use `/hippo:new` to add
> to it; it needs no index maintenance here.
