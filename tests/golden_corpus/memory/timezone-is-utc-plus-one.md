---
name: timezone-is-utc-plus-one
description: "the operator's working timezone is UTC+1 for scheduling and timestamp interpretation"
metadata:
  type: user
---

When a relative time like "tomorrow morning" needs a concrete timestamp, resolve
it against UTC+1 rather than assuming UTC or the server's local time.
