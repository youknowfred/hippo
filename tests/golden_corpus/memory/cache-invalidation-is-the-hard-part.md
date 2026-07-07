---
name: cache-invalidation-is-the-hard-part
description: "caching is easy; knowing exactly when to invalidate a cached value is the hard part"
metadata:
  type: reference
---

A stale cache silently serving wrong data is worse than no cache at all — prefer
short TTLs or explicit invalidation hooks over 'cache forever and hope'.
