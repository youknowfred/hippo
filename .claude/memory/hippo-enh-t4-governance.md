---
name: hippo-enh-t4-governance
description: "Enhancement Tier T4 (v1.4.0, \"A corpus you can govern\") — shipped 7/7 items (GOV-1..GOV-7), corpus_format 2→3→4, SCHEMA_VERSION 4→5→6; PR #12 MERGED (squash 6f4c9a3); next tier T5 (knowledge growth)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 4e201da2-f0f8-458f-9214-09afef5349af
  last_verified: "2026-07-16T14:40:05.887607+00:00"
  verified_by: "81190215_youknowfred_users.noreply.github.com@2026-07-18T23:11:14.627504+00:00"
  cited_paths: ["plugin/memory/resolve_view.py", "plugin/.claude-plugin/plugin.json", "plugin/memory/capture.py"]
  source_commit: "05b0c28349a9c3088a7870e5c0eee49859ce9bab"
  source_commit_time: 1784415607
---

Tier T4 (v1.4.0, "A corpus you can govern") — session 2026-07-08. **COMPLETE, 7/7, [PR
#12](https://github.com/youknowfred/hippo/pull/12) squash-MERGED to main as `6f4c9a3`
on 2026-07-09** (all CI green: dense lane + 4-way hermetic matrix + shellcheck; owner
directed the merge, head branch `enh-t4-governance` deleted remote+local, stale
remote-tracking ref pruned; the stray local `release-v0.7.0-team-and-fleet` leftover
from the v0.7.0 session was deleted too on owner direction — only `main` remains, local
AND remote). Shipped from 7 commits on top of T3's `7b5ca5c`. Build
order honored the audit note: GOV-6 LAST (it aggregates everything), GOV-2 before GOV-7
(two sequential clean-break pairs).

SHIPPED THIS SESSION (one id-prefixed commit each, suite green after each):
- **GOV-1** `a9120d2` — contradiction inbox + /hippo:resolve. `LinkGraph.all_typed_edges
  (relation)` (the one new graph primitive — deliberately NOT built on typed_unresolved,
  which records resolve() misses, not unverdicted edges); `memory/resolve_view.py`
  (canonical order-free pairs + declared_by; per-clone gitignored ledger
  `.resolve-ledger-<sha256(realpath(repo_root))[:16]>` under CLAUDE_PLUGIN_DATA — ONLY the
  mark-not-conflicting verdict lands there, and an unset CLAUDE_PLUGIN_DATA REFUSES the
  verdict loudly rather than forgetting it); `contradiction_inbox_producer` (counts every
  pair, skips re-PRINTING pairs the T2 rules_conflict radar already surfaced — producers
  share no state, so the subset is re-derived from conflict_radar); /hippo:resolve skill
  (verdicts: keep-A-supersede-B via reconsolidate demote --superseded-by + drop the edge;
  scope-both; merge; dismiss). resolve_view structurally has no corpus-write path
  (AST-pinned). WRITE-TIME-CONFLICT ARM DESCOPED per the note's sanctioned alternative
  (inbox = typed contradicts only; write-time neighbor warnings stay surfaced-at-write).
- **GOV-2** `5d33241` — steer:pin. **corpus_format 2→3 + SCHEMA_VERSION 4→5** (the tier's
  FIRST break pair). `_extract_steer` (closed enum: junk → unsteered, no user float ever
  reaches ranking); manifest carries steer; refresh_index no-op compare extended with
  old_steer==now_steer (3rd starvation-guarded field, both directions pinned); boost in
  the BASE penalized loop (`_PIN_BOOST` 1.2, `HIPPO_PIN_BOOST`, the `_mmr_lambda()` reader
  idiom — NOT _apply_salience, which is default-off); distinct `steer` result key; doctor
  ("steering", check_steering) pre-wires the mandatory MUTE count. Init's corpus_format
  literal + README history + the per-bump pin test (test_provenance) updated in lockstep —
  that pin test was a bump-blocker the explorer fan-out missed and only the REAL full
  suite caught (see gotcha 2).
- **GOV-3** `cdb3ea8` — proposals carry evidence. check_candidate gains `baseline` =
  provenance.git_head at PROPOSAL time (--check prints "baseline: as of HEAD <sha12>" or
  an honest non-git absence); write_memory/--rationale fences the WHY into the body as a
  trailing `Rationale:` line (_append_related_line's exact additive discipline, applied
  before Related); consolidate SKILL renders a per-proposal rationale block (seed episode
  evidence + --check neighbors/rules echoes/baseline) with a hunks hook for GRW-1.
- **GOV-4** `4e54690` — floor & corpus change governance. Per-clone watermark
  `.gov4-watermark-<key>` (same key derivation as the nudge counters, but the unset-env
  branch deliberately INVERTED: silent None, not fire-every-session); sorted-set diff
  (git-free), whole-file sha1 per project-tier floor pointer (the manifest hash is
  name+description only — it would miss exactly the body-edit case the always-loaded
  floor most needs caught); surfaced-once (watermark advances AFTER surfacing);
  `floor_change_peek` = the read-only delta for GOV-6 (never consumes the nag).
- **GOV-5** `d1c8482` — /hippo:why. describe(why=True): per-hit "won via <backend>",
  salience components, rule containment relabel ("containment 0.710 ≥ floor 0.60");
  note + "pinned ×1.2" now render ALWAYS (GOV-2's legibility contract — they were emitted
  but invisible). Abstention receipt honesty-ordered: UNTRUSTED (withheld — nothing
  scored) → no corpus → BM25-only ("no memory shares a token"; the match-set IS the
  floor) → dense near-miss → ≥-floor-but-display-filtered. Surfaces: --why flag, thin
  /hippo:why skill, 4th MCP tool `why` (exactly-N-tools test 3→4; delegation pinned
  byte-identical). **THE TIER'S ONE PREMISE CORRECTION** (recorded in-file as
  implementation_correction): the note's floors-disabled-recall()-re-run recipe quotes
  INCOMMENSURABLE numbers — recall emits RRF-FUSED scores (~1/60 scale, COR-8-pinned)
  while _dense_floor is a COSINE (0.5–0.6). Shipped receipt probes `index.dense @
  embed_query(query)` directly (entry rows only — the matrix is RET-2-widened with chunk
  rows), true cosine vs cosine floor, no env overrides needed at all.
