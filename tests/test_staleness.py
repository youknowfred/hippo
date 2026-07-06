"""Tests for memory/staleness.py — the git-drift signal."""

from __future__ import annotations

import os

from memory.staleness import (
    count_unresolvable_baselines,
    find_stale,
    find_unparseable,
    read_provenance,
    read_source_commit_time,
    set_invalid_after,
)

from .conftest import git_commit, write_file

# Wide window so the pinned-epoch fixtures are always in range.
_ALL = "2000-01-01"


def _memory(cited, source_commit):
    cp = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    sc = f'"{source_commit}"' if source_commit is not None else "null"
    return f"---\nname: A\ntype: project\ncited_paths: {cp}\nsource_commit: {sc}\n---\nbody\n"


def _memory_with_time(cited, source_commit, source_commit_time):
    cp = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    sc = f'"{source_commit}"' if source_commit is not None else "null"
    sct = str(int(source_commit_time)) if source_commit_time is not None else "null"
    return (
        f"---\nname: A\ntype: project\ncited_paths: {cp}\nsource_commit: {sc}\n"
        f"source_commit_time: {sct}\n---\nbody\n"
    )


def test_read_provenance_top_level_and_metadata_block():
    top = "---\ncited_paths: [\"src/a.py\"]\nsource_commit: \"abc\"\n---\nb\n"
    nested = (
        "---\nmetadata:\n  cited_paths: [\"src/a.py\"]\n  source_commit: \"abc\"\n---\nb\n"
    )
    assert read_provenance(top) == (["src/a.py"], "abc")
    assert read_provenance(nested) == (["src/a.py"], "abc")


