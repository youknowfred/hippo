"""Query-time recall over the agent-memory index (Tier 2 of the activation roadmap).

Given a natural-language query, return the top-K most relevant memories by FUSING:
  - DENSE cosine similarity over ``bge-small`` embeddings (when the index + cached model
    are available), and
  - BM25 lexical scores (always available — ``rank-bm25`` is a repo dep),
combined with Reciprocal Rank Fusion (RRF).

Robustness contract (the UserPromptSubmit hook depends on this):
  - NEVER raises — every failure degrades to BM25-only, then to empty.
  - NEVER triggers a synchronous model download — the dense model is loaded OFFLINE from
    the cache ``build_index.py`` warmed; a cache miss falls back to BM25.
  - Output is bounded below the harness's 10,000-char cap.

Also hosts the SessionStart ``git-recent`` producer (recently-captured memories), which
reuses Tier 1's ``source_commit`` provenance — registered into ``session_start.py``.
"""

from __future__ import annotations

import os
import re
import time
from typing import Dict, List, Optional, Tuple

from .build_index import (
    DEFAULT_MODEL,
    DENSE_QUERY_TIMEOUT_SECS,
    LoadedIndex,
    _hash,
    build_index,
    default_index_dir,
    embed_query,
    entry_description,
    load_index,
    memory_doc_text,
    run_bounded,
    tokenize,
)
from . import trust
from .lint_floor import floor_memory_names
from .provenance import _iter_memory_files, resolve_dirs
from .staleness import _commit_times, read_provenance

# Harness caps hook output at 10,000 chars; stay well under it.
_MAX_RECALL_CHARS = 9000
_RRF_K = 60
DEFAULT_K = 10

# RET-2: body-chunk rankings (bm25_body / dense_body) enter fusion as a BACKSTOP, not a peer
# of the description rankings -- a memory whose crucial fact lives only in its body should be
# findABLE, but a description-vocabulary hit is still the stronger, more deliberate signal (the
# author chose those words to BE the recall surface). Weighting body rankings down (rather than
# giving them full RRF weight) keeps description rows primary and prevents a corpus of long,
# keyword-dense bodies from systematically outranking well-written descriptions purely on body
# volume. Env-overridable (not a hook env var in the MEMOBOT_ prefix sense of "per-invocation
# tuning" -- this is a corpus-wide ranking knob an operator might calibrate via /hippo:audit).
_BODY_RRF_WEIGHT = 0.5


def _body_rrf_weight() -> float:
    """``MEMOBOT_BODY_RRF_WEIGHT`` override; malformed/absent -> the module default. Never raises."""
    raw = os.environ.get("MEMOBOT_BODY_RRF_WEIGHT")
    if raw is None or not raw.strip():
        return _BODY_RRF_WEIGHT
    try:
        return float(raw)
    except ValueError:
        return _BODY_RRF_WEIGHT

# Soft-invalidation (Tier 3, graceful decay) — "recent" halves the fused score BEFORE the
# top-k cut (real demotion, can fall out of top-k); "old" is filtered from DISPLAY only,
# after the cut (the memory stays fully in the corpus/index, never excluded from ranking).
_INVALIDATION_PENALTY = 0.5
_INVALIDATION_RECENT_DAYS = 30.0

# Mid-session drift (COR-4) — a stat+reread per entry is cheap, but bound it so a huge
# corpus can never turn the hot path into an O(corpus) disk scan of unbounded size.
_MAX_DRIFT_CHECKS = 200

# 1-hop graph expansion (GRA-1) — the first load-bearing graph READ. After fusion +
# invalidation penalties, the top-_GRAPH_SEEDS entries seed a 1-hop neighbor pull from the
# persisted edge list (GRA-6's links.json, one small-JSON read, no corpus scan). Neighbors
# are injected at _NEIGHBOR_DISCOUNT x their best seed's penalized score and COMPETE for
# top-k — no reserved slots, so expansion can only surface a linked memory when its
# discounted score actually beats an organic candidate, never by displacing one for free.
_GRAPH_SEEDS = 3  # override: MEMOBOT_GRAPH_SEEDS (0 disables expansion entirely)
_NEIGHBOR_DISCOUNT = 0.5

# --------------------------------------------------------------------------- #
# Query hygiene
# --------------------------------------------------------------------------- #
# The UserPromptSubmit hook feeds the prompt VERBATIM. In practice a large fraction of prompts
# are harness envelopes (<task-notification> tool-use blobs) or near-empty continuations
# ("?", "continue") that carry no retrieval intent — embedding them wastes a ~400ms cold model
# load to inject pure semantic noise. clean_query() strips the envelopes and returns "" to SKIP
# recall entirely when nothing of substance remains (the hook then injects no context).
_ENVELOPE_BLOCK_RE = re.compile(
    r"<(task-notification|system-reminder|local-command-stdout)\b[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
_FENCE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)

