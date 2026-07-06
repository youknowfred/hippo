"""Pure-python fallbacks for the pinned deps — the pre-bootstrap degraded path (ONB-2).

Before /hippo:bootstrap builds the plugin venv, hooks run under a bare ``python3``
with NONE of the pinned deps. The docs promise "recall works immediately in
BM25-only mode" in exactly that window — these two modules make that claim true:

  - ``bm25``     — an original, dependency-free implementation of the Okapi BM25
                   scorer, API-compatible with ``rank_bm25.BM25Okapi`` (same formula,
                   same defaults). Used by ``recall._bm25_rank`` only when the pinned
                   ``rank-bm25`` import fails.
  - ``miniyaml`` — a frontmatter-subset YAML parser exposing ``safe_load``. Used by
                   ``provenance`` only when the pinned PyYAML import fails.

Neither module imports numpy/fastembed (nor anything outside the stdlib) — the
vendored path must run on a stock interpreter. The venv path is unchanged: with the
pinned deps installed these modules are never imported. Both are original code,
MIT-licensed with the rest of the plugin.
"""
