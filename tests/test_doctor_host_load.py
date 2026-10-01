"""MSR-7: doctor's KPI-3 line splits the hot-path p95 on host load (split from
``test_doctor.py`` per the test-size ratchet).

Field finding 2026-10-01 (em-growth-labs, 1.36.0): doctor blamed "a heavier model or new
per-import cost" for a p95 of 2339ms that tracked a host at load 33-43 on 16 cores. Rows
now carry ``load1``/``cpus``; the regression wording is reserved for a breach in the
uncontended slice, and rows without the fields are counted as unclassified, never guessed.
"""

from __future__ import annotations

import json
import os

import memory.doctor as D

from .test_doctor import _ctx


def _write_loaded_ledger(memory_dir, rows):
    """rows: (latency_ms, load1, cpus) — load1/cpus None writes a pre-MSR-7 row."""
    from memory.provenance import ensure_self_ignoring_dir
    from memory.telemetry import default_telemetry_dir

    td = default_telemetry_dir(memory_dir)
    ensure_self_ignoring_dir(td)
    with open(os.path.join(td, "recall_events.jsonl"), "w", encoding="utf-8") as fh:
        for lat, load1, cpus in rows:
            ev = {"ts": 1.0, "latency_ms": lat, "names": [], "backend": "bm25",
                  "k": 10, "query_preview": "q"}
            if load1 is not None:
                ev["load1"], ev["cpus"] = load1, cpus
            fh.write(json.dumps(ev) + "\n")


def test_hot_path_contended_tail_is_attributed_to_host_load(memory_dir, repo):
    # The em-growth-labs shape: quiet recalls are fast, the tail lives where load > cpus.
    rows = [(400.0, 4.0, 16)] * 40 + [(3000.0, 38.0, 16)] * 10
    _write_loaded_ledger(memory_dir, rows)
    r = D.check_hot_path_latency(_ctx(memory_dir, repo))
    m = r["message"]
    assert r["status"] == "ok", m
    assert "p95 = 3000ms overall, 400ms uncontended" in m
    assert "tracks host load (median 38/16 CPUs" in m and "not hippo" in m
    assert "heavier model" not in m
    assert "40 uncontended / 10 contended / 0 unclassified" in m


def test_hot_path_uncontended_breach_keeps_regression_wording(memory_dir, repo):
    rows = [(400.0, 2.0, 16)] * 20 + [(2500.0, 2.0, 16)] * 5 + [(3000.0, 40.0, 16)] * 5
    _write_loaded_ledger(memory_dir, rows)
    r = D.check_hot_path_latency(_ctx(memory_dir, repo))
    m = r["message"]
    assert r["status"] == "warn", m
    assert "2500ms over the 25 uncontended" in m and "even on a quiet host" in m
    assert "heavier model or new per-import cost likely regressed it" in m


def test_hot_path_unclassified_rows_never_guessed(memory_dir, repo):
    # Every row predates MSR-7: the breach is named, nothing is blamed.
    _write_loaded_ledger(memory_dir, [(100.0, None, None), (5000.0, None, None)])
    r = D.check_hot_path_latency(_ctx(memory_dir, repo))
    m = r["message"]
    assert r["status"] == "warn" and "ABOVE" in m
    assert "predates host-load recording" in m
    assert "0 uncontended / 0 contended / 2 unclassified" in m
    assert "heavier model" not in m and "host load (median" not in m


def test_hot_path_mixed_ledger_counts_old_rows_as_unclassified(memory_dir, repo):
    # Old rows sit outside both slices; too few uncontended rows -> no verdict either way.
    rows = [(5000.0, None, None)] * 30 + [(400.0, 2.0, 16)] * 5 + [(3000.0, 40.0, 16)] * 5
    _write_loaded_ledger(memory_dir, rows)
    r = D.check_hot_path_latency(_ctx(memory_dir, repo))
    m = r["message"]
    assert r["status"] == "warn"
    assert "only 5 uncontended recall(s) (need 20)" in m
    assert "5 uncontended / 5 contended / 30 unclassified" in m
    assert "heavier model" not in m


def test_hot_path_under_budget_reports_split_only_once_classified(memory_dir, repo):
    _write_loaded_ledger(memory_dir, [(100.0, None, None)] * 3)
    assert "uncontended" not in D.check_hot_path_latency(_ctx(memory_dir, repo))["message"]
    _write_loaded_ledger(memory_dir, [(100.0, 1.0, 16)] * 3 + [(200.0, 20.0, 16)])
    r = D.check_hot_path_latency(_ctx(memory_dir, repo))
    assert r["status"] == "ok"
    assert "3 uncontended / 1 contended / 0 unclassified" in r["message"]


def test_hot_path_threshold_boundary_and_malformed_load(memory_dir, repo):
    # load1 == cpus is contended (run queue as long as the core count); a malformed
    # load field (bool, string, zero cpus) is unclassified rather than coerced.
    rows = [(100.0, 16.0, 16), (100.0, 15.99, 16), (100.0, True, 16),
            (100.0, "3", 16), (100.0, 3.0, 0)]
    _write_loaded_ledger(memory_dir, rows)
    m = D.check_hot_path_latency(_ctx(memory_dir, repo))["message"]
    assert "1 uncontended / 1 contended / 3 unclassified" in m
