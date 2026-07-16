---
name: hippo-enh-t7-learned-ranking
description: "Enhancement Tier T7 (v1.7.0, \"Learned ranking\") — session 2026-07-09: RUL-7 shipped (PR #15 MERGED 5dcbc2a) + TAGGED v1.7.0 (3042cdd); then owner commissioned RET-8 (PR #16 MERGED f2d1979: category eval + tracked gates + edge-aware eval) and RATIFIED SIG-5 = salience default OFF (still the default today). The tier is COMPLETE since: SIG-6 shipped (4dc3256 — draft_abstention_fixtures/confirm_hard_set_row) and RUL-6 shipped once the owner cleared the LIF-7 CAP-soak gate (/hippo:promote-rule is live). Historical record."
metadata: 
  node_type: memory
  type: project
  originSessionId: 5e683ae7-b857-47d3-a37d-daafd56e45e2
  last_verified: "2026-07-13T04:49:53.242134+00:00"
  cited_paths: ["ROADMAP.enhancements.yaml", "plugin/memory/lint_floor.py", "plugin/memory/recall.py", "plugin/memory/export_agents.py", "tests/test_export_agents.py", "ROADMAP.yaml", ".github/workflows/release.yml", "plugin/memory/eval_recall.py", "tests/test_fixture_drafts.py"]
  source_commit: "bf57e6c1b86d1b0ef1c0090ab281459ceb8ab286"
  source_commit_time: 1784211707
---

