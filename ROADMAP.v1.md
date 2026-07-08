# hippo — Road to v1.0.0 (OSS launch)

**Status: DRAFT PROPOSAL for owner review — does not modify `ROADMAP.yaml`.**
Authored 2026-07-08. Companion to [`ROADMAP.yaml`](ROADMAP.yaml); mirrors its
structure (`workstreams` / `release_train` / `decisions` / `non_goals` / `kpis`)
and respects its `guiding_invariants`. Where a proposal would touch an existing
decision or invariant, it is flagged, never silently overridden.

Method: same as the original roadmap — a multi-agent read-only reconnaissance
(six subsystem mappers + a 2026 landscape survey), every gate-clearance and
"already shipped" claim adversarially verified against the actual source
(`file:line` cited throughout), not against the CHANGELOG's self-description.

---

## 0. TL;DR

**The engine is done. The launch is not.**

Every planned item in `ROADMAP.yaml`'s `release_train` from v0.2.0 → v0.7.0 has
shipped, tagged, on `main` (verified against `CHANGELOG.md` and `git log`). All
14 workstreams (ONB, COR, SHP, OSP, RET, GRA, LIF, CAP, INT, TEA, SEC, PRF, QUA,
DOC) landed their scoped items. What is left in `ROADMAP.yaml` is only the
`exploratory` gate-blocked set — and on inspection **only 2 of those 7 items earn
a place in v1** (RET-8, GRA-8); the other 5 are cut or deferred with reasons
(§3).

So the road to v1 is **not "more engine."** Adding retrieval/graph/lifecycle
depth now adds risk without moving the launch. The gap between "works" and
"launchable in the open" is a different, unscoped body of work:

1. **The trust boundary was built for private/team corpora; v1 makes corpora
   public.** hippo's headline security feature — the SEC-1 trust gate — reviews
   memory *names* but injects memory *descriptions* (verified: `trust.py:166-178`
   samples `splitext(basename)`; `recall.py:1716` injects `r["description"]`).
   For the exact public-corpus prompt-injection threat a launch introduces, the
   review is blind to the payload. This is the single sharpest blocker.
2. **The positioning is now a me-too.** By mid-2026 the "markdown corpus +
   offline hybrid BM25+dense + git-diffable + staleness" wedge is occupied
   feature-for-feature by shipping tools (memweave, EverOS/EverMind, memsearch,
   sqlite-memory) and the category incumbent **claude-mem (~86K stars)**, while
   **Anthropic itself now ships a GA file-memory tool** (`memory_20250818`) and
   Claude Code "Auto Memory." hippo's *surviving* differentiators are narrower
   and sharper: **git IS the store**, **git-drift *semantic* staleness** (did the
   cited code move — not calendar age), a **zero-LLM / zero-token / zero-network
   hot path**, and **review-gated team memory**. v1 must re-cut its identity
   around these or be dismissed as derivative regardless of its real depth.
3. **The OSS on-ramp does not exist.** No `CONTRIBUTING.md`, `SECURITY.md`,
   `CODE_OF_CONDUCT.md`, issue/PR templates; author is a bare "Fred" with no
   contact; the marketplace-visible `plugin.json` description still reads
   *"Extracted from the ic-memobot/Memosa agent-memory tooling"*; there is no
   demo, no comparison table, no benchmark number, and **no statement of what
   "1.0" commits to.**
4. **The first-run payoff is invisible.** KPI-1 is "time to first *successful*
   recall," yet the quickstart never has the stranger *observe* a recall — it
   ends at "just work … injected automatically" (`README.md:35-39`). For a tool
   whose whole value is silent injection, that reads as a no-op.

**Proposed arc (4 focused releases, mirroring the project's demonstrated
~10-15-item cadence):**

| Release | Theme | One-line gate |
|---|---|---|
| **v0.8.0** | **Safe in the open** | Cloning any public hippo corpus is safe *by review of what actually injects*; deps/model are inventoried, pinned, disclosable. |
| **v0.9.0** | **Proven for strangers** | Recall precision is measurable per-category on *any* corpus and holds on the cold BM25-only path; the capture loop is bounded and integration-tested. |
| **v0.10.0** | **Legible to strangers** | A newcomer reaches an *observable* first recall in <5 min with zero doc-reading; the README teaches the mental model, not acronyms. |
| **v1.0.0** | **Launch** | Identity re-cut around what survives 2026; comparison table + reproducible number + git-drift hero demo; community on-ramp; a written stability commitment. |

The single highest-leverage insight: **v1.0.0 should be a positioning +
commitment release, not a feature release.** Ship the engine you have; make it
safe, provable, legible, and differentiated; then freeze a surface and promise
it. The top 3-5 moves are in §9.

---

## 1. Ground truth — what actually shipped (verified, not remembered)

