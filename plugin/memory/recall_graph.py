"""Recall's graph plane: soft-invalidation state, the COR-4 mid-session drift patch,
and GRA-6's persisted-edge readers — GRA-4 typed relations and GRA-1/RET-13/GRF-2
1-hop neighbor expansion. Decomposed out of ``recall.py`` as pure code motion; every
symbol stays importable at ``memory.recall.<name>`` via the façade's explicit
re-exports."""

from __future__ import annotations

import math
import os
from typing import Dict, List, Optional, Tuple

from .build_index import _hash, bm25_terms, memory_doc_text, tokenize

# The soft-invalidation horizon and its classifier live in ``staleness`` (its terminal-state
# count needs them, and a foundation module may not import recall — RWY-5); re-imported here
# so ``memory.recall_graph.<name>`` and the façade's ``memory.recall.<name>`` keep resolving.
from .staleness import _INVALIDATION_RECENT_DAYS, _invalidation_state  # noqa: F401

# Soft-invalidation (Tier 3, graceful decay) — "recent" halves the fused score BEFORE the
# top-k cut (real demotion, can fall out of top-k); "old" is filtered from DISPLAY only,
# after the cut (the memory stays fully in the corpus/index, never excluded from ranking).
_INVALIDATION_PENALTY = 0.5

# Typed-edge demotion (GRA-4) — a memory that is the TARGET of a live supersedes edge
# (some OTHER memory in the index declares `supersedes: [it]`) has its fused score halved
# BEFORE the top-k cut, exactly the invalidation penalty's bounded-multiplier style: real
# demotion (it can fall out of top-k, and its successor outranks it), never a hard exclude
# (a wide-k query still surfaces it, annotated). `contradicts` targets are deliberately
# NOT demoted — a contradiction means "one of these is wrong, VERIFY", not "this one lost"
# — they carry a conflict annotation only. `refines`/`derives-from` carry no penalty and no
# annotation either (unlike supersedes/contradicts, being refined-by or derived-from isn't
# evidence the target is wrong or disputed) — RET-13 (owner-directed) instead seeds them
# into 1-hop graph expansion below, the same discounted-candidate treatment an untyped
# [[wikilink]] neighbor gets, on the theory that a memory a top hit refines/derives from is
# usually also relevant. Typed edges reach this hot path via GRA-6's persisted links.json
# ONLY (one small-JSON read, shared with 1-hop expansion) — cache absent degrades to
# no-demotion/no-annotation/no-expansion, never a corpus read per prompt.
_SUPERSEDED_PENALTY = 0.5


# 1-hop graph expansion (GRA-1) — the first load-bearing graph READ. After fusion +
# invalidation penalties, the top-_GRAPH_SEEDS entries seed a 1-hop neighbor pull from the
# persisted edge list (GRA-6's links.json, one small-JSON read, no corpus scan). Neighbors
# are injected at _NEIGHBOR_DISCOUNT x their best seed's penalized score and COMPETE for
# top-k — no reserved slots, so expansion can only surface a linked memory when its
# discounted score actually beats an organic candidate, never by displacing one for free.
_GRAPH_SEEDS = 3  # override: HIPPO_GRAPH_SEEDS (0 disables expansion entirely)
_NEIGHBOR_DISCOUNT = 0.5

# HOT-9: hub damping. A neighbor's injected score is divided by f(in_degree / median), where
# in-degree counts the edges expansion itself traverses INTO a node (wikilinks, plus typed
# refines / derives-from), and the median is the corpus's, taken once per edge-list load. f is 1
# at or below the median (no boost for a sparse node, no change for a typical one) and grows
# sub-linearly above it, so a memory half the corpus links to stops riding every expansion.
# A corpus whose median in-degree is 0 divides by 1. HIPPO_HUB_DAMPING=0 turns it off, and off
# is byte-identical to before (no division happens at all).
_HUB_DAMPING_FN = "sqrt"  # "sqrt" or "log" (1 + ln x); chosen from the three-corpus replay
# Drop reasons the recall autopsy counts (MSR-4 drop_log): damped out of the final cut, and
# suppressed because it already entered by expansion earlier in this session.
HUB_DAMPED = "hub_damped"
EXPANDED_THIS_SESSION = "expanded_this_session"


