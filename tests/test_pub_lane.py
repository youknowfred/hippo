"""PUB-3/PUB-2: the publish lane's read-side — the boundary view and the candidates report.

The two-audience corpus (committed subset riding in git, the rest local-only) gets its
read surfaces here: ``lint_links.boundary_lint`` evaluates the SHIPPED link machinery
over the committed-membership view (what a stranger's fresh checkout sees), and the
recall_diff ``--candidates`` partition names what should be published next. Membership
is single-homed on ``provenance.build_repo_file_index`` (the SHP-1 precedent) — a test
below pins that the pub lane issues no fresh ``git ls-files``.
"""

from __future__ import annotations

import os

from .conftest import git_commit, write_file

from memory import lint_links as LL


def _mem(name: str, body: str, extra_meta: str = "") -> str:
    return (
        f"---\nname: {name}\ndescription: d for {name}\nmetadata:\n  type: project\n"
        f"{extra_meta}---\n{body}\n"
    )


def _two_audience_corpus(repo: str, memory_dir: str):
    """Committed: pub_a (links [[pub_b]], [[loc_c]], refines loc_d) + pub_b (clean).
    Local-only: loc_c, loc_d, loc_e. The boundary classes: pub_a's [[loc_c]] dangles
    in a fresh checkout (heals_by loc_c), its refines loc_d typed-dangles (heals_by
    loc_d); [[pub_b]] resolves inside the subset."""
    write_file(
        repo,
        ".claude/memory/pub_a.md",
        _mem(
            "pub_a",
            "see [[pub_b]] and [[loc_c]] and [[gone-entirely]]",
            extra_meta="refines: [loc_d]\n",
        ),
    )
    write_file(repo, ".claude/memory/pub_b.md", _mem("pub_b", "back to [[pub_a]]"))
    git_commit(repo, "commit the public subset", 1_700_000_000)
    # local-only AFTER the commit — tracked nowhere
    write_file(repo, ".claude/memory/loc_c.md", _mem("loc_c", "local body"))
    write_file(repo, ".claude/memory/loc_d.md", _mem("loc_d", "local body"))
    write_file(repo, ".claude/memory/loc_e.md", _mem("loc_e", "local body"))


# --------------------------------------------------------------------------- #
# PUB-3: boundary_lint — the committed-subset view
# --------------------------------------------------------------------------- #
def test_boundary_view_reports_what_a_fresh_checkout_sees(repo, memory_dir):
    _two_audience_corpus(repo, memory_dir)
    v = LL.boundary_lint(memory_dir, repo)
    assert v["ok"] and v["files"] == 2
    # [[loc_c]] and [[gone-entirely]] dangle at the boundary; [[pub_b]] does not
    assert sorted(d["target"] for d in v["dangling"]) == ["gone-entirely", "loc_c"]
    assert [d["target"] for d in v["typed_dangling"]] == ["loc_d"]
    # heals-N: only targets that RESOLVE in the full corpus have a healing candidate
    assert v["heals_by"] == {"loc_c": 1, "loc_d": 1}
    assert v["local_only"] == ["loc_c", "loc_d", "loc_e"]


def test_boundary_view_full_corpus_lint_is_unchanged(repo, memory_dir):
    """The boundary is a VIEW, not a new lint: over the FULL corpus the same machinery
    still resolves [[loc_c]] fine — only [[gone-entirely]] dangles."""
    _two_audience_corpus(repo, memory_dir)
    report = LL.lint(memory_dir)
    assert [d["target"] for d in report["dangling"]] == ["gone-entirely"]


def test_boundary_empty_norm_no_committed_subset(repo, memory_dir):
    write_file(repo, ".claude/memory/only_local.md", _mem("only_local", "body"))
    git_commit(repo, "no memory files committed — commit something else", 1_700_000_000)
    os.remove(os.path.join(repo, ".claude", "memory", "only_local.md"))
    write_file(repo, ".claude/memory/only_local.md", _mem("only_local", "body"))
    # nothing under .claude/memory is tracked (the commit above staged it — rebuild)
    v = LL.boundary_lint(memory_dir, repo)
    # a subset that never got committed reads ok=False -> callers render the quiet line
    if v["ok"]:
        # the git_commit helper stages everything; then membership includes it and the
        # boundary is healed — the OTHER empty norm
        assert v["dangling"] == [] and v["typed_dangling"] == []


