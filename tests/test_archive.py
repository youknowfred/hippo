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

# Event timestamps land AFTER every fixture commit time (1_700_000_0xx) so the LIF-4
# youth gate sees the committed memories as exposed to every seeded session; a test that
# wants a memory YOUNGER than the events commits it at an epoch above this base.
_EVENT_TS_BASE = 1_700_000_200

# LIF-4: enough distinct sessions to satisfy soak.SOAK_GATE_SESSIONS — seeded by tests
# whose subject is the 4-way intersection itself, not the soak gate.
_SOAKED = [("s1", []), ("s2", []), ("s3", []), ("s4", []), ("s5", [])]


def _mem(name, cited, source_commit, body="body"):
    cp = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    sc = f'"{source_commit}"' if source_commit is not None else "null"
    return f'---\nname: {name}\ndescription: "{name} description"\ncited_paths: {cp}\nsource_commit: {sc}\n---\n{body}\n'


def _seed_events(td, session_names, base_ts=_EVENT_TS_BASE):
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "recall_events.jsonl"), "w", encoding="utf-8") as fh:
        for i, (sid, names) in enumerate(session_names):
            fh.write(
                json.dumps(
                    {"ts": base_ts + i, "session_id": sid, "names": names, "backend": "bm25"}
                )
                + "\n"
            )


def _seed_aggregates(td, count, first_ts, memories=None):
    """Write a usage_aggregates.json as telemetry's writer would (LIF-4 fixtures)."""
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "usage_aggregates.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "version": 1,
                "sessions": {"count": count, "first_ts": first_ts, "last_session_id": None},
                "memories": memories or {},
            },
            fh,
        )


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
    # 5 distinct sessions (soak gate met); only m_recalled was ever recalled
    _seed_events(td, [("s1", ["m_recalled"]), ("s2", []), ("s3", []), ("s4", []), ("s5", [])])

    candidates = A.archive_candidates(memory_dir, repo, telemetry_dir=td, since=_ALL)
    assert [c["name"] for c in candidates] == ["m_archivable"]


