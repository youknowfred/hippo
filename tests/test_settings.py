"""SRF-4: config consolidation — the corpus policy file, the plugin options, the frozen env set,
and every old spelling still routing (with a doctor line and a once-per-session count)."""

from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

from memory import settings as S
from memory import telemetry_rollup as TR
from memory.attention import CALM, FULL, attention_mode, attention_source, muted_signals
from memory.doctor_checks_env import DoctorContext
from memory.doctor_checks_settings import check_settings
from memory.provenance_format import (
    legacy_policy_keys,
    read_floor_lint,
    read_fold_digests,
    read_policy_key,
    read_volatile_paths,
)

_HERE = os.path.dirname(__file__)
_PLUGIN = os.path.abspath(os.path.join(_HERE, "..", "plugin"))
_RESOLVE_SH = os.path.join(_PLUGIN, "hooks", "_resolve_py.sh")


def _write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


@pytest.fixture
def md(tmp_path):
    d = str(tmp_path / ".claude" / "memory")
    os.makedirs(d)
    return d


# --------------------------------------------------------------------------- #
# Corpus policy: hippo.json first, .format second (both read through v1.43)
# --------------------------------------------------------------------------- #
def test_policy_keys_read_from_the_legacy_format_marker(md):
    _write_json(os.path.join(md, ".format"), {
        "corpus_format": 5, "volatile_paths": ["ROADMAP.md"], "fold_digests": ["folded-*.md"],
        "floor_lint": {"max_line": 120},
    })
    assert read_volatile_paths(md) == ["ROADMAP.md"]
    assert read_fold_digests(md) == ["folded-*.md"]
    assert read_floor_lint(md)["max_line"] == 120
    assert legacy_policy_keys(md) == ["floor_lint", "fold_digests", "volatile_paths"]


def test_hippo_json_wins_over_format_key_by_key(md):
    _write_json(os.path.join(md, ".format"), {
        "corpus_format": 5, "volatile_paths": ["OLD.md"], "fold_digests": ["old-*.md"],
    })
    _write_json(os.path.join(md, "hippo.json"), {"volatile_paths": ["NEW.md"]})
    assert read_volatile_paths(md) == ["NEW.md"]          # hippo.json answers
    assert read_fold_digests(md) == ["old-*.md"]          # .format still answers the rest
    assert read_policy_key(md, "corpus_format") == 5      # the format axis stays in .format
    _write_json(os.path.join(md, "hippo.json"), {"volatile_paths": []})
    assert read_volatile_paths(md) == []                  # an explicit empty list wins too


def test_junk_hippo_json_falls_back_to_format(md):
    _write_json(os.path.join(md, ".format"), {"volatile_paths": ["A.md"]})
    with open(os.path.join(md, "hippo.json"), "w") as fh:
        fh.write("{not json")
    assert read_volatile_paths(md) == ["A.md"]


# --------------------------------------------------------------------------- #
# attention / mute: env > plugin option > hippo.json > default
# --------------------------------------------------------------------------- #
def test_attention_precedence_with_the_policy_file(md):
    _write_json(os.path.join(md, "hippo.json"), {"attention": "calm"})
    assert attention_source({}, md) == (CALM, "hippo.json")
    assert attention_mode({"CLAUDE_PLUGIN_OPTION_CALM_SESSION_START": "false"}, md) == FULL
    assert attention_mode({"CLAUDE_PLUGIN_OPTION_CALM_SESSION_START": "${user_config.calm_session_start}"}, md) == CALM
    assert attention_mode({"HIPPO_ATTENTION": "full", "CLAUDE_PLUGIN_OPTION_CALM_SESSION_START": "true"}, md) == FULL
    assert attention_mode({}, None) == FULL


def test_mute_list_from_hippo_json_and_env_override(md):
    _write_json(os.path.join(md, "hippo.json"), {"mute": ["staleness", "trust_drift"]})
    assert muted_signals({}, md) == ({"staleness"}, {"trust_drift"})
    _write_json(os.path.join(md, "hippo.json"), {"mute": "link_health, staleness"})
    assert muted_signals({}, md)[0] == {"link_health", "staleness"}
    assert muted_signals({"HIPPO_MUTE": "recent"}, md)[0] == {"recent"}


