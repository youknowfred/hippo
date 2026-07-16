# hippo — Enhancement & Capability Explorations, Round 3

**Status: DRAFT idea catalog for owner review.** Round-3 addendum to
[`EXPLORATIONS.md`](EXPLORATIONS.md) (round 1 → `ROADMAP.enhancements.yaml`,
T1–T7, shipped) and [`EXPLORATIONS2.md`](EXPLORATIONS2.md) (round 2 →
`ROADMAP.enhancements2.yaml`, T8–T13, draft). This is **research and proposal
only** — no implementation, no status flips on existing roadmap files. Its
executable companion is [`ROADMAP.enhancements3.yaml`](ROADMAP.enhancements3.yaml)
(tiers **T14–T17**, namespaces **INV/SLP/JIT/EXT**). Authored 2026-07-16.

Method — deliberately different from round 2's 158-agent fan-out, and honest
about it: **this round's grounding pass was the 2026-07-16 first-party QA
sweep itself.** Six read-only sweep maps covered the whole engine (frontmatter
discipline, write-path atomicity/interruption, batch-refusal + exception +
envelope hygiene, cross-surface parity, PyYAML↔miniyaml parity, and a
concurrent-writer/lock map), and **thirteen defects were reproduced and fixed**
(COR-14..19, SEC-18/19, INT-17..19, RCH-8/9 — each with a failing test first).
A defect a sweep can reproduce is the strongest grounding a proposal can have:
every tier below generalizes a class the sweep fixed *by hand* into a
capability that prevents the class, schedules the hand-work, or extends what
the hand-work surfaced. Where round 2 asked "what could hippo become," round 3
asks "what did fixing hippo just teach us it needs."

---

## 0. TL;DR

Round 1 activated the dark signals; round 2 proposed making hippo measurable,
defensible, and team-ready. Round 3's thesis, after a full defect pass over
the tree:

**hippo's invariants, cadence, and reach are still manual.** The invariants
live in prose and review-memory (eleven of thirteen fixed defects violated a
rule the project had already written down); the maintenance loops run only
when the human remembers (this repo woke to 21 stale memories and 11 pending
captures); and everything hippo knows surfaces only inside a Claude session,
at prompt-shaped moments. Four tiers follow:

1. **T14 INV — self-enforcing invariants.** The COR-9 walk rule, the atomic
   write rule, and the every-verb-on-both-surfaces rule become CI lints and a
   fault-injection harness; the two dead-end nudges (resolve, audit) get their
   second-surface tools. The INT-13/14/15/16/17/18/19 recurrence is the
   evidence that patching instances doesn't end a class — only a build-time
   check does.
2. **T15 SLP — scheduled sleep.** One headless runner renders the existing
   read-only worklists into a morning report on a schedule the user installs
   explicitly. Zero corpus writes by default; the one autonomy question
   (Tier-A dream edges overnight, reversible by the existing contract) is an
   opt-in flag flagged for owner decision.
3. **T16 JIT — point-of-action recall.** The first edit to a file cited by a
   `steer:pin`/feedback memory gets one bounded reminder line — procedural
   memory firing at the act, not the plan. First-touch-only, type-scoped,
   capped, no LLM; plus touch-grain outcome evidence sharpening graduation.
4. **T17 EXT — memory beyond the session.** Recall on the PR diff (a sticky
   CI comment; the first hippo surface a Claude-less teammate benefits from),
   cross-project promotion mining over the machine's registered corpora
   (report-only, trusted-only), and an interview step in consolidate that
   turns detected gaps into at-most-three grounded questions.

Judged against the commissioning bar (solo/small-team value, consent/trust
fit, cost, dogfoodability): every item is dogfoodable on this repo or this
machine today; nothing weakens per-item consent — the two posture-adjacent
items (SLP-3 autonomy, JIT-1 default-on) are explicitly flagged as owner
decisions rather than buried in acceptance criteria.

---

## 1. What the QA sweep established (the re-baseline)

Facts the proposals below stand on, each verifiable in the sweep's fix PR:

- **The INT class recurs on schedule.** Four consecutive releases patched the
  same shape (a verb/nudge shipping terminal-first and dead-ending Desktop:
  INT-13 consolidate, INT-14/15 repair verbs, INT-16 pack, INT-17/18/19 this
  round). No artifact declares a verb's surface story, so nothing can check
  one. Seven verbs remain terminal-only *by intent* — intent that today lives
  in seven separately-worded preflights.
- **The write-discipline classes were found by grep, not by design.** The
  sixth and seventh ad-hoc frontmatter walks (COR-14) and eleven plain
  `open("w")` corpus writers (COR-18) existed *after* the shared primitives
  shipped. The primitives win only when a check makes bypassing them loud.
- **Crash-safety is now uniform enough to state as a contract.** After
  SEC-19/COR-17/COR-18, every irreplaceable file writes atomically and the
  known two-write chains roll back (COR-16). That's a testable, publishable
  guarantee — currently proven only at the sites the sweep happened to test.