def _hub_damping_on() -> bool:
    raw = os.environ.get("HIPPO_HUB_DAMPING")
    if raw is None or not raw.strip():
        return True
    return raw.strip().lower() not in ("0", "false", "off", "no")


def _in_degree(rec: dict) -> int:
    typed_in = rec.get("typed_in") or {}
    return (
        len(rec.get("in") or ())
        + len(typed_in.get("refines") or ())
        + len(typed_in.get("derives-from") or ())
    )


def _median_in_degree(edges: dict) -> float:
    """The corpus median in-degree, at least 1 (the denominator for a median of 0)."""
    degs = sorted(_in_degree(rec) for rec in edges.values())
    if not degs:
        return 1.0
    mid = len(degs) // 2
    median = degs[mid] if len(degs) % 2 else (degs[mid - 1] + degs[mid]) / 2.0
    return float(median) if median > 0 else 1.0


def _hub_divisor(in_degree: int, median: float) -> float:
    x = in_degree / median
    if x <= 1.0:
        return 1.0
    return math.sqrt(x) if _HUB_DAMPING_FN == "sqrt" else 1.0 + math.log(x)


def _expansion_drops(notes: dict, final: set, cut: Optional[float]) -> List[Tuple[int, str, float]]:
    """``(entry index, reason, score)`` for the two HOT-9 drops, off ``_expand_neighbors``'
    ``notes``: a damped neighbor absent from the final results whose UNDAMPED score would have
    cleared the final cut (``cut`` is the lowest final score, None when results were short of
    k), and a suppressed already-expanded neighbor absent from the final results."""
    out: List[Tuple[int, str, float]] = []
    for j, (undamped, damped) in sorted((notes.get("damped") or {}).items()):
        if j not in final and (cut is None or undamped >= cut):
            out.append((j, HUB_DAMPED, damped))
    for j, score in sorted((notes.get("expanded_before") or {}).items()):
        if j not in final:
            out.append((j, EXPANDED_THIS_SESSION, score))
    return out


# HOT-15: the drift check's per-process memo. A long-lived process (the warm recall server,
# the MCP server) re-ran the check's frontmatter parse for up to 200 unchanged files on every
# prompt: about a quarter of a warm served call on the field corpus. The parse result is kept
# per file under the open file's own stat signature; any write to the file changes its ctime
# (which no tool can set back) and so misses the memo. A one-shot hook process pays one fstat
# per file on top of the read it already did.
_DRIFT_MEMO: Dict[Tuple[str, str], tuple] = {}
_DRIFT_MEMO_MAX = 8192


def _file_sig(fh) -> Optional[tuple]:
    try:
        st = os.fstat(fh.fileno())
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino, st.st_dev)


