"""INT-10: the /hippo:init flow as ONE tested engine function — ``init_project``.

The init skill's steps have always lived as bash-in-prose (SKILL.md), which only a surface
with typed /hippo:* commands can drive. The Claude desktop app runs plugin hooks, skills,
and MCP servers but has no typed-command surface, so the mechanical core of init is
factored here for the MCP ``init`` tool (the DOC-4 shape doctor already uses: a
deterministic engine, thin presentation on top). The SKILL keeps the interactive extras —
the starter-pack menu, the ONB-10 ``user_role.md`` interview — this module deliberately
does NOT reproduce: a fresh seed here is core-pack-only (the same default an unanswered
skill menu yields), and the tool's response nudges the conversational fill afterward.

One consent rule distinguishes this from the terminal skill, and it is load-bearing
(SEC-1): typing ``/hippo:init`` is the user's own explicit act, so the skill may mark an
EXISTING corpus trusted ("re-running init against it IS the review"). An MCP init is
model-invoked — a session influenced by a malicious repo must never be able to silently
trust a foreign corpus by "helpfully" running setup. So:

  - a corpus THIS call creates from nothing is marked trusted (``origin="init"``) — its
    entire content is the plugin's own shipped starter files; there is nothing foreign to
    review, and the SEC-6 fingerprint is stamped over exactly those bytes;
  - a PRE-EXISTING corpus is NEVER auto-trusted here. The caller reports the untrusted
    state and routes consent through the ``trust_corpus`` tool's review→confirm flow.

Everything else mirrors the skill's semantics: idempotent, never overwrites an existing
memory file, never creates a ``.gitignore`` from scratch, never commits. Pre-bootstrap
(bare python3) the index build degrades to BM25-only exactly as the hooks do.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

# The three derived/private paths init keeps out of git (the skill's step 5 list).
GITIGNORE_ENTRIES = (
    ".claude/.memory-index/",
    ".claude/.memory-telemetry/",
    ".claude/memory.local/",
)


def _plugin_root() -> str:
    return os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )


def _core_pack_files(plugin_root: str) -> List[str]:
    """The core pack's memory filenames, from its manifest (never hardcoded here)."""
    manifest_path = os.path.join(plugin_root, "assets", "packs", "core", "manifest.json")
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        return [
            str(m["file"])
            for m in manifest.get("memories") or []
            if isinstance(m, dict) and str(m.get("file", "")).endswith(".md")
        ]
    except Exception:
        return []


def _copy_if_absent(src: str, dst: str) -> Optional[str]:
    """Copy ``src`` to ``dst`` unless ``dst`` exists. Returns "seeded"/"already_present",
    or None when the source itself is missing (reported upstream as a warning)."""
    if os.path.exists(dst):
        return "already_present"
    if not os.path.isfile(src):
        return None
    with open(src, "rb") as fh:
        data = fh.read()
    with open(dst, "wb") as fh:
        fh.write(data)
    return "seeded"


def _has_existing_corpus(memory_dir: str) -> bool:
    """ONB-5's existing-corpus preflight, hardened: a MEMORY.md floor OR any memory file
    counts. (The skill keys on MEMORY.md alone; a floor-less directory of memory files
    must still take the no-seed, no-auto-trust path — SEC-1 fails toward review.)"""
    if os.path.isfile(os.path.join(memory_dir, "MEMORY.md")):
        return True
    try:
        from .provenance import _iter_memory_files

        return next(_iter_memory_files(memory_dir), None) is not None
    except Exception:
        return False


def _patch_gitignore(repo_root: str) -> str:
    """Append the derived-path entries to an EXISTING .gitignore; never create one
    (a repo with zero .gitignore may be intentional — the skill asks first, so a
    non-interactive engine just reports)."""
    path = os.path.join(repo_root, ".gitignore")
    if not os.path.isfile(path):
        return "absent_not_created"
    try:
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        present = {ln.strip() for ln in content.splitlines()}
        missing = [e for e in GITIGNORE_ENTRIES if e not in present and e.rstrip("/") not in present]
        if not missing:
            return "already_covered"
        with open(path, "a", encoding="utf-8") as fh:
            if content and not content.endswith("\n"):
                fh.write("\n")
            fh.write("\n".join(missing) + "\n")
        return "patched"
    except Exception:
        return "patch_failed"


