# hippo — Enhancement & Capability Explorations

**Status: DRAFT idea catalog for owner review.** Companion to
[`ROADMAP.yaml`](ROADMAP.yaml) (shipped v0.2–0.7) and
[`ROADMAP.v1.md`](ROADMAP.v1.md) (the launch plan). This is **not** a commitment
or a release train — it is a vetted menu of *net-new* memory/context
capabilities to raise hippo's end-user efficacy, most of them **post-v1** bets.
Authored 2026-07-08.

Method: a 15-agent divergent-ideation + adversarial-vetting workflow — 3
grounding agents (context/injection seams; the Claude-rules interconnection;
2026 memory research), 6 ideation lenses (~53 ideas), each idea stress-tested by
a dedicated skeptic against hippo's `guiding_invariants` / `non_goals` and — the
hard test — *"would this actually help real users, or just sound clever?"* Every
`file:line` was verified against the working tree; the vetters killed or
reshaped roughly two-thirds of the raw ideas, which is where the signal is.

---

## 0. TL;DR

The biggest near-term leverage is **not new machinery**. It is two things:

1. **Activate the signals hippo already collects and then discards.** The
   engine quietly records an *episode buffer* (query+recalled-names+HEAD
   watermark — read by nothing but capture), a *salience machine*
   (recency/usage/staleness priors, fully built and tested, **default OFF**), an
   *abstention stream* (every "nothing cleared the floor" logged as
   `backend=none`, surfaced nowhere), a *committed team-usage union* (not a
   ranking input), and a *per-hit salience breakdown* (computed, then **rendered
   nowhere**). A large share of the best ideas are simply *"consume a signal you
   already have."*
2. **Become the ranking / hygiene / staleness layer over the Claude-rules
   plane** hippo already reads. This is the user-flagged avenue, and it is
   richer than "preliminary" — see §2.

Three structural gaps recurred **independently across multiple lenses** (strong
signal):

- **No "relevant to what you're doing" context.** All 13 SessionStart producers
  are warnings or the floor; none say *"here are the memories about the code
  you're touching."* Three lenses invented the same fix.
- **Silent abstention is invisible.** RET-1 correctly injects nothing when
  nothing is relevant — but the corpus never learns *what it keeps being asked
  and can't answer.* Three lenses invented the same "blind-spot" fix.
- **The corpus can silently contradict itself.** Typed `contradicts` edges only
  annotate *if both sides happen to co-surface*; a live conflict can sit forever.

And one design discipline the vetters enforced over and over: **ship the
legible detection / human-in-the-loop half; defer the autonomous ranking/write
half** until there's signal density to justify it (most ideas that flexed
*ranking* or *auto-writes* assume deep queues, team scale, long histories, or
co-occurrence volume that solo/fresh users — hippo's base — lack).

Relationship to the launch plan: **these are v1.1+ capability bets.** A handful
sharpen items already named in `ROADMAP.v1.md` (the KPI-2 read-signal, the
salience-default decision, `promote-to-user-tier` = TEA-1's own unmet criterion);
a couple could ride into late-v0.x. None should displace the v1 launch work.

---

## 1. Five structural openings the whole idea-corpus points at

1. **The dark-signal reservoir.** hippo already pays to collect the episode
   buffer (`telemetry.py:585`, read only by `capture.py`), the salience priors
   (`recall.py:1052`, gated behind `HIPPO_SALIENCE`, **default off**), abstention
   events (`backend=none`, empty score arrays, `telemetry.py:234`), and the
   emitted-but-unrendered per-hit salience breakdown (`recall.py:1688` →
   rendered nowhere in `format_results`). Lighting these up is cheap and
   on-identity. *This is the single highest-yield theme.*
2. **The missing positive producer.** SessionStart is all nudges + floor. A
   *"relevant to your current branch/diff"* block is the first context hippo
   would ever inject because of **where you're working**, not because you named
   it — and the ingredients (`capture.gather_session_context`,
   `_git_changed_paths`, `recall.recall`) already exist.
3. **The abstention blind spot.** Correct silence is still silence. Mining the
   `backend=none` stream turns "I had nothing" into "you keep reaching for X —
   capture it," and (separately) into the **KPI-2** injection-precision signal
   the roadmap explicitly says nothing produces yet.
4. **Two authority planes, unreconciled.** Claude's rules plane (CLAUDE.md /
   `.claude/rules` / agents) is *always-loaded, unranked, un-staled,
   monotonically growing*; hippo is *ranked, staleness-tracked, git-reviewed,
   de-duped*. hippo is precisely the layer the rules plane lacks — and it already
   reads that plane one-directionally (§2).
