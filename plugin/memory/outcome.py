"""SIG-4: PostToolUse read-signal — the KPI-2 injection-precision MEASUREMENT.

hippo has always recorded what it INJECTED (the episode buffer's ``recalled_names``) but never
whether the injection HELPED, so "frequently recalled" was indistinguishable from "frequently
correct." KPI-2 (ROADMAP.yaml) names the missing PostToolUse read-signal outright.

This is that signal. A PostToolUse hook (scoped by ``hooks.json`` matcher to the file-touching
tools) hands each touched file to ``record_from_payload``, which appends a repo-relative
``{ts, session_id, tool, path}`` line to a gitignored outcome ledger — cheap, fire-and-forget,
never raising. The expensive JOIN is OFF the hook: ``injection_precision`` later reconciles the
episode buffer's injected memories against the outcome ledger's touches via each memory's
``cited_paths`` — "was an injected memory's cited file subsequently touched in the same session?"

RET-14 (owner-directed): the ranking-utility prior this docstring used to say was "deliberately
gated on the salience keystone" now exists — recall.py's ``_apply_outcome_prior`` consumes
``write_outcome_cache``'s persisted ``outcome.json`` as its OWN independently-gated prior
(``HIPPO_OUTCOME_PRIOR``, default OFF, separate from ``HIPPO_SALIENCE``) rather than folding it
into the SIG-5 salience blend — RET-10 found recency/usage moved nothing on the golden eval, and
KPI-2's "was this actually useful" signal is different enough in kind (outcome evidence, not a
popularity/recency proxy) to deserve measuring on its own. This module itself still computes and
WRITES the cache only — it does not read recall's ranking state, so the negative-capability test
below (this module must not import recall/new_memory) still holds; only recall.py imports FROM
outcome, never the reverse.

T16 (JIT): the same PostToolUse spawn now also runs the jit lane — ``jit.observe_touch``
hands back optional touch-grain provenance stamped onto the outcome row (JIT-2,
``cited_by``; ``injection_hits(grain="touch")`` + ``--touch-grain`` consume it
report-only) and, on the first touch of a cited file per session, the bounded
first-touch reminder that ``main --from-hook`` prints as the hook's additionalContext
(JIT-1). Neither half touches ranking; ``HIPPO_DISABLE_JIT`` restores the pre-T16 hook
byte-for-byte.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, Optional

from .provenance import resolve_dirs
from .telemetry import default_telemetry_dir, log_outcome, read_episodes, read_outcomes

# The tools whose invocation means the agent READ or EDITED a file — the "used it" signal. Kept
# in sync with the hooks.json PostToolUse matcher (that matcher is the fast pre-filter; this set
# is the authoritative guard, so a matcher slip can never log a non-file tool).
_FILE_TOOLS = frozenset({"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"})

# T18 FLT-3: the MUTATING subset — the ONE canonical "this tool changed a file" constant
# (Read excluded; no other such subset may exist). The fleet lane's worktree-first nudge
# keys on it; a test pins its relationship to _FILE_TOOLS so a future tool joining the
# matcher forces a conscious mutating-or-not decision here.
MUTATING_FILE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})


def _touched_path(tool_input: dict) -> Optional[str]:
    """The file a file-touching tool acted on (``file_path`` for most; ``notebook_path`` for
    NotebookEdit). ``None`` when the payload carries neither."""
    if not isinstance(tool_input, dict):
        return None
    return tool_input.get("file_path") or tool_input.get("notebook_path") or None


def record_from_payload(
    payload: dict,
    *,
    memory_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    context_out: Optional[list] = None,
) -> bool:
    """Log ONE outcome event iff the PostToolUse payload is a file-touching tool INSIDE the repo.

    The touched path is stored repo-relative (cited_paths are repo-relative); a touch outside the
    repo can never match a citation, so it is dropped. Never raises; returns False when nothing
    was logged.

    T16 rides the same single Python spawn: the jit lane (``jit.observe_touch``) sees the
    touch first, handing back OPTIONAL touch-grain provenance for the row (JIT-2,
    ``cited_by``) and — on the first touch of a cited file per session — the bounded
    first-touch reminder (JIT-1), appended to ``context_out`` (caller-supplied list, the
    ``compute_corpus`` texts_out pattern) for ``main`` to print as hook additionalContext.
    Existing callers pass no ``context_out`` and see the exact pre-T16 behavior; with
    ``HIPPO_DISABLE_JIT`` set the lane contributes nothing at all.
    """
    try:
        if not isinstance(payload, dict):
            return False
        tool = payload.get("tool_name") or payload.get("tool") or ""
        if tool not in _FILE_TOOLS:
            return False
        raw = _touched_path(payload.get("tool_input") or {})
        if not raw:
            return False
        session_id = payload.get("session_id") or None
        if memory_dir is None or repo_root is None:
            md, rr = resolve_dirs()
            memory_dir = memory_dir or md
            repo_root = repo_root or rr
        rel = None
        try:
            ap = os.path.abspath(raw)
            base = os.path.abspath(repo_root)
            if ap == base or ap.startswith(base + os.sep):
                rel = os.path.relpath(ap, base)
        except Exception:
            rel = None
        if not rel:
            return False
        # MEA-6: derive the in-tree path ONCE via the module's _WORKTREE_PREFIX (the same
        # split lane health's would-map diagnosis already renders — no second copy, inv5).
        # A worktree touch normalizes to its repo-relative tail so the touchmap join,
        # JIT-1 first-touch reminders, and JIT-2 cited_by go LIVE in worktree sessions —
        # the item's ONE behavior delta: bounded by MAX_LINES_PER_SESSION, killed by
        # HIPPO_DISABLE_JIT. The RAW rel stays the row's `path` (honest record of where
        # the touch landed); `tree_path` rides additively only when different (ED-4).
        # FLT-3's shared_tree exemption and presence.observe_fleet below stay RAW-keyed —
        # a worktree mutation must never render as a shared-tree mutation post-strip.
        tree_rel = rel
        if rel.startswith(_WORKTREE_PREFIX):
            tail = rel[len(_WORKTREE_PREFIX):].split("/", 1)
            if len(tail) == 2 and tail[1]:
                tree_rel = tail[1]
        td = telemetry_dir or default_telemetry_dir(memory_dir)
        cited_by = None
        try:
            from .jit import observe_touch

            cited_by, context = observe_touch(
                tree_rel,
                memory_dir=memory_dir,
                repo_root=repo_root,
                telemetry_dir=td,
                session_id=session_id,
            )
            if context and context_out is not None:
                context_out.append(context)
        except Exception:
            cited_by = None
        # T18 FLT-2/FLT-3: the fleet lane rides the SAME single spawn — the moved-tree
        # tripwire and the worktree-first nudge (presence.observe_fleet), debounced and
        # budget-pinned, appending to the same context_out so the hook still emits
        # exactly ONE hookSpecificOutput (QUA-2). The worktree-prefix self-exemption is
        # the same prefix logic EVD-2's lane-health diagnosis names (_WORKTREE_PREFIX —
        # built aware of each other by design). Killed entirely by HIPPO_DISABLE_PRESENCE.
        try:
            from .presence import observe_fleet

            fleet = observe_fleet(
                rel,
                memory_dir=memory_dir,
                repo_root=repo_root,
                telemetry_dir=td,
                session_id=session_id,
                mutating=tool in MUTATING_FILE_TOOLS,
                shared_tree=not rel.startswith(_WORKTREE_PREFIX),
            )
            if fleet and context_out is not None:
                context_out.extend(fleet)
        except Exception:
            pass
        return log_outcome(
            tool, rel, session_id=session_id, telemetry_dir=td, cited_by=cited_by,
            tree_path=tree_rel if tree_rel != rel else None,
        )
    except Exception:
        return False


def _cited_paths_of(memory_dir: str, name: str, cache: dict) -> set:
    """The (cached) ``cited_paths`` of memory ``name`` in ``memory_dir`` — empty if unreadable."""
    if name in cache:
        return cache[name]
    cited: set = set()
    try:
        from .staleness import read_provenance

        with open(os.path.join(memory_dir, f"{name}.md"), "r", encoding="utf-8") as fh:
            cited = set(read_provenance(fh.read())[0])
    except Exception:
        cited = set()
    cache[name] = cited
    return cited


def _injection_join(
    memory_dir: str, telemetry_dir: Optional[str] = None, *, grain: str = "session"
) -> dict:
    """The ONE episode×outcome×cited_paths join: ``{(session, name): {"cited", "hit"}}``.

    ``cited`` — the injected memory carries cited_paths (it has a file signal at all);
    ``hit`` — at the default ``"session"`` grain, one of those cited files was touched in
    the SAME session at/after the memory's earliest recall ts. Both aggregates
    (``injection_precision``, ``injection_hits``) read this so the join semantics can
    never fork. MEA-6: touch paths read ``tree_path`` (the record-time worktree
    normalization) over ``path`` when present.

    JIT-2 (T16): ``grain="touch"`` joins on the RECORDED coincidence instead — a touch
    row whose ``cited_by`` field (stamped by the jit lane at touch time) names the
    memory, same session, at/after injection. Sharper (robust to a memory's cited_paths
    changing between the touch and this join) but honest about its blind spots: rows
    recorded pre-T16, past the per-session provenance cap, or with the lane killed carry
    no ``cited_by`` and simply do not count. Touch grain therefore UNDER-counts by
    construction — evidence-plus, never evidence-instead; session grain stays the
    default. Never raises; ``{}`` on any failure or empty ledgers.
    """
    try:
        td = telemetry_dir or default_telemetry_dir(memory_dir)
        # (session, memory) -> earliest recall ts in that session
        injected: dict = {}
        for e in read_episodes(td):
            sid = e.get("session_id")
            ts = e.get("ts")
            ts = ts if isinstance(ts, (int, float)) else 0
            for n in e.get("recalled_names") or []:
                if not n:
                    continue
                key = (sid, n)
                if key not in injected or ts < injected[key]:
                    injected[key] = ts
        if not injected:
            return {}
        # session -> [(path, ts, cited_by-or-None)]. MEA-6: prefer the record-time
        # in-tree normalization when present — a worktree touch of a cited tree path
        # COUNTS as touching the citation; historical rows (no tree_path) read as before.
        touches: dict = {}
        for o in read_outcomes(td):
            p = o.get("tree_path") or o.get("path")
            if not p:
                continue
            ts = o.get("ts")
            cb = o.get("cited_by")
            touches.setdefault(o.get("session_id"), []).append(
                (p, ts if isinstance(ts, (int, float)) else 0, cb if isinstance(cb, list) else None)
            )
        cache: dict = {}
        out: dict = {}
        for (sid, name), inject_ts in injected.items():
            cited = _cited_paths_of(memory_dir, name, cache)
            if grain == "touch":
                hit = any(
                    cb is not None and name in cb and t >= inject_ts
                    for _p, t, cb in touches.get(sid, [])
                )
            else:
                hit = bool(cited) and any(
                    p in cited and t >= inject_ts for p, t, _cb in touches.get(sid, [])
                )
            out[(sid, name)] = {"cited": bool(cited), "hit": hit}
        return out
    except Exception:
        return {}


def injection_precision(memory_dir: str, telemetry_dir: Optional[str] = None) -> dict:
    """KPI-2 proxy: of the injected memories that cite a file, the fraction whose cited file was
    subsequently touched in the SAME session (touch ts >= that memory's earliest recall ts).

    Injected memories with NO cited_paths are excluded from the denominator — they carry no file
    signal, so counting them as misses would understate precision. Returns
    ``{"injected_with_cites", "hits", "precision", "sessions"}`` (``precision`` is ``None`` when
    there is no signal yet). MEASUREMENT ONLY; never raises.
    """
    try:
        join = _injection_join(memory_dir, telemetry_dir)
        if not join:
            return {"injected_with_cites": 0, "hits": 0, "precision": None, "sessions": 0}
        denom = sum(1 for r in join.values() if r["cited"])
        hits = sum(1 for r in join.values() if r["hit"])
        return {
            "injected_with_cites": denom,
            "hits": hits,
            "precision": (hits / denom) if denom else None,
            "sessions": len({sid for sid, _ in join}),
        }
    except Exception:
        return {"injected_with_cites": 0, "hits": 0, "precision": None, "sessions": 0}


def injection_hits(
    memory_dir: str, telemetry_dir: Optional[str] = None, *, grain: str = "session"
) -> dict:
    """Per-MEMORY recorded-outcome evidence: ``{name: {"hits", "sessions"}}``, hits ≥ 1 only.

    JIT-2 (T16): ``grain="touch"`` computes the same aggregate over the RECORDED
    (memory, file, touch) coincidences (outcome rows carrying ``cited_by``) instead of
    the join-time corpus×path match — see ``_injection_join``. Flag-gated and
    report-only (``--touch-grain``); every persisted consumer (the RET-14 cache, DRM-6
    graduation, dream's reward pass) stays on the session-grain default.

    The DRM-5 read surface: a memory appears here iff at least one session both injected it
    AND touched one of its cited files at/after the injection — the ledger-recorded,
    positive outcome signal (today's outcome ledger records positive evidence only).
    ``hits`` counts qualifying sessions; ``sessions`` lists them (sorted, for provenance).
    Same join as ``injection_precision`` (``_injection_join``) — the semantics cannot fork.
    Computes the join LIVE over the raw episode/outcome ledgers — fine for an occasional
    /dream pass or doctor report, too expensive to call per-prompt. RET-14: SessionStart
    calls this ONCE per run and persists the result via ``write_outcome_cache`` so
    recall.py's hot-path prior (``_apply_outcome_prior``) never re-runs this join itself,
    the same "compute once upstream, read a small cached JSON on the hot path" posture
    ``stale.json``/``usage_aggregates.json`` already have. Never raises; ``{}`` when there
    is no signal.
    """
    try:
        out: dict = {}
        for (sid, name), r in _injection_join(memory_dir, telemetry_dir, grain=grain).items():
            if not r["hit"]:
                continue
            rec = out.setdefault(name, {"hits": 0, "sessions": []})
            rec["hits"] += 1
            rec["sessions"].append(str(sid))
        for rec in out.values():
            rec["sessions"].sort()
        return out
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# RET-14: the persisted cache recall.py's hot-path prior reads (never the live join above).
# --------------------------------------------------------------------------- #
_OUTCOME_CACHE_NAME = "outcome.json"
OUTCOME_CACHE_SCHEMA_VERSION = 1


def outcome_cache_path(index_dir: str) -> str:
    """``<index_dir>/outcome.json`` — the one path the writer and ``read_outcome_cache``
    below (recall.py's RET-14 outcome prior) must agree on."""
    return os.path.join(index_dir, _OUTCOME_CACHE_NAME)


def write_outcome_cache(index_dir: str, hits: dict) -> bool:
    """Persist ``injection_hits``'s result to ``<index_dir>/outcome.json``.

    Derived, rebuildable, gitignored — same standing as ``stale.json``/``links.json``, and
    written the same tmp + ``os.replace`` way (``staleness.write_stale_cache``'s pattern) so
    a reader never sees a torn file. Written on EVERY call, including an empty ``hits`` dict
    — an honest ``{"hits": {}}`` means "checked this session, found no positive evidence
    yet", never a skipped write. This function only WRITES a file computed from
    ``injection_hits``'s already-measurement-shaped output — it reads no recall/ranking
    state itself, so the negative-capability guarantee below (this module imports no
    recall/new_memory) is untouched; only recall.py reads FROM this cache, never the
    reverse. Never raises; ``True`` on a successful write, ``False`` on any failure.
    """
    try:
        os.makedirs(index_dir, exist_ok=True)
        payload = {
            "schema_version": OUTCOME_CACHE_SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "hits": {
                name: {
                    "hits": int(rec.get("hits") or 0),
                    "sessions": list(rec.get("sessions") or []),
                }
                for name, rec in (hits or {}).items()
            },
        }
        path = outcome_cache_path(index_dir)
        tmp = path + f".tmp.{os.getpid()}"  # COR-17: unique per writer — concurrent processes must not share a tmp
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
        return True
    except Exception:
        return False


def read_outcome_cache(index_dir: str) -> Optional[Dict[str, dict]]:
    """The reader half of ``write_outcome_cache`` — ``{"<name>": {"hits", "sessions"}}``, or
    ``None`` when the cache is absent, corrupt, or schema-mismatched.

    Advisory, same posture as ``staleness.read_stale_cache``: a single small-JSON read of a
    file SessionStart already refreshed once per run — never a live episode/outcome ledger
    join on the hot path. Never raises.
    """
    try:
        path = outcome_cache_path(index_dir)
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict) or payload.get("schema_version") != OUTCOME_CACHE_SCHEMA_VERSION:
            return None
        hits = payload.get("hits")
        return hits if isinstance(hits, dict) else None
    except Exception:
        return None


def format_touch_grain_report(memory_dir: str, telemetry_dir: Optional[str] = None) -> str:
    """JIT-2: the session-grain vs touch-grain ``injection_hits`` comparison. Report-only —
    nothing persists, nothing changes ranking; the delta is the honest picture of how much
    evidence the sharper join actually has (touch grain UNDER-counts by construction:
    pre-T16 rows, capped rows, and killed-lane rows carry no provenance). Never raises."""
    try:
        session = injection_hits(memory_dir, telemetry_dir)
        touch = injection_hits(memory_dir, telemetry_dir, grain="touch")
        s_hits = sum(r["hits"] for r in session.values())
        t_hits = sum(r["hits"] for r in touch.values())
        only_session = sorted(set(session) - set(touch))
        lines = [
            "injection_hits by grain (JIT-2, report-only — session grain stays the default):",
            f"  session grain: {len(session)} memories / {s_hits} hit-session(s) — injected AND a cited file touched that session",
            f"  touch grain:   {len(touch)} memories / {t_hits} hit-session(s) — only recorded (memory, file, touch) coincidences",
        ]
        if only_session:
            preview = ", ".join(only_session[:5]) + ("…" if len(only_session) > 5 else "")
            lines.append(
                f"  under-count (expected direction): {len(only_session)} memory(ies) hit at session grain only — {preview}"
            )
        return "\n".join(lines)
    except Exception:
        return "injection_hits by grain: no signal yet (report-only)."


# EVD-2: worktree sessions record touches as paths under this prefix relative to the MAIN
# root — such rows can never match repo-relative cited_paths at either grain. One canonical
# prefix constant; FLT-3's worktree self-exemption (T18) shares this exact logic by design.
_WORKTREE_PREFIX = ".claude/worktrees/"


def format_lane_health(memory_dir: str, telemetry_dir: Optional[str] = None) -> str:
    """EVD-2: the touch-grain lane's HEALTH/diagnosis surface — it explains this lane's
    zeros instead of charting them as insight (a trend chart over a mechanically-starved
    lane would chart zeros and call it signal).

    Lane-LEVEL volumes and shares (total rows, cited_by share, distinct sessions,
    worktree-prefixed share with the normalized-vs-historical counts), touchmap coverage
    (cited/reminders map sizes), and the existing both-grains comparison COMPOSED via
    ``format_touch_grain_report`` — extended, never duplicated (a test pins the
    composition). The two verified mechanical reasons for zeros are named in-line:
    live-hook lag (rows written by a pre-T16 installed hook can never carry
    ``cited_by``) and HISTORICAL worktree-prefixed paths (recorded relative to the
    MAIN root before MEA-6's record-time normalization; new worktree rows carry
    ``tree_path`` and join directly — the stamped-vs-would-map split is this item's
    own before/after receipt).

    Aggregation stays lane-level or positive-evidence-only (``injection_hits``'s
    shipped shape) — NEVER a per-memory injected-but-never-touched table (the MSR-6 /
    round-1 noise-finder kill), and ``_injection_join`` remains the single join.
    Cold path only (CLI/report). Read-only; never raises.
    """
    try:
        td = telemetry_dir or default_telemetry_dir(memory_dir)
        rows = list(read_outcomes(td))
        total = len(rows)
        with_cb = sum(1 for r in rows if isinstance(r.get("cited_by"), list) and r.get("cited_by"))
        sessions = len({r.get("session_id") for r in rows if r.get("session_id")})
        wt_paths = [
            str(r.get("path") or "") for r in rows if str(r.get("path") or "").startswith(_WORKTREE_PREFIX)
        ]
        cited_map: dict = {}
        reminders_map: dict = {}
        try:
            from .build_index import default_index_dir
            from .jit import read_touch_cache

            cache = read_touch_cache(default_index_dir(memory_dir)) or {}
            cited_map = cache.get("cited") or {}
            reminders_map = cache.get("reminders") or {}
        except Exception:
            pass
        # MEA-6: stamped rows already join the evidence base at record time; the
        # would-map count now measures ONLY the historical dark rows (the before/after
        # receipt this item ships against its own class).
        stamped = sum(1 for r in rows if r.get("tree_path"))
        mappable = 0
        for r in rows:
            p = str(r.get("path") or "")
            if not p.startswith(_WORKTREE_PREFIX) or r.get("tree_path"):
                continue
            tail = p[len(_WORKTREE_PREFIX):].split("/", 1)
            if len(tail) == 2 and tail[1] in cited_map:
                mappable += 1
        lines = [
            "touch-grain lane health (EVD-2 — diagnosis, not trend; report-only):",
            f"  outcome rows: {total} across {sessions} session(s); {with_cb} carry cited_by touch provenance",
            f"  touchmap: {len(cited_map)} cited path(s) / {len(reminders_map)} reminder path(s)",
        ]
        # MEA-4: rows by producing version — the forensic complement to OPS-1's skew
        # line. Historical version-less rows aggregate as ONE "unstamped" bucket, never
        # backfilled; provenance only, nothing branches on the stamp.
        if total:
            from .telemetry import _producer_version

            by_v: dict = {}
            for r in rows:
                key = r.get("v") if isinstance(r.get("v"), str) else "unstamped"
                by_v[key] = by_v.get(key, 0) + 1
            running = _producer_version() or "unknown"
            buckets = ", ".join(
                f"{k}: {n}" for k, n in sorted(by_v.items(), key=lambda kv: (-kv[1], kv[0]))
            )
            lines.append(
                f"  rows by producing version (running v{running}): {buckets} — provenance "
                "only (MEA-4); rows stamped by an older version date the lagged-hook window "
                "from the ledger itself"
            )
        if total:
            share = 100.0 * len(wt_paths) / total
            lines.append(
                f"  worktree-prefixed rows: {len(wt_paths)} of {total} ({share:.0f}%) — "
                f"{stamped} carry tree_path (normalized at record time, MEA-6 — these join "
                f"the touch-grain evidence directly); {mappable} historical row(s) would map "
                "if prefix-stripped (recorded before normalization; left untouched)"
            )
        if total and not with_cb:
            lines.append(
                "  diagnosis: 0 rows carry cited_by — rows written by a pre-T16 live hook never "
                "carry provenance (live-hook lag: the lane records only once the INSTALLED "
                "plugin ships the jit lane and touchmap.json exists)"
            )
        if wt_paths and not stamped:
            lines.append(
                "  diagnosis: worktree-prefixed touches starved the touch-grain joins — these "
                "rows predate MEA-6's record-time normalization (new worktree touches carry "
                "tree_path and join directly); historical rows stay untouched"
            )
        lines.append("")
        lines.append(format_touch_grain_report(memory_dir, td))
        return "\n".join(lines)
    except Exception:
        return "touch-grain lane health: unreadable ledgers (report-only)."


def format_report(memory_dir: str, telemetry_dir: Optional[str] = None) -> str:
    """One-line KPI-2 proxy report for the CLI / doctor to present. Never raises."""
    r = injection_precision(memory_dir, telemetry_dir)
    n = r.get("injected_with_cites", 0)
    if not n:
        return "injection precision (KPI-2): no injected-then-touched signal yet."
    pct = (r["precision"] or 0) * 100
    return (
        f"injection precision (KPI-2) ~ {pct:.0f}% — {r['hits']}/{n} injected memories had a "
        f"cited file touched in-session across {r['sessions']} session(s). Measurement only."
    )


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="SIG-4 PostToolUse read-signal (KPI-2). --from-hook logs one file-touch "
        "outcome from the PostToolUse stdin payload; otherwise prints the injection-precision "
        "proxy over the ledger. Measurement only — never influences ranking."
    )
    parser.add_argument(
        "--from-hook", action="store_true", help="read the PostToolUse JSON payload from stdin and log it"
    )
    parser.add_argument("--report", action="store_true", help="print the KPI-2 injection-precision proxy")
    parser.add_argument(
        "--touch-grain",
        action="store_true",
        help="print the touch-grain lane health/diagnosis + the touch-vs-session grain "
        "comparison (JIT-2/EVD-2; report-only — explains the lane's zeros, changes nothing)",
    )
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args(argv)
    try:
        if args.from_hook:
            try:
                payload = json.load(sys.stdin)
            except Exception:
                payload = None
            if isinstance(payload, dict):
                context: list = []
                record_from_payload(
                    payload,
                    memory_dir=args.memory_dir,
                    repo_root=args.repo_root,
                    context_out=context,
                )
                if context:
                    # JIT-1: the first-touch reminder rides this same spawn's stdout as
                    # the hook's ONE additionalContext JSON (the QUA-2 contract: stdout
                    # is empty or exactly one hookSpecificOutput object).
                    print(
                        json.dumps(
                            {
                                "hookSpecificOutput": {
                                    "hookEventName": "PostToolUse",
                                    "additionalContext": "\n".join(context),
                                }
                            },
                            ensure_ascii=False,
                        )
                    )
            return 0  # fire-and-forget: PostToolUse must never fail loudly
        memory_dir = args.memory_dir
        if memory_dir is None:
            memory_dir, _ = resolve_dirs()
        if args.touch_grain:
            print(format_lane_health(memory_dir))
            return 0
        print(format_report(memory_dir))
        return 0
    except Exception:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
