"""Soak ledger + curation analyzer over the recall-event telemetry (read-only).

Reads the recall-event ledger (``memory/telemetry.py``) into two decisions:

  - ``soak_status()`` — how many DISTINCT sessions have been logged, and whether the
    ``>=5``-session CURATION-SOAK bar is met (enough distinct sessions that the dead-weight
    curation signal below is minimally trustworthy rather than topic-noise from one session).
  - ``curation_report()`` — per-memory recall-hit counts, the NEVER-RECALLED set (curation
    "dead weight" — read with the topic-bias caveat: cold tracks recent session mix, not
    value), and the BM25-fallback rate (dense unavailable on some session).

Read-only over the LEDGER and the corpus; never raises; output bounded. This is a CLI/analysis
surface — it is NOT a SessionStart producer (the former Option-C soak announcer was removed: the
auto-extraction draft queue it announced was killed, so a met gate must not advertise it).
"""

from __future__ import annotations

import os
from collections import Counter
from typing import List, Optional

from .telemetry import default_telemetry_dir, read_events

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

    Read-only over the ledger; never raises.
    """
    sessions: set = set()
    total = 0
    try:
        for e in read_events(telemetry_dir):
            total += 1
            sid = e.get("session_id")
            if sid:
                sessions.add(sid)
    except Exception:
        pass
    distinct = len(sessions)
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

    ``never_recalled`` = corpus memories that never surfaced in any recall event (curation
    candidates). ``bm25_fallback_rate`` = fraction of result-serving events that fell back to
    BM25-only (dense unavailable). Read-only; never raises.
    """
    hits: Counter = Counter()
    serving = 0
    bm25_only = 0
    total = 0
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
    except Exception:
        pass

    corpus = _corpus_names(memory_dir)
    recalled = set(hits)
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

    REPORT-ONLY: does not write anything, and is never consulted by ``recall()``'s ranking —
    folding it into ranking is a separate, explicitly DEFERRED roadmap item (K2). Read-only
    over the ledger; never raises; ``{}`` when no sessions have been logged yet.
    """
    sessions_per_name: dict = {}
    all_sessions: set = set()
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
    except Exception:
        pass
    total = len(all_sessions)
    if not total:
        return {}
    return {name: round(len(sids) / total, 4) for name, sids in sessions_per_name.items()}


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
