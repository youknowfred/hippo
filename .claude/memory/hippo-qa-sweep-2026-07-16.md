---
name: hippo-qa-sweep-2026-07-16
description: "2026-07-16 first-party QA sweep: 13 reproduced defects fixed and SHIPPED as v1.17.0 'Old or new, never torn' (PR #54 squash e3a3001; release PR #56 squash 6735a7e; tagged, release.yml green) \u2014 COR-14..19 frontmatter/atomicity/YAML-parity, SEC-18/19 traversal+registry, INT-17..19 surface dead-ends, RCH-8/9 rollback+silent-failure reporting; round-3 enhancement roadmap (T14-T17, INV/SLP/JIT/EXT) MERGED as PR #55 (squash 8f86cbe) with all four owner decisions ratified"
metadata:
  type: project
  last_verified: "2026-07-16T11:50:16.113080+00:00"
  cited_paths: [".github/workflows/release.yml", "ROADMAP.enhancements3.yaml", "plugin/memory/atomic.py"]
  source_commit: "0489fe3996361971582d7fb157e046584c644418"
  source_commit_time: 1784213258
---

Commissioned as routine post-v1.16.0 maintenance QA (owner brief, 2026-07-16): find defects with repro-first discipline, fix on a branch, and draft round-3 enhancement proposals. **Fred ratified and merged PR #54 the same day** (squash e3a3001; branch deleted), and the sweep SHIPPED as **v1.17.0 "Old or new, never torn"** — release PR #56 (DOC-7 manifest bump + CHANGELOG capstone, re-bootstrap: no), squash 6735a7e, tagged v1.17.0, release.yml green. Release validated in a clean detached worktree because the T14 implementation session was concurrently editing this same clone (its in-progress INV-2 lint briefly polluted an in-tree pytest run — two sessions, one working tree). PR #55 (round-3 proposals) was ratified and MERGED same-day too (squash 8f86cbe) — EXPLORATIONS3.md + ROADMAP.enhancements3.yaml live on main with the four §4 decisions recorded as RATIFIED; implementation is the next baton (start: T14 INV, INV-1 keystone first per §5). (Distinct from [[hippo-v190-launch-and-sweep]] — that was the v1.9.0 launch security sweep; this is the post-v1.16.0 tree-wide defect sweep.)

**Why:** v1.16.0's field report showed defect classes (ad-hoc frontmatter walks, partial-write stranding, one-error-per-call refusals, surface dead-ends) generalize beyond the pack code; this sweep hunted the classes tree-wide, with the rule that a finding enters the fix set only after a failing test or repro script.

**PR #54 (branch qa-2026-07-16-defect-sweep, 8 id-prefixed commits, ~20 new tests, suite green):**
- COR-14 dream_generate's `_set_confidence`/`_set_cited_paths` were the 6th/7th ad-hoc frontmatter walks — hard-coded 2-space insert, no damage check; corrupted 4-space metadata silently. Now insert_frontmatter_keys + _frontmatter_damage.
- COR-15 pack_extract dest-inside-corpus check was lexical; symlinks (the native-memory layout itself!) and APFS case-respelling bypassed it → new inode-walk `_dest_inside_corpus`.
- SEC-18 explicit extract names traversed paths (read outside corpus into a shareable pack; write escaped dest and clobbered the source in repro) → bare-stem name gate, report-all.
- COR-16 two-write chains (deparasite dedup-merge, reconsolidate demote+supersede, dream refines) stranded write #1 when write #2 failed while reporting "refused" → shared `provenance.restore_file_bytes` rollback (restores bytes + re-folds SEC-6 baseline).
- SEC-19 trust + projects registries wrote non-atomically (machine-wide blast radius; torn read = deny-all recall or quarantine-off) → new `memory/atomic.py` (unique-tmp + os.replace, symlink-aware).
- COR-17 packs lockfile: atomic writes + corrupt-lockfile refuses loudly (used to silently reset, orphaning every pack's merge base); shared caches' fixed `.tmp` names now per-process-unique.
- COR-18 all eleven in-place corpus .md writers route through write_text_atomic (torn write = truncated committed memory; body-truncation still parsed = silent).
- INT-17 install adopts byte-identical existing files (closes the install→update→install refusal circle after a crash window; gives hand-seeded packs an update route).
- RCH-8 extract rollback now includes the in-flight partial file; BaseException (Ctrl-C) rolls back then propagates.
- COR-19 miniyaml↔PyYAML parity: inline `# comments` after values diverged (steer/confidence silently OFF pre-bootstrap; quoted-description comment gutted whole frontmatter); read_last_verified now coerces PyYAML date objects.
- RCH-9 swallowed failures named at four sites (heal_baselines returns (healed, failed); SessionStart producer backstop emits ⚠ line; install-plan dup-check failure on the row; corrupt capture seeds named in listing).
- INT-18 reconsolidation nudge named nonexistent `provenance --reverify` (visible in this repo's own SessionStart!) → names the reconsolidate tool + /hippo:consolidate. INT-19 Desktop surface note stopped claiming resolve/audit work on Desktop; names dream/new_memory/recall/why tools + recall's two terminal-only modes.

Deliberate contract changes to ratify: identical re-install adopts; doc names refuse at the name gate; raising producers are named. Open questions (no repro → no fix) live in PR #54's body: heal's letter-of-COR-9 gap, RMW lost-updates under the single-writer assumption, latent miniyaml value diffs with no consumer, new_memory partial-on-crash, secrets verb naming.

**PR #55 (MERGED 8f86cbe):** EXPLORATIONS3.md + ROADMAP.enhancements3.yaml — T14 INV self-enforcing invariants (verb-surface registry+parity lint, write-discipline AST lint, crash-fault harness, resolve/audit tools), T15 SLP scheduled sleep (headless morning report, zero-write default, opt-in Tier-A flagged as owner decision), T16 JIT point-of-action recall (first-touch reminder, touch-grain outcomes), T17 EXT beyond-the-session (PR-diff recall comment, cross-project promotion mining, interview loop). The four owner decisions in EXPLORATIONS3 §4 were RATIFIED same-day (2026-07-16), all on the recommended option: SLP-3 ships the opt-in Tier-A-in-sleep flag (default OFF); JIT-1 ships default-ON with an env kill switch; EXT-1 dogfoods quietly on this repo first; INV-4 scopes the tool wave to resolve + audit only. Recorded in the roadmap's meta.owner_decisions + inline on the items (commit on the PR #55 branch).

**How to apply:** when Fred reviews, PR #54's body is the full ranked findings report; the commit messages are CHANGELOG-ready if this becomes a release. Round-3 ids must not be treated as committed work until ratified. Related: [[hippo-v1160-pack-fix]] (the seed field report), [[hippo-enhancement-roadmap]], [[hippo-enh-exploration-r2]].

Related: [[hippo-v1160-pack-fix]], [[hippo-enhancement-roadmap]], [[hippo-enh-exploration-r2]], [[hippo-v190-launch-and-sweep]]
