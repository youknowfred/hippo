"""SRF-7's cofire floor: a completion edge needs the text mention AND an observed co-recall.

Until this, a completion was exempt from the co-fire threshold, so the linker applied an
edge whenever a body named another memory, even when recall had never returned the pair
together (cofire 0.00). Ruling V2Q-27 sets the floor at "any nonzero cofire" and asks that
the edges already applied below it be LISTED for one reviewed undo, never undone
automatically.

Pinned here:
  - ``apply_eligible``: a completion at cofire 0 is not eligible; any cofire above 0 is,
    whatever the bridge/refines threshold says;
  - an apply pass skips a below-floor completion and leaves its source file untouched;
  - edges applied before the floor are listed (``dream --below-floor``), and the apply
    pass's digest names their count and the two commands, but never undoes them;
  - ``dream --undo-below-floor`` reverts exactly those edges, byte for byte, and stamps
    the ledger line with why;
  - a pair undone by the floor may be applied again once it has co-fired; a pair undone
    by hand still never auto re-applies.
"""

from __future__ import annotations

import os

import pytest

import memory.dream as dream
import memory.dream_config as dream_config
import memory.dream_floor as dream_floor
from memory.dream_cli import main as dream_main

from .test_dream import _bridge_corpus, _seed_sessions, _snapshot_md
from .test_dream_apply import _snapshot_corpus, _stub_discover


@pytest.fixture(autouse=True)
def _bm25_only(monkeypatch):
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


def _completion(source, target, cofire):
    return {
        "kind": "completion", "source": source, "target": target, "cofire": cofire,
        "query": "q", "mutual": False, "signal": "body-mention", "distance": 2,
    }


def _apply_before_the_floor(monkeypatch, md, idx, td, candidates):
    """Apply ``candidates`` the way the linker did before the floor existed."""
    _stub_discover(monkeypatch, md, candidates)
    monkeypatch.setattr(dream_config, "COMPLETION_MIN_COFIRE", -1.0)
    code, _ = dream.run_apply_pass(md, idx, td)
    assert code == 0
    monkeypatch.setattr(dream_config, "COMPLETION_MIN_COFIRE", 0.0)


# --------------------------------------------------------------------------- #
# The bar
# --------------------------------------------------------------------------- #
def test_a_completion_needs_any_nonzero_cofire():
    assert not dream.apply_eligible({"kind": "completion", "cofire": 0.0}, theta=0.9)
    assert not dream.apply_eligible({"kind": "completion", "cofire": None}, theta=0.9)
    assert not dream.apply_eligible({"kind": "completion"}, theta=0.9)
    assert not dream.apply_eligible({"kind": "completion", "cofire": "junk"}, theta=0.9)
    # Any observed co-recall clears it, well under the bridge/refines threshold.
    assert dream.apply_eligible({"kind": "completion", "cofire": 0.01}, theta=0.9)
    assert dream.apply_eligible({"kind": "completion", "cofire": 0.29}, theta=0.9)
    assert dream.apply_eligible({"kind": "completion", "cofire": 1.0}, theta=0.9)


def test_the_other_kinds_keep_their_bar():
    assert dream.apply_eligible({"kind": "refines", "cofire": 0.91}, theta=0.9)
    assert not dream.apply_eligible({"kind": "refines", "cofire": 0.89}, theta=0.9)
    assert dream.apply_eligible({"kind": "bridge", "cofire": 0.95, "mutual": True}, theta=0.9)
    assert not dream.apply_eligible({"kind": "bridge", "cofire": 0.95, "mutual": False}, theta=0.9)


# --------------------------------------------------------------------------- #
# The apply pass
# --------------------------------------------------------------------------- #
def test_apply_skips_a_completion_that_never_co_fired(dirs, monkeypatch):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    before = _snapshot_corpus(md)
    _stub_discover(monkeypatch, md, [
        _completion("alpha", "charlie", 0.0),
        _completion("zulu", "bravo", 0.4),
    ])
    code, _ = dream.run_apply_pass(md, idx, td)
    assert code == 0
    ledger = dream.read_apply_ledger(md)
    assert [(e["source"], e["target"]) for e in ledger] == [("zulu", "bravo")]
    after = _snapshot_corpus(md)
    assert after["alpha.md"] == before["alpha.md"], "the below-floor source must be untouched"
    assert after["zulu.md"] != before["zulu.md"]


# --------------------------------------------------------------------------- #
# Edges applied before the floor: listed, never undone on their own
# --------------------------------------------------------------------------- #
def test_edges_applied_before_the_floor_are_listed(dirs, monkeypatch):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    assert dream_floor.below_floor_edges(md) == []
    assert "no applied" in dream_floor.render_below_floor(md)
    _apply_before_the_floor(monkeypatch, md, idx, td, [
        _completion("alpha", "charlie", 0.0),
        _completion("zulu", "bravo", 0.4),
    ])
    below = dream_floor.below_floor_edges(md)
    assert [(e["source"], e["target"]) for e in below] == [("alpha", "charlie")]
    text = dream_floor.render_below_floor(md)
    assert "1 applied" in text and below[0]["edge_id"] in text
    assert "hippo dream --undo-below-floor" in text
    assert "zulu" not in text, "an edge above the floor is not on the list"


