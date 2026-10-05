"""TND-2: the ``tend`` MCP tool — the maintenance queue on every surface.

The same engine as ``hippo tend`` (``memory.tend``): list the derived queue, show the next
item with its evidence and verdicts, apply ONE verdict, or manage snoozes, skips and owner
holds. Reads and writes gate on corpus trust like every other corpus tool; ``apply`` stays
per-item (there is no apply-all), and each verdict routes to the engine call that already
owns that write.
"""

from __future__ import annotations

from typing import Any, Dict

from .mcp_tools_packs import _corpus_gate, _opt_str


def _tool_tend(args: Dict[str, Any]) -> str:
    from . import tend
    from . import tend_queue as Q

    action = _opt_str(args, "action") or "list"
    refusal, memory_dir, repo_root = _corpus_gate(
        "tend", "the queue renders corpus text and its verdicts write corpus files"
    )
    if refusal:
        return refusal
    kind = _opt_str(args, "kind")
    kinds = (kind,) if kind in Q.KINDS else None
    item = _opt_str(args, "id")

    if action in ("list", "next"):
        result = Q.build_queue(memory_dir, repo_root, kinds=kinds)
        if action == "list":
            return tend.render_list(result)
        top = result["pending"][0] if result["pending"] else None
        return tend.render_next(top, memory_dir, repo_root, Q.total_pending(result))
    if action == "release":
        key = item or kind
        if not key:
            return "tend: action='release' needs kind=… or id=…."
        r = tend.release(key, memory_dir=memory_dir, repo_root=repo_root)
    elif action == "hold":
        key = item or kind
        if not key:
            return "tend: action='hold' needs kind=… or id=…, plus reason=…."
        r = tend.hold(key, _opt_str(args, "reason") or "", memory_dir=memory_dir, repo_root=repo_root)
    else:
        if not item:
            return f"tend: action='{action}' needs id=… (from action='list')."
        if action == "show":
            entry, state = tend.find_entry(memory_dir, repo_root, item)
            if entry is None:
                return f"{item} is not in the maintenance queue now."
            text = tend.render_next(entry, memory_dir, repo_root, 1)
            return text if state == "pending" else f"{text}\n  ({state})"
        if action == "apply":
            verdict = _opt_str(args, "verdict")
            if not verdict:
                return "tend: action='apply' needs verdict=… (see action='show')."
            r = tend.apply(
                item, verdict, memory_dir=memory_dir, repo_root=repo_root,
                winner=_opt_str(args, "winner"), loser=_opt_str(args, "loser"),
                superseded_by=_opt_str(args, "superseded_by"),
            )
        elif action == "snooze":
            days = args.get("days")
            days = float(days) if isinstance(days, (int, float)) and days > 0 else 7.0
            r = tend.snooze(item, days, memory_dir=memory_dir, repo_root=repo_root)
        elif action == "skip":
            r = tend.skip(item, memory_dir=memory_dir, repo_root=repo_root)
        else:
            return ("tend: action is one of list (default), next, show, apply, snooze, skip, "
                    "hold, release.")
    return r["message"] if r["ok"] else f"refused — {r['message']}"