def _remember_drift(key: Tuple[str, str], value: tuple) -> None:
    if value[0] is None:
        return
    if len(_DRIFT_MEMO) >= _DRIFT_MEMO_MAX:
        _DRIFT_MEMO.clear()
    _DRIFT_MEMO[key] = value


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
    if entry.get("digest"):
        return entry  # HOT-11: a folded section is not its file's description; it heals at refresh
    try:
        path = os.path.join(memory_dir, entry["file"])
        with open(path, "r", encoding="utf-8") as fh:
            sig = _file_sig(fh)
            hit = _DRIFT_MEMO.get((path, entry["name"]))
            if hit is not None and hit[0] == sig:
                doc_text, fresh_hash = hit[1], hit[2]
            else:
                doc_text = memory_doc_text(entry["name"], fh.read())
                fresh_hash = _hash(doc_text)
                _remember_drift((path, entry["name"]), (sig, doc_text, fresh_hash))
        if fresh_hash == entry.get("hash"):
            return entry
        patched = dict(entry)
        patched["tokens"] = bm25_terms(tokenize(doc_text))
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

        # HOT-15: a long-lived process re-parsed links.json and rebuilt every edge set on
        # each prompt. Kept per index dir under the file's stat signature and the dream
        # admission view (``links_cache``'s own cache key); the hot path only reads it.
        from .links_cache import _LINKS_CACHE_NAME, dream_edges_admitted

        try:
            st = os.stat(os.path.join(index_dir, _LINKS_CACHE_NAME))
            sig = (st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino, dream_edges_admitted())
        except OSError:
            sig = None
        hit = _EDGES_MEMO.get(index_dir)
        if sig is not None and hit is not None and hit[0] == sig:
            return hit[1]
        edges = load_edges(index_dir)
        _EDGES_MEMO.clear()  # one corpus per process in practice; never grows
        if sig is not None and edges is not None:
            _EDGES_MEMO[index_dir] = (sig, edges)
        return edges
    except Exception:
        return None


