"""OBS-4: the recall hook's wall time is measured from the shell, so KPI-3 counts interpreter
start and imports. The hook stamps epoch-ms before Python starts, the hook entry logs
``wall_ms``, and doctor shows it beside the in-process ``latency_ms``."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

import pytest

from memory import build_index as B
from memory import doctor as D
from memory import recall as R
from memory.doctor_checks_env import DoctorContext
from memory.doctor_checks_kpi import check_hook_wall

_RESOLVE = os.path.join(os.path.dirname(__file__), "..", "plugin", "hooks", "_resolve_py.sh")
_BASH = shutil.which("bash")


def _bash_major() -> int:
    if not _BASH:
        return 0
    out = subprocess.run([_BASH, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True)
    return int(out.stdout.strip() or 0)


@pytest.mark.skipif(_bash_major() < 5, reason="needs bash 5's $EPOCHREALTIME")
def test_stamp_is_epoch_milliseconds_on_bash5():
    out = subprocess.run(
        [_BASH, "-c", f'. "{_RESOLVE}"; hippo_stamp_t0; printf %s "$HIPPO_HOOK_T0_MS"'],
        capture_output=True, text=True, check=True,
    ).stdout
    assert len(out) == 13 and out.isdigit()
    assert abs(int(out) - time.time() * 1000) < 5_000


@pytest.mark.skipif(_BASH is None, reason="no bash")
def test_no_clock_means_no_stamp_never_a_guess():
    """No $EPOCHREALTIME and no `date` on PATH: the variable stays unset."""
    script = f'unset EPOCHREALTIME; PATH=/nonexistent; . "{_RESOLVE}"; hippo_stamp_t0; printf %s "${{HIPPO_HOOK_T0_MS:-none}}"'
    out = subprocess.run([_BASH, "-c", script], capture_output=True, text=True).stdout
    assert out == "none"


def test_user_prompt_hook_stamps_before_python():
    hook = os.path.join(os.path.dirname(_RESOLVE), "memory_user_prompt.sh")
    text = open(hook, encoding="utf-8").read()
    assert text.index("hippo_stamp_t0") < text.index("bin/hippo\" recall --stdin-json")


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    td = str(tmp_path / ".memory-telemetry")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    md = str(tmp_path / "memory")
    os.makedirs(md)
    with open(os.path.join(md, "zebra.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: zebra\ndescription: zebra canary deploy rollout\nmetadata:\n  type: project\n---\nbody\n")
    idx = str(tmp_path / "idx")
    B.build_index(md, idx)
    return md, idx, td


def _last_row(td):
    with open(os.path.join(td, "recall_events.jsonl"), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()][-1]


def _hook(md, idx, monkeypatch, capsys):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "zebra canary deploy rollout"})))
    assert R.main(["--stdin-json", "--memory-dir", md, "--index-dir", idx]) == 0
    capsys.readouterr()


def test_hook_row_carries_the_shell_wall(corpus, monkeypatch, capsys):
    md, idx, td = corpus
    monkeypatch.setenv("HIPPO_HOOK_T0_MS", str(int(time.time() * 1000) - 750))
    _hook(md, idx, monkeypatch, capsys)
    row = _last_row(td)
    assert 750 <= row["wall_ms"] < 600_000
    assert row["wall_ms"] >= row["latency_ms"]


@pytest.mark.parametrize("value", [None, "", "garbage", "1"])
def test_no_or_junk_stamp_writes_no_key(corpus, monkeypatch, capsys, value):
    md, idx, td = corpus
    if value is None:
        monkeypatch.delenv("HIPPO_HOOK_T0_MS", raising=False)
    else:
        monkeypatch.setenv("HIPPO_HOOK_T0_MS", value)  # "1" = 1970: out of range
    _hook(md, idx, monkeypatch, capsys)
    assert "wall_ms" not in _last_row(td)


def test_cli_path_never_logs_a_wall(corpus, monkeypatch, capsys):
    md, idx, td = corpus
    monkeypatch.setenv("HIPPO_HOOK_T0_MS", str(int(time.time() * 1000) - 750))
    assert R.main(["zebra canary deploy rollout", "--memory-dir", md, "--index-dir", idx]) == 0
    capsys.readouterr()
    assert "wall_ms" not in _last_row(td)


def _write_rows(td, rows):
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "recall_events.jsonl"), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_doctor_reports_the_gap(tmp_path, monkeypatch):
    md = str(tmp_path / "memory")
    os.makedirs(md)
    td = str(tmp_path / "tel")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    ctx = DoctorContext(memory_dir=md, repo_root=str(tmp_path))
    assert "no shell-stamped recalls yet" in check_hook_wall(ctx)["message"]

    _write_rows(
        td,
        [
            {"latency_ms": 300.0, "wall_ms": 900.0},
            {"latency_ms": 400.0, "wall_ms": 1100.0},
            {"latency_ms": 500.0, "wall_ms": 1200.0},
            {"latency_ms": 80.0, "wall_ms": 999.0, "channel": "mcp"},  # MCP rows never count
            {"latency_ms": 350.0},  # pre-OBS-4 row: no wall
        ],
    )
    res = check_hook_wall(ctx)
    assert res["status"] == "ok"
    assert "over 3 stamped recall(s)" in res["message"]
    assert "p50 1100ms" in res["message"] and "p50 400ms" in res["message"]
    assert "misses a median 700ms" in res["message"]


def test_doctor_registers_the_line_after_recall_channels():
    labels = [label for label, _ in D.CHECKS]
    assert labels.index("hook_wall") == labels.index("recall_channels") + 1
