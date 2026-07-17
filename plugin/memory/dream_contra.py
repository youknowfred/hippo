"""DRM-C — contradiction discovery (decomposed out of ``dream.py``).

The opt-in LLM comprehension pass over discovery's high-cofire pairs: the derived
verdict ledger, the strict-JSON per-pair prompt, and ``discover_contradictions``
(propose-only, Tier-C by construction). Every name re-exports via the ``dream`` façade.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from .dream_config import (
    _CONTRA_SIDE_CHARS,
    contra_llm_timeout,
    contra_max_pairs,
    contra_min_cofire,
)
from .dream_ledgers import dream_dir
from .links import LinkGraph

# --------------------------------------------------------------------------- #
# DRM-C — contradiction discovery (LLM comprehension over the high-cofire pairs)
#
# The gap this fills is structural, not an oversight: every organic signal in this module
# is a similarity (cofire = the pair co-surfaced under each other's self-queries), and
# similarity can never distinguish "these two memories disagree" from "these two memories
# describe the same thing". The /hippo:resolve inbox — the standing human-verdict queue —
# only ever enumerated PRE-EXISTING ``contradicts:`` frontmatter a human already typed;
# nothing anywhere ever PROPOSED a new one. DRM-C proposes them:
#
#   - SOURCE SET: this pass's own ``result["pairs"]`` (no separate corpus scan), filtered
#     to cofire ≥ the θ-defaulted bar, minus pairs already declared (``contradicts`` — the
#     inbox has them — or ``supersedes`` — versions are EXPECTED to disagree; succession
#     already resolved it), minus pairs a prior DRM-C pass already judged (verdicts
#     persist in a derived ledger so a stable corpus never re-burns calls on the same
#     pair; edit a memory and the pair's key is unchanged — re-judging after edits is a
#     deliberate non-goal, precision-first).
#   - PER PAIR: one ``llm_client.complete`` call ("conflict in substance, or merely
#     related?") with a strict-JSON verdict. An unusable response (None / junk / missing
#     field) means the pair is simply NOT proposed this pass — no verdict row, no crash,
#     no partial write; it is retried on a future pass.
#   - SINK: a ``conflict: true`` verdict becomes a ``kind: "contradicts"`` candidate —
#     Tier-C by the pre-existing routing (``_ROUTED_KINDS``), NEVER apply-eligible — and a
#     row in ``contradictions.jsonl`` under the DERIVED dream dir (inv1: gitignored,
#     rebuildable-by-redreaming; the corpus is untouched). ``resolve_view`` reads that
#     ledger and feeds the pairs into the SAME inbox + verdict flow humans already use.
# --------------------------------------------------------------------------- #
def contradictions_ledger_path(telemetry_dir: str) -> str:
    """``<telemetry>/dream/contradictions.jsonl`` — DRM-C's derived verdict ledger."""
    return os.path.join(dream_dir(telemetry_dir), "contradictions.jsonl")


def _contra_key(a: str, b: str) -> Tuple[str, str]:
    """One order-free identity per pair — ``resolve_view._canonical_pair``'s convention."""
    return (a, b) if a <= b else (b, a)


def read_contradiction_verdicts(telemetry_dir: str) -> Dict[Tuple[str, str], dict]:
    """Latest DRM-C verdict per canonical pair (``read_apply_ledger``'s last-line-wins
    merge). Missing file/junk lines contribute nothing; never raises."""
    out: Dict[Tuple[str, str], dict] = {}
    try:
        with open(contradictions_ledger_path(telemetry_dir), "r", encoding="utf-8") as fh:
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
                a, b = rec.get("a"), rec.get("b")
                if not (isinstance(a, str) and a and isinstance(b, str) and b):
                    continue
                key = _contra_key(a, b)
                if key in out:
                    merged = dict(out[key])
                    merged.update(rec)
                    out[key] = merged
                else:
                    out[key] = rec
    except FileNotFoundError:
        return {}
    except Exception:
        return out
    return out


