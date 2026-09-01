"""TMB-3 (e): the evidence-only archive-regret detector (split from ``archive.py``
along its own section banner — the module-size ratchet's prescribed cut).

Recurring abstention clusters that an ARCHIVED body would have answered. Inert text +
a logged regret event; NO wiring to restore (that would be the demand-gap-auto-draft
shape round 1 killed). ``archive.py`` re-exports ``archive_regret`` so every existing
import path (doctor's lifecycle check, the CLI, tests) is unchanged.
"""

from __future__ import annotations

import os
from typing import List, Optional

_REGRET_MIN_OVERLAP = 2   # distinct query terms the archived body must share — one shared
                          # token is coincidence, not evidence
_REGRET_MAX_MATCHES = 5   # bounded evidence, never a worklist


def archive_regret(memory_dir: str, telemetry_dir: Optional[str] = None) -> List[dict]:
    """Recurring abstention clusters whose best BM25 match among ARCHIVED bodies overlaps
    on >= ``_REGRET_MIN_OVERLAP`` distinct terms — "you keep asking for something you
    archived". EVIDENCE ONLY: ``[{"query", "count", "stem", "overlap"}]`` for a human to
    judge; the restore verb exists separately and nothing here names, suggests, or
    invokes it. Runs at doctor time (cold) — never per-prompt. Vendored BM25
    (``_vendor.bm25.BM25Okapi``) over the archive listing; the journal is not consulted
    (reversibility metadata only). Read-only; never raises; ``[]`` on any failure or an
    absent/empty archive.
    """
    try:
        from ._vendor.bm25 import BM25Okapi
        from .archive import _ARCHIVE_SUBDIR
        from .build_index import bm25_terms, tokenize
        from .telemetry import abstention_backlog

        archive_dir = os.path.join(memory_dir, _ARCHIVE_SUBDIR)
        if not os.path.isdir(archive_dir):
            return []
        stems: List[str] = []
        docs: List[List[str]] = []
        for fn in sorted(os.listdir(archive_dir)):
            if not fn.endswith(".md"):
                continue
            try:
                with open(os.path.join(archive_dir, fn), "r", encoding="utf-8") as fh:
                    docs.append(bm25_terms(tokenize(fh.read())))
                stems.append(fn[:-3])
            except Exception:
                continue
        if not stems:
            return []
        clusters = abstention_backlog(telemetry_dir)
        if not clusters:
            return []
        bm25 = BM25Okapi(docs)
        term_sets = [set(d) for d in docs]
        out: List[dict] = []
        for c in clusters:
            q = (c.get("sample_query") or "").strip()
            qterms = list(dict.fromkeys(bm25_terms(tokenize(q))))
            if not q or not qterms:
                continue
            # The evidence GATE is distinct-term overlap; BM25 only RANKS among the docs
            # that clear it. (BM25 scores alone can't gate here: on a small archive the
            # IDF of a term present in most docs goes negative — the classic single-doc
            # pathology — so a sign test would silently blind the detector.)
            candidates = [
                i for i in range(len(stems))
                if len(set(qterms) & term_sets[i]) >= _REGRET_MIN_OVERLAP
            ]
            if not candidates:
                continue
            scores = bm25.get_scores(qterms)
            best = max(candidates, key=lambda i: (scores[i], -i))
            out.append(
                {
                    "query": q,
                    "count": int(c.get("count") or 0),
                    "stem": stems[best],
                    "overlap": len(set(qterms) & term_sets[best]),
                }
            )
            if len(out) >= _REGRET_MAX_MATCHES:
                break
        return out
    except Exception:
        return []


