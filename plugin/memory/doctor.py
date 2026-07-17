"""Deterministic doctor engine for the memory plugin (DOC-4).

``python -m memory.doctor`` runs every environment/corpus health check in a FIXED order and
prints one ``✔``/``✘``/``⚠`` line per check. The ``/hippo:doctor`` SKILL is a thin wrapper: it
resolves the venv python (OSP-6) and presents this module's output verbatim. The point is
DETERMINISM — the same underlying state must yield byte-identical output across models and
sessions, because doctor is the skill users reach for when frustrated and run-to-run variance
in a diagnostic is itself a bug. Two of the checks (venv-sentinel hash compare, index/corpus
count) previously asked the agent to invent a verification procedure in prose; both now live
here as a single canonical implementation.

Structure mirrors ``session_start.py``'s producer pattern: a list of ``(label, check_fn)``
pairs, each ``check_fn(ctx) -> dict`` returning ``{"status": ok|warn|fail, "message": str}``,
and a ``main()`` that runs every check in order and prints a status-prefixed line each. The
FIXED order and the never-iterate-an-unordered-collection rule are what make the output
reproducible; every check reuses the canonical implementation of its concern (imported from
the module that owns it) rather than re-deriving it, so doctor and SessionStart can never drift.

Contract (mirrors the rest of the package):
  - Every check degrades rather than raises — a check that hits an unexpected error returns a
    ``warn`` line naming the failure, never crashes the run. A flagless ``main()`` always
    returns 0; the only CLI surface is argparse's own (``--help`` exits 0, an unknown flag
    exits 2 — both settled before any check runs).
  - No randomness, no set/dict-iteration-order dependence, no agent-invented wording: a given
    ``DoctorContext`` deterministically maps to a fixed list of lines.
  - Checks are read-only diagnostics. Doctor NAMES problems and the exact command to fix each;
    it never writes/repairs/re-baselines anything itself (destructive writes stay agent-gated).

The check implementations live in the flat siblings ``doctor_checks_env`` /
``doctor_checks_corpus`` / ``doctor_checks_recall``; this façade keeps the ordered check
registry, the engine, the CLI, and explicit re-exports of every check-surface name.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from .provenance import resolve_dirs

# DOC-4 decomposition: the check implementations live in the flat, prefix-named siblings
# below; these explicit grouped re-imports keep every historical ``memory.doctor.<name>``
# import and monkeypatch target (mcp_server, sleep, tests) resolving unchanged.
from .doctor_checks_env import (
    DoctorContext,
    _iter_memory_files_safe,
    _REQUIRED_DEPS,
    check_bootstrap,
    check_venv,
    check_corpus_exists,
    check_symlink,
    check_native_coexistence,
    check_corpus_resolution,
    check_git_mode,
    check_unresolvable_baselines,
    check_empty_baselines,
    check_integrity,
    check_index_corruption,
    check_index_count,
    check_mcp_launch,
    check_stale_memobot_env,
    check_projects_registry,
    check_plugin_version,
)
from .doctor_checks_corpus import (
    check_steering,
    check_format_version,
    check_pack_drift,
    check_fill_me,
    check_trust,
    check_trust_drift,
    check_secrets,
    check_threat_lint,
    check_ungrounded_prescriptions,
    check_committed_usage_privacy,
    check_dream_ledger,
    check_invalid_after_terminal,
    check_archive_shadowing,
    check_archive_regret,
    check_evidence_fences,
    check_merge_digest,
    _LATIN_ALPHA_RANGES,
    _NON_ENGLISH_MIN_ALPHA_SAMPLE,
    _NON_ENGLISH_ALPHA_FRACTION,
    _is_latin_alpha,
    check_non_english_corpus,
)
from .doctor_checks_recall import (
    _LINK_DENSITY_MIN_CORPUS,
    check_link_density,
    _EDGE_ROT_WARN_MIN,
    _FLOOR_CAL_TOLERANCE,
    _SALIENCE_LIVEDIN_MIN_SESSIONS,
    check_salience_evidence,
    check_floor_calibration,
    check_edge_rot,
    _HOT_PATH_P95_BUDGET_MS,
    check_hot_path_latency,
    check_recall_blind_spots,
    check_recall_channels,
    _DROP_AUTOPSY_MIN_EVENTS,
    check_drop_autopsy,
    check_abstention_cold_start,
    _ABSTENTION_SANITY_MAX_QUERIES,
    check_abstention_floor_sanity,
    check_injection_precision,
    check_rules_conflicts,
    check_rules_plane_rot,
    check_rules_source,
    check_succession_replay,
    check_update_eval,
)

# One glyph per status — the deterministic line prefix. Ordered dict-free lookup.
_GLYPH = {"ok": "✔", "warn": "⚠", "fail": "✘"}


# GOV-6 stays in the façade (not a checks sibling): the MSR-6 AST pin in
# tests/test_injection_cost.py parses THIS file for the `_scorecard_message` FunctionDef.
def _scorecard_message(memory_dir: str, repo_root: str) -> Tuple[str, str]:
    """GOV-6: the trust scorecard — one deterministic ``(status, message)`` rollup line.

    The governance signals are scattered point-checks (contradictions, blind spots, floor
    drift, steering, orphans); this is the ONE line a lead scans before trusting the
    corpus — the point-checks below it stay the drill-down (deliberate overlap, not
    double-reporting). Every input is INDIVIDUALLY guarded so an unshipped/failing
    producer contributes 0/absent, never an error; every iteration is sorted and nothing
    is timestamped, so identical state renders byte-identical (the doctor determinism
    pin). Shared with the ``hippo://scorecard`` MCP resource — one implementation, two
    surfaces.
    """
    from .build_index import _load_manifest, default_index_dir

    index_dir = default_index_dir(memory_dir)

    # GOV-1: contested-unresolved = corpus-wide contradicts pairs minus this clone's ledger.
    contested = 0
    try:
        from .resolve_view import unresolved_contradictions

        contested = len(unresolved_contradictions(memory_dir, repo_root=repo_root))
    except Exception:
        contested = 0

    # T2: rule↔memory conflicts + authority gaps, and rules-plane rot.
    rule_conflicts = 0
    try:
        from .rules_plane import conflict_radar

        radar = conflict_radar(memory_dir, repo_root)
        rule_conflicts = len(radar["edge_conflicts"]) + len(radar["authority_gaps"])
    except Exception:
        rule_conflicts = 0
    rot = 0
    try:
        from .rules_plane import rules_rot

        r = rules_rot(repo_root)
        rot = len(r["code_ref_rot"]) + len(r["dead_path_globs"])
    except Exception:
        rot = 0

    # T1/SIG-3: recurring recall blind spots.
    blind = 0
    try:
        from .telemetry import abstention_backlog, default_telemetry_dir

        blind = len(abstention_backlog(default_telemetry_dir(memory_dir)))
    except Exception:
        blind = 0

    # GOV-2 / GOV-7: steering + author-confidence counts off the manifest (0 when absent —
    # .get() on entries that predate the fields is exactly the graceful-absence contract).
    pinned = muted = draft = 0
    try:
        manifest = _load_manifest(index_dir)
        entries = manifest.get("entries", []) if manifest else []
        pinned = sum(1 for e in entries if e.get("steer") == "pin")
        muted = sum(1 for e in entries if e.get("steer") == "mute")  # 0 until MUTE ships
        draft = sum(1 for e in entries if e.get("confidence") == "draft")
    except Exception:
        pinned = muted = draft = 0

    # Orphans: graph isolates ∩ never-recalled (curation_report has no graph awareness).
    orphans = 0
    try:
        from .links import build_graph
        from .soak import curation_report
        from .telemetry import default_telemetry_dir

        graph = build_graph(memory_dir, index_dir)
        report = curation_report(memory_dir, default_telemetry_dir(memory_dir))
        if graph is not None:
            orphans = len(sorted(set(graph.isolates()) & set(report["never_recalled"])))
    except Exception:
        orphans = 0

    # GRA-8: how many weakly-connected components the memory graph fragments into. Informational
    # (never flips the rollup to warn — a small young corpus is legitimately fragmented); the
    # per-item drill-down is `hippo links --components`. None when the graph can't be built.
    components = None
    try:
        from .links import component_count

        components = component_count(memory_dir, index_dir)
    except Exception:
        components = None

    # MSR-6: the cost-honesty line — what hippo actually SPENT injecting, measured off
    # the ledgers (recall events' injected_chars + the SessionStart producer rows),
    # joined with the live KPI-2 touched-proxy. Session/producer aggregation ONLY —
    # never a per-memory touch table (the inert-recall-noise-finder kill; the MSR-6
    # AST pin holds this function to injection_precision's scalar aggregates).
    cost_chars = 0
    cost_sessions: set = set()
    try:
        from .telemetry import default_telemetry_dir as _dtd
        from .telemetry import read_events as _cost_read_events
        from .telemetry import read_injection_producers as _cost_read_producers

        _cost_td = _dtd(memory_dir)
        for e in _cost_read_events(_cost_td):
            ic = e.get("injected_chars")
            if isinstance(ic, int) and not isinstance(ic, bool) and ic > 0:
                cost_chars += ic
                if e.get("session_id"):
                    cost_sessions.add(e["session_id"])
        for row in _cost_read_producers(_cost_td):
            t = row.get("total")
            if isinstance(t, int) and not isinstance(t, bool) and t > 0:
                cost_chars += t
                if row.get("session_id"):
                    cost_sessions.add(row["session_id"])
    except Exception:
        cost_chars = 0
        cost_sessions = set()
    touched_pct = "touched n/a"
    try:
        from .outcome import injection_precision

        prec = injection_precision(memory_dir, None)
        if prec.get("precision") is not None:
            touched_pct = f"{round(prec['precision'] * 100)}% touched"
    except Exception:
        pass

    # GOV-4: floor/corpus changed since this clone's watermark (read-only peek — never
    # consumes the producer's surfaced-once semantics).
    floor_line = "floor/corpus delta: no watermark baseline yet"
    try:
        from .session_start import floor_change_peek

        peek = floor_change_peek(memory_dir, repo_root)
        if peek is not None:
            if any(peek.values()):
                floor_line = (
                    f"floor +{len(peek['floor_added'])}/−{len(peek['floor_removed'])}"
                    f"/{len(peek['floor_edited'])} edited, corpus +{peek['corpus_added']}"
                    f"/−{peek['corpus_removed']} since last session (→ review the git log)"
                )
            else:
                floor_line = "floor/corpus unchanged since last session"
    except Exception:
        pass

    parts = [
        f"{contested} contested-unresolved (→ /hippo:resolve)",
        f"{rule_conflicts} rule↔memory conflict(s) (→ /hippo:consolidate)",
        f"{rot} rules-plane rot (edit the named file)",
        f"{blind} blind spot(s) (→ /hippo:consolidate)",
        f"{orphans} orphan(s) never recalled (→ /hippo:audit)",
        f"{pinned} pinned / {muted} muted",
        f"{draft} draft",
        (f"{components} graph component(s)" if components is not None else "graph components: n/a"),
        # MSR-6: the folded-in cost-honesty part — one part, not a new check/line.
        f"injected ~{cost_chars} chars over {len(cost_sessions)} session(s); {touched_pct}",
        floor_line,
    ]
    status = "warn" if (contested or rule_conflicts or rot or blind or orphans) else "ok"
    return status, "trust scorecard: " + " · ".join(parts) + "."


def check_trust_scorecard(ctx: DoctorContext) -> Dict[str, str]:
    """GOV-6: the consolidated corpus-health rollup — see ``_scorecard_message``."""
    try:
        status, message = _scorecard_message(ctx.memory_dir, ctx.repo_root)
        return {"status": status, "message": message}
    except Exception as exc:
        return {"status": "warn", "message": f"trust scorecard failed: {exc}."}


# (label, check_fn) in a FIXED order — the source of the deterministic output. New checks append
# here; the order is never sorted-by-name or set-derived, so the printed sequence is stable.
CHECKS: List[Tuple[str, Callable[[DoctorContext], Dict[str, str]]]] = [
    ("bootstrap", check_bootstrap),
    ("plugin_version", check_plugin_version),
    ("venv", check_venv),
    ("corpus", check_corpus_exists),
    ("symlink", check_symlink),
    ("native_coexistence", check_native_coexistence),
    ("resolution", check_corpus_resolution),
    ("git_mode", check_git_mode),
    ("trust", check_trust),
    ("trust_scorecard", check_trust_scorecard),  # GOV-6: the one-line rollup a lead scans first; the point-checks below are the drill-down
    ("trust_drift", check_trust_drift),  # SEC-6: content drift since consent — quarantine state + the re-consent path
    ("integrity", check_integrity),
    ("index_corruption", check_index_corruption),
    ("index_count", check_index_count),
    ("steering", check_steering),  # GOV-2: N pinned (pre-wires the mandatory MUTE count)
    ("hot_path_latency", check_hot_path_latency),
    ("recall_channels", check_recall_channels),  # MSR-3: hook/mcp volume + MCP blind-spot arm
    ("recall_blind_spots", check_recall_blind_spots),
    ("drop_autopsy", check_drop_autopsy),  # MSR-4: which mechanism eats candidates, aggregated

    ("abstention_cold_start", check_abstention_cold_start),  # RET-11: abstention is dense-gated
    ("abstention_floor_sanity", check_abstention_floor_sanity),  # RET-9: per-corpus off-topic leak
    ("floor_calibration", check_floor_calibration),  # GRF-3: configured floor vs the sweep's number
    ("salience_evidence", check_salience_evidence),  # MSR-5: the ED-2 lived-in nudge (measures only)
    ("injection_precision", check_injection_precision),
    ("rules_conflicts", check_rules_conflicts),
    ("rules_plane_rot", check_rules_plane_rot),
    ("rules_source", check_rules_source),
    ("format_version", check_format_version),
    ("empty_baselines", check_empty_baselines),  # COR-10: the heal moved off the hook
    ("pack_drift", check_pack_drift),
    ("fill_me", check_fill_me),
    ("secrets", check_secrets),
    ("threat_lint", check_threat_lint),  # SEN-2: Tier-A corpus payloads + the Tier-B dark-ledger count
    ("ungrounded_prescriptions", check_ungrounded_prescriptions),  # SEN-3: sycophancy-amplification fraction

    ("link_density", check_link_density),
    ("edge_rot", check_edge_rot),  # GRF-1: edges into archived/superseded/dangling targets
    ("dream_ledger", check_dream_ledger),  # DRM-2: on-disk dream stamps ↔ dream-ledger.jsonl reconcile
    ("non_english_corpus", check_non_english_corpus),
    ("mcp_launch", check_mcp_launch),  # INT-8: the stdio MCP server (bin/hippo mcp) actually starts
    ("committed_usage_privacy", check_committed_usage_privacy),  # SEC-14: TEA-5 usage on a shared remote
    ("projects_registry", check_projects_registry),  # RCH-11: dead-row hygiene, machine-level
    ("invalid_after_terminal", check_invalid_after_terminal),  # TMB-2: non-drift retirements, corpus-wide
    ("succession_replay", check_succession_replay),  # TMB-5: supersedes with failing/unrun replay
    ("archive_shadowing", check_archive_shadowing),  # TMB-3: archive/ stem colliding with a live one
    ("archive_regret", check_archive_regret),  # TMB-3: abstentions matching archived bodies (evidence-only)
    ("update_eval", check_update_eval),  # TMB-4: outrank failures from the latest persisted run
    ("evidence_fences", check_evidence_fences),  # CLB-3: quoted-evidence coverage + cited-code drift
    ("merge_digest", check_merge_digest),  # CLB-4: incoming-merge duplicate pairs, human-routed
    ("stale_memobot_env", check_stale_memobot_env),  # pinned last (env hygiene trails)
]


def run_checks(ctx: DoctorContext) -> List[Tuple[str, Dict[str, str]]]:
    """Run every check in ``CHECKS`` order; return ``[(label, result)]``. Never raises.

    A check that raises despite its own try/except is caught here too and reported as a ``warn``
    line — a single misbehaving check can never abort the whole diagnostic run.
    """
    out: List[Tuple[str, Dict[str, str]]] = []
    for label, fn in CHECKS:
        try:
            result = fn(ctx)
        except Exception as exc:  # defense in depth — checks already guard themselves
            result = {"status": "warn", "message": f"{label} check crashed: {exc}."}
        status = result.get("status", "warn")
        if status not in _GLYPH:
            status = "warn"
        out.append((label, {"status": status, "message": result.get("message", "")}))
    return out


def format_line(result: Dict[str, str]) -> str:
    """One deterministic ``<glyph> <message>`` line for a check result."""
    glyph = _GLYPH.get(result.get("status", "warn"), _GLYPH["warn"])
    return f"{glyph} {result.get('message', '')}".rstrip()


def render(ctx: DoctorContext) -> str:
    """The full doctor report for ``ctx`` — one line per check, FIXED order. Deterministic.

    Same ``ctx`` (same underlying state) in => byte-identical string out. This is the literal
    DOC-4 acceptance criterion: the SKILL presents this verbatim, so identical state must yield
    identical output across models/sessions.
    """
    return "\n".join(format_line(result) for _label, result in run_checks(ctx))


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point — resolve dirs, run all checks, print the report.

    Zero-flag surface, parsed anyway: ``--help`` must print usage (exit 0) and an unknown
    flag must exit 2 rather than silently succeed with a report — both settle before any
    check runs. A flagless run always returns 0, even when context resolution fails.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Run every memory-plugin health check in a fixed order and print "
        "one status line per check. Read-only diagnostics; takes no options."
    )
    parser.parse_args(argv)
    try:
        memory_dir, repo_root = resolve_dirs()
        ctx = DoctorContext(memory_dir, repo_root)
        print(render(ctx))
    except Exception:
        # Even a total failure to resolve context must not crash the diagnostic — say so.
        print("⚠ doctor could not resolve the corpus/environment — is CLAUDE_PROJECT_DIR set?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
