"""DRM-7 — a GHOST dream edge can be retired by a supported verb.

The live incident (2026-09-17, em-growth-labs, GRO-1745): doctor reported "dream stamp/
ledger MISMATCH — 3 active ledger edge(s) with no on-disk stamp" and named two reconcile
routes that were BOTH shut:

  - one source memory had been deleted by a corpus compaction (a plain delete, not
    ``archive_memory``) — no file to undo in, no ``archive/`` copy to fall back to;
  - two ledger rows were committed while their stamped lines never reached a commit, and
    a later hand rewrite of the file dropped the machine-managed block — so git history
    held no bytes to restore, and ``--undo`` (byte-exact) refused on manual drift.

The ✘ was permanent, and hand-editing the ledger is the one thing its contract forbids.
Pinned here: ``retire_ghost_edge`` appends the superseding ``state: "undone"`` line ONLY
after proving the stamp is absent from the corpus root AND ``archive/``; it is per-edge
(no bulk form exists to call); it refuses a live stamp toward ``--undo``; and doctor names
each ghost's cause plus the verb.
"""

from __future__ import annotations

import inspect
import json
import os

import pytest

import memory.dream as dream
from memory.doctor_checks_corpus import check_dream_ledger
from memory.doctor_checks_env import DoctorContext
from memory.dream_apply import edge_source_location

from .test_archive_dream import _applied_corpus, _states


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


def _doctor(md):
    return check_dream_ledger(DoctorContext(md, os.path.dirname(md)))


def _ledger_lines(md):
    with open(os.path.join(md, "dream-ledger.jsonl"), encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def _drop_dream_block(path):
    """The fold-ritual shape: a hand rewrite that loses every machine-stamped line."""
    with open(path, encoding="utf-8") as fh:
        kept = [ln for ln in fh.read().split("\n") if "dream:" not in ln]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(kept))


def test_source_deleted_ghost_retires_and_doctor_goes_green(dirs):
    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    # Every active row stamped in this file becomes a ghost; rows in OTHER files stay live.
    os.remove(os.path.join(md, fname))  # a compaction's plain delete — no archive copy

    verdict = _doctor(md)
    assert verdict["status"] == "fail", verdict
    assert "DELETED" in verdict["message"] and fname in verdict["message"]
    assert "--retire-ghost" in verdict["message"] and "retire_ghost" in verdict["message"]

    # The documented byte-exact route is genuinely shut — this is the gap.
    code, text = dream.undo_edges(md, idx, edge_id=edge_ids[0])
    assert code == 1 and "unreadable" in text

    before = _ledger_lines(md)
    for eid in edge_ids:
        code, text = dream.retire_ghost_edge(md, eid, reason="compaction deleted the source")
        assert code == 0, text
        assert eid in text and "neither the corpus nor archive/" in text
    after = _ledger_lines(md)
    # Append-only: history intact, exactly one superseding line per retired edge.
    assert after[: len(before)] == before
    added = after[len(before):]
    assert [r["edge_id"] for r in added] == edge_ids
    for row in added:
        assert row["state"] == "undone" and row["retired_ghost"] is True
        assert row["retire_cause"] == "source-deleted"
        assert row["retire_reason"] == "compaction deleted the source"
        assert row["undone_at_ts"]
    states = _states(md)
    assert all(states[eid] == "undone" for eid in edge_ids)
    verdict = _doctor(md)
    assert verdict["status"] == "ok", verdict


def test_stamp_missing_ghost_retires_with_its_own_cause(dirs):
    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    _drop_dream_block(os.path.join(md, fname))

    verdict = _doctor(md)
    assert verdict["status"] == "fail", verdict
    assert f"stamp missing from {fname}" in verdict["message"]
    assert "DELETED" not in verdict["message"]

    code, text = dream.undo_edges(md, idx, edge_id=edge_ids[0])
    assert code == 1 and "manual drift" in text  # the designed guard, not an obstacle

    code, text = dream.retire_ghost_edge(md, edge_ids[0])
    assert code == 0, text
    row = _ledger_lines(md)[-1]
    assert row["retire_cause"] == "stamp-missing"
    assert fname in row["retire_reason"]  # no reason given → the proven cause is recorded
    # The source file is untouched by a retire — only the ledger moved.
    with open(os.path.join(md, fname), encoding="utf-8") as fh:
        assert "dream:" not in fh.read()


def test_a_live_stamp_is_never_retired(dirs):
    md, td, idx = dirs
    _fname, edge_ids = _applied_corpus(md, td, idx)
    before = _ledger_lines(md)
    code, text = dream.retire_ghost_edge(md, edge_ids[0])
    assert code == 1
    assert "not a ghost" in text and "--undo" in text
    assert _ledger_lines(md) == before
    assert _states(md)[edge_ids[0]] == "active"


def test_a_stamp_under_archive_is_never_retired(dirs):
    """The legacy-trap shape (hand ``git mv`` into archive/) is INERT, not a ghost — its
    bytes exist, so the archive-aware ``--undo`` is the verb and retire must refuse."""
    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    os.makedirs(os.path.join(md, "archive"))
    os.rename(os.path.join(md, fname), os.path.join(md, "archive", fname))
    before = _ledger_lines(md)
    code, text = dream.retire_ghost_edge(md, edge_ids[0])
    assert code == 1 and os.path.join("archive", fname) in text
    assert _ledger_lines(md) == before


