"""INT-2 core tool implementations — the frozen v1.0 surface (STABILITY.md): recall,
new_memory, traverse, why (GOV-5), and decision_history (RCH-3), plus the shared SEC-1
untrusted-corpus remedy every refusal on this surface appends. Decomposed out of
``mcp_server.py`` as pure code motion; the façade re-imports every name, so
``memory.mcp_server.<name>`` stays importable."""

from __future__ import annotations

from typing import Any, Dict


# --------------------------------------------------------------------------- #
# Tool implementations — each returns a plain string; never raises.
# --------------------------------------------------------------------------- #

# The ONE untrusted-corpus remedy every SEC-1 refusal on THIS surface appends. It names
# this server's own tools FIRST (always present here — INT-9..12 — and the only working
# invocation on surfaces that reject typed commands, e.g. the Claude Desktop app) and the
# typed terminal commands second.
_UNTRUSTED_REMEDY = (
    "Review and trust it with this server's doctor + trust_corpus tools — or the init tool "
    "if the corpus is yours (in a terminal: /hippo:doctor, or /hippo:init)."
)


def _tool_recall(args: Dict[str, Any]) -> str:
    from .recall_view import describe

    query = str(args.get("query") or "").strip()
    if not query:
        return "recall: a non-empty query is required."
    k = args.get("k")
    k = int(k) if isinstance(k, (int, float)) and int(k) > 0 else 10
    # MSR-3: agent-issued recalls are channel-tagged on the recall ledger (SEC-1/SEC-3
    # gated inside describe) — this surface was telemetry-invisible before.
    return describe(query, k, channel="mcp")


