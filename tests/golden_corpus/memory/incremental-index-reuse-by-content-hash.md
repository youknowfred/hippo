---
name: incremental-index-reuse-by-content-hash
description: "keep a content-hash keyed cache so unchanged documents skip re-embedding on rebuild"
metadata:
  type: reference
---

Hashing each document's text and reusing the prior embedding row when the hash
matches turns a full-corpus rebuild into an incremental update for the common case.
