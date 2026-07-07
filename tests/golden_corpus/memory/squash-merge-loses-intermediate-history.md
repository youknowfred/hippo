---
name: squash-merge-loses-intermediate-history
description: "squash merges collapse intermediate commits so bisect loses per-step granularity"
metadata:
  type: feedback
---

Prefer merge commits or rebase-and-merge on branches where later bisection across
individual steps matters; squash is fine for small single-purpose PRs.