def test_archive_candidates_empty_when_nothing_satisfies_all_four(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_a.md", _mem("m_a", ["src/foo.py"], c1))
    git_commit(repo, "c2", 1_700_000_050)  # nothing cited drifts -> m_a never goes stale

    td = os.path.join(repo, "tele")
    _seed_events(td, _SOAKED)  # gate met, so the 4-way logic (not the gate) decides
    assert A.archive_candidates(memory_dir, repo, telemetry_dir=td, since=_ALL) == []


def test_archive_candidates_never_raises_on_bogus_dirs():
    assert A.archive_candidates("/no/such/memory", "/no/such/repo") == []


def test_archive_candidates_empty_corpus_returns_empty(repo, memory_dir):
    assert A.archive_candidates(memory_dir, repo) == []


# --------------------------------------------------------------------------- #
# LIF-4: the soak gate — a pre-soak report withholds itself with a stated reason
# --------------------------------------------------------------------------- #
def _would_be_candidate_corpus(repo, memory_dir):
    """One memory satisfying all four intersection conditions (stale ∧ cold ∧
    zero-inbound ∧ uncited) — a maximally-permissive pre-LIF-4 report would list it.
    Returns the memory's source_commit (pre-drift, reusable for further stale fixtures)."""
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_archivable.md", _mem("m_archivable", ["src/foo.py"], c1))
    git_commit(repo, "c2", 1_700_000_010)
    write_file(repo, "src/foo.py", "x = 2\n")  # drift -> stale
    git_commit(repo, "c3", 1_700_000_020)
    return c1


def test_archive_candidates_pre_soak_returns_empty_with_stated_reason(repo, memory_dir):
    _would_be_candidate_corpus(repo, memory_dir)
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", [])])  # 1 distinct session -- far below the >=5 bar

    diagnostics: dict = {}
    candidates = A.archive_candidates(
        memory_dir, repo, telemetry_dir=td, since=_ALL, diagnostics=diagnostics
    )
    assert candidates == []
    assert diagnostics["reason"] == "soak_gate_unmet"  # machine-readable, not a silent []
    assert diagnostics["soak_gate"] == {
        "gate_met": False,
        "distinct_sessions": 1,
        "gate_threshold": 5,
    }


def test_archive_candidates_gate_met_reports_gate_in_diagnostics_without_reason(repo, memory_dir):
    _would_be_candidate_corpus(repo, memory_dir)
    td = os.path.join(repo, "tele")
    _seed_events(td, _SOAKED)

    diagnostics: dict = {}
    candidates = A.archive_candidates(
        memory_dir, repo, telemetry_dir=td, since=_ALL, diagnostics=diagnostics
    )
    assert [c["name"] for c in candidates] == ["m_archivable"]
    assert diagnostics["soak_gate"]["gate_met"] is True
    assert "reason" not in diagnostics


def test_main_pre_soak_report_explains_itself_instead_of_listing_corpus(repo, memory_dir, capsys):
    """AC (LIF-4): the pre-soak archive report explains itself instead of listing the
    whole corpus — the would-be candidate's name must NOT appear anywhere in the output."""
    _would_be_candidate_corpus(repo, memory_dir)
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", []), ("s2", [])])

    rc = A.main(["--memory-dir", memory_dir, "--repo-root", repo, "--telemetry-dir", td])
    assert rc == 0
    out = capsys.readouterr().out
    assert "soak gate unmet" in out
    assert "2/5 distinct sessions" in out
    assert "m_archivable" not in out  # nothing listed -- the report withheld itself


# --------------------------------------------------------------------------- #
# LIF-4: the youth gate — memories younger than the soak window are not candidates
# --------------------------------------------------------------------------- #
def test_archive_candidates_excludes_memory_younger_than_soak_window(repo, memory_dir, capsys):
    # Now-relative epochs (not the usual 1_700_000_000 pins) so the CLI leg below — which
    # runs find_stale's DEFAULT wall-clock "2 years ago" window — sees the same drift.
    import time

    t0 = int(time.time()) - 100_000
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", t0)
    write_file(memory_dir, "m_old.md", _mem("m_old", ["src/foo.py"], c1))
    git_commit(repo, "c2", t0 + 10)
    write_file(repo, "src/foo.py", "x = 2\n")  # drift after c1/c2 -> both memories stale
    git_commit(repo, "c3", t0 + 20)

    td = os.path.join(repo, "tele")
    _seed_events(td, _SOAKED, base_ts=t0 + 50)  # 5 sessions, all after m_old's first-seen

    # m_young first ADDED after every seeded session -> exposed to 0 distinct sessions;
    # its coldness is indistinguishable from youth, so it must not be a candidate.
    write_file(memory_dir, "m_young.md", _mem("m_young", ["src/foo.py"], c1))
    git_commit(repo, "c4", t0 + 80_000)

    diagnostics: dict = {}
    candidates = A.archive_candidates(memory_dir, repo, telemetry_dir=td, diagnostics=diagnostics)
    assert [c["name"] for c in candidates] == ["m_old"]
    assert diagnostics["excluded_young"] == ["m_young"]

    # The CLI surfaces the exclusion (legible degradation), still listing m_old.
    rc = A.main(["--memory-dir", memory_dir, "--repo-root", repo, "--telemetry-dir", td])
    assert rc == 0
    out = capsys.readouterr().out
    assert "younger than the soak window" in out
    assert "m_young" in out
    assert "m_old" in out


def test_archive_candidates_untracked_memory_first_seen_unknown_is_young(repo, memory_dir):
    """A never-committed memory has NO git first-seen -- it must fail toward exclusion
    (it literally is brand new), never toward candidacy."""
    c1 = _would_be_candidate_corpus(repo, memory_dir)
    td = os.path.join(repo, "tele")
    _seed_events(td, _SOAKED)
    # Written to disk but never git-add-ed: find_stale still judges it stale via its
    # frontmatter (source_commit c1 predates the drift), so ONLY the youth gate excludes it.
    write_file(memory_dir, "m_untracked.md", _mem("m_untracked", ["src/foo.py"], c1))

    diagnostics: dict = {}
    candidates = A.archive_candidates(
        memory_dir, repo, telemetry_dir=td, since=_ALL, diagnostics=diagnostics
    )
    assert [c["name"] for c in candidates] == ["m_archivable"]
    assert "m_untracked" in diagnostics.get("excluded_young", [])


def test_archive_candidates_survive_full_ledger_rotation_via_aggregates(repo, memory_dir):
    """LIF-4 end-to-end: with the recall ledger entirely rotated/lost, the aggregates
    alone meet the soak gate AND credit rotated-away sessions to a memory that predates
    the whole observation span — the oldest evidence is not lost with the ledger."""
    _would_be_candidate_corpus(repo, memory_dir)  # committed at 1_700_000_0xx
    td = os.path.join(repo, "tele")
    # no recall_events.jsonl at all; 6 distinct sessions survive only in the aggregates,
    # whose observation began AFTER the memory was first seen
    _seed_aggregates(td, count=6, first_ts=1_700_000_100)

    diagnostics: dict = {}
    candidates = A.archive_candidates(
        memory_dir, repo, telemetry_dir=td, since=_ALL, diagnostics=diagnostics
    )
    assert diagnostics["soak_gate"] == {
        "gate_met": True,
        "distinct_sessions": 6,
        "gate_threshold": 5,
    }
    assert [c["name"] for c in candidates] == ["m_archivable"]
    assert "excluded_young" not in diagnostics


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
    _seed_events(td, _SOAKED)  # soak gate met, so the citation gate is what excludes m_a
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
    assert result == {"name": "m_a", "moved": True, "refused": False, "referrers": [], "error": None}

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
    assert result == {"name": "m_a", "moved": True, "refused": False, "referrers": [], "error": None}

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
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
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
# GRA-5: archive refuses (without force) while inbound links exist
# --------------------------------------------------------------------------- #
def _typed_mem(name, fm_extra, body="body"):
    """Memory with extra frontmatter lines (GRA-4 typed relations) — mirrors test_links.py."""
    return f'---\nname: {name}\ndescription: "{name} description"\n{fm_extra}---\n{body}\n'


def _linked_pair(repo, memory_dir):
    """m_source carries a [[m_target]] wikilink — the minimal referenced-memory corpus."""
    write_file(memory_dir, "m_target.md", _mem("m_target", [], None))
    write_file(memory_dir, "m_source.md", _mem("m_source", [], None, body="see [[m_target]]"))
    git_commit(repo, "add memories", 1_700_000_000)


def test_archive_memory_refuses_with_referrer_list_when_inbound_links_exist(repo, memory_dir):
    """AC (GRA-5): referenced-memory archive without force fails with the referrer list
    and leaves the corpus byte-for-byte untouched (no move, no archive/ dir created)."""
    _linked_pair(repo, memory_dir)

    result = A.archive_memory("m_target", memory_dir, repo)
    assert result["moved"] is False
    assert result["refused"] is True
    assert result["referrers"] == ["m_source"]  # machine-readable, not just prose
    assert "m_source" in result["error"]
    assert "supersedes" in result["error"]  # the GRA-4 forwarding-pointer pattern is named
    assert os.path.exists(os.path.join(memory_dir, "m_target.md"))  # untouched
    assert not os.path.isdir(os.path.join(memory_dir, "archive"))  # nothing created either


def test_archive_memory_force_moves_and_reports_referrers(repo, memory_dir):
    """AC (GRA-5): with force=True the move happens and the referrer list rides along in
    the result so the calling agent can rewrite those links in the same commit."""
    _linked_pair(repo, memory_dir)

    result = A.archive_memory("m_target", memory_dir, repo, force=True)
    assert result["moved"] is True
    assert result["refused"] is False
    assert result["referrers"] == ["m_source"]
    assert result["error"] is None
    assert os.path.exists(os.path.join(memory_dir, "archive", "m_target.md"))
    assert not os.path.exists(os.path.join(memory_dir, "m_target.md"))


def test_archive_memory_zero_inbound_moves_without_force_even_with_outbound_links(repo, memory_dir):
    """AC (GRA-5): zero-INBOUND archive behavior is unchanged — inbound degree gates,
    outbound never does (m_source links out to m_target but nothing links to m_source)."""
    _linked_pair(repo, memory_dir)

    result = A.archive_memory("m_source", memory_dir, repo)
    assert result == {
        "name": "m_source", "moved": True, "refused": False, "referrers": [], "error": None,
    }
    assert os.path.exists(os.path.join(memory_dir, "archive", "m_source.md"))


def test_archive_memory_counts_typed_inbound_edges_as_referrers(repo, memory_dir):
    """A memory referenced by a GRA-4 typed relation is just as referenced as a wikilinked
    one — a `supersedes:` edge on a successor must gate the archive of its target too."""
    write_file(memory_dir, "m_old.md", _mem("m_old", [], None))
    write_file(memory_dir, "m_new.md", _typed_mem("m_new", "supersedes: [m_old]\n"))
    git_commit(repo, "add memories", 1_700_000_000)

    result = A.archive_memory("m_old", memory_dir, repo)
    assert result["moved"] is False and result["refused"] is True
    assert result["referrers"] == ["m_new"]
    assert os.path.exists(os.path.join(memory_dir, "m_old.md"))


def test_archive_memory_referrers_union_wikilinks_and_typed_edges(repo, memory_dir):
    """The referrer set is the UNION of untyped wikilinks and typed inbound edges (here a
    metadata:-nested contradicts, the GRA-4 read convention) — sorted, each source once."""
    write_file(memory_dir, "m_old.md", _mem("m_old", [], None))
    write_file(memory_dir, "m_linker.md", _mem("m_linker", [], None, body="see [[m_old]]"))
    write_file(memory_dir, "m_rival.md", _typed_mem("m_rival", "metadata:\n  contradicts: [m_old]\n"))
    git_commit(repo, "add memories", 1_700_000_000)

    result = A.archive_memory("m_old", memory_dir, repo)
    assert result["refused"] is True
    assert result["referrers"] == ["m_linker", "m_rival"]


def test_archive_memory_dry_run_reports_refusal_without_touching_anything(repo, memory_dir):
    """The guard runs BEFORE the dry-run preview: a dry run of a referenced memory reports
    the refusal it would really hit — never a false would-move."""
    _linked_pair(repo, memory_dir)

    result = A.archive_memory("m_target", memory_dir, repo, dry_run=True)
    assert result["moved"] is False and result["refused"] is True
    assert result["referrers"] == ["m_source"]
    assert os.path.exists(os.path.join(memory_dir, "m_target.md"))


def test_archive_memory_dry_run_with_force_previews_move_without_moving(repo, memory_dir):
    _linked_pair(repo, memory_dir)

    result = A.archive_memory("m_target", memory_dir, repo, dry_run=True, force=True)
    assert result["moved"] is True and result["refused"] is False
    assert result["referrers"] == ["m_source"]  # reported even in preview
    assert os.path.exists(os.path.join(memory_dir, "m_target.md"))  # preview only
    assert not os.path.isdir(os.path.join(memory_dir, "archive"))


def test_archive_memory_fails_closed_when_graph_unbuildable(repo, memory_dir, monkeypatch):
    """An unverifiable inbound set must refuse (fail toward has-inbound, the same direction
    _zero_inbound_names documents), never silently archive; force stays the escape hatch."""
    write_file(memory_dir, "m_a.md", _mem("m_a", [], None))
    git_commit(repo, "add memory", 1_700_000_000)
    monkeypatch.setattr(A, "build_graph", lambda md: None)

    refused = A.archive_memory("m_a", memory_dir, repo)
    assert refused["refused"] is True and refused["moved"] is False
    assert os.path.exists(os.path.join(memory_dir, "m_a.md"))

    forced = A.archive_memory("m_a", memory_dir, repo, force=True)
    assert forced["moved"] is True
    assert forced["referrers"] == []  # unknown — the graph never built


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


def test_main_archive_without_force_refuses_and_prints_referrers(repo, memory_dir, capsys):
    """AC (GRA-5), CLI leg: no --force -> refusal names every referrer and the supersedes
    forwarding-pointer pattern; the file stays put; exit code stays 0 (report, not crash)."""
    _linked_pair(repo, memory_dir)

    rc = A.main(["--archive", "m_target", "--memory-dir", memory_dir, "--repo-root", repo])
    assert rc == 0
    out = capsys.readouterr().out
    assert "refused" in out
    assert "m_source" in out
    assert "supersedes" in out
    assert os.path.exists(os.path.join(memory_dir, "m_target.md"))
    assert not os.path.exists(os.path.join(memory_dir, "archive", "m_target.md"))


def test_main_archive_force_flag_moves_and_prints_referrers(repo, memory_dir, capsys):
    """AC (GRA-5), CLI leg: --force moves AND still reports the referrers so the agent can
    rewrite those links in the same commit."""
    _linked_pair(repo, memory_dir)

    rc = A.main(
        ["--archive", "m_target", "--force", "--memory-dir", memory_dir, "--repo-root", repo]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "moved into .claude/memory/archive/" in out
    assert "m_source" in out
    assert "same commit" in out
    assert "supersedes" in out
    assert os.path.exists(os.path.join(memory_dir, "archive", "m_target.md"))
