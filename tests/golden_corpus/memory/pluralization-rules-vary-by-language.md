---
name: pluralization-rules-vary-by-language
description: "plural forms are not universally singular/plural \u2014 some languages have 3-6 categories"
metadata:
  type: reference
---

Polish has distinct forms for 1, few, many, and other; a naive `count == 1 ?
singular : plural` breaks for most non-English locales — use CLDR plural rules.
