"""OBS-1: rotation-proof daily rollups — one durable row per active day per corpus."""

from __future__ import annotations

import io
import json
import os
import threading
import time

from memory import build_index as B
from memory import recall as R
from memory import telemetry_rollup as TR
from memory.doctor_checks_env import DoctorContext
from memory.doctor_checks_kpi import check_kpi_rollups

DAY = 86400.0
T0 = time.mktime((2026, 10, 1, 12, 0, 0, 0, 0, -1))  # local noon, so day math never straddles DST


def _rows(td):
    path = os.path.join(td, TR._ROLLUP_NAME)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_prompts_fold_into_todays_row_by_trigger(tmp_path):
    td = str(tmp_path)
    TR.record_prompt(td, trigger="human", session_id="s1", ran_recall=True, backend="bm25",
                     injected_chars=1200, latency_ms=180, wall_ms=640, now=T0)
    TR.record_prompt(td, trigger="human", session_id="s1", ran_recall=True, backend="none", now=T0)
    TR.record_prompt(td, trigger="human", session_id="s2", ran_recall=False, now=T0)
    TR.record_prompt(td, trigger="task-notification", session_id="s1", wall_ms=90, now=T0)
    [row] = TR.read_rollups(td, now=T0)
    hook = row["hook"]
    assert hook["prompts"] == 4
    assert hook["trigger"] == {"human": 3, "task-notification": 1}
    assert hook["backend"] == {"bm25": 1, "none": 1}
    assert hook["abstained"] == 1 and hook["skipped"] == 1
    assert hook["injected_chars"] == 1200 and hook["injected_by_trigger"] == {"human": 1200}
    assert hook["sessions"] == 2 and "session_chars" not in hook  # ids never leave the day
    assert hook["wall_ms_hist"] == {"650": 1, "100": 1}


def test_a_new_day_finalizes_the_old_row_and_caps_at_365(tmp_path, monkeypatch):
    td = str(tmp_path)
    monkeypatch.setattr(TR, "MAX_DAYS", 3)
    for d in range(5):
        TR.record_prompt(td, trigger="human", ran_recall=True, backend="bm25", now=T0 + d * DAY)
    finalized = _rows(td)
    assert [r["date"] for r in finalized] == [TR._today(T0 + d * DAY) for d in (1, 2, 3)]
    assert TR.read_rollups(td, now=T0 + 4 * DAY, days=30)[-1]["date"] == TR._today(T0 + 4 * DAY)


