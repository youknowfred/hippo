"""INV-4: the /hippo:audit report MATERIAL, as one read-only engine call.

The audit skill's Phase 1 gathers every signal source in one sequential pass and
cross-references them (its whole value over the raw CLIs); on surfaces where the
skill's bash blocks can't run (the Claude Desktop app — the agent's Bash tool never
inherits CLAUDE_PLUGIN_DATA), the old-invalidation SessionStart nudge routed users to
an audit they could not start. This module is that Phase-1 gather as a function the
audit MCP tool serves: same signals, same join keys the skill's Phases 2-4 reason
over, STRICTLY READ-ONLY.

Judgment stays agent-driven — this produces material, never verdicts, and never a
write (the consolidate-skill pattern): unlike the skill's own Phase 1 it does NOT
update ``.claude/state/memory-audit-history.json`` (recurrence is computed against the
file if present and reported; the bookkeeping write stays with the skill flow), and
the Phase 0.6 abstention DRAFTING it also leaves to its own per-item tool
(``abstention_fixtures``). Zero corpus writes, zero registry writes, assertable.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Dict, List, Optional

# The skill's own Phase-1 constants (kept in lockstep — the tool and the bash block
# must describe the same material).
_LINK_SIM_K = 3
_LINK_SIM_MAX_SAMPLE = 200
_GOV_GLOBS = [
    "CLAUDE.md",
    "AGENTS.md",
    ".claude/rules/*.md",
    ".claude/agents/*.md",
    ".claude/skills/**/*.md",
]
_CITATION_RE = re.compile(r"`([A-Za-z0-9_-]+(?:\.md)?)`")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def gather_material(
    memory_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
    *,
    skip_eval: bool = False,
    window_sessions: int = 30,
) -> dict:
    """Gather the ranked-report material — the skill's Phase-1 JSON, read-only.

    Every section degrades per-signal (a failed producer is NAMED in ``errors``,
    never silently dropped — RCH-9), so a partial environment still yields usable
    material. Never raises.
    """
    from .provenance import resolve_dirs

    if memory_dir is None or repo_root is None:
        md, rr = resolve_dirs()
        memory_dir = memory_dir or md
        repo_root = repo_root or rr

    errors: Dict[str, str] = {}

    def _section(name, fn, default):
        try:
            return fn()
        except Exception as exc:
            errors[name] = f"{type(exc).__name__}: {exc}"
            return default

    from . import archive, lint_floor, lint_links, links, reconsolidate, soak, staleness, telemetry

    fixtures_dir = os.path.join(memory_dir, ".audit-fixtures")
    hard_set = os.path.join(fixtures_dir, "recall_hard_set.yaml")
    rel_set = os.path.join(fixtures_dir, "recall_relevance_set.yaml")
    hard_set = hard_set if os.path.exists(hard_set) else None
    rel_set = rel_set if os.path.exists(rel_set) else None

    ev = {}
    if not skip_eval:
        from . import eval_recall

        ev = _section(
            "eval_recall",
            lambda: eval_recall.evaluate(
                repo_root=repo_root, hard_set_path=hard_set, relevance_set_path=rel_set
            ),
            {},
        )
    td = telemetry.default_telemetry_dir(memory_dir)
    soak_status = _section("soak_status", lambda: soak.soak_status(td, memory_dir=memory_dir), {})
    curation = _section("curation", lambda: soak.curation_report(memory_dir, td), {})
    strength = _section("strength", lambda: soak.compute_strength_scores(td), {})
    unparseable = _section("unparseable", lambda: staleness.find_unparseable(memory_dir), [])
    stale = _section("stale", lambda: staleness.find_stale(memory_dir, repo_root), [])
    worklist = _section(
        "worklist",
        lambda: reconsolidate.recalled_stale_worklist(
            memory_dir, repo_root, window_sessions=window_sessions
        ),
        [],
    )
    archive_cands = _section(
        "archive_candidates", lambda: archive.archive_candidates(memory_dir, repo_root), []
    )
    graph = _section("links_graph", lambda: links.build_graph(memory_dir), None)
    link_report = _section("link_report", lambda: lint_links.lint(memory_dir), {})
    floor = _section("floor_violations", lambda: lint_floor.floor_violations(memory_dir), {})

    names = set(graph.files) if graph else set()
    never_recalled = set(curation.get("never_recalled") or [])
    orphans = set(graph.orphans()) if graph else set()
    unparseable_set = set(unparseable)

    # Join 1: cascading blind spot — invisible to three tools at once.
    join_cascading_blindspot = sorted(unparseable_set & never_recalled & orphans)

    # Authority-citation scan (reimplemented like the skill's — no private helpers).
    def _authority_gap():
        cited_tokens = set()
        import glob as _glob

        for pattern in _GOV_GLOBS:
            for gf in _glob.glob(os.path.join(repo_root, pattern), recursive=True):
                text = _read_text(gf)
                if text is None:
                    continue
                for m in _CITATION_RE.finditer(text):
                    tok = m.group(1)
                    cited_tokens.add(tok[:-3] if tok.endswith(".md") else tok)
        return sorted(
            name for name in (names & cited_tokens) if strength.get(name, 0.0) < 0.15
        )

    join_authority_gap = _section("authority_gap", _authority_gap, [])
    join_graph_isolated = _section(
        "graph_isolated", lambda: graph.isolates() if graph else [], []
    )

    # GRA-3 link-densification pass — suggestions only, read-only.
    def _link_density():
        from .build_index import memory_doc_text
        from .recall import recall

        out = []
        for name in sorted(names)[:_LINK_SIM_MAX_SAMPLE]:
            text = _read_text(os.path.join(memory_dir, f"{name}.md"))
            if text is None:
                continue
            query = memory_doc_text(name, text)
            existing_out = graph.adjacency.get(name, set())
            hits = recall(
                query, _LINK_SIM_K + 1, memory_dir=memory_dir, repo_root=repo_root
            )
            candidates = [
                {"name": h["name"], "score": h["score"]}
                for h in hits
                if h["name"] != name and h["name"] not in existing_out
            ][:_LINK_SIM_K]
            if candidates:
                out.append({"memory": name, "candidates": candidates})
        return out

    link_density_suggestions = _section("link_density", _link_density, []) if graph else []

    # GRW-3 merge-candidate pass — both-direction near-duplicates, suggestions only.
    def _merge_candidates():
        from .new_memory import committed_duplicate_neighbors

        invalidated = set(staleness.invalid_after_map(sorted(names), memory_dir))
        dup_hits = {}
        for name in sorted(names)[:_LINK_SIM_MAX_SAMPLE]:
            neighbors, _note = committed_duplicate_neighbors(name, memory_dir)
            dup_hits[name] = {n["name"]: n["score"] for n in neighbors}
        out = []
        for x in sorted(dup_hits):
            for y, s_xy in sorted(dup_hits[x].items()):
                if y <= x or x in invalidated or y in invalidated:
                    continue
                s_yx = dup_hits.get(y, {}).get(x)
                if s_yx is None:
                    continue  # one-way similarity is not a merge signal
                out.append({"pair": [x, y], "score_a_to_b": s_xy, "score_b_to_a": s_yx})
        return out

    merge_candidates = _section("merge_candidates", _merge_candidates, []) if names else []

    # Join 4: per-memory staleness-baseline age.
    def _ages():
        ages, cache = {}, {}
        for name in names:
            text = _read_text(os.path.join(memory_dir, f"{name}.md"))
            if text is None:
                continue
            _cited, source_commit = staleness.read_provenance(text)
            if not source_commit:
                continue
            if source_commit not in cache:
                out = subprocess.run(
                    ["git", "-C", repo_root, "show", "-s", "--format=%ct", source_commit],
                    capture_output=True, text=True,
                )
                cache[source_commit] = (
                    int(out.stdout.strip())
                    if out.returncode == 0 and out.stdout.strip()
                    else None
                )
            ct = cache[source_commit]
            if ct:
                ages[name] = round(
                    (datetime.now(timezone.utc).timestamp() - ct) / 86400.0, 1
                )
        return ages

    ages = _section("staleness_ages", _ages, {})

    # Join 6: graduation-rate history filtered to currently-stale names.
    def _grad_history():
        stale_names = {item["name"] for item in stale}
        ledger = os.path.join(td, "reconsolidation_events.jsonl")
        out: Dict[str, List[dict]] = {}
        text = _read_text(ledger)
        for line in (text or "").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("name") in stale_names:
                out.setdefault(row["name"], []).append(row)
        return out

    graduation_history = _section("graduation_history", _grad_history, {})

    # Join 3, READ-ONLY: recurrence against the skill's history file if present.
    # The +1 mirrors what the skill's Phase 1 would record for THIS run — but the
    # write stays with the skill (this tool performs zero bookkeeping).
    def _recurrence():
        path = os.path.join(repo_root, ".claude", "state", "memory-audit-history.json")
        history = json.loads(_read_text(path) or "{}")
        return {
            item["name"]: (history.get(item["name"], {}).get("seen_count", 0) + 1)
            for item in worklist
        }

    recurrence = _section("worklist_recurrence", _recurrence, {})

    return {
        "run_date": datetime.now(timezone.utc).date().isoformat(),
        "corpus_size": len(names),
        "fixtures_present": {
            "hard_set": hard_set is not None,
            "relevance_set": rel_set is not None,
        },
        "recall_backend": ev.get("backend"),
        "recall_backend_mismatch": ev.get("backend_mismatch", False),
        "soak_status": soak_status,
        "eval_recall": ev,
        "curation": {
            "never_recalled_count": len(never_recalled),
            "bm25_fallback_rate": curation.get("bm25_fallback_rate"),
        },
        "unparseable": sorted(unparseable_set),
        "stale": stale,
        "worklist": worklist,
        "worklist_recurrence": recurrence,
        "archive_candidates": archive_cands,
        "link_report": link_report,
        "floor_violations": floor,
        "joins": {
            "cascading_blindspot": join_cascading_blindspot,
            "authority_evidence_gap": join_authority_gap,
            "graph_isolated_watchlist": join_graph_isolated,
            "staleness_ages": ages,
        },
        "graduation_history_for_stale": graduation_history,
        "link_density_suggestions": link_density_suggestions,
        "merge_candidates": merge_candidates,
        "history_bookkeeping": (
            "not written — this producer is read-only; the audit skill's Phase 1/3 owns "
            ".claude/state/memory-audit-history.json"
        ),
        "errors": errors,  # RCH-9: a failed section is named, never dropped
    }


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Audit report material (INV-4): the /hippo:audit skill's Phase-1 "
        "gather as one read-only JSON document. Judgment stays with the skill."
    )
    parser.add_argument("--skip-eval", action="store_true", help="skip the eval_recall cluster")
    parser.add_argument("--window-sessions", type=int, default=30)
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args(argv)
    try:
        material = gather_material(
            args.memory_dir,
            args.repo_root,
            skip_eval=args.skip_eval,
            window_sessions=args.window_sessions,
        )
        print(json.dumps(material, indent=2, default=str))
        return 0
    except Exception as exc:  # never raise out of the CLI — recall_view's discipline
        print(json.dumps({"error": str(exc)}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
