"""TND-7: the scheduled-sleep offer in the tend summary, and the floor sweep a sleep run
keeps current.

The offer: one line, only when the backlog is at least ten, no sleep run is recorded for
the corpus in seven days, and it has not shown today (the date it last showed is kept in
the corpus's sleep state, written only when it shows). It names the print command, never
installs anything, and never suggests the write-capable tier.

The sweep: a sleep run refreshes a STALE floor sweep with the cached model and never
downloads; a missing model, dense off, a BM25-only index or missing fixtures skip with the
reason named and counted; doctor's calibration line stops warning once it has run.
"""

from __future__ import annotations

import json
import os
import zlib
from datetime import datetime, timedelta, timezone

import numpy as np
import yaml

from memory import build_index as B
from memory import sleep as SL
from memory import sleep_offer as O
from memory import sleep_sweep as S
from memory import tend as T
from memory import tend_queue as Q
from memory.telemetry import default_telemetry_dir

_NOW = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)


def _state(md):
    return SL._read_state(default_telemetry_dir(md))


def _set_state(md, **kv):
    td = default_telemetry_dir(md)
    st = SL._read_state(td)
    st.update(kv)
    assert SL._write_state(td, st) is None


# --------------------------------------------------------------------------- #
# The offer
# --------------------------------------------------------------------------- #
def test_offer_needs_the_backlog(memory_dir):
    assert O.offer_line(memory_dir, O.BACKLOG_MIN - 1, now=_NOW, today="2026-11-12") is None
    assert "schedule_offer_shown_on" not in _state(memory_dir)  # nothing written below it


def test_offer_shows_once_a_day_and_names_only_the_print_command(memory_dir):
    line = O.offer_line(memory_dir, 36, now=_NOW, today="2026-11-12")
    assert line is not None
    assert line.startswith("36 items wait here")
    assert "`hippo sleep --print-schedule`" in line and "report-only" in line
    assert "installs nothing" in line and "once a day" in line
    for word in ("TIER_A", "tier", "--yes", "apply"):
        assert word not in line
    assert _state(memory_dir)["schedule_offer_shown_on"] == "2026-11-12"
    assert O.offer_line(memory_dir, 36, now=_NOW, today="2026-11-12") is None
    assert O.offer_line(memory_dir, 40, now=_NOW + timedelta(days=1), today="2026-11-13") is not None


def test_a_recent_sleep_run_suppresses_the_offer(memory_dir):
    _set_state(memory_dir, last_run_at=(_NOW - timedelta(days=2)).isoformat(timespec="seconds"))
    assert O.offer_line(memory_dir, 36, now=_NOW, today="2026-11-12") is None
    assert "schedule_offer_shown_on" not in _state(memory_dir)
    _set_state(memory_dir, last_run_at=(_NOW - timedelta(days=8)).isoformat(timespec="seconds"))
    assert O.offer_line(memory_dir, 36, now=_NOW, today="2026-11-12") is not None


def test_the_offer_keeps_the_sleep_state_a_sleep_run_reads(memory_dir):
    _set_state(memory_dir, last_run_at="2026-10-01T07:30:00+00:00", snooze_until="2026-10-02T00:00:00+00:00")
    assert O.offer_line(memory_dir, 12, now=_NOW, today="2026-11-12")
    st = _state(memory_dir)
    assert st["last_run_at"] == "2026-10-01T07:30:00+00:00"
    assert st["snooze_until"] == "2026-10-02T00:00:00+00:00"


def _fake_queue(n):
    pending = [{"id": f"capture:s{i}", "kind": "capture", "target": f"s{i}", "evidence": "e",
                "proposed": "p", "gate": "g"} for i in range(n)]
    return {"pending": pending, "held": [], "snoozed": [], "skipped": [], "counts": {"capture": n},
            "errors": {}}


def test_hippo_tend_prints_the_line_once_and_not_for_one_kind(repo, memory_dir, monkeypatch, capsys):
    monkeypatch.setattr(Q, "build_queue", lambda *a, **k: _fake_queue(12))
    assert T.main(["list", "--memory-dir", memory_dir, "--repo-root", repo]) == 0
    first = capsys.readouterr().out
    assert first.count("hippo sleep --print-schedule") == 1
    assert first.rstrip().endswith("(Shown once a day.)")
    assert T.main(["list", "--memory-dir", memory_dir, "--repo-root", repo]) == 0
    assert "print-schedule" not in capsys.readouterr().out
    _set_state(memory_dir, schedule_offer_shown_on="2000-01-01")
    assert T.main(["list", "--kind", "capture", "--memory-dir", memory_dir, "--repo-root", repo]) == 0
    assert "print-schedule" not in capsys.readouterr().out


def test_the_tend_tool_summary_carries_the_line(repo, memory_dir, monkeypatch):
    from memory import mcp_tools_tend as MT

    monkeypatch.setattr(Q, "build_queue", lambda *a, **k: _fake_queue(15))
    monkeypatch.setattr(MT, "_corpus_gate", lambda *a: (None, memory_dir, repo))
    text, structured = MT._tool_tend({"action": "list"})
    assert "hippo sleep --print-schedule" in text and structured["text"] == text
    text2, _ = MT._tool_tend({"action": "list"})
    assert "print-schedule" not in text2


