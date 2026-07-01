# Agent-Memory Activation Tooling

Local, no-server tooling over the markdown memory corpus at `.claude/memory/`. It
**activates** structure that already lives in the files (citations, provenance,
`[[wikilinks]]`, `description:` recall hooks) without changing the contract that
**markdown-in-git is the single source of authority** — everything here is derived,
rebuildable, and side-effect-light.

Design + rationale: [`docs/plans/active/agent-memory-activation-layer-2026-06-23.yaml`](../../docs/plans/active/agent-memory-activation-layer-2026-06-23.yaml)
and the exploration [`docs/plans/active/trustgraph-for-agent-memory-exploration-2026-06-23.md`](../../docs/plans/active/trustgraph-for-agent-memory-exploration-2026-06-23.md).

The **memory-organism** layer (instrument + immunize — see
[`docs/plans/active/memory-organism-instrument-immunize-2026-06-30.yaml`](../../docs/plans/active/memory-organism-instrument-immunize-2026-06-30.yaml)
and the architecture doc [`docs/plans/active/biology-informed-memory-architecture-2026-06-30.md`](../../docs/plans/active/biology-informed-memory-architecture-2026-06-30.md))
adds three organs on top of everything below, in shipped order:

1. **Measurement** ([Scorecard extensions + episode buffer](#scorecard-extensions--episode-buffer-tier-1-memory-organism-instrument-immunize)) — `eval_recall.py` gains precision@k / staleness-half-life / per-session-cost / graduation-rate (all report-only); `telemetry.py` gains a second ledger (the episode buffer) pinning a HEAD-commit watermark for a *future, not-yet-shipped* capture pass.
2. **Immune system** ([Reconsolidation worklist + graduation rate](#reconsolidation-worklist--graduation-rate-tier-2-memory-organism-instrument-immunize)) — `reconsolidate.py` re-grounds recently-recalled-and-stale memories against current code; a `demote` verdict can never silently clear the staleness flag.
3. **Graceful decay** ([Soft-invalidation + archive](#graceful-decay--soft-invalidation--archive-tier-3-memory-organism-instrument-immunize)) — `staleness.py`/`recall.py` add a soft (never-hard) recall penalty; `archive.py` adds a git-reversible, single-item, 4-way-gated `git mv` — never an autonomous sweep, never a delete.

Autonomous CAPTURE (the architecture doc's headline want — an LLM-driven extraction pass)
is **deliberately deferred** to its own future roadmap, gated on this layer proving green
across real, temporally-diverse sessions. See the changelog
[`changelog/2026-06-30-memory-organism-instrument-immunize.md`](../../changelog/2026-06-30-memory-organism-instrument-immunize.md)
for the measured scorecard snapshot and the exact unblock gate.

## Tier 1 — code-tied staleness + provenance (shipped)

The harness already warns "this memory is N days old" — but that's **calendar age**,
uncorrelated with whether the cited code actually changed. Tier 1 replaces it with a
**git-drift** signal.

### `provenance.py` — backfill citation provenance

Extracts `path:line` code citations from each memory **body** and records them as
**additive** frontmatter (`cited_paths`, `source_commit`). The body is never modified;
re-running is a no-op (idempotent); both frontmatter schemas in the corpus (a `metadata:`
block and the flat top-level style) are handled.

```bash
# preview (no writes)
./.venv/bin/python -m memory.provenance --dry-run
# apply
./.venv/bin/python -m memory.provenance
```

- `cited_paths` — the repo-relative code files the memory talks about (a bare basename
  resolves via `git ls-files`; an ambiguous basename keeps all candidates).
- `source_commit` — the memory file's own last-edit commit; the staleness baseline.

### `staleness.py` — the git-drift signal

A memory is **stale** when any of its `cited_paths` changed *after* its `source_commit`.
Two git calls total regardless of corpus size (path-change times + baseline-commit times),
then pure comparison. Never raises.

```bash
./.venv/bin/python -m memory.staleness
```

### `session_start.py` — the SessionStart hook dispatcher

ONE process, ONE corpus load, ONE merged `additionalContext`. Tier 1 registers the
staleness producer; later tiers **add a producer function here** (git-recent in Tier 2,
link-health in Tier 3) rather than registering a parallel hook. Self-suppresses when
there's nothing to say, bounds output under the harness's 10,000-char cap, and **always
exits 0**.

Wired via [`.claude/hooks/memory_session_start.sh`](../../.claude/hooks/memory_session_start.sh)
in `.claude/settings.json` (coexists with `agent_staleness.sh` — the harness concatenates
multiple SessionStart `additionalContext` values).

## Tier 2 — hybrid on-demand recall (shipped)

Instead of always-loading the whole index, the relevant subset is **recalled on demand**.
The durable floor (`MEMORY.md`, trimmed to the **User** + **Working-Style** memories + a
section map) is the only always-load; the project/reference long-tail is pulled per prompt.

### `build_index.py` — offline hybrid index builder

Builds a gitignored, rebuildable index at `.claude/.memory-index/` over each memory's
`name` + `description`:
- **dense** — `bge-small-en-v1.5` via `fastembed` (ONNX, no torch), and
- **sparse** — `rank-bm25` (already a repo dep).

It **warms the ~130 MB model cache OFFLINE** — a hook must never download. Incremental:
unchanged memories reuse their cached embedding; only edited files re-embed. With
`fastembed` absent it builds **BM25-only** (no error).

```bash
# one-time: enable the dense half (ISOLATED dep — never in the product lock)
./.venv/bin/pip install -r memory/requirements-memory.txt
# build + warm the model cache (re-run after writing memories; incremental)
./.venv/bin/python -m memory.build_index
```

**Auto-refresh at SessionStart:** you rarely need to run `build_index` by hand. The
SessionStart dispatcher calls `refresh_index()` on every start — an incremental, **offline**,
bounded, **never-downgrade** rebuild — so a memory written during one session is indexed
(and recallable) by the next. It is a fast no-op (~tens of ms, no model load) when nothing
changed, embeds only new/edited memories from the warm cache otherwise, and if a cold cache
can't embed offline it leaves the last good index in place rather than degrade it to BM25.
The initial `pip install` + `build_index` above is still the one-time setup that warms the
~130 MB model cache (a hook must never download it).

### `recall.py` — query-time fused recall

`recall(query, k=10)` → top-K via **RRF fusion** of dense + BM25, **degrading to BM25** when
the dense model/cache is unavailable. Never raises; output bounded < 10K. The dense path is
**wall-clock bounded** (`MEMOBOT_DENSE_TIMEOUT`, default 5 s) so a cold/wiped cache aborts to
BM25 instead of blocking the hook.

```bash
./.venv/bin/python -m memory.recall "how do we keep the memo writer from timing out"
```

Wired at **UserPromptSubmit** via [`.claude/hooks/memory_user_prompt.sh`](../../.claude/hooks/memory_user_prompt.sh)
— injects the top-K as `additionalContext`, **always exits 0** (exit 2 would erase the
prompt), and forces HF offline so it can't trigger a download.

`recall.py` also hosts the SessionStart **git-recent** producer (recently-captured
memories), registered into `session_start.py` alongside staleness.

### `eval_recall.py` — the 5 merge gates

```bash
./.venv/bin/python -m memory.eval_recall
```

Gates (all must pass to trust the recall path): synthetic self-recall@10 ≥ 0.90, curated
hard-set recall@10 ≥ 0.80 ([`fixtures/recall_hard_set.yaml`](../../tests/unit/memory_tools/fixtures/recall_hard_set.yaml)),
MRR@10 ≥ 0.60, net token reduction > 0, recall p95 < 300 ms (warm).

## Tier 3 — wikilink traversal + link lint (shipped)

Makes the `[[wikilinks]]` walkable and catches link rot — READ-ONLY (never edits a memory).

### `links.py` — the resolved wikilink graph

`build_graph(memory_dir)` parses `[[name]]` markers into adjacency and resolves each target,
slug-normalizing so `_`/`-` variants and a dropped category prefix resolve
(`[[151-avenue-a-is-standard-size]]` → `feedback_151_avenue_a_is_standard_size.md`) while
genuinely-absent targets (`[[ship-roadmap]]`) stay unresolved. `traverse(name, hops)` walks
N outbound hops.

```bash
./.venv/bin/python -m memory.links --traverse async-retrieval-migration --hops 2
```

### `lint_links.py` — link-integrity linter (read-only)

Flags **dangling** targets, **slug-mismatches** (resolve only via a soft alias), and
**orphans** (zero outbound links). Idempotent; never edits a file.

```bash
./.venv/bin/python -m memory.lint_links
```

Its one-line health summary is the SessionStart **link-health** producer, merged into the
single dispatcher `additionalContext` alongside staleness + git-recent.

## Measurement + creation hygiene (the 2026-06-29 layer)

On top of staleness/recall/links, four tools make the system measurable and keep new memories
from re-bloating the trimmed floor (see
[`changelog/2026-06-29-recall-telemetry-and-creation-convention.md`](../../changelog/2026-06-29-recall-telemetry-and-creation-convention.md)):

| Tool | What it does |
|------|--------------|
| `telemetry.py` | append-only ledger of every hook recall (names, backend, latency, truncated query) |
| `soak.py` | distinct-session **curation-soak bar** (≥5 = enough sessions to trust the dead-weight signal) + dead-weight curation report (CLI-only) |
| `lint_floor.py` | guards the `MEMORY.md` floor invariant (memory links only under User + Working-Style) |
| `new_memory.py` | writes a recall-ready memory; floor pointer **only** for user/feedback |

```bash
./.venv/bin/python -m memory.soak        # curation-soak status + dead-weight report
./.venv/bin/python -m memory.lint_floor  # floor-invariant check
```

## Recall telemetry (instrumentation)

The recall path retrieves reliably but was **blind to its own behavior in the wild**.
`telemetry.py` logs **one JSON line per hook recall** so we can see what it actually does.

### `telemetry.py` — the recall-event ledger

Each hook recall appends an event — `{ts, session_id, names, backend, latency_ms, k,
query_preview}` — to an append-only JSONL at `.claude/.memory-telemetry/recall_events.jsonl`
(its **own** gitignored sibling of the index, because it is append-only **history**, not a
rebuildable cache).

Contract (the hook depends on it):
- **Never raises / never delays** — logging fires *after* recall results are computed and is
  fully wrapped; an unwritable dir or a race degrades to a silent no-op and recall still
  returns its results.
- **No sensitive content** — only the surfaced memory **names**, the serving **backend**
  (`dense+bm25` / `dense` / `bm25` / `none`), latency, `k`, and a **truncated** query preview
  (first 80 chars) — never the full prompt.
- **Size-bounded** — the ledger caps at a byte ceiling (`MEMOBOT_TELEMETRY_MAX_BYTES`,
  default 2 MB) and **rotates** (keeps the recent tail), so it can never grow unbounded.

Wiring (no new hook — the existing entry points do the work):
- **`recall.main()`** (the `UserPromptSubmit` CLI/hook entry) fires `log_recall_event(...)`
  after results. Logging lives **only** in `main()` — **not** in `recall()` — so
  `eval_recall`'s direct `recall()` calls never pollute the ledger.
- **`session_start.main()`** calls `mark_session()` (a side effect, alongside
  `refresh_index()`) so each SessionStart opens a new ledger **session** — letting the
  ledger count distinct sessions (the curation-soak signal the analyzer reads).

Markdown-in-git stays the single source of authority; the ledger is derived, local, and
gitignored — deleting it loses only history. `read_events()` is the read surface the
soak/curation analyzer consumes.

### `soak.py` — soak ledger + curation report (read-only)

Reads the ledger into two decisions:

- **`soak_status()`** — distinct-session count and whether the **≥5-real-session** curation-soak
  bar is met (enough distinct sessions that the dead-weight signal below is minimally trustworthy
  rather than one-session topic noise — NOT an Option-C unblock gate).
- **`curation_report()`** — per-memory recall-hit counts, the **never-recalled** set (curation
  "dead weight" — read with the topic-bias caveat: cold tracks recent session mix, not value),
  and the **BM25-fallback rate** (dense unavailable on some session).

```bash
./.venv/bin/python -m memory.soak
```

`soak.py` is a CLI / analysis surface — it is **not** a SessionStart producer. The former
Option-C soak announcer was **removed**: the auto-extraction draft queue it advertised was killed,
so a met bar must never resurrect-by-accident a dead feature. Read-only over the ledger; never
raises; empty/missing ledger yields an empty report.

## Recall input hygiene + honest latency + staleness re-verify (the 2026-06-29 round 2)

Four small, ledger-justified refinements on top of the above (rationale + measurements in
[`docs/plans/active/memory-system-enhancement-exploration-2026-06-29.md`](../../docs/plans/active/memory-system-enhancement-exploration-2026-06-29.md)):

- **Query hygiene** (`recall.clean_query`) — `recall.main()` strips harness envelopes
  (`<task-notification>` tool-use blobs, fenced code, stray tags) and SKIPS recall entirely on
  near-empty / continuation prompts (`?`, `continue`) *before* embedding. ~a third of real prompts
  were paying a ~400 ms cold model load to inject pure semantic noise; now they cost nothing.
- **Floor-dedup** (`lint_floor.floor_memory_names`) — `recall.main()` drops User+Working-Style
  memories ALREADY always-loaded in the `MEMORY.md` floor from the per-prompt results (they were
  ~25 % of surfaced slots), topping off to `k` from the fused tail. DISPLAY-layer only — never
  inside `recall()`, so `eval_recall.self_recall` is byte-identical.
- **Honest cold latency** (`eval_recall.cold_latency`) — reports the REAL per-process model-load
  cost (~400 ms; a fresh subprocess per sample) alongside the warm p95 (~30 ms, which understates
  it ~10×). Report-only, never gated.
- **`--reverify NAME`** (`provenance.reverify_file`) — the verification-gated way to CLEAR a
  staleness flag (which `--refresh` deliberately CANNOT). After the memory's content is **re-read
  and confirmed to still match current code** — by the memory-master agent (see below) or a human —
  it re-baselines `source_commit` to **HEAD** ("verified current as of now"). Frontmatter-only, body
  byte-identical, refuses unparseable files, idempotent. Per-memory by design.
- **`--refresh-one NAME`** (`provenance.backfill_file(..., refresh=True)`) — the scoped sibling of
  `--refresh`: re-derives `cited_paths` for ONE memory only, `source_commit` untouched. Use this
  whenever a memory's body is hand-edited after creation (e.g. via `new_memory.py` with a
  placeholder body, then `Edit`) and its citations need picking up — plain `backfill_file` refuses
  to re-derive `cited_paths` on a file that already has the key, and corpus-wide `--refresh` was the
  ONLY way to force it before this flag existed, which meant re-deriving citations for every OTHER
  already-backfilled memory too (silently dropping references to any file since renamed/deleted,
  whether that review was wanted or not). `--refresh-one` never touches any file but the one named.

```bash
./.venv/bin/python -m memory.provenance --reverify <name>      # clear ONE memory after re-verifying it
./.venv/bin/python -m memory.provenance --refresh-one <name>  # re-derive ONE memory's citations only
```

> **There is intentionally NO bulk re-baseline** (no `--reverify-all`). A blind bulk pass would
> anchor `source_commit` to each file's last *touch* — but ~168/179 memories were last touched by a
> mechanical, body-identical commit (the provenance backfill / `--refresh` run), so that date is
> "when we ran the tooling," not when the content was written. Re-baselining to it **silences
> genuine drift** rather than draining an artifact. The staleness count is mostly a TRUE signal;
> the only correct way to clear a flag is to actually re-verify the memory's content against current
> code first, then `--reverify <name>`. Doing that *at scale* is the memory-master agent's job, not a
> CLI flag — see the staleness-resolution pass.

## Scorecard extensions + episode buffer (Tier 1, memory-organism-instrument-immunize)

`eval_recall.py` measured the recall INDEX (5 merge gates); it had no precision/coverage/cost
metric for the recall PATH itself. This layer adds three REPORT-ONLY scorecard metrics — they
extend `evaluate()`'s output dict, **never** the `gates` dict, and never change a gate
threshold — plus an episode buffer that starts soaking now so a future (separately
roadmapped, NOT yet shipped) autonomous-capture pass has something to replay.

### Scorecard metrics (`eval_recall.py`, report-only)

- **`precision_at_k`** — a GRADED measure: `|top-k ∩ relevant| / k`, averaged over
  [`fixtures/recall_relevance_set.yaml`](../../tests/unit/memory_tools/fixtures/recall_relevance_set.yaml)
  (hand-judged `{query, relevant: [name, ...]}` pairs — some list a real multi-memory
  cluster). Distinct from `hard_set_metrics`' binary recall@k (any ONE expected name in the
  top-k counts as a full hit) — precision rewards surfacing MORE of a relevant cluster, not
  just one member of it.
- **`staleness_half_life`** — the MEDIAN age (days) of the corpus's staleness baselines
  (`source_commit`) versus now. A half-life proxy: half the corpus's content baselines are
  younger than this figure, half are older. Memories with no baseline yet are excluded from
  the sample (not counted as age zero).
- **`session_token_cost`** — average recall-injection tokens **per session** (vs.
  `token_reduction`'s existing per-QUERY figure) — average recall events per session, read
  from the REAL telemetry ledger, times the average per-query token cost.

```bash
./.venv/bin/python -m memory.eval_recall   # now also prints the 3 report-only lines
```

### `telemetry.log_episode()` — the episode buffer

A SECOND, distinct ledger from the recall-event ledger above — `.claude/.memory-telemetry/episode_buffer.jsonl`.
The recall ledger records memory **names** surfaced per query; the episode buffer additionally
pins the repo **HEAD commit** at recall time, so a future capture pass has a watermark to diff
`git log <head_commit>..HEAD` against. It has to start soaking now even though nothing reads it
yet — there is no way to backfill it retroactively.

Same contract as the recall ledger: never raises, fire-and-forget (fires in `recall.main()`
right after `log_recall_event`, in the SAME wrapped block — never inside `recall()` itself, so
`eval_recall`'s direct `recall()` calls never pollute either ledger), size-bounded + rotated
(shares `_rotate_if_needed`), and no sensitive content — a truncated query preview only, same
80-char budget as the recall ledger, never the full prompt. `read_episodes()` is the read
surface a future consumer would use; nothing in this tier consumes it yet.

```python
{"ts": ..., "session_id": ..., "query_preview": "...", "recalled_names": [...], "head_commit": "..."}
```

### `soak.compute_strength_scores()` — topic-bias-resistant strength (report-only)

`curation_report()`'s `per_memory_hits` is a raw event Counter — a memory hit 5× in one chatty
session scores the same as one hit once across 5 distinct sessions, which over-weights
whatever a single session happened to be about. `compute_strength_scores(telemetry_dir)`
instead returns `{name: distinct_sessions_recalled / total_sessions}` — the numerator counts
**sessions**, not events, and the denominator is the FULL distinct-session pool (mirrors
`soak_status()`), not just sessions that recalled *something*. Report-only in the `soak` CLI;
no write, no ranking change — folding it into `recall()`'s ranking is a separate, explicitly
DEFERRED roadmap item (K2 in the architecture doc).

## Reconsolidation worklist + graduation rate (Tier 2, memory-organism-instrument-immunize)

The immune keystone — neutralizes two failure modes the architecture doc identified: a
birth-defect WRONG claim that passes `reverify_file`'s SYNTACTIC gate (it only checks "does
the cited code still match", never "is the content actually correct"), and a frequently-recalled
WRONG memory that grows its strength score and is the LAST thing curated (recall frequency
measures use, not correctness).

### `reconsolidate.py` — recall-filtered staleness, the "labile-on-recall" set

`recalled_stale_worklist(memory_dir, repo_root, telemetry_dir, window_sessions)` intersects
names **recently recalled** (the Tier-1 recall-event ledger, over the last `window_sessions`
sessions, default 10) with `staleness.find_stale()`'s stale set — memories ACTIVELY shaping
recent agent behavior AND whose cited code has drifted. This is the shipped
`claude_is_memory_master` re-grounding flow (read body + `git diff source_commit..HEAD` →
reverify / fix body + reverify / archive), just **triggered by recall** instead of only by
calendar SessionStart.

```bash
./.venv/bin/python -m memory.reconsolidate   # the worklist, read-only
```

`semantic_reverify(name, outcome, memory_dir, repo_root)` is the per-item write primitive —
`outcome` is one of `graduate` / `fix` / `demote`:
- **`graduate`** / **`fix`** route through the EXISTING `provenance.reverify_file()` (per-item,
  body byte-identical, refuses unparseable frontmatter) to clear the staleness flag, **then**
  log the verdict.
- **`demote`** does **NOT** call `reverify_file` — the staleness flag stays SET. Clearing it on
  a confirmed-WRONG memory would hide it from future staleness detection (the exact hole this
  tier exists to close); demotion is `staleness.set_invalid_after`'s job (Tier 3), not this
  function's. The verdict is still logged either way.

There is **no bulk variant** — `semantic_reverify` takes one `name: str`, never a list (mirrors
`reverify_head_only_no_bulk`: the per-item judgment is the memory-master agent's job, never an
autonomous sweep).

`reconsolidation_producer(memory_dir, repo_root)` is registered in `session_start.PRODUCERS`
(right after `staleness`) — **silent** unless a recently-recalled memory is currently stale,
otherwise a bounded, most-recently-drifted-first worklist block. The UserPromptSubmit recall
hot path is untouched: the intersection runs at SessionStart/CLI over the ledger, never
per-prompt.

### `eval_recall.graduation_rate()` — the accuracy axis (report-only)

`graduate / (graduate + demote)` over the reconsolidation outcome ledger
(`.claude/.memory-telemetry/reconsolidation_events.jsonl`, a THIRD sibling ledger, written by
`telemetry.record_reconsolidation_outcome()`). `fix` outcomes are excluded from both the
numerator and denominator by design — a fix is a distinct outcome (content was wrong, then
corrected), not a verdict on whether the originally-flagged content was right or wrong, which
is what this ratio measures. Report-only — never a gate threshold; folded into `evaluate()`'s
output alongside the Tier-1 scorecard metrics.

## Graceful decay — soft-invalidation + archive (Tier 3, memory-organism-instrument-immunize)

Decay is DEMOTION, never deletion. Soft-invalidation is a recall-time score penalty; archive
is a `git mv` into a tracked subdir. Neither ever deletes a memory.

### `staleness.set_invalid_after()` / `provenance.reverify_file()` — the tri-state contract

`invalid_after` is an ADDITIVE frontmatter timestamp (mirrors `cited_paths`/`source_commit`'s
top-level-or-`metadata:`-nested schema awareness; body byte-identical; refuses unparseable
frontmatter):

- **absent** — valid (the default; nothing changes for the overwhelming majority of the corpus).
- **set, < 30 days old** — **soft-invalid ("recent")** — still recallable, ranked lower.
- **set, ≥ 30 days old** — **soft-invalid ("old")** — dropped from recall DISPLAY only, still
  in the corpus/index.
- **cleared by `reverify_file`** — back to valid (a genuine re-verification re-opens the
  window; the mechanical `--refresh` path deliberately does NOT clear it — only an actual
  content re-check may).

```bash
./.venv/bin/python -m memory.staleness --invalidate <name>   # close the validity window
./.venv/bin/python -m memory.provenance --reverify <name>    # re-open it (existing primitive)
```

### `recall.py` — the soft-invalidation penalty (pre-cut, not a post-hoc relabel)

`_rrf_fuse` returns `[(index, fused_score), ...]` (widened from a bare index ordering) so a
penalty can be applied **before** the top-k cut — a recently-invalidated memory must be able
to actually fall out of top-k on its own demerits, not just get a cosmetic post-hoc label.
`_invalidation_state(entry)` classifies `"recent"` / `"old"` / `None` (absent or unparseable —
fails OPEN to valid). In `recall()`: `"recent"` halves the fused score *before* the cut and
re-sort; `"old"` is skipped in the emission loop (a walk-and-break, not a fixed slice-then-
filter, so old-invalidated entries never cause an under-fill). The corpus/index itself
(`idx.entries`, `_bm25_rank`, `_dense_rank`) is completely untouched either way — only the
returned `results` list is affected. `eval_recall`'s 5 gates are unaffected when no memory
carries `invalid_after` (the universal case today) — verified, not just asserted.

### `build_index.py` — `invalid_after` ingestion (metadata refresh decoupled from re-embed)

`compute_corpus()` re-reads every memory file from disk on every call; only the embedding
**row** is cache-reused (keyed by `hash(doc_text)`, where `doc_text` is name + description
only — `invalid_after` never enters that hash). So a metadata-only change (a fresh
`invalid_after`) is reflected on the very next build, including one where every embedding row
is a 100% cache-hit — no special-case refresh logic needed. Reads top-level or
`metadata:`-nested (mirrors `extract_description`'s exact fallback — the load-bearing fix: a
top-level-only read would make the field PERMANENTLY inert the moment a memory uses the
nested schema) and coerces a YAML-auto-typed `date`/`datetime` (the natural unquoted-date
authoring form) to its ISO string rather than silently dropping it or crashing `json.dump`.

### `archive.py` — the 4-way gate + git-reversible move

`archive_candidates(memory_dir, repo_root, telemetry_dir)` is a REPORT over the intersection
of:

- **cold** — never recalled (`soak.curation_report`'s `never_recalled`)
- **stale** — cites code that drifted (`staleness.find_stale`)
- **zero-inbound** — no OTHER memory `[[wikilinks]]` to it (a NEW inverted-adjacency
  computation — distinct from `links.LinkGraph.orphans()`, which is zero-*outbound*)
- **not-cited-by-CLAUDE.md** — the memory's filename isn't referenced (backtick-quoted, with
  or without `.md`) anywhere across `CLAUDE.md` + `.claude/rules/*.md` + `.claude/agents/*.md`
  + `.claude/skills/*.md` + `docs/prompts/*.md` — matched via backtick/code-span-anchored
  token extraction (never bare substring containment — a real collision risk in this corpus),
  fresh-read-per-call, failing CLOSED (treated as cited) on any read error

```bash
./.venv/bin/python -m memory.archive             # the candidate report (read-only)
./.venv/bin/python -m memory.archive --archive <name>   # per-item git mv, after review
```

`archive_memory(name)` is single-name-only — no batch/list/`--all` parameter exists anywhere
in this module (mirrors `reverify_head_only_no_bulk`); a bulk sweep would need a separately-
approved function, never this one. It `git mv`s into `.claude/memory/archive/` — a TRACKED
subdir the non-recursive `_iter_memory_files` already skips, so an archived memory instantly
drops from index/recall/staleness with zero other code change, and the move is fully
git-reversible (`git mv` it back). **Never fires automatically** — REPORT-then-move, per-item,
gated by the memory-master agent reviewing the report first. The roadmap's never-recalled
signal in particular must not be acted on until the ledger spans temporally diverse weeks
(today's window is a few days) — `archive_candidates` already requires ALL four conditions,
but the *cold* leg specifically is the least trustworthy signal this early.

## Writing a new memory (post-trim convention)

After the `MEMORY.md` trim, the floor is the **only always-loaded** memory context, so it
must stay lean: **memory pointers belong ONLY under `## User` and `## Working Style & Process
Feedback`.** The project/reference long-tail is **recalled on demand** (the recall hook +
SessionStart auto-refresh index it) and must **not** be added to the floor.

### `new_memory.py` — recall-ready creation, right-by-construction

```bash
./.venv/bin/python -m memory.new_memory my_slug "one-line recall hook" --type project
```

`write_memory(name, description, type, body)`:
- writes frontmatter the system depends on — `name`, `description` (**the recall hook**),
  `metadata.type`;
- backfills Tier-1 provenance (`cited_paths` / `source_commit`) so it's born staleness-tracked;
- refreshes the recall index so it's **immediately recallable**;
- appends a `MEMORY.md` floor pointer **ONLY for `user` / `feedback`** — `project` / `reference`
  are left off the floor (recalled on demand). Never silently overwrites an existing file.

### `lint_floor.py` — the floor-invariant guard (read-only)

Flags any non-allow-listed `](file.md)` link **outside** the two floor sections (re-bloat),
plus floor link rot (a pointer to a missing file). `MEMORY.full.md` / `MEMORY.md` restore
pointers are allow-listed. Its one-line summary is the SessionStart **floor** producer
(silent when the invariant holds). Never edits `MEMORY.md`.

```bash
./.venv/bin/python -m memory.lint_floor
```

## Environment overrides

- `MEMOBOT_MEMORY_DIR` — point the tooling at a different memory dir (hermetic tests use this).
- `CLAUDE_PROJECT_DIR` — repo root override (set by the harness); otherwise derived from git.
- `MEMOBOT_INDEX_DIR` — override the index cache location (default `.claude/.memory-index/`).
- `MEMOBOT_EMBED_MODEL` — dense model name (default `BAAI/bge-small-en-v1.5`).
- `MEMOBOT_DISABLE_DENSE=1` — force BM25-only (used by hermetic tests).
- `MEMOBOT_DENSE_TIMEOUT` — seconds before the dense query aborts to BM25 (default 5).
- `MEMOBOT_REFRESH_TIMEOUT` — seconds before the offline SessionStart embed aborts (default 15).
- `MEMOBOT_RECENT_DAYS` — window for the SessionStart git-recent producer (default 14).
- `MEMOBOT_TELEMETRY_DIR` — override the recall-telemetry ledger location (default
  `.claude/.memory-telemetry/`; hermetic tests use this).
- `MEMOBOT_TELEMETRY_MAX_BYTES` — ledger byte ceiling before it rotates (default 2 MB).

## Tests

Hermetic — each test builds a throwaway git repo + fixture memory files and sets
`MEMOBOT_MEMORY_DIR`; nothing reads the real `~/.claude` memory dir.

```bash
./.venv/bin/python -m pytest tests/unit/memory_tools/ -v
```
