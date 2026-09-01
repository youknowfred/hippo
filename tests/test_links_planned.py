"""GRF-6 — the ``planned:`` deliberate-forward-reference idiom.

Ratified 2026-09-01 (owner decision, the PR #114 follow-up round): an intentional
``[[not-yet-written]]`` wikilink stops appearing even as advisory dangling ONLY when its
source declares the target in ``planned:`` frontmatter. Pinned here:

  - a DECLARED absent target reclassifies to the informational ``planned`` class — out
    of ``dangling``, silent in ``health_line``, out of doctor's rot count (noted in the
    line, never counted);
  - an UNMARKED absent target stays advisory ``dangling`` — the idiom never weakens
    true-rot detection, and the class's advisory posture is unchanged (CLB-1: never a
    gate);
  - a marker can never mask an ARCHIVED, CROSS-TIER, or SUPERSEDED target, and B's
    marker never quiets A's link (per-source semantics);
  - declarations ride links.json (schema v5), so the GRA-6 cached producer path
    classifies planned refs with ZERO memory-file reads;
  - ``boundary_lint`` stays marker-blind (PR #67: the fresh-checkout view is untouched
    — a stranger's clone sees the link dangle, and that is correct there);
  - typed relations are out of scope — a forward-declared typed target stays loud;
  - ``graph_audit`` carries ``planned`` beside ``cross_tier`` and flags declarations
    whose target now exists (``planned_stale``, with a reason) — CLI-only, never a nag.
"""

from __future__ import annotations

import builtins
import os

import pytest

from memory import build_index as B
from memory.doctor_checks_env import DoctorContext
from memory.doctor_checks_recall import check_edge_rot
from memory.links import graph_audit
from memory.links import main as links_main
from memory.links_graph import parse_planned
from memory.lint_links import boundary_lint, health_line, lint
from memory.lint_links import main as lint_main

from .conftest import git_commit, write_file


def _mem(name, body, extra_fm=""):
    return f'---\nname: {name}\ndescription: "{name} description"\n{extra_fm}---\n{body}\n'


# --------------------------------------------------------------------------- #
# the split itself: declared -> planned, unmarked -> still advisory dangling
# --------------------------------------------------------------------------- #
@pytest.fixture
def corpus(tmp_path):
    md = str(tmp_path / "proj" / ".claude" / "memory")
    os.makedirs(md)
    # metadata:-nested list form on hub; top-level bare-string form on solo — both
    # schemas of the parse_typed_relations read convention must work.
    write_file(
        md,
        "hub.md",
        _mem(
            "hub",
            "See [[future-plan]] and [[genuinely-gone]] and [[local-target]].",
            extra_fm="metadata:\n  planned: [future-plan]\n",
        ),
    )
    write_file(
        md,
        "solo.md",
        _mem("solo", "Waiting on [[solo-future]].", extra_fm="planned: solo-future\n"),
    )
    write_file(md, "local-target.md", _mem("local-target", "lives in the project"))
    return md


def test_declared_forward_ref_classifies_planned_unmarked_stays_dangling(corpus):
    report = lint(corpus)
    dangling = {d["target"] for d in report["dangling"]}
    planned = {(d["file"], d["target"]) for d in report["planned"]}
    assert dangling == {"genuinely-gone"}
    assert planned == {("hub", "future-plan"), ("solo", "solo-future")}
    line = health_line(report)
    assert line is not None and "genuinely-gone" in line
    assert "future-plan" not in line and "solo-future" not in line


def test_health_line_silent_and_doctor_ok_when_only_planned_remain(tmp_path):
    md = str(tmp_path / "proj" / ".claude" / "memory")
    os.makedirs(md)
    write_file(
        md,
        "hub.md",
        _mem("hub", "See [[future-plan]] and [[other]].", extra_fm="planned: [future-plan]\n"),
    )
    write_file(md, "other.md", _mem("other", "resolved neighbor"))
    assert health_line(lint(md)) is None  # nothing to nag — the only dangling is declared
    verdict = check_edge_rot(DoctorContext(md, os.path.dirname(md)))
    assert verdict["status"] == "ok"
    assert "edge rot: 0" in verdict["message"]
    assert "1 planned forward reference" in verdict["message"]  # noted, never counted


