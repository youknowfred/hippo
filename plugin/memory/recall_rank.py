"""Recall's rankers: the PRF-1 BM25 postings fast path, RET-2 body-chunk backstops,
dense cosine under the RET-1 calibrated floor, RRF fusion, the RCL-5/RET-16
cross-encoder rerank, and the RCL-4 MMR diversity re-cut. Decomposed out of
``recall.py`` as pure code motion; every symbol stays importable at
``memory.recall.<name>`` via the façade's explicit re-exports."""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from .build_index import (
    DEFAULT_MODEL,
    DENSE_QUERY_TIMEOUT_SECS,
    LoadedIndex,
    bm25_terms,
    embed_query,
    run_bounded,
)

_RRF_K = 60


# --------------------------------------------------------------------------- #
# RET-1: relevance floor + knee cutoff — "earn every injected token"
# --------------------------------------------------------------------------- #
# The dense ranker (a nearest-neighbor search over cosine similarity) ALWAYS returns an
# ordering over the WHOLE corpus -- there is no such thing as "no match" in raw cosine
# space, only "less similar". Before this item, that ordering was trusted wholesale: an
# off-topic prompt on a 500-memory corpus still got its full top-k of "least dissimilar"
# junk. A calibrated FLOOR turns "ranked" into "ranked AND actually relevant" by dropping
# candidates whose cosine similarity to the query falls below a per-model threshold.
#
# Calibration method (recorded in the RET-1 commit body): embed the QUA-6 golden-corpus
# hard-set queries (on-topic, cross-vocabulary paraphrases) and a handful of CLEARLY
# off-topic probes (pizza dough hydration, quantum entanglement, celebrity gossip, ...)
# against the golden corpus with the REAL warm model, and look at where the two
# similarity distributions separate. For bge-small-en-v1.5: on-topic hits landed in
# [0.65, 0.85], off-topic probes topped out at 0.59 -- a floor of 0.60 sits just above the
# off-topic ceiling with margin below every measured on-topic hit (conservative: a false
# abstention costs a real recall miss, so the floor is set to the LOW side of the gap, not
# the midpoint). The multilingual preset (RET-3) is only given a "reasonable default" per
# the roadmap item's scope (not the full calibration sweep) -- mean-pooled MiniLM cosine
# similarities run on a visibly lower absolute scale (on-topic hits [0.32, 0.70], off-topic
# probes topping out at 0.28), so its floor is calibrated separately and is NOT the
# bge-small number.
_DENSE_FLOOR_BY_MODEL = {
    "BAAI/bge-small-en-v1.5": 0.60,
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": 0.30,
}
# Fallback for any OTHER model id (a future default bump, a user-supplied HIPPO_EMBED_MODEL
# not in the table above) -- conservative (low) rather than guessing a tight number for an
# uncalibrated embedding space; admits more than it should rather than risk false abstention.
_DENSE_FLOOR_DEFAULT = 0.50


def _dense_floor(model: Optional[str]) -> float:
    """Calibrated cosine floor for ``model`` — ``HIPPO_DENSE_FLOOR`` overrides everything.

    Lookup order: env override (any float, including 0 to disable the floor entirely) ->
    per-model table -> module-level default. Malformed env value degrades to "no override"
    (falls through to the table/default) rather than raising -- recall() must never break
    over a typo'd env var.
    """
    raw = os.environ.get("HIPPO_DENSE_FLOOR")
    if raw is not None and raw.strip():
        try:
            return float(raw)
        except ValueError:
            pass  # malformed -> fall through to the calibrated table/default
    return _DENSE_FLOOR_BY_MODEL.get(model or "", _DENSE_FLOOR_DEFAULT)


