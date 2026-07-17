"""RCH-2 — /hippo:import, Cursor .mdc adapter: foreign rules become ranked, deduped,
secret-linted memories.

Hermetic: a real scratch git repo with a ``.cursor/rules`` fixture; BM25-only. Pins the
acceptance criteria — globs land as cited_paths via the shipped backfill, the dup gate
holds a re-statement, a secret-bearing .mdc is HELD (never written, never recallable) —
plus the premise correction this item shipped on: real Cursor frontmatter is NOT valid
YAML (``globs: **/*.ts`` — a bare ``*`` is a YAML alias), so the adapter's tolerant
line-based fallback is what makes the dominant real-world shape parse at all.
"""

from __future__ import annotations

import os
import subprocess

from memory import import_mdc as I
from memory import new_memory as N
from memory.provenance import parse_frontmatter
from memory.staleness import read_provenance

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def _repo(tmp_path, monkeypatch):
    """A committed scratch repo with code files + a .cursor/rules dir + a memory dir."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo = str(tmp_path / "repo")
    os.makedirs(os.path.join(repo, "src"))
    for rel in ("src/app.py", "src/util.py"):
        with open(os.path.join(repo, rel), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
    with open(os.path.join(repo, "notes.txt"), "w", encoding="utf-8") as fh:
        fh.write("not code\n")
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", repo, "commit", "-q", "-m", "seed"], check=True, env=_GIT_ENV
    )
    rules = os.path.join(repo, ".cursor", "rules")
    os.makedirs(rules)
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    return repo, rules, md


def _mdc(rules_dir: str, fname: str, text: str) -> str:
    path = os.path.join(rules_dir, fname)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


# --------------------------------------------------------------------------- #
# parse_mdc — the tolerant parser (the shipped premise correction)
# --------------------------------------------------------------------------- #
def test_parse_mdc_bare_star_globs_survive_invalid_yaml():
    # `globs: **/*.py` is NOT valid YAML (bare * = alias) — parse_frontmatter yields {}
    # for the whole block; the line-based fallback must recover every field.
    text = "---\ndescription: python service rules\nglobs: **/*.py\nalwaysApply: false\n---\n\nUse type hints.\n"
    got = I.parse_mdc(text)
    assert got["description"] == "python service rules"
    assert got["globs"] == ["**/*.py"]
    assert got["always_apply"] is False
    assert got["body"] == "Use type hints.\n"


def test_parse_mdc_comma_separated_globs_split():
    text = "---\ndescription: x\nglobs: src/**/*.py,lib/*.py\n---\nbody\n"
    assert I.parse_mdc(text)["globs"] == ["src/**/*.py", "lib/*.py"]


def test_parse_mdc_yaml_list_and_quoted_forms():
    text = '---\ndescription: "quoted: with colon"\nglobs:\n  - src/**/*.py\n  - "lib/*.py"\n---\nbody\n'
    got = I.parse_mdc(text)
    assert got["description"] == "quoted: with colon"
    assert got["globs"] == ["src/**/*.py", "lib/*.py"]


def test_parse_mdc_always_apply_true_and_no_frontmatter():
    assert I.parse_mdc("---\ndescription: x\nalwaysApply: true\n---\nb\n")["always_apply"]
    got = I.parse_mdc("just a body, no frontmatter\n")
    assert got["description"] == "" and got["globs"] == []
    assert got["body"] == "just a body, no frontmatter\n"


# --------------------------------------------------------------------------- #
# resolve_globs — rules_plane's match pipeline over the real repo universe
# --------------------------------------------------------------------------- #
def test_resolve_globs_matches_tracked_and_untracked_not_ignored(tmp_path, monkeypatch):
    repo, _, _ = _repo(tmp_path, monkeypatch)
    with open(os.path.join(repo, "src", "new_untracked.py"), "w", encoding="utf-8") as fh:
        fh.write("y = 2\n")
    with open(os.path.join(repo, ".gitignore"), "w", encoding="utf-8") as fh:
        fh.write("src/ignored.py\n")
    with open(os.path.join(repo, "src", "ignored.py"), "w", encoding="utf-8") as fh:
        fh.write("z = 3\n")
    got = I.resolve_globs(repo, ["src/**/*.py"])
    assert "src/app.py" in got and "src/util.py" in got
    assert "src/new_untracked.py" in got  # untracked-but-not-ignored is alive
    assert "src/ignored.py" not in got
    assert I.resolve_globs(repo, ["src/*.{py,txt}"]) >= ["src/app.py"]  # brace expansion
    assert I.resolve_globs(repo, []) == []


# --------------------------------------------------------------------------- #
# import_mdc_file — the per-item write leg
# --------------------------------------------------------------------------- #
def test_import_lands_globs_as_cited_paths_via_backfill(tmp_path, monkeypatch):
    repo, rules, md = _repo(tmp_path, monkeypatch)
    path = _mdc(
        rules, "python-style.mdc",
        "---\ndescription: python style conventions for services\nglobs: src/**/*.py\n---\n\nPrefer explicit imports over star imports.\n",
    )
    r = I.import_mdc_file(path, memory_dir=md, repo_root=repo)
    assert r["error"] is None and r["imported"] is True
    assert r["slug"] == "python-style"
    text = open(r["path"], encoding="utf-8").read()
    assert "Applies to: src/app.py, src/util.py" in text
    cited, sc = read_provenance(text)
    assert set(cited) == {"src/app.py", "src/util.py"}  # globs -> cited_paths, shipped path
    assert sc  # born staleness-tracked
    fm = parse_frontmatter(text)
    assert fm["metadata"]["type"] == "project"
    assert "imported from .cursor/rules/python-style.mdc" in text  # GOV-3 rationale line


def test_reimport_rides_the_exclusive_create_refusal(tmp_path, monkeypatch):
    repo, rules, md = _repo(tmp_path, monkeypatch)
    path = _mdc(rules, "r.mdc", "---\ndescription: d\nglobs: src/*.py\n---\nbody\n")
    assert I.import_mdc_file(path, memory_dir=md, repo_root=repo)["imported"]
    again = I.import_mdc_file(path, memory_dir=md, repo_root=repo)
    assert not again["imported"]
    assert "already exists" in again["error"] and "idempotent" in again["error"]


def test_secret_bearing_mdc_is_held_and_never_written(tmp_path, monkeypatch):
    repo, rules, md = _repo(tmp_path, monkeypatch)
    fake_key = "AKIAIOSFODNN7EXAMPLE"  # the canonical AWS docs placeholder
    path = _mdc(
        rules, "creds.mdc",
        f"---\ndescription: deployment rule\n---\nuse key {fake_key} for deploys\n",
    )
    r = I.import_mdc_file(path, memory_dir=md, repo_root=repo)
    assert r["held"] is True and not r["imported"]
    assert any("AWS access key" in w for w in r["warnings"])
    assert not os.path.exists(os.path.join(md, "creds.md"))  # never written -> never recallable
    # and there is no override parameter for the secret hold, by design
    import inspect

    assert "allow_secret" not in inspect.signature(I.import_mdc_file).parameters


def test_duplicate_route_holds_until_explicitly_allowed(tmp_path, monkeypatch):
    repo, rules, md = _repo(tmp_path, monkeypatch)
    # Seed a corpus twin + enough distinct-vocab docs that BM25 idf never zeroes out
    # (the GRW-3 gotcha: a shared-vocab pair at df=2 in a tiny corpus scores 0).
    seeds = {
        "twin": "prefer explicit imports over star imports in python services",
        "a": "the deploy pipeline caches artifacts by content hash",
        "b": "widget rendering uses the legacy canvas fallback",
        "c": "the queue consumer retries with exponential backoff",
        "d": "release notes are generated from conventional commits",
        "e": "database migrations run in a transaction per file",
    }
    for stem, desc in seeds.items():
        N.write_memory(stem, desc, "project", "body", memory_dir=md, repo_root=repo,
                       no_links=True)
    path = _mdc(
        rules, "twin-rule.mdc",
        "---\ndescription: prefer explicit imports over star imports in python services\n---\n\nprefer explicit imports over star imports in python services\n",
    )
    r = I.import_mdc_file(path, memory_dir=md, repo_root=repo)
    assert r["held"] and not r["imported"] and r["route"] == "review"
    assert any(n["name"] == "twin" for n in r["neighbors"])
    assert not os.path.exists(os.path.join(md, "twin-rule.md"))
    r2 = I.import_mdc_file(path, memory_dir=md, repo_root=repo, allow_duplicate=True)
    assert r2["imported"], r2["error"]


def test_import_missing_file_and_bad_type_are_errors(tmp_path, monkeypatch):
    repo, _, md = _repo(tmp_path, monkeypatch)
    assert "not found" in I.import_mdc_file(
        os.path.join(repo, "ghost.mdc"), memory_dir=md, repo_root=repo
    )["error"]
    assert "invalid type" in I.import_mdc_file(
        os.path.join(repo, "ghost.mdc"), memory_dir=md, repo_root=repo, mtype="nope"
    )["error"]


# --------------------------------------------------------------------------- #
# import_candidates — the read-only report the skill walks
# --------------------------------------------------------------------------- #
def test_candidates_report_is_read_only_and_complete(tmp_path, monkeypatch):
    repo, rules, md = _repo(tmp_path, monkeypatch)
    _mdc(rules, "one.mdc",
         "---\ndescription: python conventions\nglobs: src/**/*.py\nalwaysApply: true\n---\nbody one\n")
    fake_key = "AKIAIOSFODNN7EXAMPLE"
    _mdc(rules, "two.mdc", f"---\ndescription: risky\n---\nkey {fake_key}\n")
    before = sorted(os.listdir(md))
    got = I.import_candidates(repo_root=repo, memory_dir=md)
    assert sorted(os.listdir(md)) == before  # read-only
    assert [c["slug"] for c in got] == ["one", "two"]
    one, two = got
    assert one["paths_matched"] == 2 and one["always_apply"] is True
    assert one["globs"] == ["src/**/*.py"] and one["exists"] is False
    assert any("AWS access key" in w for w in two["secret_warnings"])


def test_candidates_flag_already_imported(tmp_path, monkeypatch):
    repo, rules, md = _repo(tmp_path, monkeypatch)
    path = _mdc(rules, "done.mdc", "---\ndescription: d\n---\nbody\n")
    assert I.import_mdc_file(path, memory_dir=md, repo_root=repo)["imported"]
    got = I.import_candidates(repo_root=repo, memory_dir=md)
    assert got[0]["slug"] == "done" and got[0]["exists"] is True


def test_slug_sanitization():
    assert I._slug_for("/x/001-General Rules.mdc") == "001-General-Rules"
    assert I._slug_for("/x/---.mdc") == "imported-rule"


# --------------------------------------------------------------------------- #
# IOP-2 — upstream fingerprint: the source .mdc rides the cited-paths route, so
# RET-6's shipped git-log staleness scan flags upstream drift AND deletion for free.
# --------------------------------------------------------------------------- #
_T0 = 1_700_000_000  # pinned epochs make the staleness ct-comparison deterministic
_ALL = "2000-01-01"  # explicit find_stale window covering the pinned epochs


def _commit(repo: str, message: str, when: int) -> None:
    iso = f"{int(when)} +0000"
    env = {**_GIT_ENV, "GIT_AUTHOR_DATE": iso, "GIT_COMMITTER_DATE": iso}
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", repo, "commit", "-q", "--allow-empty", "-m", message],
        check=True, env=env,
    )


def _pinned_repo(tmp_path, monkeypatch):
    """_repo's layout with EVERY commit epoch-pinned, so find_stale's `changed > base`
    comparison is deterministic (seed at _T0, the rule commit strictly after)."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo = str(tmp_path / "repo")
    os.makedirs(os.path.join(repo, "src"))
    for rel in ("src/app.py", "src/util.py"):
        with open(os.path.join(repo, rel), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
    subprocess.run(["git", "init", "-q", repo], check=True)
    _commit(repo, "seed", _T0)
    rules = os.path.join(repo, ".cursor", "rules")
    os.makedirs(rules)
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    return repo, rules, md


def test_import_fingerprints_a_tracked_source_mdc(tmp_path, monkeypatch):
    """AC: cited_paths includes the source .mdc's repo-relative path ALONGSIDE the
    glob-target paths — via the shipped body-line backfill, no new frontmatter field."""
    repo, rules, md = _pinned_repo(tmp_path, monkeypatch)
    path = _mdc(
        rules, "python-style.mdc",
        "---\ndescription: python style conventions for services\nglobs: src/**/*.py\n---\n\nPrefer explicit imports over star imports.\n",
    )
    _commit(repo, "track the rule", _T0 + 100)  # tracked source -> resolvable citation
    r = I.import_mdc_file(path, memory_dir=md, repo_root=repo)
    assert r["error"] is None and r["imported"] is True
    text = open(r["path"], encoding="utf-8").read()
    assert "Source: .cursor/rules/python-style.mdc" in text  # the fingerprint line
    cited, sc = read_provenance(text)
    assert ".cursor/rules/python-style.mdc" in cited
    assert {"src/app.py", "src/util.py"} <= set(cited)  # glob targets still there
    assert sc


def test_upstream_edit_flags_the_imported_memory_stale(tmp_path, monkeypatch):
    """AC (ED-3-mirrored hermetically): editing the source .mdc post-import makes
    find_stale flag the memory, with the .mdc named in changed_paths."""
    from memory.staleness import find_stale

    repo, rules, md = _pinned_repo(tmp_path, monkeypatch)
    path = _mdc(rules, "r.mdc", "---\ndescription: d\nglobs: src/*.py\n---\nold body\n")
    _commit(repo, "rule v1", _T0 + 100)
    r = I.import_mdc_file(path, memory_dir=md, repo_root=repo)
    assert r["imported"], r["error"]
    assert find_stale(md, repo, since=_ALL) == []  # freshly imported: nothing stale yet
    _mdc(rules, "r.mdc", "---\ndescription: d\nglobs: src/*.py\n---\nnew body\n")
    _commit(repo, "rule v2 (upstream drift)", _T0 + 600)
    stale = find_stale(md, repo, since=_ALL)
    assert [s["name"] for s in stale] == ["r"]
    assert stale[0]["changed_paths"] == [".cursor/rules/r.mdc"]


def test_upstream_delete_flags_the_imported_memory_stale(tmp_path, monkeypatch):
    """AC: deleting the source .mdc is picked up by the SAME git-log --name-only scan
    as a changed path — no separate orphaned-detection machinery."""
    from memory.staleness import find_stale

    repo, rules, md = _pinned_repo(tmp_path, monkeypatch)
    path = _mdc(rules, "gone.mdc", "---\ndescription: d\nglobs: src/*.py\n---\nbody\n")
    _commit(repo, "rule lands", _T0 + 100)
    r = I.import_mdc_file(path, memory_dir=md, repo_root=repo)
    assert r["imported"], r["error"]
    os.remove(path)
    _commit(repo, "rule deleted upstream", _T0 + 600)
    stale = find_stale(md, repo, since=_ALL)
    assert [s["name"] for s in stale] == ["gone"]
    assert stale[0]["changed_paths"] == [".cursor/rules/gone.mdc"]


def test_untracked_source_degrades_to_glob_targets_only(tmp_path, monkeypatch):
    """An UNTRACKED source .mdc can't resolve against git ls-files: the Source: line is
    still written (documentation), but cited_paths carries only the glob targets — the
    fingerprint quietly waits for the source to be committed."""
    repo, rules, md = _repo(tmp_path, monkeypatch)
    path = _mdc(
        rules, "floating.mdc",
        "---\ndescription: d\nglobs: src/*.py\n---\nbody\n",
    )
    r = I.import_mdc_file(path, memory_dir=md, repo_root=repo)  # .mdc never committed
    assert r["imported"], r["error"]
    text = open(r["path"], encoding="utf-8").read()
    assert "Source: .cursor/rules/floating.mdc" in text
    cited, _sc = read_provenance(text)
    assert set(cited) == {"src/app.py", "src/util.py"}
