---
name: feedback_no_feature_flagged_refactor_rollouts
description: Never propose env-var flags, per-domain allowlists, or phased canary rollouts for internal refactors or prompt/code unifications — one PR, one correct path, delete the old
metadata:
  type: feedback
---

For internal refactors and code/prompt unifications, never propose a feature flag, a per-domain
allowlist env var, a per-component canary, or a multi-phase staged rollout. Ship as one change
that builds the new shape, migrates every caller, and deletes the deprecated surface in the same
commit set — verified via tests + a normal deploy/smoke check.

**Why:** A phased-rollout design for a purely internal refactor is backward-compat theater
([[feedback_no_backward_compat]]) combined with tactical deferral
([[feedback_no_tactical_shortcuts]]) — it keeps two code paths alive "for safety" on a change
that has no actual user-facing contract or data-corruption surface to protect. Environment
variables used as permanent workarounds for an incomplete migration are a well-known
anti-pattern for exactly this reason.

**How to apply:**
- When a plan (yours or a sub-agent's) proposes "Phase 1 canary → Phase 2 expand → Phase N flip
  default" for an internal refactor, stop and ask: is there an actual user-visible contract at
  stake, or is this invented risk? For internal refactors, it's almost always the latter.
- Acceptable: a normal PR → review → merge → deploy flow. Unacceptable: feature flags defaulting
  off in production, per-callsite allowlists, or "parity tests" kept alive across phases
  indefinitely.
- Tests and a normal review/audit process are the correctness gate for an internal refactor —
  runtime flags are not a substitute for that.
- Exceptions exist for GENUINE user-facing contracts (a real opt-in feature, environment
  isolation between dev/staging/prod) — but those are durable product boundaries, not migration
  scaffolding for an internal change.
