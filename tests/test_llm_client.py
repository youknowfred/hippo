"""memory/llm_client.py — the provider-agnostic seam for hippo's opt-in standalone LLM calls.

The contract every enrichment surface (capture triage, dream contradiction discovery) leans
on: ``complete()`` returns ``None`` on ANY failure — no key, unknown provider, HTTP error,
junk body, empty prompt — and NEVER raises; transport is stdlib urllib; provider, model,
base URL, and key are env config points, hard-coded nowhere else. Hermetic: urllib is
monkeypatched in every test — nothing here ever touches a network, and an ambient
developer ANTHROPIC_API_KEY is stripped so a real key can never leak into a test call.
"""

from __future__ import annotations

import json

import pytest

import memory.llm_client as L


@pytest.fixture(autouse=True)
def _no_ambient_keys_or_network(monkeypatch):
    """Strip real keys and BOMB the transport by default — a test that wants a fake
    response overrides urlopen itself. Any un-overridden network attempt fails loudly."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HIPPO_LLM_API_KEY", raising=False)
    monkeypatch.delenv("HIPPO_LLM_MODEL", raising=False)
    monkeypatch.delenv("HIPPO_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("HIPPO_LLM_BASE_URL", raising=False)

    def _bomb(*a, **kw):  # pragma: no cover - only fires on a contract breach
        raise AssertionError("llm_client attempted a real network call in a test")

    monkeypatch.setattr("urllib.request.urlopen", _bomb)


class _Resp:
    """Minimal stand-in for the urlopen context manager + read(n)."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self, n=-1):
        return self._body if n in (-1, None) else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_fake(monkeypatch, *, payload=None, body=None, exc=None, calls=None):
    """urlopen double: records (request, timeout), then raises ``exc`` or returns a body."""

    def impl(req, timeout=None):
        if calls is not None:
            calls.append({"req": req, "timeout": timeout})
        if exc is not None:
            raise exc
        raw = body if body is not None else json.dumps(payload).encode("utf-8")
        return _Resp(raw)

    monkeypatch.setattr("urllib.request.urlopen", impl)


def _anthropic_payload(text="hello"):
    return {"content": [{"type": "text", "text": text}]}


# ---- the fail-open contract ----------------------------------------------------------- #
def test_missing_api_key_returns_none_without_any_network_attempt():
    # autouse fixture bombed urlopen: reaching the transport would AssertionError.
    assert L.complete("hi", timeout_s=1.0) is None


def test_empty_prompt_and_nonpositive_timeout_return_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert L.complete("", timeout_s=1.0) is None
    assert L.complete("   ", timeout_s=1.0) is None
    assert L.complete("hi", timeout_s=0) is None


def test_unknown_provider_returns_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("HIPPO_LLM_PROVIDER", "not-a-provider")
    assert L.complete("hi", timeout_s=1.0) is None


def test_http_error_returns_none(monkeypatch):
    import urllib.error

    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _install_fake(monkeypatch, exc=urllib.error.URLError("boom"))
    assert L.complete("hi", timeout_s=1.0) is None


def test_junk_body_returns_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _install_fake(monkeypatch, body=b"<html>this is not json</html>")
    assert L.complete("hi", timeout_s=1.0) is None


def test_empty_content_returns_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _install_fake(monkeypatch, payload={"content": []})
    assert L.complete("hi", timeout_s=1.0) is None


