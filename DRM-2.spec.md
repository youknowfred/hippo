# DRM-2 — Tier-A auto-apply + MCP notify-with-undo (implementation spec)

**Status: DRAFT spec for owner review.** The executable design for the reversible-autonomy
loop introduced in [`EXPLORATIONS.dream.md`](EXPLORATIONS.dream.md) §4. Scope is **Tier A
only** — the additive, body-preserving, ranking-only edges that are safe to apply
autonomously because undo is trivial. Tiers B/C (schemas, hypotheses, supersede/contradict)
are explicitly **out of scope** here and stay gated/quarantined (DRM-6 / `/resolve`).
Authored 2026-07-12.

Depends on: **DRM-1** (the replay harness + candidate-edge ledger; DRM-2 turns its
above-threshold candidates into applied, reversible edges). Verified reuses:
`links.add_typed_relation`, `archive.archive_memory` (git-mv reversibility pattern),
`soak.soak_status`, `lint_floor.floor_memory_names`, `mcp_server._tool_*`,
`session_start.*_producer`, `build_index` (index rebuild), `secrets.scan_text`.

---

## 1. What DRM-2 auto-applies (and what it never does)

| Candidate class (from DRM-1) | Auto-apply? | Mechanism |
|---|---|---|
| **Dangling-completion** — body already names target, just unlinked | ✅ highest precision | append to the memory's `<!-- dream:links -->` block |
| **Latent `refines`** — child memory generalized by an existing parent | ✅ if co-fire ≥ θ | `add_typed_relation(path, 'refines', target)` (frontmatter, additive) |
| **Transitive bridge** `[[wikilink]]` — A–B–C, no A–C, co-fire ≥ θ | ✅ if co-fire ≥ θ | append to `<!-- dream:links -->` block |
| Transitive bridge, co-fire < θ | ❌ ledger-only | logged as proposal, never written |
| `supersedes` (demotes a trusted memory) | ❌ **gated** | surfaced in digest, applied only on explicit owner action |
| `contradicts` | ❌ **routed** | → `/hippo:resolve` inbox, never auto |
| schema / gist / hypothesis (net-new body) | ❌ **out of scope** | DRM-6 (Tier B/C) |

**Two invariants that make Tier-A safe:**

1. **No prose mutation.** Auto-applied body edges live *only* inside a machine-managed,
   delimited block appended at end of body:
   ```
   <!-- dream:links -->
   [[other-memory]] <!-- dream: bridge · pass=p7 · q="…" · cofire=0.71 -->
   <!-- /dream:links -->
   ```
   Prose is never touched; undo = remove the exact stamped line(s). `refines` edges are
   pure additive frontmatter via `add_typed_relation` (already body-byte-identical).
   Neither perturbs `doc_text` (name+description), so the semantic index is stable; only
   the adjacency graph changes.
2. **Single-digit cap, threshold-gated.** At most **`DREAM_MAX_APPLY_PER_PASS` (default 5,
   hard max 9)** edges auto-apply per pass, and only candidates with co-fire strength ≥ θ
   (`DREAM_COFIRE_THETA`, calibrated in DRM-1). Everything else is ledger-only. **The empty
   pass must be the common outcome** — a non-empty digest is signal, not noise.

## 2. Preconditions checked before any write (all must hold)

- `soak.soak_status(...)['soaked']` is true (≥5 distinct sessions) — a young corpus
  proposes nothing.
- Target is not in `lint_floor.floor_memory_names()` (the always-loaded floor is never an
  edge endpoint — neither source nor target).
- The edge does not already exist (`add_typed_relation` is idempotent; wikilink block is
  checked against `parse_wikilinks`).
- The edge was **not** produced from a not-yet-aged `discovered-by:dream` source (the aging
  firewall, §5).
- `secrets.scan_text` over the generated rationale text returns clean — **hard BLOCK on
  the dream path** (deviation from hippo's WARN default; see EXPLORATIONS.dream.md §8
  correction 1 — **owner-ratified 2026-07-12**).
- Provenance is complete (§4) — an edge with a missing ledger field is rejected pre-write.

## 3. Apply mechanics & commit discipline

- **Effect is immediate, commit stays human.** DRM-2 writes edges to the **working tree**
  and rebuilds the index (`build_index`), so edges are live in recall this session. It does
  **not** auto-commit — the human's commit authority is preserved. The digest states plainly:
  *"N edges applied (uncommitted, live in recall)."*
- **Undo works regardless of commit state.** `dream --undo` removes the exact stamped
  lines/frontmatter targets from the working tree and rebuilds; it does not depend on a
  commit having happened. If the owner has since committed, `git revert` of the pass also
  works (each pass is greppable by `pass=<id>`).
- **Refuse-on-drift.** Undo refuses (no-op + report, per the no-silent-no-op invariant) if
  the stamped lines were manually edited since apply — never clobber a human edit.

## 4. Provenance & the ledger (the audit trail)

Committed, append-only: **`.claude/memory/dream-ledger.jsonl`** (tracked — the audit record;
add a gitignore un-ignore if the corpus dot-glob would sweep it). One line per applied edge:

