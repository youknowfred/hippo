---
name: feedback_dont_blame_vendor_latency
description: When a system degrades, don't default to "it's just vendor/provider latency" as the diagnosis — especially if a similar diagnosis was already made (and "fixed") before and it recurred
metadata:
  type: feedback
---

When a system degrades (timeouts, cascading failures, budget exhaustion), do NOT default to
"temporary latency from an external vendor/provider" as the diagnosis, especially if this is a
RECURRENCE of something already diagnosed and "fixed" that way before.

**Why:** A diagnosis that keeps recurring after its "fixes" ship is not the real root cause.
Blaming an external, uncontrollable factor is a convenient way to avoid finding the defect in
your own code — and if the previous "vendor latency" fix didn't actually stop the recurrence,
that's direct evidence the diagnosis was wrong, not that the vendor is still slow.

**How to apply:** Treat "it's just [vendor] latency" as a hypothesis to actively DISPROVE, not a
default. Hunt the code-side mechanism FIRST: unbounded state/message growth, retry loops or
non-convergent processing, retry amplification, self-inflicted concurrency saturation
(thundering-herd behavior that *looks* like provider throttling from the outside), schema/prompt
mismatches, marker/state-propagation bugs, or a "salvage" path that's actually destructive. A
fallback firing is still a bug ([[feedback_fallback_is_still_bug]]); fix WHY, don't add another
defensive layer ([[feedback_root_cause_not_symptom_handling]]). Do NOT "fix" a recurring cascade
by reflexively bumping a budget/timeout/cap constant without first identifying the actual
mechanism — that's the forbidden treadmill of tactical fixes that "work then regress." Only
conclude "genuinely external" with strong, specific evidence, especially given a recurrence
history.
