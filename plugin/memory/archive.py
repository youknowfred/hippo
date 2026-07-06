"""Graceful decay — the git-reversible archive primitive (Tier 3, memory-organism
instrument-immunize roadmap).

Decay is DEMOTION, never deletion. ``archive_candidates()`` is a REPORT over the 4-WAY
INTERSECTION:
  - **cold**          — never recalled in the telemetry ledger (``soak.curation_report``'s
                        ``never_recalled`` set)
  - **stale**          — cites code that has drifted (``staleness.find_stale``)
  - **zero-inbound**   — no OTHER memory ``[[wikilinks]]`` to it (inverts
                        ``links.LinkGraph.adjacency`` — distinct from
                        ``LinkGraph.orphans()``, which is zero-OUTBOUND)
  - **not-cited-by-CLAUDE.md** — the memory's filename is not referenced (backtick-quoted,
                        with or without ``.md``) anywhere across the project's instruction
                        surface: ``CLAUDE.md``, ``.claude/rules/*.md``, ``.claude/agents/*.md``,
                        ``.claude/skills/*.md``, ``docs/prompts/*.md``

``archive_memory(name)`` is the per-item write primitive: a single ``git mv`` into
``.claude/memory/archive/`` (a tracked, non-recursive subdir ``_iter_memory_files`` already
skips — the memory instantly drops from index/recall/staleness with no code change
elsewhere, and the move is fully git-reversible: ``git mv`` it back). There is deliberately
NO batch/list parameter on either function — an autonomous bulk sweep is exactly the failure
mode this primitive must never enable (mirrors ``reverify_head_only_no_bulk``). REPORT-then-
move, per-item, gated by the memory-master agent. Never deletes. Never raises.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import List, Optional, Set

from .links import build_graph
from .provenance import _iter_memory_files
from .soak import curation_report
from .staleness import find_stale

_ARCHIVE_SUBDIR = "archive"

# A backtick-quoted token, with an OPTIONAL trailing ".md" -- confirmed empirically against
# the live repo: CLAUDE.md cites memory files BOTH with the suffix (`formula_graph_*.md`) AND
# without it anywhere nearby (`feedback_no_backward_compat` in rules/20-patterns.md). A regex
# anchored on a mandatory ".md" silently misses the latter, a real false-negative class.
_BACKTICK_TOKEN_RE = re.compile(r"`([A-Za-z0-9_-]+(?:\.md)?)`")

# The project's full instruction surface -- CLAUDE.md itself delegates authority into agents/
# ("Proactive invocation required") and mandates docs/prompts/evergreen-prompt-library.md
# updates (CRITICAL RULES); both cite real corpus memories nowhere near CLAUDE.md/rules/*.md.
_SCAN_TARGETS = (
    "CLAUDE.md",
    ".claude/rules",
    ".claude/agents",
    ".claude/skills",
    "docs/prompts",
)


def _corpus_names(memory_dir: str) -> Set[str]:
    try:
        return {os.path.splitext(os.path.basename(p))[0] for p in _iter_memory_files(memory_dir)}
    except Exception:
        return set()


def _zero_inbound_names(memory_dir: str) -> Set[str]:
    """Memory stems with NO inbound ``[[wikilink]]`` from any OTHER memory.

    Distinct from ``links.LinkGraph.orphans()`` (zero OUTBOUND) — a new computation that
    inverts the adjacency graph; no inbound-degree primitive existed before this. Never
    raises; ``set()`` on any failure (fails toward "has inbound", i.e. NOT a candidate — the
    safe direction for an archive gate, since this is one of four ANDed conditions).
    """
    try:
        g = build_graph(memory_dir)
        if g is None:
            return set()
        has_inbound: Set[str] = set()
        for targets in g.adjacency.values():
            has_inbound |= targets
        all_stems = {os.path.splitext(f)[0] for f in g.files}
        has_inbound_stems = {os.path.splitext(f)[0] for f in has_inbound}
        return all_stems - has_inbound_stems
    except Exception:
        return set()


def _scan_files(repo_root: str) -> List[str]:
    """Resolve the project's instruction-surface files to scan for citations. Never raises."""
    out: List[str] = []
    for rel in _SCAN_TARGETS:
        full = os.path.join(repo_root, rel)
        try:
            if os.path.isfile(full):
                out.append(full)
            elif os.path.isdir(full):
                for fname in sorted(os.listdir(full)):
                    if fname.endswith(".md"):
                        out.append(os.path.join(full, fname))
        except Exception:
            continue
    return out


