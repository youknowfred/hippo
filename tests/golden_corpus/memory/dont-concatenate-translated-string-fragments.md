---
name: dont-concatenate-translated-string-fragments
description: "building a sentence by concatenating separately-translated word fragments breaks grammar"
metadata:
  type: feedback
---

Word order and inflection differ across languages; always translate the whole
templated sentence with placeholders, never glue pre-translated pieces together.
