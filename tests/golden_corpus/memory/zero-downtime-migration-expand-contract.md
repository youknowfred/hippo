---
name: zero-downtime-migration-expand-contract
description: "schema changes use expand-then-contract: add the new column, backfill, cut over, drop the old"
metadata:
  type: reference
---

Never rename/drop a column in the same deploy that starts writing the new one —
old code paths still reading the old column would break instantly.
