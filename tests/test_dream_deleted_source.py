"""A memory deleted outside ``archive_memory`` retires its dream edges.

The field case (em-growth-labs, 2026-11-12): a fold committed by a session (a plain git
delete of 78 settled journals, their text moved into digests) left one ACTIVE dream-ledger
row whose source memory existed nowhere, and doctor failed on it until someone ran
``hippo dream --retire-ghost <edge-id>`` by hand. hippo's own promote move deletes the
project copy the same way.

Now such a row is retired by the write that deletes the memory (the promote move) or by
the next link pass (a delete hippo did not make), with the proof ``--retire-ghost`` asks
for: the source is in neither the corpus, ``archive/`` nor the outbox, and no readable
memory file carries the edge's stamp. A row whose source is still here but lost its stamp
is NOT retired automatically (a hand edit worth chasing stays a per-edge verdict), an
unreadable file retires nothing, and an untrusted corpus is never written.
"""

from __future__ import annotations

import os

import pytest

import memory.dream as dream
from memory import trust
from memory.dream_apply import retire_deleted_sources

from .test_archive_dream import _applied_corpus, _states
from .test_dream_retire_ghost import _doctor, _drop_dream_block, _ledger_lines, dirs  # noqa: F401


@pytest.fixture(autouse=True)
def _bm25_only_and_permissive_theta(monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("DREAM_COFIRE_THETA", "0.10")


def test_the_next_link_pass_retires_edges_whose_source_was_deleted(dirs):  # noqa: F811
    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    os.remove(os.path.join(md, fname))  # a fold's plain delete, outside archive_memory
    assert _doctor(md)["status"] == "fail"

    code, text = dream.run_apply_pass(md, idx, td)
    assert code == 0
    states = _states(md)
    assert all(states[e] == "undone" for e in edge_ids)
    rows = [r for r in _ledger_lines(md) if r.get("edge_id") in edge_ids and r.get("retired_ghost")]
    assert {r["retire_cause"] for r in rows} == {"source-deleted"}
    assert "source memory" in text and str(len(edge_ids)) in text
    assert _doctor(md)["status"] != "fail"


def test_the_helper_retires_only_the_named_file_when_asked(dirs):  # noqa: F811
    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    os.remove(os.path.join(md, fname))
    assert retire_deleted_sources(md, only_file="some-other-memory.md") == []
    assert all(_states(md)[e] == "active" for e in edge_ids)
    assert sorted(retire_deleted_sources(md, only_file=fname)) == sorted(edge_ids)
    assert retire_deleted_sources(md) == []  # nothing left: idempotent


def test_a_source_that_is_still_here_without_its_stamp_is_left_to_the_verb(dirs):  # noqa: F811
    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    _drop_dream_block(os.path.join(md, fname))  # stamp-missing: maybe a hand edit
    before = _ledger_lines(md)
    assert retire_deleted_sources(md) == []
    assert _ledger_lines(md) == before
    assert "--retire-ghost" in _doctor(md)["message"]


def test_a_stamp_anywhere_or_an_archived_source_blocks_it(dirs):  # noqa: F811
    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    with open(os.path.join(md, fname), encoding="utf-8") as fh:
        text = fh.read()
    os.makedirs(os.path.join(md, "archive"), exist_ok=True)
    os.rename(os.path.join(md, fname), os.path.join(md, "archive", fname))
    assert retire_deleted_sources(md) == []  # archived: inert, archive-aware --undo handles it
    os.remove(os.path.join(md, "archive", fname))
    stamps = [ln for ln in text.split("\n") if "<!-- dream:" in ln]
    with open(os.path.join(md, "holder.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: holder\ndescription: d\n---\n" + "\n".join(stamps) + "\n")
    assert retire_deleted_sources(md) == []  # every stamp lives on in another memory
    with open(os.path.join(md, "holder.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: holder\ndescription: d\n---\n" + stamps[0] + "\n")
    kept = [e for e in edge_ids if e in stamps[0]]
    assert sorted(retire_deleted_sources(md)) == sorted(set(edge_ids) - set(kept))


def test_an_outbox_copy_counts_as_the_source(dirs, monkeypatch):  # noqa: F811
    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    from memory import outbox

    box = str(os.path.join(os.path.dirname(md), "outbox-here"))
    os.makedirs(box)
    os.rename(os.path.join(md, fname), os.path.join(box, fname))
    monkeypatch.setattr(outbox, "present", lambda memory_dir: box)
    assert retire_deleted_sources(md) == []


def test_an_unreadable_file_retires_nothing(dirs, monkeypatch):  # noqa: F811
    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    os.remove(os.path.join(md, fname))
    other = next(n for n in sorted(os.listdir(md)) if n.endswith(".md") and n != "MEMORY.md")
    real_open = open

    def flaky(path, *a, **kw):
        if str(path).endswith(other):
            raise PermissionError(path)
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", flaky)
    assert retire_deleted_sources(md) == []
    monkeypatch.undo()
    assert all(_states(md)[e] == "active" for e in edge_ids)


def test_an_untrusted_corpus_is_not_written_by_the_link_pass(dirs, monkeypatch):  # noqa: F811
    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    os.remove(os.path.join(md, fname))
    before = _ledger_lines(md)
    monkeypatch.setattr(trust, "gate_repo_root", lambda memory_dir, repo_root=None: "/somewhere")
    monkeypatch.setattr(trust, "is_trusted", lambda root: False)
    code, _text = dream.run_apply_pass(md, idx, td)
    assert code == 1 and _ledger_lines(md) == before


def test_the_per_edge_verb_keeps_its_signature():
    import inspect

    assert list(inspect.signature(dream.retire_ghost_edge).parameters) == ["memory_dir", "edge_id", "reason"]


def test_the_promote_move_retires_the_edges_of_the_file_it_removes(dirs, tmp_path, monkeypatch):  # noqa: F811
    from memory import new_memory as N

    md, td, idx = dirs
    fname, edge_ids = _applied_corpus(md, td, idx)
    others = [e for e, st in _states(md).items() if st == "active" and e not in edge_ids]
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", str(tmp_path / "usertier"))
    r = N.promote_memory(fname[:-3], memory_dir=md, repo_root=os.path.dirname(md), force=True)
    assert r.get("promoted"), r
    assert not os.path.exists(os.path.join(md, fname))
    assert sorted(r["dream_edges_retired"]) == sorted(edge_ids)
    states = _states(md)
    assert all(states[e] == "undone" for e in edge_ids)
    assert all(states[e] == "active" for e in others)  # other files' edges are untouched
    assert _doctor(md)["status"] != "fail"
