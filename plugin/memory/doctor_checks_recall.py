"""Graph/recall/measurement checks for the deterministic doctor engine — decomposed out of
``doctor.py`` (DOC-4), which keeps the ordered check registry, the engine, and the CLI.

Link density (GRA-3), edge rot (GRF-1), floor calibration (GRF-3, RET-9), salience evidence
(MSR-5), hot-path latency (INT-5, KPI-3), recall channels/blind spots/drop autopsy (MSR-3,
SIG-3, MSR-4), abstention (RET-11, RET-9), injection precision (SIG-4, KPI-2), and the
rules-plane trio (RUL-1, RUL-2, RUL-4). ``DoctorContext`` lives in ``doctor_checks_env``.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from .doctor_checks_env import DoctorContext, _iter_memory_files_safe


# GRA-3: a corpus this small (< 5 memories) genuinely may have nothing worth cross-linking yet
# — the nudge below is about a corpus that has GROWN without ever discovering [[wikilinks]],
# not about a brand-new project's first couple of files.
_LINK_DENSITY_MIN_CORPUS = 5


def check_link_density(ctx: DoctorContext) -> Dict[str, str]:
    """One-time hint when the corpus has grown but never gained a single wikilink edge.

    GRA-3: the graph machinery (links.py / lint_links.py / recall's 1-hop expansion) was
    extracted from a corpus where links were hand-authored over months — a snap-in install
    starts at zero edges and, pre-GRA-3, no code path ever created one. ``new_memory`` now
    seeds a "Related: [[...]]" suggestion at write time, but a corpus that already has
    ``_LINK_DENSITY_MIN_CORPUS`` or more memories and STILL carries zero edges (memories
    written before this feature landed, or every suggestion so far was trimmed) never
    hears about the feature at all — this is the one-time doctor-level hint that closes that
    gap. Deliberately NOT a per-session SessionStart nag (``lint_links.health_line`` already
    treats bare orphan-hood as informational, never rot, on purpose — see its docstring); doctor
    is invoked on demand, so surfacing it here is a single ask-when-asked signal, not a repeated
    per-session nag. Silent (``ok``) below the corpus-size floor, when the graph fails to build,
    or once at least one edge exists anywhere in the corpus.
    """
    try:
        from .links import build_graph

        n = len(_iter_memory_files_safe(ctx.memory_dir))
        if n < _LINK_DENSITY_MIN_CORPUS:
            return {
                "status": "ok",
                "message": f"link density: N/A ({n} memories, below the {_LINK_DENSITY_MIN_CORPUS}-file floor for this hint).",
            }
        g = build_graph(ctx.memory_dir)
        if g is None:
            return {"status": "ok", "message": "link density: could not build the link graph."}
        total_edges = sum(len(v) for v in g.adjacency.values())
        if total_edges > 0:
            return {
                "status": "ok",
                "message": f"link density: {total_edges} wikilink edge(s) across {n} memories.",
            }
        return {
            "status": "warn",
            "message": f"link density is ZERO across {n} memories — memories can reference each "
            "other with [[name]] — see /hippo:new (new memories now suggest related links "
            "automatically; existing ones can be cross-linked by hand or via /hippo:audit's "
            "link-densification pass).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"link-density check failed: {exc}."}


# GRF-1: warn once the graph carries at least this many rotten edges (edges whose target
# is archived, superseded, or resolves to nothing). 1 — a single edge into retired
# knowledge is already worth a look, and the audit CLI names each one; below the
# threshold the line stays an informational ok (deterministic, no timestamps).
_EDGE_ROT_WARN_MIN = 1

# GRF-3: how far the sweep's recommended dense floor may sit from the configured table
# entry before the advisory line escalates ok -> warn. 0.02 — inside that band the two
# agree to within measurement noise on a small fixture set; beyond it the configured
# floor is measurably mis-calibrated for THIS corpus.
_FLOOR_CAL_TOLERANCE = 0.02

# MSR-5: a corpus counts as LIVED-IN — the ED-2 salience-revisit trigger — once this many
# distinct sessions have logged recalls into usage_aggregates.json. 10: enough sessions
# that the usage prior has a real distribution to boost from; below it a salience A/B is
# structurally the same signal-less run SIG-5 already judged.
_SALIENCE_LIVEDIN_MIN_SESSIONS = 10


def check_salience_evidence(ctx: DoctorContext) -> Dict[str, str]:
    """MSR-5: THE one automatic surface of the salience-revisit rig — a deterministic
    nudge once the corpus crosses the lived-in threshold and no A/B evidence exists.

    ED-2 is binding: this line never flips (or recommends flipping) the default — it
    only says the EVIDENCE the revisit needs is now measurable and names the runnable
    rig. Reads two small JSONs (usage aggregates + the recorded report); no eval runs
    here (a multi-second dense double-run can never be on a health check). No
    timestamps — the render/line-count determinism pins hold.
    """
    try:
        from .salience_eval import read_report
        from .telemetry import default_telemetry_dir, read_usage_aggregates

        agg = read_usage_aggregates(default_telemetry_dir(ctx.memory_dir))
        sessions = agg.get("sessions", {}).get("count") or 0
        tracked = len(agg.get("memories") or {})
        report = read_report(ctx.memory_dir)
        if report is not None:
            deltas = report.get("deltas") or {}
            arms = "identical arms" if report.get("identical_arms") else "arms differ"
            return {
                "status": "ok",
                "message": f"salience evidence: A/B recorded ({len(deltas)} categor(ies), "
                f"{arms}) — the flip stays a dated owner decision (ED-2).",
            }
        if sessions < _SALIENCE_LIVEDIN_MIN_SESSIONS:
            return {
                "status": "ok",
                "message": f"salience evidence: corpus not yet lived-in "
                f"({sessions}/{_SALIENCE_LIVEDIN_MIN_SESSIONS} sessions logged) — the ED-2 "
                "revisit rig waits.",
            }
        return {
            "status": "warn",
            "message": f"salience evidence: corpus is lived-in ({sessions} sessions, "
            f"{tracked} usage-tracked memories) but no A/B evidence is recorded — run "
            "`python -m memory.eval_recall --ab HIPPO_SALIENCE` (measures only; the "
            "default stays owner-decided-OFF per ED-2).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"salience-evidence check failed: {exc}."}


def check_floor_calibration(ctx: DoctorContext) -> Dict[str, str]:
    """GRF-3 (RET-9's calibration half): configured dense floor vs the persisted sweep.

    Reads the gitignored ``floor_sweep.json`` the ``--floor-sweep`` CLI wrote — doctor
    never runs the sweep itself (it embeds every fixture query with the dense model;
    seconds, not a health-check budget). Advisory-only by design (inv4): the line
    NAMES both numbers and the remedy; a human edits ``recall._DENSE_FLOOR_BY_MODEL``
    or sets ``HIPPO_DENSE_FLOOR`` — nothing here (or anywhere) auto-writes the table.
    A sweep keyed to a different corpus fingerprint is reported stale, not compared —
    a floor recommendation from last month's corpus says nothing about today's.
    """
    try:
        from .build_index import _load_manifest, default_index_dir, load_index
        from .eval_recall import corpus_fingerprint, read_floor_sweep
        from .recall import _dense_floor

        sweep = read_floor_sweep(ctx.memory_dir)
        if sweep is None:
            return {
                "status": "ok",
                "message": "floor calibration: no sweep recorded — "
                "`python -m memory.eval_recall --floor-sweep` writes one (RET-9).",
            }
        # Staleness leg: only when an index is actually loadable — the sweep report is
        # self-contained (model + recommendation), so a deleted/rebuildable index cache
        # must not silence the comparison; it just can't prove freshness.
        index_dir = default_index_dir(ctx.memory_dir)
        if _load_manifest(index_dir) is not None:
            index = load_index(index_dir)
            if index is not None and len(index):
                if sweep.get("corpus_fingerprint") != corpus_fingerprint(index):
                    return {
                        "status": "ok",
                        "message": "floor calibration: recorded sweep is STALE (corpus changed "
                        "since) — re-run `python -m memory.eval_recall --floor-sweep`.",
                    }
        configured = _dense_floor(sweep.get("model"))
        recommended = sweep.get("recommended")
        if not isinstance(recommended, (int, float)):
            return {"status": "ok", "message": "floor calibration: recorded sweep is unreadable."}
        delta = round(float(recommended) - float(configured), 4)
        overlap = " (no clean on/off-topic separation on this corpus)" if sweep.get("overlap") else ""
        if abs(delta) <= _FLOOR_CAL_TOLERANCE:
            return {
                "status": "ok",
                "message": f"floor calibration: configured {configured} ≈ recommended "
                f"{recommended} (Δ{delta:+}){overlap}.",
            }
        return {
            "status": "warn",
            "message": f"floor calibration: configured {configured} vs sweep-recommended "
            f"{recommended} (Δ{delta:+}, off-topic max {sweep.get('off_max')}){overlap} — "
            "edit recall._DENSE_FLOOR_BY_MODEL or set HIPPO_DENSE_FLOOR yourself; "
            "advisory only, nothing auto-writes.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"floor-calibration check failed: {exc}."}


def check_edge_rot(ctx: DoctorContext) -> Dict[str, str]:
    """GRF-1: ONE deterministic line for edge rot — the graph-audit's headline number.

    ``links.graph_audit`` classifies every edge whose target is archived (file moved to
    ``archive/``), superseded (another memory ``supersedes`` it — pointing at retired
    knowledge), or dangling (resolves to nothing). Same ask-when-asked posture as
    ``check_link_density`` (SessionStart's ``lint_links.health_line`` already nags plain
    dangling links per-session; doctor aggregates ALL rot classes on demand). Silent
    ``ok`` when the graph cannot be built (other checks own that failure) or rot is
    below ``_EDGE_ROT_WARN_MIN``.
    """
    try:
        from .links import graph_audit

        report = graph_audit(ctx.memory_dir)
        if report is None:
            return {"status": "ok", "message": "edge rot: N/A (could not build the link graph)."}
        rot = report.get("rot") or []
        by_class: Dict[str, int] = {}
        for r in rot:
            by_class[r["class"]] = by_class.get(r["class"], 0) + 1
        if len(rot) < _EDGE_ROT_WARN_MIN:
            return {
                "status": "ok",
                "message": f"edge rot: 0 across {report.get('edges', 0)} resolved edge(s).",
            }
        detail = ", ".join(f"{cls}={n}" for cls, n in sorted(by_class.items()))
        return {
            "status": "warn",
            "message": f"edge rot: {len(rot)} edge(s) into retired/missing targets "
            f"({detail}) — `python -m memory.links --audit` names each one.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"edge-rot check failed: {exc}."}


_HOT_PATH_P95_BUDGET_MS = 1500.0  # KPI-3 / PRF-2: the cold per-prompt budget


def check_hot_path_latency(ctx: DoctorContext) -> Dict[str, str]:
    """INT-5: report the recall hook's measured p95 wall-time over the telemetry ledger.

    ``latency_ms`` has always been in the recall ledger but nothing watched it. Because each
    recall runs in a FRESH hook process, its logged latency includes the cold model load — it IS
    the real per-prompt cost users pay, not a warm benchmark. This surfaces the p95 so a
    regression (a heavier model, a new per-import cost) is visible, warning past the KPI-3 cold
    budget. Read-only; N/A when the ledger is empty; never raises.

    MSR-3: filtered to ``channel in (hook, absent)`` — the ledger now also carries
    MCP-channel events (the recall/why tools), and an in-process MCP recall's timing is
    a different animal from the fresh-hook-process cost this p95 budgets. Without the
    filter, one MCP call would corrupt the KPI-3 gate this line exists to watch.
    """
    try:
        from .telemetry import default_telemetry_dir, read_events

        td = default_telemetry_dir(ctx.memory_dir)
        lats = sorted(
            float(e["latency_ms"])
            for e in read_events(td)
            if isinstance(e.get("latency_ms"), (int, float))
            and e.get("channel") in (None, "hook")
        )
        if not lats:
            return {
                "status": "ok",
                "message": "hot-path latency: no recall events logged yet — nothing to measure.",
            }
        n = len(lats)
        rank = max(1, min(n, (95 * n + 99) // 100))  # nearest-rank ceil(0.95*n), no float math
        p95 = lats[rank - 1]
        if p95 > _HOT_PATH_P95_BUDGET_MS:
            return {
                "status": "warn",
                "message": f"hot-path p95 = {p95:.0f}ms over {n} recall(s) — ABOVE the "
                f"{_HOT_PATH_P95_BUDGET_MS:.0f}ms per-prompt budget (KPI-3). A heavier model or "
                "new per-import cost likely regressed it.",
            }
        return {
            "status": "ok",
            "message": f"hot-path p95 = {p95:.0f}ms over {n} recall(s) "
            f"(budget {_HOT_PATH_P95_BUDGET_MS:.0f}ms).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"hot-path latency check failed: {exc}."}


def check_recall_blind_spots(ctx: DoctorContext) -> Dict[str, str]:
    """SIG-3: recurring recall abstentions (backend='none' clusters) the corpus can't answer.

    The always-available surface for the blind-spot backlog (SessionStart shows it only rarely).
    Reads the gitignored recall ledger, clusters recurring abstained queries, and reports the
    top one so a genuine, repeated gap becomes a capture prompt instead of staying invisible.
    Read-only; ``ok`` when there is no recurring backlog; never raises.
    """
    try:
        from .telemetry import abstention_backlog, default_telemetry_dir

        backlog = abstention_backlog(default_telemetry_dir(ctx.memory_dir))
        if not backlog:
            return {
                "status": "ok",
                "message": "recall blind spots: none — no recurring abstained queries in the ledger.",
            }
        top = backlog[0]
        q = top.get("sample_query") or ", ".join(top.get("terms") or [])
        return {
            "status": "warn",
            "message": f"recall blind spots: {len(backlog)} recurring question(s) your corpus "
            f'can\'t answer — top: "{q}" (asked {top["count"]}× recently, nothing above the '
            "floor). Capture via /hippo:consolidate.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"recall blind-spots check failed: {exc}."}


def check_recall_channels(ctx: DoctorContext) -> Dict[str, str]:
    """MSR-3: the per-channel recall volume line + the MCP abstention arm.

    MCP recall/why events were telemetry-INVISIBLE (recall_view bypassed main()'s
    logging), so the usage ledger undercounted exactly the highest-intent recalls —
    an agent explicitly asking mid-turn. This line says how much of each surface the
    ledger now sees, and how many recurring blind-spot clusters are MCP-specific
    (``abstention_backlog(channel="mcp")`` — an agent asked and got nothing,
    repeatedly). Counts only, no timestamps (the doctor determinism pin); read-only;
    informational, never a warn. NB the deliberate asymmetry: an UNTRUSTED corpus's
    MCP recalls leave zero ledger trace (SEC-1), so this line can never become the
    trust-posture flight recorder the round-2 vetting killed.
    """
    try:
        from .telemetry import abstention_backlog, default_telemetry_dir, read_events

        td = default_telemetry_dir(ctx.memory_dir)
        hook = mcp = 0
        for e in read_events(td):
            if (e.get("channel") or "hook") == "mcp":
                mcp += 1
            else:
                hook += 1
        if not (hook or mcp):
            return {
                "status": "ok",
                "message": "recall channels: no recall events logged yet — nothing to count.",
            }
        if not mcp:
            return {
                "status": "ok",
                "message": f"recall channels: all {hook} event(s) via hook — no MCP "
                "recall/why traffic logged yet.",
            }
        mcp_blind = len(abstention_backlog(td, channel="mcp"))
        return {
            "status": "ok",
            "message": f"recall channels: {hook} hook / {mcp} mcp event(s); "
            f"{mcp_blind} recurring MCP blind-spot cluster(s).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"recall channels check failed: {exc}."}


# MSR-4: below this many drop-carrying events the aggregation stays quiet — a couple of
# fresh recalls are anecdote, not evidence worth a doctor line's attention.
_DROP_AUTOPSY_MIN_EVENTS = 5


def check_drop_autopsy(ctx: DoctorContext) -> Dict[str, str]:
    """MSR-4: ONE deterministic aggregation line over the recall ledger's drop records.

    The admission-walk cut reasons (``drops``) and the abstention near-miss scores were
    write-only until this line: per-reason counts say WHICH mechanism eats candidates
    (knee vs floor vs MMR vs pool), and the abstention arm's median sub-floor margin
    says HOW CLOSE the misses run — the first measured evidence for RET-11's BM25-floor
    decision and the SIG-5 revisit. Counts only, sorted, no timestamps (the doctor
    determinism pin); gated on a minimum event count; informational, never a warn.
    """
    try:
        from .telemetry import default_telemetry_dir, read_events

        td = default_telemetry_dir(ctx.memory_dir)
        counts: Dict[str, int] = {}
        events_with_drops = 0
        near_miss_margins: List[float] = []
        for e in read_events(td):
            drops = e.get("drops")
            if isinstance(drops, list) and drops:
                events_with_drops += 1
                for d in drops:
                    reason = d.get("reason") if isinstance(d, dict) else None
                    if isinstance(reason, str) and reason:
                        counts[reason] = counts.get(reason, 0) + 1
            floor = e.get("dense_floor")
            if isinstance(floor, (int, float)):
                for nm in e.get("near_miss") or []:
                    s = nm.get("score") if isinstance(nm, dict) else None
                    if isinstance(s, (int, float)):
                        near_miss_margins.append(float(floor) - float(s))
        if events_with_drops < _DROP_AUTOPSY_MIN_EVENTS:
            return {
                "status": "ok",
                "message": f"drop autopsy: {events_with_drops} recall event(s) carry drop "
                f"records (aggregation starts at {_DROP_AUTOPSY_MIN_EVENTS}) — not enough "
                "evidence yet.",
            }
        parts = [
            f"{reason} ×{n}"
            for reason, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        line = f"drop autopsy: over {events_with_drops} recall event(s): " + ", ".join(parts)
        if near_miss_margins:
            near_miss_margins.sort()
            median = near_miss_margins[len(near_miss_margins) // 2]
            line += f" · abstention near-miss median margin {median:.4f} below the dense floor"
        return {"status": "ok", "message": line + "."}
    except Exception as exc:
        return {"status": "warn", "message": f"drop autopsy check failed: {exc}."}


def check_abstention_cold_start(ctx: DoctorContext) -> Dict[str, str]:
    """RET-11: reliable ABSTENTION (returning nothing for an off-topic prompt) is DENSE-GATED.

    Measured, not assumed. On the BM25-only cold-start path — before ``/hippo:bootstrap`` warms
    the dense model, or whenever the model cache is cold — recall cannot reliably reject an
    off-topic query: BM25 admits any prompt that shares even ONE keyword with a memory, and no
    lexical threshold (summed IDF mass, matched-token count, or single-token IDF) separates that
    coincidental overlap from a genuine single-keyword match without also dropping real hits —
    on the golden fixture the two classes overlap in every BM25-observable signal (a real
    "combining a keyword and an embedding ranking" query and an off-topic "classic French onion
    soup" query each match exactly one distinctive token). Only the dense model's semantic floor
    tells them apart. So rather than ship a false-precision BM25 floor, this check NAMES the
    limitation when it is live and nudges the one real fix. Read-only; ``ok`` once dense is
    serving or nothing is indexed yet; never raises.
    """
    try:
        from .build_index import _load_manifest, default_index_dir

        manifest = _load_manifest(default_index_dir(ctx.memory_dir))
        if manifest is None:
            return {
                "status": "ok",
                "message": "abstention: no index built yet — SessionStart will build it.",
            }
        if manifest.get("dense_ready"):
            return {
                "status": "ok",
                "message": "abstention floor active — the dense model is warmed.",
            }
        return {
            "status": "warn",
            "message": "recall is serving BM25-only (dense model not warmed), so ABSTENTION is "
            "degraded: an off-topic prompt that shares even one keyword with a memory can still "
            "surface a weak match. Reliable rejection of off-topic queries is dense-gated — no "
            "lexical threshold separates a coincidental keyword overlap from a real one (RET-11) "
            "— so run /hippo:bootstrap to warm the dense model and enable the abstention floor.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"abstention cold-start check failed: {exc}."}


# Bound the per-corpus abstention sweep — doctor runs on every /hippo:doctor and each off-topic
# query is a real recall() (an embed on the dense path), so cap how many the sanity check runs.
_ABSTENTION_SANITY_MAX_QUERIES = 25


def check_abstention_floor_sanity(ctx: DoctorContext) -> Dict[str, str]:
    """RET-9: per-corpus dense-floor sanity — run the corpus's OWN off-topic fixture against the
    live index and warn when off-topic queries LEAK through (the distribution-overlap symptom).

    The dense floor (0.60 for bge) is a global default; a particular corpus can still admit
    off-topic prompts whose scores overlap its real hits. This runs the corpus-local fixture
    (``<memory_dir>/.audit-fixtures/recall_abstention_set.yaml``, written by ``/hippo:audit``)
    against the live index and reports how many actually abstained — the EMPIRICAL per-corpus
    number, distinct from ``check_abstention_cold_start`` (RET-11)'s STRUCTURAL bm25-only
    statement, and it fires on the dense path too when a corpus's own floor is too permissive.
    Bounded (``_ABSTENTION_SANITY_MAX_QUERIES``), read-only, deterministic (recall() is), and
    degrades to ``ok``/``warn`` — never raises. Skips cleanly when there is no fixture or index.
    """
    try:
        from .build_index import _load_manifest, default_index_dir, load_index
        from .eval_recall import GATE_ABSTENTION, abstention_rate, load_abstention_set

        fixture = os.path.join(ctx.memory_dir, ".audit-fixtures", "recall_abstention_set.yaml")
        queries = load_abstention_set(fixture)
        if not queries:
            return {
                "status": "ok",
                "message": "abstention floor: no corpus-local off-topic fixture "
                "(.audit-fixtures/recall_abstention_set.yaml) — run /hippo:audit to generate one.",
            }
        index_dir = default_index_dir(ctx.memory_dir)
        if _load_manifest(index_dir) is None:
            return {"status": "ok", "message": "abstention floor: no index built yet."}
        index = load_index(index_dir)
        sample = queries[:_ABSTENTION_SANITY_MAX_QUERIES]
        result = abstention_rate(index, sample, index_dir=index_dir)
        rate, n = result["rate"], result["n"]
        abstained = round(n * rate)
        backend = "dense" if index.dense_ready else "bm25-only"
        if rate < GATE_ABSTENTION:
            hint = (
                "warm the dense model with /hippo:bootstrap — abstention is dense-gated (RET-11)"
                if not index.dense_ready
                else "the dense floor is too permissive for this corpus — consider raising "
                "HIPPO_DENSE_FLOOR"
            )
            return {
                "status": "warn",
                "message": f"abstention floor: only {abstained}/{n} off-topic fixture queries "
                f"abstained on this {backend} corpus (rate {rate:.2f} < {GATE_ABSTENTION}) — "
                f"off-topic prompts may inject; {hint}.",
            }
        return {
            "status": "ok",
            "message": f"abstention floor: {abstained}/{n} off-topic queries correctly abstained "
            f"on this {backend} corpus (rate {rate:.2f}).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"abstention floor sanity check failed: {exc}."}


def check_injection_precision(ctx: DoctorContext) -> Dict[str, str]:
    """SIG-4/KPI-2: the injection-precision proxy over the outcome ledger — MEASUREMENT ONLY.

    Reports the fraction of injected memories (that cite a file) whose cited file was later
    touched in-session — the read-signal KPI-2 said nothing produced today. Read-only; ``ok``
    always (a measurement, not a fault); ``ok`` with 'no signal yet' before any data. Never
    raises. This number never influences ranking (that is gated on SIG-5).
    """
    try:
        from .outcome import format_report

        return {"status": "ok", "message": format_report(ctx.memory_dir)}
    except Exception as exc:
        return {"status": "warn", "message": f"injection-precision check failed: {exc}."}


def check_rules_conflicts(ctx: DoctorContext) -> Dict[str, str]:
    """RUL-1: the rule↔memory conflict radar's always-available surface.

    Joins governance-plane citations (CLAUDE.md/AGENTS.md/.claude/rules|agents|skills)
    against the corpus: a cited memory another memory ``supersedes``/``contradicts`` is a
    live conflict; a cited memory no session recalls (strength < 0.15, once the 5-session
    soak gate is met) is an authority-evidence gap. Read-only; findings route to a per-item
    decision via /hippo:consolidate — nothing auto-resolves. ``ok`` when the planes agree;
    never raises.
    """
    try:
        from .rules_plane import conflict_radar
        from .soak import SOAK_GATE_SESSIONS

        radar = conflict_radar(ctx.memory_dir, ctx.repo_root)
        conflicts = radar["edge_conflicts"]
        gaps = radar["authority_gaps"]
        if not conflicts and not gaps:
            if radar["gate_met"]:
                return {
                    "status": "ok",
                    "message": "rule↔memory conflicts: none — governance citations agree with the corpus.",
                }
            return {
                "status": "ok",
                "message": "rule↔memory conflicts: none — typed-edge leg clean; the "
                f"strength leg waits on the soak gate ({radar['distinct_sessions']}/"
                f"{SOAK_GATE_SESSIONS} sessions).",
            }
        if conflicts:
            c = conflicts[0]
            top = f"{c['cited_by'][0]} cites `{c['name']}` but `{c['by']}` {c['relation']} it"
        else:
            g = gaps[0]
            top = (
                f"{g['cited_by'][0]} cites `{g['name']}` but no session recalls it "
                f"(strength {g['strength']:.2f})"
            )
        return {
            "status": "warn",
            "message": f"rule↔memory conflicts: {len(conflicts)} typed-edge conflict(s) + "
            f"{len(gaps)} authority gap(s) — top: {top}. Decide per item via "
            "/hippo:consolidate.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"rules-conflict check failed: {exc}."}


def check_rules_plane_rot(ctx: DoctorContext) -> Dict[str, str]:
    """RUL-2: citation rot + dead ``paths:`` globs across the always-loaded rules plane.

    The doctor twin of the ``rules_rot`` producer: a governance backtick reference whose
    path/symbol left the tree, or a ``.claude/rules`` frontmatter ``paths:`` glob matching
    nothing (a rule that can never lazy-load). Read-only; names the exact reference so the
    fix is a per-item human edit — never a rewrite. ``ok`` on a clean plane; never raises.
    """
    try:
        from .rules_plane import rules_rot

        rot = rules_rot(ctx.repo_root)
        code_rot = rot["code_ref_rot"]
        dead_globs = rot["dead_path_globs"]
        if not code_rot and not dead_globs:
            return {
                "status": "ok",
                "message": "rules-plane rot: none — governance code references and paths: globs resolve.",
            }
        if code_rot:
            r = code_rot[0]
            top = f"{r['file']} references `{r['ref']}` ({r['kind']} gone)"
        else:
            d = dead_globs[0]
            top = f"{d['file']} scopes paths: '{d['glob']}' (matches nothing)"
        return {
            "status": "warn",
            "message": f"rules-plane rot: {len(code_rot)} rotten code reference(s) + "
            f"{len(dead_globs)} dead paths: glob(s) — top: {top}. Edit the named file per item.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"rules-plane rot check failed: {exc}."}


def check_rules_source(ctx: DoctorContext) -> Dict[str, str]:
    """RUL-4: the rules recall source's legibility line (inv3).

    Recall silently skips the rules-plane source when its side-index is absent — this makes
    that state visible: how many governance sections are indexed, or why none are (no
    governance files, or a cache the next SessionStart will build). Read-only; ``ok``
    always (an absent plane is a fact, not a fault); never raises.
    """
    try:
        from .build_index import default_index_dir
        from .rules_plane import gov_files, load_rules_cache

        cache = load_rules_cache(default_index_dir(ctx.memory_dir))
        if cache is not None:
            n = len(cache.get("entries") or [])
            files = len({e.get("file") for e in cache.get("entries") or []})
            return {
                "status": "ok",
                "message": f"rules recall source: {n} governance section(s) indexed from "
                f"{files} file(s) — strong query matches surface as '(rule)' pointers.",
            }
        if not gov_files(ctx.repo_root):
            return {
                "status": "ok",
                "message": "rules recall source: no governance files (CLAUDE.md/.claude/rules) "
                "in this repo — nothing to index.",
            }
        return {
            "status": "ok",
            "message": "rules recall source: side-index not built yet — the next SessionStart "
            "builds it; until then recall surfaces no '(rule)' pointers.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"rules-source check failed: {exc}."}


# --------------------------------------------------------------------------- #
# TMB-5/TMB-4 (T11): succession + update-knowledge instruments
# --------------------------------------------------------------------------- #
def check_succession_replay(ctx: DoctorContext) -> Dict[str, str]:
    """TMB-5: ONE line for supersedes pairs whose succession replay is failing or unrun.

    A supersede whose replay FAILED left a leaking tombstone (queries that used to recall
    the old name don't rank the successor); one with NO replay evidence (a hand-authored
    edge, or a verdict predating TMB-5) was never verified at all. Evidence source: the
    links.json typed edges (one cache read) joined against the reconsolidation ledger's
    demote events — the replay itself only ever runs inside the per-item
    ``semantic_reverify`` demote path, never from here (doctor stays read-only). Silent
    ``ok`` when the graph is unavailable (other checks own that failure).
    """
    try:
        from .build_index import default_index_dir
        from .links import load_edges
        from .telemetry import read_reconsolidation_events

        edges = load_edges(default_index_dir(ctx.memory_dir))
        if edges is None:
            return {"status": "ok", "message": "succession replay: N/A (no links cache)."}
        pairs = []  # (declarer, target)
        for stem, rec in edges.items():
            for tgt in rec.get("typed_out", {}).get("supersedes", ()):
                pairs.append((stem, tgt))
        if not pairs:
            return {"status": "ok", "message": "succession replay: no supersedes edges."}
        latest: Dict[tuple, Optional[dict]] = {}
        for e in read_reconsolidation_events():
            if e.get("outcome") == "demote" and e.get("superseded_by") and e.get("name"):
                latest[(str(e["superseded_by"]), str(e["name"]))] = e.get("succession_replay")
        failing, unrun = [], []
        for declarer, tgt in sorted(pairs):
            if (declarer, tgt) not in latest or not isinstance(latest[(declarer, tgt)], dict):
                unrun.append(f"{declarer}→{tgt}")
            elif int(latest[(declarer, tgt)].get("fail") or 0) > 0:
                failing.append(f"{declarer}→{tgt}")
        if not failing and not unrun:
            return {
                "status": "ok",
                "message": f"succession replay: {len(pairs)} supersede pair(s), none failing or unreplayed.",
            }
        detail = "; ".join(
            ([f"failing: {', '.join(failing[:3])}"] if failing else [])
            + ([f"never replayed: {', '.join(unrun[:3])}"] if unrun else [])
        )
        return {
            "status": "warn",
            "message": f"succession replay: {len(failing) + len(unrun)} of {len(pairs)} "
            f"supersede pair(s) lack a passing replay ({detail}) — a `reconsolidate "
            "--reverify <old> --outcome demote --superseded-by <new>` verdict replays "
            "automatically; failing pairs suggest the successor misses the old name's "
            "recall surface.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"succession-replay check failed: {exc}."}


def check_update_eval(ctx: DoctorContext) -> Dict[str, str]:
    """TMB-4: the per-project update-knowledge line — the outrank-failure count from the
    LATEST persisted eval run (MSR-1's run ledger; ED2R-1 — doctor reads persisted eval,
    it never re-runs one). ok-at-zero and ok-at-no-evidence; GATE_UPDATE_* promotion is
    a dated owner decision — this line never gates anything.
    """
    try:
        from .eval_ledger import read_run_ledger
        from .telemetry import default_telemetry_dir

        latest = None
        for row in read_run_ledger(ctx.memory_dir, default_telemetry_dir(ctx.memory_dir)):
            latest = row
        if latest is None:
            return {
                "status": "ok",
                "message": "update eval: no persisted eval runs yet (eval_recall --json "
                "records one; update rows land via --draft-update + per-item confirm).",
            }
        u = (latest.get("report") or {}).get("update_knowledge")
        if not isinstance(u, dict) or not u.get("n"):
            return {
                "status": "ok",
                "message": "update eval: latest persisted run carries no update-category "
                "rows (the category has zero rows until drafted + confirmed).",
            }
        failures = int(u.get("outrank_failures") or 0)
        if failures == 0:
            return {
                "status": "ok",
                "message": f"update eval: {u['n']} update row(s), 0 outrank failures "
                "(successors beat their corpses in the latest persisted run).",
            }
        return {
            "status": "warn",
            "message": f"update eval: {failures} outrank failure(s) across {u['n']} update "
            "row(s) in the latest persisted run — a superseded memory still outranks (or "
            "hides) its successor; enrich the successor or re-run `python -m "
            "memory.eval_recall` after corpus fixes (report-only; GATE_UPDATE_* stays an "
            "owner decision).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"update-eval check failed: {exc}."}
