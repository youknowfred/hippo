---
name: feedback_no_hedging_on_confirmed_paid_services
description: Once the operator has confirmed a service is on a paid plan with adequate headroom, never frame "plan limits" or "plan upgrade" as a risk or precondition for a technical decision involving that service
metadata:
  type: feedback
  pack: engineering-process
  pack_version: "0.2.0"
---

If the operator has told you a service (a cloud provider, an LLM API, a database, an
observability tool, etc.) is already on a paid plan with adequate headroom, don't write language
in a plan, PR description, or risk list that frames "plan limits" or "would need a plan upgrade"
as a precondition or risk for adopting a feature, raising a sample rate, increasing a pool size,
or scaling that service.

**Why:** Hedging on a constraint the operator has already told you doesn't apply implies they
might be cost-blocked when they aren't, and it distracts from the REAL risks (technical
correctness, integration cost, blast radius, latency, memory/resource headroom, storage scaling
behavior) by inventing a business-side risk that was already ruled out.

**How to apply:**
- If you're about to write "verify plan headroom," "may require a plan upgrade," "subject to
  plan limits," or any equivalent for a service the operator has already confirmed is
  well-provisioned — delete it.
- The real cost question is *technical impact* (latency, memory, storage/scaling behavior), not
  *invoice impact*, once the operator has told you the plan tier isn't the constraint.
- Capacity decisions should triage against: (1) per-service resource allocation, (2) any
  in-code thresholds/limits, and (3) measured headroom from real usage/logs — not against a
  vendor's plan tier the operator already confirmed is not the binding constraint.
- If a hardcoded threshold is the actual binding constraint, the answer is "tune the threshold"
  or "scale the allocation" — not "wait for a plan upgrade" that was never actually needed.
- This is scoped to services the operator has EXPLICITLY confirmed as adequately provisioned —
  don't over-apply it to a service you have no confirmation about; ask instead of assuming.
