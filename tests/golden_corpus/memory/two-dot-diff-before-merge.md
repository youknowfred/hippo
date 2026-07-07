---
name: two-dot-diff-before-merge
description: "always diff against the merge-base with two dots before promoting a branch, never three"
metadata:
  type: feedback
---

A three-dot diff silently hides commits already on main from the review — use
`git diff main..branch` (two dots) to see exactly what lands, not `main...branch`.
Related: [[squash-merge-loses-intermediate-history]]
