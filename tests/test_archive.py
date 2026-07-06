"""Tests for memory/archive.py — the 4-way archive-candidate gate + git-mv primitive.

Hermetic: a throwaway git repo (`repo`/`memory_dir` fixtures) + a synthesized recall-event
ledger + a synthesized instruction-surface (CLAUDE.md / .claude/rules) under the repo root.
Nothing touches the real ~/.claude memory dir or the real ic-memobot repo.
"""

from __future__ import annotations

import inspect
import json
import os

import memory.archive as A

from .conftest import git_commit, write_file

# find_stale()'s default `since` window is wall-clock-relative; widen it for pinned fixtures.
_ALL = "2000-01-01"


def _mem(name, cited, source_commit, body="body"):
    cp = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    sc = f'"{source_commit}"' if source_commit is not None else "null"
    return f'---\nname: {name}\ndescription: "{name} description"\ncited_paths: {cp}\nsource_commit: {sc}\n---\n{body}\n'


def _seed_events(td, session_names):
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "recall_events.jsonl"), "w", encoding="utf-8") as fh:
        for sid, names in session_names:
            fh.write(json.dumps({"session_id": sid, "names": names, "backend": "bm25"}) + "\n")


# --------------------------------------------------------------------------- #
# archive_candidates — the 4-way intersection
# --------------------------------------------------------------------------- #
def test_archive_candidates_is_exactly_the_4way_intersection(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)

    # m_archivable: stale + cold + zero-inbound + not cited -> THE only candidate.
    write_file(memory_dir, "m_archivable.md", _mem("m_archivable", ["src/foo.py"], c1))
    # m_recalled: same shape, but WAS recalled -> not cold -> excluded.
    write_file(memory_dir, "m_recalled.md", _mem("m_recalled", ["src/foo.py"], c1))
    # m_pointed_to: same shape, but HAS an inbound wikilink -> excluded.
    write_file(memory_dir, "m_pointed_to.md", _mem("m_pointed_to", ["src/foo.py"], c1))
    # m_linker: fresh (no provenance -> never flagged stale itself), exists only to give
    # m_pointed_to an inbound edge.
    write_file(memory_dir, "m_linker.md", _mem("m_linker", [], None, body="see [[m_pointed_to]] for context"))
    # m_cited: same shape, but CITED in CLAUDE.md -> excluded.
    write_file(memory_dir, "m_cited.md", _mem("m_cited", ["src/foo.py"], c1))
    # m_fresh: baseline matches HEAD (no drift at all) -> never stale -> excluded regardless.
    write_file(repo, "src/bar.py", "y = 1\n")
    c2 = git_commit(repo, "c2", 1_700_000_050)
    write_file(memory_dir, "m_fresh.md", _mem("m_fresh", ["src/bar.py"], c2))

    write_file(repo, "src/foo.py", "x = 2\n")  # drift -> archivable/recalled/pointed_to/cited all stale
    git_commit(repo, "c3", 1_700_000_100)

    write_file(repo, "CLAUDE.md", "See `m_cited.md` for details on this subsystem.\n")

    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_recalled"])])  # only m_recalled was ever recalled

    candidates = A.archive_candidates(memory_dir, repo, telemetry_dir=td, since=_ALL)
    assert [c["name"] for c in candidates] == ["m_archivable"]


