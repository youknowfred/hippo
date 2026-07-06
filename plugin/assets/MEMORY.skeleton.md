# <FILL-ME: project name> — Agent Memory Index (durable floor)
> Always-loaded floor: the **User** + **Working Style & Process Feedback** memories in full
> (below). Everything else is **recalled on demand** by the hybrid recall hook per-prompt — not
> listed here, to keep the always-loaded floor lean. As this corpus grows, keep new
> `project`/`reference` memories OFF this floor; only `user`/`feedback` memories get a pointer
> here (see `plugin/memory/README.md`'s floor-lint rule).

## User
- [User Role](user_role.md) — <FILL-ME once user_role.md is filled in — one-line hook>

## Working Style & Process Feedback
- [Claude is memory master](claude_is_memory_master.md) — agent owns memory upkeep autonomously

## Recalled on demand
> Everything below the floor — project facts, external references, and anything not linked
> above — is surfaced per-prompt by the recall hook, not always-loaded. Use `/hippo:new` to add
> to it; it needs no index maintenance here.