5. **The autonomy ceiling.** The safe, high-value core of nearly every "smart"
   idea is a **legible report or a HITL queue**; the autonomous ranking/write
   half is where the invariant risk and the "assumes usage patterns users lack"
   risk live. Design to the detection half first.

---

## 2. Featured deep-dive — hippo ↔ Claude rules (the named avenue)

**Ground truth (web-verified + repo-verified).** "Claude rules" is not one
feature but a layered instruction plane, and hippo is already wired into it:

- **What the plane is (mid-2026):** CLAUDE.md memory (project/user/enterprise,
  `@import` depth-5, *all always-loaded*); **`.claude/rules/` (Claude Code
  2.0.64)** — modular rule files **pattern-scoped by a YAML `paths:` glob**,
  auto-loaded at CLAUDE.md priority when Claude touches matching files; **Auto
  Memory (v2.1.59)** — the native always-load slot hippo's symlink piggybacks;
  the GA **`memory_20250818`** tool (client-side files); `settings.json`, skills,
  output styles, subagents, MCP resources/prompts; and **`AGENTS.md`** — now a
  Linux-Foundation cross-tool standard (~60k repos, read by Codex/Cursor/
  Copilot/Gemini/Aider/Zed).
- **hippo's current connection is real but one-directional.** `archive.py`
  `_SCAN_TARGETS` (verified, `archive.py:62-68`) already scans `CLAUDE.md`,
  `.claude/rules`, `.claude/agents`, `.claude/skills`, `docs/prompts` — and the
  audit skill adds `AGENTS.md` — to protect any memory a governance file *names*
  from archival. The audit "authority-evidence mismatch" join (a memory cited by
  a rule but that telemetry says nobody recalls) is a **working prototype of
  rule↔memory conflict detection.** hippo writes **nothing** back to the plane;
  its only write-side native touch is the projects-dir symlink that floors
  MEMORY.md through native always-load.
- **The structural gift:** `.claude/rules`' `paths:` glob is the **same shape as
  hippo's `cited_paths`**. Several bridges below are "almost free structurally"
  because of that symmetry. And the **#1 web-confirmed CLAUDE.md pain** —
  `@import` doesn't reduce context; everything always-loads — is *exactly* what
  hippo's ranked on-demand recall exists to fix.

**The interconnection ladder** (cheap/safe → bold; verdicts from the vetting):