# --------------------------------------------------------------------------- #
# Plugin options: unsubstituted placeholders and empty values are unset
# --------------------------------------------------------------------------- #
def test_plugin_option_treats_placeholder_and_empty_as_unset():
    assert S.plugin_option("llm_api_key", {"CLAUDE_PLUGIN_OPTION_LLM_API_KEY": "${user_config.llm_api_key}"}) is None
    assert S.plugin_option("llm_api_key", {"CLAUDE_PLUGIN_OPTION_LLM_API_KEY": "  "}) is None
    assert S.plugin_option("llm_api_key", {}) is None
    assert S.plugin_option("llm_api_key", {"CLAUDE_PLUGIN_OPTION_LLM_API_KEY": " sk-x "}) == "sk-x"
    assert S.option_bool("capture_llm", {"CLAUDE_PLUGIN_OPTION_CAPTURE_LLM": "true"}) is True
    assert S.option_bool("capture_llm", {"CLAUDE_PLUGIN_OPTION_CAPTURE_LLM": "False"}) is False
    assert S.option_bool("capture_llm", {"CLAUDE_PLUGIN_OPTION_CAPTURE_LLM": "maybe"}) is None


def test_llm_key_and_model_precedence(tmp_path, monkeypatch):
    from memory import llm_client as L

    cfg = str(tmp_path / "hippo-llm.json")
    _write_json(cfg, {"api_key": "sk-file", "model": "file-model"})
    monkeypatch.setenv("HIPPO_LLM_CONFIG", cfg)
    assert L._api_key() == "sk-file" and L.model_name() == "file-model"
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LLM_API_KEY", "${user_config.llm_api_key}")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LLM_MODEL", "")
    assert L._api_key() == "sk-file" and L.model_name() == "file-model"
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LLM_API_KEY", "sk-option")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LLM_MODEL", "option-model")
    assert L._api_key() == "sk-option" and L.model_name() == "option-model"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    monkeypatch.setenv("HIPPO_LLM_MODEL", "env-model")
    assert L._api_key() == "sk-env" and L.model_name() == "env-model"


def test_llm_opt_ins_read_the_plugin_options(tmp_path, monkeypatch):
    from memory.capture_triage import triage_enabled
    from memory.dream_config import contradictions_enabled
    from memory.dream_generate import generative_enabled

    cfg = str(tmp_path / "hippo-llm.json")
    _write_json(cfg, {"capture_triage": True})
    monkeypatch.setenv("HIPPO_LLM_CONFIG", cfg)
    assert triage_enabled() is True                      # legacy file still read
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_CAPTURE_LLM", "false")
    assert triage_enabled() is False                     # a saved option outranks the file
    monkeypatch.setenv("HIPPO_CAPTURE_LLM", "1")
    assert triage_enabled() is True                      # env outranks the option
    assert contradictions_enabled() is False and generative_enabled() is False
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_DREAM_CONTRADICTIONS", "true")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_DREAM_GENERATIVE", "true")
    assert contradictions_enabled() is True and generative_enabled() is True
    monkeypatch.setenv("HIPPO_DREAM_GENERATIVE", "0")
    assert generative_enabled() is False


def test_dream_contra_knobs_gain_the_prefix_and_keep_the_old_name(monkeypatch):
    from memory.dream_config import contra_max_pairs, contra_min_cofire

    monkeypatch.setenv("DREAM_CONTRA_MAX_PAIRS", "3")
    monkeypatch.setenv("DREAM_CONTRA_MIN_COFIRE", "0.5")
    assert contra_max_pairs() == 3 and contra_min_cofire() == 0.5
    monkeypatch.setenv("HIPPO_DREAM_CONTRA_MAX_PAIRS", "4")
    monkeypatch.setenv("HIPPO_DREAM_CONTRA_MIN_COFIRE", "0.7")
    assert contra_max_pairs() == 4 and contra_min_cofire() == 0.7


# --------------------------------------------------------------------------- #
# HIPPO_DISABLE: the list equals every legacy flag (Python and bash)
# --------------------------------------------------------------------------- #
def _feature_fns():
    from memory.build_index import dense_disabled
    from memory.jit import jit_disabled
    from memory.lint_floor import floor_nag_disabled
    from memory.presence import presence_disabled
    from memory.recall_abstain import gate_disabled

    return {
        "dense": dense_disabled, "jit": jit_disabled, "floor-nag": floor_nag_disabled,
        "presence": presence_disabled, "abstain-gate": gate_disabled,
    }


@pytest.mark.parametrize("feature", sorted(S.DISABLE_FEATURES))
def test_disable_list_matches_the_legacy_flag(feature, monkeypatch):
    legacy, _style = S.DISABLE_FEATURES[feature]
    for name in [legacy, "HIPPO_DISABLE"] + [v[0] for v in S.DISABLE_FEATURES.values()]:
        monkeypatch.delenv(name, raising=False)
    fn = _feature_fns().get(feature)
    assert S.disabled(feature) is False and (fn is None or fn() is False)
    monkeypatch.setenv(legacy, "1")
    via_legacy = S.disabled(feature), (fn() if fn else None)
    monkeypatch.delenv(legacy)
    monkeypatch.setenv("HIPPO_DISABLE", f"jit , {feature.upper().replace('-', '_')}")
    via_list = S.disabled(feature), (fn() if fn else None)
    assert via_legacy == via_list and via_list[0] is True


