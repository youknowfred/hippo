# hippo — Road to v2.0.0, amendment 1: the estate evaluation

**Status: PROPOSED 2026-11-12. Nothing here is ratified.** This is a companion to
[`ROADMAP.v2.md`](ROADMAP.v2.md) (ratified 2026-10-03), in the same shape: ground truth →
corrections → items → train → rulings → KPIs. It does not edit the ratified document.
[`ROADMAP.v2.yaml`](ROADMAP.v2.yaml) carries its rows under `amendment_1`, with status
`proposed` until the owner rules (§A6). Authored at **v1.42.8** (`b85a907`).

**Why an amendment.** Six weeks after ratification the first three releases of the train have
run on a production estate: five corpora owned by one person, the largest ("emgl") at 1,068
memories and about 250 human prompts a day. On 2026-11-12 a session rooted in that estate
evaluated how hippo is integrated there and handed over its findings. This document re-verifies
the numbers it leans on, sorts every finding against the chartered train, and proposes what the
train is missing.

**Method.** One session, inline, with three subagents (one read-only status sweep of the engine,
two builders for chartered items). Every number marked **✓** was recomputed on 2026-11-12 from the
ledgers under each corpus's `.hippo/telemetry/`, from `hippo census`, or from the corpus at its
default branch (`git show origin/<default>:…`, because primary checkouts lag). Numbers marked
*(handed over)* come from the estate session and were not recomputed. Per the public-repo rule,
estate evidence appears as counts and mechanisms only.

---

## A0. TL;DR

**The engine is selective about when to inject and not about what.** Machine turns now inject
nothing and 8.4% of human prompts abstain, both as chartered. But what gets injected is a mirror
of the corpus: history outnumbers behavior-changing memories four to one, and almost none of it
is opened afterwards.

- **K2 is met.** 0 chars injected on machine turns in 30 days on emgl (8,527 machine prompts of
  16,118) ✓.
- **K4 went the wrong way.** Injection precision on emgl is **0.75%** (77 of 10,269) ✓ against a
  1.7% baseline and a 5% target.
- **A direct usefulness signal exists and reads lower still.** The hook tells the agent to read a
  memory's file before relying on it, and PostToolUse logs Reads. Built as OBS-10 in this
  amendment's first PR and measured on three corpora ✓: emgl **11 of 10,986 (0.10%)**, Skyline
  **4 of 7,708 (0.05%)**, hippo **125 of 6,563 (1.90%)**. Feedback memories are essentially never
  opened (0 of 3,650; 0 of 911; 2 of 455).
- **The write path is the estate's largest hippo cost.** 363 of 1,468 commits on emgl's default
  branch in 30 days touched the corpus (24.7%) ✓. The estate built a lane script, a sync script
  and a save-then-ship rule around it; the other four corpora have no ship path, and 4 of 7
  registered corpora on the machine are withholding memories after pulls ✓.
- **Knowledge about the owner is trapped in one corpus.** emgl holds 173 feedback and 10 user
  memories; the siblings hold 14, 8, 9 and 3 ✓. Nothing promotes, and nothing recalls across.
- **About 60 KB of estate code enforces what hippo warns about.** A write-time floor gate, a
  seven-assertion floor checker and a pre-commit hook, vendored into two siblings and absent from
  two.

**What this amendment proposes.** Thirty-one new items: 14 S, 13 M, 4 L. Twenty-five ship on
the road to v2.0.0 (six small ones in v1.43.0, nineteen in a proposed v1.44.0) and six in v2.x.
The ratified thesis stands (earned context, derived maintenance, a smaller contract), and
so do the invariants. Two chartered items change design (SRF-7, NAT-2), two v2.x threads are
proposed for pull-forward (LDG-1, GP-S4), and the train gains one minor so that v2.0.0 stays the
subtraction release (ruling V2Q-13).

---

## A1. Ground truth, re-verified 2026-11-12

