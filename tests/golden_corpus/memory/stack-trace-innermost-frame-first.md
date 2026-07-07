---
name: stack-trace-innermost-frame-first
description: "read a traceback from the bottom frame up \u2014 that's where the exception was raised"
metadata:
  type: reference
---

The outer frames are just the call chain that got you there; the innermost frame
(closest to the error message) is almost always where the actual defect lives.
