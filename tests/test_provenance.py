"""Tests for memory/provenance.py — citation extraction + body-preserving backfill."""

from __future__ import annotations

import json
import os

from memory import provenance as P
from memory.staleness import read_last_verified, read_provenance, read_source_commit_time

from .conftest import git_commit, write_file


# --------------------------------------------------------------------------- #
# extraction + resolution (pure)
# --------------------------------------------------------------------------- #
def test_extract_citations_handles_all_forms_and_dedupes():
    body = (
        "See src/utils/foo.py:12 and bar.py:5-9 and `qux.py:3` in passing.\n"
        "A markdown link [scorer](src/q/scorer.py) too.\n"
        "Prose mentions readme.md which must NOT match (.md excluded).\n"
        "Repeat src/utils/foo.py:99 to prove dedupe.\n"
    )
    toks = P.extract_citations(body)
    assert "src/utils/foo.py" in toks
    assert "bar.py" in toks
    assert "qux.py" in toks
    assert "src/q/scorer.py" in toks
    assert "readme.md" not in toks  # .md is intentionally excluded
    assert toks.count("src/utils/foo.py") == 1  # line numbers stripped + de-duped


def test_resolve_citations_only_keeps_pinned_files():
    repo_files = {"src/utils/foo.py", "src/a/bar.py", "src/b/bar.py", "src/q/scorer.py"}
    index = {
        "foo.py": ["src/utils/foo.py"],
        "bar.py": ["src/a/bar.py", "src/b/bar.py"],  # ambiguous basename
        "scorer.py": ["src/q/scorer.py"],
    }
    out = P.resolve_citations(
        ["src/utils/foo.py", "bar.py", "nope.py", "scorer.py"], repo_files, index
    )
    assert "src/utils/foo.py" in out  # exact repo path kept
    assert "src/q/scorer.py" in out  # unique basename kept
    assert "src/a/bar.py" not in out and "src/b/bar.py" not in out  # AMBIGUOUS bare basename DROPPED
    assert all("nope" not in p for p in out)  # unresolvable dropped


def test_refresh_re_derives_cited_paths_and_preserves_baseline(repo, memory_dir):
    write_file(repo, "src/a/dup.py", "x = 1\n")
    write_file(repo, "src/b/dup.py", "y = 1\n")  # makes 'dup.py' ambiguous
    write_file(repo, "src/u/uniq.py", "z = 1\n")
    git_commit(repo, "init", 1_700_000_000)
    # A memory carrying an INFLATED cited_paths (old resolver) + an existing baseline.
    write_file(
        repo,
        ".claude/memory/m.md",
        '---\nname: M\ncited_paths: ["src/a/dup.py", "src/b/dup.py", "src/u/uniq.py"]\n'
        'source_commit: "BASELINE123"\n---\nbody refs dup.py and src/u/uniq.py\n',
    )
    before = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()
    _, body_before = P.split_frontmatter(before)

    P.backfill_corpus(memory_dir, repo, refresh=True)

    after = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()
    _, body_after = P.split_frontmatter(after)
    cited, sc = read_provenance(after)
    assert sc == "BASELINE123"  # staleness baseline PRESERVED across refresh
    assert "src/u/uniq.py" in cited  # unique kept
    assert not any("dup.py" in c for c in cited)  # ambiguous re-derived OUT
    assert body_after == body_before  # body still byte-identical


def test_refresh_refuses_unparseable_frontmatter_and_does_not_rebaseline(repo, memory_dir):
    write_file(repo, "src/a.py", "x = 1\n")
    git_commit(repo, "init", 1_700_000_000)
    # Frontmatter carries a cited_paths line + a real baseline, but `description:` does NOT
    # yaml-parse (unquoted ': '). Refresh must NOT fall through to git_last_commit and
    # silently re-baseline — it must refuse and leave the broken file byte-identical.
    bad = (
        "---\nname: B\ndescription: oops Also: a colon\n"
        'cited_paths: ["src/a.py"]\nsource_commit: "BASE_KEEP"\n---\nbody\n'
    )
    write_file(repo, ".claude/memory/m.md", bad)
    before = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()

    results = P.backfill_corpus(memory_dir, repo, refresh=True)

    after = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()
    assert after == before  # UNTOUCHED — no silent rewrite / re-baseline
    r = next(x for x in results if x["path"].endswith("m.md"))
    assert r["changed"] is False
    assert r["error"] and "unparseable" in r["error"]


# --------------------------------------------------------------------------- #
# backfill_text (pure, body-preserving, idempotent, both schemas)
# --------------------------------------------------------------------------- #
def test_backfill_metadata_block_inserts_under_metadata_and_preserves_body():
    text = (
        "---\n"
        'name: ""\n'
        "metadata: \n"
        "  node_type: memory\n"
        "  originSessionId: abc123\n"
        "---\n"
        "BODY LINE 1\nBODY LINE 2\n"
    )
    new, changed = P.backfill_text(text, ["src/foo.py"], "deadbeef")
    assert changed
    _, body_before = P.split_frontmatter(text)
    _, body_after = P.split_frontmatter(new)
    assert body_after == body_before  # body byte-identical
    assert '  cited_paths: ["src/foo.py"]' in new  # nested under metadata (2-space indent)
    assert '  source_commit: "deadbeef"' in new
    cited, sc = read_provenance(new)
    assert cited == ["src/foo.py"] and sc == "deadbeef"


def test_backfill_flat_frontmatter_inserts_top_level():
    text = "---\nname: X\ntype: project\noriginSessionId: z9\n---\nbody here\n"
    new, changed = P.backfill_text(text, [], "c1sha")
    assert changed
    assert "\ncited_paths: []" in new  # top-level (no indent)
    assert '\nsource_commit: "c1sha"' in new
    _, body_before = P.split_frontmatter(text)
    _, body_after = P.split_frontmatter(new)
    assert body_after == body_before


def test_backfill_is_idempotent():
    text = "---\nname: X\ntype: project\n---\nbody\n"
    new, changed = P.backfill_text(text, ["src/foo.py"], "c1")
    assert changed
    new2, changed2 = P.backfill_text(new, ["src/foo.py"], "c1")
    assert changed2 is False and new2 == new


def test_backfill_no_frontmatter_is_untouched():
    text = "no frontmatter at all\njust body\n"
    new, changed = P.backfill_text(text, ["a.py"], "c")
    assert changed is False and new == text


# --------------------------------------------------------------------------- #
# backfill_corpus (end-to-end against a tmp git repo)
# --------------------------------------------------------------------------- #
def test_backfill_corpus_sets_provenance_preserves_body_and_is_idempotent(repo, memory_dir):
    write_file(repo, "src/utils/foo.py", "x = 1\n")
    write_file(repo, "src/a/bar.py", "y = 1\n")
    write_file(
        memory_dir,
        "m_alpha.md",
        "---\nname: A\ntype: project\noriginSessionId: s1\n---\n"
        "This cites src/utils/foo.py:3 and bar.py:2 in the body.\n",
    )
    git_commit(repo, "init", 1_700_000_000)

    before = open(os.path.join(memory_dir, "m_alpha.md"), encoding="utf-8").read()
    _, body_before = P.split_frontmatter(before)

    results = P.backfill_corpus(memory_dir, repo)
    assert any(r["changed"] for r in results)

    after = open(os.path.join(memory_dir, "m_alpha.md"), encoding="utf-8").read()
    _, body_after = P.split_frontmatter(after)
    assert body_after == body_before  # body byte-identical

    cited, sc = read_provenance(after)
    assert "src/utils/foo.py" in cited and "src/a/bar.py" in cited
    assert isinstance(sc, str) and len(sc) == 40  # a real commit sha

    second = P.backfill_corpus(memory_dir, repo)
    assert all(r["changed"] is False for r in second)  # idempotent


# --------------------------------------------------------------------------- #
# SHP-3 — source_commit_time recorded ALONGSIDE source_commit at backfill/reverify,
# via ONE extra git-show call (or folded into the existing git_last_commit call).
# --------------------------------------------------------------------------- #
def test_git_last_commit_with_time_returns_sha_and_epoch(repo):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    sha, ct = P.git_last_commit_with_time("src/foo.py", repo)
    assert sha == c1 and ct == 1_700_000_000


def test_git_last_commit_with_time_none_on_no_history(repo):
    git_commit(repo, "init", 1_700_000_000)
    sha, ct = P.git_last_commit_with_time("src/never-existed.py", repo)
    assert sha is None and ct is None


def test_git_head_with_time_returns_head_and_epoch(repo):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    sha, ct = P.git_head_with_time(repo)
    assert sha == c1 and ct == 1_700_000_000


def test_git_head_with_time_none_when_no_commits(repo):
    sha, ct = P.git_head_with_time(repo)
    assert sha is None and ct is None


def test_backfill_corpus_writes_source_commit_time_alongside_sha(repo, memory_dir):
    write_file(repo, "src/utils/foo.py", "x = 1\n")
    write_file(
        memory_dir,
        "m_alpha.md",
        "---\nname: A\ntype: project\noriginSessionId: s1\n---\nCites src/utils/foo.py:3.\n",
    )
    c1 = git_commit(repo, "init", 1_700_000_000)

    results = P.backfill_corpus(memory_dir, repo)
    r = next(x for x in results if x["path"].endswith("m_alpha.md"))
    assert r["source_commit"] == c1
    assert r["source_commit_time"] == 1_700_000_000

    after = open(os.path.join(memory_dir, "m_alpha.md"), encoding="utf-8").read()
    assert read_source_commit_time(after) == 1_700_000_000


def test_refresh_preserves_source_commit_time_alongside_sha(repo, memory_dir):
    write_file(repo, "src/a/dup.py", "x = 1\n")
    write_file(repo, "src/b/dup.py", "y = 1\n")  # makes 'dup.py' ambiguous
    git_commit(repo, "init", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m.md",
        '---\nname: M\ncited_paths: ["src/a/dup.py", "src/b/dup.py"]\n'
        'source_commit: "BASELINE123"\nsource_commit_time: 1650000000\n---\nbody refs dup.py\n',
    )

    P.backfill_corpus(memory_dir, repo, refresh=True)

    after = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()
    _, sc = read_provenance(after)
    assert sc == "BASELINE123"  # baseline preserved
    assert read_source_commit_time(after) == 1650000000  # time preserved alongside it


