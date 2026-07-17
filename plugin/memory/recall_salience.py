"""Recall's bounded ranking priors: RET-5 salience fusion (recency/usage/staleness),
RET-14's outcome prior, and the RET-6 verify-at-use stale-banner map. Decomposed out
of ``recall.py`` as pure code motion; every symbol stays importable at
``memory.recall.<name>`` via the façade's explicit re-exports."""

from __future__ import annotations

import os
import time
from typing import Dict, List, Optional, Tuple

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
# RET-14 (owner-directed): the outcome prior — KPI-2's "was this memory injected AND then
# actually used" evidence (outcome.injection_hits, persisted via write_outcome_cache),
# folded in as its OWN bounded ranking prior, gated by ITS OWN flag (``HIPPO_OUTCOME_PRIOR``,
# default OFF) — deliberately SEPARATE from ``HIPPO_SALIENCE`` rather than a fourth term
# inside ``_apply_salience``. Two reasons: (1) RET-10 measured recency/usage moving nothing
# on the golden eval and the owner does not want a real signal's measurement entangled with
# two that already tested inert; (2) usage/recency are POPULARITY/AGE proxies, while KPI-2
# is actual outcome evidence (the cited file got touched after injection) — a qualitatively
# different signal worth its own on/off switch to evaluate independently. Same bounded-
# multiplier, pre-cut posture as every other prior here: relevance dominates, this only
# nudges within it. Saturates at _OUTCOME_PRIOR_SATURATION corroborating sessions -- a
# memory with that much positive evidence already earns the full (small) nudge; more
# sessions add no further boost, so a heavily-used memory can't compound this indefinitely.
_OUTCOME_PRIOR_CAP = 0.10
_OUTCOME_PRIOR_SATURATION = 3

