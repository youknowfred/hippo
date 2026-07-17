"""CLB-4: the incoming-merge duplicate digest — merge-time gets write-time's dedup.

hippo prevents two-plane drift at WRITE time (GRW-3's near-duplicate check runs on
every ``new_memory``), but when a teammate's memories arrive via ``git merge`` no
write path runs — an incoming memory can duplicate or contradict a local one with
nothing surfacing it. This producer closes that gap at SessionStart, reusing the
existing machinery end to end (inv5 — nothing new is invented):

  - **The incoming range** is the ``_last_session_watermark``/``_recent_merge_signals``
    lineage GRW-5/GRW-6 already ride: the last session's episode ``head_commit``
    diffed against HEAD, filtered to live memory files — gated on the SAME
    merge-signal probe ``squash_merge_heal`` trusts, so a purely-local work session
    (your own writes, already dup-checked at write time) stays silent. On a trusted
    corpus with no usable watermark (fresh clone, cleared telemetry), the SEC-6
    consent-drift delta (``trust.untrusted_changes``) is the fallback stem source.
  - **The detector** is ``new_memory.committed_duplicate_neighbors`` (GRW-3) — the
    ONE calibrated near-duplicate check; no second detector, no new thresholds.
  - **Seen-state** is the advancing watermark itself (the GOV-4 ledger pattern,
    reused rather than re-implemented: each session's episodes move the watermark,
    so a surfaced pair never re-nags — and no parallel per-clone ledger exists).
  - **Routing** is entirely human: a pair with a declared ``contradicts`` edge goes
    to ``/hippo:resolve``; everything else to ``/hippo:consolidate`` (the GRW-3
    merge tier: update-existing / supersede / skip). No contradicts/supersedes
    edge is EVER written here — read-only by construction (inv4), and
    ``resolve_view``'s inbox derivation is untouched.

Degradation is legible (inv3): a watermark that no longer resolves (squash-merge
or history rewrite ate it) emits an explicit one-line notice — never silent
nothing — and heals itself next session when fresh episodes re-anchor it.

Distinct from round 1's KILLED cross-clone-auto-harvest (re-verified against that
kill's rationale): this reads a single repo's OWN incoming git range, proposes to
a human, and writes nothing — the kill was about autonomously importing/merging
OTHER clones' content, which stays dead.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

# Bounds: stems scanned per session and pairs surfaced (the spec's cap).
_MAX_INCOMING_STEMS = 10
_MAX_PAIRS = 5


def _incoming_memory_stems(
    memory_dir: str, repo_root: str, telemetry_dir: Optional[str]
) -> Tuple[List[str], Optional[str]]:
    """``(stems, degradation)`` — live memory stems the incoming range touched.

    ``degradation`` is a one-line notice when the watermark exists but no longer
    resolves (the squash/rewrite case); stems are then derived from the SEC-6
    trust-drift fallback if available. ``([], None)`` means genuinely nothing
    incoming — the silent-normal case. Never raises.
    """
    from .provenance import run_git
    from .reconsolidate import _last_session_watermark

    degradation: Optional[str] = None
    stems: List[str] = []
    wm = _last_session_watermark(telemetry_dir)
    prefix = _memory_prefix(memory_dir, repo_root)
    if wm:
        if not run_git(["rev-parse", "--verify", "--quiet", f"{wm}^{{commit}}"], repo_root).strip():
            degradation = (
                f"🔀 Incoming-merge dedup: the last-session watermark {wm[:7]} is "
                "unreachable in this history (squash-merge or rewrite) — the incoming "
                "range could not be derived, so merged-in memories were NOT dup-checked "
                "this session. This self-heals once this session's episodes re-anchor "
                "the watermark; baseline repair itself is the 🩹 heal producer's job."
            )
        else:
            for line in run_git(["diff", "--name-only", f"{wm}..HEAD"], repo_root).splitlines():
                path = line.strip()
                if not path.startswith(f"{prefix}/") or not path.endswith(".md"):
                    continue
                if path.startswith(f"{prefix}/archive/"):
                    continue
                stem = os.path.splitext(os.path.basename(path))[0]
                if stem == "MEMORY":
                    continue
                if os.path.isfile(os.path.join(memory_dir, f"{stem}.md")) and stem not in stems:
                    stems.append(stem)
                if len(stems) >= _MAX_INCOMING_STEMS:
                    break
            return stems, None
    # No usable watermark: the SEC-6 trust-drift delta is the sanctioned fallback
    # stem source on a TRUSTED corpus (untrusted corpora never reach producers).
    try:
        from . import trust

        gate_root = trust.gate_repo_root(memory_dir, repo_root)
        if gate_root is not None and trust.is_trusted(gate_root):
            drift = trust.untrusted_changes(gate_root, memory_dir)
            for stem in list(drift.get("changed") or []) + list(drift.get("added") or []):
                if os.path.isfile(os.path.join(memory_dir, f"{stem}.md")) and stem not in stems:
                    stems.append(stem)
                if len(stems) >= _MAX_INCOMING_STEMS:
                    break
    except Exception:
        pass
    return stems, degradation


def _memory_prefix(memory_dir: str, repo_root: str) -> str:
    """Toplevel-relative corpus prefix; falls back to the conventional location."""
    try:
        from .provenance import run_git

        top = run_git(["rev-parse", "--show-toplevel"], repo_root).strip() or repo_root
        rel = os.path.relpath(os.path.realpath(memory_dir), os.path.realpath(top))
        if not rel.startswith(".."):
            return rel.replace(os.sep, "/")
    except Exception:
        pass
    return ".claude/memory"


def _contradicts_declared(a: str, b: str, memory_dir: str) -> bool:
    """True iff a ``contradicts`` edge exists between ``a`` and ``b`` in either
    direction — those pairs route to /hippo:resolve (the inbox already owns them).
    Reads the two files' frontmatter directly (two small reads, no graph build).
    """
    try:
        from .links import parse_typed_relations
        from .provenance import parse_frontmatter

        for src, tgt in ((a, b), (b, a)):
            try:
                with open(os.path.join(memory_dir, f"{src}.md"), "r", encoding="utf-8") as fh:
                    rels = parse_typed_relations(parse_frontmatter(fh.read()))
            except Exception:
                continue
            if tgt in (rels.get("contradicts") or []):
                return True
    except Exception:
        return False
    return False


def incoming_duplicate_pairs(
    memory_dir: str, repo_root: str, telemetry_dir: Optional[str] = None
) -> Tuple[List[dict], Optional[str], int]:
    """``(pairs, degradation, incoming_count)`` — the digest's whole derivation.

    Each pair is ``{"incoming", "neighbor", "score", "route"}`` (``route`` is
    ``"resolve"`` for declared contradictions, else ``"consolidate"``), capped at
    ``_MAX_PAIRS``, incoming-stem-sorted for a deterministic render. Read-only;
    never raises; ``([], None, 0)`` on any failure.
    """
    try:
        stems, degradation = _incoming_memory_stems(memory_dir, repo_root, telemetry_dir)
        if not stems:
            return [], degradation, 0
        from .new_memory import committed_duplicate_neighbors

        pairs: List[dict] = []
        seen: set = set()
        for stem in sorted(stems):
            neighbors, _note = committed_duplicate_neighbors(stem, memory_dir)
            for n in neighbors or []:
                other = n.get("name")
                if not other or other == stem:
                    continue
                key = frozenset((stem, other))
                if key in seen:
                    continue
                seen.add(key)
                pairs.append(
                    {
                        "incoming": stem,
                        "neighbor": other,
                        "score": float(n.get("score") or 0.0),
                        "route": "resolve"
                        if _contradicts_declared(stem, other, memory_dir)
                        else "consolidate",
                    }
                )
                if len(pairs) >= _MAX_PAIRS:
                    return pairs, degradation, len(stems)
        return pairs, degradation, len(stems)
    except Exception:
        return [], None, 0


def merge_digest_producer(
    memory_dir: str, repo_root: str, ctx=None
) -> Optional[str]:
    """CLB-4's SessionStart producer — one bounded block, or None (the empty norm).

    Fires only when the merge-signal probe says a merge/pull plausibly landed
    (``session_start._recent_merge_signals`` — the ONE detector, shared with the
    GRW-6 heal producer) AND the incoming range touched live memory files AND the
    GRW-3 detector finds pairs. The degradation line (unreachable watermark) rides
    the same gate so a squash-merge is loud exactly when a merge happened. ``ctx``
    (LIF-6) is unused — this producer's inputs are git + telemetry, not the
    staleness context. Read-only; never raises.
    """
    try:
        from .session_start import _recent_merge_signals
        from .telemetry import default_telemetry_dir

        if not _recent_merge_signals(repo_root):
            return None
        pairs, degradation, incoming = incoming_duplicate_pairs(
            memory_dir, repo_root, default_telemetry_dir(memory_dir)
        )
        if degradation and not pairs:
            return degradation
        if not pairs:
            return None
        lines = [
            f"🔀 Incoming-merge duplicate digest — {incoming} merged-in memory file(s) "
            f"since your last session; {len(pairs)} pair(s) look duplicate/conflicting. "
            "Route each (nothing merges or writes edges automatically):"
        ]
        for p in pairs:
            if p["route"] == "resolve":
                target = "/hippo:resolve (declared contradicts — already in the inbox)"
            else:
                target = "/hippo:consolidate (GRW-3 merge tier: update-existing / supersede / skip)"
            lines.append(
                f"  • {p['incoming']} ⇄ {p['neighbor']} ({p['score']:.2f}) → {target}"
            )
        if degradation:
            lines.append(f"  {degradation}")
        return "\n".join(lines)
    except Exception:
        return None