def test_rollups_survive_ledger_truncation(tmp_path, monkeypatch, capsys):
    """The Done criterion's truncation test: the recall ledger rotating (or being deleted)
    takes no rollup with it."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    td = str(tmp_path / ".memory-telemetry")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    md = str(tmp_path / "memory")
    os.makedirs(md)
    with open(os.path.join(md, "zebra.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: zebra\ndescription: zebra canary deploy rollout\nmetadata:\n  type: project\n---\nb\n")
    idx = str(tmp_path / "idx")
    B.build_index(md, idx)
    for prompt in ("zebra canary deploy rollout", "<task-notification><task-id>x</task-id></task-notification>"):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": prompt, "session_id": "s"})))
        assert R.main(["--stdin-json", "--memory-dir", md, "--index-dir", idx]) == 0
    capsys.readouterr()
    before = TR.read_rollups(td)
    assert before[-1]["hook"]["trigger"] == {"human": 1, "task-notification": 1}

    with open(os.path.join(td, "recall_events.jsonl"), "w", encoding="utf-8"):
        pass  # the ledger rotated away to nothing
    os.remove(os.path.join(td, "episode_buffer.jsonl"))
    assert TR.read_rollups(td) == before


def test_cli_recall_is_not_a_prompt(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    td = str(tmp_path / ".memory-telemetry")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    md = str(tmp_path / "memory")
    os.makedirs(md)
    with open(os.path.join(md, "zebra.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: zebra\ndescription: zebra canary\nmetadata:\n  type: project\n---\nb\n")
    B.build_index(md, str(tmp_path / "idx"))
    assert R.main(["zebra canary", "--memory-dir", md, "--index-dir", str(tmp_path / "idx")]) == 0
    capsys.readouterr()
    assert TR.read_rollups(td) == []


def test_session_start_folds_chars_cap_and_dropped_producers(tmp_path):
    td = str(tmp_path)
    TR.record_session_start(td, total=8_990, cap=9_000, dropped=["floor"], cut=["git_recent"], now=T0)
    TR.record_session_start(td, total=1_200, cap=9_000, now=T0)
    [row] = TR.read_rollups(td, now=T0)
    ss = row["session_start"]
    assert ss["runs"] == 2 and ss["at_cap"] == 1 and ss["chars_total"] == 10_190
    assert ss["dropped"] == {"floor": 1} and ss["cut"] == {"git_recent": 1}


def test_dropped_producers_reads_the_cut_from_the_payload():
    producers = {"a": 10, "b": 10, "c": 10}
    full = "\n\n".join(["x" * 10] * 3)
    assert TR.dropped_producers(producers, full) == {"dropped": [], "cut": []}
    bounded = full[:15] + "\n…(truncated)"  # a's block fits; b is cut mid-block; c is gone
    assert TR.dropped_producers(producers, bounded) == {"dropped": ["c"], "cut": ["b"]}


def test_concurrent_writers_lose_no_increment(tmp_path):
    td = str(tmp_path)

    def burst():
        for _ in range(25):
            TR.record_prompt(td, trigger="human", ran_recall=True, backend="bm25", now=T0)

    threads = [threading.Thread(target=burst) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert TR.read_rollups(td, now=T0)[0]["hook"]["prompts"] == 100


def test_summary_and_doctor_line(tmp_path, monkeypatch):
    td = str(tmp_path / "tel")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    md = str(tmp_path / "memory")
    os.makedirs(md)
    ctx = DoctorContext(memory_dir=md, repo_root=str(tmp_path))
    assert "no daily rollups yet" in check_kpi_rollups(ctx)["message"]

    now = time.time()
    TR.record_prompt(td, trigger="human", session_id="s", ran_recall=True, backend="bm25",
                     injected_chars=900, latency_ms=120, wall_ms=480, now=now - DAY)
    TR.record_prompt(td, trigger="agent-message", session_id="s", now=now)
    TR.record_session_start(td, total=8_999, cap=9_000, dropped=["floor"], now=now)
    k = TR.summarize(TR.read_rollups(td))
    assert k["days"] == 2 and k["prompts"] == 2 and k["machine_prompts"] == 1
    assert k["machine_injected_chars"] == 0 and k["wall_p95"] == "500"
    msg = check_kpi_rollups(ctx)["message"]
    assert "2 prompt(s), 1 human / 1 machine (0 chars injected on machine turns)" in msg
    assert "1/1 at the cap" in msg and "floor (1)" in msg


def test_a_new_trigger_class_merges_with_days_that_predate_it(tmp_path):
    """A class added to harness_envelopes.MACHINE_TURN_CLASSES is one more key in an open
    counter map: a row finalized before it existed still reads, and summarize and the doctor
    line count the new class as machine traffic without a ROLLUP_VERSION bump."""
    td = str(tmp_path)
    TR.record_prompt(td, trigger="human", ran_recall=True, backend="bm25", now=T0)
    TR.record_prompt(td, trigger="task-notification", now=T0)
    TR.record_prompt(td, trigger="ci-monitor-event", now=T0 + DAY)
    rows = TR.read_rollups(td, now=T0 + DAY)
    assert [r["hook"]["trigger"] for r in rows] == [
        {"human": 1, "task-notification": 1},
        {"ci-monitor-event": 1},
    ]
    k = TR.summarize(rows)
    assert k["trigger"] == {"human": 1, "task-notification": 1, "ci-monitor-event": 1}
    assert k["prompts"] == 3 and k["machine_prompts"] == 2


def test_a_corrupt_accumulator_restarts_the_day(tmp_path):
    td = str(tmp_path)
    with open(os.path.join(td, TR._TODAY_NAME), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert TR.record_prompt(td, trigger="human", now=T0)
    assert TR.read_rollups(td, now=T0)[0]["hook"]["prompts"] == 1
