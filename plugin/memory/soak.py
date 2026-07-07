"""Soak ledger + curation analyzer over the recall-event telemetry (read-only).

Reads the recall-event ledger (``memory/telemetry.py``) into two decisions:

  - ``soak_status()`` — how many DISTINCT sessions have been logged, and whether the
    ``>=5``-session CURATION-SOAK bar is met (enough distinct sessions that the dead-weight
    curation signal below is minimally trustworthy rather than topic-noise from one session).
  - ``curation_report()`` — per-memory recall-hit counts, the NEVER-RECALLED set (curation
    "dead weight" — read with the topic-bias caveat: cold tracks recent session mix, not
    value), and the BM25-fallback rate (dense unavailable on some session).

LIF-4: the ledger is a byte-capped rotating buffer, so on a long-lived corpus its oldest
evidence is exactly what rotation drops first — a genuinely-used memory would drift toward
"never recalled" if the ledger were the only source. Every analyzer here therefore UNIONS
the rotation-surviving ``usage_aggregates.json`` (``telemetry.read_usage_aggregates``) into
its session counts and recalled-set; the raw per-EVENT hit counts remain ledger-window-only
(the aggregates deliberately don't store event counts).

Read-only over the LEDGER + AGGREGATES and the corpus; never raises; output bounded. This is
a CLI/analysis surface — it is NOT a SessionStart producer (the former Option-C soak announcer
was removed: the auto-extraction draft queue it announced was killed, so a met gate must not
advertise it).
"""

from __future__ import annotations

import os
from collections import Counter
from typing import List, Optional

from .telemetry import default_telemetry_dir, read_events, read_usage_aggregates

# Curation-soak bar: enough distinct sessions that the never-recalled signal isn't one-session
# topic noise (a floor for trusting the dead-weight report, NOT an Option-C unblock gate).
SOAK_GATE_SESSIONS = 5

# Backends that actually served results (an empty recall logs backend="none").
_SERVING_BACKENDS = ("dense+bm25", "dense", "bm25")


# --------------------------------------------------------------------------- #
# Soak status (the curation-soak session bar)
# --------------------------------------------------------------------------- #
def soak_status(telemetry_dir: Optional[str] = None) -> dict:
    """Distinct-session count + whether the ``>=5``-session curation-soak bar is met.

    ``distinct_sessions`` is the LARGER of the ledger-window count and the
    rotation-surviving aggregate count (LIF-4) — the two observe the same session stream,
    so max() is the honest union: rotation can only shrink the ledger's view, and a
    deleted/reset aggregate file can only shrink the aggregate's. ``total_events`` stays
    ledger-window-only (the aggregates don't store event counts). Read-only; never raises.
    """
    sessions: set = set()
    total = 0
    agg_sessions = 0
    try:
        for e in read_events(telemetry_dir):
            total += 1
            sid = e.get("session_id")
            if sid:
                sessions.add(sid)
        agg_sessions = read_usage_aggregates(telemetry_dir)["sessions"]["count"]
    except Exception:
        pass
    distinct = max(len(sessions), agg_sessions)
    return {
        "distinct_sessions": distinct,
        "total_events": total,
        "gate_threshold": SOAK_GATE_SESSIONS,
        "gate_met": distinct >= SOAK_GATE_SESSIONS,
    }


# --------------------------------------------------------------------------- #
# Curation report (dead-weight + backend health)
# --------------------------------------------------------------------------- #
def _corpus_names(memory_dir: str) -> List[str]:
    """Memory slug names in the corpus (excludes MEMORY.md / MEMORY.full.md). [] on failure."""
    try:
        from .provenance import _iter_memory_files

        return [os.path.splitext(os.path.basename(p))[0] for p in _iter_memory_files(memory_dir)]
    except Exception:
        return []


def curation_report(memory_dir: str, telemetry_dir: Optional[str] = None) -> dict:
    """Per-memory hit counts, the never-recalled set, and the BM25-fallback rate.

    ``never_recalled`` = corpus memories that never surfaced in any recall event — in the
    retained ledger OR in the rotation-surviving aggregates (LIF-4: a memory whose only
    recalls rotated out of the ledger is NOT dead weight). ``recalled_count`` counts that
    same union, so it can exceed ``len(per_memory_hits)`` — the raw hit Counter stays
    ledger-window-only by design (the aggregates don't store event counts).
    ``bm25_fallback_rate`` = fraction of result-serving events that fell back to BM25-only
    (dense unavailable). Read-only; never raises.
    """
    hits: Counter = Counter()
    serving = 0
    bm25_only = 0
    total = 0
    agg_recalled: set = set()
    try:
        for e in read_events(telemetry_dir):
            total += 1
            for name in e.get("names") or []:
                if name:
                    hits[name] += 1
            backend = e.get("backend")
            if backend in _SERVING_BACKENDS:
                serving += 1
                if backend == "bm25":
                    bm25_only += 1
        agg_recalled = set(read_usage_aggregates(telemetry_dir)["memories"])
    except Exception:
        pass

    corpus = _corpus_names(memory_dir)
    recalled = set(hits) | agg_recalled
    never_recalled = sorted(n for n in corpus if n not in recalled)
    bm25_fallback_rate = round(bm25_only / serving, 4) if serving else 0.0
    return {
        "total_events": total,
        "per_memory_hits": dict(hits),
        "recalled_count": len(recalled),
        "corpus_count": len(corpus),
        "never_recalled": never_recalled,
        "never_recalled_count": len(never_recalled),
        "serving_events": serving,
        "bm25_fallback_events": bm25_only,
        "bm25_fallback_rate": bm25_fallback_rate,
    }


