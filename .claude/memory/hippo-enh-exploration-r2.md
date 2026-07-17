---
name: hippo-enh-exploration-r2
description: "Round-2 greenfield exploration+vetting cycle (2026-07-09) → EXPLORATIONS2.md + ROADMAP.enhancements2.yaml (29 items, workstreams MSR/GRF/SEN/TMB/CLB/IOP, tiers T8–T13). PR #20 MERGED to main (f7e5ae9), as were its cross-file deps #17/#18/#19. Execution detoured through round 3 first (T14–T17), then BEGAN 2026-07-16: T8 MSR shipped (PR #68) and T9 GRF shipped (PR #70, incl. MSR-5's HIPPO_SALIENCE rig); T10 SEN in flight — T11–T13 remain planned."
metadata:
  node_type: memory
  type: project
  originSessionId: a5a922b1-a6ab-4fcf-96cc-bec2e048816f
  last_verified: "2026-07-16T14:39:50.641856+00:00"
  cited_paths: ["ROADMAP.enhancements.yaml", "ROADMAP.enhancements2.yaml", "plugin/memory/telemetry.py", "ROADMAP.enhancements3.yaml", "plugin/memory/eval_recall.py", "plugin/memory/dream_eval.py", "plugin/memory/salience_eval.py"]
  source_commit: "f99bfa89571ac19cadf0967693103b9b9bd464f1"
  source_commit_time: 1784246873
---

