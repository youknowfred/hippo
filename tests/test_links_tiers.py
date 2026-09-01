"""Tier-aware link classification (GRF-1 tier awareness) — the promoted-target trap.

Live finding (2026-09-01, em-growth-labs): doctor reported "edge rot: 24" — 21 of the 24
targets had been PROMOTED to the machine-local user tier, present in its index manifest
and resolvable at recall time; links.py audited only the project corpus, so every inbound
link to a promoted memory was rot forever. Pinned here:

  - a target resolving in the user tier / TEA-3 private tier is classified ``cross_tier``
    (a distinct NON-rot category), out of ``dangling`` and out of doctor's rot count;
  - a target resolving in the PROJECT corpus was never dangling (control);
  - a genuinely absent target — and the supported deliberate-forward-reference idiom —
    stays in the advisory ``dangling`` class (never escalated, never a doctor fail);
  - ``boundary_lint`` (PR #67: expected-not-error, never a gate) keeps the fresh-checkout
    view: a committed memory's link to a user-tier stem still dangles THERE.
"""

from __future__ import annotations

import os

import pytest

from memory.doctor_checks_env import DoctorContext
from memory.doctor_checks_recall import check_edge_rot
from memory.links import extra_tier_stems, graph_audit
from memory.lint_links import boundary_lint, health_line, lint

from .conftest import git_commit, write_file


def _mem(name, body):
    return f'---\nname: {name}\ndescription: "{name} description"\n---\n{body}\n'


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    md = str(tmp_path / "proj" / ".claude" / "memory")
    user = str(tmp_path / "user-tier")
    private = str(tmp_path / "private-tier")
    os.makedirs(md)
    os.makedirs(user)
    os.makedirs(private)
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", user)
    monkeypatch.setenv("HIPPO_LOCAL_MEMORY_DIR", private)
    write_file(user, "promoted-lesson.md", _mem("promoted-lesson", "lifted to user tier"))
    write_file(private, "private-note.md", _mem("private-note", "TEA-3 private"))
    write_file(
        md,
        "hub.md",
        _mem(
            "hub",
            "Links: [[local-target]], [[promoted-lesson]], [[private-note]], "
            "[[genuinely-gone]], and a deliberate forward ref [[future-plan]].",
        ),
    )
    write_file(md, "local-target.md", _mem("local-target", "lives in the project"))
    return md, user, private


def test_extra_tier_stems_lists_both_tiers(corpus):
    md, _user, _private = corpus
    stems = extra_tier_stems(md)
    assert stems == {"promoted-lesson": "user", "private-note": "private"}


def test_lint_classifies_cross_tier_out_of_dangling(corpus):
    md, _user, _private = corpus
    report = lint(md)
    dangling = {d["target"] for d in report["dangling"]}
    cross = {(d["target"], d["tier"]) for d in report["cross_tier"]}
    assert ("promoted-lesson", "user") in cross
    assert ("private-note", "private") in cross
    assert "promoted-lesson" not in dangling and "private-note" not in dangling
    # Genuinely absent + the deliberate forward reference: both stay ADVISORY dangling —
    # the supported idiom is never escalated and never reclassified.
    assert {"genuinely-gone", "future-plan"} <= dangling
    assert "local-target" not in dangling  # project-resolved control


def test_health_line_stops_nagging_promoted_targets(corpus):
    md, _user, _private = corpus
    line = health_line(lint(md))
    assert line is not None  # the two real danglings still nag (legible degradation)
    assert "promoted-lesson" not in line and "private-note" not in line


def test_graph_audit_reports_cross_tier_beside_rot(corpus):
    md, _user, _private = corpus
    report = graph_audit(md)
    rot_targets = {r["target"] for r in report["rot"]}
    cross = {(r["target"], r["tier"]) for r in report["cross_tier"]}
    assert ("promoted-lesson", "user") in cross and ("private-note", "private") in cross
    assert "promoted-lesson" not in rot_targets and "private-note" not in rot_targets
    assert {"genuinely-gone", "future-plan"} <= rot_targets
    for r in report["rot"]:
        if r["target"] in ("genuinely-gone", "future-plan"):
            assert r["class"] == "dangling"  # never archived/cross-tier/superseded


def test_check_edge_rot_counts_only_true_rot(corpus):
    md, _user, _private = corpus
    verdict = check_edge_rot(DoctorContext(md, os.path.dirname(md)))
    assert verdict["status"] == "warn"  # the two real danglings — advisory, never fail
    assert "edge rot: 2" in verdict["message"]
    assert "cross-tier" in verdict["message"]


def test_clean_when_only_cross_tier_links_remain(tmp_path, monkeypatch):
    md = str(tmp_path / "proj" / ".claude" / "memory")
    user = str(tmp_path / "user-tier")
    os.makedirs(md)
    os.makedirs(user)
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", user)
    write_file(user, "promoted-lesson.md", _mem("promoted-lesson", "user tier"))
    write_file(md, "hub.md", _mem("hub", "See [[promoted-lesson]]."))
    assert health_line(lint(md)) is None  # nothing to nag — the link resolves at recall
    verdict = check_edge_rot(DoctorContext(md, os.path.dirname(md)))
    assert verdict["status"] == "ok"
    assert "edge rot: 0" in verdict["message"] and "cross-tier" in verdict["message"]


def test_typed_relation_into_user_tier_is_cross_tier(corpus):
    md, _user, _private = corpus
    write_file(
        md,
        "refiner.md",
        '---\nname: refiner\ndescription: "refines a promoted memory"\n'
        "refines: [promoted-lesson]\n---\nbody\n",
    )
    report = lint(md)
    assert [d["target"] for d in report["cross_tier_typed"]] == ["promoted-lesson"]
    assert all(d["target"] != "promoted-lesson" for d in report["typed_dangling"])


def test_boundary_lint_keeps_the_fresh_checkout_view(tmp_path, monkeypatch):
    """PR #67 contract: the committed-subset view must NOT become tier-aware — a
    stranger's clone has neither the user tier nor memory.local."""
    repo = str(tmp_path / "repo")
    md = os.path.join(repo, ".claude", "memory")
    user = str(tmp_path / "user-tier")
    os.makedirs(md)
    os.makedirs(user)
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", user)
    write_file(user, "promoted-lesson.md", _mem("promoted-lesson", "user tier"))
    write_file(md, "hub.md", _mem("hub", "See [[promoted-lesson]]."))
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    git_commit(repo, "seed", 1_700_000_000)
    view = boundary_lint(md, repo)
    assert view["ok"]
    assert [d["target"] for d in view["dangling"]] == ["promoted-lesson"]
