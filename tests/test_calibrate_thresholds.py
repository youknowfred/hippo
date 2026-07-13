"""Tests for memory/calibrate_thresholds.py (RET-15) — the knee-ratio/dense-floor sweep.

Hermetic: a tmp corpus + tmp hard-set fixture, BM25-only (no fastembed needed) — mirrors
test_eval_recall.py's conventions.
"""

from __future__ import annotations

import os

from memory import build_index as B
from memory import calibrate_thresholds as C
from memory import eval_recall as E

_CORPUS = {
    "reranker_voyage.md": "voyage rerank cross encoder primary reranker hybrid bm25 fallback circuit breaker",
    "budget_envelope.md": "phase envelope budget authority synthesis tail reservation degradation",
    "excel_header.md": "excel parser header rescue inference noncanonical column layout capped calls",
    "canvas_pdf.md": "canvas pdf export gotenberg pypdf footnote dagger marker two pass",
}
_HARD_SET = [
    {"query": "which reranker model runs first on search candidates", "expected": ["reranker_voyage"]},
    {"query": "guaranteed time envelope for each pipeline phase", "expected": ["budget_envelope"]},
    {"query": "infer spreadsheet column headers when layout is unusual", "expected": ["excel_header"]},
    {"query": "render a document to pdf with headless chrome", "expected": ["canvas_pdf"]},
]


def _mem(name: str, description: str) -> str:
    return f'---\nname: {name}\ndescription: "{description}"\ntype: project\n---\nbody for {name}\n'


def _build_corpus(tmp_path):
    md = str(tmp_path / "memory")
    os.makedirs(md, exist_ok=True)
    for fname, desc in _CORPUS.items():
        with open(os.path.join(md, fname), "w", encoding="utf-8") as fh:
            fh.write(_mem(fname[:-3], desc))
    return md


def _write_hard_set(tmp_path):
    import yaml

    p = str(tmp_path / "hard_set.yaml")
    with open(p, "w", encoding="utf-8") as fh:
        yaml.safe_dump(_HARD_SET, fh)
    return p


def test_sweep_knee_ratio_restores_env_and_reports_every_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.delenv("HIPPO_KNEE_RATIO", raising=False)
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    hs = _write_hard_set(tmp_path)

    rows = C.sweep_knee_ratio(
        memory_dir=md, index_dir=idx, hard_set_path=hs, candidates=(0.3, 0.5, 0.7)
    )
    assert [r["ratio"] for r in rows] == [0.3, 0.5, 0.7]
    for r in rows:
        assert r["gates_ok"] is True
        assert r["recall@10"] == 1.0

    # the sweep must not leak HIPPO_KNEE_RATIO into the caller's environment afterward
    assert "HIPPO_KNEE_RATIO" not in os.environ


def test_sweep_knee_ratio_records_error_without_dropping_the_row(tmp_path, monkeypatch):
    """A candidate whose eval() call raises is recorded, not silently skipped."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    rows = C.sweep_knee_ratio(
        memory_dir=str(tmp_path / "does_not_exist"), candidates=(0.5,)
    )
    assert len(rows) == 1
    assert rows[0]["gates_ok"] is False


def test_sweep_dense_floor_skips_cleanly_when_dense_not_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)

    result = C.sweep_dense_floor(memory_dir=md, index_dir=idx)
    assert "skipped" in result
    assert "dense" in result["skipped"].lower()


def test_recommend_keeps_current_on_a_tie():
    rows = [
        {"ratio": 0.3, "mrr@10": 0.9, "gates_ok": True},
        {"ratio": 0.5, "mrr@10": 0.9, "gates_ok": True},
        {"ratio": 0.7, "mrr@10": 0.9, "gates_ok": True},
    ]
    rec = C._recommend(rows, current=0.5, key="ratio")
    assert rec["current_is_best"] is True
    assert set(rec["best"]) == {0.3, 0.5, 0.7}


def test_recommend_flags_a_clearly_better_candidate():
    rows = [
        {"ratio": 0.3, "mrr@10": 0.5, "gates_ok": True},
        {"ratio": 0.5, "mrr@10": 0.5, "gates_ok": True},
        {"ratio": 0.7, "mrr@10": 0.9, "gates_ok": True},
    ]
    rec = C._recommend(rows, current=0.5, key="ratio")
    assert rec["current_is_best"] is False
    assert rec["best"] == [0.7]


def test_recommend_none_when_nothing_clears_every_gate():
    rows = [{"ratio": 0.3, "mrr@10": None, "gates_ok": False}]
    rec = C._recommend(rows, current=0.5, key="ratio")
    assert rec["current_is_best"] is None
    assert rec["best"] == []


def test_format_report_never_raises_on_a_missing_corpus(tmp_path):
    out = C.format_report(memory_dir=str(tmp_path / "nope"))
    assert isinstance(out, str) and out  # some legible line, never a traceback


def test_format_report_end_to_end_recommends_keeping_current(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    hs = _write_hard_set(tmp_path)

    out = C.format_report(memory_dir=md, index_dir=idx, hard_set_path=hs)
    assert "knee ratio sweep" in out
    assert "RECOMMENDATION" in out
    assert "dense floor sweep: SKIPPED" in out


def test_cli_calibrate_flag_dispatches_without_running_normal_gates(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    hs = _write_hard_set(tmp_path)

    code = E.main(["--memory-dir", md, "--index-dir", idx, "--hard-set", hs, "--calibrate"])
    assert code == 0
    out = capsys.readouterr().out
    assert "knee ratio sweep" in out
    assert "RESULT:" not in out  # the normal gate-report footer must NOT also print