def test_a_stamp_copied_into_another_memory_still_blocks_the_retire(dirs):
    """Absence is proven over the WHOLE reconciled surface, not just the recorded source —
    exactly the set doctor scans, so the verb retires what doctor calls a ghost and nothing
    else."""
    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    with open(os.path.join(md, fname), encoding="utf-8") as fh:
        stamped = next(ln for ln in fh.read().split("\n") if f"edge={edge_ids[0]}" in ln)
    os.remove(os.path.join(md, fname))
    other = next(n for n in sorted(os.listdir(md)) if n.endswith(".md"))
    with open(os.path.join(md, other), "a", encoding="utf-8") as fh:
        fh.write("\n" + stamped + "\n")
    code, text = dream.retire_ghost_edge(md, edge_ids[0])
    assert code == 1 and other in text


def test_prose_that_merely_mentions_the_edge_id_is_not_a_stamp(dirs):
    """A memory DOCUMENTING the incident names the edge id in prose (the field corpus has
    one). Only a ``<!-- dream: … edge=<id>`` line is a stamp — doctor's own definition."""
    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    os.remove(os.path.join(md, fname))
    other = next(n for n in sorted(os.listdir(md)) if n.endswith(".md"))
    with open(os.path.join(md, other), "a", encoding="utf-8") as fh:
        fh.write(f"\nThe stray row is edge={edge_ids[0]} in the ledger.\n")
    code, text = dream.retire_ghost_edge(md, edge_ids[0])
    assert code == 0, text


def test_only_an_active_known_edge_retires(dirs):
    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    assert dream.retire_ghost_edge(md, "")[0] == 1
    code, text = dream.retire_ghost_edge(md, "p19990101000000-e9")
    assert code == 1 and "no ledger edge" in text
    os.remove(os.path.join(md, fname))
    assert dream.retire_ghost_edge(md, edge_ids[0])[0] == 0
    before = _ledger_lines(md)
    code, text = dream.retire_ghost_edge(md, edge_ids[0])  # already undone
    assert code == 1 and "already" in text
    assert _ledger_lines(md) == before


def test_an_unreadable_file_means_absence_is_unproven(dirs):
    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    os.remove(os.path.join(md, fname))
    with open(os.path.join(md, "binary-junk.md"), "wb") as fh:
        fh.write(b"\xff\xfe\x00 not utf-8 \xff")
    before = _ledger_lines(md)
    code, text = dream.retire_ghost_edge(md, edge_ids[0])
    assert code == 1 and "unproven" in text and "binary-junk.md" in text
    assert _ledger_lines(md) == before


def test_there_is_no_bulk_form():
    """Per-edge by SIGNATURE: one edge id, no list, no since-window, no 'all'."""
    params = inspect.signature(dream.retire_ghost_edge).parameters
    assert list(params) == ["memory_dir", "edge_id", "reason"]
    assert params["edge_id"].annotation in (str, "str")


def test_a_retired_pair_is_a_standing_verdict(dirs):
    """``state: "undone"`` is what ``run_apply_pass``'s re-apply guard reads — a retired
    ghost pair is never silently re-stamped by the next pass."""
    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    _drop_dream_block(os.path.join(md, fname))
    for eid in edge_ids:
        assert dream.retire_ghost_edge(md, eid)[0] == 0
    retired = {
        (e["source"], e["target"]) for e in dream.read_apply_ledger(md) if e["edge_id"] in edge_ids
    }
    code, _ = dream.run_apply_pass(md, idx, td)
    assert code == 0
    live = {
        (e["source"], e["target"])
        for e in dream.read_apply_ledger(md)
        if e.get("state") == "active"
    }
    assert not (retired & live)


def test_edge_source_location_names_the_three_places(dirs):
    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    edge = next(e for e in dream.read_apply_ledger(md) if e["edge_id"] == edge_ids[0])
    assert edge_source_location(md, edge) == (fname, "corpus")
    os.makedirs(os.path.join(md, "archive"))
    os.rename(os.path.join(md, fname), os.path.join(md, "archive", fname))
    assert edge_source_location(md, edge) == (fname, "archive")
    os.remove(os.path.join(md, "archive", fname))
    assert edge_source_location(md, edge) == (fname, "gone")


def test_cli_surface_reaches_the_verb(dirs, capsys):
    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    os.remove(os.path.join(md, fname))
    rc = dream.main(["--memory-dir", md, "--retire-ghost", edge_ids[0], "--reason", "cli"])
    assert rc == 0
    assert "retired" in capsys.readouterr().out
    assert _ledger_lines(md)[-1]["retire_reason"] == "cli"
    # A live stamp exits non-zero on the CLI too (the refusal is the exit status).
    assert dream.main(["--memory-dir", md, "--retire-ghost", "p19990101000000-e9"]) == 1


def test_mcp_surface_reaches_the_verb(dirs, monkeypatch):
    import memory.mcp_tools_setup as T

    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    os.remove(os.path.join(md, fname))
    monkeypatch.setattr("memory.provenance.resolve_dirs", lambda: (md, os.path.dirname(md)))
    text = T._tool_dream({"action": "retire_ghost", "edge_id": edge_ids[0], "reason": "mcp"})
    assert "retired" in text, text
    assert _ledger_lines(md)[-1]["retire_reason"] == "mcp"
    assert "edge id is required" in T._tool_dream({"action": "retire_ghost"})


def test_mcp_schema_names_the_action():
    from memory.mcp_schemas import _TOOLS

    tool = next(t for t in _TOOLS if t["name"] == "dream")
    props = tool["inputSchema"]["properties"]
    assert "retire_ghost" in props["action"]["enum"]
    assert "reason" in props and "retire_ghost" in tool["description"]