def test_archive_candidates_empty_when_nothing_satisfies_all_four(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_a.md", _mem("m_a", ["src/foo.py"], c1))
    git_commit(repo, "c2", 1_700_000_050)  # nothing cited drifts -> m_a never goes stale

    td = os.path.join(repo, "tele")
    assert A.archive_candidates(memory_dir, repo, telemetry_dir=td, since=_ALL) == []


def test_archive_candidates_never_raises_on_bogus_dirs():
    assert A.archive_candidates("/no/such/memory", "/no/such/repo") == []


def test_archive_candidates_empty_corpus_returns_empty(repo, memory_dir):
    assert A.archive_candidates(memory_dir, repo) == []


# --------------------------------------------------------------------------- #
# _cited_by_claude_md_names — backtick-anchored, extension-optional matching
# --------------------------------------------------------------------------- #
def test_cited_names_matches_backtick_with_md_suffix(repo):
    write_file(repo, "CLAUDE.md", "See `some_memory.md` for the full design.\n")
    cited = A._cited_by_claude_md_names(repo, {"some_memory", "other_memory"})
    assert cited == {"some_memory"}


def test_cited_names_matches_backtick_without_md_suffix(repo):
    """The exact false-negative an adversarial review caught: rules/20-patterns.md-style
    citations are bare backtick slugs with no .md suffix anywhere nearby."""
    write_file(repo, "CLAUDE.md", "find the mechanism (`feedback_dont_blame_anthropic_latency`), then ship.\n")
    cited = A._cited_by_claude_md_names(repo, {"feedback_dont_blame_anthropic_latency", "unrelated_memory"})
    assert cited == {"feedback_dont_blame_anthropic_latency"}


def test_cited_names_avoids_substring_collision(repo):
    """A naive bare-substring scan would false-positive a SHORTER name that happens to be a
    substring of a LONGER cited one (e.g. "foo" inside "foo_extended") -- the backtick-token
    extraction matches the FULL token exactly, never a substring containment check."""
    write_file(repo, "CLAUDE.md", "See `foo_extended.md` for the full design.\n")
    cited = A._cited_by_claude_md_names(repo, {"foo", "foo_extended"})
    assert cited == {"foo_extended"}  # "foo" is a substring of the cited token but NOT cited itself


def test_cited_names_scans_rules_agents_skills_and_prompts_dirs(repo):
    write_file(repo, ".claude/rules/20-patterns.md", "see `from_rules`\n")
    write_file(repo, ".claude/agents/scaffold.md", "see `from_agents.md`\n")
    write_file(repo, ".claude/skills/deploy.md", "see `from_skills`\n")
    write_file(repo, "docs/prompts/evergreen-prompt-library.md", "see `from_prompts.md`\n")
    corpus = {"from_rules", "from_agents", "from_skills", "from_prompts", "untouched"}
    cited = A._cited_by_claude_md_names(repo, corpus)
    assert cited == {"from_rules", "from_agents", "from_skills", "from_prompts"}


def test_cited_names_ignores_non_corpus_backtick_tokens(repo):
    """Backtick-quoted code identifiers, function names, and the rules files' own
    self-references must not falsely inflate the cited set -- only names that ARE in the
    real corpus ever survive the intersection."""
    write_file(repo, "CLAUDE.md", "Run `_run_bounded_stage` and see `20-patterns.md` and `README.md`.\n")
    cited = A._cited_by_claude_md_names(repo, {"real_memory"})
    assert cited == set()


def test_cited_names_fails_closed_when_scan_target_unreadable(repo, monkeypatch):
    """Fail CLOSED (treat as cited -> excluded from candidates), not open -- an unreadable
    instruction surface must never let the gate wrongly conclude 'definitely not cited'."""
    monkeypatch.setattr(A, "_scan_files", lambda repo_root: (_ for _ in ()).throw(RuntimeError("boom")))
    corpus = {"a", "b"}
    assert A._cited_by_claude_md_names(repo, corpus) == corpus


def test_cited_names_no_scan_targets_present_is_empty(repo):
    # no CLAUDE.md, no .claude/rules, etc. at all -- a clean empty result, not a crash.
    assert A._cited_by_claude_md_names(repo, {"a", "b"}) == set()


def test_cited_names_fails_closed_on_per_file_unreadable_claude_md(repo):
    """A per-FILE read error (unreadable CLAUDE.md) must fail CLOSED for the WHOLE scan --
    not silently `continue` past it, which would fail OPEN for exactly that file's would-be
    citations. chmod 000 to simulate a permission-denied read; restore in finally so the
    test always cleans up regardless of assertion outcome."""
    claude_md = write_file(repo, "CLAUDE.md", "see `a` and `b`\n")
    try:
        os.chmod(claude_md, 0o000)
        cited = A._cited_by_claude_md_names(repo, {"a", "b", "c"})
        assert cited == {"a", "b", "c"}  # fail closed: entire corpus treated as cited
    finally:
        os.chmod(claude_md, 0o644)


def test_cited_names_records_unreadable_path_for_reporting(repo):
    claude_md = write_file(repo, "CLAUDE.md", "see `a`\n")
    try:
        os.chmod(claude_md, 0o000)
        unreadable: list = []
        A._cited_by_claude_md_names(repo, {"a"}, unreadable=unreadable)
        assert unreadable == [claude_md]
    finally:
        os.chmod(claude_md, 0o644)


def test_archive_candidates_excludes_memory_when_claude_md_unreadable(repo, memory_dir):
    """End-to-end: archive_candidates must exclude a memory whose citation would only be
    found in an unreadable CLAUDE.md -- fail closed, matching the documented contract."""
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_a.md", _mem("m_a", ["src/foo.py"], c1))
    claude_md = write_file(repo, "CLAUDE.md", "see `m_a`\n")
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_050)

    td = os.path.join(repo, "tele")
    try:
        os.chmod(claude_md, 0o000)
        candidates = A.archive_candidates(memory_dir, repo, telemetry_dir=td, since=_ALL)
        assert candidates == []  # m_a fails closed to "cited" -> excluded
    finally:
        os.chmod(claude_md, 0o644)


