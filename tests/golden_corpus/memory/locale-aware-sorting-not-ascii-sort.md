---
name: locale-aware-sorting-not-ascii-sort
description: "sorting user-facing strings needs a locale-aware collator, not a raw codepoint sort"
metadata:
  type: project
---

A plain ASCII/byte sort puts accented letters after 'z' in most Latin locales,
producing an order native speakers find obviously wrong.