def test_reverify_writes_source_commit_time_at_head(repo, memory_dir):
    write_file(repo, "src/dep.py", "v = 1\n")
    c1 = git_commit(repo, "dep v1", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m.md",
        f'---\nname: M\ncited_paths: ["src/dep.py"]\nsource_commit: "{c1}"\n---\nbody src/dep.py\n',
    )
    head = git_commit(repo, "memory", 1_700_000_100)

    repo_files, basename_index = P.build_repo_file_index(repo)
    res = P.reverify_file(os.path.join(memory_dir, "m.md"), repo, repo_files, basename_index)
    assert res["source_commit"] == head
    assert res["source_commit_time"] == 1_700_000_100
    after = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()
    assert read_source_commit_time(after) == 1_700_000_100


def test_backfill_corpus_skips_index_files(repo, memory_dir):
    write_file(memory_dir, "MEMORY.md", "---\nname: index\n---\nindex body cites src/x.py:1\n")
    write_file(memory_dir, "MEMORY.full.md", "---\nname: full\n---\nbody src/x.py:1\n")
    write_file(repo, "src/x.py", "z = 1\n")
    git_commit(repo, "init", 1_700_000_000)
    results = P.backfill_corpus(memory_dir, repo)
    touched = {os.path.basename(r["path"]) for r in results}
    assert "MEMORY.md" not in touched and "MEMORY.full.md" not in touched


# --------------------------------------------------------------------------- #
# reverify — re-baseline source_commit to HEAD after the content is re-verified
# against current code (clears a staleness flag; --refresh CANNOT). Per-memory +
# verification-gated; there is NO bulk re-baseline (blind bulk anchors to the
# mechanical backfill touch and silences real drift — see the README warning).
# --------------------------------------------------------------------------- #
from memory.staleness import find_stale  # noqa: E402

_ALL = "2000-01-01"  # wide window so the pinned-epoch fixtures are always in range


def _reverify_one(memory_dir, repo, name):
    repo_files, basename_index = P.build_repo_file_index(repo)
    target = os.path.join(memory_dir, f"{name}.md")
    return P.reverify_file(target, repo, repo_files, basename_index)


def test_reverify_rebaselines_to_head_and_clears_drift(repo, memory_dir):
    # A memory whose cited code drifted is stale; re-verifying it (re-baseline to HEAD = "I
    # confirmed this matches the code as of now") clears the flag. Baseline is HEAD, NOT the
    # file's own last touch (which would be the mechanical backfill commit).
    write_file(repo, "src/dep.py", "v = 1\n")
    c1 = git_commit(repo, "dep v1 + memory era", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m.md",
        f'---\nname: M\ncited_paths: ["src/dep.py"]\nsource_commit: "{c1}"\n---\nbody src/dep.py\n',
    )
    git_commit(repo, "memory", 1_700_000_001)
    write_file(repo, "src/dep.py", "v = 2\n")  # cited code drifts -> genuinely stale
    git_commit(repo, "dep v2", 1_700_000_500)

    assert any(s["name"] == "m" for s in find_stale(memory_dir, repo, since=_ALL))  # pre: stale

    before = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()
    _, body_before = P.split_frontmatter(before)
    res = _reverify_one(memory_dir, repo, "m")
    assert res["changed"] is True and res["error"] is None

    after = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()
    _, sc = read_provenance(after)
    head = P.run_git(["rev-parse", "HEAD"], repo).strip()
    assert sc == head  # re-baselined to HEAD, not the file's own (mechanical) last touch
    assert not any(s["name"] == "m" for s in find_stale(memory_dir, repo, since=_ALL))  # cleared
    _, body_after = P.split_frontmatter(after)
    assert body_after == body_before  # body byte-identical


def test_reverify_is_idempotent(repo, memory_dir):
    write_file(repo, "src/dep.py", "v = 1\n")
    git_commit(repo, "init", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m.md",
        '---\nname: M\ncited_paths: ["src/dep.py"]\nsource_commit: "OLD"\n---\nbody src/dep.py\n',
    )
    git_commit(repo, "memory", 1_700_000_100)
    assert _reverify_one(memory_dir, repo, "m")["changed"] is True   # OLD -> HEAD
    assert _reverify_one(memory_dir, repo, "m")["changed"] is False  # already HEAD -> no-op


# --------------------------------------------------------------------------- #
# RET-6 reinforcement: reverify_file also stamps last_verified, WRITE-ONCE, the first
# time a memory is ever re-verified — the drift-banner-clearing signal itself stays
# source_commit (re-baselined above on EVERY call, changed or not); last_verified is
# supplementary audit provenance layered on top, per the roadmap's "touch last_verified
# via the existing reverify primitive."
# --------------------------------------------------------------------------- #
def test_reverify_stamps_last_verified_on_first_confirmation(repo, memory_dir):
    write_file(repo, "src/dep.py", "v = 1\n")
    c1 = git_commit(repo, "dep v1", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m.md",
        f'---\nname: M\ncited_paths: ["src/dep.py"]\nsource_commit: "{c1}"\n---\nbody src/dep.py\n',
    )
    git_commit(repo, "memory", 1_700_000_001)

    res = _reverify_one(memory_dir, repo, "m")
    assert res["changed"] is True and res["error"] is None
    assert res["last_verified"]  # a real, non-empty ISO-8601 stamp

    after = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()
    assert read_last_verified(after) == res["last_verified"]


def test_reverify_never_overwrites_an_existing_last_verified_stamp(repo, memory_dir):
    """A memory reverified a SECOND time (real code drift in between) keeps its ORIGINAL
    last_verified — write-once, never a running log of every re-check — while
    source_commit still re-baselines to the new HEAD both times (the actual
    banner-clearing signal is unaffected by this)."""
    write_file(repo, "src/dep.py", "v = 1\n")
    c1 = git_commit(repo, "dep v1", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m.md",
        f'---\nname: M\ncited_paths: ["src/dep.py"]\nsource_commit: "{c1}"\n---\nbody src/dep.py\n',
    )
    git_commit(repo, "memory", 1_700_000_001)

    first = _reverify_one(memory_dir, repo, "m")
    assert first["changed"] is True
    first_stamp = first["last_verified"]
    head1 = P.run_git(["rev-parse", "HEAD"], repo).strip()
    assert first["source_commit"] == head1

    # Idempotent re-run right away: last_verified is preserved, changed is False.
    again = _reverify_one(memory_dir, repo, "m")
    assert again["changed"] is False
    assert again["last_verified"] == first_stamp

    # Real drift + a SECOND genuine re-verification: source_commit moves to the new HEAD,
    # but last_verified stays pinned to the FIRST confirmation.
    write_file(repo, "src/dep.py", "v = 2\n")
    git_commit(repo, "dep v2", 1_700_000_500)
    second = _reverify_one(memory_dir, repo, "m")
    assert second["changed"] is True
    head2 = P.run_git(["rev-parse", "HEAD"], repo).strip()
    assert second["source_commit"] == head2 and head2 != head1
    assert second["last_verified"] == first_stamp  # unchanged — write-once

    after = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()
    assert read_last_verified(after) == first_stamp


def test_reverify_refuses_unparseable_frontmatter(repo, memory_dir):
    write_file(repo, "src/a.py", "x = 1\n")
    git_commit(repo, "init", 1_700_000_000)
    bad = (
        "---\nname: B\ndescription: oops Also: a colon\n"
        'cited_paths: ["src/a.py"]\nsource_commit: "BASE_KEEP"\n---\nbody\n'
    )
    write_file(repo, ".claude/memory/m.md", bad)
    before = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()
    res = _reverify_one(memory_dir, repo, "m")
    after = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()
    assert after == before  # UNTOUCHED — no silent rewrite / re-baseline
    assert res["changed"] is False and res["error"] and "unparseable" in res["error"]


def test_reverify_requires_backfill_first(repo, memory_dir):
    git_commit(repo, "init", 1_700_000_000)
    write_file(repo, ".claude/memory/m.md", "---\nname: M\ntype: project\n---\nbody\n")
    res = _reverify_one(memory_dir, repo, "m")
    assert res["changed"] is False and res["error"] and "backfill" in res["error"]


def test_no_bulk_reverify_symbol():
    # The blind bulk re-baseline (reverify_corpus / --reverify-all) was a footgun — it anchors to
    # the mechanical backfill touch and silences real drift. Pin that it does NOT exist.
    assert not hasattr(P, "reverify_corpus")


# --------------------------------------------------------------------------- #
# reverify_file strips invalid_after (Tier 3, graceful decay — re-opens the validity window)
# --------------------------------------------------------------------------- #
def test_reverify_strips_invalid_after_top_level(repo, memory_dir):
    write_file(repo, "src/dep.py", "v = 1\n")
    c1 = git_commit(repo, "dep v1", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m.md",
        f'---\nname: M\ncited_paths: ["src/dep.py"]\nsource_commit: "{c1}"\n'
        'invalid_after: "2026-01-01T00:00:00+00:00"\n---\nbody src/dep.py\n',
    )
    git_commit(repo, "memory", 1_700_000_001)

    res = _reverify_one(memory_dir, repo, "m")
    assert res["changed"] is True and res["error"] is None
    after = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()
    assert "invalid_after" not in after  # the validity window is re-opened


def test_reverify_strips_invalid_after_nested_under_metadata(repo, memory_dir):
    write_file(repo, "src/dep.py", "v = 1\n")
    c1 = git_commit(repo, "dep v1", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m.md",
        f'---\nname: M\nmetadata:\n  cited_paths: ["src/dep.py"]\n  source_commit: "{c1}"\n'
        '  invalid_after: "2026-01-01T00:00:00+00:00"\n---\nbody src/dep.py\n',
    )
    git_commit(repo, "memory", 1_700_000_001)

    res = _reverify_one(memory_dir, repo, "m")
    assert res["changed"] is True and res["error"] is None
    after = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()
    assert "invalid_after" not in after


def test_reverify_with_no_invalid_after_is_unaffected(repo, memory_dir):
    """A memory with no invalid_after at all still re-baselines normally -- the new strip
    step is a pure no-op when there's nothing to strip."""
    write_file(repo, "src/dep.py", "v = 1\n")
    c1 = git_commit(repo, "dep v1", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m.md",
        f'---\nname: M\ncited_paths: ["src/dep.py"]\nsource_commit: "{c1}"\n---\nbody src/dep.py\n',
    )
    git_commit(repo, "memory", 1_700_000_001)
    res = _reverify_one(memory_dir, repo, "m")
    assert res["changed"] is True and res["error"] is None


