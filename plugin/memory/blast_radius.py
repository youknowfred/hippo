"""Blast-radius report (SEN-5) — what did a suspect memory touch? Read-only incident forensics.

After ``untrust`` revokes a bad corpus (or after spotting one poisoned memory), the next
question is scope: which sessions recalled it, what links and rules cite it, was it ever
archived? Nothing answered that. This is the read-only join that does — over the four places
a memory leaves a trace:

  - ``episode_buffer.jsonl`` (telemetry) — the sessions whose recall surfaced this memory
    (``recalled_names``).
  - ``links.json`` (links.load_edges) — the typed + untyped graph adjacency: who this memory
    points at, and who points at it. This is ``load_edges``' FIRST real consumer — the
    ``typed_out`` direction shipped DARK (recall only reads ``typed_in``); blast-radius lights
    it up.
  - governance citations (rules_plane.gov_citations) — CLAUDE.md/.claude/rules/AGENTS.md
    blocks that name this memory.
  - ``.archive_journal.jsonl`` (archive) — any archive move of this stem (the GRA-5 incident
    template, extended).

Writes NOTHING (inv4 — pure read; ``untrust`` is the single-target state change, this is its
read-only companion). Never raises. Its output ALWAYS states its coverage LIMITS in-band
(inv3): the episode buffer ROTATES at a byte cap, so recalls older than the window are
invisible; and MCP-channel recall (``recall_view.describe``) does not write the episode
buffer today, so a memory surfaced only through the MCP recall tool leaves no episode trace.
A blast-radius that hid those blind spots would read as "nothing touched it" when the truth
is "nothing WE CAN SEE touched it" — the exact silent-degradation failure KPI-5 exists to
prevent.

The 'quarantine' tier this workstream once carried is DROPPED, not renamed: SEC-6 already
ships per-file drift-withholding under that word (inv5), and a second concept under the same
name would collide. blast-radius EXTENDS GRA-8's graph observability; it does not re-invent
SEC-6's quarantine.
"""

from __future__ import annotations

import os
from typing import List, Optional


_COVERAGE_BANNER = (
    "coverage limits (read this before concluding 'nothing touched it'): the episode buffer "
    "ROTATES at a byte cap, so recalls older than the retained window are not counted here; "
    "and MCP-channel recall (the recall/why MCP tools) does NOT write the episode buffer "
    "today, so a memory surfaced only through MCP recall leaves no episode trace. Link, "
    "governance, and archive-journal coverage is complete; episode coverage is a lower bound."
)


