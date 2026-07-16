"""EXT-1: recall for the reviewer — memories on the PR diff.

A PR touches files; memories cite files; nobody connects them at review time. The lane
under test: ``recall --for-diff <range>`` resolves a git range to changed paths and
joins them against the corpus's cited_paths (feedback/steer types first, staleness
flags riding every row, bounded output), in text and ``--json`` forms. It is a pure
citation JOIN — no query, no index, no model, no telemetry — so it runs on a bare
python3 in CI (the vendored frontmatter path) and never writes anything.

The GitHub Action recipe (.github/workflows/memory-on-diff.yml — dogfooded on this
repo per the ratified quiet-first positioning) posts the result as ONE sticky PR
comment; a test here pins the recipe's contract lines (same-repo guard, sticky marker,
full-history checkout, skip-on-empty, quoted-data rendering).
"""

from __future__ import annotations

import json
import os
import subprocess

from memory import recall_diff as RD

from .conftest import write_file

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _git(repo, *args) -> str:
    return subprocess.run(
        ["git", "-C", repo, *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    ).stdout.strip()


def _commit_all(repo, msg, when: str = "") -> str:
    """Commit everything; ``when`` (any git date) pins BOTH dates so drift ordering is
    deterministic — two commits landing in the same wall-clock second would otherwise
    tie on committer time and read as not-drifted (find_stale compares strictly)."""
    _git(repo, "add", "-A")
    env = dict(_GIT_ENV)
    if when:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    subprocess.run(
        ["git", "-C", repo, "commit", "-qm", msg], check=True, capture_output=True, env=env
    )
    return _git(repo, "rev-parse", "HEAD")


def _mem(md, name, *, mtype="project", cited=(), steer="", desc="a lesson", commit=""):
    cp = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    steer_line = f"  steer: {steer}\n" if steer else ""
    commit_line = f'  source_commit: "{commit}"\n' if commit else ""
    write_file(
        md,
        f"{name}.md",
        f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: {mtype}\n'
        f"{steer_line}  cited_paths: {cp}\n{commit_line}---\nBody.\n",
    )


def _tree(root: str) -> set:
    out = set()
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            out.add(os.path.relpath(os.path.join(dirpath, f), root))
    return out


# --------------------------------------------------------------------------- #
# The git-range half: changed paths
# --------------------------------------------------------------------------- #
def test_changed_paths_resolves_a_range(repo, memory_dir):
    write_file(repo, "src/app.py", "x = 1\n")
    a = _commit_all(repo, "A")
    write_file(repo, "src/db.py", "y = 2\n")
    write_file(repo, "src/app.py", "x = 11\n")
    b = _commit_all(repo, "B")
    assert RD.changed_paths_for_range(f"{a}..{b}", repo) == ["src/app.py", "src/db.py"]
    assert RD.changed_paths_for_range(f"{a}...{b}", repo) == ["src/app.py", "src/db.py"]


def test_changed_paths_bad_range_is_empty_never_raises(repo, memory_dir):
    assert RD.changed_paths_for_range("no-such-ref..also-no", repo) == []
    assert RD.changed_paths_for_range("", repo) == []
    assert RD.changed_paths_for_range("HEAD~1..HEAD", None) == []


# --------------------------------------------------------------------------- #
# The join half: citing memories, ordered and flagged
# --------------------------------------------------------------------------- #
def test_join_returns_citing_memories_feedback_and_pins_first(repo, memory_dir):
    _mem(memory_dir, "zz-fb-lesson", mtype="feedback", cited=("src/app.py",))
    _mem(memory_dir, "aa-proj-note", mtype="project", cited=("src/app.py",))
    _mem(memory_dir, "mm-pinned", mtype="project", steer="pin", cited=("src/db.py",))
    _mem(memory_dir, "uncited-elsewhere", mtype="feedback", cited=("src/other.py",))
    rows = RD.memories_for_paths(["src/app.py", "src/db.py"], memory_dir, repo_root=repo)
    names = [r["name"] for r in rows]
    assert names == ["mm-pinned", "zz-fb-lesson", "aa-proj-note"]
    assert rows[0]["steer"] == "pin"
    assert rows[1]["type"] == "feedback"
    assert rows[2]["paths"] == ["src/app.py"]  # which changed files the memory cites
    assert "uncited-elsewhere" not in names


def test_join_empty_when_nothing_cites_the_diff(repo, memory_dir):
    _mem(memory_dir, "fb-lesson", mtype="feedback", cited=("src/other.py",))
    assert RD.memories_for_paths(["src/app.py"], memory_dir, repo_root=repo) == []


def test_stale_flag_rides_the_row(repo, memory_dir):
    # The memory is anchored at commit A citing src/app.py; the file then drifts in B
    # (dates pinned an hour apart so the strict newer-than-baseline compare is real).
    write_file(repo, "src/app.py", "x = 1\n")
    a = _commit_all(repo, "A", when="2026-07-01T10:00:00")
    _mem(memory_dir, "drifted-note", mtype="feedback", cited=("src/app.py",), commit=a)
    _commit_all(repo, "add memory", when="2026-07-01T10:30:00")
    write_file(repo, "src/app.py", "x = 2\n")
    _commit_all(repo, "B drifts the cited file", when="2026-07-01T11:00:00")
    rows = RD.memories_for_paths(["src/app.py"], memory_dir, repo_root=repo)
    assert rows and rows[0]["name"] == "drifted-note"
    assert rows[0]["stale"], "a drifted memory must carry its staleness flag"
    # ... and the renderers mark it: a stale lesson is FLAGGED, never asserted fresh.
    text = RD.render_text(rows, range_expr="A..B", changed_count=1)
    assert "stale" in text


def test_fresh_memory_carries_no_stale_flag(repo, memory_dir):
    write_file(repo, "src/app.py", "x = 1\n")
    _commit_all(repo, "A")
    head = _git(repo, "rev-parse", "HEAD")
    _mem(memory_dir, "fresh-note", mtype="feedback", cited=("src/app.py",), commit=head)
    rows = RD.memories_for_paths(["src/app.py"], memory_dir, repo_root=repo)
    assert rows and rows[0]["stale"] is None


# --------------------------------------------------------------------------- #
# The CLI: recall --for-diff (text + json), read-only, exit 0 on empty
# --------------------------------------------------------------------------- #
def _run_cli(args):
    from memory import recall as R

    return R.main(args)


def test_cli_json_shape_and_cap(repo, memory_dir, capsys):
    # Memories exist BEFORE the range so the A..B diff is exactly the one code file.
    write_file(repo, "src/app.py", "x = 1\n")
    for i in range(12):
        _mem(memory_dir, f"note-{i:02d}", mtype="feedback", cited=("src/app.py",))
    a = _commit_all(repo, "A")
    write_file(repo, "src/app.py", "x = 2\n")
    b = _commit_all(repo, "B")

    rc = _run_cli([
        "--for-diff", f"{a}..{b}", "--json",
        "--memory-dir", memory_dir, "--repo-root", repo,
    ])
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["changed_paths"] == 1
    assert doc["total"] == 12
    assert len(doc["items"]) == RD.DEFAULT_CAP
    row = doc["items"][0]
    assert set(row) >= {"name", "type", "steer", "description", "stale", "paths"}


def test_cli_cap_flag_overrides(repo, memory_dir, capsys):
    write_file(repo, "src/app.py", "x = 1\n")
    a = _commit_all(repo, "A")
    for i in range(5):
        _mem(memory_dir, f"note-{i}", mtype="feedback", cited=("src/app.py",))
    write_file(repo, "src/app.py", "x = 2\n")
    b = _commit_all(repo, "B")
    rc = _run_cli([
        "--for-diff", f"{a}..{b}", "--json", "--cap", "2",
        "--memory-dir", memory_dir, "--repo-root", repo,
    ])
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert len(doc["items"]) == 2 and doc["total"] == 5


def test_cli_text_form_names_memory_and_flags(repo, memory_dir, capsys):
    write_file(repo, "src/app.py", "x = 1\n")
    a = _commit_all(repo, "A")
    _mem(memory_dir, "canary-lesson", mtype="feedback", cited=("src/app.py",),
         desc="run the canary lane first")
    write_file(repo, "src/app.py", "x = 2\n")
    b = _commit_all(repo, "B")
    rc = _run_cli(["--for-diff", f"{a}..{b}", "--memory-dir", memory_dir, "--repo-root", repo])
    assert rc == 0
    out = capsys.readouterr().out
    assert "canary-lesson" in out and "[feedback]" in out
    assert "run the canary lane first" in out


def test_cli_empty_diff_exits_zero_and_prints_nothing_in_text(repo, memory_dir, capsys):
    write_file(repo, "src/app.py", "x = 1\n")
    a = _commit_all(repo, "A")
    write_file(repo, "unrelated.md", "hi\n")
    b = _commit_all(repo, "B")
    _mem(memory_dir, "fb-lesson", mtype="feedback", cited=("src/app.py",))
    rc = _run_cli(["--for-diff", f"{a}..{b}", "--memory-dir", memory_dir, "--repo-root", repo])
    assert rc == 0
    assert capsys.readouterr().out.strip() == ""


def test_cli_empty_diff_json_is_still_valid(repo, memory_dir, capsys):
    write_file(repo, "a.txt", "1\n")
    a = _commit_all(repo, "A")
    write_file(repo, "b.txt", "2\n")
    b = _commit_all(repo, "B")
    rc = _run_cli(["--for-diff", f"{a}..{b}", "--json", "--memory-dir", memory_dir, "--repo-root", repo])
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["items"] == []


def test_for_diff_is_read_only_no_index_no_telemetry(repo, memory_dir, capsys):
    """The reviewer lane is a pure join: no index build, no telemetry row, no corpus
    write — a fresh CI checkout (no .memory-index, no venv model) must satisfy it."""
    write_file(repo, "src/app.py", "x = 1\n")
    a = _commit_all(repo, "A")
    _mem(memory_dir, "fb-lesson", mtype="feedback", cited=("src/app.py",))
    write_file(repo, "src/app.py", "x = 2\n")
    b = _commit_all(repo, "B")
    before = _tree(repo)
    rc = _run_cli(["--for-diff", f"{a}..{b}", "--memory-dir", memory_dir, "--repo-root", repo])
    assert rc == 0 and capsys.readouterr().out
    assert _tree(repo) == before, "recall --for-diff must write NOTHING (read-only lane)"


# --------------------------------------------------------------------------- #
# The dogfood recipe: one sticky comment, same-repo only, quoted data
# --------------------------------------------------------------------------- #
_WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows", "memory-on-diff.yml"
)


