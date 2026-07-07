---
name: single-pr-per-cluster-of-related-fixes
description: "several related bugs found and triaged together ship as one PR with separate commits"
metadata:
  type: feedback
---

Splitting three tightly-related fixes into three PRs multiplies review overhead for
no benefit; keep them as one PR with each fix in its own clearly-scoped commit.
