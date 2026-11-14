# hippo — `/dream`: the generative sleep pass (design exploration)

**Status: DRAFT design for owner review.** A focused capability exploration (not a
broad catalog like [`EXPLORATIONS.md`](EXPLORATIONS.md) /
[`EXPLORATIONS2.md`](EXPLORATIONS2.md)) for a single new verb: **`/dream`**. Research
and design only — no implementation, no status flips on existing roadmap files. Its
executable companion is the **DRM** workstream (§7 below); the DRM-2 auto-apply loop
has its own implementation spec in [`DRM-2.spec.md`](DRM-2.spec.md). Authored
2026-07-12.

Method: a 14-agent orchestrated pass — a 4-lens landscape sweep (neuroscience of
sleep-dependent consolidation; deployed LLM-agent memory systems; self-sustaining /
"dreaming" memory + continual-learning; eval methodology), a 4-angle design panel
(replay/edges · schema/gist · downscaling · insight/hypotheses), a 3-judge adversarial
score, a proof-plan + red-team pair, and a synthesis — followed by a **hand
verification pass over `plugin/memory/{links,recall,eval_recall,soak,secrets,
outcome,history,archive,session_start,mcp_server,build_index}.py`** that confirmed the
backbone's primitives and caught three places the design memos were wrong (§8). Every
mechanism below cites `file:symbol` and was checked against the working tree.

---

## 0. TL;DR

hippo already implements the **housekeeping** functions of sleep — `consolidate`
(systems consolidation: episodic capture → durable memory), `reconsolidate.py`
(reconsolidation: recently-recalled + drifted = the labile-on-recall set), `staleness`
(decay), salience priors (recency/usage). It does **not** implement the **generative**
function of sleep: replay that *recombines* existing memories into net-new knowledge.

**`/dream` is the generative sleep pass: an offline turn that re-activates the corpus
against itself, watches what co-fires, and produces the graph structure the corpus is
structurally missing — additive, body-preserving, git-reversible.** It fills the one
niche none of the four housekeeping verbs can reach.

Two theses drive the design:

1. **The open niche is discovery, not housekeeping.** `consolidate` refreshes links
   from the only two signals hippo has today — write-time embedding similarity (GRA-3)
   and Hebbian co-recall (GRW-2) — both of which, *by construction*, can only connect
   memories that were already similar at creation or already co-surface. Neither can
   reach the edge class that matters most for retrieval: **pairs bridged transitively
   (A–B–C with no A–C), named-but-unlinked in a body, or related by an undeclared typed
   relation.** Those latent edges are exactly what `recall._expand_neighbors`
   (`recall.py:1025`, GRA-1) traverses to turn a 2-hop miss into a 1-hop hit. `/dream`
   is the verb that finds them.

2. **Reversible autonomy, not propose-only.** The safety mechanism is not human
   approval — it is that **memory lives in git, so an additive edge is trivially and
   completely reversible.** When an action is cheaply reversible *and* legible after the
   fact, "act-then-undo" is a *stronger* safety model than "gate-then-act." `/dream`
   therefore **auto-applies its safest outputs and surfaces an undo affordance after**,
   spending autonomy exactly where undo is real and blast radius is a ranking nudge —
   and holding the gate only where undo-after genuinely fails. This is the layer that
   moves hippo off ~100% HITL without weakening it.

---

## 1. The open niche (grounded)

| Verb | What it does | Why it can't reach latent edges |
|---|---|---|
| `consolidate` | drains the CAP-2 capture queue (episodic seeds → memory), refreshes links | links come from write-time similarity (GRA-3) + co-recall (GRW-2) only — already-similar / already-co-surfacing pairs |
| `reconsolidate.py` | repairs drifted *recently-recalled* memories | fixes bodies that cite drifted code; discovers nothing new |
| `audit` | judges corpus health, report-only | reports; never writes an edge |
| `resolve` | drains the `contradicts` inbox | acts only on already-declared contradictions |

