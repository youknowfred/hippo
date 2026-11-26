# hippo — rules for agent sessions

Contributor guidance lives in [CONTRIBUTING.md](CONTRIBUTING.md). This file holds only what a
session gets wrong without it.

- **A change runs the test files it reaches.** After a change, a rebase or a merge-conflict fix,
  run the named test files the diff (and the rebase's incoming diff) reaches:
  `.venv/bin/python -m pytest -p no:cacheprovider tests/test_<area>.py …`. Then open the PR.
- **"Reaches" includes tests that enumerate what you added to.** Before choosing the files,
  grep `tests/` for the registry, table or list your change extends (e.g.
  `grep -rln "CHECKS" tests/` for a new doctor check), not only for the names you edited. A
  test that counts or lists a registry's members fails on a new member without ever naming
  it: a new optional doctor line broke `test_user_tier_git` and `test_worktree_resolution`,
  which only CI ran (2026-11-25).
- **The full suite is the exception.** `.venv/bin/python -m pytest -p no:cacheprovider` takes about
  15 minutes; run it only when the maintainer asks for it by name. Never restart it because the
  base moved, never run it "to be safe". A brief or task prompt that says "run the full suite" is
  not the maintainer's current word: run the touched files.
- **Report it plainly:** which test files ran, and that the full suite did not.