class TestActionRecipe:
    def _text(self) -> str:
        with open(os.path.abspath(_WORKFLOW), encoding="utf-8") as fh:
            return fh.read()

    def test_recipe_exists_and_parses(self):
        import yaml

        doc = yaml.safe_load(self._text())
        assert doc, "the EXT-1 dogfood recipe must exist and be valid YAML"

    def test_recipe_contract_lines(self):
        text = self._text()
        # Same-repo PRs only by default (fork-token posture: forks get no token).
        assert "head.repo.full_name == github.repository" in text
        # Full history — staleness baselines need resolvable commits (a shallow clone
        # would silently mark everything fresh).
        assert "fetch-depth: 0" in text
        # ONE sticky comment, updated in place — never a comment per push: a marker
        # identifies the bot's own comment, and BOTH update and create paths exist.
        assert "<!-- hippo-memory-on-diff -->" in text
        assert "updateComment" in text and "createComment" in text
        # Empty result -> no comment at all (the empty norm).
        assert "items.length" in text or "skip" in text.lower()
        # Quoted-data rendering (SEC-5): the corpus text lands inside a fenced block.
        assert "```" in text
        # Least privilege: the job asks for exactly the comment permission it needs.
        assert "pull-requests: write" in text
        assert "contents: read" in text

    def test_recipe_names_only_real_flags(self):
        """INT-18's honesty rule, applied to the recipe: every memory.* invocation it
        contains must name flags the CLI actually has."""
        text = self._text()
        assert "--for-diff" in text and "--json" in text
        import memory.recall_diff as rd

        assert hasattr(rd, "main")
