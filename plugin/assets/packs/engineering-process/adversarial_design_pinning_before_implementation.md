---
name: adversarial_design_pinning_before_implementation
description: Before implementing a risky/consequential design, pin it via a multi-candidate judge panel + adversarial-verify pass BEFORE writing code — this catches real bugs that code review alone tends to miss
metadata:
  type: feedback
  pack: engineering-process
  pack_version: "0.2.0"
---

For a risky, consequential design decision (touches a hot path, adds the first write primitive
in a module, changes a return-type contract other code depends on, or interacts with an
established-but-not-obvious convention elsewhere in the codebase), pin the design before writing
any code: spawn 2-3 independent candidate designs from different angles, synthesize a winner
(grafting the best element of a rejected candidate if it genuinely improves the winner), then
run 3-5 independent adversarial skeptics against the synthesized design — each told to actively
try to REFUTE it, not confirm it, and to default to "refuted" on any plausible concern.

**Why:** This technique has caught real, silent design flaws before implementation that a plain
code review likely would have missed — the kind of bug that doesn't fail any test written
against the naive design (because the triggering condition doesn't exist yet in the data), so it
would otherwise ship silently broken and only surface much later. Skeptics who verify claims
EMPIRICALLY against real data/code (grep, direct simulation, actually running the candidate
functions) find sharper issues than skeptics who just reason about the proposal in the
abstract.

**How to apply:** Reach for this when a design decision is (a) genuinely consequential — not a
routine CRUD change, and (b) hard to fully verify by static reading alone — e.g. it involves
subtle interactions with existing sort/tie-break behavior, cache-reuse semantics, or a
schema/frontmatter convention that must match an established pattern elsewhere. Have skeptics
verify empirically, not just re-read the spec. Pin the final decision into a durable artifact
(a design doc, a roadmap file) so the implementer doesn't have to re-derive it, and so a later
re-implementation attempt doesn't silently reintroduce the rejected design. Don't reach for this
on routine, low-risk, easily-statically-verified changes — it's real overhead (multiple agents,
real token cost) and is only worth it when the failure mode it guards against is a silent,
hard-to-test-for one.
