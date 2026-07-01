---
name: feedback_no_git_stash_for_fault_attribution
description: Never use git stash to determine whether a test failure or bug predates your own changes — it risks losing in-flight work, including popping a stale/pre-existing stash even when your own tree is clean
metadata:
  type: feedback
---

Never run `git stash` (in any form — `push`, `pop`, `apply`, `--include-untracked`,
`--keep-index`, single-file `-- <path>`) to determine whether a failing test or bug predates
your own session's changes.

**Why:** This pattern risks losing in-flight work — stash-apply conflicts, accidentally dropping
a stash, or (the more insidious failure) `git stash pop` running even when YOUR tree is clean and
popping whatever stale stash entry a previous session left behind, applying old unrelated changes
onto a clean tree and causing spurious conflicts in files you never touched. There is no reliably
"safe" way to use stash for this purpose, even when you're confident there's nothing to stash.

**How to apply:** When a test fails and you suspect it's pre-existing, attribute fault using:
- `git diff <file>` / `git diff --stat` to see if your session's changes touched it
- `git log -p <file>` to see when it last changed, and by what
- `git show <ref>:<path>` to read another branch/commit's version of a file into stdout,
  without touching the working tree
- `git worktree add` for an isolated sibling checkout if you truly need a second working copy

The trigger to watch for: any time you want to "temporarily unset my changes to see something"
or "compare against another version of a file," there is a non-stash answer. If you catch
yourself typing `git stash` for any reason — fault attribution, quick comparison, "just to check
something" — stop and use one of the above instead.
