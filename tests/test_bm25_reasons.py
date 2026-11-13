"""OBS-11: every BM25-only hook recall carries a reason token beside its backend label."""

from __future__ import annotations

import io
import json
import os
import time

import numpy as np
import pytest

from memory import build_index as B
from memory import recall as R
from memory import recall_fallback as F
from memory import recall_rank as RR
from memory import telemetry_rollup as TR
from memory.build_index import DenseTimeout, DenseUnavailable, LoadedIndex

T0 = time.mktime((2026, 10, 1, 12, 0, 0, 0, 0, -1))


def _index(*, dense_ready=True, with_matrix=True, model=None, **extra):
    entries = [{"name": "a", "row": 0, "tokens": ["alpha"]}, {"name": "b", "row": 1, "tokens": ["beta"]}]
    manifest = {"entries": entries, "dense_ready": dense_ready, "model": model or B.DEFAULT_MODEL, **extra}
    dense = np.eye(2, 4, dtype=np.float32) if with_matrix else None
    return LoadedIndex(manifest, dense)


def _why(index, monkeypatch, embed=None):
    if embed is not None:
        monkeypatch.setattr(RR, "embed_query", embed)
    why: list = []
    assert RR._dense_rank_rows("alpha", index, why_out=why) == []
    return why


@pytest.fixture
def dense_on(monkeypatch):
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.delenv("HIPPO_DISABLE", raising=False)


def test_each_no_matrix_branch_names_its_cause(monkeypatch, dense_on):
    assert _why(_index(dense_ready=False, with_matrix=False), monkeypatch) == ["no_dense_index"]
    assert _why(_index(with_matrix=False), monkeypatch) == ["dense_matrix_unusable"]
    fused = _index(dense_ready=False, with_matrix=False, fused_without_dense=True)
    assert _why(fused, monkeypatch) == ["fused_without_dense"]
    assert _why(_index(model="some/other-model"), monkeypatch) == ["model_mismatch"]
    monkeypatch.setenv("HIPPO_DISABLE", "dense")
    assert _why(_index(with_matrix=False), monkeypatch) == ["dense_disabled"]


def _raiser(exc):
    def embed(*_a, **_k):
        raise exc
    return embed


@pytest.mark.parametrize("exc,token", [
    (DenseTimeout(), "dense_timeout"),
    (DenseUnavailable("not cached", "model_not_warmed"), "model_not_warmed"),
    (DenseUnavailable("off", "dense_disabled"), "dense_disabled"),
    (ValueError("boom"), "dense_error"),
    (ImportError("no fastembed"), "dense_error"),
])
def test_each_exception_names_its_cause(monkeypatch, dense_on, exc, token):
    assert _why(_index(), monkeypatch, embed=_raiser(exc)) == [token]


def test_the_offline_model_loader_raises_a_typed_cause(monkeypatch, dense_on, tmp_path):
    monkeypatch.setattr(B, "ensure_fastembed_cache_path", lambda: str(tmp_path))
    monkeypatch.setattr(B, "_fastembed_model_cached", lambda _d: False)
    monkeypatch.setattr(B, "_MODEL_CACHE", {})
    with pytest.raises(DenseUnavailable) as ei:
        B._get_model(allow_download=False)
    assert ei.value.reason == "model_not_warmed" and isinstance(ei.value, RuntimeError)
    monkeypatch.setenv("HIPPO_DISABLE", "dense")
    with pytest.raises(DenseUnavailable) as ei:
        B._get_model(allow_download=False)
    assert ei.value.reason == "dense_disabled"


def test_a_lane_that_ran_appends_nothing(monkeypatch, dense_on):
    monkeypatch.setattr(RR, "embed_query", lambda *_a, **_k: np.array([0, 0, 1, 0], dtype=np.float32))
    why: list = []
    RR._dense_rank_rows("alpha", _index(), why_out=why)
    assert why == []


def test_result_reason_covers_every_bm25_shape():
    bm = [{"name": "a", "backend": "bm25"}]
    assert F.result_reason(bm, []) == "no_dense_match"
    assert F.result_reason(bm, ["dense_timeout"]) == "dense_timeout"
    assert F.result_reason(bm, ["something-new"]) == "unknown"
    assert F.result_reason([{"name": "r", "backend": "bm25", "via": "rules"}], ["no_dense_index"]) == "rules_only"
    assert F.result_reason([{"name": "a", "backend": "dense+bm25"}], []) is None
    assert F.result_reason([], ["no_dense_index"]) is None
    assert set(F.REMEDY) == set(F.REASONS)