def _cited_by_claude_md_names(
    repo_root: str, corpus_names: Set[str], *, unreadable: Optional[List[str]] = None
) -> Set[str]:
    """Corpus memory names cited (backtick-quoted, with/without ``.md``) anywhere across the
    project's instruction surface.

    Fresh-read-per-call — no cross-call cache, mirroring ``staleness.py``/``lint_links.py``'s
    minimal-I/O convention at this corpus's scale (under a dozen scan-target files). Matches
    via backtick/code-span-anchored token extraction, NOT bare substring containment — a real
    collision exists in this corpus today (``"MEMORY"`` is a substring of ``"MEMORY.full"``).
    Never raises; on a total scan failure returns the FULL ``corpus_names`` (fail CLOSED to
    "cited" — the safe direction: an unreadable scan target must never let the gate
    conclude "not cited" for everything). A PER-FILE read failure fails closed the same
    way — an unreadable instruction-surface file could have cited anything, so it must never
    silently cause its would-be citations to read as "not cited"; ``unreadable`` (if passed)
    collects the paths that failed so callers can surface the degradation.
    """
    try:
        cited: Set[str] = set()
        for path in _scan_files(repo_root):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                if unreadable is not None:
                    unreadable.append(path)
                return set(corpus_names)
            for tok in _BACKTICK_TOKEN_RE.findall(text):
                stem = tok[:-3] if tok.endswith(".md") else tok
                if stem in corpus_names:
                    cited.add(stem)
        return cited
    except Exception:
        return set(corpus_names)


def archive_candidates(
    memory_dir: str,
    repo_root: str,
    telemetry_dir: Optional[str] = None,
    *,
    since: Optional[str] = None,
) -> List[dict]:
    """REPORT (never autonomous) — the 4-way intersection: cold ∧ stale ∧ zero-inbound ∧
    not-cited-by-CLAUDE.md.

    Returns ``[{"name", "changed_paths", "citation_scan_unreadable"}]`` (every candidate is
    necessarily stale, so the stale-set shape is reused), most-recently-drifted first.
    ``citation_scan_unreadable`` is the list of instruction-surface paths (if any) that
    could not be read during the citation scan — attached to EVERY item regardless of
    whether that item itself survived the citation gate, so a fail-closed degradation is
    never silent even though it can only ever shrink (never inflate) the candidate set
    (guiding invariant: legible degradation). ``since`` passes through to ``find_stale``
    (its own default when omitted) — exposed so hermetic tests can widen the
    wall-clock-relative window (mirrors ``reconsolidate.recalled_stale_worklist``'s same
    passthrough for pinned-epoch fixtures). Read-only; never raises; ``[]`` when nothing
    satisfies all four conditions (the common, expected case — and ALWAYS the case on the
    current corpus, since the recall ledger's temporal window is still far too young to
    trust the cold signal; see the roadmap's never-act-on-young-window gate).
    """
    try:
        corpus_names = _corpus_names(memory_dir)
        if not corpus_names:
            return []
        report = curation_report(memory_dir, telemetry_dir)
        cold = set(report.get("never_recalled") or [])
        stale = find_stale(memory_dir, repo_root, **({"since": since} if since else {}))
        zero_inbound = _zero_inbound_names(memory_dir)
        unreadable: List[str] = []
        cited = _cited_by_claude_md_names(repo_root, corpus_names, unreadable=unreadable)

        out = [
            item
            for item in stale
            if item["name"] in cold and item["name"] in zero_inbound and item["name"] not in cited
        ]
        out.sort(key=lambda d: (-d["recency"], d["name"]))
        for item in out:
            item["citation_scan_unreadable"] = list(unreadable)
        return out
    except Exception:
        return []


_JOURNAL_NAME = ".archive_journal.jsonl"


def _journal_untracked_move(memory_dir: str, fname: str, src: str, dest: str) -> None:
    """Append a one-line record of an os.rename fallback move, for reversibility.

    Untracked files have no git history to fall back on, so this sidecar is the only trace
    of the pre-archive path. Best-effort only — a journal write failure must never abort an
    already-successful move.
    """
    import json
    import time

    journal = os.path.join(memory_dir, _ARCHIVE_SUBDIR, _JOURNAL_NAME)
    try:
        with open(journal, "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "name": fname,
                        "from": src,
                        "to": dest,
                        "method": "os.rename",
                        "ts": time.time(),
                    }
                )
                + "\n"
            )
    except Exception:
        pass


