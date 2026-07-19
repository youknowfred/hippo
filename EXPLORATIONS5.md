# hippo — Enhancement & Capability Explorations, Round 5

**Status: VETTED 2026-07-19** (5-namespace read-only grounding fan-out + inline
3-lens vetting at `827c607` = v1.27.0; [`ROADMAP.enhancements5.yaml`](ROADMAP.enhancements5.yaml)
authored — the normative form; owner ratification of scope PENDING — see §6).
Round-5 addendum to [`EXPLORATIONS.md`](EXPLORATIONS.md) (round 1 → T1–T7,
shipped), [`EXPLORATIONS2.md`](EXPLORATIONS2.md) (round 2 → T8–T13, shipped and
released through v1.22.0), [`EXPLORATIONS3.md`](EXPLORATIONS3.md) (round 3 →
T14–T17, shipped and released as v1.18.0/v1.19.0), and
[`EXPLORATIONS4.md`](EXPLORATIONS4.md) (round 4 → T18–T21, shipped and released
through v1.27.0; round 4 CLOSED 2026-07-19 with Q2/Q3 still pending). This is
**research and proposal only** — no implementation, no status flips on existing
roadmap files. Its executable companion is
[`ROADMAP.enhancements5.yaml`](ROADMAP.enhancements5.yaml) (tiers **T22–T26**,
namespaces **REL/MEA/WRT/OPS/BND** — all five verified collision-free against
every prior train's id set). Authored 2026-07-19.

Method — different from round 2's 158-agent fan-out, round 4's
shipping-week-as-grounding, and honest about it: **Phase 1 was a 5-namespace
read-only grounding fan-out** (five agents — retrieval-and-evidence,
write-side-and-lifecycle, fleet-machine-ops, corpus-reach-and-team,
cross-cutting-quality — ~787k tokens, 314 tool calls; every claim
file:symbol-verified or ledger-reproduced against the live tree at 827c607;
each namespace also filed an open-threads ledger of everything probed and found
NOT ripe). **Phase 2 — the vetting — ran INLINE as a single-session 3-lens
pass** (signal-existence/autonomy-creep · solo-owner value ·
fit/redundancy-vs-shipped) with spot re-verification of every load-bearing
claim against the tree, because the round-2/4-style per-item adversarial
skeptic panels (a 72-agent plan) were **killed for cost by owner directive**.
The catalog below is grounded to the same evidence bar as round 4 — every
signal cites a PR, a committed capstone, a code seam, or a report reproduced
read-only on this machine this week — but the adversarial pressure per item was
one careful pass, not a panel. Stated plainly so the confidence label is
honest.

---

## 0. TL;DR

Round 4 made the fleet visible, gave machine state a lifecycle, gave the
two-audience corpus a publish lane, and gave the write-only ledgers readers.
The week that shipped and then lived with all of it — four tiers released in
three days, then the first full lived-in day on v1.27.0 — exposed the three
trusts the engine still extends without checking:

**hippo trusts its instruments, its running code, and its own writes.**

- **Its flagship instrument was near-null and nothing said so.** The recorded
  ED-2 salience evidence (Arm A, 2026-07-17 — the file that quiets the doctor
  nudge) was measured through a hard set where only **1 of 32 rows** resolves
  against the lived-in corpus: a ~3%-sensitivity instrument whose zero deltas
  were arithmetically near-certain under BOTH arms, rendered as a normal
  report. The resolvability filter exists in the codebase — in `floor_sweep`,
  and not in `evaluate()`.
- **The code running its hooks is not the code in the tree.** Sessions
  launch-pin the installed plugin; that skew bit twice in one week (a
  pre-GRW-5 install re-flagged 4 fresh-baselined memories, costing a 4-verdict
  human pass; the same lag era wrote 3 hippo-authored memories into a sibling
  corpus that then sat quarantined 4–5 days, machine-invisible) — and the
  class recurs every release by construction, because every release mints lag
  until update + restart.
- **Its writes fail silently at four seams.** A consent fold that returns
  False "with nothing to say" (the shipped docstring's own words) while every
  call site discards the bool; MCP-recorded decisions keyed so they can
  **never** ride the capture seed the tool literally promises they will; a
  capture-triage prompt that ORDERS the model to fabricate when a session has
  no durable fact (live exhibit in the queue right now); and a capture CLI
  whose foreign-session-id, success, and internal-failure outcomes are all
  exit-0 with zero output.

And the runway is frozen: the two highest-traffic integration files sit AT
their ratchet caps (`session_start.py` 1705/1705, touched by 8 of the last 8
release merges; `doctor_checks_corpus.py` 899/900), the pressure has already
produced its first architecture distortion (VOL-1's check landed in the doctor
facade "because the sibling had no room"), and the 2026-07-19 release merged
against a red board because RELEASING.md documents WHAT must be green but no
gating mechanic — while the branch-protection ruleset codified since QUA-12
stays unapplied even though its stated enabling condition (a public repo) is
now true.

Five tiers follow:

1. **T22 REL — the runway & the ship gate.** Split the two at-cap modules
   before any feature needs them (pure code motion, the shipped PR #72
   pattern, repatriating the displaced VOL-1 check); write the red-board
   lesson into RELEASING.md as a mechanic; pin the doc's facts DOC-16-style
   (it has already rotted: "all six CI checks" vs the actual seven); close the
   kill-switch documentation direction. Surfaces Q2(r5): apply the ripened
   QUA-12 ruleset.
2. **T23 MEA — honest instruments.** Report hard-set resolvability in
   `evaluate()` and stamp it on A/B evidence; draft lived-in fixture rows from
   the positive outcome join (192 verified candidates — the missing fourth
   draft lane); give co-recall a null model (20/20 live pairs are chance-level
   and three consumers treat them as behavioral evidence); stamp evidence rows
   with their producing plugin version; normalize worktree touch paths at
   record time (35–50% of rows structurally dark); and — Q1(r5)-gated,
   sequenced last — un-sever the EVD-4 Arm B outcome-prior A/B harness now
   that its data condition ripened.
3. **T24 WRT — the write side tells the truth.** The triage abstain lane plus
   a zero-LLM ungrounded-identifier flag; one legible outcome line for
   human-invoked `capture --from-hook`; dead-letter decisions surfaced with an
   honest tool reply and a labeled time-window fallback at the drain.
4. **T25 OPS — running-state legibility.** The running-vs-source plugin skew
   named where it bites (a DOC-7-sibling doctor line + a presence-doc version
   stamp so launch-pin skew shows fleet-wide); scheduler dead-man freshness
   from the oracle that already exists with zero readers.
5. **T26 BND — the boundary & consent edge.** The introduces-side of heals-N
   (the measured publish treadmill: two real publishes moved the boundary 20 →
   19); trust drift joins the machine census (the alloy trio was invisible
   from where the owner works); write-time fold-failure disclosure.

Judged against the standing commissioning bar (solo-owner value,
consent/trust fit, cost, dogfoodability): every item is dogfoodable on this
repo or this machine today; nothing weakens per-item consent; nothing touches
default ranking (ED-2); every new artifact names its reader (ED4R-2); the two
genuinely posture-shaped calls are pulled out as §4 owner decisions. Two round-5
laws join the canon: **evidence carries its instrument's sensitivity**
(ED5R-2 — the MEA-1 finding as law) and **at-cap modules are split or re-pinned
deliberately, never mid-feature** (ED5R-3).

---

## 1. What the first fully-lived-in week established (the re-baseline)

Facts the proposals below stand on, each verifiable:

- **The flagship measurement was made through a near-null instrument, and the
  instrument class is systemic.** `salience_ab.json` (2026-07-17) records
  Δ=+0.0000 on both categories; reproduced this week: only 1/32 rows of
  `tests/fixtures/recall_hard_set.yaml` (the ONLY hard set on this machine —
  `.claude/memory/.audit-fixtures/` does not exist) have any expected stem
  present in the lived-in corpus. Off-arm single-hop recall 0.0455 is exactly
  1/22; multi-hop 0.0 is 0/10. `hard_set_metrics` (eval_metrics.py:602) counts
  unresolvable rows as silent misses; `floor_sweep` (eval_recall.py:1236)
  already filters to rows "whose expected stems actually exist in THIS corpus"
  — the logic exists in one instrument and not the other. Meanwhile 192
  distinct (verbatim episode query → outcome-confirmed injected memory) draft
  candidates sit unread across the ledgers, and fixture drafting lanes exist
  for exactly three categories (abstention/forgetting/update) — none positive.
- **The live-hook-lag class bit twice and was predicted a third time.** Bite 1:
  5 releases of lag (1.17.0 → 1.22.0) diagnosed by EVD-2's mechanical-zeros
  forensics. Bite 2: on 2026-07-19 a session launch-pinned at 1.26.0 ran
  pre-GRW-5 watermark code and re-flagged 4 fresh-baselined memories
  (reconsolidation_events.jsonl 15:18–15:25Z; GRW-5 is contained only in tag
  v1.27.0), before the install caught up at 15:25:48Z. Prediction: the t18
  capstone pre-recorded that presence "can't fire until the plugin updates."
  Both diagnoses were hand forensics: no ledger row records its producing
  version, `check_plugin_version` compares installed-vs-venv only, and nothing
  anywhere compares the RUNNING plugin to the SOURCE TREE it operates on.
- **Worktree evidence starvation is measured, total, and growing.** 1140 of
  2288 post-07-16 outcome rows (~50%; 1140/3304 = 35% all-time) are recorded
  main-root-relative with the `.claude/worktrees/` prefix; **0 of 1140 carry
  cited_by**; 883 would join the 112-path cited touchmap if stripped. JIT-1
  first-touch reminders and JIT-2 provenance are structurally dead inside
  worktrees — while FLT-3's shipped nudge (correctly) drives more mutation
  into worktrees. The shipped lane-health surface itself defers the fix as
  "its own follow-up item."
- **The proposal lanes minted their first pure-confound batches.** Co-recall:
  all 20 pairs at the cap (17 fresh after the adjacency drop — the incident's
  exact number) are dev-history staples individually present in 54–68 of 72
  sessions; lift vs independence 0.99–1.20, median 1.06 — chance level — yet
  three shipped consumers (the consolidate proposal turn, deparasite's
  "co-recalled" protection evidence, the demote weak-link signal) ingest the
  pairs as behavioral evidence. Capture triage: the prompt's forced-answer
  clause (capture_triage.py:227–229) plus a parser that rejects abstention
  produced "#237 merged" (no hippo PR above #85 exists) and the live seed's
  "merged T14 invariants" (T14 merged 2026-07-16 as PR #57).
- **The write side broke four promises quietly.** `record_authored_write`
  returned False "with nothing to say" and the alloy trio quarantined 4–5
  days (written by the lagged install in the pre-COR-10 era — the same skew
  class); MCP `add_decision` keys rows by the shared file token while seed
  matching requires strict harness-id equality, so decisions.jsonl's only two
  rows (durable INT-13/v1.13.0 facts) rode no seed — while the tool replies
  "it will ride this session's capture seed"; `capture --from-hook` is exit-0
  silent for foreign-sid, success, and failure alike (reproduced), which is
  exactly how a concurrent SubagentStop seed got misattributed this week.
- **The fleet lane went live and behaved.** The installed plugin reached
  1.27.0 at 15:25:48Z; the presence producer emitted its first real fleet line
  at 15:42:56Z (1 of 54 SessionStart rows, 168 chars); presence dir clean
  (docs written, then cleared by graceful SessionEnds); tripwire fires so far:
  zero. The lane's kill switch and bounds all verified live in code.
- **The runway is at zero.** `session_start.py` 1705/1705 (pin 1645 + slack
  60; the T18 wiring consumed the last 3 lines; touched by 8/8 recent release
  merges); `doctor_checks_corpus.py` 899/900 — and the cap has already
  produced its first architecture distortion: VOL-1's `check_volatile_paths`
  lives in the doctor.py facade (:313) "because doctor_checks_corpus is at
  899/900." Next in line: eval_recall.py 1989/1993, telemetry.py 1295/1302 —
  both carrying (module,function)-keyed registry pins that make their splits
  costlier than pure code motion.
- **The release protocol failed open, once, and its doc is rotting.** The
  v1.27.0 merge fired against a red board (merge chained after a display
  command; the hermetic macos-py3.12 job had hung 45m in setup-python); the
  remedy — tag held until the main run went fully green — lives only in a
  local-only capstone. RELEASING.md:32 says "All six CI checks" (the required
  set has been seven since SEC-8); nothing lints the file. The QUA-12
  branch-protection ruleset has been codified as a ci.yml comment since round
  1; `gh` confirms the repo is now PUBLIC and main is NOT protected — the
  enabling condition ripened while the gap stayed open through ~daily
  releases.
- **The boundary treadmill is measured.** Two real publishes landed since T20
  shipped (38098d9, 9404b0c — both riding larger commits, neither using the
  preflight's suggested commit form) and moved the boundary only 20 → 19:
  each publish heals danglings and imports its own outbound links as new
  ones. The preflight prints "heals 3" for the current top candidate while
  silent about the dangling its own local-only link will add. No shipped
  surface shows the introduces side.
- **Live machine state at authoring** (reproducible read-only): outcome ledger
  3304 rows / 132 cited_by; recall_events 373 rows / 94 with drop autopsies
  (dense_floor the #1 class at 254); reconsolidation ledger 824 events;
  episode buffer 72 sessions; usage_aggregates 154 sessions / 59 tracked
  memories; corpus 57 memories (+MEMORY.md +CONVENTIONS.md), committed subset
  17 files, boundary 19 dangling / 8 source files / 0 typed; trust registry
  3/3 live+fingerprinted (alloy WITHHOLDING 3 hippo-authored files); stale.json
  15 entries (8 volatile-suppressed, 7 code-drifted; nag-eligible worklist 0
  after the 07-19 re-verdicts); pending captures 1; contradiction inbox 3
  pairs; sleep last ran 14:30:05Z (the oracle nothing reads); suite 2,885
  tests.

## 2. The catalog (summary — the YAML is normative)

| Tier | Items | One-line pitch |
|---|---|---|
| T22 REL | REL-1 runway split · REL-2 merge-gate hardening · REL-3 RELEASING facts lint · REL-4 kill-switch reverse lint | The at-cap modules split before features need them; the ship gate becomes a mechanic, not a convention |
| T23 MEA | MEA-1 instrument sensitivity · MEA-2 lived-in hard-set drafting · MEA-3 co-recall null model · MEA-4 producer-version stamps · MEA-5 Arm B un-sever (Q1-gated) · MEA-6 worktree touch normalization | Every instrument states its sensitivity; the evidence base stops starving; the severed arm gets its harness |
| T24 WRT | WRT-1 triage groundedness · WRT-2 legible capture outcomes · WRT-3 dead-letter decisions | The capture lane stops fabricating, stops whispering, stops dropping the WHY |
| T25 OPS | OPS-1 running-vs-source skew legibility · OPS-2 scheduler dead-man freshness | The machine says what code is actually running and whether the 07:30 heart still beats |
| T26 BND | BND-1 net boundary delta · BND-2 trust drift in the census · BND-3 fold-failure disclosure | The publish ledger shows both sides; withheld corpora become visible from where the owner works |

Sequencing note: **no cross-tier build dependencies** — no round-5 item needs
a `session_start.py` line (OPS-1 deliberately routes to doctor; the presence
stamp rides presence.py), so T22's REL-1 is recommended-first as relief, not a
gate. In-tier: MEA-5 sequences after MEA-1/MEA-2 (an Arm B run through the
current 1/32-resolvable instrument would be evidence theater, which MEA-1's
own stamp would expose); REL-3 lints the text REL-2 rewrites; MEA-1 carries
the eval_recall.py split-or-shift decision and MEA-3 the telemetry.py one
(4 and 7 lines of headroom respectively — ED5R-3).

## 3. Considered and cut

- **Per-item adversarial skeptic panels for this vetting** — killed for cost
  by owner directive (a 72-agent plan); the vetting ran inline (see Method
  and §6). Recorded here so the confidence label on §6's verdicts is honest.
- **A SessionStart producer for the skew line (OPS-1)** — cut: session_start
  sits at 1705/1705, and DOC-7's own precedent ("rot is not per-session news")
  says on-demand doctor is the right surface. The presence-doc version stamp
  covers the fleet-wide view without touching the file.
- **PreToolUse/Bash coverage for FLT-3's blind spot** — cut: the cwd-trap
  class is real (7 documented strikes) but every strike predates the lane
  being live on this machine; a new hook surface has its own cost and needs
  its own dated decision. Revisit only on a post-install bite.
- **GRW-5 long-session persisted-watermark extension** — cut: 10.5h sessions
  are now normal (today's seed spans one) but no measured pain is attributable
  to the skip; the T5-era severance holds.
- **VOL-1 tier-2 co-drift arming** — cut: the armed worklist is 0 right now
  and tier-1 (armed-iff-any-non-volatile) has produced no measured miss.
- **Pinning the MSR-1 recall baseline today** — cut/sequenced: a pin taken now
  would freeze the pack-fixture near-null instrument as this corpus's
  baseline. Pin after MEA-1/MEA-2 land.
- **Worktree-spanning presence (git-common-dir)** — cut again: no
  cross-worktree collision has ever been recorded; the v1 per-tree scope
  holds.
- **A blanket reverse env-var lint (~60 HIPPO_* names)** — cut: the ranking
  knobs are deliberately undocumented (STABILITY.md's own posture); only the
  kill-switch class is an inv3 matter (REL-4's exact scope).
- **Auto-re-consent / auto-fold-retry on trust drift** — refused: an
  unattended re-baseline is the gate consenting to itself, trust.py's own
  named anti-pattern. BND-2/BND-3 are detection halves only.
- **Co-recall cap raise or auto-pruning of confound pairs** — cut: annotate
  and label first (MEA-3); cap/ordering motion only after the lift ordering
  is measured on live proposals, and any suppressed pair stays visible under
  a legible collapsed-count line (inv3).
- **Publishing the t18 capstone as a roadmap item** — cut to a note: the
  red-board lesson belongs in RELEASING.md itself (REL-2); whether the
  capstone also publishes is an ordinary per-item owner publish call.
- **Bulk sweep of the 10 temp-rooted LIVE projects-registry rows** — cut:
  the census already labels them and ships per-item `--drop`; a bulk sweep of
  LIVE rows is the inv4 line the census explicitly refuses to cross.
- **EVD-3 decline-aware interviewing** — still not ripened (re-probed:
  interview-state.json has never existed; the ask half renders 3 grounded
  questions today, the respond half has never once been exercised). The
  round-4 deferral holds untouched; noted for the owner as a
  living-with-the-tool observation, not an item.
- **Standing kills re-affirmed** (recorded in the YAML's not_pursuing with
  their origins): locking/coordination daemon (ED4R-3, permanent); auto-drain
  of the reconsolidation queue (LIF-1); LLM-judged lanes (WRT-1's groundedness
  flag is deliberately zero-LLM); the per-memory injected-but-never-touched
  table (MSR-6 AST pin); publish content transform; publish hard-gates on
  staleness/conflict (BND-1's introduces-M stays display-only); the
  touch-grain graduation arm (MEA-5 un-severs Arm B's MEASUREMENT only —
  graduation/ranking stays severed behind its own decision).

## 4. Owner decisions this round surfaces — **all PENDING**

1. **Q1(r5) — commission the EVD-4 Arm B evidence run (gates MEA-5).** The
   round-4 severance named two conditions: nonzero touch/outcome rows AND its
   own dated decision. The data half ripened this week (120–132 cited_by rows
   counted live; the ledger grew during the count). MEA-5 builds the harness
   delta EVD-4 itself specified (AB_FLAGS entry, `--ab` dispatch, generalized
   arm runner, outcome-signal precondition) and runs it — measuring the
   EXISTING RET-14 flag, flipping nothing. Recommendation: **yes, but only
   after MEA-1/MEA-2 land** — an Arm B run through the current
   1/32-resolvable instrument would be evidence theater, and MEA-1's own
   sensitivity stamp would say so on the report.
2. **Q2(r5) — apply the QUA-12 branch-protection ruleset (an owner
   GitHub-settings act).** The ruleset text has been codified verbatim in
   ci.yml:29–40 since round 1, waiting on "a public repo"; the repo is now
   PUBLIC and `gh` confirms main is NOT protected, through ~daily releases and
   one real red-board merge. REL-2 writes the operator-side mechanic either
   way; the ruleset is the mechanical backstop only the owner can switch on.
   Recommendation: **yes — apply as codified** (require the seven named
   checks + PR before merge; then `gh pr merge` refuses on red mechanically).
3. **Q2(r4) — trust-registry remediation — carried, still PENDING.** The
   report-only posture holds and the round-5 census evidence says holding
   costs nothing: 3/3 trust rows live+fingerprinted, zero dead — there is
   nothing for a remediation verb to act on. Recommendation unchanged:
   report-only.
4. **Q3(r4) — publish `--stage` — carried, still PENDING.** Print-only now
   has usage evidence: two real publishes landed without it, riding larger
   commits, with no recorded friction; the flip's proving condition
   (print-only demonstrably blocking cadence) has not materialized.
   Recommendation unchanged: print-only.

## 5. Ranked top moves

1. **REL-1** (the runway split) — every other tier's items land on files with
   zero or near-zero headroom; the distortion already happened once (VOL-1's
   displaced check, repatriated by this item). Pure code motion on the
   shipped PR #72 pattern; the honest alternative (a deliberate re-pin) is
   documented in-item for the owner.
2. **MEA-1 + MEA-2** (the instrument-honesty pair) — the round's headline
   finding made permanent: instruments report their own sensitivity, and the
   lived-in corpus gets resolvable fixtures from its own outcome evidence
   (192 candidates waiting). Everything measured afterward means something.
3. **OPS-1** (running-vs-source skew) — the twice-bitten, every-release class
   gets named where it bites, for two files' worth of read.
4. **MEA-6** (worktree touch normalization) — lights up the dark half of the
   touch-evidence stream (883 mappable rows and growing), which is also Arm
   B's data base.
5. **WRT-1 + WRT-3** (the write-side truth pair) — the drain reviewer stops
   reading fabrications first (live exhibit in the queue), and recorded
   decisions stop being dead letters the tool falsely promises will ride.

## 6. Vetting outcome (2026-07-19)

The catalog above was assembled from the Phase-1 grounding fan-out at
`827c607` (five namespace agents, every claim receipted), the cross-agent
duplicates merged, and then vetted **inline** — the per-item skeptic panels
were killed for cost by owner directive, so each item got a single careful
3-lens pass (signal-existence/autonomy-creep · solo-owner value ·
fit/redundancy-vs-shipped) with spot re-verification of every load-bearing
claim (file line counts, function seams, ledger rows, gh state, tag
containment — all re-confirmed live before authoring). **All 18 items
survived; zero KILL.** That zero is honest rather than complacent: the kill
work happened at grounding time — each namespace's open-threads ledger holds
the probed-and-not-ripened class (EVD-3 still zero declines; VOL-1 tier-2
unarmed; GRW-5 extension painless; FLT Bash-coverage biteless post-install;
Q2(r4) with nothing to act on), and those became §3 cuts, not items. The
vetting's work product was merges (4 cross-agent duplicate sets), demotions
(4 items to P2 on the solo-owner-value lens), sequencing constraints (MEA-5
after MEA-1/2; REL-3 after REL-2; the ED5R-3 split-debt assignments), and the
premise corrections below.
[`ROADMAP.enhancements5.yaml`](ROADMAP.enhancements5.yaml) is **normative**;
this document is the motivation record. Scope ratification — which tiers to
build, in what order, if any — is the owner's scheduling call; the two new
owner decisions (Q1/Q2 r5) and the two carried ones (Q2/Q3 r4) are **all
PENDING** and gate the halves named in the YAML (ED5R-1).

### Verdicts

| Item | Verdict | Vetting headline |
|---|---|---|
| REL-1 | KEEP | Enabler, not artifact — ED4R-2 vacuously satisfied; the ratchet ledger itself enforces honesty (dead-entry lint); the sanctioned re-pin alternative documented in-item; eval_recall/telemetry splits deliberately NOT bundled (reviewability) |
| REL-2 | KEEP (merged) | Absorbs the reach-namespace RELEASING rewrite leg; doc mechanic + Q2(r5) surface only — explicitly NOT auto-merge (that stays severed); capstone-publish leg cut to a note |
| REL-3 | KEEP | DOC-16 pattern one document over; RELEASING.md:32 rot re-verified live ("six" vs seven); depends on REL-2 so the lint pins the rewritten text, not the rot |
| REL-4 | KEEP | Scoped to the kill-switch class only (3/3 documented today — passes green at birth); the blanket reverse lint stays cut (deliberate undocumented-knobs posture) |
| MEA-1 | KEEP | The headline finding; reuses floor_sweep's existing filter REPORTED-not-applied; gate semantics untouched; carries the eval_recall.py split-or-shift call (4 lines headroom, ED5R-3); the doctor line qualifies evidence, never recommends a flip |
| MEA-2 | KEEP | Verbatim-queries-only honors the templated-fixture kill; per-item confirm gate already refuses absent stems (inv4); distinct from the round-1 demand-gap kill (evidence-derived rows, not generated memory content); volume-capped with dedup vs tracked queries |
| MEA-3 | KEEP (merged) | Two independent repros agreed (20 raw / 17 fresh, lift 0.99–1.20); annotation + labeled render + legible collapsed-count suppression; deparasite's two reads decided at build with permissive-protection default; no cap motion yet |
| MEA-4 | KEEP (demoted P2) | Complementary to OPS-1, not redundant — row-level provenance makes the NEXT mechanical-zeros diagnosis one command instead of hand forensics; additive `v` field, absence-emits-nothing; carries the telemetry.py headroom warning |
| MEA-5 | KEEP (gated) | The un-sever is exactly EVD-4's own named minimal delta; data condition verified met (120–132 cited_by live); Q1(r5) is the dated decision the severance demanded; sequenced after MEA-1/2 or the run is theater; graduation/ranking arm stays severed regardless |
| MEA-6 | KEEP (merged) | The fleet-namespace mechanism adopted (stripped path to observe_touch; raw `path` kept + additive `tree_path`; readers prefer tree_path); FLT-3's shared_tree exemption stays RAW-keyed with a pin; JIT-1 newly firing in worktrees is the one behavior delta — bounded and kill-switched |
| WRT-1 | KEEP | Reduces LLM authority (abstention right + mechanical doubt); groundedness flag is zero-LLM (the judged-lanes kill honored); rides the default-OFF HIPPO_CAPTURE_LLM lane; live fabrication exhibit re-verified in the queue |
| WRT-2 | KEEP | inv3 at a human seam; stderr-only, hook bytes identical (hooks discard stderr — verified); reproduced all three silent outcomes |
| WRT-3 | KEEP (P0) | A literal broken promise in shipped reply text ("it will ride this session's capture seed" — structurally impossible on the MCP path); honest reply + LABELED window fallback under a separate additive key; the strict session-proven lane untouched |
| OPS-1 | KEEP (merged) | Two detection halves, one class: DOC-7-sibling doctor line (this-repo-is-the-source shape, empty-norm elsewhere) + presence-doc `plugin_version` stamp rendered only on DIFFER; zero session_start lines; ED4R-3 clean (visibility, never coordination) |
| OPS-2 | KEEP | Closes the in-code-admitted blind spot with the file oracle that already exists (zero readers verified by grep); ED-3 boundary held — file oracles only, no launchctl probing; "quiet" status is a census/doctor word, remediation stays human |
| BND-1 | KEEP | Display-only everywhere; introduces-M is receipt vocabulary and NEVER a gate class (the publish-hard-gate kill re-affirmed in-item); the measured 20→19 treadmill is the exhibit |
| BND-2 | KEEP | Reuses the ONE shipped detector (trust.untrusted_changes — inv5); report-only naming the existing per-project re-consent route; Q2(r4) posture untouched; cold-path cost only |
| BND-3 | KEEP | The genuinely-anomalous case only (trusted+fingerprinted and fold False) — legacy fingerprint-less no-ops stay silent by design; needs the small disambiguation helper (the overloaded False); never hooks/index builds (pinned never-consent) |

### Premise corrections (vs the raw grounding outputs)

- **Two denominators for worktree starvation, both true**: 1140/3304 (35%,
  all-time) and 1140/2288 (~50%, post-07-16 rows). The YAML states both;
  the post-07-16 figure is the honest live-rate.
- **The corpus is 57 memories, 59 .md files** (+MEMORY.md +CONVENTIONS.md) —
  re-verified; one namespace agent's "57 memory files" and another's file
  count both hold, stated precisely now.
- **`evaluate()` is at eval_recall.py:580** (one agent cited :583) and
  **`heals_by` is computed at lint_links.py:160–170** (one agent cited
  :110–129, which is the docstring/empty-shape region). Cosmetic drift;
  corrected in the YAML's implementation_notes.
- **capture silence is a `--from-hook`-path fact**: a plain
  `python -m memory.capture` success DOES print `captured → <path>`; the
  three-indistinguishable-outcomes claim applies to `--from-hook` invocations
  (exactly how the operator ran it). WRT-2 is scoped and titled accordingly.
- **"Armed worklist 0" vs "7 code-armed" is not a conflict**: 15 stale.json
  entries → 8 volatile-suppressed + 7 code-drifted (arming classification),
  while the nag-eligible worklist is 0 because the GRW-5 baseline guard
  correctly quiets freshly-re-verdicted memories. Both stated.
- **The cited_by count moved while being counted** (120 → 132 across two
  same-day passes; the ledger grew during the session). Stated as a range
  with the growth as its own receipt: the lane records.

Round 5's own laws land in the YAML: **ED5R-2** (evidence carries its
instrument's sensitivity — an A/B or eval report without its resolvable-n
context may not be cited as decision evidence) and **ED5R-3** (an at-cap
module is split or deliberately re-pinned as its own reviewed PR BEFORE a
feature needs the lines — never a drive-by inside a feature PR). ED4R-2 (no
standing artifact without a named reader) and ED4R-3 (fleet visibility never
becomes coordination) carry forward verbatim as permanent law.
