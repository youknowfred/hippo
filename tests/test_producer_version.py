"""MEA-4: producer-version stamps on evidence-ledger rows + the rows-by-version surfaces.

The live-hook-lag class bit twice in one week and both diagnoses were hand forensics —
no row said which plugin version minted it. The pins:

  AC1  new rows in all three ledgers (outcome / recall / reconsolidation) carry `v` when
       the RUNNING plugin's plugin.json is readable (CLAUDE_PLUGIN_ROOT resolution, env
       first, module root as the dev fallback); the read is cached per process;
       unreadable -> field omitted and the writer never raises (ED-4 / inv2).
  AC2  format_lane_health gains a rows-by-producing-version line naming the running
       version; historical version-less rows aggregate as ONE "unstamped" bucket, never
       backfilled; one doctor line surfaces the same aggregate.
  AC3  provenance only: no behavior branches on the stamp (readers use .get()).
"""

from __future__ import annotations

import json
import os

import pytest

from memory import telemetry as T
from memory.telemetry import default_telemetry_dir, read_outcomes


@pytest.fixture(autouse=True)
def _fresh_version_cache(monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setattr(T, "_producer_version_cache", T._PRODUCER_VERSION_UNSET)
    yield
    T._producer_version_cache = T._PRODUCER_VERSION_UNSET


def _plugin_root(tmp_path, version="9.9.9"):
    root = tmp_path / "plugin-root"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "hippo", "version": version}), encoding="utf-8"
    )
    return str(root)


def test_all_three_writers_stamp_v(memory_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", _plugin_root(tmp_path))
    td = default_telemetry_dir(memory_dir)
    assert T.log_outcome("Edit", "src/a.py", session_id="s1", telemetry_dir=td)
    assert T.log_recall_event(
        [{"name": "m", "score": 1.0, "rank": 1, "backend": "bm25"}],
        query="q", k=5, latency_ms=1.0, telemetry_dir=td, session_id="s1",
    )
    assert T.record_reconsolidation_outcome("mem-a", "graduate", telemetry_dir=td)

    out_rows = list(read_outcomes(td))
    assert out_rows[-1]["v"] == "9.9.9"
    rec_rows = list(T.read_events(td))
    assert rec_rows[-1]["v"] == "9.9.9"
    rc_rows = list(T.read_reconsolidation_events(td))
    assert rc_rows[-1]["v"] == "9.9.9"


def test_unreadable_manifest_omits_field_and_never_raises(memory_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "nowhere"))
    td = default_telemetry_dir(memory_dir)
    assert T.log_outcome("Edit", "src/a.py", session_id="s1", telemetry_dir=td)
    row = list(read_outcomes(td))[-1]
    assert "v" not in row


def test_version_read_is_cached_per_process(memory_dir, tmp_path, monkeypatch):
    root = _plugin_root(tmp_path, "1.0.0")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", root)
    td = default_telemetry_dir(memory_dir)
    T.log_outcome("Edit", "a.py", session_id="s1", telemetry_dir=td)
    # mutate the manifest AFTER the first read — the cached value keeps stamping
    with open(os.path.join(root, ".claude-plugin", "plugin.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"version": "2.0.0"}))
    T.log_outcome("Edit", "b.py", session_id="s1", telemetry_dir=td)
    rows = list(read_outcomes(td))
    assert [r.get("v") for r in rows] == ["1.0.0", "1.0.0"]


def test_lane_health_renders_rows_by_version_with_unstamped_bucket(memory_dir, tmp_path, monkeypatch):
    from memory.outcome import format_lane_health

    td = default_telemetry_dir(memory_dir)
    # one historical (unstamped) row: no CLAUDE_PLUGIN_ROOT and an unreadable fallback…
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "nowhere"))
    T.log_outcome("Edit", "old.py", session_id="s0", telemetry_dir=td)
    # …then the process "updates": fresh cache, readable manifest
    T._producer_version_cache = T._PRODUCER_VERSION_UNSET
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", _plugin_root(tmp_path, "9.9.9"))
    T.log_outcome("Edit", "new.py", session_id="s1", telemetry_dir=td)

    text = format_lane_health(memory_dir, td)
    assert "rows by producing version" in text
    assert "running v9.9.9" in text
    assert "9.9.9: 1" in text
    assert "unstamped: 1" in text


def test_doctor_line_surfaces_the_same_aggregate(memory_dir, tmp_path, monkeypatch):
    from memory import doctor as D
    from memory.doctor_checks_corpus import check_producer_versions

    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", _plugin_root(tmp_path, "9.9.9"))
    td = default_telemetry_dir(memory_dir)
    T.log_outcome("Edit", "a.py", session_id="s1", telemetry_dir=td)
    r = check_producer_versions(D.DoctorContext(memory_dir, os.path.dirname(os.path.dirname(memory_dir))))
    assert r["status"] == "ok"
    assert "9.9.9: 1" in r["message"]
    assert "provenance only" in r["message"]


def test_doctor_line_empty_ledger_is_quiet_ok(memory_dir):
    from memory import doctor as D
    from memory.doctor_checks_corpus import check_producer_versions

    r = check_producer_versions(D.DoctorContext(memory_dir, os.path.dirname(os.path.dirname(memory_dir))))
    assert r["status"] == "ok"
    assert "no rows" in r["message"]


def test_doctor_tail_order_still_pinned():
    """MEA-4's check registers BEFORE the pinned tail [machine_state, subset_boundary,
    stale_memobot_env]."""
    from memory import doctor as D

    labels = [label for label, _ in D.CHECKS]
    assert labels.index("producer_versions") < labels.index("machine_state")
    assert labels[-3:] == ["machine_state", "subset_boundary", "stale_memobot_env"]
