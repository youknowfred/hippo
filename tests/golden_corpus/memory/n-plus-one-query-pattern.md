---
name: n-plus-one-query-pattern
description: "looping over a result set and issuing one query per row is the classic N+1 bug"
metadata:
  type: reference
---

Fetching a list then querying related rows individually inside the loop turns one
cheap join into hundreds of round trips — batch-load or join instead.
