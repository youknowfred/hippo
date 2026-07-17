"""/dream discovery (DRM-1's core + DRM-5 reverse replay; decomposed out of ``dream.py``).

Corpus + firewalled graph views, mention detection (the completion kind), reward-gated
reverse-replay boosts, and ``discover`` — the read-only replay + graph-diff pass. Zero
writes to any memory file. Every name here re-exports via the ``dream`` façade.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Set, Tuple

from .dream_config import (
    _CANDIDATE_KINDS,
    _DEFAULT_MAX_SEEDS,
    _DEFAULT_PROBE_K,
    _DISTANCE_CUTOFF,
    _MIN_MENTION_CHARS,
    _REWARD_BOOST_RANK_CAP,
    _env_int,
    apply_eligible,
    cofire_theta,
    contradictions_enabled,
    max_apply_per_pass,
    reward_weight,
)
from .dream_contra import discover_contradictions
from .dream_ledgers import _new_pass_id, unaged_dream_pairs, unaged_generated_stems
from .links import LinkGraph, build_graph, normalize_slug
from .lint_floor import floor_memory_names
from .provenance import _iter_memory_files, parse_frontmatter
from .soak import soak_status
from .telemetry import default_telemetry_dir, read_usage_aggregates

# --------------------------------------------------------------------------- #
# Corpus + graph views
# --------------------------------------------------------------------------- #
def _corpus_texts(memory_dir: str) -> Dict[str, str]:
    """``{stem: full text}`` for every memory file; unreadable files skipped. Never raises."""
    texts: Dict[str, str] = {}
    try:
        for path in _iter_memory_files(memory_dir):
            stem = os.path.splitext(os.path.basename(path))[0]
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    texts[stem] = fh.read()
            except Exception:
                continue
    except Exception:
        return texts
    return texts


def _confidence_map(texts: Dict[str, str]) -> Dict[str, Optional[str]]:
    """Per-stem ``confidence`` tier (GOV-7; None = unset = user-asserted default)."""
    from .build_index import _extract_confidence

    out: Dict[str, Optional[str]] = {}
    for stem, text in texts.items():
        try:
            out[stem] = _extract_confidence(parse_frontmatter(text))
        except Exception:
            out[stem] = None
    return out


def _undirected_view(graph: LinkGraph, minus_pairs: Set[frozenset]) -> Dict[str, Set[str]]:
    """The undirected neighbor map candidate generation reads, with ``minus_pairs`` removed.

    This is THE firewalled source view (inv-DRM-firewall): un-aged dream pairs are invisible
    to worklist priority, distances, and bridge detection. Aged-in dream edges remain — they
    earned trust by surviving the window un-undone.
    """
    view: Dict[str, Set[str]] = {}
    for stem in graph.files:
        nbrs = set(graph.undirected_neighbors(stem))
        for other in list(nbrs):
            if frozenset((stem, other)) in minus_pairs:
                nbrs.discard(other)
        view[stem] = nbrs
    return view


def _distance(view: Dict[str, Set[str]], a: str, b: str, cutoff: int = _DISTANCE_CUTOFF) -> Optional[int]:
    """Undirected BFS distance a→b over ``view``, or None beyond ``cutoff``/disconnected."""
    if a == b:
        return 0
    if a not in view or b not in view:
        return None
    seen = {a}
    frontier = {a}
    for depth in range(1, cutoff + 1):
        nxt: Set[str] = set()
        for node in frontier:
            for nb in view.get(node, ()):
                if nb == b:
                    return depth
                if nb not in seen:
                    seen.add(nb)
                    nxt.add(nb)
        if not nxt:
            return None
        frontier = nxt
    return None


# --------------------------------------------------------------------------- #
# Mention detection (the completion kind)
# --------------------------------------------------------------------------- #
_WIKILINK_SPAN_RE = re.compile(r"\[\[[^\]\[]+?\]\]")


def _mention_regex(alias: str) -> Optional[re.Pattern]:
    """A word-bounded pattern matching ``alias`` with ``-``/``_``/whitespace-flexible joints.

    ``hippo-v090-measurement`` matches ``hippo_v090_measurement`` / ``hippo v090
    measurement`` too — the same separator-equivalence ``normalize_slug`` applies at resolve
    time. Aliases below ``_MIN_MENTION_CHARS`` or with a single segment return None (too
    generic to assert "the body names this memory"). The boundary lookarounds reject
    adjacent ``-``/``_`` as well as alnum, so a parent slug can never match INSIDE a longer
    child slug (``deploy-runbook`` must not fire on ``deploy-runbook-rollback``)."""
    slug = normalize_slug(alias)
    parts = [p for p in slug.split("-") if p]
    if len(slug) < _MIN_MENTION_CHARS or len(parts) < 2:
        return None
    joined = r"[-_\s]+".join(re.escape(p) for p in parts)
    try:
        return re.compile(
            r"(?<![A-Za-z0-9_-])" + joined + r"(?![A-Za-z0-9_-])", re.IGNORECASE
        )
    except re.error:
        return None


def _body_text(text: str) -> str:
    """The BODY of a memory file (frontmatter fence stripped); the whole text if unfenced.

    The completion scan reads bodies only — descriptions/names are dense keyword summaries
    whose vocabulary routinely overlaps siblings (and a child's own ``name:`` line always
    contains its parent's slug), so scanning frontmatter would manufacture false mentions.
    "Body-names-target" is the design's claim; the scan honors it literally."""
    lines = (text or "").split("\n")
    if not lines or lines[0].strip() != "---":
        return text or ""
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1:])
    return text or ""


