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
from .staleness import RunContext, _commit_times, read_provenance

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
# volume. Env-overridable (not a hook env var in the HIPPO_ prefix sense of "per-invocation
# tuning" -- this is a corpus-wide ranking knob an operator might calibrate via /hippo:audit).
_BODY_RRF_WEIGHT = 0.5


def _body_rrf_weight() -> float:
    """``HIPPO_BODY_RRF_WEIGHT`` override; malformed/absent -> the module default. Never raises."""
    raw = os.environ.get("HIPPO_BODY_RRF_WEIGHT")
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

# Typed-edge demotion (GRA-4) — a memory that is the TARGET of a live supersedes edge
# (some OTHER memory in the index declares `supersedes: [it]`) has its fused score halved
# BEFORE the top-k cut, exactly the invalidation penalty's bounded-multiplier style: real
# demotion (it can fall out of top-k, and its successor outranks it), never a hard exclude
# (a wide-k query still surfaces it, annotated). `contradicts` targets are deliberately
# NOT demoted — a contradiction means "one of these is wrong, VERIFY", not "this one lost"
# — they carry a conflict annotation only. `refines` is navigational: no ranking effect,
# no annotation. Typed edges reach this hot path via GRA-6's persisted links.json ONLY
# (one small-JSON read, shared with 1-hop expansion) — cache absent degrades to
# no-demotion/no-annotation, never a corpus read per prompt.
_SUPERSEDED_PENALTY = 0.5

# Mid-session drift (COR-4) — a stat+reread per entry is cheap, but bound it so a huge
# corpus can never turn the hot path into an O(corpus) disk scan of unbounded size.
_MAX_DRIFT_CHECKS = 200

# 1-hop graph expansion (GRA-1) — the first load-bearing graph READ. After fusion +
# invalidation penalties, the top-_GRAPH_SEEDS entries seed a 1-hop neighbor pull from the
# persisted edge list (GRA-6's links.json, one small-JSON read, no corpus scan). Neighbors
# are injected at _NEIGHBOR_DISCOUNT x their best seed's penalized score and COMPETE for
# top-k — no reserved slots, so expansion can only surface a linked memory when its
# discounted score actually beats an organic candidate, never by displacing one for free.
_GRAPH_SEEDS = 3  # override: HIPPO_GRAPH_SEEDS (0 disables expansion entirely)
_NEIGHBOR_DISCOUNT = 0.5

# --------------------------------------------------------------------------- #
# RET-5: salience fusion — recency / usage / staleness as BOUNDED ranking priors.
# DEFAULT OFF (``HIPPO_SALIENCE=1`` opts in — the roadmap: "ship behind an env flag
# first"). Applied to the fused+penalized score, PRE-CUT (same "real demotion, can
# reorder top-k" posture as the invalidation/supersede penalties above), never post-hoc
# display-only. Each signal is a MULTIPLICATIVE, individually-capped nudge — relevance (the
# RRF fusion this runs after) picks the candidate set and dominates ordering; salience only
# re-orders NEAR-TIES within it:
#   - recency prior  : up to +10% for a same-day ``source_commit_time``, decaying linearly
#                       to 0 by _SALIENCE_RECENCY_WINDOW_DAYS; absent/old -> 0 (no penalty
#                       for an undated memory, only a missed boost).
#   - usage prior     : up to +10% at usage_score == 1.0 (recalled in EVERY distinct session
#                       LIF-4's aggregates have observed) — capped HARD so a much-recalled
#                       memory can NEVER outrank a clearly-more-relevant one on usage alone.
#   - staleness penalty: up to -15% for a memory LIF-6's stale.json marked drifted, graduated
#                       by how many cited paths changed (saturating at
#                       _SALIENCE_STALENESS_SATURATION) — advisory, absent -> no penalty.
# Combined worst-case swing is (1.10 * 1.10) / 0.85 ≈ 1.42x -- enough to break a tie between
# two candidates the fusion already ranks close together (see the controlled-fixture test),
# but far short of the multi-x gaps a genuine relevance difference produces in RRF scores.
_SALIENCE_RECENCY_CAP = 0.10
_SALIENCE_RECENCY_WINDOW_DAYS = 180.0
_SALIENCE_USAGE_CAP = 0.10
_SALIENCE_STALENESS_CAP = 0.15
_SALIENCE_STALENESS_SATURATION = 5

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

