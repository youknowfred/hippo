---
name: feedback_anchor_fire_and_forget_tasks
description: "asyncio.create_task() is only weakly referenced by the event loop — an unanchored fire-and-forget task can be garbage-collected mid-execution. Always anchor it on a module-level set with a done_callback that removes it."
metadata:
  type: feedback
  pack: stack-specific
  pack_version: "0.2.0"
  stack: python-asyncio
---

`asyncio.create_task()` returns a Task that the event loop holds with only a **weak reference**
(a well-known Python asyncio gotcha, CPython bpo-44665). If your code doesn't keep a strong
reference, the Task can be garbage-collected mid-execution — silently killing whatever work it
was doing, with no exception raised anywhere.

**Why:** This is a real, recurring production bug class — a fire-and-forget background task
(startup warm-up, periodic sweep, best-effort cleanup) introduced without anchoring can simply
vanish partway through, undoing whatever side effect it was meant to provide, with no error
signal to explain why.

**How to apply:** Whenever you spawn a fire-and-forget task, keep a strong reference. The
canonical pattern:
```python
# Module-level anchor — a set of live background tasks.
_BG_TASKS: set = set()

# At spawn time:
task = asyncio.create_task(coro(), name="descriptive_name")
_BG_TASKS.add(task)
task.add_done_callback(_BG_TASKS.discard)
```
This works because the set holds a strong reference (the Task can't be GC'd while alive), and
the done-callback removes the entry when the task finishes — no leak.

Apply this pattern whenever you see `asyncio.create_task(...)` without a captured/anchored
return value, especially in startup/lifespan setup, cleanup/shutdown tasks, periodic loops
(heartbeats, sweeps), or anything spawned from a synchronous-feeling factory. If a task IS
awaited (e.g. `await asyncio.create_task(...)`), the reference is held by the awaiter, so this
anchor isn't needed.
