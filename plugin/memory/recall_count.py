"""HOT-8: an injection count that scales with signal.

The prompt hook used to serve ``k`` rows whenever the gate let a prompt through: a median
of 10 memories per injecting prompt on every field corpus, however weak the match. The
count now comes from the evidence, not from ``k``:

  - when the query's best cosine clears the dense floor by at least ``wide``, the prompt is
    about something this corpus knows well, and up to ``k`` rows are served as before;
  - otherwise at most ``_NARROW_ROWS`` rows are served, and a row is served only when it
    has evidence of its own:
      * its best cosine (description or body chunk) is within ``band`` of the query's best
        cosine over the whole corpus, or
      * it ranks in the top ``_AGREE_DEPTH`` of both the dense lane and a lexical lane
        (HOT-2's corroboration, asked of the row instead of the query), or
      * its description is in the lexical top ``_AGREE_DEPTH`` and shares at least
        ``_STRONG_DESC_TERMS`` query terms: a description that plainly names the subject,
        which the dense lane sometimes ranks far down. HOT-2 also asks such a description
        to carry 40% of the query's IDF mass before it admits a QUERY on lexical evidence
        alone; a row of an already admitted query is not asked that (on Skyline's hard set
        it cut an expected memory sharing four terms of a ten-term question);
    and the best-ranked servable row is always served: it is the ranker's best answer, and a
    prompt the gate admitted is never emptied by this rule alone.

Why per row and not one margin per query: on a corpus with recall lanes the first rows are
behavior-lane rows, so "the top three" is not "the best three", and the absolute level of a
good cosine differs by corpus (emgl's off-topic p90 sits above its on-topic median). The
gap between a row's cosine and the query's best does not: on emgl's hard set the expected
memory sits a median 0.013 under the query's best cosine and other served rows 0.070.

Applies in the prompt hook only (``recall(lanes=...)``); explicit recall, the MCP tool and
the eval bench are unchanged. Off for a BM25-only index, a dense failure or an uncalibrated
embedding model, exactly like the corroboration gate: no cosine, no judgment, ``k`` rows.
``HIPPO_SIGNAL_COUNT=0`` turns it off; ``HIPPO_COUNT_BAND`` / ``HIPPO_COUNT_WIDE`` override
the two numbers. It never changes how anything ranks, only how many rows are served.

Stdlib only at import time: the hook imports this module on every prompt.
"""

from __future__ import annotations

import os
from typing import Dict, Iterable, List, Optional, Sequence, Set

from .recall_abstain import _AGREE_DEPTH, _STRONG_DESC_TERMS, _ranks

# Per embedding model, in that model's cosine space (like ``recall_abstain._COSINE_BY_MODEL``):
# ``band`` is how far under the query's best cosine a row may sit and still count as near it;
# ``wide`` is the margin over the dense floor at which the whole of ``k`` is served.
_COUNT_BY_MODEL: Dict[str, Dict[str, float]] = {
    "BAAI/bge-small-en-v1.5": {"band": 0.05, "wide": 0.18},
}
# The first servable rows in rank order are served whatever their evidence.
_LEAD_ROWS = 1
# Rows served when the best cosine does not clear the floor widely. Three (the roadmap's
# first number) cost emgl's hook-mode hard set 0.086 of recall@10 when simulated over its
# captured rows; five, measured on the replay, costs 0.010 (one row of 105) and holds the
# median at or under five on all three corpora.
_NARROW_ROWS = 5
REASON_CAP = "signal_cap"
REASON = "low_signal"


def enabled() -> bool:
    """False only for ``HIPPO_SIGNAL_COUNT`` set to 0/false/off/no."""
    raw = (os.environ.get("HIPPO_SIGNAL_COUNT") or "").strip().lower()
    return raw not in ("0", "false", "off", "no")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        val = float(raw)
    except ValueError:
        return default
    return val if val == val and val >= 0.0 else default


def thresholds(model: Optional[str]) -> Optional[Dict[str, float]]:
    """``{band, wide}`` for ``model`` with the env overrides applied; None when uncalibrated."""
    base = _COUNT_BY_MODEL.get(model or "")
    if base is None:
        return None
    return {
        "band": _env_float("HIPPO_COUNT_BAND", base["band"]),
        "wide": _env_float("HIPPO_COUNT_WIDE", base["wide"]),
    }