# Knee/score-gap cutoff (leg 2): applied to the FUSED, penalized list at emission time, not
# to either backend individually -- it is a property of "how much weaker is the next
# candidate than the one before it", which only means something once both signals (and the
# soft-invalidation penalty) have already been combined into one comparable scale. Ratio
# rather than absolute gap because RRF-fused scores have no fixed unit -- a ratio is scale
# invariant across corpus size / RRF k / body-weight tuning. 0 disables (every candidate
# admitted regardless of gap, "up to k" degenerates back to "exactly k" subject only to the
# floor/skip legs) -- see _knee_ratio()'s docstring for why 0 is exact-equality-safe.
#
# Calibrated LOW (0.5, the bottom of the roadmap's suggested 0.5-0.7 band): RRF fusion has a
# characteristic, EXPECTED cliff at "hit both rankings" vs "hit only one" -- a doc appearing
# in both the dense and BM25 top ranks scores roughly double a doc appearing in only one
# (each contributes its own 1/(k+rank+1) term), independent of whether the single-ranking
# hit is still a genuinely correct answer. Measured on the pack-corpus hard-set (recall_
# hard_set.yaml): at 0.6 this dual-vs-single-hit cliff alone cost two real hard-set hits
# (claude_is_memory_master / feedback_new_logs_mean_recurrence, both dense-only top hits
# behind a cluster of dual-backend matches) -- recall@10 dropped 1.0 -> 0.9091, violating
# the roadmap's explicit "on-topic recall@10 UNCHANGED" bar even though the tracked GATE
# (>=0.80) still passed. At 0.5 the same fixture is back to a clean 1.0 (see the RET-1
# commit body's before/after table) while the golden-corpus dense bands and the abstention
# fixture are unaffected either way (the floor/hard-skip legs, not the knee, do the real
# off-topic-rejection work) -- confirming 0.5 is conservative-enough to "admit when in
# doubt" without giving up the cutoff's ability to stop injecting once genuinely irrelevant
# tail candidates show up.
_KNEE_RATIO = 0.5  # override: HIPPO_KNEE_RATIO


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


def _knee_ratio() -> float:
    """``HIPPO_KNEE_RATIO`` override; malformed/absent -> the module default. Never raises.

    0 (or any non-positive value) disables the knee cutoff outright -- see its use in
    ``recall()``'s emission loop, which skips the check entirely rather than comparing
    against a degenerate ratio.
    """
    raw = os.environ.get("HIPPO_KNEE_RATIO")
    if raw is None or not raw.strip():
        return _KNEE_RATIO
    try:
        return float(raw)
    except ValueError:
        return _KNEE_RATIO

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
        return [int(i) for i in order if float(sims[i]) >= floor]
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
    """Seed count for 1-hop expansion; HIPPO_GRAPH_SEEDS overrides, junk -> default."""
    raw = os.environ.get("HIPPO_GRAPH_SEEDS")
    if raw is None or not raw.strip():
        return _GRAPH_SEEDS
    try:
        return int(raw)
    except ValueError:
        return _GRAPH_SEEDS


def _load_hot_edges(index_dir: Optional[str]) -> Optional[dict]:
    """The hot path's ONE links.json read (GRA-6's ``load_edges``), or None.

    Loaded exactly once per ``recall()`` call and shared by BOTH graph consumers — the
    typed-edge maps (GRA-4) and 1-hop expansion (GRA-1) — so adding typed edges cost the
    hot path zero additional I/O. Never raises; None (no ``index_dir`` resolvable, cache
    absent/corrupt) simply disables both consumers, the same degrade-to-organic-ranking
    posture ``_expand_neighbors`` always had.
    """
    try:
        if not index_dir:
            return None
        from .links import load_edges

        return load_edges(index_dir)
    except Exception:
        return None


