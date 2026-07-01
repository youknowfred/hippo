---
name: feedback_no_tactical_shortcuts
description: When asked for remediation, pick the architecturally sound path — never defer part of the work to a follow-up "to avoid complexity," and never write a stub plan doc in place of actually implementing the fix
metadata:
  type: feedback
---

When given a remediation request, default to the architecturally sound fix — not a
telemetry-only stub, not "let's see if a smaller fix handles it first," not "defer to a
follow-up." Even if the sound fix is more invasive and higher-risk, that's the expected approach
unless the operator explicitly scopes it down.

**Why:** Deferring the harder part of a fix "to keep things safe" quietly narrows scope the
operator didn't actually ask to narrow. If a plan or investigation already scoped in a piece of
work, implementing only the easy part and writing a follow-up plan for the rest is a stub — not
a deliverable.

**How to apply:**
- When facing a complex fix that "could be deferred," don't defer it — build the right
  architecture now.
- Do NOT use "conservative scope reduction" as a framing. If the plan called for a fix,
  implement it.
- If the architecturally sound path requires a refactor (moving a phase boundary, adjusting
  dependency injection, threading new state through), do the refactor.
- Ask the operator BEFORE scope-reducing anything they already signed off on — don't change
  scope unilaterally.
- **Stub plan docs are NOT a deliverable.** When work is scoped in, implement it (code + tests).
  Writing a follow-up plan document instead of doing the work is the exact anti-pattern this
  memory exists to prevent. If the work is genuinely too large for one session, ask the operator
  before stubbing — never decide unilaterally that it's "architectural, needs its own ticket."
