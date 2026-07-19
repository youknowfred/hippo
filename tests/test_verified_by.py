"""CLB-2: verified_by attribution — per-verification identity, suppressed at solo.

Covers: the reverify-gate stamp (``verified_by: "<slug>@<own-ts>"`` refreshed on
every verdict; last_verified's write-once semantics untouched; body byte-identical;
everything-but-verified_by stays idempotent), the slug discipline (one shared
``slugify_identity`` for stamps AND git-log joins), the report-time consumers
(``verification_summary`` lights the dark last_verified read path for every corpus;
``team_coverage`` returns None at ≤1 git author so every team line is OMITTED, not
rendered empty — the solo scorecard renders byte-identically to pre-CLB-2), and the
NEW AST pin: ``verified_by`` is never a ranking input — recall's whole module family
plus build_index reference the key zero times, structurally.
"""

from __future__ import annotations

import ast
import inspect
import os
import subprocess

from .conftest import git_commit, write_file

from memory import provenance as P
from memory import team_coverage as TC


def _mem_text(name: str, cited: str = "src/dep.py") -> str:
    return (
        f'---\nname: {name}\ncited_paths: ["{cited}"]\nsource_commit: "OLD"\n---\n'
        f"body {cited}\n"
    )


def _reverify(memory_dir: str, repo: str, stem: str, *, dry_run: bool = False) -> dict:
    repo_files, basename_index = P.build_repo_file_index(repo)
    return P.reverify_file(
        os.path.join(memory_dir, f"{stem}.md"), repo, repo_files, basename_index, dry_run=dry_run
    )


def _commit_as(repo: str, message: str, when: int, email: str) -> str:
    iso = f"{int(when)} +0000"
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": iso,
        "GIT_COMMITTER_DATE": iso,
        "GIT_AUTHOR_NAME": email.split("@")[0],
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": email.split("@")[0],
        "GIT_COMMITTER_EMAIL": email,
    }
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message, "--allow-empty"],
        cwd=repo, check=True, capture_output=True, env=env,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _strip_vb(text: str) -> str:
    return P._strip_verified_by(text)


# --------------------------------------------------------------------------- #
# The stamp — through the ONE reverify gate
# --------------------------------------------------------------------------- #
def test_reverify_stamps_verified_by_with_slug_and_own_ts(repo, memory_dir):
    write_file(repo, "src/dep.py", "v = 1\n")
    git_commit(repo, "dep", 1_700_000_000)
    write_file(repo, ".claude/memory/m.md", _mem_text("m"))
    git_commit(repo, "memory", 1_700_000_100)

    res = _reverify(memory_dir, repo, "m")
    assert res["error"] is None and res["changed"] is True
    assert res["verified_by"].startswith("tester_example.com@")

    after = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()
    parsed = TC.read_verified_by(after)
    assert parsed is not None
    slug, ts = parsed
    assert slug == "tester_example.com"
    assert res["verified_by"] == f"{slug}@{ts}"
    # decoupled: verified_by's ts is its OWN, distinct field from last_verified
    assert res["last_verified"] and res["last_verified"] != res["verified_by"]


def test_repeat_verdict_refreshes_only_verified_by(repo, memory_dir):
    """The sharpened idempotence contract: the provenance triplet + last_verified are
    idempotent; verified_by refreshes per verdict — so two back-to-back reverifies
    differ ONLY in the verified_by key (byte-identical once it is stripped)."""
    write_file(repo, "src/dep.py", "v = 1\n")
    git_commit(repo, "dep", 1_700_000_000)
    write_file(repo, ".claude/memory/m.md", _mem_text("m"))
    git_commit(repo, "memory", 1_700_000_100)

    first = _reverify(memory_dir, repo, "m")
    text1 = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()
    second = _reverify(memory_dir, repo, "m")
    text2 = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()

    assert second["changed"] is True  # a repeat verdict IS a state change now
    assert second["last_verified"] == first["last_verified"]  # write-once holds
    assert _strip_vb(text1) == _strip_vb(text2)  # nothing else moved
    assert TC.read_verified_by(text2) is not None


def test_verified_by_respects_usage_user_override(repo, memory_dir, monkeypatch):
    monkeypatch.setenv("HIPPO_USAGE_USER", "Alice Smith")
    write_file(repo, "src/dep.py", "v = 1\n")
    git_commit(repo, "dep", 1_700_000_000)
    write_file(repo, ".claude/memory/m.md", _mem_text("m"))
    git_commit(repo, "memory", 1_700_000_100)
    res = _reverify(memory_dir, repo, "m")
    assert res["verified_by"].startswith("alice_smith@")


