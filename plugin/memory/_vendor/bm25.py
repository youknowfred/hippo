"""Dependency-free Okapi BM25, API-compatible with ``rank_bm25.BM25Okapi``.

An original implementation of the standard Okapi BM25 formula (k1/b term-frequency
saturation + length normalization, with rank_bm25's epsilon-floor for negative IDF),
written for the bare-python3 pre-bootstrap path. Only the surface ``recall._bm25_rank``
uses is provided: ``BM25Okapi(corpus)`` + ``get_scores(query_tokens)``. Scores are
numerically identical to rank_bm25's (a test pins the parity), so pre- and
post-bootstrap BM25 rankings never differ.
"""

from __future__ import annotations

import math
from typing import Dict, List, Sequence


class BM25Okapi:
    def __init__(
        self,
        corpus: Sequence[Sequence[str]],
        k1: float = 1.5,
        b: float = 0.75,
        epsilon: float = 0.25,
    ):
        self.k1 = k1
        self.b = b
        self.epsilon = epsilon
        self.corpus_size = len(corpus)
        self.doc_len = [len(doc) for doc in corpus]
        self.avgdl = (sum(self.doc_len) / self.corpus_size) if self.corpus_size else 0.0

        # Per-doc term frequencies + corpus document frequencies, one pass.
        self.doc_freqs: List[Dict[str, int]] = []
        df: Dict[str, int] = {}
        for doc in corpus:
            freqs: Dict[str, int] = {}
            for tok in doc:
                freqs[tok] = freqs.get(tok, 0) + 1
            self.doc_freqs.append(freqs)
            for tok in freqs:
                df[tok] = df.get(tok, 0) + 1

        # Okapi IDF; terms in most/all docs go NEGATIVE and are floored to
        # epsilon * average_idf (rank_bm25's exact behavior — the recall layer's
        # token-overlap match filter depends on matched docs keeping finite scores).
        self.idf: Dict[str, float] = {}
        negative: List[str] = []
        idf_sum = 0.0
        for tok, freq in df.items():
            idf = math.log(self.corpus_size - freq + 0.5) - math.log(freq + 0.5)
            self.idf[tok] = idf
            idf_sum += idf
            if idf < 0:
                negative.append(tok)
        self.average_idf = idf_sum / len(self.idf) if self.idf else 0.0
        floor = self.epsilon * self.average_idf
        for tok in negative:
            self.idf[tok] = floor

    def get_scores(self, query: Sequence[str]) -> List[float]:
        scores = [0.0] * self.corpus_size
        if not self.corpus_size or not self.avgdl:
            return scores
        for q in query:
            idf = self.idf.get(q)
            if idf is None:
                continue
            for i, freqs in enumerate(self.doc_freqs):
                f = freqs.get(q, 0)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avgdl)
                scores[i] += idf * (f * (self.k1 + 1)) / denom
        return scores
