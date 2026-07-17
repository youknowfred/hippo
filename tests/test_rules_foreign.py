"""IOP-1 — the foreign-dialect radar: census + cross-dialect divergence + .mdc rot.

Hermetic: a real scratch git repo carrying all three dialect surfaces. Pins the
acceptance criteria — presence-only census with the honest single-dialect degrade,
divergence via rule_dup_candidates verbatim (report-only, never enqueued), the two
existence-only .mdc rot legs — and above all the ISOLATION invariant (inv5):
FOREIGN_GLOBS never merges into GOV_GLOBS, never reaches the RUL-1/3/4 authority
paths, and the radar never joins PRODUCERS (grep/AST verified, as the AC demands).
"""

from __future__ import annotations

import ast
import os
import subprocess

from memory import rules_foreign as F
from memory.rules_plane import GOV_GLOBS

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def _repo(tmp_path):
    repo = str(tmp_path / "repo")
    os.makedirs(os.path.join(repo, "src"))
    for rel in ("src/app.py", "src/util.py"):
        with open(os.path.join(repo, rel), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "seed"], check=True, env=_GIT_ENV)
    return repo


def _write(repo, rel, text):
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(text)


# --------------------------------------------------------------------------- #
# census — filesystem glob-presence only
# --------------------------------------------------------------------------- #
def test_census_reports_all_three_dialects_by_presence(tmp_path):
    repo = _repo(tmp_path)
    _write(repo, ".cursor/rules/a.mdc", "---\ndescription: d\n---\nbody\n")
    _write(repo, ".cursor/rules/b.mdc", "---\ndescription: d\n---\nbody\n")
    _write(repo, ".github/instructions/py.instructions.md",
           "---\napplyTo: '**/*.py'\n---\nCopilot python rules\n")
    _write(repo, ".agents/rules/x.md", "unratified dialect content\n")
    census = F.foreign_census(repo)
    assert census["cursor"] == [".cursor/rules/a.mdc", ".cursor/rules/b.mdc"]
    assert census["copilot"] == [".github/instructions/py.instructions.md"]
    assert census["agents-rules"] == [".agents/rules/x.md"]


def test_census_degrades_to_no_other_dialects_line(tmp_path):
    repo = _repo(tmp_path)
    radar = F.foreign_radar(repo)
    assert all(fs == [] for fs in radar["census"].values())
    text = F.describe_radar(radar)
    assert "no other dialects found" in text


def test_agents_rules_is_marked_watch_only(tmp_path):
    repo = _repo(tmp_path)
    _write(repo, ".agents/rules/x.md", "content\n")
    text = F.describe_radar(F.foreign_radar(repo))
    assert "watch-only" in text and "unratified" in text


# --------------------------------------------------------------------------- #
# cross-dialect divergence — rule_dup_candidates verbatim, report-only
# --------------------------------------------------------------------------- #
def test_divergence_flags_a_foreign_file_restating_governance(tmp_path):
    repo = _repo(tmp_path)
    rule = "Always run the linter and the type checker before every commit lands."
    _write(repo, "CLAUDE.md", f"# Rules\n\n{rule}\n")
    _write(repo, ".cursor/rules/lint.mdc", f"---\ndescription: lint rule\n---\n{rule}\n")
    _write(repo, ".github/instructions/lint.instructions.md",
           f"---\napplyTo: '**'\n---\n{rule}\n")
    radar = F.foreign_radar(repo)
    by_file = {d["foreign"]: d for d in radar["divergence"]}
    assert ".cursor/rules/lint.mdc" in by_file
    assert ".github/instructions/lint.instructions.md" in by_file
    assert by_file[".cursor/rules/lint.mdc"]["matches"][0]["file"] == "CLAUDE.md"
    assert "same-rule-diverged pair" in F.describe_radar(radar)


def test_divergence_silent_when_no_governance_overlap(tmp_path):
    repo = _repo(tmp_path)
    _write(repo, "CLAUDE.md", "# Rules\n\nCommit messages stay under fifty characters.\n")
    _write(repo, ".cursor/rules/other.mdc",
           "---\ndescription: deploy\n---\nDeploy artifacts go through the staging bucket pipeline.\n")
    assert F.foreign_radar(repo)["divergence"] == []


# --------------------------------------------------------------------------- #
# .mdc rot — existence-only citations + dead globs (never git-log framed)
# --------------------------------------------------------------------------- #
def test_mdc_citation_rot_flags_missing_paths_only(tmp_path):
    repo = _repo(tmp_path)
    _write(repo, ".cursor/rules/refs.mdc",
           "---\ndescription: d\n---\nsee src/app.py and src/vanished.py for details\n")
    radar = F.foreign_radar(repo)
    assert radar["mdc_citation_rot"] == [
        {"file": ".cursor/rules/refs.mdc", "missing": ["src/vanished.py"]}
    ]
    assert "existence check only" in F.describe_radar(radar)