def _corpus(tmp_path):
    md = str(tmp_path / "memory")
    os.makedirs(md)
    with open(os.path.join(md, "zebra.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: zebra\ndescription: zebra canary deploy rollout\nmetadata:\n  type: project\n---\nb\n")
    return md


def _hook(monkeypatch, md, idx, prompt="zebra canary deploy rollout", sid="s"):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": prompt, "session_id": sid})))
    assert R.main(["--stdin-json", "--memory-dir", md, "--index-dir", idx]) == 0


def _events(td):
    with open(os.path.join(td, "recall_events.jsonl"), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_the_hook_records_the_reason_on_the_event_and_in_the_rollup(tmp_path, monkeypatch, capsys):
    td = str(tmp_path / "telemetry")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    md = _corpus(tmp_path)
    idx = str(tmp_path / "idx")
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    B.build_index(md, idx)  # a BM25-only index
    _hook(monkeypatch, md, idx)
    monkeypatch.delenv("HIPPO_DISABLE_DENSE")
    monkeypatch.delenv("HIPPO_DISABLE", raising=False)
    _hook(monkeypatch, md, idx)  # dense allowed now, but the index has no vectors
    _hook(monkeypatch, md, idx, prompt="quantum marmalade xylophone", sid="s2")  # abstains: no reason
    capsys.readouterr()
    ev = _events(td)
    assert [e["backend"] for e in ev] == ["bm25", "bm25", "none"]
    assert [e.get("backend_reason") for e in ev] == ["dense_disabled", "no_dense_index", None]
    [row] = TR.read_rollups(td)
    assert row["hook"]["backend"] == {"bm25": 2, "none": 1}
    assert row["hook"]["backend_reason"] == {"dense_disabled": 1, "no_dense_index": 1}


def test_rollup_counts_reasons_only_for_bm25_and_defaults_to_unknown(tmp_path):
    td = str(tmp_path)
    TR.record_prompt(td, trigger="human", ran_recall=True, backend="bm25", backend_reason="dense_timeout", now=T0)
    TR.record_prompt(td, trigger="human", ran_recall=True, backend="bm25", now=T0)
    TR.record_prompt(td, trigger="human", ran_recall=True, backend="dense+bm25", now=T0)
    TR.record_prompt(td, trigger="human", ran_recall=True, backend="none", now=T0)
    [row] = TR.read_rollups(td, now=T0)
    assert row["hook"]["backend_reason"] == {"dense_timeout": 1, "unknown": 1}


def test_a_corpus_with_no_bm25_recall_gains_no_reason_key(tmp_path):
    td = str(tmp_path)
    TR.record_prompt(td, trigger="human", ran_recall=True, backend="dense+bm25", now=T0)
    [row] = TR.read_rollups(td, now=T0)
    assert "backend_reason" not in row["hook"]


def test_old_rows_count_as_unrecorded():
    rows = [
        {"hook": {"backend": {"bm25": 5, "dense+bm25": 9}}},  # before reasons were kept
        {"hook": {"backend": {"bm25": 3}, "backend_reason": {"dense_timeout": 2, "no_dense_match": 1}}},
    ]
    assert F.window_reasons(rows) == {"unrecorded": 5, "dense_timeout": 2, "no_dense_match": 1}


def test_the_fused_index_marks_a_tier_that_cost_dense():
    from memory.recall_tiers import _merge_loaded_indexes

    project = _index()
    tier = LoadedIndex({"entries": [{"name": "c", "row": 0, "tokens": ["gamma"]}], "dense_ready": False}, None)
    merged = _merge_loaded_indexes([(project, "/p", "project"), (tier, "/u", "user")])
    assert merged.dense_ready is False and merged.manifest.get("fused_without_dense") is True
    both = _merge_loaded_indexes([(_index(dense_ready=False, with_matrix=False), "/p", "project"),
                                  (tier, "/u", "user")])
    assert "fused_without_dense" not in both.manifest
