"""INT-2: minimal stdio MCP server — mid-turn and subagent memory access.

Recall fires exactly once per user prompt, keyed on the raw prompt text. Mid-turn — after the
agent discovers what it is actually working on — there is no retrieval path; and a subagent
launched via Task gets ZERO memory (no ``UserPromptSubmit`` fires for it), even though the
shipped corpus explicitly prescribes subagent workflows. This server closes both gaps: an MCP
server the plugin declares (``plugin.json`` → ``bin/hippo mcp`` → the PLUGIN_DATA venv python)
exposes memory as first-class tools that mid-turn calls and subagents both inherit — no new
hooks, and the hook path is untouched and still works with this server absent.

It is a dependency-free JSON-RPC 2.0 server over stdio (newline-delimited messages, stdlib
only — no ``mcp`` package, consistent with the vendoring/offline identity). Five core tools
(the frozen v1.0 surface, STABILITY.md):

  - ``recall(query, k)``    — REUSES ``recall_view.describe`` → ``recall.recall`` (the exact
                              hook ranking; it does not fork behavior), returning the
                              human-readable listing (type / staleness / graph neighbors).
  - ``new_memory(...)``     — the per-item, agent-gated corpus write (same ``write_memory`` the
                              /hippo:new skill runs, LIF-2 near-duplicate neighbors included so
                              the caller can route add/update/supersede — never a bulk sweep).
  - ``traverse(name, hops)``— 1..N-hop graph neighbors (untyped + typed) for a memory.
  - ``why(query, k)``       — the GOV-5 glass-box recall receipt (same ``describe(why=True)``
                              path as ``/hippo:recall --why``): per-hit winning backend, typed
                              edges, steering, salience; near-miss receipts on abstention.
  - ``decision_history(name)`` — RCH-3: replay the supersedes/refines chain around a memory
                              as an ordered narrative ("chose X → refined to Y → Z superseded
                              it"), with retirement boundaries and contradiction branch
                              points — ``history.render_decision_history``, the same builder
                              ``/hippo:recall --history`` renders.

Plus four SETUP tools (INT-9..12, additive post-1.0) — the /hippo:* setup flows re-served
for surfaces with no typed-command input. The Claude desktop app's local sessions run
installed plugins' hooks, skills, and MCP servers through the same engine as the CLI, but
reject typed ``/hippo:*`` commands — before these tools, setup was terminal-only there:

  - ``doctor()``          — the DOC-4 diagnostic engine verbatim + a fix→tool mapping for
                            this surface. Ungated: doctor IS the pre-consent review path.
  - ``bootstrap(action)`` — kick-off-and-poll per-surface provisioning (``memory.bootstrap``:
                            detached worker, sentinel-last, log tail via action="status").
                            Needed per SURFACE: the harness hands the terminal and the
                            desktop app different plugin-data dirs.
  - ``init()``            — the mechanical /hippo:init flow (``memory.init_project``). A
                            corpus this call CREATES is trusted (it is the plugin's own
                            starter content); a pre-existing corpus is NEVER auto-trusted
                            from a model-invoked surface — consent routes to trust_corpus.
  - ``trust_corpus(confirm_digest)`` — the SEC-1 consent flow, two-step: a review call
                            returns count + the injectable descriptions + a consent digest
                            (never trusts); the confirm call requires that digest, binding
                            consent to the reviewed bytes (SEC-6 fingerprint + SEC-7 origin
                            stamped; drift re-consent reviews the delta, preserves origin).

Plus the ``dream`` verb tool (DRM-2, v1.11.0) and the CONSOLIDATE-FLOW tools (INT-13) —
``/hippo:consolidate``'s five steps as thin, per-item primitives, so sleep-time
consolidation runs on surfaces where the agent's Bash tool never inherits
``CLAUDE_PLUGIN_DATA`` (the Claude desktop app) and in subagents. The skill stays the
doctrine; these are the same engine calls its bash blocks run, one approval-gated step
per call — deliberately NOT one monolithic "consolidate" tool that could batch writes
past the per-item gate:

  - ``capture(action)``       — the CAP-2 pending queue: list / discard / snooze /
                                add_decision (the drain's read + housekeeping half; the
                                corpus writes route through ``new_memory``, which grew a
                                ``check`` flag for the CAP-3 dry-run).
  - ``secrets_scan(text)``    — the drain's HARD GATE: lint exact lines BEFORE any
                                verbatim hunk is fenced into a committed body.
  - ``reconsolidate(action)`` — the LIF-1 worklist + the per-item reverify verdict
                                (graduate/fix/demote/snooze, demote's superseded_by).
  - ``build_index()``         — refresh the index + persisted link graph (Step 3).
  - ``co_recall_proposals()`` — GRW-2 co-recall edge proposals, floor names excluded,
                                already-linked pairs dropped (read-only; an approved
                                append stays a per-item agent edit).
  - ``abstention_fixtures(action)`` — the SIG-6 blind-spot loop: draft + per-item confirm.

And three RESOURCES (RUL-5) — the baseline-memory pull path for subagents:

  - ``hippo://floor``       — the always-on memory floor (project MEMORY.md + the TEA-1
                              user/private-tier portable floor) as one markdown document. A
                              Task subagent receives NONE of this automatically; reading this
                              resource at start is its explicit, agent-PULLED substitute.
  - ``hippo://rules-view``  — the rules↔memory reconciliation (RUL-1 conflict radar + RUL-2
                              rules-plane rot), so an agent can inspect where the governance
                              plane and the corpus disagree without running the audit skill.
  - ``hippo://scorecard``   — the GOV-4 trust scorecard (corpus health at a glance).

Resources are AGENT-INVOKED reads, never an implicit always-load channel — hippo's one
always-load path stays the native-memory floor (the NATIVE_MEMORY.md promise), and both
resources honor the SEC-1 trust gate (an untrusted corpus reads as an explicit "withheld"
notice, never silently as its content).

Offline + corpus-local: it pins the durable fastembed cache and honors every existing contract
(SEC-1 trust gate, RET-1 abstention, the never-raise degradation ladder). Protocol I/O goes to
stdout ONLY; all diagnostics go to stderr, so a stray print can never corrupt the JSON-RPC
stream. Any handler failure degrades to a JSON-RPC error or an ``isError`` tool result — the
read loop never dies.

Decomposed 2026-07-16 (pure code motion) into mcp_schemas / mcp_tools_core / mcp_tools_setup /
mcp_tools_consolidate / mcp_tools_packs / mcp_resources; this façade keeps the dispatch wiring
and the JSON-RPC plumbing, and re-imports every moved name so ``memory.mcp_server.<name>``
stays importable.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional

# --------------------------------------------------------------------------- #
# Decomposition re-imports — every name moved to a sibling module stays
# importable (and monkeypatchable where it is CALLED) as before; explicit and
# grouped, never a star import. The dispatch tables below bind these names.
# --------------------------------------------------------------------------- #
from .mcp_resources import (
    _RESOURCES,
    _resource_floor,
    _resource_rules_view,
    _resource_scorecard,
)
from .mcp_schemas import _TOOLS
from .mcp_tools_consolidate import (
    _tool_abstention_fixtures,
    _tool_build_index,
    _tool_capture,
    _tool_co_recall_proposals,
    _tool_heal_baselines,
    _tool_reconsolidate,
    _tool_rederive,
    _tool_secrets_scan,
)
from .mcp_tools_core import (
    _UNTRUSTED_REMEDY,
    _tool_decision_history,
    _tool_new_memory,
    _tool_recall,
    _tool_traverse,
    _tool_why,
)
from .mcp_tools_packs import (
    _corpus_gate,
    _opt_str,
    _tool_audit,
    _tool_blast_radius,
    _tool_interview,
    _tool_pack_extract,
    _tool_pack_install_item,
    _tool_pack_install_plan,
    _tool_pack_update_item,
    _tool_pack_update_plan,
    _tool_resolve,
    _tool_untrust,
)
from .mcp_tools_setup import (
    _CONSENT_DIGEST_CHARS,
    _NO_DATA_DIR_MSG,
    _consent_digest,
    _consent_review_block,
    _fresh_python,
    _subprocess_env,
    _tool_bootstrap,
    _tool_doctor,
    _tool_dream,
    _tool_init,
    _tool_trust_corpus,
)

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


_DISPATCH = {
    "recall": _tool_recall,
    "new_memory": _tool_new_memory,
    "traverse": _tool_traverse,
    "why": _tool_why,
    "decision_history": _tool_decision_history,
    "doctor": _tool_doctor,
    "bootstrap": _tool_bootstrap,
    "init": _tool_init,
    "trust_corpus": _tool_trust_corpus,
    "dream": _tool_dream,
    "capture": _tool_capture,
    "secrets_scan": _tool_secrets_scan,
    "reconsolidate": _tool_reconsolidate,
    "build_index": _tool_build_index,
    "co_recall_proposals": _tool_co_recall_proposals,
    "abstention_fixtures": _tool_abstention_fixtures,
    # INT-14/15 — corpus REPAIR, a category of its own: not a consolidate step, and the only
    # verbs that exist purely to undo a defect hippo itself shipped.
    "rederive": _tool_rederive,
    "heal_baselines": _tool_heal_baselines,
    # INT-16 — /hippo:pack's five primitives, in the skill's own flow order.
    "pack_extract": _tool_pack_extract,
    "pack_install_plan": _tool_pack_install_plan,
    "pack_install_item": _tool_pack_install_item,
    "pack_update_plan": _tool_pack_update_plan,
    "pack_update_item": _tool_pack_update_item,
    # INV-4 (scope ratified 2026-07-16): the two nudge-routed verbs' second surface —
    # resolve + audit ONLY; the other five terminal-only verbs keep their honest
    # preflights. Appended at the END: STABILITY.md freezes names, shapes AND positions.
    "resolve": _tool_resolve,
    "audit": _tool_audit,
    # EXT-3 (T17): consolidate's asks step — appended at the END (STABILITY.md freezes
    # names, shapes AND positions).
    "interview": _tool_interview,
    # SEN-5 (T10): incident-response verbs — untrust (revoke) + blast_radius (read-only
    # forensics). Appended at the END, same position freeze.
    "untrust": _tool_untrust,
    "blast_radius": _tool_blast_radius,
}


_RESOURCE_DISPATCH = {
    "hippo://floor": _resource_floor,
    "hippo://rules-view": _resource_rules_view,
    "hippo://scorecard": _resource_scorecard,
}


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
                # RUL-5: resources declared minimally ({} — no subscribe/listChanged), the
                # same style as tools; the 2024-11-05 rev supports resources/list + /read.
                "capabilities": {"tools": {}, "resources": {}},
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
    if method == "resources/list":
        return result({"resources": _RESOURCES})
    if method == "resources/read":
        params = req.get("params") or {}
        uri = params.get("uri")
        fn = _RESOURCE_DISPATCH.get(uri)
        if fn is None:
            return error(-32602, f"unknown resource: {uri}")
        try:
            text = fn()
        except Exception as exc:  # a resource failure is a legible payload, not a dead server
            _log(f"resource {uri} raised: {exc!r}")
            text = f"resource error: {exc}"
        return result({"contents": [{"uri": uri, "mimeType": "text/markdown", "text": text}]})
    if is_notification:
        return None
    return error(-32601, f"method not found: {method}")


# SEC-13: a single JSON-RPC line larger than this is rejected before it is parsed or handled.
# The largest legitimate message for this server is a new_memory call with a short body; 1 MiB
# is orders of magnitude over that, so the cap only ever trips on a runaway/adversarial payload
# (bounding json.loads + handler cost). Overridable for the rare huge-body case.
_MAX_MESSAGE_CHARS = int(os.environ.get("HIPPO_MCP_MAX_MESSAGE_CHARS") or 1_048_576)


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
        if len(line) > _MAX_MESSAGE_CHARS:
            # SEC-13: refuse an oversized message rather than parse/handle an unbounded payload.
            # The id is unrecoverable without parsing, so per JSON-RPC emit a null-id error and
            # keep serving — one bad message never wedges or kills the loop.
            _write(stdout, {"jsonrpc": "2.0", "id": None,
                            "error": {"code": -32600, "message": "message too large"}})
            continue
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