def test_mdc_dead_glob_flagged_live_glob_not(tmp_path):
    repo = _repo(tmp_path)
    _write(repo, ".cursor/rules/scoped.mdc",
           "---\ndescription: d\nglobs: src/*.py,ghost/**/*.zzz\n---\nbody\n")
    radar = F.foreign_radar(repo)
    assert radar["mdc_dead_globs"] == [
        {"file": ".cursor/rules/scoped.mdc", "glob": "ghost/**/*.zzz"}
    ]


def test_rot_is_silent_without_a_git_oracle(tmp_path):
    plain = str(tmp_path / "plain")
    os.makedirs(os.path.join(plain, ".cursor", "rules"))
    _write(plain, ".cursor/rules/x.mdc", "---\ndescription: d\n---\nsee src/gone.py\n")
    rot = F.mdc_rot(plain, [".cursor/rules/x.mdc"])
    assert rot == {"citation_rot": [], "dead_globs": []}


# --------------------------------------------------------------------------- #
# inv5 — the isolation pins the AC names (grep/AST verified)
# --------------------------------------------------------------------------- #
def _source(name: str) -> str:
    import memory

    with open(os.path.join(os.path.dirname(memory.__file__), name), encoding="utf-8") as fh:
        return fh.read()


def test_gov_globs_is_unchanged_and_disjoint_from_foreign_globs():
    """FOREIGN_GLOBS never merges into GOV_GLOBS: the authority surface keeps its exact
    shipped value, and no foreign glob appears in it."""
    assert GOV_GLOBS == (
        "CLAUDE.md",
        "AGENTS.md",
        ".claude/rules/*.md",
        ".claude/agents/*.md",
        ".claude/skills/**/*.md",
    )
    assert not (set(F.FOREIGN_GLOBS) & set(GOV_GLOBS))


def test_rules_plane_never_references_the_foreign_surface():
    src = _source("rules_plane.py")
    assert "FOREIGN" not in src and "rules_foreign" not in src


def test_radar_never_calls_the_authority_paths():
    """AST pin: rules_foreign never invokes gov_citations / conflict_radar /
    load_rules_cache / refresh_rules_cache — its ONE rules_plane reuse is
    rule_dup_candidates with foreign content as the DRAFT argument."""
    tree = ast.parse(_source("rules_foreign.py"))
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            called.add(fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None))
    for banned in ("gov_citations", "conflict_radar", "load_rules_cache",
                   "refresh_rules_cache", "gov_files"):
        assert banned not in called, f"rules_foreign must not call {banned}"
    assert "rule_dup_candidates" in called  # the sanctioned reuse, draft-side only


def test_radar_is_not_a_producer_and_writes_nothing():
    """inv6/inv1: session_start's PRODUCERS never grow the radar (audit/doctor
    on-demand only), and the module has no write-mode open at all."""
    assert "rules_foreign" not in _source("session_start.py")
    assert "foreign_radar" not in _source("session_start.py")
    tree = ast.parse(_source("rules_foreign.py"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name == "open":
                modes = [a for a in node.args[1:2]] + [
                    kw.value for kw in node.keywords if kw.arg == "mode"
                ]
                for m in modes:
                    assert isinstance(m, ast.Constant) and "r" in m.value and "w" not in m.value


# --------------------------------------------------------------------------- #
# the doctor face — appended before the pinned-last env check, one line
# --------------------------------------------------------------------------- #
def test_doctor_check_registered_after_team_coverage(tmp_path):
    import memory.doctor as D

    labels = [label for label, _ in D.CHECKS]
    assert labels.index("foreign_dialects") == labels.index("team_coverage") + 1
    assert labels[-1] == "stale_memobot_env"


def test_doctor_line_states_when_none_present(tmp_path, memory_dir, repo):
    import memory.doctor as D

    r = D.check_foreign_dialects(D.DoctorContext(memory_dir, repo))
    assert r["status"] == "ok"
    assert "none present" in r["message"] and "\n" not in r["message"]


def test_doctor_line_counts_clean_dialects(tmp_path, memory_dir, repo):
    import memory.doctor as D

    _write(repo, ".cursor/rules/a.mdc", "---\ndescription: d\nglobs: src/*.py\n---\nUse spaces not tabs.\n")
    _write(repo, "src/app.py", "x = 1\n")
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "add"], check=True, env=_GIT_ENV)
    r = D.check_foreign_dialects(D.DoctorContext(memory_dir, repo))
    assert r["status"] == "ok"
    assert "cursor: 1 file(s)" in r["message"] and "no cross-dialect divergence" in r["message"]
    assert "\n" not in r["message"]


def test_doctor_line_warns_with_finding_counts(tmp_path, memory_dir, repo):
    import memory.doctor as D

    _write(repo, ".cursor/rules/bad.mdc",
           "---\ndescription: d\nglobs: ghost/*.zzz\n---\nsee src/vanished.py\n")
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "add"], check=True, env=_GIT_ENV)
    r = D.check_foreign_dialects(D.DoctorContext(memory_dir, repo))
    assert r["status"] == "warn"
    assert "1 .mdc with missing cited path(s)" in r["message"]
    assert "1 dead .mdc glob(s)" in r["message"]
    assert "/hippo:audit" in r["message"] and "\n" not in r["message"]