# RET-4: tag stripping is scoped to KNOWN harness tag names only. Previously _TAG_RE matched
# ANY angle-bracketed span, which silently ate `<lambda>`, `Vec<String>`, `<module>` — exactly
# the symbol-shaped tokens debugging prompts are made of. Restricting the deletion to the
# harness's own envelope/wrapper tags means an unknown tag (a Python repr, a generic type, a
# stray HTML-ish fragment a user pasted) is LEFT IN PLACE; angle brackets tokenize harmlessly
# (they're not word chars) so the identifier text inside/around them still reaches BM25/dense.
_KNOWN_HARNESS_TAGS = (
    "task-notification",
    "system-reminder",
    "local-command-stdout",
    "local-command-caveat",
    "command-name",
    "command-message",
    "command-args",
)
_TAG_RE = re.compile(
    r"</?(?:" + "|".join(_KNOWN_HARNESS_TAGS) + r")\b[^>]*/?>",
    re.IGNORECASE,
)
_MIN_CONTENT_TOKENS = 2
# Terse continuation/filler prompts that carry no retrieval intent (matched normalized+lowered).
_CONTINUATION_PHRASES = frozenset(
    {
        "continue", "pls continue", "please continue", "go on", "keep going", "next",
        "proceed", "lets proceed", "let's proceed", "go ahead", "yes pls", "yes please",
        "option 1", "option 2", "option 3", "drop it", "ok", "okay", "yes", "no", "y", "n",
        "thanks", "ty", "stop",
    }
)

# --------------------------------------------------------------------------- #
# RET-4: fence/traceback mining
# --------------------------------------------------------------------------- #
# clean_query used to DELETE fenced code blocks outright (_FENCE_BLOCK_RE.sub(" ", ...)) —
# throwing away the error class, symbol names, and file paths that are the strongest lexical
# signal in exactly the debugging prompts where recall matters most ("why does
# `_BG_TASKS.discard` never fire" is far more retrievable than the sentence around it once the
# identifier survives). Instead of deleting fence contents, MINE them: pull identifier-like
# tokens out, rank by a cheap rarity proxy, and APPEND the top few to the cleaned text so they
# still reach tokenize()/BM25/dense. clean_query has no index access (it must stay pure/total,
# no I/O), so true corpus-frequency rarity is unavailable — "longer / multi-part (underscored,
# dotted, camelCase) / UPPER_CASE" is the cheap proxy: those shapes are overwhelmingly
# project-specific identifiers, while short bare words are usually keywords/builtins that BM25
# already handles fine from the surrounding prose.
_IDENTIFIER_RE = re.compile(
    r"""
    [A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+   # dotted path: module.attr, a.b.c
    | [A-Za-z0-9_]+/[A-Za-z0-9_./-]*[A-Za-z0-9_]           # path-like: dir/file.py, a/b/c
    | [A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+               # snake_case (>=1 underscore)
    | [a-z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]*                   # camelCase / PascalCase-ish
    | [A-Z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]*                   # ErrorClass-style (CapWords, e.g. IOError)
    | [A-Z][A-Z0-9_]{2,}                                    # UPPER_CASE constants (>=3 chars)
    """,
    re.VERBOSE,
)
# Traceback lines carry identifier signal even OUTSIDE a fence (a pasted stack trace often
# isn't triple-backtick'd). Both are cheap, total (no backtracking blowup — bounded charsets,
# anchored per-line) regexes: `File "path", line N` and a trailing `SomeError: message`.
_TRACEBACK_FILE_RE = re.compile(r'File\s+"([^"]+)",\s*line\s+(\d+)')
_TRACEBACK_ERROR_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception|Warning))\b\s*:")
_MAX_MINED_TOKENS = 8  # module constant per the roadmap spec — cap on mined tokens appended


def _mine_identifiers(text: str) -> List[str]:
    """Extract+rank identifier-like tokens from ``text`` (fence contents or raw traceback lines).

    Rarity proxy (clean_query is pure — no index/corpus access to compute real IDF): rank by
    (is multi-part [underscore/dot/slash] first, then length desc) — longer, structured tokens
    are overwhelmingly project-specific identifiers (error classes, symbol names, file paths),
    while short undecorated words are usually common tokens BM25 already scores fine from the
    surrounding prose. Dedupes while preserving first-seen order among equal-ranked tokens
    (stable sort). Never raises (caller wraps); pure text in, list out.
    """
    found = _IDENTIFIER_RE.findall(text)
    # File "path", line N -> the path is exactly as strong a signal as a fenced identifier.
    for path, _line in _TRACEBACK_FILE_RE.findall(text):
        found.append(path)
    found.extend(_TRACEBACK_ERROR_RE.findall(text))

    seen: dict = {}  # token -> first-seen index (for stable ordering among equal rank)
    for i, tok in enumerate(found):
        if tok not in seen:
            seen[tok] = i

    def _is_multipart(tok: str) -> bool:
        return ("_" in tok) or ("." in tok) or ("/" in tok)

    ranked = sorted(
        seen.keys(),
        key=lambda tok: (not _is_multipart(tok), -len(tok), seen[tok]),
    )
    return ranked[:_MAX_MINED_TOKENS]


