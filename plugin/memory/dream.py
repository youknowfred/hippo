"""/dream — the generative sleep pass (DRM workstream, ``ROADMAP.dream.yaml``).

hippo's housekeeping verbs (consolidate / reconsolidate / staleness / salience) cover the
maintenance functions of sleep. This module is the GENERATIVE one: an offline pass that
replays the corpus against itself — re-runs recall over each memory's own derived
self-query — watches what co-fires, and diffs that against the link graph to surface the
latent edges the corpus is structurally missing:

  - **completion** — a body already NAMES another memory (plain-text mention, or a dangling
    ``[[wikilink]]`` that nearly resolves) but no edge exists. Highest precision.
  - **bridge**     — a transitive A–B–C pair (A–B and B–C linked, A–C absent) that co-fires.
    Exactly the 2-hop miss ``recall._expand_neighbors`` (GRA-1) turns into a 1-hop hit once
    the A–C edge exists.
  - **refines**    — an undeclared typed relation: a child memory whose slug extends a
    parent's (``foo-bar-baz`` refines ``foo-bar``) and that co-fires with it.

consolidate's two link signals — write-time similarity (GRA-3) and co-recall (GRW-2) — can
only connect already-similar or already-co-surfacing pairs; this latent class is unreachable
by construction. /dream is the verb that finds it.

DRM-1 (this slice) is REPORT-ONLY: zero writes to any memory file. It emits a candidate-edge
ledger (jsonl) under the gitignored derived telemetry dir and prints the co-fire-strength
distribution + count-by-kind so DRM-2's θ (``DREAM_COFIRE_THETA``) and per-pass cap are
calibrated from live data, not guessed. The workstream keystone: DRM-2..6 consume this
ledger and its calibration.

Non-negotiables carried from the roadmap (guiding_invariants, all load-bearing):
  - inv1  — the candidate ledger lives under the DERIVED telemetry dir (gitignored); the
            committed ``dream-ledger.jsonl`` (DRM-2's audit record) is provenance, not a
            second authority; aging state on top of it is DERIVED, never stored.
  - inv3  — the empty pass says so; below the soak bar says so; no silent no-ops.
  - inv4  — this slice writes NOTHING to any memory (report-only is inv4's strongest form).
  - inv6  — /dream is an offline turn (like consolidate); never the UserPromptSubmit hot path.
  - inv-DRM-firewall — the candidate generator's SOURCE set is confidence:verified +
            user-asserted memories + AGED-IN dream edges only. Un-aged dream edges (from the
            DRM-2 apply ledger) are invisible to generation: subtracted from the graph view
            used for worklist priority / distances / bridges, and probes run WITHOUT graph
            expansion (``recall(index=...)`` only — pure organic co-firing), so a dream edge
            can never feed the next pass's candidates before it ages in. Kills the
            dream-cites-a-dream tower structurally.
  - inv-DRM-empty-norm — θ and the cap are tuned so the EMPTY pass is the common outcome.

Floor memories (``lint_floor.floor_memory_names`` — always loaded in full) are never an edge
endpoint, source or target. Memories at ``confidence: draft`` are excluded as seeds AND as
endpoints (the firewall extends to quarantined content). The pass gates on
``soak.soak_status`` (≥5 distinct sessions): a young corpus proposes nothing.

Decomposed (pure code motion) into the ``dream_config/_ledgers/_discover/_contra/_apply``
siblings; every ``memory.dream.<name>`` still imports here via the explicit re-exports.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from .links import normalize_slug, parse_wikilinks
from .provenance import parse_frontmatter
from .telemetry import default_telemetry_dir

# --------------------------------------------------------------------------- #
# Decomposition re-exports (pure code motion): the machinery lives in the flat
# prefix siblings; every historical ``memory.dream.<name>`` stays importable
# here, and the orchestration below resolves through THIS namespace so existing
# ``monkeypatch.setattr(dream, ...)`` seams keep working. No sibling imports
# this façade back — the import graph is acyclic by construction.
# --------------------------------------------------------------------------- #
from .dream_apply import (
    _GATED_KINDS,
    _PRODUCER_MAX_ITEMS,
    _ROUTED_KINDS,
    _TIER_A_KINDS,
    _frontmatter_region,
    _insert_block_line,
    _refresh_index_quiet,
    _sanitize_stamp_text,
    _stamp_line,
    _undo_one_edge,
    apply_mode_default,
    dream_applied_producer,
    render_log,
    undo_edges,
)
from .dream_config import (
    _CANDIDATE_KINDS,
    _CONTRA_SIDE_CHARS,
    _DEFAULT_AGE_SESSIONS,
    _DEFAULT_CONTRA_MAX_PAIRS,
    _DEFAULT_CONTRA_TIMEOUT_S,
    _DEFAULT_MAX_APPLY,
    _DEFAULT_MAX_SEEDS,
    _DEFAULT_PROBE_K,
    _DEFAULT_REWARD_WEIGHT,
    _DEFAULT_THETA,
    _DISTANCE_CUTOFF,
    _HARD_CONTRA_MAX_PAIRS,
    _HARD_MAX_APPLY,
    _MIN_MENTION_CHARS,
    _REWARD_BOOST_RANK_CAP,
    _env_float,
    _env_int,
    _llm_file_setting,
    age_sessions,
    apply_eligible,
    cofire_theta,
    contra_llm_timeout,
    contra_max_pairs,
    contra_min_cofire,
    contradictions_enabled,
    max_apply_per_pass,
    reward_weight,
)
from .dream_contra import (
    _append_contradiction_rows,
    _contra_key,
    _contradiction_prompt,
    contradictions_ledger_path,
    discover_contradictions,
    read_contradiction_verdicts,
)
from .dream_discover import (
    _WIKILINK_SPAN_RE,
    _body_text,
    _body_without_wikilinks,
    _candidate_boost,
    _confidence_map,
    _corpus_texts,
    _distance,
    _fuzzy_stem_match,
    _mention_regex,
    _undirected_view,
    discover,
    reward_boosts,
)
from .dream_ledgers import (
    _GENERATED_KINDS,
    _new_pass_id,
    apply_ledger_path,
    boost_ledger_path,
    candidate_ledger_path,
    dream_dir,
    edge_aged_in,
    generated_rows,
    read_apply_ledger,
    unaged_dream_pairs,
    unaged_generated_stems,
    write_boost_ledger,
    write_candidate_ledger,
)


def _histogram(strengths: List[float], buckets: int = 10) -> List[str]:
    """Fixed 0.0–1.0 bucket histogram lines (ascii bars) for the distribution print."""
    counts = [0] * buckets
    for s in strengths:
        i = min(int(s * buckets), buckets - 1)
        counts[i] += 1
    peak = max(counts) if any(counts) else 1
    lines = []
    for i, n in enumerate(counts):
        lo, hi = i / buckets, (i + 1) / buckets
        bar = "█" * max(1, round(n * 24 / peak)) if n else ""
        lines.append(f"  {lo:.1f}–{hi:.1f}  {n:>4}  {bar}")
    return lines


def render_report(result: dict, *, ledger_path: Optional[str]) -> str:
    """The human-readable pass report: status, candidates, and the calibration surface."""
    lines: List[str] = []
    status = result.get("status")
    pass_id = result.get("pass_id", "?")
    if status != "ok":
        lines.append(f"🌙 dream pass {pass_id} — no candidates: {result.get('reason')}")
        lines.append("   (an empty pass is the norm; this one never reached replay)")
        return "\n".join(lines)

    stats = result.get("stats") or {}
    candidates = result.get("candidates") or []
    lines.append(
        f"🌙 dream pass {pass_id} — REPORT-ONLY (zero memory writes): "
        f"{len(candidates)} candidate edge(s) from {stats.get('seeds_probed', 0)} replay probe(s) "
        f"over {stats.get('corpus_files', 0)} memories"
    )
    if ledger_path:
        lines.append(f"   candidate ledger: {ledger_path}")
    kc = stats.get("kind_counts") or {}
    lines.append(
        "   count-by-kind: "
        + " · ".join(f"{k}={kc.get(k, 0)}" for k in _CANDIDATE_KINDS)
        + f" · unclassified-cofire-pairs={stats.get('unclassified_pairs', 0)}"
        + f" · novelty-excluded={stats.get('novelty_excluded', 0)}"
    )
    contra = stats.get("contradictions")
    if contra:
        lines.append(
            f"   ⚡ contradiction discovery (DRM-C, LLM opt-in): {contra.get('attempts', 0)} "
            f"pair(s) checked → {contra.get('judged', 0)} judged, {contra.get('proposed', 0)} "
            "proposed into the /hippo:resolve inbox (propose-only — never auto-applied)"
            + (
                f" · {contra.get('llm_failures', 0)} LLM failure(s) skipped"
                if contra.get("llm_failures")
                else ""
            )
        )
    if stats.get("unaged_dream_pairs_firewalled"):
        lines.append(
            f"   aging firewall: {stats['unaged_dream_pairs_firewalled']} un-aged dream edge(s) "
            "excluded from the source graph this pass"
        )
    if stats.get("reward_boosted_edges") or stats.get("reward_outcome_memories"):
        lines.append(
            f"   reward (DRM-5 reverse replay): {stats.get('reward_boosted_edges', 0)} upstream "
            f"edge boost(s) from {stats.get('reward_outcome_memories', 0)} outcome-anchored "
            f"memory(ies) — replay priority + candidate ORDERING only (θ reads raw cofire)"
        )
    if not candidates:
        lines.append("   empty pass — no latent edges above the reporting floor (this is the norm).")
    for c in candidates[:20]:
        dist = c.get("distance")
        dist_s = f"d={dist}" if isinstance(dist, int) else "d=∞"
        q = (c.get("query") or "")[:48]
        lines.append(
            f"   • {c['source']} → {c['target']}   {c['kind']:<10} "
            f"cofire={c['cofire']:.2f} {dist_s}"
            + (" mutual" if c.get("mutual") else "")
            + (f" ★boost={c['boost']:g}" if c.get("boost") else "")
            + f" [{c.get('signal')}]"
            + (f' q="{q}"' if q else "")
        )
    if len(candidates) > 20:
        lines.append(f"   …and {len(candidates) - 20} more (see the ledger).")

    # The calibration surface: the co-fire-strength DISTRIBUTION + θ sweep (DRM-1's point).
    all_s = stats.get("cofire_strengths_all_pairs") or []
    lines.append("")
    lines.append(
        f"   co-fire strength distribution — ALL observed pairs (n={len(all_s)}, "
        "strength = pair score / probe top score):"
    )
    lines.extend(_histogram(all_s))
    if all_s:
        import statistics

        qs = {
            "p50": statistics.median(all_s),
            "p75": all_s[max(0, round(len(all_s) * 0.25) - 1)],
            "p90": all_s[max(0, round(len(all_s) * 0.10) - 1)],
            "max": all_s[0],
        }
        lines.append(
            "   percentiles: " + " · ".join(f"{k}={v:.2f}" for k, v in qs.items())
        )
    sweep = stats.get("theta_sweep") or []
    lines.append(
        "   θ sweep (apply-eligible candidates at each θ — bridges need MUTUAL co-fire, "
        "refines need cofire≥θ, completions are text-evidence based and θ-exempt):"
    )
    lines.append("     " + " ".join(f"θ≥{row['theta']:.2f}:{row['apply_eligible']}" for row in sweep))
    lines.append(
        f"   current knobs: θ={stats.get('theta_current')} cap={stats.get('cap_current')} "
        "(DREAM_COFIRE_THETA / DREAM_MAX_APPLY_PER_PASS)"
    )
    if apply_mode_default():
        lines.append(
            "   this was a report-only pass — the auto-apply default is ON (owner flip "
            "2026-07-12): a bare pass applies Tier-A candidates above the calibrated bar."
        )
    else:
        lines.append(
            "   auto-apply is OFF (report-only) — the DRM-2 flip is a dated owner decision "
            "after this calibration."
        )
    return "\n".join(lines)


def _apply_one(
    memory_dir: str, cand: dict, edge_id: str, pass_id: str
) -> Tuple[bool, str, Optional[dict]]:
    """Apply ONE Tier-A candidate to the working tree. ``(ok, reason, undo_record)``.

    The undo record is what the ledger persists so --undo can reverse this exact edit:
      - bridge/completion: ``{"file", "block": {inserted, wrapper, lead}}``
      - refines:           ``{"file", "block": {...stamp...}, "fm_before", "fm_after"}``
    Nothing is written unless every part of the edit can proceed (per-edge atomicity).
    """
    from .links import add_typed_relation, parse_typed_relations

    src_path = os.path.join(memory_dir, cand["source"] + ".md")
    try:
        with open(src_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except Exception as exc:
        return False, f"source unreadable: {exc}", None

    line = _stamp_line(edge_id, pass_id, cand)

    if cand["kind"] in ("completion", "bridge"):
        # Idempotency re-check against CURRENT text (the discovery snapshot may be stale).
        if cand["target"] in parse_wikilinks(text):
            return False, "edge already present (wikilink)", None
        new_text, block_rec = _insert_block_line(text, line)
        try:
            from .atomic import write_text_atomic

            write_text_atomic(src_path, new_text)  # COR-18: never a torn corpus file
        except Exception as exc:
            return False, f"write failed: {exc}", None
        return True, "", {"file": os.path.basename(src_path), "block": block_rec}

    if cand["kind"] == "refines":
        fm = parse_frontmatter(text)
        existing = parse_typed_relations(fm).get("refines", [])
        if normalize_slug(cand["target"]) in {normalize_slug(t) for t in existing}:
            return False, "edge already present (refines)", None
        region_before = _frontmatter_region(text)
        if region_before is None:
            return False, "no frontmatter — cannot write a typed relation", None
        res = add_typed_relation(src_path, "refines", cand["target"])
        if res.get("error") or not res.get("changed"):
            return False, res.get("error") or "add_typed_relation was a no-op", None

        def _roll_back(step: str, exc: Exception) -> Tuple[bool, str, None]:
            # COR-16: write #1 (the frontmatter edge) landed; a failure anywhere before
            # write #2 completes used to strand it — a permanent, ledger-less edge that
            # undo cannot see and the idempotency guard refuses to ever re-complete.
            # The docstring's per-edge atomicity means the file comes back byte-identical.
            from .provenance import restore_file_bytes

            undo_err = restore_file_bytes(src_path, text, memory_dir)
            if undo_err:
                return False, (
                    f"{step}: {exc} — AND the rollback failed ({undo_err}): "
                    f"{cand['source']} carries an untracked refines edge; restore it "
                    "from git"
                ), None
            return False, f"{step}: {exc} — the frontmatter edge was rolled back", None

        try:
            with open(src_path, "r", encoding="utf-8") as fh:
                after_fm_text = fh.read()
            region_after = _frontmatter_region(after_fm_text)
            new_text, block_rec = _insert_block_line(after_fm_text, line)
        except Exception as exc:
            return _roll_back("re-read failed after frontmatter write", exc)
        try:
            from .atomic import write_text_atomic

            write_text_atomic(src_path, new_text)  # COR-18
        except Exception as exc:
            return _roll_back("write failed", exc)
        return True, "", {
            "file": os.path.basename(src_path),
            "block": block_rec,
            "fm_before": region_before[2],
            "fm_after": region_after[2] if region_after else [],
        }

    return False, f"kind {cand.get('kind')!r} is not Tier-A", None


def run_apply_pass(
    memory_dir: str,
    index_dir: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    *,
    probe_k: Optional[int] = None,
    max_seeds: Optional[int] = None,
    repo_root: Optional[str] = None,
    origin: Optional[str] = None,
) -> Tuple[int, str]:
    """The DRM-2 loop: discover → gate → apply (capped) → stamp+ledger → digest.

    ``origin`` (SLP-3): optional ledger provenance for a non-interactive invoker —
    the sleep runner stamps ``sleep:<ts>`` so the audit trail records WHO applied.
    Additive metadata only: every gate, cap, and downstream consumer (log, undo,
    aging, de-parasite) treats a stamped edge exactly like an interactive one.

    Preconditions before ANY write (all must hold; each refusal is named in the digest):
    soak bar met; corpus trusted (SEC-1 — autonomy never extends to an unreviewed corpus);
    per-edge: Tier-A kind above the calibrated bar, endpoints non-floor/non-draft, edge not
    already present, secret-lint CLEAN on every generated byte (hard BLOCK — the ratified
    dream-path deviation), provenance complete. Effect is immediate (working tree + index
    rebuild); the commit stays the owner's.
    """
    from . import trust
    from .secrets import scan_text
    from .telemetry import current_session_id

    td = telemetry_dir or default_telemetry_dir(memory_dir)

    # SEC-1: the write path refuses on an untrusted corpus (report-only remains available —
    # like doctor, it is a pre-consent-safe analysis).
    gate_root = trust.gate_repo_root(memory_dir, repo_root)
    if gate_root is not None and not trust.is_trusted(gate_root):
        return 1, (
            "🌙 dream: APPLY REFUSED — this corpus is untrusted (SEC-1). Review and trust "
            "it first (/hippo:doctor → trust flow); the report-only pass (--dry-run) "
            "remains available."
        )

    result = discover(memory_dir, index_dir, td, probe_k=probe_k, max_seeds=max_seeds)
    if result["status"] != "ok":
        return (1 if result["status"] == "no-index" else 0), render_report(
            result, ledger_path=None
        )
    write_candidate_ledger(td, result["pass_id"], result["candidates"])
    write_boost_ledger(td, result["pass_id"], (result.get("reward") or {}).get("edges") or [])

    pass_id = result["pass_id"]
    theta = cofire_theta()
    cap = max_apply_per_pass()
    soak = result.get("soak") or {}
    distinct_now = int(soak.get("distinct_sessions") or 0)
    session_id = current_session_id(td)

    gated = [c for c in result["candidates"] if c.get("kind") in _GATED_KINDS]
    routed = [c for c in result["candidates"] if c.get("kind") in _ROUTED_KINDS]
    eligible = [
        c
        for c in result["candidates"]
        if c.get("kind") in _TIER_A_KINDS and apply_eligible(c, theta=theta)
    ]

    # An undone/retracted pair NEVER auto-re-applies: an undo (owner) or retraction
    # (DRM-4 counterweight) is a standing verdict recorded in the committed ledger, and
    # autonomy must not override the audit record (DREAM-KILL-2's spirit; also the
    # retract→re-apply ping-pong guard the de-parasiting pass depends on). The candidate
    # still appears in report passes — re-applying it is a per-item human/agent action.
    prior_undone = {
        frozenset((e["source"], e["target"]))
        for e in read_apply_ledger(memory_dir)
        if e.get("state") == "undone"
        and isinstance(e.get("source"), str)
        and isinstance(e.get("target"), str)
    }

    applied: List[dict] = []
    refused: List[Tuple[dict, str]] = []
    fold_failures = 0  # BND-3: stamped files whose consent fold anomalously failed
    ledger_lines: List[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for cand in eligible:
        if len(applied) >= cap:
            break
        if frozenset((cand["source"], cand["target"])) in prior_undone:
            refused.append(
                (cand, "pair was undone/retracted before — never auto re-applied "
                       "(re-apply by hand if genuinely wanted)")
            )
            continue
        edge_id = f"{pass_id}-e{len(applied) + 1}"
        ledger_row = {
            "edge_id": edge_id,
            "pass": pass_id,
            "kind": cand["kind"],
            "source": cand["source"],
            "target": cand["target"],
            "cofire": cand.get("cofire"),
            "firing_query": cand.get("query") or "",
            "derives_from": [cand["source"], cand["target"]],
            "applied_at_session": session_id,
            "applied_at_distinct_count": distinct_now,
            "applied_at_ts": now_iso,
            "state": "active",
            # SLP-3: provenance of a non-interactive apply; absent on interactive passes.
            **({"origin": origin} if origin else {}),
        }
        # Provenance completeness is a hard precondition (DRM-2.spec.md §2): an edge with
        # a missing field is rejected pre-write.
        if not all(
            ledger_row.get(k) not in (None, "")
            for k in ("edge_id", "pass", "kind", "source", "target")
        ) or ledger_row.get("cofire") is None:
            refused.append((cand, "incomplete provenance"))
            continue
        # HARD secret BLOCK over every byte this edge would put on disk or in the ledger —
        # the stamp line AND the ledger row (the firing query flows into both). Ratified
        # dream-path deviation from secrets.py's WARN default: REFUSED, not warned.
        rationale = _stamp_line(edge_id, pass_id, cand) + "\n" + json.dumps(
            ledger_row, ensure_ascii=False
        )
        findings = scan_text(rationale)
        if findings:
            refused.append((cand, f"secret lint BLOCK: {'; '.join(findings)}"))
            continue
        ok, reason, undo_rec = _apply_one(memory_dir, cand, edge_id, pass_id)
        if not ok:
            refused.append((cand, reason))
            continue
        # SEC-6: fold the stamped file's FINAL bytes into the consent baseline. Without
        # this, a fingerprinted corpus quarantines every edge-stamped file out of recall
        # (new-since-consent bytes are withheld) — the exact opposite of "live in recall
        # immediately". The write is gated (trusted corpus, capped, θ-barred, secret-
        # blocked), so authorship-is-consent applies the same way it does for
        # add_typed_relation's own internal fold.
        # BND-3: an anomalous fold failure is counted into the digest's one line.
        try:
            note = trust.record_authored_write_disclosing(
                memory_dir, os.path.join(memory_dir, cand["source"] + ".md"), repo_root
            )
            if note:
                fold_failures += 1
        except Exception:
            pass
        ledger_row["undo"] = undo_rec
        ledger_lines.append(ledger_row)
        applied.append({**cand, "edge_id": edge_id})

    if ledger_lines:
        try:
            with open(apply_ledger_path(memory_dir), "a", encoding="utf-8") as fh:
                for row in ledger_lines:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as exc:
            # A ledger append failure after a write would orphan stamps — undo the pass.
            for row in reversed(ledger_lines):
                _undo_one_edge(memory_dir, row)
            return 1, f"🌙 dream: ledger append FAILED ({exc}) — pass rolled back, nothing applied."
        _refresh_index_quiet(memory_dir, index_dir)

    # ---- digest -------------------------------------------------------------------- #
    lines = [
        f"🌙 dream pass {pass_id} — applied {len(applied)} edge(s) "
        f"(uncommitted, live in recall; cap {cap}, θ={theta:g}):"
    ]
    if not applied:
        lines[0] = (
            f"🌙 dream pass {pass_id} — applied 0 edges (cap {cap}, θ={theta:g}): "
            "no candidate cleared the Tier-A bar. Empty is the norm."
        )
    glyph = {"bridge": "↔", "completion": "↔", "refines": "→"}
    for a in applied:
        q = (a.get("query") or "")[:40]
        lines.append(
            f"  • {a['source']} {glyph.get(a['kind'], '→')} {a['target']}   {a['kind']}"
            f"  (cofire {float(a.get('cofire') or 0):.2f}"
            + (f', q:"{q}"' if q else "")
            + f")  [{a['edge_id']}]"
        )
    for cand, reason in refused:
        lines.append(
            f"  ✘ refused {cand['source']} → {cand['target']} ({cand['kind']}): {reason}"
        )
    if fold_failures:
        # BND-3: the pass's ONE consent-disclosure line (aggregate, per the
        # at-most-one-line-per-verb ceiling) — remediation stays human.
        lines.append(
            f"  ⚠ {fold_failures} stamped file(s) did not join the consent baseline — "
            "withheld from recall until re-consent (trust_corpus)"
        )
    if gated:
        lines.append(
            f"  ⛔ {len(gated)} supersedes candidate(s) GATED — never auto-applied; apply "
            "only by explicit owner action:"
        )
        for c in gated[:5]:
            lines.append(f"     • {c['source']} supersedes {c['target']} (cofire {c['cofire']:.2f})")
    if routed:
        lines.append(
            f"  ↪ {len(routed)} contradicts candidate(s) routed to /hippo:resolve — never auto."
        )
    contra_stats = (result.get("stats") or {}).get("contradictions")
    if contra_stats:
        lines.append(
            f"  ⚡ contradiction discovery (DRM-C): {contra_stats.get('attempts', 0)} pair(s) "
            f"checked → {contra_stats.get('proposed', 0)} proposed into the /hippo:resolve "
            "inbox (propose-only)"
            + (
                f" · {contra_stats.get('llm_failures', 0)} LLM failure(s) skipped"
                if contra_stats.get("llm_failures")
                else ""
            )
        )
    if applied:
        lines.append(
            f"  reply `undo` to revert all · `undo <edge-id>` for one · they age into "
            f"/dream's trusted source set after {age_sessions()} sessions"
        )

    # DRM-6: the generative tier rides the apply pass. NEW generation is flag-gated
    # (HIPPO_DREAM_GENERATIVE, default OFF — DREAM-KILL-1: P3 ships behind a flag) and
    # reuses THIS pass's discovery result so the corpus is probed once per pass; the
    # decay SWEEP runs whenever generated rows exist, flag or no flag — the tier's
    # self-decay must never depend on the flag staying on (inv4: a generation tier
    # without the decay path must not ship). run_generative_pass sweeps internally on
    # its stage path, so the two lanes never double-sweep.
    try:
        from . import dream_generate as _dg

        if _dg.generative_enabled():
            _gc, gen_text = _dg.run_generative_pass(
                memory_dir, index_dir, td, stage=True, repo_root=repo_root, result=result
            )
            lines.append(gen_text)
        elif generated_rows(memory_dir):
            _sc, sweep_text = _dg.sweep_drafts(memory_dir, td, index_dir, repo_root=repo_root)
            lines.append(sweep_text)
    except Exception as exc:
        lines.append(f"  (generative tier skipped: {exc})")
    return 0, "\n".join(lines)


# --------------------------------------------------------------------------- #
# Pass orchestration + CLI
# --------------------------------------------------------------------------- #
def run_report_pass(
    memory_dir: str,
    index_dir: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    *,
    probe_k: Optional[int] = None,
    max_seeds: Optional[int] = None,
) -> Tuple[int, str]:
    """DRM-1's entry: discover → write the candidate ledger → render the report.

    Returns ``(exit_code, report_text)``. Exit 0 on an ok pass (even an empty one — empty is
    the norm) AND on a legible refusal (below-soak / empty-corpus: correct outcomes, not
    errors); 1 only on a genuine failure (no index).
    """
    td = telemetry_dir or default_telemetry_dir(memory_dir)
    result = discover(memory_dir, index_dir, td, probe_k=probe_k, max_seeds=max_seeds)
    ledger = None
    if result["status"] == "ok":
        ledger = write_candidate_ledger(td, result["pass_id"], result["candidates"])
        write_boost_ledger(td, result["pass_id"], (result.get("reward") or {}).get("edges") or [])
    text = render_report(result, ledger_path=ledger)
    code = 1 if result["status"] == "no-index" else 0
    return code, text


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    from .provenance import resolve_dirs

    parser = argparse.ArgumentParser(
        prog="memory.dream",
        description=(
            "/dream — the generative sleep pass: replay the corpus against itself and "
            "surface latent graph edges (DRM-1: report-only, zero memory writes)."
        ),
    )
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--index-dir", default=None)
    parser.add_argument("--telemetry-dir", default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="explicit report-only pass (the shipped default — auto-apply is OFF pending "
        "the dated owner flip; see ROADMAP.dream.yaml owner_decisions)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="run the DRM-2 Tier-A auto-apply loop this pass (capped, θ/mutuality-gated, "
        "stamped, undoable; never commits). Also enabled by HIPPO_DREAM_APPLY=1.",
    )
    parser.add_argument(
        "--undo",
        nargs="?",
        const="",
        default=None,
        metavar="EDGE_ID",
        help="revert applied dream edges: bare --undo reverts the latest pass; "
        "--undo <edge-id> exactly one. Byte-exact; refuses on manual drift.",
    )
    parser.add_argument(
        "--undo-since",
        default=None,
        metavar="DATE|N",
        help="revert edges applied since an ISO date, or within the last N distinct sessions",
    )
    parser.add_argument(
        "--log", action="store_true", help="list every dream edge (active / aged-in / undone)"
    )
    parser.add_argument(
        "--deparasite",
        action="store_true",
        help="DRM-4: the de-parasiting counterweight — report per-memory out-degree, flag "
        "hubs over DREAM_MAX_OUT_DEGREE, and PROPOSE retractions (dream's own un-aged "
        "edges) vs gated demotions/dedup-merges. Report/propose only; zero memory writes.",
    )
    parser.add_argument(
        "--retract",
        action="store_true",
        help="with --deparasite: additionally EXECUTE the Tier-A lane — retract the "
        "flagged, un-aged dream edges via the byte-exact undo machinery. Human "
        "structures and aged-in edges stay gated regardless.",
    )
    parser.add_argument(
        "--dedup-merge",
        nargs=2,
        metavar=("SURVIVOR", "LOSER"),
        default=None,
        help="execute ONE ratified dedup-merge proposal (per-item, no batch): SURVIVOR "
        "gains supersedes:[LOSER], LOSER's validity window closes (set_invalid_after). "
        "Non-lossy — additive frontmatter only, no body byte touched, nothing deleted.",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="DRM-6: the generative tier — cluster co-firing sets into schema/gist + "
        "hypothesis PROPOSALS (report-only by default; proposals land under the derived "
        "dream dir). With --stage (or HIPPO_DREAM_GENERATIVE=1 on apply passes), stages "
        "them into the corpus at confidence:draft — quarantined, capped, ledgered, "
        "undoable, self-decaying.",
    )
    parser.add_argument(
        "--stage",
        action="store_true",
        help="with --generate: actually stage the proposals as confidence:draft memories "
        "(explicit opt-in, like --apply; the corpus must be trusted). Never verified — "
        "graduation needs recorded outcome evidence (DREAM-KILL-1).",
    )
    parser.add_argument(
        "--sweep-drafts",
        action="store_true",
        help="DRM-6 decay: graduate evidence-confirmed drafts (draft→verified on a "
        "recorded outcome, never a glance), expire drafts past DREAM_DRAFT_HORIZON "
        "(auto-close validity + propose archive). Also runs inside every apply pass.",
    )
    parser.add_argument(
        "--archive-draft",
        default=None,
        metavar="NAME",
        help="execute ONE proposed draft archive (per-item; only dream-generated "
        "memories — human memories use the audit archive flow)",
    )
    parser.add_argument(
        "--prospective",
        action="store_true",
        help="DRM-6 metric: abstain→hit flips over the FROZEN abstention backlog, with "
        "dream-attribution (measure-only, off the hot path)",
    )
    parser.add_argument(
        "--contradictions",
        action="store_true",
        help="DRM-C: run the LLM contradiction check over this pass's high-cofire pairs "
        "(propose-only → the /hippo:resolve inbox; also enabled by "
        "HIPPO_DREAM_CONTRADICTIONS=1; needs an API key — silently skipped without one)",
    )
    parser.add_argument("--probe-k", type=int, default=None, help="co-fire probe depth (default 10)")
    parser.add_argument(
        "--max-seeds", type=int, default=None, help="cap the replay worklist (default 0 = all)"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the raw discovery result as JSON instead of the report"
    )
    args = parser.parse_args(argv)

    memory_dir = args.memory_dir
    if memory_dir is None:
        memory_dir, _ = resolve_dirs()

    # --contradictions is the CLI spelling of the env flag (the --generate/HIPPO_DREAM_
    # GENERATIVE convention): every downstream pass reads contradictions_enabled(), so the
    # in-process env set is the one wiring point.
    if args.contradictions:
        os.environ["HIPPO_DREAM_CONTRADICTIONS"] = "1"

    if args.log:
        print(render_log(memory_dir))
        return 0
    if args.deparasite:
        from .deparasite import run_deparasite_pass

        code, text = run_deparasite_pass(
            memory_dir, args.index_dir, args.telemetry_dir, retract=args.retract
        )
        print(text)
        return code
    if args.retract:
        print("🧹 --retract is a --deparasite modifier — run `dream --deparasite --retract`.")
        return 1
    if args.dedup_merge:
        from .deparasite import apply_dedup_merge

        survivor, loser = args.dedup_merge
        res = apply_dedup_merge(
            memory_dir,
            survivor,
            loser,
            telemetry_dir=args.telemetry_dir,
            index_dir=args.index_dir,
        )
        if res.get("error"):
            print(f"🧹 dedup-merge REFUSED: {res['error']}")
            return 1
        print(
            f"🧹 dedup-merge applied (non-lossy, reversible): {survivor} now supersedes "
            f"{loser}; {loser} invalid_after {res['invalid_after']['ts']}. Both files "
            "remain on disk; commit stays yours."
        )
        return 0
    if args.undo is not None or args.undo_since:
        code, text = undo_edges(
            memory_dir,
            args.index_dir,
            edge_id=(args.undo or None),
            since=args.undo_since,
        )
        print(text)
        return code
    if args.generate:
        from .dream_generate import run_generative_pass

        code, text = run_generative_pass(
            memory_dir,
            args.index_dir,
            args.telemetry_dir,
            stage=args.stage,
            probe_k=args.probe_k,
            max_seeds=args.max_seeds,
        )
        print(text)
        return code
    if args.stage:
        print("🌱 --stage is a --generate modifier — run `dream --generate --stage`.")
        return 1
    if args.sweep_drafts:
        from .dream_generate import sweep_drafts

        code, text = sweep_drafts(memory_dir, args.telemetry_dir, args.index_dir)
        print(text)
        return code
    if args.archive_draft:
        from .dream_generate import archive_draft

        res = archive_draft(
            memory_dir,
            args.archive_draft,
            telemetry_dir=args.telemetry_dir,
            index_dir=args.index_dir,
        )
        if res.get("error"):
            print(f"🌱 archive-draft REFUSED: {res['error']}")
            return 1
        print(
            f"🌱 archived dream draft {args.archive_draft} (git-reversible move into "
            "archive/; ledger updated; commit stays yours)."
        )
        return 0
    if args.prospective:
        from .dream_generate import prospective_recall, render_prospective

        print(render_prospective(prospective_recall(memory_dir, args.telemetry_dir, args.index_dir)))
        return 0
    # --json is a READ surface (raw discovery dump) — it never applies unless --apply is
    # explicit, regardless of the shipped default.
    if args.apply or (apply_mode_default() and not args.dry_run and not args.json):
        code, text = run_apply_pass(
            memory_dir,
            args.index_dir,
            args.telemetry_dir,
            probe_k=args.probe_k,
            max_seeds=args.max_seeds,
        )
        print(text)
        return code

    if args.json:
        td = args.telemetry_dir or default_telemetry_dir(memory_dir)
        result = discover(
            memory_dir, args.index_dir, td, probe_k=args.probe_k, max_seeds=args.max_seeds
        )
        if result["status"] == "ok":
            write_candidate_ledger(td, result["pass_id"], result["candidates"])
            write_boost_ledger(
                td, result["pass_id"], (result.get("reward") or {}).get("edges") or []
            )
        print(json.dumps(result, indent=2, ensure_ascii=False, default=list))
        return 1 if result["status"] == "no-index" else 0

    code, text = run_report_pass(
        memory_dir,
        args.index_dir,
        args.telemetry_dir,
        probe_k=args.probe_k,
        max_seeds=args.max_seeds,
    )
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
