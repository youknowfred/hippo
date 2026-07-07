---
name: additive-provenance-writes-never-touch-body
description: "provenance metadata backfills are additive frontmatter edits that never rewrite the body"
metadata:
  type: reference
---

Citation/staleness tracking fields get added or updated in frontmatter only — the
human-authored body text is never silently rewritten by an automated pass.
