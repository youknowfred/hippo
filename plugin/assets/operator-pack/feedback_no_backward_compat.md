---
name: feedback_no_backward_compat
description: Never use backward compatibility as a reason for architecture decisions — one correct path, no shims
metadata:
  type: feedback
---

Do not use "backward compatibility" as a motivating reason for architecture decisions. Design
the correct interface and update all callers — don't add optional params with None defaults,
env-var fallbacks, deprecated aliases, or module-level shim attributes to avoid touching
existing call sites.

**Why:** Compat shims are technical debt with no expiration date — the codebase should have one
correct behavior, not "old way still works if you don't pass the new param." If a signature or
name changes, update all callers in the same commit.

**How to apply:** When adding new capability to an existing function, if the new capability
should be the default behavior, make it the default. If callers need updating, update them.
Don't add `Optional[X] = None` with "None means old behavior" branching. **Specifically
prohibited refactor patterns:**
- `OLD_NAME = NEW_NAME` module-level alias.
- `def old_func = new_func` function alias.
- A helper that reads both an old and a new env-var/config name.
- Deprecation-warning fallbacks that still honor the old key.
- "Accept either name" matchers in shell/config parsing.
- Docs that document both old and new names "so existing setups keep working."

This applies during refactors *as much as* in steady state — the temptation is highest mid-rename
to "soften the landing" with a backward-compat layer. Resist it. Rename in one commit, callers
updated, no shim. If the refactor turns out wrong, revert; don't leave a permanent dual pathway.

**Trigger:** if you catch yourself writing "backward-compat", "deprecation", "legacy fallback",
"soft-land", or "for callers that still use the old name" in a comment or commit message — stop,
delete the shim, fix the callers.
