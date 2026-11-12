"""RWY-6: a writer's next write removes the ``<target>.tmp.<pid>`` a killed process left.

The field case: hook processes killed between writing ``usage_aggregates.json.tmp.<pid>``
and the ``os.replace`` stranded three of them for weeks. The kill here is real (the child
SIGKILLs itself at the replace); the tmp is then aged past the grace period, as weeks-old
orphans are, and one ordinary write must leave none behind.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

import memory.telemetry as T
from memory.atomic import TMP_SWEEP_GRACE_S, sweep_dead_pid_tmps

_PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugin"))

_KILL_AT_REPLACE = r"""
import os, signal, sys
sys.path.insert(0, {plugin_root!r})
_real_replace = os.replace
def _replace(src, dst, *a, **k):
    if str(dst).endswith({target!r}):
        os.kill(os.getpid(), signal.SIGKILL)  # the tmp is written; the swap never happens
    return _real_replace(src, dst, *a, **k)
os.replace = _replace
import memory.telemetry as T
{body}
"""


def _kill_mid_write(body: str, target: str) -> None:
    code = _KILL_AT_REPLACE.format(plugin_root=_PLUGIN_ROOT, target=target, body=body)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == -signal.SIGKILL, proc.stderr


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()  # reaped: the pid is provably not alive
    return proc.pid


def _age(path: str, seconds: float = TMP_SWEEP_GRACE_S + 60) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


def _tmps(directory: str, base: str):
    return sorted(n for n in os.listdir(directory) if n.startswith(base + ".tmp."))


def test_kill_mid_aggregate_write_leaves_no_orphan_after_the_next_write(tmp_path):
    td = str(tmp_path / "tele")
    os.makedirs(td)
    T._update_usage_aggregates(td, names=["alpha"], session_id="s1", ts=100.0)
    _kill_mid_write(
        f"T._update_usage_aggregates({td!r}, names=['beta'], session_id='s2', ts=200.0)",
        "usage_aggregates.json",
    )
    orphans = _tmps(td, "usage_aggregates.json")
    assert len(orphans) == 1  # the killed writer stranded its tmp
    _age(os.path.join(td, orphans[0]))

    T._update_usage_aggregates(td, names=["gamma"], session_id="s3", ts=300.0)

    assert _tmps(td, "usage_aggregates.json") == []
    assert set(T.read_usage_aggregates(td)["memories"]) == {"alpha", "gamma"}


def test_kill_mid_ledger_rotation_leaves_no_orphan_after_the_next_rotation(tmp_path, monkeypatch):
    td = str(tmp_path / "tele")
    monkeypatch.setenv("HIPPO_TELEMETRY_MAX_BYTES", "800")
    log = (
        "for i in range(60):\n"
        "    T.log_recall_event([{'name': 'm%d' % i, 'backend': 'bm25'}], query='q %d' % i,"
        f" k=5, latency_ms=1.0, telemetry_dir={td!r})\n"
    )
    _kill_mid_write(log, "recall_events.jsonl")
    ledger = os.path.basename(T._ledger_path(td))
    orphans = _tmps(td, ledger)
    assert len(orphans) == 1
    _age(os.path.join(td, orphans[0]))
    for i in range(60):
        T.log_recall_event([{"name": f"n{i}", "backend": "bm25"}], query=f"q {i}", k=5,
                           latency_ms=1.0, telemetry_dir=td)
    assert _tmps(td, ledger) == []


def test_committed_usage_summary_write_sweeps_its_own_orphans(tmp_path):
    md, td = str(tmp_path / "memory"), str(tmp_path / "tele")
    os.makedirs(td)
    T._update_usage_aggregates(td, names=["alpha"], session_id="s1", ts=100.0)
    path = T.write_user_usage_summary(md, "carol", td)
    orphan = f"{path}.tmp.{_dead_pid()}"
    with open(orphan, "w") as fh:
        fh.write("{")
    _age(orphan)
    assert T.write_user_usage_summary(md, "carol", td) == path
    assert not os.path.exists(orphan)
    with open(path, encoding="utf-8") as fh:
        assert "alpha" in json.load(fh)["memories"]


def test_live_own_fresh_and_foreign_files_survive(tmp_path):
    target = str(tmp_path / "usage_aggregates.json")
    dead = _dead_pid()
    keep = {
        f"{target}.tmp.{os.getppid()}": True,  # a live pid (the test runner's parent)
        f"{target}.tmp.{os.getpid()}": True,  # this process's own
        f"{target}.tmp.{dead}x": True,  # not a bare pid
        f"{target}.tmp.abc123": True,  # a mkstemp-style name: no pid to attribute
        str(tmp_path / f"other.json.tmp.{dead}"): True,  # another target's tmp
        str(tmp_path / f"usage_aggregates.jsonl.tmp.{dead}"): True,  # a longer name, same stem
        f"{target}.tmp.0": True,  # pid 0 would signal the process group; never probed
    }
    for p in keep:
        with open(p, "w") as fh:
            fh.write("x")
        _age(p)
    fresh = f"{target}.tmp.{_dead_pid()}"  # dead, but inside the grace period
    with open(fresh, "w") as fh:
        fh.write("x")
    victim = str(tmp_path / "victim")
    with open(victim, "w") as fh:
        fh.write("keep me")
    link = f"{target}.tmp.{_dead_pid()}"  # a symlink under a dead pid's name: never removed
    os.symlink(victim, link)

    assert sweep_dead_pid_tmps(target) == 0
    for p in list(keep) + [fresh, victim]:
        assert os.path.exists(p), p
    assert os.path.islink(link)


def test_a_dead_pids_aged_tmp_goes_and_the_sweep_never_raises(tmp_path):
    target = str(tmp_path / "usage_aggregates.json")
    orphan = f"{target}.tmp.{_dead_pid()}"
    with open(orphan, "w") as fh:
        fh.write("")  # the field case included an empty one
    _age(orphan)
    assert sweep_dead_pid_tmps(target) == 1
    assert not os.path.exists(orphan)
    assert sweep_dead_pid_tmps(str(tmp_path / "missing-dir" / "x.json")) == 0