_EDGES_MEMO: Dict[str, tuple] = {}


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
    draft_seeds: Optional[set] = None,
    *,
    expanded_before: Optional[set] = None,
    notes: Optional[dict] = None,
) -> Tuple[List[Tuple[int, float, Optional[str]]], set]:
    """1-hop neighbor expansion (GRA-1): inject linked memories at a discounted score.

    HOT-9: each neighbor's injected score is hub-damped (see ``_hub_divisor``), and a
    non-seed neighbor whose NAME is in ``expanded_before`` (the names that already entered
    by expansion earlier in this session; None outside a hook session) is neither injected
    nor endorsed — it keeps only its organic standing. ``notes`` (an optional out-param)
    collects ``damped`` ``{j: (undamped, damped)}`` and ``expanded_before`` ``{j: score}`` for
    the drop autopsy (``_expansion_drops``).

    Takes the ALREADY-penalized candidate list (post-fusion, post-invalidation re-sort),
    seeds on its top-N entries, and unions their outbound+inbound 1-hop neighbor stems from
    GRA-6's persisted edge list (``edges`` — the ``_load_hot_edges`` result ``recall()``
    loaded once, links.json only, the hot path's single extra small-JSON read) — RET-13:
    now including typed ``refines``/``derives-from`` targets (both directions) alongside
    the untyped ``[[wikilink]]`` adjacency, so a memory related only via one of those
    frontmatter relations (no body wikilink) is reachable too; ``supersedes``/
    ``contradicts`` are deliberately excluded from this traversal set, unchanged, since
    they keep their own dedicated penalty/annotation handling. Injection rules, in order:

      - stems absent from the index are dropped (a link can outlive its target);
      - the seeds themselves are never INJECTED (a seed is already ranked as well as it
        can be) — but a seed that is another seed's 1-hop neighbor still joins the
        ENDORSED set (GRF-2): the graph vouches for it, so the knee and MMR exemptions
        apply to it exactly as to a non-seed neighbor;
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

    Returns ``(re-sorted list, {injected entry indices}, {endorsed entry indices})``.
    ``graph_injected`` (the second element) is the REPLACED set — entries whose tuple now
    carries a discounted seed score — and stamps "via" provenance. ``graph_endorsed`` (the
    third, a superset) is every resolvable seed-neighbor whether or not injection beat its
    organic score: the GRA-1 dense-side finding (RET-8's multi-hop category) is that under
    dense a neighbor usually ALREADY has an organic rank (cosine orders the whole corpus),
    so injection declines — but the entry is still graph-endorsed, and the emission loop's
    knee must judge it by that endorsement, not by its deliberately-weak organic rank.
    Never raises; ANY failure — no edges loaded (caller-supplied in-memory index with no
    dirs: eval self_recall probes, hermetic LoadedIndex tests; absent/corrupt links.json),
    junk env — returns the input untouched, so expansion can only ever be additive, never
    a new degradation mode.
    """
    try:
        if not edges or not penalized:
            return penalized, set(), set()
        seeds_n = _graph_seed_count()
        if seeds_n <= 0:
            return penalized, set(), set()
        seeds = penalized[:seeds_n]
        seed_idxs = {i for i, _score, _state in seeds}
        # Entry "name" == file stem == the edge list's node identity (both come from the
        # same os.path.splitext(basename) in compute_corpus / LinkGraph).
        name_to_idx = {e.get("name"): j for j, e in enumerate(entries)}
        organic_score = {i: score for i, score, _state in penalized}
        injected: dict = {}  # entry idx -> best discounted seed score
        median = _median_in_degree(edges) if _hub_damping_on() else None  # HOT-9: once per load
        endorsed_seeds: set = set()  # seeds that are other seeds' 1-hop neighbors (GRF-2)
        for si, sscore, _sstate in seeds:
            rec = edges.get(entries[si].get("name"))
            if not rec:
                continue
            # RET-13 (owner-directed): a memory a seed REFINES or DERIVES-FROM is usually
            # also relevant to the same query in practice, so those typed relations now
            # seed the SAME 1-hop pull as untyped [[wikilinks]] -- widening the traversal
            # SOURCE only. supersedes/contradicts keep their existing dedicated handling
            # (penalty/annotation below) and are deliberately NOT added here, unchanged.
            #
            # ONE exception: a DRAFT seed's own outbound refines/derives-from are BOTH
            # excluded. A dream-generated draft declares `derives-from: [children]` on
            # ITSELF (DRM-6) — letting that self-declared lineage seed expansion would let
            # an unproven draft manufacture apparent corroboration from its own children
            # regardless of query relevance, defeating the draft-quarantine guard ("a draft
            # must never answer alone" — recall()'s all-draft-results collapse). The same
            # risk applies to `refines`: nothing stops a hand- or agent-authored draft from
            # declaring `refines: [x]` on itself the exact same way — dream.py's auto-apply
            # firewall only blocks ITS OWN automated refines-writing pass from touching
            # drafts, it cannot block a hand-authored frontmatter line — so both outbound
            # typed relations get the same exclusion, not just derives-from. The REVERSE
            # direction (a non-draft seed pulling in a draft that refines/derives FROM it)
            # is unaffected — that draft still enters at the standard discount and still
            # can't answer alone (the quarantine lives in the emission-time all-draft
            # collapse, not here). ``draft_seeds`` is a caller-precomputed index set (never
            # a fresh ``.get("confidence")`` read here) — confidence reads stay confined to
            # recall() itself, a closed-set AST invariant a dedicated test pins.
            typed_out = rec.get("typed_out") or {}
            typed_in = rec.get("typed_in") or {}
            seed_is_draft = bool(draft_seeds and si in draft_seeds)
            refines_out = set() if seed_is_draft else typed_out.get("refines", set())
            derives_out = set() if seed_is_draft else typed_out.get("derives-from", set())
            neighbor_stems = (
                rec.get("out", set())
                | rec.get("in", set())
                | refines_out
                | derives_out
                | typed_in.get("refines", set())
                | typed_in.get("derives-from", set())
            )
            # SORTED iteration (GRF-2, found by MSR-1's pass^k probe): two siblings of
            # one seed inject at the IDENTICAL discounted score, and the emission
            # sort is stable — so their relative rank inherits INSERTION order, which
            # for a str-set comprehension is per-process hash order. n=2-era fixtures
            # never saw it (both siblings were any-of expected, a swap moved nothing);
            # the grown multi-hop set pins single stems and caught the flake. Sorting
            # here makes tie order deterministic (seed rank, then stem name) at a cost
            # of O(deg log deg) on a handful of neighbors.
            for stem in sorted(neighbor_stems):
                j = name_to_idx.get(stem)
                if j is None:
                    continue
                if j in seed_idxs:
                    # GRF-2: a seed that is ANOTHER seed's 1-hop neighbor is ENDORSED —
                    # the graph vouches for it exactly as it vouches for a non-seed
                    # neighbor — but never score-injected (a top seed already ranks as
                    # well as it can; there is no better score to give it). Endorsement
                    # is what exempts it from the knee and the MMR diversity penalty:
                    # two linked cluster members ranking top-of-list is the graph
                    # AGREEING with the ranking, and MMR must not punish the pair for
                    # resembling each other (the mixed-mode 0.0 leg the T9 re-measure
                    # attributed to exactly this displacement).
                    if j != si:
                        endorsed_seeds.add(j)
                    continue
                cand = sscore * _NEIGHBOR_DISCOUNT
                if expanded_before and stem in expanded_before:
                    if notes is not None:  # HOT-9: once per session by expansion
                        prior = notes.setdefault("expanded_before", {})
                        prior[j] = max(prior.get(j, float("-inf")), cand)
                    continue
                if median is not None:
                    div = _hub_divisor(_in_degree(edges.get(stem) or {}), median)
                    if div > 1.0:
                        if notes is not None:
                            was = notes.setdefault("damped", {}).get(j)
                            if was is None or cand > was[0]:
                                notes["damped"][j] = (cand, cand / div)
                        cand = cand / div
                if cand > injected.get(j, float("-inf")):
                    injected[j] = cand
        return _apply_injection(penalized, entries, injected, endorsed_seeds, superseded, organic_score)
    except Exception:
        return penalized, set(), set()


