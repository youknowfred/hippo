---
name: flaky-test-is-still-a-bug
description: "an intermittently failing test is not noise to be re-run away \u2014 it is signal"
metadata:
  type: feedback
---

Quarantine a flaky test with a tracked ticket and a skip reason, never a silent
retry-until-green loop that hides the underlying race.
