---
name: hippo-enhancement-roadmap
description: ROADMAP.enhancements.yaml — executable post-v1 capability roadmap with a capstone-memory session protocol
metadata: 
  node_type: memory
  type: project
  originSessionId: 607b0701-e396-4c92-8070-0cbb31a38e60
  cited_paths: ["ROADMAP.enhancements.yaml", "ROADMAP.yaml"]
  source_commit: "c6669ca7ec7ac0c2c2b783605639dd3e306a7c58"
  source_commit_time: 1783904241
---

`ROADMAP.enhancements.yaml` (hippo repo root, DRAFT, created 2026-07-08) is the
EXECUTABLE form of [[hippo-enh-exploration-r2]] / EXPLORATIONS.md — the
vetted enhancement catalog formalized into a real roadmap for **multi-session
implementation**. 6 workstreams (SIG/RUL/RCL/GOV/GRW/RCH), **41 items**, **7
work tiers** v1.1.0→v1.7.0 (T1 Positive-context & dark-signals · T2 The
rules-bridge · T3 Retrieval-precision · T4 Corpus-governance · T5
Knowledge-growth · T6 Reach · T7 Learned-ranking). Validated: parses clean, every
item in exactly one tier, all deps resolve (cross-file deps RET-8/LIF-7 live in
ROADMAP.v1/ROADMAP.yaml). Downstream of ROADMAP.v1's v1.0.0 (decision ED-5).

**How future agent sessions execute it — the capstone protocol (`session_protocol`
block):** continuity lives in TWO synced places — this file's per-item/per-tier
`status:` (planned/in_progress/done/deferred, the durable LEDGER) and a chain of
CAPSTONE MEMORY files `hippo-enh-t<N>-<slug>` (the narrative HANDOFF). A session:
(1) reads MEMORY.md + the latest `hippo-enh-t*` capstone + the roadmap → picks the
next item whose deps are done; (2) implements one item per RELEASING.md discipline
(one id-prefixed commit, suite green), preserving the 6 guiding_invariants
(honoring each item's `invariant_note`); (3) flips `status:`; (4) **at session/tier
end WRITES the capstone** (template in `session_protocol.capstone_template`:
shipped items+shas, engine state, decisions/gotchas, NEXT item, deferrals) and adds
its one-line MEMORY.md pointer. Governing design law **ED-1: ship the legible
detection/HITL half, defer autonomous ranking/write**; **ED-2: salience keystone
(SIG-5=RET-10) gates the whole automatic-ranking class**; **ED-3: verify the
`.claude/rules paths:` feature (RUL-0 spike) before building on it**.

**DRY-RUN VALIDATED 2026-07-08** (A-to-Z seam check: 7-agent read-only workflow +
independent pass). 95 checks, **0 broken seams** — no item cites a non-existent
symbol. 13 wording/attribution drifts + 3 dependency gaps corrected IN-FILE (statuses
left `planned`). Notable fixes: GRW-7 renamed `valid_until`→canonical `invalid_after`
(inv5 — reuses the shipped field, NO schema bump; dropped from ED-4's bump list + its
spurious GRW-3 dep); SIG-1 seam corrected (use `capture._git_changed_paths` with
'HEAD' watermark, NOT `gather_session_context`, which is episode-keyed / returns None
at a fresh SessionStart); RUL-7 AGENTS.md scan is audit-skill-`GOV_GLOBS`-only, not
`archive._SCAN_TARGETS`; GRW-2 GRA-3 links by BM25 write-time recall (not embedding);
GRW-8 dep [GOV-1] + GOV-6 deps widened to its real inputs. Graph re-checked acyclic,
no forward-tier deps. **Tier 1 (SIG-1..4) = GO** — all deps [], seams confirmed. Held
for owner review; no implementation started.

Companions: [[hippo-v1-roadmap-proposal]] (the v0.8→v1.0 launch plan).
When work begins, create `hippo-enh-t1-signals` as the first capstone.