# --------------------------------------------------------------------------- #
# mask guards: archived / cross-tier / superseded targets are never maskable
# --------------------------------------------------------------------------- #
def test_marker_cannot_mask_an_archived_target(tmp_path):
    md = str(tmp_path / "proj" / ".claude" / "memory")
    os.makedirs(os.path.join(md, "archive"))
    write_file(md, "hub.md", _mem("hub", "See [[attic]].", extra_fm="planned: [attic]\n"))
    write_file(md, os.path.join("archive", "attic.md"), _mem("attic", "retired body"))
    report = lint(md)
    assert [d["target"] for d in report["dangling"]] == ["attic"]  # stays advisory
    assert report["planned"] == []
    audit = graph_audit(md)
    assert [(r["class"], r["target"]) for r in audit["rot"]] == [("archived", "attic")]
    # ...and the declaration itself is flagged stale (the target exists — in archive/).
    assert audit["planned_stale"] == [{"src": "hub", "target": "attic", "reason": "archived"}]


def test_marker_cannot_mask_a_cross_tier_target(tmp_path, monkeypatch):
    md = str(tmp_path / "proj" / ".claude" / "memory")
    user = str(tmp_path / "user-tier")
    os.makedirs(md)
    os.makedirs(user)
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", user)
    write_file(user, "promoted-lesson.md", _mem("promoted-lesson", "user tier"))
    write_file(
        md,
        "hub.md",
        _mem("hub", "See [[promoted-lesson]].", extra_fm="planned: [promoted-lesson]\n"),
    )
    report = lint(md)
    # The cross-tier split runs FIRST: the target classifies by what it IS, and the
    # marker is never consulted for it.
    assert [d["target"] for d in report["cross_tier"]] == ["promoted-lesson"]
    assert report["planned"] == [] and report["dangling"] == []
    audit = graph_audit(md)
    assert audit["planned_stale"] == [
        {"src": "hub", "target": "promoted-lesson", "reason": "resolves-cross-tier"}
    ]


def test_marker_cannot_mask_a_superseded_target(tmp_path):
    md = str(tmp_path / "proj" / ".claude" / "memory")
    os.makedirs(md)
    write_file(
        md,
        "hub.md",
        _mem("hub", "See [[retired]].", extra_fm="planned: [retired]\n"),
    )
    write_file(md, "retired.md", _mem("retired", "old knowledge"))
    write_file(
        md,
        "successor.md",
        _mem("successor", "replacement", extra_fm="supersedes: [retired]\n"),
    )
    audit = graph_audit(md)
    # The target RESOLVES, so it never reaches dangling and the marker is inert — the
    # superseded rot classification is untouched.
    assert ("superseded", "hub", "retired") in {
        (r["class"], r["src"], r["target"]) for r in audit["rot"]
    }
    assert audit["planned"] == []
    assert audit["planned_stale"] == [
        {"src": "hub", "target": "retired", "reason": "resolves-in-project"}
    ]


def test_marker_is_per_source_and_never_quiets_another_files_link(tmp_path):
    md = str(tmp_path / "proj" / ".claude" / "memory")
    os.makedirs(md)
    write_file(
        md,
        "declarer.md",
        _mem("declarer", "See [[shared-future]].", extra_fm="planned: [shared-future]\n"),
    )
    write_file(md, "undeclared.md", _mem("undeclared", "Also see [[shared-future]]."))
    report = lint(md)
    assert [(d["file"], d["target"]) for d in report["planned"]] == [
        ("declarer", "shared-future")
    ]
    assert [(d["file"], d["target"]) for d in report["dangling"]] == [
        ("undeclared", "shared-future")
    ]


def test_typed_relations_are_out_of_scope(tmp_path):
    md = str(tmp_path / "proj" / ".claude" / "memory")
    os.makedirs(md)
    write_file(
        md,
        "refiner.md",
        _mem(
            "refiner",
            "body with no wikilinks",
            extra_fm="refines: [ghost-target]\nplanned: [ghost-target]\n",
        ),
    )
    report = lint(md)
    # A forward-declared typed relation is a defect, not an idiom: typed_dangling stays
    # loud regardless of any planned declaration.
    assert [d["target"] for d in report["typed_dangling"]] == ["ghost-target"]
    assert health_line(report) is not None


