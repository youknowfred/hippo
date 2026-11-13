"""OBS-5: the hard-set pins that no longer resolve.

A row of the corpus's ``.audit-fixtures/recall_hard_set.yaml`` pins the memories that
should answer its query (``expected``). When one of them is renamed, archived, folded into
a digest or deleted, the row keeps scoring against a name the corpus no longer has: a row
with no pin left can only score a miss, and the eval reads that as a recall regression.

Doctor counts those rows: read-only, one YAML parse and one directory listing (the index
manifest only when some pin misses the files, for digest section slugs), never a recall
run. Silent when the corpus has no hard set or every pin resolves. With a pinned baseline
(``recall_eval_baseline.json``) whose ``hard_recall@10`` is above what the fixture can
still score (the share of rows with a pin left), the line says so too.

The line names counts and the remedy, never the pinned names: the rows are re-pointed by
hand in the fixture, and ``hippo eval --draft-update`` drafts the rows for a pin a
supersedes chain replaced (each confirmed per item).
"""

from __future__ import annotations

import json
import os
from typing import Dict, Optional

_FIXTURE = os.path.join(".audit-fixtures", "recall_hard_set.yaml")
_BASELINE = os.path.join(".audit-fixtures", "recall_eval_baseline.json")


def _section_slugs(memory_dir: str) -> set:
    try:
        from .build_index import _read_manifest_json, default_index_dir

        rec = (_read_manifest_json(default_index_dir(memory_dir)) or {}).get("sections")
        slugs = rec.get("slugs") if isinstance(rec, dict) else None
        return set(slugs or ())
    except Exception:
        return set()


def pin_drift(memory_dir: str) -> Optional[Dict[str, float]]:
    """``{rows, drifted, unscorable, ceiling}`` for the corpus's hard set, or None when it
    has none (or it does not parse). ``drifted``: rows with a pin that no longer resolves;
    ``unscorable``: rows with no pin left; ``ceiling``: the share of rows that can still hit."""
    from .eval_metrics import load_hard_set
    from .lint_floor import memory_exists

    path = os.path.join(memory_dir, _FIXTURE)
    if not os.path.isfile(path):
        return None
    rows = load_hard_set(path)
    if not rows:
        return None
    exists = memory_exists(memory_dir)
    slugs: Optional[set] = None

    def resolves(name: str) -> bool:
        nonlocal slugs
        if exists(name):
            return True
        if slugs is None:
            slugs = _section_slugs(memory_dir)
        return name in slugs

    drifted = unscorable = 0
    for row in rows:
        hits = [resolves(n) for n in row["expected"]]
        if not all(hits):
            drifted += 1
            if not any(hits):
                unscorable += 1
    n = len(rows)
    return {"rows": n, "drifted": drifted, "unscorable": unscorable, "ceiling": (n - unscorable) / n}


def _pinned_recall(memory_dir: str) -> Optional[float]:
    try:
        with open(os.path.join(memory_dir, _BASELINE), encoding="utf-8") as fh:
            v = ((json.load(fh).get("metrics") or {}).get("gates") or {}).get("hard_recall@10")
        return float(v) if isinstance(v, (int, float)) else None
    except Exception:
        return None


def check_hard_set_pins(ctx) -> Optional[Dict[str, str]]:
    try:
        d = pin_drift(ctx.memory_dir)
    except Exception:
        return None
    if not d or not d["drifted"]:
        return None
    msg = (
        f"hard-set pins: {d['drifted']} of {d['rows']} row(s) in {_FIXTURE} pin a memory that no "
        f"longer resolves (renamed, archived, folded into a digest or deleted); "
        f"{d['unscorable']} resolve to nothing and can only score as misses. Re-point those rows "
        "in the fixture; `hippo eval --draft-update` drafts replacement rows for a superseded "
        "pin, each confirmed per item."
    )
    pinned = _pinned_recall(ctx.memory_dir)
    if pinned is not None and pinned > d["ceiling"] + 1e-9:
        msg += (
            f" The pinned baseline ({_BASELINE}) records hard recall@10 {pinned:g}; the fixture "
            f"can now score at most {round(d['ceiling'], 4):g}, so re-pin it with `hippo eval --write-baseline` after the refresh."
        )
    return {"status": "warn", "message": msg}
