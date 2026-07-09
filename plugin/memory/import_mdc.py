"""Cursor ``.mdc`` migration adapter (RCH-2) — the ``/hippo:import`` on-ramp.

Adopters don't start from zero: years of accumulated rules live in other tools' formats,
and the closest structural match is Cursor's ``.cursor/rules/*.mdc`` — YAML-ish
frontmatter (``description`` / ``globs`` / ``alwaysApply``) over a markdown body, where
``globs`` maps almost perfectly onto hippo's cited-path provenance. This module turns
each ``.mdc`` into ONE per-item-confirmed, secret-linted, dup-gated memory candidate.

ADAPTER SHAPE (so claude-mem / Mem0 / sectioned-CLAUDE.md drop in later): an adapter is a
(discover, parse) pair — ``mdc_rule_files`` + ``parse_mdc`` here — feeding the SHARED
tail: ``import_candidates`` (read-only report) and ``import_one_candidate`` (one write
through the shipped ``check_candidate`` → secret-lint → ``write_memory`` path). A future
adapter adds its own discover/parse pair and reuses the tail unchanged.

TOLERANT PARSING IS THE ADAPTER'S JOB: real Cursor frontmatter is frequently NOT valid
YAML — the dominant shape ``globs: **/*.ts`` starts a value with ``*`` (a YAML alias), so
``yaml.safe_load`` fails and ``provenance.parse_frontmatter`` returns ``{}`` for the whole
block, description included. ``parse_mdc`` therefore tries YAML first (full fidelity for
well-formed files) and falls back to a line-based scan of the frontmatter for whatever
YAML could not deliver. Comma-separated glob strings (Cursor's own inline convention)
are split either way.

TRUST POSTURE — foreign input is UNTRUSTED: unlike ``write_memory``'s warn-after-write
secret lint, an import candidate is linted BEFORE the write and a non-empty finding set
HOLDS it (never written, never recallable) until the source file is cleaned. The dup gate
(``check_candidate``) also runs BEFORE the write — a duplicate never becomes a file — and
re-imports ride ``write_memory``'s exclusive-create refusal (idempotence).

CITED-PATHS ROUTE (verified): ``write_memory`` does not take cited_paths — they are
derived from body path tokens by the provenance backfill after the write. Glob-resolved
concrete paths are therefore appended to the BODY as one ``Applies to: ...`` line
(bounded), and the shipped backfill stamps ``cited_paths``/``source_commit`` from it —
100% the shipped write path. Glob RESOLUTION reuses ``rules_plane``'s exact match
pipeline (``_expand_braces`` → ``_glob_to_re`` over ``_repo_paths_for_globs``'s
tracked∪untracked-unignored universe) — never a re-implementation.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional

# One "Applies to:" line stays legible: cap the paths listed (the cited-path backfill
# reads the same line, so the cap also bounds cited_paths per imported memory).
_MAX_APPLIES_PATHS = 20

_RULES_SUBDIR = os.path.join(".cursor", "rules")


def mdc_rule_files(repo_root: str) -> List[str]:
    """Sorted absolute paths of ``.cursor/rules/*.mdc``. ``[]`` when none; never raises."""
    try:
        rules_dir = os.path.join(repo_root, _RULES_SUBDIR)
        return sorted(
            os.path.join(rules_dir, f)
            for f in os.listdir(rules_dir)
            if f.endswith(".mdc") and os.path.isfile(os.path.join(rules_dir, f))
        )
    except Exception:
        return []


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1].strip()
    return s


def _split_globs(raw: str) -> List[str]:
    """Split one inline globs value: comma-separated, optionally flow-list bracketed."""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [g for g in (_unquote(part) for part in raw.split(",")) if g]


def parse_mdc(text: str) -> dict:
    """``{"description", "globs", "always_apply", "body"}`` from one ``.mdc``'s text.

    YAML-first for well-formed files; line-based fallback for the invalid-YAML reality
    (bare-``*`` globs). Missing pieces come back empty/False — never raises.
    """
    out = {"description": "", "globs": [], "always_apply": False, "body": text}
    try:
        from .provenance import parse_frontmatter, split_frontmatter

        fm_lines, body = split_frontmatter(text)
        if fm_lines is None:
            return out
        out["body"] = body.lstrip("\n")

        fm = parse_frontmatter(text)  # {} when the frontmatter is not valid YAML
        desc = fm.get("description")
        if isinstance(desc, str) and desc.strip():
            out["description"] = desc.strip()
        raw_globs = fm.get("globs")
        if isinstance(raw_globs, str):
            out["globs"] = _split_globs(raw_globs)
        elif isinstance(raw_globs, list):
            out["globs"] = [
                g for item in raw_globs if isinstance(item, str) for g in _split_globs(item)
            ]
        if isinstance(fm.get("alwaysApply"), bool):
            out["always_apply"] = fm["alwaysApply"]

        # Line-based fallback fills whatever YAML could not parse out of the block.
        i = 0
        while i < len(fm_lines):
            stripped = fm_lines[i].strip()
            if stripped.startswith("description:") and not out["description"]:
                out["description"] = _unquote(stripped[len("description:"):])
            elif stripped.startswith("globs:") and not out["globs"]:
                inline = stripped[len("globs:"):].strip()
                if inline:
                    out["globs"] = _split_globs(inline)
                else:
                    j = i + 1
                    while j < len(fm_lines) and fm_lines[j].strip().startswith("- "):
                        out["globs"].extend(_split_globs(fm_lines[j].strip()[2:]))
                        j += 1
                    i = j - 1
            elif stripped.startswith("alwaysApply:") and not out["always_apply"]:
                out["always_apply"] = (
                    stripped[len("alwaysApply:"):].strip().lower() == "true"
                )
            i += 1
        return out
    except Exception:
        return out


def resolve_globs(repo_root: str, globs: List[str]) -> List[str]:
    """Concrete repo paths the globs scope — ``rules_plane``'s exact match pipeline
    (brace expansion → anchored glob regex over the tracked∪untracked-unignored
    universe). Sorted; ``[]`` when nothing matches or on any failure."""
    if not globs:
        return []
    try:
        from .provenance import build_repo_file_index
        from .rules_plane import _expand_braces, _glob_to_re, _repo_paths_for_globs

        repo_files, _ = build_repo_file_index(repo_root)
        universe = _repo_paths_for_globs(repo_root, set(repo_files))
        hits: set = set()
        for glob in globs:
            try:
                for g in _expand_braces(glob):
                    rx = _glob_to_re(g)
                    hits.update(p for p in universe if rx.match(p))
            except Exception:
                continue
        return sorted(hits)
    except Exception:
        return []


def _applies_line(paths: List[str]) -> str:
    if not paths:
        return ""
    shown = paths[:_MAX_APPLIES_PATHS]
    extra = len(paths) - len(shown)
    return "Applies to: " + ", ".join(shown) + (f" (+{extra} more)" if extra else "")


def _candidate_body(parsed: dict, paths: List[str]) -> str:
    body = (parsed.get("body") or "").rstrip("\n")
    line = _applies_line(paths)
    if not line:
        return body
    return f"{body}\n\n{line}" if body else line


def _slug_for(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.")
    return slug or "imported-rule"


def _fallback_description(parsed: dict, slug: str) -> str:
    for line in (parsed.get("body") or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:120]
    return f"{slug} (imported Cursor rule)"


def import_candidates(
    repo_root: Optional[str] = None, memory_dir: Optional[str] = None
) -> List[dict]:
    """Read-only per-file import report — what WOULD each ``.mdc`` become, and what
    stands in its way (dup-gate route, restated-rule neighbors, secret findings,
    already-imported). The skill walks this list per-item; nothing here writes."""
    out: List[dict] = []
    try:
        from .new_memory import check_candidate
        from .provenance import resolve_dirs
        from .secrets import scan_text

        if memory_dir is None or repo_root is None:
            md, repo = resolve_dirs()
            memory_dir = memory_dir or md
            repo_root = repo_root or repo
        for path in mdc_rule_files(repo_root):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            parsed = parse_mdc(text)
            slug = _slug_for(path)
            description = parsed["description"] or _fallback_description(parsed, slug)
            paths = resolve_globs(repo_root, parsed["globs"])
            body = _candidate_body(parsed, paths)
            check = check_candidate(
                slug, description, "project", body,
                memory_dir=memory_dir, repo_root=repo_root,
            )
            out.append(
                {
                    "file": os.path.relpath(path, repo_root),
                    "slug": slug,
                    "description": description,
                    "globs": parsed["globs"],
                    "paths_matched": len(paths),
                    "always_apply": parsed["always_apply"],
                    "route": check.get("route"),
                    "neighbors": check.get("neighbors") or [],
                    "rule_neighbors": check.get("rule_neighbors") or [],
                    "secret_warnings": scan_text(f"{description}\n{body}"),
                    "exists": os.path.isfile(os.path.join(memory_dir, f"{slug}.md")),
                }
            )
    except Exception:
        return out
    return out


def import_mdc_file(
    path: str,
    *,
    memory_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
    mtype: str = "project",
    slug: Optional[str] = None,
    allow_duplicate: bool = False,
) -> dict:
    """Import ONE ``.mdc`` file as a memory — the per-item write leg. Never raises.

    Order is the trust posture: (1) secret-lint the candidate FIRST — foreign input is
    untrusted, so a non-empty finding set HOLDS the import (nothing written, nothing
    recallable) until the source is cleaned; (2) ``check_candidate`` dup gate — a
    ``review`` route holds unless ``allow_duplicate=True`` (the agent reviewed the
    neighbors and the user said import anyway); a duplicate never becomes a file;
    (3) ``write_memory`` on the shipped path — link discovery, provenance backfill (the
    ``Applies to:`` line lands as cited_paths), index refresh, exclusive-create refusal
    for idempotent re-imports. One file per call — never a bulk loop (inv4).
    """
    result = {
        "imported": False,
        "held": False,
        "slug": None,
        "path": None,
        "route": None,
        "neighbors": [],
        "rule_neighbors": [],
        "warnings": [],
        "error": None,
        "note": None,
    }
    try:
        from .new_memory import VALID_TYPES, check_candidate, write_memory
        from .provenance import resolve_dirs
        from .secrets import scan_with_remediation

        if memory_dir is None or repo_root is None:
            md, repo = resolve_dirs()
            memory_dir = memory_dir or md
            repo_root = repo_root or repo
        if mtype not in VALID_TYPES:
            result["error"] = f"invalid type {mtype!r} (expected one of {VALID_TYPES})"
            return result
        if not os.path.isfile(path):
            result["error"] = f"not found: {path}"
            return result
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        parsed = parse_mdc(text)
        slug = slug or _slug_for(path)
        result["slug"] = slug
        description = parsed["description"] or _fallback_description(parsed, slug)
        paths = resolve_globs(repo_root, parsed["globs"])
        body = _candidate_body(parsed, paths)
        rel = os.path.relpath(path, repo_root)

        warnings = scan_with_remediation(f"{description}\n{body}")
        if warnings:
            result["held"] = True
            result["warnings"] = warnings
            result["error"] = (
                "held for review: secret-looking content in the source .mdc — clean "
                f"{rel} first; foreign input is never written flagged"
            )
            return result

        check = check_candidate(
            slug, description, mtype, body, memory_dir=memory_dir, repo_root=repo_root
        )
        result["route"] = check.get("route")
        result["neighbors"] = check.get("neighbors") or []
        result["rule_neighbors"] = check.get("rule_neighbors") or []
        result["note"] = check.get("note")
        if result["route"] == "review" and not allow_duplicate:
            result["held"] = True
            result["error"] = (
                "held for review: near-duplicate of an existing memory — review the "
                "neighbors, then re-run with allow_duplicate=True to import anyway"
            )
            return result

        write = write_memory(
            slug,
            description,
            mtype,
            body,
            memory_dir=memory_dir,
            repo_root=repo_root,
            rationale=f"imported from {rel} (Cursor .mdc rule)",
        )
        result["warnings"] = write.get("warnings") or []
        if write.get("error"):
            err = write["error"]
            if "already exists" in err:
                err += " (already imported — re-imports are idempotent by refusal)"
            result["error"] = err
            return result
        result["imported"] = True
        result["path"] = write.get("path")
    except Exception as exc:
        result["error"] = result["error"] or f"import failed: {exc}"
    return result
