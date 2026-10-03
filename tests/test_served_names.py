"""OBS-3: a recall row records what was SERVED separately from what was collapsed.

The hook over-fetches by the floor size plus this session's cooldown set, then renders
floor members and recently-surfaced memories as one collapsed line each. Rows used to log
that whole pool as ``names``, and the rotation-surviving usage aggregates folded it in, so
every floor memory counted as "recalled" on every prompt and no memory could ever look
orphaned. ``names`` is now the served entries; ``collapsed`` carries the rest.
"""

from __future__ import annotations

import json
import os

from memory import build_index as B
from memory import recall as R
from memory.telemetry import read_usage_aggregates

from .test_recall import _write_corpus, _write_floor


def _rows(td):
    with open(os.path.join(td, "recall_events.jsonl"), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    td = str(tmp_path / ".memory-telemetry")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(
        md,
        {
            "feedback_no_backward_compat.md": "no backward compat one path refactor single pr",
            "reranker_voyage.md": "voyage rerank cross encoder primary reranker bm25 hybrid fallback",
        },
    )
    _write_floor(md, ["feedback_no_backward_compat"])
    B.build_index(md, idx)
    return md, idx, td


def test_floor_collapsed_names_are_not_logged_as_served(tmp_path, monkeypatch, capsys):
    md, idx, td = _setup(tmp_path, monkeypatch)
    assert R.main(["no backward compat refactor voyage reranker", "--memory-dir", md, "--index-dir", idx]) == 0
    assert "feedback_no_backward_compat" in capsys.readouterr().out  # rendered, collapsed

    row = _rows(td)[-1]
    assert row["names"] == ["reranker_voyage"]
    assert row["collapsed"] == ["feedback_no_backward_compat"]
    assert len(row["scores"]) == len(row["ranks"]) == len(row["names"])
    assert "feedback_no_backward_compat" not in read_usage_aggregates(td)["memories"]


def test_cooldown_collapsed_names_are_not_logged_as_served(tmp_path, monkeypatch, capsys):
    md, idx, td = _setup(tmp_path, monkeypatch)
    common = ["--memory-dir", md, "--index-dir", idx, "--session-id", "s-obs3"]
    assert R.main(["voyage reranker cross encoder"] + common) == 0
    assert R.main(["voyage reranker cross encoder fallback"] + common) == 0
    capsys.readouterr()
    first, second = _rows(td)[-2:]
    assert "reranker_voyage" in first["names"]
    assert "reranker_voyage" not in second["names"]
    assert "reranker_voyage" in second["collapsed"]


def test_a_row_with_nothing_collapsed_writes_no_collapsed_key(tmp_path, monkeypatch, capsys):
    md, idx, td = _setup(tmp_path, monkeypatch)
    assert R.main(["voyage reranker cross encoder", "--memory-dir", md, "--index-dir", idx]) == 0
    capsys.readouterr()
    assert "collapsed" not in _rows(td)[-1]
