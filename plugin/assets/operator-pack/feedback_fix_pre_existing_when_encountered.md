---
name: feedback_fix_pre_existing_when_encountered
description: When a review pass OR a full test run surfaces pre-existing failures, fix them in the same session — don't skip/ignore them or scope them out as "out of scope"
metadata:
  type: feedback
---

When you encounter a pre-existing failure during your own work — surfaced by a review/audit
pass or by a test sweep you're running to verify your edits — fix it in the same session. Do NOT
skip it, disable it, or defer it to a follow-up as "out of scope."

**Why:** "Pre-existing" is context, not a permission slip to skip it. Discovery cost is sunk;
remediation cost is usually low compared to leaving the suite/codebase broken for the next
session.

**How to apply:**
- If a flagged failing test or stale artifact takes a few minutes to diagnose + fix, just fix
  it — same session, same PR.
- "Adjacent" is broad: any failure surfaced while you're running tests/checks right now counts,
  even if it's in a different area than your in-flight work. The threshold is "I'm seeing the
  breakage right now," not "this file is one I'm already editing."
- Reproduce the failure individually, read the touched code, fix the root cause — don't just
  silence the check or comment out the assertion.
- Re-run to confirm green before moving on.
- If the fix is genuinely large (a deprecated subsystem needing a rewrite), surface it
  explicitly and ask before deferring — don't unilaterally scope it out.