| # | Move | What | Verdict |
|---|---|---|---|
| R1 | **Rule↔memory conflict radar** | Standing SessionStart/doctor producer generalizing the audit prototype: fire loud when a rule cites a memory another memory `supersedes`/`contradicts`, **or** cites one with recall-strength <0.15 ("governance says do X, telemetry says nobody uses it"). | **KEEP** (lead with the strength leg — no typed-edge dependency) |
| R2 | **Staleness over rules** | Apply hippo's git-drift detection to the rules plane: flag a `.claude/rules` file whose `paths:` glob matches nothing, or a CLAUDE.md backtick code-reference whose code moved. Reuses `citation_rot` + `provenance` drift. | **KEEP** (lead with CLAUDE.md code-ref drift; gate the `paths:` leg on verifying the harness feature) |
| R3 | **Rules as an on-demand recall source** | *(vetter blind-spot — the elegant one.)* Let a CLAUDE.md/rule surface as a low-priority **recall hit** when relevant, **without importing** it into the corpus — same "rule appears when needed" payoff, zero two-plane duplication. | **KEEP-worthy** — cleaner than importing |
| R4 | **Write-time dedup vs the rules plane** | *(vetter blind-spot — preventive.)* When a new memory restates a rule already in CLAUDE.md/`.claude/rules`, warn "duplicates rule X — link, don't copy." Reuses shipped write-time dup machinery + the governance scan. Stops two-plane drift *before* it happens. | **KEEP** |
| R5 | **Glob-scoped rule promotion** | A repeatedly-reinforced procedural memory → a **PR-able `.claude/rules/<name>.md`** whose `paths:` is derived from the memory's `cited_paths` — so it's *pattern-scoped*, not another always-load line. | **FOLD into LIF-7** (this is the right *scoping* for LIF-7, which `ROADMAP.v1` defers until the CAP capture pipeline is field-proven) |
| R6 | **MCP resources for subagents** | Expose `hippo://floor` (+ a rules-view) as an **MCP resource** so a Task subagent can pull baseline memory at start (no UserPromptSubmit fires for subagents today). Prompts capability is entirely unused (`mcp_server.py:226`). | **KEEP the resource half**; hold the `promote`/`absorb` prompts |
| R7 | **AGENTS.md fan-out** | Render the reinforced floor as a proposed `AGENTS.md` diff so non-Claude agents inherit the same *ranked, staleness-checked* rules — hippo as the one source under the flat-file rule plane. | **RESHAPE** → one-shot export, **post-v1 reach**, scope to AGENTS.md only |
| — | **Floor via `.claude/rules/hippo-floor.md`** | Replace the fragile projects-dir symlink with a git-visible generated rules file. | **KILL** for now — violates inv1 (derived-yet-committed duplicate) + inv5, and bets the delivery path on an unverified harness feature; revisit only if `.claude/rules` priority-load is confirmed real *and* it becomes a first-class authored surface. |

**Guardrails (from the grounding, non-negotiable):** any write into the rules
plane is a **per-item, agent-gated, reviewable diff** (inv4) — never a bulk sync;
CLAUDE.md is a **user-owned peer file**, sync *proposes*, never regenerates
(inv1); path-triggered recall stays **pure retrieval** on the hot path (inv6);
every conflict/stale-rule signal surfaces **loud** at doctor/SessionStart (inv3);
don't create a **second silent always-load channel** (the NATIVE_MEMORY.md
promise). Verify the `.claude/rules` `paths:` auto-load feature actually exists as
described before building R2/R5 on it.

**Net:** the highest-value, lowest-risk rules work is **R1 + R2 + R4** (all
detection/hygiene, all reusing shipped machinery, all loud-not-silent). R3 is the
cleanest mechanism for the reverse direction. R5–R7 are the bolder, later bets.

---

## 3. The vetted catalog (by theme)

Verdicts: **KEEP** (survived cleanly) · **RESHAPE** (survives in a smaller/safer
form — the reshape is stated) · **DEFER/KILL** (§4). Leverage 1–5 (vetter score,
stingy). Effort S/M/L.

### Theme A — Context that follows your work
The missing positive producer + task continuity. **The flagship cluster.**

| Idea | Lev·Eff | What | Verdict |
|---|---|---|---|
| **`diff-seeded relevance producer`** | 4·M | First-ever "relevant to your current branch/diff" SessionStart block: build a query from your uncommitted diff + touched files, run recall, inject the top matches — before you type a word. *(3 lenses converged on this.)* | **KEEP** — cleanest attach in the whole set; empty diff → silent |
| **`where-was-i resume card`** | 4·M | Replay last session's episodes for this repo → "Last session you worked on X; you leaned on M1–M3; since then these cited files changed." The most-requested agent superpower, ingredients already soaking. | **KEEP** — needs a substantive-thread gate + budget cap; label clone-local |
| **`path-scoped recall`** | 3·M | Boost memories whose `cited_paths` intersect the files currently open — memory attaches to *code*, not wording. | **RESHAPE** → share one PostToolUse "active-file cache," enter as a single labelled RRF ranking; keep the aggressive per-tool auto-inject **out** |