def blast_radius(
    name: str,
    *,
    memory_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
    index_dir: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
) -> dict:
    """Read-only join over the four traces a memory ``name`` leaves. Writes nothing; never raises.

    Returns ``{name, recalled_sessions, recall_events, links, gov_citations, archive_journal,
    coverage}``. ``links`` is ``{out, in, typed_out, typed_out_of? ...}`` from ``load_edges``;
    absent traces come back empty (not an error). ``coverage`` ALWAYS names the episode-buffer
    blind spots (inv3).
    """
    stem = os.path.splitext(os.path.basename(str(name or "")))[0]
    out: dict = {
        "name": stem,
        "recalled_sessions": [],
        "recall_events": 0,
        "links": {"out": [], "in": [], "typed_out": {}, "typed_in": {}},
        "gov_citations": [],
        "archive_journal": [],
        "coverage": _COVERAGE_BANNER,
    }
    if not stem:
        return out
    try:
        from .provenance import resolve_dirs

        if memory_dir is None or repo_root is None:
            md, repo = resolve_dirs()
            memory_dir = memory_dir or md
            repo_root = repo_root or repo
    except Exception:
        pass

    # --- 1. episode buffer: sessions whose recall surfaced this memory ---
    try:
        from .telemetry import default_telemetry_dir, read_episodes

        td = telemetry_dir or (default_telemetry_dir(memory_dir) if memory_dir else None)
        sessions: List[str] = []
        seen = set()
        events = 0
        for ep in read_episodes(td):
            if stem in (ep.get("recalled_names") or []):
                events += 1
                sid = ep.get("session_id")
                if sid and sid not in seen:
                    seen.add(sid)
                    sessions.append(sid)
        out["recalled_sessions"] = sessions
        out["recall_events"] = events
    except Exception:
        pass

    # --- 2. links.json: typed + untyped adjacency (load_edges' first real consumer) ---
    try:
        from .build_index import default_index_dir
        from .links import load_edges

        idx = index_dir or (default_index_dir(memory_dir) if memory_dir else None)
        edges = load_edges(idx) if idx else None
        if edges and stem in edges:
            e = edges[stem]
            out["links"] = {
                "out": sorted(e.get("out") or []),
                "in": sorted(e.get("in") or []),
                "typed_out": {k: sorted(v) for k, v in (e.get("typed_out") or {}).items()},
                "typed_in": {k: sorted(v) for k, v in (e.get("typed_in") or {}).items()},
            }
    except Exception:
        pass

    # --- 3. governance citations: rules-plane files naming this memory ---
    try:
        from .provenance import _iter_memory_files
        from .rules_plane import gov_citations

        if repo_root and memory_dir:
            corpus_names = {
                os.path.splitext(os.path.basename(p))[0] for p in _iter_memory_files(memory_dir)
            }
            cites = gov_citations(repo_root, corpus_names)
            out["gov_citations"] = sorted(cites.get(stem) or [])
    except Exception:
        pass

    # --- 4. archive journal: any archive move of this stem ---
    try:
        import json

        from .archive import _ARCHIVE_SUBDIR, _JOURNAL_NAME

        if memory_dir:
            journal = os.path.join(memory_dir, _ARCHIVE_SUBDIR, _JOURNAL_NAME)
            if os.path.isfile(journal):
                with open(journal, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except Exception:
                            continue
                        rname = os.path.splitext(str(row.get("name") or ""))[0]
                        if rname == stem:
                            out["archive_journal"].append(row)
    except Exception:
        pass

    return out


def render(report: dict) -> str:
    """Human-readable blast-radius block, coverage banner ALWAYS last. Never raises."""
    try:
        lines = [f"blast-radius for '{report.get('name')}' (read-only — nothing was changed):"]
        s = report.get("recalled_sessions") or []
        lines.append(
            f"  recalled by: {len(s)} session(s), {report.get('recall_events', 0)} recall event(s)"
            + (f" — {', '.join(s[:6])}" if s else "")
        )
        links = report.get("links") or {}
        tin = links.get("typed_in") or {}
        tout = links.get("typed_out") or {}
        lines.append(
            f"  links: {len(links.get('in') or [])} inbound / {len(links.get('out') or [])} "
            f"outbound wikilink(s); typed_in {sum(len(v) for v in tin.values())}, "
            f"typed_out {sum(len(v) for v in tout.values())}"
        )
        for rel, stems in sorted(tin.items()):
            lines.append(f"    <- {rel}: {', '.join(stems)}")
        for rel, stems in sorted(tout.items()):
            lines.append(f"    -> {rel}: {', '.join(stems)}")
        gov = report.get("gov_citations") or []
        lines.append(f"  governance citations: {', '.join(gov) if gov else 'none'}")
        arch = report.get("archive_journal") or []
        lines.append(f"  archive journal: {len(arch)} move(s) recorded")
        lines.append(f"  ⓘ {report.get('coverage')}")
        return "\n".join(lines)
    except Exception:
        return "blast-radius: report render failed"


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: ``python -m memory.blast_radius <name>`` — the read-only incident forensics report."""
    import argparse

    ap = argparse.ArgumentParser(
        prog="memory.blast_radius",
        description="SEN-5: read-only blast-radius report for a suspect memory (writes nothing).",
    )
    ap.add_argument("name", help="the memory slug (with or without .md)")
    ap.add_argument("--memory-dir", default=None)
    ap.add_argument("--repo-root", default=None)
    args = ap.parse_args(argv)
    report = blast_radius(args.name, memory_dir=args.memory_dir, repo_root=args.repo_root)
    print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
