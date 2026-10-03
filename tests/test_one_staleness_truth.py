"""HOT-4: one staleness truth. Recall's verify-at-use banner names the same stale set
SessionStart arms (VOL-1 volatile-only and TYPE-1 type-exempt suppressed; demoted and
snoozed excluded). Before HOT-4 the banner was registry-blind and fired on every detected
drift: on hippo's own corpus it bannered 49 memories while SessionStart armed 1.

Hermetic: throwaway git repo + corpus (conftest ``repo``/``memory_dir``), commit times
relative to now so find_stale's wall-clock window covers them.
"""

from __future__ import annotations

import json
import os
import time

from memory import reconsolidate as RC
from memory import session_start as S
from memory.build_index import default_index_dir
from memory.recall_salience import _stale_banner_map
from memory.staleness import read_armed_names, set_invalid_after, write_stale_cache
from memory.telemetry import default_telemetry_dir

from .conftest import git_commit, write_file

_ROADMAP = "ROADMAP.yaml"


def _mem(name, mtype, cited, sc):
    cp = ", ".join(f'"{c}"' for c in cited)
    return (
        f'---\nname: {name}\ndescription: "{name} description"\nmetadata:\n  type: {mtype}\n'
        f'  cited_paths: [{cp}]\n  source_commit: "{sc}"\n---\nbody for {name}\n'
    )


def _drifted(repo, memory_dir):
    t0 = int(time.time()) - 10_000
    write_file(repo, "src/a.py", "a = 1\n")
    write_file(repo, "src/b.py", "b = 1\n")
    write_file(repo, _ROADMAP, "phase: 1\n")
    c1 = git_commit(repo, "c1", t0)
    write_file(memory_dir, "m_armed.md", _mem("m_armed", "feedback", ["src/a.py"], c1))
    write_file(memory_dir, "m_project.md", _mem("m_project", "project", ["src/b.py"], c1))
    write_file(memory_dir, "m_vol.md", _mem("m_vol", "feedback", [_ROADMAP], c1))
    write_file(memory_dir, "m_demoted.md", _mem("m_demoted", "feedback", ["src/a.py"], c1))
    write_file(memory_dir, "m_snoozed.md", _mem("m_snoozed", "feedback", ["src/a.py"], c1))
    with open(os.path.join(memory_dir, ".format"), "w", encoding="utf-8") as fh:
        json.dump({"volatile_paths": [_ROADMAP]}, fh)
    write_file(repo, "src/a.py", "a = 2\n")
    write_file(repo, "src/b.py", "b = 2\n")
    write_file(repo, _ROADMAP, "phase: 2\n")
    git_commit(repo, "c2", t0 + 100)
    set_invalid_after(os.path.join(memory_dir, "m_demoted.md"))
    RC.snooze("m_snoozed", memory_dir, telemetry_dir=default_telemetry_dir(memory_dir))


def test_banner_names_only_what_sessionstart_arms(repo, memory_dir, monkeypatch):
    monkeypatch.delenv("HIPPO_ARMING_EXEMPT_TYPES", raising=False)
    _drifted(repo, memory_dir)
    ctx = S._build_run_context(memory_dir, repo)
    detected = {item["name"] for item in ctx.stale}
    assert detected == {"m_armed", "m_project", "m_vol", "m_demoted", "m_snoozed"}

    idx = default_index_dir(memory_dir)
    assert read_armed_names(idx) == {"m_armed"}
    assert set(_stale_banner_map(idx)) == {"m_armed"}


def test_sessionstart_note_and_banner_count_the_same_set(repo, memory_dir, monkeypatch):
    """With nothing demoted or snoozed, the SessionStart arming partition and the banner
    agree name for name."""
    monkeypatch.delenv("HIPPO_ARMING_EXEMPT_TYPES", raising=False)
    t0 = int(time.time()) - 10_000
    write_file(repo, "src/a.py", "a = 1\n")
    write_file(repo, "src/b.py", "b = 1\n")
    c1 = git_commit(repo, "c1", t0)
    write_file(memory_dir, "m_one.md", _mem("m_one", "feedback", ["src/a.py"], c1))
    write_file(memory_dir, "m_two.md", _mem("m_two", "reference", ["src/b.py"], c1))
    write_file(memory_dir, "m_proj.md", _mem("m_proj", "project", ["src/b.py"], c1))
    write_file(repo, "src/a.py", "a = 2\n")
    write_file(repo, "src/b.py", "b = 2\n")
    git_commit(repo, "c2", t0 + 100)

    from memory.staleness_policy import arming_partition

    ctx = S._build_run_context(memory_dir, repo)
    armed, _vol, type_sup = arming_partition(memory_dir, ctx.stale)
    assert {i["name"] for i in type_sup} == {"m_proj"}
    assert set(_stale_banner_map(default_index_dir(memory_dir))) == {i["name"] for i in armed} == {"m_one", "m_two"}


def test_override_that_arms_every_type_banners_every_type(repo, memory_dir, monkeypatch):
    monkeypatch.setenv("HIPPO_ARMING_EXEMPT_TYPES", "")
    _drifted(repo, memory_dir)
    S._build_run_context(memory_dir, repo)
    assert set(_stale_banner_map(default_index_dir(memory_dir))) == {"m_armed", "m_project"}


def test_a_pre_hot4_cache_keeps_the_old_banner_until_the_next_sessionstart(tmp_path):
    idx = str(tmp_path / "idx")
    stale = [
        {"name": "a", "changed_paths": ["x.py"], "source_commit": "abc1234def"},
        {"name": "b", "changed_paths": ["y.py"], "source_commit": "abc1234def"},
    ]
    assert write_stale_cache(idx, stale)  # no armed list: the shape every older cache has
    assert read_armed_names(idx) is None
    assert set(_stale_banner_map(idx)) == {"a", "b"}


def test_an_empty_armed_list_means_no_stale_lane_banner(tmp_path):
    idx = str(tmp_path / "idx")
    stale = [{"name": "a", "changed_paths": ["x.py"], "source_commit": "abc1234def"}]
    assert write_stale_cache(idx, stale, armed=[])
    assert read_armed_names(idx) == set()
    assert _stale_banner_map(idx) == {}


def test_evidence_drift_still_banners_unfiltered(tmp_path):
    """CLB-3 quoted-evidence drift arms through every filter (span-level truth), so its
    banner is not gated by the armed list."""
    idx = str(tmp_path / "idx")
    stale = [{"name": "a", "changed_paths": ["x.py"], "source_commit": "abc1234def"}]
    write_stale_cache(idx, stale, evidence_drift={"q": {"fences": 2, "missing": 1, "whitespace": 0}}, armed=[])
    banners = _stale_banner_map(idx)
    assert set(banners) == {"q"} and "quoted evidence drift" in banners["q"]
