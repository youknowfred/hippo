---
name: golden-file-tests-need-a-refresh-workflow
description: "snapshot/golden-file tests need an explicit, reviewed way to intentionally update the snapshot"
metadata:
  type: project
---

Without a `--update-golden` flag and code-review step, golden files rot into either
permanently-failing tests or rubber-stamped diffs nobody actually reads.