`/dream` is the creative/insight function of sleep — quarantined to **discovery**,
while every **write** stays additive, stamped, and git-reversible. It is distinct from
and additive to the round-1 catalog's Theme D ("the corpus governs itself") and Theme F
("knowledge grows itself"): those grow the corpus from *sessions*; `/dream` grows it
from the corpus's own *latent structure*.

## 2. The landscape (neuroscience ↔ deployed AI memory)

The neuroscience→AI bridge is a live, real field, and hippo already sits inside it (the
roadmap cites Letta's sleep-time-compute paper, arXiv:2504.13171, in MSR-5). Anchors
that survived verification:

**Neuroscience.**
- **Replay is the engine.** Hippocampal sharp-wave-ripple replay re-fires waking
  sequences offline (Foster & Wilson 2006, *Nature*); **reverse** replay propagates
  reward backward along the path — literal credit assignment — and is *reward-gated*
  (Ambrose, Pfeiffer & Foster 2016). → the DRM-5 reverse-credit pass.
- **Preplay = recombination.** The hippocampus assembles trajectories it *never took*
  from existing assemblies (Dragoi & Tonegawa 2011, *Nature*) — the biological warrant
  for proposing connections the user never explicitly stated. (Contested; treat as
  inspiration, not proof.)
- **Complementary Learning Systems / systems consolidation** (McClelland, McNaughton &
  O'Reilly 1995): fast sparse store → slow structured store via *interleaved* replay →
  the schema-abstraction payload.
- **The forgetting function.** Synaptic Homeostasis (Tononi & Cirelli 2014) downscales
  weak weights; Crick & Mitchison 1983 "reverse learning" removes spurious attractors →
  the de-parasiting counterweight. Modern ML echo: van de Ven's brain-inspired replay.
- **Sleep and insight** (Wagner et al. 2004, *Nature*, "Sleep inspires insight"): sleep
  extracts the hidden rule — the warrant for the (quarantined) hypothesis tier.

**Deployed AI memory (what proves these operations work in practice).**
- **HippoRAG / HippoRAG2** (Gutiérrez et al.) — neurobiologically-inspired (hippocampal
  indexing theory), Personalized PageRank over an offline knowledge graph. The closest
  cousin to hippo by *name and mechanism*; validates offline-graph-building for
  multi-hop retrieval.
- **A-MEM** (`WujiangXu/A-mem`, NeurIPS 2025) — Zettelkasten notes that auto-annotate
  and auto-link to top-k neighbors; the closest cousin to the edge-discovery backbone.
- **Letta sleep-time compute** (arXiv:2504.13171) — a background agent reorganizes
  memory off the critical path; ~5× test-time token savings for equivalent quality.
- **Generative Agents' reflection tree** (Park et al. 2023) — importance-gated periodic
  synthesis of higher-level insights → the schema/gist tier.
- **Zep / Graphiti** — bi-temporal KG with edges carrying validity intervals and
  contradiction-invalidation → the temporal/supersede routing.
- **Mem0** (production) — autonomously applies its own ADD/UPDATE/DELETE memory
  decisions with **no per-item gate**. Direct field precedent that autonomous memory
  writes are deployable; hippo's advantage is real git undo, which Mem0 lacks.
- **microsoft/graphrag** — hierarchical community summaries = schema abstraction at
  scale. **van de Ven** (`GMvandeVen/brain-inspired-replay`) — generative replay against
  catastrophic forgetting.

The "self-evolving memory" wave (NEMORI, Self-Consolidation, SSGM, 2026) is mostly
speculative/toy — the owner's prior that "not all are practical" held up.

## 3. The design (winning conception + grafts)

Unanimous judge verdict: **replay-driven latent-edge discovery is the provable, safest
backbone**, with the bolder generative ideas riding on top once it proves out. Each
operation maps neuro-analog → the **verified** hippo primitive → the artifact:

| Neuro-analog | hippo primitive (verified) | Artifact |
|---|---|---|
| Prioritized SWR replay | `links.LinkGraph.degrees/isolates/orphans` + telemetry usage/staleness | ranked replay worklist, each entry tagged *why* (`isolate, 0 inbound`) |
| SWR replay / co-firing | `recall.recall()` offline + `eval_recall.derive_self_query` + `_expand_neighbors` (to exclude already-reachable) | per-memory co-firing set (edge provenance) |
| Preplay / associative knitting | `LinkGraph.traverse/connected_components` + `parse_wikilinks/resolve` | proposed `[[wikilink]]`: source, target, graph distance, co-fire strength, the firing query |
| Reconsolidation re-indexing | `parse_wikilinks` + `LinkGraph.resolved_via_stem` | **body-already-names-target** completion (highest precision, top-sorted) |
| REM schema integration / contradiction detect | `links.add_typed_relation(dry_run=True)` — `supersedes/contradicts/refines` only, idempotent, **no batch path** | `refines`/`supersedes` frontmatter diff; **`contradicts` → `/hippo:resolve`, never auto-applied** |
| Reverse replay from reward | `outcome.injection_precision` + `history.decision_chain` | outcome-anchored edge boosts carrying the justifying decision chain |
| SHY downscaling / unlearning *(graft)* | `LinkGraph.degrees` (hub detection) + out-degree cap | de-parasiting guard: cap fan-in, de-prioritize edges into hubs |

The **schema/gist** tier (net-new semantic parents, `refines` up from each child) and
the **hypothesis** tier (`A+B ⇒ likely C`) ride on top as later, opt-in, *quarantined*
tiers (§4 Tier B/C) — never v1.

## 4. The autonomy model — reversible autonomy

This is the heart of the design and the answer to "hippo is ~100% HITL." The loop is
**not** `propose → gate → apply`. It is:

> **apply-reversibly → notify → undo-window → age-in**

### 4.1 The reversibility gradient

Autonomy is proportional to **reversibility × (inverse) blast-radius**. Not every
operation earns the same autonomy:

| Tier | Operations | Posture | Why safe at this level |
|---|---|---|---|
| **A** | `[[wikilink]]` / dangling-completion / `refines` edges | **Auto-apply**, digest + undo surfaced after | Additive frontmatter, body byte-identical (`add_typed_relation`), blast radius = a ranking nudge (`_NEIGHBOR_DISCOUNT=0.5`; a neighbor must out-compete organic candidates), perfectly `git revert`-able. An un-noticed one costs almost nothing. |
| **B** | schema / gist memories (net-new semantic parents) | **Auto-stage, self-decaying** — created at `confidence:draft`, down-weighted, **auto-archives at a horizon** unless an external event graduates it | Asserts a *claim* at higher altitude → bigger blast radius. Autonomy is fine because influence is capped and *expires on its own*. |
| **C** | `supersedes` (demotes a trusted memory), `contradicts`, hypotheses asserting new fact as `verified` | **Gated** (`contradicts` → `/resolve`) | These *remove/override* trusted knowledge or assert new fact. In the undo window a superseded memory silently stops surfacing — you may not notice it is gone. Not safely reversible-after. |

This is not conservatism — it spends autonomy where undo is real, and holds it only
where "undo-after" genuinely fails.

### 4.2 The aging firewall (what makes Tier-A auto-apply safe *without* a human)

The one failure mode that breaks with auto-apply is the **dream-cites-a-dream tower**:
if pass N+1 consumes pass N's un-reviewed edges as source, drift compounds silently. The
fix is a firewall, not a gate:

> Auto-applied edges influence **recall immediately**, but are **excluded from
> `/dream`'s own source set** until they *age in* — survive ~5 sessions without being
> undone — or the owner explicitly blesses one.

`/dream` therefore never bootstraps on its own un-reviewed output; the tower cannot
form; yet nothing had to be approved. **Aging = implicit ratification by non-undo.**
(Reuses the ≥5-session bar already in `soak.soak_status`.)

### 4.3 Notify-with-undo (verified buildable on three existing patterns)

- **MCP auto-reply** — MCP tools are functions returning user-visible text
  (`mcp_server._tool_recall` etc.). `_tool_dream` applies the pass and returns
  *"🌙 added N edges — [each + why] — reply `undo` / `undo <id>`."*
- **SessionStart nudge** — a `dream_applied_producer` (same shape as
  `session_start.pending_capture_producer`, `_MAX_ITEMS_PER_PRODUCER`-bounded)
  surfaces *"dream applied N edges since last session — `--undo-since`."*
- **Undo is git-native** — `archive.archive_memory` is `git mv` ("fully
  git-reversible"); `add_typed_relation` is additive frontmatter. `--undo` reverts what
  is stamped `discovered-by: dream`. No new storage model.

Full loop spec: [`DRM-2.spec.md`](DRM-2.spec.md).

## 5. The safety case (five non-negotiables, reframed)

The autonomy is spent on *reversible generation*; the discipline that keeps it safe:

1. **Empty pass is the norm; non-empty is salient.** Hard single-digit cap per pass;
   tune the cohesion bar aggressively conservative (the `soak`/`co_recall min_sessions=3`
   philosophy). A non-empty digest must be rare enough to read. Report-only remains the
   *default posture until DRM-1 calibration earns the Tier-A flip* (§7).
2. **`/dream` reads only confirmed sources — never its own unconfirmed output.** Source
   set = `confidence:verified` + user-asserted; never `confidence:draft`, never a
   not-yet-aged `discovered-by:dream` edge. Dream-injected co-recalls must not feed the
   next pass's priority. *(The aging firewall, §4.2 — the only defense against the
   speculation tower; nothing today provides it.)*
3. **Generative claims are quarantined and must decay** (Tier B): `confidence:draft`,
   down-weighted, graduation to `verified` requires an *external evidence/outcome event*,
   auto-propose-archive past a horizon. The closer for hippo's own FM2
   (`reconsolidate.py:8` — a frequently-recalled wrong memory that grows strength).
4. **Hard secret BLOCK on the dream write path + mandatory provenance-stamp lint.** Note
   this is a *deliberate deviation* from hippo's WARN-never-BLOCK secret philosophy
   (`secrets.py`), justified because dream *generates* text rather than transcribing user
   intent (§8, correction 1 — **ratified 2026-07-12**). `discovered-by: dream` +
   `derives-from` is a hard lint (a proposal without complete provenance is rejected).
5. **Honest eval scoping.** The fixture guardrails gate *release of the verb*; they
   **cannot run on the real unlabeled corpus** (§6). On the deployed corpus the live
   protection is exactly **additive git-revertibility + the undo affordance**. Any claim
   that the eval protects the real corpus is struck.

**vs. the "ChatGPT Dreaming" anti-pattern** (MSR-6: offline generative memory that
erodes provenance): hippo defends this *structurally*. `add_typed_relation` is
body-byte-identical additive frontmatter; `set_invalid_after` and `archive_memory` are
additive/reversible; the store is git, so every applied edge is a stamped, greppable,
`git revert`-able diff. There is **no code path** by which `/dream` mutates a body or
bulk-applies edges. Auto-apply does not change this — it changes *when* you look, not
*whether it is reversible*.

### 5.1 The honest cost of auto-apply

Auto-apply trades **"review fatigue defeats the gate"** for **"inattention lets drift
ride"** — same root (human doesn't look), different failure. It is an acceptable trade
**only for Tier A**, because an un-noticed edge is a reversible ranking nudge; it is
*not* acceptable for Tier C, where an un-noticed supersede quietly deletes recall. The
gradient is what makes the trade sound. Mitigations: the empty-pass norm (§5.1), and
`dream --log` / `--undo-since <date>` so bulk audit stays one command.

## 6. The proof plan

**Falsifiable hypothesis.** On a frozen corpus snapshot, admitting the typed edges one
`/dream` pass produces raises *multi-hop* retrieval **because of the edges** — not
general lift — without eroding the recall floor or abstention. FALSIFIED if any of:
(a) multi-hop recall@10 does not rise above the multi-seed noise floor; (b) it rises
only together with a matched single-hop control (gain not attributable to the edges);
(c) the bridged edge already existed at baseline via GRA-3/GRW-2 (dream discovered
nothing); (d) any guardrail regresses past its gate.

**Primary metric.** `eval_recall.evaluate() → report['by_category']['multi-hop']
['recall']@10` — the bucket whose docstring says it *"validates GRA-1 expansion"*
(`eval_recall.py:45`) — as a **paired** OFF→ON delta with a Wilcoxon signed-rank test.
Secondary: MRR@10 on the same bucket (catches ranking-only lift near saturation).

**Attribution test (load-bearing).** For each multi-hop probe, author a **MuSiQue-style
matched single-hop control** (Trivedi et al. 2022) answerable without traversal. The
credible signal is multi-hop rising *more* than the control; if both move together,
attribute to general lift, not the edges.

**A/B protocol.** A `HIPPO_DREAM` arm toggling only whether the LinkGraph admits
`discovered-by:dream` edges; OFF must reproduce the pinned baseline byte-identical; N≥5
index rebuilds establish the noise floor (à la Mem0's ±1σ). **Because auto-apply stamps
every edge, this A/B runs on the *live* corpus continuously — not just a fixture** —
which is the direct answer to the red-team's sharpest criticism.

**Guardrails (release gates, GEM-style — Lopez-Paz & Ranzato 2017):** `self_recall@10 ≥
0.90` with a BWT-analog (zero previously-passing queries flip hit→miss); matched
single-hop control flat; `abstention_rate ≥ 0.30` and non-decreasing on the fixed
negative set; `precision@10 ≥ 0.12`; net token reduction > 0; MRR ≥ 0.60; warm p95 <
300ms.

**Honest limits.** Proves *admitted edges help*, not *discovery precision* (needs a
separate ratify/undo-rate alarm); retrieval proxy, not proof agent decisions improved;
**no token-saving claim** (edges *add* injection cost — the token win belongs to the
Tier-B schema payload); fixture-green is necessary, not sufficient.

## 7. Build path (DRM workstream)

DRM-1 (ledger-only) is still the right first slice — its job is now explicit: *earn the
right to flip Tier-A auto-apply on* by proving discovery signal-to-noise on the live
corpus first.

| id | title | posture | reuses | gate |
|---|---|---|---|---|
| **DRM-1** | Replay harness, ledger-only, zero writes | report-only | `recall()`, `derive_self_query`, `LinkGraph.traverse` | — |
| **DRM-2** | Tier-A auto-apply + MCP notify-with-undo | **autonomous (reversible)** | `add_typed_relation`, `archive` git-mv, `_tool_*`, SessionStart producer, `soak_status` aging | DRM-1 calibration (S/N acceptable) |
| **DRM-3** | Proof harness: `/dream`'s OWN `HIPPO_DREAM` live+frozen A/B | measure-only | `evaluate()`, `by_category`, `hard_set_metrics_by_category` | self-contained — does NOT block on MSR-5 (owner decision, §9) |
| **DRM-4** | De-parasiting counterweight (C3) | proposes demote/merge | `LinkGraph.degrees`, `set_invalid_after`, `archive` | DRM-2 |
| **DRM-5** | Reward-gated reverse replay | outcome-anchored boosts | `outcome.injection_precision`, `history.decision_chain` | DRM-2 |
| **DRM-6** | Generative payload (schema/gist + hypotheses), Tier B/C | quarantined/decaying | *prereq: make `confidence` load-bearing in ranking + add `derives-from` relation* (§8, correction 2) | DRM-3 green |

## 8. Verification ledger (what the design got right vs. wrong)

**✅ Confirmed real — the DRM-1/DRM-2 backbone rests entirely on primitives that exist
as designed:** `links.add_typed_relation(path, relation, target, *, dry_run=False)`
(additive, body-verbatim, idempotent, *deliberately no batch param*, refuses on bad
frontmatter); `LinkGraph.{resolve, resolved_via_stem, traverse(hops=1), orphans,
isolates, connected_components, degrees}`; `recall._expand_neighbors` +
`_NEIGHBOR_DISCOUNT=0.5`; `soak.soak_status` (≥5-session bar); `eval_recall.
{evaluate, by_category, hard_set_metrics_by_category, derive_self_query}`;
`outcome.injection_precision`; `history.decision_chain`; `archive.archive_memory`
(git mv, reversible); MCP tools + SessionStart producers as extension points.

**⚠️ Three corrections (2 from the red-team, 1 from the harness dependency check):**
1. **secret-lint is WARN-*never*-BLOCK by deliberate design** (`secrets.py` — high
   precision over recall; blocking a false positive is judged worse than missing an
   exotic secret). Non-negotiable #4's "hard BLOCK on the dream path" is a genuine
   *philosophy deviation the owner must ratify*, not a free adoption.
2. **`confidence` exists (GOV-7: `draft|verified|authoritative`, `build_index.
   _extract_confidence`) but is display-only** — not a ranking down-weight
   (`build_index.py:539`). The Tier-B quarantine has schema support already; DRM-6 must
   *wire it into recall ranking* (smaller lift than "invent a field," but real). Also:
   `derives-from` is **not** in `links.TYPED_RELATIONS` (`supersedes/contradicts/
   refines`) — `add_typed_relation` will refuse it until added.
3. **The `--ab HIPPO_SALIENCE` A/B rig the proof plan says to "extend" is not shipped**
   — it is **MSR-5** in `ROADMAP.enhancements2.yaml` (status: planned), gated on
   MSR-1/SIG-6. The pieces exist (`evaluate`, `by_category`); the paired-A/B wrapper is
   net-new. **Resolved (2026-07-12): `/dream` ships its own snapshot-diff harness**,
   written to the `eval --ab <flag>` shape so it converges with MSR-5 if that lands (§9).

## 9. Owner decisions (ratified 2026-07-12)

- **Tier-A auto-apply default — APPROVED.** Ship DRM-1 report-only, calibrate S/N on the
  live corpus, then flip DRM-2 to auto-apply — not auto-apply from day one.
- **Secret-lint BLOCK deviation on the dream path — APPROVED** (correction 1). The dream
  write path hard-BLOCKs on a secret match, departing from hippo's WARN-never-BLOCK default,
  justified because dream generates rather than transcribes.
- **DRM-3 harness — `/dream` ships its OWN snapshot-diff harness** (does not block on
  MSR-5). Net-positive regardless: MSR-5 is gated on unshipped MSR-1/SIG-6; the dream
  harness is self-contained (wrap `evaluate()` twice over a frozen snapshot, toggle whether
  the LinkGraph admits `discovered-by:dream` edges, reuse `by_category`, assert OFF
  byte-identical), genuinely needed regardless, and written to the `eval --ab <flag>` shape
  so it converges with MSR-5 if that later lands. See §8 correction 3 (resolved).
- **Aging window — 5 distinct sessions** (reusing `soak`'s bar; `DREAM_AGE_SESSIONS`
  override). The edge is live in recall the whole window, so aging gates only whether
  `/dream` re-consumes it as source — a longer window costs no utility, only delays
  trust-propagation; 5 reuses the one already-calibrated threshold and still gives several
  sessions of nudge visibility before an edge becomes trusted source. Deferral-shaped,
  cheap to tune.
