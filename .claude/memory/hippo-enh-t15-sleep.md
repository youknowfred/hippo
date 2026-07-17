---
name: hippo-enh-t15-sleep
description: "Enhancement Tier T15 (round 3, \"Scheduled sleep\") \u2014 shipped 3/3 items (SLP-1..3); PR #58 MERGED same day (squash 46d2b5f, owner-ratified, no version bump): the headless sleep runner + morning report (zero corpus writes, registry-driven drain verbs), print-only scheduler recipes + report snooze + hippo sleep subcommand, and the ratified Tier-A-in-sleep opt-in flag (HIPPO_SLEEP_TIER_A, default OFF, origin=sleep ledger stamp, undo-first report); next tier T16 JIT"
metadata:
  type: project
  last_verified: "2026-07-16T13:38:02.407027+00:00"
  cited_paths: [".github/workflows/release.yml", "plugin/memory/sleep.py", "tests/test_sleep.py"]
  source_commit: "f99bfa89571ac19cadf0967693103b9b9bd464f1"
  source_commit_time: 1784246873
---

T15 SLP implemented 2026-07-16 (same session that merged T14's PR #57); **Fred ratified and squash-MERGED PR #58 the same day (46d2b5f on main, branch deleted both sides)**. 2 id-prefixed commits — SLP-1+2 share the one runner module, SLP-3 is the autonomy flag — tests-first, suite 2209 green at merge, dogfooded live on this repo's own 14-seed backlog. RELEASED same day in v1.18.0 "Checks itself, sleeps on schedule" (release PR #59, squash 5779b60, tagged, release.yml green — covers T14+T15 together; re-bootstrap: no).

**What shipped:**
- SLP-1 `plugin/memory/sleep.py` (`python -m memory.sleep` / `hippo sleep`) + `tests/test_sleep.py`: one headless entrypoint runs the EXISTING read-only producers off-session (doctor, CAP-2 triage listing with queue-snooze honored, LIF-1 reconsolidation producer VERBATIM, dream report pass with its empty norms read as nothing, link lint) into ONE morning-report artifact (telemetry dir + stdout). ZERO corpus writes + ZERO trust-registry writes asserted byte-for-byte; per-section RCH-9 degradation; empty queues → one-line report with plumbing + last-run stamp folded in; every section names its drain verb per surface FROM THE INV-1 REGISTRY (its designed offline consumer — the hot-path never-reads lint allowlists exactly `sleep`).
- SLP-2 `--print-schedule` (launchd plist / crontab / scheduled-task JSON for THIS machine's paths, print-only — bootstrap's explicit-install posture) + `--snooze Nd` (silences; says so once on resume) + the `hippo sleep` bin subcommand (STABILITY.md CLI list + surfaces.BIN_HIPPO_SUBCOMMANDS updated together). Failure modes (machine asleep / venv moved / repo moved) documented next to the recipes; the report's last-run stamp makes a stall visible next run.
- SLP-3 (RATIFIED 2026-07-16: ship the flag, default OFF) `HIPPO_SLEEP_TIER_A`: OFF = zero-write byte-for-byte even with an eligible edge (asserted); ON = the unchanged DRM-2 apply contract (cap/θ/SEC-1/aging/ping-pong) with `run_apply_pass(origin="sleep:<ts>")` — an additive ledger field so the audit trail records who applied (interactive rows stay origin-free); the report's FIRST line is the undo recipe when anything applied.

**Post-release follow-ups (same day):** dogfooding the FIRST real schedule install caught an SLP-2 bug — the printed launchd plist embedded the shell line's `&&`/`>>` raw inside XML `<string>` (plutil refused it); fixed in PR #60 (squash bf57e6c, merged post-v1.18.0, UNRELEASED — rides the next release) with a plistlib round-trip regression test. The schedule is INSTALLED live on this repo/machine: launchd agent `com.hippo.sleep.hippo` (~/Library/LaunchAgents/com.hippo.sleep.hippo.plist), weekdays 07:30 local, report → .claude/.memory-telemetry/sleep-report.md, log → sleep.log; kickstart-verified exit 0. Snooze: `hippo sleep --snooze Nd`; uninstall: `launchctl unload` + rm the plist.

**Why:** the maintenance loops ran only when the human remembered — this repo itself woke to 21 stale memories and 11 pending captures during the round-3 exploration. The T14 lints proved themselves mid-tier: INV-3's discovery failed on the runner's two new writers until they declared crash classes, and INV-1's hot-path assertion forced the explicit offline-consumer allowance.

**How to apply:** when Fred reviews PR #58, the body maps every AC to its test. After merge, dogfood the cadence on this repo (`hippo sleep --print-schedule`, install the crontab line). Next baton: T16 JIT (first-touch reminder JIT-1 default-ON with kill switch per the ratified decision, + JIT-2 touch-grain outcomes) — or re-scope to EXT-1 (recall on the PR diff), still EXPLORATIONS3 §5's highest new-user-value move. Related: [[hippo-enh-t14-invariants]], [[hippo-enhancement-roadmap]], [[hippo-qa-sweep-2026-07-16]].

Related: [[hippo-enh-t14-invariants]], [[hippo-enhancement-roadmap]], [[hippo-qa-sweep-2026-07-16]]
