"""OBS-11: doctor names the top reason recalls ran BM25-only, with its remedy in plain words."""

from __future__ import annotations

import re
import time

from memory import doctor as D
from memory import telemetry_rollup as TR
from memory.doctor_checks_env import DoctorContext
from memory.recall_fallback import REMEDY, check_bm25_reasons


def _ctx(tmp_path, monkeypatch):
    td = tmp_path / "telemetry"
    td.mkdir()
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(td))
    md = tmp_path / "memory"
    md.mkdir()
    return DoctorContext(str(md), str(tmp_path)), str(td)


def test_the_check_is_in_doctors_table():
    assert ("bm25_reasons", check_bm25_reasons) in D.CHECKS


def test_no_recalls_yet_is_ok(tmp_path, monkeypatch):
    ctx, _td = _ctx(tmp_path, monkeypatch)
    r = check_bm25_reasons(ctx)
    assert r["status"] == "ok" and "none" in r["message"]


def test_no_bm25_recall_is_ok_and_says_none(tmp_path, monkeypatch):
    ctx, td = _ctx(tmp_path, monkeypatch)
    TR.record_prompt(td, trigger="human", ran_recall=True, backend="dense+bm25")
    r = check_bm25_reasons(ctx)
    assert r == {"status": "ok", "message": "BM25-only recalls (30 days): none of 1 recalls that ran."}


def test_names_the_top_reason_with_its_remedy_and_stays_informational(tmp_path, monkeypatch):
    ctx, td = _ctx(tmp_path, monkeypatch)
    for reason in ("dense_timeout", "dense_timeout", "model_not_warmed"):
        TR.record_prompt(td, trigger="human", ran_recall=True, backend="bm25", backend_reason=reason)
    TR.record_prompt(td, trigger="human", ran_recall=True, backend="dense+bm25")
    r = check_bm25_reasons(ctx)
    assert r["status"] == "ok"
    msg = r["message"]
    assert msg.startswith("BM25-only recalls (30 days): 3 of 4 recalls that ran; top reason dense_timeout (2): ")
    assert REMEDY["dense_timeout"] in msg and "model_not_warmed (1)" in msg
    assert "python -m" not in msg and not re.search(r"\b[A-Z]{2,4}-\d+\b", msg)


def test_rows_from_before_reasons_are_counted_as_unrecorded(tmp_path, monkeypatch):
    ctx, td = _ctx(tmp_path, monkeypatch)
    old = time.time() - 3 * 86400
    TR.record_prompt(td, trigger="human", ran_recall=True, backend="bm25", now=old)
    acc = TR._read_json(f"{td}/{TR._TODAY_NAME}")
    acc["hook"].pop("backend_reason")  # what a row written before this release looks like
    import json

    with open(f"{td}/{TR._TODAY_NAME}", "w", encoding="utf-8") as fh:
        json.dump(acc, fh)
    TR.record_prompt(td, trigger="human", ran_recall=True, backend="bm25", backend_reason="no_dense_index")
    msg = check_bm25_reasons(ctx)["message"]
    assert "2 of 2" in msg and "top reason no_dense_index (1)" in msg
    assert "1 recorded before reasons were kept" in msg


def test_every_remedy_is_plain_words():
    for token, text in REMEDY.items():
        assert "python -m" not in text and not re.search(r"\b[A-Z]{2,4}-\d+\b", text), token