# ---- the happy path + config points --------------------------------------------------- #
def test_complete_parses_anthropic_text_blocks(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _install_fake(
        monkeypatch,
        payload={
            "content": [
                {"type": "text", "text": "part one "},
                {"type": "tool_use", "id": "ignored"},
                {"type": "text", "text": "part two"},
            ]
        },
    )
    assert L.complete("hi", timeout_s=2.0) == "part one part two"


def test_request_carries_key_model_version_and_timeout(monkeypatch):
    calls = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sekrit-key")
    _install_fake(monkeypatch, payload=_anthropic_payload(), calls=calls)
    assert L.complete("the prompt", timeout_s=3.5, system="be terse") == "hello"
    assert len(calls) == 1
    req = calls[0]["req"]
    assert calls[0]["timeout"] == 3.5
    assert req.get_full_url() == L.DEFAULT_BASE_URL + "/v1/messages"
    # urllib normalizes header casing via capitalize()
    assert req.get_header("X-api-key") == "sekrit-key"
    assert req.get_header("Anthropic-version")
    body = json.loads(req.data.decode("utf-8"))
    assert body["model"] == L.DEFAULT_MODEL
    assert body["system"] == "be terse"
    assert body["messages"] == [{"role": "user", "content": "the prompt"}]


def test_model_base_url_and_key_are_env_config_points(monkeypatch):
    calls = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient")
    monkeypatch.setenv("HIPPO_LLM_API_KEY", "hippo-scoped")  # wins over the ambient key
    monkeypatch.setenv("HIPPO_LLM_MODEL", "claude-custom-tier")
    monkeypatch.setenv("HIPPO_LLM_BASE_URL", "https://proxy.example.test/")
    _install_fake(monkeypatch, payload=_anthropic_payload("ok"), calls=calls)
    assert L.complete("hi", timeout_s=1.0) == "ok"
    req = calls[0]["req"]
    assert req.get_full_url() == "https://proxy.example.test/v1/messages"
    assert req.get_header("X-api-key") == "hippo-scoped"
    assert json.loads(req.data.decode("utf-8"))["model"] == "claude-custom-tier"
    assert L.model_name() == "claude-custom-tier"


# ---- extract_json (the shared defensive parse) ----------------------------------------- #
def test_extract_json_finds_first_object_through_prose_and_fences():
    assert L.extract_json('{"a": 1}') == {"a": 1}
    assert L.extract_json('Sure! Here you go:\n```json\n{"conflict": true}\n```') == {
        "conflict": True
    }
    assert L.extract_json('prefix {"x": {"nested": [1, 2]}} suffix {"y": 2}') == {
        "x": {"nested": [1, 2]}
    }


def test_extract_json_none_on_junk_or_empty():
    assert L.extract_json(None) is None
    assert L.extract_json("") is None
    assert L.extract_json("no json here") is None
    assert L.extract_json("{broken: json") is None


# ---- the config file (~/.claude/hippo-llm.json; HIPPO_LLM_CONFIG relocates it) ---------- #
def _write_config(tmp_path, monkeypatch, obj) -> str:
    path = tmp_path / "hippo-llm.json"
    path.write_text(json.dumps(obj), encoding="utf-8")
    monkeypatch.setenv("HIPPO_LLM_CONFIG", str(path))
    return str(path)


def test_default_model_is_the_alias_not_a_dated_snapshot():
    """Owner decision 2026-07-13 (max flexibility): the default tracks the tier's current
    snapshot via the alias, so a model refresh needs no hippo release."""
    import re

    assert L.DEFAULT_MODEL == "claude-haiku-4-5"
    assert not re.search(r"\d{8}$", L.DEFAULT_MODEL), "default must not pin a dated snapshot"


def test_config_file_supplies_model_base_url_and_key(tmp_path, monkeypatch):
    calls = []
    _write_config(
        tmp_path,
        monkeypatch,
        {"model": "claude-sonnet-5", "base_url": "https://gw.example.test/", "api_key": "file-key"},
    )
    _install_fake(monkeypatch, payload=_anthropic_payload("ok"), calls=calls)
    assert L.model_name() == "claude-sonnet-5"
    assert L.complete("hi", timeout_s=1.0) == "ok"
    req = calls[0]["req"]
    assert req.get_full_url() == "https://gw.example.test/v1/messages"
    assert req.get_header("X-api-key") == "file-key"
    assert json.loads(req.data.decode("utf-8"))["model"] == "claude-sonnet-5"


def test_env_vars_win_over_the_config_file(tmp_path, monkeypatch):
    calls = []
    _write_config(tmp_path, monkeypatch, {"model": "claude-from-file", "api_key": "file-key"})
    monkeypatch.setenv("HIPPO_LLM_MODEL", "claude-from-env")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    _install_fake(monkeypatch, payload=_anthropic_payload(), calls=calls)
    assert L.complete("hi", timeout_s=1.0) == "hello"
    req = calls[0]["req"]
    assert req.get_header("X-api-key") == "env-key"
    assert json.loads(req.data.decode("utf-8"))["model"] == "claude-from-env"


def test_junk_or_absent_config_file_falls_to_defaults(tmp_path, monkeypatch):
    (tmp_path / "hippo-llm.json").write_text("not json at all", encoding="utf-8")
    monkeypatch.setenv("HIPPO_LLM_CONFIG", str(tmp_path / "hippo-llm.json"))
    assert L.file_config() == {}
    assert L.model_name() == L.DEFAULT_MODEL
    assert L.provider_name() == L.DEFAULT_PROVIDER
    # And a config-file key without a key anywhere else still means no network attempt.
    assert L.complete("hi", timeout_s=1.0) is None


def test_as_bool_matches_the_flag_convention():
    assert L.as_bool(True) is True
    assert L.as_bool("1") is True and L.as_bool(" true ") is True
    for junk in (False, "0", "yes", 1, None, {}):
        assert L.as_bool(junk) is False
