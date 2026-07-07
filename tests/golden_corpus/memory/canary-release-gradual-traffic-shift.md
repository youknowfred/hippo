---
name: canary-release-gradual-traffic-shift
description: "canary releases send a small traffic percentage to the new version before full rollout"
metadata:
  type: reference
---

Unlike blue-green's all-at-once switch, a canary ramps 1% -> 10% -> 100% while
watching error rates, catching regressions before they hit every user.
