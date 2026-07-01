---
name: feedback_dont_poll_ci_on_hotfix_merges
description: For hotfix PRs into the production/default branch, merge immediately once mergeable — don't sit polling CI checks for minutes
metadata:
  type: feedback
---

For hotfix PRs fixing a production-impacting issue, merge immediately (with an admin override
if needed, e.g. `gh pr merge <N> --merge --admin`) once the PR is open and mergeable. Do NOT
poll CI checks for minutes before merging.

**Why:** Polling wastes time on hotfixes where the change is already validated locally (typecheck
passes, targeted tests pass, scope is tightly bounded) — the CI wait adds latency without adding
safety in that situation.

**How to apply:**
- For hotfixes (production-impacting, especially a follow-up in a tight sequence): merge as
  soon as the PR is open and mergeable. Don't wait for a fully "clean" CI status if you've
  already validated locally.
- For routine promotions (large diffs, multi-feature batches): wait for green checks like
  normal — that's still the right pattern there.
- Heuristic: if the operator says "quickly", "hotfix", "just merge", or this is the 2nd/3rd PR
  in a tight remediation sequence, skip polling.
- Still do the local sanity checks first (typecheck, targeted tests, diff review) — it's the CI
  *wait*, not the review itself, that's the time-waste.
