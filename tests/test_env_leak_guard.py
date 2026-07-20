"""The ``HIPPO_*`` env-leak guard in tests/conftest.py, pinned end-to-end.

The guard exists because hippo's behaviour switches are ALL env vars: a test that writes
``os.environ`` directly instead of going through ``monkeypatch`` reconfigures every test
that runs after it in the same process. The instance that produced it — the index-rebuild
concurrency test toggling ``HIPPO_DISABLE_DENSE`` from a worker thread and exiting on the
disabled half — turned later dense-path tests into bm25-only tests that still passed. A
false-green class: green alone (proving the behaviour), green in-suite (proving nothing).

The guard's own failure mode is silence, and it is only observable at the level of a whole
pytest run, so this file exercises the SHIPPED conftest in a child pytest over a
purpose-built probe suite and asserts all three halves:

  - a direct ``os.environ`` write is reported, and names the test that did it;
  - a legitimate ``monkeypatch.setenv`` is NOT reported (the false-positive half — the
    guard is a ``pytest_runtest_setup``/``pytest_runtest_teardown`` hook pair precisely
    because ``monkeypatch`` is finalized after every autouse fixture, so a fixture-based
    guard flags every well-behaved test instead);
  - the leak is REPAIRED before it is reported, so a later test sees clean env and one
    leak can't cascade into a wall of unrelated failures.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

_CONFTEST = os.path.join(os.path.dirname(__file__), "conftest.py")

# Ordered: the leak lands between a well-behaved test and the test that proves repair.
_PROBE_SUITE = '''
import os


def test_a_monkeypatch_setenv_is_not_a_leak(monkeypatch):
    monkeypatch.setenv("HIPPO_PROBE_VAR", "x")
    monkeypatch.setenv("HIPPO_TRUST_ALL", "0")     # overriding an autouse-set var
    monkeypatch.delenv("HIPPO_TRUST_FILE", raising=False)


def test_b_direct_environ_write_leaks():
    os.environ["HIPPO_PROBE_VAR"] = "leaked"


def test_c_sees_repaired_env():
    assert "HIPPO_PROBE_VAR" not in os.environ           # the leak was undone...
    assert os.environ.get("HIPPO_TRUST_ALL") == "1"      # ...and nothing else was
'''


def _run_probe_suite(tmp_path) -> subprocess.CompletedProcess:
    """Run a child pytest over the probe suite under a COPY of the shipped conftest.

    A copy, not an import: the guard is hook-based, so it only exists when pytest
    collects a conftest — and the copy is byte-identical to the file under test, so a
    change to the real guard shows up here.
    """
    shutil.copy(_CONFTEST, os.path.join(str(tmp_path), "conftest.py"))
    with open(os.path.join(str(tmp_path), "test_probe.py"), "w", encoding="utf-8") as fh:
        fh.write(_PROBE_SUITE)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )


def test_guard_reports_only_the_test_that_leaked(tmp_path):
    proc = _run_probe_suite(tmp_path)
    out = proc.stdout + proc.stderr

    assert proc.returncode != 0, f"leaking suite passed clean:\n{out}"
    reported = [ln for ln in out.splitlines() if ln.startswith("ERROR ")]
    assert len(reported) == 1, f"expected exactly one flagged test, got {reported}:\n{out}"
    assert "test_b_direct_environ_write_leaks" in reported[0], out
    # The message must name the leaked var — a bare "env changed" is not actionable.
    assert "HIPPO_PROBE_VAR" in out, out


def test_guard_repairs_the_leak_before_reporting_it(tmp_path):
    """All three probes PASS; only the teardown of the leaky one errors.

    ``test_c_sees_repaired_env`` passing is the load-bearing assertion: it ran after the
    leak and saw ``HIPPO_PROBE_VAR`` gone and ``HIPPO_TRUST_ALL`` untouched.
    """
    out = _run_probe_suite(tmp_path).stdout
    assert "3 passed" in out and "1 error" in out, out
