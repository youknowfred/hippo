---
name: hippo-enh-t1-signals
description: "Enhancement Tier T1 (v1.1.0, \"Positive context & dark signals\") — shipped 4/4 items; next tier T2 (RUL-0 spike first)"
metadata: 
  node_type: memory
  type: project
  originSessionId: f4077387-baf8-4f74-9935-6a530806e406
  last_verified: "2026-07-13T04:49:53.161921+00:00"
  cited_paths: ["plugin/memory/session_start.py", "plugin/memory/staleness.py", "plugin/hooks/memory_post_tool.sh", "plugin/hooks/hooks.json", "plugin/memory/outcome.py", "tests/test_trust.py"]
  source_commit: "0489fe3996361971582d7fb157e046584c644418"
  source_commit_time: 1784213258
---

Tier T1 (v1.1.0, "Positive context & dark signals") — session 2026-07-08. **COMPLETE, 4/4,
MERGED.** Shipped on branch `enh-t1-signals`, **[PR #9](https://github.com/youknowfred/hippo/pull/9)
squash-MERGED to main as `ed5a98a`** (owner-approved; branch deleted; the 6 item commits were
rebased onto origin/main's v0.7.0 squash `6c2f1dd` for a clean diff — 15 files, +3659/-11; all CI
green: dense + 4× hermetic + shellcheck). main now carries T1. This is the first execution of
[[hippo-enhancement-roadmap]]'s session protocol; the roadmap was dry-run-corrected first (see
that memo). Started ahead of ED-5's "after v1.0.0" gate at owner's explicit direction — SIG-1..4
have no cross-file/v1.0.0 deps, so no technical blocker.

SHIPPED THIS SESSION (one id-prefixed commit each, suite green after each):
- **SIG-1** `265d7a5` — first POSITIVE SessionStart producer (`relevant_to_work`, session_start.py).
  Selects project memories whose `cited_paths` intersect the uncommitted working-tree diff
  (`capture._git_changed_paths("HEAD")`), orders by `recall()` strength on a diff-derived query,
  names the matched path. New `RunContext.changed_paths` field (staleness.py) computed once in
  `_build_run_context` (trusted-only path → inherits SEC-1). Clean tree → silent.
- **SIG-2** `7f6ce76` — `resume_card` producer (session_start.py): replays the most-recent session
  in the clone-local episode buffer via `capture.gather_session_context` — themes / relied-on
  memories / cited files changed since the watermark (intersected with the corpus cited-union).
  Gated to substantive threads; labelled clone-local.
- **SIG-3** `5c14bb3` — `telemetry.abstention_backlog` clusters recurring `backend='none'` queries
  (content-token Jaccard, min-count 3, NO time window → deterministic + self-clearing via ledger
  rotation). Surfaced at `doctor.check_recall_blind_spots` (always) + `session_start.blind_spot`
  producer (rare, via the generalized `_periodic_nudge_should_fire`, which the trust nudge now
  shares). backend='none' arm ONLY (near-miss scores aren't logged).
- **SIG-4** `56ff316` — KPI-2 injection-precision MEASUREMENT. New PostToolUse hook
  (`memory_post_tool.sh`, matcher-scoped in hooks.json to Read|Edit|Write|MultiEdit|NotebookEdit)
  → `outcome.py` logs file touches to a gitignored `outcome_events.jsonl`; `injection_precision`
  JOINS episodes × outcomes × cited_paths off-hook ("injected then touched, same session, touch
  ts >= recall ts"). Surfaced at `doctor.check_injection_precision` + `python -m memory.outcome
  --report`. MEASUREMENT ONLY (AST test pins no recall/new_memory import) — ranking gated on SIG-5.

TIER STATE: 4/4 done; T1 tier status flipped to `done` (`4d015f7`); done_means MET. 37 items
remain planned (T2–T7).
ENGINE STATE: suite **1139 passed / 11 deselected** (baseline was 1089; +50 tests across 3 new
test files: test_relevant_to_work, test_resume_card, test_blind_spot, test_outcome + a
TestPostToolUseHook class). corpus_format 2, index schema 3 unchanged; **re-bootstrap NO**
(requirements.txt untouched, no schema bump — T1 is producers + measurement only, as designed).
DECISIONS / GOTCHAS: (1) SIG-1 uses `capture._git_changed_paths('HEAD')`, NOT
`gather_session_context` (episode-keyed, returns None at fresh SessionStart) — the dry-run
correction held up in implementation. (2) SIG-3 dropped the time window for deterministic doctor
render + self-clearing via the byte-bounded rotating ledger. (3) SIG-4 hook is matcher-scoped so
python only spawns on file tools (keeps PostToolUse cheap); the KPI-2 join is off-hook.
(4) `_trust_nudge_should_fire` generalized to `_periodic_nudge_should_fire` (behavior-identical;
test_trust.py green). End-to-end smoke confirmed all four blocks render cleanly together.
NEXT: **Tier T2 "The rules bridge"** — start with **RUL-0** (P0 spike: verify the `.claude/rules`
`paths:` auto-load harness feature, ED-3) BEFORE anything paths:-dependent (RUL-2 glob leg, RUL-6).
Then RUL-1 (conflict radar), RUL-2, RUL-3, RUL-4, RUL-5. All T2 items are unblocked except the
paths:-dependent legs which RUL-0 gates.
DEFERRED / BLOCKED: none.
Related: [[hippo-enhancement-roadmap]], [[hippo-v1-roadmap-proposal]], [[hippo-v070-release-pr]].
