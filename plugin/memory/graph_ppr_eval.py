"""HCR-5: the PPR graph lane's A/B rig — ``eval --ab HIPPO_GRAPH_PPR``.

The personalized-PageRank lane (``recall_graph._ppr_expand``) ships default-OFF beside
GRA-1's 1-hop expansion. Replacing GRA-1 is a dated owner decision on THIS evidence
(ROADMAP.retrieval.yaml HCR-5; EXPLORATIONS.retrieval.md Q4). This module is that
instrument on the DRM-3 convention — the flag's own harness, ``eval_recall.main`` the one
CLI front door — parameterizing ``ab_runner.run_flag_arms`` (inv5: never a second copy of
the OFF → ON → OFF core, its byte-identity self-check included). On top of the core's
QUA-6 per-category deltas it adds the two readings the roadmap names:

  - GRF-4's typed-2-hop reachability audit run under EACH arm. The audit seeds on the
    production ranking's top 3, so the arm changes what it sees: more expected stems at
    depth 0 (ranked as a seed) under ON is the multi-hop gain the lane is for.
  - warm recall latency per arm over the fixture's queries (volatile — reported, never
    in the identity check or the deltas).

No cache staging is needed: both lanes read the same links.json, which the index build
already writes. A corpus without it measures identical arms and says so.

MEASURES ONLY — HIPPO_GRAPH_PPR stays default-OFF and GRA-1 stays; any flip is a separate
dated owner decision on affirmative evidence, never a consequence of this report.
"""

from __future__ import annotations

import json
import os
import time
from typing import List, Optional

from .provenance import ensure_self_ignoring_dir, resolve_dirs

FLAG = "HIPPO_GRAPH_PPR"
_PPR_AB_NAME = "graph_ppr_ab.json"
_PPR_AB_SCHEMA = 1
_LATENCY_ROUNDS = 5

_FOOTER = (
    "measures only — HIPPO_GRAPH_PPR stays default-OFF and the 1-hop lane stays; "
    "replacing it is a separate dated owner decision on affirmative evidence, never an "
    "automatic consequence of this report."
)


def default_report_path(memory_dir: str, telemetry_dir: Optional[str] = None) -> str:
    """``<telemetry_dir>/graph_ppr_ab.json`` — gitignored, beside the other A/B files."""
    from .telemetry import default_telemetry_dir

    return os.path.join(telemetry_dir or default_telemetry_dir(memory_dir), _PPR_AB_NAME)


def _graph_stamp(index_dir: str) -> dict:
    """Node / walkable-pair counts of the edge list both lanes read (``{}`` when absent)."""
    try:
        from .links import load_edges
        from .recall_graph import _ppr_graph

        edges = load_edges(index_dir)
        if not edges:
            return {}
        g = _ppr_graph(edges)
        n = len(g["names"])
        pairs = int(g["src"].size)
        return {"nodes": n, "walkable_pairs": pairs, "mean_out_degree": round(pairs / n, 2) if n else 0.0}
    except Exception:
        return {}


def _arm_readings(index, hard_set: List[dict], index_dir: str, memory_dir: str, k: int, on: bool) -> dict:
    """The reachability audit and warm latency under one arm of the flag."""
    from .ab_runner import flag_context
    from .eval_arms import latency, reachability_audit

    with flag_context(FLAG, on):
        audit = reachability_audit(index, hard_set, index_dir, k=k, memory_dir=memory_dir)
        queries = [r.get("query") or "" for r in hard_set] * _LATENCY_ROUNDS
        lat = latency(index, queries, k=k, index_dir=index_dir, memory_dir=memory_dir)
    return {"reachability": audit, "latency_ms": lat}


def _reachability_shift(off: dict, on: dict) -> dict:
    """OFF vs ON audit summaries plus the rows whose depth moved (``{"skipped"}`` passes
    through when the fixture has too few multi-hop rows to audit)."""
    if off.get("skipped") or on.get("skipped"):
        return {"skipped": off.get("skipped") or on.get("skipped")}
    moved = []
    for a, b in zip(off.get("rows") or (), on.get("rows") or ()):
        if a.get("depth") != b.get("depth"):
            moved.append({"query": a["query"], "stem": a["stem"], "off": a["depth"], "on": b["depth"]})
    return {"off": off["summary"], "on": on["summary"], "moved": moved}