### Theme B — The memory that knows its own gaps
Close the abstention loop; produce the KPI-2 signal. **3 lenses converged.**

| Idea | Lev·Eff | What | Verdict |
|---|---|---|---|
| **`recall blind-spot mining`** | 4·M | Cluster recurring `backend=none` abstentions → "you asked about deploy-rollback 6× this month; hippo had nothing — capture one." Silent abstention becomes a curation backlog. | **KEEP** — ship the `backend=none` arm; drop "just under floor" (not logged); keep low-frequency |
| **`injection-precision measurement (KPI-2)`** | 3·M | A PostToolUse read-signal: when an injected memory's cited file is then opened/edited, log it to a gitignored outcome ledger → surface as the **KPI-2 proxy** in doctor/eval. Fills a *named* roadmap gap. | **KEEP the measurement half**; **defer** the ranking-prior half (weak proxy + rides default-off salience) |
| **`abstention → eval-fixture generation`** | 3·M | *(blind-spot.)* Auto-draft **agent-gated** candidate RET-7 eval fixtures (query→expected) from real un-answered traffic, so each project's yardstick (**KPI-4**) self-populates from what users actually asked. | **KEEP-worthy** — complements RET-8 from `ROADMAP.v1` |

### Theme C — Reconcile the two authority planes
= §2 (rules ↔ memory). R1/R2/R4 are the buildable-now core.

### Theme D — The corpus governs itself
Contradiction resolution, steering, trust legibility.

| Idea | Lev·Eff | What | Verdict |
|---|---|---|---|
| **`contradiction inbox` + `/hippo:resolve`** | 4·M | Standing producer enumerating *all* unresolved `contradicts` edges + write-time conflicts (not just those that co-surface), + a HITL flow: supersede / scope-both / merge / not-a-conflict. | **KEEP** — empty-is-fine; value scales with corpus maturity |
| **`steer: pin` (then `mute`)** | 4·(S then M) | The missing **control axis**: "always keep this in mind here" / "surface this less" → a per-item, agent-gated frontmatter field honored as a bounded ranking prior. | **RESHAPE** → **ship PIN first** (bounded boost, safe, legible); **defer MUTE** and make it a *counted* down-weight (doctor shows "N muted"), never a silent suppress |
| **`consolidate proposals carry evidence`** | 3·M | Each proposed add/merge/supersede shows its rationale (triggering episode, `source_commit`, superseded neighbor + similarity) — memory changes reviewed like code. | **RESHAPE** → keep the **rationale payload** (cheap, capture already assembles it); the fancy Artifact UI is optional |
| **`floor-change governance`** | 3·S | Diff the always-loaded floor pointer-set vs a per-clone watermark → "the always-loaded floor changed since your last pull: +2/−1." | **KEEP** — drop the CODEOWNERS speculation; generalize to a **corpus-delta-since-last-pull** producer *(blind-spot: the higher-leverage general case)* |
| **`/hippo:why` recall receipt** | 3·M | Glass-box the last injection: per-pointer score/ranking/edge/tier breakdown, **and the abstention reason** when nothing was injected. | **RESHAPE** → don't replay the ledger (abstention scores aren't stored); **re-run recall live** off the existing `recall_view` surface |
| **`trust scorecard` (doctor rollup)** | 3·S | *(blind-spot.)* Roll the point-signals into one doctor section: N unverified / N contested-unresolved / N muted / N orphan-never-recalled / floor-changed-since-pull. | **KEEP-worthy** — the natural aggregation of this theme |

Reshaped-down here too: `inject-time trust badges` → ship only the *net-new*
`✓reinforced` glyph (contested/stale already render); `author confidence tier`
*(blind-spot)* — a write-time `draft|verified|authoritative` field, the one trust
dial the author controls.

### Theme E — Better retrieval mechanics (hot-path-safe)