- **v0.2.0–v0.7.0: every scheduled `release_train` item shipped.** Confirmed
  against `CHANGELOG.md` (v0.4.0–v0.7.0 fully itemized; v0.2.0/v0.3.0 referenced
  to PRs #3/#4) and `git log`. Corpus format is at **2**, index schema at **3**
  (`CHANGELOG.md:122-132`).
- **The engine is substantial and well-tested.** `recall.py` is 1963 lines of
  four-ranking RRF fusion with RET-1 floor/knee/hard-skip, RET-2 body chunks,
  RET-5 salience, GRA-1 1-hop expansion, GRA-4 typed-edge demotion, all live and
  covered (~40 tests in `test_recall.py`). 45 test files; CI runs a real
  `{ubuntu,macos}×{py3.10,py3.12}` matrix + dense lane + shellcheck + nightly
  scale lane; release engineering (two-manifest version lockstep, four-way
  tag-time check, `RELEASING.md`) is mature.
- **Reconnaissance turned up real debt inside the "shipped" set** (§4) and
  **roadmap drift**: LIF-8's evidence-block convention already shipped verbatim
  under DOC-6 (`CONVENTIONS.md`; `CHANGELOG.md:181-183`) yet is still listed
  P3-unshipped-exploratory at `ROADMAP.yaml:1216`.

Takeaway: this is a mature codebase whose *plan* is stale relative to its own
completeness, and whose *launch surface* was never in scope.

---

## 2. The v1 thesis

`ROADMAP.yaml` was an **internal engineering plan**: front-load truthfulness,
then breadth, depth, autonomy, fleet scale. It executed. It was never a **launch
plan**, so it has no workstream for security-under-public-exposure, positioning,
community, or demo — the four things that decide whether a *good* tool *lands*.

v1 adds exactly two new workstreams and finishes the debt:

- **POS — Positioning & launch** (new): differentiation, comparison, benchmark,
  demo, native-memory framing.
- **COM — Community & contribution** (new): the on-ramp + the stability
  commitment that makes "1.0" mean something.
- Plus targeted **SEC / RET / CAP / ONB / DOC / QUA / INT** items that close
  verified gaps and reconcile the plan.

Guardrail: **no new engine capability enters v1 unless it fixes a broken promise
or is required for the launch.** New associative-retrieval depth (PPR), taxonomy
schema changes, autonomous-write features, and a warm daemon are explicitly
post-v1 (§3). This is the "bold" call: refuse feature risk before 1.0.

---

## 3. Verdict on the exploratory / semi-scoped set (adversarial)

Each gate was re-checked against real code. Summary first, evidence below.

| Item | Gate | Gate status (verified) | v1 verdict |
|---|---|---|---|
| **RET-8** category-tagged eval | RET-1 + GRA-1 landed | **CLEARED** — both live (`recall.py:594-595,840-917`) | **KEEP → v0.9.0** |
| **GRA-8** graph observability | none (opportunistic) | n/a — genuinely unshipped | **KEEP → v0.9.0** |
| **LIF-8** verbatim-evidence convention | RET-2 landed | **CLEARED — but deliverable already shipped** under DOC-6 | **RECONCILE** (mark done; residual = low-pri polish) |
| **GRA-7** personalized PageRank | beats GRA-1 on RET-8 multi-hop | **NOT CLEARED** — un-evaluatable until RET-8 exists | **DEFER post-v1** |
| **LIF-7** typed-memory taxonomy | CAP-2 proven across sessions | **NOT CLEARED** — CAP-2 is 1 day old, zero field soak | **DEFER post-v1** |
| **CAP-5** auto-MOC + rolling summary | CAP-4 proven | **NOT CLEARED** — CAP-4 1 day old; + invariant tension | **DEFER post-v1** |
| **INT-6** warm recall daemon | INT-5 p95 shows it's worth it | **NOT CLEARED** — cold budget already met without it | **CUT for v1** |

### KEEP

- **RET-8 — category-tagged eval suite (multi-hop / temporal / update /
  abstention).** Gate cleared: RET-1 (`_dense_floor` `recall.py:212-226`, knee
  `:1639-1645`, hard-skip `:1516-1517`) and GRA-1 (`_expand_neighbors`
  `recall.py:840-917`, called `:1581`) are both live. The abstention leg is even
  half-built (`abstention_rate` `eval_recall.py:242` + `recall_abstention_set.yaml`).
  What's greenfield is small: the fixture loader reads only `{query, expected}`
  (`eval_recall.py:442-459`) with **no category field** and one aggregate metric.
  **Why keep:** RET-8 is the *measurement keystone*. It operationalizes KPI-4
  (per-category recall@10/MRR) and the numerator of KPI-2, it is the only way to
  license RET-5 salience default-on (§4) or promote RET-1's precision@k to a
  tracked gate, and it is the substrate for a public benchmark number (POS-3). A
  v1 that *claims* multi-hop/temporal/abstention capability cannot leave it
  unmeasured. Effort **M**.

- **GRA-8 — graph observability (`--components` / `--degree` / `--export
  dot|mermaid|json`).** Confirmed absent (`links.py` main exposes only
  `--traverse/--hops`) though the primitives exist (`isolates()`/`orphans()`
  `:383-402`, `inbound()` `:333`, `load_edges` `:720`). Gate-free by design,
  ~100 lines, no deps, read-only. **Why keep:** the cheapest high-visibility win
  in the whole plan — a screenshot-able "inspect your memory graph" moment for a
  tool that markets "graph-backed," and it fills the audit scorecard's
  component-count (KPI-7). Effort **S**.

### RECONCILE (not new work)

- **LIF-8 — verbatim-evidence convention.** Its actual deliverable already
  shipped: `CONVENTIONS.md` (init-seeded) carries the full evidence-block
  convention + a reconsolidation rule against deleting evidence blocks;
  `CHANGELOG.md:181-183` lists it under DOC-6. **Action:** mark LIF-8 shipped in
  `ROADMAP.yaml`; the only residual scope is *optional* fenced-block
  code-enforcement in `reconsolidate` — reclassify as a **low-priority polish
  nice-to-have**, not a v1 need.

### DEFER post-v1 (their gates cannot honestly clear at launch)

- **GRA-7 — personalized PageRank.** Gate un-evaluatable (needs RET-8's multi-hop
  category, which doesn't exist yet); no PPR code anywhere. Adversarially: PPR
  earns its keep on large, dense corpora (HippoRAG's thousands-of-notes
  benchmarks); hippo targets tens-to-hundreds of memories starting at near-zero
  edge density, where GRA-1's discounted 1-hop union already captures the
  associative neighborhood — and per-query matrix iteration spends against the
  tight cold-p95 budget. Revisit *only if* RET-8 shows GRA-1 leaving material
  multi-hop recall on the table. The adjacency substrate (`load_edges`) is
  present, so it stays cheap to build later.

- **LIF-7 — typed-memory taxonomy with per-type lifecycle.** Effort **L**, and a
  *breaking frontmatter-schema change* — a migration liability for the earliest
  OSS adopters. Its gate ("CAP-2 proven across temporally-diverse sessions") is
  provably unmet: CAP-2 shipped **2026-07-08** (`CHANGELOG.md:64`), the same day
  as this review, on a plugin at ~zero adopters. The gate exists precisely to
  stop this. Defer until real corpora exercise capture across weeks.

- **CAP-5 — auto-maintained MOC + rolling session-summary note.** Same "proven"
  problem (CAP-4 is 1 day old) *and* an invariant tension: a note regenerated on
  every `/hippo:consolidate` run is an **autonomous corpus write** that sits
  against the guiding invariant "destructive/corrective writes are per-item and
  agent-gated; no bulk autonomous sweeps" (`ROADMAP.yaml:56`). Human-legibility
  is v1.x polish. (Note: the shipped `consolidate/SKILL.md:97` already
  forward-references CAP-5 — trim or keep as a roadmap breadcrumb.)

### CUT for v1

- **INT-6 — warm recall daemon.** The very evidence its gate hinges on argues
  *against* it: the cold-path p50 is **already gated at 1500ms**
  (`GATE_COLD_P50_MS=1500`, `eval_recall.py:80`; enforced on the dense CI lane
  via `--gate-cold`) and doctor enforces the same p95 budget
  (`doctor.py:836-878`). Green CI ⇒ **KPI-3's <1500ms is already met without a
  daemon.** The daemon only chases the aspirational <300ms half of KPI-3 (a
  nice-to-have), while INT-2's MCP server is *already* a long-lived process with
  an in-process model cache (`build_index.py:718`) serving warm recall for the
  mid-turn/subagent cases where latency compounds. A unix-socket daemon
  (SessionStart-launched, idle-suicide, permanent-fallback, cross-process
  lifecycle) adds a whole failure surface directly against the "local, offline,
  simple hooks" identity — textbook post-v1 scope. **Record the cut as a
  decision** (§7, OQ-9).

---

## 4. Debt & regressions inside the shipped set (fold into v1)

Reconnaissance found real gaps behind green tests. These become v1 items:

- **SEC-1 under-delivers its own acceptance criterion (BLOCKER).** "Trust prompt
  shows what would be injected before consent" (`ROADMAP.yaml:1610`) is only
  half-met: consent shows **names** (`trust.py:166-178`), injection uses
  **descriptions** (`recall.py:1716`, ≤220 chars). → **SEC-5**.
- **Trust is TOFU with no re-review on change.** `mark_trusted` stores only path
  + timestamp (`trust.py:107-142`); a trusted public upstream can ship injected
  memories in any later commit with zero re-consent. → **SEC-6**.
- **No inject-time provenance/defensive demarcation.** Foreign-corpus
  descriptions inject verbatim, indistinguishable from trusted context. → **SEC-7**.
- **Trust gate is inapplicable for non-git corpora** — a "Download ZIP" of a
  public repo extracts a non-git dir and auto-injects (`trust.py:145-163`). → **SEC-12**.
- **MCP `new_memory` write path has no trust gate** while MCP `recall` does
  (`new_memory.py` has no `is_trusted`; cf. `recall.py:1423`): a subagent in an
  untrusted-but-writable clone can *write* memories it cannot *read*. `serve()`
  also reads unbounded stdin lines (`mcp_server.py:271`). → **SEC-13**.
- **TEA-5 commits per-user usage (names + recall counts) to git by design**
  (`telemetry.py:451-568`) — a privacy footgun on a public repo, no opt-out/warn.
  → **SEC-14**.
- **Secret-lint pattern set is narrow** (AWS/GitHub/PEM + conservative entropy,
  `secrets.py:51-65`) yet is the only defense and runs in **no CI job**. → **SEC-8**.
- **No dependency/model license inventory** though bootstrap pulls Apache-2.0
  (fastembed, rank-bm25), BSD (numpy), MIT (PyYAML) + downloads bge-small-en-v1.5
  (MIT). README's "no third-party code ported" is true of *source* but silent on
  the runtime tree. → **SEC-9**.
- **RET-1's abstention floor is dense-only** (inert when `index.dense` absent,
  `recall.py:580`) — so on the **BM25-only cold-start path CI itself treats as
  first-class** (`ci.yml:107`), the most-marketed retrieval property silently
  weakens exactly when a new user first tries it. → **RET-11**.
- **The dense floor 0.60 is calibrated to the maintainer's golden corpus**
  (`recall.py:176-183`); nothing recalibrates per-install despite RET-7
  generating per-project fixtures. → **RET-9**.
- **RET-5 salience shipped default-off with no follow-up decision** — a fully
  built, tested ranking feature dark for every user (`recall.py:925-931`;
  ledgers LIF-4/LIF-6 do work nobody benefits from). → **RET-10**.
- **RET-1's precision@k was never promoted to a tracked gate** (report-only,
  never in the gates dict, `eval_recall.py`) — injection precision can regress
  without reddening CI. → folded into **RET-8**.