def _apply_injection(
    penalized: List[Tuple[int, float, Optional[str]]],
    entries: List[dict],
    injected: dict,
    endorsed_seeds: set,
    superseded: Optional[Dict[int, List[str]]],
    organic_score: Dict[int, float],
) -> Tuple[List[Tuple[int, float, Optional[str]]], set, set]:
    """The shared tail of both graph lanes: penalize each injected candidate exactly as an
    organic one ("recent" invalidation halves it, "old" rides through as state, a live
    supersedes target is halved), keep an organic tuple that already scores at least as
    well, and re-sort. Returns ``(list, replaced indices, endorsed indices)``."""
    if not injected and not endorsed_seeds:
        return penalized, set(), set()
    # Every resolvable seed-neighbor, organic-kept or not — plus seed-linked seeds.
    endorsed = set(injected) | endorsed_seeds
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
        return penalized, set(), endorsed
    expanded = [t for t in penalized if t[0] not in replace]
    expanded.extend((j, adj, state) for j, (adj, state) in replace.items())
    expanded.sort(key=lambda triple: triple[1], reverse=True)
    return expanded, set(replace), endorsed


# --------------------------------------------------------------------------- #
# HCR-5: the personalized-PageRank graph lane (HippoRAG-2 style), opt-in beside GRA-1.
#
# GRA-1 sees only the direct neighbors of its seeds. This lane walks the same edge set
# (wikilinks both ways, typed refines / derives-from both ways, never supersedes /
# contradicts) as a random walk that restarts at the seeds, so a memory two hops out — or
# one reached from SEVERAL seeds at once — collects mass the 1-hop pull cannot see.
#
#   - Personalization: the seeds' fused, penalty-adjusted scores (the same top-N seeds
#     GRA-1 uses, HIPPO_GRAPH_SEEDS; 0 disables this lane too), normalized to sum 1.
#   - Graph: GRA-6's links.json via the ONE ``_load_hot_edges`` read recall() already did;
#     no edges -> no expansion, exactly as GRA-1. Turned into CSR arrays once per edge-list
#     object (memoized; the warm server pays it once per links.json change).
#   - Walk: numpy power iteration, damping _PPR_DAMPING, over the seeds' _PPR_HOPS-hop ball
#     (mass that walks out of the ball is dropped — truncated PPR; nodes further out could
#     never win a slot anyway). This keeps the cost proportional to the neighborhood, not
#     the corpus: well under 1 ms at 5k nodes (a test pins the budget).
#   - Draft guard (RET-13 equivalence): a draft's OWN outbound refines / derives-from are
#     not walkable, so a draft cannot manufacture corroboration from lineage it declared on
#     itself; the reverse direction (a non-draft pulling in a draft that derives from it)
#     walks as before. ``draft_seeds`` is recall()'s precomputed draft index set.
#   - Candidates: the top _PPR_ENDORSE_MAX non-seed nodes by mass (at least _PPR_MIN_REL
#     of the strongest) are ENDORSED, and each is injected at _NEIGHBOR_DISCOUNT x the top
#     seed's score x (its mass / the strongest mass) — so the strongest graph candidate
#     enters exactly where GRA-1's best 1-hop neighbor would, and the rest scale down by
#     mass. They then go through ``_apply_injection`` — the same compete-for-top-k rule,
#     invalidation and supersede penalties, and organic-score-wins tie rule as GRA-1; no
#     reserved slots. Hub damping (HOT-9) and the once-per-session suppression apply as in
#     GRA-1, and a seed adjacent to another seed is endorsed (GRF-2).
#
# Opt-in (HIPPO_GRAPH_PPR, falsy-set convention); off, recall never calls this and stays
# byte-identical. Replacing GRA-1 is an owner decision on the committed A/B evidence.
# --------------------------------------------------------------------------- #
_PPR_DAMPING = 0.85
_PPR_HOPS = 3
_PPR_MAX_ITERS = 60
_PPR_TOL = 1e-6  # L1 change in the mass vector; total mass is at most 1
_PPR_BALL_MAX = 2048  # a hop that would grow the ball past this is not taken
_PPR_ENDORSE_MAX = 10
_PPR_MIN_REL = 0.1
_PPR_MEMO: list = []  # [(edges object, graph)] — holds the object, so identity is safe


