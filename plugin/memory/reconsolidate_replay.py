"""TMB-5's succession replay — decomposed out of ``reconsolidate.py`` (the façade keeps
the worklist, the ``semantic_reverify`` write gate that TRIGGERS this, and the CLI; it
re-imports these names).

Read-only over the recall-event ledger + the index; fires ONLY inside
``semantic_reverify``'s demote+superseded_by verdict (the existing single-item,
AST-pinned path — no replay_all verb exists, and none may). Zero hot-path cost: this is
verdict-time work.
"""

from __future__ import annotations

import os
from typing import List, Optional, Set

from .telemetry import read_events

_REPLAY_MAX_QUERIES = 10  # bounded harvest — the most recent distinct previews
_REPLAY_K = 10            # the eval's own top-k; "ranks" below means "in this top-k"


def succession_replay(
    old_name: str,
    successor: str,
    memory_dir: str,
    *,
    telemetry_dir: Optional[str] = None,
) -> dict:
    """Replay historical queries that recalled ``old_name`` against the POST-VERDICT corpus.

    Harvest: the recall-event ledger's ``query_preview`` for events whose ``names``
    include ``old_name`` — the LIVE fields only (names + the 80-char preview; the
    scores/ranks arrays are MSR-territory and deliberately not read here). Distinct
    previews, most recent first, capped at ``_REPLAY_MAX_QUERIES``. Each replays through
    the same ``recall()`` API the eval drives (supplied index, offline), then classifies:

      - ``PASS``          — the successor ranks in the top-``_REPLAY_K`` (and the
                            tombstone, if present at all, ranks below it).
      - ``FAIL``          — the tombstone still surfaces while the successor is absent
                            or outranked — the supersede left a leaking tombstone.
      - ``INCONCLUSIVE``  — neither side ranks: the 80-char preview is insufficient
                            evidence either way (truncation, or the corpus moved on).

    ``{"queries": [{"query", "verdict", "successor_rank", "old_rank"}], "counts":
    {"pass", "fail", "inconclusive"}, "harvested": N}`` — ``harvested`` 0 means nothing
    to replay (no prior hit for the predecessor: report, never fabricate a query).
    Refreshes the index first (best-effort) so the replay measures what the NEXT session
    will actually see — the verdict's invalid_after and the successor's new supersedes
    edge included. Read-only otherwise; never raises.
    """
    out = {"queries": [], "counts": {"pass": 0, "fail": 0, "inconclusive": 0}, "harvested": 0}
    try:
        previews: List[str] = []
        seen: Set[str] = set()
        for e in read_events(telemetry_dir):
            if old_name not in (e.get("names") or []):
                continue
            q = (e.get("query_preview") or "").strip()
            if q and q not in seen:
                seen.add(q)
                previews.append(q)
        previews = previews[-_REPLAY_MAX_QUERIES:]
        out["harvested"] = len(previews)
        if not previews:
            return out
        try:
            from .build_index import refresh_index

            refresh_index(memory_dir)  # the edge write postdates the demote-time refresh
        except Exception:
            pass
        from .build_index import default_index_dir, load_index
        from .recall import recall

        index_dir = default_index_dir(memory_dir)
        idx = load_index(index_dir)
        for q in previews:
            try:
                names = [
                    r.get("name")
                    for r in recall(
                        q, k=_REPLAY_K, index=idx, index_dir=index_dir, memory_dir=memory_dir
                    )
                ]
            except Exception:
                names = []
            s_rank = names.index(successor) + 1 if successor in names else None
            o_rank = names.index(old_name) + 1 if old_name in names else None
            if s_rank is not None and (o_rank is None or s_rank < o_rank):
                verdict = "PASS"
            elif o_rank is not None:
                verdict = "FAIL"
            else:
                verdict = "INCONCLUSIVE"
            out["counts"][verdict.lower() if verdict != "INCONCLUSIVE" else "inconclusive"] += 1
            out["queries"].append(
                {"query": q, "verdict": verdict, "successor_rank": s_rank, "old_rank": o_rank}
            )
        return out
    except Exception:
        return out


def succession_replay_lines(old_name: str, successor: str, replay: Optional[dict]) -> List[str]:
    """The ONE replay rendering both surfaces print (CLI + the reconsolidate MCP tool)."""
    if not replay:
        return []
    if not replay.get("harvested"):
        return [
            f"succession replay: nothing to replay — no prior recall-event hit for "
            f"{old_name} (no query is fabricated in its place)"
        ]
    lines = [
        f"succession replay ({replay['harvested']} historical quer"
        + ("y" if replay["harvested"] == 1 else "ies")
        + f" that recalled {old_name}, re-run post-verdict):"
    ]
    for row in replay.get("queries") or []:
        detail = []
        if row.get("successor_rank"):
            detail.append(f"{successor} #{row['successor_rank']}")
        if row.get("old_rank"):
            detail.append(f"tombstone #{row['old_rank']}")
        suffix = f" ({', '.join(detail)})" if detail else " (neither ranks — preview insufficient)"
        lines.append(f"  {row['verdict']:12s} “{row['query']}”{suffix}")
    c = replay.get("counts") or {}
    if c.get("fail"):
        lines.append(
            f"  ⚠ {c['fail']} FAIL — the tombstone still leaks where the successor "
            "doesn't rank; consider enriching the successor's description/body with the "
            "old claim's vocabulary"
        )
    return lines