# --------------------------------------------------------------------------- #
# the cached producer path: zero memory-file reads, identical classification
# --------------------------------------------------------------------------- #
def test_cached_path_classifies_planned_with_zero_memory_file_reads(corpus, monkeypatch):
    import memory.links_graph as LKG

    idx = os.path.join(os.path.dirname(corpus), ".memory-index")
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    B.build_index(corpus, idx)
    cold = lint(corpus)

    def boom(_md):
        raise AssertionError("cached path must not iterate memory files")

    monkeypatch.setattr(LKG, "_iter_memory_files", boom)
    opened: list = []
    real_open = builtins.open

    def spy(file, *a, **k):
        try:
            p = os.fspath(file)
        except TypeError:
            p = ""
        if isinstance(p, str) and p.startswith(corpus):
            opened.append(p)
        return real_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    cached = lint(corpus, index_dir=idx)
    assert opened == []  # declarations rode links.json (v5) — not one corpus read
    assert [d["target"] for d in cached["planned"]] == [d["target"] for d in cold["planned"]]
    assert [d["target"] for d in cached["dangling"]] == [d["target"] for d in cold["dangling"]]


# --------------------------------------------------------------------------- #
# boundary view, failure shape, CLI rendering, parser edges
# --------------------------------------------------------------------------- #
def test_boundary_lint_stays_marker_blind(tmp_path):
    """PR #67 contract: the committed-subset view must NOT become marker-aware — the
    fresh checkout genuinely lacks the not-yet-written memory, and saying so is the
    boundary view's whole job (expected-not-error, never a gate)."""
    repo = str(tmp_path / "repo")
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    write_file(
        md,
        "hub.md",
        _mem("hub", "See [[future-plan]].", extra_fm="planned: [future-plan]\n"),
    )
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    git_commit(repo, "seed", 1_700_000_000)
    view = boundary_lint(md, repo)
    assert view["ok"]
    assert [d["target"] for d in view["dangling"]] == ["future-plan"]


def test_failure_shape_carries_the_planned_key(tmp_path):
    report = lint(str(tmp_path / "no" / "such" / "dir"))
    assert report["ok"] is False and report["planned"] == []


def test_lint_cli_and_audit_cli_render_planned_lines(corpus, capsys):
    assert lint_main(["--memory-dir", corpus]) == 0
    out = capsys.readouterr().out
    assert "planned forward refs: 2 (declared deliberate — not rot)" in out
    assert "hub -> [[future-plan]] (planned)" in out
    assert links_main(["--memory-dir", corpus, "--audit"]) == 0
    out = capsys.readouterr().out
    assert "planned forward refs (2) — declared deliberate (GRF-6), NOT rot:" in out
    assert "edge rot (1):" in out  # the unmarked control is still named


def test_audit_cli_renders_stale_planned_markers(tmp_path, capsys):
    md = str(tmp_path / "proj" / ".claude" / "memory")
    os.makedirs(md)
    write_file(md, "hub.md", _mem("hub", "See [[arrived]].", extra_fm="planned: [arrived]\n"))
    write_file(md, "arrived.md", _mem("arrived", "the target got written"))
    assert links_main(["--memory-dir", md, "--audit"]) == 0
    out = capsys.readouterr().out
    assert "stale planned marker(s) (1)" in out
    assert "resolves-in-project" in out and "hub planned: arrived" in out


def test_parse_planned_shapes():
    """Tolerant-read pins: both schemas, scalar form, dedupe, non-strings dropped."""
    assert parse_planned({"planned": ["a", "b", "a", 3, " "]}) == ["a", "b"]
    assert parse_planned({"planned": "solo"}) == ["solo"]
    assert parse_planned({"metadata": {"planned": ["nested"]}}) == ["nested"]
    # top-level wins over metadata: (the parse_typed_relations precedence)
    assert parse_planned({"planned": ["top"], "metadata": {"planned": ["nested"]}}) == ["top"]
    assert parse_planned({"planned": {"not": "a list"}}) == []
    assert parse_planned("not a dict") == []
    assert parse_planned({}) == []