# --------------------------------------------------------------------------- #
# The floor sweep in a sleep run
# --------------------------------------------------------------------------- #
_CORPUS = {
    "reranker_voyage": "the reranker model that runs first on search candidates",
    "budget_envelope": "the guaranteed time envelope for each pipeline phase",
    "canvas_pdf": "render a document to pdf with headless chrome",
}
_OFF = ["best pizza dough hydration for a home oven", "marathon training plan for a first runner"]


def _fake_dense(monkeypatch, dim=64):
    from memory import recall as R

    def _vec(text):
        v = np.zeros(dim, dtype="float32")
        for tok in B.tokenize(text):
            v[zlib.crc32(tok.encode("utf-8")) % dim] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents",
                        lambda texts, allow_download=True: np.vstack([_vec(t) for t in texts]).astype("float32"))
    monkeypatch.setattr(R, "embed_query", lambda text, allow_download=False: _vec(text))
    monkeypatch.setattr(S, "_model_cached_offline", lambda: True)


def _corpus(memory_dir, fixtures=True):
    for name, desc in _CORPUS.items():
        with open(os.path.join(memory_dir, f"{name}.md"), "w", encoding="utf-8") as fh:
            fh.write(f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: project\n---\nbody\n')
    if fixtures:
        fx = os.path.join(memory_dir, ".audit-fixtures")
        os.makedirs(fx, exist_ok=True)
        with open(os.path.join(fx, "recall_hard_set.yaml"), "w", encoding="utf-8") as fh:
            yaml.safe_dump([{"query": d, "expected": [n]} for n, d in _CORPUS.items()], fh)
        with open(os.path.join(fx, "recall_abstention_set.yaml"), "w", encoding="utf-8") as fh:
            yaml.safe_dump([{"query": q} for q in _OFF], fh)
    B.build_index(memory_dir, B.default_index_dir(memory_dir))


def _stale_sweep(memory_dir):
    td = default_telemetry_dir(memory_dir)
    os.makedirs(td, exist_ok=True)
    doc = {"ok": True, "schema": 1, "model": B.DEFAULT_MODEL, "configured_floor": 0.68,
           "corpus_fingerprint": "an-old-corpus", "generated_at": "2026-08-17", "recommended": 0.6793}
    with open(os.path.join(td, "floor_sweep.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def _calibration(memory_dir, repo):
    from memory.doctor_checks_abstention import check_floor_calibration
    from memory.doctor_checks_env import DoctorContext

    return check_floor_calibration(DoctorContext(memory_dir, repo))


def test_a_sleep_run_refreshes_a_stale_sweep_and_doctor_stops_warning(repo, memory_dir, monkeypatch, capsys):
    _fake_dense(monkeypatch)
    _corpus(memory_dir)
    _stale_sweep(memory_dir)
    before = _calibration(memory_dir, repo)
    assert before["status"] == "warn" and "STALE" in before["message"]
    assert SL.main(["--memory-dir", memory_dir, "--repo-root", repo]) == 0
    out = capsys.readouterr().out
    assert "floor sweep: refreshed (the corpus changed since 2026-08-17)" in out
    assert "STALE" not in _calibration(memory_dir, repo)["message"]
    assert S.stale_reason(memory_dir)[0] is None
    assert _state(memory_dir)["floor_sweep_counts"] == {"refreshed": 1}
    assert SL.main(["--memory-dir", memory_dir, "--repo-root", repo]) == 0
    capsys.readouterr()  # a current sweep adds nothing to the one-line report; it is counted
    assert S.refresh(memory_dir, repo)[1].startswith("floor sweep: current (from ")
    assert _state(memory_dir)["floor_sweep_counts"] == {"refreshed": 1, "current": 1}


def test_a_stale_sweep_without_the_cached_model_is_skipped_and_counted(repo, memory_dir, monkeypatch, capsys):
    _fake_dense(monkeypatch)
    _corpus(memory_dir)
    _stale_sweep(memory_dir)
    monkeypatch.setattr(S, "_model_cached_offline", lambda: False)
    from memory import eval_floor

    monkeypatch.setattr(eval_floor, "floor_sweep", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    assert SL.main(["--memory-dir", memory_dir, "--repo-root", repo]) == 0
    out = capsys.readouterr().out
    assert "not in the offline cache, and a sleep run never downloads it" in out
    assert _state(memory_dir)["floor_sweep_counts"] == {"skipped_model_not_cached": 1}
    assert _calibration(memory_dir, repo)["status"] == "warn"


def test_other_skips_name_their_reason(repo, memory_dir, monkeypatch):
    _fake_dense(monkeypatch)
    _corpus(memory_dir, fixtures=False)
    _stale_sweep(memory_dir)
    assert S.refresh(memory_dir, repo)[0] == "skipped_no_fixtures"
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    assert S.refresh(memory_dir, repo)[0] == "skipped_dense_off"


def test_no_sweep_is_left_alone_and_the_one_line_report_stays_one_line(repo, memory_dir, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    assert S.refresh(memory_dir, repo)[0] == "none"
    assert SL.main(["--memory-dir", memory_dir, "--repo-root", repo]) == 0
    body = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip() and not ln.startswith("report:")]
    assert len(body) == 1 and "floor sweep" not in body[0]
    assert not os.path.exists(os.path.join(default_telemetry_dir(memory_dir), "floor_sweep.json"))