def _body_without_wikilinks(text: str) -> str:
    """The BODY with every ``[[...]]`` span blanked — resolved links are edges already, and
    a dangling wikilink is handled by its own (fuzzy-resolution) path, not the prose scan."""
    return _WIKILINK_SPAN_RE.sub(" ", _body_text(text))


def _fuzzy_stem_match(raw_target: str, stems: List[str]) -> Optional[str]:
    """The ONE stem a dangling ``[[raw_target]]`` plausibly meant, or None.

    Conservative by design: the normalized raw target and the stem's slug must contain one
    another (either direction), the shorter side must still clear ``_MIN_MENTION_CHARS``,
    and EXACTLY ONE corpus stem may qualify — two claimants = ambiguous = no candidate
    (mirrors ``links``' ambiguity refusal)."""
    rslug = normalize_slug(raw_target)
    if len(rslug) < _MIN_MENTION_CHARS:
        return None
    hits: List[str] = []
    for stem in stems:
        sslug = normalize_slug(stem)
        if rslug == sslug:
            # An exact match would have resolved; unresolvable exact means ambiguity — skip.
            continue
        shorter = min(len(rslug), len(sslug))
        if shorter < _MIN_MENTION_CHARS:
            continue
        if rslug in sslug or sslug in rslug:
            hits.append(stem)
    return hits[0] if len(hits) == 1 else None