def init_project(claude_projects_dir: Optional[str] = None) -> Dict[str, object]:
    """Run the mechanical init flow against the resolved corpus. Returns a result dict;
    never raises (per-step failures degrade to reported statuses).

    ``claude_projects_dir`` overrides the symlink base (hermetic tests); None uses the
    real ``~/.claude/projects``.
    """
    from . import trust
    from .build_index import build_index, default_index_dir
    from .provenance import (
        CORPUS_FORMAT_VERSION,
        create_project_symlink,
        ensure_self_ignoring_dir,
        git_root,
        resolve_dirs,
    )
    from .registry import register_project

    plugin_root = _plugin_root()
    memory_dir, repo_root = resolve_dirs()
    is_git = git_root(repo_root) is not None
    result: Dict[str, object] = {
        "memory_dir": memory_dir,
        "repo_root": repo_root,
        "git": is_git,
        "seeded": [],
        "warnings": [],
    }

    existing = _has_existing_corpus(memory_dir)
    result["mode"] = "existing" if existing else "fresh"
    try:
        os.makedirs(memory_dir, exist_ok=True)
    except Exception as exc:
        result["warnings"].append(f"could not create {memory_dir}: {exc}")
        return result

    if not existing:
        # Steps 1-2b (fresh only): core pack, MEMORY.md skeleton, format marker. The
        # shipped skeleton already carries the core pack's floor pointers, so a
        # core-only seed needs no pointer appends.
        for fname in _core_pack_files(plugin_root):
            status = _copy_if_absent(
                os.path.join(plugin_root, "assets", "packs", "core", fname),
                os.path.join(memory_dir, fname),
            )
            if status == "seeded":
                result["seeded"].append(fname)
            elif status is None:
                result["warnings"].append(f"core pack file missing from plugin: {fname}")
        skel = _copy_if_absent(
            os.path.join(plugin_root, "assets", "MEMORY.skeleton.md"),
            os.path.join(memory_dir, "MEMORY.md"),
        )
        if skel == "seeded":
            result["seeded"].append("MEMORY.md")
        elif skel is None:
            result["warnings"].append("MEMORY.skeleton.md missing from plugin bundle")
        fmt_path = os.path.join(memory_dir, ".format")
        if not os.path.exists(fmt_path):
            with open(fmt_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"corpus_format": CORPUS_FORMAT_VERSION}) + "\n")
            result["format_marker"] = "stamped"
        else:
            result["format_marker"] = "already_present"
    else:
        # An existing corpus is never stamped with a format it wasn't migrated to —
        # doctor's format check (COR-7) owns that comparison.
        result["format_marker"] = "skipped_existing_corpus"

    # Step 2c (both paths): CONVENTIONS.md backfill, idempotent.
    conv = _copy_if_absent(
        os.path.join(plugin_root, "assets", "CONVENTIONS.md"),
        os.path.join(memory_dir, "CONVENTIONS.md"),
    )
    result["conventions"] = conv or "source_missing"

    # Step 3: the cross-machine symlink (SHP-5 encoding, ONE tested helper).
    result["symlink"] = create_project_symlink(
        repo_root, memory_dir, claude_projects_dir=claude_projects_dir
    )

    # Step 4: the recall index. allow_download=False — init is offline by contract (the
    # model warm belongs to bootstrap alone); pre-bootstrap this builds BM25-only.
    try:
        manifest = build_index(memory_dir, default_index_dir(memory_dir), allow_download=False)
        result["index"] = {
            "count": manifest.get("count"),
            "dense_ready": bool(manifest.get("dense_ready")),
        }
    except Exception as exc:
        result["index"] = {"error": str(exc)}

    # Step 4b — trust + registration. See the module docstring: fresh-created → trusted
    # (origin="init", SEC-6 fingerprint over the just-seeded bytes); pre-existing → NEVER
    # auto-trusted from a model-invoked surface; consent routes through trust_corpus.
    gate_root = trust.gate_repo_root(memory_dir, repo_root)
    if trust.trust_all():
        result["trust"] = {"status": "bypassed"}
    elif gate_root is None:
        result["trust"] = {"status": "inapplicable"}
    elif trust.is_trusted(gate_root):
        result["trust"] = {"status": "already_trusted"}
    elif not existing:
        ok = trust.mark_trusted(gate_root, memory_dir=memory_dir, origin="init")
        result["trust"] = {"status": "marked_init" if ok else "write_failed"}
    else:
        result["trust"] = {"status": "untrusted_needs_review"}
    result["registered"] = register_project(repo_root, memory_dir)

    # Step 5 + 5b (git repo only): .gitignore patch + the self-ignoring private tier.
    if is_git:
        result["gitignore"] = _patch_gitignore(repo_root)
        try:
            ensure_self_ignoring_dir(os.path.join(os.path.dirname(memory_dir), "memory.local"))
            result["private_tier"] = "ensured"
        except Exception as exc:
            result["private_tier"] = f"failed: {exc}"
    else:
        result["gitignore"] = "skipped_non_git"
        result["private_tier"] = "skipped_non_git"

    # The step-6 user_role warning input: is the template still unfilled?
    result["user_role_unfilled"] = _user_role_unfilled(memory_dir)
    return result


def _user_role_unfilled(memory_dir: str) -> bool:
    try:
        with open(os.path.join(memory_dir, "user_role.md"), encoding="utf-8") as fh:
            return "<FILL-ME" in fh.read()
    except Exception:
        return False
