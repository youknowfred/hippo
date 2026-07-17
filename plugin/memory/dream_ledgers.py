"""/dream ledger paths + readers/writers (DRM-1/2/5/6; decomposed out of ``dream.py``).

The committed append-only apply ledger (``dream-ledger.jsonl``) with its aging-firewall
readers, and the DERIVED candidate/boost ledgers under the telemetry dream dir (inv1).
Every name here re-exports via the ``dream`` façade.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from .dream_config import age_sessions

# --------------------------------------------------------------------------- #
# Paths — candidate ledger (derived, gitignored) vs apply ledger (corpus, committed)
# --------------------------------------------------------------------------- #
def dream_dir(telemetry_dir: str) -> str:
    """``<telemetry_dir>/dream`` — the derived home for candidate ledgers (inv1)."""
    return os.path.join(telemetry_dir, "dream")


def candidate_ledger_path(telemetry_dir: str, pass_id: str) -> str:
    return os.path.join(dream_dir(telemetry_dir), f"candidates-{pass_id}.jsonl")


def apply_ledger_path(memory_dir: str) -> str:
    """``<memory_dir>/dream-ledger.jsonl`` — DRM-2's committed, append-only audit record.

    Lives in the corpus dir (it rides the corpus's own git posture) but is NOT a memory
    file: ``_iter_memory_files`` yields only ``*.md``, so it is never indexed or recalled.
    DRM-1 only READS it (the aging firewall must hold from the first applied edge).
    """
    return os.path.join(memory_dir, "dream-ledger.jsonl")


def _new_pass_id() -> str:
    return "p" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


# --------------------------------------------------------------------------- #
# Apply-ledger reading (DRM-2 writes it; the DRM-1 firewall reads it from day one)
# --------------------------------------------------------------------------- #
def read_apply_ledger(memory_dir: str) -> List[dict]:
    """Parse ``dream-ledger.jsonl`` into per-edge CURRENT state (last line per edge_id wins).

    The ledger is append-only: an undo appends a superseding ``state: "undone"`` line rather
    than rewriting history. Returns one dict per edge_id, in first-seen order. Missing file
    or junk lines contribute nothing; never raises.
    """
    path = apply_ledger_path(memory_dir)
    order: List[str] = []
    latest: Dict[str, dict] = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if not isinstance(rec, dict):
                    continue
                eid = rec.get("edge_id")
                if not isinstance(eid, str) or not eid:
                    continue
                if eid not in latest:
                    order.append(eid)
                    latest[eid] = rec
                else:
                    # A superseding line may be sparse (undo writes edge_id/state/pass only);
                    # merge over the prior record so the current view keeps full provenance.
                    merged = dict(latest[eid])
                    merged.update(rec)
                    latest[eid] = merged
    except FileNotFoundError:
        return []
    except Exception:
        return []
    return [latest[eid] for eid in order]


def edge_aged_in(edge: dict, distinct_sessions_now: int, *, window: Optional[int] = None) -> bool:
    """The aging firewall's pure function (DRM-2.spec.md §5) — no stored state, ever.

    ``distinct_sessions_now − applied_at_distinct_count ≥ DREAM_AGE_SESSIONS``. An edge with
    a missing/junk ``applied_at_distinct_count`` NEVER ages in (fail toward the firewall).
    """
    w = age_sessions() if window is None else window
    applied_at = edge.get("applied_at_distinct_count")
    if not isinstance(applied_at, int) or isinstance(applied_at, bool) or applied_at < 0:
        return False
    return (distinct_sessions_now - applied_at) >= w


def unaged_dream_pairs(memory_dir: str, distinct_sessions_now: int) -> Set[frozenset]:
    """Unordered ``{source, target}`` pairs of ACTIVE, NOT-yet-aged dream edges.

    These are subtracted from the graph view candidate generation reads (inv-DRM-firewall):
    an applied edge influences recall immediately, but /dream's own source set must not see
    it until it ages in. An edge undone before aging is ``state: "undone"`` and therefore
    never in this set — nor in the graph (its stamped line was removed).
    """
    out: Set[frozenset] = set()
    for edge in read_apply_ledger(memory_dir):
        if edge.get("state") != "active":
            continue
        if edge_aged_in(edge, distinct_sessions_now):
            continue
        src, tgt = edge.get("source"), edge.get("target")
        if isinstance(src, str) and isinstance(tgt, str) and src and tgt:
            out.add(frozenset((src, tgt)))
    return out


# DRM-6: ledger kinds that are generated MEMORIES (whole staged files), not edges. They
# share the apply ledger (one audit record, one undo machinery, one doctor reconciler);
# ``read_apply_ledger``'s pair helpers skip them naturally (no source/target fields).
_GENERATED_KINDS = ("schema", "hypothesis")


def generated_rows(memory_dir: str) -> List[dict]:
    """Current-state ledger rows for the DRM-6 generative tier (schema/hypothesis)."""
    return [e for e in read_apply_ledger(memory_dir) if e.get("kind") in _GENERATED_KINDS]


def unaged_generated_stems(memory_dir: str, distinct_sessions_now: int) -> Set[str]:
    """ACTIVE dream-GENERATED memories not yet aged in — excluded from /dream's SOURCE set.

    The inv-DRM-firewall extension to generative output: a ``confidence: draft`` memory is
    already excluded by tier; this catches the GRADUATED-BUT-YOUNG one (evidence flipped it
    to verified, but it must still survive ``DREAM_AGE_SESSIONS`` un-undone before the pass
    may read it — same window, same ``edge_aged_in`` arithmetic as edges). An archived or
    undone draft has left the corpus and needs no entry here.
    """
    out: Set[str] = set()
    for row in generated_rows(memory_dir):
        if row.get("state") != "active":
            continue
        if edge_aged_in(row, distinct_sessions_now):
            continue
        stem = row.get("memory")
        if isinstance(stem, str) and stem:
            out.add(stem)
    return out


def boost_ledger_path(telemetry_dir: str, pass_id: str) -> str:
    return os.path.join(dream_dir(telemetry_dir), f"boosts-{pass_id}.jsonl")


def write_boost_ledger(telemetry_dir: str, pass_id: str, edges: List[dict]) -> Optional[str]:
    """Persist the pass's boosted-edge rows (with decision_chain provenance) to the derived
    dream dir. One row per (edge, outcome_memory). Written only when boosts exist — the
    candidate ledger is already the proof the pass ran (empty-norm hygiene). Never raises."""
    if not edges:
        return None
    try:
        os.makedirs(dream_dir(telemetry_dir), exist_ok=True)
        path = boost_ledger_path(telemetry_dir, pass_id)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(path, "w", encoding="utf-8") as fh:
            for row in edges:
                fh.write(
                    json.dumps({"pass": pass_id, **row, "generated_at": stamp}, ensure_ascii=False)
                    + "\n"
                )
        return path
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Candidate ledger (jsonl, derived dir) + the printed report
# --------------------------------------------------------------------------- #
def write_candidate_ledger(telemetry_dir: str, pass_id: str, candidates: List[dict]) -> Optional[str]:
    """Write the pass's candidate rows to the derived dream dir; returns the path or None.

    One JSON object per line: ``{pass, kind, source, target, distance, cofire, query,
    mutual, signal, generated_at}``. An OK pass with zero candidates still writes the (empty)
    file — the auditable record that the pass ran and found nothing. Never raises.
    """
    try:
        os.makedirs(dream_dir(telemetry_dir), exist_ok=True)
        path = candidate_ledger_path(telemetry_dir, pass_id)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(path, "w", encoding="utf-8") as fh:
            for c in candidates:
                row = {"pass": pass_id, **c, "generated_at": stamp}
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path
    except Exception:
        return None
