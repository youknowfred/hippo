"""OBS-5: the per-corpus field scoreboard — release notes read field numbers, not bench.

The bench corpus scores recall@10 1.00 while the largest field corpus scored 0.577 the last
time anyone measured it, and no field run was ever persisted. MSR-1 already gave each
corpus a run ledger (``eval_runs.jsonl``, appended by ``eval_recall --out``) and a committed
baseline pin (``.audit-fixtures/recall_eval_baseline.json``, written by
``--write-baseline``). This module only READS them: for each corpus it takes the newest
run row and the pin, and renders one row of aggregate numbers — counts, rates and the
recall@10 delta against the pin. No query text, no memory name and no path is rendered, so
the table can go straight into a public CHANGELOG.

Comparability follows MSR-1: the delta is printed only when the newest run's corpus and
fixture fingerprints match the pin's. A mismatch prints ``n/c`` (not comparable) instead of
a number, because a different corpus or fixture is not drift.

Corpora come from the projects registry (live entries) unless the caller names them.
Read-only, never raises, zero network.
"""

from __future__ import annotations

import json
import os
import time
from typing import Iterable, List, Optional, Tuple

from .eval_ledger import _BASELINE_FILENAME, read_run_ledger

# Columns the table renders, in order. Every value is an aggregate.
_HEADER = (
    "| corpus | memories | backend | hard set (multi-hop) | recall@10 | MRR@10 | "
    "self-recall@10 | off-topic abstained | pinned | Δ recall@10 vs pin |"
)
_RULE = "|---|---:|---|---:|---:|---:|---:|---:|---|---:|"


def _gate_value(report: dict, gate: str):
    g = (report.get("gates") or {}).get(gate) or {}
    return g.get("value")


def _fmt(value, digits: int = 3) -> str:
    if isinstance(value, bool) or value is None:
        return "—"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}" if isinstance(value, float) else str(value)
    return str(value)


def _latest_run(memory_dir: str, telemetry_dir: Optional[str] = None) -> Optional[dict]:
    # Each corpus's OWN sibling telemetry dir: a HIPPO_TELEMETRY_DIR override names one
    # dir, and reading it for every corpus would print one corpus's run on every row.
    from .telemetry_store import _TELEMETRY_DIRNAME

    td = telemetry_dir or os.path.join(
        os.path.dirname(os.path.abspath(memory_dir)), _TELEMETRY_DIRNAME
    )
    latest = None
    for row in read_run_ledger(memory_dir, td):
        if isinstance(row.get("report"), dict):
            latest = row
    return latest


def _read_pin(memory_dir: str) -> Optional[dict]:
    path = os.path.join(memory_dir, ".audit-fixtures", _BASELINE_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def corpus_row(label: str, memory_dir: str, telemetry_dir: Optional[str] = None) -> dict:
    """One corpus's scoreboard numbers: ``{label, run, pin, ...}``. Never raises."""
    row = {"label": label, "has_run": False, "has_pin": False}
    run = _latest_run(memory_dir, telemetry_dir)
    pin = _read_pin(memory_dir)
    if pin is not None:
        row["has_pin"] = True
        row["pinned_at"] = pin.get("generated_at")
    if run is None:
        return row
    report = run["report"]
    row["has_run"] = True
    row["run_date"] = time.strftime("%Y-%m-%d", time.localtime(run.get("ts") or 0))
    row["memories"] = report.get("count")
    row["backend"] = report.get("backend")
    row["hard_set_n"] = report.get("hard_set_n")
    row["multi_hop_n"] = ((report.get("by_category") or {}).get("multi-hop") or {}).get("n", 0)
    row["recall"] = _gate_value(report, "hard_recall@10")
    row["mrr"] = _gate_value(report, "mrr@10")
    row["self_recall"] = _gate_value(report, "self_recall@10")
    abst = report.get("abstention_rate") or {}
    n = abst.get("n") or 0
    row["abstained"] = (round((abst.get("rate") or 0.0) * n), n) if n else None
    if row["hard_set_n"] in (0, None):
        row["recall"] = row["mrr"] = None
    if pin is not None:
        comparable = (
            pin.get("corpus_fingerprint") == run.get("corpus_fingerprint")
            and pin.get("fixture_fingerprint") == run.get("fixture_fingerprint")
        )
        row["comparable"] = comparable
        pinned = ((pin.get("metrics") or {}).get("gates") or {}).get("hard_recall@10")
        if comparable and isinstance(pinned, (int, float)) and isinstance(row["recall"], (int, float)):
            row["delta"] = round(row["recall"] - pinned, 4)
    return row


def render_table(rows: Iterable[dict]) -> str:
    """The markdown scoreboard. A corpus with no persisted run says so in its row."""
    lines = [_HEADER, _RULE]
    for r in rows:
        if not r.get("has_run"):
            lines.append(
                f"| {r['label']} | — | — | — | — | — | — | — | "
                f"{r.get('pinned_at') or 'no pin'} | no run persisted |"
            )
            continue
        hard = (
            f"{r['hard_set_n']} ({r.get('multi_hop_n') or 0})" if r.get("hard_set_n") else "none"
        )
        abst = f"{r['abstained'][0]}/{r['abstained'][1]}" if r.get("abstained") else "—"
        if not r.get("has_pin"):
            pinned, delta = "no pin", "—"
        else:
            pinned = r.get("pinned_at") or "?"
            if "delta" in r:
                d = r["delta"]
                delta = f"{'+' if d >= 0 else ''}{d:.3f}"
            elif r.get("comparable") is False:
                delta = "n/c"
            else:
                delta = "—"
        lines.append(
            f"| {r['label']} | {_fmt(r.get('memories'))} | {r.get('backend') or '—'} | {hard} | "
            f"{_fmt(r.get('recall'))} | {_fmt(r.get('mrr'))} | {_fmt(r.get('self_recall'))} | "
            f"{abst} | {pinned} | {delta} |"
        )
    return "\n".join(lines)


def _label(memory_dir: str) -> str:
    """A corpus's label: its repo root's basename (``<root>/.claude/memory``)."""
    return os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(memory_dir))))


def corpus_arg(value: str) -> List[Tuple[str, str]]:
    """``DIR`` or ``LABEL=DIR`` → ``[(label, dir)]`` (the CLI's one-corpus form)."""
    label, sep, md = value.partition("=")
    if not sep:
        return [(_label(value), value)]
    return [(label, md)]


def registry_corpora() -> List[Tuple[str, str]]:
    """``(label, memory_dir)`` for every live, non-volatile registry entry; label = the
    repo root's basename. ``[]`` when the registry is unreadable."""
    try:
        from .registry import registry_census

        out = []
        for e in registry_census().get("entries") or []:
            if e.get("live") and not e.get("volatile") and not e.get("retired_worktree"):
                out.append((_label(e["memory_dir"]), e["memory_dir"]))
        return out
    except Exception:
        return []


def scoreboard(corpora: Optional[List[Tuple[str, str]]] = None) -> str:
    """Render the scoreboard for ``corpora`` (default: the live registry entries)."""
    pairs = corpora if corpora is not None else registry_corpora()
    return render_table(corpus_row(label, md) for label, md in pairs)