def _graph_ppr_enabled() -> bool:
    """True only when ``HIPPO_GRAPH_PPR`` is explicitly truthy — DEFAULT OFF, the same
    falsy-set convention as ``_rerank_enabled()``."""
    raw = os.environ.get("HIPPO_GRAPH_PPR", "").strip()
    return raw not in ("", "0", "false", "False")


def _ppr_graph(edges: dict) -> dict:
    """CSR arrays for the walk, built once per edge-list object.

    Node ids follow sorted stem order (deterministic across processes). Each directed
    pair ``u -> v`` is one entry (set union, as GRA-1's neighbor set); ``typed_only``
    marks a pair that exists ONLY because ``u`` declared refines / derives-from toward
    ``v`` — the pairs the draft guard removes when ``u`` is a draft."""
    if _PPR_MEMO and _PPR_MEMO[0][0] is edges:
        return _PPR_MEMO[0][1]
    import numpy as np

    stems = set(edges)
    for rec in edges.values():
        to, ti = rec.get("typed_out") or {}, rec.get("typed_in") or {}
        stems |= set(rec.get("out") or ()) | set(rec.get("in") or ())
        for rel in ("refines", "derives-from"):
            stems |= set(to.get(rel) or ()) | set(ti.get(rel) or ())
    names = sorted(stems)
    node = {s: n for n, s in enumerate(names)}
    src: List[int] = []
    dst: List[int] = []
    typed_only: List[bool] = []
    for u, name in enumerate(names):
        rec = edges.get(name)
        if not rec:
            continue
        to, ti = rec.get("typed_out") or {}, rec.get("typed_in") or {}
        base = (
            set(rec.get("out") or ()) | set(rec.get("in") or ())
            | set(ti.get("refines") or ()) | set(ti.get("derives-from") or ())
        )
        own = set(to.get("refines") or ()) | set(to.get("derives-from") or ())
        for v in sorted(base | own):
            if v == name:
                continue
            src.append(u)
            dst.append(node[v])
            typed_only.append(v not in base)
    n = len(names)
    src_a = np.asarray(src, dtype=np.int64)
    flag = np.asarray(typed_only, dtype=bool)
    graph = {
        "names": names,
        "node": node,
        "indptr": np.concatenate(([0], np.cumsum(np.bincount(src_a, minlength=n)))).astype(np.int64),
        "src": src_a,
        "dst": np.asarray(dst, dtype=np.int64),
        "typed_only": flag,
        "outdeg": np.bincount(src_a, minlength=n).astype(np.float64),
        "typed_only_deg": np.bincount(src_a[flag], minlength=n).astype(np.float64),
    }
    _PPR_MEMO.clear()
    _PPR_MEMO.append((edges, graph))
    return graph


