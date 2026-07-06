---
name: feedback_batch_tests_at_end
description: During multi-commit refactors/upgrades, don't run tests between each file edit and don't stall diagnosing every failure as it surfaces — finish all edits, then run the full suite once as the gate
metadata:
  type: feedback
  pack: engineering-process
  pack_version: "0.2.0"
---

During a multi-commit workstream (refactor, dependency upgrade, multi-file fix), don't run the
test suite between each edit, and don't stall to diagnose every test failure as it surfaces.
Finish all the edits, then run the suite once at the end as the gate.

**Why:** Interleaving test runs during a multi-step change slows the work down and breaks flow.
Stopping to triage each failing test individually mid-execution burns the review window on
noise, especially when many failures may be pre-existing and unrelated to the change at hand.

**How to apply:**
- Multi-file fixes, multi-file refactors, dependency/SDK version bumps, larger PR-level work →
  edit everything, test at the end.
- Optionally run the suite once at the START to know the baseline shape (which failures are
  pre-existing). Then don't triage individual failures mid-execution.
- Save the failure list, keep executing, and come back to it in the verification phase.
- If a failure clearly blocks the very next edit (e.g. an import error), fix it inline.
  Otherwise queue it and move on.
- Trust the plan's scope — if exploration reveals a plan item is a no-op, collapse it and keep
  moving, don't stop to re-confirm.
- Debugging a single, specific failure or regression is the exception — tests mid-flight are
  fine there.
