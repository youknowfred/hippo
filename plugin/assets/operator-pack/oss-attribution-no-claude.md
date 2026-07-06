---
name: oss-attribution-no-claude
description: Attribute commits solely to the operator (their own git identity) — no Claude Co-Authored-By trailer or "Generated with Claude Code" line, unless they say otherwise.
metadata:
  type: feedback
---

On this operator's repos, commits, PR bodies, and any repo content should be attributed SOLELY
to the operator. Use their own configured git identity as author/committer. Do NOT add a
`Co-Authored-By: Claude ...` trailer or a "Generated with Claude Code" line unless the operator
explicitly asks for it.

**Why:** This is a personal attribution/branding preference, not a technical constraint — some
operators want clean personal attribution on their own repos (e.g. building credibility in a
community, or just preferring it). No disrespect to the tooling intended.

**How to apply:** On any repo published under the operator's own name, omit Claude attribution
by default and commit under their configured git identity. This OVERRIDES the harness default of
appending a `Co-Authored-By: Claude ...` trailer — confirm the operator's actual preference on
`/hippo:init` (this memory ships as a DEFAULT assumption, not a universal law) and adjust or
delete this memory if they want the trailer kept.
