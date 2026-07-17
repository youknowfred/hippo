"""/dream tunables + gates (the DRM-1/2/5/C knob surface; decomposed out of ``dream.py``).

The env-overridable calibration surface — θ, the per-pass cap, the aging window, the
DRM-5 reward weight, the DRM-C opt-in flag family — and ``apply_eligible``, the
calibrated Tier-A auto-apply bar. Every name here re-exports via the ``dream`` façade.
"""

from __future__ import annotations

import os
from typing import Optional

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

# --------------------------------------------------------------------------- #
# DRM-C (contradiction discovery) tunables — OPT-IN, default OFF.
#
# Cofire (cosine/Jaccard-family similarity over replayed self-queries) can say two memories
# are ABOUT the same thing; it can never say they DISAGREE — semantic conflict needs actual
# comprehension. DRM-C adds exactly that: one bounded small-model call per HIGH-cofire pair
# ("do these conflict in substance, or merely relate?"), reusing the SAME pair surface the
# pass already computed (``result["pairs"]``) so candidate volume stays bounded by dream's
# existing "worth considering" filter. Propose-only, Tier-C by construction: ``contradicts``
# is already in ``_ROUTED_KINDS`` (→ the /hippo:resolve inbox, never auto) and
# ``apply_eligible`` never admits it — there is no Tier-A here because nothing about a
# contradiction has the "direct text evidence" that lets completions auto-apply.
# --------------------------------------------------------------------------- #
# LLM attempts per pass (cost/wall-clock bound — attempts, not successes, so a dead
# endpoint can't stall a pass timeout-by-timeout). Single-digit default, hard-capped.
_DEFAULT_CONTRA_MAX_PAIRS = 6
_HARD_CONTRA_MAX_PAIRS = 12
# Seconds per LLM call (dream is an offline deliberate verb — roomier than a hook budget).
_DEFAULT_CONTRA_TIMEOUT_S = 10.0
# Verbatim text per side shown to the model (frontmatter included — name + description are
# exactly the claim summary a conflict judgment needs).
_CONTRA_SIDE_CHARS = 1_500


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
    """``DREAM_REWARD_WEIGHT`` (≥0) — per-hit rank nudge for DRM-5 boosts (default 0.01)."""
    return max(0.0, _env_float("DREAM_REWARD_WEIGHT", _DEFAULT_REWARD_WEIGHT))


def _llm_file_setting(key: str):
    """One key from ``~/.claude/hippo-llm.json`` via the llm_client seam; None on trouble."""
    try:
        from . import llm_client

        return llm_client.file_setting(key)
    except Exception:
        return None


def contradictions_enabled() -> bool:
    """The DRM-C flag — DEFAULT OFF. Env ``HIPPO_DREAM_CONTRADICTIONS`` > config file > off.

    A SET env var decides entirely (truthy enables, anything else is an explicit off, so
    ``HIPPO_DREAM_CONTRADICTIONS=0`` overrides a config file that says on); an UNSET one
    defers to ``dream_contradictions`` in ``hippo-llm.json``. Junk stays off (the
    ``generative_enabled`` convention).
    """
    env = os.environ.get("HIPPO_DREAM_CONTRADICTIONS")
    if env is not None and env.strip():
        return env.strip() in ("1", "true", "True")
    try:
        from . import llm_client

        return llm_client.as_bool(llm_client.file_setting("dream_contradictions"))
    except Exception:
        return False


def contra_max_pairs() -> int:
    """LLM attempts per pass — env ``DREAM_CONTRA_MAX_PAIRS`` > config ``contra_max_pairs``
    > 6; clamped to [0, 12] regardless of source (the hard max is not overridable)."""
    raw = os.environ.get("DREAM_CONTRA_MAX_PAIRS", "").strip()
    val = None
    if raw:
        try:
            val = int(raw)  # an explicit env value wins outright, even a negative one
        except ValueError:
            val = None
    if val is None:
        cfg = _llm_file_setting("contra_max_pairs")
        val = cfg if isinstance(cfg, int) and not isinstance(cfg, bool) else _DEFAULT_CONTRA_MAX_PAIRS
    return max(0, min(val, _HARD_CONTRA_MAX_PAIRS))


def contra_min_cofire() -> float:
    """The "high-cofire" bar for DRM-C — env ``DREAM_CONTRA_MIN_COFIRE`` > config
    ``contra_min_cofire`` > θ.

    Reusing ``cofire_theta`` by default keeps "which pairs are even worth an LLM call"
    consistent with the pass's existing calibrated notion of a strong pair.
    """
    val = _env_float("DREAM_CONTRA_MIN_COFIRE", float("nan"))
    if val == val:  # not NaN — the env var parsed
        return val
    cfg = _llm_file_setting("contra_min_cofire")
    if isinstance(cfg, (int, float)) and not isinstance(cfg, bool):
        return float(cfg)
    return cofire_theta()


def contra_llm_timeout() -> float:
    """Per-call cap (seconds) — env ``HIPPO_DREAM_LLM_TIMEOUT`` > config
    ``dream_timeout_s`` > 10.0; nonpositive/malformed falls to the default."""
    val = _env_float("HIPPO_DREAM_LLM_TIMEOUT", float("nan"))
    if val != val:  # NaN — no env var; try the config file
        cfg = _llm_file_setting("dream_timeout_s")
        val = float(cfg) if isinstance(cfg, (int, float)) and not isinstance(cfg, bool) else _DEFAULT_CONTRA_TIMEOUT_S
    return val if val > 0 else _DEFAULT_CONTRA_TIMEOUT_S


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
