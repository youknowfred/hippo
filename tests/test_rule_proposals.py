"""RUL-9: a feedback memory whose recorded touches concentrate on one glob is proposed, in
tend, as the path-scoped rule file ``promote_rule`` drafts — written only on a yes.

Pins:
  - the touch ledger is the JIT-2 ``cited_by`` rows, at first-touch grain: one
    (session, path) pair per memory, ``tree_path`` preferred, outside paths dropped;
  - the glob is deterministic and specific: the single file, else the deepest directory
    with an extension pattern; never the repo root; it covers >= 80% of the touches and
    the evidence carries the share;
  - who proposes: feedback, not on the floor, >= MIN_TOUCHES touches, no rule for it yet
    (its own file or a rule citing it), a glob that matches a file in the tree;
  - tend: a ``rule`` kind with one verdict, ``write``: the draft is the evidence; a yes
    writes that one rule file and nothing else; listing writes nothing.
"""

from __future__ import annotations

import hashlib
import os
from collections import Counter

from memory import rule_proposals as RP
from memory import tend_queue as Q
from memory.promote_rule import promote_to_rule
from memory.telemetry import default_telemetry_dir, log_outcome
from memory.tend import VERDICTS, apply as tend_apply, render_next

from .conftest import git_commit, write_file

_MEM = """---
name: {name}
description: "{desc}"
metadata:
  type: {mtype}
  cited_paths: [{cited}]
---

{body}
"""


def _mem(md, name, cited, mtype="feedback", body="Run the app linter before touching the app module."):
    write_file(md, f"{name}.md", _MEM.format(
        name=name, desc=f"lesson {name}", mtype=mtype, body=body,
        cited=", ".join(f'"{c}"' for c in cited)))


def _touch(md, name, path, session, tree_path=None):
    log_outcome("Edit", path, session_id=session, telemetry_dir=default_telemetry_dir(md),
                cited_by=[name], tree_path=tree_path)


def _tree(repo):
    for rel in ("src/app/a.py", "src/app/b.py", "src/app/c.py", "src/app/deep/d.py",
                "docs/notes.md", "README.md"):
        write_file(repo, rel, "x\n")


def _corpus(repo, md, floor=""):
    _tree(repo)
    _mem(md, "app-lint", ["src/app/a.py", "src/app/b.py"])
    write_file(md, "MEMORY.md", f"# Memory\n\n## User\n\n## Working Style & Process Feedback\n{floor}")
    git_commit(repo, "tree", 1_700_000_000)


def _three_touches(md, name="app-lint"):
    _touch(md, name, "src/app/a.py", "s1")
    _touch(md, name, "src/app/a.py", "s1")  # same session, same file: one touch
    _touch(md, name, "src/app/b.py", "s2")
    _touch(md, name, "src/app/a.py", "s3")


# --------------------------------------------------------------------------- #
# The ledger and the glob
# --------------------------------------------------------------------------- #
def test_touches_are_first_touch_pairs_from_the_cited_by_rows(repo, memory_dir):
    td = default_telemetry_dir(memory_dir)
    _three_touches(memory_dir)
    _touch(memory_dir, "app-lint", ".claude/worktrees/w1/src/app/c.py", "s4", tree_path="src/app/c.py")
    _touch(memory_dir, "app-lint", "/abs/elsewhere.py", "s5")
    _touch(memory_dir, "app-lint", ".claude/worktrees/w2/src/app/c.py", "s6")  # no tree_path: dropped
    log_outcome("Edit", "src/app/a.py", session_id="s7", telemetry_dir=td)  # nothing cites it
    assert RP.memory_touches(td) == {"app-lint": Counter({"src/app/a.py": 2, "src/app/b.py": 1, "src/app/c.py": 1})}


def test_the_glob_is_the_single_file_when_it_carries_the_share():
    assert RP.derive_glob(Counter({"src/app/a.py": 4, "src/app/b.py": 1})) == ("src/app/a.py", 4)


def test_the_glob_is_the_deepest_directory_with_an_extension_pattern():
    assert RP.derive_glob(Counter({"src/app/a.py": 2, "src/app/b.py": 2})) == ("src/app/*.py", 4)
    nested = Counter({"src/app/a.py": 2, "src/app/deep/d.py": 2})
    assert RP.derive_glob(nested) == ("src/app/**/*.py", 4)
    mixed = Counter({"src/app/a.py": 2, "src/app/notes.md": 2})
    assert RP.derive_glob(mixed) == ("src/app/*", 4)


def test_the_glob_never_takes_the_repo_root_and_needs_the_share():
    assert RP.derive_glob(Counter({"a.py": 1, "b.py": 1, "c.py": 1})) is None
    assert RP.derive_glob(Counter({"src/a.py": 1, "docs/b.md": 1, "lib/c.py": 1})) is None
    # 4 of 5 is exactly the 80% share; 3 of 5 is not.
    assert RP.derive_glob(Counter({"src/app/a.py": 2, "src/app/b.py": 2, "docs/x.md": 1})) == ("src/app/*.py", 4)
    assert RP.derive_glob(Counter({"src/app/a.py": 2, "src/app/b.py": 1, "docs/x.md": 2})) is None