def _typed_relation_maps(
    entries: List[dict], edges: Optional[dict]
) -> Tuple[Dict[int, List[str]], Dict[int, List[str]]]:
    """``(superseded_by, contradicted_by)`` — entry index -> sorted LIVE source names.

    GRA-4: built from the persisted edge list's ``typed_in`` direction (who declares the
    relation TOWARD this entry), filtered to LIVE sources — a source stem must itself be
    present in the loaded index, so a slightly-stale cache naming a deleted successor
    degrades to "no edge" (fail open, no demotion) rather than annotating with a ghost.
    Entry ``name`` == file stem == the edge list's node identity, same join
    ``_expand_neighbors`` relies on. Never raises; ``({}, {})`` when the cache is absent.
    """
    superseded: Dict[int, List[str]] = {}
    contradicted: Dict[int, List[str]] = {}
    try:
        if not edges:
            return superseded, contradicted
        live = {e.get("name") for e in entries}
        for i, e in enumerate(entries):
            rec = edges.get(e.get("name"))
            if not rec:
                continue
            typed_in = rec.get("typed_in") or {}
            sup = sorted(s for s in typed_in.get("supersedes", ()) if s in live)
            if sup:
                superseded[i] = sup
            con = sorted(s for s in typed_in.get("contradicts", ()) if s in live)
            if con:
                contradicted[i] = con
        return superseded, contradicted
    except Exception:
        return {}, {}


def _typed_note(i: int, superseded: Dict[int, List[str]], contradicted: Dict[int, List[str]]) -> str:
    """One bounded annotation string for entry ``i`` ("" when it carries no typed edge).

    Names at most two sources per relation (+N more) so a heavily-superseded memory can
    never balloon its pointer line past the display budget.
    """

    def _names(names: List[str]) -> str:
        head = ", ".join(names[:2])
        return head if len(names) <= 2 else f"{head} (+{len(names) - 2} more)"

    bits: List[str] = []
    if i in superseded:
        bits.append(f"superseded by {_names(superseded[i])}")
    if i in contradicted:
        bits.append(f"contradicts {_names(contradicted[i])} — verify")
    return "; ".join(bits)


