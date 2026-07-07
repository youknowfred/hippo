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


def check_format_version(ctx: DoctorContext) -> Dict[str, str]:
    """Persisted index ``schema_version`` vs the running module's ``SCHEMA_VERSION``.

    A corpus indexed by an older plugin carries an older on-disk schema; the next rebuild
    upgrades it. This is a legible heads-up, not an error — recall degrades gracefully across
    versions. Uses data that ALREADY exists (the manifest's ``schema_version``); no new
    instrumentation. Silent-clean when the versions match or nothing is built.
    """
    try:
        from .build_index import SCHEMA_VERSION, _load_manifest, default_index_dir

        manifest = _load_manifest(default_index_dir(ctx.memory_dir))
        if manifest is None:
            return {"status": "ok", "message": "no index built yet — nothing to version-check."}
        on_disk = manifest.get("schema_version")
        if on_disk == SCHEMA_VERSION:
            return {"status": "ok", "message": f"index format version current (v{SCHEMA_VERSION})."}
        return {
            "status": "warn",
            "message": f"index format version is v{on_disk}, this plugin writes v{SCHEMA_VERSION} "
            "— the next rebuild upgrades it (recall degrades gracefully meanwhile).",
        }
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
                "message": "corpus trust bypassed (MEMOBOT_TRUST_ALL) — recall ungated.",
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
            "(or set MEMOBOT_TRUST_ALL=1 for CI).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"trust check failed: {exc}."}


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


# (label, check_fn) in a FIXED order — the source of the deterministic output. New checks append
# here; the order is never sorted-by-name or set-derived, so the printed sequence is stable.
CHECKS: List[Tuple[str, Callable[[DoctorContext], Dict[str, str]]]] = [
    ("bootstrap", check_bootstrap),
    ("venv", check_venv),
    ("corpus", check_corpus_exists),
    ("symlink", check_symlink),
    ("resolution", check_corpus_resolution),
    ("git_mode", check_git_mode),
    ("trust", check_trust),
    ("integrity", check_integrity),
    ("index_corruption", check_index_corruption),
    ("index_count", check_index_count),
    ("format_version", check_format_version),
    ("pack_drift", check_pack_drift),
    ("fill_me", check_fill_me),
    ("secrets", check_secrets),
    ("link_density", check_link_density),
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
