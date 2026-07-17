"""TMB-1's per-pair evidence card — decomposed out of ``resolve_view.py`` (the façade
keeps the inbox, the ledger, and the verdict engine; it re-imports these names).

Deterministic, read-only adjudication aid: git-mined conflict age, the git-newer side,
cached cited-code drift, usage asymmetry above a stated floor, and a prefill suggestion
expressed STRICTLY in the resolve skill's four verdict names + ``abstain``. Git-mined on
demand (inv1: no new persisted state; git history IS the birth record) and confined to
the describe()/--list cold path (inv6: the SessionStart contradiction producer and the
recall hot path never reach these git calls — pinned). Freshness comes from
``provenance.git_last_commit_with_time`` DIRECTLY: importing reconsolidate here would
put the demote engine one typo away from the read-only listing half (the
no-corpus-write AST pin's whole point).
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

# TMB-1: the ONE verdict taxonomy — exactly the four names the /hippo:resolve skill and
# the resolve MCP tool expose, plus the explicit no-suggestion token. The evidence card's
# prefill is expressed STRICTLY in these; there is deliberately no second vocabulary.
_VERDICT_NAMES = ("keep_one", "scope_both", "merge", "not_conflicting")
_ABSTAIN = "abstain"

# TMB-1: usage asymmetry is only evidence once the ledger has seen enough distinct
# sessions to mean anything — below this floor the column is withheld (labeled, not
# silently dropped). Same 5-session rhythm as soak.SOAK_GATE_SESSIONS /
# reconsolidate._SNOOZE_WINDOW_SESSIONS; a plain module constant, no env knob.
_EVIDENCE_USAGE_MIN_SESSIONS = 5


# --------------------------------------------------------------------------- #
# TMB-1: the per-pair evidence card — a deterministic, read-only adjudication aid.
# Git-mined on demand (inv1: no new persisted state; git history IS the birth record),
# and confined to this module's describe()/--list cold path (inv6: the SessionStart
# contradiction producer and the recall hot path never reach these git calls — pinned).
# Freshness comes from provenance.git_last_commit_with_time DIRECTLY: importing
# reconsolidate here would put the demote engine one typo away from the read-only
# listing half (the no-corpus-write AST pin's whole point).
# --------------------------------------------------------------------------- #
def _memory_rel_path(name: str, memory_dir: str, repo_root: str) -> Optional[str]:
    """``<name>.md``'s repo-relative path, or ``None`` when outside the repo."""
    try:
        rel = os.path.relpath(
            os.path.realpath(os.path.join(memory_dir, f"{name}.md")),
            os.path.realpath(repo_root),
        )
        return None if rel.startswith("..") else rel.replace(os.sep, "/")
    except Exception:
        return None


def _edge_birth_commits_ago(
    declared_by: List[str], other_of: Dict[str, str], memory_dir: str, repo_root: str
) -> Optional[int]:
    """Commits since the ``contradicts`` declaration was introduced, or ``None`` (unknown).

    Git-mined, zero persisted state: pickaxe (``-S<counterpart>``) finds the OLDEST commit
    that changed the counterpart-name's occurrence count in the declaring file — the
    commit that introduced the reference — then ``rev-list --count <sha>..HEAD`` is the
    age in commits. An approximation by design (a body mention of the counterpart that
    predates the edge line reads as the birth), honest for an evidence card. With BOTH
    sides declaring, the OLDER introduction wins (the conflict has existed since the
    first declaration). ``None`` — rendered "age unknown" — for uncommitted files,
    shallow/rewritten history, a proposal-only pair (nothing declared), or any git
    failure. Never raises.
    """
    try:
        from .provenance import run_git

        best: Optional[int] = None
        for side in declared_by or []:
            other = other_of.get(side)
            rel = _memory_rel_path(side, memory_dir, repo_root)
            if not other or not rel:
                continue
            log = run_git(
                ["log", "--reverse", "--format=%H", f"-S{other}", "--", rel], repo_root
            )
            sha = log.split("\n")[0].strip() if log.strip() else ""
            if not sha:
                continue
            count = run_git(["rev-list", "--count", f"{sha}..HEAD"], repo_root).strip()
            try:
                n = int(count)
            except ValueError:
                continue
            best = n if best is None else max(best, n)
        return best
    except Exception:
        return None


