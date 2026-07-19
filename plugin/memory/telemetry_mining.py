"""Read-side ledger mining — decomposed out of ``telemetry.py`` (ED5R-3, pure code
motion; the façade re-imports every name here).

Two blocks: SIG-3 recall blind-spot mining (``abstention_backlog`` — recurring abstained
queries become a curation backlog) and GRW-2 Hebbian co-recall (``co_recall_pairs`` —
memory pairs that co-surface across distinct sessions, the consolidate proposal surface).
Both are read-only aggregation over the gitignored ledgers; never raise; TALLIES, never
writers.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from .telemetry_store import _resolve_dir, read_episodes, read_events

# --------------------------------------------------------------------------- #
# SIG-3: recall blind-spot mining — turn silent abstention into a curation backlog.
#
# RET-1 correctly injects nothing when nothing clears the floor, but that abstention was
# invisible: the corpus never learned what it keeps being ASKED and cannot answer. Every
# such event is already in the recall ledger as ``backend == "none"`` with a truncated
# query preview. This clusters the RECURRING ones into a legible backlog line. Ships the
# ``backend == "none"`` arm ONLY — sub-floor "near-miss" scores are never logged on an
# abstention (``log_recall_event`` records scores only for SURFACED named hits), so that
# sub-arm has no data. Read-only aggregation of the gitignored ledger; never raises.
#
# No time window: the ledger is a byte-bounded rotating buffer, so "recurring in the buffer"
# already means "recently", the analysis stays deterministic (no wall-clock input, so the
# doctor render is reproducible), and a captured blind spot self-clears — recall stops
# abstaining on it, so its cluster stops growing and rotates out.
# --------------------------------------------------------------------------- #
# Question words / articles / fillers stripped before clustering so "how do I X" and "what's
# the X" cluster on the CONTENT tokens (X), not the boilerplate. Intentionally small — a
# too-aggressive list would merge genuinely different questions.
_ABSTENTION_STOPWORDS = frozenset(
    {
        "the", "a", "an", "how", "do", "does", "did", "i", "we", "you", "to", "of", "in", "on",
        "for", "is", "are", "was", "were", "what", "whats", "why", "when", "where", "which",
        "who", "can", "could", "should", "would", "and", "or", "with", "my", "our", "this",
        "that", "it", "its", "be", "get", "got", "use", "using", "there", "here", "about",
    }
)
_ABSTENTION_JACCARD = 0.5   # content-token overlap for two abstained queries to share a cluster
_ABSTENTION_MIN_COUNT = 3   # a cluster is a "recurring" blind spot only at/above this many asks
_ABSTENTION_MAX_CLUSTERS = 5
_ABSTENTION_MAX_TERMS = 6


def _abstention_content_tokens(text: str) -> set:
    """Significant (non-stopword, length>=3) lowercased word tokens of a query preview."""
    toks = re.findall(r"[a-z0-9][a-z0-9_-]+", (text or "").lower())
    return {t for t in toks if len(t) >= 3 and t not in _ABSTENTION_STOPWORDS}


def abstention_backlog(
    telemetry_dir: Optional[str] = None,
    *,
    min_count: int = _ABSTENTION_MIN_COUNT,
    max_clusters: int = _ABSTENTION_MAX_CLUSTERS,
    channel: Optional[str] = None,
) -> List[dict]:
    """Recurring abstained-query clusters — the recall blind-spot backlog.

    Reads ``backend == "none"`` recall events, greedily clusters their query previews by
    content-token Jaccard overlap (>= ``_ABSTENTION_JACCARD``), and returns clusters asked at
    least ``min_count`` times, most-frequent first, as
    ``[{"count", "sample_query", "terms", "queries"}]``. One-off / diverse abstentions never
    reach ``min_count``, so they never surface. Read-only; never raises; ``[]`` on any failure.

    MSR-3 ``channel``: ``None`` (every pre-MSR-3 caller) clusters ALL abstentions,
    byte-identical to before; ``'hook'``/``'mcp'`` restricts to that surface's events
    (absent-means-hook on the row) — the MCP arm is the highest-intent demand signal
    (an agent explicitly asked and got nothing), surfaced by doctor's channel line.
    """
    try:
        td = _resolve_dir(telemetry_dir)
        clusters: List[dict] = []  # {"seed": set, "all": set, "count": int, "queries": [str]}
        for e in read_events(td):
            if e.get("backend") != "none":
                continue
            if channel is not None and (e.get("channel") or "hook") != channel:
                continue
            q = (e.get("query_preview") or "").strip()
            if not q:
                continue
            toks = _abstention_content_tokens(q)
            if not toks:
                continue
            best = None
            best_j = 0.0
            for c in clusters:
                union = len(toks | c["seed"])
                j = (len(toks & c["seed"]) / union) if union else 0.0
                if j > best_j:
                    best_j = j
                    best = c
            if best is not None and best_j >= _ABSTENTION_JACCARD:
                best["count"] += 1
                best["queries"].append(q)
                best["all"].update(toks)
            else:
                clusters.append({"seed": set(toks), "all": set(toks), "count": 1, "queries": [q]})
        recurring = [c for c in clusters if c["count"] >= min_count]
        # Most-asked first; tie-break by sample query for a deterministic (DOC-4) ordering.
        recurring.sort(key=lambda c: (-c["count"], c["queries"][0]))
        out: List[dict] = []
        for c in recurring[:max_clusters]:
            out.append(
                {
                    "count": c["count"],
                    "sample_query": c["queries"][0],
                    "terms": sorted(c["all"])[:_ABSTENTION_MAX_TERMS],
                    "queries": list(c["queries"]),
                }
            )
        return out
    except Exception:
        return []


# GRW-2: how many DISTINCT sessions two memories must co-surface in before the pair becomes
# an edge proposal. Deliberately HIGH so a sparse/noisy co-recall map proposes NOTHING —
# an empty result is the designed behavior on a young corpus, not a failure.
_CORECALL_MIN_SESSIONS = 3
_CORECALL_MAX_PAIRS = 20  # bounded output for the consolidate proposal turn


def co_recall_pairs(
    telemetry_dir: Optional[str] = None,
    *,
    min_sessions: int = _CORECALL_MIN_SESSIONS,
    exclude_names: Optional[set] = None,
) -> List[dict]:
    """Hebbian co-recall tally (GRW-2): memory pairs that co-surface across many sessions.

    GRA-3 links by write-time similarity, so it can never connect pairs that are semantically
    DISTANT but operationally inseparable (a bug and its unrelated-looking workaround). The
    episode buffer already records exactly that signal — which names surfaced together — and
    nothing read it. This tallies it: per session, the recalled names are UNIONED FIRST (a
    chatty single session counts ONCE, structurally), every unordered pair in that union is
    credited one distinct session, and only pairs reaching ``min_sessions`` return, as
    ``[{"pair": [a, b], "sessions": n}]`` (pair sorted, list most-sessions-first, capped at
    ``_CORECALL_MAX_PAIRS``). Below threshold → ``[]`` — the sparse map STAYS empty rather
    than proposing spurious edges. ``exclude_names`` drops names before pairing (pass
    ``lint_floor.floor_memory_names`` so always-recalled floor memories can't dominate every
    pair). Read-only over the gitignored buffer; a TALLY, never a writer — the consumer
    (the consolidate skill) proposes each edge per-item, agent-gated. Never raises.
    """
    try:
        excluded = exclude_names or set()
        by_session: Dict[str, set] = {}
        for e in read_episodes(telemetry_dir):
            sid = e.get("session_id") or ""
            names = {n for n in (e.get("recalled_names") or []) if n and n not in excluded}
            if not names:
                continue
            by_session.setdefault(str(sid), set()).update(names)
        counts: Dict[frozenset, int] = {}
        for names in by_session.values():
            ordered = sorted(names)
            for i, a in enumerate(ordered):
                for b in ordered[i + 1 :]:
                    key = frozenset((a, b))
                    counts[key] = counts.get(key, 0) + 1
        out = [
            {"pair": sorted(pair), "sessions": n}
            for pair, n in counts.items()
            if n >= min_sessions
        ]
        out.sort(key=lambda p: (-p["sessions"], p["pair"]))
        return out[:_CORECALL_MAX_PAIRS]
    except Exception:
        return []
