---
name: reproduce-before-you-patch
description: "write a failing test that reproduces the bug before touching the fix"
metadata:
  type: feedback
---

A patch without a red test first is a guess dressed as a fix — the red test is
the only proof the bug existed and the proof the fix actually closed it.