- **The maintenance debt is visible and quantified.** The repo's own
  SessionStart shows the queues a scheduled sleep would drain; the capture
  queue's own nudge/list mismatch (RCH-9) shows what silent maintenance debt
  does.
- **The recall moments are all prompt-shaped.** SIG-1's `relevant_to_work`
  fires at SessionStart; injection fires at UserPromptSubmit; nothing fires at
  the *edit*. Meanwhile the reverse index (cited_paths → memories) that a
  point-of-action lane needs already exists in the derived caches.
- **Reach stops at the session boundary.** `recall --json`, the projects
  registry, and the SIG-3 abstention backlog are all substrate for surfaces
  (PR comments, cross-project mining, elicitation) that need no new retrieval
  machinery — only plumbing and posture decisions.

## 2. The catalog (summary — the YAML is normative)

| Tier | Items | One-line pitch |
|---|---|---|
| T14 INV | INV-1 verb-surface registry + parity lint · INV-2 write-discipline lint · INV-3 crash-fault harness + published contract · INV-4 resolve/audit tools | Prose invariants become build failures; the two live dead-end nudges get real routes |
| T15 SLP | SLP-1 sleep runner + morning report · SLP-2 scheduler recipes + snooze · SLP-3 Tier-A-in-sleep (opt-in) | The maintenance loops get a cadence; consent posture unchanged by default |
| T16 JIT | JIT-1 first-touch reminder · JIT-2 touch-grain outcome evidence | Procedural memory fires at the action; graduation evidence sharpens |
| T17 EXT | EXT-1 recall on the PR diff · EXT-2 cross-project promotion mining · EXT-3 the interview loop | The corpus reaches review, sibling repos, and the gaps nobody writes down |

Sequencing note: INV-1 is the keystone — SLP-1's report, INT-class honesty,
and EXT-1's rendered verbs all consume the registry. Nothing else in T15–T17
depends on T14, so tiers can run in parallel if ratified together.

## 3. Considered and cut

- **Git merge driver for corpus files / lockfile** — adjacent to CLB-4's
  incoming-merge digest (round 2); a custom merge driver additionally requires
  per-clone gitattributes setup, which fails hippo's zero-setup-per-clone
  posture. Revisit only if CLB ships and conflict pain is still observed.
- **Automatic recall into subagents** — the harness owns subagent spawn;
  hippo already exposes `hippo://floor` as the agent-pulled channel. Anything
  more is a harness feature request, not a hippo capability.
- **Fixing the latent miniyaml↔PyYAML value differences** (YAML-1.1 booleans,
  octal/sexagesimal ints, special floats) — real divergences, zero consumers
  today (COR-19 fixed the consumed ones). Pinned as documentation in the QA
  report's open questions instead; code changes there would be risk without a
  reader.
- **A remote pack registry / federation** — IOP territory and a v2-scale
  posture question (hosting, trust, moderation). The pack spine is
  deliberately files-and-git; nothing observed this round argues for more.
- **LLM-judged memory quality scoring** — round 2's MSR thesis (deterministic
  beats judge) still holds; nothing here needs a judge.

## 4. Owner decisions this round surfaces — **all four RATIFIED 2026-07-16**

1. **SLP-3 autonomy**: may a *scheduled* pass apply reversible Tier-A dream
   edges under the existing undo/aging contract, opt-in, capped, undo-first in
   the report? (Default ships OFF either way; the decision is whether the flag
   exists.)
   **→ RATIFIED: ship the opt-in flag** (default OFF; per-pass cap; the
   morning report leads with the undo line).
2. **JIT-1 default**: point-of-action reminders default-on with empty-norm +
   kill switch, or opt-in? Default-on is the recommendation (the lane is
   silent on most edits by construction), but it's a new always-armed hook
   lane and deserves an explicit yes.
   **→ RATIFIED: default-on with an env kill switch** — the empty-norm design
   carries the restraint.
3. **EXT-1 positioning**: the PR comment is hippo's first surface visible to
   people who never run Claude — that's a marketing statement as much as a
   feature. Ship quietly on this repo first, or launch as the team-adoption
   wedge?
   **→ RATIFIED: quiet dogfood on the hippo repo first**; the marketing story
   waits for real examples.
4. **INV-4 scope**: tools for exactly the two nudge-routed verbs now
   (recommended), or the full seven terminal-only verbs in one wave?
   **→ RATIFIED: resolve + audit only** — the other five keep their honest
   terminal-only preflights until field reports ask.

## 5. Ranked top moves

1. **INV-1** (registry + parity lint) — kills the most-recurrent defect class
   in the project's history; every other tier's texts consume it.
2. **EXT-1** (recall on the PR diff) — highest new-user-value per line of
   code; read-only; dogfoodable the day it merges.
3. **SLP-1** (sleep runner + morning report) — converts the observed
   maintenance debt into a drainable artifact without touching consent.
4. **INV-2/INV-3** (write lint + crash harness) — cheap insurance that the
   COR-14..18 work stays fixed; publishable contract.
5. **JIT-1** (first-touch reminder) — the biomimetic story's missing moment,
   and the sharpest daily-felt UX win, gated on the latency measurement.
