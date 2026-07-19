"""The generalized flag-context A/B arm runner — the ONE core every ``--ab`` harness
parameterizes (MEA-5 / inv5; extracted from the MSR-5 ``salience_eval`` pattern rather
than copied into a second rig).

``run_flag_arms`` owns the shape every paired flag A/B shares: OFF → ON → OFF over one
live corpus via in-process ``evaluate()`` calls under an exactly-restored env flag; the
OFF-arm byte-identity self-check (the two flag-off runs bracketing the ON arm must be
deterministically identical, or the rig leaked state and the delta is invalid);
per-category deltas with low-n labels; and the MEA-1/ED5R-2 sensitivity stamp
(``resolvable_by_category``) so every A/B evidence file states its instrument's
resolvable_n by construction. Harness modules (``salience_eval``,
``outcome_prior_eval``) keep what is genuinely theirs: preconditions (signal
inventories), report schema/footers, and persistence.

MEASURES ONLY — ED-2 is binding for every consumer: nothing here flips a default or
recommends flipping one.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Optional

# Categories at/below this n are labeled low-n in reports (mirrors MSR-1's
# _BASELINE_N_FLOOR): a delta over two rows is an anecdote, not evidence.
LOW_N_FLOOR = 3


@contextmanager
def flag_context(name: str, on: bool):
    """Set/clear env flag ``name`` for one arm, restoring the prior value EXACTLY
    (the ``_dense_disabled_env`` save/restore pattern — an A/B that leaks its flag
    into the caller's environment poisons every later measurement)."""
    prev = os.environ.get(name)
    if on:
        os.environ[name] = "1"
    else:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = prev


def run_flag_arms(
    flag: str,
    *,
    memory_dir: str,
    index_dir: str,
    hard_set_path: Optional[str] = None,
    k: int = 10,
    telemetry_dir: Optional[str] = None,
) -> dict:
    """OFF → ON → OFF for one env flag; paired per-category deltas; self-checked.

    Returns ``{"ok": True, condition, off_by_category, on_by_category, deltas,
    identical_arms, off_arm_self_check}`` or ``{"ok": False, "error"}``. ``condition``
    carries the index/query/backend/model/corpus stamp AND ``resolvable_by_category``
    (MEA-1 — evidence states its instrument's sensitivity unconditionally; ``{}`` only
    when there is no fixture/index to measure). Latency/cold-latency are excluded from
    identity and deltas (MSR-1's volatile exclusions). Never touches gates; never
    writes.
    """
    from .eval_recall import canonical_json, deterministic_view, evaluate

    def _arm(on: bool) -> dict:
        with flag_context(flag, on):
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
            "paired delta is not attributable to the flag. Nothing recorded.",
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
            **({"low_n": True} if n_rows <= LOW_N_FLOOR else {}),
        }

    # MEA-1 (ED5R-2): the sensitivity stamp rides every A/B evidence file by
    # construction — {} only when there is no fixture/index to measure against.
    resolvable_by_category: dict = {}
    try:
        from .build_index import load_index
        from .eval_metrics import hard_set_resolvability, load_hard_set

        _idx = load_index(index_dir)
        if _idx is not None and hard_set_path:
            resolvable_by_category = hard_set_resolvability(_idx, load_hard_set(hard_set_path))
    except Exception:
        resolvable_by_category = {}

    return {
        "ok": True,
        "condition": {
            "index_mode": "dense" if off_1.get("dense_ready") else "bm25-only",
            "query_mode": "dense+bm25" if off_1.get("dense_ready") else "bm25-only",
            "backend": off_1.get("backend"),
            "model": off_1.get("model"),
            "corpus_n": int(off_1.get("count") or 0),
            "hard_set_n": off_1.get("hard_set_n"),
            "resolvable_by_category": resolvable_by_category,
        },
        "off_by_category": off_cat,
        "on_by_category": on_cat,
        "deltas": deltas,
        "identical_arms": identical_arms,
        "off_arm_self_check": "pass — flag-off runs byte-identical before and after the ON arm",
    }