# --------------------------------------------------------------------------- #
# Rankers
# --------------------------------------------------------------------------- #
def _bm25_score_via_postings(
    query_tokens: List[str], stats: dict, matched: List[int]
) -> Dict[int, float]:
    """Score exactly ``matched`` doc indices from precomputed postings/idf/doc_len/avgdl.

    Replicates rank_bm25.BM25Okapi.get_scores' per-term formula EXACTLY:
        score[doc] += idf[tok] * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len[doc] / avgdl))
    summed over every query token whose postings list contains ``doc`` — but instead of
    walking the WHOLE corpus per query token (what BM25Okapi.get_scores does internally),
    this walks only the (token -> [[doc, tf], ...]) postings for the query's own tokens, and
    only for the docs already known (by the caller's token-overlap filter) to matter. Cost is
    proportional to the number of matched postings, independent of total corpus size N.
    """
    k1 = stats["k1"]
    b = stats["b"]
    avgdl = stats["avgdl"]
    idf = stats["idf"]
    postings = stats["postings"]
    doc_len = stats["doc_len"]
    matched_set = set(matched)
    scores: Dict[int, float] = {i: 0.0 for i in matched}
    if not avgdl:
        return scores
    for tok in query_tokens:
        term_idf = idf.get(tok)
        if term_idf is None:
            continue
        for doc_i, tf in postings.get(tok, ()):
            if doc_i not in matched_set:
                continue  # postings can reference docs outside this query's match set
            denom = tf + k1 * (1 - b + b * doc_len[doc_i] / avgdl)
            scores[doc_i] += term_idf * (tf * (k1 + 1)) / denom
    return scores


def _bm25_rank(
    query_tokens: List[str],
    entries: List[dict],
    *,
    stats: Optional[dict] = None,
    patched_indices: Optional[set] = None,
    doc_offset: int = 0,
) -> List[int]:
    """Indices of docs that SHARE >=1 query token, ordered by descending BM25 score.

    The match set (token-overlap) is the right filter — NOT ``score > 0``: BM25 IDF goes
    NEGATIVE for a term that appears in most/all docs (e.g. a tiny corpus, or a common
    token), so a genuinely-matching doc can score below an unrelated doc's 0. Filtering on
    overlap keeps matched docs (even negative-scored) and drops only the truly-unrelated.

    PRF-1: when the caller supplies ``stats`` (the manifest's precomputed postings/doc_len/
    avgdl/idf — see ``build_index.compute_bm25_stats``) AND no entry in ``patched_indices``
    (COR-4 mid-session drift — ``recall()`` tracks which indices got fresh tokens this query
    that the persisted postings do not know about) participates, this scores ONLY the
    matched docs by walking the query tokens' postings lists directly — cost proportional to
    matched postings, INDEPENDENT of corpus size, never constructing a BM25Okapi over the
    whole corpus. Falls back to the full from-scratch construction (today's behavior,
    unchanged) when ``stats`` is absent (an old manifest predating this item, or a caller —
    e.g. the two direct-call test sites — that doesn't have a manifest to draw stats from)
    or when any matched doc was drift-patched (its persisted postings are stale for THIS
    query's fresh tokens, so only a full rebuild over the CURRENT ``entries`` tokens is
    correct). Both paths produce IDENTICAL rankings — same match-set filter, same score
    formula, same stable descending sort — a golden test pins this equivalence.

    RET-2: ``doc_offset`` lets this same function rank the BODY-CHUNK doc list too. The
    manifest's persisted ``stats`` (``build_index.compute_bm25_stats``) is built over a
    UNIFIED doc space — description docs at indices ``0..N-1``, body-chunk docs APPENDED at
    ``N..`` — so when ``entries`` here is actually the flat ``body_chunks`` list (local
    indices ``0..len(body_chunks)-1``), ``doc_offset=N`` translates each local index to its
    real position in ``stats["postings"]``/``stats["doc_len"]`` before scoring, and translates
    the winning indices back to LOCAL ones before returning — callers of this function never
    need to know the offset trick happened. ``doc_offset=0`` (the default) is a no-op,
    preserving every existing call site's behavior byte-for-byte.
    """
    if not query_tokens or not entries:
        return []
    # RET-12: entries' persisted "tokens" are stemmed (build_index.bm25_terms); stem the
    # query side the same way so both halves of the match are in the same term-space. Doing
    # it here (rather than expecting every caller to pre-stem) covers _bm25_rank_body's
    # delegation and the direct-call test site uniformly.
    query_tokens = bm25_terms(query_tokens)
    corpus = [e.get("tokens") or [] for e in entries]
    qset = set(query_tokens)
    matched = [i for i in range(len(entries)) if qset.intersection(corpus[i])]
    if not matched:
        return []

    fast_path_ok = stats is not None and not (patched_indices and patched_indices & set(matched))
    if fast_path_ok:
        try:
            global_matched = [i + doc_offset for i in matched] if doc_offset else matched
            scores = _bm25_score_via_postings(query_tokens, stats, global_matched)
            order = sorted(range(len(matched)), key=lambda pos: scores[global_matched[pos]], reverse=True)
            return [matched[pos] for pos in order]
        except Exception:
            pass  # any stats-shape surprise -> fall through to the full-construction path

    try:
        try:
            from rank_bm25 import BM25Okapi  # the pinned venv dep (full-fidelity path)
        except ImportError:  # bare python3 pre-bootstrap (ONB-2): score-identical fallback
            from ._vendor.bm25 import BM25Okapi

        bm25 = BM25Okapi(corpus)
        scores = bm25.get_scores(query_tokens)
    except Exception:
        return []
    matched.sort(key=lambda i: scores[i], reverse=True)
    return matched


