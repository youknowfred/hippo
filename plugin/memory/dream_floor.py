"""SRF-7: the completion edges applied before completions had a co-recall floor.

``dream_config.apply_eligible`` now asks a completion for any nonzero cofire (the text
mention AND at least one observed co-recall; ruling V2Q-27). Edges the linker applied
before that are still in the corpus. This module lists the ones under the floor and
offers them as ONE reviewed undo. It never runs on its own: the replay that set the floor
shows where the edges sit, not which are right, so a person looks at the list first.

The undo is ``dream_apply.undo_edges`` (the one undo implementation): byte-exact,
refuse-on-drift per edge, an appended superseding ledger line per edge. Those lines carry
``retracted_by: cofire-floor``, which the apply pass reads: a pair undone here may be
applied again once recall has returned it together, where a pair undone by hand never is.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .dream_apply import undo_edges
from .dream_config import completion_clears_floor
from .dream_ledgers import read_apply_ledger

RETRACTED_BY = "cofire-floor"
_RETRACT_REASON = "applied with no observed co-recall, before completions needed one"
_LIST_CMD = "hippo dream --below-floor"
_UNDO_CMD = "hippo dream --undo-below-floor"


def below_floor_edges(memory_dir: str) -> List[dict]:
    """Active completion edges whose recorded cofire does not clear the floor, oldest first."""
    return [
        e
        for e in read_apply_ledger(memory_dir)
        if e.get("state") == "active"
        and e.get("kind") == "completion"
        and not completion_clears_floor(e.get("cofire"))
    ]


def below_floor_line(memory_dir: str) -> Optional[str]:
    """The one line an apply pass adds while such edges exist; None when there are none."""
    try:
        n = len(below_floor_edges(memory_dir))
    except Exception:
        return None
    if not n:
        return None
    return (
        f"  ⚖ {n} completion edge(s) were applied with no observed co-recall, before "
        f"completions needed one — list them with `{_LIST_CMD}`, revert them in one step "
        f"with `{_UNDO_CMD}` (nothing reverts them for you)"
    )


def render_below_floor(memory_dir: str) -> str:
    """``dream --below-floor``: the list, and the one command that reverts it."""
    edges = below_floor_edges(memory_dir)
    if not edges:
        return "🌙 dream --below-floor: no applied completion edge sits below the co-recall floor."
    lines = [
        f"🌙 dream --below-floor — {len(edges)} applied completion edge(s) with no observed "
        "co-recall (the body names the target; recall has never returned the pair together):"
    ]
    for e in edges:
        lines.append(
            f"  • {e.get('edge_id')}  {e.get('source')} ↔ {e.get('target')}  "
            f"cofire={float(e.get('cofire') or 0):.2f}  applied {str(e.get('applied_at_ts') or '')[:10]}"
        )
    lines.append(
        f"  Review the list, then `{_UNDO_CMD}` reverts exactly these (byte-exact; an edge "
        "whose stamp was edited by hand is refused and reported). A pair reverted this way "
        "may be linked again once recall returns it together. One edge at a time: "
        "`hippo dream --undo <edge-id>`."
    )
    return "\n".join(lines)


def undo_below_floor(memory_dir: str, index_dir: Optional[str] = None) -> Tuple[int, str]:
    """``dream --undo-below-floor``: revert exactly the listed edges in one call."""
    edges = below_floor_edges(memory_dir)
    if not edges:
        return 0, "🌙 dream --undo-below-floor: no applied completion edge sits below the co-recall floor."
    return undo_edges(
        memory_dir,
        index_dir,
        edge_ids=[e["edge_id"] for e in edges],
        annotate={"retracted_by": RETRACTED_BY, "retract_reason": _RETRACT_REASON},
    )