- **PRF-3 scale lane is BM25-only** (`ci.yml:107`); RET-2's widened dense+body
  matrix (~2000 rows at 500 memories, vstacked across TEA tiers) — the path that
  dominates cold cost — has no scale tripwire. And **CI gates cold *p50* while
  KPI-3/doctor speak *p95*** (a strictly weaker statistic). → **PRF-4 / PRF-5**.
- **The capture pending-queue nags every SessionStart forever, unbounded**
  (`session_start.py:367-393`) — no snooze, no cap — directly violating the LIF
  workstream goal "nothing nags forever" that LIF-1 established for
  reconsolidation but capture never inherited. → **CAP-6**.
- **CI never proves resolution/bootstrap on py3.11/3.13/3.14** though numpy was
  widened to `<3` for exactly 3.13 (OSP-3 half-shipped); the docs link-check
  skips external URLs. → **QUA-11**.
- **Stranger-facing origin-repo jargon persists** in `README.md:7-8,149` and the
  **marketplace-visible** `plugin.json` description. → **DOC-14**.

---

## 5. Proposed new/updated items (in the existing id + scoring scheme)

Priority `P0` (broken promise / launch blocker) · `P1` (core to launch) · `P2`
(quality/leverage) · `P3` (deferred). Effort `S`/`M`/`L`.

