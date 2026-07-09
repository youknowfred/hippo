"""Tests for the RCH-6 portability lint — repo-coupling + consequential defaults.

Pure-function coverage of ``memory.portability`` (table-driven, mirroring
test_secret_lint.py) plus the PARITY leg: the consequential-default catalog must flag
exactly the shipped pack memories marked ``confirm: individual`` — driven from the
manifests themselves (the same source test_packs pins to ``_INDIVIDUAL_CONFIRM``), so
the linter's catalog and the packs' individual-confirm set cannot drift apart.
"""

from __future__ import annotations

import glob
import json
import os

from memory import portability as P

_PACKS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "plugin", "assets", "packs")
)


def _kinds(findings):
    return {f["kind"] for f in findings}


# --------------------------------------------------------------------------- #
# repo_coupling — cited paths, absolute home paths, git remotes (severity warn)
# --------------------------------------------------------------------------- #
def test_cited_paths_each_yield_a_coupling_finding():
    findings = P.scan_portability(
        "portable body", cited_paths=["plugin/memory/recall.py", "docs/a.md"]
    )
    assert [f["kind"] for f in findings] == ["repo_coupling", "repo_coupling"]
    assert all(f["severity"] == "warn" for f in findings)
    details = " | ".join(f["detail"] for f in findings)
    assert "plugin/memory/recall.py" in details and "docs/a.md" in details


def test_cited_paths_default_from_frontmatter():
    text = (
        "---\nname: m\nmetadata:\n  cited_paths:\n    - src/app.py\n---\n\nbody\n"
    )
    findings = P.scan_portability(text)
    assert len(findings) == 1 and "src/app.py" in findings[0]["detail"]


def test_absolute_home_paths_flagged_and_echoed():
    for path in ("/Users/fred/GitHub/hippo/x.py", "/home/ci/runner/cache"):
        findings = P.scan_portability(f"set the cache to {path} first")
        assert _kinds(findings) == {"repo_coupling"}
        assert path in findings[0]["detail"]


def test_mid_path_users_component_not_flagged():
    # "app/Users/…" is a repo-relative component, not a machine home directory.
    assert P.scan_portability("edit app/Users/controller.rb accordingly") == []


def test_git_remote_flagged():
    findings = P.scan_portability("push to git@github.com:youknowfred/hippo.git")
    assert _kinds(findings) == {"repo_coupling"}
    assert "git@github.com:youknowfred/hippo.git" in findings[0]["detail"]


def test_https_reference_links_are_portable():
    assert P.scan_portability("see https://github.com/org/repo/issues/1") == []


def test_repeated_value_deduplicated():
    findings = P.scan_portability("/Users/a/x then /Users/a/x again")
    assert len(findings) == 1


# --------------------------------------------------------------------------- #
# consequential_default — attribution + CI-bypass catalog (severity confirm)
# --------------------------------------------------------------------------- #
def test_attribution_defaults_require_confirm():
    for text in (
        "do NOT add a Co-Authored-By trailer",
        "omit the 'Generated with Claude Code' line",
    ):
        findings = P.scan_portability(text)
        assert _kinds(findings) == {"consequential_default"}
        assert all(f["severity"] == "confirm" for f in findings)


def test_ci_bypass_defaults_require_confirm():
    for text in (
        "merge without waiting for checks",
        "do NOT poll CI checks for minutes",
        "bypass the CI gate on hotfixes",
        "gh pr merge 7 --merge --admin",
    ):
        findings = P.scan_portability(text)
        assert "consequential_default" in _kinds(findings), text


def test_ci_words_far_apart_do_not_trip():
    # Window bounds keep the skip/wait detector proximate and same-line: the
    # [^\n]{0,40} window cannot cross a newline, and distant words don't pair.
    assert P.scan_portability("don't wait for the lock.\nCI runs nightly.") == []
    assert (
        P.scan_portability(
            "don't wait for the file lock to clear before retrying the whole "
            "operation from scratch; unrelatedly, CI runs nightly"
        )
        == []
    )


# --------------------------------------------------------------------------- #
# contract — clean text, never-raise
# --------------------------------------------------------------------------- #
def test_portable_prose_is_clean():
    prose = (
        "Prefer table-driven tests. Run the suite from the repo root and read the "
        "real summary line before writing a count anywhere."
    )
    assert P.scan_portability(prose) == []


def test_never_raises_on_non_string_input():
    assert P.scan_portability(12345) == []  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# parity — the catalog covers exactly the packs' individual-confirm set
# --------------------------------------------------------------------------- #
def test_consequential_catalog_matches_shipped_individual_confirm_exactly():
    manifests = sorted(glob.glob(os.path.join(_PACKS_DIR, "*", "manifest.json")))
    assert manifests, "shipped packs must exist"
    individual, flagged = set(), set()
    for mpath in manifests:
        with open(mpath, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        for entry in manifest["memories"]:
            fname = entry["file"]
            if entry.get("confirm") == "individual":
                individual.add(fname)
            with open(
                os.path.join(os.path.dirname(mpath), fname), "r", encoding="utf-8"
            ) as fh:
                text = fh.read()
            if "consequential_default" in _kinds(P.scan_portability(text)):
                flagged.add(fname)
    assert individual, "the individual-confirm catalog anchor must be non-empty"
    assert flagged == individual, (
        "portability's consequential-default catalog must flag exactly the pack "
        f"memories marked confirm=individual; flagged={sorted(flagged)} "
        f"individual={sorted(individual)}"
    )
