# hippo — Road to v2.0.0

**Status: RATIFIED 2026-10-03.** The owner ratified the whole plan, including every §8 ruling
as recommended ("i ratify this all"). This document does not modify `ROADMAP.yaml`,
`STABILITY.md`, or any `ROADMAP.enhancements*.yaml`; CON-4 charters the train in
`ROADMAP.v2.yaml`. Authored 2026-10-03 at **v1.39.0** (`0d65178`). Companion to
[`ROADMAP.v1.md`](ROADMAP.v1.md) (same section shape: thesis → items → train → decisions → is/is-not
→ top moves) and to [`STABILITY.md`](STABILITY.md), whose frozen surface a major version is the
one chance to redraw.

**Method.** The run was sized to the session's 10-agent guideline and the budget-lean fan-out rule.
**Phase 1:** six read-only grounding agents covered architecture and contract, the open-threads
ledger, human experience, the Claude Code platform, the competitive field, and measured field
telemetry over 5 local corpora. **Phase 2:** two independent v2 plans were drafted from the same
grounding. *LEAP* is capability-first. *DISTILL* is consolidation-first. **Phase 3:** one
adversarial critic spot-checked both plans against the tree and merged them. That is 9 agents in
total, about 1.85M subagent tokens, 555 tool calls and about 33 minutes. **Phase 4** ran inline in
the orchestrating session. It re-verified every load-bearing number against the tree, the
telemetry ledgers, and the official Claude Code docs (fetched 2026-10-03). That pass found
**three platform facts no agent had established** (§2) and six errors in the drafts (§4).
**Phase 5:** one more adversarial agent fact-checked the first draft of *this* document. It
re-verified about 50 receipts clean and found three wrong baselines, which are corrected here
(§4, second table). That makes 10 agents in total. Receipts the orchestrator re-verified are
marked **✓**. Others are attributed to the lens that produced them.

---

## 0. TL;DR

**The roadmap is done. Hippo now costs users more context and attention than it returns.**

119 of 121 items across `ROADMAP.enhancements{,2..5}.yaml` and `ROADMAP.dream.yaml` are done
(threads lens). The engine works. Telemetry from the production corpus (em-growth-labs, "emgl":
about 1,024 memories, 93% of all hippo MCP traffic) shows that the layer users actually live with
has outgrown its budget:

- **SessionStart is pinned at its cap.** 3,823 of 3,856 emgl SessionStarts (**99.1%**) reach at
  least 98% of the 9,000-char cap ✓. With every session truncated, what the agent reads is decided
  by truncation order, not priority. Seven maintenance producers fire in 93–100% of emgl sessions
  ✓.
- **Recall never abstains, and most of it is triggered by machine turns.** 0 of 486 emgl recall
  rows injected nothing ✓ (0 of 1,571 across 5 ledgers, measured lens). **282 of 486 (58%)** fired
  on machine-generated turns ✓, and they injected about 1.06M of 1.78M chars ✓:
  - background-task notifications (222)
  - subagent hand-backs (39)
  - cross-session messages (19)
  - a scheduled task (1)

  `clean_query` strips only three envelope tags (`recall_query.py:24` ✓). The other kinds pass
  straight into ranking. Injection precision (KPI-2) is **1.7%** on emgl and about 10% on hippo
  itself (measured lens).
- **Maintenance work mostly confirms that nothing changed.**
  - Over 3 in 4 reverify verdicts are `graduate`: 185 of 242 on hippo (76%) ✓, after removing the
    test-fixture rows that leak into the live ledger (OBS-9), and 297 of 371 on emgl (80%) ✓.
    Snoozes are deferrals, not verdicts, so they sit outside both denominators.
  - Two citation-derivation bumps cost **about 600 per-item `rederive` calls** (419 + 179;
    fact-check transcript grep).
  - `trust_drift` fires in **97%** of emgl SessionStarts and 59% of hippo's ✓.
- **Two of the README's headline claims are now false.** "Recall costs zero tokens"
  (`README.md:19-20` ✓) sits beside about 28.5k injected chars per session (doctor scorecard, ux
  lens). "No other tool checks whether the code a memory cites has moved" (`README.md:151-152` ✓)
  is contradicted by GitHub Copilot Memory, which checks citations against the current branch
  (competition lens, docs.github.com, 2026-10-03).
- **The platform moved under hippo** (§2, all ✓ against official docs):
  - Hooks can now **call an MCP tool directly** (`type: "mcp_tool"`). Warm recall no longer needs
    a socket.
  - Desktop now **lists and runs plugin skills from `/`** and installs plugins without the
    terminal. The 1,723-char Desktop routing note ✓ (about 21% of every Desktop SessionStart)
    answers a constraint that may no longer exist.
  - Native auto memory is **on by default**, uses hippo's four types, and writes into whatever
    directory the symlink points at.
  - `userConfig` keeps secrets in OS secure storage.

**The v2 thesis: earned context, derived maintenance, a smaller contract.** Every injected
character has to earn its place. Maintenance that derived state can prove becomes derivation, not
human toil. The major version is spent on **subtraction**: one `hippo <verb>` door, about 10 MCP
tools instead of 28, 9 skills instead of 18, at most 12 frozen env vars instead of 22,
corpus_format 6, and a redrawn, linted STABILITY v2. The capability leaps are the few the simpler
core makes cheap:
- warm recall served by the session's own MCP server
- an explicit native-memory contract
- field-grade evaluation as the release gate

**Proposed arc.** Five releases, 59 items, most of them small. Every surface removal sits behind
a two-minor, usage-counted window. The format change runs check → dual mode → one revertable
apply.

| Release | Theme | One-line gate |
|---|---|---|
| **v1.40.0** | **Room, receipts, clean turns** | Runway restored; durable rollups recording on 3 corpora; 0 injections on machine turns; tests stop polluting the live ledgers; the platform-reality spike has dated receipts; the falsified README claims are gone; owner rulings recorded. |
| **v1.41.0** | **Earned injection** | Field baselines pinned on 3 corpora *before* any ranking change; off-topic abstention ≥8/11; emgl hook chars per session ≤12,000; calm SessionStart (opt-in) median ≤2,000 chars; CAS writes; readers refuse a newer corpus format loudly. |
| **v1.42.0** | **One door, one queue** | `hippo <verb>` everywhere; `tend` drains every queue kind; v2 tools/skills/config ship beside old names that still route; warm recall opt-in. |
| **v1.43.0** | **Last 1.x: derived self-tending** | Evidence anchors replay-validated; dual citation binding at ≤1% parity drift on emgl; STABILITY v2 draft published; dream's fate decided on field numbers. |
| **v2.0.0** | **Smaller, calmer, honest** | Format 6 rehearsed on 3 corpora with zero recall diff; aliases gone (0 calls in 14 days); calm by default; native contract applied only if the spike proved floor delivery survives; one re-bootstrap. |

The top five moves are in §13.

---

## 1. Ground truth — what the field says (verified, not remembered)