### SEC — Security, privacy & trust *(reframed for PUBLIC corpora)*
- **SEC-5** `P0/S` — Consent surfaces the **descriptions** that actually inject
  (not just filenames), with framing that these strings enter every prompt.
  Closes SEC-1's own acceptance criterion. *Revisits OQ-6.*
- **SEC-6** `P1/M` — Trust record stores a corpus **content fingerprint**;
  re-prompt on material change (defeats trusted-upstream supply-chain injection).
  *Revisits OQ-6 → OQ-7.*
- **SEC-7** `P1/M` — **Inject-time provenance banner** + defensive demarcation
  for foreign/cloned-corpus lines in `format_results`. *(KPI-5.)*
- **SEC-8** `P1/S` — Broaden secret-lint prefixes (Slack/Google/Stripe/OpenAI/
  Anthropic/JWT/npm/PyPI/connection-strings, staying high-precision) + a **CI
  secret-scan gate** over shipped packs + repo.
- **SEC-9** `P1/S` — `THIRD_PARTY_NOTICES` / `NOTICE`: dependency + **model**
  license inventory (Apache/BSD/MIT + bge-small model card).
- **SEC-10** `P1/S` — `SECURITY.md`: private disclosure channel, supported
  versions, pointer to SEC-2 lint / SEC-4 purge. *(also a COM launch-standard.)*
- **SEC-11** `P2/M` — Supply chain: pin/lock deps (or hash-locked requirements) +
  document/optionally verify the ~130MB model artifact. Bootstrap is the one
  online step; it currently fetches range-pinned wheels + an unverified binary.