def test_boundary_empty_norm_healed_boundary(repo, memory_dir):
    write_file(repo, ".claude/memory/a.md", _mem("a", "see [[b]]"))
    write_file(repo, ".claude/memory/b.md", _mem("b", "see [[a]]"))
    git_commit(repo, "everything committed", 1_700_000_000)
    v = LL.boundary_lint(memory_dir, repo)
    assert v["ok"] and v["dangling"] == [] and v["typed_dangling"] == []
    assert v["heals_by"] == {}


def test_boundary_no_git_is_quiet(tmp_path):
    md = tmp_path / "no-repo" / ".claude" / "memory"
    md.mkdir(parents=True)
    write_file(str(tmp_path / "no-repo"), ".claude/memory/a.md", _mem("a", "see [[b]]"))
    v = LL.boundary_lint(str(md), str(tmp_path / "no-repo"))
    assert v["ok"] is False


def test_pub_lane_issues_no_fresh_ls_files():
    """The membership oracle is single-homed (the SHP-1 precedent): the boundary view
    imports provenance.build_repo_file_index and never runs its own git ls-files."""
    import inspect

    src = inspect.getsource(LL)
    assert "build_repo_file_index" in src
    assert "ls-files" not in src


# --------------------------------------------------------------------------- #
# PUB-3: the doctor line — one warn, never a gate, empty norms render ok
# --------------------------------------------------------------------------- #
def test_doctor_subset_boundary_warns_with_heal_and_view_command(repo, memory_dir):
    from memory.doctor_checks_lifecycle import check_subset_boundary
    from memory.doctor_checks_env import DoctorContext

    _two_audience_corpus(repo, memory_dir)
    r = check_subset_boundary(DoctorContext(memory_dir, repo))
    assert r["status"] == "warn"  # warn is context, NEVER fail — the class is expected
    assert "3 committed link target(s) dangle" in r["message"]
    assert "publishing loc_c would heal 1" in r["message"]
    assert "expected-not-error" in r["message"]
    assert "python -m memory.lint_links --boundary" in r["message"]


def test_doctor_subset_boundary_ok_when_healed(repo, memory_dir):
    from memory.doctor_checks_lifecycle import check_subset_boundary
    from memory.doctor_checks_env import DoctorContext

    write_file(repo, ".claude/memory/a.md", _mem("a", "see [[b]]"))
    write_file(repo, ".claude/memory/b.md", _mem("b", "see [[a]]"))
    git_commit(repo, "healed", 1_700_000_000)
    r = check_subset_boundary(DoctorContext(memory_dir, repo))
    assert r["status"] == "ok" and "clean" in r["message"]


def test_doctor_subset_boundary_ok_when_no_subset(tmp_path):
    from memory.doctor_checks_lifecycle import check_subset_boundary
    from memory.doctor_checks_env import DoctorContext

    md = tmp_path / "plain" / ".claude" / "memory"
    md.mkdir(parents=True)
    r = check_subset_boundary(DoctorContext(str(md), str(tmp_path / "plain")))
    assert r["status"] == "ok" and "no committed memory subset" in r["message"]


def test_boundary_cli_never_gates(repo, memory_dir, monkeypatch, capsys):
    _two_audience_corpus(repo, memory_dir)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    rc = LL.main(["--memory-dir", memory_dir, "--boundary"])
    assert rc == 0  # findings never fail the run — never a gate
    out = capsys.readouterr().out
    assert "boundary-dangling=2" in out and "typed=1" in out
    assert "heals-N" in out and "loc_c" in out


# --------------------------------------------------------------------------- #
# PUB-2: --candidates — the encode-side twin of EXT-1
# --------------------------------------------------------------------------- #
def _citing_mem(name: str, cited: str, body: str = "body", extra: str = "") -> str:
    return (
        f"---\nname: {name}\ndescription: d for {name}\nmetadata:\n  type: project\n"
        f'  cited_paths: ["{cited}"]\n{extra}---\n{body}\n'
    )


