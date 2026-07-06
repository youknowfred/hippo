---
name: feedback_two_dot_diff_for_lost_work
description: "To check whether a merge/promote would DROP work, use a two-dot `git diff A B` (full symmetric diff, or check both directions) — never rely on a single three-dot `A...B`, which hides A's own additions"
metadata:
  type: feedback
  pack: git-workflow
  pack_version: "0.2.0"
---

When resolving a merge conflict or a branch promotion, to answer **"would taking one side drop
real work?"**, use a **two-dot** `git diff <branch-a> <branch-b>` (full symmetric diff of the two
trees), or run BOTH `A...B` and `B...A`. **Never rely on a single three-dot** `git diff
A...B` — three-dot diffs only show what the RIGHT side changed since the merge-base with the
left side. They are blind to additions that exist ONLY on the left side.

**Why:** A three-dot diff can report "identical" or "no changes of note" between two branches
even when the source branch has substantial work the target branch entirely lacks — because
those additions are on the LEFT side of the three-dot comparison, which the three-dot form
never surfaces. Concluding "nothing to lose" from a three-dot diff alone is a real way to
silently drop work during a merge/promote.

**How to apply:** Before declaring "no work lost" on any merge or promotion, diff both
directions (or use the two-dot form) — never trust a single three-dot diff for this question.
This matters most exactly when it looks like it matters least: when the three-dot diff comes
back suspiciously small or empty, that's the signal to check the other direction, not a reason
to skip it.
