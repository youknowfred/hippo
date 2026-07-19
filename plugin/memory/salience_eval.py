"""MSR-5: the ED-2 salience-revisit A/B rig — ``eval --ab HIPPO_SALIENCE``.

SIG-5 decided salience default-OFF (owner-ratified 2026-07-09) because fixture corpora
carry no usage/staleness/recency signal, so a flag-on eval measured nothing. ED-2's
revisit trigger names "a lived-in corpus re-run" — THIS is that rig: run ``evaluate()``
twice in-process over a live per-project corpus (OFF arm, then ON arm under
``HIPPO_SALIENCE=1``, then OFF again), emit a paired per-category delta with a
condition stamp and a dated evidence footer to the gitignored telemetry dir.

MEASURES ONLY — ED-2 is binding. Nothing here (or anywhere) flips the default: the
report is evidence FOR a dated owner decision, never a trigger of one. The only
automatic surface anywhere in MSR-5 is doctor's one deterministic lived-in nudge line.

Preconditions asserted, not assumed (the usage-prior-blind lesson): before the ON arm
runs, the rig resolves the same ``_usage_boost_map``/``_staleness_penalty_map`` recall
itself would use — a non-empty ``usage_aggregates.json`` (or ``stale.json``) that
resolves to an EMPTY map is a loud structured error, because that exact silent shape
is what made every pre-MSR-5 salience eval vacuous. A corpus with NO signal at all is
legal: byte-identical arms then report as a SELF-CHECK PASS, explicitly labeled "not a
finding" (and never "decision-grade" at low n).

The DRM-3 convention: each ``--ab`` flag owns a self-contained harness module;
``eval_recall.main`` stays the one CLI front door and dispatches here.
"""

from __future__ import annotations

import json
import os
import time
from typing import List, Optional

from .ab_runner import LOW_N_FLOOR, flag_context, run_flag_arms
from .provenance import ensure_self_ignoring_dir, resolve_dirs

_SALIENCE_AB_NAME = "salience_ab.json"
_SALIENCE_AB_SCHEMA = 1

# MEA-5: the OFF→ON→OFF core, self-check, deltas, low-n labels, and the MEA-1
# sensitivity stamp all generalized into ab_runner (each --ab flag parameterizes ONE
# runner — inv5); this module keeps what is salience's own: the signal inventory and
# the report schema. The low-n floor's canonical home moved with the delta logic.
_LOW_N_FLOOR = LOW_N_FLOOR

_ED2_FOOTER = (
    "ED-2: measures only — salience stays owner-decided-OFF (SIG-5 ratified 2026-07-09); "
    "any default flip is a dated owner decision on affirmative evidence, never an "
    "automatic consequence of this report."
)


def default_report_path(memory_dir: str, telemetry_dir: Optional[str] = None) -> str:
    """``<telemetry_dir>/salience_ab.json`` — gitignored, never the golden/CI report."""
    from .telemetry import default_telemetry_dir

    return os.path.join(telemetry_dir or default_telemetry_dir(memory_dir), _SALIENCE_AB_NAME)


def _salience_flag(on: bool):
    """Set/clear ``HIPPO_SALIENCE`` for one arm, restoring the prior value EXACTLY.
    Kept under its historical name; delegates to the generalized
    ``ab_runner.flag_context`` (MEA-5 / inv5 — one save/restore implementation)."""
    return flag_context("HIPPO_SALIENCE", on)


def _signal_inventory(memory_dir: str, index_dir: Optional[str]) -> dict:
    """What salience COULD see on this corpus — and the loud precondition check.

    ``{"ok": True, usage_n, stale_n, committed_usage}`` or ``{"ok": False, "error"}``
    when a non-empty ledger resolves to an empty prior map (the usage-prior-blind
    shape MSR-5 exists to close — measuring an ON arm that cannot see its inputs
    would produce a byte-identical non-finding that LOOKS like "salience is inert").
    """
    from .recall import _staleness_penalty_map, _usage_boost_map
    from .staleness import read_stale_cache
    from .telemetry import default_telemetry_dir, read_committed_usage, read_usage_aggregates

    usage_map = _usage_boost_map(memory_dir)
    agg = read_usage_aggregates(default_telemetry_dir(memory_dir))
    if (agg.get("memories") or {}) and (agg.get("sessions", {}).get("count") or 0) > 0 and not usage_map:
        return {
            "ok": False,
            "error": "usage-prior precondition violated: usage_aggregates.json is non-empty "
            "but recall's _usage_boost_map resolved empty — the ON arm would measure "
            "nothing (the pre-MSR-5 usage-blind shape). Fix the wiring before measuring.",
        }
    stale_map = _staleness_penalty_map(index_dir)
    try:
        stale_cache = read_stale_cache(index_dir) if index_dir else {}
    except Exception:
        stale_cache = {}
    if stale_cache and not stale_map:
        return {
            "ok": False,
            "error": "staleness precondition violated: stale.json is non-empty but recall's "
            "_staleness_penalty_map resolved empty — the ON arm would measure nothing.",
        }
    committed = read_committed_usage(memory_dir)
    return {
        "ok": True,
        "usage_n": len(usage_map),
        "stale_n": len(stale_map),
        "committed_usage": committed,
    }


