---
name: rebase-interactive-reorders-not-rewrites-content
description: "interactive rebase reorders/squashes commits but a conflicting reorder still needs manual resolution"
metadata:
  type: reference
---

`git rebase -i` lets you reorder, squash, and reword, but moving a commit past one
it conflicts with still stops for manual resolution — it does not resolve semantic
conflicts automatically.
