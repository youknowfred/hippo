"""RET-15: threshold calibration — grid-search the knee-ratio/dense-floor constants against
the eval harness's labeled hard-set, instead of hand-tuning by feel each time a regression
surfaces. Prior art this tool is meant to make repeatable: the knee ratio was walked back
0.6 -> 0.5 after GRA-1 found it cost real hard-set hits — a one-off manual fix. This sweeps
``HIPPO_KNEE_RATIO``/``HIPPO_DENSE_FLOOR`` (both already env-overridable in recall.py, no
code change needed to test a candidate) over a small range, running
``eval_recall.evaluate()`` once per candidate, and reports which candidates clear every gate
while maximizing mrr@10 — REPORT-ONLY, never mutates recall.py's shipped constant.

Point ``--memory-dir``/``--hard-set`` at your OWN corpus + hard-set fixture (not just the
shipped golden one) to recalibrate as your corpus grows — an 18-query fixture is enough to
catch a clear regression, not enough to responsibly auto-apply a new production default
from; a tie between the current value and a candidate is reported as a tie, not churned.

Dense-floor sweeping needs a bootstrapped embedding model (the floor's real job is
separating on-topic hits from off-topic admission, which recall@10 alone can't see, so an
``--abstention-set`` fixture materially improves the recommendation) — both requirements
degrade to a clearly-labeled skip, never a silent no-op or a fabricated recommendation.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from .build_index import DEFAULT_MODEL, default_index_dir, load_index
from .eval_recall import evaluate

_KNEE_RATIO_CANDIDATES: Tuple[float, ...] = (0.3, 0.4, 0.5, 0.6, 0.7)
_DENSE_FLOOR_CANDIDATES: Tuple[float, ...] = (0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75)


def _run_with_env(env_overrides: Dict[str, str], **evaluate_kwargs) -> dict:
    """Run ``eval_recall.evaluate()`` with ``env_overrides`` set, restoring the prior
    environment afterward (present-or-absent, not just the prior string) even on failure —
    a sweep must never leak an env var into the caller's process."""
    prior = {k: os.environ.get(k) for k in env_overrides}
    try:
        os.environ.update(env_overrides)
        return evaluate(**evaluate_kwargs)
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def sweep_knee_ratio(
    *,
    memory_dir: Optional[str] = None,
    index_dir: Optional[str] = None,
    hard_set_path: Optional[str] = None,
    k: int = 10,
    candidates: Tuple[float, ...] = _KNEE_RATIO_CANDIDATES,
) -> List[dict]:
    """One eval run per candidate ``HIPPO_KNEE_RATIO``. Each row:
    ``{ratio, "recall@10", "hard_recall@10", "mrr@10", gates_ok}``. Never raises — a
    candidate whose eval errors is recorded with ``gates_ok=False`` and an ``error`` key,
    never silently dropped from the report."""
    rows: List[dict] = []
    for ratio in candidates:
        try:
            report = _run_with_env(
                {"HIPPO_KNEE_RATIO": str(ratio)},
                memory_dir=memory_dir,
                index_dir=index_dir,
                hard_set_path=hard_set_path,
                k=k,
            )
            gates = report.get("gates", {})
            rows.append(
                {
                    "ratio": ratio,
                    "recall@10": gates.get("self_recall@10", {}).get("value"),
                    "hard_recall@10": gates.get("hard_recall@10", {}).get("value"),
                    "mrr@10": gates.get("mrr@10", {}).get("value"),
                    "gates_ok": bool(report.get("ok")),
                }
            )
        except Exception as exc:
            rows.append({"ratio": ratio, "gates_ok": False, "error": str(exc)})
    return rows


def sweep_dense_floor(
    *,
    memory_dir: Optional[str] = None,
    index_dir: Optional[str] = None,
    hard_set_path: Optional[str] = None,
    abstention_set_path: Optional[str] = None,
    k: int = 10,
    candidates: Tuple[float, ...] = _DENSE_FLOOR_CANDIDATES,
) -> dict:
    """Same sweep as ``sweep_knee_ratio``, for ``HIPPO_DENSE_FLOOR`` — SKIPPED (not a silent
    no-op: returns ``{"skipped": "<reason>"}``) when dense isn't actually serving on this
    index, since sweeping a floor that never gets consulted would report meaningless rows."""
    idx_dir = index_dir or (default_index_dir(memory_dir) if memory_dir else None)
    if not idx_dir:
        return {"skipped": "no index_dir/memory_dir resolvable"}
    index = load_index(idx_dir)
    if index is None or not index.dense_ready:
        return {
            "skipped": "dense not ready on this index (cold model cache, HIPPO_DISABLE_DENSE, "
            "or pre-bootstrap) — the floor is never consulted on a BM25-only run"
        }
    rows: List[dict] = []
    for floor in candidates:
        try:
            report = _run_with_env(
                {"HIPPO_DENSE_FLOOR": str(floor)},
                memory_dir=memory_dir,
                index_dir=index_dir,
                hard_set_path=hard_set_path,
                abstention_set_path=abstention_set_path,
                k=k,
            )
            gates = report.get("gates", {})
            rows.append(
                {
                    "floor": floor,
                    "recall@10": gates.get("self_recall@10", {}).get("value"),
                    "hard_recall@10": gates.get("hard_recall@10", {}).get("value"),
                    "mrr@10": gates.get("mrr@10", {}).get("value"),
                    "abstention_rate": gates.get("abstention_rate", {}).get("value"),
                    "gates_ok": bool(report.get("ok")),
                }
            )
        except Exception as exc:
            rows.append({"floor": floor, "gates_ok": False, "error": str(exc)})
    note = None
    if not abstention_set_path:
        note = (
            "no --abstention-set fixture: this sweep can only see recall@10/mrr@10 impact, "
            "not the floor's actual job (rejecting off-topic admission) — treat any "
            "recommendation here as recall-safe, not precision-validated"
        )
    return {"model": index.model or DEFAULT_MODEL, "rows": rows, "note": note}