def test_refresh_does_NOT_strip_invalid_after(repo, memory_dir):
    """The mechanical --refresh path must NEVER silently clear a soft-invalidation flag --
    only a genuine re-verification (reverify_file) re-opens the validity window."""
    write_file(repo, "src/dep.py", "v = 1\n")
    c1 = git_commit(repo, "dep v1", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m.md",
        f'---\nname: M\ncited_paths: ["src/dep.py"]\nsource_commit: "{c1}"\n'
        'invalid_after: "2026-01-01T00:00:00+00:00"\n---\nbody src/dep.py\n',
    )
    git_commit(repo, "memory", 1_700_000_001)

    repo_files, basename_index = P.build_repo_file_index(repo)
    target = os.path.join(memory_dir, "m.md")
    res = P.backfill_file(target, repo, repo_files, basename_index, refresh=True)
    assert res["error"] is None
    after = open(target, encoding="utf-8").read()
    assert "invalid_after" in after  # untouched by the mechanical refresh path
    assert res["source_commit"] == c1  # refresh preserves the baseline too, as documented


# --------------------------------------------------------------------------- #
# --refresh-one — the scoped sibling of --refresh (touches ONE memory, not the corpus)
# --------------------------------------------------------------------------- #
def test_refresh_one_touches_only_the_named_memory(repo, memory_dir, monkeypatch):
    """The exact bug this flag fixes: re-deriving one memory's citations after a hand-edit
    must NOT re-derive (and potentially shrink) every OTHER memory's cited_paths too."""
    write_file(repo, "src/a.py", "x = 1\n")
    write_file(repo, "src/b.py", "y = 1\n")
    git_commit(repo, "init", 1_700_000_000)
    write_file(memory_dir, "m_target.md", '---\nname: m_target\ncited_paths: ["src/a.py"]\nsource_commit: "abc"\n---\nold body src/a.py\n')
    write_file(memory_dir, "m_other.md", '---\nname: m_other\ncited_paths: ["src/b.py"]\nsource_commit: "xyz"\n---\nbody src/b.py\n')
    before_other = open(os.path.join(memory_dir, "m_other.md"), encoding="utf-8").read()

    monkeypatch.setattr(P, "resolve_dirs", lambda: (memory_dir, repo))
    # simulate a hand-edit that adds a new citation to m_target only
    write_file(memory_dir, "m_target.md", '---\nname: m_target\ncited_paths: ["src/a.py"]\nsource_commit: "abc"\n---\nnew body src/a.py and src/b.py\n')
    rc = P.main(["--refresh-one", "m_target"])
    assert rc == 0

    after_target = open(os.path.join(memory_dir, "m_target.md"), encoding="utf-8").read()
    assert "src/b.py" in after_target  # the new citation WAS picked up
    after_other = open(os.path.join(memory_dir, "m_other.md"), encoding="utf-8").read()
    assert after_other == before_other  # completely untouched


def test_refresh_one_preserves_source_commit(repo, memory_dir, monkeypatch):
    write_file(repo, "src/a.py", "x = 1\n")
    git_commit(repo, "init", 1_700_000_000)
    write_file(memory_dir, "m.md", '---\nname: m\ncited_paths: ["src/a.py"]\nsource_commit: "OLD_BASELINE"\n---\nbody src/a.py\n')

    monkeypatch.setattr(P, "resolve_dirs", lambda: (memory_dir, repo))
    rc = P.main(["--refresh-one", "m"])
    assert rc == 0
    after = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()
    assert "OLD_BASELINE" in after  # source_commit untouched, unlike --reverify


def test_refresh_one_accepts_name_with_or_without_md_suffix(repo, memory_dir, monkeypatch):
    write_file(repo, "src/a.py", "x = 1\n")
    git_commit(repo, "init", 1_700_000_000)
    write_file(memory_dir, "m.md", '---\nname: m\ncited_paths: ["src/a.py"]\nsource_commit: "abc"\n---\nbody src/a.py and src/new.py\n')
    write_file(repo, "src/new.py", "z = 1\n")
    git_commit(repo, "add new.py", 1_700_000_100)

    monkeypatch.setattr(P, "resolve_dirs", lambda: (memory_dir, repo))
    rc = P.main(["--refresh-one", "m.md"])  # WITH suffix
    assert rc == 0
    assert "src/new.py" in open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()


def test_refresh_one_body_byte_identical(repo, memory_dir, monkeypatch):
    write_file(repo, "src/a.py", "x = 1\n")
    git_commit(repo, "init", 1_700_000_000)
    body = "line one\nline two src/a.py\nline three\n"
    write_file(memory_dir, "m.md", f'---\nname: m\ncited_paths: ["src/a.py"]\nsource_commit: "abc"\n---\n{body}')

    monkeypatch.setattr(P, "resolve_dirs", lambda: (memory_dir, repo))
    P.main(["--refresh-one", "m"])
    _, after_body = P.split_frontmatter(open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read())
    assert after_body == body


def test_refresh_one_refuses_unparseable_frontmatter(repo, memory_dir, monkeypatch, capsys):
    write_file(repo, "src/a.py", "x = 1\n")
    git_commit(repo, "init", 1_700_000_000)
    bad = '---\nname: m\ndescription: oops Also: a colon\ncited_paths: ["src/a.py"]\nsource_commit: "abc"\n---\nbody\n'
    write_file(memory_dir, "m.md", bad)

    monkeypatch.setattr(P, "resolve_dirs", lambda: (memory_dir, repo))
    rc = P.main(["--refresh-one", "m"])
    assert rc == 0  # the CLI itself still exits 0 -- the refusal is reported, not raised
    assert "refused" in capsys.readouterr().out
    assert open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read() == bad  # untouched


def test_refresh_one_dry_run_does_not_write(repo, memory_dir, monkeypatch):
    write_file(repo, "src/a.py", "x = 1\n")
    git_commit(repo, "init", 1_700_000_000)
    before = '---\nname: m\ncited_paths: ["src/a.py"]\nsource_commit: "abc"\n---\nbody src/a.py and src/new.py\n'
    write_file(memory_dir, "m.md", before)
    write_file(repo, "src/new.py", "z = 1\n")
    git_commit(repo, "add new.py", 1_700_000_100)

    monkeypatch.setattr(P, "resolve_dirs", lambda: (memory_dir, repo))
    rc = P.main(["--refresh-one", "m", "--dry-run"])
    assert rc == 0
    assert open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read() == before  # untouched


def test_refresh_one_never_raises_on_missing_memory(repo, memory_dir, monkeypatch, capsys):
    monkeypatch.setattr(P, "resolve_dirs", lambda: (memory_dir, repo))
    rc = P.main(["--refresh-one", "does_not_exist"])
    assert rc == 0
    assert "refused" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# COR-1: memories are BORN staleness-tracked (HEAD fallback + baseline healing)
# --------------------------------------------------------------------------- #
def test_backfill_uncommitted_file_falls_back_to_head(repo, memory_dir):
    """A memory with no commit history of its own (just created, dirty worktree) gets
    HEAD as its baseline — "reflects code as of now" — never an empty source_commit
    that leaves it invisible to staleness."""
    write_file(repo, "src/x.py", "x = 1\n")
    head = git_commit(repo, "init", 1_700_000_000)
    path = write_file(
        repo, ".claude/memory/fresh.md", "---\nname: fresh\n---\nbody cites src/x.py\n"
    )  # NOT committed
    repo_files, basename_index = P.build_repo_file_index(repo)
    r = P.backfill_file(path, repo, repo_files, basename_index)
    assert r["changed"] is True and r["error"] is None
    assert r["source_commit"] == head
    _, sc = read_provenance(open(path, encoding="utf-8").read())
    assert sc == head


def test_backfill_no_commits_at_all_still_writes_empty(repo, memory_dir):
    """A repo with ZERO commits has no resolvable baseline anywhere — the empty
    source_commit is honest there; SessionStart heals it once HEAD exists."""
    path = write_file(repo, ".claude/memory/early.md", "---\nname: early\n---\nbody\n")
    repo_files, basename_index = P.build_repo_file_index(repo)
    r = P.backfill_file(path, repo, repo_files, basename_index)
    assert r["changed"] is True
    assert r["source_commit"] is None


def test_heal_empty_baselines_heals_only_empty(repo, memory_dir):
    write_file(repo, "src/x.py", "x = 1\n")
    head = git_commit(repo, "init", 1_700_000_000)
    empty = write_file(
        repo,
        ".claude/memory/m_empty.md",
        '---\nname: m_empty\nmetadata:\n  type: project\n  cited_paths: ["src/x.py"]\n'
        '  source_commit: ""\n---\nbody one\n',
    )
    real = write_file(
        repo,
        ".claude/memory/m_real.md",
        '---\nname: m_real\ncited_paths: []\nsource_commit: "REALBASELINE"\n---\nbody two\n',
    )
    none = write_file(repo, ".claude/memory/m_none.md", "---\nname: m_none\n---\nno provenance\n")
    broken = write_file(
        repo, ".claude/memory/m_broken.md", "---\nname: m: broken: yaml\nsource_commit: \n---\nb\n"
    )
    healed = P.heal_empty_baselines(memory_dir, repo)
    assert healed == ["m_empty"]
    healed_text = open(empty, encoding="utf-8").read()
    _, sc = read_provenance(healed_text)
    assert sc == head
    assert healed_text.endswith("---\nbody one\n")  # body byte-identical
    assert 'source_commit: "REALBASELINE"' in open(real, encoding="utf-8").read()  # untouched
    assert "source_commit" not in open(none, encoding="utf-8").read()  # backfill's job, not heal's
    assert "m_broken" not in healed  # unparseable skipped (integrity producer's territory)

    # Idempotent: a second sweep heals nothing.
    assert P.heal_empty_baselines(memory_dir, repo) == []


def test_heal_empty_baselines_noop_without_head(repo, memory_dir):
    write_file(
        repo,
        ".claude/memory/m.md",
        '---\nname: m\ncited_paths: []\nsource_commit: ""\n---\nbody\n',
    )
    assert P.heal_empty_baselines(memory_dir, repo) == []  # no commits yet -> no HEAD -> no-op