# --------------------------------------------------------------------------- #
# DRM-5 — reward-gated reverse replay (outcome-anchored edge boosts)
#
# Biological reverse replay propagates reward BACKWARD along the path that led to it, and
# is reward-GATED (Ambrose/Pfeiffer/Foster 2016 — no reward, no propagation). hippo's
# analogue: a memory whose injection was followed by a touch of one of its cited files in
# the same session carries a RECORDED outcome (``outcome.injection_hits``, the KPI-2 join);
# from each such memory the pass walks its authored lineage BACKWARD — the
# ``history.decision_chain`` closure, predecessor direction only (the declarer is the newer
# side, so ``typed_outbound`` supersedes/refines targets are the upstream steps that led
# here) — and promotes the replay priority / candidate ordering of that chain.
#
# The boost is DERIVED STATE, never a write path of its own (inv1): it feeds
#   - DRM-1's replay priority — boosted memories move to the FRONT of the replay worklist
#     (outcome-anchored traces are over-sampled, like under-connected ones), and
#   - DRM-2's cofire RANKING — a candidate touching a boosted memory sorts earlier among
#     eligible candidates under the per-pass cap (``reward_weight`` per hit).
# It NEVER changes apply eligibility (θ reads the raw cofire), never asserts a claim,
# never mutates a body — a pass with boosts and no eligible candidates writes nothing.
#
# Hard preconditions, all firewall-family:
#   - reward-gated: no recorded outcome → no boost (an empty outcome ledger is a no-op);
#   - the backward walk never crosses an UN-AGED dream edge (inv-DRM-firewall extends to
#     this round: a dream-applied refines edge cannot conduct reward before it ages in);
#   - floor memories and confidence:draft memories are never boosted (same endpoint
#     exclusions as candidate generation).
# Every boosted edge gets a ledger row under the DERIVED dream dir carrying its justifying
# decision_chain (provenance) — the acceptance-criteria audit surface.
# --------------------------------------------------------------------------- #
def reward_boosts(
    memory_dir: str,
    index_dir: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    *,
    exclude_stems: Optional[Set[str]] = None,
    unaged_pairs: Optional[Set[frozenset]] = None,
) -> dict:
    """Compute DRM-5 boosts. Read-only; never raises; empty maps when there is no outcome.

    Returns::

        {"outcome_memories": {stem: hits},          # the reward sources (recorded outcomes)
         "memories": {stem: boost},                  # replay-priority map (source ∪ upstream)
         "memory_sources": {stem: [outcome stems]},  # which outcome(s) justify each boost
         "edges": [{"edge": {"from", "relation", "to"}, "boost", "outcome_memory",
                    "hits", "decision_chain": [stems...]}, ...]}

    ``boost`` accumulates hit-session counts across outcome memories; one ledger row is
    emitted per (edge, outcome_memory) so every row carries exactly ITS justifying chain.
    """
    empty = {"outcome_memories": {}, "memories": {}, "memory_sources": {}, "edges": []}
    try:
        from .history import decision_chain
        from .outcome import injection_hits

        excluded = exclude_stems or set()
        unaged = unaged_pairs or set()
        hits_by_memory = injection_hits(memory_dir, telemetry_dir)
        outcome_memories = {
            s: rec.get("hits", 0)
            for s, rec in hits_by_memory.items()
            if s not in excluded and isinstance(rec.get("hits"), int) and rec["hits"] >= 1
        }
        if not outcome_memories:
            return empty  # reward-gated: no recorded outcome → no boost, ever.

        memories: Dict[str, float] = {}
        memory_sources: Dict[str, Set[str]] = {}
        edges: List[dict] = []
        for origin in sorted(outcome_memories):
            hits = outcome_memories[origin]
            # The rewarded trace itself always earns replay priority (it is the terminus
            # reverse replay re-fires first), chain or no chain.
            memories[origin] = memories.get(origin, 0.0) + hits
            memory_sources.setdefault(origin, set()).add(origin)

            chain = decision_chain(origin, memory_dir, index_dir)
            if not chain or not chain.get("edges"):
                continue
            # Chronological node order is the chain's narrative — the provenance each
            # boosted-edge ledger row carries.
            chain_nodes = [n.get("name") for n in chain.get("nodes", []) if n.get("name")]
            # Forward-declared adjacency: from → [(relation, to)]. ``from`` is the newer
            # side (the declarer), so following it IS the backward/upstream direction.
            declared: Dict[str, List[Tuple[str, str]]] = {}
            for e in chain["edges"]:
                f, rel, t = e.get("from"), e.get("relation"), e.get("to")
                if isinstance(f, str) and isinstance(rel, str) and isinstance(t, str):
                    declared.setdefault(f, []).append((rel, t))

            seen = {origin}
            frontier = [origin]
            while frontier:
                cur = frontier.pop(0)
                for rel, upstream in declared.get(cur, ()):
                    if upstream in excluded:
                        continue
                    if frozenset((cur, upstream)) in unaged:
                        # inv-DRM-firewall extension: an un-aged dream edge conducts no
                        # reward — the chain is cut here until the edge ages in.
                        continue
                    edges.append(
                        {
                            "edge": {"from": cur, "relation": rel, "to": upstream},
                            "boost": hits,
                            "outcome_memory": origin,
                            "hits": hits,
                            "decision_chain": chain_nodes,
                        }
                    )
                    memories[upstream] = memories.get(upstream, 0.0) + hits
                    memory_sources.setdefault(upstream, set()).add(origin)
                    if upstream not in seen:
                        seen.add(upstream)
                        frontier.append(upstream)
        return {
            "outcome_memories": outcome_memories,
            "memories": memories,
            "memory_sources": {s: sorted(v) for s, v in memory_sources.items()},
            "edges": edges,
        }
    except Exception:
        return empty