def run_ab(
    memory_dir: Optional[str] = None,
    index_dir: Optional[str] = None,
    hard_set_path: Optional[str] = None,
    k: int = 10,
    *,
    telemetry_dir: Optional[str] = None,
    write: bool = True,
) -> dict:
    """OFF -> ON -> OFF over one live corpus; paired per-category deltas; self-checked.

    The two OFF runs bracket the ON arm: their deterministic views must be
    byte-identical (to each other — i.e. to the pinned production result both before
    AND after the flag flip), or the rig itself leaked state and the delta is invalid.
    Latency/cold-latency are excluded from identity and from deltas (MSR-1's volatile
    exclusions). Never touches gates, never writes anywhere but the gitignored dir.
    """
    from .build_index import default_index_dir

    if memory_dir is None:
        memory_dir, _ = resolve_dirs()
    if index_dir is None:
        index_dir = default_index_dir(memory_dir)

    inventory = _signal_inventory(memory_dir, index_dir)
    if not inventory.get("ok"):
        return inventory

    # MEA-5 (inv5): the OFF→ON→OFF arms, the byte-identity self-check, the per-category
    # deltas with low-n labels, and the MEA-1 resolvable_by_category condition stamp all
    # run in the generalized core — parameterized by flag, never copied per rig.
    core = run_flag_arms(
        "HIPPO_SALIENCE",
        memory_dir=memory_dir,
        index_dir=index_dir,
        hard_set_path=hard_set_path,
        k=k,
        telemetry_dir=telemetry_dir,
    )
    if not core.get("ok"):
        return core

    identical_arms = core["identical_arms"]
    committed = inventory["committed_usage"]
    corpus_n = int(core["condition"].get("corpus_n") or 0)
    report = {
        "ok": True,
        "schema": _SALIENCE_AB_SCHEMA,
        "flag": "HIPPO_SALIENCE",
        "generated_at": time.strftime("%Y-%m-%d"),
        # The condition stamp: index-mode x query-mode x backend (+ the MEA-1
        # sensitivity stamp) — assembled by the shared core; both arms share the same
        # resident index and query mode, only the flag differs.
        "condition": core["condition"],
        "signal": {
            "usage_boosted_n": inventory["usage_n"],
            "staleness_penalized_n": inventory["stale_n"],
        },
        # The absorbed team-soak usage-annotation lane: coverage ONLY, never a ranking
        # input — how much of the corpus committed .usage/<user>.json summaries touch.
        **(
            {
                "committed_usage_coverage": {
                    "label": "coverage only — never a ranking input",
                    "covered_stems": len(committed.get("memories") or ()),
                    "corpus_stems": corpus_n,
                    "summed_sessions": committed.get("sessions") or 0,
                }
            }
            if (committed.get("memories") or committed.get("sessions"))
            else {}
        ),
        "off_by_category": core["off_by_category"],
        "on_by_category": core["on_by_category"],
        "deltas": core["deltas"],
        "off_arm_self_check": core["off_arm_self_check"],
        "identical_arms": identical_arms,
        **(
            {
                "identical_arms_note": "self-check pass on a signal-less/low-signal corpus — "
                "NOT a finding about salience, and not decision-grade at this n"
            }
            if identical_arms
            else {}
        ),
        "ed2": _ED2_FOOTER,
    }
    if write:
        path = default_report_path(memory_dir, telemetry_dir)
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
        return {"ok": False, "error": f"salience A/B report write failed: {exc}"}


def read_report(memory_dir: str, telemetry_dir: Optional[str] = None) -> Optional[dict]:
    """The persisted A/B report, or None (absent/corrupt/wrong-schema). Never raises."""
    try:
        path = default_report_path(memory_dir, telemetry_dir)
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict) or doc.get("schema") != _SALIENCE_AB_SCHEMA:
            return None
        return doc
    except Exception:
        return None


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="MSR-5: paired salience A/B over a live corpus (measures only — ED-2)."
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
        print(f"salience A/B: {report.get('error')}")
        return 1

    cond = report["condition"]
    sig = report["signal"]
    print(
        f"salience A/B [{report['flag']}] backend={cond['backend']} corpus={cond['corpus_n']} "
        f"hard_set={cond['hard_set_n']} — signal: {sig['usage_boosted_n']} usage-boosted, "
        f"{sig['staleness_penalized_n']} staleness-penalized"
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
    cov = report.get("committed_usage_coverage")
    if cov:
        print(
            f"  committed usage coverage: {cov['covered_stems']}/{cov['corpus_stems']} stems "
            f"({cov['label']})"
        )
    if report.get("path"):
        print(f"  evidence recorded: {report['path']}")
    print(f"  {report['ed2']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