def _expand_neighbors(
    penalized: List[Tuple[int, float, Optional[str]]],
    entries: List[dict],
    edges: Optional[dict],
    superseded: Optional[Dict[int, List[str]]] = None,
) -> Tuple[List[Tuple[int, float, Optional[str]]], set]:
    """1-hop neighbor expansion (GRA-1): inject linked memories at a discounted score.

    Takes the ALREADY-penalized candidate list (post-fusion, post-invalidation re-sort),
    seeds on its top-N entries, and unions their outbound+inbound 1-hop neighbor stems from
    GRA-6's persisted edge list (``edges`` — the ``_load_hot_edges`` result ``recall()``
    loaded once, links.json only, the hot path's single extra small-JSON read). Injection
    rules, in order:

      - stems absent from the index are dropped (a link can outlive its target);
      - the seeds themselves are dropped (a seed is already ranked as well as it can be);
      - a neighbor's injected score is ``_NEIGHBOR_DISCOUNT x its BEST seed's penalized
        score`` (touching several seeds does not stack — the graph is a hint, not a vote);
      - invalidation applies IDENTICALLY to organic candidates: "recent" halves the
        injected score, "old" rides through as state so the display filter downstream
        drops it — expansion must never resurrect an invalidated memory;
      - the superseded penalty (GRA-4, ``superseded`` — the entry-index map ``recall()``
        already built) applies identically too: a superseded neighbor enters at the SAME
        halved score it would rank at organically — the untyped graph must not become a
        side door around supersession;
      - a neighbor already in the penalized list at an equal-or-higher score keeps its
        ORGANIC tuple (and organic provenance); only a strictly-better injected score
        replaces it, and only then does the result carry the "graph" marker.

    Returns ``(re-sorted list, {entry indices injected via graph})`` so the emission loop
    can stamp provenance ("via"). Never raises; ANY failure — no edges loaded
    (caller-supplied in-memory index with no dirs: eval self_recall probes, hermetic
    LoadedIndex tests; absent/corrupt links.json), junk env — returns the input untouched,
    so expansion can only ever be additive, never a new degradation mode.
    """
    try:
        if not edges or not penalized:
            return penalized, set()
        seeds_n = _graph_seed_count()
        if seeds_n <= 0:
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
            if superseded and j in superseded:
                adj *= _SUPERSEDED_PENALTY
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
# RET-5: salience fusion — recency / usage / staleness (see the constants block above for
# the caps and why they're small). DEFAULT OFF; every reader here degrades to "no signal"
# (never a hard error) on any missing/corrupt input, matching the graph readers' posture.
# --------------------------------------------------------------------------- #
def _salience_enabled() -> bool:
    """True only when ``HIPPO_SALIENCE`` is explicitly truthy — DEFAULT OFF (the roadmap:
    "ship behind an env flag first"). Mirrors ``build_index.dense_disabled()``'s falsy set
    so ``HIPPO_SALIENCE=0``/``false`` reads as an explicit opt-out, not a truthy string.
    """
    raw = os.environ.get("HIPPO_SALIENCE", "").strip()
    return raw not in ("", "0", "false", "False")


def _recency_boost(entry: dict, *, now: float) -> float:
    """Bounded ``[0, _SALIENCE_RECENCY_CAP]`` recency prior from the entry's persisted
    ``source_commit_time`` (copied into the manifest at build time by
    ``build_index.compute_corpus`` — see its docstring; NOT re-derived here, so this is
    pure arithmetic, no git call on the hot path). Linear decay from the full cap at age 0
    to 0 at ``_SALIENCE_RECENCY_WINDOW_DAYS``; missing/malformed/future/older -> 0 (no
    boost, but never a PENALTY — an undated memory is judged on relevance alone). Never
    raises.
    """
    sct = entry.get("source_commit_time")
    if not isinstance(sct, (int, float)) or isinstance(sct, bool):
        return 0.0
    age_days = (now - sct) / 86400.0
    if age_days <= 0.0:
        return _SALIENCE_RECENCY_CAP  # future/clock-skew timestamp -> treat as "as fresh as it gets"
    if age_days >= _SALIENCE_RECENCY_WINDOW_DAYS:
        return 0.0
    return _SALIENCE_RECENCY_CAP * (1.0 - age_days / _SALIENCE_RECENCY_WINDOW_DAYS)


def _usage_boost_map(memory_dir: Optional[str]) -> Dict[str, float]:
    """Name -> bounded ``[0, _SALIENCE_USAGE_CAP]`` usage prior from LIF-4's
    rotation-surviving ``usage_aggregates.json`` (distinct-session count recalling this
    memory / total distinct sessions observed) — ONE small JSON read
    (``telemetry.read_usage_aggregates``), the same cost class as the graph's ``links.json``
    read already on this hot path. Capped HARD: even a memory recalled in EVERY session ever
    logged only earns ``_SALIENCE_USAGE_CAP`` — a much-recalled memory can never outrank a
    clearly-more-relevant one on usage alone (the roadmap's explicit AC). Never raises; ``{}``
    when ``memory_dir`` is falsy or no sessions have been logged yet.
    """
    if not memory_dir:
        return {}
    try:
        from .telemetry import default_telemetry_dir, read_usage_aggregates

        agg = read_usage_aggregates(default_telemetry_dir(memory_dir))
        total = agg.get("sessions", {}).get("count") or 0
        if not total:
            return {}
        out: Dict[str, float] = {}
        for name, rec in (agg.get("memories") or {}).items():
            n = rec.get("sessions") if isinstance(rec, dict) else None
            if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
                continue
            out[name] = _SALIENCE_USAGE_CAP * min(1.0, n / total)
        return out
    except Exception:
        return {}


