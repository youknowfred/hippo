---
name: profile-before-optimizing
description: "measure with a profiler before optimizing \u2014 intuition about hot paths is often wrong"
metadata:
  type: feedback
---

The function that feels slow is rarely the actual bottleneck; a flamegraph or
sampling profiler tells you where the wall-clock time is really going.
