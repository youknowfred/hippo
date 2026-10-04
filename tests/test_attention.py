"""CLM-2: the attention setting — calm/full, a mute list, integrity never muted."""

from __future__ import annotations

import json
import os

from memory import session_start as SS
from memory.attention import CALM, FULL, INTEGRITY_SIGNALS, attention_mode, muted_signals
from memory.doctor_checks_env import DoctorContext
from memory.doctor_checks_platform import check_attention

_PLUGIN_JSON = os.path.join(os.path.dirname(__file__), "..", "plugin", ".claude-plugin", "plugin.json")


def test_mode_precedence():
    assert attention_mode({}) == FULL
    assert attention_mode({"CLAUDE_PLUGIN_OPTION_CALM_SESSION_START": "true"}) == CALM
    assert attention_mode({"HIPPO_ATTENTION": "full", "CLAUDE_PLUGIN_OPTION_CALM_SESSION_START": "true"}) == FULL
    assert attention_mode({"HIPPO_ATTENTION": "CALM"}) == CALM
    assert attention_mode({"HIPPO_ATTENTION": "loud"}) == FULL


def test_integrity_signals_cannot_be_muted():
    muted, refused = muted_signals({"HIPPO_MUTE": "staleness, trust_drift,link_health,,"})
    assert muted == {"staleness", "link_health"} and refused == {"trust_drift"}


def _fake_producers(monkeypatch):
    monkeypatch.setattr(SS, "PRODUCERS", [
        ("trust_drift", lambda md, rr, ctx: "⚠ integrity line"),
        ("staleness", lambda md, rr, ctx: "stale list"),
        ("link_health", lambda md, rr, ctx: "links list"),
    ])
    monkeypatch.setattr(SS, "_build_run_context", lambda md, rr: None)


def test_a_muted_signal_is_skipped_and_an_integrity_one_is_not(tmp_path, monkeypatch):
    _fake_producers(monkeypatch)
    monkeypatch.delenv("HIPPO_ATTENTION", raising=False)
    monkeypatch.setenv("HIPPO_MUTE", "staleness,trust_drift")
    out = SS.build_context(str(tmp_path), str(tmp_path))
    assert "stale list" not in out
    assert "⚠ integrity line" in out and "links list" in out


def test_doctor_reports_the_setting(tmp_path, monkeypatch):
    ctx = DoctorContext(memory_dir=str(tmp_path), repo_root=None)
    monkeypatch.setenv("HIPPO_ATTENTION", "calm")
    monkeypatch.setenv("HIPPO_MUTE", "staleness,index_integrity")
    r = check_attention(ctx)
    assert r["status"] == "warn" and "attention: calm" in r["message"]
    assert "muted: staleness" in r["message"] and "NOT muted" in r["message"]


def test_the_plugin_exposes_a_boolean_option_and_no_options_picker():
    cfg = json.load(open(_PLUGIN_JSON, encoding="utf-8"))["userConfig"]
    assert cfg["calm_session_start"]["type"] == "boolean"
    assert cfg["calm_session_start"]["default"] is False
    # An `options` picker stops the plugin loading on Claude Code before 2.1.271.
    assert not any("options" in field for field in cfg.values())


def test_integrity_set_names_real_producers():
    labels = {label for label, _fn in SS.PRODUCERS}
    assert INTEGRITY_SIGNALS <= labels