def test_dry_run_reports_stamp_without_writing(repo, memory_dir):
    write_file(repo, "src/dep.py", "v = 1\n")
    git_commit(repo, "dep", 1_700_000_000)
    write_file(repo, ".claude/memory/m.md", _mem_text("m"))
    git_commit(repo, "memory", 1_700_000_100)
    before = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()
    res = _reverify(memory_dir, repo, "m", dry_run=True)
    assert res["verified_by"] and res["changed"] is True
    assert open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read() == before


def test_body_stays_byte_identical(repo, memory_dir):
    write_file(repo, "src/dep.py", "v = 1\n")
    git_commit(repo, "dep", 1_700_000_000)
    write_file(repo, ".claude/memory/m.md", _mem_text("m"))
    git_commit(repo, "memory", 1_700_000_100)
    _reverify(memory_dir, repo, "m")
    after = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()
    _fm, body = P.split_frontmatter(after)
    assert body == "body src/dep.py\n"


def test_corpus_format_not_bumped():
    """ED-4: the stamp is additive/absence-emits-nothing — no corpus format move.
    (The item spec said 'stays 4'; the LIVE value was already 5 before CLB-2 — the
    binding requirement is NO BUMP, which this pins.)"""
    assert P.CORPUS_FORMAT_VERSION == 5


# --------------------------------------------------------------------------- #
# Parsing + slug discipline
# --------------------------------------------------------------------------- #
def test_read_verified_by_parses_both_schemas_and_rejects_malformed():
    top = '---\nname: m\nverified_by: "alice@2026-01-01T00:00:00+00:00"\n---\nb\n'
    nested = (
        "---\nname: m\nmetadata:\n  type: project\n"
        '  verified_by: "bob@2026-01-01T00:00:00+00:00"\n---\nb\n'
    )
    assert TC.read_verified_by(top) == ("alice", "2026-01-01T00:00:00+00:00")
    assert TC.read_verified_by(nested) == ("bob", "2026-01-01T00:00:00+00:00")
    assert TC.read_verified_by("---\nname: m\n---\nb\n") is None
    assert TC.read_verified_by('---\nname: m\nverified_by: "no-separator"\n---\nb\n') is None


def test_slugify_identity_is_the_shared_join_rule():
    assert P.slugify_identity("Alice.Smith@Example.COM") == "alice.smith_example.com"
    assert P.slugify_identity("") == "unknown"
    assert P.current_user_slug.__doc__  # the stamp side documents the delegation
    # the join side uses the SAME function — no second slugifier exists in team_coverage
    assert "slugify_identity" in inspect.getsource(TC)


# --------------------------------------------------------------------------- #
# Consumers — solo suppression + team coverage
# --------------------------------------------------------------------------- #
def test_verification_summary_lights_the_dark_field(repo, memory_dir):
    write_file(repo, "src/dep.py", "v = 1\n")
    git_commit(repo, "dep", 1_700_000_000)
    write_file(repo, ".claude/memory/m.md", _mem_text("m"))
    write_file(repo, ".claude/memory/n.md", _mem_text("n"))
    git_commit(repo, "memories", 1_700_000_100)
    _reverify(memory_dir, repo, "m")
    vs = TC.verification_summary(memory_dir)
    assert vs == {"total": 2, "last_verified": 1, "verified_by": 1}


def test_team_coverage_none_at_single_author(repo, memory_dir):
    write_file(repo, ".claude/memory/m.md", _mem_text("m"))
    git_commit(repo, "solo", 1_700_000_000)
    assert TC.team_coverage(memory_dir, repo) is None


def test_team_coverage_counts_on_two_author_corpus(repo, memory_dir):
    write_file(repo, "src/dep.py", "v = 1\n")
    write_file(repo, ".claude/memory/m-a.md", _mem_text("m-a"))
    git_commit(repo, "alice writes", 1_700_000_000)  # tester@example.com
    write_file(repo, ".claude/memory/m-b.md", _mem_text("m-b"))
    _commit_as(repo, "bob writes", 1_700_000_100, "bob@example.com")

    # tester verifies bob's memory -> a NON-author verification of m-b
    _reverify(memory_dir, repo, "m-b")
    _commit_as(repo, "stamp lands", 1_700_000_200, "tester@example.com")

    team = TC.team_coverage(memory_dir, repo)
    assert team is not None
    assert team["authors"] == 2
    assert team["total"] == 2
    assert team["stamped"] == 1
    assert team["non_author_verified"] == 1  # tester vouched bob's file
    assert team["never_other_verified"] == 1  # m-a has never been vouched by a non-author
    assert team["departed"] == 0