# --------------------------------------------------------------------------- #
# Who proposes
# --------------------------------------------------------------------------- #
def test_a_concentrated_feedback_memory_proposes_the_draft_promote_rule_makes(repo, memory_dir):
    _corpus(repo, memory_dir)
    _three_touches(memory_dir)
    [p] = RP.proposals(memory_dir, repo)
    assert (p["name"], p["glob"], p["touches"], p["covered"], p["coverage"]) == ("app-lint", "src/app/*.py", 3, 3, 1.0)
    draft = promote_to_rule(memory_dir, "app-lint", repo, globs=["src/app/*.py"])
    assert p["proposed"] == draft["proposed"] and p["path"] == os.path.join(".claude", "rules", "app-lint.md")
    assert '  - "src/app/*.py"' in p["proposed"] and "Run the app linter" in p["proposed"]


def test_below_the_minimum_proposes_nothing(repo, memory_dir):
    _corpus(repo, memory_dir)
    _touch(memory_dir, "app-lint", "src/app/a.py", "s1")
    _touch(memory_dir, "app-lint", "src/app/a.py", "s2")
    assert RP.MIN_TOUCHES == 3 and RP.proposals(memory_dir, repo) == []


def test_project_and_floor_memories_propose_nothing(repo, memory_dir):
    _corpus(repo, memory_dir, floor="- [App lint](app-lint.md) — lint first\n")
    _mem(memory_dir, "app-fact", ["src/app/a.py"], mtype="project")
    _three_touches(memory_dir)
    _three_touches(memory_dir, "app-fact")
    assert RP.proposals(memory_dir, repo) == []


def test_an_existing_rule_for_the_memory_proposes_nothing(repo, memory_dir):
    _corpus(repo, memory_dir)
    _three_touches(memory_dir)
    write_file(repo, ".claude/rules/linting.md", "---\npaths:\n  - \"src/**\"\n---\n\nSee `app-lint`.\n")
    assert RP.proposals(memory_dir, repo) == []
    os.remove(os.path.join(repo, ".claude/rules/linting.md"))
    write_file(repo, ".claude/rules/app-lint.md", "hand-written\n")
    assert RP.proposals(memory_dir, repo) == []


def test_a_glob_that_matches_nothing_in_the_tree_proposes_nothing(repo, memory_dir):
    _corpus(repo, memory_dir)
    for s in ("s1", "s2", "s3"):
        _touch(memory_dir, "app-lint", "gone/old.py", s)
    assert RP.proposals(memory_dir, repo) == []


# --------------------------------------------------------------------------- #
# tend
# --------------------------------------------------------------------------- #
def _hashes(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        if ".git" in dirpath.split(os.sep) or ".hippo" in dirpath.split(os.sep):
            continue
        for f in files:
            p = os.path.join(dirpath, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def test_rule_is_a_registered_kind_with_one_verdict():
    assert "rule" in Q.KINDS and "rule" in Q.GATES and "rule" in Q.SOURCES
    assert VERDICTS["rule"] == ("write",)


def test_tend_shows_the_draft_and_a_yes_writes_that_one_file(repo, memory_dir):
    _corpus(repo, memory_dir)
    _three_touches(memory_dir)
    before = _hashes(repo)
    res = Q.build_queue(memory_dir, repo, kinds=("rule",), write_cache=False)
    [e] = res["pending"]
    assert e["id"] == "rule:app-lint" and "3 of its 3 recorded touches (100%)" in e["evidence"]
    assert "src/app/*.py" in e["evidence"]
    shown = render_next(e, memory_dir, repo, 1)
    assert "would be written as:" in shown and "# hippo:rule-promote" in shown and "Run the app linter" in shown
    assert _hashes(repo) == before  # listing and showing write nothing

    r = tend_apply("rule:app-lint", "write", memory_dir=memory_dir, repo_root=repo)
    assert r["ok"], r
    after = _hashes(repo)
    added = set(after) - set(before)
    assert added == {os.path.join(".claude", "rules", "app-lint.md")}
    assert all(after[k] == v for k, v in before.items())
    # The rule now exists, so the memory proposes nothing.
    assert Q.build_queue(memory_dir, repo, kinds=("rule",), write_cache=False)["pending"] == []


def test_the_written_file_is_the_draft_shown(repo, memory_dir):
    _corpus(repo, memory_dir)
    _three_touches(memory_dir)
    draft = RP.proposals(memory_dir, repo)[0]["proposed"]
    assert tend_apply("rule:app-lint", "write", memory_dir=memory_dir, repo_root=repo)["ok"]
    with open(os.path.join(repo, ".claude", "rules", "app-lint.md"), encoding="utf-8") as fh:
        assert fh.read() == draft


def test_other_verdicts_are_refused(repo, memory_dir):
    _corpus(repo, memory_dir)
    _three_touches(memory_dir)
    r = tend_apply("rule:app-lint", "done", memory_dir=memory_dir, repo_root=repo)
    assert not r["ok"] and "write" in r["message"]
    assert not os.path.exists(os.path.join(repo, ".claude", "rules"))