def _staleness_penalty_map(index_dir: Optional[str]) -> Dict[str, float]:
    """Name -> bounded ``[0, _SALIENCE_STALENESS_CAP]`` penalty from LIF-6's persisted
    ``stale.json`` (``staleness.read_stale_cache``) — advisory: absent/corrupt cache -> ``{}``
    (no penalty for anyone), NEVER a git call on this hot path (the cache was computed once,
    upstream, by SessionStart's staleness scan; recall only reads the small JSON it left
    behind). Graduated by how much cited code drifted (``changed``, saturating at
    ``_SALIENCE_STALENESS_SATURATION`` paths) rather than a flat penalty, so a memory citing
    one long-ago-touched path is nudged less than one whose entire cited surface moved. Never
    raises; ``{}`` when ``index_dir`` is falsy or the cache is missing/empty/corrupt.
    """
    if not index_dir:
        return {}
    try:
        from .staleness import read_stale_cache

        stale = read_stale_cache(index_dir)
        if not stale:
            return {}
        out: Dict[str, float] = {}
        for name, rec in stale.items():
            changed = rec.get("changed") if isinstance(rec, dict) else None
            if not isinstance(changed, int) or isinstance(changed, bool) or changed <= 0:
                changed = 1  # present in stale.json at all -> at least a floor penalty
            out[name] = _SALIENCE_STALENESS_CAP * min(1.0, changed / _SALIENCE_STALENESS_SATURATION)
        return out
    except Exception:
        return {}


