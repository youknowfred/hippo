---
name: feedback_no_legacy_read_fallbacks
description: Never preserve legacy cookie/header/state-key names via "read modern, fall back to legacy" patterns during a rename or migration — one canonical name only
metadata:
  type: feedback
---

Never preserve a legacy-read fallback during a rename or migration. Pick the new canonical name
and use it exclusively — both for reads and writes. Any orphaned legacy values in the wild get
silently ignored (they'll expire, or get re-minted on next interaction).

**Why:** Legacy-read fallbacks become permanent in practice — the "migration window" never
closes because nobody decommissions code that's still doing real work. The fallback path becomes
a maintenance burden (it doubles the read surface that security review and tests have to cover),
and the "we'll remove this in a follow-up" plan almost never happens.

**How to apply:**
- When adding a new cookie/header/state-key/config-key name, never include a "fall back to old
  name" read path. Write under the new name; read only the new name.
- Same applies to any rename/migration — the new code reads only the new name from day one. The
  old name's values expire naturally.
- Scheme- or environment-conditional canonical names (e.g. a `__Secure-` prefix on HTTPS vs. a
  bare name on HTTP) are NOT legacy reads — they're conditionally-chosen canonical names. Use
  the condition to choose ONE name to read; never fall through across names within the same
  condition.
- "We'll remove the fallback in a follow-up PR" is explicitly rejected — follow-ups don't
  happen. Ship the clean version now.
