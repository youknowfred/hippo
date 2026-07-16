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

T16 (JIT-1): the same PostToolUse spawn now also runs the jit lane — on the first touch
of a cited file per session, ``jit.observe_touch`` hands back the bounded first-touch
reminder that ``main --from-hook`` prints as the hook's additionalContext. It touches
no ranking; ``HIPPO_DISABLE_JIT`` restores the pre-T16 hook byte-for-byte.
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

    T16 JIT-1 rides the same single Python spawn: the jit lane (``jit.observe_touch``)
    sees the touch first and — on the first touch of a cited file per session — hands
    back the bounded first-touch reminder, appended to ``context_out`` (caller-supplied
    list, the ``compute_corpus`` texts_out pattern) for ``main`` to print as hook
    additionalContext. Existing callers pass no ``context_out`` and see the exact
    pre-T16 behavior; with ``HIPPO_DISABLE_JIT`` set the lane contributes nothing at all.
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
        td = telemetry_dir or default_telemetry_dir(memory_dir)
        try:
            from .jit import observe_touch

            _cited_by, context = observe_touch(
                rel,
                memory_dir=memory_dir,
                repo_root=repo_root,
                telemetry_dir=td,
                session_id=session_id,
            )
            if context and context_out is not None:
                context_out.append(context)
        except Exception:
            pass
        return log_outcome(tool, rel, session_id=session_id, telemetry_dir=td)
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


def _injection_join(memory_dir: str, telemetry_dir: Optional[str] = None) -> dict:
    """The ONE episode×outcome×cited_paths join: ``{(session, name): {"cited", "hit"}}``.

    ``cited`` — the injected memory carries cited_paths (it has a file signal at all);
    ``hit`` — one of those cited files was touched in the SAME session at/after the
    memory's earliest recall ts. Both aggregates (``injection_precision``,
    ``injection_hits``) read this so the join semantics can never fork. Never raises;
    ``{}`` on any failure or empty ledgers.
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
        # session -> [(path, ts)]
        touches: dict = {}
        for o in read_outcomes(td):
            p = o.get("path")
            if not p:
                continue
            ts = o.get("ts")
            touches.setdefault(o.get("session_id"), []).append(
                (p, ts if isinstance(ts, (int, float)) else 0)
            )
        cache: dict = {}
        out: dict = {}
        for (sid, name), inject_ts in injected.items():
            cited = _cited_paths_of(memory_dir, name, cache)
            out[(sid, name)] = {
                "cited": bool(cited),
                "hit": bool(cited)
                and any(p in cited and t >= inject_ts for p, t in touches.get(sid, [])),
            }
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


def injection_hits(memory_dir: str, telemetry_dir: Optional[str] = None) -> dict:
    """Per-MEMORY recorded-outcome evidence: ``{name: {"hits", "sessions"}}``, hits ≥ 1 only.

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
        for (sid, name), r in _injection_join(memory_dir, telemetry_dir).items():
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
        print(format_report(memory_dir))
        return 0
    except Exception:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