def _csr_edges(indptr, nodes):
    """Positions of every CSR entry whose source is in ``nodes`` (an int array)."""
    import numpy as np

    starts = indptr[nodes]
    lens = indptr[nodes + 1] - starts
    total = int(lens.sum())
    if not total:
        return np.zeros(0, dtype=np.int64)
    return np.repeat(starts - (np.cumsum(lens) - lens), lens) + np.arange(total)


def _ppr_mass(graph: dict, seed_nodes, seed_weights, draft_nodes) -> Tuple[object, object]:
    """``(ball node ids, PPR mass per ball node)`` for one personalization.

    Power iteration ``x <- (1 - d) p + d P^T x`` over the seeds' ``_PPR_HOPS``-hop ball,
    with the draft guard applied to the walkable pairs and each step divided by the
    node's full (draft-adjusted) out-degree, so mass leaving the ball is dropped."""
    import numpy as np

    indptr, src, dst = graph["indptr"], graph["src"], graph["dst"]
    n = len(graph["names"])
    is_draft = np.zeros(n, dtype=bool)
    if draft_nodes:
        is_draft[list(draft_nodes)] = True
    blocked = graph["typed_only"] & is_draft[src] if draft_nodes else None

    def _walkable(pos):
        return pos if blocked is None else pos[~blocked[pos]]

    in_ball = np.zeros(n, dtype=bool)
    frontier = np.unique(np.asarray(seed_nodes, dtype=np.int64))
    in_ball[frontier] = True
    size = frontier.size
    for _ in range(_PPR_HOPS):
        nxt = dst[_walkable(_csr_edges(indptr, frontier))]
        nxt = np.unique(nxt[~in_ball[nxt]])
        if not nxt.size or size + nxt.size > _PPR_BALL_MAX:
            break
        in_ball[nxt] = True
        size += nxt.size
        frontier = nxt
    ball = np.flatnonzero(in_ball)
    pos = _walkable(_csr_edges(indptr, ball))
    pos = pos[in_ball[dst[pos]]]
    deg = graph["outdeg"] - (graph["typed_only_deg"] * is_draft if draft_nodes else 0.0)
    ls = np.searchsorted(ball, src[pos])
    ld = np.searchsorted(ball, dst[pos])
    w = 1.0 / deg[src[pos]]
    m = ball.size
    p = np.zeros(m)
    np.add.at(p, np.searchsorted(ball, np.asarray(seed_nodes, dtype=np.int64)), seed_weights)
    restart = (1.0 - _PPR_DAMPING) * p
    x = p
    for _ in range(_PPR_MAX_ITERS):
        nx = restart + _PPR_DAMPING * np.bincount(ld, weights=x[ls] * w, minlength=m)
        if np.abs(nx - x).sum() < _PPR_TOL:
            x = nx
            break
        x = nx
    return ball, x