def test_file_author_slugs_normalize_through_the_shared_rule(repo, memory_dir):
    write_file(repo, ".claude/memory/m-a.md", _mem_text("m-a"))
    _commit_as(repo, "write", 1_700_000_000, "Carol.X@Example.COM")
    slugs = TC.file_author_slugs(memory_dir, repo)
    assert slugs.get("m-a") == {"carol.x_example.com"}


# --------------------------------------------------------------------------- #
# Doctor + scorecard — the line is OMITTED (not empty) solo
# --------------------------------------------------------------------------- #
def test_doctor_team_coverage_suppressed_solo(repo, memory_dir):
    from memory.doctor import CHECKS
    from memory.doctor_checks_lifecycle import check_team_coverage
    from memory.doctor_checks_env import DoctorContext

    write_file(repo, "src/dep.py", "v = 1\n")
    write_file(repo, ".claude/memory/m.md", _mem_text("m"))
    git_commit(repo, "solo", 1_700_000_000)
    r = check_team_coverage(DoctorContext(memory_dir, repo))
    assert r["status"] == "ok"
    assert "suppressed (single git author)" in r["message"]
    assert "verified_by stamp(s)" not in r["message"]  # zero team numbers rendered
    assert "\n" not in r["message"]
    labels = [label for label, _fn in CHECKS]
    assert labels.count("team_coverage") == 1
    assert labels[-1] == "stale_memobot_env"


def test_doctor_team_coverage_renders_on_multi_author(repo, memory_dir):
    from memory.doctor_checks_lifecycle import check_team_coverage
    from memory.doctor_checks_env import DoctorContext

    write_file(repo, "src/dep.py", "v = 1\n")
    write_file(repo, ".claude/memory/m-a.md", _mem_text("m-a"))
    git_commit(repo, "one", 1_700_000_000)
    write_file(repo, ".claude/memory/m-b.md", _mem_text("m-b"))
    _commit_as(repo, "two", 1_700_000_100, "bob@example.com")
    r = check_team_coverage(DoctorContext(memory_dir, repo))
    assert r["status"] == "ok"
    assert "team (2 authors):" in r["message"]
    assert "never verified by a non-author" in r["message"]
    assert check_team_coverage(DoctorContext(memory_dir, repo)) == r  # deterministic


def test_scorecard_team_part_omitted_solo_present_multi(repo, memory_dir):
    from memory.doctor import _scorecard_message

    write_file(repo, ".claude/memory/m-a.md", _mem_text("m-a"))
    git_commit(repo, "solo", 1_700_000_000)
    _status, solo_msg = _scorecard_message(memory_dir, repo)
    assert "team:" not in solo_msg  # OMITTED entirely — byte-identical to pre-CLB-2

    write_file(repo, ".claude/memory/m-b.md", _mem_text("m-b"))
    _commit_as(repo, "two", 1_700_000_100, "bob@example.com")
    _status, multi_msg = _scorecard_message(memory_dir, repo)
    assert "team: 0 non-author-verified / 0 verified_by stamp(s) across 2 authors" in multi_msg


# --------------------------------------------------------------------------- #
# The NEW AST pin: verified_by is never a ranking input
# --------------------------------------------------------------------------- #
def test_verified_by_never_a_ranking_input():
    """Stricter than the confidence reads-confined pin (confidence IS a ranking
    input now, confined to two functions): recall's whole module family plus
    build_index must reference the ``verified_by`` key in ZERO functions — the
    stamp is report-time attribution, never retrieval signal."""
    from memory import build_index, recall, recall_graph, recall_query, recall_rank
    from memory import recall_salience, recall_tiers

    readers = {}
    for module in (recall, recall_rank, recall_salience, recall_tiers, recall_query,
                   recall_graph, build_index):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            consts = {
                c.value
                for c in ast.walk(node)
                if isinstance(c, ast.Constant) and isinstance(c.value, str)
            }
            if "verified_by" in consts:
                readers.setdefault(module.__name__, set()).add(node.name)
    assert readers == {}, (
        f"verified_by leaked into ranking-adjacent code: {readers} — it is report-time "
        "attribution (doctor/scorecard), never a retrieval input"
    )
