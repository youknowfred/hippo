---
name: user_role
description: "<FILL-ME: your name> — <FILL-ME: your role, e.g. 'solo founder/engineer' or 'tech lead on a 3-person team'> building <FILL-ME: project/product name>. Claude is <FILL-ME: the engineering bench / a pair-programming partner / one of several tools>."
metadata:
  type: user
---

The user is **<FILL-ME: your name>** — <FILL-ME: one-line role description, e.g. "a solo
founder building <product>" or "the tech lead for <team>">. <FILL-ME: describe scope of
ownership, e.g. "They own the full stack end-to-end: product strategy, system architecture, and
hands-on engineering" — narrow this if not full-stack-solo>.

**Standing assumption — pick one and delete the others:**
- [ ] There are NO other engineers on this project; all engineering is <name> + Claude. Never
  propose "have an engineer review this," "assign to the team," on-call rotations, or
  multi-engineer processes. The review bench is Claude (subagents/reviewers) + <name>.
- [ ] There IS a small team of <FILL-ME: N> people; Claude is one contributor among <FILL-ME:
  names/roles>. <FILL-ME: describe coordination expectations, e.g. "changes should be reviewable
  by teammates unfamiliar with Claude's session context.">
- [ ] <FILL-ME: other structure>

Carry this through everything:
- <FILL-ME: delete if not solo> Ops/runbooks/alerting must be single-operator friendly:
  prioritize self-healing designs, loud-but-actionable telemetry, and automation over process.
- <FILL-ME: delete if not solo> Headcount is never the answer to a scale question — architecture,
  automation, and managed services are.
- Cost/effort tradeoffs are evaluated against <FILL-ME: "one person's attention" / "the team's
  bandwidth" / other constraint>.

How to collaborate with <FILL-ME: name>:
- <FILL-ME: technical sophistication level — e.g. "a sophisticated technical operator, frame
  explanations at the architecture/system level" OR "prefers plain-language explanations with
  concrete examples">
- <FILL-ME: optional — how they weigh product/business concerns vs. pure engineering rigor, if
  relevant>
- <FILL-ME: decision-making authority — e.g. "the only product+engineering decision-maker, so
  their judgment is the bar — don't unilaterally narrow scope after they've approved it" OR
  "decisions on X go through <person/process>">
- <FILL-ME: optional — attribution preference for user-facing copy, delete if not applicable>

**Delete this whole file's `<FILL-ME>` scaffolding once filled in** — it's a template, not a
memory yet. `/hippo:doctor` flags any remaining `<FILL-ME` markers by filename.
