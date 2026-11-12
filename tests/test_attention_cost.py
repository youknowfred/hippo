"""CLM-9: doctor joins attention ``full`` with the 30-day share of SessionStarts at the cap.

Gate: the warn line appears on a corpus with full attention and a meaningful share of
SessionStarts at the cap, and is absent on a calm corpus and on one under the cap.
"""

from __future__ import annotations

import json
import os
import time

from memory import doctor as D
from memory import telemetry_rollup as TR
from memory.doctor_checks_env import DoctorContext
from memory.doctor_checks_platform import check_attention_cost


def _ctx(tmp_path, monkeypatch, runs: int, at_cap: int, dropped=("floor",)):
    td = str(tmp_path / "tel")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    monkeypatch.delenv("HIPPO_ATTENTION", raising=False)
    md = tmp_path / "memory"
    md.mkdir(exist_ok=True)
    now = time.time()
    for i in range(runs):
        hit = i < at_cap
        TR.record_session_start(
            td, total=9_000 if hit else 1_000, cap=9_000,
            dropped=list(dropped) if hit else [], now=now,
        )
    return DoctorContext(memory_dir=str(md), repo_root=str(tmp_path))


def test_clm9_warns_when_full_and_at_the_cap(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch, runs=20, at_cap=7, dropped=("floor", "tend"))
    r = check_attention_cost(ctx)
    assert r["status"] == "warn"
    assert r["message"] == (
        "full attention is costing context: 7 of 20 SessionStarts in the last 30 days hit "
        "the cap, dropping floor (7), tend (7) — turn calm on: the plugin's "
        "calm_session_start option (from /plugin)."
    )


def test_clm9_names_the_env_override_when_it_forces_full(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch, runs=10, at_cap=1)
    monkeypatch.setenv("HIPPO_ATTENTION", "full")
    r = check_attention_cost(ctx)
    assert r["status"] == "warn"
    assert "1 of 10 SessionStarts" in r["message"]
    assert r["message"].endswith("set HIPPO_ATTENTION=calm (it is set to full here).")


def test_clm9_absent_on_a_calm_corpus(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch, runs=20, at_cap=20)
    with open(os.path.join(ctx.memory_dir, "hippo.json"), "w", encoding="utf-8") as fh:
        json.dump({"attention": "calm"}, fh)
    r = check_attention_cost(ctx)
    assert r == {"status": "ok", "message": "attention cost: calm, nothing to add."}


def test_clm9_absent_under_the_cap(tmp_path, monkeypatch):
    # below the share: 1 of 11 is under 10%
    r = check_attention_cost(_ctx(tmp_path, monkeypatch, runs=11, at_cap=1))
    assert r["status"] == "ok" and "costing" not in r["message"]
    assert "1/11 SessionStart(s) at the cap" in r["message"]


def test_clm9_absent_below_the_minimum_runs(tmp_path, monkeypatch):
    r = check_attention_cost(_ctx(tmp_path, monkeypatch, runs=9, at_cap=9))
    assert r["status"] == "ok" and "costing" not in r["message"]


def test_clm9_no_rollups_is_ok(tmp_path, monkeypatch):
    r = check_attention_cost(_ctx(tmp_path, monkeypatch, runs=0, at_cap=0))
    assert r["status"] == "ok"
    assert "0/0" in r["message"]


def test_clm9_registered_right_after_attention():
    labels = [label for label, _ in D.CHECKS]
    assert labels.index("attention_cost") == labels.index("attention") + 1
    assert D.check_attention_cost is check_attention_cost
