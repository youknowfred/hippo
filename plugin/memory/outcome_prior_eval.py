"""MEA-5: the EVD-4 Arm B rig — ``eval --ab HIPPO_OUTCOME_PRIOR``.

RET-14 shipped the outcome prior (recall_salience._apply_outcome_prior) default-OFF and
severed its measurement behind "nonzero touch/outcome rows + a dated owner decision."
Both ripened: the data half (147 cited_by rows at build, growing) and the decision
(Q1(r5), 2026-07-19 — commissioned, sequenced after MEA-1/MEA-2 so the report reads
through an instrument whose sensitivity is known). This module is exactly the minimal
delta EVD-4 named: the flag's own harness on the DRM-3 convention (each ``--ab`` flag
owns a self-contained module; ``eval_recall.main`` stays the one CLI front door),
parameterizing ``ab_runner.run_flag_arms`` (inv5 — never a second copy of the
OFF→ON→OFF core).

The flag-OFF cache problem, decided at build: under default-OFF, SessionStart never
writes ``outcome.json`` (session_start gates the writer on the flag), so an ON arm
would read an absent cache and silently measure nothing. The harness therefore STAGES
the cache in-process for the run — ``write_outcome_cache(index_dir,
injection_hits(...))``, the exact computation the flag-ON SessionStart performs — and
RESTORES prior state after (a pre-existing cache is left untouched; a staged one is
removed, so a flag-OFF machine keeps no cache it did not write).

The outcome-signal precondition (ED-3 posture): a corpus with ZERO outcome-confirmed
memories is legal — the arms are then byte-identical and the report says SELF-CHECK,
not finding. The loud error is reserved for the wiring-bug shape (hits exist but the
staged cache resolves to an empty boost map — the usage-prior-blind lesson).

MEASURES ONLY — ED-2/LIF-7 binding: HIPPO_OUTCOME_PRIOR stays default-OFF; the ranking
flip remains a separate dated owner decision on affirmative evidence; the touch-grain
GRADUATION arm stays severed (the standing not_pursuing row). Evidence lands beside
``salience_ab.json`` in the gitignored telemetry dir; its standing reader is
``check_salience_evidence``'s sibling sentence (decided at build — no second doctor
check; the one salience-evidence line carries both files' existence).
"""

from __future__ import annotations

import json
import os
import time
from typing import List, Optional

from .provenance import ensure_self_ignoring_dir, resolve_dirs

_OUTCOME_AB_NAME = "outcome_prior_ab.json"
_OUTCOME_AB_SCHEMA = 1

_ED2_FOOTER = (
    "ED-2/LIF-7: measures only — HIPPO_OUTCOME_PRIOR stays default-OFF (RET-14); any "
    "ranking flip is a separate dated owner decision on affirmative evidence, never an "
    "automatic consequence of this report; the touch-grain graduation arm stays severed."
)


def default_report_path(memory_dir: str, telemetry_dir: Optional[str] = None) -> str:
    """``<telemetry_dir>/outcome_prior_ab.json`` — gitignored, beside salience_ab.json."""
    from .telemetry import default_telemetry_dir

    return os.path.join(telemetry_dir or default_telemetry_dir(memory_dir), _OUTCOME_AB_NAME)


