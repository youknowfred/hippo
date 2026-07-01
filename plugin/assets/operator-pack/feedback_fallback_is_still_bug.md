---
name: feedback_fallback_is_still_bug
description: When primary functionality fails and falls back, the primary failure IS a bug — never dismiss it as "working as designed"
metadata:
  type: feedback
---

When primary functionality fails and the system falls back to a degraded path, the PRIMARY
FAILURE is always a bug that needs investigation and remediation. The fallback mechanism
catching it doesn't make it "not a bug" — it means the system is resilient, but the root cause
still needs fixing.

**Why:** Fallback paths produce lower-quality output (degraded results, missing data, reduced
confidence). Treating fallback activation as normal operation means accepting degraded quality
as the baseline.

**How to apply:** When reviewing logs or test output, any fallback activation (a retry
exhausting to a default, a timeout dropping to a cheaper path, a validation failure routing to a
degraded finalize) should be triaged as a bug in the primary path, not dismissed as operational
noise. Investigate root cause and fix it — don't just confirm the fallback worked.
