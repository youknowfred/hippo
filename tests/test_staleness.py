"""Tests for memory/staleness.py — the git-drift signal."""

from __future__ import annotations

import os

from memory.staleness import find_stale, find_unparseable, read_provenance, set_invalid_after

from .conftest import git_commit, write_file

# Wide window so the pinned-epoch fixtures are always in range.
_ALL = "2000-01-01"


def _memory(cited, source_commit):
    cp = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    sc = f'"{source_commit}"' if source_commit is not None else "null"
    return f"---\nname: A\ntype: project\ncited_paths: {cp}\nsource_commit: {sc}\n---\nbody\n"


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
