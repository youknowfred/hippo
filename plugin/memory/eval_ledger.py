"""MSR-1 run-ledger + baseline-diff primitives — decomposed out of ``eval_recall``
(which re-exports every name here unchanged; the --out/--baseline/--repeat CLI
plumbing and the two pinned writers, ``append_run_ledger``/``write_baseline``, stay
there).

Carries the deterministic pass^k view, canonical JSON, the corpus/fixture
fingerprints, and the report-only baseline diff.
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

from .build_index import LoadedIndex


# --------------------------------------------------------------------------- #
# MSR-1: the eval run ledger + fingerprint-keyed baseline diff + pass^k probe.
#
# RET-8 gave hippo category-tagged eval with tracked gates, but every gate is an
# ABSOLUTE frozen threshold — a regression that stays above it is invisible, no run
# persists, and nothing ever proved the deterministic metrics are actually
# deterministic. Three additions, all REPORT-ONLY (no gate constant moves, no new
# CI-failing check — the fail ratchet is explicitly deferred behind a dated owner
# blessing of the first baseline, never a metric-proxied gate):
#
#   --json / --out    serialize the full evaluate() report; --out appends it (with
#                     git-HEAD + fixture + corpus fingerprints) to a gitignored,
#                     byte-rotated run ledger in the derived telemetry dir (inv1).
#   --baseline        report-only per-gate/per-category drift vs a COMMITTED baseline
#                     file (the recall_hard_set.yaml fixture-class precedent, written
#                     via --write-baseline). Comparability is fingerprint-KEYED:
#                     a fixture/corpus fingerprint mismatch SKIPS with a loud note
#                     (different inputs are not drift); a HEAD difference is the
#                     attribution context and prints, never skips.
#   --repeat k        the pass^k determinism probe: k FRESH processes on the hermetic
#                     (HIPPO_DISABLE_DENSE=1) lane must produce byte-identical
#                     deterministic metrics (epsilon=0). Latency and every other
#                     wall-clock-derived value is excluded (see _VOLATILE_KEYS);
#                     any nonzero delta is a bug to fix, not jitter to tolerate.
# --------------------------------------------------------------------------- #
_RUN_LEDGER_NAME = "eval_runs.jsonl"
_BASELINE_FILENAME = "recall_eval_baseline.json"
_BASELINE_SCHEMA = 1
# Categories at/below this n are structurally too thin for their delta to mean much
# (today's multi-hop fixture is n=2 until GRF-2 grows it) — their drift lines carry an
# explicit low-n marker and are ALWAYS report-only, like everything else here.
_BASELINE_N_FLOOR = 3

# Report keys derived from wall-clock or ledger-external state — excluded from the
# determinism view so the pass^k claim is about the METRICS, not the machine:
#   latency/cold_latency + their two gate entries — timing;
#   staleness_half_life — ages are computed against *now*;
#   ok — folds the latency gates' pass flags in, so it inherits their volatility.
_VOLATILE_KEYS = ("latency", "cold_latency", "staleness_half_life", "ok")
_VOLATILE_GATES = ("recall_p95_ms", "cold_p95_ms")


def deterministic_view(report: dict) -> dict:
    """The report minus every wall-clock-derived value — the pass^k comparison surface.

    A deep-enough copy (top level + gates) that the caller's report is never mutated.
    """
    view = {k: v for k, v in report.items() if k not in _VOLATILE_KEYS}
    gates = report.get("gates")
    if isinstance(gates, dict):
        view["gates"] = {k: v for k, v in gates.items() if k not in _VOLATILE_GATES}
    return view


def canonical_json(view: dict) -> str:
    """One canonical byte form (sorted keys, no whitespace variance) for byte-identity."""
    return json.dumps(view, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _git_head(memory_dir: Optional[str], repo_root: Optional[str]) -> Optional[str]:
    """The corpus repo's HEAD sha, or None (non-git corpus). CLI-only — never the hot path."""
    from .provenance import run_git

    try:
        root = repo_root
        if not root and memory_dir:
            root = run_git(
                ["rev-parse", "--show-toplevel"], os.path.dirname(os.path.abspath(memory_dir))
            ).strip()
        if not root:
            return None
        return run_git(["rev-parse", "HEAD"], root).strip() or None
    except Exception:
        return None


def corpus_fingerprint(index: LoadedIndex) -> str:
    """sha256 over exactly the compare-field lists ``build_index.refresh_index`` uses to
    decide "corpus unchanged" (entry hashes, body-chunk hashes, invalid_after,
    source_commit_time, steer, confidence) — one definition of corpus identity, reused,
    so the baseline diff and the index refresh can never disagree about what "the same
    corpus" means."""
    import hashlib

    entries = index.manifest.get("entries", []) or []
    chunks = index.manifest.get("body_chunks", []) or []
    material = [
        [e.get("hash") for e in entries],
        [c.get("hash") for c in chunks],
        [e.get("invalid_after") for e in entries],
        [e.get("source_commit_time") for e in entries],
        [e.get("steer") for e in entries],
        [e.get("confidence") for e in entries],
    ]
    return hashlib.sha256(canonical_json({"corpus": material}).encode("utf-8")).hexdigest()


def fixture_fingerprint(*paths: Optional[str]) -> str:
    """sha256 over the raw bytes of every provided fixture file, position-stable.

    An absent/None path contributes a marker (not silence) so "hard set present, no
    abstention set" and "abstention set present, no hard set" can never collide."""
    import hashlib

    h = hashlib.sha256()
    for p in paths:
        if p and os.path.exists(p):
            try:
                with open(p, "rb") as fh:
                    h.update(hashlib.sha256(fh.read()).hexdigest().encode("ascii"))
            except Exception:
                h.update(b"<unreadable>")
        else:
            h.update(b"<absent>")
        h.update(b"|")
    return h.hexdigest()


