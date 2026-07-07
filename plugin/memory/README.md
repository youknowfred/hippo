# The hippo memory engine

Local, no-server tooling over the markdown memory corpus at `.claude/memory/` in the
**consuming project**. It **activates** structure that already lives in the files
(citations, provenance, `[[wikilinks]]`, `description:` recall hooks) without changing the
contract that **markdown-in-git is the single source of authority** — everything else
(index, telemetry, graph caches) is derived, rebuildable, and gitignored.

This package ships inside the hippo Claude Code plugin (`plugin/memory/`, imported as
`memory`). The skills (`/hippo:bootstrap|init|new|doctor|audit`) and hooks
(`plugin/hooks/`) are thin orchestration over the CLI entry points documented here.

## Running the commands in this doc

Inside a Claude Code session (skills, agents), the plugin env vars are set. Every code
block below assumes this one-time shell setup:

```bash
PY="${CLAUDE_PLUGIN_DATA}/venv/bin/python"   # built by /hippo:bootstrap
export PYTHONPATH="${CLAUDE_PLUGIN_ROOT}"    # so `import memory` resolves to this package
```

The four stateless commands also have a launcher — `"${CLAUDE_PLUGIN_ROOT}/bin/hippo"
<recall|new|build-index|staleness>` — which does the same resolution internally (and falls
back to bare `python3` pre-bootstrap: BM25-only via the vendored fallbacks in
[`_vendor/`](_vendor/__init__.py)).

In a **dev checkout of this repo**, use `PY=.venv/bin/python` and `PYTHONPATH=plugin`
instead.

## Hybrid recall

### `build_index.py` — offline hybrid index builder

Builds a gitignored, rebuildable index at `.claude/.memory-index/` over each memory's
`name` + `description`:
- **dense** — `bge-small-en-v1.5` via `fastembed` (ONNX, no torch), and
- **sparse** — BM25 (`rank-bm25`, or the vendored scorer pre-bootstrap).

Incremental: unchanged memories reuse their cached embedding row (keyed by content hash);
only edited files re-embed. With `fastembed` absent/disabled it builds **BM25-only** (no
error). The index dir is self-ignoring (it drops a `.gitignore` containing `*` on first
creation) so it stays invisible to git even in projects that never patched `.gitignore`.

```bash
"$PY" -m memory.build_index --memory-dir .claude/memory --index-dir .claude/.memory-index
```