def _ppr_expand(
    penalized: List[Tuple[int, float, Optional[str]]],
    entries: List[dict],
    edges: Optional[dict],
    superseded: Optional[Dict[int, List[str]]] = None,
    draft_seeds: Optional[set] = None,
    *,
    expanded_before: Optional[set] = None,
    notes: Optional[dict] = None,
) -> Tuple[List[Tuple[int, float, Optional[str]]], set, set]:
    """The PPR lane — ``_expand_neighbors``' exact signature and return shape (see the
    section comment above). Never raises; any failure returns the input untouched."""
    try:
        if not edges or not penalized:
            return penalized, set(), set()
        seeds_n = _graph_seed_count()
        if seeds_n <= 0:
            return penalized, set(), set()
        import numpy as np

        graph = _ppr_graph(edges)
        node = graph["node"]
        seeds = [
            (node[entries[i].get("name")], max(score, 0.0), i)
            for i, score, _state in penalized[:seeds_n]
            if entries[i].get("name") in node
        ]
        if not seeds:
            return penalized, set(), set()
        total = sum(s for _n, s, _i in seeds)
        weights = [s / total if total > 0 else 1.0 / len(seeds) for _n, s, _i in seeds]
        draft_nodes = {
            node[entries[i].get("name")]
            for i in (draft_seeds or ())
            if 0 <= i < len(entries) and entries[i].get("name") in node
        }
        ball, mass = _ppr_mass(graph, [nd for nd, _s, _i in seeds], weights, draft_nodes)
        names = graph["names"]
        name_to_idx = {e.get("name"): j for j, e in enumerate(entries)}
        seed_idxs = {i for _n, _s, i in seeds} | {i for i, _s, _st in penalized[:seeds_n]}

        # GRF-2: a seed one walkable step from another seed is endorsed, never injected.
        endorsed_seeds: set = set()
        seed_node_to_idx = {nd: i for nd, _s, i in seeds}
        for nd, _s, i in seeds:
            for pos in range(int(graph["indptr"][nd]), int(graph["indptr"][nd + 1])):
                v = int(graph["dst"][pos])
                if v in seed_node_to_idx and v != nd:
                    if graph["typed_only"][pos] and nd in draft_nodes:
                        continue
                    endorsed_seeds.add(seed_node_to_idx[v])

        # Best mass first; ties by node id, which is sorted-stem order (deterministic).
        order = np.lexsort((ball, -mass)).tolist()
        fresh: List[Tuple[float, str, int]] = []
        before: List[Tuple[float, int]] = []  # HOT-9: already entered by expansion
        for k in order:
            mval = float(mass[k])
            if mval <= 0.0 or len(fresh) >= _PPR_ENDORSE_MAX:
                break
            stem = names[int(ball[k])]
            j = name_to_idx.get(stem)
            if j is None or j in seed_idxs:
                continue
            if expanded_before and stem in expanded_before:
                before.append((mval, j))
                continue
            fresh.append((mval, stem, j))
        top_score = penalized[0][1]
        best = fresh[0][0] if fresh else (before[0][0] if before else 1.0)
        if notes is not None:
            for mval, j in before:
                prior = notes.setdefault("expanded_before", {})
                cand = top_score * _NEIGHBOR_DISCOUNT * min(1.0, mval / best)
                prior[j] = max(prior.get(j, float("-inf")), cand)
        median = None
        if _hub_damping_on():
            median = graph.get("median")
            if median is None:
                median = graph["median"] = _median_in_degree(edges)
        injected: dict = {}
        for mval, stem, j in fresh:
            if mval < _PPR_MIN_REL * best:
                break
            cand = top_score * _NEIGHBOR_DISCOUNT * (mval / best)
            if median is not None:
                div = _hub_divisor(_in_degree(edges.get(stem) or {}), median)
                if div > 1.0:
                    if notes is not None:
                        notes.setdefault("damped", {})[j] = (cand, cand / div)
                    cand = cand / div
            injected[j] = cand
        organic_score = {i: score for i, score, _state in penalized}
        return _apply_injection(penalized, entries, injected, endorsed_seeds, superseded, organic_score)
    except Exception:
        return penalized, set(), set()