# --------------------------------------------------------------------------- #
# Strength scores (topic-bias-resistant alternative to raw hit counts) — REPORT ONLY
# --------------------------------------------------------------------------- #
def compute_strength_scores(telemetry_dir: Optional[str] = None) -> dict:
    """Per-memory STRENGTH: distinct sessions that recalled it / total distinct sessions.

    Topic-bias-resistant alternative to ``curation_report()``'s raw per-event hit Counter: a
    memory recalled 5 times in ONE chatty session scores identically to one recalled there
    once (the numerator counts SESSIONS, not events), so a single session's topic mix can't
    inflate a memory's apparent staying power. The denominator is the FULL distinct-session
    pool (mirrors ``soak_status()``'s count), not just sessions that recalled *something* — a
    memory surfaced in every session ever logged scores ``1.0``; one never recalled is simply
    absent from the returned dict (read it as ``0.0``).

    LIF-4: both sides of the ratio union the rotation-surviving aggregates with the
    retained ledger — per-name numerator and total denominator each take the LARGER of the
    two observations of the same session stream, so a memory whose earliest recalls rotated
    out of the ledger keeps its staying power (and a score can never exceed ``1.0``: a
    name's aggregate count can't exceed the aggregate total, nor a ledger name-count the
    ledger total — a hand-corrupted aggregate file is clamped anyway).

    REPORT-ONLY: does not write anything, and is never consulted by ``recall()``'s ranking —
    folding it into ranking is a separate, explicitly DEFERRED roadmap item (K2). Read-only
    over the ledger + aggregates; never raises; ``{}`` when no sessions have been logged yet.
    """
    sessions_per_name: dict = {}
    all_sessions: set = set()
    agg_memories: dict = {}
    agg_total = 0
    try:
        for e in read_events(telemetry_dir):
            sid = e.get("session_id")
            if sid:
                all_sessions.add(sid)
            if not sid:
                continue
            for name in e.get("names") or []:
                if name:
                    sessions_per_name.setdefault(name, set()).add(sid)
        agg = read_usage_aggregates(telemetry_dir)
        agg_memories = agg["memories"]
        agg_total = agg["sessions"]["count"]
    except Exception:
        pass
    total = max(len(all_sessions), agg_total)
    if not total:
        return {}
    scores: dict = {}
    for name in set(sessions_per_name) | set(agg_memories):
        agg_n = (agg_memories.get(name) or {}).get("sessions")
        if not isinstance(agg_n, int) or isinstance(agg_n, bool) or agg_n < 0:
            agg_n = 0
        n = max(len(sessions_per_name.get(name, ())), agg_n)
        if n:
            scores[name] = round(min(n / total, 1.0), 4)
    return scores


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    from .provenance import resolve_dirs

    parser = argparse.ArgumentParser(description="Recall soak ledger + curation report (read-only).")
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--telemetry-dir", default=None)
    args = parser.parse_args(argv)

    memory_dir, _ = resolve_dirs()
    memory_dir = args.memory_dir or memory_dir
    td = args.telemetry_dir or default_telemetry_dir(memory_dir)

    status = soak_status(td)
    report = curation_report(memory_dir, td)

    print("=== Recall soak ledger ===")
    print(f"distinct sessions     : {status['distinct_sessions']} (curation-soak bar >= {status['gate_threshold']})")
    print(f"total events          : {status['total_events']}")
    print(f"curation soak         : {'MET ✅' if status['gate_met'] else 'pending'}")
    if status["gate_met"]:
        print("  (enough distinct sessions for the dead-weight signal below to be minimally meaningful)")
    print()
    print("=== Curation report ===")
    print(f"corpus memories        : {report['corpus_count']}")
    print(f"recalled >= once       : {report['recalled_count']}")
    print(f"never recalled (dead weight): {report['never_recalled_count']}")
    serving = report["serving_events"]
    print(
        f"bm25-fallback rate     : {report['bm25_fallback_rate'] * 100:.1f}% "
        f"({report['bm25_fallback_events']}/{serving} serving events)"
    )
    if report["per_memory_hits"]:
        print("top recalled:")
        ranked = sorted(report["per_memory_hits"].items(), key=lambda kv: (-kv[1], kv[0]))
        for name, n in ranked[:10]:
            print(f"  • {name}: {n}")

    strength = compute_strength_scores(td)
    if strength:
        print()
        print("=== Strength scores (report-only; distinct sessions recalled / total sessions) ===")
        ranked_strength = sorted(strength.items(), key=lambda kv: (-kv[1], kv[0]))
        for name, score in ranked_strength[:10]:
            print(f"  • {name}: {score}")

    if report["never_recalled"]:
        print("never-recalled memories:")
        for name in report["never_recalled"][:50]:
            print(f"  • {name}")
        if report["never_recalled_count"] > 50:
            print(f"  …and {report['never_recalled_count'] - 50} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