def _recommend(rows: List[dict], current: float, key: str) -> dict:
    """Among candidates that pass every gate, the one(s) with the HIGHEST mrr@10 — a tie
    that includes the CURRENT value reports ``current_is_best=True`` (never recommend
    churning a shipped constant for a statistically-insignificant tie on a small fixture)."""
    safe = [r for r in rows if r.get("gates_ok") and r.get("mrr@10") is not None]
    if not safe:
        return {"current_is_best": None, "best": [], "best_mrr": None}
    best_mrr = max(r["mrr@10"] for r in safe)
    best = [r[key] for r in safe if r["mrr@10"] == best_mrr]
    return {"current_is_best": current in best, "best": best, "best_mrr": best_mrr}


def format_report(
    *,
    memory_dir: Optional[str] = None,
    index_dir: Optional[str] = None,
    hard_set_path: Optional[str] = None,
    abstention_set_path: Optional[str] = None,
    k: int = 10,
) -> str:
    """The CLI's one rendered report: the knee-ratio sweep (always runs) + the dense-floor
    sweep (runs, or names why it skipped). Never raises — any internal failure renders as
    an honest error line rather than a traceback, matching every other CLI in this package.
    """
    lines: List[str] = []
    try:
        from .recall import _KNEE_RATIO

        knee_rows = sweep_knee_ratio(
            memory_dir=memory_dir, index_dir=index_dir, hard_set_path=hard_set_path, k=k
        )
        knee_rec = _recommend(knee_rows, _KNEE_RATIO, "ratio")
        lines.append(f"knee ratio sweep (current default: {_KNEE_RATIO}):")
        for r in knee_rows:
            mark = "✅" if r.get("gates_ok") else "❌"
            tag = " <- current" if r["ratio"] == _KNEE_RATIO else ""
            if "error" in r:
                lines.append(f"  {mark} ratio={r['ratio']}: error — {r['error']}{tag}")
            else:
                lines.append(
                    f"  {mark} ratio={r['ratio']}: recall@10={r['recall@10']} "
                    f"hard_recall@10={r['hard_recall@10']} mrr@10={r['mrr@10']}{tag}"
                )
        if knee_rec["current_is_best"] is None:
            lines.append("  no candidate cleared every gate — corpus/fixture may be too small or absent")
        elif knee_rec["current_is_best"]:
            lines.append(f"  RECOMMENDATION: keep {_KNEE_RATIO} — already among the best (mrr@10={knee_rec['best_mrr']})")
        else:
            lines.append(
                f"  RECOMMENDATION: consider {knee_rec['best']} (mrr@10={knee_rec['best_mrr']}) "
                f"over the current {_KNEE_RATIO} — re-run on your OWN corpus before applying"
            )

        dense = sweep_dense_floor(
            memory_dir=memory_dir,
            index_dir=index_dir,
            hard_set_path=hard_set_path,
            abstention_set_path=abstention_set_path,
            k=k,
        )
        lines.append("")
        if "skipped" in dense:
            lines.append(f"dense floor sweep: SKIPPED — {dense['skipped']}")
        else:
            from .recall import _DENSE_FLOOR_BY_MODEL, _DENSE_FLOOR_DEFAULT

            current_floor = _DENSE_FLOOR_BY_MODEL.get(dense["model"] or "", _DENSE_FLOOR_DEFAULT)
            lines.append(f"dense floor sweep (model={dense['model']}, current default: {current_floor}):")
            for r in dense["rows"]:
                mark = "✅" if r.get("gates_ok") else "❌"
                tag = " <- current" if r["floor"] == current_floor else ""
                if "error" in r:
                    lines.append(f"  {mark} floor={r['floor']}: error — {r['error']}{tag}")
                else:
                    lines.append(
                        f"  {mark} floor={r['floor']}: recall@10={r['recall@10']} "
                        f"hard_recall@10={r['hard_recall@10']} mrr@10={r['mrr@10']} "
                        f"abstention_rate={r['abstention_rate']}{tag}"
                    )
            if dense.get("note"):
                lines.append(f"  NOTE: {dense['note']}")
    except Exception as exc:
        lines.append(f"calibration error: {exc}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="memory.calibrate_thresholds",
        description="RET-15: grid-search the knee-ratio/dense-floor constants against the "
        "eval harness (report-only — never mutates recall.py's shipped default).",
    )
    ap.add_argument("--memory-dir", default=None)
    ap.add_argument("--index-dir", default=None)
    ap.add_argument("--hard-set", default=None)
    ap.add_argument("--abstention-set", default=None)
    ap.add_argument("-k", type=int, default=10)
    args = ap.parse_args(argv)
    print(
        format_report(
            memory_dir=args.memory_dir,
            index_dir=args.index_dir,
            hard_set_path=args.hard_set,
            abstention_set_path=args.abstention_set,
            k=args.k,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