def test_legacy_parses_are_kept_exactly():
    assert S.disabled("floor-nag", {"HIPPO_DISABLE_FLOOR_NAG": "0"}) is True     # any non-blank
    assert S.disabled("abstain-gate", {"HIPPO_DISABLE_ABSTAIN_GATE": "2"}) is False  # closed truthy set
    assert S.disabled("abstain-gate", {"HIPPO_DISABLE_ABSTAIN_GATE": "YES"}) is True
    assert S.disabled("dense", {"HIPPO_DISABLE_DENSE": "false"}) is False
    assert S.disabled("dense", {"HIPPO_DISABLE_DENSE": "0", "HIPPO_DISABLE": "dense"}) is True
    assert S.disable_list({"HIPPO_DISABLE": "dense,warp drive"}) == ({"dense"}, {"warp", "drive"})


_BASH_CASES = [
    ({}, "dense"),
    ({"HIPPO_DISABLE": "dense"}, "dense"),
    ({"HIPPO_DISABLE": "JIT, presence"}, "presence"),
    ({"HIPPO_DISABLE": "floor_nag"}, "floor-nag"),
    ({"HIPPO_DISABLE": "dense jit"}, "jit"),
    ({"HIPPO_DISABLE": "densex"}, "dense"),
    ({"HIPPO_DISABLE": "touch-fastpath"}, "touch-fastpath"),
    ({"HIPPO_DISABLE_TOUCH_FASTPATH": "1"}, "touch-fastpath"),
    ({"HIPPO_DISABLE_JIT": "false"}, "jit"),
    ({"HIPPO_DISABLE_FLOOR_NAG": "0"}, "floor-nag"),
    ({"HIPPO_DISABLE_ABSTAIN_GATE": "On"}, "abstain-gate"),
    ({"HIPPO_DISABLE_ABSTAIN_GATE": "2"}, "abstain-gate"),
    ({"HIPPO_DISABLE": "abstain-gate"}, "abstain-gate"),
]


@pytest.mark.parametrize("env,feature", _BASH_CASES)
def test_bash_twin_agrees_with_python(env, feature):
    base = {"PATH": "/usr/bin:/bin"}
    proc = subprocess.run(
        ["bash", "-c", f'. "{_RESOLVE_SH}"; hippo_disabled {feature} && echo on || echo off'],
        env={**base, **env}, capture_output=True, text=True,
    )
    assert proc.stdout.strip() == ("on" if S.disabled(feature, env) else "off"), proc.stderr


def test_bash_twin_restores_nocasematch():
    proc = subprocess.run(
        ["bash", "-c", f'. "{_RESOLVE_SH}"; hippo_disabled dense; shopt -q nocasematch && echo leaked || echo clean'],
        env={"PATH": "/usr/bin:/bin", "HIPPO_DISABLE": "dense"}, capture_output=True, text=True,
    )
    assert proc.stdout.strip() == "clean"


# --------------------------------------------------------------------------- #
# Legacy names: doctor lines + once-per-session counting
# --------------------------------------------------------------------------- #
def test_doctor_lists_every_legacy_name_with_its_new_spelling(md, tmp_path, monkeypatch):
    for name in [v[0] for v in S.DISABLE_FEATURES.values()] + ["HIPPO_DISABLE"]:
        monkeypatch.delenv(name, raising=False)
    ctx = DoctorContext(md, str(tmp_path))
    r = check_settings(ctx)
    assert r["status"] == "ok" and "no legacy names in use" in r["message"]
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("DREAM_CONTRA_MAX_PAIRS", "3")
    monkeypatch.setenv("HIPPO_DISABLE", "dense,warp")
    _write_json(os.path.join(md, ".format"), {"corpus_format": 5, "volatile_paths": ["X.md"]})
    cfg = str(tmp_path / "hippo-llm.json")
    _write_json(cfg, {"api_key": "sk-plain", "capture_triage": True})
    monkeypatch.setenv("HIPPO_LLM_CONFIG", cfg)
    msg = check_settings(ctx)["message"]
    assert check_settings(ctx)["status"] == "warn"
    for pair in (
        "DREAM_CONTRA_MAX_PAIRS → HIPPO_DREAM_CONTRA_MAX_PAIRS",
        "HIPPO_DISABLE_DENSE → HIPPO_DISABLE=dense",
        ".format volatile_paths → hippo.json volatile_paths",
        "hippo-llm.json api_key → the plugin's llm_api_key option",
        "hippo-llm.json capture_triage → the plugin's capture_llm option",
    ):
        assert pair in msg
    assert "holds an LLM API key in plain text" in msg and "sk-plain" not in msg
    assert "unknown feature(s): warp" in msg
    assert not re.search(r"\b[A-Z]{2,5}-\d+\b", msg), "no roadmap ids in a doctor line"


