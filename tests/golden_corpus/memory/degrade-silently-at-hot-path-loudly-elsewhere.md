---
name: degrade-silently-at-hot-path-loudly-elsewhere
description: "a hot-path failure should degrade silently in place but surface loudly at a health check"
metadata:
  type: reference
---

The per-request path exits cleanly even on failure (never blocks the user), while
a separate doctor/health command surfaces the same failure with full detail.