def _git_newer_side(
    a: str, b: str, memory_dir: str, repo_root: str
) -> Tuple[Optional[str], Dict[str, Optional[int]]]:
    """``(newer_side_or_None, {name: last-commit epoch})`` — the freshness leg.

    ``provenance.git_last_commit_with_time`` on both ``.md`` files (their last-edit
    moments in history) — NEVER via reconsolidate (see the section comment). ``None``
    newer-side when either file is uncommitted/outside the repo or the epochs tie —
    unknown stays unknown, never a coin flip. Never raises.
    """
    epochs: Dict[str, Optional[int]] = {a: None, b: None}
    try:
        from .provenance import git_last_commit_with_time

        for side in (a, b):
            rel = _memory_rel_path(side, memory_dir, repo_root)
            if rel is None:
                continue
            _sha, epoch = git_last_commit_with_time(rel, repo_root)
            epochs[side] = epoch
        ea, eb = epochs[a], epochs[b]
        if ea is not None and eb is not None and ea != eb:
            return (a if ea > eb else b), epochs
        return None, epochs
    except Exception:
        return None, epochs


def _drift_evidence(a: str, b: str, memory_dir: str, index_dir: Optional[str]) -> Dict[str, int]:
    """``{side: changed-file count}`` for sides in the stale cache — cited-code drift.

    Reads ``stale.json`` (the LIF-6 cache SessionStart already refreshed) — zero git
    calls, zero corpus reads. ``{}`` when the cache is absent (advisory, same posture as
    every other reader of that file). Never raises.
    """
    try:
        from .build_index import default_index_dir
        from .staleness import read_stale_cache

        cache = read_stale_cache(index_dir or default_index_dir(memory_dir))
        if cache is None:
            return {}
        out: Dict[str, int] = {}
        for side in (a, b):
            rec = cache.get(side)
            if isinstance(rec, dict):
                try:
                    n = int(rec.get("changed") or 0)
                except (TypeError, ValueError):
                    continue
                if n > 0:
                    out[side] = n
        return out
    except Exception:
        return {}


def _usage_evidence(
    a: str, b: str, telemetry_dir: Optional[str]
) -> Tuple[Dict[str, int], bool]:
    """``({side: distinct-session recall count}, confident)`` — the usage-asymmetry leg.

    ``confident`` only once the aggregates have seen ``_EVIDENCE_USAGE_MIN_SESSIONS``
    distinct sessions (below the floor the card labels the column withheld rather than
    presenting two near-zero counts as signal). Read-only over the rotation-surviving
    aggregates; never raises.
    """
    counts: Dict[str, int] = {a: 0, b: 0}
    try:
        from .telemetry import read_usage_aggregates

        agg = read_usage_aggregates(telemetry_dir)
        for side in (a, b):
            rec = agg["memories"].get(side) or {}
            s = rec.get("sessions")
            if isinstance(s, int) and not isinstance(s, bool) and s >= 0:
                counts[side] = s
        total = agg["sessions"]["count"]
        confident = isinstance(total, int) and total >= _EVIDENCE_USAGE_MIN_SESSIONS
        return counts, confident
    except Exception:
        return counts, False


def _suggest_verdict(
    a: str, b: str, drift: Dict[str, int], newer: Optional[str]
) -> Tuple[str, Optional[str], str]:
    """``(suggested, winner_or_None, reason)`` — deterministic, evidence-only prefill.

    STRICTLY the resolve skill's own verdict names plus the explicit ``abstain`` (no
    second taxonomy). The ONE mechanical rule: when exactly one side cites drifted code
    and git freshness does not contradict it (the clean side is the newer edit, or
    freshness is unknown), suggest ``keep_one`` with the clean side as winner. Everything
    else — both stale, neither stale, signals disagreeing — is ``abstain``: merge /
    scope_both / not_conflicting are CONTENT judgments no drift/freshness arithmetic can
    make, so the card never fakes one. A suggestion is a prefill for the human, never
    auto-applied (accept-prefill stays gated behind a dated owner decision).
    """
    stale_sides = [s for s in (a, b) if s in drift]
    if len(stale_sides) == 1:
        loser = stale_sides[0]
        winner = b if loser == a else a
        if newer in (None, winner):
            return (
                "keep_one",
                winner,
                f"{loser} alone cites drifted code"
                + (f" and {winner} is the fresher edit" if newer == winner else ""),
            )
        return (
            _ABSTAIN,
            None,
            f"signals disagree: {loser} cites drifted code but is the fresher edit",
        )
    if len(stale_sides) == 2:
        return _ABSTAIN, None, "both sides cite drifted code — re-read both"
    return _ABSTAIN, None, "no drift asymmetry — this is a content judgment"


