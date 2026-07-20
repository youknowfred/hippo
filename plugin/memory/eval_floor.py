"""GRF-3: the dense-floor calibration sweep — RET-9's missing calibration half.

``recall._DENSE_FLOOR_BY_MODEL`` is a static table calibrated on the maintainer's golden
corpus; ``doctor.check_abstention_floor_sanity`` (RET-9's leak-detector half, shipped
2026-07-10) can SAY "the floor is too permissive for this corpus" but not what number to
raise it to. This sweep automates RET-1's documented cosine-separation recipe: embed the
corpus's own on-topic queries and off-topic probes with its configured/warm model, take
each query's best cosine over the gated matrix — the exact value the floor gates in
``recall._dense_rank_rows`` — and recommend a per-model/per-corpus floor from the
separation of the two distributions. RAW cosine space throughout, never fused RET-8
metrics (the two logged fused-vs-cosine incommensurability corrections).

Advisory-only by construction (inv4): the sweep recommends, one doctor line compares the
recommendation to the configured entry, and a HUMAN edits the table (or sets
``HIPPO_DENSE_FLOOR``). Nothing here writes a floor anywhere. The persisted report is
derived/gitignored telemetry (inv1), keyed to the corpus fingerprint so doctor can tell a
stale sweep from a fresh one. Ettin/Li-LSR reranker arms are explicitly out of scope
(ED-3-blocked — see the roadmap's not_pursuing).

Decomposed out of ``eval_recall.py`` (ED5R-3, pure code motion): the sweep reads the two
eval fixtures, embeds them, and writes ONE advisory report that only
``doctor.check_floor_calibration`` consumes — nothing in the gate path calls into it and it
calls nothing in the façade, which made it the cleanest seam when ``eval_recall.py`` reached
its ratchet pin. The façade re-exports every public name here, so
``memory.eval_recall.floor_sweep`` and ``python -m memory.eval_recall --floor-sweep`` are
unchanged. ``write_floor_sweep``'s crash-contract pin moves WITH it (the INV-3 registry in
``tests/test_crash_faults.py`` keys on the file holding the call site).
"""

from __future__ import annotations

import json
import os
import time
from typing import List, Optional

from .build_index import LoadedIndex, default_index_dir, load_index
from .eval_fixtures import _default_abstention_set_path, _default_hard_set_path
from .eval_ledger import corpus_fingerprint
from .eval_metrics import (
    _DEFAULT_CATEGORY,
    load_abstention_set,
    load_hard_set,
    resolvable_row,
)
from .provenance import ensure_self_ignoring_dir, resolve_dirs

_FLOOR_SWEEP_NAME = "floor_sweep.json"
_FLOOR_SWEEP_SCHEMA = 1


def default_floor_sweep_path(memory_dir: str, telemetry_dir: Optional[str] = None) -> str:
    """``<telemetry_dir>/floor_sweep.json`` — beside the run ledger (derived, gitignored)."""
    from .telemetry import default_telemetry_dir

    return os.path.join(telemetry_dir or default_telemetry_dir(memory_dir), _FLOOR_SWEEP_NAME)


def recommend_floor(on_scores: List[float], off_scores: List[float]) -> Optional[dict]:
    """Pure separation math over raw cosines. ``None`` when either side is empty.

    Clean separation (every on-topic max above every off-topic max): recommend the
    midpoint of the gap. Overlap: recommend the 10th-percentile on-topic score — the
    conservative "keep ~90% of real hits admitted" point — and report the leak/cut
    counts at that floor so the human sees exactly what the overlap costs. Either way
    ``safety_delta`` = recommendation − best off-topic cosine: positive means every
    off-topic probe stays below the recommended floor; negative names the leak margin.
    """
    if not on_scores or not off_scores:
        return None
    on = sorted(float(s) for s in on_scores)
    off = sorted(float(s) for s in off_scores)
    on_min, off_max = on[0], off[-1]
    overlap = on_min <= off_max
    if not overlap:
        recommended = round((on_min + off_max) / 2.0, 4)
    else:
        p10 = max(0, min(len(on) - 1, int(len(on) * 0.10)))
        recommended = round(on[p10], 4)
    return {
        "recommended": recommended,
        "overlap": overlap,
        "on_n": len(on),
        "off_n": len(off),
        "on_min": round(on_min, 4),
        "off_max": round(off_max, 4),
        "safety_delta": round(recommended - off_max, 4),
        "leaked_off": sum(1 for s in off if s >= recommended),
        "cut_on": sum(1 for s in on if s < recommended),
    }