def clean_query(raw: str) -> str:
    """Normalize a raw prompt into a recall query, or "" to SKIP recall (no model load).

    Strips harness envelopes (``<task-notification>`` / ``<system-reminder>`` tool-use blobs)
    and KNOWN harness wrapper tags, MINES identifier-like tokens out of fenced code blocks and
    traceback lines (rather than deleting them — RET-4), and returns "" when what remains
    carries no retrieval intent (a terse continuation like "?"/"continue", or fewer than
    ``_MIN_CONTENT_TOKENS`` content tokens). Pure; never raises — any failure degrades to the
    raw prompt (recall on the un-cleaned text rather than skip).

    RET-3: the min-content gate below calls the SAME ``tokenize()`` used for BM25/dense, which
    is now Unicode-aware (word tokens for Latin/Cyrillic/etc., character bigrams for CJK runs
    lacking whitespace segmentation — see ``build_index.tokenize``). That makes this gate
    effectively grapheme-aware for free: a substantive Japanese/Russian prompt tokenizes to
    >=2 real content tokens (never "0 tokens" the way the old ASCII-only tokenizer produced for
    non-Latin text) and clears the gate exactly like an equivalent English prompt would. The
    one deliberately-unchanged edge case: a 1-2 character CJK prompt yields only 0-1 bigram
    tokens and DOES trip the skip — the same treatment a single English word gets today (not a
    regression, just applying the existing "too terse to carry retrieval intent" rule uniformly
    across scripts; a 3+ character CJK prompt already yields >=2 bigrams and passes).
    """
    try:
        if not raw or not raw.strip():
            return ""
        text = _ENVELOPE_BLOCK_RE.sub(" ", raw)

        # Mine identifiers from fenced blocks AND raw traceback lines BEFORE the fences are
        # removed from the running text — mining reads the ORIGINAL raw (fences + surrounding
        # prose both scanned) so a traceback pasted without a fence is treated identically.
        mined = _mine_identifiers(raw)

        text = _FENCE_BLOCK_RE.sub(" ", text)
        text = _TAG_RE.sub(" ", text)
        text = " ".join(text.split()).strip()
        if mined:
            text = (text + " " + " ".join(mined)).strip() if text else " ".join(mined)
        if not text:
            return ""
        if text.lower().strip(" ?!.,") in _CONTINUATION_PHRASES:
            return ""
        if len(tokenize(text)) < _MIN_CONTENT_TOKENS:
            return ""
        return text
    except Exception:
        return (raw or "").strip()

# The dense wall-clock bound + timeout machinery live in build_index (shared with the
# offline SessionStart refresh); recall uses the short per-query bound.


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
    query_tokens: List[str], index: LoadedIndex, *, patched_indices: Optional[set] = None
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
    return out


