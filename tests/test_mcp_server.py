"""Tests for memory/mcp_server.py — the INT-2 stdio MCP server.

Dependency-free JSON-RPC 2.0 over stdio (no `mcp` package). Covers the protocol surface
(initialize / tools/list / tools/call / notifications / errors), each tool against a real
corpus, the end-to-end newline-delimited stream, and the two acceptance guarantees: recall
does not fork the hook ranking, and the hook path never imports the server.
"""

from __future__ import annotations

import inspect
import io
import json
import os

import pytest

from memory import build_index as B
from memory import mcp_server as M
from memory import recall as R
from memory.build_index import default_index_dir


def _mem(name, desc, mtype="project", body="body"):
    return f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: {mtype}\n---\n{body}\n'


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    md = str(tmp_path / ".claude" / "memory")
    os.makedirs(md)
    with open(os.path.join(md, "deploy_runbook.md"), "w") as fh:
        fh.write(_mem("deploy_runbook", "how the web service is deployed via the canary lane",
                      body="Deploy via canary. See [[rollback_plan]]."))
    with open(os.path.join(md, "rollback_plan.md"), "w") as fh:
        fh.write(_mem("rollback_plan", "how to roll back a bad web deploy"))
    B.build_index(md, default_index_dir(md))
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    return md


def _call(tool, arguments, req_id=99):
    return M.handle_request(
        {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
         "params": {"name": tool, "arguments": arguments}}
    )


def _text(resp):
    return resp["result"]["content"][0]["text"]


# --------------------------------------------------------------------------- #
# Protocol surface
# --------------------------------------------------------------------------- #
def test_initialize_echoes_protocol_and_names_server():
    resp = M.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18"}}
    )
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == "2025-06-18"  # echoes the client's version
    assert resp["result"]["serverInfo"]["name"] == "hippo"
    assert "tools" in resp["result"]["capabilities"]


def test_initialize_defaults_protocol_when_absent():
    resp = M.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert resp["result"]["protocolVersion"] == "2024-11-05"


def test_tools_list_exposes_exactly_three_tools():
    resp = M.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {"recall", "new_memory", "traverse"}
    for t in resp["result"]["tools"]:
        assert t["inputSchema"]["type"] == "object"  # every tool has a JSON schema


def test_notifications_get_no_response():
    assert M.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert M.handle_request({"jsonrpc": "2.0", "method": "notifications/cancelled"}) is None


def test_ping():
    resp = M.handle_request({"jsonrpc": "2.0", "id": 7, "method": "ping"})
    assert resp["result"] == {}


def test_unknown_method_is_method_not_found():
    resp = M.handle_request({"jsonrpc": "2.0", "id": 3, "method": "no_such_method"})
    assert resp["error"]["code"] == -32601


def test_unknown_tool_is_invalid_params():
    resp = M.handle_request(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope", "arguments": {}}}
    )
    assert resp["error"]["code"] == -32602


# --------------------------------------------------------------------------- #
# Tools against a real corpus
# --------------------------------------------------------------------------- #
def test_recall_tool_answers_from_corpus(corpus):
    resp = _call("recall", {"query": "how do we deploy the web service", "k": 3})
    assert "deploy_runbook" in _text(resp)


def test_recall_tool_requires_query(corpus):
    resp = _call("recall", {"query": "   "})
    assert "non-empty query" in _text(resp)


def test_new_memory_tool_writes_a_memory(corpus):
    resp = _call("new_memory", {
        "name": "cache_ttl", "description": "the redis cache TTL is five minutes",
        "type": "project", "body": "Set in config/redis.yaml.",
    })
    text = _text(resp)
    assert "created" in text
    assert os.path.exists(os.path.join(corpus, "cache_ttl.md"))


def test_new_memory_tool_validates_required_fields(corpus):
    resp = _call("new_memory", {"name": "x"})  # missing description + type
    assert "required" in _text(resp)


def test_traverse_tool_walks_the_graph(corpus):
    resp = _call("traverse", {"name": "deploy_runbook", "hops": 1})
    text = _text(resp)
    assert "rollback_plan" in text  # deploy_runbook links to rollback_plan (outbound)
    # inbound direction resolves too
    resp_b = _call("traverse", {"name": "rollback_plan"})
    assert "deploy_runbook" in _text(resp_b)


def test_traverse_unknown_name(corpus):
    resp = _call("traverse", {"name": "does-not-exist"})
    assert "no memory resolves" in _text(resp)


# --------------------------------------------------------------------------- #
# End-to-end newline-delimited stream
# --------------------------------------------------------------------------- #
def test_serve_processes_a_stream(corpus):
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),  # no response
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "recall", "arguments": {"query": "deploy"}}}),
    ]
    out = io.StringIO()
    M.serve(io.StringIO("\n".join(lines) + "\n"), out)
    responses = [json.loads(ln) for ln in out.getvalue().splitlines()]
    # 3 responses (the notification produced none), ids 1,2,3 in order
    assert [r.get("id") for r in responses] == [1, 2, 3]
    assert "deploy_runbook" in responses[2]["result"]["content"][0]["text"]


def test_serve_emits_parse_error_on_bad_json():
    out = io.StringIO()
    M.serve(io.StringIO("{ not json\n"), out)
    resp = json.loads(out.getvalue().splitlines()[0])
    assert resp["error"]["code"] == -32700
    assert resp["id"] is None


def test_serve_survives_a_tool_that_raises(corpus, monkeypatch):
    # A tool blowing up becomes an isError result, never a dead loop.
    monkeypatch.setitem(M._DISPATCH, "recall", lambda a: (_ for _ in ()).throw(RuntimeError("boom")))
    out = io.StringIO()
    M.serve(io.StringIO(json.dumps(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "recall", "arguments": {"query": "x"}}}) + "\n"), out)
    resp = json.loads(out.getvalue().splitlines()[0])
    assert resp["result"]["isError"] is True
    assert "tool error" in resp["result"]["content"][0]["text"]


# --------------------------------------------------------------------------- #
# Acceptance guarantees
# --------------------------------------------------------------------------- #
def test_recall_tool_does_not_fork_the_hook_ranking(corpus):
    # The MCP recall tool must reflect the SAME ranking recall.recall() (the hook path) returns.
    hook_names = [h["name"] for h in R.recall("how do we deploy the web service", k=3, memory_dir=corpus)]
    tool_text = _text(_call("recall", {"query": "how do we deploy the web service", "k": 3}))
    assert hook_names, "precondition: the hook path returns something"
    for name in hook_names:
        assert name in tool_text, f"MCP recall dropped {name} that the hook path surfaced"


def test_hook_path_never_imports_the_server():
    # Acceptance: the hook path is unchanged and works with the server absent — it can only be so
    # if the hot-path modules never import mcp_server.
    for mod in (R, __import__("memory.session_start", fromlist=["x"])):
        assert "mcp_server" not in inspect.getsource(mod)
