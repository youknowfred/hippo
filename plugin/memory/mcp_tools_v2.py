"""SRF-2: the v2 tools' handlers — five new names that route to the v1 handlers, and the
structured results of the read tools.

``inspect``, ``setup``, ``trust``, ``share`` and ``review`` are new names; each routes by
``action`` to the handler the v1 name already used, so a v1 call and its v2 route return
the same text. ``recall``, ``doctor`` and ``tend`` keep their names and gain structured
content (returned as ``(text, structured)``; the server drops the structured half for a
client that negotiated an older protocol). No behavior lives here that a v1 handler does
not already have — this is routing.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple, Union

Result = Union[str, Tuple[str, dict]]


def _action(args: Dict[str, Any], default: str = "") -> str:
    v = args.get("action")
    return v.strip() if isinstance(v, str) and v.strip() else default


def _without(args: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    return {k: v for k, v in args.items() if k not in keys}


def _unknown(tool: str, action: str, allowed) -> str:
    return f"{tool}: unknown action {action!r} — one of {', '.join(allowed)}."


# --------------------------------------------------------------------------- #
# Read tools with structured content
# --------------------------------------------------------------------------- #
def _tool_recall_v2(args: Dict[str, Any]) -> Result:
    from .recall_view import describe

    query = str(args.get("query") or "").strip()
    if not query:
        return "recall: a non-empty query is required."
    k = args.get("k")
    k = int(k) if isinstance(k, (int, float)) and int(k) > 0 else 10
    hits: list = []
    text = describe(query, k, channel="mcp", collect=hits)
    return text, {"text": text, "query": query, "abstained": not hits, "hits": hits}


_GLYPHS = {"✔": "ok", "⚠": "warn", "✘": "fail"}


def _doctor_structured(text: str) -> dict:
    checks = []
    for line in text.splitlines():
        status = _GLYPHS.get(line[:1])
        if status:
            checks.append({"status": status, "message": line[1:].strip()})
    counts = {s: sum(1 for c in checks if c["status"] == s) for s in ("ok", "warn", "fail")}
    return {"text": text, "action": "check", "checks": checks, "counts": counts}


def _tool_doctor_v2(args: Dict[str, Any]) -> Result:
    from .mcp_tools_consolidate import _tool_secrets_scan
    from .mcp_tools_packs import _tool_audit
    from .mcp_tools_setup import _tool_doctor

    action = _action(args, "check")
    if action == "check":
        text = _tool_doctor({})
        return text, _doctor_structured(text)
    if action == "audit":
        text = _tool_audit(_without(args, "action"))
    elif action == "secrets_scan":
        text = _tool_secrets_scan(_without(args, "action"))
    else:
        return _unknown("doctor", action, ("check", "audit", "secrets_scan"))
    return text, {"text": text, "action": action}


# --------------------------------------------------------------------------- #
# New names routing to v1 handlers
# --------------------------------------------------------------------------- #
def _tool_inspect(args: Dict[str, Any]) -> str:
    from .mcp_tools_core import _tool_decision_history, _tool_traverse, _tool_why
    from .mcp_tools_packs import _tool_blast_radius

    routes = {
        "why": _tool_why,
        "traverse": _tool_traverse,
        "history": _tool_decision_history,
        "blast_radius": _tool_blast_radius,
    }
    action = _action(args)
    fn = routes.get(action)
    if fn is None:
        return _unknown("inspect", action, routes)
    return fn(_without(args, "action"))


def _tool_setup(args: Dict[str, Any]) -> str:
    from .mcp_tools_consolidate import _tool_build_index
    from .mcp_tools_setup import _tool_bootstrap, _tool_init

    action = _action(args)
    if action == "bootstrap":
        step = args.get("step") if args.get("step") in ("status", "start") else "status"
        return _tool_bootstrap({"action": step, "multilingual": bool(args.get("multilingual"))})
    if action == "init":
        return _tool_init({})
    if action == "build_index":
        return _tool_build_index({})
    return _unknown("setup", action, ("bootstrap", "init", "build_index"))


def _trust_status() -> str:
    from . import trust
    from .provenance import resolve_dirs

    memory_dir, repo_root = resolve_dirs()
    gate = trust.gate_repo_root(memory_dir, repo_root)
    if gate is None:
        return "trust: this project has no memory corpus yet — nothing to consent to."
    if not trust.is_trusted(gate):
        return ("trust: not consented — recall and writes are withheld. action='review' shows "
                "what would be injected; 'grant' with its digest consents after the user agrees.")
    drift = trust.untrusted_changes(gate, memory_dir)
    changed, added = drift.get("changed") or [], drift.get("added") or []
    if changed or added:
        return (f"trust: consented, but {len(changed)} changed and {len(added)} new file(s) since "
                "then are withheld from recall — action='review' shows them.")
    return "trust: consented; nothing has changed since."


def _tool_trust(args: Dict[str, Any]) -> str:
    from .mcp_tools_packs import _tool_untrust
    from .mcp_tools_setup import _tool_trust_corpus

    action = _action(args, "status")
    if action == "status":
        return _trust_status()
    if action == "review":
        return _tool_trust_corpus({})
    if action == "grant":
        digest = args.get("digest")
        if not isinstance(digest, str) or not digest.strip():
            return "trust: action='grant' needs digest=… from action='review' (after the user agrees)."
        return _tool_trust_corpus({"confirm_digest": digest.strip()})
    if action == "revoke":
        return _tool_untrust(_without(args, "action"))
    return _unknown("trust", action, ("status", "review", "grant", "revoke"))


def _tool_share(args: Dict[str, Any]) -> str:
    from .mcp_tools_packs import (
        _tool_pack_extract,
        _tool_pack_install_item,
        _tool_pack_install_plan,
        _tool_pack_update_item,
        _tool_pack_update_plan,
    )

    routes = {
        "pack_extract": _tool_pack_extract,
        "pack_install_plan": _tool_pack_install_plan,
        "pack_install_item": _tool_pack_install_item,
        "pack_update_plan": _tool_pack_update_plan,
        "pack_update_item": _tool_pack_update_item,
    }
    action = _action(args)
    fn = routes.get(action)
    if fn is None:
        return _unknown("share", action, routes)
    return fn(_without(args, "action"))


def _tool_review(args: Dict[str, Any]) -> str:
    from .mcp_tools_packs import _corpus_gate
    from .review import run

    refusal, memory_dir, repo_root = _corpus_gate("review", "the packet renders corpus text")
    if refusal:
        return refusal
    argv = []
    rng = args.get("range")
    if isinstance(rng, str) and rng.strip():
        argv.append(rng.strip())
    if args.get("ci"):
        argv.append("--ci")
    _code, text = run(argv, memory_dir=memory_dir, repo_root=repo_root)
    return text
