"""IOP-3 — the curated export receipt: WHY each floor line earned its AGENTS.md export.

The landscape warns that LLM-generated AGENTS.md files HURT agent success; hippo's
counter-story is a CURATED, evidence-bearing export — but until now the evidence behind
each exported floor line (did anyone actually recall it? is it stale? does the corpus
disagree with it?) was invisible at export time. This module is that evidence, as a
report-only receipt rendered ALONGSIDE ``export_agents``'s diff.

Composition, not computation: every evidence value comes from a shipped function called
verbatim — ``export_agents`` (the selection, reused as-is), ``soak.soak_status`` +
``soak.compute_strength_scores`` (recall strength under the soak-maturity gate),
``staleness.read_stale_cache`` (LIF-6's persisted stale.json), ``rules_plane.rules_rot``
(prior-block rot on the existing AGENTS.md) and ``rules_plane.conflict_radar`` (authority
gaps + typed-edge conflicts). Nothing here re-implements evidence math, and nothing here
feeds BACK into selection: ``export_agents`` itself never imports this module and never
reads a strength/staleness value (AST-pinned) — the proposed AGENTS.md bytes are
untouched by the receipt's existence. Evidence is display-only, never a ranking input.

inv3 — a thin corpus must read "insufficient evidence", never false-confidence clean:
below the soak gate every strength is withheld (on a fresh clone every memory scores
0.0, which is indistinguishable from "measured and unused"), and an absent stale.json
renders "staleness unknown", never "fresh".

Standing-surface posture: this fires ONLY on the explicit ``/hippo:export-agents``
invocation — no doctor check, no SessionStart producer, no hot-path read — so
``export_agents``'s "zero new reporting surface" drift-check design intent still holds:
the STANDING drift story remains all-reuse (rules_rot/archive/conflict_radar); the
receipt is a point-in-time explanation at the one moment a human is deciding.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

_AGENTS_BASENAME = "AGENTS.md"


def _graduation(text: str) -> Dict[str, Optional[str]]:
    """The memory's graduation state — type, confidence tier, last_verified stamp —
    straight from its own frontmatter (display-only)."""
    out: Dict[str, Optional[str]] = {"type": None, "confidence": None, "last_verified": None}
    try:
        from .provenance import parse_frontmatter
        from .staleness import read_last_verified

        fm = parse_frontmatter(text)
        meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
        mtype = (meta or {}).get("type") or fm.get("type")
        if isinstance(mtype, str):
            out["type"] = mtype
        conf = (meta or {}).get("confidence")
        if isinstance(conf, str):
            out["confidence"] = conf
        out["last_verified"] = read_last_verified(text)
    except Exception:
        pass
    return out


def curation_receipt(
    *,
    memory_dir: str,
    repo_root: str,
    telemetry_dir: Optional[str] = None,
    index_dir: Optional[str] = None,
) -> dict:
    """The evidence ledger for one would-be export — read-only, zero AGENTS.md bytes.

    Runs the SAME selection as ``export_agents`` (by calling it) and annotates every
    selected item + every exclusion with the shipped evidence. Returns::

        {
          "reason": None | str,          # export refusal passthrough (nothing to receipt)
          "gate_met": bool, "distinct_sessions": int, "gate_threshold": int,
          "staleness_cache": bool,       # stale.json present? False -> staleness UNKNOWN
          "agents_exists": bool,
          "items": [{"name", "globs", "flags",
                     "type", "confidence", "last_verified",
                     "strength": float | None,   # None below the soak gate (withheld)
                     "stale": None | {"changed", "sha"},
                     "conflicts": [ ... conflict_radar rows for this name ... ]}],
          "skipped": [{"name", "reason"}],   # what was excluded and why, verbatim
          "prior_rot": {"code_ref_rot": [...], "dead_path_globs": [...]},  # AGENTS.md only
        }

    Never raises; on any failure returns a refusal-shaped receipt.
    """
    empty = {
        "reason": "receipt unavailable (unexpected failure)",
        "gate_met": False,
        "distinct_sessions": 0,
        "gate_threshold": 0,
        "staleness_cache": False,
        "agents_exists": False,
        "items": [],
        "skipped": [],
        "prior_rot": {"code_ref_rot": [], "dead_path_globs": []},
    }
    try:
        from . import soak
        from .build_index import default_index_dir
        from .export_agents import export_agents
        from .rules_plane import conflict_radar, rules_rot
        from .staleness import read_stale_cache
        from .telemetry import default_telemetry_dir

        export = export_agents(memory_dir=memory_dir, repo_root=repo_root)
        skipped = list(export.get("skipped") or [])
        if export.get("proposed") is None:
            return {**empty, "reason": export.get("reason"), "skipped": skipped}

        td = telemetry_dir or default_telemetry_dir(memory_dir)
        status = soak.soak_status(td, memory_dir=memory_dir)
        gate_met = bool(status.get("gate_met"))
        strength = soak.compute_strength_scores(td) if gate_met else {}

        idx = index_dir or default_index_dir(memory_dir)
        stale_cache = read_stale_cache(idx)  # None: never scanned / corrupt -> UNKNOWN

        radar = conflict_radar(memory_dir, repo_root, telemetry_dir=td)
        conflicts_by_name: Dict[str, List[dict]] = {}
        for g in radar.get("authority_gaps") or []:
            conflicts_by_name.setdefault(g["name"], []).append(
                {"kind": "authority_gap", **g}
            )
        for c in radar.get("edge_conflicts") or []:
            conflicts_by_name.setdefault(c["name"], []).append(
                {"kind": "edge_conflict", **c}
            )

        agents_exists = os.path.isfile(os.path.join(repo_root, _AGENTS_BASENAME))
        rot = rules_rot(repo_root) if agents_exists else {}
        prior_rot = {
            "code_ref_rot": [
                f for f in rot.get("code_ref_rot") or [] if f.get("file") == _AGENTS_BASENAME
            ],
            "dead_path_globs": [
                f for f in rot.get("dead_path_globs") or [] if f.get("file") == _AGENTS_BASENAME
            ],
        }

        items: List[dict] = []
        for it in export.get("items") or []:
            name = it["name"]
            try:
                with open(
                    os.path.join(memory_dir, f"{name}.md"), "r", encoding="utf-8"
                ) as fh:
                    grad = _graduation(fh.read())
            except Exception:
                grad = {"type": None, "confidence": None, "last_verified": None}
            items.append(
                {
                    "name": name,
                    "globs": it.get("globs") or [],
                    "flags": it.get("flags") or [],
                    **grad,
                    "strength": strength.get(name) if gate_met else None,
                    "stale": (stale_cache or {}).get(name),
                    "conflicts": conflicts_by_name.get(name, []),
                }
            )
        return {
            "reason": None,
            "gate_met": gate_met,
            "distinct_sessions": int(status.get("distinct_sessions") or 0),
            "gate_threshold": int(status.get("gate_threshold") or 0),
            "staleness_cache": stale_cache is not None,
            "agents_exists": agents_exists,
            "items": items,
            "skipped": skipped,
            "prior_rot": prior_rot,
        }
    except Exception:
        return empty


def _strength_line(receipt: dict, item: dict) -> str:
    if not receipt["gate_met"]:
        return (
            f"strength: insufficient evidence "
            f"({receipt['distinct_sessions']}/{receipt['gate_threshold']} distinct sessions "
            "— below the soak gate, numbers withheld)"
        )
    s = item["strength"]
    return "strength: 0.0 (never recalled in any logged session)" if s is None else f"strength: {s}"


def _staleness_line(receipt: dict, item: dict) -> str:
    if not receipt["staleness_cache"]:
        return "staleness: unknown (no stale.json — run a SessionStart or doctor first)"
    st = item["stale"]
    if not st:
        return "staleness: fresh at last scan"
    return (
        f"staleness: STALE — {st.get('changed', '?')} cited file(s) changed since "
        f"{st.get('sha') or '?'} (verify before exporting)"
    )


def describe_receipt(receipt: dict) -> str:
    """Human-readable receipt: the gate state up front (inv3), one evidence block per
    floor line, every exclusion named with its reason, prior-block rot last. Renders
    ZERO bytes of the proposed AGENTS.md — the diff belongs to ``describe``."""
    if receipt.get("reason"):
        return f"✘ no curation receipt: {receipt['reason']}"
    lines: List[str] = []
    gate = (
        f"soak gate MET ({receipt['distinct_sessions']} distinct sessions)"
        if receipt["gate_met"]
        else (
            f"soak gate NOT met ({receipt['distinct_sessions']}/"
            f"{receipt['gate_threshold']} distinct sessions)"
        )
    )
    lines.append(
        f"Curation receipt — {len(receipt['items'])} floor line(s), "
        f"{len(receipt['skipped'])} excluded; {gate}. Evidence is display-only: "
        "it never selects, filters, or ranks what exports."
    )
    for it in receipt["items"]:
        grad = ", ".join(
            str(v) for v in (it["type"], it["confidence"] and f"confidence {it['confidence']}",
                             it["last_verified"] and f"last verified {it['last_verified']}")
            if v
        ) or "no graduation stamps"
        lines.append(f"  • {it['name']} — {grad}")
        lines.append(f"      {_strength_line(receipt, it)}")
        lines.append(f"      {_staleness_line(receipt, it)}")
        for c in it["conflicts"]:
            if c["kind"] == "authority_gap":
                lines.append(
                    f"      ⚑ authority gap: cited by {', '.join(c.get('cited_by') or [])} "
                    f"but recall strength {c.get('strength')} — exported content nobody retrieves"
                )
            else:
                lines.append(
                    f"      ⚑ conflict: {c.get('by')} {c.get('relation')} this memory "
                    f"(cited by {', '.join(c.get('cited_by') or [])})"
                )
        if not it["conflicts"]:
            lines.append("      conflicts: none")
    for s in receipt["skipped"]:
        lines.append(f"  ◦ excluded: {s['name']} — {s['reason']}")
    if not receipt["agents_exists"]:
        lines.append("Prior AGENTS.md: none — no prior-block rot to check.")
    else:
        rot_bits = []
        if receipt["prior_rot"]["dead_path_globs"]:
            rot_bits.append(
                f"{len(receipt['prior_rot']['dead_path_globs'])} dead paths: glob(s)"
            )
        if receipt["prior_rot"]["code_ref_rot"]:
            rot_bits.append(
                f"{len(receipt['prior_rot']['code_ref_rot'])} rotten code ref(s)"
            )
        lines.append(
            "Prior AGENTS.md rot: " + ("; ".join(rot_bits) + " — a re-export refreshes the managed block"
                                       if rot_bits else "none")
        )
    return "\n".join(lines)