def _candidate_boost(cand: dict, boost_of: Dict[str, float]) -> float:
    """A candidate's rank boost: the STRONGER endpoint's memory boost (0.0 when neither
    endpoint sits on an outcome-anchored chain — untouched, the DRM-5 assertion)."""
    return max(boost_of.get(cand.get("source"), 0.0), boost_of.get(cand.get("target"), 0.0))


# --------------------------------------------------------------------------- #
# The discovery pass (DRM-1's core) — read-only over the corpus
# --------------------------------------------------------------------------- #
def discover(
    memory_dir: str,
    index_dir: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    *,
    probe_k: Optional[int] = None,
    max_seeds: Optional[int] = None,
) -> dict:
    """Run the replay + graph-diff discovery pass. READ-ONLY over every memory file.

    Returns ``{"status", "reason", "pass_id", "candidates", "stats", "soak"}`` where status ∈
    ``ok | below-soak | empty-corpus | no-index``. ``candidates`` is a list of dicts, each
    ``{kind, source, target, distance, cofire, query, mutual, signal}`` (the ledger row
    shape). ``stats`` carries the calibration surface: every observed pair strength, novelty
    exclusions, per-kind counts, θ sweep. Never raises; a broken subsystem degrades to an
    explicit non-ok status (inv3), never a silent empty result.
    """
    probe_k = probe_k if isinstance(probe_k, int) and probe_k > 0 else _env_int("DREAM_PROBE_K", _DEFAULT_PROBE_K)
    max_seeds = max_seeds if isinstance(max_seeds, int) and max_seeds >= 0 else _env_int("DREAM_MAX_SEEDS", _DEFAULT_MAX_SEEDS)
    td = telemetry_dir or default_telemetry_dir(memory_dir)
    pass_id = _new_pass_id()
    result = {
        "status": "ok",
        "reason": "",
        "pass_id": pass_id,
        "candidates": [],
        "stats": {},
        "soak": {},
        # DRM-5: filled by reward_boosts on an ok pass; this empty shape on every early
        # return keeps the reward surface total (consumers never KeyError on a refusal).
        "reward": {"outcome_memories": {}, "memories": {}, "memory_sources": {}, "edges": []},
        # DRM-6: every observed co-fired pair (the generative tier clusters these) —
        # same total-shape discipline as "reward" so consumers never KeyError.
        "pairs": [],
    }

    # Gate 1 — the soak bar (a young corpus proposes nothing; inv3: say so).
    soak = soak_status(td, memory_dir=memory_dir)
    result["soak"] = soak
    if not soak.get("gate_met"):
        result["status"] = "below-soak"
        result["reason"] = (
            f"corpus is below the curation-soak bar ({soak.get('distinct_sessions', 0)}/"
            f"{soak.get('gate_threshold', 5)} distinct sessions) — replay signal would be "
            "one-session topic noise, so no candidates are proposed"
        )
        return result

    # Gate 2 — a corpus too small to have latent structure.
    texts = _corpus_texts(memory_dir)
    if len(texts) < 3:
        result["status"] = "empty-corpus"
        result["reason"] = (
            f"corpus has {len(texts)} memory file(s) — too few for latent edges "
            "(need ≥3 for even one transitive bridge); nothing to replay"
        )
        return result

    stems = sorted(texts)
    floor = floor_memory_names(memory_dir)
    confidence = _confidence_map(texts)
    drafts = {s for s, c in confidence.items() if c == "draft"}
    distinct_now = int(soak.get("distinct_sessions") or 0)
    # DRM-6: graduated-but-young generated memories stay firewalled until they age in.
    unaged_gen = unaged_generated_stems(memory_dir, distinct_now)

    def _eligible(stem: str) -> bool:
        # inv-DRM-firewall + floor exclusion: floor memories are never an endpoint;
        # confidence:draft is quarantined content, never source nor target; a dream-
        # GENERATED memory (DRM-6) additionally has to age in after graduation before
        # the pass may read it.
        return stem not in floor and stem not in drafts and stem not in unaged_gen

    # The graph, two views: RAW (novelty — does ANY edge already connect this pair, aged or
    # not?) and FIREWALLED (generation — un-aged dream edges subtracted; inv-DRM-firewall).
    graph = build_graph(memory_dir, index_dir)
    if graph is None:
        graph = LinkGraph(memory_dir, texts=texts)
    raw_view = _undirected_view(graph, set())
    unaged = unaged_dream_pairs(memory_dir, distinct_now)
    fw_view = _undirected_view(graph, unaged)
    # DRM-6 firewall extension: quarantined NODES (drafts + un-aged generated memories)
    # leave the firewalled topology ENTIRELY — a staged draft's own [[child]] wikilinks
    # must never manufacture the next pass's 2-hop bridge distances (the dream-cites-a-
    # dream tower, node form). The RAW view keeps them: novelty must see every edge that
    # exists, whatever its provenance.
    quarantined_nodes = drafts | unaged_gen
    if quarantined_nodes:
        fw_view = {
            s: {n for n in nbrs if n not in quarantined_nodes}
            for s, nbrs in fw_view.items()
            if s not in quarantined_nodes
        }

    # DRM-5: reward-gated reverse-replay boosts — outcome-anchored lineage chains earn
    # replay priority + candidate-rank promotion. Same endpoint exclusions as generation
    # (floor + drafts); the backward walk never crosses an un-aged dream pair. With no
    # recorded outcome this is empty and every downstream consumer is provably inert.
    reward = reward_boosts(
        memory_dir, index_dir, td, exclude_stems=floor | drafts | unaged_gen, unaged_pairs=unaged
    )
    boost_of = reward["memories"]

    # Replay worklist — outcome-anchored traces FIRST (DRM-5 replay priority), then
    # over-sample under-connected, under-consolidated traces: firewalled degree ascending
    # (isolates first), then usage-sessions ascending (cold first). With no boosts the
    # leading key is 0.0 everywhere and the pre-DRM-5 ordering is byte-identical.
    usage = {}
    try:
        usage = read_usage_aggregates(td).get("memories") or {}
    except Exception:
        usage = {}

    def _usage_sessions(stem: str) -> int:
        rec = usage.get(stem)
        n = rec.get("sessions") if isinstance(rec, dict) else 0
        return n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else 0

    worklist = sorted(
        (s for s in stems if _eligible(s)),
        key=lambda s: (-boost_of.get(s, 0.0), len(fw_view.get(s, ())), _usage_sessions(s), s),
    )
    if max_seeds and max_seeds > 0:
        worklist = worklist[:max_seeds]
    worklist_preview = worklist[:10]  # exposed in stats: the replay-priority audit surface

    # The index for offline probes. refresh_index is offline/bounded/never-downgrade (the
    # SessionStart path) — an index write is a DERIVED-dir write, not a memory write.
    from .build_index import build_index, default_index_dir, load_index, refresh_index

    idx_dir = index_dir or default_index_dir(memory_dir)
    try:
        refresh_index(memory_dir, idx_dir)
    except Exception:
        pass
    idx = load_index(idx_dir)
    if idx is None:
        try:
            build_index(memory_dir, idx_dir, allow_download=False)
        except Exception:
            pass
        idx = load_index(idx_dir)
    if idx is None or not len(idx):
        result["status"] = "no-index"
        result["reason"] = "could not build/load a recall index for this corpus — nothing replayed"
        return result

    from .eval_recall import derive_self_query
    from .recall import recall

    entries_by_name = {e.get("name"): e for e in idx.entries}

    # --- Replay: probe each seed's self-query OFFLINE, organic ranking only. ------------
    # ``recall(index=idx)`` with no memory_dir/index_dir deliberately skips BOTH graph
    # expansion and tier fusion: co-firing must be pure semantic/lexical signal, so a dream
    # edge (aged or not) can never manufacture the co-fire that justifies the next edge —
    # the firewall's second half. Also hermetic: no trust-gate git work on an offline pass.
    pair_best: Dict[frozenset, dict] = {}
    partner_counts: Dict[str, Set[str]] = {}
    probes_run = 0
    probes_abstained = 0
    for seed in worklist:
        entry = entries_by_name.get(seed)
        if entry is None:
            continue
        query = derive_self_query(entry)
        if not query:
            continue
        results = recall(query, k=probe_k, index=idx)
        probes_run += 1
        if not results:
            probes_abstained += 1
            continue
        top_score = max((r.get("score") or 0.0) for r in results)
        if top_score <= 0:
            continue
        for r in results:
            name = r.get("name")
            if not name or name == seed or name not in texts or not _eligible(name):
                continue
            strength = round(float(r.get("score") or 0.0) / top_score, 4)
            key = frozenset((seed, name))
            rec = pair_best.setdefault(
                key, {"strength": 0.0, "query": "", "seed": seed, "directions": set()}
            )
            rec["directions"].add(seed)
            partner_counts.setdefault(seed, set()).add(name)
            partner_counts.setdefault(name, set()).add(seed)
            if strength > rec["strength"]:
                rec.update({"strength": strength, "query": query, "seed": seed})

    # --- Kind 1: completion — the body already names the target. ------------------------
    candidates: List[dict] = []
    claimed: Set[frozenset] = set()
    novelty_excluded = 0

    def _pair_cofire(a: str, b: str) -> Tuple[float, str, bool]:
        rec = pair_best.get(frozenset((a, b)))
        if not rec:
            return 0.0, "", False
        return rec["strength"], rec["query"], len(rec["directions"]) > 1

    mention_res = {s: _mention_regex(s) for s in stems}
    for source in stems:
        if not _eligible(source):
            continue
        prose = _body_without_wikilinks(texts[source])
        # 1a. plain-text mention of another memory's slug in the body prose.
        for target in stems:
            if target == source or not _eligible(target):
                continue
            pat = mention_res.get(target)
            if pat is None or not pat.search(prose):
                continue
            if target in raw_view.get(source, ()):  # novelty: an edge already exists
                novelty_excluded += 1
                continue
            key = frozenset((source, target))
            if key in claimed:
                continue
            claimed.add(key)
            cof, q, mutual = _pair_cofire(source, target)
            candidates.append(
                {
                    "kind": "completion",
                    "source": source,
                    "target": target,
                    "distance": _distance(fw_view, source, target),
                    "cofire": cof,
                    "query": q,
                    "mutual": mutual,
                    "signal": "body-mention",
                }
            )
        # 1b. dangling [[wikilink]] that fuzzy-resolves to exactly one real stem.
        for raw in graph.unresolved.get(source, []):
            target = _fuzzy_stem_match(raw, [s for s in stems if s != source and _eligible(s)])
            if target is None:
                continue
            if target in raw_view.get(source, ()):
                novelty_excluded += 1
                continue
            key = frozenset((source, target))
            if key in claimed:
                continue
            claimed.add(key)
            cof, q, mutual = _pair_cofire(source, target)
            candidates.append(
                {
                    "kind": "completion",
                    "source": source,
                    "target": target,
                    "distance": _distance(fw_view, source, target),
                    "cofire": cof,
                    "query": q,
                    "mutual": mutual,
                    "signal": f"dangling-wikilink:[[{raw}]]",
                }
            )

    # --- Kinds 2+3: bridge / refines over the co-fired pairs. ---------------------------
    unclassified_pairs = 0
    for key, rec in sorted(pair_best.items(), key=lambda kv: -kv[1]["strength"]):
        if key in claimed:
            continue
        a, b = sorted(key)
        if b in raw_view.get(a, ()):  # novelty filter: GRA-3/GRW-2 (or any) edge exists
            novelty_excluded += 1
            continue
        dist = _distance(fw_view, a, b)
        strength, query, mutual = rec["strength"], rec["query"], len(rec["directions"]) > 1
        a_slug, b_slug = normalize_slug(a), normalize_slug(b)
        refines_pair: Optional[Tuple[str, str]] = None  # (child/source, parent/target)
        if a_slug.startswith(b_slug + "-"):
            refines_pair = (a, b)
        elif b_slug.startswith(a_slug + "-"):
            refines_pair = (b, a)
        if refines_pair is not None:
            claimed.add(key)
            candidates.append(
                {
                    "kind": "refines",
                    "source": refines_pair[0],
                    "target": refines_pair[1],
                    "distance": dist,
                    "cofire": strength,
                    "query": query,
                    "mutual": mutual,
                    "signal": "slug-prefix",
                }
            )
        elif dist == 2:
            claimed.add(key)
            # Direction of discovery: the seed whose probe fired the pair strongest.
            src = rec["seed"]
            tgt = b if src == a else a
            candidates.append(
                {
                    "kind": "bridge",
                    "source": src,
                    "target": tgt,
                    "distance": 2,
                    "cofire": strength,
                    "query": query,
                    "mutual": mutual,
                    "signal": "transitive-2hop",
                }
            )
        else:
            # Co-fired but neither adjacent-in-2 nor typed-signal-bearing: counted, not
            # emitted — the closed three-kind taxonomy is the roadmap's scope, and honest
            # reporting beats a speculative fourth kind.
            unclassified_pairs += 1

    # DRM-6: the raw co-fired pair surface the generative tier clusters (schema = a
    # mutual component ≥ the cluster bar; hypothesis = a strong mutual pair with NO
    # firewalled path). Serialized here — strength order, then name — so the clustering
    # never re-probes the corpus. Firewall-clean by construction: only _eligible stems
    # ever enter pair_best, and distances read the quarantine-stripped fw_view.
    pairs_out: List[dict] = []
    for key, rec in sorted(
        pair_best.items(), key=lambda kv: (-kv[1]["strength"], sorted(kv[0]))
    ):
        a, b = sorted(key)
        pairs_out.append(
            {
                "a": a,
                "b": b,
                "cofire": rec["strength"],
                "mutual": len(rec["directions"]) > 1,
                "distance": _distance(fw_view, a, b),
                "query": rec["query"],
                "seed": rec["seed"],
            }
        )
    result["pairs"] = pairs_out

    # DRM-5 annotation: a candidate touching an outcome-anchored (boosted) memory carries
    # its boost + the justifying outcome memories, and sorts earlier among its peers —
    # RANKING ONLY (apply eligibility reads the raw cofire; see ``apply_eligible``).
    rw = reward_weight()
    sources = reward.get("memory_sources") or {}
    for c in candidates:
        b = _candidate_boost(c, boost_of)
        if b > 0:
            c["boost"] = b
            c["boost_provenance"] = sorted(
                set(sources.get(c["source"], [])) | set(sources.get(c["target"], []))
            )
    candidates.sort(
        key=lambda c: (
            c["kind"] != "completion",
            -(c["cofire"] + rw * min(c.get("boost", 0.0), _REWARD_BOOST_RANK_CAP)),
            c["source"],
            c["target"],
        )
    )

    # --- Calibration stats (the DRM-1 deliverable). --------------------------------------
    all_strengths = sorted((rec["strength"] for rec in pair_best.values()), reverse=True)
    kind_counts = {k: sum(1 for c in candidates if c["kind"] == k) for k in _CANDIDATE_KINDS}
    theta_sweep = []
    for step in range(30, 100, 5):
        theta = step / 100.0
        theta_sweep.append(
            {
                "theta": theta,
                "apply_eligible": sum(1 for c in candidates if apply_eligible(c, theta=theta)),
            }
        )
    result["stats"] = {
        "corpus_files": len(texts),
        "floor_excluded": sorted(floor),
        "draft_excluded": sorted(drafts),
        "seeds_probed": probes_run,
        "probes_abstained": probes_abstained,
        "pairs_observed": len(pair_best),
        "novelty_excluded": novelty_excluded,
        "unclassified_pairs": unclassified_pairs,
        "unaged_dream_pairs_firewalled": len(unaged),
        "unaged_generated_firewalled": sorted(unaged_gen),
        "worklist_preview": worklist_preview,
        "reward_outcome_memories": len(reward.get("outcome_memories") or {}),
        "reward_boosted_memories": len(boost_of),
        "reward_boosted_edges": len(reward.get("edges") or []),
        "kind_counts": kind_counts,
        "cofire_strengths_all_pairs": all_strengths,
        "cofire_strengths_candidates": sorted((c["cofire"] for c in candidates), reverse=True),
        "theta_sweep": theta_sweep,
        "theta_current": cofire_theta(),
        "cap_current": max_apply_per_pass(),
    }
    result["candidates"] = candidates
    result["reward"] = reward

    # DRM-C (opt-in, default OFF): the LLM comprehension pass over the SAME high-cofire
    # pairs serialized above. Appended AFTER the Tier-A sort + stats so the organic
    # surface is byte-identical flag-off vs flag-on-but-empty; its own try/except because
    # an LLM-layer failure must never break the organic pass (fail open — inv3 still
    # holds: the stats block says what was judged/skipped when the flag is on).
    if contradictions_enabled():
        try:
            contra = discover_contradictions(
                pairs_out, texts, memory_dir, td, pass_id=pass_id, graph=graph
            )
            result["candidates"] = candidates + contra["candidates"]
            result["stats"]["contradictions"] = contra["stats"]
        except Exception:
            pass
    return result