def _apply_salience(
    penalized: List[Tuple[int, float, Optional[str]]],
    entries: List[dict],
    *,
    memory_dir: Optional[str],
    index_dir: Optional[str],
) -> Tuple[List[Tuple[int, float, Optional[str]]], Dict[int, dict]]:
    """Fold the three bounded salience priors into ``penalized``'s score, BEFORE the top-k
    cut / graph expansion — same "real demotion, can reorder top-k" posture the
    invalidation/supersede penalties above already have, not a cosmetic display-only
    adjustment. Multiplicative and bounded (see the ``_SALIENCE_*`` caps at the top of this
    module): relevance (the RRF fusion this runs after) sets the candidate set and dominates
    ordering; salience only nudges WITHIN it.

    Returns ``(re-sorted list, {entry index: {"recency", "usage", "staleness"}})`` so the
    emission loop can both re-cut on the adjusted order and surface the breakdown (COR-8
    true-score discipline) on every emitted result. Only called when ``_salience_enabled()``
    — callers must not pay this cost, or change scores by even a float no-op multiply, when
    the flag is off. Never raises: any failure degrades to the UNTOUCHED input list and an
    empty component map, the same fail-open posture ``_expand_neighbors`` already has.
    """
    try:
        now = time.time()
        usage_map = _usage_boost_map(memory_dir)
        stale_map = _staleness_penalty_map(index_dir)
        components: Dict[int, dict] = {}
        adjusted: List[Tuple[int, float, Optional[str]]] = []
        for i, score, state in penalized:
            e = entries[i]
            rec_b = _recency_boost(e, now=now)
            use_b = usage_map.get(e.get("name"), 0.0)
            stale_p = stale_map.get(e.get("name"), 0.0)
            multiplier = (1.0 + rec_b) * (1.0 + use_b) * (1.0 - stale_p)
            adjusted.append((i, score * multiplier, state))
            components[i] = {
                "recency": round(rec_b, 4),
                "usage": round(use_b, 4),
                "staleness": round(stale_p, 4),
            }
        adjusted.sort(key=lambda triple: triple[1], reverse=True)
        return adjusted, components
    except Exception:
        return penalized, {}


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
    prev = os.environ.get("HIPPO_DISABLE_DENSE")
    os.environ["HIPPO_DISABLE_DENSE"] = "1"
    try:
        build_index(memory_dir, index_dir)
        return load_index(index_dir)
    except Exception:
        return None
    finally:
        if prev is None:
            os.environ.pop("HIPPO_DISABLE_DENSE", None)
        else:
            os.environ["HIPPO_DISABLE_DENSE"] = prev


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
        # proceeds; only a real git corpus NOT in the trust registry is denied. HIPPO_TRUST_ALL
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
        # RET-1 leg 3 — hard skip: ABSTENTION IS THE CORRECT OUTPUT when no signal, of any
        # kind, actually matched this query. `dense`/`dense_body` are already floor-filtered
        # (see `_dense_rank_rows`) so an empty `dense` here means "zero above-floor
        # candidates", not merely "the least-bad candidates happened to rank last". `bm25`/
        # `bm25_body` are already token-overlap filtered (`_bm25_rank`'s match-set IS its
        # floor -- BM25 never had the "whole corpus always ranks" problem dense did). So
        # `not rankings` (all four empty) is EXACTLY "dense cleared no floor, and neither
        # BM25 ranking shares a single token with the query" -- the roadmap's hard-skip
        # condition, checked over BOTH the description and body-backstop signals so a memory
        # whose only match is a distinctive BODY token is never abstained away. GRA-1
        # interplay: this return happens BEFORE `_expand_neighbors` ever runs, so an empty
        # organic list yields NO graph seeds and thus no expansion -- abstention is absolute,
        # never overridden by a linked memory that shares no signal with the query itself.
        if not rankings:
            return []
        fused = _rrf_fuse(rankings, weights=weights)  # [(idx, score), ...] desc by fused score
        # backend label reflects the PRIMARY (description) signals only -- body rankings are
        # a backstop, not a third backend a user needs to reason about at the display layer.
        backend = "dense+bm25" if (dense and bm25) else ("dense" if dense else "bm25")

        # RET-1: a SEPARATE, primary-signal-only fusion (dense_desc + bm25_desc, always
        # weight 1.0, never body/graph) feeds the knee cutoff below. The full `fused` score
        # already has _BODY_RRF_WEIGHT baked in for any entry the body backstop ALSO ranked
        # -- comparing knee ratios against that blended number would conflate "this memory's
        # topical relevance genuinely dropped" with "this memory only has a deliberately
        # down-weighted body-backstop signal, by design" (RET-2's whole point). Entries with
        # NO primary-only ranking (a pure body-only hit, or a not-yet-injected graph
        # neighbor) are simply absent from this dict -- the emission loop below treats that
        # as "no organic relevance baseline to judge a cliff against" and exempts them from
        # the knee check entirely, exactly like the soft-invalidation/graph-discount cases.
        primary_rankings = [r for r in (dense, bm25) if r]
        primary_relevance: Dict[int, float] = (
            {i: score for i, score in _rrf_fuse(primary_rankings)} if primary_rankings else {}
        )

        # --- Graph edges: ONE links.json read for both typed edges and expansion. ---
        # Resolvable index_dir only: an explicit index_dir wins, else it derives from
        # memory_dir exactly as _ensure_index does (same default_index_dir, same
        # HIPPO_INDEX_DIR override). A caller-supplied in-memory index with NO dirs
        # (eval self_recall probes, hermetic LoadedIndex tests) resolves to None ->
        # no typed maps, no expansion — zero behavior change there.
        graph_index_dir = index_dir
        if graph_index_dir is None and memory_dir:
            graph_index_dir = default_index_dir(memory_dir)
        edges = _load_hot_edges(graph_index_dir)
        superseded_by, contradicted_by = _typed_relation_maps(entries, edges)

        # --- Soft-invalidation + supersession: applied to the SCORE, BEFORE the top-k cut. ---
        # This is the exact point of the x0.5 multiplies -- "recent" invalidation halves the
        # fused score so a borderline-ranked recently-invalidated memory can legitimately
        # fall out of the top-k (real demotion, not a cosmetic post-hoc label), and a LIVE
        # supersedes target (GRA-4) is halved the same bounded way so its successor outranks
        # it in the SAME top-k. "old" does NOT change the score here -- it is filtered from
        # DISPLAY only, in the emission loop below, so it keeps its true rank for internal
        # bookkeeping but never reaches `results`. Contradicted entries deliberately get NO
        # penalty (annotation-only — see _SUPERSEDED_PENALTY's comment block).
        penalized: List[Tuple[int, float, Optional[str]]] = []
        for i, score in fused:
            state = _invalidation_state(entries[i])
            adj_score = score * _INVALIDATION_PENALTY if state == "recent" else score
            if i in superseded_by:
                adj_score *= _SUPERSEDED_PENALTY
            penalized.append((i, adj_score, state))
        penalized.sort(key=lambda triple: triple[1], reverse=True)

        # --- RET-5: salience fusion (recency/usage/staleness) — DEFAULT OFF. ---------------
        # Gated entirely behind HIPPO_SALIENCE so a flag-off run pays zero extra I/O and
        # produces a BYTE-IDENTICAL `penalized` (no no-op float multiply even) to before this
        # item. When enabled, runs BEFORE graph expansion — same "pre-cut" posture as the
        # invalidation/supersede penalties above, so a salience-boosted memory can compete
        # for graph-expansion seed slots exactly like an organically-boosted one would.
        salience_components: Dict[int, dict] = {}
        if _salience_enabled():
            penalized, salience_components = _apply_salience(
                penalized, entries, memory_dir=memory_dir, index_dir=graph_index_dir
            )

        # --- 1-hop graph expansion (GRA-1): AFTER fusion + invalidation/supersession re-sort. ---
        penalized, graph_injected = _expand_neighbors(penalized, entries, edges, superseded_by)

        # Walk the re-sorted list and emit up to k DISPLAY-eligible results, skipping "old"
        # entries as we go. This is NOT `penalized[:k]` followed by a filter -- a fixed-size
        # slice-then-filter could yield fewer than k results when an "old" entry occupies a
        # slot inside the naive top-k window while a display-eligible candidate sits just
        # past it. Walking in score order with a `continue`/`break` is the correct
        # implementation of "filter old, then take k" without truncating early. The corpus
        # itself (`idx.entries`, `idx.dense`, the BM25 corpus) is untouched by this filter --
        # "old" entries still fully participate in `_bm25_rank`/`_dense_rank`/`_rrf_fuse`,
        # they are simply never emitted into `results`.
        #
        # RET-1 leg 2 — knee/score-gap cutoff: k becomes "up to k". Compared against the
        # PREVIOUS EMITTED score (not the previous `penalized` entry) so a skipped "old" or
        # dangling-file candidate never counts as the reference point -- the gap that matters
        # is between consecutive results a user would actually SEE, not internal bookkeeping
        # rows. Only checked from the second result onward (`results` non-empty): the first
        # result has no predecessor to be a "knee" relative to, and the floor/skip legs above
        # already gate whether ANYTHING is admitted at all. A non-positive ratio (env
        # override 0, or negative) disables the check outright -- `ratio <= 0` can never be
        # satisfied by `score < ratio * prev` for any non-negative score/prev pair anyway,
        # but the explicit early-out keeps the intent legible and skips a division-adjacent
        # comparison entirely when the knee is turned off.
        knee_ratio = _knee_ratio()
        results: List[dict] = []
        prev_relevance: Optional[float] = None
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
            # RET-1: the knee compares PRIMARY-SIGNAL-ONLY relevance (`primary_relevance`,
            # see its construction above), never the display/sort score -- an entry with NO
            # primary ranking of its own (a pure body-backstop hit, or a graph-injected
            # neighbor that was never organically ranked -- see `_expand_neighbors`) has
            # nothing in `primary_relevance` at all. Such an entry is EXEMPT from the knee
            # check both ways: it is never cut for "falling off a cliff" relative to the
            # previous result (its only relevance signal is a deliberate backstop weight or
            # graph discount, not a topical-relevance drop), and it never becomes the
            # reference point for the NEXT comparison either (`prev_relevance` only advances
            # on an entry that actually HAS a primary score) -- a body/graph hit sitting
            # between two organic ones must not silently loosen or tighten the knee for
            # whatever organic candidate comes after it.
            relevance = primary_relevance.get(i)
            if (
                knee_ratio > 0
                and relevance is not None
                and prev_relevance is not None
                and relevance < knee_ratio * prev_relevance
            ):
                break  # relevance fell off a cliff relative to the last EMITTED result -- stop
            if relevance is not None:
                prev_relevance = relevance
            results.append(
                {
                    "name": e["name"],
                    "file": e["file"],
                    "description": entry_description(e).strip(),
                    # COR-8: emit the REAL penalized fused score -- exactly the value
                    # `penalized` (post-invalidation-penalty, post-graph-discount,
                    # post-salience when RET-5's flag is on) sorted on -- NOT fabricated
                    # 1/rank noise. Telemetry and threshold calibration inherit this number
                    # verbatim, so it must be the actual ranking signal, not a proxy that
                    # just happens to be monotone in emission order by construction. `rank`
                    # is the separate, explicit 1-based EMISSION rank (position in `results`,
                    # not `penalized` index -- "old"/deleted entries are skipped above and
                    # must not leave gaps in the emitted rank sequence).
                    "score": round(float(_adj_score), 6),
                    "rank": len(results) + 1,
                    "backend": backend,
                    # Injection provenance (GRA-1) — ALWAYS present so downstream code never
                    # branches on key existence: "graph" = surfaced by 1-hop expansion,
                    # "rank" = organic fusion. format_results renders "graph" as " (linked)"
                    # so a user reading the injected block can see WHY a line is there.
                    "via": "graph" if i in graph_injected else "rank",
                    # Typed-edge annotation (GRA-4) — ALWAYS present ("" when none), same
                    # no-key-branching convention as "via": "superseded by <successor>"
                    # names why the line ranks below its successor; "contradicts <name> —
                    # verify" flags a live conflict without demoting either side. Absent
                    # links cache -> _typed_relation_maps returned empty maps -> "".
                    "note": _typed_note(i, superseded_by, contradicted_by),
                    # RET-5: the salience breakdown behind THIS result's score — ALWAYS
                    # present (None when the flag is off, or for an entry `_apply_salience`
                    # never scored, e.g. a pure graph injection) so a consumer can inspect
                    # the components without branching on the flag itself (COR-8 true-score
                    # discipline: no fabricated numbers, an honest None beats a fake 0).
                    "salience": salience_components.get(i),
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
        # Typed-edge annotation (GRA-4): the one-line supersession/conflict note rides on
        # the same pointer line — bounded upstream (_typed_note caps names) and by the
        # overall max_chars truncation below, so it can never blow the injection budget.
        note = f" [{r['note']}]" if r.get("note") else ""
        lines.append(f"  • {r['name']} ({r['file']}) — {desc}{marker}{note}")
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


def git_recent_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> Optional[str]:
    """SessionStart producer: a one-block digest of recently-captured memories.

    Window via ``HIPPO_RECENT_DAYS`` (default 14). Self-suppresses when nothing is recent.
    The untrusted-corpus gate (SEC-1) is enforced once, upstream, by ``session_start``'s
    ``build_context`` short-circuit — no producer re-checks it (one gate boundary, no extra
    per-producer git call on the trusted hot path). ``ctx`` (LIF-6's shared per-run
    ``RunContext``) is unused here — declared only so every producer in ``PRODUCERS``
    shares ONE call shape.
    """
    try:
        days = float(os.environ.get("HIPPO_RECENT_DAYS", "14") or 14)
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
