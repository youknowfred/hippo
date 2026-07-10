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


def test_tools_list_exposes_exactly_five_tools():
    resp = M.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {"recall", "new_memory", "traverse", "why", "decision_history"}
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


def test_new_memory_refuses_untrusted_corpus_then_writes_after_consent(corpus, monkeypatch):
    """SEC-13: MCP new_memory honors the trust gate — no write-without-read asymmetry.

    conftest's autouse TRUST_ALL=1 normally opens the gate; delete it and the (non-git,
    SEC-12-gated) corpus is untrusted, so the WRITE is refused just as recall would be —
    until the user consents (what /hippo:init or /hippo:doctor do).
    """
    from memory import trust as T
    from memory.provenance import resolve_dirs

    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)  # HIPPO_TRUST_FILE stays tmp (conftest)
    resp = _call("new_memory", {
        "name": "leak_me", "description": "a write into an untrusted corpus", "type": "project",
    })
    assert "REFUSED" in _text(resp)
    assert not os.path.exists(os.path.join(corpus, "leak_me.md"))  # nothing written

    assert T.mark_trusted(T.gate_repo_root(*resolve_dirs()))  # consent
    resp2 = _call("new_memory", {
        "name": "leak_me", "description": "a write into an untrusted corpus", "type": "project",
    })
    assert "created" in _text(resp2)
    assert os.path.exists(os.path.join(corpus, "leak_me.md"))


def test_serve_rejects_oversized_message_and_keeps_serving(corpus, monkeypatch):
    """SEC-13: an oversized JSON-RPC line is refused before parsing, and the loop keeps going."""
    monkeypatch.setattr(M, "_MAX_MESSAGE_CHARS", 200)
    big = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"x": "z" * 500}})
    good = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    out = io.StringIO()
    M.serve(io.StringIO(big + "\n" + good + "\n"), out)
    responses = [json.loads(ln) for ln in out.getvalue().splitlines() if ln.strip()]
    assert any(r.get("error", {}).get("message") == "message too large" for r in responses)
    assert any(r.get("id") == 2 for r in responses)  # the valid message after it still served


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


def test_decision_history_tool_replays_the_chain(tmp_path, monkeypatch):
    md = str(tmp_path / ".claude" / "memory")
    os.makedirs(md)
    with open(os.path.join(md, "api-v1.md"), "w") as fh:
        fh.write(
            '---\nname: api-v1\ndescription: "the original api"\nmetadata:\n'
            "  type: project\n  source_commit_time: 1000000000\n---\nbody\n"
        )
    with open(os.path.join(md, "api-v2.md"), "w") as fh:
        fh.write(
            '---\nname: api-v2\ndescription: "the v2 api"\nsupersedes: ["api-v1"]\n'
            "metadata:\n  type: project\n  source_commit_time: 1700000000\n---\nbody\n"
        )
    B.build_index(md, default_index_dir(md))
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    text = _text(_call("decision_history", {"name": "api-v1"}))
    # One builder, two surfaces: the tool renders history.render_decision_history verbatim.
    from memory.history import render_decision_history

    assert text == render_decision_history("api-v1", md, default_index_dir(md))
    assert "supersedes api-v1" in text and "standing today: api-v2" in text


def test_decision_history_requires_name_and_degrades(corpus):
    assert "required" in _text(_call("decision_history", {}))
    assert "no memory resolves" in _text(_call("decision_history", {"name": "ghost"}))


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


def test_why_tool_delegates_to_the_receipt_path(corpus):
    """GOV-5: the MCP why tool and `recall_view --why` share ONE code path — identical
    receipts for identical queries, hits and abstentions both."""
    from memory.recall_view import describe

    for query in ("how do we deploy the web service", "watering indoor houseplants in winter"):
        tool_text = _text(_call("why", {"query": query, "k": 3}))
        assert tool_text == describe(query, 3, why=True)


def test_hook_path_never_imports_the_server():
    # Acceptance: the hook path is unchanged and works with the server absent — it can only be so
    # if the hot-path modules never import mcp_server.
    for mod in (R, __import__("memory.session_start", fromlist=["x"])):
        assert "mcp_server" not in inspect.getsource(mod)


# --------------------------------------------------------------------------- #
# Resources (RUL-5) — hippo://floor + hippo://rules-view, agent-pulled
# --------------------------------------------------------------------------- #
def _read(uri, req_id=77):
    return M.handle_request(
        {"jsonrpc": "2.0", "id": req_id, "method": "resources/read", "params": {"uri": uri}}
    )


def _contents(resp):
    return resp["result"]["contents"][0]


def test_initialize_declares_resources_capability():
    resp = M.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert "resources" in resp["result"]["capabilities"]
    assert "tools" in resp["result"]["capabilities"]  # unchanged


def test_resources_list_exposes_floor_rules_view_and_scorecard():
    resp = M.handle_request({"jsonrpc": "2.0", "id": 5, "method": "resources/list"})
    resources = resp["result"]["resources"]
    assert {r["uri"] for r in resources} == {
        "hippo://floor", "hippo://rules-view", "hippo://scorecard"
    }
    for r in resources:
        assert r["mimeType"] == "text/markdown"
        assert r["name"] and r["description"]
    floor = next(r for r in resources if r["uri"] == "hippo://floor")
    assert "never auto-loaded" in floor["description"]  # the no-second-channel promise