def archive_memory(name: str, memory_dir: str, repo_root: str, *, dry_run: bool = False) -> dict:
    """``git mv`` ONE memory into ``.claude/memory/archive/`` — per-item, report-then-move.

    NEVER deletes; the move is fully git-reversible (or, for an untracked file, at least
    fully recoverable — see the ``os.rename`` fallback below). The non-recursive
    ``_iter_memory_files`` already skips the ``archive/`` subdir, so an archived memory
    instantly drops from index/recall/staleness with NO code change elsewhere. Deliberately
    NO batch/list parameter — a bulk sweep would require a separately-approved function,
    never this one. Never raises.

    A memory that ``write_memory`` just created and that was never ``git add``-ed is
    UNTRACKED, and ``git mv`` refuses to move an untracked path. Falling back to a plain
    ``os.rename`` (journaled to a small sidecar log for reversibility, since git itself has
    no history of an untracked file anyway) is the only way archiving such a memory can
    ever succeed — without it, every just-written memory would be unarchivable until some
    unrelated later commit happened to stage it.
    """
    result = {"name": name, "moved": False, "error": None}
    try:
        fname = name if name.endswith(".md") else f"{name}.md"
        src = os.path.join(memory_dir, fname)
        if not os.path.isfile(src):
            result["error"] = f"not found: {fname}"
            return result
        if dry_run:
            result["moved"] = True  # would-move (report-only preview); no filesystem change
            return result
        archive_dir = os.path.join(memory_dir, _ARCHIVE_SUBDIR)
        os.makedirs(archive_dir, exist_ok=True)
        dest = os.path.join(archive_dir, fname)
        proc = subprocess.run(
            ["git", "-C", repo_root, "mv", src, dest],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode != 0:
            # git mv refuses untracked/ignored paths -- fall back to a plain rename so a
            # just-written (not yet git-add-ed) memory can still be archived.
            try:
                os.rename(src, dest)
            except OSError:
                result["error"] = (proc.stderr or proc.stdout or "git mv failed").strip()
                return result
            _journal_untracked_move(memory_dir, fname, src, dest)
        result["moved"] = True
        try:
            from . import build_index

            build_index.refresh_index(memory_dir)
        except Exception:
            pass
    except Exception as exc:
        result["error"] = str(exc)
    return result


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    from .provenance import resolve_dirs
    from .telemetry import default_telemetry_dir

    parser = argparse.ArgumentParser(
        description="Archive-candidate report (read-only) + per-item git-mv archive."
    )
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--telemetry-dir", default=None)
    parser.add_argument(
        "--archive",
        metavar="NAME",
        default=None,
        help="git-mv ONE memory into .claude/memory/archive/ after confirming it's a "
        "genuine candidate (per-item, gated by the memory-master agent reviewing the "
        "report first). NAME is the slug, with or without .md",
    )
    args = parser.parse_args(argv)

    md, repo = resolve_dirs()
    memory_dir = args.memory_dir or md
    repo_root = args.repo_root or repo

    if args.archive:
        r = archive_memory(args.archive, memory_dir, repo_root)
        if r["error"]:
            print(f"archive {args.archive}: refused — {r['error']}")
        else:
            print(f"archive {args.archive}: moved into .claude/memory/archive/ (git mv)")
        return 0

    td = args.telemetry_dir or default_telemetry_dir(memory_dir)
    candidates = archive_candidates(memory_dir, repo_root, telemetry_dir=td)
    unreadable: List[str] = []
    _cited_by_claude_md_names(repo_root, _corpus_names(memory_dir), unreadable=unreadable)
    if unreadable:
        print(
            f"warning: {len(unreadable)} instruction-surface file(s) unreadable during "
            f"citation scan (treated as cited, fail-closed): {', '.join(unreadable)}"
        )
    if not candidates:
        print("No archive candidates (4-way: cold ∧ stale ∧ zero-inbound ∧ not-CLAUDE.md-cited).")
        return 0
    print(f"{len(candidates)} archive candidate(s) — review each before archiving:")
    for item in candidates:
        print(f"  • {item['name']}: {', '.join(item['changed_paths'][:6])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