def default_run_ledger_path(memory_dir: str, telemetry_dir: Optional[str] = None) -> str:
    """``<telemetry_dir>/eval_runs.jsonl`` — beside the recall ledger (derived, gitignored)."""
    from .telemetry import default_telemetry_dir

    return os.path.join(telemetry_dir or default_telemetry_dir(memory_dir), _RUN_LEDGER_NAME)


def read_run_ledger(memory_dir: str, telemetry_dir: Optional[str] = None):
    """Yield parsed eval-run rows (oldest first), skipping corrupt lines. Never raises.

    The MSR-1 ledger's first read-side consumer beyond ``--baseline`` diffing: TMB-4's
    doctor line reads the LATEST run's persisted ``update_knowledge`` block instead of
    re-running the eval at doctor time (ED2R-1 — persisted eval is what downstream
    surfaces consume).
    """
    try:
        path = default_run_ledger_path(memory_dir, telemetry_dir)
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except Exception:
        return


def baseline_metrics(report: dict) -> dict:
    """The comparable (deterministic, per-metric/per-category) subset a baseline pins."""
    view = deterministic_view(report)
    gates = {k: g.get("value") for k, g in (view.get("gates") or {}).items()}
    tokens = view.get("tokens") or {}
    return {
        "gates": gates,
        "by_category": view.get("by_category") or {},
        "tokens": {k: tokens.get(k) for k in ("full", "floor", "recall_avg", "net")},
        "body_probe": view.get("body_probe") or {},
        "count": view.get("count"),
        "hard_set_n": view.get("hard_set_n"),
        "backend": view.get("backend"),
    }


def _fmt_delta(new, old) -> str:
    try:
        d = float(new) - float(old)
    except (TypeError, ValueError):
        return f"{old!r} -> {new!r}"
    return f"{old} -> {new} ({'+' if d >= 0 else ''}{round(d, 4)})"


def diff_baseline(
    report: dict,
    baseline: dict,
    *,
    head: Optional[str],
    fixture_fp: str,
    corpus_fp: str,
) -> List[str]:
    """Report-only drift lines vs a committed baseline. NEVER affects the exit code.

    Comparability is keyed on the fixture + corpus fingerprints: a mismatch means the
    INPUTS changed (different corpus / different fixtures), so per-metric deltas would
    compare apples to oranges — the diff SKIPS, loudly naming which key moved. A HEAD
    difference is the whole point (code drift between two runs of the same inputs) and
    prints as attribution context.
    """
    lines: List[str] = []
    if not isinstance(baseline, dict) or baseline.get("schema") != _BASELINE_SCHEMA:
        return [
            "baseline: SKIPPED — unrecognized baseline schema "
            f"(want {_BASELINE_SCHEMA}, got {baseline.get('schema') if isinstance(baseline, dict) else type(baseline).__name__})"
        ]
    mismatched = [
        key
        for key, current in (
            ("fixture_fingerprint", fixture_fp),
            ("corpus_fingerprint", corpus_fp),
        )
        if baseline.get(key) != current
    ]
    if mismatched:
        return [
            "baseline: SKIPPED — "
            + " and ".join(mismatched)
            + " changed since the baseline was written; the numbers are not comparable "
            "(different inputs, not drift). Re-pin with --write-baseline after reviewing.",
        ]
    b_head = baseline.get("head")
    lines.append(
        f"baseline: comparing HEAD {(head or 'no-git')[:12]} against baseline "
        f"@ {(b_head or 'no-git')[:12]} (written {baseline.get('generated_at') or '?'})"
    )
    old = baseline.get("metrics") or {}
    new = baseline_metrics(report)
    drift = 0
    old_gates = old.get("gates") or {}
    new_gates = new.get("gates") or {}
    for gname in sorted(set(old_gates) | set(new_gates)):
        if old_gates.get(gname) != new_gates.get(gname):
            drift += 1
            lines.append(f"  gate {gname}: {_fmt_delta(new_gates.get(gname), old_gates.get(gname))}")
    old_cat = old.get("by_category") or {}
    new_cat = new.get("by_category") or {}
    for cat in sorted(set(old_cat) | set(new_cat)):
        o, n = old_cat.get(cat) or {}, new_cat.get(cat) or {}
        if o == n:
            continue
        drift += 1
        n_floor = min(x for x in (o.get("n"), n.get("n")) if isinstance(x, (int, float))) if (o.get("n") is not None or n.get("n") is not None) else 0
        low_n = " [low n — report-only]" if (n_floor or 0) <= _BASELINE_N_FLOOR else ""
        lines.append(
            f"  category {cat}: recall {_fmt_delta(n.get('recall'), o.get('recall'))}, "
            f"mrr {_fmt_delta(n.get('mrr'), o.get('mrr'))}, n {o.get('n')}->{n.get('n')}{low_n}"
        )
    for scalar in ("count", "hard_set_n", "backend"):
        if old.get(scalar) != new.get(scalar):
            drift += 1
            lines.append(f"  {scalar}: {old.get(scalar)!r} -> {new.get(scalar)!r}")
    if old.get("tokens") != new.get("tokens"):
        drift += 1
        lines.append(f"  tokens: {old.get('tokens')} -> {new.get('tokens')}")
    if old.get("body_probe") != new.get("body_probe"):
        drift += 1
        lines.append(f"  body_probe: {old.get('body_probe')} -> {new.get('body_probe')}")
    if not drift:
        lines.append("  no drift — deterministic metrics match the committed baseline.")
    lines.append(
        "  (report-only: baseline drift never fails a run; the CI ratchet stays deferred "
        "behind a dated owner blessing of the first baseline)"
    )
    return lines