# --------------------------------------------------------------------------- #
# RET-6: verify-at-use banner — a currently-stale injected memory carries a one-line
# "anchored to <sha>; N cited files changed since — verify before relying" banner on its
# rendered pointer, sourced from LIF-6's persisted stale.json (advisory, SessionStart-derived
# -- NEVER a git call on this hot path; absent/corrupt cache -> no banners for anyone).
# UNLIKE RET-5's salience fusion, this is NOT gated behind a flag and does NOT touch ranking
# or score at all -- staleness here is a correctness signal a user reading the injected
# pointer should always see, not a ranking nudge someone might opt out of. Reinforcement
# (clearing the banner) needs no NEW machinery: `reconsolidate.semantic_reverify`'s
# graduate/fix outcomes already re-baseline `source_commit` to HEAD via
# `provenance.reverify_file` -- so a reinforced memory simply drops out of the NEXT
# SessionStart's `find_stale` scan (and thus `stale.json`), and is rendered bannerless from
# then on, exactly like a memory that was never stale. See `_stale_banner_map` below.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# RET-5: salience fusion — recency / usage / staleness (see the constants block above for
# the caps and why they're small). DEFAULT OFF; every reader here degrades to "no signal"
# (never a hard error) on any missing/corrupt input, matching the graph readers' posture.
# --------------------------------------------------------------------------- #
def _salience_enabled() -> bool:
    """True only when ``HIPPO_SALIENCE`` is explicitly truthy — DEFAULT OFF. Mirrors
    ``build_index.dense_disabled()``'s falsy set so ``HIPPO_SALIENCE=0``/``false`` reads as
    an explicit opt-out, not a truthy string.

    RET-10 / OQ-10 (resolved 2026-07-10): default OFF is now a DECISION, not a "ship behind a
    flag first" placeholder. Running the RET-8 category-tagged eval both ways on the golden
    corpus produced IDENTICAL recall@10 / mrr@10 — salience's usage and staleness terms are
    zero on a corpus with no usage telemetry and no staleness baselines, so the eval cannot
    exercise it; "no regression" was vacuous, and defaulting-on would ship an unmeasured
    ranking change. The owner resolved OQ-10 as default-OFF (revisit only with a
    salience-exercising eval or field evidence). ``test_salience_enabled_default_off_and_env_parsing``
    pins this default so it cannot silently drift.
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


def _stale_banner_map(index_dir: Optional[str]) -> Dict[str, str]:
    """Name -> RET-6's one-line verify-at-use banner text, from LIF-6's persisted
    ``stale.json`` (``staleness.read_stale_cache``) — advisory, SessionStart-derived: an
    absent/corrupt cache degrades to ``{}`` (no banners for anyone), NEVER a git call on this
    hot path (mirrors ``_staleness_penalty_map``'s read, but UNCONDITIONAL — this runs
    regardless of ``_salience_enabled()``, since a correctness banner is not a ranking knob).
    A memory is banner-eligible purely by PRESENCE in the cache; the exact wording is the
    roadmap's own: ``"anchored to <sha>; N cited files changed since — verify before
    relying"``, pulling both ``<sha>`` and ``N`` straight from the cache's ``sha``/``changed``
    fields (LIF-6 already wrote both — no writer/schema change needed here). A record with no
    usable ``sha`` is skipped (a blank anchor is worse than no banner). CLB-3 upgrade: names
    in the cache's optional ``evidence_drift`` field get the match level appended (or a
    standalone banner when only their quoted evidence — not their cited-path timeline —
    drifted); the field's ABSENCE leaves every banner byte-identical to pre-CLB-3. Never
    raises; ``{}`` when ``index_dir`` is falsy or the cache is missing/empty/corrupt.
    """
    if not index_dir:
        return {}
    try:
        from .staleness import read_evidence_drift, read_stale_cache

        stale = read_stale_cache(index_dir)
        out: Dict[str, str] = {}
        for name, rec in (stale or {}).items():
            if not isinstance(rec, dict):
                continue
            sha = rec.get("sha")
            if not isinstance(sha, str) or not sha:
                continue  # no anchor to name -- degrade to no banner rather than a blank one
            changed = rec.get("changed")
            if not isinstance(changed, int) or isinstance(changed, bool) or changed <= 0:
                changed = 1  # present in stale.json at all -> at least one, same floor as the salience penalty
            out[name] = (
                f"anchored to {sha}; {changed} cited files changed since — verify before relying"
            )
        for name, rec in sorted(read_evidence_drift(index_dir).items()):
            missing = rec.get("missing")
            if not isinstance(missing, int) or isinstance(missing, bool) or missing <= 0:
                continue  # whitespace-only or malformed — deliberately NOT drift (inv3)
            fences = rec.get("fences")
            if not isinstance(fences, int) or isinstance(fences, bool) or fences < missing:
                fences = missing
            ws = rec.get("whitespace")
            ws_note = (
                f", {ws} more match only whitespace-normalized"
                if isinstance(ws, int) and not isinstance(ws, bool) and ws > 0
                else ""
            )
            evi = (
                f"quoted evidence drift: {missing} of {fences} marked hunk(s) no longer "
                f"match the tree{ws_note} — verify before reuse"
            )
            out[name] = f"{out[name]}; {evi}" if name in out else evi
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


def _outcome_prior_enabled() -> bool:
    """True only when ``HIPPO_OUTCOME_PRIOR`` is explicitly truthy — DEFAULT OFF, same
    falsy-set convention as ``_salience_enabled()``, but its OWN independent flag (see the
    ``_OUTCOME_PRIOR_*`` constants' comment for why this isn't just a fourth salience term)."""
    raw = os.environ.get("HIPPO_OUTCOME_PRIOR", "").strip()
    return raw not in ("", "0", "false", "False")


def _outcome_boost_map(index_dir: Optional[str]) -> Dict[str, float]:
    """Name -> bounded ``[0, _OUTCOME_PRIOR_CAP]`` outcome prior from RET-14's persisted
    ``outcome.json`` (``outcome.read_outcome_cache``) — ONE small JSON read, the same cost
    class as ``stale.json``/``links.json``, NEVER the live episode/outcome ledger join on
    this hot path (that join is ``outcome.injection_hits``, run once per SessionStart and
    cached). Advisory: absent/corrupt cache -> ``{}`` (no boost for anyone). Never raises.
    """
    if not index_dir:
        return {}
    try:
        from .outcome import read_outcome_cache

        cache = read_outcome_cache(index_dir)
        if not cache:
            return {}
        out: Dict[str, float] = {}
        for name, rec in cache.items():
            hits = rec.get("hits") if isinstance(rec, dict) else None
            if not isinstance(hits, int) or isinstance(hits, bool) or hits <= 0:
                continue
            out[name] = _OUTCOME_PRIOR_CAP * min(1.0, hits / _OUTCOME_PRIOR_SATURATION)
        return out
    except Exception:
        return {}


def _apply_outcome_prior(
    penalized: List[Tuple[int, float, Optional[str]]],
    entries: List[dict],
    *,
    index_dir: Optional[str],
) -> Tuple[List[Tuple[int, float, Optional[str]]], Dict[int, float]]:
    """Fold RET-14's outcome prior into ``penalized``'s score, same pre-cut posture as
    ``_apply_salience`` (so an outcome-boosted memory can compete for graph-expansion seed
    slots too) — but a SEPARATE function/flag (``HIPPO_OUTCOME_PRIOR``), not a term inside
    ``_apply_salience`` itself, so it can be measured independently (see the constants'
    comment). Composes multiplicatively with whatever ``_apply_salience`` already did to
    ``penalized`` when BOTH flags are on — each still individually capped and small, so the
    combined swing stays in the same "break a near-tie, never override real relevance" class.

    Returns ``(re-sorted list, {entry index: outcome boost float})`` — the COR-8 true-score
    breakdown convention every other prior here follows. Only called when
    ``_outcome_prior_enabled()``; never raises (fails open to the untouched input list and
    an empty component map).
    """
    try:
        boost_map = _outcome_boost_map(index_dir)
        if not boost_map:
            return penalized, {}
        components: Dict[int, float] = {}
        adjusted: List[Tuple[int, float, Optional[str]]] = []
        for i, score, state in penalized:
            boost = boost_map.get(entries[i].get("name"), 0.0)
            adjusted.append((i, score * (1.0 + boost), state))
            if boost:
                components[i] = round(boost, 4)
        adjusted.sort(key=lambda triple: triple[1], reverse=True)
        return adjusted, components
    except Exception:
        return penalized, {}
