---
name: test-pyramid-favors-unit-over-e2e
description: "most tests should be fast unit tests, fewer integration, fewest slow end-to-end"
metadata:
  type: reference
---

An inverted pyramid (mostly e2e) means slow, flaky feedback loops; keep unit tests
as the broad base and reserve e2e for the handful of critical user journeys.