| Signal | Number | Receipt |
|---|---|---|
| Roadmap completion | 119/121 items done; only GRF-5, EVD-3 open (correctly deferred) | threads lens (ROADMAP parse) |
| Engine size | 110 modules, 54,395 LOC (+251 vendored); tests 58,401 LOC / 3,069 tests | arch lens (`wc`) |
| Runway | `recall.py` 1697/1697, `dream.py` 900/900, `staleness.py` 896/900, `reconsolidate.py` 875/900 | ✓ `tests/test_module_size.py:26-62`, `wc -l` |
| SessionStart saturation | emgl 3,823/3,856 (99.1%) at ≥98% of the 9,000 cap; median 8,997 chars | ✓ `injection_producers.jsonl` |
| Producers fired (emgl) | staleness, resume_card, portable_floor 100%; reconsolidation 99%; trust_drift 97%; link_health 97%; pending_capture 96%; floor 93% | ✓ same ledger |
| Desktop routing note | 1,723 chars appended to every Desktop SessionStart (116/116 on hippo) | ✓ `session_start.py:137-163` `len()` |
| Abstention | 0/486 emgl recall rows inject nothing; 0/11 off-topic fixtures abstain on hippo | ✓ ledger; sleep-report (ux lens) |
| Machine-turn recall | 282/486 (58%) emgl recalls fire on machine turns (task-notification 222, agent-message 39, cross-session-message 19, scheduled-task 1); ~1.06M of ~1.78M chars | ✓ ledger (live, 2026-10-03) |
| The leak's mechanism | `clean_query` strips only `task-notification`, `system-reminder`, `local-command-stdout`; the RCL-3 rescue blends the **raw** prompt and **raw** previews, then re-cleans | ✓ `recall_query.py:24`; `recall.py:1481-1490` |
| Injection precision (KPI-2) | emgl 1.7% (132/7,660); hippo ~10% (250/2,446) | measured lens (`outcome.injection_precision`) |
| Hook latency | emgl p50 423 ms / p95 2,269 ms logged ✓; logged ms omits 0.4–1.1 s interpreter start | ✓ ledger; `CHANGELOG.md` v1.38.0 |
| PostToolUse spawns | median 7.47 Python spawns per recall prompt (63 sessions) | arch lens |
| Reverify verdicts | hippo 185/242 graduate (76%) after removing test-fixture rows; emgl 297/371 (80%); snoozes excluded | ✓ `reconsolidation_events.jsonl` |
| Ledger hygiene | 727 of hippo's 999 verdict rows name test fixtures (`m_alpha` 394, `m_feature_design` 294, `reranker_voyage` 39) that exist in no corpus; stamped up to v1.39.0, so the leak is ongoing | ✓ ledger vs corpus listing; fact-check |
| Staleness backlog | emgl 764/1,024 (74.6%) flagged stale; 72 verdicts in 30 days | measured lens |
| Two staleness stories | recall banners 49/80 hippo memories while SessionStart arms 1 (+45 type-exempt, +3 volatile) | ux lens; `recall_salience.py:173-206` |
| MCP surface | 28 tools, 36,546 chars of `tools/list`; no annotations, no `outputSchema`; protocol version echoed | ✓ `json.dumps(_TOOLS)`; `mcp_server.py:168,264` |
| MCP usage (all transcripts) | 17 of 28 tools ever called; 11 never (`why`, `traverse`, `decision_history` — 3 of the 5 *frozen* — plus `blast_radius`, `heal_baselines`, `untrust`, 5× `pack_*`). Top: `new_memory` 881, `reconsolidate` 769, `rederive` 623, `capture` 378, `recall` 147, `trust_corpus` 138 | ✓ transcript grep (not deduped across subagents) |
| Skills | 18 shipped (STABILITY freezes 15; README says 16); 3,456 SKILL.md lines; roadmap ids in 18/18 bodies | ✓ `ls`, `wc`, grep |
| Raw engine calls | 65 `-m memory.*` call sites across skills + hooks | ✓ grep |
| Env namespace | 64 `HIPPO_*` tokens in code (62 real; 22 frozen; 4 undocumented anywhere; 2 unprefixed `DREAM_CONTRA_*`) | ✓ grep; arch lens |
| Trust fold | "authorship = consent" already exists: `record_authored_write`, called from 8 write modules (new_memory, provenance, staleness, dream, dream_apply, dream_generate, links, packs) | ✓ `trust.py:335-350`; grep |
| Evidence fences | future drains only, never backfilled; 0/1,028 emgl, 2/81 hippo carry one | ✓ `staleness_evidence.py:8-9`; critic grep |
| Floor size | emgl 102 lines / 17,862 B; Skyline 88 / 17,881; `lint_floor` checks bytes, never the 200-line limit | ✓ `wc`; grep |
| Field recall quality | emgl recall@10 0.577 / MRR 0.265 (52 queries, at 357 memories, 2026-08-17) vs bench 1.00/0.91 | measured lens (`outcome_prior_ab.json`) |
| Telemetry retention | 2 MB rotation keeps ~2.3 days of emgl recall rows | measured lens (`telemetry_store.py:29`) |
| Disk | ~1.18 GB of hippo caches across 2 plugin-data dirs + `~/Library/Caches`; bootstrap downloads ~210 MB (README says ~130 MB) | arch + ux lenses |
| Adoption | 2 stars, 0 forks; absent from the official 315-plugin marketplace; claude-mem ~95k stars | competition lens |

**What the numbers say together:** the hot path does what was built — offline, zero-LLM, ranked —
but nothing tells it when *not* to inject, the push surfaces grew from 13 to 26 producers
against a fixed cap, and the maintenance loops ask a human to re-confirm what git already proves.
The maintainer's own corpus shows the same signs: citation derivation 4 against the plugin's 6,
9 pending captures (oldest 2026-07-20), 5 unresolved contradiction pairs, and 1 memory withheld
for re-consent.

---

## 2. What moved under hippo (verified against official docs, fetched 2026-10-03)

These three findings change the v2 design. The grounding agents missed or could not confirm them.
The orchestrator verified each one directly.

