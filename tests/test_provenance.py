"""Tests for memory/provenance.py — citation extraction + body-preserving backfill."""

from __future__ import annotations

import os

from memory import provenance as P
from memory.staleness import read_provenance, read_source_commit_time

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

    monkeypatch.delenv("MEMOBOT_MEMORY_DIR", raising=False)
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

    monkeypatch.delenv("MEMOBOT_MEMORY_DIR", raising=False)
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

    monkeypatch.delenv("MEMOBOT_MEMORY_DIR", raising=False)
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
    monkeypatch.setenv("MEMOBOT_MEMORY_DIR", explicit_md)

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
    assert P.encode_project_dir("/Users/fredbook/GitHub/hippo") == "-Users-fredbook-GitHub-hippo"


def test_encode_project_dir_dotted_path_replaces_every_dot():
    # Confirmed empirically: '/' AND '.' are each replaced 1-for-1, never collapsed.
    assert P.encode_project_dir("/Users/x/dev/next.js-app") == "-Users-x-dev-next-js-app"


def test_encode_project_dir_underscored_path_replaces_underscore():
    assert P.encode_project_dir("/Users/x/dev/sdk_2.0") == "-Users-x-dev-sdk-2-0"


def test_encode_project_dir_consecutive_punctuation_produces_consecutive_hyphens():
    # "/." (slash then dot) before "claude" -> TWO hyphens, not collapsed to one.
    path = "/Users/fredbook/Documents/GitHub/ic-memobot/.claude/memory"
    expected = "-Users-fredbook-Documents-GitHub-ic-memobot--claude-memory"
    assert P.encode_project_dir(path) == expected


def test_encode_project_dir_existing_hyphens_pass_through_unchanged():
    path = "/Users/fredbook/Documents/GitHub/ic-memobot/canvas-ui/src"
    expected = "-Users-fredbook-Documents-GitHub-ic-memobot-canvas-ui-src"
    assert P.encode_project_dir(path) == expected


def test_encode_project_dir_keeps_leading_hyphen_no_strip():
    assert P.encode_project_dir("/a").startswith("-")


def test_legacy_encode_project_dir_differs_from_fixed_only_when_punctuation_present():
    plain = "/Users/fredbook/GitHub/hippo"
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