class Signals:
    """One query's per-entry evidence, read off values recall already computed."""

    __slots__ = ("top", "floor", "band", "wide", "_cos", "_dense_rank", "_lex_rank", "_desc_rank",
                 "_entries", "_qset")

    def __init__(self, top, floor, band, wide, cos, dense_rank, lex_rank, desc_rank, entries, qset):
        self.top = top
        self.floor = floor
        self.band = band
        self.wide = wide
        self._cos = cos
        self._dense_rank = dense_rank
        self._lex_rank = lex_rank
        self._desc_rank = desc_rank
        self._entries = entries
        self._qset = qset

    def _named_by_description(self, i: int) -> bool:
        if self._desc_rank.get(i, 10**9) > _AGREE_DEPTH:
            return False
        shared = self._qset.intersection(self._entries[i].get("tokens") or ())
        return len(shared) >= _STRONG_DESC_TERMS

    def row(self, i: int) -> Optional[dict]:
        """The ``signal`` dict recall attaches to entry ``i``'s result row."""
        cos = self._cos.get(i)
        if cos is None:
            return None
        wide = (self.top - self.floor) >= self.wide
        dr, lr = self._dense_rank.get(i), self._lex_rank.get(i)
        near = cos >= self.top - self.band
        both = dr is not None and lr is not None and dr <= _AGREE_DEPTH and lr <= _AGREE_DEPTH
        earned = bool(wide or near or both or self._named_by_description(i))
        out = {"cos": round(cos, 4), "gap": round(self.top - cos, 4), "earned": earned}
        if dr is not None:
            out["dense_rank"] = dr
        if lr is not None:
            out["lexical_rank"] = lr
        if wide:
            out["wide"] = True
        return out


def signals(
    q_terms: Sequence[str], index, sims, bm25_desc: Sequence[int], bm25_body: Sequence[int], floor: float
) -> Optional[Signals]:
    """Per-entry evidence for one query, or None when the count rule does not apply
    (switched off, no dense scores, an uncalibrated model). ``q_terms`` are the stemmed
    query terms, ``sims`` the raw cosine of every dense row, the two rankings the lexical
    lanes recall already computed. Never raises."""
    try:
        if sims is None or not enabled():
            return None
        th = thresholds(getattr(index, "model", None))
        if th is None:
            return None
        entries = index.entries
        n = len(entries)
        cos: Dict[int, float] = {}
        for i, e in enumerate(entries):
            row = e.get("row", i)
            if isinstance(row, int) and 0 <= row < len(sims):
                cos[i] = float(sims[row])
        for j, c in enumerate(index.body_chunks or []):
            p, row = c.get("entry"), c.get("row", n + j)
            if isinstance(p, int) and isinstance(row, int) and 0 <= row < len(sims):
                s = float(sims[row])
                if s > cos.get(p, -1.0):
                    cos[p] = s
        if not cos:
            return None
        order = sorted(cos, key=lambda i: -cos[i])
        desc_rank = _ranks(bm25_desc)
        lex = dict(desc_rank)
        for i, r in _ranks(bm25_body).items():
            if r < lex.get(i, 10**9):
                lex[i] = r
        return Signals(cos[order[0]], float(floor), th["band"], th["wide"], cos, _ranks(order), lex,
                       desc_rank, entries, set(q_terms or ()))
    except Exception:
        return None


def cut(rows: Iterable[dict]) -> Set[str]:
    """Names of the servable ``rows`` (recall order; the caller leaves out collapsed rows)
    that have no evidence of their own. A row without a ``signal`` dict (a rule pointer,
    any row when the rule is off) is never cut, and the first ``_LEAD_ROWS`` judged rows are
    served whatever their evidence. Pure."""
    judged: List[dict] = [r for r in rows if isinstance(r.get("signal"), dict)]
    return {
        r["name"]
        for pos, r in enumerate(judged)
        if pos >= _LEAD_ROWS and not r["signal"].get("earned") and r.get("name")
    }


def row_budget(rows: Iterable[dict], k: int) -> int:
    """How many rows this prompt may serve: ``k`` when the best cosine clears the floor
    widely (or nothing was judged), else ``min(k, _NARROW_ROWS)``. Pure."""
    judged = [r["signal"] for r in rows if isinstance(r.get("signal"), dict)]
    if not judged or any(s.get("wide") for s in judged):
        return int(k)
    return max(0, min(int(k), _NARROW_ROWS))