def run_ab(
    memory_dir: Optional[str] = None,
    index_dir: Optional[str] = None,
    hard_set_path: Optional[str] = None,
    k: int = 10,
    *,
    telemetry_dir: Optional[str] = None,
    write: bool = True,
) -> dict:
    """OFF → ON → OFF over the live corpus; per-category deltas; reachability per arm.

    The report carries the core's condition stamp (MEA-1's ``resolvable_by_category``
    included), the edge-list stamp, per-category deltas with low-n labels, the OFF-arm
    self-check, the reachability audit under each arm, warm latency per arm, and the
    footer. Never touches gates; writes only the gitignored evidence file."""
    from .ab_runner import run_flag_arms
    from .build_index import default_index_dir, load_index
    from .eval_metrics import load_hard_set
    from .recall_sections import for_eval
    from .telemetry import default_telemetry_dir

    if memory_dir is None:
        memory_dir, _ = resolve_dirs()
    if index_dir is None:
        index_dir = default_index_dir(memory_dir)
    td = telemetry_dir or default_telemetry_dir(memory_dir)

    core = run_flag_arms(
        FLAG,
        memory_dir=memory_dir,
        index_dir=index_dir,
        hard_set_path=hard_set_path,
        k=k,
        telemetry_dir=td,
    )
    if not core.get("ok"):
        return core

    readings: dict = {}
    try:
        index = for_eval(load_index(index_dir))
        hard_set = load_hard_set(hard_set_path) if hard_set_path else []
        if index is not None and len(index) and hard_set:
            off = _arm_readings(index, hard_set, index_dir, memory_dir, k, False)
            on = _arm_readings(index, hard_set, index_dir, memory_dir, k, True)
            readings = {
                "reachability": _reachability_shift(off["reachability"], on["reachability"]),
                "latency_ms": {"off": off["latency_ms"], "on": on["latency_ms"]},
            }
    except Exception as exc:
        readings = {"error": f"per-arm readings failed: {exc}"}

    graph = _graph_stamp(index_dir)
    report = {
        "ok": True,
        "schema": _PPR_AB_SCHEMA,
        "flag": FLAG,
        "generated_at": time.strftime("%Y-%m-%d"),
        "condition": {**core["condition"], "k": k, "graph": graph},
        "off_by_category": core["off_by_category"],
        "on_by_category": core["on_by_category"],
        "deltas": core["deltas"],
        "off_arm_self_check": core["off_arm_self_check"],
        "identical_arms": core["identical_arms"],
        **(
            {
                "identical_arms_note": (
                    "self-check pass — "
                    + ("no edge list: neither lane had a graph to walk" if not graph
                       else "the ranking view did not move under the flag")
                    + "; NOT a finding about the lane"
                )
            }
            if core["identical_arms"]
            else {}
        ),
        **readings,
        "footer": _FOOTER,
    }
    if write:
        written = write_report(report, default_report_path(memory_dir, td))
        report["path"] = written.get("path") if written.get("ok") else None
    return report


def write_report(report: dict, path: str) -> dict:
    """Persist the A/B evidence (atomic — a torn report is worse than none).
    ``{ok, path}`` or ``{ok: False, error}``; never raises."""
    from .atomic import write_json_atomic

    try:
        ensure_self_ignoring_dir(os.path.dirname(path))  # SEC-3 self-ignoring pattern
        write_json_atomic(path, report, indent=2)
        return {"ok": True, "path": path}
    except Exception as exc:
        return {"ok": False, "error": f"graph-walk A/B report write failed: {exc}"}


def read_report(memory_dir: str, telemetry_dir: Optional[str] = None) -> Optional[dict]:
    """The persisted A/B report, or None (absent/corrupt/wrong-schema). Never raises."""
    try:
        with open(default_report_path(memory_dir, telemetry_dir), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict) or doc.get("schema") != _PPR_AB_SCHEMA:
            return None
        return doc
    except Exception:
        return None


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="paired graph-walk lane A/B over a live corpus (measures only)."
    )
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--index-dir", default=None)
    parser.add_argument("--hard-set", default=None)
    parser.add_argument("--telemetry-dir", default=None)
    parser.add_argument("-k", type=int, default=10)
    args = parser.parse_args(argv)

    report = run_ab(
        memory_dir=args.memory_dir,
        index_dir=args.index_dir,
        hard_set_path=args.hard_set,
        k=args.k,
        telemetry_dir=args.telemetry_dir,
    )
    if not report.get("ok"):
        print(f"graph-walk A/B: {report.get('error')}")
        return 1
    cond = report["condition"]
    g = cond.get("graph") or {}
    print(
        f"graph-walk A/B [{report['flag']}] backend={cond['backend']} corpus={cond['corpus_n']} "
        f"hard_set={cond['hard_set_n']} — graph: "
        + (f"{g['nodes']} nodes, {g['walkable_pairs']} walkable pairs" if g else "none")
    )
    for cat, d in sorted(report["deltas"].items()):
        low = " [low n — report-only]" if d.get("low_n") else ""
        print(f"  {cat}: Δrecall={d['recall']:+.4f} Δmrr={d['mrr']:+.4f} n={d['n']}{low}")
    reach = report.get("reachability") or {}
    if reach.get("skipped"):
        print(f"  reachability: SKIPPED — {reach['skipped']}")
    elif reach:
        o, n_ = reach["off"], reach["on"]
        print(
            f"  reachability (seeds/row={o['seeds_per_row']}): ranked as a seed "
            f"{o['seed_rank_0']} -> {n_['seed_rank_0']}, at 1 hop {o['reachable_at_1']} -> "
            f"{n_['reachable_at_1']}, at 2 hops {o['reachable_at_2']} -> {n_['reachable_at_2']}, "
            f"unreachable {o['unreachable']} -> {n_['unreachable']}"
        )
    lat = report.get("latency_ms") or {}
    if lat:
        print(
            f"  warm latency p50/p95 ms: off {lat['off']['p50']}/{lat['off']['p95']}, "
            f"on {lat['on']['p50']}/{lat['on']['p95']} (n={lat['on']['n']})"
        )
    if report.get("identical_arms"):
        print(f"  {report['identical_arms_note']}")
    print(f"  OFF-arm self-check: {report['off_arm_self_check']}")
    if report.get("path"):
        print(f"  evidence recorded: {report['path']}")
    print(f"  {report['footer']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
