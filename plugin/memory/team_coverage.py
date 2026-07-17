"""CLB-2 read side: last_verified + verified_by consumers — team coverage, suppressed solo.

hippo records WHO authored a memory (git) and — since CLB-2's stamp — who last
VERIFIED it (``verified_by: "<slug>@<own-ts>"``, refreshed by every human-gated
reverify verdict). This module is the report-time join that makes both fields
legible, under two hard rules:

  - **Computed at report time, never persisted** (inv1): nothing here writes; the
    only durable state is the frontmatter the reverify gate already wrote.
  - **Every team-coverage line is SUPPRESSED (omitted entirely, not rendered
    empty) at ≤1 distinct git author** — solo coverage numbers are vacuously 100%
    self-verification and would only teach the reader to ignore the line. The
    ``last_verified`` half (WHEN was this vouched) is solo-meaningful and renders
    for everyone — it is this field's FIRST production consumer (the read path
    was dark: written since RET-6, surfaced nowhere).

Identity joining: git-log author identities normalize through
``provenance.slugify_identity`` — the SAME transform ``current_user_slug`` applies
before stamping — so a stamp and a log line can never disagree about one human.
``verified_by`` is NEVER a ranking input (tests/test_verified_by.py holds recall's
whole module family + build_index to zero references, structurally).
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Set, Tuple

from .provenance import (
    _iter_memory_files,
    parse_frontmatter,
    run_git,
    slugify_identity,
)

_AUTHOR_MARKER = "__A__"


def read_verified_by(text: str) -> Optional[Tuple[str, str]]:
    """``(slug, ts)`` from a memory's ``verified_by`` (top-level or under
    ``metadata:`` — the read_last_verified convention), or ``None`` when absent or
    malformed. The value's grammar is ``<slug>@<own-ts>``; the slug alphabet
    (``slugify_identity``) contains no ``@``, so ``rsplit`` is unambiguous even
    though the timestamp itself may not carry one. Never raises."""
    try:
        fm = parse_frontmatter(text)
        if not fm:
            return None
        meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
        raw = fm.get("verified_by")
        if raw is None:
            raw = (meta or {}).get("verified_by")
        if not isinstance(raw, str) or "@" not in raw:
            return None
        slug, ts = raw.rsplit("@", 1)
        if not slug.strip() or not ts.strip():
            return None
        return slug.strip(), ts.strip()
    except Exception:
        return None


def _memory_prefix(memory_dir: str, repo_root: str) -> str:
    try:
        top = run_git(["rev-parse", "--show-toplevel"], repo_root).strip() or repo_root
        rel = os.path.relpath(os.path.realpath(memory_dir), os.path.realpath(top))
        if not rel.startswith(".."):
            return rel.replace(os.sep, "/")
    except Exception:
        pass
    return ".claude/memory"


def _author_walk(memory_dir: str, repo_root: str, extra_args: list) -> Dict[str, Set[str]]:
    """``{stem: {author slugs}}`` from ONE ``git log --format=__A__%ae --name-only``
    walk scoped to the corpus prefix — never a call per memory. Identities
    normalize through ``slugify_identity`` before any join. Never raises."""
    out: Dict[str, Set[str]] = {}
    try:
        prefix = _memory_prefix(memory_dir, repo_root)
        log = run_git(
            ["log", *extra_args, f"--format={_AUTHOR_MARKER}%ae", "--name-only", "--", prefix],
            repo_root,
        )
        current: Optional[str] = None
        for line in log.split("\n"):
            if line.startswith(_AUTHOR_MARKER):
                current = slugify_identity(line[len(_AUTHOR_MARKER):])
            elif line.strip() and current is not None:
                path = line.strip()
                if path.endswith(".md") and path.startswith(f"{prefix}/"):
                    stem = os.path.splitext(os.path.basename(path))[0]
                    out.setdefault(stem, set()).add(current)
    except Exception:
        return {}
    return out


def file_author_slugs(memory_dir: str, repo_root: str) -> Dict[str, Set[str]]:
    """``{stem: {CREATOR slugs}}`` — the authors of each memory's ADD commit(s)
    (``--diff-filter=A``, the archive.py convention). The non-author-verified join
    deliberately compares against CREATORS, not all committers: committing a
    verify-stamp makes the verifier a committer of the file, which would otherwise
    launder every non-author verification back into "author-verified" one commit
    later. Never raises; ``{}`` on any failure / non-git corpus."""
    return _author_walk(memory_dir, repo_root, ["--diff-filter=A"])


def corpus_author_slugs(memory_dir: str, repo_root: str) -> Set[str]:
    """Every distinct author slug that ever committed a memory file — the ≤1-author
    suppression gate's population (an edit-only teammate is still a teammate)."""
    out: Set[str] = set()
    for slugs in _author_walk(memory_dir, repo_root, []).values():
        out |= slugs
    return out


def verification_summary(memory_dir: str) -> Dict[str, int]:
    """``{"total", "last_verified", "verified_by"}`` counts — the solo-meaningful
    half (lights the dark ``last_verified`` read path for every corpus). Never
    raises; zeros on failure."""
    total = lv = vb = 0
    try:
        from .staleness import read_last_verified

        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            total += 1
            if read_last_verified(text):
                lv += 1
            if read_verified_by(text):
                vb += 1
    except Exception:
        pass
    return {"total": total, "last_verified": lv, "verified_by": vb}


def team_coverage(memory_dir: str, repo_root: str) -> Optional[dict]:
    """The team half — ``None`` at ≤1 distinct git author (the suppression rule:
    callers omit their line entirely, so a solo corpus renders byte-identically to
    pre-CLB-2). Otherwise::

        {"authors": N,                 # distinct corpus-committing author slugs
         "total": M, "stamped": S,     # memories / memories carrying verified_by
         "non_author_verified": J,     # stamp's slug is NOT one of the file's CREATORS
         "never_other_verified": K,    # M - J: only-ever-creator-vouched (or unvouched)
         "departed": D}                # stamped slugs never among corpus authors at all

    All counts, no names, no timestamps — deterministic for the doctor pin; the
    per-memory evidence stays in the files themselves. Never raises."""
    try:
        creators_by_stem = file_author_slugs(memory_dir, repo_root)
        all_authors = corpus_author_slugs(memory_dir, repo_root)
        if len(all_authors) <= 1:
            return None
        total = stamped = non_author = departed = 0
        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            total += 1
            parsed = read_verified_by(text)
            if not parsed:
                continue
            stamped += 1
            slug = parsed[0]
            stem = os.path.splitext(os.path.basename(path))[0]
            if slug not in creators_by_stem.get(stem, set()):
                non_author += 1
            if slug not in all_authors:
                departed += 1
        return {
            "authors": len(all_authors),
            "total": total,
            "stamped": stamped,
            "non_author_verified": non_author,
            "never_other_verified": total - non_author,
            "departed": departed,
        }
    except Exception:
        return None
