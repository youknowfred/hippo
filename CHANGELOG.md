# Changelog

All notable changes to hippo are recorded here. Format is loosely
[Keep a Changelog](https://keepachangelog.com/)-shaped, kept plain — DOC-7
(tags, version-sync CI, formal release process) will formalize this later;
until then entries are written by hand alongside each release.

## v0.4.0 — 2026-07-06 — "Recall precision: earn every injected token, in every language"

### Breaking

Every `MEMOBOT_*` environment variable is renamed to `HIPPO_*` (suffix
unchanged). This is a **clean break** — per the one-canonical-name invariant
there are NO alias shims and NO fallback reads of the old prefix. Any
`MEMOBOT_*` var still set in a shell profile, CI secret, or `.env` file is now
silently ignored by every module; `/hippo:doctor` gained a check that warns
(by name) when a stale `MEMOBOT_*` var is present in the environment, but it
will not repair anything for you — rename it yourself.

Rename table (old -> new, suffix identical in every case):

| Old (removed)                    | New (canonical)                |
|-----------------------------------|---------------------------------|
| `MEMOBOT_DISABLE_DENSE`           | `HIPPO_DISABLE_DENSE`           |
| `MEMOBOT_TRUST_ALL`               | `HIPPO_TRUST_ALL`               |
| `MEMOBOT_TELEMETRY_DIR`           | `HIPPO_TELEMETRY_DIR`           |
| `MEMOBOT_MEMORY_DIR`              | `HIPPO_MEMORY_DIR`              |
| `MEMOBOT_TRUST_FILE`              | `HIPPO_TRUST_FILE`              |
| `MEMOBOT_TELEMETRY_MAX_BYTES`     | `HIPPO_TELEMETRY_MAX_BYTES`     |
| `MEMOBOT_INDEX_DIR`               | `HIPPO_INDEX_DIR`               |
| `MEMOBOT_EMBED_MODEL`             | `HIPPO_EMBED_MODEL`             |
| `MEMOBOT_RECENT_DAYS`             | `HIPPO_RECENT_DAYS`             |
| `MEMOBOT_DENSE_TIMEOUT`           | `HIPPO_DENSE_TIMEOUT`           |
| `MEMOBOT_REFRESH_TIMEOUT`         | `HIPPO_REFRESH_TIMEOUT`         |
| `MEMOBOT_EMBED_CHUNK_SIZE`        | `HIPPO_EMBED_CHUNK_SIZE`        |
| `MEMOBOT_GRAPH_SEEDS`             | `HIPPO_GRAPH_SEEDS`             |
| `MEMOBOT_BODY_RRF_WEIGHT`         | `HIPPO_BODY_RRF_WEIGHT`         |
| `MEMOBOT_DENSE_FLOOR`             | `HIPPO_DENSE_FLOOR`             |
| `MEMOBOT_KNEE_RATIO`              | `HIPPO_KNEE_RATIO`              |

Unrelated vars are untouched: `FASTEMBED_CACHE_PATH`, `HF_HUB_OFFLINE`,
`TRANSFORMERS_OFFLINE`, `XDG_CACHE_HOME`, and every `CLAUDE_*` var belong to
other systems and keep their names. `plugin.json` / `marketplace.json`
version numbers are intentionally NOT bumped in this release — that sync is
DOC-7's job (v0.6.0).

### Shipped this release

- **RET-1** — relevance floor + knee cutoff, so a low-signal query surfaces
  nothing rather than padding out to a fixed count.
- **RET-2** — body-aware indexing; recall is no longer capped at the
  `description:` field's discipline.
- **RET-3** — Unicode-aware tokenization plus an opt-in `--multilingual`
  bootstrap preset for non-English corpora.
- **RET-4** — mines fence/traceback identifiers instead of stripping them.
- **RET-7** — records the serving backend and audits the eval-set generation
  phase.
- **GRA-1** — 1-hop neighbor expansion in recall: the graph's first
  load-bearing ranking read.
- **GRA-2** — stem-normalized `LinkGraph` identity plus `inbound()` /
  `isolates()` backlink primitives.
- **GRA-3** — bootstraps the link graph at write time.
- **GRA-6** — persists the resolved edge list in the index (`links.json`).
- **PRF-1** — persists BM25 statistics; stops rebuilding the scorer per query.
- **PRF-2** — promotes `cold_latency` from report-only to a gated check.
- **COR-8** — emits true fused scores and cross-checks index vs. query
  embedding model.
- **COR-9** — makes soft-alias collisions ambiguous instead of
  first-claimant-wins.
- **QUA-6** — pins gate constants and adds a golden-corpus dense benchmark.
- **QUA-7** — adds subprocess tests for the `bin/hippo` launcher.
- **DOC-8** — this rename, plus a doctor check for stale `MEMOBOT_*` vars.

## Earlier releases

Pre-v0.4.0 releases (v0.2.0 "Truthful snap-in", v0.3.0 "Any repo, any
machine") predate this file — see `ROADMAP.yaml`'s `release_train` section
and the corresponding merged PRs (#3, #4) for their shipped-item lists.
