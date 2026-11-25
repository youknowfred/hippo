"""HCR-1 tests — the retrieval profile switch: resolution precedence, the preset
writer's never-clobber rule, the floor profile's dense_disabled wiring, the hardware
gate on the OFFER (never on the switch), the doctor line, and the setup CLI's
preview/apply/off surface.

Hermetic: every test points CLAUDE_PLUGIN_DATA at tmp_path and clears the profile env
var; nothing touches the real machine preset. The hardware gate is monkeypatched both
ways — a test must pass identically on a qualifying and a non-qualifying machine.
"""

from __future__ import annotations

import json
import os

import pytest

from memory import build_index as B
from memory import retrieval_profile as RP
from memory import setup_cli
from memory.doctor_checks_env import DoctorContext, check_retrieval_profile


@pytest.fixture
def plugin_data(tmp_path, monkeypatch):
    data = tmp_path / "plugin-data"
    data.mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data))
    monkeypatch.delenv(RP.ENV_VAR, raising=False)
    return data


def _write_preset(data, payload) -> str:
    path = os.path.join(str(data), "model.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(payload if isinstance(payload, str) else json.dumps(payload))
    return path


# --------------------------------------------------------------------------- #
# resolution precedence — env > preset > default, invalid falls through
# --------------------------------------------------------------------------- #
def test_default_is_lean(plugin_data):
    assert RP.profile_source() == ("lean", "default")


def test_no_plugin_data_is_lean(monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.delenv(RP.ENV_VAR, raising=False)
    assert RP.resolve_retrieval_profile() == "lean"


def test_preset_sticks(plugin_data):
    _write_preset(plugin_data, {"retrieval_profile": "high"})
    assert RP.profile_source() == ("high", "preset")


def test_env_wins_over_preset(plugin_data, monkeypatch):
    _write_preset(plugin_data, {"retrieval_profile": "high"})
    monkeypatch.setenv(RP.ENV_VAR, "floor")
    assert RP.profile_source() == ("floor", "env")


def test_env_is_case_insensitive(plugin_data, monkeypatch):
    monkeypatch.setenv(RP.ENV_VAR, "  HIGH ")
    assert RP.resolve_retrieval_profile() == "high"


def test_invalid_env_falls_through_to_preset(plugin_data, monkeypatch):
    _write_preset(plugin_data, {"retrieval_profile": "floor"})
    monkeypatch.setenv(RP.ENV_VAR, "turbo")
    assert RP.profile_source() == ("floor", "preset")
    assert ("env", "turbo") in RP.ignored_values()


def test_invalid_preset_falls_through_to_default(plugin_data):
    _write_preset(plugin_data, {"retrieval_profile": "mega"})
    assert RP.profile_source() == ("lean", "default")
    assert ("preset", "mega") in RP.ignored_values()


def test_corrupt_preset_never_raises(plugin_data):
    _write_preset(plugin_data, "{not json")
    assert RP.resolve_retrieval_profile() == "lean"
    assert RP.ignored_values() == []  # unreadable preset is the bootstrap checks' problem


# --------------------------------------------------------------------------- #
# writer — preserves siblings, refuses what does not parse
# --------------------------------------------------------------------------- #
def test_write_preserves_embed_model(plugin_data):
    path = _write_preset(plugin_data, {"embed_model": "some/model"})
    RP.write_retrieval_profile("high")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data == {"embed_model": "some/model", "retrieval_profile": "high"}
    # and resolve_embed_model still sees its key through the shared file
    assert B.resolve_embed_model() == "some/model"


def test_write_creates_missing_file(plugin_data):
    written = RP.write_retrieval_profile("floor")
    assert json.load(open(written, encoding="utf-8")) == {"retrieval_profile": "floor"}


def test_write_refuses_unknown_profile(plugin_data):
    with pytest.raises(ValueError):
        RP.write_retrieval_profile("turbo")


def test_write_refuses_corrupt_file(plugin_data):
    _write_preset(plugin_data, "{not json")
    with pytest.raises(ValueError):
        RP.write_retrieval_profile("high")


def test_write_refuses_without_plugin_data(monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    with pytest.raises(ValueError):
        RP.write_retrieval_profile("high")


def test_remove_keeps_siblings(plugin_data):
    path = _write_preset(plugin_data, {"embed_model": "some/model", "retrieval_profile": "high"})
    assert RP.remove_retrieval_profile() is True
    assert json.load(open(path, encoding="utf-8")) == {"embed_model": "some/model"}
    assert RP.remove_retrieval_profile() is False  # idempotent


# --------------------------------------------------------------------------- #
# floor wiring — ONE choke point: build_index.dense_disabled()
# --------------------------------------------------------------------------- #
@pytest.fixture
def no_kill_switch(monkeypatch):
    monkeypatch.delenv("HIPPO_DISABLE", raising=False)
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)


def test_floor_profile_disables_dense(plugin_data, monkeypatch, no_kill_switch):
    monkeypatch.setenv(RP.ENV_VAR, "floor")
    assert B.dense_disabled() is True


def test_lean_and_high_keep_dense(plugin_data, monkeypatch, no_kill_switch):
    for profile in ("lean", "high"):
        monkeypatch.setenv(RP.ENV_VAR, profile)
        assert B.dense_disabled() is False


def test_kill_switch_still_wins(plugin_data, monkeypatch):
    monkeypatch.setenv(RP.ENV_VAR, "high")
    monkeypatch.setenv("HIPPO_DISABLE", "dense")
    assert B.dense_disabled() is True


# --------------------------------------------------------------------------- #
# the hardware gate — gates the OFFER, never the switch
# --------------------------------------------------------------------------- #
def test_offer_only_when_qualifying(plugin_data, monkeypatch):
    monkeypatch.setattr(RP, "qualifies_for_high", lambda: True)
    monkeypatch.setattr(RP, "machine_memsize_bytes", lambda: 128 * 2**30)
    line = RP.high_offer_line()
    assert line and "128 GiB" in line and "--profile high" in line


def test_no_offer_when_under_bar(plugin_data, monkeypatch):
    monkeypatch.setattr(RP, "qualifies_for_high", lambda: False)
    assert RP.high_offer_line() is None


def test_no_offer_when_already_high(plugin_data, monkeypatch):
    monkeypatch.setattr(RP, "qualifies_for_high", lambda: True)
    monkeypatch.setenv(RP.ENV_VAR, "high")
    assert RP.high_offer_line() is None


def test_memsize_probe_failure_means_no_offer(monkeypatch):
    monkeypatch.setattr(RP, "machine_memsize_bytes", lambda: None)
    assert RP.qualifies_for_high() is False


# --------------------------------------------------------------------------- #
# doctor line
# --------------------------------------------------------------------------- #
def _ctx(tmp_path):
    return DoctorContext(str(tmp_path / "mem"), str(tmp_path), plugin_data="", plugin_root="")


def test_doctor_names_profile_and_source(plugin_data, tmp_path, monkeypatch):
    monkeypatch.setattr(RP, "qualifies_for_high", lambda: False)
    res = check_retrieval_profile(_ctx(tmp_path))
    assert res["status"] == "ok"
    assert "lean" in res["message"] and "default" in res["message"]


def test_doctor_floor_says_bm25_only(plugin_data, tmp_path, monkeypatch):
    monkeypatch.setenv(RP.ENV_VAR, "floor")
    res = check_retrieval_profile(_ctx(tmp_path))
    assert res["status"] == "ok"
    assert "BM25-only" in res["message"]


def test_doctor_warns_on_ignored_value(plugin_data, tmp_path, monkeypatch):
    monkeypatch.setenv(RP.ENV_VAR, "turbo")
    res = check_retrieval_profile(_ctx(tmp_path))
    assert res["status"] == "warn"
    assert "turbo" in res["message"]


def test_doctor_carries_the_offer(plugin_data, tmp_path, monkeypatch):
    monkeypatch.setattr(RP, "qualifies_for_high", lambda: True)
    monkeypatch.setattr(RP, "machine_memsize_bytes", lambda: 128 * 2**30)
    res = check_retrieval_profile(_ctx(tmp_path))
    assert res["status"] == "ok"
    assert "--profile high" in res["message"]


# --------------------------------------------------------------------------- #
# setup CLI surface
# --------------------------------------------------------------------------- #
def test_cli_bare_profile_shows_state(plugin_data, capsys, monkeypatch):
    monkeypatch.setattr(RP, "qualifies_for_high", lambda: False)
    assert setup_cli.main(["--profile"]) == 0
    out = capsys.readouterr().out
    assert "active profile is `lean`" in out


def test_cli_preview_writes_nothing(plugin_data, capsys):
    assert setup_cli.main(["--profile", "high"]) == 0
    out = capsys.readouterr().out
    assert "Nothing was written" in out
    assert not os.path.exists(os.path.join(str(plugin_data), "model.json"))


def test_cli_yes_writes_and_names_the_hcr2_gap(plugin_data, capsys, monkeypatch):
    monkeypatch.setattr(RP, "qualifies_for_high", lambda: True)
    assert setup_cli.main(["--profile", "high", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "lean's models" in out
    assert RP.profile_source() == ("high", "preset")


def test_cli_write_under_bar_still_allowed_but_noted(plugin_data, capsys, monkeypatch):
    monkeypatch.setattr(RP, "qualifies_for_high", lambda: False)
    assert setup_cli.main(["--profile", "high", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "hardware bar" in out
    assert RP.resolve_retrieval_profile() == "high"


def test_cli_unknown_profile_is_an_error(plugin_data, capsys):
    assert setup_cli.main(["--profile", "turbo"]) == 2
    assert "unknown profile" in capsys.readouterr().out


def test_cli_off_removes(plugin_data, capsys):
    RP.write_retrieval_profile("floor")
    assert setup_cli.main(["--profile", "--off"]) == 0  # preview
    assert RP.resolve_retrieval_profile() == "floor"
    assert setup_cli.main(["--profile", "--off", "--yes"]) == 0
    assert RP.resolve_retrieval_profile() == "lean"


def test_cli_listing_includes_profile(plugin_data, capsys):
    assert setup_cli.main([]) == 2
    assert "--profile" in capsys.readouterr().out
