---
name: rollback-plan-before-risky-deploy
description: "every deploy that touches schema or config should have a written rollback path first"
metadata:
  type: project
---

Write down the exact revert command / migration-down step BEFORE running the
forward migration, not after something breaks in production.