def test_main_surfaces_unreadable_scan_target_warning(repo, memory_dir, capsys):
    """Even though a fail-closed unreadable CLAUDE.md can only ever SHRINK the candidate
    set (never surface a candidate alongside the warning), the CLI must still print the
    degradation -- never a silent no-op (guiding invariant: legible degradation)."""
    claude_md = write_file(repo, "CLAUDE.md", "see `m_a`\n")
    git_commit(repo, "c1", 1_700_000_000)
    try:
        os.chmod(claude_md, 0o000)
        rc = A.main(["--memory-dir", memory_dir, "--repo-root", repo])
        assert rc == 0
        out = capsys.readouterr().out
        assert "unreadable during citation scan" in out
        assert claude_md in out
    finally:
        os.chmod(claude_md, 0o644)


# --------------------------------------------------------------------------- #
# _zero_inbound_names — distinct from LinkGraph.orphans() (zero OUTBOUND)
# --------------------------------------------------------------------------- #
def test_zero_inbound_excludes_a_memory_with_an_inbound_link(memory_dir):
    write_file(memory_dir, "m_target.md", _mem("m_target", [], None))
    write_file(memory_dir, "m_source.md", _mem("m_source", [], None, body="see [[m_target]]"))
    zi = A._zero_inbound_names(memory_dir)
    assert "m_target" not in zi  # has an inbound link
    assert "m_source" in zi  # nothing links TO m_source


def test_zero_inbound_includes_a_memory_with_no_links_at_all(memory_dir):
    write_file(memory_dir, "m_isolated.md", _mem("m_isolated", [], None))
    assert "m_isolated" in A._zero_inbound_names(memory_dir)


def test_zero_inbound_never_raises_on_missing_dir():
    assert A._zero_inbound_names("/no/such/memory") == set()


# --------------------------------------------------------------------------- #
# archive_memory — per-item git mv, never delete, git-reversible
# --------------------------------------------------------------------------- #
def test_archive_memory_git_mvs_into_archive_subdir(repo, memory_dir):
    write_file(memory_dir, "m_a.md", _mem("m_a", [], None))
    git_commit(repo, "add memory", 1_700_000_000)

    result = A.archive_memory("m_a", memory_dir, repo)
    assert result == {"name": "m_a", "moved": True, "error": None}

    archived_path = os.path.join(memory_dir, "archive", "m_a.md")
    assert os.path.exists(archived_path)
    assert not os.path.exists(os.path.join(memory_dir, "m_a.md"))
    # content is preserved byte-for-byte (a move, not a delete+recreate)
    with open(archived_path, encoding="utf-8") as fh:
        assert "m_a description" in fh.read()