| Idea | Lev·Eff | What | Verdict |
|---|---|---|---|
| **`query-intent routing`** | 3·S | Reweight dense-vs-lexical per query from the identifier-density signal `clean_query` **already computes** — stacktraces lean BM25, prose leans dense; balanced default when ambiguous. | **KEEP** — cheap, no new model; gate behind the eval golden-corpus |
| **`floor-dedup` + `within-session cooldown`** | 3·M | Stop spending the 9000-char budget re-injecting memories already in the floor/CLAUDE.md, or the *same* pointer every turn of a thread. *(cooldown is a blind-spot.)* | **KEEP** — collapse to a one-line "(already in floor)" note, don't silently drop |
| **`multi-turn query formation`** | 3·S | *(blind-spot.)* Terse follow-ups ("and the other one?") `clean_query`→"" and **abstain entirely**; form the query from the last few user turns (pure string blend, hot-path-safe). | **KEEP-worthy** |
| **`MMR intra-block diversity`** | 3·S | *(blind-spot.)* Re-cut the top-N for **distinct facets** so two paraphrases of one decision don't both eat a slot — cheap pairwise cosine over the resident dense matrix. | **KEEP-worthy** |
| **`cross-encoder rerank`** | 3·L | A local ONNX cross-encoder second stage over the fused top-N. | **RESHAPE** → **off the hot path**: ship only on `/hippo:recall` + the MCP tool (latency-tolerant, no p95 gate). At hippo's short-doc scale the hot-path win is small and the p95 risk large. |
| **`evidence-snippet form`** | 3·M | Inject the actual body chunk inline for a rank-1 hit so the model acts without a file round-trip. | **RESHAPE** → **body-hit-only**, rank-1, high-score-band, always with the staleness banner + `@sha` (else redundant with the description or serves stale text) |

### Theme F — Knowledge grows itself (capture / consolidation)

