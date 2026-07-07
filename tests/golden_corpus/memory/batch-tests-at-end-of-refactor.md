---
name: batch-tests-at-end-of-refactor
description: "run the full suite once at the end of a mechanical refactor, not after every file"
metadata:
  type: feedback
---

Re-running the whole suite after each individual rename wastes wall-clock time when
the refactor is purely mechanical — batch validation to the end, then fix in one pass.
