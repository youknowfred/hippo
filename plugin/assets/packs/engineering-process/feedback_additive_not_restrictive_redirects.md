---
name: feedback_additive_not_restrictive_redirects
description: When the operator redirects mid-task to a specific item, treat it as ADDITIVE not RESTRICTIVE — other already-identified items still need fixing in the same session
metadata:
  type: feedback
  pack: engineering-process
  pack_version: "0.2.0"
---

When the operator redirects to one item mid-task (e.g. "can we bump this value to X"), treat
that as ADDITIVE, not RESTRICTIVE. Other items already identified in the current
investigation/task still get addressed in the same session — the redirect is not implicit
permission to drop everything else.

**Why:** A narrow mid-task redirect is easy to over-read as "only do this now." Left unchecked,
that reading silently drops other already-identified work the operator still expects done.

**How to apply:**
- When the operator redirects mid-task, default to: "handle the redirected item first, then
  continue with the other identified items" — NOT "handle only the redirected item."
- Only narrow scope if the operator explicitly says "skip the others" / "only do this" / "defer
  the rest."
- If an investigation already returned a high-confidence diagnosis for other issues, that work
  belongs to the operator by default — don't volunteer to throw it away.