| Idea | Lev·Eff | What | Verdict |
|---|---|---|---|
| **`capture verbatim evidence + seed salience`** | 4·M | Enrich the SessionEnd seed with bounded **verbatim diff hunks** (so the drain drafts with real error text/commands — LIF-8's verbatim-beats-extraction on the capture side) + a per-seed value score so a deep queue drains best-first. | **KEEP** — run secret-lint before any hunk-bearing draft lands; keep salience a *label*, never a prune |
| **`co-recall (Hebbian) edges`** | 4·M | At consolidate, memories that co-surface across ≥N distinct sessions get a **per-item, agent-gated** associative edge — "fire together, wire together" — capturing operationally-inseparable-but-semantically-distant pairs embeddings miss; then GRA-1 carries them for free. | **KEEP** — high threshold so a sparse map stays empty; the only truly new *edge source* |
| **`capture decisions / the WHY`** | 3·M | *(blind-spot.)* The seed captures *what changed* (diff) but not the *why* (the session's tradeoffs/decisions) — exactly what makes a durable memory and can't be re-derived from git. Bounded capture of user-confirmed decisions at Stop. | **KEEP-worthy** |
| **`consolidate-time merge of near-dup committed memories`** | 3·M | *(blind-spot.)* LIF-2 dedups one *new* memory; audit *archives*; nothing **merges** two already-committed near-duplicates into one canonical note. The most common accretion toil, untouched. | **KEEP-worthy** |
| **`commit-watermark reverify`** | 2→·M | Catch staleness at commit precision by diffing `<episode-watermark>..HEAD` at SessionStart against `cited_paths` → a precise per-item re-verify worklist. | **RESHAPE** → **drop the git-hook** version (fragile new run surface, inv2 risk); the watermark-diff form gets the same precision with zero new surface |
| **`squash-merge provenance auto-rebaseline`** | 3·M | *(blind-spot — healing, not just detection.)* SHP-3 only *degrades* around a squash-merged `source_commit`; a per-item, agent-gated watermark rebaseline would **heal** it. | **KEEP-worthy** |
| **`standing contradiction scan`** | 3·S | Detect content contradictions (no code drift) among high-similarity pairs → feed `contradicts`/`supersedes`. | **RESHAPE** → don't build a module; **add a contradiction lens to audit's existing neighbor-pair pass** (S, not M) |
| **`temporal valid_until stamp`** | 2·(S of L) | On supersede, auto-close the loser's `valid_until` so demotion is auditable. | **RESHAPE** → **ship the auto-stamp** (cheap, legible); **defer** the `--as-of` retrieval mode (rare query, flirts with the no-DSL non-goal) |
| **`spaced re-verify`** | 2·S | Forgetting-curve spot-checks for memories that never trip drift (the FM1 hole). | **RESHAPE** → tiny bounded tail (1–3 items/session) **only when the pending queue is empty**; a safety net, not a scheduler |

### Theme G — Reach: cross-project & migration

| Idea | Lev·Eff | What | Verdict |
|---|---|---|---|
| **`/hippo:promote` → user tier** | 4·(S–M) | A per-item verb to lift a proven-portable lesson into the machine-local **user tier**, stamped "learned in `repo@sha`." Lands **TEA-1's own unmet acceptance criterion** (passive fusion alone doesn't satisfy "recallable in B *with provenance*"). | **KEEP** — sharpest, safest, most on-mission idea in the whole exploration |
| **`/hippo:import` from rival tools** | 4·(M) | Source adapters (start with **Cursor `.mdc` — `globs:`→`cited_paths` is a near-perfect match**) that ingest existing rules/notes into ranked, staleness-tracked, deduped memories, per-item + secret-lint. | **KEEP** — highest **adoption** leverage; makes hippo the tool you *graduate to* (attacks the me-too positioning blocker). Ship **one** solid adapter, not four half-built ones |
| **`decision-chain replay` (memory-as-active-tool)** | 3·M | *(blind-spot.)* An MCP/skill that walks the authored `supersedes`/`refines`/`contradicts` graph to reconstruct *how a decision evolved* — a net-new **use** of edges the corpus already stores. | **KEEP-worthy** |
| **`/hippo:recall --all-projects`** | 3·L | Explicit, trust-gated recall across every corpus you've init'd on this machine — "how did I ever fix that ONNX mismatch." | **RESHAPE** → explicit command/MCP tool only (never the hook), **trust-gate each source at query time**, provenance-label every hit |
| **pack ecosystem** (`install`/`extract`/`update`) | 3·L | Install a curated domain pack from a git URL; extract your own; three-way-merge upstream updates. | **RESHAPE / post-v1** — ship the *mechanism* (source resolver on the **full v0.8.0 trust spine** — a foreign pack **is** the public-corpus injection threat); the *ecosystem* is downstream of adoption. The reusable nugget now: the **portability linter** (shared with `/hippo:promote`) |

---

## 4. Defer / kill (with reasons) — the discipline that protects users

The vetters were consistent: **kill the autonomous ranking/write half, keep the
legible half.**

- **KILL — `floor-as-git-rule`**: derived-yet-committed floor duplicate (inv1) +
  clean-break (inv5), staked on an unverified harness feature; trades a
  doctor-monitored risk for an unmonitored one. *(§2.)*
- **KILL — auto-demotion in `rules-to-memory diet`**: guardrail rules ("never
  commit secrets") derive value from being *always* present, and RET-1
  deliberately abstains — a demoted guardrail silently misses on exactly the
  ambiguous prompts where it's needed. Keep only a **read-only "context-diet
  visibility" report** ("of your N always-loaded rules, only these 3 touch
  today's files").
- **KILL — auto-draft in `demand-gap synthesis`** and **auto-reword in
  `self-healing descriptions`**: generating memory content to answer a question
  nobody grounded is a **fabrication vector** against the capture-from-evidence
  spine. Keep the **detection** ("12 sessions asked, mean 0.3 — consider
  capturing"); let a human write it.
- **DEFER — every read-side ranking prior** (`negative-signal demotion`,
  `team-hot salience`, the injection-precision prior, the `mute` half of
  steering): all fold into the **default-off salience machine** and all need
  signal density (a reliable "acted-on" outcome; a non-trivial team union;
  dense explicit feedback) that hippo doesn't have yet. **`RET-10` (decide
  salience default-on, from `ROADMAP.v1`) is the keystone** that must land first
  — with RET-8 evidence — before any of these are more than an env flag.
- **KILL — `co-recall as a hot-path ranking`, `inert-recall finder`,
  `packs.lock`, `hippopack bundle`**: redundant with existing machinery (GRA-1 /
  audit joins / git itself) or built on usage volume solo users lack.

---

## 5. Cross-cutting dependencies & decisions these surface

- **Salience graduation (`RET-10`) is a keystone.** A whole class of ranking
  ideas is inert until salience is decided default-on (needs RET-8's per-category
  eval as evidence). Sequence RET-8 → RET-10 → the ranking priors.
- **Verify the `.claude/rules` `paths:` auto-load feature** before building R2/R5
  on it (web-derived claim; confirm against the shipped harness).
- **Corpus-format bumps** (clean breaks per inv5, one migration each) are needed
  for: the `steer` field, `valid_from/until`, and any distinct `associates` edge
  type — sequence them behind COR-7's versioning + `ROADMAP.v1`'s UPGRADING story.
- **The design law:** ship the **legible detection / HITL** half; gate any
  autonomous ranking or write on proven signal density; every silent path stays
  loud at doctor/SessionStart (inv3). This is what keeps "self-managing" from
  drifting into "silently wrong."
- **Guardrails restated:** markdown-in-git stays the only authority (inv1);
  hot path stays pure retrieval (inv6); writes are per-item agent-gated diffs
  (inv4); no second silent always-load channel; local/offline only (non-goal).

---

## 6. Top 7 highest-leverage moves (ranked)

Chosen for: end-user efficacy × on-identity fit × reuses shipped machinery ×
(bonus) validated by cross-lens convergence.

1. **Ship the first "relevant to your current work" producer** (`diff-seeded
   relevance` + `where-was-i resume card`). The biggest structural gap, invented
   independently by three lenses, and the ingredients (episode buffer, git-diff
   context, `recall.recall`) already exist. This is the clearest *new superpower*
   with the cleanest attach.
2. **Turn silent abstention into a growth signal** (`recall blind-spot mining` +
   the **KPI-2** injection-precision *measurement* half). Closes a loop the
   roadmap explicitly names as unfilled, from a stream hippo already logs.
3. **Build the rules-plane hygiene core** (R1 conflict radar + R2 staleness-
   over-rules + R4 write-time dedup). The user's avenue, all detection/hygiene,
   all reusing the shipped governance scan — hippo becomes the ranking + staleness
   layer the always-loaded rules plane structurally lacks.
4. **`/hippo:promote` + one `/hippo:import` adapter (Cursor `.mdc`).** Promote
   lands TEA-1's own unmet criterion; import is the highest *adoption* lever and
   directly answers the me-too positioning blocker by making hippo the tool you
   *graduate to* — `globs:`→`cited_paths` is a near-perfect structural match.
5. **The contradiction inbox + `/hippo:resolve`.** A self-contradicting corpus
   is the sharpest trust failure; make latent `contradicts` edges a drainable
   HITL queue instead of an annotation that only fires on coincidental
   co-surfacing.
6. **Capture verbatim evidence + `co-recall edges`.** Higher-fidelity drafts
   (real error text, not paraphrase) and the only genuinely new *edge source* —
   both make the corpus grow better with less toil, off the hot path.
7. **`steer: pin` (the control axis) + the cheap retrieval wins**
   (`query-intent routing`, `floor-dedup`/cooldown, `multi-turn query
   formation`, MMR). Individually small, collectively they make every injected
   token count and give users their first real steering wheel — all hot-path-safe
   and reusing signals already computed.

---

*Prepared as an ideation catalog, adversarially vetted. No code, `ROADMAP.yaml`,
or `ROADMAP.v1.md` changes were made. Items here are candidates for post-v1
(v1.1+) planning; a few sharpen already-named roadmap items and are noted inline.*
