"""RUL-6 — promote a reinforced procedural memory into a glob-scoped .claude/rules/<name>.md.

Hermetic: each test builds a throwaway git repo (so build_repo_file_index has a real oracle)
with a few source files + a memory citing them, then exercises promote_to_rule / the CLI.
The derivation itself (over-scoping cap, literals, missing) is rules_plane.derive_paths_globs,
tested there; here we pin the promotion mechanic: propose-only, refuse cases, render shape, CLI.
"""

from __future__ import annotations

import os
import subprocess

from memory import promote_rule as PR

_GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _repo(tmp_path, files):
    root = str(tmp_path / "repo")
    for rel in files:
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(["git", "-C", root, "add", "-A"], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "seed"], check=True, env=_GIT_ENV)
    md = os.path.join(root, ".claude", "memory")
    os.makedirs(md, exist_ok=True)
    return root, md


def _mem(md, name, cited, body="Do the thing.", *, extra_fm=""):
    quoted = ", ".join(f'"{c}"' for c in cited)
    with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write(
            f'---\nname: {name}\ndescription: "d"\nmetadata:\n  type: project\n'
            f"  cited_paths: [{quoted}]\n{extra_fm}---\n{body}\n"
        )


# --------------------------------------------------------------------------- #
# Derivation into the rule (the RUL-7-shared over-scoping cap, exercised end-to-end)
# --------------------------------------------------------------------------- #
def test_same_dir_ext_group_collapses_within_cap(tmp_path):
    root, md = _repo(tmp_path, ["src/a.py", "src/b.py"])
    _mem(md, "lint", ["src/a.py", "src/b.py"])
    res = PR.promote_to_rule(md, "lint", root)
    assert res["globs"] == ["src/*.py"] and res["flags"] == []
    assert res["proposed"] and 'paths:\n  - "src/*.py"' in res["proposed"]  # JSON-quoted


def test_single_citation_stays_a_literal(tmp_path):
    root, md = _repo(tmp_path, ["src/solo.py", "src/other.py"])
    _mem(md, "solo", ["src/solo.py"])
    res = PR.promote_to_rule(md, "solo", root)
    assert res["globs"] == ["src/solo.py"]  # exact drift detection, never a dir glob


def test_over_scope_falls_back_to_literals_with_flag(tmp_path):
    # 2 cited in a dir of 8 same-ext files → *.py would match 8 > 3×2 → over_scope → literals.
    root, md = _repo(tmp_path, [f"pkg/f{i}.py" for i in range(8)])
    _mem(md, "two", ["pkg/f0.py", "pkg/f1.py"])
    res = PR.promote_to_rule(md, "two", root)
    assert res["globs"] == ["pkg/f0.py", "pkg/f1.py"]
    assert any(f["kind"] == "over_scope" for f in res["flags"])


def test_missing_cited_path_is_excluded_and_flagged(tmp_path):
    root, md = _repo(tmp_path, ["src/a.py"])
    _mem(md, "m", ["src/a.py", "src/gone.py"])
    res = PR.promote_to_rule(md, "m", root)
    assert res["globs"] == ["src/a.py"]
    assert any(f["kind"] == "missing" and f["path"] == "src/gone.py" for f in res["flags"])


# --------------------------------------------------------------------------- #
# Render shape + propose-only
# --------------------------------------------------------------------------- #
def test_body_wikilinks_rewritten_and_frontmatter_marked(tmp_path):
    root, md = _repo(tmp_path, ["src/a.py"])
    _mem(md, "m", ["src/a.py"], body="See [[other_memory|display]] and `run it`.")
    res = PR.promote_to_rule(md, "m", root)
    assert PR._RULE_MARKER in res["proposed"]
    assert "`other_memory`" in res["proposed"] and "[[" not in res["proposed"]


def test_promote_never_writes(tmp_path):
    root, md = _repo(tmp_path, ["src/a.py"])
    _mem(md, "m", ["src/a.py"])
    PR.promote_to_rule(md, "m", root)
    assert not os.path.exists(os.path.join(root, ".claude", "rules", "m.md"))  # propose-only


# --------------------------------------------------------------------------- #
# Refuse cases
# --------------------------------------------------------------------------- #
def test_refuses_unreadable_memory(tmp_path):
    root, md = _repo(tmp_path, ["src/a.py"])
    assert PR.promote_to_rule(md, "nope", root)["reason"]


def test_refuses_memory_without_cited_paths(tmp_path):
    root, md = _repo(tmp_path, ["src/a.py"])
    _mem(md, "m", [])
    res = PR.promote_to_rule(md, "m", root)
    assert res["proposed"] is None and "cites no paths" in res["reason"]


def test_refuses_retired_memory(tmp_path):
    root, md = _repo(tmp_path, ["src/a.py"])
    _mem(md, "m", ["src/a.py"], extra_fm="  invalid_after: 2020-01-01\n")
    res = PR.promote_to_rule(md, "m", root)
    assert res["proposed"] is None and "retired" in res["reason"]


def test_refuses_all_cited_missing(tmp_path):
    root, md = _repo(tmp_path, ["src/a.py"])
    _mem(md, "m", ["src/gone.py", "src/also_gone.py"])
    res = PR.promote_to_rule(md, "m", root)
    assert res["proposed"] is None and "no scopable path" in res["reason"]


def test_refuses_to_clobber_hand_authored_rule(tmp_path):
    root, md = _repo(tmp_path, ["src/a.py"])
    _mem(md, "m", ["src/a.py"])
    rules = os.path.join(root, ".claude", "rules")
    os.makedirs(rules)
    with open(os.path.join(rules, "m.md"), "w", encoding="utf-8") as fh:
        fh.write("---\npaths:\n  - \"src/a.py\"\n---\nhand-written rule, no hippo marker\n")
    res = PR.promote_to_rule(md, "m", root)
    assert res["proposed"] is None and "hand-authored" in res["reason"]


def test_regenerates_a_previously_promoted_rule(tmp_path):
    root, md = _repo(tmp_path, ["src/a.py"])
    _mem(md, "m", ["src/a.py"])
    res = PR.promote_to_rule(md, "m", root)
    rules = os.path.join(root, ".claude", "rules")
    os.makedirs(rules)
    with open(os.path.join(rules, "m.md"), "w", encoding="utf-8") as fh:
        fh.write(res["proposed"])  # our marker present
    again = PR.promote_to_rule(md, "m", root)
    assert again["proposed"] is not None and again["exists"] is True
    assert again["changed"] is False  # identical → no-op diff


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_render_is_read_only_then_apply_writes(tmp_path, capsys):
    root, md = _repo(tmp_path, ["src/a.py", "src/b.py"])
    _mem(md, "lint", ["src/a.py", "src/b.py"])
    rc = PR.main(["--name", "lint", "--memory-dir", md, "--repo-root", root])
    assert rc == 0
    out = capsys.readouterr().out
    assert "src/*.py" in out and "proposed diff" in out
    assert not os.path.exists(os.path.join(root, ".claude", "rules", "lint.md"))  # render wrote nothing

    rc = PR.main(["--name", "lint", "--memory-dir", md, "--repo-root", root, "--apply"])
    assert rc == 0
    assert os.path.isfile(os.path.join(root, ".claude", "rules", "lint.md"))


def test_cli_refusal_returns_1(tmp_path, capsys):
    root, md = _repo(tmp_path, ["src/a.py"])
    _mem(md, "m", [])
    rc = PR.main(["--name", "m", "--memory-dir", md, "--repo-root", root])
    assert rc == 1
    assert "REFUSED" in capsys.readouterr().out

