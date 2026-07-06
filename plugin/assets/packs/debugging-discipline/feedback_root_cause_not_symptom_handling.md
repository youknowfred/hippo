---
name: feedback_root_cause_not_symptom_handling
description: When a bug has a clear root cause, fix THAT — don't layer defense-in-depth exception handlers, log-level demotions, or fallback paths that mask the underlying issue
metadata:
  type: feedback
  pack: debugging-discipline
  pack_version: "0.2.0"
---

When diagnosing bugs, attack WHY the failure exists, not just how it's handled. Adding
"defense-in-depth" on top of a root-cause fix — a backup classifier, a graceful-degrade branch,
a warning-to-error downgrade "just in case" — is exactly the tactical-shortcut pattern that
undermines root-cause discipline.

**Why:** Defense-in-depth layered on top of a root-cause fix makes future regressions harder to
diagnose (two layers now mask the issue instead of one), and it implies the root-cause fix
wasn't trusted to be complete in the first place.

**How to apply:**
- When a bug has a clear root cause (bad config, wrong constant, a missing call, an unhandled
  input shape), fix ONLY that. Don't add a backup classifier, a graceful-degrade branch, or a
  warning-to-error downgrade "just in case."
- The one exception: a narrow log-level adjustment where the behavior was already correct and
  only the surface was too noisy. That's log hygiene, not a new safety net.
- Reject framings like "we should also add..." / "let's make the check stricter" / "we can
  catch this earlier" during root-cause remediation — those are separate follow-ups, not bonus
  work bundled into the fix.
- When cascading symptoms are driven by a single root cause, the cascade resolves the moment the
  root cause is fixed — don't write a separate fix for each symptom.
