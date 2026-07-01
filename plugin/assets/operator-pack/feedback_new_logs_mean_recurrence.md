---
name: feedback_new_logs_mean_recurrence
description: When the operator shares NEW logs for a problem you previously "fixed", assume the prior fix did NOT address it — it's a recurrence, not deploy lag or a backstop working as intended
metadata:
  type: feedback
---

When the operator shares **new logs** showing a symptom you have a prior fix for, **assume the
prior fix did NOT address it** — treat it as a recurrence, not "deploy lag" or "the backstop
working as intended."

**Why:** If the operator is showing you the symptom again, the earlier fix is empirically
insufficient — that's a stronger signal than any theory about why the old fix "should" still be
working. This matches a broader tactical-fix pattern: when a fix "works then regresses," the
real cause is usually deeper than the layer that was originally patched (a prompt-level
directive instead of a data-contract fix, a symptom handler instead of the structural cause,
etc.).

**How to apply:**
- Don't reach for "deploy lag / promotion pending / backstop firing as designed" to explain a
  freshly-shared failing log. Verify the fix is actually deployed if that's cheap to check, but
  lead with: the prior fix is insufficient — find the NEXT layer down.
- Prefer the structural root cause over the tactical/surface layer. A soft directive ("never do
  X") is a fix LLMs or downstream code can partially ignore; the durable fix makes the
  underlying data contract or invariant actually correct.
- Ship the structural fix WITH a regression test that reproduces the recurrence, not just a
  targeted patch for the exact symptom shown.
