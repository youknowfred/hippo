---
name: root-cause-not-symptom-handling
description: "wrapping a crash in try/except to silence it is not the same as fixing the underlying bug"
metadata:
  type: feedback
---

A caught exception that gets logged and swallowed still means the invariant that
raised it is violated somewhere upstream — find that spot before adding the catch.