def _tool_new_memory(args: Dict[str, Any]) -> str:
    from . import trust
    from .new_memory import write_memory
    from .provenance import resolve_dirs

    name = str(args.get("name") or "").strip()
    description = str(args.get("description") or "").strip()
    mtype = str(args.get("type") or "").strip()
    if not (name and description and mtype):
        return "new_memory: name, description, and type are all required."
    # SEC-13: honor the trust gate on the WRITE path, exactly as recall + the resources do.
    # Without this, a subagent in an untrusted-but-writable clone could WRITE memories it
    # cannot READ — the write-without-read asymmetry. Gate on the same corpus resolve_dirs
    # hands write_memory (it resolves the same way with no explicit dirs), so the refusal and
    # the would-be write target are always the same corpus.
    memory_dir, repo_root = resolve_dirs()
    gate_root = trust.gate_repo_root(memory_dir, repo_root)
    if gate_root is not None and not trust.is_trusted(gate_root):
        return (
            "new_memory REFUSED — this project's memory corpus is untrusted (SEC-13: writing "
            "to an unreviewed corpus is gated just as reading it is — and the check dry-run "
            "reads its descriptions). " + _UNTRUSTED_REMEDY
        )
    if args.get("check"):
        # CAP-3: the check-FIRST dry-run — the same check_candidate the CLI --check runs,
        # rendered the same way, so the drain can route add/update/supersede/skip BEFORE
        # any file exists. Writes nothing (no file, no index refresh, no floor edit).
        from .new_memory import check_candidate

        decision = check_candidate(name, description, mtype, body=str(args.get("body") or ""))
        out = [f"check (dry-run — nothing was written): route = {decision['route']}"]
        # GOV-3: the proposal-time git baseline — the honest anchor a reviewer can check
        # out ("as of HEAD <sha>"). source_commit exists only after the real write.
        if decision.get("baseline"):
            out.append(f"baseline: as of HEAD {decision['baseline'][:12]}")
        else:
            out.append("baseline: no git HEAD at proposal time (non-git corpus)")
        if decision["neighbors"]:
            out.append("neighbors (decide update-existing / supersede / skip — NAME the target):")
            for n in decision["neighbors"]:
                desc = str(n["description"]).replace("\n", " ").strip()
                if len(desc) > 220:
                    desc = desc[:217].rstrip() + "…"
                out.append(f"  • {n['name']} (similarity {n['score']:.2f}) — {desc}")
        elif decision["route"] == "add":
            out.append("  → no near-duplicate cleared the threshold: safe to add as a new memory.")
        # RUL-3: rules-plane echoes flag but never flip the route — a wording decision.
        if decision.get("rule_neighbors"):
            out.append("warning : restates the governance plane — link, don't copy:")
            for r in decision["rule_neighbors"]:
                out.append(f"  • {r['file']} (overlap {r['score']:.2f}) — \"{r['preview']}\"")
        # SEN-1: render the write ticket verbatim at the same approval-prompt step the
        # CLI --check does — one gate stamp, two surfaces, no drift.
        from .new_memory import render_write_ticket

        ticket_block = render_write_ticket(decision.get("ticket"))
        if ticket_block:
            out.append(ticket_block)
        if decision.get("note"):
            out.append(f"note: {decision['note']}")
        out.append(
            "Next: route add → call new_memory again WITHOUT check to write; route review → "
            "update-existing (edit the named memory) / supersede (write the new one, then "
            "reconsolidate action='reverify' outcome='demote' superseded_by=<the-new-name> "
            "on the old) / skip."
        )
        return "\n".join(out)
    links = args.get("links")
    links = [str(x) for x in links] if isinstance(links, list) else None
    confidence = args.get("confidence")
    confidence = str(confidence) if isinstance(confidence, str) and confidence else None
    result = write_memory(
        name, description, mtype, str(args.get("body") or ""), links=links,
        confidence=confidence,
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


def _tool_why(args: Dict[str, Any]) -> str:
    """GOV-5: delegates to the SAME recall_view.describe(why=True) code path the
    /hippo:recall --why CLI uses — one receipt implementation, two surfaces."""
    from .recall_view import describe

    query = str(args.get("query") or "").strip()
    if not query:
        return "why: a non-empty query is required."
    k = args.get("k")
    k = int(k) if isinstance(k, (int, float)) and int(k) > 0 else 10
    # MSR-3: same channel tag as the recall tool — a why-receipt lookup is a real
    # agent-issued recall the usage ledger must count.
    return describe(query, k, why=True, channel="mcp")


def _tool_traverse(args: Dict[str, Any]) -> str:
    from . import trust
    from .build_index import default_index_dir
    from .links import TYPED_RELATIONS, build_graph
    from .provenance import resolve_dirs

    name = str(args.get("name") or "").strip()
    if not name:
        return "traverse: a memory name is required."
    hops = args.get("hops")
    hops = int(hops) if isinstance(hops, (int, float)) and int(hops) >= 1 else 1
    memory_dir, repo_root = resolve_dirs()
    # SEC-1: gate on trust exactly as recall/why/new_memory and every resource do. The link
    # graph renders memory NAMES + typed edges into agent context; on an untrusted foreign
    # corpus those names are themselves attacker-controlled injection surface, so withhold
    # them until the corpus is reviewed — the read-without-trust gap traverse used to leave.
    gate_root = trust.gate_repo_root(memory_dir, repo_root)
    if gate_root is not None and not trust.is_trusted(gate_root):
        return (
            "traverse: withheld — this project's memory corpus is untrusted (SEC-1: the link "
            "graph exposes memory names and typed edges, gated just as recall is). "
            + _UNTRUSTED_REMEDY
        )
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


def _tool_decision_history(args: Dict[str, Any]) -> str:
    """RCH-3: delegates to the SAME history.render_decision_history the
    /hippo:recall --history CLI renders — one chain builder, two surfaces."""
    from . import trust
    from .build_index import default_index_dir
    from .history import render_decision_history
    from .provenance import resolve_dirs

    name = str(args.get("name") or "").strip()
    if not name:
        return "decision_history: a memory name is required."
    memory_dir, repo_root = resolve_dirs()
    # SEC-1: gate on trust like every sibling. The lineage narrative renders memory names,
    # dates, and typed edges — withhold them on an untrusted foreign corpus (the same
    # read-without-trust gap traverse had).
    gate_root = trust.gate_repo_root(memory_dir, repo_root)
    if gate_root is not None and not trust.is_trusted(gate_root):
        return (
            "decision_history: withheld — this project's memory corpus is untrusted (SEC-1: "
            "the lineage narrative exposes memory names, dates, and typed edges, gated just as "
            "recall is). " + _UNTRUSTED_REMEDY
        )
    return render_decision_history(name, memory_dir, default_index_dir(memory_dir))
