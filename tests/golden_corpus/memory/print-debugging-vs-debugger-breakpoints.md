---
name: print-debugging-vs-debugger-breakpoints
description: "a real breakpoint with local-variable inspection beats sprinkling print statements"
metadata:
  type: reference
---

`pdb.set_trace()` or an IDE breakpoint lets you inspect the full call stack and
mutate state interactively, whereas prints require guessing what to log ahead of time.