def test_flags_memory_when_cited_code_changed_after_baseline(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_alpha.md", _memory(["src/foo.py"], c1))
    write_file(repo, "src/foo.py", "x = 2\n")  # cited file drifts
    git_commit(repo, "c2", 1_700_000_100)

    stale = find_stale(memory_dir, repo, since=_ALL)
    hit = [s for s in stale if s["name"] == "m_alpha"]
    assert hit and "src/foo.py" in hit[0]["changed_paths"]


# --------------------------------------------------------------------------- #
# SHP-1 — staleness must fire identically for a monorepo-subdir-rooted corpus.
#
# ``git log --name-only`` always emits toplevel-relative paths, -C notwithstanding. Before
# the fix, ``build_repo_file_index`` called ``git ls-files`` (no ``--full-name``), which is
# CWD-relative to repo_root — so when repo_root is a subdir (the CLAUDE_PROJECT_DIR shape
# for a package inside a monorepo), cited_paths ended up subdir-relative while
# ``_path_change_times`` keys stayed toplevel-relative: find_stale's ``path_times.get(p)``
# could never match. These two tests run the SAME drift scenario once rooted at the repo
# toplevel and once rooted at a subdirectory, and assert both flag identically.
# --------------------------------------------------------------------------- #
def test_flags_drift_identically_toplevel_vs_subdir_rooted_corpus(repo):
    from memory import provenance as P

    # --- control: corpus rooted at the toplevel ---------------------------------
    write_file(repo, "packages/web/src/foo.py", "x = 1\n")
    top_memory_dir = os.path.join(repo, ".claude", "memory")
    os.makedirs(top_memory_dir, exist_ok=True)
    write_file(
        top_memory_dir,
        "m_top.md",
        "---\nname: top\ntype: project\noriginSessionId: s1\n---\n"
        "Cites packages/web/src/foo.py:1.\n",
    )
    git_commit(repo, "init", 1_700_000_000)
    P.backfill_corpus(top_memory_dir, repo)

    write_file(repo, "packages/web/src/foo.py", "x = 2\n")  # cited file drifts
    git_commit(repo, "drift", 1_700_000_100)

    top_stale = find_stale(top_memory_dir, repo, since=_ALL)
    top_hit = [s for s in top_stale if s["name"] == "m_top"]
    assert top_hit and "packages/web/src/foo.py" in top_hit[0]["changed_paths"]

    # --- key case: corpus rooted at a monorepo SUBDIR (CLAUDE_PROJECT_DIR=subdir) -----
    subdir_root = os.path.join(repo, "packages", "web")
    sub_memory_dir = os.path.join(subdir_root, ".claude", "memory")
    os.makedirs(sub_memory_dir, exist_ok=True)
    write_file(
        sub_memory_dir,
        "m_sub.md",
        "---\nname: sub\ntype: project\noriginSessionId: s1\n---\n"
        "Cites src/foo.py:1.\n",
    )
    git_commit(repo, "add sub memory", 1_700_000_200)
    # backfill + find_stale invoked with repo_root = the SUBDIR, reproducing the exact
    # monorepo CLAUDE_PROJECT_DIR=subdir scenario.
    P.backfill_corpus(sub_memory_dir, subdir_root)

    write_file(repo, "packages/web/src/foo.py", "x = 3\n")  # cited file drifts again
    git_commit(repo, "drift again", 1_700_000_300)

    sub_stale = find_stale(sub_memory_dir, subdir_root, since=_ALL)
    sub_hit = [s for s in sub_stale if s["name"] == "m_sub"]
    assert sub_hit and "packages/web/src/foo.py" in sub_hit[0]["changed_paths"]


def test_build_repo_file_index_is_toplevel_relative_from_a_subdir(repo):
    from memory import provenance as P

    write_file(repo, "packages/web/src/foo.py", "x = 1\n")
    git_commit(repo, "init", 1_700_000_000)

    subdir_root = os.path.join(repo, "packages", "web")
    repo_files, basename_index = P.build_repo_file_index(subdir_root)

    assert "packages/web/src/foo.py" in repo_files
    assert "src/foo.py" not in repo_files
    assert basename_index.get("foo.py") == ["packages/web/src/foo.py"]


def test_does_not_flag_when_cited_code_unchanged(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_alpha.md", _memory(["src/foo.py"], c1))
    write_file(repo, "src/other.py", "y = 1\n")  # a DIFFERENT, uncited file changes
    git_commit(repo, "c2", 1_700_000_100)

    stale = find_stale(memory_dir, repo, since=_ALL)
    assert all(s["name"] != "m_alpha" for s in stale)


def test_missing_source_commit_is_not_flagged(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_b.md", _memory(["src/foo.py"], None))  # no baseline
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)

    stale = find_stale(memory_dir, repo, since=_ALL)
    assert all(s["name"] != "m_b" for s in stale)


def test_unknown_baseline_commit_is_skipped_not_raised(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_c.md", _memory(["src/foo.py"], "0" * 40))  # sha not in history
    stale = find_stale(memory_dir, repo, since=_ALL)
    assert isinstance(stale, list) and all(s["name"] != "m_c" for s in stale)


# --------------------------------------------------------------------------- #
# SHP-3 — squash-merge / shallow-clone resilience: an unresolvable source_commit sha
# falls back to the memory's OWN stored source_commit_time instead of being silently
# skipped forever (which is what happened before this fix — see the test just above,
# preserved because a memory with NO fallback time at all must still be un-judgeable).
# --------------------------------------------------------------------------- #
def test_hermetic_squash_merge_drift_still_detected_via_time_fallback(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    git_commit(repo, "c1", 1_700_000_000)
    fabricated_sha = "a" * 40  # NEVER exists in this repo's history (simulates squash-merge)
    write_file(
        memory_dir,
        "m_squashed.md",
        _memory_with_time(["src/foo.py"], fabricated_sha, 1_700_000_050),
    )
    write_file(repo, "src/foo.py", "x = 2\n")  # cited file drifts AFTER the stored baseline time
    git_commit(repo, "c2", 1_700_000_100)

    stale = find_stale(memory_dir, repo, since=_ALL)
    hit = [s for s in stale if s["name"] == "m_squashed"]
    assert hit and "src/foo.py" in hit[0]["changed_paths"]


def test_squash_merge_fallback_not_flagged_when_drift_precedes_stored_time(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    git_commit(repo, "c1", 1_700_000_000)
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)  # drift happens BEFORE the stored baseline time
    fabricated_sha = "b" * 40
    write_file(
        memory_dir,
        "m_squashed2.md",
        _memory_with_time(["src/foo.py"], fabricated_sha, 1_700_000_200),
    )

    stale = find_stale(memory_dir, repo, since=_ALL)
    assert all(s["name"] != "m_squashed2" for s in stale)


def test_resolvable_sha_takes_priority_over_stored_time_fallback(repo, memory_dir):
    """When the sha DOES resolve, the git cross-check is used — the stored time is never
    consulted (it's purely a fallback for the unresolvable case)."""
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    # A wildly-wrong stored time that would (wrongly) flag every future commit if it were used.
    write_file(memory_dir, "m_real.md", _memory_with_time(["src/foo.py"], c1, 0))
    write_file(repo, "src/other.py", "y = 1\n")  # an UNCITED file changes
    git_commit(repo, "c2", 1_700_000_100)

    stale = find_stale(memory_dir, repo, since=_ALL)
    assert all(s["name"] != "m_real" for s in stale)  # resolvable sha correctly NOT flagged


def test_read_source_commit_time_top_level_and_metadata_block():
    top = "---\ncited_paths: []\nsource_commit: \"abc\"\nsource_commit_time: 1700000000\n---\nb\n"
    nested = (
        "---\nmetadata:\n  source_commit: \"abc\"\n  source_commit_time: 1700000000\n---\nb\n"
    )
    assert read_source_commit_time(top) == 1700000000
    assert read_source_commit_time(nested) == 1700000000


def test_read_source_commit_time_absent_returns_none():
    assert read_source_commit_time(_memory(["src/a.py"], "abc")) is None


# --------------------------------------------------------------------------- #
# count_unresolvable_baselines — the visible-degradation count (SessionStart + doctor)
# --------------------------------------------------------------------------- #
def test_count_unresolvable_baselines_counts_only_unresolvable_shas(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_ok.md", _memory(["src/foo.py"], c1))  # resolvable
    write_file(
        memory_dir,
        "m_squashed.md",
        _memory_with_time(["src/foo.py"], "c" * 40, 1_700_000_050),
    )  # unresolvable
    write_file(memory_dir, "m_no_baseline.md", _memory([], None))  # no source_commit at all

    assert count_unresolvable_baselines(memory_dir, repo) == 1


def test_count_unresolvable_baselines_zero_when_all_resolvable(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_ok.md", _memory(["src/foo.py"], c1))
    assert count_unresolvable_baselines(memory_dir, repo) == 0


def test_count_unresolvable_baselines_empty_corpus_is_zero(repo, memory_dir):
    assert count_unresolvable_baselines(memory_dir, repo) == 0


def test_malformed_frontmatter_never_raises(repo, memory_dir):
    git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_bad.md", "---\n: : : not : valid : yaml\n[[[\n---\nbody\n")
    stale = find_stale(memory_dir, repo, since=_ALL)
    assert isinstance(stale, list)


def test_stale_results_ranked_by_drift_recency(repo, memory_dir):
    write_file(repo, "src/old.py", "a = 1\n")
    write_file(repo, "src/new.py", "b = 1\n")
    c0 = git_commit(repo, "c0", 1_700_000_000)
    write_file(memory_dir, "m_old.md", _memory(["src/old.py"], c0))
    write_file(memory_dir, "m_new.md", _memory(["src/new.py"], c0))
    write_file(repo, "src/old.py", "a = 2\n")  # drifts earlier
    git_commit(repo, "c1", 1_700_000_100)
    write_file(repo, "src/new.py", "b = 2\n")  # drifts later
    git_commit(repo, "c2", 1_700_000_200)

    stale = find_stale(memory_dir, repo, since=_ALL)
    names = [s["name"] for s in stale]
    assert names.index("m_new") < names.index("m_old")  # most-recent drift surfaces first
    assert all("recency" in s for s in stale)


def test_find_stale_on_missing_dir_returns_empty(repo):
    assert find_stale("/no/such/memory/dir", repo, since=_ALL) == []


# --------------------------------------------------------------------------- #
# find_unparseable — the LOUD signal for malformed frontmatter
# --------------------------------------------------------------------------- #
def test_find_unparseable_flags_only_malformed_frontmatter(memory_dir):
    # The real failure mode: an unquoted value containing ': ' (here in `description:`)
    # breaks yaml.safe_load for the WHOLE frontmatter → silently untracked + re-baselined.
    write_file(
        memory_dir,
        "m_bad.md",
        "---\nname: B\ndescription: has a colon Also: boom in it\n"
        'cited_paths: ["src/a.py"]\nsource_commit: "abc"\n---\nbody\n',
    )
    write_file(memory_dir, "m_ok.md", _memory(["src/a.py"], "abc"))      # valid, code-tied
    write_file(memory_dir, "m_empty_prov.md", _memory([], None))          # valid, no citations
    write_file(memory_dir, "m_no_fm.md", "plain body, no frontmatter\n")  # no frontmatter block
    assert find_unparseable(memory_dir) == ["m_bad"]


def test_find_unparseable_quoting_the_value_fixes_it(memory_dir):
    write_file(
        memory_dir,
        "m_fixed.md",
        "---\nname: B\ndescription: 'has a colon Also: boom in it'\n"
        'cited_paths: ["src/a.py"]\nsource_commit: "abc"\n---\nbody\n',
    )
    assert find_unparseable(memory_dir) == []  # quoted → parses → tracked again


def test_find_unparseable_empty_on_missing_dir():
    assert find_unparseable("/no/such/memory/dir") == []


# --------------------------------------------------------------------------- #
# set_invalid_after — soft-invalidation primitive (additive, idempotent, never deletes)
# --------------------------------------------------------------------------- #
def test_set_invalid_after_is_additive_and_body_byte_identical(memory_dir):
    body_text = "this is the body\nwith multiple lines\n"
    content = _memory(["src/a.py"], "abc").replace("body\n", body_text)
    path = write_file(memory_dir, "m_top.md", content)
    r = set_invalid_after(path, "2026-01-01T00:00:00+00:00")
    assert r["changed"] is True
    assert r["error"] is None
    with open(path, encoding="utf-8") as fh:
        new_text = fh.read()
    assert new_text.split("---\n", 2)[-1] == body_text  # body untouched
    assert 'invalid_after: "2026-01-01T00:00:00+00:00"' in new_text
    # pre-existing keys survive
    assert "cited_paths:" in new_text and "source_commit:" in new_text


def test_set_invalid_after_nests_under_metadata_block(memory_dir):
    nested = '---\nname: m_nested\nmetadata:\n  cited_paths: ["src/a.py"]\n  source_commit: "abc"\n---\nbody\n'
    path = write_file(memory_dir, "m_nested.md", nested)
    r = set_invalid_after(path, "2026-01-01T00:00:00+00:00")
    assert r["changed"] is True
    with open(path, encoding="utf-8") as fh:
        new_text = fh.read()
    # invalid_after lands INSIDE the metadata: block (same indent as the sibling keys)
    lines = new_text.split("\n")
    meta_idx = lines.index("metadata:")
    block = lines[meta_idx + 1 :]
    block_end = next(i for i, ln in enumerate(block) if ln.strip() == "---")
    block = block[:block_end]
    assert any(ln.strip().startswith("invalid_after:") and ln.startswith("  ") for ln in block)


def test_set_invalid_after_is_idempotent_on_same_ts(memory_dir):
    path = write_file(memory_dir, "m_a.md", _memory(["src/a.py"], "abc"))
    r1 = set_invalid_after(path, "2026-01-01T00:00:00+00:00")
    assert r1["changed"] is True
    r2 = set_invalid_after(path, "2026-01-01T00:00:00+00:00")
    assert r2["changed"] is False  # byte-identical text -> no-op


def test_set_invalid_after_refreshes_on_different_ts(memory_dir):
    path = write_file(memory_dir, "m_a.md", _memory(["src/a.py"], "abc"))
    set_invalid_after(path, "2026-01-01T00:00:00+00:00")
    r2 = set_invalid_after(path, "2026-02-01T00:00:00+00:00")
    assert r2["changed"] is True
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert text.count("invalid_after:") == 1  # old value replaced, not duplicated
    assert "2026-02-01" in text and "2026-01-01" not in text


def test_set_invalid_after_defaults_ts_to_now(memory_dir):
    path = write_file(memory_dir, "m_a.md", _memory(["src/a.py"], "abc"))
    r = set_invalid_after(path)  # no ts -> defaults to now (UTC)
    assert r["changed"] is True
    assert r["invalid_after"] is not None
    import datetime

    datetime.datetime.fromisoformat(r["invalid_after"])  # parses as a valid ISO timestamp


def test_set_invalid_after_refuses_unparseable_frontmatter(memory_dir):
    bad = "---\nname: m_bad\ndescription: has an unquoted colon: right here\n---\nbody\n"
    path = write_file(memory_dir, "m_bad.md", bad)
    r = set_invalid_after(path, "2026-01-01T00:00:00+00:00")
    assert r["changed"] is False
    assert r["error"] is not None
    with open(path, encoding="utf-8") as fh:
        assert fh.read() == bad  # untouched


def test_set_invalid_after_refuses_no_frontmatter(memory_dir):
    path = write_file(memory_dir, "m_plain.md", "just a plain body, no frontmatter\n")
    r = set_invalid_after(path, "2026-01-01T00:00:00+00:00")
    assert r["changed"] is False
    assert r["error"] is not None


def test_set_invalid_after_never_raises_on_missing_file(memory_dir):
    r = set_invalid_after(os.path.join(memory_dir, "does_not_exist.md"), "2026-01-01T00:00:00+00:00")
    assert r["changed"] is False
    assert r["error"] is not None


def test_find_stale_read_logic_unaffected_by_invalid_after(repo, memory_dir):
    """NO change to find_stale's read logic -- invalid_after is invisible to staleness
    detection (a separate concern: code-drift vs content-validity)."""
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    path = write_file(memory_dir, "m_a.md", _memory(["src/foo.py"], c1))
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)

    before = find_stale(memory_dir, repo, since=_ALL)
    set_invalid_after(path, "2026-01-01T00:00:00+00:00")
    after = find_stale(memory_dir, repo, since=_ALL)
    assert before == after  # identical -- find_stale doesn't even look at invalid_after


# --------------------------------------------------------------------------- #
# CLI --invalidate
# --------------------------------------------------------------------------- #
def test_main_invalidate_flag_writes_and_reports(memory_dir, capsys):
    import memory.staleness as S

    write_file(memory_dir, "m_a.md", _memory(["src/a.py"], "abc"))
    rc = S.main(["--invalidate", "m_a", "--memory-dir", memory_dir])
    assert rc == 0
    out = capsys.readouterr().out
    assert "validity window closed" in out
    with open(os.path.join(memory_dir, "m_a.md"), encoding="utf-8") as fh:
        assert "invalid_after:" in fh.read()


def test_main_invalidate_flag_accepts_name_without_md_suffix(memory_dir, capsys):
    import memory.staleness as S

    write_file(memory_dir, "m_b.md", _memory(["src/a.py"], "abc"))
    rc = S.main(["--invalidate", "m_b.md", "--memory-dir", memory_dir])
    assert rc == 0
    assert "validity window closed" in capsys.readouterr().out
