"""The episode record separates what the prompt SERVED from what it collapsed.

The hook over-fetches by the floor size plus the session's cooldown set and renders floor
and cooldown members as collapse lines, but the episode buffer's ``recalled_names`` lists
that whole pool. Consumers reading it as "injected" counted every floor memory the pool
held as injected on every prompt. ``served_names`` (additive) is the served rows alone, the
recall ledger's own ``names``; ``recalled_names`` keeps its meaning for the readers that
want collapsed names too (the cooldown, the floor diet, the floor trim report). An old
record without ``served_names`` reads as before.
"""

from __future__ import annotations

import json
import os

from memory import build_index as B
from memory import recall as R
from memory.episode_names import served

from .test_recall import _write_corpus, _write_floor

_FLOORED = "feedback_no_backward_compat"
_SERVED = "reranker_voyage"


def _episodes(td):
    with open(os.path.join(td, "episode_buffer.jsonl"), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _write_episodes(td, rows):
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "episode_buffer.jsonl"), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    td = str(tmp_path / ".memory-telemetry")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md, {
        f"{_FLOORED}.md": "no backward compat one path refactor single pr",
        f"{_SERVED}.md": "voyage rerank cross encoder primary reranker bm25 hybrid fallback",
    })
    _write_floor(md, [_FLOORED])
    B.build_index(md, idx)
    return md, idx, td


def test_the_episode_records_served_names_apart_from_collapsed(tmp_path, monkeypatch, capsys):
    md, idx, td = _setup(tmp_path, monkeypatch)
    assert R.main(["no backward compat refactor voyage reranker", "--memory-dir", md, "--index-dir", idx]) == 0
    assert _FLOORED in capsys.readouterr().out  # rendered on the floor collapse line
    ep = _episodes(td)[-1]
    assert ep["served_names"] == [_SERVED]
    assert set(ep["recalled_names"]) == {_FLOORED, _SERVED}  # unchanged: the cooldown reads it


def test_the_cooldown_still_reads_collapsed_names(tmp_path, monkeypatch, capsys):
    md, idx, td = _setup(tmp_path, monkeypatch)
    common = ["--memory-dir", md, "--index-dir", idx, "--session-id", "s-cool"]
    for q in ("voyage reranker cross encoder", "voyage reranker fallback", "voyage reranker hybrid"):
        assert R.main([q] + common) == 0
    capsys.readouterr()
    first, second, third = _episodes(td)[-3:]
    assert _SERVED in first["served_names"]
    assert _SERVED not in second["served_names"] and _SERVED in second["recalled_names"]
    assert _SERVED not in third["served_names"]  # still cooling down


def test_served_falls_back_to_recalled_names_on_an_old_record():
    assert served({"recalled_names": ["a", "b"]}) == ["a", "b"]
    assert served({"recalled_names": ["a", "b"], "served_names": ["b"]}) == ["b"]
    assert served({"recalled_names": ["a"], "served_names": []}) == []
    assert served({}) == []


def _ep(sid, ts, served_names, collapsed, *, old=False):
    row = {"ts": ts, "session_id": sid, "query_preview": "a question about things",
           "recalled_names": served_names + collapsed, "head_commit": None}
    if not old:
        row["served_names"] = served_names
    return row


def test_hook_served_reads_served_names(tmp_path):
    from memory.outcome_opened import served_before

    td = str(tmp_path / "tel")
    _write_episodes(td, [_ep("s1", 100.0, ["served_one"], ["floor_one"]),
                         _ep("s2", 101.0, ["served_two"], ["floor_two"], old=True)])
    assert served_before(td, "s1", "served_one")
    assert not served_before(td, "s1", "floor_one")
    assert served_before(td, "s2", "floor_two")  # an old record reads as before


def test_co_recall_pairs_count_served_names(tmp_path):
    from memory.telemetry_mining import co_recall_pairs

    td = str(tmp_path / "tel")
    _write_episodes(td, [_ep(f"s{i}", 100.0 + i, ["a_mem", "b_mem"], ["floor_x", "floor_y"]) for i in range(6)])
    pairs = [p["pair"] for p in co_recall_pairs(td, min_sessions=2)]
    assert pairs == [["a_mem", "b_mem"]], pairs


def test_the_capture_seed_lists_served_names(tmp_path):
    from memory.capture import gather_session_context

    td = str(tmp_path / "tel")
    _write_episodes(td, [_ep("s1", 100.0, ["served_one"], ["floor_one"]),
                         _ep("s1", 101.0, ["served_two"], ["served_one", "floor_two"])])
    seed = gather_session_context("s1", telemetry_dir=td, include_hunks=False)
    assert seed["recalled_names"] == ["served_one", "served_two"]


def test_the_injection_join_counts_served_names_only(tmp_path, memory_dir):
    from memory.outcome import _injection_join

    td = str(tmp_path / "tel")
    _write_episodes(td, [_ep("s1", 100.0, ["served_one"], ["floor_one"])])
    keys = set(_injection_join(memory_dir, td))
    assert keys == {("s1", "served_one")}