def _dense_rank_rows(query: str, index: LoadedIndex) -> List[int]:
    """RAW dense-matrix row indices ordered by descending cosine similarity, or [].

    RET-2: the matrix is WIDENED (description rows ``0..N-1`` then body-chunk rows ``N..``),
    so this returns ROW indices over that whole matrix — callers split the result into a
    description ranking and a body-chunk ranking (see ``_dense_rank``/``_dense_rank_body``)
    rather than this function knowing anything about the entries/chunks split itself.

    Loads the embedding model OFFLINE (no download); any failure -> [] (BM25 carries it).

    COR-8 model cross-check: a manifest embedded under model X, cosine-scored against a
    query embedded under model Y (the CURRENTLY configured ``build_index.DEFAULT_MODEL``),
    is comparing vectors from two different embedding spaces -- the resulting "similarity"
    is not meaningful, just noise that happens to look like a score. This can happen after
    ``MEMOBOT_EMBED_MODEL`` changes (or a stale index survives a plugin update that bumps the
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
        return [int(i) for i in order]
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
    query: str, index: LoadedIndex, *, raw_rows: Optional[List[int]] = None
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


def _invalidation_state(entry: dict, *, now: Optional[float] = None) -> Optional[str]:
    """Classify one entry's ``invalid_after`` as ``"recent"``, ``"old"``, or ``None``.

    ``None`` covers both "not invalidated" (no ``invalid_after``) and "unparseable
    ``invalid_after``" — both fail OPEN to "treat as valid/not-invalidated", never to "treat
    as invalidated". Pure; never raises.
    """
    raw = entry.get("invalid_after")
    if not raw:
        return None
    try:
        from datetime import datetime, timezone

        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ref = (
            datetime.fromtimestamp(now, tz=timezone.utc)
            if now is not None
            else datetime.now(timezone.utc)
        )
        age_days = (ref - ts).total_seconds() / 86400.0
    except Exception:
        return None
    return "recent" if age_days < _INVALIDATION_RECENT_DAYS else "old"


def _drift_patch(entry: dict, memory_dir: str) -> dict:
    """Detect mid-session edits (COR-4) and return a display/BM25-fresh COPY of ``entry``.

    Cheaply re-reads the file and recomputes ``doc_text``/``hash`` exactly as
    ``build_index.compute_corpus`` would. If the hash still matches the index's stored
    value, the entry is returned UNCHANGED (no drift) -- this is the common case and stays
    just a stat + read + hash, no re-tokenizing. If it differs (the description was edited
    on disk since the index was last built), the returned copy carries fresh ``tokens`` (so
    THIS query's BM25 re-ranks against the current text) and a fresh ``description`` (so the
    displayed line matches). The DENSE row is deliberately left untouched -- re-embedding
    synchronously here would violate the pure-retrieval hot-path invariant; the stale cached
    embedding keeps being used for this session, and a full re-embed happens at the next
    SessionStart rebuild. Never raises: any read/parse failure returns ``entry`` as-is
    (fail open to the last-known-good index state, same as every other degrade path here).

    RET-2: this stays DESCRIPTION-scoped only -- a memory's BODY (and hence its persisted
    ``body_chunks``) is deliberately NOT drift-patched here, on the exact same rationale as
    the dense row above: patching body chunks live would mean re-tokenizing (cheap) but also
    re-deriving which chunks even qualify (heading/paragraph re-split, bounds re-applied) on
    every query touching a possibly-large corpus, which is a heavier per-query cost than the
    single-entry hash+reread this function already does, and it still couldn't fix the STALE
    dense chunk row either. Body drift instead heals the same way the stale dense row does:
    at the next SessionStart ``refresh_index`` rebuild (which now also compares body-chunk
    hashes, not just entry hashes, to notice a body-only edit -- see ``refresh_index``'s
    docstring). Mid-session, a query for a just-edited body fact may miss until then.
    """
    try:
        path = os.path.join(memory_dir, entry["file"])
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        doc_text = memory_doc_text(entry["name"], text)
        fresh_hash = _hash(doc_text)
        if fresh_hash == entry.get("hash"):
            return entry
        patched = dict(entry)
        patched["tokens"] = tokenize(doc_text)
        patched["description"] = doc_text.split(". ", 1)[1] if ". " in doc_text else doc_text
        patched["hash"] = fresh_hash
        return patched
    except Exception:
        return entry


def _graph_seed_count() -> int:
    """Seed count for 1-hop expansion; MEMOBOT_GRAPH_SEEDS overrides, junk -> default."""
    raw = os.environ.get("MEMOBOT_GRAPH_SEEDS")
    if raw is None or not raw.strip():
        return _GRAPH_SEEDS
    try:
        return int(raw)
    except ValueError:
        return _GRAPH_SEEDS


def _expand_neighbors(
    penalized: List[Tuple[int, float, Optional[str]]],
    entries: List[dict],
    index_dir: Optional[str],
) -> Tuple[List[Tuple[int, float, Optional[str]]], set]:
    """1-hop neighbor expansion (GRA-1): inject linked memories at a discounted score.

    Takes the ALREADY-penalized candidate list (post-fusion, post-invalidation re-sort),
    seeds on its top-N entries, and unions their outbound+inbound 1-hop neighbor stems from
    GRA-6's persisted edge list (``load_edges`` — links.json only, the hot path's single
    extra small-JSON read). Injection rules, in order:

      - stems absent from the index are dropped (a link can outlive its target);
      - the seeds themselves are dropped (a seed is already ranked as well as it can be);
      - a neighbor's injected score is ``_NEIGHBOR_DISCOUNT x its BEST seed's penalized
        score`` (touching several seeds does not stack — the graph is a hint, not a vote);
      - invalidation applies IDENTICALLY to organic candidates: "recent" halves the
        injected score, "old" rides through as state so the display filter downstream
        drops it — expansion must never resurrect an invalidated memory;
      - a neighbor already in the penalized list at an equal-or-higher score keeps its
        ORGANIC tuple (and organic provenance); only a strictly-better injected score
        replaces it, and only then does the result carry the "graph" marker.

    Returns ``(re-sorted list, {entry indices injected via graph})`` so the emission loop
    can stamp provenance ("via"). Never raises; ANY failure — no index_dir resolvable
    (caller-supplied in-memory index with no dirs: eval self_recall probes, hermetic
    LoadedIndex tests), absent/corrupt links.json, junk env — returns the input untouched,
    so expansion can only ever be additive, never a new degradation mode.
    """
    try:
        if not index_dir or not penalized:
            return penalized, set()
        seeds_n = _graph_seed_count()
        if seeds_n <= 0:
            return penalized, set()
        from .links import load_edges

        edges = load_edges(index_dir)
        if not edges:
            return penalized, set()
        seeds = penalized[:seeds_n]
        seed_idxs = {i for i, _score, _state in seeds}
        # Entry "name" == file stem == the edge list's node identity (both come from the
        # same os.path.splitext(basename) in compute_corpus / LinkGraph).
        name_to_idx = {e.get("name"): j for j, e in enumerate(entries)}
        organic_score = {i: score for i, score, _state in penalized}
        injected: dict = {}  # entry idx -> best discounted seed score
        for si, sscore, _sstate in seeds:
            rec = edges.get(entries[si].get("name"))
            if not rec:
                continue
            for stem in rec.get("out", set()) | rec.get("in", set()):
                j = name_to_idx.get(stem)
                if j is None or j in seed_idxs:
                    continue
                cand = sscore * _NEIGHBOR_DISCOUNT
                if cand > injected.get(j, float("-inf")):
                    injected[j] = cand
        if not injected:
            return penalized, set()
        replace: dict = {}  # entry idx -> (adj_score, state)
        for j, cand in injected.items():
            state = _invalidation_state(entries[j])
            adj = cand * _INVALIDATION_PENALTY if state == "recent" else cand
            if j in organic_score and organic_score[j] >= adj:
                continue  # organic rank is already at least as good — keep it (and its label)
            replace[j] = (adj, state)
        if not replace:
            return penalized, set()
        expanded = [t for t in penalized if t[0] not in replace]
        expanded.extend((j, adj, state) for j, (adj, state) in replace.items())
        expanded.sort(key=lambda triple: triple[1], reverse=True)
        return expanded, set(replace)
    except Exception:
        return penalized, set()


# --------------------------------------------------------------------------- #
# Recall
# --------------------------------------------------------------------------- #
def _ensure_index(
    index: Optional[LoadedIndex], memory_dir: str, index_dir: Optional[str]
) -> Optional[LoadedIndex]:
    if index is not None:
        return index
    # Never-opted-in guard (SEC-3): a project with no .claude/memory corpus must gain
    # ZERO derived files — without this, the implicit build below mkdir-p's the index
    # dir (creating .claude/ itself) in every repo the user merely opens.
    if not memory_dir or not os.path.isdir(memory_dir):
        return None
    index_dir = index_dir or default_index_dir(memory_dir)
    loaded = load_index(index_dir)
    if loaded is not None:
        return loaded
    # No persisted index yet: build an in-memory BM25 view WITHOUT touching the dense model
    # (a hook must never block on indexing). Disable dense for this implicit build.
    prev = os.environ.get("MEMOBOT_DISABLE_DENSE")
    os.environ["MEMOBOT_DISABLE_DENSE"] = "1"
    try:
        build_index(memory_dir, index_dir)
        return load_index(index_dir)
    except Exception:
        return None
    finally:
        if prev is None:
            os.environ.pop("MEMOBOT_DISABLE_DENSE", None)
        else:
            os.environ["MEMOBOT_DISABLE_DENSE"] = prev


def recall(
    query: str,
    k: int = DEFAULT_K,
    *,
    index: Optional[LoadedIndex] = None,
    memory_dir: Optional[str] = None,
    index_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
) -> List[dict]:
    """Top-``k`` memories for ``query`` as ``[{name, file, description, score, backend, via}]``.

    Never raises; returns [] on any failure or empty query. ``repo_root`` is the SEC-1 trust
    gate's key — the hook entry (``main``) resolves it ONCE via ``resolve_dirs`` and threads it
    through so the hot path pays no second ``git rev-parse``; a direct caller that omits it has
    it derived (once) from ``memory_dir``'s git toplevel.
    """
    try:
        if not query or not query.strip():
            return []
        if index is not None:
            idx = index  # caller supplied the index -> never touch the real memory dir / git
        else:
            if memory_dir is None:
                memory_dir, resolved = resolve_dirs()
                if repo_root is None:
                    repo_root = resolved
            idx = _ensure_index(None, memory_dir, index_dir)
        if idx is None or not len(idx):
            return []

        # Trust gate (SEC-1): a foreign corpus (clone any repo carrying .claude/memory)
        # must inject NOTHING until this machine's user has explicitly trusted it — an
        # untrusted corpus is an unreviewed prompt-injection channel. The gate LOOKUP is a
        # stat + small-JSON read (no git/network/LLM), safe on the hot path; the git toplevel
        # it keys on is resolved by the caller (``main``) ONCE and threaded in via repo_root,
        # so the hot path pays no extra ``git rev-parse``. When there is NO resolvable git root
        # (a non-git corpus, or a caller-supplied in-memory `index` with no memory_dir —
        # eval/self_recall and the hermetic recall tests) the gate is inapplicable and recall
        # proceeds; only a real git corpus NOT in the trust registry is denied. MEMOBOT_TRUST_ALL
        # bypasses it for CI. The user-visible signal for the deny path is the SessionStart
        # untrusted-corpus nudge + /hippo:doctor — never a silent no-op with zero trace.
        if index is None:
            gate_root = trust.gate_repo_root(memory_dir, repo_root)
            if gate_root is not None and not trust.is_trusted(gate_root):
                return []

        entries = idx.entries

        # --- Mid-session drift (COR-4) -----------------------------------------------
        # The persisted index is only as fresh as the last SessionStart rebuild; a memory
        # edited or deleted DURING the session must not keep serving stale text/paths for
        # the rest of it. Bounded by _MAX_DRIFT_CHECKS so a huge corpus can't turn this
        # into an unbounded per-query disk scan -- beyond the bound, entries are passed
        # through untouched (fail open to "may be stale", never fail closed to "crash").
        # PRF-1: track WHICH indices actually changed under drift-patching (identity
        # comparison against the pre-patch entry — `_drift_patch` returns the SAME dict
        # object, unchanged, when the hash still matches, and only a NEW dict when it
        # patched fresh tokens in). The persisted BM25 postings (manifest's "bm25" block)
        # know nothing about a patched entry's fresh tokens, so `_bm25_rank`'s fast path
        # must be skipped for THIS query whenever any patched index is in play — it falls
        # back to the full from-scratch construction over the CURRENT `entries`, which is
        # always correct (just not the O(1)-per-matched-posting fast path).
        patched_indices: set = set()
        if memory_dir:
            patched_entries = []
            for i, e in enumerate(entries):
                if i < _MAX_DRIFT_CHECKS:
                    patched = _drift_patch(e, memory_dir)
                    if patched is not e:
                        patched_indices.add(i)
                    patched_entries.append(patched)
                else:
                    patched_entries.append(e)
            entries = patched_entries

        q_tokens = tokenize(query)
        bm25 = _bm25_rank(
            q_tokens, entries, stats=idx.manifest.get("bm25"), patched_indices=patched_indices
        )
        # RET-2: the dense matrix is WIDENED (description rows + body-chunk rows); embed the
        # query and score the WHOLE matrix exactly ONCE here (_dense_rank_rows), then split the
        # single raw order into a description ranking and a body ranking below -- doing this
        # twice (once per ranking) would double the per-query embed+matmul cost for no benefit,
        # which is exactly what an earlier draft of this item did and blew the p95 gate.
        raw_dense_rows = _dense_rank_rows(query, idx)
        dense = _dense_rank(query, idx, raw_rows=raw_dense_rows)

        # RET-2: FOUR rank lists total. bm25_desc/dense_desc (above) are the primary,
        # description-vocabulary signal -- unchanged from before this item. bm25_body/
        # dense_body are the BACKSTOP: body chunks ranked and mapped back to their parent
        # entry (deduped to each parent's best chunk -- see _bm25_rank_body/_dense_rank_body),
        # so a memory whose crucial fact lives only in its body (behind a generic description)
        # still surfaces, just at a discounted RRF weight (_BODY_RRF_WEIGHT) so a
        # keyword-dense body can never systematically outrank a well-written description.
        # COR-4: body drift is NOT patched here (only description entries are, above) --
        # see _drift_patch's docstring; a body edited mid-session keeps serving its
        # last-indexed chunk text until the next SessionStart rebuild, same rationale as the
        # stale dense row already accepted for entries pre-RET-2.
        bm25_body = _bm25_rank_body(q_tokens, idx, patched_indices=patched_indices)
        dense_body = _dense_rank_body(query, idx, raw_rows=raw_dense_rows)

        rankings = [r for r in (dense, bm25, dense_body, bm25_body) if r]
        weights = [
            w
            for r, w in zip(
                (dense, bm25, dense_body, bm25_body),
                (1.0, 1.0, _body_rrf_weight(), _body_rrf_weight()),
            )
            if r
        ]
        if not rankings:
            return []
        fused = _rrf_fuse(rankings, weights=weights)  # [(idx, score), ...] desc by fused score
        # backend label reflects the PRIMARY (description) signals only -- body rankings are
        # a backstop, not a third backend a user needs to reason about at the display layer.
        backend = "dense+bm25" if (dense and bm25) else ("dense" if dense else "bm25")

        # --- Soft-invalidation: applied to the SCORE, BEFORE the top-k cut. ---
        # This is the exact point of the x0.5 multiply -- "recent" halves the fused score so
        # a borderline-ranked recently-invalidated memory can legitimately fall out of the
        # top-k (real demotion, not a cosmetic post-hoc label). "old" does NOT change the
        # score here -- it is filtered from DISPLAY only, in the emission loop below, so it
        # keeps its true rank for internal bookkeeping but never reaches `results`.
        penalized: List[Tuple[int, float, Optional[str]]] = []
        for i, score in fused:
            state = _invalidation_state(entries[i])
            adj_score = score * _INVALIDATION_PENALTY if state == "recent" else score
            penalized.append((i, adj_score, state))
        penalized.sort(key=lambda triple: triple[1], reverse=True)

        # --- 1-hop graph expansion (GRA-1): AFTER fusion + invalidation re-sort. ---
        # Resolvable index_dir only: an explicit index_dir wins, else it derives from
        # memory_dir exactly as _ensure_index does (same default_index_dir, same
        # MEMOBOT_INDEX_DIR override). A caller-supplied in-memory index with NO dirs
        # (eval self_recall probes, hermetic LoadedIndex tests) resolves to None ->
        # _expand_neighbors is a no-op, zero behavior change there.
        graph_index_dir = index_dir
        if graph_index_dir is None and memory_dir:
            graph_index_dir = default_index_dir(memory_dir)
        penalized, graph_injected = _expand_neighbors(penalized, entries, graph_index_dir)

        # Walk the re-sorted list and emit up to k DISPLAY-eligible results, skipping "old"
        # entries as we go. This is NOT `penalized[:k]` followed by a filter -- a fixed-size
        # slice-then-filter could yield fewer than k results when an "old" entry occupies a
        # slot inside the naive top-k window while a display-eligible candidate sits just
        # past it. Walking in score order with a `continue`/`break` is the correct
        # implementation of "filter old, then take k" without truncating early. The corpus
        # itself (`idx.entries`, `idx.dense`, the BM25 corpus) is untouched by this filter --
        # "old" entries still fully participate in `_bm25_rank`/`_dense_rank`/`_rrf_fuse`,
        # they are simply never emitted into `results`.
        results: List[dict] = []
        for i, _adj_score, state in penalized:
            if len(results) >= k:
                break
            if state == "old":
                continue
            e = entries[i]
            # Deleted/renamed since the index was built (COR-4): drop it from THIS
            # session's output immediately rather than keep injecting a dangling path.
            if memory_dir and not os.path.isfile(os.path.join(memory_dir, e["file"])):
                continue
            results.append(
                {
                    "name": e["name"],
                    "file": e["file"],
                    "description": entry_description(e).strip(),
                    # COR-8: emit the REAL penalized fused score -- exactly the value
                    # `penalized` (post-invalidation-penalty, post-graph-discount) sorted
                    # on -- NOT fabricated 1/rank noise. Telemetry, threshold calibration,
                    # and RET-5's future salience fusion all inherit this number, so it must
                    # be the actual ranking signal, not a proxy that just happens to be
                    # monotone in emission order by construction. `rank` is the separate,
                    # explicit 1-based EMISSION rank (position in `results`, not `penalized`
                    # index -- "old"/deleted entries are skipped above and must not leave
                    # gaps in the emitted rank sequence).
                    "score": round(float(_adj_score), 6),
                    "rank": len(results) + 1,
                    "backend": backend,
                    # Injection provenance (GRA-1) — ALWAYS present so downstream code never
                    # branches on key existence: "graph" = surfaced by 1-hop expansion,
                    # "rank" = organic fusion. format_results renders "graph" as " (linked)"
                    # so a user reading the injected block can see WHY a line is there.
                    "via": "graph" if i in graph_injected else "rank",
                }
            )
        return results
    except Exception:
        return []


def format_results(results: List[dict], max_chars: int = _MAX_RECALL_CHARS) -> str:
    """Render recall results as a bounded one-pointer-per-line additionalContext block."""
    if not results:
        return ""
    header = (
        f"📎 Relevant memory (top {len(results)} by hybrid recall — read the file before "
        "relying on it; recalled facts reflect when they were written):"
    )
    lines = [header]
    for r in results:
        desc = r["description"].replace("\n", " ").strip()
        if len(desc) > 220:
            desc = desc[:217].rstrip() + "…"
        # Graph-injected lines (GRA-1) carry a legible provenance marker so injection is
        # inspectable — a "(linked)" entry is here because a top-seed memory links to it,
        # not because it matched the query lexically/semantically on its own.
        marker = " (linked)" if r.get("via") == "graph" else ""
        lines.append(f"  • {r['name']} ({r['file']}) — {desc}{marker}")
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[: max_chars - 16].rstrip() + "\n…(truncated)"
    return out


# --------------------------------------------------------------------------- #
# SessionStart producer: recently-captured memories (reuses T1 source_commit)
# --------------------------------------------------------------------------- #
def recent_memories(
    memory_dir: str,
    repo_root: str,
    *,
    now: Optional[float] = None,
    window_days: float = 14.0,
    limit: int = 10,
) -> List[dict]:
    """Memories whose ``source_commit`` lands within the last ``window_days``, newest first.

    Reuses Tier 1 provenance (``source_commit``) + the staleness ``_commit_times`` git
    helper. Pure; never raises; returns [] on failure or when nothing is recent.
    """
    try:
        recs = []  # (name, source_commit)
        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            _, sc = read_provenance(text)
            if sc:
                recs.append((os.path.splitext(os.path.basename(path))[0], sc))
        if not recs:
            return []
        ctimes = _commit_times([sc for _, sc in recs], repo_root)
        ref = time.time() if now is None else now
        cutoff = ref - window_days * 86400.0
        dated = [
            {"name": name, "committed": ctimes[sc]}
            for name, sc in recs
            if sc in ctimes and ctimes[sc] >= cutoff
        ]
        dated.sort(key=lambda d: (-d["committed"], d["name"]))
        return dated[:limit]
    except Exception:
        return []


def git_recent_producer(memory_dir: str, repo_root: str) -> Optional[str]:
    """SessionStart producer: a one-block digest of recently-captured memories.

    Window via ``MEMOBOT_RECENT_DAYS`` (default 14). Self-suppresses when nothing is recent.
    The untrusted-corpus gate (SEC-1) is enforced once, upstream, by ``session_start``'s
    ``build_context`` short-circuit — no producer re-checks it (one gate boundary, no extra
    per-producer git call on the trusted hot path).
    """
    try:
        days = float(os.environ.get("MEMOBOT_RECENT_DAYS", "14") or 14)
    except ValueError:
        days = 14.0
    recent = recent_memories(memory_dir, repo_root, window_days=days)
    if not recent:
        return None
    lines = [f"🆕 Recently captured memory (last {int(days)}d, newest first):"]
    for item in recent:
        lines.append(f"  • {item['name']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI / hook entry
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Recall top-K memories for a query.")
    parser.add_argument("query", nargs="*", help="the query text")
    parser.add_argument("-k", type=int, default=DEFAULT_K)
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--index-dir", default=None)
    parser.add_argument("--repo-root", default=None)
    parser.add_argument(
        "--session-id",
        default=None,
        help="harness-provided session id (COR-6) — keys telemetry directly instead of the "
        "shared file-based token, fixing concurrent-session attribution.",
    )
    args = parser.parse_args(argv)

    raw_query = " ".join(args.query).strip()
    # Query hygiene: strip harness envelopes / skip near-empty prompts BEFORE embedding, so a
    # task-notification blob or a "?" continuation never pays a model load to inject noise.
    query = clean_query(raw_query)

    # Resolve the memory dir + repo root once so we can both drive recall and read the
    # MEMORY.md floor for floor-dedup, plus stamp the episode-log watermark commit. A
    # resolution failure leaves whichever wasn't explicitly passed at None — recall resolves
    # its own dir, floor-dedup is skipped, and the episode log's head_commit is omitted.
    memory_dir = args.memory_dir
    repo_root = args.repo_root
    if memory_dir is None:
        # Only resolve_dirs() when memory_dir actually needs it -- never spend an EXTRA git
        # call just to backfill repo_root when --memory-dir was already explicit (keeps an
        # explicit-memory-dir CLI/test invocation fully hermetic: repo_root simply stays None,
        # same as today, rather than resolving against whatever the real cwd happens to be).
        try:
            resolved_memory_dir, resolved_repo_root = resolve_dirs()
            memory_dir = resolved_memory_dir
            if repo_root is None:
                repo_root = resolved_repo_root
        except Exception:
            memory_dir = None

    t0 = time.perf_counter()
    if query:
        # Floor-dedup (DISPLAY layer only — never inside recall(), which eval_recall's
        # self_recall probes directly): the User + Working-Style memories are ALREADY
        # always-loaded in the MEMORY.md floor, so re-surfacing them wastes a top-k slot +
        # injects redundant tokens. Over-fetch by the floor size, drop floor members, slice to k.
        floor = floor_memory_names(memory_dir) if memory_dir else set()
        pool_k = args.k + len(floor) if floor else args.k
        results = recall(
            query, k=pool_k, memory_dir=memory_dir, index_dir=args.index_dir, repo_root=repo_root
        )
        if floor:
            results = [r for r in results if r["name"] not in floor]
        results = results[: args.k]
    else:
        results = []  # hygiene skipped recall — no model load, no junk injection
    latency_ms = (time.perf_counter() - t0) * 1000.0

    out = format_results(results)
    if out:
        print(out)
    # Telemetry: fire-and-forget AFTER results are computed/printed. Logs even a SKIP (empty
    # results -> backend "none") under the RAW prompt preview, so the ledger shows hygiene at
    # work. Logging lives ONLY in main() (the CLI/hook entry) — NOT in recall() — so
    # eval_recall's direct recall() calls never pollute the ledger. Wrapped so it can never
    # raise into / delay the hook. The episode buffer (the future capture pass's replay log)
    # is logged in the SAME block, right after the recall ledger, on the SAME raw_query gate —
    # it must start soaking now even though nothing reads it yet.
    #
    # SEC-1 gate: an UNTRUSTED corpus already makes recall() return [] (the trust gate inside
    # recall() denies it), but before this fix main() still appended a backend="none"
    # telemetry line for it -- a ledger entry (even an empty one) is itself a trace that a
    # foreign, unreviewed corpus was queried. Reuse the ALREADY-resolved repo_root here (no
    # extra git call on top of what recall() itself just paid) so an untrusted corpus leaves
    # ZERO ledger trace, matching recall()'s own zero-injection posture. A non-git corpus, or
    # one with no resolvable repo_root, has an inapplicable gate (gate_root is None) and is
    # untouched by this check -- same fail-open posture as recall()'s own gate.
    trusted_or_gate_inapplicable = True
    if memory_dir:
        gate_root = trust.gate_repo_root(memory_dir, repo_root)
        if gate_root is not None and not trust.is_trusted(gate_root):
            trusted_or_gate_inapplicable = False
    if raw_query and memory_dir and os.path.isdir(memory_dir) and trusted_or_gate_inapplicable:
        # The corpus-existence gate (SEC-3): a project that never opted in (no
        # .claude/memory) must never gain a telemetry ledger with prompt previews —
        # a habitual `git add .` would commit prompt fragments to shared history.
        try:
            from .telemetry import default_telemetry_dir, log_episode, log_recall_event

            td = default_telemetry_dir(memory_dir)
            log_recall_event(
                results,
                query=raw_query,
                k=args.k,
                latency_ms=latency_ms,
                telemetry_dir=td,
                session_id=args.session_id or None,
            )
            log_episode(
                [r.get("name") for r in results if r.get("name")],
                query=raw_query,
                repo_root=repo_root,
                telemetry_dir=td,
                session_id=args.session_id or None,
            )
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
