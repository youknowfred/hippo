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
from typing import List, Optional, Tuple

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
_TAG_RE = re.compile(r"<[^>]+>")
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


def clean_query(raw: str) -> str:
    """Normalize a raw prompt into a recall query, or "" to SKIP recall (no model load).

    Strips harness envelopes (``<task-notification>`` / ``<system-reminder>`` tool-use blobs,
    fenced code blocks, stray XML-ish tags) and returns "" when what remains carries no
    retrieval intent (a terse continuation like "?"/"continue", or fewer than
    ``_MIN_CONTENT_TOKENS`` content tokens). Pure; never raises — any failure degrades to the
    raw prompt (recall on the un-cleaned text rather than skip).
    """
    try:
        if not raw or not raw.strip():
            return ""
        text = _ENVELOPE_BLOCK_RE.sub(" ", raw)
        text = _FENCE_BLOCK_RE.sub(" ", text)
        text = _TAG_RE.sub(" ", text)
        text = " ".join(text.split()).strip()
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
def _bm25_rank(query_tokens: List[str], entries: List[dict]) -> List[int]:
    """Indices of docs that SHARE >=1 query token, ordered by descending BM25 score.

    The match set (token-overlap) is the right filter — NOT ``score > 0``: BM25 IDF goes
    NEGATIVE for a term that appears in most/all docs (e.g. a tiny corpus, or a common
    token), so a genuinely-matching doc can score below an unrelated doc's 0. Filtering on
    overlap keeps matched docs (even negative-scored) and drops only the truly-unrelated.
    """
    if not query_tokens or not entries:
        return []
    try:
        try:
            from rank_bm25 import BM25Okapi  # the pinned venv dep (full-fidelity path)
        except ImportError:  # bare python3 pre-bootstrap (ONB-2): score-identical fallback
            from ._vendor.bm25 import BM25Okapi

        corpus = [e.get("tokens") or [] for e in entries]
        bm25 = BM25Okapi(corpus)
        scores = bm25.get_scores(query_tokens)
    except Exception:
        return []
    qset = set(query_tokens)
    matched = [i for i in range(len(entries)) if qset.intersection(corpus[i])]
    matched.sort(key=lambda i: scores[i], reverse=True)
    return matched


def _dense_rank(query: str, index: LoadedIndex) -> List[int]:
    """Indices ordered by descending cosine similarity, or [] if dense is unavailable.

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


def _rrf_fuse(rankings: List[List[int]], k: int = _RRF_K) -> List[Tuple[int, float]]:
    """Reciprocal Rank Fusion of several rank lists -> ``[(index, fused_score), ...]``, best first.

    Returns the SCORE alongside the index (not just an ordering) so a caller can apply a
    post-fusion, pre-cut soft-invalidation penalty that can actually change which indices
    survive into the top-k — not just relabel them after the cut already happened. The sort
    key (score alone) is UNCHANGED from before this widening — deliberately no explicit
    tie-break was added here: an adversarial review of an earlier draft that DID add one
    (``(-score, index)``) found it silently flips top-k SET MEMBERSHIP on real corpus ties,
    independent of any invalidation penalty — an unrelated behavior change this widening must
    not smuggle in. This function has exactly one caller (``recall()``).
    """
    scores: dict = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)
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
        if memory_dir:
            entries = [
                _drift_patch(e, memory_dir) if i < _MAX_DRIFT_CHECKS else e
                for i, e in enumerate(entries)
            ]

        q_tokens = tokenize(query)
        bm25 = _bm25_rank(q_tokens, entries)
        dense = _dense_rank(query, idx)

        rankings = [r for r in (dense, bm25) if r]
        if not rankings:
            return []
        fused = _rrf_fuse(rankings)  # [(idx, score), ...] desc by fused score
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
