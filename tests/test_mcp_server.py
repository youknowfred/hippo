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


# The frozen v1.0 tool surface (STABILITY.md) — these five names, FIRST and in this order.
_FROZEN_TOOLS = ["recall", "new_memory", "traverse", "why", "decision_history"]
# Additive post-1.0 tools (INT-9..12): the setup flows, for surfaces without typed /hippo:*.
_SETUP_TOOLS = ["doctor", "bootstrap", "init", "trust_corpus"]
# Additive verb tools: /dream (DRM-2) — the generative sleep pass with notify-with-undo.
_VERB_TOOLS = ["dream"]
# Additive consolidate-flow tools (INT-13): /hippo:consolidate's five steps as per-item
# primitives, for surfaces whose Bash tool never inherits CLAUDE_PLUGIN_DATA (the desktop
# app) and for subagents — listed in the skill's own step order.
_CONSOLIDATE_TOOLS = [
    "capture", "secrets_scan", "reconsolidate", "build_index",
    "co_recall_proposals", "abstention_fixtures",
]
# Additive corpus-REPAIR tools (INT-14/15): a category of their own, not consolidate steps —
# they exist purely to undo a defect hippo itself shipped. MIG-1's re-derivation and COR-10's
# baseline heal were CLI-only in v1.15.0, so the DRV-2 nudge (a hook — it fires on BOTH
# surfaces) routed a Desktop user to doctor, and doctor routed them nowhere.
_REPAIR_TOOLS = ["rederive", "heal_baselines"]
# Additive pack tools (INT-16): /hippo:pack's five primitives, in the skill's own flow
# order. Pre-INT-16 the pack skill's preflight ABORTED on Desktop ("re-run from a
# terminal") and agents hand-rolled venv paths around it — the exact gap INT-13 closed
# for consolidate.
_PACK_TOOLS = [
    "pack_extract", "pack_install_plan", "pack_install_item",
    "pack_update_plan", "pack_update_item",
]
# Additive INV-4 tools: the two nudge-routed verbs' second surface (scope ratified
# 2026-07-16 — resolve + audit ONLY; the other five terminal-only verbs keep their
# honest preflights). Appended at the END per STABILITY.md's position freeze.
_INV4_TOOLS = ["resolve", "audit"]
# Additive EXT-3 tool (T17): consolidate's asks step — the interview loop (≤3 grounded
# questions; declines remembered in telemetry). Appended after INV-4, same freeze.
_EXT3_TOOLS = ["interview"]
# Additive SEN-5 incident-response tools (T10): untrust (revoke) + blast_radius (read-only
# forensics). Appended after EXT-3, same position freeze.
_INCIDENT_TOOLS = ["untrust", "blast_radius"]


def test_tools_list_exposes_frozen_five_plus_setup_tools():
    resp = M.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in resp["result"]["tools"]]
    assert names == (
        _FROZEN_TOOLS + _SETUP_TOOLS + _VERB_TOOLS + _CONSOLIDATE_TOOLS + _REPAIR_TOOLS
        + _PACK_TOOLS + _INV4_TOOLS + _EXT3_TOOLS + _INCIDENT_TOOLS
    )
    for t in resp["result"]["tools"]:
        assert t["inputSchema"]["type"] == "object"  # every tool has a JSON schema
    # STABILITY.md: the frozen five keep their names, shapes AND positions. Additive tools
    # append; they never reorder what shipped. This test is what made that true here — the
    # repair tools were first written into the middle of the consolidate block.
    assert names[: len(_FROZEN_TOOLS)] == _FROZEN_TOOLS


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


# --------------------------------------------------------------------------- #
# INT-16 — the pack tools: /hippo:pack on the surface whose Bash tool never sees
# CLAUDE_PLUGIN_DATA. The extract text must carry the COMPLETE refusal reason map
# (the Desktop transcript's "the reason isn't in the fields I printed" failure).
# --------------------------------------------------------------------------- #
def test_pack_extract_tool_extracts_all_and_reports_every_refusal_reason(corpus, tmp_path):
    dest = str(tmp_path / "site-pack")
    resp = _call("pack_extract", {"dest": dest, "all": True})
    text = _text(resp)
    assert "✔ extracted 2 memories" in text
    assert os.path.isfile(os.path.join(dest, "manifest.json"))

    # A refusal names EVERY problem in the tool text itself — nothing to forget to print.
    resp2 = _call("pack_extract", {"dest": dest, "names": ["deploy_runbook", "ghost"]})
    t2 = _text(resp2)
    assert "zero files written" in t2
    assert "ghost: not found" in t2
    assert "deploy_runbook" in t2  # the collision with the already-extracted copy, too


def test_pack_extract_tool_requires_dest_and_a_selection(corpus, tmp_path):
    assert "'dest' is required" in _text(_call("pack_extract", {}))
    resp = _call("pack_extract", {"dest": str(tmp_path / "p"), "names": []})
    assert "never glob the corpus dir" in _text(resp)