Round-2 exploration+vetting cycle — session 2026-07-09, mirroring the round-1
process that produced [[hippo-enhancement-roadmap]] (EXPLORATIONS.md →
ROADMAP.enhancements.yaml). **RESEARCH/PROPOSAL ONLY** — two additive docs, no
code, no status flips on existing roadmap files. **[PR #20](https://github.com/youknowfred/hippo/pull/20)
was opened as a DRAFT then — at the owner's explicit direction later the same session
(2026-07-10) — squash-MERGED to `main` as `f7e5ae9`** (all 6 required checks green:
dense, 4× hermetic matrix, shellcheck; head branch `explorations-r2` deleted
remote+local, stale tracking ref pruned). So EXPLORATIONS2.md + ROADMAP.enhancements2.yaml
now live on `main` (like round 1's docs). **FOLLOW-UP same session (owner-directed): the
three open dev-line PRs were then squash-merged to main IN ORDER #17→#18→#19** — SIG-6
(`4dc3256`), the GRA-1 dense-knee fix (`8128411`), and the trust spine SEC-5/6/7 + RCH-5
(`ca36e57`) — all 6 required checks green each, all head branches deleted. So main tip is
now `ca36e57` and **every cross-file dep the round-2 roadmap references (SIG-6, GRA-1,
SEC-5/6/7, RCH-5) is now literally on main** (verified by symbol grep: draft_abstention_fixtures,
graph_endorsed, corpus_consent_sample, untrusted_changes, pack_install_item). The stacked
#18→#19 squash was clean (git's 3-way merge saw the shared GRA-1 commit identically on both
sides — no conflict, #19 landed only the trust-spine+RCH-5 delta). NB the SEC-8..14
launch-security tier (SEC-8/9/10 committed + WIP) is now in progress on the OWNER's separate
`v080-sec-tail` branch, stacked on the trust spine — do not disturb it.

**Deliverables (both at repo root on branch `explorations-r2`):**
- **EXPLORATIONS2.md** — round-2 catalog. 47 raw candidates from an 8-lens ideation
  over a 13-agent verified grounding fan-out + a 6-survey provenance-tagged 2026
  landscape survey; merged to 37; each faced a 3-skeptic adversarial panel (signal-
  existence/autonomy · user-value · fit/redundancy) + a judge — **158 agents total**.
  Verdicts: **36 RESHAPE** (every survivor pared to its detection/HITL half, ED-1) +
  **1 KILL** (`trust-ratchet-state-anchored-consent` — superseded by the shipped
  trust spine, inv5).
- **ROADMAP.enhancements2.yaml** — executable form: **29 items, 6 workstreams**
  (MSR measurement · GRF graph/retrieval · SEN write-side/security · TMB temporal/
  lifecycle · CLB team collaboration · IOP interop/reach), **6 tiers T8–T13**, full
  round-1 machinery (acceptance_criteria, resolving deps, invariant_notes,
  implementation_notes citing verified file:symbol seams, tier gates, session_protocol
  + capstone_template, decisions ED2R-1/2/3 + carried ED-1..5, not_pursuing,
  adopt_from_v1, kpis). **VALIDATED**: parses; every item in exactly one tier; dep
  graph acyclic; **no forward-tier deps**; all 29 cross-file dep ids resolve to real
  prior-roadmap items.

**THE LIVE RE-BASELINE (the round-1 premise-correction law paid off immediately —
the branch `v080-trust-spine` advanced THREE times mid-exploration):** what the
grounding first recorded as "unbuilt/in-flight" was, at HEAD `3a4a94a`, verified by
reading commits + re-running the eval:
- **GRA-1 dense-knee suppression FIXED** (`4d16022`): production dense+bm25 multi-hop
  **0.0 → 1.0** (re-measured live — the fix added a 3rd `graph_endorsed` set to
  `_expand_neighbors` + knee cliff-latching). **Residual still open**: MIXED mode
  (dense index resident, bm25 ranking at query time — reachable on embed_query
  timeout/cold-cache) STILL scores **0.0** because MMR's diversity penalty drops the
  wikilink neighbor (definitionally similar to its seed) — the one exemption posture
  4d16022 didn't unify. **GRF-2 targets exactly this.**
- **v0.8.0 trust spine SEC-5/6/7 SHIPPED** (`2f960a7`): `corpus_consent_sample`
  (SEC-5), `corpus_fingerprint`/`file_sha256`/`consented_hashes`/`untrusted_changes`/
  `record_authored_write` + `check_trust_drift` (SEC-6/7) all live. **RCH-5 pack
  install/update SHIPPED** (`d6ec77d`: `pack_install_item`/`pack_install_plan`).
  → six round-2 items the skeptics thought were "gated behind the spine" now BUILD
  ON it; SEN-4 exists to acceptance-test it; IOP-4's write leg is unblocked.
- **SIG-6 is on its own unmerged PR, ABSENT from this baseline** (NOT reverted —
  the "reverted" framing in an early draft of the docs was corrected before the
  amend/force-push, commit `bdd3b43`). SIG-6 (`draft_abstention_fixtures`/
  `confirm_hard_set_row`) is committed on `enh-t7-sig6` = **PR #17** (open, base
  main, NOT draft) but has **0 symbol hits on both main and v080-trust-spine** —
  verified. Mid-session it showed as uncommitted working-tree work, then got
  committed to its own branch. From the v080-trust-spine baseline this roadmap
  builds on, it does not exist, so it is a real cross-file dep for 6 items (MSR-5,
  TMB-3, TMB-4, + confirm-flow riders) that must LAND (merge PR #17). ED2R-2 records this.
- Engine unchanged: corpus_format **4**, index SCHEMA **6**, seed **2**, MCP 5/3,
  **14 skills** (RCH-5 extended the existing `pack` skill, no new one), suite ~1474.

**Round-2 thesis (EXPLORATIONS2 §0):** hippo can now *measure itself, defend itself,
and be handed to a team* — if it first lights up the ledgers it writes-but-never-reads
(dark `recall_events` scores/ranks `telemetry.py:227-231`; score-less abstention arm
`:300-303`; telemetry-invisible MCP recall via `recall_view.describe` bypassing
`main()`). The T8 eval+telemetry observability spine is the round's keystone (ED2R-1):
MSR-5 salience A/B, GRF-2/4 GRA-7 baseline, TMB-3 forgetting + TMB-4 update categories
all need persisted/baseline-diffable/condition-matrixed eval that doesn't exist yet.

**Governing law preserved (no drift from round 1):** ED-1 detection-first (every
autonomous half severed + deferred behind a DATED OWNER DECISION, never a
metric-proxied gate — the LIF-7 `soak_status` lesson); **ED-2 salience stays
owner-decided-OFF** (SIG-5 2026-07-09 ratified) — no item ships an always-on read-side
prior; MSR-5 only builds the lived-in-corpus A/B *evidence* for the revisit. Round-1
kills re-enforced: fabrication-vector (TMB-4 verbatim-span-only; TMB-3 no auto-restore
= demand-gap-auto-draft), inert-recall-noise-finder (MSR-6 new AST pin against
per-memory cross-session touch aggregation), cross-clone-auto-harvest (CLB-4 re-checked).

**Process notes for the next agent / a T8 implementer:**
1. **The tree moves — re-verify every symbol before coding.** implementation_notes are
   snapshot guidance @ `3a4a94a`; the owner is actively committing on `v080-trust-spine`.
   Especially: re-read SIG-6's landed schema fresh (its working-tree form moved once;
   it lives on PR #17, not the baseline) and the trust-spine shapes (2f960a7 moved
   recall/trust/doctor).
2. **PR base choice:** branched from `main` for a clean 2-file diff + to dodge the
   squash-and-delete orphaning that would hit a feature-branch base (round-1 pattern).
   The "shipped at `<sha>`" refs become literally true on main once v080-trust-spine
   merges. PR body offers retargeting to v080-trust-spine if the owner prefers.
3. **Eval re-measurement recipe** (used this session): seed a scratch corpus from
   `plugin/assets/*.md` (minus MEMORY.skeleton/README) into `.claude/memory/`, build
   the index, run `memory.eval_recall` both backends; for the mixed-mode probe, build
   a dense index then set `HIPPO_DISABLE_DENSE=1` at query time (matrix stays resident).
   The two multi-hop fixture rows are `feedback_fallback_is_still_bug` and the
   `feedback_no_backward_compat`/`feedback_no_tactical_shortcuts` pair.
4. Coverage-critic misses handled: RET-11 detection folded into MSR-4; SEC-12/13/8/14
   noted in `adopt_from_v1` as ROADMAP.v1-owned launch-readiness (not enhancement bets).

**WHAT ACTUALLY HAPPENED (re-verified 2026-07-16): the round-2 tiers were never built.**
The docs and every cross-file dep landed on main as described, but execution never started
on T8 — the owner commissioned round 3 instead (EXPLORATIONS3.md + ROADMAP.enhancements3.yaml,
tiers T14–T17 INV/SLP/JIT/EXT, grounded in the 2026-07-16 QA sweep), and that is the chain
that is executing: [[hippo-enh-t14-invariants]] and [[hippo-enh-t15-sleep]] shipped in
v1.18.0. So **T8–T13 remain PROPOSED, not dead** — `eval_recall.py` still says "HIPPO_SALIENCE
is MSR-5 — planned, not shipped", and `dream_eval.py`'s AB_FLAGS still reserves the seam
MSR-5 would extend. The original handoff, preserved: when execution starts, T8 first (the
measurement spine, all deps shipped) — MSR-1 is the keystone; write `hippo-enh-t8-*` as the
first round-2 capstone, continuing the chain. The only still-open
cross-file deps are the ROADMAP.v1 launch items SEC-8 (CLB-1's CI vehicle) and SEC-14
(CLB-2's gate) — being built NOW on the owner's `v080-sec-tail` branch (SEC-8/9/10 committed
+ WIP). No round-2 tier is blocked on the trust spine anymore.

**EXECUTION BEGAN — and the "never built" paragraph above is now itself history
(re-verified 2026-07-17):** T8 SHIPPED 2026-07-16 ([[hippo-enh-t8-measurement]], PR #68
squash `8782ab9`) and T9 SHIPPED the same day ([[hippo-enh-t9-graph]], PR #70 squash
`f99bfa8`) — so `eval_recall.py`'s "HIPPO_SALIENCE is MSR-5 — planned, not shipped" line
is GONE (MSR-5's rig lives at `memory/salience_eval.py`; `dream_eval.AB_FLAGS` is now
`("HIPPO_DREAM", "HIPPO_SALIENCE")` — the seam was extended exactly as reserved). T10 SEN
is in flight (spawned 2026-07-16). The SEC-8/SEC-14 "still-open deps" note above also
aged out: the whole SEC-8..14 tail merged 2026-07-10 (see [[hippo-v080-sec-tail]]) — the
only remaining gap for CLB-1/SEN-2's CI leg is CLB-1's own `--ci` vehicle (unbuilt, T12).

Related: [[hippo-enhancement-roadmap]], [[hippo-enh-t7-learned-ranking]],
[[hippo-v1-roadmap-proposal]].
