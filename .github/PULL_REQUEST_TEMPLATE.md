<!--
Thanks for contributing! Keep PRs coherent (one theme). See CONTRIBUTING.md for the dev-venv recipe,
the test markers, and the commit conventions.
-->

## What & why

<!-- What does this change, and what problem does it solve? Link the roadmap item id if there is one
(e.g. CAP-6), or describe the change plainly for an outside contribution. -->

## Checklist

- [ ] The full suite is green: `HIPPO_DISABLE_DENSE=1 python -m pytest` from the repo root
- [ ] Secret-scan is clean: `PYTHONPATH=plugin python -m memory.secrets --repo .`
- [ ] New wall-clock/timing assertions are marked `slow`; model-download tests are marked `network`
- [ ] Preserves the guiding invariants (markdown-in-git is the only authority; hooks exit 0 / never
      download / never block; the UserPromptSubmit hot path stays pure retrieval; writes are per-item
      and agent-gated)
- [ ] Docs/skills updated if user-facing behavior changed; no new broken relative links
- [ ] If this touches the frozen surface (see STABILITY.md), it's a major bump or ships a deprecation
      window

## Notes for reviewers

<!-- Anything non-obvious: a design tradeoff, a follow-up you deferred, a test you couldn't run. -->