- **GOV-7** `26a86c0` — author confidence tier. **corpus_format 3→4 + SCHEMA_VERSION 5→6**
  (the SECOND break pair, sequenced after GOV-2 exactly as coordinated). `confidence:
  draft|verified|authoritative` under metadata: (new_memory --confidence + MCP tool
  property; argparse choices + runtime validation → a bad tier REFUSES the write);
  `_extract_confidence` + manifest carry + 4th no-op-compare extension; compact
  " [draft]" bracket in format_results + recall_view tag. NEVER a ranking input, pinned
  TWICE: behaviorally (same corpus ± confidence → identical (name, score) lists) and
  structurally (AST: only recall() + format_results touch "confidence"; steer's readers
  pinned to recall() alone simultaneously).
- **GOV-6** `97eb57f` — trust scorecard, built LAST. `doctor._scorecard_message` → ONE
  deterministic line (the doctor render/line-count determinism pins force no-embedded-
  newlines — the "block" is middot-separated), registered directly after ("trust", ...):
  contested (→ /hippo:resolve) · rule↔memory conflicts (→ /hippo:consolidate) · rules rot
  (edit the named file) · blind spots (→ /hippo:consolidate) · orphans = links isolates ∩
  curation_report never_recalled, computed in the check (curation_report has NO graph
  awareness — the note's claim needed that correction) (→ /hippo:audit) · N pinned /
  N muted (muted structurally 0 until MUTE ships — absent renders as zero, self-heals) ·
  N draft · GOV-4 delta via floor_change_peek. Every input individually try/except-guarded
  → 0/absent, never an error. Plus SEC-1-gated `hippo://scorecard` MCP resource (same
  withhold prologue as rules-view; counts never leak past the gate; resource-set pins
  updated ×2).

SCHEMA/FORMAT BREAKS AS ACTUALLY LANDED (confirmed against the tree): corpus_format
**2→3 (GOV-2) →4 (GOV-7)**; index SCHEMA_VERSION **4→5 (GOV-2) →6 (GOV-7)** — the prompt's
corrected numbers held; the roadmap note's "3→4" for GOV-2 was indeed stale (T3's RCL-6
took 3→4). Each corpus bump is stamp-only additive (GRA-4 precedent, no migration code);
each index bump forces exactly one clean rebuild via the _load_manifest schema gate.

