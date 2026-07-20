---
name: hippo-enh-t2-rules-bridge
description: "Enhancement Tier T2 (v1.2.0, \"The rules bridge\") — shipped 6/6 items incl. RUL-0 paths: CONFIRMED; next tier T3 (retrieval precision)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 8290772b-b5b1-43a3-856d-416ac6e3302d
  last_verified: "2026-07-16T14:40:03.428277+00:00"
  verified_by: "81190215_youknowfred_users.noreply.github.com@2026-07-18T23:11:09.547289+00:00"
  cited_paths: ["plugin/memory/rules_plane.py", "tests/test_rules_plane.py", "plugin/.claude-plugin/plugin.json", "ROADMAP.enhancements.yaml"]
  source_commit: "05b0c28349a9c3088a7870e5c0eee49859ce9bab"
  source_commit_time: 1784415607
---

Tier T2 (v1.2.0, "The rules bridge") — session 2026-07-08. **COMPLETE, 6/6,
[PR #10](https://github.com/youknowfred/hippo/pull/10) squash-MERGED to main as
`c7873f1`** (owner-approved; head branch deleted; all CI green incl. the audit commit).
Shipped from branch `enh-t2-rules-bridge` (9 commits on top of T1's `ab8c6cd`). Same
session also deleted the long-merged stray branch `release-v0.7.0-team-and-fleet` (tip
= PR #8's merged head; the v0.7.0 TAG remains) — only `main` exists on the remote now.

> **SHA note (added 2026-07-20):** the per-item commit SHAs listed below were BRANCH commits,
> squashed away at merge and the branch deleted — they never existed on `main` and are gone by
> design, not broken. Only the squash-merge SHA above is resolvable. Navigate by PR number or
> commit subject. See [[pre-launch-commit-shas-are-dead-in-this-repo]].

SHIPPED THIS SESSION (one id-prefixed commit each, suite green after each):
- **RUL-0** `e3a78a2` — spike CONFIRMED (ED-3 resolved, dated finding in the YAML): docs
  (code.claude.com/docs/en/memory, feature since 2.0.64), binary strings in the installed
  2.1.96 harness, AND a LIVE in-harness probe — a `paths:`-scoped `.claude/rules` file was
  injected as a system-reminder right after a Read of a matching file, **including inside a
  Task subagent** (which got NO unscoped rules at start → confirms RUL-5's premise; scoped
  rules DO reach subagents on matching Read). Headless `claude -p` probe was blocked by
  401s (nested CLI can't use the session proxy or refresh OAuth) — the in-session
  probe-rule + fresh-subagent trick worked instead.
- **RUL-1** `cdecae6` — NEW module `plugin/memory/rules_plane.py` (the canonical GOV_GLOBS
  surface as importable API) + `conflict_radar`: typed-edge leg (rule cites memory another
  memory supersedes/contradicts — fires always) + strength<0.15 leg **gated on the
  5-session soak bar** (fresh clones never nagged; audit keeps its ungated join). Surfaced
  at `rules_conflict` producer + `check_rules_conflicts` doctor twin.
- **RUL-2** `04c0835` — `rules_rot`: backtick code-ref rot over gov files (path leg +
  conservative dotted `module.symbol` leg — only a uniquely-resolved module with a missing
  def/class/assign is a finding) + the RUL-0-enabled dead-`paths:`-glob leg (first paths:
  frontmatter parser in the codebase; glob→regex with `**`/braces; universe = tracked ∪
  untracked-unignored). `rules_rot` producer + `check_rules_plane_rot`.
- **RUL-3** `a69e4b6` (+ fix `ec81566`) — write-time dedup vs the rules plane:
  `rule_dup_candidates` = asymmetric CONTAINMENT (|draft∩block|/|draft| ≥ 0.6, ≥5 draft
  tokens) of draft tokens in one gov block; warn-only `rule_neighbors` in
  write_memory/check_candidate + both CLI renders + /hippo:new SKILL.md section. The fix
  commit: main's --check path now resolves repo_root (was silently skipping the leg) and
  killed a LATENT NameError — check_candidate's memory_dir=None arm called resolve_dirs()
  bare with no module-level import since CAP-3.
- **RUL-4** `f967eff` — rules as recall SOURCE: gitignored `<index_dir>/rules.json`
  (heading-scoped sections, sig fast-path, built at SessionStart's offline moment) +
  `_rules_source_hits` on the hot path (ONE JSON read + set arithmetic). Relevance =
  QUERY CONTAINMENT (|q∩section|/|q|, floor 0.6, `HIPPO_RULES_RECALL_FLOOR`) — **NOT BM25:
  a 1-5-section rules plane has degenerate Okapi idf** (caught live when the first
  BM25-normalized cut scored 0.0 on a 2-section fixture). Pointers APPEND after organic
  top-k (never displace; surface on corpus ABSTENTION too), labelled "(rule)"
  (_CORPUS_MARKER) / "rule — governance plane" (recall_view); `check_rules_source` keeps
  absent-cache degradation legible. Organic byte-identity with/without cache is pinned.
- **RUL-5** `9e9a129` — MCP RESOURCES capability: `hippo://floor` (project MEMORY.md +
  portable tier floor; the subagent pull path) + `hippo://rules-view` (RUL-1+RUL-2
  rendered). SEC-1: untrusted corpus → explicit WITHHELD notice covering BOTH in-repo
  parts (project floor AND private tier — they ride the same clone). NATIVE_MEMORY.md
  notes resources are agent-pulled, never a second always-load channel.

TIER STATE: 6/6 done; T2 status flipped to `done` (`1665ec4`); done_means MET.
RUL-6/RUL-7 stay T7 — RUL-0 unblocked only their harness-side premise; LIF-7 gate still
closed. 31 items remain planned (T3–T7).
ENGINE STATE: suite **1203 passed / 11 deselected** (T1 baseline 1139; +64 across
tests/test_rules_plane.py (new, 50) + creation-convention + mcp-server pins).
corpus_format 2, index schema 3 unchanged (rules.json is its OWN schema-1 side-cache — no
manifest bump); **re-bootstrap NO** (requirements.txt untouched). NO plugin.json/CHANGELOG
bump — T1 set the precedent (still 0.7.0; release mechanics stay separate from tier work).
DECISIONS / GOTCHAS: (1) rules_plane adopts audit's GOV_GLOBS (incl. AGENTS.md);
`archive._SCAN_TARGETS` deliberately NOT touched — different semantics (fail-closed
archive gate + docs/prompts), and adding AGENTS.md there is RUL-7's job (T7). Warning
surfaces fail toward SILENCE on unreadable gov files (opposite of archive's fail-closed).
(2) End-to-end smoke on a scratch repo exercised all seven surfaces together and caught
the RUL-3 CLI gap. (3) mcp tests: resolve_dirs' repo_root comes from CLAUDE_PROJECT_DIR
or cwd — hermetic tests MUST set CLAUDE_PROJECT_DIR or they scan the live hippo repo.
POST-T2 DRY AUDIT (same session, commit `5b2c963` on the PR): T3–T7 fully re-audited
against the live tree (5-agent fan-out). Every remaining planned item (31) now carries a
BINDING `implementation_notes:` field in ROADMAP.enhancements.yaml (verified seams, T2
rule-pointer-lane guards, attach points, schema-bump mechanics, test pins) + per-tier
`audit_note:` blocks + a meta.audits record; session_protocol.pick_up says read them
first. Premise corrections landed in-file (RCL-2/3/6, GOV-1/3, GRW-4/7). HARD GATES
confirmed closed: RET-8 blocks SIG-5/SIG-6 (build RET-8 first), LIF-7 blocks RUL-6
(owner-judgment gate — soak_status is NOT a proxy), v0.8.0 trust spine blocks RCH-5
install/update (extract-only safe early); RUL-7 is the only T7 item buildable today.
NEXT: **Tier T3 "Retrieval precision" (v1.3.0)** — RCL-1 (per-query dense/lexical intent
routing), RCL-2 (floor-dedup collapse + cooldown; interacts with RUL-4's appended rule
pointers), RCL-3, RCL-4, RCL-6 (carries the tier's one SCHEMA_VERSION bump), RCL-5.
Read each item's implementation_notes + T3's audit_note before coding.
DEFERRED / BLOCKED: none in T2.
Related: [[hippo-enh-t1-signals]], [[hippo-enhancement-roadmap]], [[hippo-v1-roadmap-proposal]].