- **SEC-12** `P2/S` — Close the **non-git (zip/tarball) trust bypass** — treat an
  unresolvable-git corpus containing `.claude/memory/` as untrusted-by-default
  (env/`init` override), not "gate inapplicable."
- **SEC-13** `P2/S` — MCP `new_memory` honors the trust gate (kill the
  write-without-read asymmetry) + a `serve()` max-message cap.
- **SEC-14** `P2/S` — TEA-5 committed usage summary behind **explicit opt-in** +
  a public-remote warning (doctor/doc). *Tension: TEA-5 deliberately excepts the
  gitignore invariant — narrow it for public remotes.*

### RET / PRF / GRA — Retrieval precision, measurement & graph
- **RET-8** `P1/M` — Category-tagged eval (multi-hop/temporal/update/abstention);
  **promote precision@k + abstention_rate to tracked gates**. *(deps: RET-1,
  GRA-1 — both landed.)*
- **RET-9** `P1/M` — Per-corpus dense-floor **sanity check** (doctor/audit runs
  the abstention fixture against the live corpus, warns on distribution overlap);
  stretch: auto-derive from the RET-7 set.
- **RET-11** `P1/M` — BM25-only **abstention floor** (normalized-score / IDF-mass
  threshold) *or* an explicit doctor/README statement that abstention is
  dense-gated + a warm-the-model nudge. *(KPI-1.)*
- **RET-10** `P2/S` — **Decide RET-5 salience default-on** using RET-8 evidence
  (run eval both ways; flip if no recall@10 regression; retire flag-only debt).
  *New decision, OQ-10.*
- **PRF-4** `P1/S` — Dense-enabled latency sample on the 500-memory scale lane
  (the production path PRF-3 skips). *(KPI-3.)*
- **PRF-5** `P2/S` — Align the CI cold gate to **p95** (the KPI-3/doctor
  statistic), not p50.
- **GRA-8** `P2/S` — Graph observability CLI (`--components/--degree/--export`);
  feed component count to the audit scorecard. *(exploratory, gate-free.)*

