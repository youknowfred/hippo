"""HOT-10: doctor's floor-calibration line, the cases beyond the stale-fingerprint one.

They sit here rather than in ``test_doctor.py`` because that file is at its size pin
(``tests/test_module_size.py``); the helpers are that module's own.
"""

from __future__ import annotations

from memory import doctor as D

from .conftest import write_file
from .test_doctor import _ctx, _mem, _seed, _write_sweep


def test_floor_calibration_current_fingerprint_compares(repo, memory_dir, monkeypatch, tmp_path):
    from memory import build_index as B
    from memory.eval_ledger import corpus_fingerprint

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed(memory_dir)
    write_file(memory_dir, "a.md", _mem("a", "note a"))
    idx = B.default_index_dir(memory_dir)
    B.build_index(memory_dir, idx)
    fp = corpus_fingerprint(B.load_index(idx))
    _write_sweep(memory_dir, monkeypatch, tmp_path, corpus_fingerprint=fp, recommended=0.61)
    r = D.check_floor_calibration(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "STALE" not in r["message"]
    assert "0.61" in r["message"]


def test_floor_calibration_other_model_is_stale(repo, memory_dir, monkeypatch, tmp_path):
    _seed(memory_dir)
    _write_sweep(memory_dir, monkeypatch, tmp_path, model="some/other-embedder")
    r = D.check_floor_calibration(_ctx(memory_dir, repo))
    assert r["status"] == "warn"
    assert "STALE" in r["message"] and "some/other-embedder" in r["message"]
    assert "hippo eval --floor-sweep" in r["message"]
    assert "\n" not in r["message"]
