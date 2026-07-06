"""Tests for SHP-4 — non-git degraded mode is supported, labeled, not an error.

init's own preflight/notice logic is prose in plugin/skills/init/SKILL.md (no Python
function backs it directly — an agent-driven skill, not a CLI), so these tests exercise
its underlying Python primitives directly, in a tmp_path dir that is explicitly NOT a git
repo (no `repo`/`memory_dir` fixture — those always `git init`): build_index.build_index,
provenance.resolve_dirs/backfill_corpus/backfill_file, staleness.find_stale, and
archive.archive_memory's os.rename fallback (COR-5, this item's prerequisite). Every one
must succeed without raising and without requiring git — that is the "engine mostly works
without git" half of the proposal; the other half (init no longer hard-refusing, and
naming the degradation loudly) is prose-only and is noted, not unit-tested, below.
"""

from __future__ import annotations

import os

from memory import archive as A
from memory import build_index as B
from memory import provenance as P
from memory import staleness as S


def _non_git_dir(tmp_path) -> str:
    d = tmp_path / "plain_dir"
    d.mkdir()
    return str(d)


# --------------------------------------------------------------------------- #
# resolve_dirs / git_root — degrade to None/fallback, never raise
# --------------------------------------------------------------------------- #
def test_git_root_returns_none_outside_any_repo(tmp_path):
    d = _non_git_dir(tmp_path)
    assert P.git_root(d) is None


def test_resolve_dirs_falls_back_to_start_when_no_git_repo(tmp_path, monkeypatch):
    d = _non_git_dir(tmp_path)
    monkeypatch.delenv("MEMOBOT_MEMORY_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", d)

    memory_dir, repo_root = P.resolve_dirs()
    assert memory_dir == os.path.join(d, ".claude", "memory")
    assert os.path.abspath(repo_root) == os.path.abspath(d)  # falls back to the raw dir


# --------------------------------------------------------------------------- #
# init's underlying seeding primitives — corpus + index build without git
# --------------------------------------------------------------------------- #
def test_build_index_succeeds_in_non_git_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")  # hermetic — no model download
    d = _non_git_dir(tmp_path)
    memory_dir = os.path.join(d, ".claude", "memory")
    os.makedirs(memory_dir)
    with open(os.path.join(memory_dir, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: MEMORY\n---\nfloor body\n")
    with open(os.path.join(memory_dir, "user_role.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: user_role\ndescription: "role"\n---\nseeded starter-pack memory\n')

    index_dir = os.path.join(d, ".claude", ".memory-index")
    manifest = B.build_index(memory_dir=memory_dir, index_dir=index_dir)
    assert manifest["count"] == 1  # MEMORY.md itself is excluded (the floor file, not corpus)
    assert os.path.isfile(os.path.join(index_dir, "manifest.json"))


def test_backfill_corpus_never_raises_and_writes_empty_baseline_in_non_git_dir(tmp_path):
    d = _non_git_dir(tmp_path)
    memory_dir = os.path.join(d, ".claude", "memory")
    os.makedirs(memory_dir)
    path = os.path.join(memory_dir, "m.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("---\nname: m\n---\nbody cites src/x.py\n")

    results = P.backfill_corpus(memory_dir, d)
    assert len(results) == 1
    assert results[0]["error"] is None
    assert results[0]["changed"] is True
    assert results[0]["source_commit"] is None  # no git history anywhere -> honest empty baseline

    # Idempotent re-run still doesn't raise or re-change anything.
    second = P.backfill_corpus(memory_dir, d)
    assert second[0]["changed"] is False


def test_build_repo_file_index_is_empty_but_does_not_raise_in_non_git_dir(tmp_path):
    d = _non_git_dir(tmp_path)
    repo_files, basename_index = P.build_repo_file_index(d)
    assert repo_files == set()
    assert basename_index == {}


# --------------------------------------------------------------------------- #
# staleness / provenance stay legible no-ops (never crash) without git
# --------------------------------------------------------------------------- #
def test_find_stale_returns_empty_not_raises_in_non_git_dir(tmp_path):
    d = _non_git_dir(tmp_path)
    memory_dir = os.path.join(d, ".claude", "memory")
    os.makedirs(memory_dir)
    with open(os.path.join(memory_dir, "m.md"), "w", encoding="utf-8") as fh:
        fh.write(
            '---\nname: m\ncited_paths: ["src/x.py"]\nsource_commit: "deadbeef"\n---\nbody\n'
        )
    assert S.find_stale(memory_dir, d) == []


def test_find_unparseable_does_not_raise_in_non_git_dir(tmp_path):
    d = _non_git_dir(tmp_path)
    memory_dir = os.path.join(d, ".claude", "memory")
    os.makedirs(memory_dir)
    with open(os.path.join(memory_dir, "m.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: m\n---\nbody\n")
    assert S.find_unparseable(memory_dir) == []


# --------------------------------------------------------------------------- #
# archive — falls back to os.rename (COR-5) when there is no git at all
# --------------------------------------------------------------------------- #
def test_archive_memory_falls_back_to_rename_with_no_git_repo_at_all(tmp_path):
    d = _non_git_dir(tmp_path)
    memory_dir = os.path.join(d, ".claude", "memory")
    os.makedirs(memory_dir)
    with open(os.path.join(memory_dir, "m.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: m\ndescription: \"m\"\n---\nbody\n")

    result = A.archive_memory("m", memory_dir, d)
    assert result == {"name": "m", "moved": True, "error": None}
    assert os.path.exists(os.path.join(memory_dir, "archive", "m.md"))
    assert not os.path.exists(os.path.join(memory_dir, "m.md"))

    journal = os.path.join(memory_dir, "archive", ".archive_journal.jsonl")
    assert os.path.isfile(journal)  # journaled fallback move, per COR-5


def test_archive_candidates_never_raises_in_non_git_dir(tmp_path):
    d = _non_git_dir(tmp_path)
    memory_dir = os.path.join(d, ".claude", "memory")
    os.makedirs(memory_dir)
    with open(os.path.join(memory_dir, "m.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: m\n---\nbody\n")
    assert A.archive_candidates(memory_dir, d) == []


# --------------------------------------------------------------------------- #
# init/doctor SKILL.md prose note
# --------------------------------------------------------------------------- #
# init's preflight ("is this a git repo, print the degradation notice, skip the .gitignore
# patch and commit nudge") and doctor's new check #10 are both agent-followed prose in
# plugin/skills/init/SKILL.md and plugin/skills/doctor/SKILL.md, not standalone Python
# entry points — there is nothing importable to unit-test for the notice text itself. The
# tests above cover every Python primitive that prose delegates to (index build, corpus
# resolution, backfill, staleness, archive fallback), all of which are exercised here in a
# tmp_path dir that is provably not a git repo (git_root() returns None, confirmed above).
