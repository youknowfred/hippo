---
name: hippo-enh-t14-invariants
description: "Enhancement Tier T14 (round 3, \"Self-enforcing invariants\") \u2014 shipped 4/4 items (INV-1..4); PR #57 MERGED same day (squash d1cf764, owner-ratified, on top of v1.17.0): verb-surface registry+parity lint, write-discipline AST lint, crash-fault harness + STABILITY.md contract, resolve/audit MCP tools; next tier T15 SLP (EXPLORATIONS3 \u00a75 ranks EXT-1 highest after INV-1)"
metadata:
  type: project
  last_verified: "2026-07-16T13:17:00.770356+00:00"
  cited_paths: [".github/workflows/release.yml", "plugin/memory/surfaces.py", "tests/test_surface_registry.py", "tests/test_write_discipline.py", "plugin/memory/atomic.py", "tests/test_crash_faults.py", "plugin/memory/audit_view.py"]
  source_commit: "f99bfa89571ac19cadf0967693103b9b9bd464f1"
  source_commit_time: 1784246873
---

T14 INV implemented 2026-07-16 in one session per the one-tier-per-session capstone protocol; **Fred ratified and squash-MERGED PR #57 the same day (d1cf764 on main, branch deleted both sides)** — it landed on top of the v1.17.0 release (6735a7e), which a concurrent session had cut mid-T14. 4 id-prefixed commits, tests-first, suite 2194 green at merge. RELEASED same day in v1.18.0 "Checks itself, sleeps on schedule" (release PR #59, squash 5779b60, tagged, release.yml green — covers T14+T15 together; re-bootstrap: no).

**What shipped:**
- INV-1 `plugin/memory/surfaces.py` (the keystone registry: every /hippo:* verb's surface story) + `tests/test_surface_registry.py` (parity lint: registry⇄skills dir⇄_DISPATCH exact; SKILL.md routing markers; Desktop surface note mapping + terminal-only list; INT-18 named-command existence). First run caught a LIVE INT-18 on main: the pending-capture nudge named `hippo capture --snooze`, a bin/hippo subcommand that doesn't exist. SLP-1's morning report and EXT-1's rendered verbs consume this registry later.
- INV-2 `tests/test_write_discipline.py` (AST lint: write-mode open() allowlist-gated outside atomic.py; hand-rolled frontmatter walks forbidden outside provenance, allowlist EMPTY; COR-14-shape self-test). Found six COR-18 stragglers on committed paths, all atomicized: .format marker ×2, corpus seeds (new `atomic.write_bytes_atomic`), tier-floor skeleton, hard-set fixture + drafts queue ×3, promote-rule file.
- INV-3 `tests/test_crash_faults.py` (27 atomic call sites AST-discovered, each registered intact/detected/rolled-back and torn once frame-precisely; all four COR-16 chains exercised; slow-marked SIGKILL lane for pack_extract/build_index) + STABILITY.md "Crash safety" section asserted against the registration.
- INV-4 (scope ratified 2026-07-16: resolve+audit ONLY) resolve tool (inbox + ONE per-pair verdict/call: keep_one/scope_both/merge/not_conflicting; COR-16 rollback via restore_file_bytes; new `links.remove_typed_relation` primitive) + audit tool (`memory/audit_view.py` — the skill's Phase-1 gather, strictly read-only, judgment stays in the skill). `_pack_gate`→`_corpus_gate`; tools APPENDED (frozen-five positions pinned); Desktop surface note terminal-only list shrank to exactly: export-agents, import, promote, promote-rule, remove.

**Why:** eleven of the 13 QA-sweep defects ([[hippo-qa-sweep-2026-07-16]]) violated invariants already written in prose; T14 turns each class into a build failure. The INV-1/INV-3 lints caught the INV-4 additions mid-PR by name — the enforcement loop closed before review.

**How to apply:** when Fred reviews PR #57, its body lists the four deliberate contract changes to ratify (nudge wording, six atomicized writers, the resolve_view structural re-pin, the published crash contract). A concurrent session cut release-v1.17.0-old-or-new-never-torn from this clone mid-session — PR #57 is clean of it and rebases trivially if v1.17.0 merges first. Next baton: T15 SLP (per protocol; SLP-1 consumes the INV-1 registry), or re-scope to EXT-1 (EXPLORATIONS3 §5's highest-value-after-INV-1) if Fred prefers. Related: [[hippo-enhancement-roadmap]], [[hippo-enh-exploration-r2]].

Related: [[hippo-qa-sweep-2026-07-16]], [[hippo-enhancement-roadmap]], [[hippo-enh-exploration-r2]]