def pair_evidence(
    a: str,
    b: str,
    memory_dir: str,
    repo_root: Optional[str],
    *,
    index_dir: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    declared_by: Optional[List[str]] = None,
) -> dict:
    """The TMB-1 evidence card for ONE pair — deterministic, read-only, zero persisted state.

    ``declared_by`` ``None`` means "nothing declared" here (proposal-only shape); the
    façade's ``resolve_view.pair_evidence`` wrapper defaults it from ``pair_edge_state``
    so external callers keep the original contract.

    ``{"age_commits", "newer", "epochs", "drift", "usage", "usage_confident",
    "suggested", "suggested_winner", "reason"}``. Every leg degrades to an honest
    unknown (``None``/empty) rather than guessing; with no ``repo_root`` the git legs
    are skipped entirely. Cold path only — callers are ``describe()``/``--list`` and
    the resolve MCP tool's inbox, never a SessionStart producer or the hot path.
    Never raises.
    """
    card = {
        "age_commits": None,
        "newer": None,
        "epochs": {},
        "drift": {},
        "usage": {},
        "usage_confident": False,
        "suggested": _ABSTAIN,
        "suggested_winner": None,
        "reason": "",
    }
    try:
        other_of = {a: b, b: a}
        if repo_root:
            card["age_commits"] = _edge_birth_commits_ago(
                declared_by, other_of, memory_dir, repo_root
            )
            card["newer"], card["epochs"] = _git_newer_side(a, b, memory_dir, repo_root)
        card["drift"] = _drift_evidence(a, b, memory_dir, index_dir)
        card["usage"], card["usage_confident"] = _usage_evidence(a, b, telemetry_dir)
        card["suggested"], card["suggested_winner"], card["reason"] = _suggest_verdict(
            a, b, card["drift"], card["newer"]
        )
        return card
    except Exception:
        return card


def render_pair_evidence(a: str, b: str, card: dict) -> List[str]:
    """The card as its two deterministic listing lines (evidence + suggested)."""
    bits: List[str] = []
    age = card.get("age_commits")
    bits.append(
        f"age: born {age} commit(s) ago"
        if isinstance(age, int)
        else "age: unknown (uncommitted, shallow/rewritten history, or proposal-only)"
    )
    newer = card.get("newer")
    bits.append(f"git-newer: {newer}" if newer else "git-newer: unknown")
    drift = card.get("drift") or {}
    if drift:
        bits.append(
            "drift: "
            + ", ".join(f"{s} cites {n} changed file(s)" for s, n in sorted(drift.items()))
        )
    else:
        bits.append("drift: none cached")
    usage = card.get("usage") or {}
    if card.get("usage_confident"):
        bits.append(
            "usage: " + " / ".join(f"{s} {usage.get(s, 0)} session(s)" for s in sorted(usage))
        )
    else:
        bits.append(
            f"usage: withheld (fewer than {_EVIDENCE_USAGE_MIN_SESSIONS} sessions logged)"
        )
    lines = [f"      evidence: {' · '.join(bits)}"]
    suggested = card.get("suggested") or _ABSTAIN
    winner = card.get("suggested_winner")
    verdict = f"{suggested} (winner: {winner})" if winner else suggested
    reason = card.get("reason") or ""
    lines.append(
        f"      suggested: {verdict} — {reason}; a prefill for your judgment, never auto-applied"
    )
    return lines
