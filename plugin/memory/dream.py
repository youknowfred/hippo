"""/dream — the generative sleep pass (DRM workstream, ``ROADMAP.dream.yaml``).

hippo's housekeeping verbs (consolidate / reconsolidate / staleness / salience) cover the
maintenance functions of sleep. This module is the GENERATIVE one: an offline pass that
replays the corpus against itself — re-runs recall over each memory's own derived
self-query — watches what co-fires, and diffs that against the link graph to surface the
latent edges the corpus is structurally missing:

  - **completion** — a body already NAMES another memory (plain-text mention, or a dangling
    ``[[wikilink]]`` that nearly resolves) but no edge exists. Highest precision.
  - **bridge**     — a transitive A–B–C pair (A–B and B–C linked, A–C absent) that co-fires.
    Exactly the 2-hop miss ``recall._expand_neighbors`` (GRA-1) turns into a 1-hop hit once
    the A–C edge exists.
  - **refines**    — an undeclared typed relation: a child memory whose slug extends a
    parent's (``foo-bar-baz`` refines ``foo-bar``) and that co-fires with it.

consolidate's two link signals — write-time similarity (GRA-3) and co-recall (GRW-2) — can
only connect already-similar or already-co-surfacing pairs; this latent class is unreachable
by construction. /dream is the verb that finds it.

DRM-1 (this slice) is REPORT-ONLY: zero writes to any memory file. It emits a candidate-edge
ledger (jsonl) under the gitignored derived telemetry dir and prints the co-fire-strength
distribution + count-by-kind so DRM-2's θ (``DREAM_COFIRE_THETA``) and per-pass cap are
calibrated from live data, not guessed. The workstream keystone: DRM-2..6 consume this
ledger and its calibration.

Non-negotiables carried from the roadmap (guiding_invariants, all load-bearing):
  - inv1  — the candidate ledger lives under the DERIVED telemetry dir (gitignored); the
            committed ``dream-ledger.jsonl`` (DRM-2's audit record) is provenance, not a
            second authority; aging state on top of it is DERIVED, never stored.
  - inv3  — the empty pass says so; below the soak bar says so; no silent no-ops.
  - inv4  — this slice writes NOTHING to any memory (report-only is inv4's strongest form).
  - inv6  — /dream is an offline turn (like consolidate); never the UserPromptSubmit hot path.
  - inv-DRM-firewall — the candidate generator's SOURCE set is confidence:verified +
            user-asserted memories + AGED-IN dream edges only. Un-aged dream edges (from the
            DRM-2 apply ledger) are invisible to generation: subtracted from the graph view
            used for worklist priority / distances / bridges, and probes run WITHOUT graph
            expansion (``recall(index=...)`` only — pure organic co-firing), so a dream edge
            can never feed the next pass's candidates before it ages in. Kills the
            dream-cites-a-dream tower structurally.
  - inv-DRM-empty-norm — θ and the cap are tuned so the EMPTY pass is the common outcome.

Floor memories (``lint_floor.floor_memory_names`` — always loaded in full) are never an edge
endpoint, source or target. Memories at ``confidence: draft`` are excluded as seeds AND as
endpoints (the firewall extends to quarantined content). The pass gates on
``soak.soak_status`` (≥5 distinct sessions): a young corpus proposes nothing.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from .links import LinkGraph, build_graph, normalize_slug, parse_wikilinks
from .lint_floor import floor_memory_names
from .provenance import _iter_memory_files, parse_frontmatter
from .soak import soak_status
from .telemetry import default_telemetry_dir, read_usage_aggregates

# --------------------------------------------------------------------------- #
# Tunables (env-overridable; defaults are the ratified/roadmap values)
# --------------------------------------------------------------------------- #

# Co-fire threshold for DRM-2 auto-apply (bridges/refines; completions are text-evidence
# based and exempt per DRM-2.spec.md §1). CALIBRATED 2026-07-12 from the live-corpus DRM-1
# pass p20260712210214 (29 memories, 28 probes, 190 co-fire pairs): RRF fused-score ratios
# are rank-compressed (rank-10/rank-1 ≈ 0.87 by construction), so raw ratio under-
# discriminates at the top — the distribution's real separator is MUTUALITY (the pair
# co-fired from BOTH probes: 16/76 bridges, and eyeballed-true edges concentrate there).
# Hence the two-part apply bar in ``apply_eligible``: bridges require MUTUAL co-fire AND
# cofire ≥ θ=0.90 (live pool: 9 eligible → drains in ~2 capped passes → empty-pass norm
# holds); refines require cofire ≥ θ (the slug-prefix signal is already strong);
# completions are θ-exempt. Report-only passes record every candidate regardless of θ.
_DEFAULT_THETA = 0.90

# Per-pass auto-apply cap (DRM-2). Single-digit by design (inv-DRM-empty-norm / DREAM-KILL-4:
# no bulk sweeps); the hard max is not overridable.
_DEFAULT_MAX_APPLY = 5
_HARD_MAX_APPLY = 9

# Aging window before a dream edge joins /dream's own SOURCE set (owner decision 2026-07-12:
# 5 distinct sessions, reusing soak's bar).
_DEFAULT_AGE_SESSIONS = 5

# DRM-5: how much one unit of reward boost (one hit session on an outcome-anchored chain)
# nudges a candidate's RANK position. CALIBRATED 2026-07-12 from the live corpus (29
# memories, 197 pairs): co-fire strengths are rank-compressed — the entire θ-eligible band
# spans ~0.90–0.98 — so a per-hit bump must be a FRACTION of that ~0.08 band or reward
# leapfrogs the whole distribution (at 0.05/hit a 3-hit boost outranked an unboosted 0.98
# from 0.90 — dominate, not promote). 0.01/hit, with the counted hits capped below, keeps
# a boosted candidate promoted WITHIN its cofire neighborhood. Reward reorders candidates
# under the cap; it never substitutes for co-fire evidence — the θ eligibility test always
# reads the RAW cofire (a boost can never push a sub-θ candidate over the auto-apply bar;
# widening autonomy is a dated owner decision, not a weight).
_DEFAULT_REWARD_WEIGHT = 0.01

# Rank-bonus saturation: hits beyond this count stop adding rank (max bonus at the default
# weight = 0.05 ≈ half the live θ-eligible band). A daily-hit memory accumulates hit
# sessions linearly; unbounded, weeks of routine use would re-dominate ordering.
_REWARD_BOOST_RANK_CAP = 5.0

# Replay probe depth: how many results each self-query probe considers as the co-firing set.
_DEFAULT_PROBE_K = 10

# Worklist bound: 0 = replay every eligible memory (fine at corpus scale ≤ a few hundred);
# a positive value caps the seed list at the N most under-connected/under-used traces.
_DEFAULT_MAX_SEEDS = 0

# Mention detection guard: a target alias shorter than this (normalized, hyphens included)
# is too generic to count as "the body names the target" (e.g. ``recall``); precision first.
_MIN_MENTION_CHARS = 10

# Distance BFS cutoff — we only need to distinguish 1 (existing edge), 2 (bridge), and
# "farther/disconnected"; a bounded walk keeps the pass O(V·E) at worst.
_DISTANCE_CUTOFF = 4

_CANDIDATE_KINDS = ("completion", "bridge", "refines")


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.environ.get(name, "").strip()
        return float(raw) if raw else default
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        raw = os.environ.get(name, "").strip()
        return int(raw) if raw else default
    except (TypeError, ValueError):
        return default


def cofire_theta() -> float:
    """DRM-2's apply threshold θ (``DREAM_COFIRE_THETA``); report passes record it only."""
    return _env_float("DREAM_COFIRE_THETA", _DEFAULT_THETA)


