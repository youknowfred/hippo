"""Tests for memory/reconsolidate_watermark.py — the [since-watermark] lane's baseline guard.

The 2026-07-18 field repro, from this repo's own consolidate pass: 20 worklist items were
graduated/fixed — every verdict re-baselining its memory's ``source_commit`` to HEAD via
``provenance.reverify_file`` — and the very next worklist call re-listed the same 20 names
as [since-watermark] hits. The lane computed ``<watermark>..HEAD ∩ cited_paths`` and never
consulted the memory's OWN baseline; a memory re-verified AFTER the flagged commits landed
is by construction not a re-verify candidate. These tests pin the guard: covered hits drop
(whole-memory and per-path), coverage falls back to the stored ``source_commit_time`` when
the baseline sha is unresolvable (SHP-3), suppression never fires without POSITIVE
evidence of coverage, and the filter runs BEFORE VOL-1's volatile-only split.

Hermetic: throwaway git repo + memory corpus (the ``repo``/``memory_dir`` fixtures),
synthesized episode buffers in tmp telemetry dirs, pinned commit epochs.
"""

from __future__ import annotations

import json
import os

import memory.reconsolidate as R
from memory import staleness_policy as SP

from .conftest import git_commit, write_file

_ROADMAP = "GROWTH-LOOP-ROADMAP.yaml"  # the VOL-1 fixture vocabulary


def _mem(name, cited, source_commit, extra=""):
    cp = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    sc = f'"{source_commit}"' if source_commit is not None else "null"
    return (
        f"---\nname: {name}\ndescription: \"{name} description\"\ncited_paths: {cp}\n"
        f"source_commit: {sc}\n{extra}---\nbody for {name}\n"
    )


def _episode_line(td, sid, ts, head_commit):
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "episode_buffer.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "ts": ts,
                    "session_id": sid,
                    "query_preview": "q",
                    "recalled_names": [],
                    "head_commit": head_commit,
                }
            )
            + "\n"
        )


def _declare(memory_dir, paths):
    with open(os.path.join(memory_dir, ".format"), "w", encoding="utf-8") as fh:
        json.dump({"volatile_paths": paths}, fh)


def _watermarked_drift(repo, td):
    """Watermark at c1; src/foo.py drifts at c2; a verdict-shaped commit c3 follows.

    The exact consolidate-pass geometry: the drift the lane flags lands INSIDE the
    watermark range, then the corpus commit a graduate/fix verdict re-baselines to
    (touching nothing cited) becomes HEAD. Returns ``(c1, c2, c3)``.
    """
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    _episode_line(td, "last-sess", 100.0, c1)  # the last session started at c1
    write_file(repo, "src/foo.py", "x = 2\n")
    c2 = git_commit(repo, "c2", 1_700_000_100)
    write_file(repo, "corpus.txt", "verdicts rendered\n")
    c3 = git_commit(repo, "c3", 1_700_000_200)
    return c1, c2, c3


# --------------------------------------------------------------------------- #
# The repro pair: a fresh baseline suppresses; a stale one still flags
# --------------------------------------------------------------------------- #
def test_rebaselined_memory_is_not_a_candidate(repo, memory_dir):
    """Graduated at HEAD=c3 AFTER c2's drift landed → no [since-watermark] flag."""
    td = os.path.join(repo, "tele")
    _, _, c3 = _watermarked_drift(repo, td)
    write_file(memory_dir, "m_alpha.md", _mem("m_alpha", ["src/foo.py"], c3))
    assert R.watermark_stale_candidates(memory_dir, repo, telemetry_dir=td) == []


def test_rebaseline_at_the_drift_commit_itself_covers_it(repo, memory_dir):
    """The graduate-at-HEAD boundary: baseline == the very commit that touched the path."""
    td = os.path.join(repo, "tele")
    _, c2, _ = _watermarked_drift(repo, td)
    write_file(memory_dir, "m_edge.md", _mem("m_edge", ["src/foo.py"], c2))
    assert R.watermark_stale_candidates(memory_dir, repo, telemetry_dir=td) == []