# --------------------------------------------------------------------------- #
# SHP-2 — resolve_dirs() walks UP for a monorepo-subdir launch (OQ-1: nested wins).
#
# ``claude`` started from ``packages/web`` sets CLAUDE_PROJECT_DIR to the subdir. Before
# this fix, resolve_dirs() looked ONLY there — a subdir with no memory dir of its own
# silently no-op'd the whole plugin (recall, every producer, new_memory's target, the
# floor symlink) even though a perfectly good corpus sat at the repo root. These tests
# pin CLAUDE_PROJECT_DIR to a subdir and assert the walk-up resolves the right corpus.
# --------------------------------------------------------------------------- #
def _init_repo(path: str) -> None:
    import subprocess

    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=path, check=True)


def test_resolve_dirs_nested_corpus_wins_over_root(tmp_path, monkeypatch):
    repo = str(tmp_path / "repo")
    _init_repo(repo)
    subdir = os.path.join(repo, "packages", "web")
    os.makedirs(subdir)

    root_md = os.path.join(repo, ".claude", "memory")
    os.makedirs(root_md)
    nested_md = os.path.join(subdir, ".claude", "memory")
    os.makedirs(nested_md)

    monkeypatch.delenv("HIPPO_MEMORY_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", subdir)

    memory_dir, repo_root = P.resolve_dirs()
    assert memory_dir == nested_md  # nested (per-package) corpus wins even though root exists
    assert os.path.abspath(repo_root) == os.path.abspath(repo)


def test_resolve_dirs_falls_through_to_root_when_subdir_has_no_corpus(tmp_path, monkeypatch):
    repo = str(tmp_path / "repo")
    _init_repo(repo)
    subdir = os.path.join(repo, "packages", "web")
    os.makedirs(subdir)

    root_md = os.path.join(repo, ".claude", "memory")
    os.makedirs(root_md)
    # No nested .claude/memory under subdir.

    monkeypatch.delenv("HIPPO_MEMORY_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", subdir)

    memory_dir, repo_root = P.resolve_dirs()
    assert memory_dir == root_md  # falls through to the repo-root corpus
    assert os.path.abspath(repo_root) == os.path.abspath(repo)


def test_resolve_dirs_falls_back_to_project_dir_when_no_corpus_anywhere(tmp_path, monkeypatch):
    repo = str(tmp_path / "repo")
    _init_repo(repo)
    subdir = os.path.join(repo, "packages", "web")
    os.makedirs(subdir)
    # Neither the subdir NOR the repo root has a .claude/memory anywhere.

    monkeypatch.delenv("HIPPO_MEMORY_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", subdir)

    memory_dir, repo_root = P.resolve_dirs()
    # Today's behavior preserved: /hippo:init still has somewhere to seed.
    assert memory_dir == os.path.join(subdir, ".claude", "memory")
    assert os.path.abspath(repo_root) == os.path.abspath(repo)


def test_resolve_dirs_explicit_memobot_memory_dir_bypasses_walk_up(tmp_path, monkeypatch):
    repo = str(tmp_path / "repo")
    _init_repo(repo)
    subdir = os.path.join(repo, "packages", "web")
    os.makedirs(subdir)
    root_md = os.path.join(repo, ".claude", "memory")
    os.makedirs(root_md)
    explicit_md = str(tmp_path / "somewhere-else")
    os.makedirs(explicit_md)

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", subdir)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", explicit_md)

    memory_dir, repo_root = P.resolve_dirs()
    assert memory_dir == explicit_md  # explicit override always wins, no walk-up


def test_walk_up_for_memory_dir_never_ascends_past_home(tmp_path, monkeypatch):
    fake_home = str(tmp_path / "home")
    os.makedirs(fake_home)
    deep = os.path.join(fake_home, "a", "b", "c")
    os.makedirs(deep)
    # No .claude/memory anywhere from `deep` up through `fake_home`, and no git repo at
    # all -- the walk must stop AT fake_home, never wandering out to the real filesystem.
    monkeypatch.setattr(os.path, "expanduser", lambda p: fake_home if p == "~" else p)
    memory_dir, reason = P.walk_up_for_memory_dir(deep)
    assert memory_dir == ""
    assert reason == "none-found"


def test_walk_up_for_memory_dir_reports_nested_reason():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        nested = os.path.join(d, ".claude", "memory")
        os.makedirs(nested)
        memory_dir, reason = P.walk_up_for_memory_dir(d)
        assert memory_dir == nested
        assert reason == "nested"


# --------------------------------------------------------------------------- #
# encode_project_dir (SHP-5 — matches the harness's real one-char-per-'-' rule)
# --------------------------------------------------------------------------- #
def test_encode_project_dir_plain_path_only_slashes():
    assert P.encode_project_dir("/Users/x/GitHub/hippo") == "-Users-x-GitHub-hippo"


def test_encode_project_dir_dotted_path_replaces_every_dot():
    # Confirmed empirically: '/' AND '.' are each replaced 1-for-1, never collapsed.
    assert P.encode_project_dir("/Users/x/dev/next.js-app") == "-Users-x-dev-next-js-app"


def test_encode_project_dir_underscored_path_replaces_underscore():
    assert P.encode_project_dir("/Users/x/dev/sdk_2.0") == "-Users-x-dev-sdk-2-0"


def test_encode_project_dir_consecutive_punctuation_produces_consecutive_hyphens():
    # "/." (slash then dot) before "claude" -> TWO hyphens, not collapsed to one.
    path = "/Users/x/dev/proj/.claude/memory"
    expected = "-Users-x-dev-proj--claude-memory"
    assert P.encode_project_dir(path) == expected


def test_encode_project_dir_existing_hyphens_pass_through_unchanged():
    path = "/Users/x/dev/proj/ui-lib/src"
    expected = "-Users-x-dev-proj-ui-lib-src"
    assert P.encode_project_dir(path) == expected


def test_encode_project_dir_keeps_leading_hyphen_no_strip():
    assert P.encode_project_dir("/a").startswith("-")


def test_legacy_encode_project_dir_differs_from_fixed_only_when_punctuation_present():
    plain = "/Users/x/GitHub/hippo"
    assert P._legacy_encode_project_dir(plain) == P.encode_project_dir(plain)  # coincidentally same

    dotted = "/Users/x/dev/next.js-app"
    assert P._legacy_encode_project_dir(dotted) != P.encode_project_dir(dotted)  # bug reproduced


# --------------------------------------------------------------------------- #
# check_project_symlink (SHP-5 — verify from the direction Claude Code reads)
# --------------------------------------------------------------------------- #
def test_check_project_symlink_ok_when_correctly_encoded_and_resolves(tmp_path):
    repo_root = str(tmp_path / "Users" / "x" / "dev" / "next.js-app")
    memory_dir = os.path.join(repo_root, ".claude", "memory")
    os.makedirs(memory_dir)
    projects_dir = str(tmp_path / "claude-projects")
    encoded = P.encode_project_dir(repo_root)
    link_dir = os.path.join(projects_dir, encoded)
    os.makedirs(link_dir)
    os.symlink(memory_dir, os.path.join(link_dir, "memory"))

    result = P.check_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)
    assert result["status"] == "ok"


def test_check_project_symlink_missing_when_no_symlink_at_all(tmp_path):
    repo_root = str(tmp_path / "Users" / "x" / "dev" / "next.js-app")
    memory_dir = os.path.join(repo_root, ".claude", "memory")
    os.makedirs(memory_dir)
    projects_dir = str(tmp_path / "claude-projects")

    result = P.check_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)
    assert result["status"] == "missing"
    assert result["repair_command"]


def test_check_project_symlink_broken_when_points_elsewhere(tmp_path):
    repo_root = str(tmp_path / "Users" / "x" / "dev" / "next.js-app")
    memory_dir = os.path.join(repo_root, ".claude", "memory")
    os.makedirs(memory_dir)
    other_dir = str(tmp_path / "somewhere-else")
    os.makedirs(other_dir)
    projects_dir = str(tmp_path / "claude-projects")
    encoded = P.encode_project_dir(repo_root)
    link_dir = os.path.join(projects_dir, encoded)
    os.makedirs(link_dir)
    os.symlink(other_dir, os.path.join(link_dir, "memory"))

    result = P.check_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)
    assert result["status"] == "broken"
    assert result["repair_command"]


def test_check_project_symlink_detects_legacy_wrong_encoding(tmp_path):
    # A dotted repo path: the pre-SHP-5 formula (only '/' transliterated) produces a
    # DIFFERENT directory name than the fixed one — simulate a symlink created by that
    # old buggy formula and confirm doctor's check names it as a legacy artifact.
    repo_root = str(tmp_path / "Users" / "x" / "dev" / "next.js-app")
    memory_dir = os.path.join(repo_root, ".claude", "memory")
    os.makedirs(memory_dir)
    projects_dir = str(tmp_path / "claude-projects")
    legacy_encoded = P._legacy_encode_project_dir(repo_root)
    assert legacy_encoded != P.encode_project_dir(repo_root)  # sanity: the two differ here
    legacy_link_dir = os.path.join(projects_dir, legacy_encoded)
    os.makedirs(legacy_link_dir)
    os.symlink(memory_dir, os.path.join(legacy_link_dir, "memory"))

    result = P.check_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)
    assert result["status"] == "legacy_wrong_encoding"
    assert result["repair_command"]
    assert legacy_link_dir in result["repair_command"] or result["legacy_path"] == os.path.join(
        legacy_link_dir, "memory"
    )


# --------------------------------------------------------------------------- #
# create_project_symlink (ONB-5 — machine-local setup for an existing corpus:
# teammate clone, new worktree, second machine)
# --------------------------------------------------------------------------- #
def test_create_project_symlink_creates_when_absent(tmp_path):
    repo_root = str(tmp_path / "Users" / "x" / "dev" / "myrepo")
    memory_dir = os.path.join(repo_root, ".claude", "memory")
    os.makedirs(memory_dir)
    projects_dir = str(tmp_path / "claude-projects")

    result = P.create_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)

    assert result["status"] == "created"
    assert os.path.islink(result["expected_path"])
    assert os.path.realpath(result["expected_path"]) == os.path.realpath(memory_dir)


def test_create_project_symlink_idempotent_noop_when_already_correct(tmp_path):
    repo_root = str(tmp_path / "Users" / "x" / "dev" / "myrepo")
    memory_dir = os.path.join(repo_root, ".claude", "memory")
    os.makedirs(memory_dir)
    marker = os.path.join(memory_dir, "MEMORY.md")
    with open(marker, "w", encoding="utf-8") as fh:
        fh.write("# existing corpus\n")
    before = open(marker, "rb").read()
    projects_dir = str(tmp_path / "claude-projects")

    first = P.create_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)
    assert first["status"] == "created"

    second = P.create_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)
    assert second["status"] == "already_correct"
    assert second["expected_path"] == first["expected_path"]

    after = open(marker, "rb").read()
    assert after == before  # MEMORY.md is byte-identical — never touched by symlink setup