def max_apply_per_pass() -> int:
    """``DREAM_MAX_APPLY_PER_PASS`` clamped to [0, 9] — the hard max is not overridable."""
    return max(0, min(_env_int("DREAM_MAX_APPLY_PER_PASS", _DEFAULT_MAX_APPLY), _HARD_MAX_APPLY))


def age_sessions() -> int:
    """``DREAM_AGE_SESSIONS`` (≥1) — sessions an applied edge must survive to become source."""
    return max(1, _env_int("DREAM_AGE_SESSIONS", _DEFAULT_AGE_SESSIONS))


def reward_weight() -> float:
    """``DREAM_REWARD_WEIGHT`` (≥0) — per-hit rank nudge for DRM-5 boosts (default 0.05)."""
    return max(0.0, _env_float("DREAM_REWARD_WEIGHT", _DEFAULT_REWARD_WEIGHT))


def apply_eligible(candidate: dict, *, theta: Optional[float] = None) -> bool:
    """The Tier-A auto-apply bar ONE candidate must clear (DRM-2's gate; DRM-1's sweep).

    Calibrated 2026-07-12 on the live corpus (see ``_DEFAULT_THETA``'s note):
      - **completion** — θ-exempt: the body already names the target (text evidence, the
        highest-precision kind per DRM-2.spec.md §1).
      - **refines**    — ``cofire ≥ θ`` (the slug-prefix signal carries the typing).
      - **bridge**     — ``cofire ≥ θ`` AND **mutual** (co-fired from BOTH endpoints'
        probes): RRF score ratios are rank-compressed, and mutuality is the separator the
        live distribution actually exposed — one-way tail pairs reach 0.44–0.97, while the
        eyeballed-true edges concentrate in the mutual set.
    Anything else (unknown kind, Tier B/C) is never apply-eligible from this gate.
    """
    th = cofire_theta() if theta is None else theta
    kind = candidate.get("kind")
    if kind == "completion":
        return True
    if kind == "refines":
        return (candidate.get("cofire") or 0.0) >= th
    if kind == "bridge":
        return bool(candidate.get("mutual")) and (candidate.get("cofire") or 0.0) >= th
    return False


# --------------------------------------------------------------------------- #
# Paths — candidate ledger (derived, gitignored) vs apply ledger (corpus, committed)
# --------------------------------------------------------------------------- #
def dream_dir(telemetry_dir: str) -> str:
    """``<telemetry_dir>/dream`` — the derived home for candidate ledgers (inv1)."""
    return os.path.join(telemetry_dir, "dream")


def candidate_ledger_path(telemetry_dir: str, pass_id: str) -> str:
    return os.path.join(dream_dir(telemetry_dir), f"candidates-{pass_id}.jsonl")


def apply_ledger_path(memory_dir: str) -> str:
    """``<memory_dir>/dream-ledger.jsonl`` — DRM-2's committed, append-only audit record.

    Lives in the corpus dir (it rides the corpus's own git posture) but is NOT a memory
    file: ``_iter_memory_files`` yields only ``*.md``, so it is never indexed or recalled.
    DRM-1 only READS it (the aging firewall must hold from the first applied edge).
    """
    return os.path.join(memory_dir, "dream-ledger.jsonl")


def _new_pass_id() -> str:
    return "p" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


# --------------------------------------------------------------------------- #
# Apply-ledger reading (DRM-2 writes it; the DRM-1 firewall reads it from day one)
# --------------------------------------------------------------------------- #
def read_apply_ledger(memory_dir: str) -> List[dict]:
    """Parse ``dream-ledger.jsonl`` into per-edge CURRENT state (last line per edge_id wins).

    The ledger is append-only: an undo appends a superseding ``state: "undone"`` line rather
    than rewriting history. Returns one dict per edge_id, in first-seen order. Missing file
    or junk lines contribute nothing; never raises.
    """
    path = apply_ledger_path(memory_dir)
    order: List[str] = []
    latest: Dict[str, dict] = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if not isinstance(rec, dict):
                    continue
                eid = rec.get("edge_id")
                if not isinstance(eid, str) or not eid:
                    continue
                if eid not in latest:
                    order.append(eid)
                    latest[eid] = rec
                else:
                    # A superseding line may be sparse (undo writes edge_id/state/pass only);
                    # merge over the prior record so the current view keeps full provenance.
                    merged = dict(latest[eid])
                    merged.update(rec)
                    latest[eid] = merged
    except FileNotFoundError:
        return []
    except Exception:
        return []
    return [latest[eid] for eid in order]


def edge_aged_in(edge: dict, distinct_sessions_now: int, *, window: Optional[int] = None) -> bool:
    """The aging firewall's pure function (DRM-2.spec.md §5) — no stored state, ever.

    ``distinct_sessions_now − applied_at_distinct_count ≥ DREAM_AGE_SESSIONS``. An edge with
    a missing/junk ``applied_at_distinct_count`` NEVER ages in (fail toward the firewall).
    """
    w = age_sessions() if window is None else window
    applied_at = edge.get("applied_at_distinct_count")
    if not isinstance(applied_at, int) or isinstance(applied_at, bool) or applied_at < 0:
        return False
    return (distinct_sessions_now - applied_at) >= w


def unaged_dream_pairs(memory_dir: str, distinct_sessions_now: int) -> Set[frozenset]:
    """Unordered ``{source, target}`` pairs of ACTIVE, NOT-yet-aged dream edges.

    These are subtracted from the graph view candidate generation reads (inv-DRM-firewall):
    an applied edge influences recall immediately, but /dream's own source set must not see
    it until it ages in. An edge undone before aging is ``state: "undone"`` and therefore
    never in this set — nor in the graph (its stamped line was removed).
    """
    out: Set[frozenset] = set()
    for edge in read_apply_ledger(memory_dir):
        if edge.get("state") != "active":
            continue
        if edge_aged_in(edge, distinct_sessions_now):
            continue
        src, tgt = edge.get("source"), edge.get("target")
        if isinstance(src, str) and isinstance(tgt, str) and src and tgt:
            out.add(frozenset((src, tgt)))
    return out


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