**Auto-refresh at SessionStart:** you rarely run `build_index` by hand. The SessionStart
dispatcher calls `refresh_index()` on every start — incremental, **offline**, bounded,
**never-downgrade** — so a memory written during one session is indexed (recallable) by
the next. A fast no-op when nothing changed; if a cold cache can't embed offline it leaves
the last good index in place rather than degrade it to BM25. The model cache itself is
warmed ONCE, online, by `/hippo:bootstrap` (a hook must never download — see
[Appendix A](#appendix-a--the-durable-model-cache-gotcha)).

### `recall.py` — query-time fused recall

`recall(query, k=10)` → top-K via **RRF fusion** of dense + BM25, **degrading to BM25**
when the dense model/cache is unavailable. Never raises; output bounded < 10K chars. The
dense path is **wall-clock bounded** (`MEMOBOT_DENSE_TIMEOUT`, default 5 s) so a cold or
wiped cache aborts to BM25 instead of blocking the hook.

```bash
"$PY" -m memory.recall "how do we keep the memo writer from timing out"
```

Wired at **UserPromptSubmit** via [`../hooks/memory_user_prompt.sh`](../hooks/memory_user_prompt.sh)
(registered in [`../hooks/hooks.json`](../hooks/hooks.json)) — injects the top-K as
`additionalContext`, **always exits 0** (exit 2 would erase the user's prompt), and forces
HF offline so it can never trigger a download.

`recall.main()` (the CLI/hook entry, never `recall()` itself) also applies **query
hygiene** (`clean_query` strips harness envelopes and skips near-empty/continuation
prompts before any model load) and **floor-dedup** (memories already always-loaded in the
`MEMORY.md` floor are dropped from per-prompt results, topping back up to `k`).

### `eval_recall.py` — the 5 merge gates

```bash
"$PY" -m memory.eval_recall
```

Gates: synthetic self-recall@10 ≥ 0.90, curated hard-set recall@10 ≥ 0.80, MRR@10 ≥ 0.60,
net token reduction > 0, recall p95 < 300 ms (warm). Two kinds of input are **skipped
honestly** rather than failed (reported as `➖ skipped`, excluded from the RESULT):

- **hard-set gates** when no fixture exists — resolution probes the project-local
  `.claude/memory/.audit-fixtures/` (written by `/hippo:audit`), then the engine repo's
  [`tests/fixtures/`](../../tests/fixtures/recall_hard_set.yaml), else skips;
- **token_reduction** when the corpus has no `MEMORY.full.md` pre-trim snapshot (every
  fresh install) — there is nothing to compare the trimmed floor against.

`cold_latency` is reported alongside (fresh subprocess per sample — the REAL per-prompt
hook cost, ~10× the warm p95) but never gated. Report-only scorecard extras: precision@k
(graded, from `recall_relevance_set.yaml`), staleness half-life, per-session token cost,
and the reconsolidation graduation rate.

## Staleness + provenance

### `provenance.py` — citation provenance backfill

Extracts `path:line` code citations from each memory **body** and records them as
**additive** frontmatter (`cited_paths`, `source_commit`). The body is never modified;
re-running is a no-op; both frontmatter schemas (a `metadata:` block and flat top-level)
are handled.

```bash
"$PY" -m memory.provenance --dry-run    # preview
"$PY" -m memory.provenance              # apply
```

- `cited_paths` — repo-relative code files the memory talks about (a bare basename keeps
  only an UNambiguous `git ls-files` resolution; ambiguous basenames are dropped).
- `source_commit` — the file's own last-edit commit, else **HEAD** ("reflects code as of
  now") when the file has no commit history yet — memories are born staleness-tracked
  even in a dirty worktree. A residual empty baseline (pre-0.2.0 files, or a corpus
  seeded before its repo's first commit) is **healed to HEAD at SessionStart** once
  resolvable; healing can never silence a flag, because an empty baseline never flags.

### `staleness.py` — the git-drift signal

A memory is **stale** when any of its `cited_paths` changed *after* its `source_commit` —
git drift, not calendar age. Two git calls total regardless of corpus size, then pure
comparison. Never raises.

```bash
"$PY" -m memory.staleness
```

### Clearing a flag: `--reverify` / `--refresh-one`

```bash
"$PY" -m memory.provenance --reverify <name>      # clear ONE memory after re-verifying it
"$PY" -m memory.provenance --refresh-one <name>   # re-derive ONE memory's citations only
```

- `--reverify NAME` — the verification-gated way to CLEAR a staleness flag (which
  `--refresh` deliberately CANNOT): after the content is re-read and confirmed to match
  current code, it re-baselines `source_commit` to HEAD and re-opens the
  soft-invalidation window. Frontmatter-only, body byte-identical, refuses unparseable
  files, per-memory by design — see
  [Appendix B](#appendix-b--why-there-is-no-bulk-re-baseline).
- `--refresh-one NAME` — re-derives `cited_paths` for one memory (e.g. after hand-editing
  its body), `source_commit` untouched. The corpus-wide `--refresh` does the same for
  every already-backfilled memory.

## The SessionStart dispatcher

### `session_start.py`

ONE process, ONE corpus load, ONE merged `additionalContext`. Producers, in order:
`stale_venv` (deps changed since bootstrap → re-bootstrap nudge), `integrity`
(unparseable frontmatter — surfaced FIRST among corpus signals so a malformed memory
can't hide), `staleness`, `reconsolidation` (recall-filtered staleness worklist),
`git_recent`, `link_health`, `floor`. Self-suppresses when no producer has anything to
say; bounds output under the harness's 10,000-char cap; **always exits 0**. Side effects
(not producers): heal empty baselines, `refresh_index()`, `mark_session()`.

Wired via [`../hooks/memory_session_start.sh`](../hooks/memory_session_start.sh), which
also owns the **first-run nudge**: venv/sentinel missing → "run /hippo:bootstrap";
bootstrapped but no corpus → "run /hippo:init" — at most once per 5 sessions, permanently
dismissable, emitted before Python is even involved.

## Wikilink graph

### `links.py` / `lint_links.py`

`build_graph(memory_dir)` parses `[[name]]` markers into adjacency, slug-normalizing so
`_`/`-` variants and dropped category prefixes resolve. Every node is a filename **stem**
(`foo`, never `foo.md`), the same identity staleness/soak/archive key by — graph output
joins against their name sets with no conversion. `traverse(name, hops)` walks N outbound
hops; `inbound(name)` answers "what refers to this memory?" from a reverse adjacency built
once at construction (the codebase's single adjacency inversion); `orphans()` is
zero-OUTBOUND (may still be well-cited), `isolates()` is zero-in AND zero-out (genuinely
disconnected). Soft aliases (prefix-strip / `name:` slug) claimed by two or more files
are **ambiguous** — `resolve()` refuses them rather than guess (a full-stem claim still
beats any soft claim). `lint_links` flags **dangling** targets, **ambiguous** targets
(naming every claimant), **slug-mismatches**, and **orphans** — read-only, never edits a
memory; its one-line summary is the `link_health` producer.

```bash
"$PY" -m memory.links --traverse <name> --hops 2
"$PY" -m memory.lint_links
```

## Writing memories

### `new_memory.py` — recall-ready creation

```bash
"$PY" -m memory.new_memory my_slug "one-line recall hook" --type project --body "the WHY"
```

Writes the frontmatter the system depends on (`name`, `description` — **the recall
hook** — and `metadata.type`), backfills provenance (born staleness-tracked), refreshes
the index (**immediately recallable**), and appends a `MEMORY.md` floor pointer **only
for `user`/`feedback`** types. Never overwrites an existing file. Prefer the
`/hippo:new` skill, which wraps this with the what-not-to-save judgment.

### `lint_floor.py` — the floor invariant

The `MEMORY.md` floor is the only always-loaded memory context, so it stays lean: memory
pointers belong ONLY under `## User` and `## Working Style & Process Feedback`;
`project`/`reference` memories are recalled on demand. `lint_floor` flags re-bloat and
floor link rot (read-only); its summary is the `floor` producer.

```bash
"$PY" -m memory.lint_floor
```

## Telemetry (three ledgers)

`telemetry.py` writes append-only, size-bounded (rotating), gitignored JSONL ledgers under
`.claude/.memory-telemetry/` — derived local history, never authority:

| Ledger | Written by | Content |
|---|---|---|
| `recall_events.jsonl` | `recall.main()` after results | `{ts, session_id, names, backend, latency_ms, k, query_preview}` |
| `episode_buffer.jsonl` | `recall.main()`, same block | adds the repo HEAD watermark — soak data for the future capture pass |
| `reconsolidation_events.jsonl` | `record_reconsolidation_outcome()` | `{ts, name, outcome}` verdicts |

Contract: never raises, never delays a recall (fires after results, fully wrapped), no
sensitive content (an 80-char query preview, never the full prompt). **Hygiene:** a
project with no `.claude/memory` corpus gets NO ledgers at all, and the telemetry dir is
self-ignoring (a `.gitignore` containing `*` inside it) so `git add .` can never commit
prompt previews.

### `soak.py` — soak status + curation report (read-only)

Distinct-session count vs the **≥5-session** curation-soak bar, per-memory recall hits,
the never-recalled set (dead-weight candidates — clone-local, topic-biased; read with
care), and the BM25-fallback rate. `compute_strength_scores()` returns
`{name: distinct_sessions_recalled / total_sessions}` — sessions, not events, so one
chatty session can't inflate a memory's strength.

```bash
"$PY" -m memory.soak
```

## Reconsolidation (the immune system)

### `reconsolidate.py`

`recalled_stale_worklist(...)` intersects recently-recalled names (the recall ledger,
last N sessions) with the stale set — memories actively shaping behavior whose cited code
drifted. Read-only CLI + the `reconsolidation` producer (silent unless non-empty).

```bash
"$PY" -m memory.reconsolidate
```

`semantic_reverify(name, outcome, ...)` is the per-item write primitive — `graduate` /
`fix` route through `reverify_file` (clears the flag), **`demote` does NOT** (a
confirmed-wrong memory must stay visible to staleness; demotion is
`set_invalid_after`'s job). Verdicts land in the reconsolidation ledger;
`eval_recall.graduation_rate()` reports `graduate / (graduate + demote)` (fixes excluded
by design). No bulk variant exists.

## Graceful decay — soft-invalidation + archive

Decay is DEMOTION, never deletion.

- `staleness.set_invalid_after(name)` stamps an additive `invalid_after` frontmatter
  timestamp: **< 30 days old** → still recallable, fused score halved BEFORE the top-k
  cut (real demotion — it can fall out of top-k); **≥ 30 days** → dropped from recall
  display only, still in corpus/index; **cleared** by a genuine `--reverify`.
- `archive.py` — `archive_candidates` reports the intersection of four gates (cold ∧
  stale ∧ zero-inbound ∧ not-cited-by-instructions, the last failing CLOSED on read
  errors); `--archive <name>` is a per-item, git-reversible `git mv` into
  `.claude/memory/archive/` (a tracked subdir the corpus iterator skips). Never fires
  automatically; no `--all` exists.

```bash
"$PY" -m memory.staleness --invalidate <name>
"$PY" -m memory.archive                       # report only
"$PY" -m memory.archive --archive <name>      # per-item move, after review
```

## Degraded modes (all legible, none fatal)

| State | Behavior |
|---|---|
| No bootstrap (bare `python3`) | BM25-only recall via [`_vendor/`](_vendor/__init__.py) (score-identical scorer + frontmatter-subset parser); SessionStart nudges `/hippo:bootstrap` |
| Model cache cold/wiped | Dense aborts within the wall-clock bound → BM25; the index never downgrades; `/hippo:doctor` flags the cache |
| Deps changed after update | The `stale_venv` producer nudges a re-bootstrap once per session |
| No `.claude/memory` corpus | Hooks stay inert: no index, no ledgers, zero files created; SessionStart nudges `/hippo:init` |
| Untrusted corpus (SEC-1) | A cloned/foreign git corpus injects NOTHING until trusted: recall returns `[]`, producers stay silent; a low-frequency SessionStart nudge points at `/hippo:doctor` (count + sample names → one-time consent). `/hippo:init` trusts corpora you create; `MEMOBOT_TRUST_ALL=1` bypasses for CI |
| Unparseable frontmatter | Skipped by staleness AND refused by refresh/reverify; the `integrity` producer names the file loudly |

## Environment overrides

- `MEMOBOT_MEMORY_DIR` — point the tooling at a different memory dir (hermetic tests).
- `CLAUDE_PROJECT_DIR` — repo root override (set by the harness); else derived from git.
- `MEMOBOT_INDEX_DIR` — override the index location (default `.claude/.memory-index/`).
- `MEMOBOT_EMBED_MODEL` — dense model name (default `BAAI/bge-small-en-v1.5`).
- `MEMOBOT_DISABLE_DENSE=1` — force BM25-only (hermetic tests, CI).
- `MEMOBOT_DENSE_TIMEOUT` — seconds before a dense query aborts to BM25 (default 5).
- `MEMOBOT_REFRESH_TIMEOUT` — overall wall-clock budget for the offline SessionStart embed;
  exhausting it stops starting new chunks but keeps whatever already embedded (default 15).
- `MEMOBOT_EMBED_CHUNK_SIZE` — docs per offline embed slice, so a large corpus persists
  partial dense progress across sessions instead of an all-or-nothing attempt (default 64).
- `MEMOBOT_RECENT_DAYS` — window for the git-recent producer (default 14).
- `MEMOBOT_TELEMETRY_DIR` — override the ledger location (default `.claude/.memory-telemetry/`).
- `MEMOBOT_TELEMETRY_MAX_BYTES` — ledger byte ceiling before rotation (default 2 MB).
- `MEMOBOT_TRUST_ALL=1` — bypass the SEC-1 foreign-corpus trust gate entirely (CI/automation);
  recall injects from any corpus without requiring a trust marker.
- `MEMOBOT_TRUST_FILE` — relocate the machine-local trust registry (default
  `~/.claude/hippo-trust.json`); hermetic tests point it at a tmp path.
- `FASTEMBED_CACHE_PATH` — model cache override (default `${CLAUDE_PLUGIN_DATA}/fastembed`).

## Tests (dev checkout)

Hermetic — each test builds a throwaway git repo + fixture memories and points the tooling
at it; nothing reads a real corpus, and the one network-capable test is deselected by
default (`-m "not network"`).

```bash
.venv/bin/python -m pytest
```

---

## Appendix A — the durable model-cache gotcha

*(An origin-repo lesson, inlined because the wikilink that used to carry it doesn't ship.)*

With `FASTEMBED_CACHE_PATH` unset, fastembed caches the ~130MB ONNX model under
`$TMPDIR/fastembed_cache` — on macOS that's `/var/folders/...`, which the OS purges on a
schedule. The hooks are offline by hard contract (a hook must NEVER download), so once the
tmp cache is wiped, nothing on the hook path can ever re-fetch the model: dense recall
silently degrades to BM25 forever, with no error anywhere. That failure mode is why:

- `/hippo:bootstrap` pins the cache to `${CLAUDE_PLUGIN_DATA}/fastembed` (survives plugin
  updates AND reboots) via `ensure_fastembed_cache_path()`;
- both hook scripts export the same precedence **before** Python starts (bash/python
  parity is enforced by test);
- `/hippo:doctor` checks the cache dir explicitly and names this exact failure mode.

## Appendix B — why there is no bulk re-baseline

*(An origin-repo lesson, inlined for the same reason.)*

In the origin corpus, ~168 of 179 memories were last touched by a mechanical,
body-identical commit — the provenance backfill itself. A "reverify everything" pass that
re-baselines `source_commit` to each file's last touch would anchor to *"when we ran the
tooling"*, not *"when the content was verified"* — silencing genuine drift wholesale
rather than draining an artifact. The staleness count is mostly a TRUE signal; the only
correct way to clear a flag is to actually re-read the memory against current code, then
`--reverify <name>` — one memory at a time, judgment applied to each. That is why
`semantic_reverify` takes one name (never a list), `archive_memory` is single-item, and no
`--all` flag exists anywhere in this engine: verification cannot be done in bulk, so the
primitives refuse to pretend it can.