def test_create_project_symlink_reports_conflict_without_clobbering(tmp_path):
    repo_root = str(tmp_path / "Users" / "x" / "dev" / "myrepo")
    memory_dir = os.path.join(repo_root, ".claude", "memory")
    os.makedirs(memory_dir)
    other_dir = str(tmp_path / "somewhere-else")
    os.makedirs(other_dir)
    projects_dir = str(tmp_path / "claude-projects")
    encoded = P.encode_project_dir(repo_root)
    link_dir = os.path.join(projects_dir, encoded)
    os.makedirs(link_dir)
    existing_link = os.path.join(link_dir, "memory")
    os.symlink(other_dir, existing_link)

    result = P.create_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)

    assert result["status"] == "conflict"
    assert result["error"]
    # the pre-existing symlink is left exactly as it was — never overwritten
    assert os.path.realpath(existing_link) == os.path.realpath(other_dir)


def test_create_project_symlink_uses_same_encoding_as_check(tmp_path):
    repo_root = str(tmp_path / "Users" / "x" / "dev" / "next.js-app")
    memory_dir = os.path.join(repo_root, ".claude", "memory")
    os.makedirs(memory_dir)
    projects_dir = str(tmp_path / "claude-projects")

    created = P.create_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)
    assert created["status"] == "created"

    checked = P.check_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)
    assert checked["status"] == "ok"


# --------------------------------------------------------------------------- #
# ONB-5: init's machine-local setup on an existing (cloned) corpus — symlink +
# index build, memory files never touched.
# --------------------------------------------------------------------------- #
def test_existing_corpus_gets_symlink_and_index_without_touching_memory(tmp_path, monkeypatch):
    from memory import build_index as B

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")  # fast, offline, BM25-only build

    repo_root = str(tmp_path / "repo")
    memory_dir = os.path.join(repo_root, ".claude", "memory")
    os.makedirs(memory_dir)
    memory_path = write_file(
        repo_root, ".claude/memory/MEMORY.md", "# Project memory\n\n## User\n- nothing yet\n"
    )
    write_file(
        repo_root,
        ".claude/memory/user_role.md",
        "---\ntype: user\n---\nSome existing corpus content already committed by a teammate.\n",
    )
    before = open(memory_path, "rb").read()
    projects_dir = str(tmp_path / "claude-projects")
    index_dir = str(tmp_path / "repo" / ".claude" / ".memory-index")

    # Simulates a teammate clone / new worktree / second machine: the corpus (MEMORY.md)
    # is already on disk, but the machine-local symlink and index have never been built.
    link_result = P.create_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)
    assert link_result["status"] == "created"
    manifest = B.build_index(memory_dir, index_dir)

    assert manifest["count"] == 1
    assert os.path.exists(os.path.join(index_dir, "manifest.json"))
    after = open(memory_path, "rb").read()
    assert after == before  # MEMORY.md is byte-identical — init never re-seeds an existing corpus

    # Re-running (idempotent — same as running /hippo:init again on the same machine)
    # leaves both the symlink and the corpus exactly as they were.
    link_again = P.create_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)
    assert link_again["status"] == "already_correct"
    manifest_again = B.build_index(memory_dir, index_dir)
    assert manifest_again["count"] == 1
    assert open(memory_path, "rb").read() == before


# --------------------------------------------------------------------------- #
# remove_project_symlink (ONB-6 — /hippo:remove's machine-local teardown: the
# inverse of create_project_symlink, never touches memory_dir itself)
# --------------------------------------------------------------------------- #
def test_remove_project_symlink_removes_when_correct(tmp_path):
    repo_root = str(tmp_path / "Users" / "x" / "dev" / "myrepo")
    memory_dir = os.path.join(repo_root, ".claude", "memory")
    os.makedirs(memory_dir)
    projects_dir = str(tmp_path / "claude-projects")

    created = P.create_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)
    assert created["status"] == "created"
    assert os.path.islink(created["expected_path"])

    result = P.remove_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)

    assert result["status"] == "removed"
    assert result["expected_path"] == created["expected_path"]
    assert not os.path.islink(result["expected_path"])
    assert not os.path.exists(result["expected_path"])


def test_remove_project_symlink_absent_is_a_noop(tmp_path):
    repo_root = str(tmp_path / "Users" / "x" / "dev" / "never-linked")
    memory_dir = os.path.join(repo_root, ".claude", "memory")
    projects_dir = str(tmp_path / "claude-projects")

    result = P.remove_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)

    assert result["status"] == "absent"
    assert result["error"] is None


def test_remove_project_symlink_reports_conflict_without_deleting(tmp_path):
    repo_root = str(tmp_path / "Users" / "x" / "dev" / "myrepo")
    memory_dir = os.path.join(repo_root, ".claude", "memory")
    os.makedirs(memory_dir)
    other_dir = str(tmp_path / "somewhere-else")
    os.makedirs(other_dir)
    projects_dir = str(tmp_path / "claude-projects")
    encoded = P.encode_project_dir(repo_root)
    link_dir = os.path.join(projects_dir, encoded)
    os.makedirs(link_dir)
    existing_link = os.path.join(link_dir, "memory")
    os.symlink(other_dir, existing_link)

    result = P.remove_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)

    assert result["status"] == "conflict"
    assert result["error"]
    # the pre-existing symlink (pointing at a DIFFERENT project's corpus) is left exactly as is
    assert os.path.islink(existing_link)
    assert os.path.realpath(existing_link) == os.path.realpath(other_dir)


def test_remove_project_symlink_never_touches_memory_dir_contents(tmp_path):
    repo_root = str(tmp_path / "repo")
    memory_dir = os.path.join(repo_root, ".claude", "memory")
    os.makedirs(memory_dir)
    marker = os.path.join(memory_dir, "MEMORY.md")
    with open(marker, "w", encoding="utf-8") as fh:
        fh.write("# committed corpus, stays inert after removal\n")
    before = open(marker, "rb").read()
    projects_dir = str(tmp_path / "claude-projects")

    P.create_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)
    result = P.remove_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)

    assert result["status"] == "removed"
    assert os.path.isdir(memory_dir)  # the corpus directory itself is untouched
    after = open(marker, "rb").read()
    assert after == before  # byte-identical — removal never edits the git-tracked corpus


def test_remove_project_symlink_uses_same_encoding_as_create(tmp_path):
    repo_root = str(tmp_path / "Users" / "x" / "dev" / "next.js-app")
    memory_dir = os.path.join(repo_root, ".claude", "memory")
    os.makedirs(memory_dir)
    projects_dir = str(tmp_path / "claude-projects")

    created = P.create_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)
    assert created["status"] == "created"

    removed = P.remove_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)
    assert removed["status"] == "removed"
    assert removed["expected_path"] == created["expected_path"]


def test_remove_project_symlink_idempotent_second_call_is_absent(tmp_path):
    repo_root = str(tmp_path / "Users" / "x" / "dev" / "myrepo")
    memory_dir = os.path.join(repo_root, ".claude", "memory")
    os.makedirs(memory_dir)
    projects_dir = str(tmp_path / "claude-projects")

    P.create_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)
    first = P.remove_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)
    assert first["status"] == "removed"

    second = P.remove_project_symlink(repo_root, memory_dir, claude_projects_dir=projects_dir)
    assert second["status"] == "absent"


# --------------------------------------------------------------------------- #
# COR-7: corpus format marker (.claude/memory/.format) — read/write helpers
# --------------------------------------------------------------------------- #
def test_read_corpus_format_is_1_when_undeclared(tmp_path):
    """A corpus with NO marker reads as format 1 — every pre-marker corpus is already on
    the baseline, so absence must mean the baseline, never an error."""
    md = str(tmp_path / "memory")
    os.makedirs(md)
    assert P.read_corpus_format(md) == 1
    assert P.read_corpus_format(str(tmp_path / "does-not-exist")) == 1


def test_write_then_read_corpus_format_round_trips(tmp_path):
    md = str(tmp_path / "memory")
    os.makedirs(md)
    assert P.write_corpus_format(md) is True
    assert P.read_corpus_format(md) == P.CORPUS_FORMAT_VERSION
    # An explicit version (the final step of a doctor-driven migration) round-trips too.
    assert P.write_corpus_format(md, version=7) is True
    assert P.read_corpus_format(md) == 7


def test_corpus_format_marker_is_json_at_the_canonical_path(tmp_path):
    md = str(tmp_path / "memory")
    os.makedirs(md)
    assert P.write_corpus_format(md) is True
    marker = P.format_marker_path(md)
    assert marker == os.path.join(md, ".format")
    with open(marker, "r", encoding="utf-8") as fh:
        assert json.load(fh) == {"corpus_format": P.CORPUS_FORMAT_VERSION}


def test_read_corpus_format_degrades_to_1_on_garbage(tmp_path):
    """An unreadable/corrupt/wrong-shape marker degrades to the baseline (never raises) —
    doctor reports against whatever this returns, so garbage at worst reads as format 1
    rather than blocking recall."""
    md = str(tmp_path / "memory")
    os.makedirs(md)
    for garbage in (
        "{not json",
        '["corpus_format", 2]',  # non-dict payload
        '{"corpus_format": "two"}',  # non-int value
        '{"corpus_format": true}',  # bool is an int subclass — must NOT read as 1==True
        '{"something_else": 2}',  # missing key
    ):
        with open(P.format_marker_path(md), "w", encoding="utf-8") as fh:
            fh.write(garbage)
        assert P.read_corpus_format(md) == 1, garbage


def test_write_corpus_format_returns_false_on_missing_dir(tmp_path):
    assert P.write_corpus_format(str(tmp_path / "does-not-exist")) is False


def test_format_marker_is_invisible_to_the_corpus_iterator(tmp_path):
    """.format is a marker, not a memory — _iter_memory_files (THE corpus-membership
    filter) must never yield it, so it can never be indexed/floor-scanned/backfilled."""
    md = str(tmp_path / "memory")
    os.makedirs(md)
    assert P.write_corpus_format(md) is True
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: a\n---\nbody\n")
    assert [os.path.basename(p) for p in P._iter_memory_files(md)] == ["a.md"]


