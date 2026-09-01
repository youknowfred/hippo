"""Archive × dream-ledger integration — the orphaned-edge trap, closed.

The live incident (2026-09-01, em-growth-labs): a dream pass stamped edges into source
memories; a later archive retirement ``git mv``'d two of those sources into ``archive/``.
``archive_memory`` had ZERO ledger handling, ``check_dream_ledger`` scans only the corpus
root, and ``--undo`` resolved only the root path — so every such edge read as a ghost
(hard doctor ✘) with a documented remedy that could not work. Pinned here:

  - archiving a stamped source RETIRES its active ledger rows (superseding
    ``state: "archived"`` lines — append-only, the ``archive_draft`` idiom; the moved
    file's bytes are untouched, so the move stays one clean git rename);
  - restore REACTIVATES exactly those rows (no orphan stamps after a round-trip);
  - doctor stays green across archive and restore, classifies the LEGACY trap (active
    row + stamp intact under archive/) as an inert-edge WARN with a remedy that works,
    and still FAILS loudly on a true ghost (stamp gone everywhere);
  - the documented remedy works: ``--undo`` falls back to ``archive/<file>`` and stays
    byte-exact with refuse-on-drift there.
"""

from __future__ import annotations

import os

import pytest

import memory.archive as A
import memory.dream as dream
from memory.doctor_checks_corpus import check_dream_ledger
from memory.doctor_checks_env import DoctorContext

from .test_dream import _bridge_corpus, _seed_sessions, _snapshot_md


@pytest.fixture(autouse=True)
def _bm25_only_and_permissive_theta(monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("DREAM_COFIRE_THETA", "0.10")


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    md = str(tmp_path / "mem")
    td = str(tmp_path / "tele")
    idx = str(tmp_path / "idx")
    os.makedirs(md)
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    return md, td, idx


def _applied_corpus(md, td, idx):
    """Real apply pass over the canonical bridge corpus → (stamped fname, its edge ids)."""
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    code, _digest = dream.run_apply_pass(md, idx, td)
    assert code == 0
    rows = [e for e in dream.read_apply_ledger(md) if e.get("state") == "active"]
    assert rows, "an eligible bridge existed — the pass must have applied edges"
    fname = (rows[0].get("undo") or {}).get("file")
    assert fname
    edge_ids = [
        e["edge_id"] for e in rows if (e.get("undo") or {}).get("file") == fname
    ]
    return fname, edge_ids


def _states(md):
    return {e["edge_id"]: e.get("state") for e in dream.read_apply_ledger(md)}


def test_archive_retires_stamped_edges_and_doctor_stays_green(dirs):
    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    stem = fname[:-3]

    # Dry run: reports exactly the would-retire set, writes nothing.
    preview = A.archive_memory(stem, md, os.path.dirname(md), dry_run=True, force=True)
    assert preview["moved"] and sorted(preview["dream_edges_retired"]) == sorted(edge_ids)
    assert _states(md)[edge_ids[0]] == "active"

    res = A.archive_memory(stem, md, os.path.dirname(md), force=True)
    assert res["moved"], res
    assert sorted(res["dream_edges_retired"]) == sorted(edge_ids)
    states = _states(md)
    for eid in edge_ids:
        assert states[eid] == "archived"
    # Stamps rode the move untouched — the archived copy still carries them.
    with open(os.path.join(md, "archive", fname), encoding="utf-8") as fh:
        assert "<!-- dream:" in fh.read()

    # Doctor reconciles: no ghosts, no orphans — the exact ✘ the incident produced.
    verdict = check_dream_ledger(DoctorContext(md, os.path.dirname(md)))
    assert verdict["status"] == "ok", verdict

    # Retired rows leave every active-only surface: the SessionStart producer and the
    # aging firewall's pair subtraction no longer see them.
    others = [e for e in dream.read_apply_ledger(md) if e.get("state") == "active"]
    pairs = dream.unaged_dream_pairs(md, 0)
    for eid in edge_ids:
        assert all(e["edge_id"] != eid for e in others)
    if not others:
        assert dream.dream_applied_producer(md, os.path.dirname(md)) is None
        assert pairs == set()


def test_restore_reactivates_the_archived_edges(dirs):
    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    stem = fname[:-3]
    assert A.archive_memory(stem, md, os.path.dirname(md), force=True)["moved"]

    res = A.restore(stem, md, os.path.dirname(md))
    assert res["restored"], res
    assert sorted(res["dream_edges_reactivated"]) == sorted(edge_ids)
    states = _states(md)
    for eid in edge_ids:
        assert states[eid] == "active"
    # Round-trip leaves the reconciler green: the stamps are back in the live scan and
    # their rows are active again — no manufactured orphans.
    verdict = check_dream_ledger(DoctorContext(md, os.path.dirname(md)))
    assert verdict["status"] == "ok", verdict


def test_legacy_trap_is_inert_warn_and_documented_undo_works(dirs):
    """A hand ``git mv`` (outside archive_memory) leaves rows ACTIVE while the stamps sit
    under archive/ — the incident's exact on-disk state. Doctor must name it inert (warn,
    not the corruption-grade fail) and ``--undo`` must actually work there."""
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    before = _snapshot_md(md)
    code, _ = dream.run_apply_pass(md, idx, td)
    assert code == 0
    rows = [e for e in dream.read_apply_ledger(md) if e.get("state") == "active"]
    stamped_files = {(e.get("undo") or {}).get("file") for e in rows}

    os.makedirs(os.path.join(md, "archive"), exist_ok=True)
    for fname in stamped_files:
        os.rename(os.path.join(md, fname), os.path.join(md, "archive", fname))

    verdict = check_dream_ledger(DoctorContext(md, os.path.dirname(md)))
    assert verdict["status"] == "warn", verdict
    assert "archived" in verdict["message"] and "--undo" in verdict["message"]

    # The documented remedy, unmodified: a full-pass undo reverts the archived copies
    # byte-exactly to their pre-apply state.
    code, text = dream.undo_edges(md, idx)
    assert code == 0, text
    for fname in stamped_files:
        with open(os.path.join(md, "archive", fname), "rb") as fh:
            assert fh.read() == before[fname], f"{fname}: undo must be byte-exact"
    assert all(s == "undone" for s in _states(md).values())
    verdict = check_dream_ledger(DoctorContext(md, os.path.dirname(md)))
    assert verdict["status"] == "ok", verdict


def test_archived_copy_drift_still_refuses_undo(dirs):
    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    os.makedirs(os.path.join(md, "archive"), exist_ok=True)
    os.rename(os.path.join(md, fname), os.path.join(md, "archive", fname))
    apath = os.path.join(md, "archive", fname)
    with open(apath, encoding="utf-8") as fh:
        text = fh.read()
    with open(apath, "w", encoding="utf-8") as fh:
        fh.write(text.replace("cofire=", "cofire=9", 1))  # hand-edit one stamp

    code, text = dream.undo_edges(md, idx, edge_id=edge_ids[0])
    assert code == 1
    assert "refus" in text or "drift" in text
    assert _states(md)[edge_ids[0]] == "active"  # untouched, per refuse-on-drift


def test_true_ghost_still_fails(dirs):
    md, td, idx = dirs
    fname, _edge_ids = _applied_corpus(md, td, idx)
    os.remove(os.path.join(md, fname))  # stamp gone EVERYWHERE — real corruption
    verdict = check_dream_ledger(DoctorContext(md, os.path.dirname(md)))
    assert verdict["status"] == "fail", verdict
    assert "MISMATCH" in verdict["message"]
