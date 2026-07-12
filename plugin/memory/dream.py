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

    # Replay worklist — over-sample under-connected, under-consolidated traces: firewalled
    # degree ascending (isolates first), then usage-sessions ascending (cold first).
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
        key=lambda s: (len(fw_view.get(s, ())), _usage_sessions(s), s),
    )
    if max_seeds and max_seeds > 0:
        worklist = worklist[:max_seeds]

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

    candidates.sort(key=lambda c: (c["kind"] != "completion", -c["cofire"], c["source"], c["target"]))

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
        "kind_counts": kind_counts,
        "cofire_strengths_all_pairs": all_strengths,
        "cofire_strengths_candidates": sorted((c["cofire"] for c in candidates), reverse=True),
        "theta_sweep": theta_sweep,
        "theta_current": cofire_theta(),
        "cap_current": max_apply_per_pass(),
    }
    result["candidates"] = candidates
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
    lines.append(
        "   auto-apply is OFF (report-only) — the DRM-2 flip is a dated owner decision "
        "after this calibration."
    )
    return "\n".join(lines)


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
        help="explicit report-only pass (the shipped default — no flag needed; kept for "
        "legibility and forward-compat with the DRM-2 apply mode)",
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

    if args.json:
        td = args.telemetry_dir or default_telemetry_dir(memory_dir)
        result = discover(
            memory_dir, args.index_dir, td, probe_k=args.probe_k, max_seeds=args.max_seeds
        )
        if result["status"] == "ok":
            write_candidate_ledger(td, result["pass_id"], result["candidates"])
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
