"""TND-5 field finding: memories carrying two ``Related:`` lines.

On the dogfood corpus about a fifth of the memories end with two ``Related:`` lines — the
agent-written body already closed on its own ``Related: [[...]]`` line, and ``new_memory``
appended a second one (from ``links`` or from discovery) underneath it instead of merging.
The repro below pins the writer; the fix merges into ONE line (union, order kept, deduped).
A read-only scan finds the files already carrying more than one line, and a per-item fixer
merges one file at a time — neither ever runs on a corpus by itself.
"""

from __future__ import annotations

import os
import re
import subprocess

import pytest

from memory import new_memory as NM

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    root = str(tmp_path / "repo")
    md = os.path.join(root, ".claude", "memory")
    os.makedirs(md)
    with open(os.path.join(root, "app.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(["git", "-C", root, "add", "-A"], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "seed"], check=True, env=_GIT_ENV)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", root)
    for name in ("alpha_topic", "beta_topic", "gamma_topic"):
        with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
            fh.write(f'---\nname: {name}\ndescription: "d {name}"\nmetadata:\n  type: project\n---\nBody.\n')
    return root, md


def _related_lines(text):
    return [ln for ln in text.split("\n") if re.match(r"^Related:", ln)]


# --------------------------------------------------------------------------- #
# Repro: the writer appends a second Related line under the body's own
# --------------------------------------------------------------------------- #
def test_explicit_links_merge_into_the_bodys_own_related_line(corpus):
    root, md = corpus
    body = "The fact.\n\nRelated: [[alpha_topic]]\n"
    r = NM.write_memory("new_fact", "a new fact", "project", body=body,
                        memory_dir=md, repo_root=root, links=["alpha_topic", "beta_topic"])
    text = open(r["path"], encoding="utf-8").read()
    assert _related_lines(text) == ["Related: [[alpha_topic]], [[beta_topic]]"]


def test_discovered_links_merge_into_the_bodys_own_related_line(corpus, monkeypatch):
    root, md = corpus
    monkeypatch.setattr(NM, "_discover_links", lambda *a, **k: ["beta_topic", "gamma_topic"])
    body = "The fact.\n\nRelated: [[alpha_topic]], [[beta_topic]]\n"
    r = NM.write_memory("disc_fact", "a discovered fact", "project", body=body,
                        memory_dir=md, repo_root=root)
    text = open(r["path"], encoding="utf-8").read()
    assert _related_lines(text) == [
        "Related: [[alpha_topic]], [[beta_topic]], [[gamma_topic]]"
    ]


def test_annotated_related_line_keeps_its_prose(corpus):
    root, md = corpus
    body = "The fact.\n\nRelated: [[alpha_topic]] (the older half of this story)\n"
    r = NM.write_memory("annot", "an annotated fact", "project", body=body,
                        memory_dir=md, repo_root=root, links=["alpha_topic", "beta_topic"])
    text = open(r["path"], encoding="utf-8").read()
    assert _related_lines(text) == [
        "Related: [[alpha_topic]] (the older half of this story), [[beta_topic]]"
    ]


def test_a_body_without_a_related_line_still_gets_one_appended(corpus):
    root, md = corpus
    r = NM.write_memory("plain", "a plain fact", "project", body="The fact.",
                        memory_dir=md, repo_root=root, links=["alpha_topic"])
    text = open(r["path"], encoding="utf-8").read()
    assert text.endswith("The fact.\n\nRelated: [[alpha_topic]]\n")


def test_a_related_line_inside_a_code_fence_is_not_the_bodys_line(corpus):
    root, md = corpus
    body = "Example:\n\n```\nRelated: [[not_a_link]]\n```\n"
    r = NM.write_memory("fenced", "a fenced example", "project", body=body,
                        memory_dir=md, repo_root=root, links=["alpha_topic"])
    text = open(r["path"], encoding="utf-8").read()
    assert "Related: [[not_a_link]]\n```\n\nRelated: [[alpha_topic]]\n" in text


# --------------------------------------------------------------------------- #
# Detection (read-only) and the per-item fixer
# --------------------------------------------------------------------------- #
def _write(md, name, body):
    path = os.path.join(md, f"{name}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f'---\nname: {name}\ndescription: "d"\nmetadata:\n  type: project\n---\n{body}')
    return path


def test_scan_finds_files_with_more_than_one_related_line_and_writes_nothing(corpus):
    from memory import related_lines as RL

    _, md = corpus
    _write(md, "twice", "Fact.\n\nRelated: [[alpha_topic]]\n\nRelated: [[alpha_topic]], [[beta_topic]]\n")
    _write(md, "once", "Fact.\n\nRelated: [[alpha_topic]]\n")
    _write(md, "fenced", "Fact.\n\n```\nRelated: [[x]]\n```\n\nRelated: [[alpha_topic]]\n")
    before = {n: open(os.path.join(md, n), "rb").read() for n in os.listdir(md)}
    found = RL.scan_duplicate_related(md)
    assert [f["name"] for f in found] == ["twice"]
    assert found[0]["count"] == 2
    assert {n: open(os.path.join(md, n), "rb").read() for n in os.listdir(md)} == before


def test_fixer_merges_pure_lines_into_one_at_the_end(corpus):
    from memory import related_lines as RL

    _, md = corpus
    path = _write(md, "twice", "Fact.\n\nRelated: [[alpha_topic]]\n\nRelated: [[alpha_topic]], [[beta_topic]]\n")
    res = RL.fix_duplicate_related(path)
    assert res["fixed"] is True and res["error"] is None
    text = open(path, encoding="utf-8").read()
    assert text.endswith("Fact.\n\nRelated: [[alpha_topic]], [[beta_topic]]\n")
    assert RL.scan_duplicate_related(md) == []


def test_fixer_keeps_one_annotated_line_and_adds_the_rest_to_it(corpus):
    from memory import related_lines as RL

    _, md = corpus
    path = _write(md, "annot",
                  "Fact.\n\nRelated: [[alpha_topic]] (why alpha matters)\n\nRelated: [[gamma_topic]], [[alpha_topic]]\n")
    assert RL.fix_duplicate_related(path)["fixed"] is True
    text = open(path, encoding="utf-8").read()
    assert text.endswith("Fact.\n\nRelated: [[alpha_topic]] (why alpha matters), [[gamma_topic]]\n")


def test_fixer_refuses_two_annotated_lines_and_writes_nothing(corpus):
    from memory import related_lines as RL

    _, md = corpus
    body = "Fact.\n\nRelated: [[alpha_topic]] (one note)\n\nRelated: [[beta_topic]] (another note)\n"
    path = _write(md, "two_notes", body)
    before = open(path, "rb").read()
    res = RL.fix_duplicate_related(path)
    assert res["fixed"] is False and "by hand" in res["error"]
    assert open(path, "rb").read() == before


def test_fixer_is_a_noop_on_a_single_line(corpus):
    from memory import related_lines as RL

    _, md = corpus
    path = _write(md, "once", "Fact.\n\nRelated: [[alpha_topic]]\n")
    before = open(path, "rb").read()
    res = RL.fix_duplicate_related(path)
    assert res["fixed"] is False and res["error"] is None
    assert open(path, "rb").read() == before


def test_lint_links_cli_reports_the_duplicates(corpus, capsys):
    from memory import lint_links as LL

    _, md = corpus
    _write(md, "twice", "Fact.\n\nRelated: [[alpha_topic]]\n\nRelated: [[beta_topic]]\n")
    LL.main(["--memory-dir", md])
    out = capsys.readouterr().out
    assert "more than one Related line: 1" in out
    assert "twice" in out and "hippo links --merge-related twice" in out


def test_links_cli_merges_one_named_memory(corpus, capsys):
    from memory import links as L

    _, md = corpus
    path = _write(md, "twice", "Fact.\n\nRelated: [[alpha_topic]]\n\nRelated: [[beta_topic]]\n")
    assert L.main(["--memory-dir", md, "--merge-related", "twice"]) == 0
    assert "merged" in capsys.readouterr().out
    assert open(path, encoding="utf-8").read().endswith("Related: [[alpha_topic]], [[beta_topic]]\n")
    assert L.main(["--memory-dir", md, "--merge-related", "no_such"]) == 1
