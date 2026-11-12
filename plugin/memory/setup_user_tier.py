"""``hippo setup --user-tier`` — OWN-1's offer: a local git home for the user tier.

  hippo setup --user-tier           preview: the .gitignore block and the `git init`; writes nothing
  hippo setup --user-tier --yes     do it (no commit, no remote)
  hippo setup --user-tier --status  read-only: the tier's git posture (doctor's line)

A tier that is already a repository only gets its machine-local ignore list refreshed (again
previewed first); a tier inside another repository (a dotfiles repo) is left alone. hippo
never pushes, pulls or fetches. The mechanics live in ``user_tier_git``.
"""

from __future__ import annotations

import os
from typing import List

from .user_tier_git import (
    GITIGNORE,
    flagged_machine_local,
    init_repo,
    posture,
    posture_line,
    render_block,
    sync_ignore_block,
    with_block,
)


def _indent(text: str) -> str:
    return "\n".join("    " + ln for ln in text.rstrip("\n").splitlines())


def _read_gitignore(user_dir: str) -> str:
    try:
        with open(os.path.join(user_dir, GITIGNORE), "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _next_steps(user_dir: str) -> List[str]:
    return [
        "Review what will go in, then make the first commit yourself:",
        f"    git -C {user_dir} status",
        f"    git -C {user_dir} add -A && git -C {user_dir} commit -m \"user tier\"",
        "To keep a memory on this machine only, add `machine_local: true` to its frontmatter "
        "(or save it with `hippo new ... --tier user --machine-local`), then run "
        "`hippo setup --user-tier --yes` again to refresh the ignore list.",
        "A remote is yours to add (`git remote add`); hippo never pushes, pulls or fetches.",
    ]


def run(args) -> int:
    from .provenance_env import user_memory_dir

    if args.off:
        print("hippo setup --user-tier has no --off: hippo never removes a repository. "
              "Nothing was written.")
        return 2
    d = user_memory_dir()
    p = posture(d)
    if args.status:
        print(posture_line(p)[1] if p else f"user tier: none on this machine yet ({d}).")
        return 0
    if p is None:
        print(f"No user tier on this machine yet ({d}). It is created the first time a memory "
              "is saved to it (`hippo new <name> <description> --type user --tier user`); run "
              "this again after that. Nothing was written.")
        return 0
    if p["repo"] == "inside":
        print(f"The user tier {d} sits inside the git repository at {p['top']}. hippo leaves "
              "that repository alone and makes no second one; keep machine-local memories out "
              "of it with that repository's own ignore rules. Nothing was written.")
        return 0
    flagged = flagged_machine_local(d)
    current = _read_gitignore(d)
    wanted = with_block(current, flagged)
    if p["repo"] == "own":
        if wanted == current:
            print(f"The user tier {d} is already a git repository and its machine-local ignore "
                  "list is current. Nothing to do.")
            print(posture_line(posture(d) or p)[1])
            return 0
        if not args.yes:
            print(f"The user tier {d} is already a git repository. `hippo setup --user-tier --yes` "
                  f"would refresh the hippo block in {os.path.join(d, GITIGNORE)} to:\n")
            print(_indent(render_block(flagged)))
            print("\nNothing was written.")
            return 0
        res = sync_ignore_block(d)
        if res["error"]:
            print(f"hippo setup: {res['error']}. Nothing was changed.")
            return 1
        print(f"Refreshed the machine-local ignore list in {os.path.join(d, GITIGNORE)} "
              f"({len(res['listed'])} listed).")
        for name in (posture(d) or {}).get("tracked_flagged") or []:
            print(f"  {name} is still tracked: `git -C {d} rm --cached {name}` stops tracking it "
                  "and keeps the file.")
        return 0
    n = p["memories"]
    verb = "add this block to the existing" if current else "write"
    if not args.yes:
        print(f"`hippo setup --user-tier --yes` would turn the user tier {d} ({n} "
              f"memor{'y' if n == 1 else 'ies'}, {len(flagged)} marked machine-local) into a "
              "local git repository:\n")
        print(f"  1. {verb} {os.path.join(d, GITIGNORE)}, so machine-local memories and "
              "hippo's transient files stay out of git:\n")
        print(_indent(render_block(flagged)))
        print("\n  2. run `git init` there.\n")
        print("It makes no commit and adds no remote; the memories stay where they are, "
              "untracked until you commit them.")
        print("\n".join(_next_steps(d)[3:]))
        print("\nNothing was written.")
        return 0
    res = init_repo(d)
    if not res["ok"]:
        print(f"hippo setup: {res['error']}. "
              + ("The ignore file was written; the repository was not created."
                 if res["gitignore_changed"] else "Nothing was changed."))
        return 1
    print(f"The user tier {d} is now a local git repository (no commit yet, no remote).")
    print("\n".join(_next_steps(d)))
    return 0
