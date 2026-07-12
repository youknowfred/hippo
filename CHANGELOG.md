# Changelog

All notable changes to hippo are recorded here. Format is loosely
[Keep a Changelog](https://keepachangelog.com/)-shaped, kept plain. The release
process is formalized in [`RELEASING.md`](RELEASING.md) (DOC-7, v0.6.0): entries
are written by hand as the final commit of each release PR, `plugin.json` and
`marketplace.json` versions are kept in lockstep by `tests/test_version_sync.py`
and the tag-time `release.yml`, and every entry states a **re-bootstrap** flag.

## v1.10.0 — 2026-07-12 — "Second surface"

**re-bootstrap: no** — `plugin/requirements.txt` is byte-identical to v1.9.0, and every persisted
shape is unchanged (corpus format still 4, index schema still 6, capture-seed schema still 2). The
`/hippo:*` skill set (15) is unchanged, the five frozen MCP tools keep their exact names and
shapes (STABILITY.md), and the recall hot path is untouched. Everything here is **additive**: four
new MCP tools and two new engine modules behind them.

The release makes hippo first-class on its **second surface**: the Claude Desktop app's local
sessions run installed plugins' hooks, skills, and MCP servers through the same engine as the
terminal CLI (verified live against the desktop harness) — they only reject *typed* `/hippo:*`
commands. Setup was the one thing stuck in the terminal; these tools unstick it. Install remains
the single terminal step; bootstrap, init, consent, and diagnostics now work from either surface
(and from subagents). The full no-terminal onboarding path — wire a cloned corpus, review it,
consent, recall — is exercised end-to-end over the real stdio transport in the test suite.

### The setup tools (INT-9..12)

- **INT-9** — **`trust_corpus`**: the SEC-1 consent flow as a two-step tool. A review call
  **never trusts** — it returns the memory count, the exact description strings recall would
  inject (quoted as untrusted data), and a **consent digest**; the confirm call requires that
  digest, binding consent to the reviewed bytes (a corpus that changes in between refuses — a
  TOCTOU guard the single-sitting terminal flow never needed). First consent stamps the SEC-6
  fingerprint + SEC-7 `origin="review"`; drift re-consent reviews exactly the changed/added
  delta (`corpus_consent_sample` grew a `stems` filter) and preserves the existing origin.
- **INT-10** — **`init`** + `memory/init_project.py`: the mechanical `/hippo:init` flow as one
  tested engine function (the DOC-4 engine/skill shape). Fresh project: core pack + `MEMORY.md`
  skeleton + format marker + `CONVENTIONS.md`, then the machine wiring; existing corpus: wiring
  only. Idempotent, never overwrites a memory file, never commits. One deliberate divergence from
  the skill, SEC-1-load-bearing: a **model-invoked init never auto-trusts a pre-existing corpus**
  (typing `/hippo:init` is itself the user's review; a tool call is not) — only a corpus the call
  *creates* is trusted (`origin="init"`), and consent otherwise routes through `trust_corpus`.
- **INT-11** — **`bootstrap`** + `memory/bootstrap.py`: the one online step (venv + ~130MB model
  warm) as kick-off-and-poll — `start` detaches a stdlib-only worker (own session, sentinel
  written LAST, log + live-pid lock under `CLAUDE_PLUGIN_DATA`), `status` polls it. This is
  load-bearing for the desktop app: the harness hands **each surface its own plugin-data dir**
  (`hippo-<marketplace>` vs `hippo-inline`), so a terminal bootstrap's venv is invisible to
  desktop sessions — without an in-surface bootstrap, desktop recall stays BM25-only forever.
  `status` names a sibling surface's install so "why is it downloading again?" answers itself.
- **INT-12** — **`doctor`**: the deterministic DOC-4 diagnostic engine verbatim, plus a mapping
  from each report line's named fix to the tool that runs it on this surface. Deliberately
  ungated (doctor is the pre-consent review entry point), with a pinned boundary: the report
  exposes state, never the injectable description strings.

### Docs (DOC-15)

- The README Quickstart callout and Troubleshooting now tell the accurate two-surface story
  (install from the terminal once; use hippo from terminal or desktop; typed commands and
  cloud/remote are the real limits), including the per-surface bootstrap entry. plugin/README's
  MCP section documents the nine-tool surface and the model-invoked-init consent rule.

## v1.9.0 — 2026-07-11 — "Out in the open"

**re-bootstrap: no** — `plugin/requirements.txt` is byte-identical to v1.8.0
(`fastembed>=0.4,<0.8`, `numpy>=1.26,<3`, `PyYAML>=6.0,<7.0`, `rank-bm25>=0.2.2,<0.3`), and every
persisted shape is unchanged (corpus format still 4, index schema still 6, capture-seed schema
still 2). The `/hippo:*` skill set (15) and the MCP tool set (5 tools / 3 resources) are unchanged;
nothing here touches the recall hot path's behavior. This is a **launch release**: it carries no
new engine risk, only the measurement, positioning, community, and brand work that makes the repo
safe to hand a stranger — and it accompanies flipping the repository **public**.

Where v1.8.0 **"Safe in the open"** made a cloned corpus safe *by review of what injects*, v1.9.0
takes hippo **out** into the open: a reproducible benchmark anyone can re-run, a self-authored
comparison against the mid-2026 field, a written 1.0-grade stability contract, a stranger's
contribution path, and — at last — a face. It folds the roadmap's **v0.9.0 "Proven for strangers"**
measurement spine, the **v0.10.0 "Legible to strangers"** doc remainder, and the **v1.0 "Launch"**
positioning/community milestone into one semver step. Every item reached `main` through a reviewed
PR (noted per group).

### Measurement spine — "Proven for strangers" (RET-9/10/11, PRF-4/5, PR #26)

- **RET-9** — a per-corpus **abstention doctor check**: `/hippo:doctor` flags a corpus whose
  recall can't cleanly abstain on off-topic queries, rather than letting it silently inject noise.
- **RET-10** — the salience-weighting experiment was **run and DECIDED OFF**: on the category-tagged
  eval it moved nothing (the signal was vacuous), so it ships disabled rather than as dead
  complexity. (Owner-resolved OQ-10.)
- **RET-11** — a BM25-only abstention floor was **empirically rejected**: on-topic and off-topic
  queries overlap in every BM25 signal measured, so the honest abstention gate stays **dense-gated**;
  the finding is captured as a doctor check, not a false promise.
- **PRF-4/5** — the cold-latency gate moved from **p50 to p95** (the figure the recall hook actually
  pays), with the measured dense@500-memory p95 (~407 ms) recorded against the BM25 budget.

### "Proven for strangers" finish (CAP-6/7, GRA-8, INT-8, QUA-11, PR #27)

- **CAP-6** — the pending-capture queue gained a **value-first bound + snooze/dismiss**, so the
  SessionStart nudge defers a snoozed queue instead of nagging unboundedly.
- **CAP-7** — an end-to-end **capture → approval** integration test over the real drain path.
- **GRA-8** — `hippo links` grew `--components/--degree/--export (json|dot|mermaid)`; the link-graph
  component count feeds the trust scorecard as an informational line.
- **INT-8** — the README MCP table now lists all **5 tools + 3 resources** with mid-turn/subagent
  discoverability notes, and `doctor.check_mcp_launch` exercises the real `serve()` handshake so a
  broken MCP launch is a caught regression, not a silent one.
- **QUA-11** — a CI **resolution lane** (py3.11/3.13/3.14 dependency bootstrap) and a
  network-marked external-URL docs check.

### Legible to strangers — first-run docs (ONB-10, DOC-12, PR #28)

- **ONB-10** — `/hippo:init` can offer an optional **`user_role.md` fill** via a question prompt,
  under a hard rule: only user-supplied answers are written, never inferred or synthesized.
- **DOC-12** — a README **## Commands** reference covering all 15 skills, with the
  recall-vs-doctor / doctor-vs-audit / consolidate-vs-audit distinctions, and the stale "8 skills"
  layout block corrected.

### Positioning & launch collateral (POS-1..7 + demo + benchmark, PR #29)

- **POS-1/2** — the README lead is re-cut to hippo's durable, plain-language capability line, and a
  **"Compared to other memory tools"** table places it honestly against claude-mem, memsearch/
  memweave, native memory, and supermemory.
- **POS-3** — a **reproducible benchmark** (`bench/run.sh`, `bench/README.md`): the real recall
  engine over the shipped 50-memory golden corpus and 18 cross-vocabulary paraphrase queries —
  **recall@10 = 1.0**, **MRR@10 0.912 → 0.9213** (BM25-only → dense hybrid), at **$0 per prompt**,
  deterministic to the digit; with the principled *why not LongMemEval/LoCoMo/BEAM*.
- **POS-4** — `demo/git_drift.sh`: a no-download hero demo that writes a memory citing a function,
  edits the function, and shows hippo flag it stale — the one behavior no competitor reproduces.
- **POS-5/6/7** — the `$0`/private rationale, the shipping-products landscape, and a native-memory
  section for the GA `memory_20250818` tool + Auto Memory.

### Community on-ramp (COM-1/2/5/6/7, QUA-12, PR #30)

- **COM-1/2** — `CONTRIBUTING.md` (the CI-matching venv recipe, markers, checks, conventions),
  `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), issue forms (platform/backend/corpus-size), a PR
  template, and `CODEOWNERS`.
- **COM-5** — `STABILITY.md` ratifies **OQ-8**: the frozen 1.0 surface (`/hippo:*`, `bin/hippo`, the
  5 MCP tools, the `HIPPO_*` namespace, documented env vars, corpus format 4) — and, explicitly,
  what is *not* frozen (caches, schemas, tuning knobs, the Python API).
- **COM-6** — `UPGRADING.md`: a worked corpus-format 2→3 migration and the three upgrade kinds.
- **COM-7** — manifests attribute to **youknowfred** with keywords/author mirrored across both;
  README CI/version/MIT badges.
- **QUA-12** — `release.yml` now cuts a **GitHub Release** from the CHANGELOG extract with
  SHA-pinned actions, plus `dependabot.yml` and a codified branch-protection ruleset.

### Concepts & brand

- **DOC-9** (PR #25) — a **"How hippo thinks"** concepts page (`CONCEPTS.md`) and a README
  mental-model lead: what a memory is, the always-on floor vs. on-demand recall, the four types,
  why markdown-in-git.
- **BRAND** (PR #37) — the **waterline-hippo** logo, wordmark, and lockup (`assets/logo/`, one file
  for light+dark grounds; a `prefers-color-scheme` lockup at the README top), and
  `author.url → youknowfred.com` in both manifests.

### Security & hygiene (pre-launch sweep)

- **SEC-1 gate coverage** — the MCP `traverse` and `decision_history` tools now honor the trust
  gate that `recall` / `why` / `new_memory` and every resource already enforce. Both rendered
  memory **names + typed edges + dates** from an untrusted foreign corpus into agent context
  without consent (metadata only — never descriptions or bodies — so the exposure was narrow);
  they now withhold until the corpus is reviewed, with regression tests that run with the
  trust-all test override removed.
- **Fixture scrub** — a `tests/` fixture hard-coded a real personal absolute path; replaced with
  a synthetic path (no behavior change). `.gitignore` gained explicit `.env*` /
  `.claude/*.local.json` entries so local secrets can't be committed by accident.
- **py3.13/3.14** — a test's `re.split(..., 1)` passed `maxsplit` positionally, which newer
  Python deprecates and the suite escalates to an error; now `maxsplit=1`. Shipped code was
  never affected.

## v1.8.0 — 2026-07-10 — "Safe in the open"

**re-bootstrap: no** — `plugin/requirements.txt`'s dependency constraints are byte-identical to
v1.7.0 (`fastembed>=0.4,<0.8`, `numpy>=1.26,<3`, `PyYAML>=6.0,<7.0`, `rank-bm25>=0.2.2,<0.3`);
SEC-11 added upper-bound *documentation* and a hardened-install recipe, not a version change.
Persisted shapes are ALL unchanged since v1.7.0 (corpus format still 4, index schema still 6,
capture-seed schema still 2) — the trust spine's content fingerprint lives in the machine-local
trust record, outside the corpus. The `/hippo:*` skill set grew from 14 to 15 (RUL-6 added
`/hippo:promote-rule`); the MCP tool set is unchanged.

This release delivers the roadmap's **v0.8.0 "Safe in the open"** security tier
(`ROADMAP.v1.md` §6) — the milestone that makes cloning hippo's own public repo, or any public
repo carrying a hippo corpus, safe *by review of what actually injects* — together with the
S-effort slice of the **v0.10.0 "Legible to strangers"** first-run polish, and the ranking,
evaluation, and reach work that merged since v1.7.0. Every item below reached `main` through a
reviewed PR — the numbers are noted per group below; several PRs bundled multiple items (e.g.
#19, #21), and this first-run-polish set ships with the release PR itself.

### Security — the "Safe in the open" tier (SEC-5..14)

**Trust spine (SEC-5/6/7, PR #19).**

- **SEC-5** — consent surfaces the memory **descriptions** that actually inject (not just
  filenames), rendered through the injection layer's own formatter (byte-equal, test-pinned),
  so you review the strings that will enter every prompt.
- **SEC-6** — the trust record stores a per-file **content fingerprint**; recall QUARANTINES a
  drifted or newly-added project-tier file, per file, until you re-review — defeating
  trusted-upstream supply-chain injection. hippo's own write primitives fold their writes into
  the baseline (authorship = consent); index builds never do.
- **SEC-7** — an inject-time **provenance banner** + defensive demarcation for foreign/cloned
  corpus lines; a corpus trusted by review (vs one you authored) is labelled as such at use.

**Launch-security tail (SEC-8..14, PRs #21/#22).**

- **SEC-8** — broadened the secret detector (Slack/Google/Stripe/OpenAI/Anthropic/JWT/npm/PyPI/
  connection-string patterns, high-precision floors) and added a `secret-scan` CI job over the
  tracked tree.
- **SEC-9** — repo-root `THIRD_PARTY_NOTICES` inventories the four direct deps, their transitive
  tree, and both embedding models, verified against installed venv metadata and drift-guarded.
- **SEC-10** — repo-root `SECURITY.md`: private vulnerability disclosure channel, supported
  versions, and threat model.
- **SEC-12** — the trust gate now covers a **non-git directory that carries a real corpus**, so
  a zip download is no longer a trust bypass; an empty non-git dir stays inapplicable.
- **SEC-13** — the MCP `new_memory` tool runs the same resource gate as reads (no
  write-without-review asymmetry); `serve()` rejects an over-long line before parsing it.
- **SEC-14** — `--record-usage` refuses to write committed telemetry on a repo that has a git
  remote unless explicitly opted in (local-only proceeds); a new doctor check surfaces
  committed-usage privacy.
- **SEC-11** — every dependency is **bounded on both sides**, so a compromised new major can't
  be pulled at bootstrap; a test enforces the no-unbounded-`>=` invariant, and
  `requirements.txt` documents a per-environment hardened hash-install recipe and the model's
  content-addressed integrity check. Ranges stay ranges on purpose: no single numpy version
  spans CPython 3.10–3.14, so a universal exact lock is infeasible.

### Ranking, evaluation & reach

- **GRA-1** (PR #18) — fixed the dense-side knee that suppressed graph expansion; multi-hop
  neighbours the dense floor previously starved now surface (golden set byte-identical).
- **RET-8** (PR #16) — a category-tagged eval suite, so recall precision is measurable
  per-category on any corpus (unblocks the salience decision).
- **SIG-6** (PR #17) — abstention blind-spots feed the eval fixtures; the T7 signals close-out.
- **RCH-5** (PR #19) — trust-gated pack install/update: a curated memory subset lifts and
  re-applies across projects, gated on the same trust spine.
- **RUL-6** (PR #23) — glob-scoped rule promotion (`/hippo:promote-rule`, the 15th skill):
  promote a proven memory into a path-scoped rule, propose-only, derived from its cited paths.
  Unblocked by the owner clearing the LIF-7 capture-soak gate.

### First-run polish for strangers (v0.10.0 S-slice)

- **DOC-14** — scrubbed origin-repo jargon ("the ic-memobot/Memosa … private origin repo") from
  every stranger-facing surface (README intro, `plugin.json` description, `requirements.txt`
  header); the License section keeps its relicensing provenance with only the internal name
  removed.
- **ONB-8** — the README Quickstart now ends on an **observable first recall** ("what do you
  remember about my role?" / `/hippo:recall`) — the KPI-1 metric it never exercised before.
- **ONB-9** — `/hippo:init`'s closing narration ends with a **try-it-now** nudge naming the exact
  next command, completing the init → observed-recall funnel.
- **DOC-11** — a README **Troubleshooting** section (empty-recall triage, on-demand vs
  always-load-floor recall, non-git degraded mode, `/hippo:doctor`).
- **CAP-8** — the README now surfaces the **automatic-capture → `/hippo:consolidate` approval
  loop** with a worked example — hippo's strongest differentiator versus native memory.

### Docs

- **explorations-r2** (PR #20) — a round-2 enhancement catalog + draft post-v1 roadmap
  (`EXPLORATIONS2.md`, `ROADMAP.enhancements2.yaml`), owner-triage pending; no shipped behavior.

## v1.7.0 — 2026-07-09 — "Reach — knowledge that travels"

**re-bootstrap: no** — `plugin/requirements.txt` is unchanged across both tiers below; the code
swap on update is sufficient. Persisted shapes are ALL unchanged since v1.5.0 (corpus format
still 4, index schema still 6, capture-seed schema still 2): T6's origin/pack stamps are
additive absence-emits-nothing frontmatter rendered from per-hit file reads, and T7 adds no
persisted surface at all.

**Covers tiers T6 and T7 (T7 partial by design).** T6 ("Reach", PR #14) and T7's one
unblocked item (PR #15) were each merged to `main` as their own reviewed, CI-green PR and are
released together here. The REST of T7 is hard-gated and NOT in this release: SIG-5/SIG-6
wait on RET-8 (not yet built), RUL-6 waits on the LIF-7 CAP-soak owner judgment — the tier
stays `in_progress` in `ROADMAP.enhancements.yaml` with dated gate notes, and the salience
default stays OFF (no ranking behavior changed in this release).

The theme: knowledge stops being repo-bound. A lesson promotes up with provenance, foreign
rules import in, decisions replay their own history, recall spans every trusted project,
curated subsets extract as packs, and the floor fans out to the cross-tool rule plane — every
one of them per-item, propose-first, and drift-checked.

### T6 — Reach (v1.6.0, PR #14)

- **RCH-6** — the portability linter, the shared lift-time primitive: `repo_coupling` findings
  route to strip/rewrite ("warn"), `consequential_default` findings (attribution/CI-bypass
  policies) each demand an individual yes ("confirm") — manifest-parity-pinned to the shipped
  packs so the two catalogs cannot drift.
- **RCH-1** — `/hippo:promote`: lift ONE proven-portable memory into the user (or private)
  tier with an origin stamp — recall everywhere then answers "learned in `<repo>@<sha>`".
  All guards run before any write; a refusal is a zero-filesystem-change event.
- **RCH-2** — `/hippo:import`, the Cursor `.mdc` adapter: foreign rules become ranked, deduped,
  secret-linted memories with globs landing as `cited_paths` (born staleness-tracked). Ships
  the tier's premise correction: real Cursor frontmatter is NOT valid YAML (`globs: **/*.ts` —
  a bare `*` is a YAML alias), so a tolerant line-based fallback does the parsing.
- **RCH-3** — decision-chain replay: `supersedes`/`refines` walked transitively into ONE
  chronological narrative with branch-point annotations (`contradicts` never traversed),
  behind the 5th MCP tool `decision_history` and `/hippo:recall --history`.
- **RCH-4** — trust-gated `--all-projects` recall: every registered corpus passes the trust
  gate AT QUERY TIME before its index loads; hits label their source repo; every skip is
  named in the output trailer. Golden eval byte-identical before/after.
- **RCH-5** — pack EXTRACT only: curated subsets leave as install-shaped packs (provenance
  stripped, consequential markers auto-derived). Install/update stay gated on the v0.8.0
  trust spine — a negative-capability pin in the suite is the tripwire that flips when the
  spine ships.

### T7 — Learned ranking, the unblocked slice (v1.7.0, PR #15)

- **RUL-7** — `/hippo:export-agents`, the AGENTS.md fan-out: the project floor renders as a
  PROPOSED `AGENTS.md` diff (the Linux-Foundation cross-tool rules file) — propose-only, the
  module never writes; a marker-delimited managed block preserves hand-maintained content
  byte-verbatim; project tier only (user/private tiers never enter a committed file). Glob
  scoping derives from `cited_paths` via the new RUL-6-shared `derive_paths_globs` (single
  citation stays a literal path; same-dir collapses capped at 3× over-coverage; `**` never
  emitted). Once applied, the exported file is drift-checked by the EXISTING doctor/
  SessionStart channels: `archive._SCAN_TARGETS` and the rules-plane dead-glob leg now cover
  `AGENTS.md`, so a moved cited path flags loud with zero new reporting surface.

New skills since v1.5.0: `/hippo:promote`, `/hippo:import`, `/hippo:pack`,
`/hippo:export-agents` (pinned skill list 10 → 14). MCP: 5 tools (+`decision_history`),
3 resources. Suite: 1462 hermetic tests; eval gates unchanged and green throughout
(self 0.98 / hard 1.0 / mrr 0.92 on the golden corpus).

## v1.5.0 — 2026-07-09 — "Knowledge that grows itself"

**re-bootstrap: no** — `plugin/requirements.txt` is unchanged across every tier below; the code
swap on update is sufficient. Persisted-shape changes since v0.7.0, each a clean break per the
usual discipline (stamp-only additive corpus bumps, one full index rebuild via the manifest
schema gate — no migration code, no compat shims): **corpus format 2 → 4** (steer:pin in GOV-2,
author confidence in GOV-7), **index schema 3 → 6** (manifest `head_commit` in RCL-6, then the
two GOV bumps in lockstep), and the gitignored capture queue's own seed schema 1 → 2 (GRW-1/4 —
not a corpus artifact).

**First tagged release since v0.7.0.** The five post-v0.7.0 enhancement tiers were each merged
to `main` as their own reviewed, CI-green PR and are released together here; per-tier detail
lives in `ROADMAP.enhancements.yaml` (every item now `status: done`) and the tier PRs.

The theme of the release: the corpus stops being something you maintain and starts maintaining
itself — recall gets precise, rules join the plane, governance gets legible, and capture,
graph, and staleness all close their loops with the human still holding every write gate.

### T1 — Positive context & dark signals (v1.1.0, PR #9)

- **SIG-1** — the first positive SessionStart producer: memories relevant to the uncommitted
  working-tree diff are surfaced because of WHAT YOU ARE DOING, not just what you typed.
- **SIG-2** — a "where you left off" resume card replayed from the episode buffer.
- **SIG-3** — silent recall abstentions become a recurring blind-spot backlog (doctor check +
  rare SessionStart nudge) instead of vanishing.
- **SIG-4** — KPI-2 finally measured: a PostToolUse read-signal ledger for injection precision.

### T2 — The rules bridge (v1.2.0, PR #10)

- **RUL-0** — the `.claude/rules` `paths:` scoping claim verified live before anything built on
  it (unscoped rules always-load; `paths:`-scoped rules lazy-inject on matching file reads).
- **RUL-1** — a loud rule↔memory conflict radar (governance cites what the corpus disputes).
- **RUL-2** — staleness over the rules plane itself (rules rot like memories do).
- **RUL-3** — write-time dedup against rules: link, don't copy.
- **RUL-4** — rules surfaced as an on-demand recall source (labelled `(rule)`, never duplicated).
- **RUL-5** — `hippo://floor` + rules MCP resources so subagents share the plane.

### T3 — Retrieval precision (v1.3.0, PR #11)

- **RCL-1** — per-query intent routing on the hot path (hot-path-safe, eval-gated).
- **RCL-2** — floor-dedup + within-session cooldown: injected tokens earn their place.
- **RCL-3** — terse-follow-up rescue rides the same session-scoped episode read.
- **RCL-4** — MMR intra-block diversity (applied after the knee — the ordering bug the tier's
  own hermetic fixtures caught).
- **RCL-6** — body-hit evidence snippets (index schema 3 → 4 for manifest `head_commit`).
- **RCL-5** — an off-hot-path cross-encoder rerank on the explicit surfaces only.

### T4 — A corpus you can govern (v1.4.0, PR #12)

- **GOV-1** — a drainable contradiction inbox + `/hippo:resolve` (verdicts: supersede, scope,
  merge, dismiss — the dismiss ledger is per-clone and refuses to run without a durable home).
- **GOV-2** — `steer: pin`, the first author control axis (corpus format 2 → 3; bounded ×1.2
  boost, closed enum, mute deliberately deferred to the salience keystone).
- **GOV-3** — consolidation proposals carry evidence: a `--check` baseline (HEAD at proposal
  time) and a fenced `Rationale:` in the written body.
- **GOV-4** — floor/corpus change governance: a per-clone watermark diff surfaces what changed
  underneath you, exactly once.
- **GOV-5** — `/hippo:why` glass-box: per-hit "won via <backend>", salience components, and an
  honesty-ordered abstention receipt (with the fused-vs-cosine scale premise correction).
- **GOV-7** — author confidence tier (`draft|verified|authoritative`; corpus format 3 → 4;
  display-only — pinned never to touch ranking).
- **GOV-6** — the doctor trust scorecard: one deterministic line aggregating contested pairs,
  rule conflicts, rot, blind spots, orphans, pins, drafts, and the floor delta.

### T5 — Knowledge that grows itself (v1.5.0, PR #13)

- **GRW-1** — capture quotes its evidence: bounded VERBATIM diff hunks in the SessionEnd seed
  (tracked + untracked, binary-stripped, line-boundary byte cap), secret-linted at capture and
  hard-gated again before any hunk lands in a memory body; plus a per-seed salience label that
  orders the pending queue and marks trivial sessions (label, never a gate). Seed schema 1 → 2.
- **GRW-2** — Hebbian co-recall: pairs that co-surface across ≥3 distinct sessions become
  per-item untyped `[[wikilink]]` proposals at consolidate (sparse maps stay empty by design).
- **GRW-3** — a merge tier for near-duplicate COMMITTED memories on the audit sweep, scored by
  the calibrated write-time dup mechanic in both directions (never recall's fused scores — the
  scale premise correction recorded in-file), with a per-item fold/rewrite/close recipe enforced
  structurally by archive's inbound guard.
- **GRW-8** — a contradiction-adjudication fork on that same sweep (merge / contradicts / link),
  with the mislabel guard pinned: a reworded duplicate is NOT a contradiction. Accepted edges
  drain through the GOV-1 inbox automatically.
- **GRW-4** — the WHY captured in-session: an agent-driven decisions ledger
  (`capture --add-decision`, PreCompact nudge with the session id baked in) folded into the
  seed — transcription of what the user confirmed, never synthesis.
- **GRW-5** — commit-precision re-verify: commits since the last session's episode watermark
  that touch cited files join the reconsolidation worklist (`[since-watermark]`), recalled
  recently or not — no `.git/hooks` surface added.
- **GRW-6** — squash-merge healing: when a merge is detectable AND staleness baselines actually
  broke, a producer names the broken memories and routes each to a confirmed per-item
  `graduate` rebaseline (the healer was always `reverify_file`; now the break gets an offer).
- **GRW-7** — supersession is an auditable boundary: demote `--superseded-by` stamps the
  loser's `invalid_after` at the SUCCESSOR's commit date (ledger records boundary + successor;
  no new field, no schema bump). `fix --superseded-by` now refuses — the old combination wrote
  an edge and stamped nothing, a silent half-supersede.

New skills since v0.7.0: `/hippo:resolve`, `/hippo:why` (pinned skill list now 10). MCP: 4
tools (+`why`), 3 resources (+`hippo://scorecard`). Suite: 1371 hermetic tests; eval gates
unchanged and green throughout (self 0.98 / hard 1.0 / mrr 0.92 / p95 <30ms on the golden
corpus).

## v0.7.0 — 2026-07-08 — "Team & fleet — memory that survives more than one human and one repo"

**re-bootstrap: no** — `plugin/requirements.txt` is unchanged; the code swap on update is
sufficient. No corpus format (still 2) or index schema (still 3) change this release: the
multi-corpus fusion is a purely in-memory merge at recall time, so no persisted manifest shape
changed and every golden/byte-identity pin holds.

New env vars: `HIPPO_USER_MEMORY_DIR` (TEA-1 user-tier location, default `~/.claude/hippo-memory`),
`HIPPO_LOCAL_MEMORY_DIR` (TEA-3 private-tier location, default `.claude/memory.local`), and
`HIPPO_USAGE_USER` (TEA-5 usage-summary identity override). New surfaces: `/hippo:new --tier
{project|user|private}`, `python -m memory.soak --record-usage`, a SessionStart `portable_floor`
producer, a new `scale` pytest marker, and a nightly CI lane. New pytest marker: `scale`.

The theme of the release: memory stops being trapped in one person's one clone. A person-scoped
lesson learned in project A is now known in project B; a team corpus carries no one's personal
policies; a private note is recallable locally yet invisible in git; usage signals say plainly
when they only speak for this clone; and there is finally a documented way to make a memory
truly forgotten. The sharpest invariant — a user/private-tier memory is recallable everywhere
yet its content NEVER enters a project's git — is adversarially pinned.

### Shipped this release

- **TEA-1** — Two-tier corpus: a machine-local **user tier** (`~/.claude/hippo-memory`,
  `HIPPO_USER_MEMORY_DIR`) holding person-scoped `user`/`feedback` memories, indexed and recalled
  ALONGSIDE the project corpus via true two-corpus fusion (a single in-memory `LoadedIndex`, so
  BM25/dense/RRF/floor/knee/graph all run once, unchanged), with each hit provenance-labelled
  (`corpus`/`root`) and the floor drawn from BOTH (recall-dedup union + a bounded SessionStart
  `portable_floor` producer, since the user tier has no native always-load channel). Machine-local
  only (OQ-5). Each tier keeps its OWN gitignored index — no merged manifest is ever written to
  disk — so **no user-tier content enters the project's git**; `/hippo:new --tier user` routes the
  file and its floor pointer to the user tier's own `MEMORY.md`. An adversarial test pins that a
  user-tier write leaves the project git tree pristine (`status`/`ls-files`/manifest all clean).
- **TEA-3** — Private memory tier (`.claude/memory.local/`): a gitignored in-repo sibling merged
  into the same recall (labelled `private memory`), created by init and self-ignoring (SEC-3 `*`
  `.gitignore`) so it is invisible in `git status` and uncommittable even without the patch, while
  staying fully recallable locally. Its index nests inside the tier (its plain sibling would
  collide with the project's). A teammate who lacks the dir degrades to silence, never an error.
- **TEA-5** — Usage signals honest about scope: every coldness surface (soak CLI, archive report,
  audit skill) now LABELS the signal clone-local vs cross-clone, so "never recalled in THIS clone"
  is never mistaken for team-wide dead weight. Opt-in committed per-user summaries
  (`.claude/memory/.usage/<user>.json`, written by `soak --record-usage`, no session ids) that
  `curation_report`/`soak_status` UNION before judging coldness. `provenance.current_user_slug`
  is the first identity derivation in shipped code.
- **SEC-4** — Documented purge procedure: a `plugin/memory/README.md` section (remove the file →
  `git filter-repo` history scrub → index rebuild → ledger clear → recall verification),
  contrasted with the reversible `/hippo:archive` and whole-project `/hippo:remove`. The pointer
  is single-sourced in `secrets.REMEDIATION`, so both the write-time warning and doctor's secret
  check name it.
- **PRF-3** — 500-memory scale lane: a deterministic generated ~500-memory BM25 corpus asserting
  recall latency (warm p95 < 300ms), bounded output with a 45-memory match set (≤ `DEFAULT_K`,
  ≤ 9000 chars), and build/refresh time budgets — each failure naming the budget it broke.
  `scale`-marked so it stays off the hermetic and per-PR dense lanes; a new nightly CI job
  (`schedule:` 07:00 UTC) runs it.

## v0.6.0 — 2026-07-07 — "The write path — capture up to the approval gate; memory reaches every agent"

**re-bootstrap: no** — `plugin/requirements.txt` is unchanged; the code swap on update is
sufficient. (`hypothesis` was added for QUA-9's fuzz tests, but as a CI/test-only install line,
NOT a runtime dependency — mirroring QUA-10's pytest-timeout.) No corpus format or index schema
change this release.

New env var: `HIPPO_PENDING_DIR` (CAP-2 override for the gitignored draft-capture queue,
`.claude/.memory-pending/`). New surfaces: two skills (`/hippo:recall`, `/hippo:consolidate`),
a stdio MCP server, and three capture hooks (`PreCompact`, `SessionEnd`, `SubagentStop`).

The theme of the release: durable facts stop dying with the session. Capture is now automated —
but only ever UP TO an explicit approval gate, never past it. Nothing a capture pass produces
can reach `.claude/memory/` without a per-item, agent-gated write; that boundary is structural
(the capture module has no corpus writer) and adversarially tested.

### Shipped this release

- **CAP-1** — a `PreCompact` hook nudges the model to persist durable facts via `/hippo:new`
  before compaction discards session detail. Prompt-level, no Python spawn, no corpus writes.
- **CAP-2** — the `SessionEnd` draft-capture pass finally consumes the soaking episode buffer:
  it snapshots a session's episode replay (queries + recalled names + HEAD watermark) plus
  `git diff` since that watermark into ONE seed in the gitignored `.claude/.memory-pending/`
  queue, for per-item approval next session. The approval gate is structural — `memory.capture`
  imports no corpus writer — and a SessionStart producer surfaces the queue so it never soaks
  silently.
- **CAP-3** — `new_memory --check`: a dry-run that scores a captured candidate against the
  corpus with LIF-2's near-duplicate machinery WITHOUT writing, so approving a duplicate routes
  to update/supersede instead of a new file.
- **CAP-4** — `/hippo:consolidate`, a sleep-time skill that drains the capture queue
  (check-first), works the reconsolidation worklist, and refreshes the graph in one deliberate
  turn — keeping the hook path pure retrieval.
- **INT-1** — `/hippo:recall`, the read-side verb: "what do you remember about X" / list by
  type, reusing the exact hook ranking and annotating each hit with type, staleness, and graph
  neighbors.
- **INT-2** — a dependency-free stdio MCP server (`recall` / `new_memory` / `traverse` tools)
  giving mid-turn and subagent memory access; the hook path never imports it and still works
  with it absent.
- **INT-3** — a `SubagentStop` capture path (subagent discoveries become capture candidates)
  plus the Task-prompt injection pattern for policy-critical delegations.
- **INT-4** — the native-memory coexistence contract: a doctor check for symlink-target drift +
  native-layout change, a compatibility doc (`plugin/memory/NATIVE_MEMORY.md`), and a README
  positioning section.
- **INT-5** — one Python launch per prompt (`memory.recall --stdin-json` reads the hook payload
  and emits the output JSON itself, replacing three spawns + jq), and a doctor p95 hot-path
  latency check over the ledger.
- **QUA-9** — property-based (Hypothesis) fuzzing over the parsing surfaces: `split_frontmatter`
  body-preservation, `backfill`/`set_invalid_after` never touching the body, `clean_query`
  totality + input-bounded output, and `tokenize`/`normalize_slug` totality over Unicode.
- **DOC-7** — release engineering: `plugin.json` / `marketplace.json` bumped to 0.6.0 and kept
  in lockstep by a version-sync test + a tag-time `release.yml`; a doctor installed-vs-
  bootstrapped version-delta check; and `RELEASING.md` formalizing the branch → per-item commits
  → CHANGELOG capstone → squash-merge → tag process this file's header used to defer.

## v0.5.0 — 2026-07-07 — "The graph earns its keep: typed relations and closed lifecycle loops"

### Format changes

This release introduces corpus format versioning (COR-7) and bumps it once:

- **Corpus format 1 → 2** (GRA-4) — additive: `supersedes:` / `contradicts:` /
  `refines:` typed relations may now appear in frontmatter alongside untyped
  `[[wikilinks]]`. Existing corpora keep working unchanged; `/hippo:doctor`
  reports the corpus's stamped format vs. what the plugin expects and names
  the exact next step (stamp the marker, no autonomous migration).
- **Index schema 2 → 3** (RET-5) — adds a `source_commit_time` field to each
  manifest entry. COR-7's enforcement means a schema mismatch now costs
  exactly one full rebuild instead of silently serving a stale shape; nothing
  the operator needs to do by hand.

New env vars: `HIPPO_DUP_THRESHOLD` (LIF-2 near-duplicate cosine/BM25
threshold override) and `HIPPO_SALIENCE` (RET-5 salience-fusion ranking
blend — **default off**; the eval numbers on this release's fixtures showed
zero regression but also zero measurable lift, so it ships opt-in rather than
on-by-default until a corpus with real usage/staleness signal can prove it).

### Shipped this release

- **COR-7** — enforces index schema versioning and adds corpus format
  versioning, with a doctor-driven migration surface for both.
- **GRA-4** — typed edges (`supersedes` / `contradicts` / `refines`): recall
  demotes and annotates superseded memories pre-cut, flags contradictions,
  and `lint_links` catches dangling typed targets.
- **GRA-5** — `archive_memory` refuses (without `--force`) when inbound
  links — untyped or typed — still point at the target, and reports the
  referrer list either way.
- **GRA-9** — the reconsolidation worklist grows a report-only 1-hop
  "linked" column so a stale memory's neighbors surface for review too.
- **LIF-1** — demote gets a terminal state: it now chains straight into
  soft-invalidation (no second manual command), and a snooze/ack primitive
  stops re-nagged items from re-appearing every session.
- **LIF-2** — write-time near-duplicate/conflict detection (warn-only):
  `write_memory` surfaces nearest-neighbor matches and the `/hippo:new`
  skill routes the add / update-existing / supersede / skip decision.
- **LIF-3** — citation rot (a renamed/deleted cited file silently dropping
  out of `cited_paths`) is now surfaced instead of vanishing unnoticed.
- **LIF-4** — usage aggregates now survive telemetry-ledger rotation, and
  `archive_candidates` finally enforces its own ≥5-session soak gate.
- **LIF-5** — a missing/renamed `MEMORY.md` floor section is repaired or
  loudly reported — never a silent no-op.
- **LIF-6** — staleness and reconsolidation SessionStart producers share one
  computed stale set, so no memory is reported twice.
- **RET-5** — salience fusion (recency/usage/staleness ranking priors),
  shipped behind `HIPPO_SALIENCE` (default off) per this release's honest
  eval numbers.
- **RET-6** — drifted injections carry a one-line verify-at-use banner;
  reverifying a memory clears it on the next SessionStart.
- **TEA-4** — floor pointers insert at a deterministic sorted position
  instead of always appending at the section tail, so concurrent teammate
  writes to the same floor section merge cleanly instead of colliding.
- **QUA-8** — the skills-contract suite now extracts and checks every fenced
  code block in every `SKILL.md` for real (compiles, resolves every
  `memory.*` reference and call signature against the live package).
- **QUA-10** — `pytest.ini` hardening: `slow` marker, `filterwarnings =
  error` with targeted ignores, a timeout default, and the suite's one
  permanent skip repointed at the shipped operator-pack assets (now a real
  packaging gate — the suite ends at 0 skipped).
- **DOC-6** — `CONVENTIONS.md` seeded into every corpus by `/hippo:init`,
  documenting the frontmatter schema, type taxonomy, floor rule, typed
  relations, and evidence-block convention as actually shipped.

## v0.4.0 — 2026-07-06 — "Recall precision: earn every injected token, in every language"

### Breaking

Every `MEMOBOT_*` environment variable is renamed to `HIPPO_*` (suffix
unchanged). This is a **clean break** — per the one-canonical-name invariant
there are NO alias shims and NO fallback reads of the old prefix. Any
`MEMOBOT_*` var still set in a shell profile, CI secret, or `.env` file is now
silently ignored by every module; `/hippo:doctor` gained a check that warns
(by name) when a stale `MEMOBOT_*` var is present in the environment, but it
will not repair anything for you — rename it yourself.

Rename table (old -> new, suffix identical in every case):

| Old (removed)                    | New (canonical)                |
|-----------------------------------|---------------------------------|
| `MEMOBOT_DISABLE_DENSE`           | `HIPPO_DISABLE_DENSE`           |
| `MEMOBOT_TRUST_ALL`               | `HIPPO_TRUST_ALL`               |
| `MEMOBOT_TELEMETRY_DIR`           | `HIPPO_TELEMETRY_DIR`           |
| `MEMOBOT_MEMORY_DIR`              | `HIPPO_MEMORY_DIR`              |
| `MEMOBOT_TRUST_FILE`              | `HIPPO_TRUST_FILE`              |
| `MEMOBOT_TELEMETRY_MAX_BYTES`     | `HIPPO_TELEMETRY_MAX_BYTES`     |
| `MEMOBOT_INDEX_DIR`               | `HIPPO_INDEX_DIR`               |
| `MEMOBOT_EMBED_MODEL`             | `HIPPO_EMBED_MODEL`             |
| `MEMOBOT_RECENT_DAYS`             | `HIPPO_RECENT_DAYS`             |
| `MEMOBOT_DENSE_TIMEOUT`           | `HIPPO_DENSE_TIMEOUT`           |
| `MEMOBOT_REFRESH_TIMEOUT`         | `HIPPO_REFRESH_TIMEOUT`         |
| `MEMOBOT_EMBED_CHUNK_SIZE`        | `HIPPO_EMBED_CHUNK_SIZE`        |
| `MEMOBOT_GRAPH_SEEDS`             | `HIPPO_GRAPH_SEEDS`             |
| `MEMOBOT_BODY_RRF_WEIGHT`         | `HIPPO_BODY_RRF_WEIGHT`         |
| `MEMOBOT_DENSE_FLOOR`             | `HIPPO_DENSE_FLOOR`             |
| `MEMOBOT_KNEE_RATIO`              | `HIPPO_KNEE_RATIO`              |

Unrelated vars are untouched: `FASTEMBED_CACHE_PATH`, `HF_HUB_OFFLINE`,
`TRANSFORMERS_OFFLINE`, `XDG_CACHE_HOME`, and every `CLAUDE_*` var belong to
other systems and keep their names. `plugin.json` / `marketplace.json`
version numbers are intentionally NOT bumped in this release — that sync is
DOC-7's job (v0.6.0).

### Shipped this release

- **RET-1** — relevance floor + knee cutoff, so a low-signal query surfaces
  nothing rather than padding out to a fixed count.
- **RET-2** — body-aware indexing; recall is no longer capped at the
  `description:` field's discipline.
- **RET-3** — Unicode-aware tokenization plus an opt-in `--multilingual`
  bootstrap preset for non-English corpora.
- **RET-4** — mines fence/traceback identifiers instead of stripping them.
- **RET-7** — records the serving backend and audits the eval-set generation
  phase.
- **GRA-1** — 1-hop neighbor expansion in recall: the graph's first
  load-bearing ranking read.
- **GRA-2** — stem-normalized `LinkGraph` identity plus `inbound()` /
  `isolates()` backlink primitives.
- **GRA-3** — bootstraps the link graph at write time.
- **GRA-6** — persists the resolved edge list in the index (`links.json`).
- **PRF-1** — persists BM25 statistics; stops rebuilding the scorer per query.
- **PRF-2** — promotes `cold_latency` from report-only to a gated check.
- **COR-8** — emits true fused scores and cross-checks index vs. query
  embedding model.
- **COR-9** — makes soft-alias collisions ambiguous instead of
  first-claimant-wins.
- **QUA-6** — pins gate constants and adds a golden-corpus dense benchmark.
- **QUA-7** — adds subprocess tests for the `bin/hippo` launcher.
- **DOC-8** — this rename, plus a doctor check for stale `MEMOBOT_*` vars.

## Earlier releases

Pre-v0.4.0 releases (v0.2.0 "Truthful snap-in", v0.3.0 "Any repo, any
machine") predate this file — see `ROADMAP.yaml`'s `release_train` section
and the corresponding merged PRs (#3, #4) for their shipped-item lists.
