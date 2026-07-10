# hippo — Enhancement & Capability Explorations, Round 2

**Status: DRAFT idea catalog for owner review.** Round-2 addendum to
[`EXPLORATIONS.md`](EXPLORATIONS.md) (the round-1 catalog, authored 2026-07-08,
which became [`ROADMAP.enhancements.yaml`](ROADMAP.enhancements.yaml) — 41 items,
tiers T1–T7, now shipped through v1.7.0 + RET-8 + the GRA-1 fix + the v0.8.0
trust spine). This is **research and proposal only** — no implementation, no
status flips on existing roadmap files. Its executable companion is
[`ROADMAP.enhancements2.yaml`](ROADMAP.enhancements2.yaml) (DRAFT, tiers T8–T13).
Authored 2026-07-09.

Method: the same discipline as round 1, run wider. A 13-agent read-only
**grounding** fan-out mapped every live surface of `plugin/memory/` (signals /
retrieval / graph / trust / growth / eval / rules / open-items / pins, plus a
completeness critic and a 4-agent gap-fill for the resolve, runtime, archive, and
index planes) — every claim cited `file:symbol` and verified against the working
tree. A 6-survey **landscape** fan-out (benchmarks, memory systems, consolidation/
failure-modes, KG/team/federation, rule-plane standards, security/learned-retrieval)
tagged every external idea with provenance. Then an **8-lens ideation** produced 47
raw candidates; a structural merge collapsed cross-lens duplicates to 37; and each
survivor faced a **3-skeptic adversarial panel** (signal-existence & autonomy /
user-value / fit & redundancy) plus a final judge — 158 agents, verdicts
KEEP/RESHAPE/KILL. A coverage critic then found the opportunity areas with no
survivor. As in round 1, ~two-thirds of the raw ideas were killed or reshaped —
that is where the signal is: **36 of 37 survived only as RESHAPE** (every one
pared to its detection/HITL half), and **1 was killed outright** (already shipped).

---

## 0. TL;DR

Round 1's thesis was "activate the dark signals hippo already collects; become the
ranking/hygiene layer over the Claude-rules plane." That work shipped. Round 2's
thesis, after grounding the whole engine again, is narrower and sharper:

**hippo can now measure itself, defend itself, and be handed to a team — but only
if it first lights up the ledgers it already writes and never reads.** Three
structural facts drive the whole catalog:

1. **The measurement substrate is one step from real.** RET-8 gave hippo
   category-tagged eval + tracked precision/abstention gates. But there is still
   **no eval history, no `--json`, no baseline-diff** (every gate is an absolute
   frozen threshold — a regression below it is invisible); `recall_events`'
   `scores[]`/`ranks[]`/`k` arrays are **written and read by nothing**
   (`telemetry.py:227-231`); **MCP recall/why are telemetry-invisible**
   (`recall_view.describe` bypasses `main()`); and abstention events carry **no
   near-miss scores** (`telemetry.py:300-303`). The single highest-leverage tier
   is an eval + telemetry observability spine that makes hippo's own behavior a
   first-class, deterministic, reproducible artifact — which the landscape says is
   *itself a differentiator* (vendor numbers routinely fail third-party
   reproduction: EverMemOS 92.32%→38.38%, *Hidden Layer 2026-05*).

2. **The trust spine shipped mid-exploration — so the security/team frontier is
   now buildable, not blocked.** During this cycle the owner committed the v0.8.0
   trust spine (**SEC-5 consent-shows-descriptions, SEC-6 content-fingerprint +
   re-consent-on-drift, SEC-7 inject-time provenance banner**, commit `2f960a7`)
   and **RCH-5 pack install/update** (`d6ec77d`). Round 1's whole "gated on the
   trust spine" tail is now *ground to build on*. The 2026 threat landscape says
   exactly what to build next: MINJA memory-injection needs no store access
   (*arXiv:2503.03704*), AgentPoison hits ≥80% at <0.1% poison rate (*NeurIPS
   2024*), invisible-Unicode payloads survive forks (*Pillar 2025*), and static
   install-time scanning is the *wrong* trust anchor vs runtime auditing
   (*SkillCloak, arXiv:2607.02357*). hippo's answer is deterministic, local,
   human-in-the-loop: write-side threat lint, an adversarial eval category that
   *acceptance-tests the shipped spine*, and incident-response verbs.