def _bm25_rank_body(
    query_tokens: List[str],
    index: LoadedIndex,
    *,
    patched_indices: Optional[set] = None,
    winning_chunk_out: Optional[Dict[int, int]] = None,
) -> List[int]:
    """BM25 ranking over BODY CHUNKS, mapped back to parent entry indices, deduped to each
    parent's best (lowest-rank) chunk hit -- the lexical half of the body backstop.

    RET-2: reuses ``_bm25_rank`` unmodified via ``doc_offset=len(index.entries)`` -- the
    manifest's persisted ``stats`` block is a UNIFIED doc space (descriptions at 0..N-1, body
    chunks appended at N..), so scoring the chunks needs the SAME offset translation the fast
    path already supports. ``patched_indices`` is passed straight through: COR-4 drift-patching
    is description-scoped (see ``_drift_patch``'s docstring) and never touches body-chunk
    entries here, but the parameter is threaded for signature symmetry with ``_bm25_rank`` and
    so a future body-drift patch (documented as healing at the next SessionStart rebuild, not
    mid-session) would have somewhere to plug in without another signature change.

    RCL-6: ``winning_chunk_out`` (caller-supplied dict, mutated in place, keyed by parent
    entry index -> the WINNING (best-ranked) body-chunk index ``j`` into ``index.body_chunks``)
    lets a caller recover WHICH chunk a body-win entry actually matched on, for the evidence
    snippet -- the return value alone (entry indices only) discards this on purpose (every
    other caller/ranking-list shape in this module is entry-index-only; adding it here as an
    optional out-param keeps that contract unchanged for everyone else).
    """
    body_chunks = index.body_chunks
    if not body_chunks or not query_tokens:
        return []
    n_entries = len(index.entries)
    local = _bm25_rank(
        query_tokens,
        body_chunks,
        stats=index.manifest.get("bm25"),
        patched_indices=patched_indices,
        doc_offset=n_entries,
    )
    seen: set = set()
    out: List[int] = []
    for j in local:
        parent = body_chunks[j].get("entry")
        if parent is None or parent in seen:
            continue
        seen.add(parent)
        out.append(parent)
        if winning_chunk_out is not None:
            winning_chunk_out[parent] = j
    return out


# MSR-4: per-mechanism cap on drop-record capture — the ledger row stays small (the
# recall ledger is byte-rotated) while the nearest misses per mechanism survive. A
# drop_log's ``watch`` set (the eval autopsy's expected stems) bypasses the cap so
# attribution is exact where it matters; the hook never passes a watch.
_DROP_CAP_PER_MECHANISM = 3