def test_archive_memory_makes_memory_drop_from_iter_memory_files(repo, memory_dir):
    """The non-recursive _iter_memory_files skip means an archived memory instantly drops
    from index/recall/staleness -- no code change needed elsewhere."""
    from memory.provenance import _iter_memory_files

    write_file(memory_dir, "m_a.md", _mem("m_a", [], None))
    write_file(memory_dir, "m_b.md", _mem("m_b", [], None))
    git_commit(repo, "add memories", 1_700_000_000)

    before = {os.path.basename(p) for p in _iter_memory_files(memory_dir)}
    assert before == {"m_a.md", "m_b.md"}

    A.archive_memory("m_a", memory_dir, repo)
    after = {os.path.basename(p) for p in _iter_memory_files(memory_dir)}
    assert after == {"m_b.md"}  # m_a is gone from the iterator (it's now under archive/)


def test_archive_memory_is_git_reversible(repo, memory_dir):
    write_file(memory_dir, "m_a.md", _mem("m_a", [], None))
    git_commit(repo, "add memory", 1_700_000_000)

    A.archive_memory("m_a", memory_dir, repo)
    # git mv stages the rename; commit it, then prove `git mv` back restores the original path.
    git_commit(repo, "archive m_a", 1_700_000_100)

    import subprocess

    archived = os.path.join(memory_dir, "archive", "m_a.md")
    restored = os.path.join(memory_dir, "m_a.md")
    proc = subprocess.run(["git", "-C", repo, "mv", archived, restored], capture_output=True, text=True)
    assert proc.returncode == 0
    assert os.path.exists(restored) and not os.path.exists(archived)


def test_archive_memory_never_deletes_only_moves(repo, memory_dir):
    body_text = "irreplaceable content that must survive the move\n"
    write_file(memory_dir, "m_a.md", _mem("m_a", [], None, body=body_text.strip()))
    git_commit(repo, "add memory", 1_700_000_000)

    A.archive_memory("m_a", memory_dir, repo)
    archived = os.path.join(memory_dir, "archive", "m_a.md")
    with open(archived, encoding="utf-8") as fh:
        assert "irreplaceable content" in fh.read()


def test_archive_memory_accepts_name_without_md_suffix(repo, memory_dir):
    write_file(memory_dir, "m_a.md", _mem("m_a", [], None))
    git_commit(repo, "add memory", 1_700_000_000)
    result = A.archive_memory("m_a", memory_dir, repo)  # no .md suffix passed
    assert result["moved"] is True


def test_archive_memory_not_found_refuses_cleanly(repo, memory_dir):
    result = A.archive_memory("does_not_exist", memory_dir, repo)
    assert result["moved"] is False
    assert result["error"] is not None


def test_archive_memory_dry_run_does_not_touch_filesystem(repo, memory_dir):
    write_file(memory_dir, "m_a.md", _mem("m_a", [], None))
    git_commit(repo, "add memory", 1_700_000_000)
    result = A.archive_memory("m_a", memory_dir, repo, dry_run=True)
    assert result["moved"] is True
    assert os.path.exists(os.path.join(memory_dir, "m_a.md"))  # untouched
    assert not os.path.exists(os.path.join(memory_dir, "archive", "m_a.md"))


def test_archive_memory_never_raises_on_bogus_dirs():
    result = A.archive_memory("m_a", "/no/such/memory", "/no/such/repo")
    assert result["moved"] is False
    assert result["error"] is not None


