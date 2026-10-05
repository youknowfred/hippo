---
description: Moved into /hippo:tend — its "capture drain and consolidate" flow, unchanged. This name keeps working until v2.0. /hippo:consolidate
---

# /hippo:consolidate → /hippo:tend

`/hippo:consolidate` is now the "capture drain and consolidate" flow of `/hippo:tend`. Load the `hippo:tend` skill and
follow that flow: it does exactly what this command did. This name is removed in v2.0.

## Preflight (shared across all hippo skills)

```bash
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # Claude Code fills both in when it loads this skill
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] && [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] || { echo "✘ hippo's plugin paths are empty in this shell; load /hippo:tend instead."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # the shared interpreter resolver
hippo_resolve_py
hippo_note_usage skill consolidate  # count the old name's use: it decides when the name goes
```