def _dense_rank_rows(
    query: str,
    index: LoadedIndex,
    *,
    subfloor_out: Optional[List] = None,
    watch_rows: Optional[set] = None,
) -> List[int]:
    """RAW dense-matrix row indices ordered by descending cosine similarity, ABOVE the
    calibrated floor for ``index.model``, or [].

    RET-2: the matrix is WIDENED (description rows ``0..N-1`` then body-chunk rows ``N..``),
    so this returns ROW indices over that whole matrix — callers split the result into a
    description ranking and a body-chunk ranking (see ``_dense_rank``/``_dense_rank_body``)
    rather than this function knowing anything about the entries/chunks split itself.

    RET-1: rows scoring below ``_dense_floor(index.model)`` are dropped HERE, before either
    caller ever sees them — the dense ranker otherwise always returns a total ordering over
    the whole corpus (cosine similarity has no notion of "no match", only "less similar"),
    so an off-topic query used to admit the entire corpus at full k regardless of relevance.
    Filtering at the ROW level (rather than in each caller) means the floor applies
    identically to description rows AND body-chunk rows with one calibrated number, and a
    caller that wants pre-floor scores (none currently do) would need a separate entry
    point — this function's contract is now "ranked AND relevant", not just "ranked".

    Loads the embedding model OFFLINE (no download); any failure -> [] (BM25 carries it).

    COR-8 model cross-check: a manifest embedded under model X, cosine-scored against a
    query embedded under model Y (the CURRENTLY configured ``build_index.DEFAULT_MODEL``),
    is comparing vectors from two different embedding spaces -- the resulting "similarity"
    is not meaningful, just noise that happens to look like a score. This can happen after
    ``HIPPO_EMBED_MODEL`` changes (or a stale index survives a plugin update that bumps the
    default) without a full rebuild. ``index.model`` is ``None`` for a BM25-only manifest
    (never built dense, or dense_ready False) -- that is NOT a mismatch, just "no dense model
    recorded yet", so only an EXPLICIT, DIFFERENT model name skips dense. The mismatch is
    made doctor-visible via ``build_index.check_index_integrity`` (a NEW corruption-adjacent
    case alongside the truncated-manifest / missing-dense / wrong-shape ones it already
    names) so "recall silently got worse after a model change" has a diagnosis surface.
    """
    if not index.dense_ready or index.dense is None:
        return []
    if index.model and index.model != DEFAULT_MODEL:
        return []
    try:
        import numpy as np

        # Wall-clock-bounded: a cold/wiped model cache aborts to BM25 instead of blocking.
        qvec = run_bounded(
            lambda: embed_query(query, allow_download=False),  # offline-guarded in build_index
            DENSE_QUERY_TIMEOUT_SECS,
        )
        sims = index.dense @ qvec  # rows are L2-normalized -> dot == cosine
        order = np.argsort(-sims)
        floor = _dense_floor(index.model or DEFAULT_MODEL)
        # MSR-4: when a caller passes ``subfloor_out``, the SAME descending walk also
        # keeps the best few sub-floor ``(description_row, cosine)`` pairs — plus every
        # ``watch_rows`` member — off values already computed (zero recomputation).
        # These are the near-miss scores the floor cut used to discard: "how close was
        # the miss", the evidence RET-11's BM25-floor decision and the SIG-5 revisit
        # never had. Body-chunk rows are skipped (a sub-floor chunk is not "this memory
        # nearly surfaced"). ``None`` (every existing caller) changes nothing.
        above: List[int] = []
        n_desc = len(index.entries)
        below_kept = 0
        for i in order:
            sim = float(sims[i])
            if sim >= floor:
                above.append(int(i))
                continue
            if subfloor_out is None:
                break  # order is descending: every remaining row is sub-floor too
            row = int(i)
            if row >= n_desc:
                continue  # body-chunk rows aren't per-memory near-misses
            if below_kept < _DROP_CAP_PER_MECHANISM or (watch_rows and row in watch_rows):
                subfloor_out.append((row, sim))
                below_kept += 1
            elif not watch_rows:
                break  # cap reached and nothing watched: nearer misses are all recorded
        return above
    except Exception:  # incl. DenseTimeout -> degrade to BM25, never block/crash
        return []