| Signal | Number | Receipt |
|---|---|---|
| Hook volume, emgl, 30 days | 16,118 prompts; 7,591 human; 6,947 injected; 636 abstained (8.4% of human); 8 skipped | ✓ `daily_rollups.jsonl`, 30 rows |
| Machine-turn injection (K2) | 0 chars; `injected_by_trigger` holds `human` only | ✓ same |
| Injected volume | 12,210,991 chars; 1,758 per injecting prompt; median 10 memories per injecting prompt | ✓ rollups; ✓ `recall_events.jsonl` (502 rows, 39.4 hours) |
| Backend mix | dense+bm25 6,288 · BM25-only 659 (9.5% of injecting recalls) · none 636 | ✓ rollups |
| Injection precision (K4) | emgl 77 / 10,269 (0.75%); Skyline 45 / 3,282 (1.4%); hippo 264 / 4,164 (6.3%) | ✓ `outcome.injection_precision` on ledger copies |
| Opened after recall (new, OBS-10) | emgl 11 / 10,986 (0.10%); Skyline 4 / 7,708 (0.05%); hippo 125 / 6,563 (1.90%) | ✓ `outcome_opened.opened_after_recall` on ledger copies |
| …by type, emgl | project 11 / 5,459; feedback 0 / 3,650; fold digests 0 / 361; reference 0 / 276; user 0 / 126; no longer in the corpus 0 / 1,114 | ✓ same |
| Memory opens that recall had named first | emgl 11 of 24; Skyline 6 of 11; hippo 135 of 189 | ✓ same |
| Injected mix vs corpus mix, emgl raw window | injected: project 51.8%, round journals 21.9%, feedback 15.5%, fold digests 5.0%, reference 3.5%, user 0.6%. Corpus: project 49.0%, round journals 21.3%, feedback 16.1%, fold digests 9.0%, reference 3.6%, user 0.9% | ✓ 4,081 injected slots over 502 events, joined to frontmatter at the default branch |
| Reach | 816 of 1,068 memories injected at least once in 39.4 hours | ✓ `recall_events.jsonl` |
| Drop autopsy, same window | dense_floor 1,491 · old_state 1,337 · floor_collapsed 1,329 · knee_cliff 1,320 · display_overflow 1,167 · cooldown_collapsed 962 · mmr_displaced 138 | ✓ same |
| Floor-sweep calibration | generated 2026-08-17 at a different corpus fingerprint; recommended 0.6793 against a configured 0.68; on/off distributions overlap | ✓ `floor_sweep.json` |
| Raw recall retention | 502 rows, 39.4 hours, bounded at 2 MB | ✓ file; `telemetry_store.py:30` |
| Corpus share of default-branch commits, 30 days | emgl 363 / 1,468 (24.7%); Skyline 22 / 183; two smaller siblings 4 / 115 and 30 / 111 | ✓ `git rev-list --count` |
| `new_memory` calls, emgl, 30 days | 1,008 | ✓ rollups, `mcp:new_memory` |
| Primary checkout lag | Skyline's primary is 113 commits behind its default branch, with 14 uncommitted corpus paths | ✓ `git rev-list`, `git status` |
| Trust withholding | 4 of 7 registered corpora: Skyline 3 changed / 10 added; two smaller siblings 7 / 19 and 6 / 35; hippo 0 / 1 | ✓ `hippo census` |
| Behavior-changing memories per corpus | emgl 173 feedback + 10 user; Skyline 14 + 1; smaller siblings 8 + 0 and 9 + 0; umbrella 3 + 0 | ✓ frontmatter at the default branch |
| Cross-corpus mentions | siblings name emgl in 67, 40 and 20 memory files; emgl names Skyline in 245 and the third sibling in 189 | ✓ `git grep -l -i` (case-insensitive, so higher than the handed-over 31 / 29 / 15 and 175 / 153) |
| Journals, emgl | 227 round journals (a filename convention) + 96 fold digests; digests total 4.42 MB with 1,298 `###` headings; largest 146 KB | ✓ default branch |
| Status tokens in memory bodies, emgl | 661 of 1,066 bodies match the corpus's own floor `banned_re` | ✓ (the handed-over 440 used a narrower pattern) |
| Capture queue, emgl, 30 days | 781 captured · 81 drafted · 225 discarded · 477 expired | ✓ rollups |
| Floor size | emgl 22,134 B / 178 lines / 5 sections; third sibling 17,418 B / 119 lines / 0 sections; Skyline 8,780 B / 48 lines | ✓ default branch |
| Descriptions vs the floor line | emgl median description 269 chars; 418 of 1,066 exceed the corpus's 300-char floor line | ✓ frontmatter |
| `CONVENTIONS.md` variants | 3 across 5 corpora (emgl = Skyline; two siblings = the plugin's asset; the umbrella differs) | ✓ content hashes |
| Native-stamped files, emgl | 260 carry `modified`, 281 `originSessionId`; 134 of 1,066 carry `last_verified` | ✓ frontmatter keys |
| SessionStart, emgl, 30 days | 857 runs; 305 at ≥98% of the 9,000 cap; `git_recent`, `rules_rot`, `staleness` dropped in all 857; user-tier floor median 2,997 chars | ✓ rollups; ✓ `injection_producers.jsonl` |
| Explicit recall | `mcp:recall` 195 calls against 16,118 prompts | ✓ rollups |
| Dream linker | completion edges are exempt from the cofire threshold, so an edge can apply at cofire 0.00 (56 of 103 applied edges *(handed over)*) | ✓ mechanism, `dream_config.py:243-265` |
| User tier | about 27 memories, not a git repository | *(handed over; outside this session's read boundary)*; #158 |
| Stray native directory | a real `memory` directory under the harness's projects dir, holding native memories no corpus received; census skips anything that is not a symlink | *(handed over)*; ✓ mechanism, `machine_census.py:104` |

**What the numbers say together.** HOT-1, HOT-2 and HOT-3 did what they were chartered to do:
recall decides *whether* to speak and *how much*. Nothing decides *which kind* of memory earns the
slot. The injected mix tracks the corpus mix to within four points in every class, so a corpus
that is 30% journals injects 27% journals. On the write side, a memory is a file in a shared
working tree, so each save is a commit someone has to land, and each landing on one clone is a
re-consent on every other.

---

## A2. Corrections to the hand-over

The hand-over was accurate where it could be checked. Five places change the plan.

| Handed-over claim or proposal | What the tree and the receipts say | Effect |
|---|---|---|
| "Recalled memory was then opened" is a better precision numerator, and doctor should gate on it | The signal reads 0.05–1.9%, an order of magnitude under K4, and **0 for feedback memories on two of three corpora**. A feedback memory does its work through its one-line description; nobody opens it. Reads through the harness's native memory path were also being dropped (the path resolves into the corpus from outside the repository). | OBS-10 ships the instrument with that path fixed, report-only. It becomes K13, a gauge for **fact** memories. The behavior lane is judged by K14 (its share of what is injected), not by opens. No gate threshold is set until the fixed instrument has 14 days (V2Q-15). |
| The index should read the default-branch corpus instead of the working tree | Every reader walks `memory_dir` with `os.listdir` (`provenance.py:732`); staleness, links, lint, tend, review and trust all take a directory. Reading a git ref live would touch most of the engine. Hooks also may not fetch (no network), so "the default branch" can only mean the local remote-tracking ref. | OUT-3 materializes that ref into a hippo-owned directory, off the hot path, and readers keep taking a directory. It ships in v2.x, after OUT-1/OUT-2 have field time. |
| Pulls of the owner's own commits should not trigger a withholding | Consent is never inferred from git authorship (TND-6, ratified; authorship is spoofable and is the quarantine boundary). OBS-6 already extends a linked worktree's hippo write to the main tree's record. | OUT-4 carries consent by **content hash**: bytes hippo wrote on this machine are consented wherever they arrive, on any registered corpus. Hand edits and native-path edits still withhold. It starts with an OBS-6-style classification of the three withholding corpora (V2Q-17). |
| A "resolved" state that retires memories about fixed bugs | The state exists: `demote` sets `invalid_after`, which halves the score and hides the memory after 30 days (`recall_graph.py:14-18`). What is missing is the trigger, because project memories are exempt from arming. | No new state. LDG-2 (already named in `LDG-1.spec.md` §4) arms on a ledger id reaching a terminal status. It is chartered here as the trigger. |
| Raw recall retention should grow toward K10 | K10 is defined on the daily rollups, which hold 40 days and cap at 365. Raw rows carry prompt previews and are bounded on purpose. | The rollups gain the counters the new gates need (opened-after-recall in OBS-10, per-kind counts in HOT-7). Raw retention stays at 2 MB. |

One platform fact was established for this amendment, against the official hooks reference fetched
2026-11-12:

- **`SubagentStart` exists and can add context to the subagent**, "at the start of its
  conversation, before its first prompt". Its input carries `agent_id` and `agent_type` only,
  **not the task prompt**. A subagent recall therefore has no query unless a `PreToolUse` hook on
  the Agent tool supplies one. The docs also say the main conversation's auto memory is not loaded
  into subagents, which contradicts `PLATFORM.md` §3's observation on 2.1.286 that the floor
  reached a general-purpose subagent under both channels. PLT-3 re-runs that row before NAT-2
  builds.

---

## A3. Every finding, sorted

Classes: **shipped** · **chartered, unchanged** (built as designed) · **chartered, changed** (the
evidence changes the design) · **new, v2.0.0** (ships on the road to the cut; §A5 names the
release) · **new, v2.x**.

| # | Finding | Class | Where it lands |
|---|---|---|---|
| A | Machine turns inject 0 chars | shipped | HOT-1 (K2 met) |
| A | 8.4% of human prompts abstain | shipped | HOT-2 |
| A | Precision 0.75%, below baseline | new, v2.0.0 | HOT-7, HOT-8, HOT-9 |
| A | Injected mix mirrors the corpus; nothing prefers user/feedback | new, v2.0.0 | HOT-7 |
| A | Median 10 memories per injecting prompt, even on weak signal | new, v2.0.0 | HOT-8 |
| A | One hub rides nearly half of all episodes (#155) | new, v2.0.0 | HOT-9 |
| A | Near-miss cluster at the dense floor; stale sweep (#156) | new, v2.0.0 | HOT-10 |
| A | Recall cannot see inside a fold digest (#157) | new, v2.0.0 | HOT-11 |
| A | 9.5% of recalls ran BM25-only, unexplained | new, v2.0.0 | OBS-11 |
| A | Raw recall rows hold 1.6 days | shipped (K10 is on rollups) | OBS-1; counters added by OBS-10, HOT-7 |
| A | Opened-after-recall is unused | new, v2.0.0, **built** | OBS-10 |
| B | 24.7% of commits touch the corpus; a lane script exists for it | new, v2.0.0 | OUT-1, OUT-2 |
| B | Index is built from a lagging primary tree | new, v2.x | OUT-3 |
| B | 4 of 7 corpora withholding after pulls (K7) | new, v2.0.0 | OUT-4 |
| C | The user tier has no git home (#158) | new, v2.0.0 | OWN-1 |
| C | Behavior-changing memories trapped in one corpus | new, v2.0.0 | OWN-2 |
| C | No recall crosses corpora | new, v2.x | OWN-3 |
| C | The same rule drifts between corpora, undetected | new, v2.0.0 | OWN-2 (its evidence view) |
| C | A real native memory directory belongs to no corpus | new, v2.0.0 | NAT-4 |
| D | Journals have no lifecycle; folds are hand-built | new, v2.x | JRN-1, JRN-2 |
| D | The corpus is capped by hand | new, v2.0.0 | GAT-2 (declared budgets) |
| D | Status words in 661 of 1,066 bodies | chartered, changed | LDG-1 pulled forward; GAT-1 body lint |
| D | No join from a tracker id to its memories | chartered, changed | GP-S4 pulled forward |
| D | 477 captures expired unreviewed in 30 days | shipped (TND-5 keeps and counts them) | no change; inflow falls with OUT-1 and NAT-2 |
| E | Floor over hippo's warn line, held by estate code | new, v2.0.0 | GAT-1, GAT-2, NAT-3 |
| E | Floor sections hardcoded to two names | new, v2.0.0 | GAT-3 |
| E | The pointer copies the whole description | new, v2.0.0 | GAT-3 |
| E | Three `CONVENTIONS.md` variants | new, v2.0.0 | GAT-4 |
| F | The harness prompt tells agents to write memory files directly | chartered, changed | NAT-2 |
| F | A CLAUDE.md import would put 22 KB into every spawn | chartered, changed | NAT-3 before NAT-2 |
| F | A live rule-versus-memory conflict doctor reports as none | new, v2.0.0 | RUL-8 |
| F | Rules plane: rotten code references; strength leg waiting on its soak gate | shipped (RUL-1, RUL-2) | no change; the soak count is a lead to re-check under RUL-8 |
| F | SessionStart at its cap in 36% of sessions, calm off | chartered, unchanged | V2Q-8 (calm by default at v2.0); CLM-9 says so now |
| F | Subagents get no recall | new, v2.x | PLT-3 spike now; HOT-12 if it proves out |
| G | A memory states a plugin version that is no longer installed | new, v2.x | TND-12 |
| G | A memory about a long-fixed bug still rides recall | chartered thread | LDG-2 (the trigger; no new state) |
| G | Dream edges applied at cofire 0.00 | chartered, changed | SRF-7 |
| G | `last_verified` on 134 of 1,066 | chartered, unchanged | TND-3 |
| G | Orphaned tmp files; old derived dirs in worktrees | new, v2.0.0 | RWY-6 |
| G | Cleared lint findings cannot be acknowledged | new, v2.0.0 | TND-13 |
| G | Legacy `.format` policy keys; a permission rule naming a removed tool | chartered, unchanged | FMT-4, SRF-8 |

**Chartered and unbuilt, design unchanged** (17): RWY-4, RWY-5, OBS-8, TND-3, TND-4, TND-7,
FMT-2, FMT-4, FMT-5, FMT-6, SRF-5, SRF-6, SRF-8, CON-2 (dated table), CON-3, CON-5, CON-6.
CON-3 keeps its design and gains surfaces to classify (the outbox, `ship`, `gate`, and the new
`hippo.json` keys). RWY-4, RWY-5 and the CI half of CON-5 are built alongside this amendment,
each as its own pull request.

---

## A4. Items

Sizes as in the ratified document: S ≈ one sitting; M ≈ a focused PR; L ≈ a multi-PR item.
Namespaces OUT, OWN, JRN and GAT are new and collision-checked against every prior train; HOT,
OBS, RWY, TND, NAT, PLT, CLM and RUL continue their numbering. Every gate names at least three
corpora. "The three corpora" means hippo, emgl and Skyline, as in OBS-5.

### HOT — earned injection, continued

- **HOT-7 Recall lanes by kind** `L` · deps OBS-5, OBS-10 · closes part of #155 — Each memory
  gets a lane from data the corpus already declares: **behavior** (`type: user`, `feedback`),
  **fact** (`project`, `reference`), **journal** (files matching `fold_digests` or a new
  `journals` glob list in `hippo.json`). `build_index` stores the lane in the manifest (a
  schema bump, which is a re-index). In the prompt hook:
  - journals are not admitted unless the prompt names their id, an issue join asks for them
    (GP-S4), or recall is explicit (`hippo recall`, the MCP tool);
  - each lane has its own share of the per-prompt budget, behavior first;
  - the rollup counts injected memories per lane, which is K14's instrument.

  Ranking inside a lane is unchanged. *Why:* the injected mix equals the corpus mix ✓; journals
  and digests take 26.9% of emgl's slots and were opened once in 1,086 pairs in the raw window ✓.
  *Gate:* on the
  three corpora, hard-set recall@10 within 0.02 of the OBS-5 pin; on emgl and Skyline, journal
  share of hook-injected memories ≤2% and behavior share at least twice its corpus share over 14
  days; a corpus with no `journals` declaration is byte-identical to today on the bench.
- **HOT-8 An injection count that scales with signal** `M` · deps HOT-7 · closes part of #155 —
  The number of rows comes from the score margin, not from `k`: zero to three rows when the top
  score clears the floor by less than a calibrated margin, up to `k` only when it clears it
  widely. Calibrated per corpus by the same sweep HOT-10 refreshes. *Why:* median 10 memories per
  injecting prompt ✓; #155's 31-memory recall at top scores near 0.02. *Gate:* on the three
  corpora, median memories per injecting prompt ≤5 with hard-set recall@10 within 0.02; emgl
  chars per injecting prompt ≤1,000 (1,758 ✓ today).
- **HOT-9 Hub damping in neighbor expansion** `S` · closes part of #155 — A neighbor's
  one-hop score is divided by a function of its in-degree relative to the corpus median, and a
  memory may enter by expansion at most once per session. *Why:* expansion has no degree term
  (`recall_graph.py:43-44`); one memory with 161 in-links rode 45% of episodes (#155). *Gate:* on
  the three corpora, no memory appears in more than 10% of a 14-day window's injecting events by
  expansion alone; hard-set multi-hop recall does not fall.
- **HOT-10 A soft band at the dense floor, and a sweep that stays current** `M` · closes #156 —
  A candidate within a band under the floor is admitted when another lane corroborates it, and is
  otherwise dropped with a distinct reason, so the same near-miss is not dropped the same way 54
  times. `hippo eval --floor-sweep` runs in the scheduled sleep (TND-7), and doctor's calibration
  line becomes a WARN when the sweep's corpus fingerprint is stale. *Why:* recommended 0.6793
  against configured 0.68, generated 2026-08-17 ✓; dense_floor is the top drop reason ✓. *Gate:*
  on the three corpora, off-topic abstention stays ≥8/11 and hard-set recall@10 does not fall;
  on emgl, no memory is dropped at `dense_floor` within 0.005 of the floor more than 5 times in
  14 days.
- **HOT-11 Digest headings as recall units** `M` · deps HOT-7 · closes #157 — In a declared fold
  digest, each `### slug` section is indexed as its own entry (BM25 and dense), addressable by
  slug. `[[slug]]` expansion resolves to the section, never to the whole digest, so a digest does
  not become a hub. The eval's gold stems resolve to folded headings. Sections ride the journal
  lane, so they reach the prompt hook only through HOT-7's three admissions. *Why:* 1,298 headings
  in 96 digests on emgl ✓; recall ranks a digest by its description and first ~1,500 characters.
  *Gate:* on emgl, a 30-query probe set of folded slugs reaches recall@10 ≥0.8 by explicit recall
  (near 0 today); on hippo and Skyline (no digests) the index is byte-identical; index build time
  on emgl grows ≤25%.
- **HOT-12 Recall for subagents** `M` · deps PLT-3 · *v2.x* — Only if PLT-3 shows a working
  pairing: a `PreToolUse` hook on the Agent tool reads the task prompt, and `SubagentStart`
  delivers at most two behavior-lane rows. *Gate:* set by PLT-3's receipts.

### OBS — receipts, continued

- **OBS-10 Opened-after-recall** `S` · **built in this amendment's first PR** — The join of
  hook-injected (session, memory) pairs against Reads of that memory's file later in the same
  session, split by type; Reads through the native path, a worktree copy, the private tier and
  the user tier are now recorded; daily rollup counters; a doctor line, `ok` always. *Gate (met):*
  measured on the three corpora (§A1).
- **OBS-11 Say why a recall ran BM25-only** `S` · closes part of #156 — The hook records a
  reason with the backend label (model not warmed, model mismatch, the 5 s dense timeout, an
  exception), the rollup counts reasons, and doctor names the top one with its remedy. *Why:* 659
  of 6,947 ✓; telemetry records only the label (`telemetry.py:249`). *Gate:* on the three
  corpora, every BM25-only recall in a 7-day window carries a reason; emgl's share is explained
  to ≥95% by named reasons.

### OUT — a write path that leaves the working tree alone *(new)*

*Goal: saving a memory is not a commit in a shared checkout, and landing one is one reviewed
batch.*

- **OUT-1 An outbox that recall already sees** `L` · deps RWY-3 — Opt-in per corpus
  (`hippo.json: "write_path": "outbox"`). `new_memory` and every tend verdict write to a
  gitignored outbox beside the corpus; the corpus dir in the working tree is not touched. Recall
  fuses the outbox as a tier, the way it already fuses the private tier
  (`recall_tiers.py:153-170`), so a new memory is recallable on the next prompt. An outbox copy of
  an existing memory shadows the committed one. The default stays today's direct write. *Why:*
  24.7% of emgl's default-branch commits ✓; 1,008 `new_memory` calls in 30 days ✓. *Gate:* on a
  worktree copy of the three corpora, 20 saves leave `git status` clean and each is recalled by
  the next prompt; crash-fault lane green for every new write site.
- **OUT-2 `hippo ship`** `M` · deps OUT-1 — Batches the outbox into one branch off the local
  default-branch ref, with a preview first. Each file is classified **new**, **same** (already
  landed, dropped from the outbox), **stale** (the committed copy moved since the outbox copy was
  made; shown as a diff, never overwritten) or **held** (the secret scan or threat lint fired).
  Deletions never travel. hippo creates the branch and the commit and stops: pushing and opening a
  pull request stay with the user or their tooling, because hippo makes no network call. *Gate:*
  the estate's lane script's classification cases reproduced as fixtures; on emgl over 14 days,
  memory-only commits on the default branch fall by ≥80% (K15); 0 credential shapes shipped in a
  seeded test of the scan.
- **OUT-3 An index that reads the default branch** `L` · deps OUT-1, OUT-2 · *v2.x* — Off the
  hot path, hippo materializes the corpus at the local remote-tracking ref into a hippo-owned
  directory and points readers at it, overlaid by the outbox. No fetch: the ref is as fresh as the
  machine's own git activity made it. *Why:* Skyline's primary is 113 commits behind ✓. *Gate:* on
  the three corpora, the indexed corpus equals `git ls-tree` of the ref plus the outbox; recall
  results identical to a clean checkout of the ref.
- **OUT-4 Consent that travels with the bytes** `M` · deps OBS-6's method, V2Q-17 — First a
  classified table, repro-first, of how the withheld files on the three withholding corpora
  arrived. Then: a memory whose bytes hippo itself wrote on this machine is consented by content
  hash on every registered corpus, so its arrival by pull withholds nothing. A hand edit, a
  native-path edit and anything written elsewhere still withhold. *Why:* 4 of 7 corpora
  withholding ✓; emgl shows none, and its sync script carries a flag that consents to the whole
  pulled corpus from the shell. *Gate:*
  the table, with a failing repro for the dominant path; on the three withholding corpora,
  withheld-after-pull counts for hippo-written files reach 0 without any blanket grant, and a
  seeded hand edit still withholds.

### OWN — one owner, many repos *(new)*

*Goal: what the owner taught one corpus is known in the next, without a server and without
identities.*

- **OWN-1 A git home for the user tier** `M` · closes #158 — `hippo setup` offers to `git init`
  the user tier (and records nothing remote). Memories marked machine-local stay out of the
  repository. Doctor reports the posture: not a repository, uncommitted changes, behind its
  upstream as of the last fetch the user ran. Cross-tier links name their tier in the link cache,
  so a fresh checkout says "lives in the user tier" instead of "dangling". hippo never pushes or
  pulls. *Gate:* on a copy of the user tier and two project corpora, a fresh clone resolves every
  cross-tier link or names its tier; doctor's posture line is correct in all three states.
- **OWN-2 Promotion as a tend kind** `M` · deps TND-2 — `promote_scan` already finds feedback
  and user memories that are near-duplicates across registered corpora, and reports them only in
  the sleep report (`sleep.py:206-228`). It becomes a tend queue kind with two shapes:
  **promote** (a portable lesson in one project corpus is proposed for the user tier) and
  **drifted** (the same rule reads differently in two corpora; the item shows both texts). Each is
  per item, with the diff, and writes only on a yes. *Why:* 173 feedback memories in emgl against
  14, 8, 9 and 3 elsewhere ✓; at least five rule pairs have drifted *(handed over)*. *Gate:* on
  the five estate corpora the scan lists the handed-over drifted pairs; after a tend pass, each
  sibling's recall answers a 10-probe set of the owner's working-style questions that it misses
  today.
- **OWN-3 Read-only recall across declared siblings** `M` · deps OWN-2, HOT-7 · *v2.x* —
  `hippo.json: "siblings"` names other registered, trusted corpora. The prompt hook may add at
  most two fact-lane rows from them, labelled with their corpus, inside a smaller budget. Explicit
  `recall --all-projects` already exists and is unchanged. *Gate:* on emgl, Skyline and one
  smaller sibling, hook wall p95 rises ≤15% with siblings declared; cross-corpus rows are opened
  at a rate no lower than same-corpus fact rows (K13).

### JRN — journal lifecycle *(new)*

- **JRN-1 `hippo fold`** `L` · deps HOT-11, V2Q-20 · *v2.x* — Families are declared in
  `hippo.json`. A fold moves each journal's text, byte for byte, under a `### slug` heading in its
  family digest and removes the original, as one previewed changeset and one revertable commit,
  with a conservation check that fails the fold if any byte is lost. *Gate:* replaying the
  estate's last two hand-built folds on a worktree copy reproduces their digests with zero lost
  bytes; HOT-11's probe set still passes afterwards; on hippo and Skyline a dry run proposes
  nothing harmful and writes nothing.
- **JRN-2 A time-to-live that proposes** `S` · deps JRN-1 · *v2.x* — A journal idle past its
  family's declared age becomes a tend item proposing its fold. Nothing folds unasked. *Gate:* on
  the three corpora, the proposed set matches the idle journals by the declared rule, and no
  journal a session touched in the window is proposed.

### GAT — plugin-owned gates *(new)*

*Goal: one guard, shipped with the plugin, on every corpus that upgrades.*

- **GAT-1 A write-time gate** `M` · deps V2Q-22 — A `PreToolUse` hook on file writes to the
  corpus dir. It checks the floor (bytes, lines, line length, declared banned pattern, section
  budgets) and, new, the same banned pattern over memory **bodies**. Delta posture: it objects
  only when the write leaves a budget over *and* made it worse. By default it adds a warning to
  context; it denies only where the corpus's committed `hippo.json` asks for that. *Why:* 661 of
  1,066 bodies carry status tokens ✓; only the floor is linted today. *Gate:* the estate gate's
  cases reproduced as fixtures; on the three corpora, 14 days with 0 false denials and every
  over-budget write named.
- **GAT-2 A commit-time gate and declared budgets** `M` · deps GAT-1 — `hippo.json` gains
  `budgets` (corpus file count, floor bytes, floor lines, journal count). `hippo gate --staged`
  runs the same checks on the staged tree with the same delta posture, and `hippo gate --install`
  writes a git pre-commit hook that calls it. *Gate:* on the three corpora, the hook passes every
  commit in the last 30 days that the estate's checker passed and fails the ones it failed; a
  corpus with no `budgets` behaves as today.
- **GAT-3 Declared floor sections and a pointer line of its own** `S` — `hippo.json` declares
  the floor's sections and which types file under which; the two hardcoded names
  (`new_memory.py:84-87`) become the default. The floor pointer takes a short line of its own
  (`hook`, which `new_memory` already accepts); when none is given and the description is longer
  than the floor's line limit, the save is refused with the limit named. A description is never
  copied whole into the floor. *Why:* emgl's floor has 5 sections ✓; 418 of 1,066 descriptions
  exceed its 300-char line ✓. *Gate:* on the three corpora, `lint_floor` reports no pointer
  outside a declared section, and 20 saves produce 0 over-length pointers.
- **GAT-4 One versioned `CONVENTIONS.md`** `S` — The plugin's asset carries a version stamp.
  A corpus's copy is that asset verbatim; local rules go in a separate file the plugin never
  writes. Doctor names a copy that is behind or edited, and `hippo setup` refreshes it on a yes.
  *Why:* three variants across five corpora ✓. *Gate:* on five corpora, doctor classifies each
  copy correctly, and after `setup` all five match the asset with local text preserved.

### NAT — native-memory coexistence, reshaped

- **NAT-3 A floor diet before a floor channel** `M` · deps GAT-3, HOT-7 — The floor holds
  behavior-lane pointers only, under a declared hard budget. Everything else is reachable by
  recall, which is what recall is for. Tend's existing `floor` kind proposes each demotion, ranked
  by use: a pointer whose memory was neither injected nor opened in 30 days goes first, and
  CLM-7's trim-safety replay says whether removing it loses a recall hit. *Why:* emgl's floor is
  22,134 B ✓, above hippo's 17,500 B warn line and 2,866 B under the harness's read limit; under
  an import channel it would ride every subagent spawn. *Gate:* on emgl, Skyline and one smaller
  sibling, the floor is ≤8,192 B after a tend pass with 0 lost hits on the trim-safety replay.
- **NAT-2 The coexistence contract** *(chartered; design changed)* `M` *breaking* · deps PLT-3,
  NAT-3, V2Q-23 — The ratified direction (V2Q-3 option B) stands: native writes go to a gitignored
  inbox that tend drains, and the floor comes from a CLAUDE.md import. What changes is the order
  and the gate. NAT-3 runs first, so the imported floor is small. PLT-3 re-measures what a
  subagent spawn carries under each channel on the current Claude Code. The channel is chosen on
  that receipt. *Gate, added:* per-spawn floor cost on emgl ≤2,100 tokens; 14 days on the three
  corpora with 0 native-stamped files landing in a corpus dir.
- **NAT-4 Census flags native memory that belongs to no corpus** `S` — The machine census
  reports a real `memory` directory under the harness's projects dir (today it skips anything
  that is not a symlink, `machine_census.py:104`), with the count of files in it and the adopt
  route. Read-only. *Gate:* on this machine, the stray directory is named; on a clean profile
  the census is unchanged.

### RUL — rules plane, continued

- **RUL-8 Conflicts between what a memory says and what a rule says** `M` — Today the radar
  compares edges and recall strength, never text (`rules_plane.py:66-68, 134`). Add a
  deterministic text leg: a memory and a rule section that share most of their content words but
  differ in their directive (ask / run, always / never, before / after) become a tend
  `contradiction` item naming both. No LLM by default; the existing opt-in contradiction check may
  confirm a candidate. *Why:* one live conflict that doctor reports as none *(handed over)*.
  *Gate:* on the five estate corpora, the handed-over conflict and its copy in a second corpus
  are both listed; on hippo and Skyline, at most 3 candidates per corpus are false on hand
  review.
- **RUL-9 Promote-to-rule as a tend proposal** `S` · deps TND-2 — When a feedback memory's
  first-touch reminders concentrate on one path glob, tend proposes the path-scoped rule file
  that `promote_rule` already drafts (`promote_rule.py:1-15`). Per item; writes on a yes. *Gate:*
  on the three corpora, every proposal's glob covers ≥80% of that memory's recorded touches.
- **RUL-10 A floor line that restates a rule** `S` — `lint_floor` runs the write-time
  duplicate-of-rule check (`rules_plane.py:598-640`) over floor lines and reports matches, so a
  line that CLAUDE.md already carries can leave the floor. *Gate:* on the three corpora, each
  reported line is a true restatement on hand review of up to 10.

### Platform, runway, calm, tend

- **PLT-3 Two platform receipts** `S` — (1) Re-run `PLATFORM.md` §3's subagent row on the
  current Claude Code under both floor channels. (2) Can a `PreToolUse` hook on the Agent tool
  read the task prompt, and can a `SubagentStart` hook for the same spawn deliver context derived
  from it? Dated rows in `PLATFORM.md`. *Gates NAT-2 and HOT-12.*
- **RWY-6 Writers clean up after themselves** `S` — The usage-aggregate writer leaves
  `*.tmp.<pid>` behind when its process is killed (`telemetry_usage.py:149, 304`). On its next
  write it removes siblings whose pid is no longer alive. `hippo setup` offers, per directory, to
  remove the derived dirs doctor already lists under dead worktrees. *Gate:* a kill-mid-write
  test leaves 0 orphans after the next write; on the three corpora, doctor's dead-derived-dir
  count is 0 after the offer is accepted.
- **CLM-9 Doctor says when full attention is costing context** `S` — One line that joins two
  facts doctor prints apart: attention is `full`, and N of M SessionStarts hit the cap with these
  producers dropped; then the one command that turns calm on. *Why:* 305 of 857 ✓. *Gate:* the
  line appears on emgl and is absent on a calm corpus and on one under the cap.
- **TND-12 Claims a memory makes about the machine** `S` · *v2.x* — At recall time, a memory
  whose text names an installed hippo version other than the running one renders with a short
  note. Doctor lists such memories. Narrow on purpose: the plugin's own version, which hippo can
  check without guessing. *Gate:* on the three corpora, each flagged memory names a version that
  is not installed.
- **TND-13 Acknowledging a cleared lint finding** `S` · deps V2Q-25 — `hippo.json` gains an
  acknowledgement list: file, rule, the content hash that was reviewed, a reason, a date. An
  acknowledged finding is counted, not warned. Any change to the file re-arms it. The list is
  part of the consented corpus, so adding to it is itself a reviewed change. *Gate:* on emgl, the
  three cleared findings stop warning and a one-byte edit brings each back; on hippo and Skyline
  the lint output is unchanged.

### Chartered items whose design changes

- **SRF-7 Dream: reduce to the linker** *(chartered)* — Add: completion edges are subject to a
  cofire floor (today they are exempt, `dream_config.py:243-265`), and the pass that ships this
  lists the edges already applied below it for one reviewed undo. *Gate, added:* on emgl, 0
  applied edges below the floor after the undo; on hippo and Skyline the linker's applied set is
  unchanged.
- **LDG-1 External-ledger read-time join** *(thread, v2.x → proposed v1.44.0)* — Direction A as
  ratified. This amendment answers its other three questions in V2Q-21. *Why now:* the boundary
  "memory cites the tracker by id; status lives in the tracker" is failing in 661 of 1,066 bodies
  ✓, and GAT-1's body lint only polices that. The join removes the reason to retype a status.
  hippo reads the committed snapshot and never the network. *Gate:* on emgl, a recall row citing
  a tracker id renders its status with the snapshot date; on hippo and Skyline (no ledger
  declared) recall output is byte-identical.
- **GP-S4 `recall --for-issue`** *(thread, v2.x → proposed v1.44.0)* · deps LDG-1, HOT-7 — The
  issue join is one of HOT-7's three admissions for journals, which is why it moves with it.
  *Gate:* on emgl, 20 sampled ids each return the memories that cite them; on the other two
  corpora the verb reports that no ledger is declared.
- **LDG-2 Arming on a terminal ledger status** *(named in `LDG-1.spec.md` §4; chartered here,
  v2.x)* · deps LDG-1 — The trigger for retiring a memory about finished work.

---

## A5. The train, amended

The ratified train ends "v1.43.0 — Last 1.x" then v2.0.0. Six of this amendment's items are
small enough to ride v1.43.0. The other nineteen that precede the major are additive. Putting
them in the major would turn a release that is "mostly deletions and stamps"
into the largest feature release of the train. The proposal (V2Q-13) is one more minor.

| Release | Theme | Items added by this amendment |
|---|---|---|
| **v1.43.0** | Last chartered 1.x: derived self-tending *(unchanged)* | OBS-10 (built), OBS-11, RWY-6, CLM-9, NAT-4, PLT-3, and SRF-7's cofire floor |
| **v1.44.0** *(new)* | **Selective, and shippable** | HOT-7, HOT-8, HOT-9, HOT-10, HOT-11, OUT-1, OUT-2, OUT-4, OWN-1, OWN-2, GAT-1, GAT-2, GAT-3, GAT-4, NAT-3, RUL-8, RUL-9, RUL-10, TND-13, LDG-1, GP-S4 |
| **v2.0.0** | Smaller, calmer, honest *(unchanged, plus)* | NAT-2 with its reshaped gate; CON-3 classifies the outbox, `ship`, `gate` and the new `hippo.json` keys |
| **v2.x** | | OUT-3, OWN-3, JRN-1, JRN-2, HOT-12, TND-12, LDG-2 |

> **v1.44.0 gate:**
> - HOT-7's lane gate on the three corpora, with K4 and K13 published before and after.
> - Median memories per injecting prompt ≤5 on the three corpora.
> - Memory-only commits on emgl's default branch down ≥80% over 14 days with the outbox on.
> - 0 corpora withholding hippo-written files after a pull, with no blanket grant.
> - The estate's floor gate, floor checker and lane script each have their cases reproduced as
>   fixtures in this repository.
> - Full suite green under pipefail, with the output file grepped for 0 `FAILED`.

**Order inside v1.44.0.** OBS-10 has to soak first (14 days), because HOT-7's gate reads it.
HOT-7 merges before HOT-8, HOT-9 and HOT-11. OUT-1 merges before OUT-2. GAT-3 merges before
NAT-3. Everything else is independent.

**If time runs short, cut in this order:** RUL-9, RUL-10, HOT-11 (to v2.x with JRN-1), GAT-4,
OWN-1. None blocks the v2.0.0 cut. HOT-7, OUT-1/OUT-2 and GAT-1/GAT-2 are the three that retire
estate code and should not slip.

---

## A6. Owner rulings needed

Batched, with a recommendation each, as §8 did. Item ids in §A4 that depend on a ruling name it.

- **V2Q-13 — Train shape.** *Options:* (a) add v1.44.0 for the additive items and keep v2.0.0
  as chartered; (b) fold them into v1.43.0; (c) fold them into v2.0.0. **Rec: (a).** It keeps the
  major a subtraction release and gives the outbox and the lanes a minor of field time before
  STABILITY v2 freezes their names.
- **V2Q-14 — Journals out of the prompt hook by default.** *Options:* (a) yes, admitted only by
  id, by issue join or by explicit recall; (b) a smaller budget instead of exclusion; (c) no
  change. **Rec: (a),** for corpora that declare `journals`. A corpus that declares nothing is
  unchanged.
- **V2Q-15 — What doctor gates precision on.** *Options:* (a) opened-after-recall alone;
  (b) K13 (opened-after-recall) for the fact lane and K14 (lane share) for the behavior lane,
  both report-only until OBS-10 has 14 days, then a floor set from the three corpora; (c) keep
  K4 only. **Rec: (b).** Feedback memories are never opened, so (a) would grade the lane that
  matters most as useless.
- **V2Q-16 — The outbox: opt-in or default.** *Options:* (a) opt-in per corpus in 1.x, default
  decided in v2.x on field data; (b) default at v2.0.0. **Rec: (a).** A single-checkout user who
  commits memories directly has no problem to solve, and (b) would change what `new_memory` does
  for everyone in the same release that freezes it.
- **V2Q-17 — Consent for bytes hippo wrote.** *Options:* (a) no change; (b) content-hash consent
  that travels across registered corpora on this machine, for hippo's own writes only; (c) trust
  commits by the owner's git identity. **Rec: (b),** after the classification table. Reject (c):
  authorship is spoofable and the ratified text rules it out.
- **V2Q-18 — What "no team features" rules out.** The ratified text lists team features as not
  ripe and says v2.0.0 is not "hosted or team-cloud anything". **Rec:** one owner, one machine,
  corpora the owner registered and trusted is in scope (OWN-1, OWN-2, OWN-3, OUT-4). Out of scope,
  unchanged: identities, permissions, attribution between people, review routing, any server, any
  network call by hippo.
- **V2Q-19 — Sibling recall in the prompt hook.** *Options:* (a) v2.x, after promotion has run,
  capped at two labelled fact rows; (b) now; (c) never, explicit only. **Rec: (a).** Promotion
  moves the behavior-changing memories, which is the larger gap, at no per-prompt cost.
- **V2Q-20 — May a fold be one reviewed changeset?** V2Q-1 (b) allows that for evidence-quiet
  graduates and lossless format rewrites only. *Options:* (a) add conserved-text folds to that
  class, with a byte-conservation check, one commit, one revert; (b) per-journal gates.
  **Rec: (a).** A fold with the check is as lossless as a format rewrite. The time-to-live only
  ever proposes.
- **V2Q-21 — LDG-1 and `recall --for-issue` before v2.0.0.** This revisits the timing in V2Q-10
  and V2Q-12; both are additive. **Rec: yes, in v1.44.0.** And the three questions `LDG-1.spec.md`
  §5 left open: Q2, the line-window contract over the committed snapshot (no new generated file
  in the consuming repo); Q3, cap of 2 ids per row, recall rows first, SessionStart later; Q4,
  charter LDG-2 now, build in v2.x.
- **V2Q-22 — A hook that can refuse a write.** The invariant says hooks never block. *Options:*
  (a) GAT-1 warns only; the commit-time hook is the hard edge; (b) GAT-1 warns by default and
  denies only where the corpus's committed `hippo.json` asks; (c) deny by default. **Rec: (b),**
  and amend the invariant text to "no hook blocks unless the corpus's own committed policy asks
  it to".
- **V2Q-23 — NAT-2's order and the floor budget.** **Rec:** floor diet (NAT-3), then the
  platform re-probe (PLT-3), then the channel; default hard budget 8,192 B for a floor delivered
  by import, declarable per corpus.
- **V2Q-24 — Subagent recall.** **Rec:** the PLT-3 spike now, a build in v2.x only if the
  pairing works. `SubagentStart` alone has no query to recall on.
- **V2Q-25 — An acknowledgement list for cleared lint findings.** **Rec: yes,** committed in
  `hippo.json`, keyed on the reviewed content hash, re-armed by any change, and itself covered by
  consent.
- **V2Q-26 — Text-level rule conflicts without an LLM.** **Rec: yes,** deterministic candidates
  into the tend queue; the existing opt-in LLM check may confirm, never decide.
- **V2Q-27 — SRF-7's cofire floor for completion edges.** **Rec:** set it from a replay of
  emgl's applied-edge ledger, and offer the edges below it as one reviewed undo.

---

## A7. KPIs added

Reported per corpus, never pooled, beside K1–K12.

| KPI | Definition | Baseline (2026-11-12) | Target | Instrument |
|---|---|---|---|---|
| K13 Opened after recall | hook-injected (session, memory) pairs whose memory file was Read later in the session, fact lane | emgl 0.10% · Skyline 0.05% · hippo 1.90% ✓ (all kinds) | set after 14 days of OBS-10; direction: up by ≥5× on emgl once HOT-7 and HOT-8 land | OBS-10 |
| K14 Lane share | behavior-lane share of hook-injected memories, against its corpus share | emgl 16.1% injected vs 17.0% of corpus ✓ | ≥2× corpus share; journals ≤2% | HOT-7 rollup counters |
| K15 Corpus commits | share of default-branch commits that touch only the corpus | emgl 24.7% touch it ✓ | down ≥80% with the outbox on | `git rev-list` |
| K16 Withholding after pulls | registered corpora withholding hippo-written files | 4 of 7 withholding anything ✓ | 0, with no blanket grant | `hippo census` |
| K17 Floor against budget | floor bytes over the declared budget, per corpus | emgl 22,134 B against a 17,500 B warn line ✓ | ≤ declared budget on every corpus | `lint_floor` |

K4 stays on the scoreboard as chartered. K7 (trust noise) gets its target from OUT-4's table,
which is where the ratified plan said it would be set.

---

## A8. Adoption on the estate

What each corpus does when the release that carries an item is installed. Roles are used for the
corpora this repository has not named before; the estate's own hand-off note carries the named
version. Nothing here is done by hippo unasked.

| Corpus | On v1.43.0 | On v1.44.0 | On v2.0.0 |
|---|---|---|---|
| **emgl** (has the lane, the gates, the folds) | Turn calm on (36% of SessionStarts are at the cap). Read doctor's opened-after-recall and BM25-only lines for a week. | Declare `journals`, `budgets`, floor sections and the tracker ledger in `hippo.json`. Set `write_path: outbox`; replace the save-then-ship rule in CLAUDE.md with `hippo ship`; retire the lane script and the sync script's trust step. Run `hippo gate --install`; retire the write-time floor gate, the floor checker and the pre-commit hook once `hippo gate` passes the same 30 days of commits. Tend the promote and drifted items. Tend the floor down to its budget. Acknowledge the three cleared lint findings. | Run `hippo migrate` (moves the three legacy `.format` policy keys). Re-run `setup` for the native contract; remove the CLAUDE.md paragraph that countermands the harness's memory instructions. Update the one permission rule that names a removed tool. |
| **Skyline** (vendors the gate, no lane) | Pull the primary checkout (113 behind). `hippo trust review` for the 13 withheld. Turn calm on. | `write_path: outbox` and `hippo ship`. `hippo gate --install`; delete the vendored gate. `hippo setup` to refresh `CONVENTIONS.md`. Tend promote items arriving from emgl. | `hippo migrate`; `setup` for the native contract. |
| **Two smaller siblings** (no gate, no lane) | `hippo trust review` (26 and 41 withheld). Turn calm on. | `hippo gate --install` (the first guard either has had). Declare floor sections; one has a flat floor of about 117 pointers, which NAT-3's tend pass sorts and trims. `write_path: outbox` if more than one checkout writes. | `hippo migrate`; `setup` for the native contract. |
| **Umbrella** (vendors the gate) | Turn calm on. | `hippo gate --install`; delete the vendored gate; refresh `CONVENTIONS.md` (the third variant); resolve the drifted rule that RUL-8 lists. | `hippo migrate`. |
| **The machine and the user tier** | Adopt or discard the native memories census now names in the stray directory. | `hippo setup` → `git init` for the user tier; mark machine-local memories; add a private remote by hand if wanted. | One re-bootstrap (SRF-5 + SRF-6), as chartered. |

---

## A9. Risks added

- **Lanes hide a journal that mattered.** *Mitigation:* three admissions (id, issue join, explicit
  recall); HOT-11 makes folded text findable at all; the gate holds hard-set recall within 0.02.
- **A precision gate on a signal agents rarely emit.** Opens are rare and miss shell reads.
  *Mitigation:* report-only for 14 days; gate the fact lane only; K4 stays.
- **Two write paths.** An outbox beside a direct write doubles the write surface.
  *Mitigation:* opt-in; every outbox site in the crash-fault registry; `ship` never overwrites
  and never deletes.
- **Hash-carried consent widens the trust gate.** *Mitigation:* only bytes hippo wrote on this
  machine; the registry records the hash at write time, not at pull time; a seeded hand edit
  must still withhold in the gate.
- **A gate that denies.** *Mitigation:* warn by default; deny only from committed corpus policy;
  delta posture, so standing debt never blocks an unrelated write.
- **Scope.** Twenty-five more items before the major. *Mitigation:* the cut order in §A5; six
  items already deferred to v2.x; only two of the twenty-five are L (HOT-7, OUT-1).

---

*Prepared as a proposal on 2026-11-12 at v1.42.8 (`b85a907`). `file:line` references were read
from that tree. ✓ receipts were recomputed the same day from the local telemetry ledgers of three
corpora, `hippo census`, and each estate corpus at its default branch. The Claude Code hooks and
memory references were fetched the same day.*