3. **Team memory is the unclaimed high ground, and git is hippo's unfair
   advantage.** The landscape is blunt: **no shipped memory system implements the
   human-promotion (PR-review-for-memory) step** (*fountaincity 2026-05*), and
   Anthropic's own tracker names shared team memory the **top Claude Code team
   bottleneck** (*claude-code #38536*). Meanwhile Letta pivoted its memory to
   **git-backed version-controlled files** (*letta.com ~2026-02*) — independent
   convergence on hippo's architecture. Review-gated writes are hippo's identity;
   round 2 turns that identity into ergonomics (a review packet, attribution,
   incoming-merge dedup, cited-code re-verification-before-reuse — Copilot's
   just-in-time verification lifted agent-PR merge rate +7pp, *GitHub 2026-01*).

The governing design law is unchanged and was enforced by every skeptic panel:
**ship the legible detection / human-in-the-loop half; gate any autonomous
ranking or corpus write on proven signal density** (ED-1). The salience keystone
stays **DECIDED OFF** (SIG-5, owner-ratified 2026-07-09) — round-2 items may build
the *evidence substrate* for its revisit (a lived-in-corpus A/B rig), but **not one
of them ships an always-on read-side prior.**

---

## 1. The re-baseline — the tree moved under the exploration

Round 1's premise-correction law ("re-verify against the live tree, not the note")
paid off immediately: **the branch advanced three times during this cycle.** What
the grounding brief first recorded as "unbuilt" or "in-flight" is now, at HEAD
`3a4a94a` (branch `v080-trust-spine`), the following — verified by reading the
commits and re-running the eval:

- **GRA-1 dense-side knee suppression: FIXED** (`4d16022`). RET-8's headline
  finding (multi-hop recall@10 = 1.0 bm25-only vs **0.0** dense+bm25) is resolved.
  Re-measured live on HEAD: **production dense+bm25 multi-hop is now 1.0** (both
  fixture rows fully recovered via `via=graph`). The fix added a third
  `graph_endorsed` set to `_expand_neighbors` and made the admission walk **exempt
  endorsed entries from the knee and latch the cliff instead of breaking** — which
  unifies two of the three inconsistent graph-exemption postures the grounding
  flagged. **The remaining leg is narrower and still open:** in *mixed mode* (a
  dense index resident but bm25 ranking at query time — reachable when
  `embed_query` times out / cold-cache fast-fails while the matrix stays loaded),
  MMR's diversity penalty still drops the wikilink neighbor (a neighbor is
  *definitionally* similar to its seed). Re-measured: mixed-mode multi-hop = **0.0**
  on current HEAD. This is the sharp, testable residue GRF-2 targets.

- **The v0.8.0 trust spine: SHIPPED** (`2f960a7`). Live symbols confirmed:
  `trust.corpus_consent_sample` (SEC-5 — consent now surfaces the *descriptions*
  that inject, not just names), `trust.corpus_fingerprint`/`file_sha256`/
  `consented_hashes`/`untrusted_changes`/`record_authored_write` (SEC-6 — per-file
  content fingerprint + drift quarantine + re-consent), `doctor.check_trust_drift`
  + a `trust_drift` producer (SEC-7 surfacing). Round 1's RCH-5 install/update
  (`pack_install_item`/`pack_install_plan`, three-way merge, lockfile) shipped on
  top (`d6ec77d`). **Consequence:** six round-2 candidates whose skeptics assumed
  they were "gated behind the trust spine" instead have their dependency
  *satisfied* — they build on the spine and, in one case (SEN-4), exist precisely
  to acceptance-test it.