def _dense_rank(query: str, index: LoadedIndex, *, raw_rows: Optional[List[int]] = None) -> List[int]:
    """Entry indices (description rows only) ordered by descending cosine similarity.

    RET-2: the raw dense matrix now also carries body-chunk rows at ``n_entries..`` (see
    ``_dense_rank_rows``); this filters the raw row order down to just the description rows
    (``row < n_entries``) so every EXISTING caller of ``_dense_rank`` (golden tests, the
    description-ranking half of fusion) keeps seeing entry-index-only results, unchanged.

    ``raw_rows``: an ALREADY-computed ``_dense_rank_rows(query, index)`` result, so a caller
    that also needs ``_dense_rank_body`` (i.e. ``recall()``) embeds the query and does the
    ``dense @ qvec`` matmul exactly ONCE per call, not once per ranking -- with the widened
    matrix this halves the dense query cost every recall() call. ``None`` (every direct/test
    caller of ``_dense_rank`` before this item) computes it fresh, unchanged behavior.
    """
    n_entries = len(index.entries)
    rows = raw_rows if raw_rows is not None else _dense_rank_rows(query, index)
    return [row for row in rows if row < n_entries]


def _dense_rank_body(
    query: str,
    index: LoadedIndex,
    *,
    raw_rows: Optional[List[int]] = None,
    winning_chunk_out: Optional[Dict[int, int]] = None,
) -> List[int]:
    """Body-CHUNK ranking, MAPPED BACK to parent entry indices, deduped to each parent's
    BEST (lowest-rank) chunk hit — a backstop ranking, never a second vote per memory.

    RET-2: filters the raw dense row order (see ``_dense_rank_rows``) to rows ``>= n_entries``
    (body-chunk rows), maps each surviving row back to its ``body_chunks[j]["entry"]`` parent,
    and keeps only the FIRST (best-ranked) occurrence of each parent entry index — a memory
    with 3 chunks that all rank well must not get 3 votes in the fusion below; it gets exactly
    one, at its best chunk's rank. Returns entry indices, in the same shape ``_dense_rank``
    does, so ``recall()`` can pass it straight into ``_rrf_fuse`` alongside the other rankings.

    ``raw_rows``: see ``_dense_rank``'s docstring -- shares the SAME single embed+matmul
    ``recall()`` already paid for the description ranking, instead of re-embedding the query
    a second time for the body ranking.

    RCL-6: ``winning_chunk_out`` -- see ``_bm25_rank_body``'s docstring; same contract, same
    caller-supplied out-param convention.
    """
    n_entries = len(index.entries)
    body_chunks = index.body_chunks
    if not body_chunks:
        return []
    raw = raw_rows if raw_rows is not None else _dense_rank_rows(query, index)
    seen: set = set()
    out: List[int] = []
    for row in raw:
        if row < n_entries:
            continue
        j = row - n_entries
        if j < 0 or j >= len(body_chunks):
            continue
        parent = body_chunks[j].get("entry")
        if parent is None or parent in seen:
            continue
        seen.add(parent)
        out.append(parent)
        if winning_chunk_out is not None:
            winning_chunk_out[parent] = j
    return out


