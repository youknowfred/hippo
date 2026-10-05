"""SRF-2: the v2 MCP toolset — ten annotated tools beside v1 names that still route."""

from __future__ import annotations

import json
import os

import pytest

from memory import mcp_server as M
from memory.mcp_schemas_v2 import (
    ANNOTATIONS,
    DEPRECATED,
    OUTPUT_SCHEMAS,
    PROTOCOL_VERSIONS,
    V2_NAMES,
    V2_TOOLS,
)
from .conftest import git_commit, write_file

_MEM = """---
name: deploy_runbook
description: "how the web service is deployed via the canary lane"
metadata:
  type: project
---

Deploy via the canary lane.
"""


@pytest.fixture(autouse=True)
def _reset_negotiation(monkeypatch):
    monkeypatch.setattr(M, "_NEGOTIATED", None)


def _init(version):
    params = {} if version is None else {"protocolVersion": version}
    return M.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params})


def _tools():
    return M.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]


def _call(name, args=None):
    return M.handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                             "params": {"name": name, "arguments": args or {}}})["result"]


@pytest.fixture
def corpus(repo, memory_dir, monkeypatch):
    write_file(memory_dir, "deploy_runbook.md", _MEM)
    git_commit(repo, "corpus", 1_700_000_000)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", memory_dir)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    return repo, memory_dir


def test_the_v2_set_is_ten_tools_and_fits_its_budget():
    assert V2_NAMES == ("recall", "new_memory", "inspect", "tend", "doctor", "setup", "trust",
                        "share", "dream", "review")
    _init(PROTOCOL_VERSIONS[0])
    v2 = [t for t in _tools() if t["name"] in V2_NAMES]
    assert len(v2) == 10
    assert len(json.dumps(v2)) <= 15_000, len(json.dumps(v2))


def test_negotiation_echoes_a_spoken_version_and_counters_an_unknown_one():
    assert _init("2025-06-18")["result"]["protocolVersion"] == "2025-06-18"
    assert _init("1999-01-01")["result"]["protocolVersion"] == PROTOCOL_VERSIONS[0]
    assert _init(None)["result"]["protocolVersion"] == "2024-11-05"


def test_annotations_and_output_schemas_follow_the_negotiated_version():
    _init("2024-11-05")
    assert not any("annotations" in t or "outputSchema" in t for t in _tools())
    _init("2025-03-26")
    tools = _tools()
    assert all("annotations" in t for t in tools) and not any("outputSchema" in t for t in tools)
    _init("2025-06-18")
    by = {t["name"]: t for t in _tools()}
    assert set(n for n, t in by.items() if "outputSchema" in t) == set(OUTPUT_SCHEMAS)
    assert by["recall"]["annotations"]["readOnlyHint"] is True
    assert by["tend"]["annotations"]["destructiveHint"] is True


def test_every_tool_is_annotated_and_every_v1_name_still_routes():
    for name in M._DISPATCH:
        assert name in ANNOTATIONS, name
    for old, (new, _how) in DEPRECATED.items():
        assert old in M._DISPATCH and new in M._DISPATCH and new in V2_NAMES


def test_deprecated_names_say_so_in_the_listing_and_in_every_answer(corpus):
    _init(PROTOCOL_VERSIONS[0])
    by = {t["name"]: t for t in _tools()}
    for old, (new, _how) in DEPRECATED.items():
        assert by[old]["description"].startswith(f"Deprecated — use `{new}`")
    res = _call("decision_history", {"name": "deploy_runbook"})
    assert len(res["content"]) == 2 and "removed in v2.0" in res["content"][1]["text"]
    assert "`inspect` with action='history'" in res["content"][1]["text"]
    assert len(_call("recall", {"query": "deploy canary"})["content"]) == 1  # a v2 name: no notice


@pytest.mark.parametrize("old,old_args,new,new_args", [
    ("why", {"query": "deploy canary"}, "inspect", {"action": "why", "query": "deploy canary"}),
    ("decision_history", {"name": "deploy_runbook"}, "inspect", {"action": "history", "name": "deploy_runbook"}),
    ("secrets_scan", {"text": "x = 1"}, "doctor", {"action": "secrets_scan", "text": "x = 1"}),
    ("trust_corpus", {}, "trust", {"action": "review"}),
    ("bootstrap", {"action": "status"}, "setup", {"action": "bootstrap", "step": "status"}),
])
def test_a_v2_route_answers_exactly_what_the_v1_name_did(corpus, old, old_args, new, new_args):
    assert _call(old, old_args)["content"][0]["text"] == _call(new, new_args)["content"][0]["text"]


def _conforms(schema, value):
    for key in schema.get("required", []):
        assert key in value, key
    for key, sub in schema.get("properties", {}).items():
        if key in value and sub.get("type") == "array":
            assert isinstance(value[key], list)


def test_read_tools_return_conforming_structured_content(corpus):
    _init("2025-06-18")
    rec = _call("recall", {"query": "deploy canary lane"})
    _conforms(OUTPUT_SCHEMAS["recall"], rec["structuredContent"])
    assert rec["structuredContent"]["hits"][0]["name"] == "deploy_runbook"
    assert rec["structuredContent"]["text"] == rec["content"][0]["text"]
    bad = _call("recall", {})
    _conforms(OUTPUT_SCHEMAS["recall"], bad["structuredContent"])
    doc = _call("doctor", {})  # doctor's own launch check must not reset this session's version
    _conforms(OUTPUT_SCHEMAS["doctor"], doc["structuredContent"])
    assert doc["structuredContent"]["checks"]
    tend = _call("tend", {"action": "list"})
    _conforms(OUTPUT_SCHEMAS["tend"], tend["structuredContent"])
    _init("2024-11-05")
    assert "structuredContent" not in _call("recall", {"query": "deploy canary lane"})


def test_a_v1_name_is_counted_by_the_name_used(corpus, monkeypatch):
    seen = []
    monkeypatch.setattr("memory.telemetry_rollup.record_usage",
                        lambda td, **kw: seen.append(kw["verb"]))
    os.makedirs(os.path.join(os.path.dirname(corpus[1]), ".memory-telemetry"), exist_ok=True)
    _call("why", {"query": "deploy canary"})
    _call("inspect", {"action": "why", "query": "deploy canary"})
    assert seen == ["why", "inspect"]


def test_review_serves_the_packet(corpus):
    repo, md = corpus
    write_file(md, "new_fact.md", _MEM.replace("deploy_runbook", "new_fact"))
    text = _call("review", {})["content"][0]["text"]
    assert text.startswith("## memory review") and "new_fact" in text


def test_doctor_names_permission_rules_for_deprecated_tools(tmp_path, monkeypatch, repo):
    from memory.doctor_checks_env import DoctorContext
    from memory.doctor_checks_platform import check_mcp_allowlist

    settings = tmp_path / "user-settings.json"
    settings.write_text(json.dumps({"permissions": {"allow": [
        "mcp__plugin_hippo_hippo__why", "mcp__plugin_hippo_hippo__recall"]}}), encoding="utf-8")
    monkeypatch.setenv("HIPPO_CLAUDE_SETTINGS", str(settings))
    r = check_mcp_allowlist(DoctorContext(memory_dir=str(tmp_path), repo_root=repo))
    assert r["status"] == "warn"
    assert "mcp__plugin_hippo_hippo__why → mcp__plugin_hippo_hippo__inspect" in r["message"]
    assert "hippo_hippo__recall →" not in r["message"]
