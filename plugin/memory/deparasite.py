"""DRM-4 — the de-parasiting counterweight (``ROADMAP.dream.yaml``).

An edge-only generative pass has no restoring force: repeated /dream passes can
over-connect a hub (one popular memory injected for everything → crosstalk, precision and
abstention regress), and a growing corpus accretes weak or redundant structure. Sleep's
FORGETTING function — SHY synaptic downscaling (Tononi & Cirelli), Crick–Mitchison
"reverse learning" — is the counterweight the backbone alone lacks. This module is that
counterweight, in hippo's reversibility-gradient form:

  - **out-degree report** — every memory's out/in/total degree (``LinkGraph.degrees``),
    with nodes over ``DREAM_MAX_OUT_DEGREE`` flagged as hubs;
  - **retract lane (Tier A)** — /dream's OWN un-aged edges touching a flagged hub are
    proposed for retraction, and MAY be auto-retracted (``--retract``): they are ranking
    hints /dream itself added minutes-to-days ago, still inside their undo window, and the
    retraction runs through the ONE byte-exact, drift-refusing undo path
    (``dream.undo_edges``). Retracting them restores the pre-dream baseline — it never
    depresses anything a human authored;
  - **demote lane (GATED, never auto)** — everything else: aged-in dream edges (implicit
    ratification makes them trusted — undoing one is now an owner action, the per-item
    ``dream --undo <edge-id>``), and hand-authored out-links with no co-recall evidence
    (named for review only; hippo never edits body prose autonomously). PROTECTED hubs —
    floor-linked, co-recalled, or cited (see ``protected_map``) — are NEVER proposed for
    depression: their demote lane is structurally empty;
  - **dedup-merge lane (GATED, never auto)** — near-duplicate memory pairs (token-Jaccard
    over the same name+description ``doc_text`` the index embeds, ≥ ``DREAM_DEDUP_JACCARD``)
    get a NON-LOSSY merge proposal: the survivor declares ``supersedes: [loser]``
    (``links.add_typed_relation``) and the loser's validity window closes
    (``staleness.set_invalid_after``) — the exact demote→invalid_after chain
    reconsolidation's LIF-1 verdict uses. No body is ever deleted; both files stay on
    disk; both writes are additive frontmatter and fully reversible. A pair already
    carrying a ``contradicts`` edge is a DISAGREEMENT, not a duplicate — it routes to
    /hippo:resolve and is never auto-resolved (or auto-merged) here.

The pass itself is REPORT/PROPOSE: it writes nothing to any memory file. The only
execution paths are (a) ``--retract`` — Tier-A only, un-aged dream edges, via the undo
machinery — and (b) ``apply_dedup_merge`` — per-item, agent-gated, one pair per call, no
batch parameter (the same no-bulk discipline as ``add_typed_relation``). inv4 holds:
removals touching human memories stay per-item gated; only /dream's own un-aged edges may
auto-retract. Floor memories and ``confidence: draft`` memories are excluded from every
lane on both sides (the round-wide endpoint rule). Gated on the soak bar like every dream
surface: a young corpus proposes nothing, and says so (inv3).

Protection semantics (``protected_map``) — a hub is protected when ANY of:
  - **floor** — pinned in the MEMORY.md floor (``lint_floor.floor_memory_names``): the
    human declared it always-load;
  - **co-recalled** — it appears in the Hebbian co-recall tally
    (``telemetry.co_recall_pairs``, ≥3 distinct sessions): live behavioral evidence it
    surfaces usefully WITH other memories;
  - **cited** — other memories point AT it (the ``orphans()`` docstring's "well-cited"
    sense): inbound untyped wikilinks, or inbound ``refines`` (children build on it).
    Inbound ``supersedes``/``contradicts`` deliberately do NOT protect — being replaced
    or disputed is the opposite of load-bearing. Dream-applied edges never confer
    protection regardless of age (pairs in the ACTIVE apply ledger are subtracted before
    counting): the counterweight must not be disarmable by the very pass it counterweighs.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from .dream import (
    _new_pass_id,
    age_sessions,
    dream_dir,
    edge_aged_in,
    read_apply_ledger,
    undo_edges,
)
from .lint_floor import floor_memory_names
from .links import LinkGraph, build_graph
from .provenance import _iter_memory_files, parse_frontmatter
from .soak import soak_status
from .telemetry import co_recall_pairs, default_telemetry_dir

# --------------------------------------------------------------------------- #
# Tunables (env-overridable; defaults calibrated on the live corpus 2026-07-12 —
# see the DRM-4 status note in ROADMAP.dream.yaml)
# --------------------------------------------------------------------------- #

# Out-degree cap: distinct outbound neighbors (wikilink + typed) above which a memory is
# flagged as a hub. The live corpus's dense hand-linked capstones sit at out≈6–8; the
# default flags only genuine outliers above that band (empty-norm: a healthy corpus
# flags nothing).
_DEFAULT_MAX_OUT_DEGREE = 8

# Near-duplicate bar: token-set Jaccard over doc_text (name + description). High by
# design — this lane exists for true accretion (re-captured notes, drifted twins), and a
# false merge proposal costs owner attention (the scarce resource per inv-DRM-empty-norm).
_DEFAULT_DEDUP_JACCARD = 0.8

# A doc_text token set smaller than this carries too little signal for a Jaccard claim
# (two terse three-word descriptions overlap trivially); such memories are skipped.
_MIN_DEDUP_TOKENS = 4

# Per-hub bound on named human-link review candidates (the demote lane is a hint list for
# a human turn, not a graph dump — same budget discipline as session_start producers).
_MAX_HUMAN_LINK_PROPOSALS = 10

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _env_int(name: str, default: int) -> int:
    try:
        raw = os.environ.get(name, "").strip()
        return int(raw) if raw else default
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.environ.get(name, "").strip()
        return float(raw) if raw else default
    except (TypeError, ValueError):
        return default


def max_out_degree() -> int:
    """``DREAM_MAX_OUT_DEGREE`` (≥1) — the hub-flagging bar (default 8)."""
    return max(1, _env_int("DREAM_MAX_OUT_DEGREE", _DEFAULT_MAX_OUT_DEGREE))


def dedup_jaccard() -> float:
    """``DREAM_DEDUP_JACCARD`` (clamped to [0.5, 1.0]) — the near-duplicate bar.

    The floor exists because a sub-0.5 "duplicate" claim is topic overlap, not duplication
    — below it this lane would start proposing merges of genuinely distinct memories.
    """
    return min(1.0, max(0.5, _env_float("DREAM_DEDUP_JACCARD", _DEFAULT_DEDUP_JACCARD)))


# --------------------------------------------------------------------------- #
# Corpus + protection views
# --------------------------------------------------------------------------- #
def _corpus_texts(memory_dir: str) -> Dict[str, str]:
    """``{stem: full text}``; unreadable files skipped. Never raises."""
    texts: Dict[str, str] = {}
    try:
        for path in _iter_memory_files(memory_dir):
            stem = os.path.splitext(os.path.basename(path))[0]
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    texts[stem] = fh.read()
            except Exception:
                continue
    except Exception:
        return texts
    return texts


def _draft_stems(texts: Dict[str, str]) -> Set[str]:
    from .build_index import _extract_confidence

    out: Set[str] = set()
    for stem, text in texts.items():
        try:
            if _extract_confidence(parse_frontmatter(text)) == "draft":
                out.add(stem)
        except Exception:
            continue
    return out


def _doc_tokens(stem: str, text: str) -> Set[str]:
    """Token set of the memory's doc_text (name + description — the index's own semantic
    surface), lowercase alnum, len ≥ 3. The dedup similarity operates on exactly what
    recall ranks, so "near-duplicate" here means near-duplicate WHERE IT MATTERS."""
    from .build_index import memory_doc_text

    try:
        doc = memory_doc_text(stem, text)
    except Exception:
        doc = stem
    return {t for t in _TOKEN_RE.findall((doc or "").lower()) if len(t) >= 3}


def _active_dream_pairs(memory_dir: str) -> Set[frozenset]:
    """Unordered pairs of ALL currently-active dream edges (aged or not).

    Used to subtract dream-created inbound from the "cited" protection count: a dream
    edge is a ranking hint, not a human assertion, so it must never shield a hub from
    the counterweight (the pass would otherwise be able to protect its own accretion).
    """
    out: Set[frozenset] = set()
    for e in read_apply_ledger(memory_dir):
        if e.get("state") != "active":
            continue
        src, tgt = e.get("source"), e.get("target")
        if isinstance(src, str) and isinstance(tgt, str) and src and tgt:
            out.add(frozenset((src, tgt)))
    return out


def protected_map(
    memory_dir: str,
    graph: LinkGraph,
    telemetry_dir: Optional[str] = None,
    *,
    floor: Optional[Set[str]] = None,
    dream_pairs: Optional[Set[frozenset]] = None,
) -> Dict[str, List[str]]:
    """``{stem: sorted [protection reasons]}`` for every protected stem. Never raises.

    Reasons: ``floor`` / ``co-recalled`` / ``cited`` (see the module docstring for the
    exact semantics of each). A stem absent from the map is unprotected.
    """
    out: Dict[str, Set[str]] = {}
    try:
        fl = floor_memory_names(memory_dir) if floor is None else floor
        for stem in fl:
            out.setdefault(stem, set()).add("floor")

        td = telemetry_dir or default_telemetry_dir(memory_dir)
        for rec in co_recall_pairs(td, exclude_names=fl):
            for stem in rec.get("pair") or []:
                out.setdefault(stem, set()).add("co-recalled")

        pairs = _active_dream_pairs(memory_dir) if dream_pairs is None else dream_pairs
        for stem in graph.files:
            citers = set(graph.inbound(stem)) | set(graph.typed_inbound(stem, "refines"))
            human_citers = {c for c in citers if frozenset((c, stem)) not in pairs}
            if human_citers:
                out.setdefault(stem, set()).add("cited")
    except Exception:
        pass
    return {s: sorted(v) for s, v in out.items()}


# --------------------------------------------------------------------------- #
# The report/propose pass (zero memory writes)
# --------------------------------------------------------------------------- #
def report_path(telemetry_dir: str, pass_id: str) -> str:
    return os.path.join(dream_dir(telemetry_dir), f"deparasite-{pass_id}.json")


def deparasite_report(
    memory_dir: str,
    index_dir: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
) -> dict:
    """Run the DRM-4 analysis. READ-ONLY over every memory file; never raises.

    Returns ``{"status", "reason", "pass_id", "max_out_degree", "dedup_jaccard",
    "degrees", "flagged", "dedup", "stats"}`` — status ∈ ``ok | below-soak |
    empty-corpus``. ``flagged`` rows split every remedy into the ``retract`` lane
    (Tier A: /dream's own un-aged edges, auto-retractable) vs the ``demote_gated`` lane
    (aged dream edges + human out-links: per-item, NEVER auto). Protected hubs carry
    ``protected: True`` and an EMPTY demote lane, and never appear as a dedup loser.
    """
    td = telemetry_dir or default_telemetry_dir(memory_dir)
    pass_id = _new_pass_id()
    result = {
        "status": "ok",
        "reason": "",
        "pass_id": pass_id,
        "max_out_degree": max_out_degree(),
        "dedup_jaccard": dedup_jaccard(),
        "degrees": [],
        "flagged": [],
        "dedup": [],
        "stats": {},
    }

    soak = soak_status(td, memory_dir=memory_dir)
    if not soak.get("gate_met"):
        result["status"] = "below-soak"
        result["reason"] = (
            f"corpus is below the curation-soak bar ({soak.get('distinct_sessions', 0)}/"
            f"{soak.get('gate_threshold', 5)} distinct sessions) — co-recall protection "
            "has no evidence to read yet, so no removal/demotion is proposed"
        )
        return result

    texts = _corpus_texts(memory_dir)
    if len(texts) < 2:
        result["status"] = "empty-corpus"
        result["reason"] = (
            f"corpus has {len(texts)} memory file(s) — nothing to de-parasite"
        )
        return result

    graph = build_graph(memory_dir, index_dir)
    if graph is None:
        graph = LinkGraph(memory_dir, texts=texts)

    floor = floor_memory_names(memory_dir)
    drafts = _draft_stems(texts)
    dream_pairs = _active_dream_pairs(memory_dir)
    protected = protected_map(
        memory_dir, graph, td, floor=floor, dream_pairs=dream_pairs
    )
    distinct_now = int(soak.get("distinct_sessions") or 0)
    window = age_sessions()

    # Co-recall evidence per pair — the demote lane's "which human links look weak" signal.
    corecalled_pairs: Set[frozenset] = set()
    for rec in co_recall_pairs(td, exclude_names=floor):
        pair = rec.get("pair") or []
        if len(pair) == 2:
            corecalled_pairs.add(frozenset(pair))

    # Active dream edges by pair, split by age (the reversibility gradient's two lanes).
    ledger_active = [e for e in read_apply_ledger(memory_dir) if e.get("state") == "active"]
    unaged_by_stem: Dict[str, List[dict]] = {}
    aged_by_stem: Dict[str, List[dict]] = {}
    for e in ledger_active:
        src, tgt = e.get("source"), e.get("target")
        if not (isinstance(src, str) and isinstance(tgt, str)):
            continue
        bucket = aged_by_stem if edge_aged_in(e, distinct_now) else unaged_by_stem
        for stem in (src, tgt):
            bucket.setdefault(stem, []).append(e)

    # ---- out-degree report + hub flagging ------------------------------------------- #
    cap = result["max_out_degree"]
    degrees = graph.degrees()  # (stem, out, in, total), total-desc — re-sort by out
    degree_rows = sorted(degrees, key=lambda r: (-r[1], r[0]))
    result["degrees"] = [list(r) for r in degree_rows]

    flagged: List[dict] = []
    for stem, out_d, _in_d, _total in degree_rows:
        if out_d <= cap:
            continue
        prot = protected.get(stem)
        row: dict = {
            "stem": stem,
            "out_degree": out_d,
            "protected": bool(prot),
            "protected_by": prot or [],
            "retract": [],
            "demote_gated": [],
        }
        seen_edges: Set[str] = set()
        for e in unaged_by_stem.get(stem, []):
            if e["edge_id"] in seen_edges:
                continue
            seen_edges.add(e["edge_id"])
            applied_at = e.get("applied_at_distinct_count")
            left = (
                max(0, window - (distinct_now - applied_at))
                if isinstance(applied_at, int) and not isinstance(applied_at, bool)
                else window
            )
            row["retract"].append(
                {
                    "edge_id": e["edge_id"],
                    "source": e.get("source"),
                    "target": e.get("target"),
                    "kind": e.get("kind"),
                    "cofire": e.get("cofire"),
                    "sessions_to_age": left,
                }
            )
        if not prot:
            # The GATED lane exists only for unprotected hubs: aged-in dream edges (now
            # trusted — undoing one is an owner action) and human out-links with no
            # co-recall evidence. A protected hub's demote lane is STRUCTURALLY empty.
            for e in aged_by_stem.get(stem, []):
                if e["edge_id"] in seen_edges:
                    continue
                seen_edges.add(e["edge_id"])
                row["demote_gated"].append(
                    {
                        "class": "aged-dream-edge",
                        "edge_id": e["edge_id"],
                        "source": e.get("source"),
                        "target": e.get("target"),
                        "cmd": f"python -m memory.dream --undo {e['edge_id']}",
                    }
                )
            dream_partner = {
                (set(p) - {stem}).pop() for p in dream_pairs if stem in p and len(p) == 2
            }
            human_targets = [
                t
                for t in sorted(graph.outbound(stem))
                if t not in dream_partner and frozenset((stem, t)) not in corecalled_pairs
            ]
            for t in human_targets[:_MAX_HUMAN_LINK_PROPOSALS]:
                row["demote_gated"].append(
                    {
                        "class": "human-out-link",
                        "target": t,
                        "evidence": "no co-recall evidence for this pair — review the "
                        "link by hand (hippo never edits body prose autonomously)",
                    }
                )
        flagged.append(row)
    result["flagged"] = flagged

    # ---- dedup-merge proposals (non-lossy, per-item gated) --------------------------- #
    bar = result["dedup_jaccard"]
    eligible = sorted(s for s in texts if s not in floor and s not in drafts)
    tokens = {s: _doc_tokens(s, texts[s]) for s in eligible}
    dedup: List[dict] = []
    skipped_protected = 0
    for i, a in enumerate(eligible):
        ta = tokens[a]
        if len(ta) < _MIN_DEDUP_TOKENS:
            continue
        for b in eligible[i + 1 :]:
            tb = tokens[b]
            if len(tb) < _MIN_DEDUP_TOKENS:
                continue
            union = ta | tb
            sim = len(ta & tb) / len(union) if union else 0.0
            if sim < bar:
                continue
            contradicting = (
                b in graph.typed_outbound(a, "contradicts")
                or a in graph.typed_outbound(b, "contradicts")
            )
            if contradicting:
                dedup.append(
                    {
                        "a": a,
                        "b": b,
                        "similarity": round(sim, 3),
                        "route": "resolve",
                        "note": "pair already carries a contradicts edge — an open "
                        "DISAGREEMENT, not a duplicate; route to /hippo:resolve "
                        "(never auto-resolved)",
                    }
                )
                continue
            # Survivor preference: protected side first (a protected memory can never be
            # the loser), then the richer file, then name — deterministic and hermetic.
            a_prot, b_prot = a in protected, b in protected
            if a_prot and b_prot:
                skipped_protected += 1
                continue
            if a_prot or b_prot:
                survivor, loser = (a, b) if a_prot else (b, a)
            else:
                survivor, loser = sorted(
                    (a, b), key=lambda s: (-len(texts[s]), s)
                )
            dedup.append(
                {
                    "a": a,
                    "b": b,
                    "similarity": round(sim, 3),
                    "route": "merge-proposal",
                    "survivor": survivor,
                    "loser": loser,
                    "proposal": {
                        "chain": "survivor declares supersedes(loser) via "
                        "links.add_typed_relation; loser's validity window closes via "
                        "staleness.set_invalid_after — additive frontmatter only, no "
                        "body byte is touched, both files stay on disk, fully "
                        "reversible (the reconsolidate demote→invalid_after chain)",
                        "command": f"python -m memory.dream --dedup-merge {survivor} {loser}",
                    },
                }
            )
    result["dedup"] = dedup

    result["stats"] = {
        "corpus_files": len(texts),
        "over_cap": len(flagged),
        "protected_over_cap": sum(1 for r in flagged if r["protected"]),
        "retractable_edges": len({e["edge_id"] for r in flagged for e in r["retract"]}),
        "gated_demotions": sum(len(r["demote_gated"]) for r in flagged),
        "dedup_merge_proposals": sum(1 for d in dedup if d["route"] == "merge-proposal"),
        "dedup_routed_resolve": sum(1 for d in dedup if d["route"] == "resolve"),
        "dedup_skipped_both_protected": skipped_protected,
        "floor_excluded": sorted(floor),
        "draft_excluded": sorted(drafts),
        "protected_total": len(protected),
    }
    return result


def write_report(telemetry_dir: str, report: dict) -> Optional[str]:
    """Persist the pass report under the derived dream dir (inv1). Never raises."""
    try:
        os.makedirs(dream_dir(telemetry_dir), exist_ok=True)
        path = report_path(telemetry_dir, report.get("pass_id", "unknown"))
        payload = {**report, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        return path
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Tier-A retraction (the ONLY auto-executable lane) + the per-item merge executor
# --------------------------------------------------------------------------- #
def retract_flagged_unaged(
    memory_dir: str, index_dir: Optional[str], report: dict
) -> Tuple[int, str]:
    """Retract every un-aged dream edge the report's retract lane names (Tier A).

    Routes through ``dream.undo_edges`` — the one undo path: byte-exact reversal,
    refuse-on-drift per edge, one appended superseding ledger line per edge (annotated
    ``retracted_by: deparasite`` + the flagging reason), one index rebuild. Aged-in dream
    edges and human structures are structurally unreachable from here (the report never
    puts them in this lane). The no-reapply guard in ``dream.run_apply_pass`` keeps a
    retracted pair from ping-ponging back in on the next pass.
    """
    ids: List[str] = []
    reasons: List[str] = []
    for row in report.get("flagged", []):
        for e in row.get("retract", []):
            if e.get("edge_id") and e["edge_id"] not in ids:
                ids.append(e["edge_id"])
                reasons.append(f"{row['stem']} out-degree {row['out_degree']}")
    if not ids:
        return 0, "🧹 de-parasite --retract: no un-aged dream edges on any flagged hub — nothing to retract."
    return undo_edges(
        memory_dir,
        index_dir,
        edge_ids=ids,
        annotate={
            "retracted_by": "deparasite",
            "retract_reason": "over DREAM_MAX_OUT_DEGREE: " + "; ".join(sorted(set(reasons))),
        },
    )


def apply_dedup_merge(
    memory_dir: str,
    survivor: str,
    loser: str,
    *,
    telemetry_dir: Optional[str] = None,
    index_dir: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Execute ONE ratified dedup-merge — per-item, agent-gated, deliberately batchless.

    The non-lossy chain and nothing else: ``add_typed_relation(survivor, "supersedes",
    loser)`` + ``set_invalid_after(loser)``. Both writes are additive frontmatter (bodies
    byte-identical, asserted by test); nothing is deleted or archived; recall's own
    supersedes/invalid_after handling does the demotion. Refuses when either name does
    not resolve to a file, when survivor == loser, when the pair carries a ``contradicts``
    edge (that is /hippo:resolve's jurisdiction), or when the LOSER is protected
    (floor / co-recalled / cited — a protected memory is never depressed, executor
    included). ``dry_run`` previews without writing. Never raises.
    """
    result = {
        "survivor": survivor,
        "loser": loser,
        "changed": False,
        "error": None,
        "supersedes": None,
        "invalid_after": None,
    }
    try:
        s_path = os.path.join(memory_dir, f"{survivor}.md")
        l_path = os.path.join(memory_dir, f"{loser}.md")
        if survivor == loser:
            result["error"] = "survivor and loser are the same memory"
            return result
        for p, label in ((s_path, survivor), (l_path, loser)):
            if not os.path.isfile(p):
                result["error"] = f"no memory file for {label!r}"
                return result

        graph = build_graph(memory_dir, index_dir)
        if graph is not None and (
            loser in graph.typed_outbound(survivor, "contradicts")
            or survivor in graph.typed_outbound(loser, "contradicts")
        ):
            result["error"] = (
                "pair carries a contradicts edge — an open disagreement is resolved in "
                "/hippo:resolve, never merged away"
            )
            return result

        prot = protected_map(memory_dir, graph, telemetry_dir) if graph is not None else {}
        if loser in prot:
            result["error"] = (
                f"loser {loser!r} is protected ({', '.join(prot[loser])}) — a protected "
                "memory is never proposed for depression, and this executor holds the "
                "same line"
            )
            return result

        from .links import add_typed_relation
        from .staleness import set_invalid_after

        # COR-16: this is a TWO-write chain (survivor's edge, then loser's window).
        # Capture the survivor's bytes so a failure on write #2 rolls write #1 back
        # out — the envelope's changed=False must describe the disk, not a hope.
        with open(s_path, "r", encoding="utf-8") as fh:
            survivor_before = fh.read()
        rel = add_typed_relation(s_path, "supersedes", loser, dry_run=dry_run)
        if rel.get("error"):
            result["error"] = f"supersedes write failed: {rel['error']}"
            return result
        result["supersedes"] = {"changed": rel.get("changed", False)}

        inv = set_invalid_after(l_path, dry_run=dry_run)
        if inv.get("error"):
            result["error"] = f"invalid_after write failed: {inv['error']}"
            if rel.get("changed") and not dry_run:
                from .provenance import restore_file_bytes

                undo_err = restore_file_bytes(s_path, survivor_before, memory_dir)
                if undo_err:
                    result["error"] += (
                        f" — AND the supersedes rollback failed ({undo_err}): "
                        f"{survivor} still carries the edge; restore it from git"
                    )
                else:
                    result["error"] += " — the supersedes edge was rolled back"
                    result["supersedes"] = {"changed": False}
            return result
        result["invalid_after"] = {
            "changed": inv.get("changed", False),
            "ts": inv.get("invalid_after"),
        }
        result["changed"] = bool(rel.get("changed") or inv.get("changed"))
        if result["changed"] and not dry_run:
            from .dream import _refresh_index_quiet

            _refresh_index_quiet(memory_dir, index_dir)
    except Exception as exc:
        result["error"] = str(exc)
    return result


# --------------------------------------------------------------------------- #
# Render + pass orchestration (the memory.dream CLI dispatches here)
# --------------------------------------------------------------------------- #
def render_report(report: dict, *, report_file: Optional[str] = None) -> str:
    """The human-readable counterweight report. Empty lanes say so (inv3)."""
    lines: List[str] = []
    pass_id = report.get("pass_id", "?")
    if report.get("status") != "ok":
        lines.append(f"🧹 de-parasite pass {pass_id} — no proposals: {report.get('reason')}")
        return "\n".join(lines)

    stats = report.get("stats") or {}
    cap = report.get("max_out_degree")
    lines.append(
        f"🧹 de-parasite pass {pass_id} — REPORT/PROPOSE (zero memory writes): "
        f"{stats.get('over_cap', 0)} hub(s) over DREAM_MAX_OUT_DEGREE={cap} across "
        f"{stats.get('corpus_files', 0)} memories"
    )
    if report_file:
        lines.append(f"   report: {report_file}")

    degree_rows = report.get("degrees") or []
    if degree_rows:
        head = " · ".join(f"{r[0]}={r[1]}" for r in degree_rows[:6])
        lines.append(f"   top out-degree: {head}")

    flagged = report.get("flagged") or []
    if not flagged:
        lines.append("   no hub over the cap — nothing to counterweight (this is the norm).")
    for row in flagged:
        prot = (
            f" PROTECTED [{', '.join(row['protected_by'])}] — never proposed for depression"
            if row["protected"]
            else ""
        )
        lines.append(f"   • {row['stem']} (out={row['out_degree']}){prot}")
        for e in row.get("retract", []):
            lines.append(
                f"       ↩ retractable (Tier A, un-aged dream edge): {e['edge_id']} "
                f"{e['source']} ↔ {e['target']} ({e['kind']}, cofire {e.get('cofire')}, "
                f"{e['sessions_to_age']} session(s) to age-in)"
            )
        for d in row.get("demote_gated", []):
            if d["class"] == "aged-dream-edge":
                lines.append(
                    f"       ⛔ gated (aged-in dream edge — owner action): {d['edge_id']} "
                    f"{d['source']} ↔ {d['target']} → `{d['cmd']}`"
                )
            else:
                lines.append(
                    f"       ⛔ gated (human out-link, review only): [[{d['target']}]] — "
                    f"{d['evidence']}"
                )

    dedup = report.get("dedup") or []
    if dedup:
        lines.append(
            f"   dedup: {stats.get('dedup_merge_proposals', 0)} merge proposal(s), "
            f"{stats.get('dedup_routed_resolve', 0)} routed to /hippo:resolve "
            f"(bar: Jaccard ≥ {report.get('dedup_jaccard')})"
        )
        for d in dedup:
            if d["route"] == "resolve":
                lines.append(
                    f"   ↪ {d['a']} ⚡ {d['b']} (sim {d['similarity']}) — {d['note']}"
                )
            else:
                lines.append(
                    f"   ≈ {d['a']} + {d['b']} (sim {d['similarity']}) → keep "
                    f"{d['survivor']}, supersede {d['loser']} (non-lossy, per-item): "
                    f"`{d['proposal']['command']}`"
                )
    else:
        lines.append("   dedup: no near-duplicate pair above the bar.")
    lines.append(
        "   posture: report/propose. Only /dream's OWN un-aged edges may auto-retract "
        "(--retract); every demotion/merge above is per-item and gated — never auto."
    )
    return "\n".join(lines)


def run_deparasite_pass(
    memory_dir: str,
    index_dir: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    *,
    retract: bool = False,
) -> Tuple[int, str]:
    """The DRM-4 entry: analyze → persist the report → render (→ optionally retract).

    ``retract=True`` additionally executes the Tier-A lane (un-aged dream edges on
    flagged hubs) through the undo machinery — the ONLY thing this pass may ever apply.
    Exit 0 on ok/below-soak/empty-corpus (legible refusals are correct outcomes);
    a retraction refusal (drift) propagates undo's exit 1.
    """
    td = telemetry_dir or default_telemetry_dir(memory_dir)
    report = deparasite_report(memory_dir, index_dir, td)
    rf = write_report(td, report) if report["status"] == "ok" else None
    text = render_report(report, report_file=rf)
    if not retract or report["status"] != "ok":
        return 0, text
    code, retract_text = retract_flagged_unaged(memory_dir, index_dir, report)
    return code, text + "\n" + retract_text
