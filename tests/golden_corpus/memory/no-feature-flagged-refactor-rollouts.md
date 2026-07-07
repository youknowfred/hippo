---
name: no-feature-flagged-refactor-rollouts
description: "internal refactors with no user-visible behavior change don't need a canary flag"
metadata:
  type: feedback
---

Feature flags exist to de-risk USER-visible changes; a pure internal refactor with
identical output should just ship — flagging it only adds bookkeeping with no upside.
