---
name: batch-embedding-calls-not-per-item
description: "embed documents in batches, not one HTTP call per document"
metadata:
  type: project
---

A per-item embedding call pays fixed request overhead N times; batching amortizes
that overhead and usually maps to the provider's actual API shape.
