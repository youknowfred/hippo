# Changelog

All notable changes to hippo are recorded here. Format is loosely
[Keep a Changelog](https://keepachangelog.com/)-shaped, kept plain. The release
process is formalized in [`RELEASING.md`](RELEASING.md) (DOC-7, v0.6.0): entries
are written by hand as the final commit of each release PR, `plugin.json` and
`marketplace.json` versions are kept in lockstep by `tests/test_version_sync.py`
and the tag-time `release.yml`, and every entry states a **re-bootstrap** flag.

## v0.6.0 — 2026-07-07 — "The write path — capture up to the approval gate; memory reaches every agent"

**re-bootstrap: no** — `plugin/requirements.txt` is unchanged; the code swap on update is
sufficient. (`hypothesis` was added for QUA-9's fuzz tests, but as a CI/test-only install line,
NOT a runtime dependency — mirroring QUA-10's pytest-timeout.) No corpus format or index schema
change this release.

New env var: `HIPPO_PENDING_DIR` (CAP-2 override for the gitignored draft-capture queue,
`.claude/.memory-pending/`). New surfaces: two skills (`/hippo:recall`, `/hippo:consolidate`),
a stdio MCP server, and three capture hooks (`PreCompact`, `SessionEnd`, `SubagentStop`).

The theme of the release: durable facts stop dying with the session. Capture is now automated —
but only ever UP TO an explicit approval gate, never past it. Nothing a capture pass produces
can reach `.claude/memory/` without a per-item, agent-gated write; that boundary is structural
(the capture module has no corpus writer) and adversarially tested.

### Shipped this release

- **CAP-1** — a `PreCompact` hook nudges the model to persist durable facts via `/hippo:new`
  before compaction discards session detail. Prompt-level, no Python spawn, no corpus writes.
- **CAP-2** — the `SessionEnd` draft-capture pass finally consumes the soaking episode buffer:
  it snapshots a session's episode replay (queries + recalled names + HEAD watermark) plus
  `git diff` since that watermark into ONE seed in the gitignored `.claude/.memory-pending/`
  queue, for per-item approval next session. The approval gate is structural — `memory.capture`
  imports no corpus writer — and a SessionStart producer surfaces the queue so it never soaks
  silently.
- **CAP-3** — `new_memory --check`: a dry-run that scores a captured candidate against the
  corpus with LIF-2's near-duplicate machinery WITHOUT writing, so approving a duplicate routes
  to update/supersede instead of a new file.
- **CAP-4** — `/hippo:consolidate`, a sleep-time skill that drains the capture queue
  (check-first), works the reconsolidation worklist, and refreshes the graph in one deliberate
  turn — keeping the hook path pure retrieval.
- **INT-1** — `/hippo:recall`, the read-side verb: "what do you remember about X" / list by
  type, reusing the exact hook ranking and annotating each hit with type, staleness, and graph
  neighbors.
- **INT-2** — a dependency-free stdio MCP server (`recall` / `new_memory` / `traverse` tools)
  giving mid-turn and subagent memory access; the hook path never imports it and still works
  with it absent.
- **INT-3** — a `SubagentStop` capture path (subagent discoveries become capture candidates)
  plus the Task-prompt injection pattern for policy-critical delegations.
- **INT-4** — the native-memory coexistence contract: a doctor check for symlink-target drift +
  native-layout change, a compatibility doc (`plugin/memory/NATIVE_MEMORY.md`), and a README
  positioning section.
- **INT-5** — one Python launch per prompt (`memory.recall --stdin-json` reads the hook payload
  and emits the output JSON itself, replacing three spawns + jq), and a doctor p95 hot-path
  latency check over the ledger.
- **QUA-9** — property-based (Hypothesis) fuzzing over the parsing surfaces: `split_frontmatter`
  body-preservation, `backfill`/`set_invalid_after` never touching the body, `clean_query`
  totality + input-bounded output, and `tokenize`/`normalize_slug` totality over Unicode.
- **DOC-7** — release engineering: `plugin.json` / `marketplace.json` bumped to 0.6.0 and kept
  in lockstep by a version-sync test + a tag-time `release.yml`; a doctor installed-vs-
  bootstrapped version-delta check; and `RELEASING.md` formalizing the branch → per-item commits
  → CHANGELOG capstone → squash-merge → tag process this file's header used to defer.

## v0.5.0 — 2026-07-07 — "The graph earns its keep: typed relations and closed lifecycle loops"

### Format changes

This release introduces corpus format versioning (COR-7) and bumps it once:

- **Corpus format 1 → 2** (GRA-4) — additive: `supersedes:` / `contradicts:` /
  `refines:` typed relations may now appear in frontmatter alongside untyped
  `[[wikilinks]]`. Existing corpora keep working unchanged; `/hippo:doctor`
  reports the corpus's stamped format vs. what the plugin expects and names
  the exact next step (stamp the marker, no autonomous migration).
- **Index schema 2 → 3** (RET-5) — adds a `source_commit_time` field to each
  manifest entry. COR-7's enforcement means a schema mismatch now costs
  exactly one full rebuild instead of silently serving a stale shape; nothing
  the operator needs to do by hand.

New env vars: `HIPPO_DUP_THRESHOLD` (LIF-2 near-duplicate cosine/BM25
threshold override) and `HIPPO_SALIENCE` (RET-5 salience-fusion ranking
blend — **default off**; the eval numbers on this release's fixtures showed
zero regression but also zero measurable lift, so it ships opt-in rather than
on-by-default until a corpus with real usage/staleness signal can prove it).

### Shipped this release

- **COR-7** — enforces index schema versioning and adds corpus format
  versioning, with a doctor-driven migration surface for both.
- **GRA-4** — typed edges (`supersedes` / `contradicts` / `refines`): recall
  demotes and annotates superseded memories pre-cut, flags contradictions,
  and `lint_links` catches dangling typed targets.
- **GRA-5** — `archive_memory` refuses (without `--force`) when inbound
  links — untyped or typed — still point at the target, and reports the
  referrer list either way.
- **GRA-9** — the reconsolidation worklist grows a report-only 1-hop
  "linked" column so a stale memory's neighbors surface for review too.
- **LIF-1** — demote gets a terminal state: it now chains straight into
  soft-invalidation (no second manual command), and a snooze/ack primitive
  stops re-nagged items from re-appearing every session.
- **LIF-2** — write-time near-duplicate/conflict detection (warn-only):
  `write_memory` surfaces nearest-neighbor matches and the `/hippo:new`
  skill routes the add / update-existing / supersede / skip decision.
- **LIF-3** — citation rot (a renamed/deleted cited file silently dropping
  out of `cited_paths`) is now surfaced instead of vanishing unnoticed.
- **LIF-4** — usage aggregates now survive telemetry-ledger rotation, and
  `archive_candidates` finally enforces its own ≥5-session soak gate.
- **LIF-5** — a missing/renamed `MEMORY.md` floor section is repaired or
  loudly reported — never a silent no-op.
- **LIF-6** — staleness and reconsolidation SessionStart producers share one
  computed stale set, so no memory is reported twice.
- **RET-5** — salience fusion (recency/usage/staleness ranking priors),
  shipped behind `HIPPO_SALIENCE` (default off) per this release's honest
  eval numbers.
- **RET-6** — drifted injections carry a one-line verify-at-use banner;
  reverifying a memory clears it on the next SessionStart.
- **TEA-4** — floor pointers insert at a deterministic sorted position
  instead of always appending at the section tail, so concurrent teammate
  writes to the same floor section merge cleanly instead of colliding.
- **QUA-8** — the skills-contract suite now extracts and checks every fenced
  code block in every `SKILL.md` for real (compiles, resolves every
  `memory.*` reference and call signature against the live package).
- **QUA-10** — `pytest.ini` hardening: `slow` marker, `filterwarnings =
  error` with targeted ignores, a timeout default, and the suite's one
  permanent skip repointed at the shipped operator-pack assets (now a real
  packaging gate — the suite ends at 0 skipped).
- **DOC-6** — `CONVENTIONS.md` seeded into every corpus by `/hippo:init`,
  documenting the frontmatter schema, type taxonomy, floor rule, typed
  relations, and evidence-block convention as actually shipped.

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
