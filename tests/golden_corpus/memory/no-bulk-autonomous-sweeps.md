---
name: no-bulk-autonomous-sweeps
description: "destructive or corrective writes happen one item at a time with agent approval, never as a bulk sweep"
metadata:
  type: feedback
---

No `--all` flag that silently rewrites every file at once — every corrective edit
is scoped to a single item and requires an explicit human-in-the-loop confirmation.
