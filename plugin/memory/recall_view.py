"""INT-1: /hippo:recall — the read-side verb.

``memory.recall`` is the hot-path engine: structured hits (``recall.recall``) plus the
injection string (``recall.format_results``) the SILENT ``UserPromptSubmit`` hook emits. This
module is the DELIBERATE, human-facing read entry point the ``/hippo:recall`` skill wraps —
the answer to "what do you remember about X", "list what you know here", and "why was that
injected", questions the invisible-by-design hook cannot answer.

It REUSES ``recall.recall()`` verbatim — it never forks the ranking. The same fusion, floor,
knee cutoff, graph expansion, and salience blend the hook would apply produce these hits; the
only thing added is presentation: each hit is enriched with the memory's ``type``, a staleness
flag (RET-6's ``stale_banner``), and its inbound/outbound graph neighbors, then rendered as a
human-readable listing. ``--list-by-type`` dumps the whole corpus grouped by type (a map of
what is known here), read straight off the corpus files with no query.

Read-only: it never writes the corpus, the index, or the ledgers. It does NOT route through
``recall.main()``, so a deliberate listing logs no episode/recall event (a human browsing the
corpus is not a recall the capture pass should later replay). ``main()`` never raises — it
degrades to a plain message, mirroring ``recall.py``'s own never-raise hook discipline.
"""

from __future__ import annotations

import os
from typing import List, Optional, Set, Tuple

from .build_index import default_index_dir, extract_description
from .provenance import _iter_memory_files, parse_frontmatter, resolve_dirs
from .recall import DEFAULT_K, recall

# Canonical floor-taxonomy order (mirrors new_memory.VALID_TYPES) so --list-by-type reads
# user → feedback → project → reference; any unknown type sorts alphabetically after.
_TYPE_ORDER = ("user", "feedback", "project", "reference")


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return ""


def _memory_type(text: str) -> str:
    """The memory's declared type (``metadata.type``, falling back to a top-level ``type``)."""
    fm = parse_frontmatter(text)
    md = fm.get("metadata")
    if isinstance(md, dict) and md.get("type"):
        return str(md.get("type"))
    return str(fm.get("type") or "")


def _name_from_path(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def _load_graph(memory_dir: str, index_dir: Optional[str]):
    """A best-effort ``LinkGraph`` for neighbor lookups; ``None`` if it can't be built."""
    try:
        from .links import build_graph

        return build_graph(memory_dir, index_dir or default_index_dir(memory_dir))
    except Exception:
        return None


def _neighbors(graph, name: str) -> Tuple[List[str], List[str]]:
    """``(outbound, inbound)`` neighbor stems for ``name`` — ``([], [])`` when unknown."""
    if graph is None:
        return [], []
    try:
        return sorted(graph.outbound(name)), sorted(graph.inbound(name))
    except Exception:
        return [], []


def describe(
    query: str,
    k: int = DEFAULT_K,
    *,
    memory_dir: Optional[str] = None,
    index_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
) -> str:
    """Human-readable answer to "what do you remember about ``query``".

    Runs the SAME ``recall.recall()`` the hook would, then annotates each hit with type,
    staleness, and graph neighbors. Abstention (no hit clears the relevance floor) is reported
    as such — a feature, not an error.
    """
    if memory_dir is None:
        memory_dir, repo_root = resolve_dirs()
    hits = recall(query, k, memory_dir=memory_dir, index_dir=index_dir, repo_root=repo_root)
    if not hits:
        return (
            f'No memories cleared the relevance floor for "{query}" — nothing would be '
            "injected for a prompt like this. Abstention is a feature (RET-1): an unrelated "
            "or too-thin query surfaces nothing rather than padding out low-signal matches. "
            "Try /hippo:recall --list-by-type to see everything this project knows."
        )
    graph = _load_graph(memory_dir, index_dir)
    out: List[str] = [f'{len(hits)} memory match(es) for "{query}" (most relevant first):', ""]
    for h in hits:
        # recall's ``file`` is a bare basename (build_index stores os.path.basename); rejoin
        # to the corpus dir to read the memory's frontmatter for its type.
        fname = h.get("file") or ""
        name = h.get("name") or _name_from_path(fname)
        mtype = _memory_type(_read_text(os.path.join(memory_dir, fname))) or "untyped"
        score = h.get("score")
        via = h.get("via")
        tags = [f"{mtype}"]
        if isinstance(score, (int, float)):
            tags.append(f"relevance {score:.3f}")
        if via == "graph":
            tags.append("via 1-hop link")  # answers "why was this injected" (GRA-1 expansion)
        if h.get("stale_banner"):
            tags.append("⚠ stale — verify before relying")
        out.append(f"  • {name}  [{' · '.join(tags)}]")
        desc = h.get("description") or ""
        if desc:
            out.append(f"      {desc}")
        outbound, inbound = _neighbors(graph, name)
        if outbound:
            out.append("      → links to: " + ", ".join(outbound))
        if inbound:
            out.append("      ← linked from: " + ", ".join(inbound))
    return "\n".join(out)


def list_by_type(*, memory_dir: Optional[str] = None) -> str:
    """The whole corpus grouped by type — a map of what this project knows. No query."""
    if memory_dir is None:
        memory_dir, _ = resolve_dirs()
    buckets: dict = {}
    try:
        paths = list(_iter_memory_files(memory_dir))
    except Exception:
        paths = []
    for path in paths:
        text = _read_text(path)
        mtype = _memory_type(text) or "untyped"
        buckets.setdefault(mtype, []).append((_name_from_path(path), extract_description(text)))
    if not buckets:
        return (
            "This project has no memory corpus yet (nothing under .claude/memory/). "
            "Run /hippo:init to seed one."
        )
    keys = [t for t in _TYPE_ORDER if t in buckets] + sorted(
        t for t in buckets if t not in _TYPE_ORDER
    )
    total = sum(len(v) for v in buckets.values())
    out: List[str] = [f"{total} memories across {len(keys)} type(s):"]
    for t in keys:
        items = sorted(buckets[t])
        out.append("")
        out.append(f"## {t} ({len(items)})")
        for name, desc in items:
            out.append(f"  • {name} — {desc}" if desc else f"  • {name}")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Read-side recall (INT-1): query the corpus, or list it by type."
    )
    parser.add_argument("query", nargs="*", help="what to recall (natural-language)")
    parser.add_argument("-k", type=int, default=DEFAULT_K, help="max matches to show")
    parser.add_argument(
        "--list-by-type",
        action="store_true",
        help="list the whole corpus grouped by type instead of querying",
    )
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--index-dir", default=None)
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args(argv)
    try:
        if args.list_by_type:
            print(list_by_type(memory_dir=args.memory_dir))
            return 0
        query = " ".join(args.query).strip()
        if not query:
            print('usage: recall "<what to recall>"   |   recall --list-by-type')
            return 2
        print(
            describe(
                query,
                args.k,
                memory_dir=args.memory_dir,
                index_dir=args.index_dir,
                repo_root=args.repo_root,
            )
        )
        return 0
    except Exception as exc:  # never raise out of the CLI — mirror recall.py's discipline
        print(f"recall view unavailable: {exc}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
