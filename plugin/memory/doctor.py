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
    ``warn`` line naming the failure, never crashes the run. ``main()`` always returns 0.
  - No randomness, no set/dict-iteration-order dependence, no agent-invented wording: a given
    ``DoctorContext`` deterministically maps to a fixed list of lines.
  - Checks are read-only diagnostics. Doctor NAMES problems and the exact command to fix each;
    it never writes/repairs/re-baselines anything itself (destructive writes stay agent-gated).
"""

from __future__ import annotations

import json
import os
from typing import Callable, Dict, List, Optional, Tuple

from .provenance import (
    check_project_symlink,
    git_root,
    parse_frontmatter,
    resolve_dirs,
    walk_up_for_memory_dir,
)

# One glyph per status — the deterministic line prefix. Ordered dict-free lookup.
_GLYPH = {"ok": "✔", "warn": "⚠", "fail": "✘"}

# The venv deps whose import must resolve for recall to run at full fidelity (SKILL.md's
# check #2). Ordered tuple — never a set — so the reported list is stable.
_REQUIRED_DEPS: Tuple[str, ...] = ("fastembed", "numpy", "yaml", "rank_bm25")


class DoctorContext:
    """The resolved inputs every check reads — assembled ONCE so checks are pure functions.

    Resolving ``memory_dir``/``repo_root`` (and the plugin-data/-root env) a single time up
    front (rather than each check calling ``resolve_dirs`` again) keeps the run cheap AND makes
    a check trivially testable: a test constructs a ``DoctorContext`` pointing at a hermetic
    fixture and calls the check function directly, no monkeypatching of module globals.
    """

    def __init__(
        self,
        memory_dir: str,
        repo_root: str,
        *,
        plugin_data: Optional[str] = None,
        plugin_root: Optional[str] = None,
    ) -> None:
        self.memory_dir = memory_dir
        self.repo_root = repo_root
        self.plugin_data = (
            plugin_data if plugin_data is not None else (os.environ.get("CLAUDE_PLUGIN_DATA") or "")
        )
        self.plugin_root = (
            plugin_root
            if plugin_root is not None
            else (
                os.environ.get("CLAUDE_PLUGIN_ROOT")
                or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
        )


def _iter_memory_files_safe(memory_dir: str) -> List[str]:
    """Sorted list of memory file paths (excludes MEMORY.md floor); [] on any problem."""
    try:
        from .provenance import _iter_memory_files

        return list(_iter_memory_files(memory_dir))
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# Checks (each: DoctorContext -> {"status", "message"}). Never raise.
# --------------------------------------------------------------------------- #
def check_bootstrap(ctx: DoctorContext) -> Dict[str, str]:
    """Bootstrap sentinel present + its recorded requirements_hash matches current deps.

    Reuses ``session_start.bootstrap_state`` — the SAME sha256(requirements.txt) compare COR-11
    established for the re-bootstrap nudge — rather than re-deriving a second hash procedure
    (DOC-4's one-implementation rule). Maps that canonical state token to a doctor line.
    """
    try:
        from .session_start import bootstrap_state

        state = bootstrap_state(ctx.plugin_data or None, ctx.plugin_root or None)
        if state == "no_data_dir":
            return {
                "status": "warn",
                "message": "CLAUDE_PLUGIN_DATA is unset — cannot locate the bootstrap sentinel "
                "or venv (this Claude Code version may be too old for self-provisioning).",
            }
        if state == "not_bootstrapped":
            return {
                "status": "fail",
                "message": "not bootstrapped (no .bootstrap-sentinel) — run /hippo:bootstrap.",
            }
        if state == "no_requirements":
            return {
                "status": "warn",
                "message": "bootstrap sentinel present but requirements.txt is unreadable — "
                "cannot verify deps are current.",
            }
        if state == "stale":
            return {
                "status": "fail",
                "message": "bootstrapped but STALE — requirements.txt changed since the last "
                "bootstrap (new imports degrade silently). Run /hippo:bootstrap again.",
            }
        return {"status": "ok", "message": "bootstrapped — deps current."}
    except Exception as exc:
        return {"status": "warn", "message": f"bootstrap check failed: {exc}."}


def check_venv(ctx: DoctorContext) -> Dict[str, str]:
    """All required deps import cleanly from the plugin-data venv.

    Only meaningful once bootstrapped; a missing import despite a sentinel claiming success
    means a corrupted/partial venv — recommend deleting the venv + sentinel and re-bootstrapping
    rather than patching in place. Names the FIRST failing dep (deterministic: ``_REQUIRED_DEPS``
    is an ordered tuple).
    """
    try:
        if not ctx.plugin_data:
            return {
                "status": "warn",
                "message": "CLAUDE_PLUGIN_DATA is unset — skipping venv import check.",
            }
        sentinel_path = os.path.join(ctx.plugin_data, ".bootstrap-sentinel")
        if not os.path.isfile(sentinel_path):
            return {
                "status": "warn",
                "message": "not bootstrapped — venv import check skipped (run /hippo:bootstrap).",
            }
        import importlib.util

        missing: List[str] = []
        for dep in _REQUIRED_DEPS:
            if importlib.util.find_spec(dep) is None:
                missing.append(dep)
        if missing:
            return {
                "status": "fail",
                "message": f"venv is missing import(s): {', '.join(missing)} — the sentinel "
                "claims success but the venv is corrupt/partial. Delete "
                "${CLAUDE_PLUGIN_DATA}/venv + .bootstrap-sentinel and re-run /hippo:bootstrap.",
            }
        return {"status": "ok", "message": f"venv healthy — {', '.join(_REQUIRED_DEPS)} all import."}
    except Exception as exc:
        return {"status": "warn", "message": f"venv check failed: {exc}."}


def check_corpus_exists(ctx: DoctorContext) -> Dict[str, str]:
    """The resolved corpus has a MEMORY.md floor — otherwise there is nothing to recall.

    ``resolve_dirs``/``walk_up_for_memory_dir`` already picked which ``.claude/memory`` this
    session uses; here we only confirm it is a real, seeded corpus. An absent one points at
    /hippo:init rather than any deeper check.
    """
    try:
        floor = os.path.join(ctx.memory_dir, "MEMORY.md")
        if os.path.isfile(floor):
            n = len(_iter_memory_files_safe(ctx.memory_dir))
            return {
                "status": "ok",
                "message": f"corpus present at {ctx.memory_dir} ({n} memories + MEMORY.md floor).",
            }
        if os.path.isdir(ctx.memory_dir):
            return {
                "status": "warn",
                "message": f"{ctx.memory_dir} exists but has no MEMORY.md floor — run /hippo:init "
                "here to seed it.",
            }
        return {
            "status": "fail",
            "message": f"no corpus at {ctx.memory_dir} — run /hippo:init to create one.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"corpus-existence check failed: {exc}."}


def check_symlink(ctx: DoctorContext) -> Dict[str, str]:
    """Project symlink health, verified the way Claude Code reads it (SHP-5 / ONB-5).

    Delegates to ``provenance.check_project_symlink`` (resolves the harness-encoded link's REAL
    target, never recomputes the formula blind) and reports its status verbatim, naming BOTH the
    returned ``repair_command`` and /hippo:init (ONB-5: safe to re-run on an existing corpus).
    """
    try:
        r = check_project_symlink(ctx.repo_root, ctx.memory_dir)
        status = r.get("status")
        repair = r.get("repair_command")
        if status == "ok":
            return {"status": "ok", "message": "project symlink resolves to this corpus."}
        if status == "missing":
            return {
                "status": "fail",
                "message": "no project symlink yet — Claude Code can't find this corpus. Fix: "
                f"`{repair}` (or run /hippo:init here — ONB-5 leaves the existing corpus untouched).",
            }
        if status == "broken":
            return {
                "status": "fail",
                "message": "project symlink points elsewhere — Claude Code reads a different "
                f"corpus. Fix: `{repair}` (or run /hippo:init here — ONB-5).",
            }
        if status == "legacy_wrong_encoding":
            return {
                "status": "warn",
                "message": "a legacy (pre-SHP-5) mis-encoded symlink exists for this repo. Fix: "
                f"`{repair}` (or run /hippo:init here — creates the correct link but does not "
                "remove the stale legacy dir).",
            }
        return {"status": "warn", "message": f"project symlink status: {status}."}
    except Exception as exc:
        return {"status": "warn", "message": f"symlink check failed: {exc}."}


def check_native_coexistence(ctx: DoctorContext) -> Dict[str, str]:
    """INT-4: the native-memory coexistence contract — detect drift + native-layout changes.

    hippo's always-load floor piggybacks on ONE undocumented Claude Code internal: the
    ``~/.claude/projects/<encoded>/memory`` symlink the harness reads as native memory, which
    /hippo:init points at this corpus. That is the whole contract (see the compatibility doc,
    ``plugin/memory/NATIVE_MEMORY.md``). check_symlink names the repair; this watches the same
    link from the COEXISTENCE angle and names the two ways the native relationship silently
    breaks: symlink-target DRIFT (the link resolves somewhere other than this corpus, so the
    floor is drawn from a different target) and a NATIVE-LAYOUT CHANGE (a real file/dir occupies
    the slot instead of hippo's symlink — Claude Code's native memory taking it over, an
    unexpected native write path the floor cannot inject through). Read-only; never raises.
    """
    try:
        r = check_project_symlink(ctx.repo_root, ctx.memory_dir)
        expected = r.get("expected_path") or ""
        # Strongest native-layout-change signal: something REAL (not hippo's symlink) sits in
        # the slot the harness reads — native memory (or a stray dir) has taken it over.
        if expected and os.path.lexists(expected) and not os.path.islink(expected):
            kind = "directory" if os.path.isdir(expected) else "file"
            return {
                "status": "warn",
                "message": f"native-layout change: {expected} is a real {kind}, not hippo's "
                "symlink — Claude Code's native memory may have taken the projects-dir slot. "
                "hippo's floor cannot inject through it; move it aside, then run /hippo:init.",
            }
        status = r.get("status")
        if status == "ok":
            return {
                "status": "ok",
                "message": "native coexistence intact — the projects-dir memory symlink (the one "
                "native behavior hippo relies on) resolves to this corpus.",
            }
        if status == "broken":
            return {
                "status": "warn",
                "message": "native-memory symlink DRIFT — the projects-dir link resolves to a "
                "different target than this corpus, so the always-load floor is drawn elsewhere "
                "(or nowhere). Fix: /hippo:init (the symlink check names the exact command).",
            }
        if status == "legacy_wrong_encoding":
            return {
                "status": "warn",
                "message": "native projects-dir layout changed — a legacy-encoded link exists, so "
                "the harness reads a different path now. Fix: /hippo:init.",
            }
        # missing → coexistence not established yet; check_symlink already flags it as the setup step.
        return {
            "status": "ok",
            "message": "native coexistence: no projects-dir memory link yet — /hippo:init "
            "establishes it (the floor injects via that native symlink).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"native-coexistence check failed: {exc}."}


def check_corpus_resolution(ctx: DoctorContext) -> Dict[str, str]:
    """Which corpus resolved and WHY (monorepo nested-vs-root walk-up, SHP-2 / OQ-1).

    A subdir session that silently fell through to the repo-root corpus looks identical to a
    healthy nested one; naming the resolution ``reason`` surfaces the fallthrough as the correct
    (but worth-knowing) behavior it is. Reads ``walk_up_for_memory_dir`` — the same walk
    ``resolve_dirs`` uses — so doctor reports exactly what recall will do.
    """
    try:
        start = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        _found, reason = walk_up_for_memory_dir(start)
        if reason == "nested":
            return {
                "status": "ok",
                "message": f"resolved corpus: {ctx.memory_dir} (nested — found at the launch dir).",
            }
        if reason == "root-fallthrough":
            return {
                "status": "ok",
                "message": f"resolved corpus: {ctx.memory_dir} (root-fallthrough — no nested "
                "corpus at the launch dir, so the walk ascended to it; correct, but your edits "
                "land in this corpus, not a per-package one).",
            }
        return {
            "status": "warn",
            "message": f"resolved corpus: {ctx.memory_dir} (none found in the walk — this is the "
            "CLAUDE_PROJECT_DIR default; run /hippo:init here or at the repo root).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"corpus-resolution check failed: {exc}."}


def check_git_mode(ctx: DoctorContext) -> Dict[str, str]:
    """Git repo present, else the SHP-4 labeled degraded-mode subsystem list.

    Non-git is SUPPORTED, not an error: staleness/provenance go inactive and archive falls back
    to ``os.rename`` (still recoverable), while recall/indexing/links/floor are unaffected. The
    inactive-subsystem list is a fixed, ordered string — no run-to-run variance.
    """
    try:
        root = git_root(ctx.repo_root)
        if root:
            return {
                "status": "ok",
                "message": "git repo detected — staleness, provenance, and archive's git-mv path "
                "are all active.",
            }
        return {
            "status": "warn",
            "message": "not a git repository — DEGRADED mode: staleness tracking INACTIVE, "
            "provenance/backfill INACTIVE, archive DEGRADED (os.rename fallback, still "
            "recoverable). recall, indexing, links, and floor loading are unaffected — run "
            "`git init` and commit to restore the rest.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"git-mode check failed: {exc}."}


def check_unresolvable_baselines(ctx: DoctorContext) -> Dict[str, str]:
    """Count memories whose staleness baseline sha isn't in history (squash-merge / shallow clone).

    Reuses ``staleness.count_unresolvable_baselines`` — the same function the SessionStart
    ``unresolvable_baseline_producer`` reports — so the two surfaces can never disagree. A weaker
    (time-based) fallback signal is a labeled degradation; silent-clean when the count is 0.
    """
    try:
        from .staleness import count_unresolvable_baselines

        n = count_unresolvable_baselines(ctx.memory_dir, ctx.repo_root)
        if not n:
            return {"status": "ok", "message": "all staleness baselines resolve in git history."}
        return {
            "status": "warn",
            "message": f"{n} memories have unresolvable staleness baselines (source_commit sha "
            "not in history — likely squash-merge or a shallow clone); falling back to "
            "time-based comparison.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"baseline check failed: {exc}."}


def check_empty_baselines(ctx: DoctorContext) -> Dict[str, str]:
    """Memories whose staleness baseline is EMPTY (``source_commit: ""``) — COR-10.

    A memory with an empty baseline is invisible to staleness, reconsolidation and archive
    gating, forever. SessionStart used to heal these silently on every run; that was a hook
    WRITING to memory frontmatter, which drifted each healed file off its own SEC-6
    fingerprint and left the trust banner asking the user "a git pull? a hand edit?" about
    hippo's own write. The heal moved to the CLI, so this check is what keeps the state
    visible — doctor reports and names the command; the human runs it.
    """
    try:
        from .provenance import _iter_memory_files, parse_frontmatter

        empty = []
        for path in _iter_memory_files(ctx.memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    fm = parse_frontmatter(fh.read())
                if not fm:
                    continue  # check_integrity owns unparseable files
                meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
                sc = fm.get("source_commit")
                if sc is None:
                    sc = (meta or {}).get("source_commit")
                if sc is not None and not str(sc).strip():
                    empty.append(os.path.basename(path)[:-3])
            except Exception:
                continue
        if not empty:
            return {"status": "ok", "message": "no empty staleness baselines."}
        return {
            "status": "warn",
            "message": (
                f"{len(empty)} memory(ies) have an EMPTY staleness baseline and are "
                f"invisible to staleness tracking: {', '.join(sorted(empty))}. Heal them to "
                "HEAD with the heal_baselines MCP tool, or in a terminal: "
                "python -m memory.provenance --heal-baselines"
            ),
        }
    except Exception as exc:
        return {"status": "warn", "message": f"empty-baseline check failed: {exc}."}


def check_integrity(ctx: DoctorContext) -> Dict[str, str]:
    """Memory files whose frontmatter does not yaml-parse (invisible to staleness, QUA-5 sibling).

    Reuses ``staleness.find_unparseable`` — an unparseable memory is a silent hole (skipped by
    the staleness signal AND re-baselined by ``provenance --refresh``). Names each file BY NAME
    in the sorted order ``find_unparseable`` returns (deterministic).
    """
    try:
        from .staleness import find_unparseable

        broken = find_unparseable(ctx.memory_dir)
        if not broken:
            return {"status": "ok", "message": "all memory frontmatter parses."}
        return {
            "status": "fail",
            "message": f"{len(broken)} memory file(s) have UNPARSEABLE frontmatter (invisible to "
            f"staleness): {', '.join(broken)}. Usually an unquoted value with a ': ' — quote it.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"integrity check failed: {exc}."}


def check_index_corruption(ctx: DoctorContext) -> Dict[str, str]:
    """On-disk recall-index corruption (QUA-5) — import and call, never reimplement.

    ``build_index.check_index_integrity`` is the ONE detector for the truncated-manifest /
    missing-dense / wrong-shape states that otherwise degrade recall to nothing silently. Its
    returned string is reported verbatim; ``None`` means healthy or nothing built yet.
    """
    try:
        from .build_index import check_index_integrity, default_index_dir

        finding = check_index_integrity(default_index_dir(ctx.memory_dir))
        if not finding:
            return {"status": "ok", "message": "recall index is intact."}
        return {"status": "fail", "message": f"{finding}."}
    except Exception as exc:
        return {"status": "warn", "message": f"index-corruption check failed: {exc}."}


def check_index_count(ctx: DoctorContext) -> Dict[str, str]:
    """Manifest entry count vs actual corpus file count (the count check DOC-4 pulls out of prose).

    Compares ``len(compute_corpus(memory_dir))`` against the loaded manifest's ``count`` — a
    mismatch means the index is stale (a memory was added/removed since the last build).
    Recommends the exact rebuild command. Silent-clean when they match; skipped (ok, "nothing
    built") when no manifest exists yet.
    """
    try:
        from .build_index import _load_manifest, compute_corpus, default_index_dir

        index_dir = default_index_dir(ctx.memory_dir)
        manifest = _load_manifest(index_dir)
        if manifest is None:
            return {"status": "ok", "message": "no index built yet — SessionStart will build it."}
        actual = len(compute_corpus(ctx.memory_dir))
        recorded = manifest.get("count")
        if recorded == actual:
            return {"status": "ok", "message": f"index count matches the corpus ({actual})."}
        return {
            "status": "warn",
            "message": f"index count ({recorded}) does not match the corpus ({actual}) — a "
            "memory was added/removed since the last build. Rebuild: `python -m "
            "memory.build_index --memory-dir <memory_dir> --index-dir <index_dir>` (a persistent "
            "mismatch across sessions points at a SessionStart hook problem).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"index-count check failed: {exc}."}


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


def check_steering(ctx: DoctorContext) -> Dict[str, str]:
    """GOV-2: how many memories carry an author steer — the control axis made visible.

    Informational (always ok) and manifest-only (no file reads). This line deliberately
    pre-wires the shape MUTE will need when it lands (a muted memory must be COUNTED here,
    never silently gone — inv3); today the only shipped mode is ``pin``.
    """
    try:
        from .build_index import _load_manifest, default_index_dir

        manifest = _load_manifest(default_index_dir(ctx.memory_dir))
        if manifest is None:
            return {
                "status": "ok",
                "message": "steering: no index built yet — pin counts appear after the first build.",
            }
        pinned = sorted(
            str(e.get("name")) for e in manifest.get("entries", []) if e.get("steer") == "pin"
        )
        if not pinned:
            return {"status": "ok", "message": "steering: no memories pinned."}
        shown = ", ".join(pinned[:5]) + (", …" if len(pinned) > 5 else "")
        return {
            "status": "ok",
            "message": f"steering: {len(pinned)} memory(ies) pinned (bounded recall lift, "
            f"capped — never beats a genuinely stronger match): {shown}.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"steering check failed: {exc}."}


def check_format_version(ctx: DoctorContext) -> Dict[str, str]:
    """BOTH format versions on one line: index ``schema_version`` and corpus format (COR-7).

    INDEX: the persisted manifest's ``schema_version`` vs the running module's
    ``SCHEMA_VERSION``. Since COR-7 this is enforced — every load path treats a mismatched
    manifest as absent, so the state is transient (the next SessionStart refresh performs
    one full rebuild) and needs no operator action. Read via the RAW manifest reader:
    ``_load_manifest`` would hide exactly the mismatch this check exists to name.

    CORPUS: the ``.claude/memory/.format`` marker's declared format vs the plugin's
    ``CORPUS_FORMAT_VERSION`` — BOTH directions. Corpus NEWER than the plugin: this plugin
    misreads/ignores conventions it predates — update the hippo plugin (same signal the
    ``corpus_format`` SessionStart producer carries). Corpus OLDER than the plugin: user
    data needs a MIGRATION, which is doctor-driven and agent-gated per the README's
    "Corpus format versioning" section — hippo never migrates the corpus autonomously, so
    doctor names the exact state and points at the documented path instead.
    """
    try:
        from .build_index import SCHEMA_VERSION, _read_manifest_json, default_index_dir
        from .provenance import CORPUS_FORMAT_VERSION, read_corpus_format

        status = "ok"
        parts: List[str] = []

        manifest = _read_manifest_json(default_index_dir(ctx.memory_dir))
        if manifest is None:
            parts.append("no index built yet — nothing to version-check")
        else:
            on_disk = manifest.get("schema_version")
            if on_disk == SCHEMA_VERSION:
                parts.append(f"index format version current (v{SCHEMA_VERSION})")
            else:
                status = "warn"
                parts.append(
                    f"index format version is v{on_disk}, this plugin writes v{SCHEMA_VERSION} "
                    "— the stale index is ignored (treated as absent) and the next "
                    "SessionStart refresh performs one full rebuild"
                )

        declared = read_corpus_format(ctx.memory_dir)
        if declared == CORPUS_FORMAT_VERSION:
            parts.append(f"corpus format current (v{declared})")
        elif declared > CORPUS_FORMAT_VERSION:
            status = "warn"
            parts.append(
                f"corpus format is v{declared} but this plugin only understands "
                f"v{CORPUS_FORMAT_VERSION} — update the hippo plugin (a newer-format corpus "
                "can carry conventions this version misreads or silently ignores)"
            )
        else:
            status = "warn"
            parts.append(
                f"corpus format is v{declared}, this plugin writes v{CORPUS_FORMAT_VERSION} "
                "— the corpus needs a MIGRATION before newer-format features work; hippo "
                "never migrates automatically — follow the doctor-driven path in "
                "plugin/memory/README.md ('Corpus format versioning')"
            )

        # DRV-2: the derivation is a SEPARATE axis from the shape. A corpus can be format-
        # current and still hold citations produced by an extractor that has since been
        # fixed — which is precisely the state that had no name, and so went unnoticed for
        # 14 minor versions.
        from .provenance import CITATION_DERIVATION_VERSION, read_cite_derivation

        cite = read_cite_derivation(ctx.memory_dir)
        if cite >= CITATION_DERIVATION_VERSION:
            parts.append(f"citation derivation current (v{cite})")
        else:
            status = "warn"
            # DOC-16: NAME the verb. This line used to say "re-derive per memory" and stop —
            # stating a conclusion while never naming the thing that acts on it, which is
            # LIF-4's own complaint one layer up. The remediation loop dead-ended here on
            # both surfaces: the nudge routed to doctor, and doctor routed to nothing.
            parts.append(
                f"citation derivation is v{cite}, this plugin derives "
                f"v{CITATION_DERIVATION_VERSION} — cited_paths in this corpus were produced "
                "by an older extractor (v1 was blind to .json/.tsx/.jsx/.mjs and a leading "
                "./; v2 to extensionless files like Dockerfile), so some memories watch the "
                "wrong file and some are staleness-EXEMPT on an empty cited_paths. Review "
                "with the rederive MCP tool (action='worklist'), apply per memory "
                "(action='one' name=…), then action='stamp' — or in a terminal, "
                "python -m memory.provenance --rederive-worklist / --rederive-one <name> / "
                "--stamp-derivation. It rewrites frontmatter, so it is per-item, "
                "consent-gated and never automatic"
            )

        return {"status": status, "message": "; ".join(parts) + "."}
    except Exception as exc:
        return {"status": "warn", "message": f"format-version check failed: {exc}."}


def check_pack_drift(ctx: DoctorContext) -> Dict[str, str]:
    """Corpus pack memories whose ``pack_version`` lags the shipped pack manifest's ``version``.

    Uses data that ALREADY exists: seeded pack memories carry ``pack``/``pack_version`` in
    frontmatter (TEA-2), and each shipped pack's ``manifest.json`` carries a ``version``. A
    memory whose recorded ``pack_version`` differs from the shipped pack's version drifted from
    the pack it came from — a legible heads-up (re-seeding is agent-gated, not automatic). Skips
    silently when the shipped packs dir isn't locatable (no CLAUDE_PLUGIN_ROOT). Deterministic:
    iterates memory files in sorted order and reports drifted names sorted.
    """
    try:
        packs_dir = os.path.join(ctx.plugin_root, "assets", "packs") if ctx.plugin_root else ""
        if not packs_dir or not os.path.isdir(packs_dir):
            return {"status": "ok", "message": "pack drift: N/A (shipped packs dir not locatable)."}
        shipped: Dict[str, str] = {}
        for name in sorted(os.listdir(packs_dir)):
            man = os.path.join(packs_dir, name, "manifest.json")
            if not os.path.isfile(man):
                continue
            try:
                with open(man, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict) and data.get("pack") and data.get("version") is not None:
                    shipped[str(data["pack"])] = str(data["version"])
            except Exception:
                continue
        # RCH-5: packs installed from EXTERNAL sources record their latest-known version
        # in the corpus lockfile — fold those in so drift covers non-shipped packs too.
        # A partially-updated pack then shows drift on exactly its not-yet-updated
        # members (the correct signal, not noise). Shipped manifests win a name clash.
        try:
            from .packs import _load_lockfile

            for pname, entry in (_load_lockfile(ctx.memory_dir).get("packs") or {}).items():
                if isinstance(entry, dict) and entry.get("version") is not None:
                    shipped.setdefault(str(pname), str(entry["version"]))
        except Exception:
            pass
        drifted: List[str] = []
        for path in _iter_memory_files_safe(ctx.memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    fm = parse_frontmatter(fh.read())
            except Exception:
                continue
            meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
            pack = fm.get("pack") or (meta or {}).get("pack")
            pver = fm.get("pack_version") or (meta or {}).get("pack_version")
            if not pack or pver is None:
                continue
            latest = shipped.get(str(pack))
            if latest is not None and str(pver) != latest:
                name = os.path.splitext(os.path.basename(path))[0]
                drifted.append(f"{name} (pack {pack} v{pver} → v{latest})")
        drifted.sort()
        if not drifted:
            return {"status": "ok", "message": "seeded pack memories are at the shipped versions."}
        return {
            "status": "warn",
            "message": f"{len(drifted)} pack memory(ies) lag the shipped pack version: "
            f"{', '.join(drifted)}. Re-seeding is agent-gated — review before overwriting local edits.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"pack-drift check failed: {exc}."}


def check_fill_me(ctx: DoctorContext) -> Dict[str, str]:
    """Unfilled ``<FILL-ME`` template placeholders anywhere in the corpus (ONB-4, ported here).

    A template memory (usually ``user_role.md``) that was never filled in embeds its placeholder
    text into the recall index and (for ``user`` types) floor-loads it every session. Scans EVERY
    corpus file — the memory files AND the MEMORY.md/MEMORY.full.md floor — for the literal
    ``<FILL-ME`` marker and names each hit BY NAME. Doctor never edits these: the content is facts
    about the user only they can supply. Deterministic: files scanned in sorted order.
    """
    try:
        if not os.path.isdir(ctx.memory_dir):
            return {"status": "ok", "message": "no unfilled <FILL-ME templates (no corpus)."}
        hits: List[str] = []
        for name in sorted(os.listdir(ctx.memory_dir)):
            if not name.endswith(".md"):
                continue
            path = os.path.join(ctx.memory_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    if "<FILL-ME" in fh.read():
                        hits.append(name)
            except Exception:
                continue
        if not hits:
            return {"status": "ok", "message": "no unfilled <FILL-ME templates."}
        return {
            "status": "fail",
            "message": f"{len(hits)} file(s) still contain <FILL-ME placeholders: "
            f"{', '.join(hits)}. Edit each and fill in your own details — the next SessionStart "
            "re-indexes automatically. (Placeholder text is otherwise embedded/floor-loaded.)",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"template check failed: {exc}."}


def check_trust(ctx: DoctorContext) -> Dict[str, str]:
    """Corpus trust state (SEC-1) — is this corpus trusted, and the exact command to trust it.

    Recall is GATED: an untrusted (usually freshly-cloned) corpus injects nothing until this
    machine's user consents. Reports the four trust states deterministically and, on the untrusted
    path, prints the exact ``mark_trusted`` command doctor's consent step runs. Doctor never
    auto-trusts here — the interactive review lives in the SKILL prose; this line only reports the
    state and the command.
    """
    try:
        from . import trust

        if trust.trust_all():
            return {
                "status": "ok",
                "message": "corpus trust bypassed (HIPPO_TRUST_ALL) — recall ungated.",
            }
        gate_root = trust.gate_repo_root(ctx.memory_dir, ctx.repo_root)
        if gate_root is None:
            return {
                "status": "ok",
                "message": "corpus trust: N/A (not a git repo — the gate applies only to cloned "
                "git corpora).",
            }
        if trust.is_trusted(gate_root):
            return {"status": "ok", "message": "corpus trusted — recall active."}
        count = trust.corpus_count(ctx.memory_dir)
        return {
            "status": "warn",
            "message": f"corpus UNTRUSTED ({count} memories) — recall injects nothing from it. "
            "Review the memory names, then trust it: "
            f"python -c \"from memory.trust import mark_trusted; mark_trusted('{gate_root}')\" "
            "(or set HIPPO_TRUST_ALL=1 for CI).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"trust check failed: {exc}."}


def check_trust_drift(ctx: DoctorContext) -> Dict[str, str]:
    """SEC-6: content drift since consent — the always-available re-consent surface.

    Three deterministic states for a TRUSTED, gate-applicable corpus:
      - baseline present, no drift  -> ok.
      - baseline present, drift     -> warn, naming the withheld stems (recall's per-file
        quarantine is ACTIVE on them) + the exact re-consent command. The interactive
        review (show what each changed file would inject — the SEC-5 consent sample —
        then take the explicit yes) lives in the doctor SKILL, same as first consent.
      - baseline ABSENT (a legacy, pre-SEC-6 trust record) -> warn: trust works but
        change detection is OFF until a re-consent stamps a fingerprint.
    ok/N-A on the bypassed / non-git / untrusted paths (``check_trust`` owns those).
    """
    try:
        from . import trust

        if trust.trust_all():
            return {"status": "ok", "message": "trust drift: N/A (HIPPO_TRUST_ALL bypass)."}
        gate_root = trust.gate_repo_root(ctx.memory_dir, ctx.repo_root)
        if gate_root is None:
            return {"status": "ok", "message": "trust drift: N/A (not a git corpus)."}
        if not trust.is_trusted(gate_root):
            return {
                "status": "ok",
                "message": "trust drift: N/A (corpus untrusted — see the trust line).",
            }
        drift = trust.untrusted_changes(gate_root, ctx.memory_dir)
        if not drift.get("baseline"):
            return {
                "status": "warn",
                "message": "trust record has NO content fingerprint (pre-SEC-6 consent) — "
                "recall cannot detect upstream changes to this corpus. Re-consent to stamp "
                "one: python -c \"from memory.trust import mark_trusted; "
                f"mark_trusted('{gate_root}', memory_dir='{ctx.memory_dir}')\"",
            }
        changed, added = drift.get("changed") or [], drift.get("added") or []
        if not changed and not added:
            return {
                "status": "ok",
                "message": "corpus content matches its consent-time fingerprint.",
            }
        names = ", ".join(changed + [f"{n} (new)" for n in added])
        return {
            "status": "warn",
            "message": f"{len(changed)} changed / {len(added)} new memory file(s) since "
            f"consent — recall is WITHHOLDING them: {names}. Review what each would inject "
            "(the consent sample shows descriptions), then re-consent: "
            "python -c \"from memory.trust import mark_trusted; "
            f"mark_trusted('{gate_root}', memory_dir='{ctx.memory_dir}')\"",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"trust-drift check failed: {exc}."}


def check_secrets(ctx: DoctorContext) -> Dict[str, str]:
    """Corpus-wide secret-pattern sweep (SEC-2) — import and call the factored-out detector.

    ``secrets.scan_corpus`` is the SAME detector ``new_memory`` warns with at write time (one
    pattern set, no duplicate regexes). Reports each flagged file BY NAME with its warning
    KIND(s) — never the matched secret text — plus the remediation once. Agent-gated: doctor
    names the files; a human reviews and triggers any purge. Deterministic: ``scan_corpus`` walks
    files in sorted order.
    """
    try:
        from .secrets import REMEDIATION, scan_corpus

        findings = scan_corpus(ctx.memory_dir)
        if not findings:
            return {"status": "ok", "message": "no secret-looking content in the corpus."}
        parts = [f"{f['file']}: {'; '.join(f['warnings'])}" for f in findings]
        return {
            "status": "warn",
            "message": f"{len(findings)} file(s) contain secret-looking content — "
            f"{' | '.join(parts)}. {REMEDIATION}.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"secret scan failed: {exc}."}


def check_committed_usage_privacy(ctx: DoctorContext) -> Dict[str, str]:
    """SEC-14: TEA-5 committed per-user usage summaries are a privacy tradeoff on a shared remote.

    ``.claude/memory/.usage/<user>.json`` is COMMITTED by design (teammates union it before
    judging coldness), so it excepts the gitignore invariant — a per-user record of which
    memories each person recalls. On a repo with a remote (especially a public host) that record
    is shared with anyone who can read it. Warn when such summaries exist AND a remote is
    configured; stay ``ok`` when there are none, or the repo is local-only (nothing to leak to).
    Read-only; never raises.
    """
    try:
        from .provenance import git_remote_info
        from .telemetry import committed_usage_dir

        usage_dir = committed_usage_dir(ctx.memory_dir)
        summaries = (
            [f for f in os.listdir(usage_dir) if f.endswith(".json")]
            if os.path.isdir(usage_dir)
            else []
        )
        if not summaries:
            return {"status": "ok", "message": "no committed usage summaries (TEA-5 opt-in unused)."}
        remote = git_remote_info(ctx.repo_root)
        if not remote["url"]:
            return {
                "status": "ok",
                "message": f"{len(summaries)} committed usage summary(ies) present; repo is "
                "local-only (no remote), so recall patterns are not shared.",
            }
        where = "a PUBLIC-host remote" if remote["public_host"] else "a remote"
        return {
            "status": "warn",
            "message": f"{len(summaries)} committed per-user usage summary(ies) in "
            f".claude/memory/.usage/ on {where} ({remote['url']}) — recall patterns (memory "
            "names + counts) are shared with anyone who can read it. Remove .claude/memory/.usage/ "
            "if unintended (TEA-5/SEC-14).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"committed-usage privacy check failed: {exc}."}


def check_dream_ledger(ctx: DoctorContext) -> Dict[str, str]:
    """DRM-2: the corpus's on-disk ``dream: … edge=`` stamps must reconcile with the ledger.

    Every auto-applied dream edge leaves BOTH an inline stamp and an ACTIVE
    ``dream-ledger.jsonl`` line — grep-reconcilable by design. A stamp with no active
    ledger line (hand-copied? ledger truncated?) or an active line with no stamp (stamp
    hand-deleted instead of ``dream --undo``) means the audit record and the corpus
    disagree — a loud ``fail``, per the roadmap's acceptance criterion (a silent mismatch
    would defeat the reversibility story). Quiet ok when /dream has never applied here.
    """
    try:
        import re as _re

        from .dream import read_apply_ledger
        from .provenance import _iter_memory_files

        on_disk: set = set()
        for path in _iter_memory_files(ctx.memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    for line in fh:
                        if "<!-- dream:" in line:
                            m = _re.search(r"edge=([\w-]+)", line)
                            if m:
                                on_disk.add(m.group(1))
            except Exception:
                continue
        active = {
            e.get("edge_id")
            for e in read_apply_ledger(ctx.memory_dir)
            if e.get("state") == "active"
        }
        if not on_disk and not active:
            return {"status": "ok", "message": "no dream edges applied (nothing to reconcile)."}
        orphans = sorted(on_disk - active)
        ghosts = sorted(active - on_disk)
        if not orphans and not ghosts:
            return {
                "status": "ok",
                "message": f"{len(active)} dream edge stamp(s) reconcile with dream-ledger.jsonl.",
            }
        parts = []
        if orphans:
            parts.append(
                f"{len(orphans)} on-disk stamp(s) with no ACTIVE ledger line: {', '.join(orphans[:5])}"
            )
        if ghosts:
            parts.append(
                f"{len(ghosts)} active ledger edge(s) with no on-disk stamp: {', '.join(ghosts[:5])}"
            )
        return {
            "status": "fail",
            "message": "dream stamp/ledger MISMATCH — " + "; ".join(parts) + ". Reconcile "
            "via `python -m memory.dream --log` (+ --undo for stray edges) or git history; "
            "never hand-edit stamped lines.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"dream-ledger check failed: {exc}."}


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


# --------------------------------------------------------------------------- #
# RET-3: non-English corpus served by the English default model
# --------------------------------------------------------------------------- #
# Codepoint ranges for "Latin script" alphabetic characters — Basic Latin + Latin-1 Supplement
# + Latin Extended-A/B, which together cover English plus the accented Latin of French,
# German, Spanish, Portuguese, Vietnamese (base letters), etc. Anything alphabetic OUTSIDE
# these ranges (Cyrillic, CJK, Greek, Arabic, Devanagari, ...) counts as "non-Latin" for this
# heuristic. Deliberately coarse (not a full script-detection library) — this is a doctor
# HINT, not a certified language classifier; it only needs to catch the obvious case (a corpus
# that reads as visibly non-English) without false-positiving on a mostly-English corpus that
# happens to contain a few French loanwords or names.
_LATIN_ALPHA_RANGES = (
    (0x0041, 0x005A),  # A-Z
    (0x0061, 0x007A),  # a-z
    (0x00C0, 0x00FF),  # Latin-1 Supplement letters (À-ÿ, excl. ×/÷ which aren't alphabetic anyway)
    (0x0100, 0x024F),  # Latin Extended-A/B (accented forms used by many European languages)
)
# Below this many sampled alphabetic chars, the sample is too small to call a verdict either
# way (a corpus of one or two short-description memories) — stay silent rather than guess.
_NON_ENGLISH_MIN_ALPHA_SAMPLE = 40
# ">30%" per the roadmap's acceptance criterion — a visible fraction, not a strict majority (a
# corpus that's mostly English with scattered non-Latin proper nouns should NOT fire this).
_NON_ENGLISH_ALPHA_FRACTION = 0.30


def _is_latin_alpha(ch: str) -> bool:
    return any(lo <= ord(ch) <= hi for lo, hi in _LATIN_ALPHA_RANGES)


def check_non_english_corpus(ctx: DoctorContext) -> Dict[str, str]:
    """Warn when the corpus reads as visibly non-English but the model is the English default.

    RET-3 / OQ-4: the release keeps ``bge-small-en-v1.5`` as the hardcoded default (an explicit
    opt-in — ``--multilingual`` — switches it), so a corpus written mostly in, say, Japanese or
    Russian would otherwise get dense embeddings from a model never trained on that language,
    with NO signal anywhere that a better-fitting preset exists. This samples every memory's
    ``description:`` (the same text the index embeds — reusing ``extract_description`` so this
    check can never disagree with what actually gets indexed) and counts alphabetic characters
    that fall OUTSIDE the Latin-script ranges. If more than
    ``_NON_ENGLISH_ALPHA_FRACTION`` of a large-enough alphabetic sample is non-Latin AND the
    manifest's recorded model is still ``ENGLISH_DEFAULT_MODEL``, this warns and names the
    `--multilingual` bootstrap preset. Silent (``ok``) on an empty/tiny corpus (nothing to
    sample, or the sample is below ``_NON_ENGLISH_MIN_ALPHA_SAMPLE``), when the model has
    already been switched away from the English default (nothing to suggest), or on any
    unexpected error. Heuristic and best-effort by design — never raises, never blocks.
    """
    try:
        from .build_index import ENGLISH_DEFAULT_MODEL, _load_manifest, default_index_dir, extract_description

        manifest = _load_manifest(default_index_dir(ctx.memory_dir))
        # No index yet, or already using a non-English model -> nothing to suggest here.
        if manifest is None:
            return {"status": "ok", "message": "non-English corpus check: N/A (no index built yet)."}
        manifest_model = manifest.get("model")
        if manifest_model and manifest_model != ENGLISH_DEFAULT_MODEL:
            return {
                "status": "ok",
                "message": f"non-English corpus check: N/A (model is already '{manifest_model}', not the English default).",
            }

        total_alpha = 0
        non_latin_alpha = 0
        for path in _iter_memory_files_safe(ctx.memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    desc = extract_description(fh.read())
            except Exception:
                continue
            for ch in desc:
                if not ch.isalpha():
                    continue
                total_alpha += 1
                if not _is_latin_alpha(ch):
                    non_latin_alpha += 1

        if total_alpha < _NON_ENGLISH_MIN_ALPHA_SAMPLE:
            return {
                "status": "ok",
                "message": f"non-English corpus check: N/A (only {total_alpha} alphabetic chars sampled, "
                f"below the {_NON_ENGLISH_MIN_ALPHA_SAMPLE}-char floor for this heuristic).",
            }

        fraction = non_latin_alpha / total_alpha
        if fraction <= _NON_ENGLISH_ALPHA_FRACTION:
            return {
                "status": "ok",
                "message": f"corpus reads as Latin-script/English ({fraction:.0%} non-Latin alphabetic chars).",
            }
        return {
            "status": "warn",
            "message": f"corpus is {fraction:.0%} non-Latin-alphabetic but is served by the English "
            "default embedding model — consider `/hippo:bootstrap --multilingual` (switches to "
            "a multilingual model; forces a one-time full re-embed of the corpus).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"non-English corpus check failed: {exc}."}


def check_mcp_launch(ctx: DoctorContext) -> Dict[str, str]:
    """INT-8: the stdio MCP server (INT-2) actually starts — ``bin/hippo mcp`` launch health.

    The MCP server closes the two recall gaps the once-per-prompt hook can't (mid-turn retrieval
    and subagent memory), but nothing verified it can START until a live client tried and failed.
    Exercises the REAL ``serve()`` read loop in-process with a canned ``initialize`` request (no
    subprocess, no network) and confirms a well-formed handshake comes back, then reports the
    tool/resource surface and the per-message bound (SEC-13). ``serve()`` pins the fastembed
    cache path + sets offline env defaults, so the relevant keys are snapshotted and restored —
    a diagnostic never mutates the caller's environment. Warn-only: a failure means a genuine
    wiring break in a stdlib-only server, not a broken corpus.
    """
    try:
        import io

        from . import mcp_server as M

        saved = {
            k: os.environ.get(k)
            for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "FASTEMBED_CACHE_PATH")
        }
        out = io.StringIO()
        try:
            req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            M.serve(io.StringIO(req + "\n"), out)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        lines = [ln for ln in out.getvalue().splitlines() if ln.strip()]
        resp = json.loads(lines[0]) if lines else {}
        info = (resp.get("result") or {}).get("serverInfo") or {}
        if not info.get("name"):
            return {
                "status": "warn",
                "message": "MCP server did not return a valid initialize handshake — "
                "`bin/hippo mcp` may be broken (run it and send an initialize request to debug).",
            }
        return {
            "status": "ok",
            "message": f"MCP server starts (`bin/hippo mcp`) — {info.get('name')} "
            f"v{info.get('version', '?')}, {len(M._TOOLS)} tool(s) / {len(M._RESOURCES)} "
            f"resource(s), per-message cap {M._MAX_MESSAGE_CHARS} bytes.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"MCP launch check failed: {exc}."}


def check_stale_memobot_env(ctx: DoctorContext) -> Dict[str, str]:
    """DOC-8: flag any lingering ``MEMOBOT_*`` env var — the pre-v0.4.0 name, now ignored.

    The rename to ``HIPPO_*`` was a clean break (one-canonical-name invariant — no alias shims,
    no fallback reads of the old prefix), which means a developer's stale shell profile or CI
    secret still exporting e.g. ``MEMOBOT_TRUST_ALL`` is now SILENTLY inert: every module only
    ever reads ``HIPPO_*``, so the old var has no effect and nothing else would ever say so. That
    silent-fallback path needs a legible signal somewhere — this is it. Scans the live environment
    (not the corpus) for any key starting with ``MEMOBOT_`` and warns, by name, that it is ignored
    and what to rename it to. Sorted so multiple stale vars report in a stable order. Warn-only —
    a leftover env var is a footgun, not a broken install, so this never fails the run.
    """
    try:
        stale = sorted(k for k in os.environ if k.startswith("MEMOBOT_"))
        if not stale:
            return {"status": "ok", "message": "no stale MEMOBOT_* env vars in the environment."}
        parts = []
        for key in stale:
            suffix = key[len("MEMOBOT_") :]
            parts.append(f"{key} is ignored since v0.4.0 — use HIPPO_{suffix}")
        return {"status": "warn", "message": "; ".join(parts) + "."}
    except Exception as exc:
        return {"status": "warn", "message": f"stale-env check failed: {exc}."}


def check_projects_registry(ctx: DoctorContext) -> Dict[str, str]:
    """RCH-11: machine-level projects-registry hygiene — the file behind ``--all-projects``.

    ``registered_projects()`` read-time-skips entries whose ``memory_dir`` vanished (RCH-4,
    deliberately never auto-pruned), which keeps recall correct while the FILE quietly
    accumulates junk rows — scratch/test sessions that ran a real ``init`` on tmp-dir clones
    are the observed source. Machine-level like the bootstrap check (the registry is a
    ``~/.claude`` sibling of the trust file, not per-corpus). Warn-only — dead rows are a
    footgun, not a broken install — and the message names the count and the hygiene verbs.
    """
    try:
        from .registry import registry_census

        census = registry_census()
        entries = census["entries"]
        if not entries:
            return {
                "status": "ok",
                "message": "projects registry: nothing registered (populated by /hippo:init).",
            }
        total = len(entries)
        dead = [e for e in entries if not e["live"]]
        if not dead:
            return {
                "status": "ok",
                "message": f"projects registry: {total} live entr"
                + ("y" if total == 1 else "ies")
                + ", none dead.",
            }
        volatile = sum(1 for e in dead if e["volatile"])
        repairable = sum(1 for e in dead if e["repairable"])
        msg = (
            f"projects registry: {len(dead)} dead entr"
            + ("y" if len(dead) == 1 else "ies")
            + f" of {total} ({volatile} temp-rooted) — report: python -m memory.registry "
            "(then --prune-dead to clear the temp-rooted, --drop <root> for one entry)"
        )
        if repairable:
            msg += (
                f"; {repairable} of them have a live corpus at the canonical "
                "<root>/.claude/memory — re-run /hippo:init there to re-register"
            )
        return {"status": "warn", "message": msg + "."}
    except Exception as exc:
        return {"status": "warn", "message": f"projects-registry check failed: {exc}."}


# (label, check_fn) in a FIXED order — the source of the deterministic output. New checks append
# here; the order is never sorted-by-name or set-derived, so the printed sequence is stable.
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


def check_plugin_version(ctx: DoctorContext) -> Dict[str, str]:
    """DOC-7: installed plugin version vs the version the venv was bootstrapped for (with COR-11).

    Version lives in ``.claude-plugin/plugin.json``; the bootstrap sentinel records which version
    the venv was provisioned for (``plugin_version``, added in v0.6.0). After a plugin update the
    code swaps but the venv does not, so a delta here is the signal to re-bootstrap. COR-11 covers
    the DEPS side (requirements hash); this covers the VERSION side. Read-only; never raises.
    """
    try:
        installed = None
        pj = os.path.join(ctx.plugin_root, ".claude-plugin", "plugin.json")
        try:
            with open(pj, encoding="utf-8") as fh:
                installed = json.load(fh).get("version")
        except Exception:
            installed = None
        if not installed:
            return {"status": "warn", "message": "plugin version unreadable (plugin.json missing or unparseable)."}
        if not ctx.plugin_data:
            return {"status": "ok", "message": f"plugin v{installed} installed (bootstrap state unknown — CLAUDE_PLUGIN_DATA unset)."}
        sentinel = os.path.join(ctx.plugin_data, ".bootstrap-sentinel")
        if not os.path.exists(sentinel):
            return {"status": "ok", "message": f"plugin v{installed} installed — not bootstrapped yet (see the bootstrap check)."}
        try:
            with open(sentinel, encoding="utf-8") as fh:
                bootstrapped = json.load(fh).get("plugin_version")
        except Exception:
            bootstrapped = None
        if not bootstrapped:
            return {
                "status": "warn",
                "message": f"plugin v{installed} installed, but the bootstrap sentinel predates "
                "version tracking — run /hippo:bootstrap to record it.",
            }
        if bootstrapped == installed:
            return {"status": "ok", "message": f"plugin v{installed} installed and bootstrapped — in sync."}
        return {
            "status": "warn",
            "message": f"version delta: plugin v{installed} installed but the venv was bootstrapped "
            f"for v{bootstrapped} — run /hippo:bootstrap (check the CHANGELOG's 're-bootstrap' flag "
            "for whether deps changed).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"plugin-version check failed: {exc}."}


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
    ("injection_precision", check_injection_precision),
    ("rules_conflicts", check_rules_conflicts),
    ("rules_plane_rot", check_rules_plane_rot),
    ("rules_source", check_rules_source),
    ("format_version", check_format_version),
    ("empty_baselines", check_empty_baselines),  # COR-10: the heal moved off the hook
    ("pack_drift", check_pack_drift),
    ("fill_me", check_fill_me),
    ("secrets", check_secrets),
    ("link_density", check_link_density),
    ("dream_ledger", check_dream_ledger),  # DRM-2: on-disk dream stamps ↔ dream-ledger.jsonl reconcile
    ("non_english_corpus", check_non_english_corpus),
    ("mcp_launch", check_mcp_launch),  # INT-8: the stdio MCP server (bin/hippo mcp) actually starts
    ("committed_usage_privacy", check_committed_usage_privacy),  # SEC-14: TEA-5 usage on a shared remote
    ("projects_registry", check_projects_registry),  # RCH-11: dead-row hygiene, machine-level
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
    """CLI entry point — resolve dirs, run all checks, print the report. Always returns 0."""
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