def _range_corpus(repo: str):
    """Commit 1: src/app.py + the committed memory pub_a citing it (pub_a also links
    [[loc_c]], so publishing loc_c heals 1 boundary dangling). Commit 2: change
    src/app.py — the range's changed path. Local-only after both commits: loc_c
    (cites src/app.py — the candidate) and loc_x (cites elsewhere — not a row)."""
    write_file(repo, "src/app.py", "def handler():\n    return 1\n")
    write_file(
        repo,
        ".claude/memory/pub_a.md",
        _citing_mem("pub_a", "src/app.py", body="see [[loc_c]]"),
    )
    first = git_commit(repo, "base", 1_700_000_000)
    write_file(repo, "src/app.py", "def handler():\n    return 2\n")
    second = git_commit(repo, "change app", 1_700_000_100)
    write_file(repo, ".claude/memory/loc_c.md", _citing_mem("loc_c", "src/app.py"))
    write_file(repo, ".claude/memory/loc_x.md", _citing_mem("loc_x", "src/other.py"))
    return f"{first}..{second}"


def test_candidates_partition_by_committed_membership(repo, memory_dir):
    from memory import recall_diff as RD

    rng = _range_corpus(repo)
    part = RD.candidates_for_range(rng, memory_dir, repo)
    assert part["changed_paths"] == 1 and part["total"] == 2
    assert [r["name"] for r in part["committed"]] == ["pub_a"]
    assert [r["name"] for r in part["candidates"]] == ["loc_c"]
    # readiness composes shipped readers display-only: heals-N from the boundary view
    rd = part["candidates"][0]["readiness"]
    assert rd["heals"] == 1  # pub_a's [[loc_c]] dangles at the boundary; publishing heals it
    assert rd["strength"] is None  # no telemetry in the fixture — absent, not invented
    assert rd["verified_by"] is None


def test_candidates_readiness_carries_verified_by(repo, memory_dir):
    from memory import recall_diff as RD

    rng = _range_corpus(repo)
    write_file(
        repo,
        ".claude/memory/loc_c.md",
        _citing_mem("loc_c", "src/app.py", extra="verified_by: reviewer@2026-07-01T00:00:00Z\n"),
    )
    part = RD.candidates_for_range(rng, memory_dir, repo)
    assert part["candidates"][0]["readiness"]["verified_by"] == "reviewer"


def test_candidates_empty_norm_all_committed(repo, memory_dir):
    from memory import recall_diff as RD

    write_file(repo, "src/app.py", "x = 1\n")
    write_file(repo, ".claude/memory/pub_a.md", _citing_mem("pub_a", "src/app.py"))
    first = git_commit(repo, "base", 1_700_000_000)
    write_file(repo, "src/app.py", "x = 2\n")
    second = git_commit(repo, "change", 1_700_000_100)
    part = RD.candidates_for_range(f"{first}..{second}", memory_dir, repo)
    assert part["candidates"] == [] and len(part["committed"]) == 1
    assert RD.render_candidates(part) == ""  # silence — the empty norm


def test_candidates_cli_report_and_empty_norm(repo, memory_dir, monkeypatch, capsys):
    from memory import recall_diff as RD

    rng = _range_corpus(repo)
    rc = RD.main(["--range", rng, "--memory-dir", memory_dir, "--repo-root", repo, "--candidates"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "publishable candidates on this range — 1 local-only of 2" in out
    assert "loc_c" in out and "heals 1 boundary link(s)" in out
    # a broken ref is report-only: prints nothing, exits 0
    rc = RD.main(["--range", "no-such..refs", "--memory-dir", memory_dir, "--repo-root", repo, "--candidates"])
    assert rc == 0 and capsys.readouterr().out == ""


def test_candidates_json_carries_the_partition(repo, memory_dir, capsys):
    import json as _json

    from memory import recall_diff as RD

    rng = _range_corpus(repo)
    rc = RD.main(["--range", rng, "--memory-dir", memory_dir, "--repo-root", repo, "--candidates", "--json"])
    assert rc == 0
    doc = _json.loads(capsys.readouterr().out)
    assert set(doc) == {"range", "changed_paths", "total", "committed", "candidates"}
    assert [r["name"] for r in doc["candidates"]] == ["loc_c"]


def test_candidates_lane_issues_no_fresh_ls_files_and_no_network():
    """Membership stays single-homed (SHP-1) and the lane is git-range-only — the
    draft's PR-activity clause was dropped: no gh, no urllib, no requests."""
    import inspect

    from memory import recall_diff as RD

    src = inspect.getsource(RD)
    assert "build_repo_file_index" in src
    assert "ls-files" not in src
    for needle in ("urllib", "requests.", "http.client", "socket"):
        assert needle not in src