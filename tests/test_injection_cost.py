"""MSR-6: the injection-cost AST pin — the ledger/scorecard never aggregates
per-memory touch rates across sessions.

Round 1 KILLED the ``inert-recall-noise-finder`` (a per-memory injected-but-never-
touched table across sessions: statistically underpowered at solo scale, and a
surveillance-shaped artifact hippo deliberately does not build). MSR-6's cost ledger
and scorecard line sit one refactor away from resurrecting it, so the kill is held
MECHANICALLY, not aspirationally (the write-discipline/crash-contract pattern):

  1. The MSR-6 consumers — ``doctor._scorecard_message``, ``telemetry.
     log_injection_producers``/``read_injection_producers``, ``eval_recall.
     session_token_cost`` — must never CALL the per-memory-touch read surfaces
     (``read_outcomes``, ``injection_hits``, ``read_usage_aggregates``,
     ``read_committed_usage``). The scorecard's ONE sanctioned join is
     ``outcome.injection_precision`` — a scalar aggregate.
  2. ``injection_precision``'s return stays SCALAR aggregates only (no per-name
     mapping a caller could quietly start keying memories by).
  3. Producer-ledger rows carry producer LABELS, never memory stems — there is
     structurally nothing per-memory in the file to aggregate.

A false positive here costs one reviewed edit to this file; a silent pass is the
round-1 kill un-dying.
"""

from __future__ import annotations

import ast
import os

from memory import atomic as _atomic

_MEMORY_PKG = os.path.dirname(os.path.abspath(_atomic.__file__))

# The read surfaces that expose per-memory touch/usage grain. The MSR-6 consumers
# below must aggregate at SESSION/PRODUCER grain only, so none of these may appear
# in their call graphs.
_PER_MEMORY_TOUCH_SURFACES = {
    "read_outcomes",
    "injection_hits",
    "read_usage_aggregates",
    "read_committed_usage",
}

# (module, function) -> the functions this pin walks.
_PINNED_CONSUMERS = [
    ("doctor", "_scorecard_message"),
    ("telemetry", "log_injection_producers"),
    ("telemetry", "read_injection_producers"),
    ("eval_recall", "session_token_cost"),
]


def _function_node(module: str, func: str) -> ast.AST:
    path = os.path.join(_MEMORY_PKG, f"{module}.py")
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            return node
    raise AssertionError(
        f"MSR-6 pin target {module}.{func} no longer exists — if it was renamed, "
        "re-point _PINNED_CONSUMERS; if the consumer was removed, prune it here"
    )


def _called_names(node: ast.AST) -> set:
    out = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            f = child.func
            if isinstance(f, ast.Attribute):
                out.add(f.attr)
            elif isinstance(f, ast.Name):
                out.add(f.id)
    return out


def test_cost_consumers_never_touch_per_memory_surfaces():
    flagged = []
    for module, func in _PINNED_CONSUMERS:
        bad = _called_names(_function_node(module, func)) & _PER_MEMORY_TOUCH_SURFACES
        if bad:
            flagged.append(f"{module}.{func} calls {sorted(bad)}")
    assert not flagged, (
        "MSR-6 cost ledger/scorecard reached a per-memory touch surface — that is the "
        "round-1 inert-recall-noise-finder kill resurrecting. Aggregate at session/"
        "producer grain (or via outcome.injection_precision's scalar aggregates) "
        "instead:\n  " + "\n  ".join(flagged)
    )


def test_injection_precision_returns_scalar_aggregates_only(tmp_path):
    """The scorecard's one sanctioned join must stay a scalar-aggregate surface —
    a per-name mapping in this return is the noise-finder's front door."""
    from memory.outcome import injection_precision

    res = injection_precision(str(tmp_path / "memory"), str(tmp_path / "telemetry"))
    assert set(res) == {"injected_with_cites", "hits", "precision", "sessions"}
    for key, value in res.items():
        assert not isinstance(value, (dict, list, set, tuple)), (
            f"injection_precision[{key!r}] grew a container value — keep this surface "
            "scalar (per-memory evidence belongs to injection_hits, which the MSR-6 "
            "consumers are pinned never to call)"
        )


def test_producer_ledger_rows_carry_labels_never_memory_stems(tmp_path):
    """Structural half of the pin: the cost ledger has nothing per-memory in it."""
    import json

    from memory import session_start as S
    from memory import telemetry as T

    td = str(tmp_path / "telemetry")
    labels = {label for label, _fn in S.PRODUCERS}
    assert T.log_injection_producers(
        {label: 10 for label in labels}, total=100, cap=9000, telemetry_dir=td
    )
    rows = [
        json.loads(ln)
        for ln in open(os.path.join(td, "injection_producers.jsonl"), encoding="utf-8")
        if ln.strip()
    ]
    assert len(rows) == 1
    assert set(rows[0]["producers"]) <= labels  # producer labels only, by construction
    assert set(rows[0]) == {"ts", "session_id", "producers", "total", "cap"}