def _rrf_fuse(
    rankings: List[List[int]], k: int = _RRF_K, *, weights: Optional[List[float]] = None
) -> List[Tuple[int, float]]:
    """Reciprocal Rank Fusion of several rank lists -> ``[(index, fused_score), ...]``, best first.

    Returns the SCORE alongside the index (not just an ordering) so a caller can apply a
    post-fusion, pre-cut soft-invalidation penalty that can actually change which indices
    survive into the top-k — not just relabel them after the cut already happened. The sort
    key (score alone) is UNCHANGED from before this widening — deliberately no explicit
    tie-break was added here: an adversarial review of an earlier draft that DID add one
    (``(-score, index)``) found it silently flips top-k SET MEMBERSHIP on real corpus ties,
    independent of any invalidation penalty — an unrelated behavior change this widening must
    not smuggle in. This function has exactly one caller (``recall()``).

    RET-2: ``weights`` (one float per ranking in ``rankings``, same order/length; ``None`` ->
    every ranking weighted 1.0, IDENTICAL to the pre-RET-2 formula) lets description rankings
    stay at full RRF weight (1.0) while body-derived rankings (bm25_body/dense_body) enter at
    ``_BODY_RRF_WEIGHT`` -- the per-term formula becomes ``score += w / (k + rank + 1)`` instead
    of the unweighted ``1 / (k + rank + 1)``. A caller passing no ``weights`` (every existing
    call site prior to this item) gets byte-identical scores to before.
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    scores: dict = {}
    for ranking, w in zip(rankings, weights):
        for rank, idx in enumerate(ranking):
            scores[idx] = scores.get(idx, 0.0) + w / (k + rank + 1)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


# --------------------------------------------------------------------------- #
# RCL-5/RET-16: cross-encoder rerank. RCL-5 shipped this model (Xenova/ms-marco-MiniLM-L-6-
# v2, offline ONNX via fastembed, build_index._get_cross_encoder) for /hippo:recall and the
# MCP tool only — an explicit surface with "no p95 budget to protect" (unconditional there,
# no flag). RET-16 (owner-directed) extends it to THIS module's hot path (UserPromptSubmit),
# which DOES have a protected p95 (eval_recall.GATE_P95_MS, gating the report's recall_p95_ms
# key) — so here it's gated OFF by default
# (HIPPO_RERANK=1 opts in) and BOUNDED under its own timeout via run_bounded, the same
# pattern the dense query call already uses: a cold model load or slow rerank degrades to
# the pre-rerank fused order, never blocks the hook past its budget. Both callers share ONE
# function (previously duplicated in recall_view.py; that module now imports it from here)
# so the reordering rule can never fork between the two surfaces — recall_view's caller
# NEWLY inherits this bound too (it previously called the model with no timeout at all); a
# generous bound is a strict improvement there (bounded beats unbounded even with "no p95 to
# protect"), never a regression, as long as it doesn't starve a genuine cold load.
#
# IMPORTANT COST NOTE for anyone setting HIPPO_RERANK=1: the hook is a fresh subprocess
# per prompt (no warm in-process model cache survives between prompts the way a long-lived
# server would keep one) — so this is NOT a rare/tail cost. Expect the cross-encoder's
# model-load-plus-inference cost on EVERY prompt this flag is on for, in the same latency
# class as the dense embedding model's own cold load (bench's cold_p95_ms). Opt in only if
# that per-prompt tax is acceptable; it is not free the way the other 4 items' gated priors
# (pure arithmetic over an already-loaded cache) are.
_RERANK_TIMEOUT_SECS = 5.0  # override: HIPPO_RERANK_TIMEOUT -- matches DENSE_QUERY_TIMEOUT_SECS,
# the same "small offline ONNX model, cold-load-dominated" cost class; 2s left too little
# headroom for a legitimate cold load to complete and was timing out productive work, not
# just runaway calls.


def _rerank_enabled() -> bool:
    """True only when ``HIPPO_RERANK`` is explicitly truthy — DEFAULT OFF, same falsy-set
    convention as ``_salience_enabled()``/``_outcome_prior_enabled()``."""
    raw = os.environ.get("HIPPO_RERANK", "").strip()
    return raw not in ("", "0", "false", "False")


def _rerank_timeout_secs() -> float:
    """``HIPPO_RERANK_TIMEOUT`` override; malformed/absent -> the module default. Never raises."""
    raw = os.environ.get("HIPPO_RERANK_TIMEOUT")
    if raw is None or not raw.strip():
        return _RERANK_TIMEOUT_SECS
    try:
        return float(raw)
    except ValueError:
        return _RERANK_TIMEOUT_SECS


def _cross_encoder_rerank(query: str, hits: List[dict]) -> List[dict]:
    """Re-order ``hits`` by a local cross-encoder's joint query/description read.

    ``corpus == "rule"`` pointers are excluded from the rerank and re-attached at the tail
    in their ORIGINAL relative order — a rule pointer has no query-vs-description joint
    signal to rerank on and must never be reordered among corpus hits. Reorders ONLY; never
    mutates a hit's own ``score``/``rank`` (COR-8: those stay the true fused-recall values,
    not a fabricated cross-encoder number on a different scale) — every consumer (this
    module, ``recall_view``'s renderer, ``format_results``) walks list order for display and
    never reads ``rank`` to number results, so reordering the list alone is sufficient.

    BOUNDED (RET-16): the model call runs under ``run_bounded``/``_rerank_timeout_secs()``,
    the same pattern ``embed_query`` already uses, so a cold model load can never block a
    caller past its own timeout budget. Degrades to the ORIGINAL order on ANY failure — no
    cached model, fastembed unavailable, timeout, any exception — never downloads, never
    raises.
    """
    rule_hits = [h for h in hits if h.get("corpus") == "rule"]
    corpus_hits = [h for h in hits if h.get("corpus") != "rule"]
    if len(corpus_hits) < 2:
        return hits  # nothing meaningful to reorder
    try:
        from .build_index import _get_cross_encoder

        def _rerank_call():
            model = _get_cross_encoder(allow_download=False)
            descriptions = [h.get("description") or "" for h in corpus_hits]
            return list(model.rerank(query, descriptions))

        scores = run_bounded(_rerank_call, _rerank_timeout_secs())
        order = sorted(range(len(corpus_hits)), key=lambda i: scores[i], reverse=True)
        return [corpus_hits[i] for i in order] + rule_hits
    except Exception:
        return hits


# --------------------------------------------------------------------------- #
# RCL-4: MMR intra-block diversity re-rank
# --------------------------------------------------------------------------- #
# Nothing collapses near-DUPLICATE hits within the injected block -- two memories
# paraphrasing one decision each eat a top-k slot that could have gone to a distinct facet.
# Classic maximal-marginal-relevance re-cut over the top ~2k of `penalized`, using cheap
# pairwise cosine from the dense matrix already resident in memory (idx.dense rows are
# L2-normalized, so a dot product IS cosine -- no re-embedding, pure arithmetic, inv6-safe).
# Runs AFTER graph expansion + salience (both already reordered `penalized`) and BEFORE the
# emission loop, so graph neighbors are diversified too and the knee cutoff (which lives
# inside the emission loop) measures gaps on the FINAL, diversified order.
_MMR_LAMBDA = 0.8  # override: HIPPO_MMR_LAMBDA -- relevance weight (1-lambda is diversity weight).
# Calibrated on the shipped starter-pack golden corpus (see the RCL-4 commit body): the
# roadmap's suggested ~0.7 measurably eroded hard_recall@10/MRR (still above the absolute
# gates, but a real, avoidable cost) by letting a WEAK diversity pick occasionally outrank a
# genuinely-relevant close second; 0.8 recovers nearly all of that margin while an adversarial
# near-paraphrase fixture (two memories on one decision) still gets diversified.
_MMR_POOL_MULT = 2  # candidates considered for the re-cut, as a multiple of k


def _mmr_lambda() -> float:
    """``HIPPO_MMR_LAMBDA`` override; malformed/absent -> the module default. Never raises."""
    raw = os.environ.get("HIPPO_MMR_LAMBDA")
    if raw is None or not raw.strip():
        return _MMR_LAMBDA
    try:
        return float(raw)
    except ValueError:
        return _MMR_LAMBDA


def _mmr_rerank(
    penalized: List[Tuple[int, float, Optional[str]]],
    entries: List[dict],
    dense,
    k: int,
    *,
    endorsed: Optional[set] = None,
) -> List[Tuple[int, float, Optional[str]]]:
    """Re-cut the top ``~2k`` of ``penalized`` for intra-block diversity, preserving length.

    A candidate with NO usable dense row -- ``dense`` itself absent (BM25-only corpus), or
    ``entries[i]["row"]`` missing/out of range (a model-mismatch merge, a graph-injected
    neighbor never itself embedded) -- KEEPS ITS ORIGINAL POSITION and is exempt from the
    diversity math entirely, the same exemption posture the knee cutoff already uses for
    entries with no primary-relevance signal. Never touches corpus="rule" pointers -- those
    are appended after emission, hold no dense row, and are never part of ``penalized``.
    Degrades to the untouched input (never raises, never drops/reorders-wrong on failure).

    GRF-2: ``endorsed`` (entry indices from ``_expand_neighbors``' graph-endorsed set)
    get the SAME original-slot exemption. A wikilink neighbor is definitionally similar
    to the seed that endorsed it, so the diversity penalty is structurally biased against
    exactly the entries the graph vouches for -- on the mixed/degraded path (dense index
    resident, bm25 ranking at query time) this scored the multi-hop category 0.0, and on
    the production dense path it displaced organically-admitted cluster members (the T9
    re-measure). This is the symmetric twin of the knee's ``graph_endorsed`` exemption
    (4d16022): when a human-authored edge and an embedding-similarity heuristic disagree
    about whether two memories belong together, the edge wins. Endorsement is the ONLY
    exemption channel -- an unlinked near-paraphrase is still demoted (the
    diversification guarantee is intact; a no-link tail can never ride in on this).
    """
    if dense is None or len(penalized) < 2:
        return penalized
    try:
        import numpy as np

        pool_n = min(len(penalized), max(k * _MMR_POOL_MULT, k))
        pool = penalized[:pool_n]
        tail = penalized[pool_n:]

        eligible: List[Tuple[int, int, float, Optional[str]]] = []  # (pool_pos, i, score, state)
        exempt_positions: Dict[int, Tuple[int, float, Optional[str]]] = {}
        for pos, (i, score, state) in enumerate(pool):
            row = entries[i].get("row")
            if (endorsed and i in endorsed) or row is None or row < 0 or row >= len(dense):
                exempt_positions[pos] = (i, score, state)
            else:
                eligible.append((pos, i, score, row, state))

        if len(eligible) < 2:
            return penalized  # nothing left to diversify against

        rows = np.array([e[3] for e in eligible])
        sims = dense[rows] @ dense[rows].T  # rows are L2-normalized -> dot == cosine
        lam = _mmr_lambda()

        remaining = list(range(len(eligible)))
        max_sim_to_picked = np.zeros(len(eligible))
        picked_order: List[int] = []
        while remaining:
            best_local, best_val = None, None
            for local_i in remaining:
                relevance = eligible[local_i][2]
                mmr_score = lam * relevance - (1.0 - lam) * max_sim_to_picked[local_i]
                if best_val is None or mmr_score > best_val:
                    best_val, best_local = mmr_score, local_i
            picked_order.append(best_local)
            remaining.remove(best_local)
            for local_i in remaining:
                s = float(sims[local_i, best_local])
                if s > max_sim_to_picked[local_i]:
                    max_sim_to_picked[local_i] = s

        # Exempt entries keep their ORIGINAL slot; MMR-ordered eligible entries fill the
        # remaining slots in the order MMR picked them -- a pure re-order, same pool length.
        out: List[Optional[Tuple[int, float, Optional[str]]]] = [None] * len(pool)
        for pos, triple in exempt_positions.items():
            out[pos] = triple
        empty_slots = [p for p in range(len(pool)) if out[p] is None]
        for slot, local_i in zip(empty_slots, picked_order):
            _, i, score, _row, state = eligible[local_i]
            out[slot] = (i, score, state)

        return out + tail
    except Exception:
        return penalized
