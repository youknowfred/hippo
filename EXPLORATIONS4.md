# hippo — Enhancement & Capability Explorations, Round 4

**Status: DRAFT idea catalog for owner review.** Round-4 addendum to
[`EXPLORATIONS.md`](EXPLORATIONS.md) (round 1 → T1–T7, shipped),
[`EXPLORATIONS2.md`](EXPLORATIONS2.md) (round 2 → T8–T13, still proposed), and
[`EXPLORATIONS3.md`](EXPLORATIONS3.md) (round 3 → T14–T17, shipped and released
as v1.18.0/v1.19.0). This is **research and proposal only** — no implementation,
no status flips on existing roadmap files. Its executable companion is
[`ROADMAP.enhancements4.yaml`](ROADMAP.enhancements4.yaml) (tiers **T18–T21**,
namespaces **FLT/HYG/PUB/EVD**). Authored 2026-07-16.

Method — different from round 2's 158-agent fan-out and from round 3's QA-sweep
grounding, and honest about it: **this round's grounding pass was the round-3
shipping week itself.** Four tiers shipped by three-plus concurrent sessions on
one machine, every new surface got its first real dogfood the same week (the
sleep schedule installed live, JIT latency measured at the gate, `--for-diff`
lit up by the curated-subset commit, `promote_scan`'s first real run), and the
machine pushed back in citable ways: three documented shared-clone collisions in
one day, a corrupted projects registry that had silently hidden this repo from
cross-project mining for two days, telemetry lanes recording evidence nothing
reads yet. Where round 3 asked "what did fixing hippo teach us it needs," round
4 asks **"what did *shipping and living with* round 3 teach us it needs."**
Every fact below is verifiable in a public PR, a committed capstone memory, or a
telemetry report reproducible on this machine.

---

## 0. TL;DR

Round 3 gave hippo self-enforcing invariants, a maintenance cadence, the
point-of-action moment, and reach beyond the session. Living with all four for
one intensive week exposed three assumptions the engine still makes:

**hippo assumes one session, one audience, and write-only evidence.** The
machine is multi-session (three same-clone collisions on 2026-07-16 alone, a
fourth near-miss the same day — sessions coordinate by reflog forensics today);
the corpus is now multi-audience (a public 15-file subset rides in git while 26
memories stay local — a posture with exactly zero tooling); and the new
evidence lanes (touch-grain rows, interview declines, the registry census) are
ledgers nothing consumes. Four tiers follow:

1. **T18 FLT — the fleet lane.** Sessions sharing a clone become visible to
   each other: a bounded presence file, a moved-under-me tripwire, and
   worktree-first guidance at the first mutating act. Detection and one-line
   nudges only — no locking, no daemon, no coordination protocol.
2. **T19 HYG — machine-state lifecycle.** RCH-11 (PR #65) gave the projects
   registry a census and a safe prune; the same rot class covers the trust
   registry, orphaned derived dirs, and installed scheduler artifacts whose
   venv moved. One machine census, per-item remedies, doctor/sleep surfacing.
3. **T20 PUB — the publish lane.** The curated-subset posture ratified
   2026-07-16 (PR #67) is currently hand tooling: two scans, an eyeball, and a
   `git add -f` per file. Give it a per-item verb, a publishable-candidates
   report (the encode-side twin of EXT-1), and subset-boundary link honesty.
4. **T21 EVD — evidence consumers.** The reverify brief (precomputed per-item
   evidence for the chronic reconsolidation queue), a touch-grain trend
   surface, decline-aware interviewing, and the owner-gated salience-revisit
   evidence run ED-2 has always named as the revisit vehicle.

Judged against the standing commissioning bar (solo/small-team value,
consent/trust fit, cost, dogfoodability): every item is dogfoodable on this
repo or this machine today; nothing weakens per-item consent; the four
posture-adjacent questions are pulled out as §4 owner decisions rather than
buried in acceptance criteria. All four tiers are detection-first (ED-1) and
none touches default ranking (ED-2).

---

## 1. What the shipping week established (the re-baseline)

Facts the proposals below stand on, each verifiable:

- **Concurrent sessions are the normal case on this machine, and they collide.**
  2026-07-16 alone: a release session's checkout moved a working tree mid-tier,
  T16's branch pointer was repositioned by a concurrent release (23 baselines
  orphaned, "cost real cleanup" per its capstone), PR #62's author built in a
  worktree specifically because "branching in place would have yanked [the T17
  session's] working tree — the third shared-clone collision today," and the
  v1.19.0 release was nearly cut twice when a follow-up session branched during
  the same 90 seconds the release session was committing. Sessions today
  discover each other by accident: reflog forensics, session-registry reads,
  luck. The one mitigation that worked every time — a git worktree — is
  documented only in PR notes.
- **Machine-local state rots without a lifecycle, and the rot has real cost.**
  RCH-11's census of this machine's real registry: 16 entries — 2 healthy, 3
  dead tmp rows, 10 live-but-temp-rooted test registrations, and 1 corrupted
  row that had pointed THIS repo's `memory_dir` into a dead pytest tmp dir
  since 2026-07-14, silently hiding the corpus from `--all-projects` and
  `promote_scan` for two days. The same never-pruned, `~/.claude`-resident
  file shape backs the SEC-1 trust registry; the same moved-path failure class
  is documented (but only documented) for SLP-2's installed schedules.
- **The corpus is now two-audience, with zero tooling.** PR #67 committed a
  15-file reviewed subset (both scan modes clean) while 26 memories stay
  local-only; the split was executed entirely by hand. Measured on the round-3
  diff range: the full corpus would render 14 memories in EXT-1's comment, the
  committed subset renders 8. The comment lane exercised its empty norm on PRs
  #64/#65 and goes live on the next engine PR.
- **The new evidence lanes are write-only today.** Touch-grain baseline this
  morning: session grain 28 memories / 125 hit-sessions vs touch grain 0 / 0
  (recording began at T16's merge). The interview decline ledger persists
  declines but only dedups — it never re-shapes what gets generated. DRM-6
  graduation consumes session-grain `injection_hits` only.
- **The maintenance queues are chronic, not transient.** After a week of
  active per-item drains, this repo woke today to a 20-item reconsolidation
  worklist, 3 pending captures, and 2 unresolved contradiction pairs. The
  bottleneck is not discovery (sleep reports all of it) — it is that each
  human verdict requires hand-gathering the evidence (what changed in the
  cited files since the baseline?), which today costs minutes per item.
- **The prose-facts-rot class keeps paying out.** DOC-15 (v1.19.0) found
  STABILITY.md misstating its own frozen numbers for eight releases; DOC-16
  (PR #66, merged today) turned those claims into lint-pinned facts. The
  pattern — a stated fact drifts silently until a lint pins it — is the same
  one INV-1/INV-2 shipped for surfaces and writes, and it generalizes.

## 2. The catalog (summary — the YAML is normative)

| Tier | Items | One-line pitch |
|---|---|---|
| T18 FLT | FLT-1 session presence · FLT-2 moved-under-me tripwire · FLT-3 worktree-first at the act | Concurrent sessions see each other; the fourth collision doesn't happen |
| T19 HYG | HYG-1 machine census · HYG-2 per-item remedies · HYG-3 doctor/sleep surfacing | RCH-11's lifecycle discipline, machine-wide |
| T20 PUB | PUB-1 publish verb · PUB-2 publishable-candidates report · PUB-3 subset-boundary link honesty | The two-audience corpus gets a lane instead of hand tooling |
| T21 EVD | EVD-1 reverify brief · EVD-2 touch-grain trend · EVD-3 decline-aware asking · EVD-4 salience-revisit evidence run (owner-gated) | The write-only ledgers get readers; the chronic queue gets cheap verdicts |

Sequencing note: no cross-tier dependencies — all four tiers can run in
parallel if ratified together. Within tiers: FLT-3 reuses FLT-1's presence
read and T16's touch lane; HYG-1 reuses RCH-11's census machinery; EVD-2/EVD-4
read the same touch-grain rows. EVD-1 and FLT-1 are the two highest-value
starts and share nothing.

## 3. Considered and cut

- **Executing or refreshing round 2 (T8–T13)** — those 29 items remain
  PROPOSED and owner-owned; round 4 deliberately avoids their territory (MSR
  measurement spine, CLB team review packets, IOP interop). Whether to execute
  T8 next is a scheduling decision for Fred, not a new proposal — re-proposing
  it here would just fork the catalog.
- **A STABILITY.md facts lint** — was on this round's candidate list from the
  DOC-15 experience; shipped out from under the draft as DOC-16 (PR #66,
  merged 2026-07-16) while this document was being written. Cited in §1 as
  evidence the class generalizes; nothing left to propose.
- **Locking / a coordination daemon for the fleet lane** — hippo is
  files-and-git; a lock service or daemon is a posture break and a new failure
  mode. The presence FILE plus one-line nudges captures the observed value
  (sessions didn't know about each other) without inventing mutual exclusion
  nobody asked for.
- **Auto-drain for the reconsolidation queue** (auto-graduate when the cited
  diff "looks harmless") — autonomy creep against LIF-1's human-verdict
  contract, and the grep-still-matches heuristic is exactly the
  plausible-but-wrong trap. EVD-1 ships the evidence brief instead; the
  verdict stays human.
- **LLM-judged anything on the new lanes** — round 2's MSR thesis
  (deterministic beats judge) has now survived three rounds; nothing here
  needs a judge.

## 4. Owner decisions this round surfaces — **all four PENDING**

1. **FLT-1 presence artifact**: may hippo write a per-clone presence/heartbeat
   file (new derived machine state — gitignored, bounded, self-aging, no
   content beyond session id / branch / timestamps)? Recommendation: yes —
   it is the same class as the episode buffer, and the collision record is
   the cost of not having it.
2. **HYG-2 trust-registry hygiene**: the trust file is the CONSENT ledger.
   May a hygiene verb remove rows whose repos vanished (per-item,
   temp-rooted-only, RCH-11's exact discipline), or does the consent ledger
   stay append-only with report-only surfacing? Recommendation: report-only
   now; revisit with census data after HYG-1 ships.
3. **PUB-1 staging semantics**: may the publish verb run `git add -f` itself
   on an explicit confirm flag (a VCS-index write), or does it print the
   command and stop (SLP-2's print-only posture)? Recommendation: print-only
   first; the staging flag can ship later without a contract break.
4. **EVD-4 the salience-revisit evidence run**: commission the report-only
   A/B (touch-grain-informed prior vs baseline, lived-in corpus, existing
   eval substrate)? This is exactly the dated revisit vehicle ED-2 named when
   salience was decided OFF (2026-07-09). Ships no default change either way.
   Recommendation: yes — evidence first, decision later, same as always.

## 5. Ranked top moves

1. **EVD-1** (the reverify brief) — attacks the one chronic, measured pain
   (20-item worklist, minutes per verdict) with zero autonomy change; every
   ingredient (baselines, hunk machinery, worklist producer) already exists.
2. **FLT-1** (session presence) — the collision class hit four times in one
   day on this machine; one bounded file and one producer line end the
   flying-blind default.
3. **PUB-1 + PUB-2** (the publish lane) — completes the posture ratified
   2026-07-16 into tooling, and PUB-2 is the encode-side twin that makes
   EXT-1's comment compound: the diff names what should be published next.
4. **HYG-1** (machine census) — the RCH-11 class, machine-wide, before the
   trust registry accumulates its own two-day-invisible corruption.
5. **EVD-4** (salience-revisit evidence) — cheapest honest step toward the
   one standing decided-OFF capability, on the substrate built for exactly
   this moment.