def test_unreverified_sibling_still_flags(repo, memory_dir):
    """The discriminating pair from the repro: same corpus, one verdict rendered, one not."""
    td = os.path.join(repo, "tele")
    c1, _, c3 = _watermarked_drift(repo, td)
    write_file(memory_dir, "m_done.md", _mem("m_done", ["src/foo.py"], c3))
    write_file(memory_dir, "m_todo.md", _mem("m_todo", ["src/foo.py"], c1))
    cands = R.watermark_stale_candidates(memory_dir, repo, telemetry_dir=td)
    assert cands == [{"name": "m_todo", "changed_paths": ["src/foo.py"], "watermark": True}]


# --------------------------------------------------------------------------- #
# Per-path precision — the listing matches the brief's own-baseline evidence
# --------------------------------------------------------------------------- #
def test_partial_coverage_keeps_only_post_baseline_paths(repo, memory_dir):
    """A covered sibling path carries zero re-verify bits and drops out of the listing —
    what survives is exactly what the brief's own source_commit..HEAD diffstat shows."""
    td = os.path.join(repo, "tele")
    _, _, c3 = _watermarked_drift(repo, td)  # foo's drift is covered by the c3 baseline
    write_file(repo, "src/bar.py", "y = 2\n")
    git_commit(repo, "c4", 1_700_000_300)  # bar drifts AFTER the re-baseline
    write_file(memory_dir, "m_mix.md", _mem("m_mix", ["src/bar.py", "src/foo.py"], c3))
    cands = R.watermark_stale_candidates(memory_dir, repo, telemetry_dir=td)
    assert cands == [{"name": "m_mix", "changed_paths": ["src/bar.py"], "watermark": True}]


# --------------------------------------------------------------------------- #
# SHP-3 in this lane: unresolvable baseline sha → the stored time judges coverage
# --------------------------------------------------------------------------- #
def test_unresolvable_baseline_falls_back_to_stored_time(repo, memory_dir):
    """Squash-merge rewrote the baseline sha away — the memory's own recorded
    source_commit_time still judges coverage, in both directions."""
    td = os.path.join(repo, "tele")
    _watermarked_drift(repo, td)
    write_file(
        memory_dir,
        "m_cov.md",
        _mem("m_cov", ["src/foo.py"], "e" * 40, extra="source_commit_time: 1700000150\n"),
    )
    write_file(
        memory_dir,
        "m_pre.md",
        _mem("m_pre", ["src/foo.py"], "f" * 40, extra="source_commit_time: 1700000050\n"),
    )
    cands = R.watermark_stale_candidates(memory_dir, repo, telemetry_dir=td)
    assert [c["name"] for c in cands] == ["m_pre"]


def test_no_baseline_evidence_keeps_the_flag(repo, memory_dir):
    """Suppression requires POSITIVE evidence of coverage: no source_commit at all, or an
    unresolvable sha with no stored time, keeps the lane's historical intersection shape."""
    td = os.path.join(repo, "tele")
    _watermarked_drift(repo, td)
    write_file(memory_dir, "m_bare.md", _mem("m_bare", ["src/foo.py"], None))
    write_file(memory_dir, "m_squash.md", _mem("m_squash", ["src/foo.py"], "a" * 40))
    names = [c["name"] for c in R.watermark_stale_candidates(memory_dir, repo, telemetry_dir=td)]
    assert names == ["m_bare", "m_squash"]


# --------------------------------------------------------------------------- #
# Composition with VOL-1 — baseline filter first, then the volatile-only split
# --------------------------------------------------------------------------- #
def test_baseline_filter_runs_before_the_volatile_split(repo, memory_dir):
    """A memory whose only POST-baseline drift is a registry-listed volatile path routes
    to VOL-1's suppressed diagnostic, not an armed flag — the baseline-covered durable
    path no longer masquerades as the arming co-driver."""
    td = os.path.join(repo, "tele")
    write_file(repo, _ROADMAP, "phase: 1\n")  # committed with c1 below
    _, _, c3 = _watermarked_drift(repo, td)
    write_file(repo, _ROADMAP, "phase: 2\n")
    git_commit(repo, "c4", 1_700_000_300)  # volatile drift, AFTER the re-baseline
    write_file(memory_dir, "m_mix.md", _mem("m_mix", [_ROADMAP, "src/foo.py"], c3))
    _declare(memory_dir, [_ROADMAP])
    diagnostics = {}
    cands = R.watermark_stale_candidates(
        memory_dir, repo, telemetry_dir=td, diagnostics=diagnostics
    )
    assert cands == []  # foo covered by the baseline; the roadmap never arms alone
    assert diagnostics[SP.DIAG_KEY] == ["m_mix"]