def run_ab(
    memory_dir: Optional[str] = None,
    index_dir: Optional[str] = None,
    hard_set_path: Optional[str] = None,
    k: int = 10,
    *,
    telemetry_dir: Optional[str] = None,
    write: bool = True,
) -> dict:
    """OFF → ON → OFF under a staged outcome cache; paired deltas; self-checked.

    See the module docstring for the cache-staging and precondition semantics. The
    report carries the condition stamp (including MEA-1's ``resolvable_by_category``),
    the outcome-signal inventory, per-category deltas with low-n labels, the OFF-arm
    self-check, and the dated ED-2 footer. Never touches gates; writes only the
    gitignored evidence file (and the staged cache it removes again).
    """
    from .build_index import default_index_dir
    from .outcome import injection_hits, read_outcome_cache, write_outcome_cache
    from .recall_salience import _outcome_boost_map
    from .telemetry import default_telemetry_dir

    if memory_dir is None:
        memory_dir, _ = resolve_dirs()
    if index_dir is None:
        index_dir = default_index_dir(memory_dir)
    td = telemetry_dir or default_telemetry_dir(memory_dir)

    hits = injection_hits(memory_dir, td)
    preexisting = read_outcome_cache(index_dir) is not None
    staged = False
    if hits and not preexisting:
        if not write_outcome_cache(index_dir, hits):
            return {
                "ok": False,
                "error": "outcome-signal staging failed: could not write the in-process "
                "outcome cache for the ON arm",
            }
        staged = True
    cache_note = (
        "pre-existing (left untouched)"
        if preexisting
        else (
            "written in-process for the ON arm (removed after — a flag-OFF machine keeps "
            "no cache it did not write)"
            if staged
            else "absent (no outcome signal to cache)"
        )
    )
    try:
        if hits and not _outcome_boost_map(index_dir):
            return {
                "ok": False,
                "error": "outcome-signal precondition violated: outcome-confirmed hits exist "
                "but recall's _outcome_boost_map resolved empty — the ON arm would measure "
                "nothing (the usage-prior-blind shape). Fix the wiring before measuring.",
            }
        from .ab_runner import run_flag_arms

        core = run_flag_arms(
            "HIPPO_OUTCOME_PRIOR",
            memory_dir=memory_dir,
            index_dir=index_dir,
            hard_set_path=hard_set_path,
            k=k,
            telemetry_dir=td,
        )
    finally:
        if staged:
            from .outcome import outcome_cache_path

            try:
                os.remove(outcome_cache_path(index_dir))
            except OSError:
                pass
    if not core.get("ok"):
        return core

    report = {
        "ok": True,
        "schema": _OUTCOME_AB_SCHEMA,
        "flag": "HIPPO_OUTCOME_PRIOR",
        "generated_at": time.strftime("%Y-%m-%d"),
        "condition": core["condition"],
        "signal": {
            "outcome_confirmed_memories": len(hits),
            "cache": cache_note,
        },
        "off_by_category": core["off_by_category"],
        "on_by_category": core["on_by_category"],
        "deltas": core["deltas"],
        "off_arm_self_check": core["off_arm_self_check"],
        "identical_arms": core["identical_arms"],
        **(
            {
                "identical_arms_note": (
                    "self-check pass — "
                    + (
                        "no outcome-confirmed touch evidence: the ON arm had nothing to read"
                        if not hits
                        else "arms byte-identical under the staged cache"
                    )
                    + "; NOT a finding about the outcome prior, and not decision-grade at "
                    "this n (ED-3)"
                )
            }
            if core["identical_arms"]
            else {}
        ),
        "ed2": _ED2_FOOTER,
    }
    if write:
        path = default_report_path(memory_dir, td)
        written = write_report(report, path)
        report["path"] = written.get("path") if written.get("ok") else None
    return report


def write_report(report: dict, path: str) -> dict:
    """Persist the A/B evidence (atomic — a torn report is worse than none).
    ``{ok, path}`` or ``{ok: False, error}``; never raises."""
    from .atomic import write_json_atomic

    try:
        ensure_self_ignoring_dir(os.path.dirname(path))  # SEC-3 self-ignoring pattern
        write_json_atomic(path, report, indent=2)
        return {"ok": True, "path": path}
    except Exception as exc:
        return {"ok": False, "error": f"outcome-prior A/B report write failed: {exc}"}


def read_report(memory_dir: str, telemetry_dir: Optional[str] = None) -> Optional[dict]:
    """The persisted A/B report, or None (absent/corrupt/wrong-schema). Never raises."""
    try:
        path = default_report_path(memory_dir, telemetry_dir)
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict) or doc.get("schema") != _OUTCOME_AB_SCHEMA:
            return None
        return doc
    except Exception:
        return None


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="MEA-5: paired outcome-prior A/B over a live corpus (measures only — ED-2)."
    )
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--index-dir", default=None)
    parser.add_argument("--hard-set", default=None)
    parser.add_argument("--telemetry-dir", default=None)
    parser.add_argument("-k", type=int, default=10)
    args = parser.parse_args(argv)

    report = run_ab(
        memory_dir=args.memory_dir,
        index_dir=args.index_dir,
        hard_set_path=args.hard_set,
        k=args.k,
        telemetry_dir=args.telemetry_dir,
    )
    if not report.get("ok"):
        print(f"outcome-prior A/B: {report.get('error')}")
        return 1

    cond = report["condition"]
    sig = report["signal"]
    print(
        f"outcome-prior A/B [{report['flag']}] backend={cond['backend']} "
        f"corpus={cond['corpus_n']} hard_set={cond['hard_set_n']} — signal: "
        f"{sig['outcome_confirmed_memories']} outcome-confirmed memor"
        + ("y" if sig["outcome_confirmed_memories"] == 1 else "ies")
        + f"; cache: {sig['cache']}"
    )
    resv = cond.get("resolvable_by_category") or {}
    if resv:
        tot_r = sum(v["resolvable_n"] for v in resv.values())
        tot_n = sum(v["n"] for v in resv.values())
        print(
            f"  sensitivity (ED5R-2): {tot_r}/{tot_n} fixture row(s) resolvable against this "
            "corpus — " + ", ".join(f"{c} {v['resolvable_n']}/{v['n']}" for c, v in resv.items())
        )
    for cat, d in sorted(report["deltas"].items()):
        low = " [low n — report-only]" if d.get("low_n") else ""
        print(f"  {cat}: Δrecall={d['recall']:+.4f} Δmrr={d['mrr']:+.4f} n={d['n']}{low}")
    if report.get("identical_arms"):
        print(f"  {report['identical_arms_note']}")
    print(f"  OFF-arm self-check: {report['off_arm_self_check']}")
    if report.get("path"):
        print(f"  evidence recorded: {report['path']}")
    print(f"  {report['ed2']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
