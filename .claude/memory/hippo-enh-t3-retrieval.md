---
name: hippo-enh-t3-retrieval
description: "Enhancement Tier T3 (v1.3.0, \"Retrieval precision\") — shipped 6/6 items (RCL-1..RCL-6), SCHEMA_VERSION 3->4; next tier T4 (corpus governance)"
metadata:
  type: project
  originSessionId: bf62e899-a4d7-4e40-b77c-d282f2d09f95
  last_verified: "2026-07-16T14:40:04.618669+00:00"
  cited_paths: ["plugin/.claude-plugin/plugin.json"]
  source_commit: "f99bfa89571ac19cadf0967693103b9b9bd464f1"
  source_commit_time: 1784246873
---

Tier T3 (v1.3.0, "Retrieval precision (hot-path-safe)") — session 2026-07-08. **COMPLETE,
6/6, [PR #11](https://github.com/youknowfred/hippo/pull/11) squash-MERGED to main as
`907f509`** (all CI green: dense lane + 4-way hermetic matrix + shellcheck; owner directed
the merge directly this session, then had the head branch `enh-t3-retrieval` deleted —
remote via `--delete-branch`, local auto-cleaned by `gh pr merge`, stale remote-tracking ref
pruned — only `main` remains). Shipped from branch `enh-t3-retrieval` (6 commits on top of
T2's `fc0fc33`).

SHIPPED THIS SESSION (one id-prefixed commit each, suite green after each):
- **RCL-1** `9db0db7` — per-query dense/lexical intent routing: a density ratio
  (`len(_mine_identifiers(query)) / len(tokenize(query))`) recomputed fresh inside `recall()`
  (the one chokepoint every surface shares) leans the RRF fusion's first two weights lexical
  (symbol/error-heavy) or dense (prose), with a dead-band returning the exact balanced
  `(1.0, 1.0)`. TWO guards the roadmap note didn't anticipate, both caught by EXISTING
  hermetic tests (not the eval gates): (a) must no-op when only one backend (dense/bm25) has
  candidates — else it rescales the lone contributor's score for nothing
  (`test_recall_emitted_score_equals_true_penalized_fused_score` caught this); (b)
  `_INTENT_MIN_TOKENS` had to be **8**, not 4 — a short-but-plain technical phrase (6 tokens,
  0 mined identifiers, e.g. "oauth token refresh flow policy gateway") is common enough that
  leaning dense on it measurably reorders the fused list and can shift the knee's cliff
  comparison (`test_recall_superseded_ranks_below_successor_dense_path` caught this).
- **RCL-2** `f1f0e8e` — floor-dedup collapse + within-session injection cooldown: converts
  `main()`'s silent floor-dedup DROP into a legible "(already in floor)" collapse, widens the
  floor with `archive._cited_by_claude_md_names`, and adds a session-scoped
  "(already surfaced this thread)" cooldown seeded from the episode buffer (via the new
  `_session_episodes()` helper, shared with RCL-3 exactly as the audit_note anticipated).
  Rule pointers stay exempt from floor-dedup but cool down like any other hit (T2 guard).
- **RCL-3** `d9012f0` — multi-turn query rescue: when `clean_query` blanks the prompt OR
  still yields fewer than `_RESCUE_MIN_TOKENS` (4) content tokens, blends the raw prompt with
  the last `_RESCUE_TURNS` (3) same-session `query_preview`s and re-runs `clean_query` on the
  combined text. Reuses RCL-2's `_session_episodes()` read. A substantive prompt is
  byte-identical with or without prior session history.
- **RCL-4** `270c5ba` — MMR intra-block diversity re-rank. **The most significant correction
  this tier**: the roadmap note's literal instruction ("re-order `penalized`... so the knee
  measures gaps on the diversified order") is WRONG — running MMR *before* the knee cutoff
  lets a diversity-promoted low-relevance candidate create a false "cliff" that silently
  drops a genuinely relevant result sitting right behind it. Caught by TWO EXISTING hermetic
  dense-path fixtures (a 3-entry Japanese-corpus test that lost `tokyo_weather` down to 1
  result; a supersession test that lost `old_way` entirely) — **not by the eval gates, which
  stayed green throughout** (hard_recall/MRR dipped from 1.0/0.894 to 0.864/0.841, still
  above the 0.80/0.60 thresholds). Fixed with a two-pass redesign: the knee/old/dangling-file
  admission walk runs FIRST in the TRUE organic order (admitting up to `pool_n = max(k *
  _MMR_POOL_MULT, k)`, not just `k`), THEN MMR diversifies within that already-vetted,
  possibly-larger-than-k pool. Also recalibrated `_MMR_LAMBDA` from the proposal's ~0.7 to
  **0.8** — even correctly sequenced, 0.7 still measurably eroded golden-corpus recall; 0.8
  recovered hard_recall@10 to the pre-RCL-4 1.0 and MRR to within ~0.02 of baseline while the
  adversarial near-paraphrase fixture (two memories on one decision + one distinct memory)
  still diversifies correctly at k=2.
- **RCL-6** `b1577d6` — evidence-snippet for the top body-hit. **TWO premise corrections**:
  (a) the note's confident claim "body-chunk TEXT is ALREADY PERSISTED" is FALSE against the
  live tree — `build_index` deliberately stripped `"text"` before writing `body_chunks` to
  the manifest (a RET-2 design decision: "reconstructible from the source file," not an
  oversight). Fixed by adding `"text"` back (now `{entry,hash,tokens,row,text}`) — this
  *is* the tier's one `SCHEMA_VERSION` bump (3→4), alongside a new manifest-wide
  `head_commit` (one `git rev-parse HEAD` per BUILD, mirroring `telemetry.log_episode`'s
  pattern). (b) the proposed score-band (`1.5/(RRF_K+1) ≈ 0.025`, calibrated against `RRF_K`
  alone) would set a bar NO body-win entry could ever clear — a body-win's entire score is
  capped by `_BODY_RRF_WEIGHT`'s 0.5x discount (ceiling ≈0.016 for agreement across both body
  rankings, ≈0.008 for a real single-lane rank-0 hit, confirmed empirically against a live
  fixture). Recalibrated to derive from `_body_rrf_weight() / (_RRF_K+1)` (a 0.6 fraction of
  a single body ranking's own rank-0 ceiling) so the default scales correctly if
  `HIPPO_BODY_RRF_WEIGHT` is ever tuned. Plumbed the winning-chunk index out of
  `_dense_rank_body`/`_bm25_rank_body` via a new `winning_chunk_out` param (caller-supplied
  dict, mutated in place) rather than changing their return shape.
- **RCL-5** `7d598e2` — offline cross-encoder rerank (`Xenova/ms-marco-MiniLM-L-6-v2`, 0.08GB)
  on `recall_view.describe()` — the one chokepoint `/hippo:recall` AND the MCP recall tool
  share (confirmed via a same-cwd smoke check: identical output for identical queries). DEP
  CHECK clean: installed fastembed 0.7.4 already exposes `TextCrossEncoder`; no
  requirements.txt bump. ONE empirical finding not anticipated by "mirror `_get_model`
  exactly" as a stylistic nicety — it turned out LOAD-BEARING: `HF_HUB_OFFLINE=1` correctly
  blocks the real network reach, but fastembed's OWN model loader wraps that local failure in
  a retry-with-backoff sleep loop regardless (~40s: 3s+9s+27s), confirmed by direct
  measurement. Without `_get_model`'s cheap pure-filesystem pre-check
  (`_cross_encoder_cached`, mirrored exactly), a cold cache on an EXPLICIT surface a human is
  actively waiting on would hang ~40s instead of degrading in ~100 microseconds (pinned by
  `test_get_cross_encoder_offline_raises_in_microseconds_on_cold_cache`). Real model exercised
  network-marked and confirmed a genuinely on-topic description outranks an off-topic one.
  Added a best-effort warm-up step to `bootstrap/SKILL.md` (no other trigger ever downloads
  this model, so a fresh install would otherwise degrade to the un-reranked order forever).

TIER STATE: 6/6 done; T3 status flipped to `done`; done_means MET — every injected token
earns its place (intent routing, floor-dedup+cooldown, terse-follow-up rescue, MMR
diversity, all hot-path-safe and eval-gated) plus body-hit evidence snippets and an
off-hot-path cross-encoder on the explicit surfaces.

EVAL BEFORE/AFTER (golden corpus: 22 memories from the shipped starter packs, `dense+bm25`
backend, real bge-small-en-v1.5 model — this dev machine has it warm):
- **Baseline** (T2 HEAD `fc0fc33`): self_recall@10 **1.0**, hard_recall@10 **1.0**,
  mrr@10 **0.8939**, recall_p95_ms ≈33ms.
- **After RCL-1/2/3**: byte-identical to baseline on this fixture (RCL-2/3 only touch
  `main()`, which `eval_recall` never calls — it probes `recall()` directly by design so the
  ledger never pollutes eval numbers; RCL-1's dead-band correctly left this corpus's queries
  unrouted).
- **After RCL-4 (first attempt, MMR-before-knee bug + λ=0.7)**: hard_recall@10 dropped to
  **0.8636**, mrr@10 to **0.8409** — gates still passed (>0.80/>0.60) but a real, avoidable
  regression. **After the two-pass fix + λ=0.8 recalibration**: hard_recall@10 recovered to
  **1.0**, mrr@10 to **0.8763**.
- **After RCL-6/RCL-5**: unaffected (RCL-6 is a schema/render addition with no ranking
  change; RCL-5 only touches `describe()`, outside `eval_recall`'s measured path).
- **FINAL**: self_recall@10 1.0 (≥0.90 ✅), hard_recall@10 1.0 (≥0.80 ✅), mrr@10 0.8763
  (≥0.60 ✅), recall_p95_ms in the 24–230ms range across repeated runs (≥300ms gate — this
  dev machine shows real load/thermal variance; one isolated 316ms reading during RCL-2
  testing was confirmed a fluke via 3 immediate repeat runs at 28/109/157ms, unrelated to any
  code change since RCL-2 never touches `recall()`'s measured path at all).

THRESHOLD/WEIGHT CONSTANTS PINNED (all env-overridable, `HIPPO_*` prefix):
- RCL-1: `_INTENT_MIN_TOKENS=8`, `_INTENT_DENSE_DENSITY=0.10`, `_INTENT_LEXICAL_DENSITY=0.35`,
  `_INTENT_LEAN_WEIGHT=1.3`.
- RCL-3: `_RESCUE_MIN_TOKENS=4`, `_RESCUE_TURNS=3`.
- RCL-4: `_MMR_LAMBDA=0.8` (recalibrated from proposal's ~0.7 — see above), `_MMR_POOL_MULT=2`.
- RCL-6: `_SNIPPET_SCORE_BAND_FRACTION=0.6` (of `_body_rrf_weight()/(_RRF_K+1)`, NOT an
  absolute constant — `HIPPO_SNIPPET_SCORE_BAND` overrides with an absolute value instead),
  `_MAX_SNIPPET_CHARS=300`.
- RCL-5: cross-encoder model `Xenova/ms-marco-MiniLM-L-6-v2` (0.08GB, grounded via
  `TextCrossEncoder.list_supported_models()` — its HF source repo IS its model name, unlike
  the embedding model's qdrant/* re-export).

ENGINE STATE: suite **1245 passed / 12 deselected** (T2 baseline 1203/11; +42 passed, +1
deselected — the RCL-5 network-marked real-model test). `corpus_format` **2 unchanged** (no
new frontmatter key this tier — GOV-2/GOV-7 are next to touch it). Index **SCHEMA_VERSION
3→4** (RCL-6's one clean break: `body_chunks[].text` restored + manifest-wide `head_commit`
added); **re-bootstrap NO** (requirements.txt untouched — fastembed's existing pin already
covers the cross-encoder API). No plugin.json/CHANGELOG bump (T1/T2 precedent continues).

DECISIONS / GOTCHAS:
(1) This tier had markedly MORE premise corrections than T1+T2 combined (RCL-1: 2 guards;
RCL-4: 1 major sequencing bug + 1 recalibration; RCL-6: 2 corrections; RCL-5: 1 empirical
finding) — all 6 were caught by EXISTING hermetic tests or direct empirical measurement,
NONE by the eval gates alone, which is exactly the audit_note's own warning ("eval-gated"
means the absolute thresholds stay green, not that a regression is impossible below them).
The pattern every time: re-verify a note's claim against the LIVE TREE and against a REAL
RUN before trusting it, even when the note says "AUDITED" — the audit was a snapshot, not a
proof.
(2) The T2 rule-pointer lane threaded through every item exactly as the audit_note
predicted: exempt from floor-dedup (RCL-2) and MMR (RCL-4), included in the cooldown
(RCL-2), excluded from the cross-encoder rerank (RCL-5) and the RCL-6 snippet guard.
(3) RCL-2 and RCL-3 share ONE bounded episode-buffer read (`_session_episodes()`) exactly as
coordinated.
(4) `recall_view.describe()` does NOT render the RCL-6 evidence snippet (only
`format_results`, the hook's renderer, does) — a deliberate scope call, not an oversight:
the acceptance criteria named `format_results`'s own framing ("the 'backend' label
collapses body to invisible today") and a human explicitly running `/hippo:recall` has much
lower friction re-reading a file than an autonomous agent mid-session does. Worth a look in
a future tier if that asymmetry turns out to matter.
(5) End-to-end smoke test on a FRESH scratch git repo (real dense model, real commits, real
CLAUDE.md) confirmed all 6 items compose correctly: floor collapse + CLAUDE.md widening +
cooldown + terse-follow-up rescue + evidence snippet (real sha) + cross-encoder rerank +
the `describe()`/MCP shared-chokepoint identity (same cwd + k → byte-identical output) — AND
confirmed an old-schema (v3) manifest from a pre-T3 install is correctly treated as absent
by `_load_manifest` and cleanly rebuilt to v4 with `head_commit` populated (the real-world
upgrade path). No new gaps found (contrast T2's smoke test, which DID catch a real CLI gap).

NEXT: **Tier T4 "A corpus you can govern" (v1.4.0)** — items `[GOV-1, GOV-2, GOV-3, GOV-4,
GOV-5, GOV-6, GOV-7]`. Read each item's `implementation_notes` + T4's `audit_note` FIRST.
Tier-wide coordination already flagged in the audit: GOV-2 (`steer: pin`) and GOV-7
(confidence tier) EACH introduce a new frontmatter key — two SEPARATE `corpus_format` clean
breaks (land GOV-2 first) — and each ALSO rides the index manifest (two more
`SCHEMA_VERSION` bumps, 4→5 then 5→6; don't conflate either with T3's 3→4). GOV-1 needs one
new graph primitive (`LinkGraph.all_typed_edges`) and a per-clone resolved ledger. Build
**GOV-6 LAST** — it aggregates GOV-1's inbox count, GOV-4's floor delta, and the T2 rules
counts, so it depends on everything else in the tier landing first.

DEFERRED / BLOCKED: none — all 6 T3 items shipped.

Related: [[hippo-enh-t2-rules-bridge]], [[hippo-enhancement-roadmap]].