### CAP / INT — Capture integrity & integration surfaces
- **CAP-6** `P1/M` — Capture pending-queue **snooze/dismiss + seed bound/prune**
  (parity with LIF-1's `_snoozed_names`). Closes the LIF-goal violation.
- **CAP-7** `P2/M` — One **end-to-end integration test**: SessionEnd capture →
  SessionStart nudge → `/hippo:consolidate` drain (`--check` routing) → approved
  candidate lands in `.claude/memory/`.
- **CAP-8** `P2/S` — Surface the capture→approval loop in README quickstart + a
  worked `/hippo:consolidate` example (hippo's strongest differentiator vs native
  memory is currently discoverable only via one SessionStart nudge line).
- **CAP-9** `P3/S` — PreCompact/SessionEnd cross-surface dedup note in the skill.
- **INT-8** `P2/M` — MCP **discoverability doc** (mid-turn/subagent recall) +
  **launch-health doctor check** (`bin/hippo mcp` actually starts) + bounds.
  *(KPI-5.)*

### ONB / DOC — First-run & documentation for strangers
- **DOC-14** `P0/S` — **Scrub origin-repo / "private repo" jargon** from README
  and the marketplace-visible `plugin.json` description. (Cheap, visible, a
  launch-credibility regression.)
- **ONB-8** `P1/S` — **Observable first recall** in the quickstart (final step
  runs `/hippo:recall` / "what do you remember about X" so the stranger *sees*
  the memory return). This *is* KPI-1's metric; it is never exercised today.
- **ONB-9** `P1/S` — Post-init **"try it now" next-command nudge** — complete the
  KPI-8 funnel (narration currently stops one step short of the payoff).
- **DOC-9** `P1/M` — **"How hippo thinks" concepts page** (what a memory is; floor
  vs on-demand recall; the four types; why markdown-in-git) linked from the top
  of the README. Stop leading with acronym-dense feature copy; stop routing
  newcomers to the 575-line engine reference as "the full docs."
- **DOC-11** `P1/S` — **Troubleshooting / FAQ** ("recall is empty →
  bootstrapped? corpus trusted? `user_role` still FILL-ME? run `/hippo:doctor`").
- **ONB-10** `P2/M` — Reduce the `user_role.md` **FILL-ME friction** (ship a
  minimal 2-line default, and/or an optional interactive init fill via
  `AskUserQuestion` — user-supplied content, never an autonomous write).
- **DOC-12** `P2/S` — Per-skill **user command reference** (one paragraph +
  when-to-use per verb; recall-vs-doctor, doctor-vs-audit, consolidate-vs-audit).

### POS — Positioning & launch *(NEW workstream)*
- **POS-1** `P0/S` — **Re-cut the differentiation one-liner** around git-native +
  git-drift semantic staleness + zero-LLM/zero-token/zero-network hot path +
  review-gated team memory. Retire the crowded "markdown + hybrid recall" lead.
- **POS-2** `P1/S` — **Competitive comparison table** in the README (claude-mem,
  EverOS/EverMind, memweave, memsearch, Anthropic native memory) across the axes
  hippo wins. Control the framing before HN supplies its own.
- **POS-3** `P1/M` — Publish **one reproducible number** hippo wins (recall@10 /
  MRR@10 + cold p95 + **$0 per-prompt token cost** on a public dev corpus,
  one-command repro), and a principled statement of *why not* LongMemEval/
  LoCoMo/BEAM (they measure autonomous chat-history extraction; hippo gates
  extraction behind human approval). *(builds on RET-8 / QUA-6 / PRF-4.)*
- **POS-4** `P1/S` — **git-drift staleness hero demo**: a memory cites a
  function → someone moves/edits it → hippo flags it stale-at-use. No
  calendar-decay competitor can reproduce this.
- **POS-5** `P2/S` — State the zero-LLM hot path as an explicit **cost + privacy
  claim** ("$0 and zero bytes leave your machine per prompt").
- **POS-6** `P2/S` — **Refresh the roadmap landscape scan** (`ROADMAP.yaml`
  references) against *shipping products*, not just research frameworks; record
  which choices they pressure (they pressure the markdown+hybrid identity; they
  do **not** pressure git-native, git-drift, or the zero-LLM hot path).
- **POS-7** `P2/S` — Update the native-memory section for Anthropic's 2026 GA
  `memory_20250818` tool + Auto Memory; frame hippo as the **ranking + hygiene +
  review layer** on top, pre-empting "why not just use Anthropic's memory?"

### COM — Community & contribution *(NEW workstream)*
- **COM-1** `P0/S` — `CONTRIBUTING.md`: **real dev-venv recipe** (`python -m venv
  … && pip install -r plugin/requirements.txt pytest pytest-timeout hypothesis`),
  marker semantics (network/slow/scale), the six required CI checks, and the
  one-commit-per-item / id-prefixed convention. (The only dev-test hint today
  assumes an undocumented `.venv`.)
- **COM-2** `P1/S` — `CODE_OF_CONDUCT.md`, `.github/ISSUE_TEMPLATE/`
  (bug form capturing platform/corpus-size/backend), `PULL_REQUEST_TEMPLATE.md`,
  `CODEOWNERS`.
- **COM-5** `P0/M` — **v1.0.0 stability / semver commitment.** Enumerate & freeze
  the compatibility surface: `HIPPO_*` env names, the `/hippo:*` namespace,
  `corpus_format` + index `schema_version`, `bin/hippo` CLI, MCP tool names
  (recall/new_memory/traverse); state the support policy (marketplace =
  latest-only); reconcile with the clean-break invariant. *Load-bearing —
  new decision OQ-8.*
- **COM-6** `P1/M` — Top-level `UPGRADING.md` + a **worked non-trivial
  migration** (format 2→3 template: doctor-detect → per-item agent-gated edits →
  `write_corpus_format` stamp). Today only the trivial additive case is written,
  buried in the engine reference.
- **COM-7** `P1/S` — Marketplace/listing polish: **real maintainer identity**
  (email/url) in both manifests; mirror keywords + author into `marketplace.json`;
  README CI/version badges.
- **QUA-11** `P1/S` — CI **resolution/bootstrap lane on py3.11/3.13/3.14**
  (proves OSP-3's numpy `<3` widening) + extend the docs link-check to external
  URLs.
- **QUA-12** `P2/S` — `release.yml` **publishes a GitHub Release** with extracted
  CHANGELOG notes; **codify branch protection**; pin actions by SHA + dependabot.
- **DOC-10** `P1/S` — **Demo GIF / asciinema** for the README landing page
  (install → bootstrap → init → remember → recall-resurfaces / git-drift flag).
  *(depends POS-4.)*

---

## 6. The release train

Each release keeps the project's format: theme, `done_means`, and a
dependency-satisfied item list. Efforts sum to roughly the project's per-release
norm (~10-15 items).

### v0.8.0 — "Safe in the open"
> **done_means:** Cloning hippo's own public repo — or any public repo carrying a
> hippo corpus — is safe *by review of what actually injects*. Consent shows the
> descriptions that will enter context; a trusted upstream can't silently change
> under you; a zip-download isn't a bypass; the dependency + model supply chain is
> inventoried and pinned; and there is a private way to report a vulnerability.

**Items:** SEC-5 `P0`, SEC-6 `P1`, SEC-7 `P1`, SEC-8 `P1`, SEC-9 `P1`, SEC-10
`P1`, SEC-11 `P2`, SEC-12 `P2`, SEC-13 `P2`, SEC-14 `P2`.
**Decision:** OQ-7 (amend OQ-6 trust posture: consent reviews injected content;
re-consent on material corpus change).
**re-bootstrap:** likely **yes** (SEC-11 dep pin/lock changes `requirements.txt`).

*Rationale for going first:* the threat model shift is what "v1 = public" means;
every later release (docs, demo, launch) assumes the artifact is safe to hand a
stranger. SEC-5 is the sharpest single blocker and is effort-S.

### v0.9.0 — "Proven for strangers"
> **done_means:** Recall quality is measurable per-category on *any* corpus
> (not just the maintainer's golden set); abstention holds on stranger corpora
> and on the BM25-only cold-start path; the 500-memory envelope covers the dense
> hot path; salience is decided on evidence, not left dark; and the marquee
> capture loop is bounded, discoverable, and integration-tested.

**Items:** RET-8 `P1`, RET-9 `P1`, RET-11 `P1`, PRF-4 `P1`, CAP-6 `P1`, QUA-11
`P1`, RET-10 `P2`, PRF-5 `P2`, GRA-8 `P2`, CAP-7 `P2`, INT-8 `P2`, CAP-9 `P3`.
**Decision:** OQ-10 (RET-5 salience default-on iff RET-8 shows no regression).
**re-bootstrap:** **no** (no `requirements.txt` change expected).

*Note the sequencing truth:* LIF-7 and CAP-5 gate on capture/consolidation being
"proven across temporally-diverse sessions," but both shipped on review day with
zero field soak — they **cannot** clear before v1 by construction. v1 ships,
accrues real capture usage, and these become v1.x candidates. CAP-6/CAP-7 here
make that field usage *trustworthy* in the meantime.

### v0.10.0 — "Legible to strangers"
> **done_means:** A newcomer reaches an *observable* first recall in under five
> minutes with zero doc-reading; the README's first screen teaches the mental
> model (memory / floor-vs-recall / four types / why markdown-in-git) instead of
> acronyms; there's a troubleshooting path and a per-verb reference; the one
> unavoidable manual step (`user_role`) no longer gates a good first session; and
> no shipped surface still advertises a "private origin repo."

**Items:** DOC-14 `P0`, ONB-8 `P1`, ONB-9 `P1`, DOC-9 `P1`, DOC-11 `P1`, ONB-10
`P2`, DOC-12 `P2`, CAP-8 `P2`.
**re-bootstrap:** **no**.

*Could merge with v0.9.0* if the owner wants a 3-release arc — they're
independent (measurement vs docs). Kept separate so each theme is crisp and 1.0
stays a clean launch.

### v1.0.0 — "Launch"
> **done_means:** hippo's identity is re-cut around what still stands alone in
> mid-2026 (git-native, git-drift semantic staleness, zero-LLM hot path,
> review-gated memory); a self-authored comparison table, one reproducible
> number, and a git-drift hero demo are on the landing page; a stranger can
> contribute (CONTRIBUTING/CoC/templates) and report vulnerabilities; and
> **"1.0" is a written commitment** — a frozen compatibility surface, a support
> policy, and a followable upgrade/migration path — ratified against a refreshed
> landscape and an explicit cut-list.

**Items:** POS-1 `P0`, COM-1 `P0`, COM-5 `P0`, POS-2 `P1`, POS-3 `P1`, POS-4
`P1`, DOC-10 `P1`, COM-6 `P1`, COM-7 `P1`, COM-2 `P1`, POS-5 `P2`, POS-6 `P2`,
POS-7 `P2`, QUA-12 `P2`.
**Decisions:** OQ-8 (post-1.0 compatibility policy), OQ-9 (INT-6 cut recorded).
**re-bootstrap:** **no**.

*Rationale:* 1.0 carries **no new engine risk** — it is positioning, collateral,
community, and commitment. That is the disciplined way to reach a version number
that adopters can trust.

---

## 7. Decisions to revisit (flagged, not overridden)

These would touch existing `decisions` / `guiding_invariants`; each needs an
explicit owner call, in the project's OQ style.

- **OQ-7 (amends OQ-6 — trust posture).** OQ-6 decided "hard-block + one-time
  consent, names-only." The public-corpus threat model breaks two assumptions:
  (a) consent must review the **injected content** (descriptions), not just names
  (SEC-5); (b) "one-time" must gain a **re-consent-on-material-change** clause
  (SEC-6). *Proposed:* consent shows injected descriptions; trust record carries
  a corpus fingerprint; re-prompt on change. Env override for CI unchanged.
- **OQ-8 (NEW — post-1.0 compatibility policy).** The guiding invariant "renames
  are clean breaks with a version bump, not compat shims" is correct **pre-1.0**
  (exercised by INT-7 in v0.2, DOC-8 in v0.4 — both on *minor* bumps). Post-1.0 a
  clean break on a minor would silently break every install. *Proposed:* after
  1.0, a rename/removal of any **frozen-surface** element (COM-5) is a **major**
  bump *or* ships a deprecation window; the clean-break invariant is explicitly
  qualified to pre-1.0 + major bumps. **Declaring 1.0 without this is declaring a
  promise you haven't written down.**
- **OQ-9 (NEW — INT-6 cut).** Record the warm-daemon deferral and its rationale
  (§3) in `decisions`, so its absence reads as a decision (the project's own
  pattern for OSP-5/Windows).
- **OQ-10 (NEW — RET-5 default).** RET-5 shipped "behind an env flag first" with
  no scoped follow-up to turn it on. v1 is the forcing function: decide on RET-8
  evidence.
- **Invariant tension — TEA-5 committed usage on public repos (SEC-14).** TEA-5
  deliberately excepts the "derived caches stay gitignored" invariant so
  teammates can union usage. On a *public* remote that publishes per-user recall
  patterns. *Proposed:* narrow the exception — committed summary is opt-in, with
  a public-remote warning; the invariant holds by default.
- **Invariant tension — CAP-5 autonomous MOC write.** A reason CAP-5 is deferred:
  a regenerated MOC note is an autonomous body write, against
  `guiding_invariants` "no bulk autonomous sweeps." If it ever ships, it needs a
  per-item, agent-gated design — not a sweep.

---

## 8. What v1.0.0 **is** and **is not**

**v1.0.0 IS:**
- A **local, git-native, offline** Claude Code memory plugin where **git is the
  store** (not merely git-diffable), recall costs **$0 / zero tokens / zero
  network** per prompt, staleness is **semantic git-drift** (did the cited code
  move), and team memory ships **through code review + an approval gate**.
- **Safe to clone in the open** — consent reviews what injects; foreign corpora
  are quarantined at inject time; deps/model are inventoried and pinned.
- **Provable** — per-category recall/precision measurable on any corpus, one
  reproducible public number, a hero demo of the one behavior no competitor has.
- **Legible & contributable** — mental-model docs, observable first recall, FAQ,
  CONTRIBUTING/SECURITY/templates.
- **A written stability commitment** — a frozen compatibility surface + support
  policy + upgrade path.

**v1.0.0 is NOT** (carried from `non_goals`, plus new cut-list):
- **Not** a conversational/chat-history memory that autonomously extracts facts —
  extraction stays human-approval-gated; this is *why* LongMemEval/LoCoMo/BEAM
  are out of scope (state it as positioning, POS-3).
- **Not** cloud/server-backed; **not** Windows-supported; **not** an
  autonomous-bulk-write tool; **no** LLM on the hot path; **not** a replacement
  for native memory (it's the layer on top); **no** query DSL. *(unchanged
  `non_goals`.)*
- **No PPR ranking (GRA-7), no typed-taxonomy schema change (LIF-7), no
  auto-MOC/rolling-summary (CAP-5), no warm daemon (INT-6)** — deferred/cut with
  reasons (§3); revisit in v1.x on field evidence.

---

## 9. Top 5 highest-leverage moves toward a credible v1 OSS launch

Ranked. Each is either a launch blocker or the single biggest multiplier in its lane.

1. **Fix the trust consent/injection gap (SEC-5).** `P0`, effort **S**. hippo's
   headline security feature currently lets a user authorize injection of content
   they were never shown — the review samples *names*, injection uses
   *descriptions*. For the exact public-corpus threat a launch introduces, this
   defeats the whole "safe by default" story. Cheapest-to-fix, highest-severity
   item on the board; do it first.
2. **Re-cut the positioning around what survives 2026 (POS-1/2/4/5), and say so
   with a comparison table + git-drift demo.** `P0`, effort **S–M**. The engine
   is done; positioning is now the highest-leverage lever because the
   "markdown + hybrid recall" wedge is crowded (claude-mem 86K stars; Anthropic's
   own GA memory tool). Lead with git-native + git-drift staleness + zero-LLM
   ($0/prompt) + review-gated. Without this, real engineering depth reads as
   me-too on launch day.
3. **Build RET-8, the measurement keystone.** `P1`, effort **M**. Its gate is
   cleared; it unblocks a *credible number* (POS-3), the RET-5 default-on
   decision, RET-1's precision gate, and any multi-hop claim. "Uniform efficacy"
   and KPI-2/KPI-4 are unfalsifiable per-project until it exists.
4. **Ship the OSS on-ramp + make "1.0" mean something (COM-1, COM-2, COM-5,
   SEC-9/10).** `P0–P1`, effort **S–M**. CONTRIBUTING + SECURITY + CoC + issue
   templates + a maintainer identity + a **written stability commitment** (frozen
   surface, support policy, post-1.0 compatibility via OQ-8). Their absence reads
   as pre-launch/abandonware, and declaring 1.0 without a stability contract is
   an empty promise.
5. **Make first recall observable and lead with the mental model (ONB-8,
   DOC-9).** `P0–P1`, effort **S–M**. KPI-1 is literally "time to first
   *successful* recall," yet the quickstart never lets a stranger *see* one, and
   the README opens with acronyms. A newcomer must be able to prove-to-themselves
   the loop closed within five minutes — and understand what a memory *is* before
   they install.

---

*Prepared as a proposal. No code, `ROADMAP.yaml`, or branch-protection changes
were made. All `file:line` references were verified against the working tree at
`v0.7.0` (branch `release-v0.7.0-team-and-fleet`).*
