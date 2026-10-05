"""CLM-6 field finding: reverify refused a memory with no provenance and named no remedy.

"no provenance yet — run backfill first" left the operator to find the right command; the
refusal must name a runnable one-liner for THIS memory, and running it must unblock the
verdict.
"""

from __future__ import annotations

import os
import re

from .conftest import git_commit, write_file
from memory import provenance as P
from memory.reconsolidate import semantic_reverify

_BARE = """---
name: handmade
description: "a memory written by hand, never backfilled"
metadata:
  type: project
---

Body that cites src/app.py.
"""


def test_a_provenance_less_reverify_names_its_one_line_remedy_and_the_remedy_works(repo, memory_dir):
    write_file(repo, "src/app.py", "x = 1\n")
    write_file(memory_dir, "handmade.md", _BARE)
    git_commit(repo, "seed", 1_700_000_000)
    r = semantic_reverify("handmade", "graduate", memory_dir, repo)
    assert r["error"], r
    m = re.search(r"`(hippo provenance --refresh-one handmade)`", r["error"])
    assert m, f"refusal names no runnable remedy: {r['error']!r}"
    assert P.main(["--refresh-one", "handmade", "--memory-dir", memory_dir, "--repo-root", repo]) == 0
    assert not semantic_reverify("handmade", "graduate", memory_dir, repo)["error"]
    with open(os.path.join(memory_dir, "handmade.md"), encoding="utf-8") as fh:
        assert "source_commit" in fh.read()