def test_pack_install_tools_plan_then_one_item(corpus, tmp_path):
    src = str(tmp_path / "src-pack")
    os.makedirs(src)
    with open(os.path.join(src, "lesson.md"), "w") as fh:
        fh.write(_mem("lesson", "never deploy on friday afternoons", mtype="feedback"))
    with open(os.path.join(src, "manifest.json"), "w") as fh:
        json.dump({
            "pack": "lessons", "version": "1.0.0", "title": "lessons",
            "description": "test pack", "seed_by_default": False,
            "memories": [{"file": "lesson.md"}],
        }, fh)

    plan_text = _text(_call("pack_install_plan", {"source_dir": src}))
    assert "UNTRUSTED DATA" in plan_text  # the SEC-5 demarcation discipline, in-band
    assert 'will inject → "never deploy on friday afternoons"' in plan_text
    assert "ONE pack_install_item call each" in plan_text
    assert not os.path.exists(os.path.join(corpus, "lesson.md"))  # a plan writes NOTHING

    item_text = _text(_call("pack_install_item", {"source_dir": src, "name": "lesson"}))
    assert "✔ installed lesson" in item_text
    assert os.path.isfile(os.path.join(corpus, "lesson.md"))
    assert os.path.isfile(os.path.join(corpus, ".packs.lock.json"))


def test_pack_tools_honor_the_trust_gate(corpus, monkeypatch, tmp_path):
    """SEC-1: every pack tool is gated — extract copies memory bodies OUT of the
    corpus, so an untrusted corpus withholds it exactly as recall is withheld."""
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    resp = _call("pack_extract", {"dest": str(tmp_path / "p"), "all": True})
    assert "untrusted" in _text(resp) and "trust_corpus" in _text(resp)


def test_pack_skill_preflight_maps_every_pack_tool():
    """INT-16 mirrors INT-13's contract: the skill's guard must route Desktop to the
    tools (not claim no path exists), and every tool it names must be served."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "plugin", "skills", "pack", "SKILL.md"
    )
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert "no Desktop-safe MCP-tool equivalent" not in text  # the pre-INT-16 claim is gone
    for tool in _PACK_TOOLS:
        assert tool in text, f"pack SKILL.md no longer names the {tool} tool"
        assert tool in M._DISPATCH, f"SKILL.md names {tool} but the server does not serve it"


def test_desktop_surface_note_maps_pack_to_the_tools():
    from memory.session_start import _DESKTOP_SURFACE_NOTE as note

    for tool in _PACK_TOOLS:
        assert tool in note, f"the Desktop surface note no longer names {tool}"


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


def test_traverse_refuses_untrusted_corpus_then_walks_after_consent(corpus, monkeypatch):
    """SEC-1: MCP traverse honors the trust gate. The link graph renders memory names +
    typed edges into agent context; on an untrusted corpus those are withheld — just as
    recall/why/new_memory withhold — until the user consents (what /hippo:init or doctor do).
    conftest's autouse TRUST_ALL=1 normally opens the gate; delete it and the (non-git,
    SEC-12-gated) corpus is untrusted."""
    from memory import trust as T
    from memory.provenance import resolve_dirs

    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    text = _text(_call("traverse", {"name": "deploy_runbook", "hops": 1}))
    assert "withheld" in text
    assert "rollback_plan" not in text  # no linked memory name leaks past the gate

    assert T.mark_trusted(T.gate_repo_root(*resolve_dirs()))  # consent
    assert "rollback_plan" in _text(_call("traverse", {"name": "deploy_runbook", "hops": 1}))


def test_decision_history_refuses_untrusted_corpus_then_renders_after_consent(corpus, monkeypatch):
    """SEC-1: MCP decision_history honors the trust gate — the lineage narrative (names,
    dates, typed edges) is withheld on an untrusted corpus until the user consents."""
    from memory import trust as T
    from memory.provenance import resolve_dirs

    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    assert "withheld" in _text(_call("decision_history", {"name": "deploy_runbook"}))

    assert T.mark_trusted(T.gate_repo_root(*resolve_dirs()))  # consent
    assert "withheld" not in _text(_call("decision_history", {"name": "deploy_runbook"}))


def test_untrusted_refusals_name_this_servers_own_tools(corpus, monkeypatch):
    """Every SEC-1/SEC-13 refusal on this surface must name a remedy that WORKS here:
    the server's own doctor/trust_corpus/init tools first, typed /hippo:* second — a
    typed command is terminal-only (the Claude Desktop app rejects it), so a refusal
    that named only /hippo:doctor would dead-end the exact client it refused."""
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    for tool, args in (
        ("new_memory", {"name": "x", "description": "d", "type": "project"}),
        ("traverse", {"name": "deploy_runbook"}),
        ("decision_history", {"name": "deploy_runbook"}),
    ):
        text = _text(_call(tool, args))
        assert "trust_corpus" in text, f"{tool} refusal must name the on-surface consent tool"
        assert "/hippo:doctor" in text, f"{tool} refusal should still name the terminal path"


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


# --------------------------------------------------------------------------- #
# MSR-3: the recall/why tools pass channel="mcp" into recall_view.describe.
# --------------------------------------------------------------------------- #
def test_recall_and_why_tools_tag_the_mcp_channel(monkeypatch):
    from memory import recall_view as V

    seen = []

    def _spy(query, k, **kwargs):
        seen.append((query, kwargs.get("channel"), kwargs.get("why")))
        return "stub"

    monkeypatch.setattr(V, "describe", _spy)
    assert M._tool_recall({"query": "canary rollout"}) == "stub"
    assert M._tool_why({"query": "canary rollout"}) == "stub"
    assert seen == [
        ("canary rollout", "mcp", None),
        ("canary rollout", "mcp", True),
    ]
