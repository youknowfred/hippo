"""Install/environment/index checks for the deterministic doctor engine — decomposed out of
``doctor.py`` (DOC-4), which keeps the ordered check registry, the engine, and the CLI.

Home of ``DoctorContext`` — the shared resolved-inputs context every check sibling imports —
plus bootstrap/venv/plugin-version (COR-11, DOC-7), corpus/symlink/native-coexistence (SHP-5,
INT-4), git mode (SHP-4), staleness baselines (COR-10), index integrity (QUA-5), MCP launch
(INT-8), and env/registry hygiene (DOC-8, RCH-11). Checks degrade to ``warn``, never raise.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

from .provenance import check_project_symlink, git_root, walk_up_for_memory_dir


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


def check_machine_state(ctx: DoctorContext) -> Dict[str, str]:
    """HYG-3: machine-state rot beyond the projects registry — warn on DEAD only.

    One line summarizing the census classes ``check_projects_registry`` does NOT
    already cover: dead trust rows, dangling memory symlinks, gone-path scheduler
    artifacts. Temp-rooted-LIVE rows never warn and the volatile split stays in the
    census command's own report — warn-on-dead-only keeps this line from becoming
    wallpaper in a section that already carries chronic warns. Sleep inherits the line
    free through its doctor section (SLP-1's reuse rule — no forked text) and machine
    rot is not per-session news, so there is deliberately NO SessionStart producer.
    Honest-surface bound: a moved venv/repo kills the scheduled 07:30 run before hippo
    starts, so the morning report cannot carry this warn for its own dead schedule —
    on-demand doctor is the live surface for that class. Read-only; never raises.
    """
    try:
        from .machine_census import scheduler_census, symlink_farm_census, trust_census

        farm = symlink_farm_census()
        dangling = farm["dangling"] + farm["dangling_temp_rooted"]
        dead_trust = trust_census()["dead"]
        stale_sched = scheduler_census()["stale"]
        if not (dangling or dead_trust or stale_sched):
            return {
                "status": "ok",
                "message": "machine state: no dead trust rows, dangling memory symlinks, "
                "or stale scheduler artifacts (full census: python -m memory.machine_census).",
            }
        parts = []
        if dangling:
            parts.append(f"{dangling} dangling memory symlink" + ("" if dangling == 1 else "s"))
        if dead_trust:
            parts.append(f"{dead_trust} dead trust row" + ("" if dead_trust == 1 else "s"))
        if stale_sched:
            parts.append(
                f"{stale_sched} stale scheduler artifact" + ("" if stale_sched == 1 else "s")
            )
        msg = "machine state: " + ", ".join(parts) + " — census: python -m memory.machine_census"
        if farm["dangling_temp_rooted"]:
            msg += " (then --prune-dangling for the temp-rooted batch)"
        return {"status": "warn", "message": msg + "."}
    except Exception as exc:
        return {"status": "warn", "message": f"machine-state check failed: {exc}."}