def test_resources_read_floor_returns_project_floor(corpus, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))  # hermetic repo_root
    with open(os.path.join(corpus, "MEMORY.md"), "w") as fh:
        fh.write("## User\n\n- [deploy runbook](deploy_runbook.md) — canary lane\n")
    resp = _read("hippo://floor")
    c = _contents(resp)
    assert c["uri"] == "hippo://floor" and c["mimeType"] == "text/markdown"
    assert "deploy runbook" in c["text"]  # the project floor content, pulled
    assert "agent-pulled" in c["text"]  # labelled as the explicit substitute channel


def test_resources_read_floor_empty_is_legible(corpus, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))  # hermetic repo_root
    text = _contents(_read("hippo://floor"))["text"]
    assert "Floor empty" in text  # no MEMORY.md in the fixture corpus — say so, don't fabricate


def test_resources_read_floor_withheld_for_untrusted_corpus(repo, memory_dir, monkeypatch):
    os.makedirs(memory_dir, exist_ok=True)
    with open(os.path.join(memory_dir, "MEMORY.md"), "w") as fh:
        fh.write("## User\n\n- [secret pointer](x.md) — should be withheld\n")
    monkeypatch.setenv("HIPPO_MEMORY_DIR", memory_dir)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)  # repo_root resolves to the test repo
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)  # exercise the real SEC-1 gate
    text = _contents(_read("hippo://floor"))["text"]
    assert "WITHHELD" in text and "untrusted" in text
    assert "secret pointer" not in text  # explicit refusal, never silent content


def test_resources_read_rules_view_reports_conflict_and_rot(repo, memory_dir, monkeypatch):
    os.makedirs(memory_dir, exist_ok=True)
    for name, extra in (("old_way", ""), ("new_way", "supersedes: old_way\n")):
        with open(os.path.join(memory_dir, f"{name}.md"), "w") as fh:
            fh.write(f"---\nname: {name}\ndescription: d\n{extra}metadata:\n  type: project\n---\nb\n")
    with open(os.path.join(repo, "CLAUDE.md"), "w") as fh:
        fh.write("Follow `old_way` and keep `src/gone.py` in mind.\n")
    monkeypatch.setenv("HIPPO_MEMORY_DIR", memory_dir)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)  # repo_root resolves to the test repo
    import subprocess

    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed"],
        check=True,
    )
    text = _contents(_read("hippo://rules-view"))["text"]
    assert "CLAUDE.md cites `old_way` but `new_way` supersedes it" in text
    assert "`src/gone.py`" in text and "path gone" in text
    assert "/hippo:consolidate" in text  # findings route to per-item decisions


def test_resources_read_rules_view_clean_plane_is_legible(corpus, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))  # a governance-plane-less project
    text = _contents(_read("hippo://rules-view"))["text"]
    assert "Conflicts: none" in text and "rot: none" in text


def test_resources_read_unknown_uri_is_invalid_params():
    resp = _read("hippo://nope")
    assert resp["error"]["code"] == -32602
    assert "unknown resource" in resp["error"]["message"]


def test_serve_stream_answers_resources_read(corpus):
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "resources/list"}),
        json.dumps(
            {"jsonrpc": "2.0", "id": 3, "method": "resources/read",
             "params": {"uri": "hippo://floor"}}
        ),
    ]
    out = io.StringIO()
    M.serve(io.StringIO("\n".join(lines) + "\n"), out)
    resps = [json.loads(ln) for ln in out.getvalue().splitlines()]
    assert [r["id"] for r in resps] == [1, 2, 3]
    assert "resources" in resps[0]["result"]["capabilities"]
    assert {r["uri"] for r in resps[1]["result"]["resources"]} == {
        "hippo://floor", "hippo://rules-view", "hippo://scorecard",
    }
    assert resps[2]["result"]["contents"][0]["uri"] == "hippo://floor"


def test_new_memory_tool_threads_confidence(corpus):
    """GOV-7: the MCP write surface can author the tier too — parity with --confidence."""
    resp = _call(
        "new_memory",
        {"name": "graded_via_mcp", "description": "a graded mcp fact", "type": "project",
         "confidence": "verified"},
    )
    assert "created:" in _text(resp)
    with open(os.path.join(corpus, "graded_via_mcp.md"), "r", encoding="utf-8") as fh:
        assert "  confidence: verified" in fh.read()


def test_resources_read_scorecard_rolls_up(corpus, tmp_path, monkeypatch):
    """GOV-6 extension: the same rollup as doctor's trust_scorecard line, agent-pulled."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    text = _contents(_read("hippo://scorecard"))["text"]
    assert "trust scorecard:" in text
    assert "contested-unresolved (→ /hippo:resolve)" in text
    assert "/hippo:doctor" in text  # the drill-down route


def test_resources_read_scorecard_withheld_for_untrusted_corpus(repo, memory_dir, monkeypatch):
    monkeypatch.setenv("HIPPO_MEMORY_DIR", memory_dir)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)  # exercise the real SEC-1 gate
    text = _contents(_read("hippo://scorecard"))["text"]
    assert "WITHHELD" in text and "untrusted" in text
    assert "contested-unresolved" not in text  # no counts leak past the gate