def _raw_max_cosines(index: LoadedIndex, queries: List[str]) -> List[float]:
    """Best cosine per query over the WHOLE dense matrix — what the floor actually gates.

    Embeds with the corpus's configured/warm model via ``recall.embed_query`` — resolved
    through the module attribute so hermetic tests' fake embedders apply (offline; the
    caller has already verified ``dense_ready``). A query that fails to embed is skipped
    (better a smaller honest sample than a fabricated zero).

    ABS-4 — this scored ``sims[:n_desc]`` while calling it "the exact quantity the dense
    floor gates": true when GRF-3 wrote it, false since RET-2 widened the matrix.
    ``_dense_rank_rows`` applies the floor ONCE at row level, to description AND body-chunk
    rows by design ("one calibrated number"), so calibrating on the narrower population made
    the sweep optimistic about leakage — a body chunk is where an adjacent-technical query
    finds its best match. On hippo's corpus the sweep contradicted the runtime it advises:
    description-only saw off-topic max 0.6523 and promised "at 0.663, 0 probes would leak",
    but the gated matrix tops out at 0.7223 and 2/11 still admit. Scoring it all costs
    nothing — the matmul covers every row.
    """
    from . import recall as _recall_mod

    out: List[float] = []
    for q in queries:
        if not q:
            continue
        try:
            qvec = _recall_mod.embed_query(q, allow_download=False)
            out.append(round(float((index.dense @ qvec).max()), 6))
        except Exception:
            continue
    return out


def floor_sweep(
    memory_dir: Optional[str] = None,
    index_dir: Optional[str] = None,
    hard_set_path: Optional[str] = None,
    abstention_set_path: Optional[str] = None,
    *,
    telemetry_dir: Optional[str] = None,
    write: bool = True,
) -> dict:
    """Run the calibration sweep; persist the report for doctor; return it.

    On-topic queries: the hard-set rows (non-abstention categories) whose expected
    stems actually exist in THIS corpus — a row whose answer the corpus lacks would
    drag the on-topic minimum down with an honest-but-irrelevant low cosine. Off-topic
    probes: the abstention set. Both resolve through the same default-fixture paths
    ``evaluate()`` uses, so a project's ``.audit-fixtures/`` rows take precedence when
    present. Loud, structured failure (``{"ok": False, "error": ...}``) when the dense
    model is unavailable — a sweep cannot calibrate a floor it cannot measure.
    """
    if memory_dir is None:
        memory_dir, _ = resolve_dirs()
    if index_dir is None:
        index_dir = default_index_dir(memory_dir)
    index = load_index(index_dir)
    if index is None or not len(index):
        return {"ok": False, "error": "no index / empty corpus — build the index first"}
    if not index.dense_ready or index.dense is None:
        return {
            "ok": False,
            "error": "dense model unavailable (bm25-only run) — the floor gates raw "
            "cosines, so the sweep needs the dense backend; run /hippo:bootstrap first",
        }

    hs_path = hard_set_path or _default_hard_set_path()
    ab_path = abstention_set_path or _default_abstention_set_path()
    hard_set = load_hard_set(hs_path) if hs_path else []
    probes = load_abstention_set(ab_path) if ab_path else []
    names = {e.get("name") for e in index.entries}
    on_queries = [
        row["query"]
        for row in hard_set
        if (row.get("category") or _DEFAULT_CATEGORY) != "abstention"
        and resolvable_row(names, row)  # MEA-1: the ONE shared predicate (inv5)
    ]
    if not on_queries or not probes:
        return {
            "ok": False,
            "error": "need both on-topic hard-set rows resolvable against this corpus and "
            "off-topic abstention probes — "
            f"(on-topic {len(on_queries)}, off-topic {len(probes)}); draft the on-topic half "
            "via /hippo:audit (it writes recall_hard_set.yaml); hand-author the rest (ABS-1)",
        }

    from .recall import _dense_floor

    on_scores = _raw_max_cosines(index, on_queries)
    off_scores = _raw_max_cosines(index, probes)
    rec = recommend_floor(on_scores, off_scores)
    if rec is None:
        return {"ok": False, "error": "embedding produced no usable scores — model failure?"}

    doc = {
        "ok": True,
        "schema": _FLOOR_SWEEP_SCHEMA,
        "model": index.model,
        "configured_floor": _dense_floor(index.model),
        "corpus_fingerprint": corpus_fingerprint(index),
        "generated_at": time.strftime("%Y-%m-%d"),
        **rec,
    }
    if write:
        path = default_floor_sweep_path(memory_dir, telemetry_dir)
        written = write_floor_sweep(doc, path)
        doc["path"] = written.get("path") if written.get("ok") else None
    return doc


def write_floor_sweep(doc: dict, path: str) -> dict:
    """Persist the sweep report (atomic — a torn report must never half-inform doctor).
    ``{ok, path}`` or ``{ok: False, error}``; never raises."""
    from .atomic import write_json_atomic

    try:
        ensure_self_ignoring_dir(os.path.dirname(path))  # SEC-3 self-ignoring pattern
        write_json_atomic(path, doc, indent=2)
        return {"ok": True, "path": path}
    except Exception as exc:
        return {"ok": False, "error": f"floor-sweep write failed: {exc}"}


def read_floor_sweep(memory_dir: str, telemetry_dir: Optional[str] = None) -> Optional[dict]:
    """The persisted sweep report, or None (absent/corrupt/wrong-schema). Never raises."""
    try:
        path = default_floor_sweep_path(memory_dir, telemetry_dir)
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict) or doc.get("schema") != _FLOOR_SWEEP_SCHEMA:
            return None
        return doc
    except Exception:
        return None