```json
{"edge_id":"p7-e2","pass":"p7","kind":"bridge|refines|completion",
 "source":"mem-a","target":"mem-b","cofire":0.71,"firing_query":"…",
 "derives_from":["mem-a","mem-b"],"applied_at_session":"<sid>",
 "applied_at_distinct_count":42,"state":"active"}
```

- `state` ∈ `active | undone` (undo appends a superseding `undone` line — the ledger is
  append-only, never rewritten, so the audit history is intact).
- **Aging is derived, not stored** (§5) — no per-session ledger churn.
- Every on-disk edge carries the `pass=<id>` stamp inline, so `grep` reconciles the corpus
  against the ledger. A stamp with no ledger line (or vice versa) is a `doctor` finding.

## 5. The aging firewall (the keystone guardrail)

> An auto-applied edge influences **recall immediately**, but is excluded from **`/dream`'s
> own source set** until it *ages in*.

- **`aged_in(edge)` is a pure function:** `distinct_session_count_now −
  edge.applied_at_distinct_count ≥ DREAM_AGE_SESSIONS` (**default 5, ratified 2026-07-12**,
  reusing `soak`'s session-count source). No mutable state; recomputed each pass.
- **On the next `/dream` pass**, the candidate generator's source set =
  `confidence:verified` + user-asserted memories + `aged_in` dream edges. **Not-yet-aged
  dream edges are invisible to it** → `/dream` can never bootstrap on its own un-reviewed
  output → the dream-cites-a-dream tower cannot form.
- **Aging = implicit ratification by non-undo.** Surviving 5 sessions without an undo is the
  signal that the edge earned trust.

## 6. Notify-with-undo surfaces

- **Immediate — MCP `_tool_dream`** (`mcp_server.py`, same shape as `_tool_recall`): runs the
  pass, returns the digest text the user sees:
  ```
  🌙 dream pass p7 — applied 3 edges (uncommitted, live in recall):
    • auth-rotation ↔ token-refresh   bridge  (cofire 0.71, q:"how do tokens refresh")
    • ci-flake-retry → flaky-tests    refines (cofire 0.68)
    • gitignore-caches ↔ derived-dirs completion (body already named it)
    reply `undo` to revert all · `undo p7-e2` for one · they age into trust in 5 sessions
  ```
- **Deferred — SessionStart `dream_applied_producer`** (`session_start.py`,
  `_MAX_ITEMS_PER_PRODUCER`-bounded): lists edges applied since last session **that are not
  yet aged in**, with `dream --undo-since`. Aged-in edges drop off the nudge (now trusted).
- **CLI:** `dream --log` (every edge: active/aged/undone), `dream --undo[ <edge-id>]`,
  `dream --undo-since <date|Nsessions>`.

## 7. Acceptance criteria

- `dream` runs a pass and auto-applies **only** Tier-A candidates above θ, capped at
  `DREAM_MAX_APPLY_PER_PASS`; `supersedes`→digest-gated, `contradicts`→`/resolve`, schemas
  →not emitted. A test asserts no body prose byte changes outside the `dream:links` block
  and no frontmatter change outside the target `refines` list.
- A pass on a corpus below the soak bar, or touching a floor memory, applies **zero** edges
  and says so (no silent no-op).
- Every applied edge has a complete `dream-ledger.jsonl` line and an on-disk `pass=` stamp;
  a doctor check fails loudly on any stamp/ledger mismatch.
- `dream --undo` restores the pre-pass working tree byte-for-byte (asserted) and rebuilds the
  index; `--undo <id>` reverts exactly one; both refuse-with-report on manual drift.
- **Aging firewall test:** a not-yet-aged dream edge is provably absent from the next pass's
  candidate source set; after ≥`DREAM_AGE_SESSIONS` it is present. A dream edge undone before
  aging never entered the source set.
- **Secret BLOCK test:** a candidate whose rationale would emit a token-shaped string is
  refused pre-write (not merely warned).
- `_tool_dream` returns the digest; `dream_applied_producer` surfaces not-yet-aged edges
  within budget and drops aged-in ones.

## 8. Invariant notes

- **inv — reversible or gated, never neither.** An operation auto-applies *only if* undo is
  a mechanical line/target removal with no prose loss. Anything else is gated (Tier C) or
  out of scope (Tier B).
- **inv — no autonomous commit.** DRM-2 writes the working tree; committing stays the
  owner's act. Autonomy is over *effect*, not over git history.
- **inv — the source firewall is load-bearing.** If aging is ever bypassed, `/dream`
  consumes its own output and the speculation-tower guarantee is void. The firewall is a
  hard precondition, not a heuristic.
- **inv — measure-only stays measure-only.** DRM-2 changes the live corpus (that is its
  job); the DRM-3 `HIPPO_DREAM` A/B remains a separate, non-mutating measurement that reads
  the `discovered-by:dream` stamp to compute OFF/ON on the live corpus.
- **inv — the empty pass is the norm.** θ and the cap are tuned so most passes apply
  nothing; a non-empty digest must be rare enough that the owner reads it. This is the real
  backstop against inattention-drift (EXPLORATIONS.dream.md §5.1).
