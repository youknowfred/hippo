---
name: feedback_single_pr_separate_commits
description: When shipping a multi-cluster fix (e.g. after triaging several related issues), bundle ALL of them into ONE PR with one commit per cluster — never split into multiple PRs or file separate tracking issues for "deferred" clusters
metadata:
  type: feedback
  pack: git-workflow
  pack_version: "0.2.0"
---

When shipping a multi-part fix — several related issues identified in one triage/investigation
pass — bundle every identified part into ONE PR with one commit per part, including parts
initially considered "defer / follow-up." Don't propose split PRs or separate tracking issues
for deferred parts.

**Why:** Splitting into multiple PRs adds review/merge overhead without improving safety, and
tracking issues opened for "later" follow-ups tend to just accumulate unaddressed.

**How to apply:**
- Default to a single PR with one commit per identified issue/cluster, even for low-severity
  ones.
- Order commits by risk: trivial/low-risk first (additive, easy to revert), then medium, then
  high-risk (schema-touching, state-changing) last. Each commit ships its own regression test in
  the same commit.
- Use a consistent commit message format that names the area and the specific fix.
- Halt conditions (scope balloon, pre-existing failures uncovered, unexpected ripple effects)
  belong in the plan/discussion, not as an excuse to split commits into separate PRs.
- Only deviate if a specific piece genuinely cannot be implemented right now (a real blocker) —
  surface that explicitly, and never deviate just to "tidy up" scope.

**Counter-example (don't do this):** "Hotfix PR for issues B+D+E now, separate PR for issue A,
separate PR for issue C" or "open a tracking issue for F, G, H as follow-up."

**Correct pattern:** One PR titled with the overall theme (e.g. "Cascade fixes: N issues") with
N commits, each implementing one issue + its regression test, ordered by risk.