def _append_contradiction_rows(telemetry_dir: str, rows: List[dict]) -> None:
    """Append verdict rows to the derived ledger. No rows = no file touch. Never raises."""
    if not rows:
        return
    try:
        os.makedirs(dream_dir(telemetry_dir), exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(contradictions_ledger_path(telemetry_dir), "a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps({**row, "generated_at": stamp}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _contradiction_prompt(a: str, a_text: str, b: str, b_text: str) -> str:
    """The per-pair verdict prompt: both memories verbatim (bounded), strict-JSON answer."""
    return "\n".join(
        [
            "Two saved memory notes from the same project knowledge base follow. Decide",
            "whether they ACTUALLY CONFLICT IN SUBSTANCE — they make claims that cannot both",
            "be true or current (contradictory facts, incompatible instructions, one says X",
            "is the way and the other says X was abandoned) — or whether they merely relate,",
            "overlap, or complement each other on the same topic (NOT a conflict). Two",
            "notes about different points in time conflict only if both claim to describe",
            "the CURRENT state and disagree about it.",
            "Respond with ONLY a JSON object, no prose:",
            '{"conflict": true|false, "reason": "<one line, <=200 chars>"}',
            "",
            f"MEMORY A ({a}):",
            a_text[:_CONTRA_SIDE_CHARS],
            "",
            f"MEMORY B ({b}):",
            b_text[:_CONTRA_SIDE_CHARS],
        ]
    )


def discover_contradictions(
    pairs: List[dict],
    texts: Dict[str, str],
    memory_dir: str,
    telemetry_dir: str,
    *,
    pass_id: str,
    graph: Optional[LinkGraph] = None,
    min_cofire: Optional[float] = None,
    max_pairs: Optional[int] = None,
    timeout_s: Optional[float] = None,
) -> dict:
    """Judge the high-cofire ``pairs`` for substantive conflict. PROPOSE-ONLY; never raises.

    Returns ``{"candidates": [...], "stats": {...}}`` where each candidate is a Tier-C
    ``kind: "contradicts"`` row (the ledger-row shape all candidates share, plus the
    model's one-line ``reason``). Writes ONLY the derived verdict ledger — never a memory
    file, never the corpus. See the section banner for the full contract.
    """
    from . import llm_client
    from .secrets import scan_text

    bar = contra_min_cofire() if min_cofire is None else min_cofire
    cap = contra_max_pairs() if max_pairs is None else max_pairs
    tmo = contra_llm_timeout() if timeout_s is None else timeout_s
    stats = {
        "pool_bar": bar,
        "cap": cap,
        "attempts": 0,
        "judged": 0,
        "proposed": 0,
        "llm_failures": 0,
        "skipped_prior_verdict": 0,
        "skipped_declared": 0,
        "model": llm_client.model_name(),
    }
    candidates: List[dict] = []
    try:
        if cap <= 0:
            return {"candidates": candidates, "stats": stats}
        prior = read_contradiction_verdicts(telemetry_dir)
        declared: Set[Tuple[str, str]] = set()
        if graph is not None:
            for rel in ("contradicts", "supersedes"):
                for src, tgt in graph.all_typed_edges(rel):
                    declared.add(_contra_key(src, tgt))
        rows: List[dict] = []
        for p in pairs:  # discover() serialized these strength-desc
            if stats["attempts"] >= cap:
                break
            a, b = p.get("a"), p.get("b")
            cof = float(p.get("cofire") or 0.0)
            if cof < bar:
                break  # sorted desc — everything past here is below the bar
            if not a or not b or a not in texts or b not in texts:
                continue
            key = _contra_key(a, b)
            if key in declared:
                stats["skipped_declared"] += 1
                continue
            if key in prior:
                stats["skipped_prior_verdict"] += 1
                continue
            stats["attempts"] += 1  # counts the CALL, so a dead endpoint stays bounded
            raw = llm_client.complete(
                _contradiction_prompt(a, texts[a], b, texts[b]),
                timeout_s=tmo,
                max_tokens=256,
            )
            verdict = llm_client.extract_json(raw) if raw else None
            if not isinstance(verdict, dict) or not isinstance(verdict.get("conflict"), bool):
                stats["llm_failures"] += 1
                continue  # fail open: not proposed, not recorded — retried a future pass
            stats["judged"] += 1
            conflict = verdict["conflict"]
            reason = " ".join(str(verdict.get("reason") or "").split())[:240]
            if reason and scan_text(reason):
                # Generated-text discipline (the dream-path deviation): no secret byte
                # persists, even into a derived file the resolve inbox will render.
                reason = "(reason withheld: secret lint)"
            rows.append(
                {
                    "pass": pass_id,
                    "a": key[0],
                    "b": key[1],
                    "cofire": cof,
                    "mutual": bool(p.get("mutual")),
                    "conflict": conflict,
                    "reason": reason,
                    "model": llm_client.model_name(),
                    "state": "proposed" if conflict else "no-conflict",
                }
            )
            if conflict:
                stats["proposed"] += 1
                candidates.append(
                    {
                        "kind": "contradicts",
                        "source": a,
                        "target": b,
                        "distance": p.get("distance"),
                        "cofire": cof,
                        "query": p.get("query") or "",
                        "mutual": bool(p.get("mutual")),
                        "signal": "llm-contradiction-verdict",
                        "reason": reason,
                    }
                )
        _append_contradiction_rows(telemetry_dir, rows)
    except Exception:
        return {"candidates": candidates, "stats": stats}
    return {"candidates": candidates, "stats": stats}