def boost_ledger_path(telemetry_dir: str, pass_id: str) -> str:
    return os.path.join(dream_dir(telemetry_dir), f"boosts-{pass_id}.jsonl")


def write_boost_ledger(telemetry_dir: str, pass_id: str, edges: List[dict]) -> Optional[str]:
    """Persist the pass's boosted-edge rows (with decision_chain provenance) to the derived
    dream dir. One row per (edge, outcome_memory). Written only when boosts exist — the
    candidate ledger is already the proof the pass ran (empty-norm hygiene). Never raises."""
    if not edges:
        return None
    try:
        os.makedirs(dream_dir(telemetry_dir), exist_ok=True)
        path = boost_ledger_path(telemetry_dir, pass_id)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(path, "w", encoding="utf-8") as fh:
            for row in edges:
                fh.write(
                    json.dumps({"pass": pass_id, **row, "generated_at": stamp}, ensure_ascii=False)
                    + "\n"
                )
        return path
    except Exception:
        return None


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

    def _eligible(stem: str) -> bool:
        # inv-DRM-firewall + floor exclusion: floor memories are never an endpoint;
        # confidence:draft is quarantined content, never source nor target.
        return stem not in floor and stem not in drafts

    # The graph, two views: RAW (novelty — does ANY edge already connect this pair, aged or
    # not?) and FIREWALLED (generation — un-aged dream edges subtracted; inv-DRM-firewall).
    graph = build_graph(memory_dir, index_dir)
    if graph is None:
        graph = LinkGraph(memory_dir, texts=texts)
    raw_view = _undirected_view(graph, set())
    unaged = unaged_dream_pairs(memory_dir, int(soak.get("distinct_sessions") or 0))
    fw_view = _undirected_view(graph, unaged)

    # DRM-5: reward-gated reverse-replay boosts — outcome-anchored lineage chains earn
    # replay priority + candidate-rank promotion. Same endpoint exclusions as generation
    # (floor + drafts); the backward walk never crosses an un-aged dream pair. With no
    # recorded outcome this is empty and every downstream consumer is provably inert.
    reward = reward_boosts(
        memory_dir, index_dir, td, exclude_stems=floor | drafts, unaged_pairs=unaged
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
    return result


# --------------------------------------------------------------------------- #
# Candidate ledger (jsonl, derived dir) + the printed report
# --------------------------------------------------------------------------- #
def write_candidate_ledger(telemetry_dir: str, pass_id: str, candidates: List[dict]) -> Optional[str]:
    """Write the pass's candidate rows to the derived dream dir; returns the path or None.

    One JSON object per line: ``{pass, kind, source, target, distance, cofire, query,
    mutual, signal, generated_at}``. An OK pass with zero candidates still writes the (empty)
    file — the auditable record that the pass ran and found nothing. Never raises.
    """
    try:
        os.makedirs(dream_dir(telemetry_dir), exist_ok=True)
        path = candidate_ledger_path(telemetry_dir, pass_id)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(path, "w", encoding="utf-8") as fh:
            for c in candidates:
                row = {"pass": pass_id, **c, "generated_at": stamp}
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path
    except Exception:
        return None


def _histogram(strengths: List[float], buckets: int = 10) -> List[str]:
    """Fixed 0.0–1.0 bucket histogram lines (ascii bars) for the distribution print."""
    counts = [0] * buckets
    for s in strengths:
        i = min(int(s * buckets), buckets - 1)
        counts[i] += 1
    peak = max(counts) if any(counts) else 1
    lines = []
    for i, n in enumerate(counts):
        lo, hi = i / buckets, (i + 1) / buckets
        bar = "█" * max(1, round(n * 24 / peak)) if n else ""
        lines.append(f"  {lo:.1f}–{hi:.1f}  {n:>4}  {bar}")
    return lines


def render_report(result: dict, *, ledger_path: Optional[str]) -> str:
    """The human-readable pass report: status, candidates, and the calibration surface."""
    lines: List[str] = []
    status = result.get("status")
    pass_id = result.get("pass_id", "?")
    if status != "ok":
        lines.append(f"🌙 dream pass {pass_id} — no candidates: {result.get('reason')}")
        lines.append("   (an empty pass is the norm; this one never reached replay)")
        return "\n".join(lines)

    stats = result.get("stats") or {}
    candidates = result.get("candidates") or []
    lines.append(
        f"🌙 dream pass {pass_id} — REPORT-ONLY (zero memory writes): "
        f"{len(candidates)} candidate edge(s) from {stats.get('seeds_probed', 0)} replay probe(s) "
        f"over {stats.get('corpus_files', 0)} memories"
    )
    if ledger_path:
        lines.append(f"   candidate ledger: {ledger_path}")
    kc = stats.get("kind_counts") or {}
    lines.append(
        "   count-by-kind: "
        + " · ".join(f"{k}={kc.get(k, 0)}" for k in _CANDIDATE_KINDS)
        + f" · unclassified-cofire-pairs={stats.get('unclassified_pairs', 0)}"
        + f" · novelty-excluded={stats.get('novelty_excluded', 0)}"
    )
    if stats.get("unaged_dream_pairs_firewalled"):
        lines.append(
            f"   aging firewall: {stats['unaged_dream_pairs_firewalled']} un-aged dream edge(s) "
            "excluded from the source graph this pass"
        )
    if stats.get("reward_boosted_edges") or stats.get("reward_outcome_memories"):
        lines.append(
            f"   reward (DRM-5 reverse replay): {stats.get('reward_boosted_edges', 0)} upstream "
            f"edge boost(s) from {stats.get('reward_outcome_memories', 0)} outcome-anchored "
            f"memory(ies) — replay priority + candidate ORDERING only (θ reads raw cofire)"
        )
    if not candidates:
        lines.append("   empty pass — no latent edges above the reporting floor (this is the norm).")
    for c in candidates[:20]:
        dist = c.get("distance")
        dist_s = f"d={dist}" if isinstance(dist, int) else "d=∞"
        q = (c.get("query") or "")[:48]
        lines.append(
            f"   • {c['source']} → {c['target']}   {c['kind']:<10} "
            f"cofire={c['cofire']:.2f} {dist_s}"
            + (" mutual" if c.get("mutual") else "")
            + (f" ★boost={c['boost']:g}" if c.get("boost") else "")
            + f" [{c.get('signal')}]"
            + (f' q="{q}"' if q else "")
        )
    if len(candidates) > 20:
        lines.append(f"   …and {len(candidates) - 20} more (see the ledger).")

    # The calibration surface: the co-fire-strength DISTRIBUTION + θ sweep (DRM-1's point).
    all_s = stats.get("cofire_strengths_all_pairs") or []
    lines.append("")
    lines.append(
        f"   co-fire strength distribution — ALL observed pairs (n={len(all_s)}, "
        "strength = pair score / probe top score):"
    )
    lines.extend(_histogram(all_s))
    if all_s:
        import statistics

        qs = {
            "p50": statistics.median(all_s),
            "p75": all_s[max(0, round(len(all_s) * 0.25) - 1)],
            "p90": all_s[max(0, round(len(all_s) * 0.10) - 1)],
            "max": all_s[0],
        }
        lines.append(
            "   percentiles: " + " · ".join(f"{k}={v:.2f}" for k, v in qs.items())
        )
    sweep = stats.get("theta_sweep") or []
    lines.append(
        "   θ sweep (apply-eligible candidates at each θ — bridges need MUTUAL co-fire, "
        "refines need cofire≥θ, completions are text-evidence based and θ-exempt):"
    )
    lines.append("     " + " ".join(f"θ≥{row['theta']:.2f}:{row['apply_eligible']}" for row in sweep))
    lines.append(
        f"   current knobs: θ={stats.get('theta_current')} cap={stats.get('cap_current')} "
        "(DREAM_COFIRE_THETA / DREAM_MAX_APPLY_PER_PASS)"
    )
    if apply_mode_default():
        lines.append(
            "   this was a report-only pass — the auto-apply default is ON (owner flip "
            "2026-07-12): a bare pass applies Tier-A candidates above the calibrated bar."
        )
    else:
        lines.append(
            "   auto-apply is OFF (report-only) — the DRM-2 flip is a dated owner decision "
            "after this calibration."
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# DRM-2 — Tier-A auto-apply (apply-reversibly → notify → undo-window → age-in)
#
# The loop DRM-2.spec.md specifies. AUTO-APPLY IS NOT THE SHIPPED DEFAULT: a pass applies
# only when explicitly asked (--apply / MCP apply:true) or when HIPPO_DREAM_APPLY is set —
# the default flip is a DATED OWNER DECISION consuming DRM-1's calibration (owner_decisions
# item 1; do not flip it in code without that date). Every applied edge is:
#   - additive + body-prose-preserving: a stamped line inside the machine-managed
#     dream:links block (bridge/completion), or additive refines frontmatter via
#     links.add_typed_relation plus a comment stamp in the block;
#   - capped (DREAM_MAX_APPLY_PER_PASS ≤ 9) and θ/mutuality-gated (apply_eligible);
#   - secret-linted with a HARD BLOCK (the owner-ratified 2026-07-12 deviation from
#     secrets.py's WARN-never-BLOCK, scoped to THIS write path only — dream GENERATES
#     text; it does not transcribe user intent);
#   - provenance-complete in the committed append-only dream-ledger.jsonl, with an inline
#     pass=/edge= stamp so grep reconciles corpus against ledger (doctor checks this);
#   - live immediately (working tree + index rebuild) but NEVER auto-committed — git
#     history stays the owner's (DREAM-KILL-2);
#   - mechanically undoable byte-for-byte (--undo / --undo <id> / --undo-since), with
#     refuse-on-drift: a stamped line or frontmatter region edited by hand since apply is
#     never clobbered.
# --------------------------------------------------------------------------- #
_TIER_A_KINDS = ("completion", "bridge", "refines")
# Tier-C routing (DREAM-KILL-1): these kinds are NEVER auto-applied. Today's generator
# does not emit them; the routing is enforced here anyway so a future/hand-fed candidate
# stream cannot slip one through the apply path.
_GATED_KINDS = ("supersedes",)   # → surfaced in the digest, applied only by explicit owner action
_ROUTED_KINDS = ("contradicts",)  # → the /hippo:resolve inbox, never auto


def apply_mode_default() -> bool:
    """Whether a bare pass auto-applies (``HIPPO_DREAM_APPLY``; SHIPPED DEFAULT: True).

    FLIPPED ON by the dated owner decision 2026-07-12 (ROADMAP.dream.yaml
    owner_decisions item 5), consuming the DRM-1 live-corpus calibration (θ=0.90, cap 5,
    bridges-require-mutual — see ``apply_eligible``). ``HIPPO_DREAM_APPLY=0`` or
    ``--dry-run`` opts a pass back to report-only; the default may only change again
    alongside a new dated entry in owner_decisions.
    """
    _SHIPPED_APPLY = True
    raw = os.environ.get("HIPPO_DREAM_APPLY", "").strip()
    if not raw:
        return _SHIPPED_APPLY
    return raw not in ("0", "false", "False")


def _sanitize_stamp_text(s: str, limit: int = 60) -> str:
    """Stamp-safe text: quotes/newlines/comment-closers stripped, bounded."""
    s = (s or "").replace('"', "'").replace("\n", " ").replace("-->", "")
    return s[:limit].strip()


def _stamp_line(edge_id: str, pass_id: str, cand: dict) -> str:
    """The exact on-disk line for one applied edge (the grep-able provenance stamp)."""
    q = _sanitize_stamp_text(cand.get("query") or "")
    cof = float(cand.get("cofire") or 0.0)
    if cand["kind"] == "refines":
        # Deliberately bracket-free: the edge itself lives in frontmatter; this comment is
        # the stamp only, and must never read as an untyped wikilink edge.
        return (
            f"<!-- dream: refines {cand['target']} · pass={pass_id} · edge={edge_id}"
            f" · cofire={cof:.2f} -->"
        )
    return (
        f"[[{cand['target']}]] <!-- dream: {cand['kind']} · pass={pass_id} · edge={edge_id}"
        f" · cofire={cof:.2f}" + (f' · q="{q}"' if q else "") + " -->"
    )


def _insert_block_line(text: str, line: str) -> Tuple[str, dict]:
    """Insert ``line`` into the dream:links block (creating it at EOF if absent).

    Returns ``(new_text, undo_record)``. The undo record captures EXACTLY what was added:
    ``{"inserted": <line+newline>, "wrapper": bool, "lead": <bytes prepended before the
    block>}`` — enough to reverse this edit byte-for-byte, alone or in reverse-order
    composition with the pass's other edits.
    """
    from .links import DREAM_BLOCK_CLOSE, DREAM_BLOCK_OPEN

    close_marker = DREAM_BLOCK_CLOSE + "\n"
    if DREAM_BLOCK_OPEN in text and close_marker in text:
        idx = text.rindex(close_marker)
        new_text = text[:idx] + line + "\n" + text[idx:]
        return new_text, {"inserted": line + "\n", "wrapper": False, "lead": ""}
    lead = "" if text.endswith("\n") else "\n"
    appended = f"{lead}{DREAM_BLOCK_OPEN}\n{line}\n{DREAM_BLOCK_CLOSE}\n"
    return text + appended, {"inserted": line + "\n", "wrapper": True, "lead": lead}


def _frontmatter_region(text: str) -> Optional[Tuple[int, int, List[str]]]:
    """``(start_line, end_line, fm_lines)`` of the frontmatter body (between fences)."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return 1, i, lines[1:i]
    return None


def _apply_one(
    memory_dir: str, cand: dict, edge_id: str, pass_id: str
) -> Tuple[bool, str, Optional[dict]]:
    """Apply ONE Tier-A candidate to the working tree. ``(ok, reason, undo_record)``.

    The undo record is what the ledger persists so --undo can reverse this exact edit:
      - bridge/completion: ``{"file", "block": {inserted, wrapper, lead}}``
      - refines:           ``{"file", "block": {...stamp...}, "fm_before", "fm_after"}``
    Nothing is written unless every part of the edit can proceed (per-edge atomicity).
    """
    from .links import add_typed_relation, parse_typed_relations

    src_path = os.path.join(memory_dir, cand["source"] + ".md")
    try:
        with open(src_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except Exception as exc:
        return False, f"source unreadable: {exc}", None

    line = _stamp_line(edge_id, pass_id, cand)

    if cand["kind"] in ("completion", "bridge"):
        # Idempotency re-check against CURRENT text (the discovery snapshot may be stale).
        if cand["target"] in parse_wikilinks(text):
            return False, "edge already present (wikilink)", None
        new_text, block_rec = _insert_block_line(text, line)
        try:
            with open(src_path, "w", encoding="utf-8") as fh:
                fh.write(new_text)
        except Exception as exc:
            return False, f"write failed: {exc}", None
        return True, "", {"file": os.path.basename(src_path), "block": block_rec}

    if cand["kind"] == "refines":
        fm = parse_frontmatter(text)
        existing = parse_typed_relations(fm).get("refines", [])
        if normalize_slug(cand["target"]) in {normalize_slug(t) for t in existing}:
            return False, "edge already present (refines)", None
        region_before = _frontmatter_region(text)
        if region_before is None:
            return False, "no frontmatter — cannot write a typed relation", None
        res = add_typed_relation(src_path, "refines", cand["target"])
        if res.get("error") or not res.get("changed"):
            return False, res.get("error") or "add_typed_relation was a no-op", None
        try:
            with open(src_path, "r", encoding="utf-8") as fh:
                after_fm_text = fh.read()
        except Exception as exc:
            return False, f"re-read failed after frontmatter write: {exc}", None
        region_after = _frontmatter_region(after_fm_text)
        new_text, block_rec = _insert_block_line(after_fm_text, line)
        try:
            with open(src_path, "w", encoding="utf-8") as fh:
                fh.write(new_text)
        except Exception as exc:
            return False, f"write failed: {exc}", None
        return True, "", {
            "file": os.path.basename(src_path),
            "block": block_rec,
            "fm_before": region_before[2],
            "fm_after": region_after[2] if region_after else [],
        }

    return False, f"kind {cand.get('kind')!r} is not Tier-A", None


def run_apply_pass(
    memory_dir: str,
    index_dir: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    *,
    probe_k: Optional[int] = None,
    max_seeds: Optional[int] = None,
    repo_root: Optional[str] = None,
) -> Tuple[int, str]:
    """The DRM-2 loop: discover → gate → apply (capped) → stamp+ledger → digest.

    Preconditions before ANY write (all must hold; each refusal is named in the digest):
    soak bar met; corpus trusted (SEC-1 — autonomy never extends to an unreviewed corpus);
    per-edge: Tier-A kind above the calibrated bar, endpoints non-floor/non-draft, edge not
    already present, secret-lint CLEAN on every generated byte (hard BLOCK — the ratified
    dream-path deviation), provenance complete. Effect is immediate (working tree + index
    rebuild); the commit stays the owner's.
    """
    from . import trust
    from .secrets import scan_text
    from .telemetry import current_session_id

    td = telemetry_dir or default_telemetry_dir(memory_dir)

    # SEC-1: the write path refuses on an untrusted corpus (report-only remains available —
    # like doctor, it is a pre-consent-safe analysis).
    gate_root = trust.gate_repo_root(memory_dir, repo_root)
    if gate_root is not None and not trust.is_trusted(gate_root):
        return 1, (
            "🌙 dream: APPLY REFUSED — this corpus is untrusted (SEC-1). Review and trust "
            "it first (/hippo:doctor → trust flow); the report-only pass (--dry-run) "
            "remains available."
        )

    result = discover(memory_dir, index_dir, td, probe_k=probe_k, max_seeds=max_seeds)
    if result["status"] != "ok":
        return (1 if result["status"] == "no-index" else 0), render_report(
            result, ledger_path=None
        )
    write_candidate_ledger(td, result["pass_id"], result["candidates"])
    write_boost_ledger(td, result["pass_id"], (result.get("reward") or {}).get("edges") or [])

    pass_id = result["pass_id"]
    theta = cofire_theta()
    cap = max_apply_per_pass()
    soak = result.get("soak") or {}
    distinct_now = int(soak.get("distinct_sessions") or 0)
    session_id = current_session_id(td)

    gated = [c for c in result["candidates"] if c.get("kind") in _GATED_KINDS]
    routed = [c for c in result["candidates"] if c.get("kind") in _ROUTED_KINDS]
    eligible = [
        c
        for c in result["candidates"]
        if c.get("kind") in _TIER_A_KINDS and apply_eligible(c, theta=theta)
    ]

    # An undone/retracted pair NEVER auto-re-applies: an undo (owner) or retraction
    # (DRM-4 counterweight) is a standing verdict recorded in the committed ledger, and
    # autonomy must not override the audit record (DREAM-KILL-2's spirit; also the
    # retract→re-apply ping-pong guard the de-parasiting pass depends on). The candidate
    # still appears in report passes — re-applying it is a per-item human/agent action.
    prior_undone = {
        frozenset((e["source"], e["target"]))
        for e in read_apply_ledger(memory_dir)
        if e.get("state") == "undone"
        and isinstance(e.get("source"), str)
        and isinstance(e.get("target"), str)
    }

    applied: List[dict] = []
    refused: List[Tuple[dict, str]] = []
    ledger_lines: List[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for cand in eligible:
        if len(applied) >= cap:
            break
        if frozenset((cand["source"], cand["target"])) in prior_undone:
            refused.append(
                (cand, "pair was undone/retracted before — never auto re-applied "
                       "(re-apply by hand if genuinely wanted)")
            )
            continue
        edge_id = f"{pass_id}-e{len(applied) + 1}"
        ledger_row = {
            "edge_id": edge_id,
            "pass": pass_id,
            "kind": cand["kind"],
            "source": cand["source"],
            "target": cand["target"],
            "cofire": cand.get("cofire"),
            "firing_query": cand.get("query") or "",
            "derives_from": [cand["source"], cand["target"]],
            "applied_at_session": session_id,
            "applied_at_distinct_count": distinct_now,
            "applied_at_ts": now_iso,
            "state": "active",
        }
        # Provenance completeness is a hard precondition (DRM-2.spec.md §2): an edge with
        # a missing field is rejected pre-write.
        if not all(
            ledger_row.get(k) not in (None, "")
            for k in ("edge_id", "pass", "kind", "source", "target")
        ) or ledger_row.get("cofire") is None:
            refused.append((cand, "incomplete provenance"))
            continue
        # HARD secret BLOCK over every byte this edge would put on disk or in the ledger —
        # the stamp line AND the ledger row (the firing query flows into both). Ratified
        # dream-path deviation from secrets.py's WARN default: REFUSED, not warned.
        rationale = _stamp_line(edge_id, pass_id, cand) + "\n" + json.dumps(
            ledger_row, ensure_ascii=False
        )
        findings = scan_text(rationale)
        if findings:
            refused.append((cand, f"secret lint BLOCK: {'; '.join(findings)}"))
            continue
        ok, reason, undo_rec = _apply_one(memory_dir, cand, edge_id, pass_id)
        if not ok:
            refused.append((cand, reason))
            continue
        ledger_row["undo"] = undo_rec
        ledger_lines.append(ledger_row)
        applied.append({**cand, "edge_id": edge_id})

    if ledger_lines:
        try:
            with open(apply_ledger_path(memory_dir), "a", encoding="utf-8") as fh:
                for row in ledger_lines:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as exc:
            # A ledger append failure after a write would orphan stamps — undo the pass.
            for row in reversed(ledger_lines):
                _undo_one_edge(memory_dir, row)
            return 1, f"🌙 dream: ledger append FAILED ({exc}) — pass rolled back, nothing applied."
        _refresh_index_quiet(memory_dir, index_dir)

    # ---- digest -------------------------------------------------------------------- #
    lines = [
        f"🌙 dream pass {pass_id} — applied {len(applied)} edge(s) "
        f"(uncommitted, live in recall; cap {cap}, θ={theta:g}):"
    ]
    if not applied:
        lines[0] = (
            f"🌙 dream pass {pass_id} — applied 0 edges (cap {cap}, θ={theta:g}): "
            "no candidate cleared the Tier-A bar. Empty is the norm."
        )
    glyph = {"bridge": "↔", "completion": "↔", "refines": "→"}
    for a in applied:
        q = (a.get("query") or "")[:40]
        lines.append(
            f"  • {a['source']} {glyph.get(a['kind'], '→')} {a['target']}   {a['kind']}"
            f"  (cofire {float(a.get('cofire') or 0):.2f}"
            + (f', q:"{q}"' if q else "")
            + f")  [{a['edge_id']}]"
        )
    for cand, reason in refused:
        lines.append(
            f"  ✘ refused {cand['source']} → {cand['target']} ({cand['kind']}): {reason}"
        )
    if gated:
        lines.append(
            f"  ⛔ {len(gated)} supersedes candidate(s) GATED — never auto-applied; apply "
            "only by explicit owner action:"
        )
        for c in gated[:5]:
            lines.append(f"     • {c['source']} supersedes {c['target']} (cofire {c['cofire']:.2f})")
    if routed:
        lines.append(
            f"  ↪ {len(routed)} contradicts candidate(s) routed to /hippo:resolve — never auto."
        )
    if applied:
        lines.append(
            f"  reply `undo` to revert all · `undo <edge-id>` for one · they age into "
            f"/dream's trusted source set after {age_sessions()} sessions"
        )
    return 0, "\n".join(lines)


def _refresh_index_quiet(memory_dir: str, index_dir: Optional[str]) -> None:
    try:
        from .build_index import default_index_dir, refresh_index

        refresh_index(memory_dir, index_dir or default_index_dir(memory_dir))
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# DRM-2 — undo (git-native reversibility, made one command)
# --------------------------------------------------------------------------- #
def _undo_one_edge(memory_dir: str, edge: dict) -> Tuple[bool, str]:
    """Reverse ONE applied edge's exact edit. ``(ok, reason)``; refuse-on-drift.

    Mechanics mirror apply in reverse, verified byte-exactly before any write:
      1. the stamped block line must exist EXACTLY as inserted (else: manual drift → refuse);
      2. for refines, the current frontmatter region must equal the recorded ``fm_after``
         (else drift → refuse) and is replaced with ``fm_before``;
      3. after removing the line, a block THIS edge created is removed entirely IF no other
         dream line remains in it (restoring the pre-pass bytes).
    A refusal writes NOTHING for this edge (report-then-skip, never clobber a human edit).
    """
    undo = edge.get("undo") or {}
    fname = undo.get("file")
    block = undo.get("block") or {}
    inserted = block.get("inserted")
    if not fname or not inserted:
        return False, "ledger row carries no undo record"
    path = os.path.join(memory_dir, fname)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except Exception as exc:
        return False, f"unreadable: {exc}"

    if text.count(inserted) != 1:
        return False, "stamped line missing or altered on disk (manual drift) — refusing"

    new_text = text
    # Refines: reverse the frontmatter edit first (verified against the recorded state).
    if "fm_before" in undo:
        region = _frontmatter_region(new_text)
        if region is None:
            return False, "frontmatter missing (manual drift) — refusing"
        start, end, fm_lines = region
        if fm_lines != undo.get("fm_after"):
            return False, "frontmatter drifted since apply — refusing (undo it by hand or git)"
        all_lines = new_text.split("\n")
        new_text = "\n".join(all_lines[:start] + list(undo["fm_before"]) + all_lines[end:])
        if new_text.count(inserted) != 1:
            return False, "stamp line lost while reversing frontmatter — refusing"

    new_text = new_text.replace(inserted, "", 1)

    # Remove a block this edge created if nothing else lives in it now.
    from .links import DREAM_BLOCK_CLOSE, DREAM_BLOCK_OPEN

    if block.get("wrapper"):
        empty_block = f"{block.get('lead', '')}{DREAM_BLOCK_OPEN}\n{DREAM_BLOCK_CLOSE}\n"
        if empty_block in new_text:
            new_text = new_text.replace(empty_block, "", 1)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
    except Exception as exc:
        return False, f"write failed: {exc}"
    return True, ""


def undo_edges(
    memory_dir: str,
    index_dir: Optional[str] = None,
    *,
    edge_id: Optional[str] = None,
    since: Optional[str] = None,
    edge_ids: Optional[List[str]] = None,
    annotate: Optional[dict] = None,
) -> Tuple[int, str]:
    """``--undo`` (latest pass) / ``--undo <edge-id>`` / ``--undo-since <ISO date|N>``.

    Reverts in reverse-apply order (so same-file edits compose back byte-for-byte), appends
    superseding ``state: "undone"`` ledger lines (append-only audit — history intact), and
    rebuilds the index. Refuse-on-drift is PER EDGE: a hand-edited stamp refuses with a
    report while clean edges still revert; exit 1 signals any refusal.

    ``edge_ids`` selects several specific edges in ONE call (one ledger append + one index
    rebuild) — the DRM-4 retraction entry point, which is why there is no second undo
    implementation anywhere. ``annotate`` merges extra provenance keys into each
    superseding ledger line (e.g. ``retracted_by``/``retract_reason``); the canonical
    ``edge_id``/``pass``/``state``/``undone_at_ts`` fields always win over it.
    """
    ledger = read_apply_ledger(memory_dir)
    active = [e for e in ledger if e.get("state") == "active"]
    if not active:
        return 0, "🌙 dream --undo: no active dream edges to revert."

    if edge_ids:
        wanted = {str(x) for x in edge_ids}
        targets = [e for e in active if e.get("edge_id") in wanted]
        if not targets:
            return 1, "🌙 dream --undo: none of the requested edges are ACTIVE (see dream --log)."
    elif edge_id:
        targets = [e for e in active if e.get("edge_id") == edge_id]
        if not targets:
            return 1, f"🌙 dream --undo: no ACTIVE edge {edge_id!r} (see dream --log)."
    elif since:
        if re.fullmatch(r"\d+", since):
            # last N distinct sessions, via the same derived count aging uses
            td = default_telemetry_dir(memory_dir)
            now = int(soak_status(td, memory_dir=memory_dir).get("distinct_sessions") or 0)
            window = int(since)
            targets = [
                e
                for e in active
                if isinstance(e.get("applied_at_distinct_count"), int)
                and now - e["applied_at_distinct_count"] < window
            ]
        else:
            targets = [e for e in active if str(e.get("applied_at_ts") or "") >= since]
        if not targets:
            return 0, f"🌙 dream --undo-since {since}: nothing in that window."
    else:
        last_pass = active[-1].get("pass")
        targets = [e for e in active if e.get("pass") == last_pass]

    undone: List[dict] = []
    refused: List[Tuple[dict, str]] = []
    for edge in reversed(targets):
        ok, reason = _undo_one_edge(memory_dir, edge)
        (undone if ok else refused).append((edge, reason) if not ok else edge)

    if undone:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            with open(apply_ledger_path(memory_dir), "a", encoding="utf-8") as fh:
                for edge in undone:
                    fh.write(
                        json.dumps(
                            {
                                **(annotate or {}),
                                "edge_id": edge["edge_id"],
                                "pass": edge.get("pass"),
                                "state": "undone",
                                "undone_at_ts": now_iso,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
        except Exception as exc:
            return 1, f"🌙 dream --undo: reverted {len(undone)} edge(s) but the ledger append failed: {exc}"
        _refresh_index_quiet(memory_dir, index_dir)

    lines = [f"🌙 dream --undo: reverted {len(undone)} edge(s)" + (":" if undone else ".")]
    for edge in undone:
        lines.append(f"  • {edge['edge_id']}  {edge.get('source')} ↔ {edge.get('target')} restored")
    for edge, reason in refused:
        lines.append(f"  ✘ {edge.get('edge_id')}: {reason}")
    if refused:
        lines.append("  (refused edges are untouched — resolve by hand or `git checkout`.)")
    return (1 if refused else 0), "\n".join(lines)


def render_log(memory_dir: str) -> str:
    """``dream --log``: every edge's current state (active / aged-in / undone), oldest first."""
    ledger = read_apply_ledger(memory_dir)
    if not ledger:
        return "🌙 dream --log: no dream edges have ever been applied here."
    td = default_telemetry_dir(memory_dir)
    now = int(soak_status(td, memory_dir=memory_dir).get("distinct_sessions") or 0)
    lines = [f"🌙 dream --log — {len(ledger)} edge(s), distinct sessions now {now}:"]
    for e in ledger:
        state = e.get("state")
        if state == "active":
            state = "aged-in" if edge_aged_in(e, now) else (
                f"active ({max(0, age_sessions() - (now - e.get('applied_at_distinct_count', now)))}"
                " session(s) to age-in)"
            )
        lines.append(
            f"  • {e.get('edge_id')}  {e.get('source')} → {e.get('target')}  "
            f"{e.get('kind')}  cofire={e.get('cofire')}  [{state}]"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# DRM-2 — the SessionStart notify surface (deferred half of notify-with-undo)
# --------------------------------------------------------------------------- #
_PRODUCER_MAX_ITEMS = 20


def dream_applied_producer(memory_dir: str, repo_root: str, ctx=None) -> Optional[str]:
    """SessionStart producer: dream edges applied but NOT yet aged in, with the undo handle.

    Aged-in edges drop off (implicit ratification by non-undo — they are trusted now);
    undone edges never appear. Silent (None) when there is nothing in the window, exactly
    like every other quiet-by-default producer. ``ctx`` (LIF-6 RunContext) is unused —
    declared so every producer shares ONE call shape. Read-only; never raises.
    """
    try:
        ledger = read_apply_ledger(memory_dir)
        active = [e for e in ledger if e.get("state") == "active"]
        if not active:
            return None
        td = default_telemetry_dir(memory_dir)
        now = int(soak_status(td, memory_dir=memory_dir).get("distinct_sessions") or 0)
        fresh = [e for e in active if not edge_aged_in(e, now)]
        if not fresh:
            return None
        window = age_sessions()
        lines = [
            f"🌙 dream applied {len(fresh)} edge(s) awaiting age-in (each becomes trusted "
            f"/dream source after {window} sessions un-undone; revert any with "
            "`python -m memory.dream --undo <edge-id>` or all recent with --undo-since):"
        ]
        for e in fresh[:_PRODUCER_MAX_ITEMS]:
            left = window - (now - e.get("applied_at_distinct_count", now))
            lines.append(
                f"  • {e.get('edge_id')}  {e.get('source')} → {e.get('target')} "
                f"({e.get('kind')}, cofire {e.get('cofire')}, {max(0, left)} session(s) left)"
            )
        if len(fresh) > _PRODUCER_MAX_ITEMS:
            lines.append(f"  …and {len(fresh) - _PRODUCER_MAX_ITEMS} more (dream --log).")
        return "\n".join(lines)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Pass orchestration + CLI
# --------------------------------------------------------------------------- #
def run_report_pass(
    memory_dir: str,
    index_dir: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    *,
    probe_k: Optional[int] = None,
    max_seeds: Optional[int] = None,
) -> Tuple[int, str]:
    """DRM-1's entry: discover → write the candidate ledger → render the report.

    Returns ``(exit_code, report_text)``. Exit 0 on an ok pass (even an empty one — empty is
    the norm) AND on a legible refusal (below-soak / empty-corpus: correct outcomes, not
    errors); 1 only on a genuine failure (no index).
    """
    td = telemetry_dir or default_telemetry_dir(memory_dir)
    result = discover(memory_dir, index_dir, td, probe_k=probe_k, max_seeds=max_seeds)
    ledger = None
    if result["status"] == "ok":
        ledger = write_candidate_ledger(td, result["pass_id"], result["candidates"])
        write_boost_ledger(td, result["pass_id"], (result.get("reward") or {}).get("edges") or [])
    text = render_report(result, ledger_path=ledger)
    code = 1 if result["status"] == "no-index" else 0
    return code, text


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    from .provenance import resolve_dirs

    parser = argparse.ArgumentParser(
        prog="memory.dream",
        description=(
            "/dream — the generative sleep pass: replay the corpus against itself and "
            "surface latent graph edges (DRM-1: report-only, zero memory writes)."
        ),
    )
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--index-dir", default=None)
    parser.add_argument("--telemetry-dir", default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="explicit report-only pass (the shipped default — auto-apply is OFF pending "
        "the dated owner flip; see ROADMAP.dream.yaml owner_decisions)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="run the DRM-2 Tier-A auto-apply loop this pass (capped, θ/mutuality-gated, "
        "stamped, undoable; never commits). Also enabled by HIPPO_DREAM_APPLY=1.",
    )
    parser.add_argument(
        "--undo",
        nargs="?",
        const="",
        default=None,
        metavar="EDGE_ID",
        help="revert applied dream edges: bare --undo reverts the latest pass; "
        "--undo <edge-id> exactly one. Byte-exact; refuses on manual drift.",
    )
    parser.add_argument(
        "--undo-since",
        default=None,
        metavar="DATE|N",
        help="revert edges applied since an ISO date, or within the last N distinct sessions",
    )
    parser.add_argument(
        "--log", action="store_true", help="list every dream edge (active / aged-in / undone)"
    )
    parser.add_argument(
        "--deparasite",
        action="store_true",
        help="DRM-4: the de-parasiting counterweight — report per-memory out-degree, flag "
        "hubs over DREAM_MAX_OUT_DEGREE, and PROPOSE retractions (dream's own un-aged "
        "edges) vs gated demotions/dedup-merges. Report/propose only; zero memory writes.",
    )
    parser.add_argument(
        "--retract",
        action="store_true",
        help="with --deparasite: additionally EXECUTE the Tier-A lane — retract the "
        "flagged, un-aged dream edges via the byte-exact undo machinery. Human "
        "structures and aged-in edges stay gated regardless.",
    )
    parser.add_argument(
        "--dedup-merge",
        nargs=2,
        metavar=("SURVIVOR", "LOSER"),
        default=None,
        help="execute ONE ratified dedup-merge proposal (per-item, no batch): SURVIVOR "
        "gains supersedes:[LOSER], LOSER's validity window closes (set_invalid_after). "
        "Non-lossy — additive frontmatter only, no body byte touched, nothing deleted.",
    )
    parser.add_argument("--probe-k", type=int, default=None, help="co-fire probe depth (default 10)")
    parser.add_argument(
        "--max-seeds", type=int, default=None, help="cap the replay worklist (default 0 = all)"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the raw discovery result as JSON instead of the report"
    )
    args = parser.parse_args(argv)

    memory_dir = args.memory_dir
    if memory_dir is None:
        memory_dir, _ = resolve_dirs()

    if args.log:
        print(render_log(memory_dir))
        return 0
    if args.deparasite:
        from .deparasite import run_deparasite_pass

        code, text = run_deparasite_pass(
            memory_dir, args.index_dir, args.telemetry_dir, retract=args.retract
        )
        print(text)
        return code
    if args.retract:
        print("🧹 --retract is a --deparasite modifier — run `dream --deparasite --retract`.")
        return 1
    if args.dedup_merge:
        from .deparasite import apply_dedup_merge

        survivor, loser = args.dedup_merge
        res = apply_dedup_merge(
            memory_dir,
            survivor,
            loser,
            telemetry_dir=args.telemetry_dir,
            index_dir=args.index_dir,
        )
        if res.get("error"):
            print(f"🧹 dedup-merge REFUSED: {res['error']}")
            return 1
        print(
            f"🧹 dedup-merge applied (non-lossy, reversible): {survivor} now supersedes "
            f"{loser}; {loser} invalid_after {res['invalid_after']['ts']}. Both files "
            "remain on disk; commit stays yours."
        )
        return 0
    if args.undo is not None or args.undo_since:
        code, text = undo_edges(
            memory_dir,
            args.index_dir,
            edge_id=(args.undo or None),
            since=args.undo_since,
        )
        print(text)
        return code
    # --json is a READ surface (raw discovery dump) — it never applies unless --apply is
    # explicit, regardless of the shipped default.
    if args.apply or (apply_mode_default() and not args.dry_run and not args.json):
        code, text = run_apply_pass(
            memory_dir,
            args.index_dir,
            args.telemetry_dir,
            probe_k=args.probe_k,
            max_seeds=args.max_seeds,
        )
        print(text)
        return code

    if args.json:
        td = args.telemetry_dir or default_telemetry_dir(memory_dir)
        result = discover(
            memory_dir, args.index_dir, td, probe_k=args.probe_k, max_seeds=args.max_seeds
        )
        if result["status"] == "ok":
            write_candidate_ledger(td, result["pass_id"], result["candidates"])
            write_boost_ledger(
                td, result["pass_id"], (result.get("reward") or {}).get("edges") or []
            )
        print(json.dumps(result, indent=2, ensure_ascii=False, default=list))
        return 1 if result["status"] == "no-index" else 0

    code, text = run_report_pass(
        memory_dir,
        args.index_dir,
        args.telemetry_dir,
        probe_k=args.probe_k,
        max_seeds=args.max_seeds,
    )
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
