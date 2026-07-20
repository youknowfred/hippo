---
name: hippo-vol1-volatile-paths
description: "VOL-1 volatile-paths staleness-arming policy SHIPPED 2026-07-18 (commissioned em-growth-labs field report) \u2014 PR #82 squash-MERGED d886ebf + RELEASED same day as v1.26.0 'The roadmap is allowed to move' (release PR #83 squash 05b0c28, tagged, release.yml green, GitHub Release published); .format volatile_paths key splits derivation from arming; watermark lane moved to reconsolidate_watermark.py sibling"
metadata:
  type: project
  last_verified: "2026-07-18T22:32:15.993414+00:00"
  verified_by: "81190215_youknowfred_users.noreply.github.com@2026-07-20T22:47:47.030050+00:00"
  cited_paths: [".github/workflows/release.yml", "plugin/memory/doctor.py", "plugin/memory/reconsolidate.py", "plugin/memory/reconsolidate_watermark.py", "plugin/memory/session_start.py", "tests/test_volatile_paths.py"]
  source_commit: "32ee471c7dfb5f9c5cc33e26ac2eac2151bc6c00"
  source_commit_time: 1784580625
---

VOL-1 — per-path staleness policy, commissioned by Fred from an em-growth-labs corpus-agent field report (their v1→v4 rederive made the reconsolidation worklist a treadmill: 23/56 items re-flagged within an hour, all by GROWTH-LOOP-ROADMAP.yaml). Built + PR'd 2026-07-18, [[hippo-cur1-cor20-citation-preservation]]-adjacent (same corpus, next report). PR #82 squash-MERGED to main as d886ebf same day, owner-directed on green CI; branch deleted local+remote. RELEASED same day, owner-directed, as **v1.26.0 "The roadmap is allowed to move"** — release PR #83 (DOC-7 two-commit shape: lockstep manifest bump then CHANGELOG capstone) squash 05b0c28, tag v1.26.0 pushed, release.yml four-way sync green, GitHub Release published from the CHANGELOG section. re-bootstrap: no — requirements byte-identical, corpus format 5 / index schema 7 / derivation 4 all unchanged. The emgl corpus adopts by committing its seven tier-1 paths into its own .format volatile_paths once on ≥v1.26.0.

**The split**: a corpus-level `volatile_paths` key in `.claude/memory/.format` (read: `provenance_format.read_volatile_paths`, exact-match toplevel-relative, no writer — operator-committed policy; NO corpus_format bump, the DRV-2 additive-marker-key precedent). Derivation + all recall surfaces (JIT, --for-diff, RET-6 banner, RET-5 penalty, find_stale/stale.json) stay registry-blind; ARMING partitions through the one policy point `staleness_policy.split_volatile_only` at three sites: recalled_stale_worklist's stale lane, watermark_stale_candidates, staleness_producer's note. Armed = ≥1 non-volatile drifted path (full path listing kept). CLB-3 evidence drift arms regardless (fold runs after the watermark filter). Suppression never silent: note tail / calm-ℹ all-suppressed line, CLI+MCP `diagnostics` out-param (`DIAG_KEY`) count line, always-ok doctor `volatile_paths` check (in doctor.py the FAÇADE, because doctor_checks_corpus is at 899/900).

**Ratchet fallout worth remembering**: reconsolidate.py sat exactly AT the 900 cap → GRW-5 watermark lane (_last_session_watermark + watermark_stale_candidates) moved to new sibling `reconsolidate_watermark.py` (pure code motion + the lane's filter half; façade re-exports keep merge_digest + all tests resolving). session_start.py has ~3 lines of pin+slack headroom left (1702/1705) — the NEXT staleness-note change probably forces a session_start split. Tier-2 co-drift arming deliberately not built (report's own call: tier-1 first).

25 tests in tests/test_volatile_paths.py map 1:1 to the report's six acceptance criteria. Local suite 2826 passed; the 1 local failure (test_two_tier dense) is environmental (no fastembed in ambient interpreter — reproduces on clean main; CI's dense job has the cached model).

Related: [[hippo-enh-exploration-r4]], [[hippo-cur1-cor20-citation-preservation]], [[hippo-modularize-engine-r1]]