def test_conventions_md_is_invisible_to_the_corpus_iterator(tmp_path):
    """CONVENTIONS.md (DOC-6) is a reference doc seeded by /hippo:init, not a memory —
    _iter_memory_files (THE corpus-membership filter) must never yield it, the same
    canonical exclusion MEMORY.md/MEMORY.full.md already get. This is the one guard every
    downstream consumer (indexing, floor lint, staleness, archive, the GRA-6 edge-cache
    stat sweep) inherits for free, since all of them read through this one filter."""
    md = str(tmp_path / "memory")
    os.makedirs(md)
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: a\n---\nbody\n")
    with open(os.path.join(md, "CONVENTIONS.md"), "w", encoding="utf-8") as fh:
        fh.write("# Memory corpus conventions\n")
    assert [os.path.basename(p) for p in P._iter_memory_files(md)] == ["a.md"]


def test_is_memory_filename_excludes_conventions_md():
    assert P._is_memory_filename("CONVENTIONS.md") is False
    assert P._is_memory_filename("MEMORY.md") is False
    assert P._is_memory_filename("MEMORY.full.md") is False
    assert P._is_memory_filename("a.md") is True


def test_corpus_format_version_is_5_for_derives_from():
    """Each corpus format bump is a deliberate act with release-notes migration steps,
    never an accident — pinned per bump. v2 = GRA-4 typed frontmatter relations
    (supersedes/contradicts/refines); v3 = GOV-2 `steer: pin` (the author's bounded,
    always-on recall lift; closed enum, MUTE deliberately excluded until the salience
    keystone); v4 = GOV-7 `confidence: draft|verified|authoritative` (the author's trust
    dial — shipped display-only, made load-bearing in ranking by DRM-6); v5 = DRM-6
    `derives-from` (derivation-provenance typed relation: a generated schema/hypothesis
    parent names the children it was abstracted from; clean addition + bump, inv5)."""
    assert P.CORPUS_FORMAT_VERSION == 5


# --------------------------------------------------------------------------- #
# LIF-3: dropped_citations — a re-derivation that loses cited paths is REPORTED
# (rename/delete case), never a silent shrink; a drop to ZERO is called out
# distinctly because the memory becomes staleness-exempt.
# --------------------------------------------------------------------------- #
import subprocess  # noqa: E402


def _git_mv(repo, src, dst):
    subprocess.run(["git", "mv", src, dst], cwd=repo, check=True, capture_output=True)


def test_reverify_reports_dropped_citations_after_rename_not_a_silent_shrink(repo, memory_dir):
    """AC (LIF-3): rename a cited file (git mv + commit) → reverify NAMES the vanished
    path in dropped_citations instead of silently shrinking cited_paths."""
    write_file(repo, "src/keep.py", "k = 1\n")
    write_file(repo, "src/dep.py", "v = 1\n")
    c1 = git_commit(repo, "deps", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m.md",
        f'---\nname: M\ncited_paths: ["src/keep.py", "src/dep.py"]\nsource_commit: "{c1}"\n'
        "---\nbody cites src/keep.py and src/dep.py\n",
    )
    git_commit(repo, "memory", 1_700_000_001)
    _git_mv(repo, "src/dep.py", "src/dep_elsewhere2.py")  # new basename → no re-resolution
    git_commit(repo, "rename dep", 1_700_000_100)

    res = _reverify_one(memory_dir, repo, "m")
    assert res["error"] is None and res["changed"] is True
    assert res["dropped_citations"] == ["src/dep.py"]  # the vanished path is NAMED
    assert res["cited"] == ["src/keep.py"]  # the survivor remains — a partial drop, not zero


def test_refresh_reports_dropped_citations_on_deleted_file(repo, memory_dir):
    """The corpus-wide --refresh path reports the same drop, with the baseline still
    preserved — surfacing the rot must not change refresh's no-re-baseline contract."""
    write_file(repo, "src/a.py", "x = 1\n")
    write_file(repo, "src/b.py", "y = 1\n")
    git_commit(repo, "init", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m.md",
        '---\nname: M\ncited_paths: ["src/a.py", "src/b.py"]\nsource_commit: "BASE"\n'
        "---\nbody cites src/a.py and src/b.py\n",
    )
    subprocess.run(["git", "rm", "-q", "src/b.py"], cwd=repo, check=True, capture_output=True)
    git_commit(repo, "delete b", 1_700_000_100)

    repo_files, basename_index = P.build_repo_file_index(repo)
    r = P.backfill_file(os.path.join(memory_dir, "m.md"), repo, repo_files, basename_index, refresh=True)
    assert r["error"] is None
    assert r["dropped_citations"] == ["src/b.py"]
    assert r["cited"] == ["src/a.py"]
    assert r["source_commit"] == "BASE"


def test_initial_backfill_never_reports_dropped_citations(repo, memory_dir):
    """A first backfill has no prior cited_paths — nothing can be lost."""
    write_file(repo, "src/a.py", "x = 1\n")
    write_file(memory_dir, "m.md", "---\nname: m\n---\nbody cites src/a.py\n")
    git_commit(repo, "init", 1_700_000_000)
    repo_files, basename_index = P.build_repo_file_index(repo)
    r = P.backfill_file(os.path.join(memory_dir, "m.md"), repo, repo_files, basename_index)
    assert r["changed"] is True and r["dropped_citations"] == []


def test_refresh_reports_no_drop_when_nothing_vanished(repo, memory_dir):
    write_file(repo, "src/a.py", "x = 1\n")
    git_commit(repo, "init", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m.md",
        '---\nname: M\ncited_paths: ["src/a.py"]\nsource_commit: "BASE"\n---\nbody src/a.py\n',
    )
    repo_files, basename_index = P.build_repo_file_index(repo)
    r = P.backfill_file(os.path.join(memory_dir, "m.md"), repo, repo_files, basename_index, refresh=True)
    assert r["error"] is None and r["dropped_citations"] == []


def test_refresh_refusal_reports_no_drop(repo, memory_dir):
    """An unparseable-frontmatter refusal re-derives nothing — dropped_citations stays []."""
    write_file(repo, "src/a.py", "x = 1\n")
    git_commit(repo, "init", 1_700_000_000)
    bad = (
        "---\nname: B\ndescription: oops Also: a colon\n"
        'cited_paths: ["src/a.py"]\nsource_commit: "BASE_KEEP"\n---\nbody\n'
    )
    write_file(repo, ".claude/memory/m.md", bad)
    repo_files, basename_index = P.build_repo_file_index(repo)
    r = P.backfill_file(os.path.join(memory_dir, "m.md"), repo, repo_files, basename_index, refresh=True)
    assert r["error"] and r["dropped_citations"] == []


