---
name: shellcheck-on-every-shell-script
description: "run shellcheck over every .sh file before merging; unquoted variables are the top bug class"
metadata:
  type: project
---

An unquoted `$var` that happens to contain a space or glob character silently
splits into multiple arguments — shellcheck catches this class of bug for free.
