"""Decision-chain replay (RCH-3) — the typed graph as an ACTIVE tool, not just a ranker.

The corpus already stores how decisions evolved — ``supersedes`` / ``refines`` edges
authored at write time, ``contradicts`` for open disagreements, ``invalid_after`` for
retirement boundaries — but nothing WALKED that graph into a narrative: 1-hop expansion
only widens recall's seed set. This module reconstructs "chose X → refined to Y → Z
superseded it" from the edges the corpus already has (no new edge storage; acceptance
criterion) and renders it for two surfaces with ONE builder:

  - the ``decision_history`` MCP tool — the agent pulls a chain MID-TURN ("what replaced
    the retry policy, and why is v1 retired?"), and
  - ``/hippo:recall --history <name>`` — the human "why did we decide X" front-end
    (``recall_view.main`` delegates here).

Semantics: from a seed memory, follow ``supersedes`` + ``refines`` TRANSITIVELY in BOTH
directions (``typed_inbound(x, "supersedes")`` = successors, ``typed_outbound`` =
predecessors — the declarer is always the newer side). ``contradicts`` edges are NOT
traversed — a contradiction is a BRANCH POINT (an unresolved fork), not a lineage step —
but every chain node's contradicts edges are annotated so forks are visible. Chronology
comes from each node's stored ``source_commit_time`` (``staleness.read_source_commit_time``
— the stamped epoch survives squash-merges; a node without one renders "date unknown"
rather than guessing), and each node's ``invalid_after`` boundary shows where a retired
link stopped being true. Read-only over the shipped graph; never raises.

The dependency is one-directional by design: the MCP server imports this module, never
the reverse — so recall_view can import the builder without pulling the server into any
hook-adjacent path (the hook hot path's own no-server pin stays intact).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import List, Optional

# Lineage relations — the two whose transitive closure IS the decision chain.
_CHAIN_RELATIONS = ("supersedes", "refines")


def _node_texts(memory_dir: str, stems: List[str]) -> dict:
    out = {}
    for stem in stems:
        try:
            with open(os.path.join(memory_dir, f"{stem}.md"), "r", encoding="utf-8") as fh:
                out[stem] = fh.read()
        except Exception:
            out[stem] = ""
    return out


def decision_chain(
    seed: str, memory_dir: str, index_dir: Optional[str] = None
) -> Optional[dict]:
    """Collect the supersedes/refines closure around ``seed``, annotated and dated.

    Returns ``None`` when no graph can be built or ``seed`` doesn't resolve. Otherwise::

        {"seed": <resolved stem>,
         "nodes": [{"name", "time" (epoch|None), "invalid_after" (str|None),
                    "contradicts" [stems], "contradicted_by" [stems]}, ...],
         "edges": [{"from", "relation", "to"}, ...]}

    ``nodes`` are chronological (stamped epochs ascending, undated last, name-stable);
    ``edges`` are sorted and deduplicated; ``from`` is always the DECLARER (the newer
    side: ``a supersedes b`` means a's frontmatter names b). Never raises.
    """
    try:
        from .links import build_graph
        from .staleness import read_invalid_after, read_source_commit_time

        graph = build_graph(memory_dir, index_dir)
        if graph is None:
            return None
        start = graph.resolve(seed)
        if start is None:
            return None

        seen = {start}
        queue = [start]
        edges = set()
        while queue:
            cur = queue.pop(0)
            for rel in _CHAIN_RELATIONS:
                for declarer in graph.typed_inbound(cur, rel):
                    edges.add((declarer, rel, cur))
                    if declarer not in seen:
                        seen.add(declarer)
                        queue.append(declarer)
                for target in graph.typed_outbound(cur, rel):
                    edges.add((cur, rel, target))
                    if target not in seen:
                        seen.add(target)
                        queue.append(target)

        texts = _node_texts(memory_dir, sorted(seen))
        nodes = []
        for stem in sorted(seen):
            text = texts.get(stem, "")
            nodes.append(
                {
                    "name": stem,
                    "time": read_source_commit_time(text),
                    "invalid_after": read_invalid_after(text),
                    "contradicts": sorted(graph.typed_outbound(stem, "contradicts")),
                    "contradicted_by": sorted(graph.typed_inbound(stem, "contradicts")),
                }
            )
        nodes.sort(key=lambda n: (n["time"] is None, n["time"] or 0, n["name"]))
        return {
            "seed": start,
            "nodes": nodes,
            "edges": [
                {"from": a, "relation": rel, "to": b}
                for a, rel, b in sorted(edges)
            ],
        }
    except Exception:
        return None


def _month(epoch: Optional[int]) -> str:
    if not isinstance(epoch, int):
        return "date unknown"
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m")
    except Exception:
        return "date unknown"


def render_decision_history(
    seed: str, memory_dir: str, index_dir: Optional[str] = None
) -> str:
    """The chain as an ordered narrative — ONE renderer for both surfaces. Never raises.

    Oldest first, one line per node ("chose X (2026-03)" for a lineage root, then
    "<name> (date) — supersedes/refines <targets>"), with retirement boundaries and
    contradiction branch points annotated per node, and a closing "standing today"
    line naming the chain's live tips (no successor inside the chain, not retired).
    Degrades to an honest diagnostic string when there is no graph / no such memory /
    no authored lineage edges around the seed.
    """
    chain = decision_chain(seed, memory_dir, index_dir)
    if chain is None:
        return (
            f"decision history: no memory resolves to '{seed}' "
            "(or no link graph could be built)."
        )
    if not chain["edges"]:
        return (
            f"decision history for '{chain['seed']}': no supersedes/refines edges touch "
            "it — there is no authored lineage to replay (typed edges are written at "
            "supersede/refine time; see /hippo:new and /hippo:resolve)."
        )

    by_declarer: dict = {}
    superseded = set()
    for e in chain["edges"]:
        by_declarer.setdefault(e["from"], []).append(e)
        if e["relation"] == "supersedes":
            superseded.add(e["to"])

    out = [
        f"decision history for '{chain['seed']}' "
        f"({len(chain['nodes'])} memories, oldest first):"
    ]
    for node in chain["nodes"]:
        name, when = node["name"], _month(node["time"])
        declared = sorted(
            by_declarer.get(name, []), key=lambda e: (e["relation"], e["to"])
        )
        if declared:
            steps = "; ".join(
                f"{e['relation']} {e['to']}" for e in declared
            )
            line = f"  • {name} ({when}) — {steps}"
        else:
            line = f"  • chose {name} ({when})"
        if node["invalid_after"]:
            line += f" [retired — invalid_after {node['invalid_after']}]"
        for other in node["contradicts"]:
            line += f" [branch point — contradicts {other}]"
        for other in node["contradicted_by"]:
            line += f" [branch point — contradicted by {other}]"
        out.append(line)

    standing = [
        n["name"]
        for n in chain["nodes"]
        if n["name"] not in superseded and not n["invalid_after"]
    ]
    out.append(
        "  standing today: " + (", ".join(sorted(standing)) if standing else "(none)")
    )
    return "\n".join(out)