Tier T7 (v1.7.0, "Learned ranking (salience-keystone-gated)") — session 2026-07-09.
**Partial by design: shipped RUL-7 (1 of 4 items — the only one with no external
blocker). [PR #15](https://github.com/youknowfred/hippo/pull/15) squash-MERGED to main
as `5dcbc2a` on 2026-07-09 (owner directed; all 6 required CI checks green: dense,
4-way hermetic matrix, shellcheck). Head branch `enh-t7-rul7` deleted remote (gh api,
avoiding the push hang) + local — only `main` remains, local AND remote. The branch's
2 pre-squash commits `52080f7`/`ef47ae0` are now UNREACHABLE from main (T6 lesson —
browsable only on the PR's Commits tab); main holds ONE commit for the tier's work so
far. Post-merge suite re-verified directly on main: 1462 passed / 12 deselected,
identical to the PR's own count.** Tier status flipped planned → **in_progress** (NOT
done — done_means requires the SIG-5 salience decision, which cannot be made yet);
dated gate-state comments written into ROADMAP.enhancements.yaml on the tier and on
RUL-7.

GATE STATE, re-verified against the live tree this session (the post-T2 audit_note was
four tiers stale; every claim held):
- **SIG-5 + SIG-6 — BLOCKED on RET-8, confirmed still greenfield**: zero "category"
  hits anywhere in plugin/tests/fixtures; eval_recall's fixture loader still reads only
  {query, expected}. Building RET-8 (cross-file ROADMAP.v1 v0.9.0: category-tagged hard
  set, per-category recall/MRR in the report, precision@k + abstention_rate promoted to
  tracked gates) was deliberately NOT attempted — it is beyond T7's scope and needs
  owner sign-off first. SIG-5's implementation_notes carry the three deliverables.
- **RUL-6 — BLOCKED on LIF-7's CAP-soak gate, confirmed un-cleared**: ROADMAP.v1 §3
  still says LIF-7 "NOT CLEARED — DEFER post-v1"; no dated owner decision anywhere in
  the enhancement roadmap. It is an OWNER JUDGMENT with no wired metric — NOT proxied
  with soak.soak_status() (its docstring disclaims that role), per the audit_note's
  explicit instruction. RUL-6's mechanics are now cheap when it clears: this session
  shipped the shared derivation it needs (see below).
- **RUL-7 — deps (RUL-2) done; every audited seam verified live before coding**
  (floor_memory_names lint_floor.py:90, fused_floor_names/portable_floor_producer
  recall.py:1771/1800, archive._SCAN_TARGETS omitted AGENTS.md as claimed,
  GOV_GLOBS included it, _rule_scoped_files was hard-coded to .claude/rules/*.md).
  **No premise correction needed this tier** — first tier since T2 where all notes held.

SHIPPED (one id-prefixed commit each, suite green after each):
- **RUL-7** `52080f7` — AGENTS.md fan-out. plugin/memory/export_agents.py:
  export_agents(*, memory_dir, repo_root) renders the PROJECT floor (project tier ONLY —
  user/private tiers never enter a repo-committed file, the no-git-leak invariant;
  portable_floor_producer stays the cross-tier CONTEXT channel) as a complete proposed
  AGENTS.md + unified diff, and NEVER writes — apply is the skill's separate,
  explicitly-approved step (inv1/inv4). Marker-delimited managed block
  (`<!-- hippo:agents-export:begin/end -->`): content outside survives byte-verbatim
  (propose a diff, never regenerate); FOREIGN frontmatter preserved verbatim (ours,
  detected by a "hippo:agents-export" fm-comment marker, regenerates); begin-without-end
  → refusal, never a guessed splice. Retired (invalid_after) floor memories skip with a
  reason (mirrors promote); wikilinks rewrite to backtick stems so exported sections
  cite memories in exactly the shape the governance scanners track. Section heading =
  `` ## `stem` `` (the citation); "Applies to:" line = derived globs.
  **rules_plane.derive_paths_globs(cited_paths, universe)** is the RUL-6-SHARED
  derivation with the over-scoping cap (DERIVE_OVERSCOPE_FACTOR=3): single citation
  stays a literal path (a glob matching exactly itself — exact drift detection);
  same-dir/same-ext 2+ groups collapse to dir/*.ext only when matches ≤ 3× cited;
  ** never emitted; collapse never crosses directories; missing paths flagged+excluded;
  empty universe → literals + no_oracle flag. Frontmatter globs JSON-quoted (bare
  leading * is a YAML alias — the RCH-2 lesson applied at write time).
  Drift-check = ALL REUSE (criterion 2): archive._SCAN_TARGETS += AGENTS.md (exported
  memories archive-protected), rules_plane._rule_scoped_files += AGENTS.md (frontmatter
  globs join RUL-2's dead-glob leg), Applies-to code-ext literals rot-check via the
  existing code-ref leg (GOV_GLOBS always covered AGENTS.md) — all loud in the EXISTING
  doctor + SessionStart channels; zero new reporting surface, hot path untouched.
  New skill plugin/skills/export-agents/SKILL.md (propose→review→apply-on-explicit-yes;
  AGENTS.md only, no auto-sync) joins the pinned skills contract → **14 skills**.
- Ledger flip `ef47ae0` — RUL-7 → done, tier → in_progress, dated gate-state comments.

ENGINE STATE: suite **1462 passed / 12 deselected** (T6 baseline 1444; +18 in new
tests/test_export_agents.py — 6 derivation contract, 9 criterion-1/propose-only,
3 criterion-2/drift). Schemas ALL unchanged: corpus_format 4, index SCHEMA_VERSION 6,
capture seed 2. MCP untouched (5 tools / 3 resources pinned). Eval untouched (no
ranking change anywhere). **Re-bootstrap: NO** (requirements.txt untouched).
SMOKE: scratch repo driving the SKILL.md snippets verbatim — render read-only (3
sections: collapse src/*.py, literal src/solo.py, unscoped) → apply → idempotent
re-render → hand afterword survives → git mv of a cited file flags BOTH drift legs in
the real SessionStart producer ("references `src/solo.py` — path no longer in the
repo" + "scopes paths: 'src/solo.py' — matches nothing") → both trees leak-free.

DECISIONS / GOTCHAS:
(1) Tier question the notes left open — WHICH floor exports: project tier only.
fused_floor_names/portable_floor_producer span user+private tiers, but AGENTS.md is
repo-committed → rendering those tiers into it would violate the no-git-leak invariant.
Recorded in the module docstring.
(2) The managed-block + foreign-frontmatter splice is what makes "PROPOSE a diff, never
regenerate" literal: byte-verbatim preservation via a raw frontmatter splitter (NOT
provenance.split_frontmatter, which normalizes) — reassembling parsed lines would break
verbatim-ness.
(3) A literal path IS a glob (matches exactly itself) — that's what makes single
citations exact drift detectors through the dead-glob leg, and why derivation falls
back to literals instead of refusing on over-scope.
(4) _PATH_REF_RE only rot-checks CODE extensions (.md deliberately excluded, RUL-1's
job) — so the frontmatter literal globs are the drift leg for non-code cited paths;
the two legs are complementary, not redundant.
(5) shellcheck CI lane covers only plugin/hooks/*.sh + plugin/bin/hippo — untouched;
SKILL.md bash blocks are covered by the in-suite bash -n contract test instead.
(6) Not taken (recorded): building RET-8 inside this session (legitimate per SIG-5's
notes but out-of-scope without owner sign-off — the session prompt said confirm first,
and under-shipping honestly beats scope-grabbing); an MCP export tool (skill/CLI only,
smallest blast radius — same call as T6's RCH-4 decision); truncating exported bodies
(portable_floor_producer's bounds are for CONTEXT injection; a reviewed committed file
renders full bodies).

RET-8 ADDENDUM (same session, later): **the owner COMMISSIONED RET-8 — built and
[PR #16](https://github.com/youknowfred/hippo/pull/16) squash-MERGED to main as
`f2d1979` on 2026-07-09 (all 7 checks green twice — on the RET-8 head and again on
the SIG-5 decision head; branch deleted remote+local, only main remains; pre-squash
commits `4c6b1c9`+`43f2ad9` unreachable from main, browsable on the PR).** Category loader (canonical set single-hop/multi-hop/temporal/update/
abstention, absent→single-hop, unknown tags data-driven for SIG-6) + by_category
recall/MRR (delegation = one scoring path) + precision@10/abstention_rate PROMOTED to
tracked fixture-gated entries (skip semantics = hard-set gates'; thresholds are
MEASURED tripwires 0.12/0.30 — the RET-1 "near 1.0" abstention aspiration was never
real: 0.3333 both backends on the pack corpus). TWO premise corrections found live:
(1) eval was EDGE-BLIND — every metric called recall() with a bare index and no
index_dir (the shape _expand_neighbors documents as no-edges), so GRA-1/GRA-4 never
ran inside eval; index_dir now threads through every metric. (2) main()'s ambient
fixture defaults applied even with explicit --memory-dir — a false-verdict source once
gated; CLI hermeticity guard added (explicit --memory-dir → only explicit fixtures).
FIRST INSTRUMENT FINDING (recorded in the fixture header + ROADMAP.yaml note):
multi-hop = 1.0 bm25-only vs 0.0 dense+bm25 — under dense every entry has a primary
rank so the knee's graph exemption never fires and GRA-1 expansion is structurally
suppressed; a GRA-1/GRA-7 ranking gap, deliberately NOT fixed here (ranking change =
eval-gated GRA work, out of RET-8's commission). SIG-5 EVIDENCE RUN (both ways,
dense, golden+packs): byte-identical on every metric — fixture corpora carry no
usage/staleness/recency signal for the priors to act on. **SIG-5 DECIDED SAME DAY,
OWNER-RATIFIED: salience default stays OFF** (commit `43f2ad9` on the PR branch,
rides in the squash) — dated decision + both-ways numbers + revisit trigger (a
lived-in corpus re-run via the same substrate; SIG-6's fixtures the natural vehicle)
recorded on SIG-5's item AND in ED-2's decision block. **SIG-5 status: done** (its
deliverable WAS the decision, either way); HIPPO_SALIENCE stays the opt-in; the T7
gate is RESOLVED (decided-off) — nothing in the tier ships as an always-on prior.
Suite 1469 / 12 deselected (+7); dense-lane twin (-m "network or slow") 9 passed
locally; bare CLI green on the exact CI seed recipe both backends (corpus=22 —
CONVENTIONS.md is copied by CI's find but never indexed, no frontmatter).

NEXT: **SIG-6 is now the ONE freely buildable T7 item** (SIG-3 done, RET-8 done,
SIG-5 decided): auto-DRAFT candidate RET-7 fixtures from telemetry.abstention_backlog
clusters at audit/consolidate time, confirmed rows tagged category: abstention —
read its implementation_notes first (the expected side is a JUDGMENT; never fabricate
a memory to make a fixture pass). RUL-6 still waits on the owner's dated LIF-7
CAP-soak decision. Tier T7 flips to done only when SIG-6 ships and the RUL-6 gate
resolves (ship or explicit defer). Also on the radar: the RET-8-surfaced GRA-1
dense-side knee suppression (multi-hop 0.0 under dense) — GRA-1/GRA-7 ranking work,
eval-gated by the new per-category instrument. Also still open from earlier tiers: RCH-5 install/update on the v0.8.0 trust
spine (SEC-5/6/7; the negative-capability pin in test_packs is the tripwire), and the
two minor v0.3.0 follow-ups. When T7's gates clear, the tier flips to done only with
the SIG-5 decision + eval evidence recorded per its capstone spec. **TAGGED v1.7.0
(owner-directed, same session): release commit `3042cdd` "v1.7.0: version sync +
CHANGELOG (enhancement tiers T6–T7)" directly on main per the 90600d7/v1.5.0 precedent
— both manifests 1.5.0→1.7.0 in lockstep, ONE `## v1.7.0` CHANGELOG entry covering
T6+T7 (no phantom v1.6.0 heading — that version was never released; the entry states
T7 is partial and which items remain gated), annotated tag on the release commit,
release.yml four-way gate GREEN (9s, same as v1.5.0's run). The CHANGELOG honesty
line to keep: "no ranking behavior changed in this release; the salience default
stays OFF."**

DEFERRED / BLOCKED this tier: SIG-5 (RET-8), SIG-6 (RET-8, then after SIG-5),
RUL-6 (LIF-7 owner gate) — all with reasons above; none forced, no evidence faked.

**ALL THREE GATES SINCE CLEARED — T7 IS DONE (re-verified 2026-07-16):** SIG-5 was ratified
salience-default-OFF (and OFF is still the shipped default — `eval_recall.py` records
"HIPPO_SALIENCE is MSR-5 — planned, not shipped"); **SIG-6 SHIPPED** on its own PR #17,
squash-merged `4dc3256` (`eval_recall.draft_abstention_fixtures` + `confirm_hard_set_row`
are live and drive /hippo:consolidate's Step 5); and **RUL-6 SHIPPED** once the owner cleared
the LIF-7 CAP-soak gate in the v0.8.0 SEC-tail session ([[hippo-v080-sec-tail]]) — the
`/hippo:promote-rule` skill and `promote_rule.promote_to_rule` are live. Also-open items this
memo listed are closed too: RCH-5 install/update landed on the trust spine, and both v0.3.0
follow-ups are done ([[hippo-v030-open-followups]]). The NEXT block above is the 2026-07-09
handoff record, not today's frontier — the chain's tip is [[hippo-enh-t15-sleep]].

T7 CLOSE-OUT (2026-07-09/10 session, see [[hippo-v080-trust-spine]] for the rest of
that session): **SIG-6 SHIPPED** — eval_recall.draft_abstention_fixtures (backlog
clusters → drafts queue in the gitignored pending dir, expected ALWAYS empty — the
judgment is never automated) + eval_recall.confirm_hard_set_row (per-item admission,
refuses fabricated stems/dups/empty; rows tagged category: abstention), wired into
audit Phase 0.6 + consolidate Step 5; PR #17 (branch enh-t7-sig6, commits d1a893a +
ledger 05a6dbb), all 7 CI checks green, handed to owner un-merged. **RUL-6 EXPLICITLY
DEFERRED by owner decision** (asked via AskUserQuestion same session): LIF-7 CAP-soak
NOT cleared (~1 day field soak); dated defer note on the item. **Tier T7 → done** per
its own done_means conditional ("if the LIF-7 gate has cleared" excludes RUL-6).
Suite at SIG-6 ship: 1491/12 (+22 tests/test_fixture_drafts.py). Gotchas: drafts
live in .claude/.memory-pending/ (SEC-3 self-ignoring — raw query_preview text must
never be `git add .`-able), NOT .audit-fixtures (that dir is committable BECAUSE
every row passed the confirm gate); fastembed is installed locally so hermetic tests
must set HIPPO_DISABLE_DENSE=1 or dense builds silently.

Related: [[hippo-enh-t6-reach]], [[hippo-enhancement-roadmap]],
[[hippo-v1-roadmap-proposal]].
