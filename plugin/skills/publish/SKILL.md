---
description: Moved into /hippo:share — its "Publish" flow, unchanged. This name keeps working until v2.0. /hippo:publish
---

# /hippo:publish → /hippo:share

`/hippo:publish` is now the "Publish" flow of `/hippo:share`. Load the `hippo:share` skill and
follow that flow: it does exactly what this command did. This name is removed in v2.0.

## Preflight (shared across all hippo skills)

```bash
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"  # Claude Code fills both in when it loads this skill
[ -n "${CLAUDE_PLUGIN_DATA:-}" ] && [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] || { echo "✘ hippo's plugin paths are empty in this shell; load /hippo:share instead."; exit 1; }
. "${CLAUDE_PLUGIN_ROOT}/hooks/_resolve_py.sh"  # the shared interpreter resolver
hippo_resolve_py
hippo_note_usage skill publish  # count the old name's use: it decides when the name goes
```