def test_cli_reverify_drop_to_zero_is_called_out_distinctly(repo, memory_dir, monkeypatch, capsys):
    """AC (LIF-3): a drop to ZERO empties cited_paths — find_stale has nothing left to
    watch, so the memory is now staleness-EXEMPT and the CLI must SAY so, distinctly."""
    write_file(repo, "src/dep.py", "v = 1\n")
    c1 = git_commit(repo, "dep", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m.md",
        f'---\nname: M\ncited_paths: ["src/dep.py"]\nsource_commit: "{c1}"\n---\nbody src/dep.py\n',
    )
    git_commit(repo, "memory", 1_700_000_001)
    _git_mv(repo, "src/dep.py", "src/dep_gone2.py")
    git_commit(repo, "rename dep away", 1_700_000_100)

    monkeypatch.setattr(P, "resolve_dirs", lambda: (memory_dir, repo))
    rc = P.main(["--reverify", "m"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "citation rot" in out and "m.md" in out and "src/dep.py" in out
    assert "ALL 1 cited path(s)" in out and "EXEMPT" in out  # zero is distinct, not a footnote
    cited, _ = read_provenance(open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read())
    assert cited == []  # the drop really happened — and it was loud, not silent


def test_cli_refresh_prints_per_file_rot_line(repo, memory_dir, monkeypatch, capsys):
    """Corpus-wide --refresh names every rotted file individually (count line + per-file
    ⚠ line); healthy files stay out of the rot block."""
    write_file(repo, "src/keep.py", "k = 1\n")
    write_file(repo, "src/gone.py", "g = 1\n")
    git_commit(repo, "init", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m_rot.md",
        '---\nname: m_rot\ncited_paths: ["src/keep.py", "src/gone.py"]\nsource_commit: "B1"\n'
        "---\nbody cites src/keep.py and src/gone.py\n",
    )
    write_file(
        repo,
        ".claude/memory/m_ok.md",
        '---\nname: m_ok\ncited_paths: ["src/keep.py"]\nsource_commit: "B2"\n---\nbody src/keep.py\n',
    )
    subprocess.run(["git", "rm", "-q", "src/gone.py"], cwd=repo, check=True, capture_output=True)
    git_commit(repo, "delete gone", 1_700_000_100)

    monkeypatch.setattr(P, "resolve_dirs", lambda: (memory_dir, repo))
    rc = P.main(["--refresh"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "citation rot" in out
    assert "1 file(s) dropped cited path(s)" in out
    assert "m_rot.md" in out and "src/gone.py" in out  # per-file, path named
    assert "1 citation(s) remain" in out  # partial drop — remaining count shown
    assert "m_ok.md" not in out  # a healthy file never appears in the rot block


def test_cli_refresh_one_prints_rot_line(repo, memory_dir, monkeypatch, capsys):
    write_file(repo, "src/gone.py", "g = 1\n")
    git_commit(repo, "init", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m.md",
        '---\nname: m\ncited_paths: ["src/gone.py"]\nsource_commit: "B"\n---\nbody src/gone.py\n',
    )
    subprocess.run(["git", "rm", "-q", "src/gone.py"], cwd=repo, check=True, capture_output=True)
    git_commit(repo, "delete gone", 1_700_000_100)

    monkeypatch.setattr(P, "resolve_dirs", lambda: (memory_dir, repo))
    rc = P.main(["--refresh-one", "m"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "citation rot" in out and "m.md" in out and "src/gone.py" in out
    assert "EXEMPT" in out  # its only citation vanished → the zero case, said distinctly


def test_cli_refresh_dry_run_rot_line_says_would_drop_and_writes_nothing(repo, memory_dir, monkeypatch, capsys):
    write_file(repo, "src/gone.py", "g = 1\n")
    git_commit(repo, "init", 1_700_000_000)
    before = '---\nname: m\ncited_paths: ["src/gone.py"]\nsource_commit: "B"\n---\nbody src/gone.py\n'
    write_file(repo, ".claude/memory/m.md", before)
    subprocess.run(["git", "rm", "-q", "src/gone.py"], cwd=repo, check=True, capture_output=True)
    git_commit(repo, "delete gone", 1_700_000_100)

    monkeypatch.setattr(P, "resolve_dirs", lambda: (memory_dir, repo))
    rc = P.main(["--refresh", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "would drop" in out and "src/gone.py" in out  # previewed with dry-run verbs
    assert open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read() == before  # untouched


def test_citation_rot_lines_empty_when_nothing_dropped():
    assert P.citation_rot_lines("m.md", {"cited": ["src/a.py"], "dropped_citations": []}) == []


# --------------------------------------------------------------------------- #
# COR-9 — a frontmatter writer may never emit frontmatter that does not parse
# --------------------------------------------------------------------------- #
import pytest  # noqa: E402

_PROV_KEYS = {"cited_paths", "source_commit", "source_commit_time"}

# The same memory in both frontmatter schemas the corpus actually uses, each with a
# BLOCK-STYLE cited_paths (what a hand edit, an import, or a foreign pack produces —
# hippo's own _flow_list never emits it, which is why this went unseen for 14 minor
# versions) and a non-provenance key both directly above and below it.
_BLOCK_FLAT = (
    "---\n"
    "name: M\n"
    "last_verified: 2026-07-01\n"
    "cited_paths:\n"
    "  - src/keep.py\n"
    "  - src/dep.py\n"
    "tags:\n"
    "  - keep-me\n"
    "---\n"
    "body cites src/keep.py and src/dep.py\n"
)
_BLOCK_META = (
    "---\n"
    "name: M\n"
    "metadata:\n"
    "  type: project\n"
    "  last_verified: 2026-07-01\n"
    "  cited_paths:\n"
    "    - src/keep.py\n"
    "    - src/dep.py\n"
    "  tags:\n"
    "    - keep-me\n"
    "---\n"
    "body cites src/keep.py and src/dep.py\n"
)


def _prov_free(fm: dict) -> dict:
    """``fm`` minus the three provenance keys, at BOTH schema levels."""
    out = {k: v for k, v in fm.items() if k not in _PROV_KEYS}
    if isinstance(out.get("metadata"), dict):
        out["metadata"] = {k: v for k, v in out["metadata"].items() if k not in _PROV_KEYS}
    return out


def test_strip_provenance_consumes_block_style_continuation_lines():
    """A block-style value IS its `- item` lines. Dropping only the key line orphans them:
    YAML then folds them into the PRECEDING key or refuses the document outright — and
    parse_frontmatter swallows that, so the memory silently loses name/type/provenance."""
    assert P.parse_frontmatter(_BLOCK_META)  # sanity: it parses going in
    out = P._strip_provenance(_BLOCK_META)
    assert "- src/keep.py" not in out  # the value left with its key
    fm = P.parse_frontmatter(out)
    assert fm, "frontmatter must still parse after the strip"
    assert fm["metadata"]["type"] == "project"  # not swallowed by the orphans
    assert fm["metadata"]["last_verified"] is not None  # the fold victim in the wild
    assert "cited_paths" not in fm["metadata"]


def test_strip_provenance_leaves_an_adjacent_block_list_alone():
    """The walk must stop at a sibling key — `tags:` sits directly below `cited_paths:`
    at the same indent, and its items must survive intact."""
    for text in (_BLOCK_FLAT, _BLOCK_META):
        fm = P.parse_frontmatter(P._strip_provenance(text))
        scope = fm.get("metadata") if "metadata" in fm else fm
        assert scope["tags"] == ["keep-me"]


def test_strip_provenance_flow_style_is_unchanged_behaviour():
    """Regression control: flow style has no continuation lines, so nothing else moves."""
    text = '---\nname: M\ncited_paths: ["a.py"]\ntags:\n  - keep-me\n---\nbody\n'
    fm = P.parse_frontmatter(P._strip_provenance(text))
    assert fm == {"name": "M", "tags": ["keep-me"]}


@pytest.mark.parametrize("schema", ["flat", "metadata"], ids=["flat", "metadata-block"])
def test_refresh_of_block_style_cited_paths_preserves_every_other_key(repo, memory_dir, schema):
    """AC (COR-9): --refresh over a block-style cited_paths must not cost the file any
    non-provenance key. Before the fix this rewrote `type`/`last_verified` into garbage or
    made the file unparseable outright."""
    write_file(repo, "src/keep.py", "k = 1\n")
    write_file(repo, "src/dep.py", "v = 1\n")
    git_commit(repo, "deps", 1_700_000_000)
    text = _BLOCK_FLAT if schema == "flat" else _BLOCK_META
    target = write_file(repo, ".claude/memory/m.md", text)
    git_commit(repo, "memory", 1_700_000_001)
    before = P.parse_frontmatter(text)
    assert before  # the fixture is valid going in

    repo_files, basename_index = P.build_repo_file_index(repo)
    res = P.backfill_file(target, repo, repo_files, basename_index, refresh=True)
    assert res["error"] is None

    after = P.parse_frontmatter(open(target, encoding="utf-8").read())
    assert after, "the rewritten file must still parse"
    assert _prov_free(after) == _prov_free(before)
    # and the re-derivation actually landed
    assert sorted(res["cited"]) == ["src/dep.py", "src/keep.py"]


@pytest.mark.parametrize("schema", ["flat", "metadata"], ids=["flat", "metadata-block"])
def test_reverify_of_block_style_cited_paths_preserves_every_other_key(repo, memory_dir, schema):
    """AC (COR-9): the same guarantee on the reverify path — a re-verify must never destroy
    the memory the human just confirmed is correct."""
    write_file(repo, "src/keep.py", "k = 1\n")
    write_file(repo, "src/dep.py", "v = 1\n")
    git_commit(repo, "deps", 1_700_000_000)
    text = _BLOCK_FLAT if schema == "flat" else _BLOCK_META
    target = write_file(repo, ".claude/memory/m.md", text)
    git_commit(repo, "memory", 1_700_000_001)
    before = P.parse_frontmatter(text)

    res = _reverify_one(memory_dir, repo, "m")
    assert res["error"] is None

    after = P.parse_frontmatter(open(target, encoding="utf-8").read())
    assert after, "the rewritten file must still parse"
    # last_verified is stamped by reverify (RET-6) — compare everything else.
    a, b = _prov_free(after), _prov_free(before)
    for d in (a, b):
        d.pop("last_verified", None)
        if isinstance(d.get("metadata"), dict):
            d["metadata"].pop("last_verified", None)
    assert a == b


def _pre_cor9_line_filter(text):
    """The pre-COR-9 ``_strip_provenance``, verbatim: a per-LINE filter."""
    return "\n".join(
        ln
        for ln in text.split("\n")
        if not P.re.match(r"\s*(cited_paths|source_commit|source_commit_time)\s*:", ln)
    )


def test_writer_refuses_rather_than_silently_fold_orphans_into_a_neighbour(
    repo, memory_dir, monkeypatch
):
    """AC (COR-9), the net itself — and specifically the SILENT half.

    Orphaned `- item` lines do not always break the parse. When the key above them carries an
    inline scalar, YAML FOLDS them into it as a multi-line plain scalar: the file parses
    perfectly and `last_verified` quietly becomes "2026-07-01 - src/keep.py - src/dep.py".
    A parse check cannot see that, which is why the guard is value-level. Re-introduce the
    old line filter and the writer must refuse, naming the key it would have damaged."""
    write_file(repo, "src/keep.py", "k = 1\n")
    write_file(repo, "src/dep.py", "v = 1\n")
    git_commit(repo, "deps", 1_700_000_000)
    target = write_file(repo, ".claude/memory/m.md", _BLOCK_META)
    git_commit(repo, "memory", 1_700_000_001)
    on_disk_before = open(target, encoding="utf-8").read()

    monkeypatch.setattr(P, "_strip_provenance", _pre_cor9_line_filter)
    repo_files, basename_index = P.build_repo_file_index(repo)
    res = P.backfill_file(target, repo, repo_files, basename_index, refresh=True)

    assert res["error"] is not None, "the pre-COR-9 fold must not be written"
    assert "refusing to write" in res["error"]
    assert "last_verified" in res["error"]  # the guard NAMES the damaged key
    assert res["changed"] is False
    assert open(target, encoding="utf-8").read() == on_disk_before  # untouched


def test_writer_refuses_rather_than_emit_unparseable_frontmatter(repo, memory_dir, monkeypatch):
    """AC (COR-9), the LOUD half: when the orphans do break the parse, refuse too."""
    write_file(repo, "src/keep.py", "k = 1\n")
    git_commit(repo, "dep", 1_700_000_000)
    # `metadata:` ending in the block list, with no trailing sibling key — the orphans land
    # under `last_verified` and the re-inserted keys land inside the folded scalar's run.
    text = (
        "---\nname: M\nmetadata:\n  type: project\n  last_verified: 2026-07-01\n"
        "  cited_paths:\n    - src/keep.py\n---\nbody cites src/keep.py\n"
    )
    target = write_file(repo, ".claude/memory/m.md", text)
    git_commit(repo, "memory", 1_700_000_001)
    on_disk_before = open(target, encoding="utf-8").read()

    monkeypatch.setattr(P, "_strip_provenance", _pre_cor9_line_filter)
    repo_files, basename_index = P.build_repo_file_index(repo)
    res = P.backfill_file(target, repo, repo_files, basename_index, refresh=True)

    assert res["error"] is not None and "refusing to write" in res["error"]
    assert res["changed"] is False
    assert open(target, encoding="utf-8").read() == on_disk_before  # untouched


def test_every_writer_preserves_parseability_across_the_corpus_shapes(repo, memory_dir):
    """Property (COR-9): for every frontmatter shape the corpus uses, a file whose
    frontmatter parses BEFORE any writer must parse AFTER it."""
    write_file(repo, "src/keep.py", "k = 1\n")
    write_file(repo, "src/dep.py", "v = 1\n")
    git_commit(repo, "deps", 1_700_000_000)
    shapes = {
        "block-flat": _BLOCK_FLAT,
        "block-meta": _BLOCK_META,
        "flow-flat": '---\nname: M\ncited_paths: ["src/keep.py"]\n---\nbody src/keep.py\n',
        "flow-meta": '---\nname: M\nmetadata:\n  type: project\n  cited_paths: ["src/keep.py"]\n'
        "---\nbody src/keep.py\n",
        "same-indent-seq": "---\nname: M\ncited_paths:\n- src/keep.py\n---\nbody src/keep.py\n",
        "no-provenance": "---\nname: M\ntype: project\n---\nbody src/keep.py\n",
    }
    for label, text in shapes.items():
        target = write_file(repo, f".claude/memory/{label}.md", text)
    git_commit(repo, "memories", 1_700_000_001)
    repo_files, basename_index = P.build_repo_file_index(repo)

    for label in shapes:
        target = os.path.join(memory_dir, f"{label}.md")
        for writer in ("refresh", "reverify"):
            if writer == "refresh":
                res = P.backfill_file(target, repo, repo_files, basename_index, refresh=True)
            else:
                res = P.reverify_file(target, repo, repo_files, basename_index)
            assert res["error"] is None, f"{label}/{writer}: {res['error']}"
            after = P.parse_frontmatter(open(target, encoding="utf-8").read())
            assert after, f"{label}/{writer} emitted unparseable frontmatter"


# --------------------------------------------------------------------------- #
# LIF-4 — the rot line reports the cause it MEASURED
# --------------------------------------------------------------------------- #
def test_partition_dropped_splits_on_repo_membership():
    gone, not_derived = P.partition_dropped(
        ["src/deleted.py", "src/present.py"], {"src/present.py"}
    )
    assert gone == ["src/deleted.py"]
    assert not_derived == ["src/present.py"]


def test_rot_line_says_no_longer_in_the_repo_only_for_paths_that_are_gone():
    """LIF-3's original case still reads exactly as it did — that phrase is EARNED here."""
    res = {
        "cited": ["src/keep.py"],
        "dropped_citations": ["src/dep.py"],
        "dropped_gone": ["src/dep.py"],
        "dropped_not_derived": [],
    }
    (line,) = P.citation_rot_lines("m.md", res)
    assert "no longer in the repo" in line
    assert "not derived" not in line
    assert "1 citation(s) remain" in line


def test_rot_line_does_not_claim_deletion_for_a_path_still_in_the_repo():
    """AC (LIF-4): the em-growth-labs failure. `dropped` was a set-difference against the
    re-derived list — a membership test that never ran — so a citation the extractor merely
    failed to produce was reported as a deleted file, sending the reader to hunt a rename
    that never happened. The file IS in the repo; say so."""
    res = {
        "cited": ["src/keep.py"],
        "dropped_citations": ["Dockerfile", "package.json"],
        "dropped_gone": [],
        "dropped_not_derived": ["Dockerfile", "package.json"],
    }
    (line,) = P.citation_rot_lines("m.md", res)
    assert "no longer in the repo" not in line, "the files are RIGHT THERE"
    assert "still in the repo" in line
    assert "Dockerfile" in line and "package.json" in line


def test_rot_line_renders_both_causes_when_a_drop_has_both():
    res = {
        "cited": ["src/keep.py"],
        "dropped_citations": ["src/deleted.py", "package.json"],
        "dropped_gone": ["src/deleted.py"],
        "dropped_not_derived": ["package.json"],
    }
    (line,) = P.citation_rot_lines("m.md", res)
    assert "no longer in the repo (src/deleted.py)" in line
    assert "still in the repo" in line and "package.json" in line


def test_rot_line_drop_to_zero_keeps_the_exempt_warning_and_the_true_cause():
    """The drop-to-zero branch carried the SAME unearned phrase as the partial branch.
    Both are fixed; the EXEMPT warning (verified accurate) is preserved."""
    res = {
        "cited": [],
        "dropped_citations": ["package.json"],
        "dropped_gone": [],
        "dropped_not_derived": ["package.json"],
    }
    (line,) = P.citation_rot_lines("m.md", res)
    assert "EXEMPT" in line  # the worst rot state is still called out
    assert "no longer in the repo" not in line  # ...but not with a fabricated cause
    assert "still in the repo" in line


def test_rot_line_falls_back_to_gone_for_a_producer_without_the_partition():
    """Back-compat: a result dict predating LIF-4 renders as it always did, rather than
    silently claiming everything is `not_derived`."""
    res = {"cited": ["a.py"], "dropped_citations": ["b.py"]}
    (line,) = P.citation_rot_lines("m.md", res)
    assert "no longer in the repo" in line


def test_refresh_partitions_a_real_not_derived_drop_end_to_end(repo, memory_dir):
    """AC (LIF-4) on the live producer: a memory whose frontmatter cites a file the
    extractor cannot derive from the body (`Dockerfile` — no dotted extension) must report
    it as not_derived, NOT as gone. Both files exist at HEAD throughout."""
    write_file(repo, "Dockerfile", "FROM scratch\n")
    write_file(repo, "src/keep.py", "k = 1\n")
    git_commit(repo, "code", 1_700_000_000)
    target = write_file(
        repo,
        ".claude/memory/m.md",
        '---\nname: M\ncited_paths: ["Dockerfile", "src/keep.py"]\nsource_commit: "abc"\n'
        "---\nbody cites src/keep.py and the Dockerfile\n",
    )
    git_commit(repo, "memory", 1_700_000_001)

    repo_files, basename_index = P.build_repo_file_index(repo)
    res = P.backfill_file(target, repo, repo_files, basename_index, refresh=True)
    assert res["error"] is None
    assert res["dropped_citations"] == ["Dockerfile"]
    assert res["dropped_gone"] == [], "Dockerfile is right there — it was never deleted"
    assert res["dropped_not_derived"] == ["Dockerfile"]
    (line,) = P.citation_rot_lines("m.md", res)
    assert "no longer in the repo" not in line


# --------------------------------------------------------------------------- #
# ORC-1 — the extractor's declared config is its contract
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ext", P._CODE_EXTS)
def test_every_declared_extension_is_reachable(ext):
    """THE test that would have caught this on day one, and the reason it is data-driven:
    adding an entry to _CODE_EXTS auto-tests its own reachability.

    Before ORC-1, `tsx`, `jsx` and `json` were DECLARED in _CODE_EXTS and structurally
    unreachable — each sat after its own prefix (`ts`, `js`) in a first-match-wins
    alternation with no trailing boundary, so `App.tsx` extracted as `App.ts` and
    `package.json` as `package.js`. Config the regex could not deliver.

    The pre-existing extraction test used only `.py` — _CODE_EXTS[0], structurally
    unshadowable — and asserted membership (`in`), so a fabricated extra token was invisible
    to it by construction. That is why 14 minor versions shipped over this.
    """
    tok = f"src/x.{ext}"
    assert P.extract_citations(f"the fix is in {tok} somewhere") == [tok]


@pytest.mark.parametrize(
    "token",
    ["build.pyc", "types.pyi", "data.jsonl", "x.tsv", "notes.shtml", "conf.inix"],
)
def test_truncation_family_fabricates_nothing(token):
    """With no trailing boundary the regex matched a PREFIX of the real extension and
    completed, inventing a path nobody wrote: data.jsonl -> data.js, build.pyc -> build.py.
    Worse than a drop — when the invention names a real sibling, resolve_citations keeps it
    and binds the memory to the wrong file (see the DRV-1 extension check)."""
    assert P.extract_citations(f"see {token} for details") == []


def test_reordering_alone_would_not_have_fixed_the_truncation_family():
    """Pins WHY the fix is the boundary and not a sort. _CODE_EXTS is sorted longest-first
    as intent-preservation, but sorting is not what does the work: rebuild the pattern
    WITHOUT the boundary, keeping today's longest-first order, and the truncation family
    fabricates again — and `data.jsonl -> data.json` is WORSE than the old `data.js`,
    because data.json is far likelier to be a real file, turning a silent drop into a
    silent wrong-binding."""
    import re as _re

    no_boundary = _re.compile(
        r"(?<![\w./-])((?:[\w.-]+/)*[\w.-]+\.(?:" + "|".join(P._CODE_EXTS) + r"))(?::\d+(?:-\d+)?)?"
    )
    m = no_boundary.search("see data.jsonl for details")
    assert m and m.group(1) == "data.json"  # sorted longest-first, still fabricating
    assert P.extract_citations("see data.jsonl for details") == []  # the boundary is the fix


def test_compiled_pattern_carries_a_trailing_boundary():
    """Source-level pin: the boundary is the whole fix, so make deleting it loud."""
    assert r"(?![\w])" in P._CITATION_RE.pattern


def test_boundary_does_not_regress_prose_or_line_suffixes():
    """The tail is deliberately `(?![\\w])`, NOT `(?![\\w./-])` mirroring the lookbehind.
    The symmetric form reads right and breaks a sentence-ending citation."""
    assert P.extract_citations("the bug is in foo.py.") == ["foo.py"]  # end-of-sentence period
    assert P.extract_citations("see `qux.py:3` in passing") == ["qux.py"]
    assert P.extract_citations("bar.py:5-9 and bar.py:99") == ["bar.py"]
    assert P.extract_citations("[scorer](src/q/scorer.py)") == ["src/q/scorer.py"]


def test_mjs_family_is_covered():
    """BUG C is orthogonal to the boundary — a membership gap, not an assertion gap. The
    enforcement chain this repo's own memories cite (scripts/*.mjs) was uncitable."""
    body = "the mirror is scripts/brand_redirect_stubs.mjs and scripts/check_import_graph.mjs"
    assert P.extract_citations(body) == [
        "scripts/brand_redirect_stubs.mjs",
        "scripts/check_import_graph.mjs",
    ]


def test_extensionless_files_are_still_not_citable():
    """Honest scope pin: BUG B (Dockerfile/Makefile) is NOT fixed by ORC-1 — the token
    shape requires a dotted extension. LIF-4 now reports these as `not_derived` rather
    than claiming they left the repo, which is the accurate statement of this gap."""
    assert P.extract_citations("the Dockerfile mirrors the Makefile") == []


def test_resolve_normalises_a_leading_dot_slash(repo):
    """ORC-1: git ls-files never emits `./`, so `./src/a/dup.py` missed the exact match and
    fell through to the basename fallback — which drops ambiguous basenames. A citation
    written MORE precisely resolved WORSE than a bare one."""
    repo_files = {"src/a/dup.py", "src/b/dup.py"}
    index = {"dup.py": ["src/a/dup.py", "src/b/dup.py"]}  # ambiguous basename
    assert P.resolve_citations(["./src/a/dup.py"], repo_files, index) == ["src/a/dup.py"]
    # the bare ambiguous basename is still correctly dropped
    assert P.resolve_citations(["dup.py"], repo_files, index) == []