def test_an_apply_pass_names_them_and_leaves_them_applied(dirs, monkeypatch):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    _apply_before_the_floor(monkeypatch, md, idx, td, [_completion("alpha", "charlie", 0.0)])
    stamped = _snapshot_corpus(md)
    _stub_discover(monkeypatch, md, [])
    code, digest = dream.run_apply_pass(md, idx, td)
    assert code == 0
    assert "hippo dream --below-floor" in digest and "hippo dream --undo-below-floor" in digest
    assert _snapshot_corpus(md) == stamped, "listing is all a pass does: nothing is undone"
    assert [e["state"] for e in dream.read_apply_ledger(md)] == ["active"]


def test_a_pass_with_nothing_below_the_floor_says_nothing_about_it(dirs, monkeypatch):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    _stub_discover(monkeypatch, md, [_completion("zulu", "bravo", 0.4)])
    code, digest = dream.run_apply_pass(md, idx, td)
    assert code == 0
    assert "below-floor" not in digest


# --------------------------------------------------------------------------- #
# The one reviewed undo
# --------------------------------------------------------------------------- #
def test_the_reviewed_undo_reverts_exactly_the_below_floor_edges(dirs, monkeypatch):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    pristine = _snapshot_corpus(md)
    _apply_before_the_floor(monkeypatch, md, idx, td, [
        _completion("alpha", "charlie", 0.0),
        _completion("zulu", "bravo", 0.4),
    ])
    stamped = _snapshot_corpus(md)
    code, report = dream_floor.undo_below_floor(md, idx)
    assert code == 0 and "reverted 1 edge(s)" in report
    after = _snapshot_corpus(md)
    assert after["alpha.md"] == pristine["alpha.md"], "byte-for-byte restore of the source"
    assert after["zulu.md"] == stamped["zulu.md"], "the edge above the floor stays applied"
    by_pair = {(e["source"], e["target"]): e for e in dream.read_apply_ledger(md)}
    assert by_pair[("alpha", "charlie")]["state"] == "undone"
    assert by_pair[("alpha", "charlie")]["retracted_by"] == dream_floor.RETRACTED_BY
    assert by_pair[("zulu", "bravo")]["state"] == "active"
    assert dream_floor.below_floor_edges(md) == []
    # Running it again is a no-op that says so.
    code, report = dream_floor.undo_below_floor(md, idx)
    assert code == 0 and "no applied" in report


def test_a_hand_edited_stamp_is_refused_and_reported(dirs, monkeypatch):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    _apply_before_the_floor(monkeypatch, md, idx, td, [_completion("alpha", "charlie", 0.0)])
    path = os.path.join(md, "alpha.md")
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace("edge=", "edge=x"))
    code, report = dream_floor.undo_below_floor(md, idx)
    assert code == 1 and "✘" in report
    assert [e["state"] for e in dream.read_apply_ledger(md)] == ["active"]


# --------------------------------------------------------------------------- #
# After the undo: evidence may bring a pair back; a hand undo still stands
# --------------------------------------------------------------------------- #
def test_a_pair_undone_by_the_floor_may_return_once_it_co_fires(dirs, monkeypatch):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    _apply_before_the_floor(monkeypatch, md, idx, td, [_completion("alpha", "charlie", 0.0)])
    assert dream_floor.undo_below_floor(md, idx)[0] == 0
    # Still no co-recall: the floor keeps it out.
    _stub_discover(monkeypatch, md, [_completion("alpha", "charlie", 0.0)])
    dream.run_apply_pass(md, idx, td)
    assert [e["state"] for e in dream.read_apply_ledger(md)] == ["undone"]
    # Recall has now returned the pair together: the linker may apply it. (A real pass has
    # its own timestamped id; the shared stub's fixed one would reuse the undone edge's id.)
    later = {"status": "ok", "reason": "", "pass_id": "plater", "stats": {},
             "candidates": [_completion("alpha", "charlie", 0.5)],
             "soak": {"distinct_sessions": 7, "gate_met": True}}
    monkeypatch.setattr(dream, "discover", lambda *a, **kw: later)
    code, digest = dream.run_apply_pass(md, idx, td)
    assert code == 0 and "never auto re-applied" not in digest
    states = [e["state"] for e in dream.read_apply_ledger(md)]
    assert states == ["undone", "active"]


def test_a_pair_undone_by_hand_still_never_auto_re_applies(dirs, monkeypatch):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    _stub_discover(monkeypatch, md, [_completion("alpha", "charlie", 0.5)])
    dream.run_apply_pass(md, idx, td)
    assert dream.undo_edges(md, idx)[0] == 0
    code, digest = dream.run_apply_pass(md, idx, td)
    assert code == 0 and "never auto re-applied" in digest
    assert [e["state"] for e in dream.read_apply_ledger(md)] == ["undone"]


# --------------------------------------------------------------------------- #
# The CLI
# --------------------------------------------------------------------------- #
def test_cli_lists_and_undoes(dirs, monkeypatch, capsys):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    _apply_before_the_floor(monkeypatch, md, idx, td, [_completion("alpha", "charlie", 0.0)])
    stamped = _snapshot_md(md)
    assert dream_main(["--memory-dir", md, "--index-dir", idx, "--below-floor"]) == 0
    out = capsys.readouterr().out
    assert "1 applied" in out and "alpha" in out
    assert _snapshot_md(md) == stamped, "the listing is read-only"
    assert dream_main(["--memory-dir", md, "--index-dir", idx, "--undo-below-floor"]) == 0
    assert "reverted 1 edge(s)" in capsys.readouterr().out
    assert dream_floor.below_floor_edges(md) == []
