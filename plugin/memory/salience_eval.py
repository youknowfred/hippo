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
from contextlib import contextmanager
from typing import List, Optional

from .provenance import ensure_self_ignoring_dir, resolve_dirs

_SALIENCE_AB_NAME = "salience_ab.json"
_SALIENCE_AB_SCHEMA = 1

# Categories at/below this n are labeled low-n in the report (mirrors MSR-1's
# _BASELINE_N_FLOOR): a delta over two rows is an anecdote, not evidence.
_LOW_N_FLOOR = 3

_ED2_FOOTER = (
    "ED-2: measures only — salience stays owner-decided-OFF (SIG-5 ratified 2026-07-09); "
    "any default flip is a dated owner decision on affirmative evidence, never an "
    "automatic consequence of this report."
)


def default_report_path(memory_dir: str, telemetry_dir: Optional[str] = None) -> str:
    """``<telemetry_dir>/salience_ab.json`` — gitignored, never the golden/CI report."""
    from .telemetry import default_telemetry_dir

    return os.path.join(telemetry_dir or default_telemetry_dir(memory_dir), _SALIENCE_AB_NAME)


@contextmanager
def _salience_flag(on: bool):
    """Set/clear ``HIPPO_SALIENCE`` for one arm, restoring the prior value EXACTLY
    (the ``_dense_disabled_env`` save/restore pattern — an A/B that leaks its flag
    into the caller's environment poisons every later measurement)."""
    prev = os.environ.get("HIPPO_SALIENCE")
    if on:
        os.environ["HIPPO_SALIENCE"] = "1"
    else:
        os.environ.pop("HIPPO_SALIENCE", None)
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("HIPPO_SALIENCE", None)
        else:
            os.environ["HIPPO_SALIENCE"] = prev


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
    from .eval_recall import canonical_json, deterministic_view, evaluate

    if memory_dir is None:
        memory_dir, _ = resolve_dirs()
    if index_dir is None:
        index_dir = default_index_dir(memory_dir)

    inventory = _signal_inventory(memory_dir, index_dir)
    if not inventory.get("ok"):
        return inventory

    # MEA-1 (ED5R-2): evidence carries its instrument's sensitivity — per-category
    # resolvable_n stamped into the condition UNCONDITIONALLY on new evidence files
    # ({} only when there is no fixture/index to measure). The recorded 2026-07-17
    # report predates this stamp and is never rewritten; doctor derives its
    # qualification for old evidence from current fixture-vs-corpus state instead.
    resolvable_by_category: dict = {}
    try:
        from .build_index import load_index
        from .eval_metrics import hard_set_resolvability, load_hard_set

        _idx = load_index(index_dir)
        if _idx is not None and hard_set_path:
            resolvable_by_category = hard_set_resolvability(_idx, load_hard_set(hard_set_path))
    except Exception:
        resolvable_by_category = {}

    def _arm(on: bool) -> dict:
        with _salience_flag(on):
            return evaluate(
                memory_dir=memory_dir,
                index_dir=index_dir,
                hard_set_path=hard_set_path,
                k=k,
                telemetry_dir=telemetry_dir,
            )

    off_1 = _arm(False)
    if not off_1.get("ok") and "error" in off_1:
        return {"ok": False, "error": f"OFF arm failed: {off_1['error']}"}
    on = _arm(True)
    off_2 = _arm(False)

    off_view_1 = canonical_json(deterministic_view(off_1))
    off_view_2 = canonical_json(deterministic_view(off_2))
    if off_view_1 != off_view_2:
        return {
            "ok": False,
            "error": "OFF-arm self-check FAILED: the two flag-off runs bracketing the ON "
            "arm are not byte-identical — the rig (or the flag flip) leaked state, so the "
            "paired delta is not attributable to salience. Nothing recorded.",
        }

    identical_arms = off_view_1 == canonical_json(deterministic_view(on))
    off_cat = off_1.get("by_category") or {}
    on_cat = on.get("by_category") or {}
    deltas = {}
    for cat in sorted(set(off_cat) | set(on_cat)):
        o, n_ = off_cat.get(cat) or {}, on_cat.get(cat) or {}
        n_rows = int(min(o.get("n") or 0, n_.get("n") or 0) or max(o.get("n") or 0, n_.get("n") or 0))
        deltas[cat] = {
            "recall": round((n_.get("recall") or 0.0) - (o.get("recall") or 0.0), 4),
            "mrr": round((n_.get("mrr") or 0.0) - (o.get("mrr") or 0.0), 4),
            "n": n_rows,
            **({"low_n": True} if n_rows <= _LOW_N_FLOOR else {}),
        }

    committed = inventory["committed_usage"]
    corpus_n = int(off_1.get("count") or 0)
    report = {
        "ok": True,
        "schema": _SALIENCE_AB_SCHEMA,
        "flag": "HIPPO_SALIENCE",
        "generated_at": time.strftime("%Y-%m-%d"),
        # The condition stamp: index-mode x query-mode x backend. Both arms share the
        # same resident index and the same query mode — only the flag differs.
        "condition": {
            "index_mode": "dense" if off_1.get("dense_ready") else "bm25-only",
            "query_mode": "dense+bm25" if off_1.get("dense_ready") else "bm25-only",
            "backend": off_1.get("backend"),
            "model": off_1.get("model"),
            "corpus_n": corpus_n,
            "hard_set_n": off_1.get("hard_set_n"),
            "resolvable_by_category": resolvable_by_category,
        },
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
        "off_by_category": off_cat,
        "on_by_category": on_cat,
        "deltas": deltas,
        "off_arm_self_check": "pass — flag-off runs byte-identical before and after the ON arm",
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