def test_archive_memory_falls_back_to_rename_for_untracked_file(repo, memory_dir):
    """git mv refuses an untracked file -- exactly what write_memory just created and never
    git-add-ed. archive_memory must still succeed via a plain os.rename fallback."""
    git_commit(repo, "init", 1_700_000_000)  # repo has a HEAD, but m_a.md is never added
    write_file(memory_dir, "m_a.md", _mem("m_a", [], None))

    result = A.archive_memory("m_a", memory_dir, repo)
    assert result == {"name": "m_a", "moved": True, "error": None}

    archived_path = os.path.join(memory_dir, "archive", "m_a.md")
    assert os.path.exists(archived_path)
    assert not os.path.exists(os.path.join(memory_dir, "m_a.md"))
    with open(archived_path, encoding="utf-8") as fh:
        assert "m_a description" in fh.read()


def test_archive_memory_journals_the_untracked_fallback_move(repo, memory_dir):
    git_commit(repo, "init", 1_700_000_000)
    write_file(memory_dir, "m_a.md", _mem("m_a", [], None))

    A.archive_memory("m_a", memory_dir, repo)

    journal = os.path.join(memory_dir, "archive", ".archive_journal.jsonl")
    assert os.path.exists(journal)
    with open(journal, encoding="utf-8") as fh:
        lines = [json.loads(line) for line in fh if line.strip()]
    assert any(entry["name"] == "m_a.md" and entry["method"] == "os.rename" for entry in lines)


def test_archive_memory_refreshes_index_so_same_session_recall_drops_it(repo, memory_dir, monkeypatch):
    """Within the SAME process (no new SessionStart), recall() must stop surfacing an
    archived memory immediately -- this requires archive_memory's refresh_index() call to
    have actually run, not just the next SessionStart's index rebuild."""
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    from memory import build_index as B
    from memory import recall as R

    index_dir = os.path.join(repo, ".claude", ".memory-index")
    write_file(
        memory_dir,
        "m_archivable.md",
        '---\nname: m_archivable\ndescription: "unique zzyzx marker memory"\ntype: project\n---\nbody about zzyzx marker\n',
    )
    git_commit(repo, "add memory", 1_700_000_000)
    B.build_index(memory_dir, index_dir)

    before = R.recall("zzyzx marker", k=5, memory_dir=memory_dir, index_dir=index_dir)
    assert any(r["name"] == "m_archivable" for r in before)

    result = A.archive_memory("m_archivable", memory_dir, repo)
    assert result["moved"] is True

    after = R.recall("zzyzx marker", k=5, memory_dir=memory_dir, index_dir=index_dir)
    assert not any(r["name"] == "m_archivable" for r in after)


def test_archive_memory_is_single_item_only_no_bulk_path():
    """No batch/list/--all parameter -- a bulk sweep would require a separately-approved
    function, never this one (mirrors reverify_head_only_no_bulk)."""
    sig = inspect.signature(A.archive_memory)
    params = list(sig.parameters)
    assert params[0] == "name"
    assert "names" not in params and "bulk" not in params and "all" not in params


def test_no_bulk_archive_symbol_exists():
    assert not hasattr(A, "archive_corpus")
    assert not hasattr(A, "archive_all")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_main_report_mode_prints_no_candidates(memory_dir, repo, capsys):
    rc = A.main(["--memory-dir", memory_dir, "--repo-root", repo])
    assert rc == 0
    assert "No archive candidates" in capsys.readouterr().out


def test_main_archive_flag_moves_and_reports(repo, memory_dir, capsys):
    write_file(memory_dir, "m_a.md", _mem("m_a", [], None))
    git_commit(repo, "add memory", 1_700_000_000)
    rc = A.main(["--archive", "m_a", "--memory-dir", memory_dir, "--repo-root", repo])
    assert rc == 0
    assert "moved into .claude/memory/archive/" in capsys.readouterr().out
    assert os.path.exists(os.path.join(memory_dir, "archive", "m_a.md"))
