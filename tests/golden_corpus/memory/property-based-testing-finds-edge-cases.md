---
name: property-based-testing-finds-edge-cases
description: "property-based tests generate many inputs to check an invariant instead of one example"
metadata:
  type: reference
---

Hypothesis-style testing shrinks a failing random input to a minimal reproducer,
often surfacing edge cases (empty string, unicode, huge ints) example tests miss.