- **SIG-6 (self-populating abstention fixtures): ON ITS OWN UNMERGED PR, ABSENT
  FROM THIS BASELINE.** SIG-6 (`draft_abstention_fixtures`/`confirm_hard_set_row`)
  is committed on branch `enh-t7-sig6` (**PR #17, open, base `main`**) but is
  **absent from both `main` and `v080-trust-spine`** (verified: zero symbol hits on
  either) — mid-cycle it appeared as uncommitted working-tree work and has since
  been committed to its own branch, not merged. **Consequence:** relative to the
  `v080-trust-spine` baseline this roadmap builds on, SIG-6's draft→confirm HITL
  surface does not exist yet, so the six round-2 items that consume it carry it as a
  genuine **cross-file dependency that must land (merge PR #17)** — like round 1's
  RET-8/LIF-7 cross-file deps, not a landed given.

- **One candidate was killed by the re-baseline.** `trust-ratchet-state-anchored-
  consent` proposed state-anchored consent with git-native foreign-delta review —
  which is *materially SEC-5/6/7*, now shipped at `2f960a7`. KILLED as
  superseded-by-shipped (inv5: one canonical implementation per concept).

Engine state at HEAD `3a4a94a`: corpus_format **4**, index SCHEMA_VERSION **6**,
seed schema **2**, MCP **5 tools / 3 resources**, **14 skills**, suite ~**1474**
(per `4d16022`'s note) — all pins from round 1 intact. Every round-2 item's
`implementation_notes` re-states the standing law: **verify each named symbol
against the tree at build time, not against this snapshot.**

---

## 2. Five structural openings the round-2 corpus points at

1. **The instrument that can't yet see its own regressions.** RET-8's gates are
   absolute; nothing persists a run, diffs a baseline, or proves determinism
   (pass^k). The dark `scores[]`/`ranks[]` arrays and the score-less abstention
   arm are the *already-paid-for* telemetry that a measurement spine consumes.
   *Deterministic scoring beats LLM judges even on hard axes* (freshness resolution
   +10.8pp code-side vs judge, *arXiv:2606.01435*; LongMemEval scores retrieval
   deterministically via `has_answer` labels) — hippo's zero-LLM eval is an asset
   to sharpen, not a limitation to apologize for.

2. **The graph gap is 90% closed — finish it and make it observable.** `4d16022`
   fixed the production path; what remains is (a) the mixed-mode MMR leg, (b) a
   multi-hop instrument still at **n=2** (too small to gate anything — brief §5),
   and (c) **no graph observability at all** (`links.py main` exposes only
   `--traverse/--hops`; GRA-8 is unbuilt). PPR (GRA-7) is the one no-LLM-compatible
   graph mechanism worth the depth (*HippoRAG2, arXiv:2502.14802*) and its gate
   ("beats GRA-1 on RET-8 multi-hop") is **now measurable against a working dense
   baseline** — but only once the instrument is grown. *Route, don't default*:
   graph wins multi-hop but LOSES single-hop/time-sensitive (*GraphRAG-Bench,
   ICLR'26*), so the instrument must measure the whole condition matrix, not just
   the win.

3. **The trust spine wants an acceptance test and an incident-response kit.** A
   shipped defense with no adversarial fixtures is an unproven defense. Poisoned-
   memory fixtures (MINJA payloads, invisible-Unicode bodies, exfil-shape
   descriptions, high-BM25 trigger phrases) that *deterministically report* whether
   each payload crossed into `format_results`, was quarantined by SEC-6, was shown
   byte-equal by SEC-5's consent surface, or was flagged by a threat lint — that is
   the missing regression harness. And `untrust`/`blast-radius` are the verbs a
   user reaches for *after* discovering a bad memory (there is **no untrust verb**
   today; `is_trusted` re-checks live, so revocation is registry-removal + a
   read-only impact report over `episode_buffer`/`links.json`/`gov_citations`).

4. **The write plane is where correctness is won or lost.** *61% of memory-system
   errors occur AFTER correct retrieval* and *every tested memory system reduced
   objective-fact accuracy vs no memory* (*MemSyco-Bench, arXiv:2607.01071*);
   memory amplifies sycophancy up to 25x (*MIST*). hippo's write plane is already
   human-gated — the leverage is **mechanizing the checks the reviewer does by
   eye**: a "write-ticket" verifier (secret lint, currently a *procedural* gate;
   fenced-hunk fidelity vs a fresh `git show`; archive-shadow collision), a
   deterministic threat lint, and an ungrounded-prescription lint (flag
   "the user always wants X" when it's grounded in neither the captured hunk nor a
   `--rationale`). All warn-only, all `secrets.py`-shaped, all off the hot path.

5. **The resolve/lifecycle plane has no clock.** The contradiction inbox derives
   fresh every call with **zero timestamps anywhere** — no pair age, no drain rate,
   no verdict provenance on three of four verdict paths. `invalid_after` is visible
   only through the git-drift stale set, so a *supersede/merge*-retired memory (no
   code drift) is invisible to both the staleness producer and `archive_candidates`.
   There is **no unarchive** and archived stems can shadow live ones. Git supplies
   the missing clock for free (commits-since-birth satisfies the no-timestamps
   render pin); *production failures are predominantly forgetting failures*
   (deletion correctness 0–93% across 13 architectures, *ForgetEval*), so
   forgetting-correctness is worth a report-only eval category and a decoupled
   `restore` primitive with a collision guard.

---

## 3. The vetted catalog (by workstream)

Verdicts: **KEEP** (survived cleanly) · **RESHAPE** (survives in a smaller/safer
form — the reshape is the item) · **KILL** (§4). Every survivor was reshaped to its
detection/HITL half by the adversarial panel; the RESHAPE tag is therefore the norm
here, and the *shape* is the signal. `xN` = cross-lens convergence (independent
lenses that invented the same mechanism — strong signal). Provenance tags cite the
landscape survey. Executable form (acceptance criteria, deps, invariant notes,
implementation_notes) is in `ROADMAP.enhancements2.yaml`.

### MSR — Measurement & the eval flywheel  *(tier T8, the foundation)*

| Idea | Verdict | What | Provenance |
|---|---|---|---|
| **MSR-1 · eval run ledger** | RESHAPE | `--json`/`--out` + a gitignored append-only run ledger + a **report-only** fingerprint-keyed `--baseline` diff (per-category deltas) + a `--repeat k` pass^k determinism probe on the hermetic lane. The CI *fail* ratchet is deferred behind a dated owner blessing of the first baseline. *(merges eval-ledger-ratchet ×2 + graph-bench persisted-reporting)* | landscape: seeds/stddev + grep-baseline hygiene (*Hidden Layer 2026-05*); reproducible determinism as differentiator |
| **MSR-2 · null-hypothesis eval arms** | RESHAPE | `evaluate()` grows report-only arms over an **index-mode × query-mode** matrix: a pure-stdlib grep null (labeled a *ranking-stack-lift* measure, not the Letta adopt-memory-at-all threshold), a **true** bm25-only arm (second index in a scratch dir — never an in-process flag flip against a resident matrix), and an explicitly-labeled **mixed/degraded** arm. | landscape: filesystem-grep 74.0% > Mem0 68.5% (*Letta 2025-08*); condition-matrix rigor |
| **MSR-3 · channel-tagged MCP telemetry** | RESHAPE | Additive `channel` field (`hook`/`mcp`/`cli`, absent=hook) on `log_recall_event`; route `recall_view.describe` through it so MCP recall/why stop being telemetry-invisible — replicating `main()`'s SEC-1 zero-trace + SEC-3 gates; scoped to `recall_events` consumers only (episode-buffer consumers explicitly out of scope). *(x3 convergence)* | landscape: on-policy vs static rankings disagree ≤3 positions (*AMemGym ICLR'26*); no framework measures its own usage feedback (*Mem0 2026*) |
| **MSR-4 · drop-reason autopsy ledger** | RESHAPE | Every recall-pipeline cut (floor, knee/cliff, MMR displacement, cooldown, pool overflow, dangling/old skip) records an additive reason-code + score-at-death; **abstention events gain near-miss scores** (closes `telemetry.py:300-303` and gives RET-11's BM25-floor decision + the SIG-5 revisit their first evidence); per-category miss autopsy over the RET-8 set; one doctor aggregation line. Pure detection. *(x3)* | landscape: post-retrieval is the bottleneck (*MemSyco-Bench 2026*) |
| **MSR-5 · salience-revisit A/B rig** | RESHAPE | `eval --ab HIPPO_SALIENCE` runs `evaluate()` twice over the live corpus, emitting a paired per-category delta to the gitignored dir — **first fixing the usage-prior-blind eval path** (thread `memory_dir` through every internal `recall()` call) so the ON arm actually sees `usage_aggregates`/`stale.json`. The OFF arm is asserted byte-identical to the pinned production result. **Measures only — never flips the default** (ED-2). *(absorbs team-soak usage lane)* | landscape: sleep-time/priors help only when signals exist (*arXiv:2504.13171*); ED-2 revisit trigger |
| **MSR-6 · injection cost ledger** | RESHAPE | Additive `injected_chars` on `log_recall_event` + a rotating per-producer byte ledger against the 9000-char cap → `session_token_cost` measured actuals + one GOV-6 scorecard cost-honesty line. A new AST pin forbids per-memory cross-session touch aggregation (keeps the round-1 `inert-recall-noise-finder` kill enforced). *(x2)* | landscape: memory saves 15–28% cost on complex tasks, pure overhead on simple (*Sandelin 2026*); ChatGPT Dreaming reduced audit trails — the anti-pattern (*press 2026-06*) |

### GRF — Graph & retrieval observability  *(tier T9)*

| Idea | Verdict | What | Provenance |
|---|---|---|---|
| **GRF-1 · GRA-8 graph-audit CLI** | RESHAPE | Extend `links.py`'s CLI into a `graph-audit` subcommand (edge-class counts, degree/orphan/component stats, **edge rot** into archived/superseded stems) computed live from the existing `typed_out`/`typed_in` maps — no links.json schema bump; one threshold-gated doctor line; a cheap `edge_origin: co-recall` frontmatter stamp on newly-proposed co-recall wikilinks. **Absorbs & closes GRA-8.** | landscape: LazyGraphRAG structural stats LLM-free (*MSR 2024*) |
| **GRF-2 · multi-hop instrument + mixed-mode leg** | RESHAPE | Grow the multi-hop hard-set **n=2 → n≥10** with a documented condition matrix; **close the mixed-mode MMR leg** (unify graph-endorsed exemption across knee AND MMR, extending `4d16022`); register the result as GRA-7's measurable baseline. No PPR ships here. | brief §5 re-measurement; *GraphRAG-Bench* route-don't-default (ICLR'26) |
| **GRF-3 · dense-floor calibration (=RET-9)** | RESHAPE | Automate RET-1's documented cosine-separation recipe into an offline floor-sweep (hard-set on-topic + off-topic probes + `.audit-fixtures`), emitting a **recommended per-model/per-corpus dense floor** in *raw cosine space* + one advisory doctor line. Human edits `_DENSE_FLOOR_BY_MODEL`; nothing auto-writes. **Sole owner of RET-9.** Ettin/Li-LSR bake-off cut (ED-3-blocked). | landscape: distilled 150M CPU rerankers (*Ettin HF 2025-26*) — cut with a spike gate |
| **GRF-4 · typed-2-hop reachability audit** | RESHAPE (P3) | Static eval-side min-hop-reachability report (which depth 1/2, which edge type first reaches each expected stem) per multi-hop row, registered as GRA-7's typed baseline arm. No router, no shipped depth-2 expansion. | landscape: HippoRAG PPR baseline framing (*arXiv:2502.14802*) |
| **GRF-5 · phrase-overlap vs GRA-3 spike** | RESHAPE (P3) | Dated RUL-0-style spike: does stopword-filtered phrase n-gram co-occurrence surface unlinked pairs GRA-3's fused densification misses? Only if a stated complement bar clears, fold phrase overlap into GRA-3's existing suggestion loop — no shadow-edge cache, no hot-path half. | landscape: A-Mem Zettelkasten link structure; co-occurrence edges survive no-LLM |

### SEN — Write-side quality & memory security  *(tier T10, builds on the shipped spine)*

| Idea | Verdict | What | Provenance |
|---|---|---|---|
| **SEN-1 · write-ticket verifier** | RESHAPE | Extend `check_candidate` into a deterministic pre-write verifier — **mechanize the currently-procedural secret gate**, fenced-hunk fidelity vs a fresh `git show <HEAD>`, archive-shadow collision — surfaced as a "write ticket" (renamed off the GOV-5 "receipt") at the approval prompt. Warn-only; the container for SEN-2/SEN-3. | landscape: Cloudflare 8-check deterministic write verifier; SAGE novelty gate (*arXiv:2605.30711*) |
| **SEN-2 · write-side threat lint** | RESHAPE | `secrets.py`-sibling deterministic lint: **Tier-A** (invisible Unicode zero-width/bidi/PUA with emoji-ZWJ carve-outs, mixed-script confusables, exfil-link shapes scoped to image-embeds/data query-strings, HTML comments *lint-only pending an ED-3 spike*) surfaced + import HOLD; **Tier-B** (imperative injection grammar) **ledger-only** until a dated FP-rate decision. Lands inside SEN-1. *(x2)* | landscape: invisible-Unicode survives forks (*Pillar 2025*); MINJA (*arXiv:2503.03704*); spotlighting cut injection ~50%→<2% (*Microsoft*) |
| **SEN-3 · ungrounded-prescription lint** | RESHAPE | Flag agent-voiced intent attribution ("the user always wants X") when grounded in neither the captured hunk (GRW-1) nor an explicit `--rationale` (GOV-3); warn-only at write + an audit sweep + a doctor fraction line. Zero-FP-gated against hippo's own prose before default-on. | landscape: memory amplifies sycophancy 25x (*MIST*); transcription-not-synthesis |
| **SEN-4 · adversarial eval category** | RESHAPE | A new `adversarial` RET-8 category — poisoned-memory fixture corpora under `.audit-fixtures` (zero loader changes) that **deterministically report** per row: payload crossed into `format_results`, SEC-6 quarantined it, SEC-5 consent showed it byte-equal, a threat lint flagged it, knee/floor/MMR admitted it. **Acceptance-tests the shipped spine.** The always-on spotlighting envelope is CUT (deferred, honestly reframed as boundary-spoof-resistance). | landscape: AgentPoison ≥80% at <0.1% (*NeurIPS'24*); static scanning is the wrong anchor (*SkillCloak 2026*) |
| **SEN-5 · incident response verbs** | RESHAPE | Two verbs: `untrust <repo>` (registry-entry removal beside `mark_trusted`; revocation-by-gate needs no cache wipe) and `blast-radius <name>` (read-only join over `episode_buffer.recalled_names`, `links.json` adjacency — its first real consumer — `gov_citations`, `.archive_journal.jsonl`, with an explicit coverage banner). Quarantine tier dropped (collides with shipped SEC-6). Extends GRA-8. | landscape: MCPoison persistent compromise via no re-consent (*CVE-2025-54136*) |

### TMB — Temporal truth, lifecycle & forgetting  *(tier T11)*

| Idea | Verdict | What | Provenance |
|---|---|---|---|
| **TMB-1 · resolve-inbox evidence card** | RESHAPE | Render a deterministic evidence card in `resolve --list`/`describe()` for each contradicts pair: **commits-since-birth age** (git-mined, satisfies the no-timestamps pin), git-newer side (via `provenance.git_last_commit_with_time`, never importing `reconsolidate`), cited-code drift, usage asymmetry (only above a session-count floor); a prefill suggestion in the resolve skill's own four verdict names; one additive field on each verdict path capturing prefill-vs-choice. *(merges conflict-chronometer ×3 + freshness-adjudicator)* | landscape: Zep bi-temporal validity (*arXiv:2501.13956*); MEMTRACK conflict resolution |
| **TMB-2 · invalid_after terminal state** | RESHAPE | Widen `invalid_after` visibility from stale-scoped to **corpus-wide** (a count of memories retired via supersede/merge that today signal nowhere) + a 5th admission leg to `archive_candidates` so a time-invalidated (no code-drift) memory can reach the shipped GRA-5-guarded archive flow. No new verb — reinstatement is the existing `graduate`/`fix`. | landscape: forgetting failures dominate production (*ForgetEval*) |
| **TMB-3 · forgetting correctness & archive reversibility** | RESHAPE | A read-only `check_archive_shadowing` doctor check + a hermetic pin that builds never traverse `archive/` + a **report-only `forgetting` eval category** (archive-absence rows via directory listing, scored by a new absence-polarity metric, through SIG-6's confirm flow) + a **decoupled `archive.restore(stem)` primitive** (per-item, journaled, GRA-5-style collision guard) + an evidence-only regret detector (abstention clusters vs archived bodies via vendored BM25). No auto-restore wiring. *(merges forgetting-gate + archive-regret)* | landscape: selective forgetting ≤7% everywhere (*MemoryAgentBench 2025*); PersistBench cross-domain leakage |
| **TMB-4 · edge-derived temporal fixtures** | RESHAPE | A deterministic synthesizer walks supersedes chains (`load_edges` typed_out + `history.typed_inbound` tip resolution) + GRW-7 successor dates to draft **`category:update`** rows (query = verbatim span of the superseded memory, gold = live tip) + premise-resistance rows, routed one-at-a-time through SIG-6's confirm flow. Populates the zero-row update category. `GATE_UPDATE_*` promotion stays a dated owner decision. *(x2)* | landscape: knowledge-update is the measured-weakest ability (*LongMemEval*); STALE write-side adjudication +13pp (*arXiv:2605.06527*) |
| **TMB-5 · succession replay** | RESHAPE | On `demote --superseded-by`, harvest historical `query_preview`s that recalled the OLD name, re-run `recall()` offline against the post-verdict corpus, print PASS/FAIL/INCONCLUSIVE per query (does the successor rank now? does the tombstone still leak?) + a doctor line. Read-only; the SIG-6-fixture-drafting half cut (SIG-6 not on this baseline — PR #17). | landscape: MemoryAgentBench knowledge-update track |

### CLB — Team collaboration substrate  *(tier T12)*

| Idea | Verdict | What | Provenance |
|---|---|---|---|
| **CLB-1 · corpus review packet** | RESHAPE | A zero-LLM `memory review [base..head]` CLI + skill: op-classify each touched memory (ADD/UPDATE/SUPERSEDE/ARCHIVE/EDGE from frontmatter/edges/archive-moves), run touched-file-scoped lints (`scan_text`/`scan_portability`/`conflict_radar`/edge-integrity), and — **local-only, never CI** — replay recent `episode_buffer` previews against a temp shadow index to preview recall impact. `--ci` mode's nonzero-exit **IS the SEC-8 CI-scan vehicle**. No auto-approve anywhere. | landscape: PR-review-for-memory is named best practice, unimplemented (*fountaincity 2026-05*); shared team memory = top CC bottleneck (*#38536*) |
| **CLB-2 · verified_by attribution** | RESHAPE | Additive `verified_by: <slug>@<own-ts>` stamp through the existing gated reverify verdict; doctor/scorecard consumers for BOTH `last_verified` (currently dark) and `verified_by` (never-verified-by-non-author, authored-vs-verified ratio) — **every team line suppressed at ≤1 git author**; a new AST pin that `verified_by` is never a ranking input. Sequenced behind SEC-14. | landscape: Copilot citation + repo-permission scoping + 28-day expiry, +7pp merge (*GitHub 2026-01*) |
| **CLB-3 · evidence-fence + cited-code drift** | RESHAPE | A machine-recognizable evidence-fence marker (path:region) in the drain contract (future drains only) + a drift detector inside the existing `find_stale` pass (never `build_index`, an AST pin proves it) that diff-aware-matches marked hunks against the tree, writing an optional `evidence_drift` into `stale.json` and upgrading the RET-6 banner. | landscape: Copilot just-in-time re-verification before reuse (*GitHub 2026-01*) |
| **CLB-4 · incoming-merge duplicate digest** | RESHAPE | At SessionStart, when the incoming range touches `.claude/memory/`, run `committed_duplicate_neighbors` over just the incoming stems (cap ≤5), emit one producer + one doctor line routing each pair to `/hippo:consolidate` (GRW-3 merge) or `/hippo:resolve`. Reuses `_recent_merge_signals`/GOV-4 ledger — no second detector, no autonomous edge write. | landscape: obsidian-git merge conflicts unresolved (#803); CRDTs merge structure not semantics |

### IOP — Interop & reach  *(tier T13)*

| Idea | Verdict | What | Provenance |
|---|---|---|---|
| **IOP-1 · foreign-dialect radar** | RESHAPE | Report-only census of scoped-rule dialect files present in the repo (Cursor `.mdc`, Copilot `.instructions.md`, watch-only `.agents/rules/`) by presence alone + cross-dialect divergence (reuse `rule_dup_candidates`) + **git-drift rules-rot** on `.mdc` via the RCH-2 `parse_mdc`/`resolve_globs`. All in a new `FOREIGN_GLOBS` sibling **never merged into `GOV_GLOBS`** (so RUL-1/3/4 authority is untouched). *(merges rule-dialect-bridge + all-dialect-rules-rot)* | landscape: ctxlint lints rules vs codebase (*GH 2026-07*); scoped-rule frontmatter converged-semantically / fragmented-syntactically |
| **IOP-2 · import upstream fingerprints** | RESHAPE | At `.mdc` import, append the source `.mdc`'s own repo-relative path into `cited_paths` so RET-6's shipped git-log staleness scan flags **upstream drift AND deletion for free** — no new frontmatter, no new doctor check. | landscape: hash-on-change re-consent (*MCPoison 2025*) |
| **IOP-3 · curated export receipts** | RESHAPE | `export_agents` gains a report-only curation receipt (per floor line: soak strength + maturity, staleness, graduation, conflicts, rot; and what was excluded) — zero AGENTS.md bytes changed. Counters the "LLM-generated AGENTS.md hurts −3%" finding with a *curated, evidence-bearing* export. | landscape: LLM-generated AGENTS.md cuts success ~3% (*ETH arXiv:2602.11988*); progressive-disclosure consensus |
| **IOP-4 · claude-mem migration audit** | RESHAPE | A single claude-mem `(discover, parse)` adapter into `import_mdc`'s generic tail, **v1 audit-only** (zero writes) gated on an ED-3 live probe of claude-mem's store as step zero; any later write leg reuses the shipped `pack_install_item` refuse-on-secret + SEC-5 consent pattern. | landscape: claude-mem 86.6K stars = the graduation-path import target (*GH 2026-07*); Letta MemFS convergence |

---

## 4. Kill & the disciplined cuts

- **KILL — `trust-ratchet-state-anchored-consent`**: unanimous, and verified —
  the working tree already ships materially this entire candidate as the SEC-5/6/7
  trust spine (`2f960a7`: `corpus_fingerprint` + `consented_hashes` +
  `untrusted_changes` + `record_authored_write`, per-file sha256 on consent,
  drift-quarantine at recall). Building it again violates inv5 (one canonical
  implementation). Its only residue — *author + signature labels* on the shipped
  drift delta — survives as a P3-late detection rider (folded into the roadmap's
  security backlog note, not a standalone tier item).

The reshapes are themselves the discipline. Recurring cuts the panels enforced,
each a round-1 kill re-derived under new evidence:

- **Every autonomous-write/ranking half was severed and gated.** Auto-restore
  (TMB-3), auto-approve of clean memory PRs (CLB-1), accept-prefill on resolve
  verdicts (TMB-1), the always-on spotlighting envelope (SEN-4), Tier-B threat
  flags (SEN-2), `GATE_UPDATE_*` promotion (TMB-4), the salience flip (MSR-5) — all
  deferred behind a **dated owner decision + measured evidence**, never a
  metric-proxied gate (the LIF-7 `soak_status` lesson).
- **The fabrication vector was re-killed twice.** Any templated/generative fixture
  query (TMB-4) or auto-drafted restore proposal (TMB-3) collapses the
  derivation-only property that separates these from round 1's killed
  `demand-gap-auto-draft`; both are constrained to **verbatim spans / evidence-only
  detection** and fail closed.
- **Two double-ownership conflicts resolved.** RET-9 is delivered **solely** by
  GRF-3 (dense-floor calibration), stripped from MSR-4. SEC-8's CI-scan gate has a
  **single vehicle** — CLB-1's `--ci` mode — into which SEN-2's threat lint feeds,
  rather than two competing CI invocation points.
- **Naming collisions cut, not renamed-around.** "receipt" (SEN-1 → "write
  ticket", vs GOV-5), "quarantine" (SEN-5 dropped its tier, vs shipped SEC-6),
  "origin" (GRF-1 → `edge_origin`, vs RCH-1's memory-level `origin`), "resurrect"
  (TMB-2 uses `graduate`/`fix`, vs the recall.py expansion invariant).

**Coverage gaps the critic flagged, and the call on each:** RET-11 (BM25-only
abstention floor) — its *detection* half is folded into MSR-4 (near-miss scores on
the abstention arm give the decision its evidence); the production floor change
stays a ROADMAP.v1 item. SEC-12 (zip bypass) and SEC-13 (MCP write-gate asymmetry)
— genuine misses, but they are **security-hardening owned by ROADMAP.v1**, not
enhancement bets; MSR-3 adds a measurement rider (count MCP writes hitting an
untrusted corpus) and the roadmap notes them as adopt-from-v1. PRF-4/5, SEC-9/10/11
— correctly out of scope (launch-readiness chores). Spotlighting envelope — the
single most evidence-backed unclaimed defense; deferred with SEN-4 as its
acceptance-test predecessor, named rather than buried.

---

## 5. Cross-cutting decisions these surface (for owner ratification)

- **ED2-1 (new): the measurement spine is the keystone this round.** Just as
  round 1's salience keystone (SIG-5) gated a ranking class, round 2's eval
  observability spine (MSR-1..4) gates a *measurement* class — the salience-revisit
  A/B rig (MSR-5), the GRA-7 PPR gate (GRF-2/4), the forgetting category (TMB-3),
  the update category (TMB-4) all need persisted, baseline-diffable, condition-
  matrixed eval that does not exist yet. **Sequence T8 first.**
- **The salience revisit is now buildable but stays owner-gated.** MSR-5 supplies
  the lived-in-corpus A/B evidence ED-2's revisit trigger names — but the flip
  remains a dated owner decision on affirmative evidence, never a delta threshold.
- **SIG-6 is a hard cross-file dependency.** Six items (MSR-5, TMB-3, TMB-4, and the
  confirm-flow riders) depend on `ROADMAP.enhancements.yaml`'s SIG-6, which sits on
  its own open PR #17 (branch `enh-t7-sig6`, base `main`), absent from this baseline.
  It should land (merge PR #17; it is the one freely-buildable T7 item) before those
  tiers.
- **The trust spine's arrival re-opens the reach tail.** With SEC-5/6/7 + RCH-5
  shipped, the install/import/team surface (SEN-4, CLB-1, IOP-4) is buildable now —
  but each still runs its own **ED-3 live-probe spike** before parsing any foreign
  format (the RCH-2 "Cursor frontmatter isn't valid YAML" lesson).
- **Schema discipline holds:** every additive field this round (`channel`,
  `injected_chars`, `edge_origin`, `evidence_drift`, `verified_by`) is
  absence-emits-nothing — **no corpus_format or SCHEMA_VERSION bump is planned**;
  `_SEED_SCHEMA`/derived-cache gates move only if a specific item needs them, one
  clean break each (ED-4).

---

## 6. Top 7 highest-leverage moves (ranked)

Chosen for end-user efficacy × on-identity fit × reuses shipped machinery ×
cross-lens convergence.

1. **Build the measurement spine** (MSR-1 eval ledger + MSR-4 drop-reason autopsy +
   MSR-3 MCP telemetry). Everything downstream — the salience revisit, the PPR
   gate, the forgetting/update categories — is unfalsifiable until hippo can
   persist a run, diff a baseline, and read the ledgers it already writes.
   Reproducible deterministic eval is *itself* the differentiator the crowded 2026
   field can't reproduce.
2. **Finish and observe the graph** (GRF-2 mixed-mode leg + n≥10 growth, GRF-1
   GRA-8 CLI). The production multi-hop path is fixed; close the last degradation
   leg, grow the instrument past n=2, and make "graph-backed" screenshot-able —
   which also makes GRA-7's PPR gate finally decidable.
3. **Acceptance-test the trust spine you just shipped** (SEN-4 adversarial category
   + SEN-1 write-ticket verifier). A defense with no adversarial fixtures is
   unproven; a procedural secret gate wants mechanizing. Both are deterministic,
   local, and turn the 2026 memory-poisoning threat model into a regression suite.
4. **Turn review-gated writes into team ergonomics** (CLB-1 review packet + CLB-3
   cited-code re-verification). The unclaimed high ground: no shipped system does
   PR-review-for-memory, hippo's identity *is* it, and git is the unfair advantage
   Letta just pivoted toward.
5. **Give the resolve/lifecycle plane a clock** (TMB-1 evidence card + TMB-2
   invalid_after terminal state). Git supplies the missing age/freshness for free;
   the contradiction inbox and the retire-without-drift blind spot are the sharpest
   remaining legibility gaps.
6. **Measure forgetting correctness** (TMB-3 shadow check + forgetting category +
   decoupled restore). Forgetting failures dominate production; hippo's archive is
   a one-way door with a shadowing hazard — a report-only category and a guarded
   `restore` close it detection-first.
7. **Extend reach onto the shipped spine** (IOP-1 foreign-dialect radar + IOP-2
   import fingerprints + IOP-4 claude-mem audit). Make hippo the tool you *graduate
   to* from the 86K-star incumbent, and the git-drift hygiene layer over every rule
   dialect in the repo — all detection-only, all trust-spine-gated where they touch
   foreign content.

---

*Prepared as an ideation catalog, adversarially vetted (47 candidates → 37 merged →
36 RESHAPE + 1 KILL across 158 agents). No code, `ROADMAP.yaml`, `ROADMAP.v1.md`, or
`ROADMAP.enhancements.yaml` status was changed. Items here are candidates for a
round-2 enhancement train (tiers T8–T13); the executable form is
`ROADMAP.enhancements2.yaml`. Held for owner review.*