1. **Hooks can call MCP tools directly.** `hooks` accepts `type: "mcp_tool"` with
   `server`, `tool` and `input`. For a plugin server the name is `plugin:<plugin>:<server>`
   (here `plugin:hippo:hippo`). String inputs take `${path}` substitution from the hook's JSON
   input, and UserPromptSubmit's input includes `prompt` and `session_id`. The tool's text output
   is parsed like command-hook stdout, so `hookSpecificOutput.additionalContext` is honored. An
   unconnected server or an `isError` result produces a **non-blocking error**. SessionStart and
   Setup `mcp_tool` hooks are skipped at launch because servers aren't up yet
   ([hooks](https://code.claude.com/docs/en/hooks)).
   **Consequence:** the warm-recall design both drafts proposed carried an open session-keying
   gap: `mcp_server.py` has 0 references to `session_id` ✓. With an `mcp_tool` hook the harness
   routes each call to *that session's own* server and passes `session_id`. There is no socket,
   no key collision and no thin client. HOT-6 is rebuilt around this, with the socket design kept
   as the fallback (§5).
2. **Desktop runs plugin skills and installs plugins.** The Desktop Code tab says typing `/`
   lists "skills from any installed plugins", and that "you can install plugins from the desktop
   app without using the terminal" ([desktop](https://code.claude.com/docs/en/desktop)). This
   session ran a typed *built-in* skill on Desktop, which says nothing yet about *plugin* skills.
   PLT-1 has to confirm those.
   **Consequence:** hippo's "typed `/hippo:*` is terminal-only" premise
   (`session_start.py:128-163`, README Quickstart) is probably stale. If so:
   - the 1,723-char note in every Desktop SessionStart can go
   - the 11 "Surface routing" SKILL.md sections can go
   - the 7 terminal-only verbs stop being second-class
   - install no longer requires the terminal
   PLT-1 confirms this before anything is deleted.
3. **Native auto memory is on by default and shares hippo's shape.** It saves four kinds of notes
   (`type` frontmatter), loads `MEMORY.md` at session start up to **200 lines or 25 KB, whichever
   comes first**, and reads topic files on demand. It stamps a `modified` field on files it
   writes. It is controlled by `autoMemoryEnabled` (user or project), by
   `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, and by `autoMemoryDirectory` (any settings scope).
   CLAUDE.md files load in full up to 4 MiB, and `@path` imports inside the working directory need
   no approval. CLAUDE.md content also loads into every non-Explore/Plan subagent; the main
   conversation's auto memory does not ([memory](https://code.claude.com/docs/en/memory),
   sub-agents doc, per the fact-check).
   **Consequence:** `/hippo:init` symlinks the native slot onto the reviewed corpus. Native writes
   therefore land in the corpus unreviewed, and hippo's only native dependency is always-load
   (`NATIVE_MEMORY.md`). Three questions are unanswered:
   - Does `autoMemoryEnabled:false` also stop `MEMORY.md` from loading?
   - Would a CLAUDE.md `@.claude/memory/MEMORY.md` import be a sturdier floor channel?
   - Is there a per-turn native recall step? Only third-party sources report one, so confidence
     is low-medium.
   These questions are the core of PLT-1. `lint_floor` also never checks the 200-line limit ✓.

Also verified, and used below:
- `userConfig` exists. `sensitive: true` values go to OS secure storage. Values reach hooks as
  `CLAUDE_PLUGIN_OPTION_<KEY>` and MCP env as `${user_config.KEY}`. `options` pickers need CC
  ≥2.1.271.
- Plugin `bin/` is **on the Bash tool's PATH** while the plugin is enabled. claude.ai and
  Cowork, however, do not install a plugin that has a top-level `bin/`, and hippo already ships
  `bin/hippo`.
- `PostToolBatch` fires once per parallel tool batch.
- Command hooks accept `async: true`.
- Mods draw panes and bands in the terminal and Desktop. They are human-facing UI, not model
  context, and need CC ≥2.1.287.
- `plugin.json` now carries directory-listing fields (`icon`, `documentationUrl`, `supportUrl`,
  `privacyPolicyUrl`) for submission to Anthropic's directory
  ([manifest reference](https://code.claude.com/docs/en/plugins-reference),
  [mods](https://code.claude.com/docs/en/plugins/mods/overview)).

---

## 3. The v2 thesis

v1's thesis was "ship the engine you have; make it safe, provable, legible; freeze a surface."
That shipped. v2's problem is different. **The product layer around the engine has grown past
its budget**: too much push context, too many verbs, and maintenance that re-confirms what git
already knows. The surface is also frozen in the wrong places. STABILITY freezes `why`,
`traverse` and `decision_history`, which have 0 calls in about 11 weeks. It leaves the
heavily used maintenance tools unfrozen: `reconsolidate` (769 calls), `rederive` (623) and
`capture` (378). v2 has three organizing moves:

1. **Earned context.** Recall runs only on human turns, abstains below a calibrated floor, renders
   inside a visible per-prompt and per-session budget, and tells one staleness story. SessionStart
   becomes a ≤2,000-char digest with an integrity lane that is always shown. Context the human
   needs but the model doesn't stays out of the model's context.
2. **Derived maintenance.** Derivation bumps become a re-index, because derived citations leave
   committed frontmatter. Staleness arms only when cited evidence moved, which is computed and
   never written. One `tend` queue replaces about 11–14 maintenance verbs. Batched
   acknowledgement covers **only** the owner-ruled classes, and only after the trust-drift root
   cause is known.
3. **A smaller, linted contract.** One `hippo <verb>` door, about 10 annotated MCP tools, 9
   skills, at most 12 frozen env vars, one config file, corpus_format 6 with a real `migrate`
   verb, and STABILITY v2 that classifies every shipped surface as frozen or explicitly unfrozen.

**Guardrail (carried from v1, sharpened):** no new engine capability enters v2 unless it removes
measured waste, removes toil, or is required by the contract redraw. Reach work (Codex adapter, PR
Action, importers, LDG-1) is good and non-breaking, so it ships in **v2.x**, after the smaller
core is frozen.

**Invariants hold unchanged.** These are:
- a zero-LLM, zero-network hot path
- git as the store
- no autonomous corpus writes
- a human-readable markdown corpus
- local-first

Two items touch an invariant and are therefore owner rulings (§8): batched acknowledgement
(V2Q-1) and in-session served recall (V2Q-2). Neither is a silent erosion.

---

## 4. Corrections to the grounding (struck before planning)

The critic and the orchestrator verification pass found six places where the drafts would have
built on false premises.

| Draft claim | Reality | Effect on plan |
|---|---|---|
| The eval modules (2,389 LOC) have no runtime importer and can move to `bench/` | `eval_recall.py:99,286,306` imports `eval_arms`, `eval_fixtures` and `eval_adversarial` at top level; `eval_floor.py:38` imports fixtures; `abstention_fixtures` is served through this path (72 calls) ✓ | RWY-4 makes the façade lazy first and moves only 845 LOC now (`calibrate_thresholds`, `dream_eval`) |
| Warm socket keyed on `session_id` | MCP server never sees a session id ✓ | HOT-6 is rebuilt on `mcp_tool` hooks (§2.1); socket design kept as fallback with a keying + version-handshake spike |
| Warm p50 ≤80 ms | Warm *in-process* recall measured at ~420–480 ms under load (`CHANGELOG.md` v1.39.0) | Gate is relative: contended wall p95 ≥40% below spawn |
| Make hippo's writes refresh trust (DISTILL TND-6) | Already shipped: `record_authored_write`, called from 8 write modules ✓ | Replaced by OBS-6 trust-drift root-cause repro |
| Evidence-gated graduation from fences (≥70% batchable) | 0/1,028 emgl memories carry fences; future-only ✓ | TND-3 adds *derived* anchors, validated by replaying historical verdicts (~613 real ones; see the next table) |
| Dual-shape idiom ×27 in 15 modules | 24 exact hits in 13 files (26 in 15 loose) | RWY-2 baseline restated |

The Phase-5 fact-check then corrected the first draft of this document:

| First-draft claim | Reality | Effect |
|---|---|---|
| "91% of hippo verdicts are `graduate`" | 727/999 rows are test fixtures leaking into the live ledger; real share 76% (185/242 verdicts) ✓ | Baselines restated; new OBS-9 (test hermeticity); TND-3 replays ~613 real verdicts |
| "About half of emgl recalls are task-notification turns" | 58% are machine turns once subagent hand-backs, cross-session messages and scheduled tasks are counted, and `clean_query` doesn't know three of those tags ✓ | HOT-1 and OBS-1 cover every machine-turn class |
| "One derivation bump cost 623 calls" | Two bumps (v1.35.0 4→5, v1.36.0 5→6): 419 + 179 per-item calls, plus worklist and stamp calls | Restated as ~600 across two bumps |
| RWY-4 moves 845 LOC cleanly | `eval_recall.py:1196-1199,1242-1243` dispatch `--ab`/`--calibrate` into those modules | RWY-4 moves the dispatch too |
| FMT-3: "today only a doctor warning" | SessionStart already warns loudly (`corpus_format_producer`, `session_start_health.py:108-133`) ✓ | FMT-3 is warn → refuse injection |
| Floor via CLAUDE.md import "trades 200 lines/25 KB for 4 MiB" | CLAUDE.md also loads into every non-Explore/Plan subagent, so a 17.9 KB floor would ride every spawn | NAT-2 presents it as a trade-off; PLT-1 measures it |

---

## 5. Workstreams and items

Ten new namespaces, all collision-checked against every prior train's id set: **RWY OBS HOT CLM
TND FMT SRF PLT NAT CON**. Sizes: S ≈ one sitting; M ≈ a focused PR; L ≈ a multi-PR item.
*Breaking* means it changes a frozen surface and ships only at v2.0.0, behind a window.

### RWY — Runway & write safety
*Goal: no v2 feature has to begin with an emergency split; corpus writes are safe across ~37
concurrent sessions per host.*

- **RWY-1 Pre-split the four at-cap modules** `M` — Pure code motion along section banners:
  - `recall.py` hook entry, plus the `clean_query`/RCL-3 block, → `recall_hook.py` (also the HOT-6
    seam)
  - `dream.py` CLI → `dream_cli.py`
  - `staleness.py` cache → `staleness_cache.py`
  - `reconsolidate.py` CLI → `reconsolidate_cli.py`

  Lower `recall.py`'s pin. Also split `mcp_schemas.py` by tool family (the `pack_*` family, ~163
  lines, is the file's own documented fallback) **before SRF-2** doubles it during the alias
  window. Check headroom on the files RWY-3 and SRF-2 will touch: `new_memory.py` has 31 lines
  left, `provenance.py` 39, `dream_generate.py` 35 and `packs.py` 48 (fact-check). *Why:* zero
  headroom ✓, and EKPI5R-4 is violated again. *Done:* each split file has ≥120 lines of headroom;
  bench recall output is byte-identical; suite green under pipefail with 0 `FAILED` lines.
- **RWY-2 One frontmatter accessor** `S` — `fm_get(fm, key)` replaces the flat-or-nested idiom (24
  exact hits / 13 files). A flat-read counter feeds FMT-1. *Done:* the idiom appears only inside
  the accessor (grep test).
- **RWY-3 Compare-and-swap writes, repro-first** `M` — Write the failing tests first (the QA-sweep
  rule): a lost update from two interleaved writers, and a partial write when `new_memory` crashes
  mid write+backfill. Then add an expected-hash CAS helper to `atomic.py` and route every corpus
  read-modify-write through it: floor pointer edits, verdicts, typed edges, `MEMORY.md`. No locks,
  no coordinator. *Why:* hippo-qa-sweep-2026-07-16 left both open; emgl had 514 `MEMORY.md`
  commits in 37 days; growing-pains S7. *Done:* both repros fail before and pass after; an AST
  registry asserts every RMW site uses CAS.
- **RWY-4 Ship only the runtime (reshaped)** `S` — Make the `eval_recall` façade re-exports lazy.
  Move `calibrate_thresholds` and `dream_eval` (845 LOC) to `bench/` now, **together with** the
  `--ab`/`--calibrate` dispatch that lazily imports them (`eval_recall.py:1196-1199,1242-1243`). `eval_arms` and
  `eval_adversarial` (721 LOC) follow once the façade is lazy. `eval_fixtures` stays because it
  backs `abstention_fixtures`. *Done:* an import-boundary test fails if `plugin/` imports
  `bench/`.
- **RWY-5 Foundation→engine edge lint** `S` — Forbid foundation modules from importing the recall
  orchestrator (`trust.py:588`, `staleness.py:814` today). Counters on lazy and private imports
  are dropped as cosmetic. *Done:* 0 such edges, enforced in required CI.

### OBS — Receipts that outlive rotation
*Goal: every v2 claim and every cut is decided from durable numbers on ≥3 corpora (hippo, emgl,
Skyline), never from 2.3 days of ledger or one corpus.*

- **OBS-1 Rotation-proof daily rollups** `M` — A gitignored daily row, capped at 365, per corpus:
  hook wall p50/p95, backend mix, abstentions, injected chars per prompt and per session, trigger
  class (human / task-notification / agent-message / cross-session-message / scheduled-task /
  bash-input), SessionStart chars and dropped producers.
  doctor reads 30-day KPIs from it. *Done:* 7 rows on 3 corpora after 7 days, surviving a
  truncation test.
- **OBS-2 Surface-usage ledger** `S` — `{surface, verb, action, plugin_version, client}` for
  every MCP call, `hippo <verb>`, skill preflight, and hook path (spawn / warm / failed). *Why:*
  no skill or CLI telemetry exists, and it decides SRF-3 and SRF-8. *Done:* doctor prints 30-day
  per-verb counts.
- **OBS-3 Log served names, not the collapsed pool** `S` — Rows record what was *served* (≤k)
  separately from over-fetched and collapsed entries (`recall.py:1495-1500,1565-1580`), so
  orphan signals stop saturating (0 memories anywhere go more than 30 days without a "recall"
  today). *Done:* hippo median served names per event ≤10.
- **OBS-4 Shell-measured hook wall time** `S` — Hook scripts stamp epoch-ms around the
  interpreter, so KPI-3 counts interpreter start and imports. *Done:* doctor shows the wall vs
  logged gap.
- **OBS-5 Field eval gate on 3 corpora** `M` — Refresh the emgl hard set to ≥100 queries (≥15
  multi-hop) at about 1,024 memories. Pin MSR-1 baselines on hippo, emgl and Skyline. Persist
  `eval_runs.jsonl`, which exists in no telemetry dir today. Release notes carry a per-corpus
  scoreboard. **This must merge before any ranking-path PR** (HOT-2, HOT-3). *Why:* bench
  1.00/0.91 against emgl field 0.577/0.265; the MSR-1 pin was never taken.
- **OBS-6 Trust-drift root cause, repro-first** `S` *(new — critic gap)* — Classify drifted files
  on emgl and hippo by arrival path: merge commit from one of 32 worktrees, Edit-tool hand edit,
  legacy fingerprint-less record, or hippo verb. Fix the dominant path. *Why:* the authored-write
  fold already exists ✓, so the 97%/59% fire rate ✓ has an undiagnosed source. *Done:* a
  classified table plus a failing repro for the dominant path. TND-6's target is set from it.
- **OBS-7 Dogfood drain, keeping derivation 4 on purpose** `S` — Drain hippo's 9 pending captures,
  resolve the 5 pairs, re-consent the withheld memory. Leave `cite_derivation: 4` as the first
  real FMT-4 rehearsal target.
- **OBS-8 1,000-memory dense-under-load nightly lane** `S` — Synthetic 1,000-memory corpus, dense
  on, ≥8 concurrent recalls, beside the 500-memory BM25 lane. Fails on >25% regression against
  the trailing 7 nights.
- **OBS-9 Tests never write the live ledgers (repro-first)** `S` *(new — fact-check)* — 727 of
  hippo's 999 verdict rows are test fixtures (`m_alpha`, `m_feature_design`, `reranker_voyage`),
  stamped as late as v1.39.0 ✓. A test is leaking into the real telemetry dir. Write the failing hermeticity test
  first: run the suite and assert that no file under any real `.memory-telemetry` changes. Then
  fix the leak and quarantine the polluted rows from KPI readers. *Why:* every KPI in §9 reads
  these ledgers.

### HOT — Earned injection
*Goal: inject only on human turns, abstain when nothing clears a floor, stay inside a visible
budget, and stop paying an interpreter per prompt and per file touch.*

- **HOT-1 Close the machine-turn leak end to end** `S` — Machine turns never trigger recall:
  task-notification, agent-message (subagent hand-back), cross-session-message, scheduled-task,
  system-reminder-only and bash-input. `clean_query` learns the three tags it doesn't strip today.
  The RCL-3 rescue blends **cleaned** previews only. The episode buffer and ledger store the
  cleaned query. *Why:* `recall_query.py:24` knows 3 tags ✓; raw blend at `recall.py:1481-1490` ✓;
  282/486 emgl recalls are machine turns ✓. *Done:* a replay over the retained ledger window, plus
  fixtures for every class (including a truncated, unclosed tag), shows 0 injections. The 30-day
  check moves to v1.41, on OBS-1 rollups.
- **HOT-2 Calibrated abstention** `M` · deps OBS-5 — Add a per-corpus BM25 score floor. A
  single-token lexical admission also needs dense support. Calibrate from `abstention_fixtures`
  plus off-topic probes. `/hippo:why` prints the near-miss receipt ("abstained: best X scored Y
  < floor Z"). *Done:* ≥8/11 off-topic fixtures abstain on hippo and emgl; recall@10 within 0.02
  of the OBS-5 baseline on all 3 corpora.
- **HOT-3 Per-prompt and per-session context budgets** `M` · deps HOT-1, OBS-1 — Score-knee
  cutoff with a 2,500-char default per prompt, rendered as compact one-line rows, plus a
  per-session budget with cooldown on re-injected names. Recall output text is explicitly not
  frozen, so this is a minor. *Done:* emgl hook chars per session ≤12,000 (from 29,339); per-prompt
  p90 ≤2,500; KPI-2 published before and after on 3 corpora.
- **HOT-4 One staleness truth** `S` — The recall-time banner reads the same armed set SessionStart
  uses (TYPE-1, VOL-1, demoted, snoozed). *Done:* on hippo, bannered count = armed count (49 → 1
  today).
- **HOT-5 Stop paying an interpreter per file touch** `M` · deps OBS-4, PLT-1 — Pick by the PLT-1
  receipts, in this order:
  - (a) PostToolUse as an `mcp_tool` hook into the session's server, if cheap and if failure is
    legible
  - (b) a bash fast path that spawns Python only for paths in a precomputed touchmap, for
    `MEMORY.md`, or when the debounced presence check is due
  - (c) `async: true` for the pure logging part

  The KPI-2 join input is unchanged. *Done:* median Python spawns per recall prompt ≤2 (from
  about 8.5: 1 recall plus a median 7.47 PostToolUse spawns); KPI-2 is identical on a replayed
  fixture.
- **HOT-6 Warm recall served by the session's own MCP server** `L` · deps RWY-1, OBS-4, PLT-1 —
  *Primary design:* a UserPromptSubmit `mcp_tool` hook → `plugin:hippo:hippo` /
  `recall_hook` with `prompt: "${prompt}"`, `session_id: "${session_id}"` and `cwd: "${cwd}"`,
  returning `hookSpecificOutput.additionalContext`. The server already holds the warm model cache
  (`build_index._MODEL_CACHE`). *Fallback design* (only if PLT-1 fails): a per-session socket,
  preceded by a keying and version-handshake spike. *Requirements either way:*
  - read-only
  - a stale-index check by manifest mtime on every call
  - a version handshake (plugin version and index schema) where a mismatch degrades to spawn, so
    the launch-pin skew class can't serve old-schema answers
  - every degrade logged as `path=warm|spawn|failed` and surfaced in doctor
  - no new blocking mode: a short budget and a per-session circuit breaker

  SessionStart stays a command hook, because `mcp_tool` is skipped at launch. Opt-in in v1.42.
  Default-on at v2.0 **only if** emgl contended wall p95 is ≥40% below the spawn path over ≥14
  days. *Ruling:* V2Q-2.

### CLM — Calm surfaces
*Goal: SessionStart is orientation, not a maintenance briefing. Advice is plain, runnable, and the
same on every surface.*

- **CLM-1 Budgeted SessionStart digest** `L` · deps OBS-1 — Four parts:
  1. An **integrity and security lane**, always shown and exempt from the budget: trust
     quarantine, index corruption, newer corpus format, native interference.
  2. Orientation: the resume card plus relevant-to-work.
  3. One next-best action.
  4. One counted line, "N items queued — say *tend memory*". Before TND-2 ships, this points at
     doctor.

  Producers become data for doctor and `tend`. Default budget 2,000 chars. Opt-in at v1.41,
  default at v2.0. *Done:* emgl calm median ≤2,000 and 0 truncated sessions over 7 days; a
  corrupt-index fixture still shows the integrity lane.
- **CLM-2 Attention setting** `S` — A calm/full switch plus a per-signal mute list, with integrity
  signals never mutable. It is exposed as a **boolean** plugin `userConfig` option. A string
  `options` picker would stop hippo from loading at all on Claude Code before v2.1.271 (manifest
  reference). The `hippo.json` mirror arrives with SRF-4.
- **CLM-3 Resume card from cleaned queries and served names** `S` · deps HOT-1, OBS-3 — No raw
  XML in "where you left off" (109/494 previews start with `<`). "You leaned on" lists ≤3 served
  memories instead of "+54 more".
- **CLM-4 Onboarding nudge scoped per repo** `S` — Nudge only where install or init happened.
  Dismissal is per repo. This replaces the global every-5th-session counter and the machine-global
  touch file (`memory_session_start.sh:41-72`).
- **CLM-5 Jargon lint** `S` — Fail CI on `[A-Z]{2,5}-\d+` in SKILL.md bodies and descriptions,
  hook-injected text, doctor output, and MCP descriptions and handler output. CHANGELOG, roadmaps
  and comments are allowlisted. *Why:* roadmap ids appear in 18/18 SKILL.md files ✓, 2 of them in
  always-loaded descriptions.
- **CLM-6 One remediation spelling** `M` · deps SRF-1 — Every hint becomes a plain intent phrase
  or a bare `hippo <verb>`. Plugin `bin/` is on the Bash tool's PATH ✓ (docs). A lint forbids
  `python -m/-c memory` in user-facing strings. Fix the two unrunnable hints
  (`session_start_health.py:209-211`, `lint_links.py:382`).
- **CLM-7 A floor with hard edges** `M` · deps RWY-3 — Enforce the declared `floor_lint.section_budgets`, which
  hippo currently reads nowhere. Model **both** native read limits (200 lines or 25 KB, whichever
  first) ✓. Add a trim-safety replay report (S5: would removing this line lose a recall hit?).
  Floor edits go through RWY-3 CAS. *Done:* over 30 days, 0 emgl commits push `MEMORY.md` past
  either limit.
- **CLM-8 Retire the Desktop routing note** `S` · deps PLT-1 — If PLT-1 confirms typed
  `/hippo:*` runs on Desktop, delete `_DESKTOP_SURFACE_NOTE` (1,723 chars ✓), the "Surface
  routing" boilerplate in 11 SKILL.md files, and the `terminal_only` rows in `surfaces.py`. Rewrite
  the README Quickstart install step for Desktop. *May ship as a v1.40.x patch.*

### TND — Tend: derived, consented maintenance
*Goal: one queue, one verb, and a human asked only where evidence actually moved.*

- **TND-1 One derived, ranked maintenance queue** `M` — A gitignored queue over pending captures,
  the reverify worklist, the contradiction inbox, derivation lag, trust drift, broken baselines,
  link rot, the merge digest, and floor overflow. Each entry carries kind, target, evidence, a
  proposed verdict, and the gate that applies.
- **TND-2 The `tend` verb** `L` · deps TND-1, SRF-1 — A skill, an MCP tool, and `hippo tend`
  (`next / apply / snooze / skip`). Consolidate, resolve, the reconsolidate worklist and audit's
  archive flow become routes inside it. Every per-item gate is preserved. *Done:* the dogfood
  corpus reaches an empty queue using only `tend`.
- **TND-3 Derived evidence anchors and quiet arming** `L` · deps TND-1, OBS-5 — A **new anchor**:
  cited-symbol and identifier hits inside the `source_commit..HEAD` hunks of each cited path,
  computed at staleness time with **zero corpus writes**. A memory whose cited path changed but
  whose anchors are intact becomes *quiet*: visible in doctor, not armed, not bannered. *Gate:*
  replay the ~613 real historical verdicts first (242 hippo after removing fixture rows and
  snoozes, plus 371 emgl). At least 50% of graduates must be
  quiet, with ≤5% of fix/demote verdicts suppressed. *Why:* fences are future-only, so 0/1,028
  emgl memories carry one ✓.
- **TND-4 Batched acknowledgement for owner-ruled classes** `M` · deps TND-2, TND-3, V2Q-1 —
  Evidence-quiet graduates and lossless format-only rewrites render as **one git diff, one
  acknowledgement, one commit, one revert**. Fix, demote, supersede and resolve stay per item.
  Each batch surfaces a random 5-item sample for spot-reading, and the sleep report tracks the
  post-graduate fix rate.
- **TND-5 Capture-queue hygiene** `S` — SubagentStop seeds fold into the parent session's seed (7
  of 10 pending on hippo came from subagent-stop). Seeds older than 14 days or 20 sessions move
  to `pending/expired/` with a counted line. They are recoverable and never deleted, so nothing
  captured is destroyed without a human. Inflow and drain are logged. *Why:*
  92% of capture operations are discards; Skyline sits at the 50-seed cap.
- **TND-6 `hippo trust` and per-session batched re-consent** `S` · deps OBS-6 — `hippo trust
  grant|revoke|status` replaces the `python -c mark_trusted` remediation. One re-consent per
  session covers every drifted file with its diff shown. Consent is **never** inferred from git
  authorship, which is spoofable and is the quarantine boundary.
- **TND-7 Scheduled sleep for every opted-in corpus** `S` — `tend` offers a report-only scheduled
  sleep once the backlog passes a threshold. Only hippo has a launchd plist today.

### FMT — corpus_format 6 and a real `migrate`
*Goal: migrations become a previewable verb, and plugin upgrades stop generating per-item corpus
work.*

- **FMT-1 `hippo migrate --check`** `S` · deps RWY-2, SRF-1 — Read-only. Reports flat-shape files
  (53/241 in one local corpus), a missing marker, derivation lag, legacy keys, and policy keys due
  to move. doctor runs it. *Done:* runs on all 5 local corpora; a test asserts zero writes.
- **FMT-2 Derived citation binding moves into the index (dual mode)** `L` · deps RWY-1, RWY-2,
  V2Q-5 — `build_index` derives `cited_paths` and anchors from the body, keyed by derivation
  version. Frontmatter keeps only authored and verdict state (`cited_paths_exclude`,
  `source_commit`, relations). In v1.43 readers **prefer** the index binding while writers still
  write frontmatter. The review packet renders derived cites so PR reviewers lose nothing. *Gate:*
  an emgl parity replay shows ≤1% staleness-verdict difference. *Why:* about 600 per-item
  rederive calls across two bumps; derivations 1, 4 and 6 coexist across local corpora.
- **FMT-3 N-1 compat guard** `S` *(new — critic gap)* — From v1.41, readers **refuse injection**
  on a `corpus_format` newer than they support, instead of warning and injecting anyway. Today
  SessionStart warns loudly (`corpus_format_producer`, `session_start_health.py:108-133`) and
  doctor warns (`doctor_checks_corpus.py:95-99`), but recall still injects. A marker read error
  also degrades silently to format 1 (`provenance_format.py:184-195`). doctor also compares the running plugin version against `installed_plugins.json`
  (growing-pains S6). *Why:* field installs lag about 3 minors (Skyline 334/337 rows on v1.36.0).
  This must be in field installs ≥2 minors before FMT-4.
- **FMT-4 `hippo migrate` apply: one previewed changeset, v5→v6** `L` *breaking* · deps FMT-1,
  FMT-2, FMT-3, RWY-3, V2Q-1:
  - per-file preview diff
  - nested-only frontmatter, derived citation fields dropped
  - `derives-from` → `derives_from`
  - policy keys moved to `hippo.json` but **dual-written** into `.format` through v2.0 for lagging
    readers
  - `.format` stamped last; a "migration in progress" refusal for concurrent writers
  - one commit, undone with `git revert`

  *Done:* rehearsed on the dogfood corpus (derivation 4) and on worktree copies of emgl and Skyline
  with zero hard-set recall diff.
- **FMT-5 Strict format-6 readers** `S` *breaking* — The `.format` marker is required (no implicit
  format 1), and a flat file is refused loudly with the `hippo migrate` remedy. The accessor's
  flat branch is deleted.
- **FMT-6 Sunset legacy readers** `M` *breaking* — Removes:
  - fingerprint-less pre-SEC-6 trust records; doctor names each affected corpus at least one
    minor ahead, because re-consent quarantines until granted
  - pre-SHP-5 symlink repair
  - `legacy_basename_repoints` (`provenance_citations.py:322-406`)
  - the implicit derivation-1 default

### SRF — Surface: one door, fewer verbs
*Goal: one CLI, about 10 tools, 9 skills, at most 12 frozen env vars, one config file, a runtime-only
plugin.*

- **SRF-1 `hippo <verb>` is the one engine entry** `M` · deps RWY-1 — It replaces 65 `-m memory.*`
  call sites ✓. The usage line is generated from `surfaces.py`, where `bin/hippo:62` omits `sleep`
  today.
- **SRF-2 MCP v2 toolset** `L` · deps SRF-1, TND-2, OBS-2 — The tools: `recall`, `new_memory`,
  `inspect` (why, traverse, history, blast radius), `tend`, `doctor` (with audit and secrets scan),
  `setup`, `trust`, `share` (packs, promote, publish, export, import), `dream`, `review`. A
  `recall_hook` tool for HOT-6 is marked internal and unfrozen. Add `readOnlyHint` and
  `destructiveHint` annotations, `outputSchema` and `structuredContent` on the read tools, and real
  `protocolVersion` negotiation. Old names route with a deprecation line. *Done:* `tools/list`
  ≤15,000 chars (36,546 ✓ today).
- **SRF-3 The v2 skill set: 9 verbs** `L` · deps SRF-1, OBS-2, TND-2 — `setup`, `new`, `recall`
  (absorbs `why`), `tend`, `doctor`, `share`, `review`, `dream`, `remove`. 12 current names retire
  into these. Old names become one-line routes through v1.43. **Before anything is removed**, the cut list is confirmed against OBS-2: any old verb with
  ≥5 calls in 30 days on any corpus keeps a named route inside its new verb.
- **SRF-4 Config consolidation** `M` — Corpus policy goes in a committed
  `.claude/memory/hippo.json`. JSON keeps it readable by pre-bootstrap bare `python3`. Machine
  settings move to plugin **`userConfig`**: `attention`, LLM opt-ins, and the model choice. The API
  key becomes `sensitive` and moves out of plaintext `~/.claude/hippo-llm.json` into OS secure
  storage ✓. The frozen env set drops to ≤12: dir overrides,
  `HIPPO_DISABLE=dense,jit,presence,floor-nag`, `HIPPO_TRUST_ALL` and `HIPPO_TRUST_NONGIT`.
  `DREAM_CONTRA_*` gets the prefix. Both spellings are read through v1.43, and doctor lists every
  legacy name it sees. *Caveat:* sensitive values reach hooks and the MCP server's env, but not
  processes Claude runs through the Bash tool, and skill text gets only placeholders. So every
  LLM-calling path has to run in a hook or the MCP server, not in a Bash-run `memory.dream
  --generate` (PLT-1 #4).
- **SRF-5 One machine-wide venv and model cache** `M` — Shared across the terminal and Desktop
  plugin-data dirs and keyed by requirements hash. The cross-encoder loads lazily. A second surface
  downloads nothing. *Done:* fresh footprint ≤450 MB (about 1.18 GB today); the README size claim
  matches the measured download.
- **SRF-6 Python 3.11 floor; drop `rank-bm25`** `S` *breaking* — Align `bootstrap.py:44`,
  `requirements.txt` and `ci.yml:68`, which disagree today, on 3.11–3.14. Use stdlib `tomllib`.
  Drop the third BM25 implementation. The pre-bootstrap path stays **3.9-syntax-clean, enforced by
  lint**, because `_resolve_py.sh` falls back to macOS system `python3`. Batched with SRF-5 into
  **one** re-bootstrap at v2.0.
- **SRF-7 Dream: reduce to the linker unless it earns more** `M` · V2Q-7 — Keep the reversible
  Tier-A unlinked-mention and bridge linker under sleep and `tend`. Cut the generative tier, the
  reverse-replay boost, and deparasite unless OBS-5 shows ≥+0.02 field recall@10 before v1.43.
  *Why:* 0 applied edges in 88 hippo passes; 25 of 3,110 candidates applied on emgl; the dream
  schema alone is 4,576 chars.
- **SRF-8 The v2 removal cut** `M` *breaking* · deps SRF-2, SRF-3, SRF-4, CON-3 — Delete aliases:
  the old tool names (including frozen `why`, `traverse` and `decision_history`, which fold into
  `inspect`), the old skill names, and env names outside the v2 set. A name is removed only if
  OBS-2 shows 0 calls in the last 14 days. Otherwise its window gets **one** dated extension.
  Windows end; they never become permanent shims.

### PLT — Platform catch-up *(new)*
*Goal: stop designing around platform constraints that no longer exist, and start using the
capabilities that replace hippo-built machinery.*

- **PLT-1 Platform-reality spike, with dated receipts** `S` — One sandbox session, each answer
  recorded with the CC version, doc URL and observed behavior, into a new `PLATFORM.md` plus the
  `NATIVE_MEMORY.md` table:
  1. **Desktop:** does a typed `/hippo:doctor` run? Does Desktop plugin install work end to end
     (bootstrap included)?
  2. **`mcp_tool` hooks:** do `${prompt}` and `${session_id}` substitute for UserPromptSubmit?
     What is the call latency into hippo's server and its sequential stdio loop
     (`mcp_server.py:318,333`)? Is the non-blocking error visible on failure, and how does it
     behave while the server is still connecting?
  3. **Native memory:**
     - Does `autoMemoryEnabled:false` or a redirected `autoMemoryDirectory` stop `MEMORY.md`
       always-load through the symlink?
     - Does a project CLAUDE.md `@.claude/memory/MEMORY.md` import load the floor in full? What
       does it cost per subagent spawn, compared with the symlink channel? What does each
       worktree read, its own branch's copy or the shared corpus?
     - Is there a per-turn native recall side-query?
     - Does native stamp `modified:` into hippo files?
  4. **`userConfig`** on both surfaces (version floors 2.1.269 and 2.1.271). Can a `sensitive`
     key reach hippo's LLM paths? Those run today via Bash and `llm_client._api_key`.
  5. **`PostToolBatch` input schema** and **`async` hooks**.

  *This is the gate for HOT-5, HOT-6, CLM-8, NAT-2 and SRF-4.*
- **PLT-2 Declare and check a minimum Claude Code version** `S` — v2 depends on version-gated
  features (`mcp_tool` hooks, `userConfig` options). The README support matrix states the floor,
  and the integrity lane names a too-old harness instead of failing silently.

### NAT — Native-memory coexistence
*Goal: one reviewed writer path into the corpus and one ranked recall channel.*

- **NAT-1 Detect native auto memory** `S` — Read-only checks in doctor and the integrity lane:
  `autoMemoryEnabled` (user and project), `CLAUDE_CODE_DISABLE_AUTO_MEMORY`,
  `autoMemoryDirectory`, corpus files carrying `modified:` with no hippo provenance, and untracked
  corpus files (emgl 30, Skyline 20 — measured lens). The repo has 0 handling of these settings
  today.
- **NAT-2 The v2 coexistence contract** `M` *breaking* · deps PLT-1, NAT-1, V2Q-3 — Choose the
  **floor channel** and the **native writer path** from PLT-1's receipts. *Candidate contract*,
  recommended if the receipts allow:
  - the floor reaches context through a project CLAUDE.md import of the corpus `MEMORY.md`. That
    removes the symlink dependency, but it is a **trade-off, not a free gain**. CLAUDE.md content
    also loads into every non-Explore/Plan subagent, so emgl's 17.9 KB floor would ride every
    spawn, and each worktree would read its own branch's copy. PLT-1 measures both channels before
    either is chosen.
  - native auto memory is redirected by a project `autoMemoryDirectory` to a gitignored inbox that
    `tend` drains as capture seeds

  *Fallback:* keep the symlink and disable native writes per project, but only if the floor
  survives. Existing installs get a doctor remedy and a re-run of `setup`.

### CON — Contract and honesty
*Goal: every shipped surface is classified; contract docs are fact-linted; the pitch matches the
telemetry.*

- **CON-1 Fix doc drift; fact-lint every contract doc** `S` — Fixes:
  - `UPGRADING.md:16` says schema 6; it is 7
  - the `bin` usage line omits `sleep`
  - README says "16 skills"
  - LIF-8, OQ-9 and the DRM-2 "DRAFT" markers are stale

  Extend `test_stability_doc` pins to UPGRADING, README, `plugin/memory/README.md`, and bin usage.
- **CON-2 Positioning: fix the false claims now, date the table later** `S` — *v1.40:* remove
  "recall costs zero tokens" and "no other tool checks whether the code a memory cites has moved"
  (`README.md:19-20,151-152`). Reword native's "static and unranked" (`README.md:328`): native
  loads a static index and lets the model open topic files on demand. Any per-turn native recall
  step waits on PLT-1. Restate the wedge as
  **deterministic, offline, zero-LLM drift verification before injection; $0 inference inside a
  context budget you can see**. *v2.0:* rebuild the comparison table with a dated receipt in every
  cell (add Copilot Memory, Codex, mem0; drop memweave), plus a 90-day landscape-age lint.
- **CON-3 STABILITY v2** `M` *breaking* — Classify every shipped surface:
  - **frozen:** 9 skills, about 10 tool names plus required inputs, ≤12 env vars, format-6 marker
    keys, the `hippo.json` schema, `.usage/`, the `dream:links` grammar, MCP resources, and the
    `hippo` verbs
  - **explicitly unfrozen:** everything else
  - **dropped:** the unstated "names, shapes AND positions" freeze (`mcp_schemas.py:910`,
    `tests/test_mcp_server.py:120`)

  Draft in v1.43, freeze at v2.0.0.
- **CON-4 `ROADMAP.v2.yaml` as the one live ledger** `S` — Charter this train plus the unchartered
  threads (LDG-1, growing-pains S4–S7, owner questions). Put history headers on
  `ROADMAP.enhancements*`.
- **CON-5 Release-gate hardening** `S` *(new — critic gap)* — A CI check flags non-linear merges
  on `main`. Four merge commits landed after protection, one of which produced the untagged
  v1.30/v1.31. Turn on `enforce_admins` for the v2.0.0 cut, and make the tag line content-guard
  `origin/main` for the STABILITY v2 header before `git tag`.
- **CON-6 Official marketplace submission** `S` — After the v2.0 docs land, fill in the
  directory-listing fields in `plugin.json` ✓ and submit. Today hippo is absent from the 315-plugin
  official marketplace.

---

## 6. The release train

The project's format is kept: a theme, a gate, and a dependency-satisfied list. Sizes skew small:
of 59 items, 32 are S, 19 are M, and 8 are L.

### v1.40.0 — "Room, receipts, clean turns"
> **Gate:**
> - `recall.py`, `dream.py`, `staleness.py` and `reconsolidate.py` each have ≥120 lines of
>   headroom.
> - A replay over the retained ledger window, plus fixtures for every machine-turn class, shows 0
>   injections.
> - The OBS-9 hermeticity test is green, and fixture rows are quarantined from KPI readers.
> - Bannered set = armed set on hippo.
> - Rollups and wall stamps are recording on hippo, emgl and Skyline.
> - The README carries neither falsified claim.
> - PLT-1's receipts table is dated and committed.
> - Trust-drift sources are classified with a repro.
> - Owner rulings V2Q-1…V2Q-12 are recorded in this doc.
> - Full suite green under pipefail, with the output file grepped for 0 `FAILED`.

**Items (14):** RWY-1, RWY-2, HOT-1, HOT-4, OBS-1, OBS-2, OBS-3, OBS-4, OBS-6, OBS-9, PLT-1,
NAT-1, CON-1, CON-2 (claim fix). 12 of 14 are S.
**Patch candidate:** CLM-8 as v1.40.x if PLT-1 confirms Desktop slash commands.
**re-bootstrap:** no.

*Why first:* every later claim needs rollups, and every later feature needs runway. The XML leak
and the banner split are the cheapest fixes for the largest measured waste. The spike can delete
whole designs (the socket, the Desktop note) before anyone builds them.

### v1.41.0 — "Earned injection"
> **Gate:**
> - OBS-5 baselines are pinned and `eval_runs.jsonl` exists on 3 corpora **before** HOT-2/HOT-3
>   merge.
> - Off-topic abstention ≥8/11 on hippo and emgl, with recall@10 Δ ≤0.02 on all 3 pinned corpora.
> - emgl hook chars per session ≤12,000.
> - Calm-mode SessionStart median ≤2,000 with the integrity lane intact.
> - Median Python spawns per recall prompt ≤2.
> - Readers refuse a newer corpus format loudly.
> - Both RWY-3 repros are green.
> - The 30-day machine-turn check passes on OBS-1 rollups.
> - The minimum Claude Code version is declared and checked.

**Items (13):** OBS-5 (merges first), HOT-2, HOT-3, HOT-5, CLM-1 (opt-in), CLM-2, CLM-3, CLM-7,
RWY-3, FMT-3, PLT-2, OBS-7 (operational, not a PR), CON-4. If v1.41 runs heavy, CON-4 slips
first.
**re-bootstrap:** no.

### v1.42.0 — "One door, one queue"
> **Gate:**
> - 0 `-m memory.` call sites in skills and hooks.
> - Every old tool, skill, env and config name routes with a deprecation line and is counted by
>   OBS-2.
> - The jargon and remediation lints (CLM-5, CLM-6) are green in required CI.
> - `migrate --check` has run on all 5 local corpora with 0 writes.
> - `tend` drains every queue kind on the dogfood corpus.
> - Warm opt-in passes: correct `session_id` routing, version-mismatch fallback, server killed
>   mid-session, and 0 writes on the served path.

**Items (13):** SRF-1, CLM-4, CLM-5, CLM-6, TND-1, TND-2, TND-5, TND-6, FMT-1, SRF-2, SRF-3,
SRF-4, HOT-6 (opt-in).
**re-bootstrap:** no.

*Why this shape:* the consolidation lands in one release, so the two-minor deprecation window
(v1.42–v1.43) collects usage before anything is removed. Warm recall gets two full minors of
opt-in data before the v2.0 default decision.

### v1.43.0 — "Last 1.x: derived self-tending"
> **Gate:**
> - The TND-3 replay over ~613 real historical verdicts shows ≥50% of graduates quiet and ≤5% of
>   fix/demote suppressed.
> - FMT-2 dual binding shows ≤1% staleness-verdict diff on emgl.
> - HOT-6 opt-in has ≥14 days of contended emgl data.
> - The STABILITY v2 draft is published, listing every legacy name doctor sees.
> - Dream's fate is decided on OBS-5 numbers.

**Items (9):** TND-3, TND-4 (if V2Q-1 = yes), TND-7, FMT-2, RWY-4, RWY-5, SRF-7, OBS-8, CON-3
(draft).
**re-bootstrap:** no.

### v2.0.0 — "Smaller, calmer, honest"
> **Gate:**
> - `hippo migrate` rehearsed on the dogfood corpus (derivation 4) and on worktree copies of emgl
>   and Skyline, with zero hard-set recall diff.
> - `surfaces.py` matches STABILITY v2 exactly.
> - 0 alias calls in OBS-2's last 14 days, or a dated one-minor extension.
> - Calm is the default.
> - HOT-6 is default-on only if its relative gate was met.
> - NAT-2 is applied only if PLT-1 proved the floor still reaches context.
> - The scoreboard covers ≥3 corpora, and every KPI is met or explicitly waived by the owner.
> - `enforce_admins` is on.
> - The tag line content-guards `origin/main`.

**Items (9):** FMT-4, FMT-5, FMT-6, SRF-5, SRF-6, SRF-8, NAT-2, CON-5, CON-6, plus completions:
CON-2 (dated table), CON-3 (freeze), and the CLM-1/HOT-6 default decisions.
**re-bootstrap:** **yes, once** (SRF-5 + SRF-6 batched).

*Why it's small:* the major is mostly deletions and stamps on top of two minors of dual-read and
dual-binding. The riskiest step, migrate apply, has three rehearsals and a one-commit undo.

---

## 7. Breaking changes and migration

| Change | Migration | Window |
|---|---|---|
| Skills 18 → 9 (`setup new recall tend doctor share review dream remove`) | New verbs ship in v1.42; old names route with "now `<verb>`"; OBS-2 confirms usage before removal | v1.42–v1.43 → removed at v2.0 (one dated extension max) |
| MCP tools 28 → about 10; frozen `why`/`traverse`/`decision_history` fold into `inspect` | Aliases warn; UPGRADING lists every `mcp__plugin_hippo_hippo__<old>` permission-allowlist id to update; doctor flags old ids in settings (read-only) | same |
| Frozen env 22 → ≤12; `HIPPO_DISABLE=<list>`; tuning knobs become config | Dual-read; doctor lists legacy names seen; `ci.yml` moves to `HIPPO_DISABLE=dense` | same |
| corpus_format 6 (nested-only, required marker, derived cites out of frontmatter, `derives_from`, policy → `hippo.json`) | `migrate --check` (v1.42) → dual binding (v1.43) → one previewed, revertable changeset (v2.0); FMT-3 guard in the field from v1.41 | flat files refused loudly at v2.0 |
| Legacy readers removed (fingerprint-less trust, pre-SHP-5, v4 repoints, implicit derivation 1) | doctor names each affected corpus a minor ahead; `hippo trust grant` | warnings from v1.42 |
| Native coexistence contract (floor channel + native writer path) | Consented at `setup`; doctor detection from v1.40 | applied at v2.0, only on a green PLT-1 |
| Python ≥3.11; `rank-bm25` dropped | One re-bootstrap; bootstrap names a `uv`-managed Python as the remedy | announced v1.41 → enforced v2.0 |
| SessionStart defaults to calm (not frozen; listed for daily impact) | `attention: full` restores the dump | opt-in from v1.41 |

---

## 8. Owner rulings (RECORDED 2026-10-03)

The critic's sharpest sequencing point was that the drafts deferred the rulings that decide
buildability to the last minor. All twelve were therefore asked up front. **On 2026-10-03 the
owner ratified every recommendation below as written.** V2Q-3 is ratified as a direction:
PLT-1's receipts confirm or revise it before NAT-2 builds. This satisfies the v1.40 gate's
"rulings recorded" clause.

- **V2Q-1 — May one reviewed changeset stand in for N per-item gates?** This touches
  `ROADMAP.yaml:55-56` ("no bulk autonomous sweeps … no blind re-baseline"). *Options:*
  (a) no: strictly per item; TND-4 is dropped and FMT-4 becomes a ~1,024-file walk on emgl;
  (b) yes, only for evidence-quiet graduates and lossless format rewrites, each as one diff and
  one revertable commit; (c) broader. **Rec: (b)**, and amend the invariant text to "no
  *unconsented* re-baseline."
- **V2Q-2 — Is in-session served recall the daemon ED4R-3 bans?** *Options:* (a) yes, keep
  spawn; (b) allow it opt-in via an `mcp_tool` hook into the session's own MCP server, with a
  measured default flip; (c) a machine-wide daemon. **Rec: (b).** The harness owns the server's
  lifetime. It is per session, coordinates nothing across sessions, writes nothing, and spawn
  stays available. Reject (c).
- **V2Q-3 — Native contract direction.** *Options:* (A) disable native auto memory per project;
  (B) redirect native writes to a gitignored inbox that `tend` drains, and deliver the floor by
  CLAUDE.md import; (C) status quo plus doctor warnings. **Rec: (B), confirmed or revised by
  PLT-1.** (C) leaves unreviewed writes in the reviewed corpus.
- **V2Q-4 — Frozen-surface policy.** **Rec:** freeze a core set. New verbs become eligible after
  2 releases plus OBS-2 usage. Drop the unstated "positions" freeze. Lint every contract doc.
- **V2Q-5 — Derived citations out of committed frontmatter (FMT-2)?** **Rec: yes.**
  `source_commit` and `cited_paths_exclude` stay committed, the review packet renders derived
  cites, and a ≤1% parity gate applies.
- **V2Q-6 — Deprecation window length.** **Rec:** two minors, with usage-counted removal and at
  most one dated extension. Windows end; there are no permanent shims (`STABILITY.md:131-132`).
- **V2Q-7 — Dream's fate (SRF-7).** **Rec:** keep the Tier-A linker; cut the generative tier,
  reverse replay and deparasite unless they show ≥+0.02 field recall@10 by v1.43.
- **V2Q-8 — Calm default at v2.0?** **Rec: yes.** With 99.1% of emgl sessions already
  truncated, "full" is a lossy digest ordered by truncation, not by value.
- **V2Q-9 — Python 3.11 floor at v2.0?** **Rec: yes,** with the pre-bootstrap path lint-held
  3.9-clean.
- **V2Q-10 — LDG-1 (open since 2026-08-27).** **Rec:** answer Q1 now (direction A, a derived
  cache) and build it in v2.x. It is non-breaking and emgl-specific (832/1,026 memories cite GRO
  ids).
- **V2Q-11 — Release gate.** **Rec:** a non-linear-merge check now, and `enforce_admins` for the
  v2.0.0 cut.
- **V2Q-12 — Reach inside v2.0?** **Rec:** marketplace submission only. Codex adapter, PR Action
  and importers ship in v2.x on the frozen core.

---

## 9. KPIs (the v2 scoreboard, reported per corpus and never pooled)

| KPI | Definition | Baseline | v2.0 target | Instrument |
|---|---|---|---|---|
| K1 Context per session | hook chars + SessionStart chars per session | emgl ≈38k (29,339 + 8,997) | ≤14,000; SessionStart median ≤2,000, 0% truncated | OBS-1 |
| K2 Machine-turn injections | injections on machine turns (all classes in HOT-1) | 282/486 (58%) emgl ✓ | 0 | OBS-1 trigger class |
| K3 Abstention | off-topic fixtures abstaining; field share reported | 0/11; 0/486 ✓ | ≥8/11, with K4 non-decreasing | `abstention_fixtures`, OBS-1 |
| K4 Injection precision (KPI-2) | injected memories whose cited file was touched in-session | emgl 1.7%, hippo ~10% | emgl ≥5%, hippo ≥20% | `outcome.injection_precision` |
| K5 Hook wall latency (KPI-3) | shell-measured UserPromptSubmit wall time | logged p95 2,269 ms emgl ✓ + unlogged start | warm contended p95 ≥40% below spawn; spawns per prompt ≤2 | OBS-4 |
| K6 Maintenance toil (KPI-6) | graduate share of human verdicts; per-item writes per derivation bump | hippo 76% (185/242) / emgl 80% (297/371) ✓ (fixture rows removed, snoozes excluded); ~600 across two bumps | ≤50%; 0 | reconsolidation ledger (after OBS-9); OBS-2 |
| K7 Trust noise | SessionStarts showing `trust_drift` | 97% emgl / 59% hippo ✓ | set after OBS-6 diagnosis | `injection_producers` |
| K8 Field recall (KPI-4) | recall@10 / MRR@10 on pinned field sets | emgl 0.577 / 0.265 (stale set) | no regression >0.02 on any corpus; stretch emgl ≥0.70 | OBS-5 |
| K9 Surface weight | skills / tools / `tools/list` chars / frozen env / user-facing ids / raw-python hints | 18 / 28 / 36,546 ✓ / 22 / ids in 18/18 SKILL.md ✓ / 51 | 9 / about 10 / ≤15,000 / ≤12 / 0 / 0 | registry and lint tests |
| K10 Evidence retention | days of recall history on the largest corpus | ~2.3 | ≥90 | OBS-1 |
| K11 Migration health | local corpora at format 6 and the current derivation | mixed (1/4/6/unset) | 5/5 | FMT-1 |
| K12 Engine size | `plugin/memory` LOC excluding `_vendor` | 54,395 | net shrink at v2.0, target recomputed after the real RWY-4/SRF-7 cut | `wc` in CI |

---

## 10. Cuts, kills, and deferrals

**What v2 stops doing:**
- recall on machine turns
- the 26-producer dump as the default
- the Desktop routing note (pending PLT-1)
- 11 zero-use tools as standalone names
- 12 current skill names, which retire into the 9 verbs (`why` → `recall`)
- the `rederive` verb and the `cite_derivation` nag (after FMT-2)
- raw `python -m/-c memory` remediation
- roadmap ids in user-facing text
- legacy readers
- `rank-bm25`
- Python 3.9/3.10
- dream's generative tier (pending V2Q-7)
- about 10 tuning and opt-in knobs as *frozen* env vars

**Killed proposals from the drafts** (the ids below are the drafts' own numbering, not this
document's):
- *LEAP SCB-5:* a full DreamBench-SWE run (180 tasks × 2 arms) contradicts the capped-spend rule.
- *DISTILL TND-6:* already shipped as `record_authored_write`.
- *RUN-4/SRF-5 as written:* would break the runtime.
- *LEAP WRM-3:* superseded by HOT-5's options.
- *LEAP TND-2 as written:* fences don't exist to compute it, and a 14-day stale-share gate rewards
  rubber-stamping.
- *LEAP K4's absolute warm latency targets:* contradicted by measurement.
- *Redirect skills through v2.3:* a permanent shim.
- *DISTILL RWY-2's private and lazy-import counters:* cosmetic.
- *The per-session Unix socket as the primary warm design:* superseded by `mcp_tool` hooks and
  kept as the fallback.

**Deferred to v2.x** (good, non-breaking, after the core freezes):
- Codex MCP adapter
- `hippo review --ci` as a GitHub Action / PR comment bot
- graduation importers with write mode (native dir, claude-mem, `~/.codex/memories`)
- LDG-1 ledger join and `recall --for-issue` (S4)
- **a hippo mod band for human-facing maintenance signals**: zero model-context cost, drawn in the
  terminal and Desktop, CC ≥2.1.287 ✓. This is the natural follow-on to CLM-1, which moves
  maintenance signals out of model context.
- PostToolUse fully on the warm path, if HOT-5 chose (b) or (c)
- the subpackage restructure
- a small sampled coding-task benchmark arm
- the JIT lane decision, which needs a pin-using corpus

**Still not ripe:** GRA-7 PPR (multi-hop set n=0), CAP-5 auto-MOC (invariant conflict), LIF-7
schema, EVD-3, GRF-5, salience and outcome-prior flips (A/Bs 0.0 and −0.04), pack investment (0
calls), team features (all corpora single-author), Windows (OQ-2), a cloud-session bridge (needs
a network endpoint), hosted sync, a graph DB, and LoCoMo/LongMemEval.

---

## 11. What v2.0.0 **is** and **is not**

**v2.0.0 IS:**
- **Quiet by default:** human turns only, calibrated abstention, budgeted injection, and a
  ≤2,000-char SessionStart whose integrity lane can't be muted.
- **Self-tending within the invariants:** derived staleness anchors, derivation bumps that cost a
  re-index, one `tend` queue, and batched acknowledgement only where the owner ruled it.
- **Smaller and linted:** one `hippo <verb>`, about 10 annotated MCP tools, 9 skills, at most 12
  frozen env vars, one config file, corpus_format 6, and a STABILITY v2 that classifies every
  surface.
- **Coexisting explicitly with native memory:** one reviewed writer path, one ranked recall
  channel, and a floor channel proven by receipts.
- **Measured on the field:** a per-corpus scoreboard on ≥3 corpora in every release note.

**v2.0.0 is NOT:**
- an LLM on the hot path
- autonomous corpus writes
- hosted or team-cloud anything
- a cross-harness product (that is v2.x)
- a new retrieval engine (no PPR, no graph DB)
- a chat-memory benchmark contender
- Windows-supported

---

## 12. Risks

- **Calm or abstention hides something that mattered.** *Mitigation:* the integrity lane is
  exempt from the budget, HOT-2 ships only inside the 3-corpus recall gate, and `attention: full`
  is one setting away.
- **n=1 overfit.** emgl is 93% of traffic. *Mitigation:* every gate is measured on ≥3 corpora,
  and a change that helps emgl but regresses Skyline or hippo does not ship.
- **Format-6 migration at production scale.** emgl has about 1,024 files, 32 worktrees and about
  51 sessions a day. *Mitigation:* FMT-3 guard in the field for 2 minors, RWY-3 CAS plus a
  "migration in progress" refusal, three rehearsals, `.format` stamped last, and a one-commit
  revert.
- **Mixed-version corpora.** Lagging installs could silently read format 6 without cites.
  *Mitigation:* FMT-3, and writers keep frontmatter cites until v2.0.
- **Warm recall degrades silently or serves skew.** *Mitigation:* the version handshake,
  path logging surfaced in doctor, a short budget with a circuit breaker, and a relative default
  gate.
- **Removed names break permission allowlists or scripts.** *Mitigation:* a two-minor window,
  usage-counted removal, and doctor reading allowlists (read-only).
- **Batched acknowledgement turns into rubber-stamping.** *Mitigation:* only the owner-ruled
  classes, a replay-validated anchor, sampled spot-reads, and a tracked post-graduate fix rate.
- **Native behavior moves again.** *Mitigation:* PLT-1 receipts are dated, NAT-1 detects instead
  of assuming, and the contract is consented and idempotent.
- **Solo bandwidth: 59 items.** *Mitigation:* 32 of the 59 items are S, and v1.40–v1.42 deliver
  user-visible value without breaking anything. If time runs short, cut in this order: TND-7,
  OBS-8, SRF-7 (keep dream as is), TND-4 (keep per-item). None of these blocks the cut.

---

## 13. Top 5 highest-leverage moves

1. **Run PLT-1 before building anything it could delete.** Three platform shifts can remove
   whole designs:
   - `mcp_tool` hooks replace the warm socket and its unsolved session keying
   - Desktop slash commands retire a 1,723-char note paid in every Desktop session
   - native auto memory's real semantics decide the floor channel

   Size S, and it gates five items.
2. **Stop the measured waste and make it durable (v1.40).** The machine-turn fix removes 58% of
   emgl recalls and about 1.06M injected chars. Also: one staleness truth, rotation-proof
   rollups, and shell wall time. All are small, and every later claim depends on them.
3. **Earned injection against a pinned field baseline (v1.41).** Order matters: 3-corpus
   baselines → abstention → budgets → calm digest with an integrity lane. This addresses 99.1% cap
   saturation and 0 abstentions without touching the frozen surface.
4. **Make maintenance derived, not written.** Derived citation binding with a parity gate removes
   the ~600-call class of migration. Replay-validated evidence anchors quiet the ~3-in-4-graduate
   treadmill. First make the ledgers trustworthy, because hippo's own verdict ledger is mostly
   test fixtures today. One `tend` queue replaces about 11–14 verbs. Diagnose trust drift before treating
   it.
5. **Spend the major on subtraction.** One door, about 10 tools, 9 skills, ≤12 env vars, format 6,
   STABILITY v2, all behind two-minor, usage-counted windows. Most of the 2.0 cut is deletion.

---

*Prepared as a proposal. No code, `ROADMAP.yaml`, `STABILITY.md`, or branch-protection changes
were made. `file:line` references and ✓-marked receipts were verified against the working tree at
v1.39.0 (`0d65178`), the local telemetry ledgers (hippo, em-growth-labs), session transcripts,
and the official Claude Code docs as fetched on 2026-10-03.*
