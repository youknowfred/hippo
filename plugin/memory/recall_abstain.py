"""HOT-2: calibrated abstention — inject only when two independent lanes agree on a memory.

Before this, recall abstained only when EVERY lane came up empty: no dense row above the
cosine floor and no BM25 match at all. BM25 admits on one shared token, and on field corpora
off-topic probes clear the 0.60 cosine floor too (emgl's top off-topic cosines run 0.58–0.74,
inside the 0.52–0.83 range of real hits). So 0 of 11 off-topic fixtures abstained on hippo
and 0 of 20 on emgl. RET-11 showed that no lexical threshold separates the classes, and
neither does any absolute cosine.

What does separate them is CORROBORATION. A real query is about some memory, so that memory
ranks high in BOTH the dense lane and the lexical lane and shares more than one term with
the query. An off-topic probe's lexical hits are coincidences ("rule" in "the offside rule")
that the dense lane ranks elsewhere. The gate admits a query when:

  - its best memory's cosine is at least ``strong`` (dense alone is convincing), or
  - a description in the lexical top ``_AGREE_DEPTH`` shares at least ``_STRONG_DESC_TERMS``
    query terms carrying ``_STRONG_DESC_COVERAGE`` of the query's IDF mass, or
  - some memory sits in the top ``_AGREE_DEPTH`` of both lanes and either shares at least
    ``_MIN_SHARED_TERMS`` query terms or has a cosine of at least ``support``.

A memory that matches on a single shared term never admits a query on its own: it needs
dense support. Otherwise recall abstains, and the receipt names the closest miss.

Calibrated 2026-10-03 on the hippo, em-growth-labs and starter-pack fixtures: 8/11, 18/20
and 6/6 off-topic probes abstain, with 0 current hard-set hits lost on any of them. Checked
on held-out rows admitted afterwards (53 emgl, 25 Skyline): recall@10 moved -0.0095 on emgl
(one terse approval prompt) and 0.000 on Skyline. The
cosine thresholds belong to one embedding space, like ``recall_rank._DENSE_FLOOR_BY_MODEL``,
so an uncalibrated model (or a BM25-only index, or a dense failure) leaves the gate off:
abstention stays dense-gated, as RET-11 decided. ``HIPPO_DISABLE_ABSTAIN_GATE=1`` turns it
off. The gate only decides whether to inject, never how anything ranks.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence

# Both lanes' top N must share a memory. Calibrated across an 86- and a 1,033-memory corpus.
_AGREE_DEPTH = 10
# Shared query terms that make a corroborated memory enough. No coverage floor here: on the
# hippo, emgl, Skyline and pack fixtures one changed no off-topic outcome anywhere from 0.00
# to 0.10, and at 0.10 it cost a real Skyline hit (2 shared terms, coverage 0.099).
_MIN_SHARED_TERMS = 2
# A DESCRIPTION that shares this many query terms, carrying this much of the query's IDF
# mass, is lexical evidence enough on its own. Body chunks never qualify: a long body shares
# common words with almost anything (off-topic probes reached 6 terms / 0.83 coverage there).
_STRONG_DESC_TERMS = 3
_STRONG_DESC_COVERAGE = 0.40

# Cosine thresholds per embedding model: ``strong`` admits on dense alone, ``support``
# admits a corroborated memory that shares too few terms.
_COSINE_BY_MODEL = {
    "BAAI/bge-small-en-v1.5": {"strong": 0.76, "support": 0.72},
}


def gate_disabled() -> bool:
    return (os.environ.get("HIPPO_DISABLE_ABSTAIN_GATE") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _thresholds(model: Optional[str]) -> Optional[Dict[str, float]]:
    return _COSINE_BY_MODEL.get(model or "")


def _ranks(order: Sequence[int]) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for pos, i in enumerate(order):
        out.setdefault(i, pos + 1)
    return out


def corroboration(
    q_terms: List[str],
    index,
    sims,
    bm25_desc: Sequence[int],
    bm25_body: Sequence[int],
) -> Optional[dict]:
    """The gate's verdict for one query, or None when the gate does not apply.

    ``q_terms`` are the stemmed query terms; ``sims`` the raw cosine of every dense row
    (description rows, then body-chunk rows); ``bm25_desc`` / ``bm25_body`` the two lexical
    rankings recall already computed (entry indices, best first). Returns
    ``{"admit": bool, "why": str, "best": {...}}`` where ``best`` describes the strongest
    candidate (the near-miss when ``admit`` is False). Never raises.
    """
    try:
        if gate_disabled() or sims is None:
            return None
        th = _thresholds(getattr(index, "model", None))
        if th is None:
            return None
        entries = index.entries
        chunks = index.body_chunks or []
        n = len(entries)
        dense: Dict[int, float] = {}
        for i, e in enumerate(entries):
            row = e.get("row", i)
            if isinstance(row, int) and 0 <= row < len(sims):
                dense[i] = float(sims[row])
        for j, c in enumerate(chunks):
            p, row = c.get("entry"), c.get("row", n + j)
            if isinstance(p, int) and isinstance(row, int) and 0 <= row < len(sims):
                s = float(sims[row])
                if s > dense.get(p, -1.0):
                    dense[p] = s
        if not dense:
            return None
        dense_order = sorted(dense, key=lambda i: -dense[i])
        top = dense_order[0]
        if dense[top] >= th["strong"]:
            return {"admit": True, "why": "strong", "best": _desc(entries, top, dense[top])}

        stats = (getattr(index, "manifest", None) or {}).get("bm25") or {}
        idf = stats.get("idf") or {}
        max_idf = max(idf.values()) if idf else 1.0
        terms = list(dict.fromkeys(q_terms))
        mass = sum(idf.get(t, max_idf) for t in terms) or 1.0
        qset = set(terms)

        def _coverage(shared) -> float:
            return sum(idf.get(t, 0.0) for t in shared) / mass

        for i in list(bm25_desc)[:_AGREE_DEPTH]:
            shared = qset.intersection(entries[i].get("tokens") or ())
            if len(shared) >= _STRONG_DESC_TERMS and _coverage(shared) >= _STRONG_DESC_COVERAGE:
                return {
                    "admit": True,
                    "why": "lexical",
                    "best": _desc(entries, i, dense.get(i, -1.0), shared=len(shared),
                                  coverage=_coverage(shared)),
                }

        rd = _ranks(dense_order)
        rl = _ranks(bm25_desc)
        for i, r in _ranks(bm25_body).items():
            if r < rl.get(i, 10**9):
                rl[i] = r
        both = [i for i, r in rl.items() if r <= _AGREE_DEPTH and rd.get(i, 10**9) <= _AGREE_DEPTH]

        chunk_terms: Dict[int, List[set]] = {}
        want = set(both)
        for c in chunks:
            p = c.get("entry")
            if p in want:
                chunk_terms.setdefault(p, []).append(qset.intersection(c.get("tokens") or ()))

        best = None
        for i in sorted(both, key=lambda i: (max(rl[i], rd[i]), -dense[i])):
            shared = [qset.intersection(entries[i].get("tokens") or ())] + chunk_terms.get(i, [])
            m = max(shared, key=lambda s: (len(s), sum(idf.get(t, 0.0) for t in s)))
            cov = _coverage(m)
            cand = _desc(entries, i, dense[i], rd[i], rl[i], len(m), cov)
            if len(m) >= _MIN_SHARED_TERMS or dense[i] >= th["support"]:
                return {"admit": True, "why": "corroborated", "best": cand}
            if best is None:
                best = cand
        if best is None:
            best = _desc(entries, top, dense[top], rd[top], rl.get(top))
        return {"admit": False, "why": "uncorroborated", "best": best, "thresholds": dict(th)}
    except Exception:
        return None


def _desc(entries, i, cosine, dense_rank=None, lex_rank=None, shared=None, coverage=None) -> dict:
    out = {"name": entries[i].get("name"), "cosine": round(cosine, 4)}
    if dense_rank is not None:
        out["dense_rank"] = dense_rank
    if lex_rank is not None:
        out["lexical_rank"] = lex_rank
    if shared is not None:
        out["shared_terms"] = shared
    if coverage is not None:
        out["coverage"] = round(coverage, 3)
    return out


def receipt_line(verdict: dict) -> str:
    """The one-line near-miss receipt ``why`` and the ledger print for an abstention."""
    b = verdict.get("best") or {}
    th = verdict.get("thresholds") or {}
    bits = [f"best {b.get('name')!s} cosine {b.get('cosine')}"]
    if "dense_rank" in b:
        lex = b.get("lexical_rank")
        bits.append(f"dense rank {b['dense_rank']}, lexical rank {lex if lex else 'none'}")
    if "shared_terms" in b:
        bits.append(f"{b['shared_terms']} shared term(s)")
    floor = (
        f"< strong {th.get('strong')}; no memory in both lanes' top {_AGREE_DEPTH} with "
        f"≥{_MIN_SHARED_TERMS} shared terms or cosine ≥ {th.get('support')}"
    )
    return "abstained: " + ", ".join(bits) + " " + floor