EVAL (golden corpus, real bge-small-en-v1.5, dense+bm25, this machine): self_recall@10
**0.98** (≥0.90) · hard_recall@10 **1.0** (≥0.80) · mrr@10 **0.9213** (≥0.60) ·
recall_p95 **28.14ms** (≤300) — ALL GATES PASS after GOV-2 (the tier's only
ranking-touching item). Before/after is byte-identical BY CONSTRUCTION on this corpus (no
steer keys anywhere in it; the no-boost-multiply-when-unpinned path is hermetically
pinned), so no separate baseline run was needed. NOTE the fixture drifted from T3's
capstone description: corpus=50/hard_set=18 here vs T3's "22 memories" note — the numbers
above are the current tests/golden_corpus truth.

ENGINE STATE: suite **1324 passed / 12 deselected** (T3 baseline 1245; +79). corpus_format
4; index SCHEMA_VERSION 6; **re-bootstrap NO** (requirements.txt untouched — T4 is
governance/presentation plane, exactly as expected). No plugin.json/CHANGELOG bump (T1–T3
precedent). New skills: resolve, why (pinned list now 10). MCP: 4 tools (why), 3 resources
(scorecard). MUTE and ALL read-side ranking beyond pin's bounded boost remain gated on the
salience keystone (SIG-5/T7) — pin shipped alone, exactly per the invariant_note.

DECISIONS / GOTCHAS:
(1) **Trust the pytest summary line, never the wrapper exit code** — one background "full
suite" run silently executed from the wrong cwd (a lingering `cd plugin` from an eval run)
and "passed" with `no such file or directory`; the commit built on it claimed a suite
count that a real run then contradicted (a missed CORPUS_FORMAT pin test in
test_provenance — a direct `== 2` assertion none of the seven seam explorers surfaced).
Re-ran for real, fixed, amended. The task brief literally warned about this failure mode.
(2) Pin-bound semantics sharpened by the smoke test: within RRF, ADJACENT same-class ranks
are near-ties by definition (≈1.016 ratio steps), so ×1.2 legitimately flips them — "can't
beat a strong organic hit" means multi-x fused gaps: cross-backend agreement vs
single-backend (2x), the dense floor, and stacked penalties (a pinned-but-superseded
memory stays below its successor: 0.5×1.2=0.6x — demonstrated live). Also observed live:
a heavily-stacked-penalty memory can vanish entirely when weak tail candidates trip the
knee cliff before the walk reaches it (RET-1 behavior, not a bug).
(3) The T2 CLAUDE_PROJECT_DIR gotcha never bit — every hermetic test passes dirs
explicitly or sets the env; conftest's autouse strips (_strip_ambient_plugin_env,
_isolate_memory_tiers, HIPPO_TRUST_ALL) did the rest.
(4) GOV-4's note-mandated inversion (CLAUDE_PLUGIN_DATA unset → silent, NOT the nudge
helper's fire-every-session) is load-bearing: without a durable baseline every session
would scream "everything changed".
(5) Producer isolation is real: producers cannot see each other's output, so GOV-1's
no-double-nag re-derives conflict_radar's contradicts subset instead of sharing state.
(6) Smoke script craft: `fail=1` inside a piped function dies in the subshell — count
failures via a file; `ls` hides dotfiles (the ledger check needs `ls -a`). The first
"ALL PASSED" banner was a lie; the re-run with a fail-file was genuine.

NEXT: **Tier T5 "Knowledge that grows itself" (v1.5.0)** — items [GRW-1, GRW-2, GRW-3,
GRW-8, GRW-4, GRW-5, GRW-6, GRW-7]. Read each GRW item's implementation_notes + T5's
audit_note FIRST (per protocol): GRW-1+GRW-4 SHARE the pending-seed schema bump (1→2 —
the gitignored queue's own schema, NOT a corpus_format event); GRW-3+GRW-8 share one
audit-skill neighbor sweep; GRW-5+GRW-6 share the SessionStart git-read moment;
capture.py must NEVER import new_memory (AST-pinned); NO body-rewrite primitive exists or
should be built; GRW-7 is narrowed (thread the successor's commit date onto the existing
invalid_after auto-stamp — reuses the shipped field, NO bump). GOV-3's rationale render
already carries the hunks hook GRW-1 will light up.

DEFERRED / BLOCKED: none — all 7 T4 items shipped (GOV-1's write-time-conflict arm
descoped within its note's sanctioned option, recorded above).

Related: [[hippo-enh-t3-retrieval]], [[hippo-enhancement-roadmap]].
