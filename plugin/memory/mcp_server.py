"""INT-2: minimal stdio MCP server — mid-turn and subagent memory access.

Recall fires exactly once per user prompt, keyed on the raw prompt text. Mid-turn — after the
agent discovers what it is actually working on — there is no retrieval path; and a subagent
launched via Task gets ZERO memory (no ``UserPromptSubmit`` fires for it), even though the
shipped corpus explicitly prescribes subagent workflows. This server closes both gaps: an MCP
server the plugin declares (``plugin.json`` → ``bin/hippo mcp`` → the PLUGIN_DATA venv python)
exposes memory as first-class tools that mid-turn calls and subagents both inherit — no new
hooks, and the hook path is untouched and still works with this server absent.

It is a dependency-free JSON-RPC 2.0 server over stdio (newline-delimited messages, stdlib
only — no ``mcp`` package, consistent with the vendoring/offline identity). Three tools:

  - ``recall(query, k)``    — REUSES ``recall_view.describe`` → ``recall.recall`` (the exact
                              hook ranking; it does not fork behavior), returning the
                              human-readable listing (type / staleness / graph neighbors).
  - ``new_memory(...)``     — the per-item, agent-gated corpus write (same ``write_memory`` the
                              /hippo:new skill runs, LIF-2 near-duplicate neighbors included so
                              the caller can route add/update/supersede — never a bulk sweep).
  - ``traverse(name, hops)``— 1..N-hop graph neighbors (untyped + typed) for a memory.

Offline + corpus-local: it pins the durable fastembed cache and honors every existing contract
(SEC-1 trust gate, RET-1 abstention, the never-raise degradation ladder). Protocol I/O goes to
stdout ONLY; all diagnostics go to stderr, so a stray print can never corrupt the JSON-RPC
stream. Any handler failure degrades to a JSON-RPC error or an ``isError`` tool result — the
read loop never dies.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional

_SERVER_NAME = "hippo"
_DEFAULT_PROTOCOL = "2024-11-05"


def _plugin_version() -> str:
    """The installed plugin version, read from plugin.json so it never drifts (DOC-7)."""
    try:
        root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        with open(os.path.join(root, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            return str(json.load(fh).get("version") or "0")
    except Exception:
        return "0"

_TOOLS = [
    {
        "name": "recall",
        "description": (
            "Recall memories relevant to a query from this project's hippo corpus — the "
            "mid-turn / subagent retrieval path (the once-per-prompt hook can't answer these). "
            "Returns each match's name, type, relevance, staleness flag, and graph neighbors. "
            "Same ranking the recall hook uses; abstains (returns nothing) on an off-topic query."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "natural-language query"},
                "k": {"type": "integer", "description": "max matches (default 10)", "minimum": 1},
            },
            "required": ["query"],
        },
    },
    {
        "name": "new_memory",
        "description": (
            "Save a new memory to this project's corpus, right-by-construction (correct "
            "frontmatter, citation-provenance backfill, index refresh, floor pointer for "
            "user/feedback types). Reports near-duplicate/conflict neighbors (warn-only) so you "
            "can decide add / update-existing / supersede / skip. A per-item, agent-initiated "
            "write — never call it in a loop to bulk-import."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "kebab/snake slug; also the filename stem"},
                "description": {"type": "string", "description": "one-line recall hook — the field recall matches"},
                "type": {"type": "string", "enum": list(("user", "feedback", "project", "reference"))},
                "body": {"type": "string", "description": "the full memory body (the WHY)"},
                "links": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "explicit related-memory names (overrides auto-discovery)",
                },
            },
            "required": ["name", "description", "type"],
        },
    },
    {
        "name": "traverse",
        "description": (
            "Walk the wikilink graph from a memory: its outbound links within N hops, its "
            "inbound referrers, and its typed relations (supersedes / contradicts / refines)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "a memory name/stem"},
                "hops": {"type": "integer", "description": "outbound depth (default 1)", "minimum": 1},
            },
            "required": ["name"],
        },
    },
]


# --------------------------------------------------------------------------- #
# Tool implementations — each returns a plain string; never raises.
# --------------------------------------------------------------------------- #
def _tool_recall(args: Dict[str, Any]) -> str:
    from .recall_view import describe

    query = str(args.get("query") or "").strip()
    if not query:
        return "recall: a non-empty query is required."
    k = args.get("k")
    k = int(k) if isinstance(k, (int, float)) and int(k) > 0 else 10
    return describe(query, k)


def _tool_new_memory(args: Dict[str, Any]) -> str:
    from .new_memory import write_memory

    name = str(args.get("name") or "").strip()
    description = str(args.get("description") or "").strip()
    mtype = str(args.get("type") or "").strip()
    if not (name and description and mtype):
        return "new_memory: name, description, and type are all required."
    links = args.get("links")
    links = [str(x) for x in links] if isinstance(links, list) else None
    result = write_memory(
        name, description, mtype, str(args.get("body") or ""), links=links
    )
    if result.get("error"):
        return f"new_memory failed: {result['error']}"
    out = [f"created: {result.get('path')}", f"indexed: {bool(result.get('indexed'))}"]
    floor = result.get("floor")
    if isinstance(floor, dict) and floor.get("status"):
        out.append(f"floor: {floor.get('status')}" + (f" ({floor['reason']})" if floor.get("reason") else ""))
    if result.get("related"):
        out.append("related: " + ", ".join(result["related"]))
    for n in result.get("neighbors") or []:
        out.append(
            f"⚠ near-duplicate: {n.get('name')} (similarity {n.get('score')}) — {n.get('description')}"
            "\n  decide: add / update-existing / supersede / skip (see /hippo:new)"
        )
    for w in result.get("warnings") or []:
        out.append(f"⚠ {w}")
    if result.get("note"):
        out.append(f"note: {result['note']}")
    return "\n".join(out)


def _tool_traverse(args: Dict[str, Any]) -> str:
    from .build_index import default_index_dir
    from .links import TYPED_RELATIONS, build_graph
    from .provenance import resolve_dirs

    name = str(args.get("name") or "").strip()
    if not name:
        return "traverse: a memory name is required."
    hops = args.get("hops")
    hops = int(hops) if isinstance(hops, (int, float)) and int(hops) >= 1 else 1
    memory_dir, _ = resolve_dirs()
    graph = build_graph(memory_dir, default_index_dir(memory_dir))
    if graph is None:
        return "traverse: no graph available (corpus empty or unbuilt)."
    if graph.resolve(name) is None:
        return f"traverse: no memory resolves to '{name}'."
    out = [f"graph neighborhood of '{name}':"]
    reachable = sorted(graph.traverse(name, hops))
    out.append(f"  outbound (≤{hops} hop): " + (", ".join(reachable) if reachable else "(none)"))
    inbound = sorted(graph.inbound(name))
    out.append("  inbound: " + (", ".join(inbound) if inbound else "(none)"))
    for rel in TYPED_RELATIONS:
        t_out = sorted(graph.typed_outbound(name, rel))
        t_in = sorted(graph.typed_inbound(name, rel))
        if t_out:
            out.append(f"  {rel} → " + ", ".join(t_out))
        if t_in:
            out.append(f"  {rel} ← (this is {rel} by) " + ", ".join(t_in))
    return "\n".join(out)


_DISPATCH = {"recall": _tool_recall, "new_memory": _tool_new_memory, "traverse": _tool_traverse}


# --------------------------------------------------------------------------- #
# JSON-RPC plumbing
# --------------------------------------------------------------------------- #
def _log(msg: str) -> None:
    try:
        sys.stderr.write(f"[hippo-mcp] {msg}\n")
        sys.stderr.flush()
    except Exception:
        pass


def handle_request(req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Dispatch ONE JSON-RPC request. Returns a response dict, or None for notifications."""
    method = req.get("method")
    req_id = req.get("id")
    is_notification = "id" not in req

    def result(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": payload}

    def error(code: int, message: str) -> Optional[Dict[str, Any]]:
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    if method == "initialize":
        params = req.get("params") or {}
        proto = params.get("protocolVersion")
        return result(
            {
                "protocolVersion": proto if isinstance(proto, str) else _DEFAULT_PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": _SERVER_NAME, "version": _plugin_version()},
            }
        )
    if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return None  # notifications: no response
    if method == "ping":
        return result({})
    if method == "tools/list":
        return result({"tools": _TOOLS})
    if method == "tools/call":
        params = req.get("params") or {}
        tool = params.get("name")
        args = params.get("arguments") or {}
        fn = _DISPATCH.get(tool)
        if fn is None:
            return error(-32602, f"unknown tool: {tool}")
        try:
            text = fn(args if isinstance(args, dict) else {})
            return result({"content": [{"type": "text", "text": text}]})
        except Exception as exc:  # a tool failure is an isError result, not a dead server
            _log(f"tool {tool} raised: {exc!r}")
            return result(
                {"content": [{"type": "text", "text": f"tool error: {exc}"}], "isError": True}
            )
    if is_notification:
        return None
    return error(-32601, f"method not found: {method}")


def serve(stdin=None, stdout=None) -> int:
    """Read newline-delimited JSON-RPC from stdin, write responses to stdout. Never raises."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    # Match the hook path exactly: pin the durable fastembed cache + force offline so recall
    # here loads the SAME warmed model the UserPromptSubmit hook does (no behavior fork), and
    # never triggers a synchronous download.
    try:
        from .build_index import ensure_fastembed_cache_path

        ensure_fastembed_cache_path()
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    except Exception:
        pass
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            # Not valid JSON — can't recover an id, so per JSON-RPC emit a parse error with null id.
            _write(stdout, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        if not isinstance(req, dict):
            continue
        try:
            resp = handle_request(req)
        except Exception as exc:  # last-resort guard: never let one request kill the loop
            _log(f"handler crashed: {exc!r}")
            resp = None
        if resp is not None:
            _write(stdout, resp)
    return 0


def _write(stdout, obj: Dict[str, Any]) -> None:
    try:
        stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        stdout.flush()
    except Exception:
        pass


def main(argv=None) -> int:
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
