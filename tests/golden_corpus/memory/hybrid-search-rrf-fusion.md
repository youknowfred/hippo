---
name: hybrid-search-rrf-fusion
description: "reciprocal rank fusion combines BM25 and dense rankings without needing score calibration"
metadata:
  type: reference
---

RRF sums 1/(k + rank) across each ranking list, sidestepping the problem that BM25
and cosine-similarity scores live on incomparable scales.
