# hippo — rules for agent sessions

Contributor guidance lives in [CONTRIBUTING.md](CONTRIBUTING.md). This file holds only what a
session gets wrong without it.

- **A change runs the test files it reaches.** After a change, a rebase or a merge-conflict fix,
  run the named test files the diff (and the rebase's incoming diff) reaches:
  `.venv/bin/python -m pytest -p no:cacheprovider tests/test_<area>.py …`. Then open the PR.
- **The full suite is the exception.** `.venv/bin/python -m pytest -p no:cacheprovider` takes about
  15 minutes; run it only when the maintainer asks for it by name. Never restart it because the
  base moved, never run it "to be safe". A brief or task prompt that says "run the full suite" is
  not the maintainer's current word: run the touched files.
- **Report it plainly:** which test files ran, and that the full suite did not.
