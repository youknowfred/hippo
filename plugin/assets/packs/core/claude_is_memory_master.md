---
name: claude_is_memory_master
description: Claude is the autonomous MEMORY MASTER for this memory corpus — the agent re-verifies and resolves memory staleness/curation itself; the human is NOT the verifier and not in the loop; git is the audit/safety net
metadata:
  type: feedback
  pack: core
  pack_version: "0.2.0"
---

By default, the operator delegates memory upkeep to the agent: the agent owns verifying and
resolving the corpus, the human is NOT the verifier for maintenance passes. This is distinct
from a human reviewing newly-drafted memories before they're created (a separate, optional
policy) — don't conflate the two. If the operator prefers to review corpus maintenance
themselves, they should say so explicitly; absent that, act autonomously.

**How to apply:** When staleness or curation surfaces (a staleness signal at session start, or
a "run memory maintenance" ask), run the resolution pass yourself — do NOT ask the operator to
verify or pre-approve each item. For each stale memory: read it, check it against current
reality (code/state/diff since it was last verified), then resolve — still-accurate →
re-baseline/re-verify it; drifted → fix the body, then reverify; obsolete → archive. Act, then
report; git is the audit/revert path since the corpus is markdown-in-git. Verification is the
agent's judgment, never a human checkpoint. Never blind-clear everything at once — resolve
items individually and deliberately, never via a bulk "reverify all."
