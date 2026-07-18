"""GRW-5: the commit-precise [since-watermark] staleness lane.

Decomposed out of ``reconsolidate.py`` when VOL-1 tripped the module-size ratchet — code
motion for ``_last_session_watermark``/``watermark_stale_candidates`` (both still
importable at ``memory.reconsolidate.<name>`` via the façade's re-export), plus the lane's
half of the volatile-paths arming policy. Sibling rules per CONTRIBUTING.md "Code layout":
imports its true dependencies (``provenance``, ``staleness``, ``telemetry``,
``staleness_policy``), never its façade.

VOL-1 lives HERE and not in ``session_start`` because this producer is called from every
watermark surface (the SessionStart dispatcher, the ``reconsolidate`` CLI, the consolidate
MCP tool, the brief) — filtering at the producer gives all of them the one policy for
free. CLB-3's evidence-drift fold (``staleness_evidence.fold_drift_candidates``) runs
AFTER this producer by design, so quoted-evidence drift arms unfiltered even when the
quoted file is registry-listed: a memory's own span changing is span-level truth, not
whole-file churn.

The lane is BASELINE-AWARE (2026-07-18): watermark hits are filtered per-path against
each memory's own ``source_commit`` (SHP-3 stored-time fallback) before the VOL-1 split —
a freshly graduated/fixed memory must not re-flag the drift it was just verified against.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Set, Tuple

from .provenance import _iter_memory_files, run_git
from .staleness import (
    _CHANGE_MARKER,
    _chunk_paths,
    _commit_times,
    read_provenance,
    read_source_commit_time,
)
from .staleness_policy import note_suppressed, split_volatile_only, volatile_set
from .telemetry import read_episodes

# GRW-5: bound on the since-watermark diff read — the same cap discipline as
# capture._MAX_CHANGED_PATHS, so a monorepo-wide <old-watermark>..HEAD stays cheap.
_MAX_WATERMARK_PATHS = 200


def _last_session_watermark(telemetry_dir: Optional[str]) -> Optional[str]:
    """The most-recent session's episode watermark — its EARLIEST recorded ``head_commit``.

    Lifts the resume card's most-recent-session scan (max ``ts`` picks the session) and the
    capture seed's watermark convention (that session's first non-empty ``head_commit`` —
    where the session STARTED, so the diff below covers everything it and any later commit
    touched). ``None`` on an empty buffer or any failure — never raises.
    """
    try:
        episodes = []
        latest_ts, sid = None, None
        for e in read_episodes(telemetry_dir):
            episodes.append(e)
            ts = e.get("ts")
            if ts is None:
                continue
            if latest_ts is None or ts > latest_ts:
                latest_ts, sid = ts, e.get("session_id")
        for e in episodes:
            if e.get("session_id") == sid and e.get("head_commit"):
                return e["head_commit"]
        return None
    except Exception:
        return None


def _range_hit_times(repo_root: str, wm: str, paths: List[str]) -> Dict[str, int]:
    """Newest commit unix-time per hit path within ``wm..HEAD``.

    The range twin of ``staleness._path_change_times`` (that scan is --since-windowed;
    this lane is commit-exact by charter). Scoped to the hit union — already bounded by
    ``_MAX_WATERMARK_PATHS`` — chunked per SHP-6's ARG_MAX discipline, and ``:/``-anchored
    so a monorepo-subdir ``repo_root`` still matches toplevel-relative ``cited_paths``.
    A failed log read yields missing entries, which the caller treats as NOT provably
    covered — degrade toward flagging, never toward silence.
    """
    out: Dict[str, int] = {}
    for chunk in _chunk_paths(paths):
        log = run_git(
            ["log", f"{wm}..HEAD", "--name-only", f"--format={_CHANGE_MARKER}%ct",
             "--", *[f":/{p}" for p in chunk]],
            repo_root,
        )
        cur: Optional[int] = None
        for line in log.split("\n"):
            if line.startswith(_CHANGE_MARKER):
                try:
                    cur = int(line[len(_CHANGE_MARKER):] or 0)
                except ValueError:
                    cur = None
            elif line.strip() and cur is not None and line not in out:
                out[line] = cur  # newest-first log: first sighting is the newest change
    return out


def watermark_stale_candidates(
    memory_dir: str,
    repo_root: str,
    telemetry_dir: Optional[str] = None,
    diagnostics: Optional[dict] = None,
) -> List[dict]:
    """GRW-5: commit-precision re-verify candidates — ``<last-watermark>..HEAD ∩ cited_paths``.

    ``find_stale`` discovers staleness lazily against a git-log WINDOW; this lane is exact:
    diff the commits landed since the last session's episode watermark and name every memory
    whose ``cited_paths`` they touched — ``[{"name", "changed_paths", "watermark": True}]``,
    name-sorted. Same commit-precision as an opt-in git hook with ZERO new run surface (the
    roadmap killed the ``.git/hooks`` variant — inv2): it rides the episode watermark the
    buffer already records and the ONE SessionStart git-read moment. ``run_git`` returns ""
    when the watermark sha is unreachable (the squash-merge case) — that yields ``[]`` here,
    honestly; GRW-6's producer detects and heals the break, this function never guesses. The
    diff read is bounded (``_MAX_WATERMARK_PATHS``). Read-only; never raises; ``[]`` on any
    failure. Consumers route every hit through ``recalled_stale_worklist``'s
    ``watermark_stale=`` union — the ONE ``semantic_reverify`` gate — never a new verb.

    BASELINE-AWARE (the 2026-07-18 consolidate-pass repro: 20 verdicts re-baselined 20
    memories to HEAD, and the next worklist re-listed all 20): a hit counts only for
    paths whose newest ``wm..HEAD`` change POSTDATES the memory's OWN baseline — the
    watermark says when the SESSION last looked; ``source_commit`` says when the MEMORY
    was last grounded, and a graduate/fix verdict re-baselines it to HEAD, past the very
    commits this lane would otherwise re-flag. Baselines resolve through
    ``staleness._commit_times``, falling back to the stored ``source_commit_time`` when
    the sha is unresolvable (SHP-3's squash-merge/shallow-clone case). Suppression needs
    POSITIVE evidence of coverage: no resolvable sha and no stored time ⇒ hits stay (the
    lane's historical shape), and a failed times read degrades the same open-handed way.
    Filtering is per-path — a covered sibling path drops out, so the surviving listing
    matches what the brief's own ``source_commit..HEAD`` diffstat will actually show —
    and runs BEFORE the VOL-1 split, so a memory whose only post-baseline drift is
    volatile-path routes to the suppressed diagnostic, not an armed flag.

    VOL-1: a memory whose watermark hits are ALL registry-listed volatile paths gets no
    [since-watermark] flag — the split_volatile_only partition, with an armed candidate
    keeping its FULL hit list (the volatile path did drift; it just didn't arm alone).
    Suppressed names are recorded into caller-owned ``diagnostics`` (``DIAG_KEY``) so the
    rendering surfaces can print what policy withheld — never silent. Empty/absent
    registry ⇒ the identity split, byte-identical to the pre-VOL-1 lane.
    """
    try:
        wm = _last_session_watermark(telemetry_dir)
        if not wm or not repo_root or not os.path.isdir(memory_dir):
            return []
        changed: Set[str] = set()
        for ln in run_git(["diff", "--name-only", f"{wm}..HEAD"], repo_root).splitlines():
            ln = ln.strip()
            if ln:
                changed.add(ln)
            if len(changed) >= _MAX_WATERMARK_PATHS:
                break
        if not changed:
            return []
        cands: List[Tuple[str, List[str], Optional[str], str]] = []
        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:
                continue
            cited, sc = read_provenance(text)
            hits = sorted(set(cited) & changed)
            if hits:
                name = os.path.splitext(os.path.basename(path))[0]
                cands.append((name, hits, sc, text))
        if not cands:
            return []
        # Two git reads, lazily — only when the intersection is non-empty (the common
        # quiet session pays nothing beyond the one diff above).
        commit_times = _commit_times([c[2] for c in cands if c[2]], repo_root)
        hit_times = _range_hit_times(
            repo_root, wm, sorted({p for _, hits, _, _ in cands for p in hits})
        )
        out: List[dict] = []
        for name, hits, sc, text in cands:
            base = commit_times.get(sc) if sc else None
            if base is None:
                base = read_source_commit_time(text)
            if base is not None:
                hits = [p for p in hits if p not in hit_times or hit_times[p] > base]
            if hits:
                out.append({"name": name, "changed_paths": hits, "watermark": True})
        out, suppressed = split_volatile_only(out, volatile_set(memory_dir))
        note_suppressed(diagnostics, [item["name"] for item in suppressed])
        return out
    except Exception:
        return []
