"""Recall's graph plane: soft-invalidation state, the COR-4 mid-session drift patch,
and GRA-6's persisted-edge readers — GRA-4 typed relations and GRA-1/RET-13/GRF-2
1-hop neighbor expansion. Decomposed out of ``recall.py`` as pure code motion; every
symbol stays importable at ``memory.recall.<name>`` via the façade's explicit
re-exports."""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from .build_index import _hash, bm25_terms, memory_doc_text, tokenize

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
    draft_seeds: Optional[set] = None,
) -> Tuple[List[Tuple[int, float, Optional[str]]], set]:
    """1-hop neighbor expansion (GRA-1): inject linked memories at a discounted score.

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
                if cand > injected.get(j, float("-inf")):
                    injected[j] = cand
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
    except Exception:
        return penalized, set(), set()
