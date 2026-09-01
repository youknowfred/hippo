"""DOC-7's degraded path — the inferred sentinel probe when CLAUDE_PLUGIN_DATA is unset
(split from ``test_doctor.py`` per the test-size ratchet).

Live 2026-09-01 finding: with the env var unset, ``check_plugin_version`` said "ok" and
HID a 21-version venv drift (sentinel v1.10.2 under a v1.31.0 install, stale ~7 weeks).
The sentinel is plain JSON readable without any venv, so the degraded path now probes the
documented ``~/.claude/plugins/data/<plugin>-<marketplace>`` convention, surfaces any
delta as INFERRED, and otherwise says plainly that a real check was skipped.
"""

from __future__ import annotations

import json
import os

import memory.doctor as D

from .test_doctor import _ctx


def _probe_home(tmp_path, monkeypatch, dirs):
    """A fake ``~/.claude/plugins/data`` holding ``dirs`` ({name: sentinel_dict|None})."""
    home = tmp_path / "home"
    data_root = home / ".claude" / "plugins" / "data"
    os.makedirs(data_root)
    for name, sentinel in dirs.items():
        os.makedirs(data_root / name)
        if sentinel is not None:
            with open(data_root / name / ".bootstrap-sentinel", "w", encoding="utf-8") as fh:
                json.dump(sentinel, fh)
    monkeypatch.setenv("HOME", str(home))
    return data_root


def _unset_data_ctx(tmp_path, installed):
    proot = tmp_path / "plugin-root"
    os.makedirs(proot / ".claude-plugin", exist_ok=True)
    with open(proot / ".claude-plugin" / "plugin.json", "w", encoding="utf-8") as fh:
        json.dump({"name": "hippo", "version": installed}, fh)
    return _ctx(str(tmp_path / "m"), str(tmp_path / "r"), plugin_data="", plugin_root=str(proot))


def test_plugin_version_unset_env_surfaces_inferred_drift(tmp_path, monkeypatch):
    """The live 2026-09-01 finding: with CLAUDE_PLUGIN_DATA unset the check said "ok"
    and HID a 21-version venv drift (sentinel v1.10.2 under a v1.31.0 install, stale ~7
    weeks). The sentinel is plain JSON readable without any venv — the degraded path must
    probe the documented data-dir convention and surface the delta as INFERRED."""
    _probe_home(tmp_path, monkeypatch, {
        "hippo-hippo": {"plugin_version": "1.10.2"},
        "hippo-inline": {"plugin_version": "1.31.0"},
        "railway-inline": {"plugin_version": "9.9.9"},   # other plugin: never a candidate
        "hippo-nosentinel": None,                         # no sentinel: never a candidate
    })
    r = D.check_plugin_version(_unset_data_ctx(tmp_path, "1.31.0"))
    assert r["status"] == "warn"
    assert "STALE" in r["message"] and "inferred" in r["message"]
    assert "hippo-hippo: v1.10.2" in r["message"] and "hippo-inline: v1.31.0" in r["message"]
    assert "9.9.9" not in r["message"]


def test_plugin_version_unset_env_all_matching_is_ok_with_caveat(tmp_path, monkeypatch):
    _probe_home(tmp_path, monkeypatch, {"hippo-hippo": {"plugin_version": "1.31.0"}})
    r = D.check_plugin_version(_unset_data_ctx(tmp_path, "1.31.0"))
    assert r["status"] == "ok"
    assert "inferred" in r["message"] and "hippo-hippo" in r["message"]


def test_plugin_version_unset_env_and_no_probe_says_skipped(tmp_path, monkeypatch):
    """Honest degradation floor: when nothing can be inferred, say a real check was
    SKIPPED — never a bare "ok" for a check that did not run."""
    _probe_home(tmp_path, monkeypatch, {})
    r = D.check_plugin_version(_unset_data_ctx(tmp_path, "1.31.0"))
    assert r["status"] == "warn" and "SKIPPED" in r["message"]


def test_data_dir_candidates_matches_name_prefixed_sentinel_dirs_only(tmp_path, monkeypatch):
    from memory import bootstrap as B

    _probe_home(tmp_path, monkeypatch, {
        "hippo-hippo": {"plugin_version": "1.0.0"},
        "hippo-inline": {"plugin_version": "1.0.0"},
        "hippopotamus-x": {"plugin_version": "1.0.0"},   # not name + "-": excluded
        "railway-inline": {"plugin_version": "1.0.0"},
        "hippo-empty": None,
    })
    proot = tmp_path / "plugin-root"
    os.makedirs(proot / ".claude-plugin", exist_ok=True)
    with open(proot / ".claude-plugin" / "plugin.json", "w", encoding="utf-8") as fh:
        json.dump({"name": "hippo", "version": "1.0.0"}, fh)
    got = [os.path.basename(d) for d in B.data_dir_candidates(str(proot))]
    assert got == ["hippo-hippo", "hippo-inline"]
