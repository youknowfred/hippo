---
name: git-worktree-for-parallel-branches
description: "use git worktree to check out two branches into sibling directories without stashing"
metadata:
  type: project
---

`git worktree add ../hippo-hotfix hotfix-branch` gives a second working directory
sharing the same object store, avoiding stash juggling when jumping between branches.