def test_legacy_names_count_once_per_session(tmp_path):
    td = str(tmp_path / "tele")
    names = [("env", "HIPPO_DISABLE_DENSE"), ("config", "format.volatile_paths")]
    t0 = 1_790_000_000.0
    assert TR.record_legacy_names(td, "s1", names, now=t0) == 2
    assert TR.record_legacy_names(td, "s1", names, now=t0 + 60) == 0     # resume / compact
    assert TR.record_legacy_names(td, "s2", names[:1], now=t0 + 120) == 1
    assert TR.record_legacy_names(td, "s1", names, now=t0 + 86400) == 0  # across midnight
    usage = TR.summarize(TR.read_rollups(td, now=t0 + 86400))["surface"]
    assert usage == {"env:HIPPO_DISABLE_DENSE": 2, "config:format.volatile_paths": 1}
    with open(os.path.join(td, TR._ROLLUP_NAME)) as fh:
        assert "legacy_sessions" not in fh.read(), "session ids never reach the day rows"


def test_session_start_counts_legacy_names_once(repo, monkeypatch, capsys):
    from memory import session_start as SS
    from memory.telemetry import default_telemetry_dir

    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    _write_json(os.path.join(md, ".format"), {"corpus_format": 5, "volatile_paths": ["X.md"]})
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    monkeypatch.setenv("HIPPO_TRUST_ALL", "1")
    monkeypatch.setenv("HIPPO_DISABLE_JIT", "1")
    for _ in range(2):
        SS.main([], source="startup", session_id="sess-legacy")
    capsys.readouterr()
    usage = TR.summarize(TR.read_rollups(default_telemetry_dir(md)))["surface"]
    assert usage.get("env:HIPPO_DISABLE_JIT") == 1
    assert usage.get("config:format.volatile_paths") == 1


def test_usage_verb_has_no_reserved_characters():
    assert S.usage_verb(".format volatile_paths") == "format.volatile_paths"
    assert S.usage_verb("hippo-llm.json api_key") == "hippo-llm.api_key"
    assert S.usage_verb("HIPPO_DISABLE_DENSE") == "HIPPO_DISABLE_DENSE"


# --------------------------------------------------------------------------- #
# The manifest: options reach the MCP server; no picker; the key is sensitive
# --------------------------------------------------------------------------- #
def test_manifest_wires_every_option_into_the_mcp_server():
    doc = json.load(open(os.path.join(_PLUGIN, ".claude-plugin", "plugin.json"), encoding="utf-8"))
    opts = doc["userConfig"]
    assert set(opts) >= {"calm_session_start", "capture_llm", "dream_contradictions",
                         "dream_generative", "llm_model", "llm_api_key"}
    assert all(o["type"] in ("boolean", "string", "number") for o in opts.values())
    assert not any("options" in o for o in opts.values()), "a picker breaks Claude Code < 2.1.271"
    assert opts["llm_api_key"]["sensitive"] is True and "default" not in opts["llm_api_key"]
    env = doc["mcpServers"]["hippo"]["env"]
    for key in opts:
        assert env[S.OPTION_PREFIX + key.upper()] == "${user_config.%s}" % key
    referenced = set(re.findall(r"\$\{user_config\.([A-Za-z0-9_]+)\}", json.dumps(doc)))
    assert referenced <= set(opts), "every ${user_config.KEY} must name a declared option"


def test_stability_states_a_frozen_env_set_of_at_most_twelve():
    text = open(os.path.join(_HERE, "..", "STABILITY.md"), encoding="utf-8").read()
    block = text.split("**The v2 environment set (from v1.42).**", 1)[1].split("The six", 1)[0]
    names = set(re.findall(r"`(HIPPO_[A-Z_]+)", block))
    assert 0 < len(names) <= 12 and "HIPPO_DISABLE" in names


# --------------------------------------------------------------------------- #
# The MCP dream tool carries the contradiction switch (the key-bearing path)
# --------------------------------------------------------------------------- #
def test_mcp_dream_contradictions_is_scoped_to_the_call(monkeypatch):
    from memory import dream
    from memory import mcp_tools_setup as MT

    seen = []
    monkeypatch.setattr(dream, "run_report_pass", lambda md: (seen.append(os.environ.get("HIPPO_DREAM_CONTRADICTIONS")), (0, "ok"))[1])
    monkeypatch.setattr("memory.provenance.resolve_dirs", lambda *a, **k: ("/nonexistent", None))
    monkeypatch.delenv("HIPPO_DREAM_CONTRADICTIONS", raising=False)
    assert MT._tool_dream({"apply": False, "contradictions": True}) == "ok"
    assert MT._tool_dream({"apply": False}) == "ok"
    assert seen == ["1", None]
    assert "HIPPO_DREAM_CONTRADICTIONS" not in os.environ
