---
name: hippo-v1-roadmap-proposal
description: "ROADMAP.v1.md — the v0.8→v1.0 OSS-launch roadmap (2026-07-08 recon+design session): APPROVED AND FULLY EXECUTED, kept as the historical design record — the whole 4-release arc shipped (v0.8 'Safe in the open' → v0.9 'Proven for strangers' → v0.10 → v1.0), the repo flipped public 2026-07-11, and its 'sharpest blocker' SEC-5 shipped in the v0.8.0 trust spine"
metadata: 
  node_type: memory
  type: project
  originSessionId: 607b0701-e396-4c92-8070-0cbb31a38e60
  last_verified: "2026-07-13T04:49:53.402288+00:00"
  verified_by: "81190215_youknowfred_users.noreply.github.com@2026-07-18T06:19:15.080872+00:00"
  cited_paths: ["ROADMAP.yaml", "plugin/memory/trust.py", "plugin/memory/recall.py", "plugin/memory/archive.py"]
  source_commit: "0477d4778fb0b40f08c5d28ba213fc864d8fcb78"
  source_commit_time: 1784354967
---

**STATUS (re-verified 2026-07-16): this proposal was approved and is FULLY EXECUTED — read
it as history, not as a plan.** The entire 4-release arc below shipped (v1.0 and well past it;
the plugin is v1.18.0 today), the repo flipped public 2026-07-11 ([[hippo-v190-launch-and-sweep]]),
and the "sharpest blocker" called out below — **SEC-5, consent blind to the injection payload —
SHIPPED in the v0.8.0 trust spine** (`trust.corpus_consent_sample` is live; see
[[hippo-v080-trust-spine]]). The OQ-7..OQ-10 flags were all decided long ago (OQ-8 became
STABILITY.md's compat policy). Nothing here is pending.

Deep recon + roadmap-design session on 2026-07-08 produced `ROADMAP.v1.md` at the
hippo repo root — at the time a DRAFT proposal for the v0.8.0→v1.0.0 open-source launch,
pending owner review (nothing decided/implemented yet; `ROADMAP.yaml` untouched).

Verified ground truth: the entire planned `release_train` v0.2.0→v0.7.0 SHIPPED;
only the `exploratory` set remained. Adversarial verdict on those 7 (checked vs
code): KEEP RET-8 (gate cleared) + GRA-8 (cheap, gate-free); RECONCILE LIF-8
(already shipped under DOC-6 — roadmap is stale); DEFER GRA-7/LIF-7/CAP-5 and CUT
INT-6 (daemon: KPI-3 <1500ms already met without it).

Thesis: **engine is done; v1 is launch-readiness, not more features.** Proposed
4-release arc — v0.8 "Safe in the open" (security for PUBLIC corpora), v0.9
"Proven for strangers" (measurement/precision), v0.10 "Legible to strangers"
(onboarding/docs), v1.0 "Launch" (positioning + community + stability contract).
Two NEW workstreams: POS (positioning) + COM (community).

Sharpest blocker (verified): **SEC-5** — the SEC-1 trust gate reviews memory
*names* (`trust.py:166-178`) but injects *descriptions* (`recall.py:1716`), so
consent is blind to the injection payload. Positioning reframe: the
"markdown+hybrid recall" wedge is now crowded (claude-mem ~86K stars; Anthropic's
own GA memory tool); hippo's surviving differentiators = git-native store +
git-drift semantic staleness + zero-LLM/$0 hot path + review-gated team memory.

Flags decisions to revisit: OQ-7 (amend OQ-6 trust posture), OQ-8 (post-1.0
compat policy vs the clean-break invariant), OQ-9 (record INT-6 cut), OQ-10
(RET-5 salience default-on). Related: [[hippo-v070-release-pr]].

**Companion: `EXPLORATIONS.md`** (repo root, same session, DRAFT) — a 15-agent
divergent-ideation + adversarial-vetting catalog of NET-NEW post-v1 (v1.1+)
capability bets. Two meta-insights: (1) activate the dark signals hippo already
collects but discards (episode buffer read only by capture; salience machine
DEFAULT-OFF; abstention `backend=none` stream surfaced nowhere; per-hit salience
breakdown rendered nowhere); (2) become the ranking/hygiene/staleness layer over
the Claude-rules plane hippo already READ-scans (archive.py `_SCAN_TARGETS` =
CLAUDE.md/.claude/rules/agents/skills). Key fact: `.claude/rules` (CC 2.0.64) is
`paths:`-glob-scoped, structurally identical to hippo's `cited_paths`; #1
CLAUDE.md pain is everything always-loads. Top moves: first
"relevant-to-current-work" SessionStart producer (diff-seeded; 3 lenses
converged); mine silent abstention into a curation + KPI-2 signal;
rules-plane conflict-radar/staleness/write-dedup; `/hippo:promote` (lands TEA-1's
unmet AC) + Cursor `.mdc` import; contradiction inbox + `/hippo:resolve`. Design
law the vetters enforced: ship the legible detection/HITL half, defer autonomous
ranking/write — RET-10 salience-default is the keystone gating a whole ranking
class.
