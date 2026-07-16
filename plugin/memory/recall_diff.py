"""EXT-1: recall for the reviewer — memories on the PR diff (T17).

A PR touches files; memories cite files; nobody connects them at review time. This
module is the join: resolve a git range to its changed paths, walk the corpus's
``cited_paths`` provenance, and render the citing memories — ``steer:pin`` and
feedback types first (the lesson-shaped knowledge a reviewer most wants), staleness
flags riding every row (a stale lesson is FLAGGED, never asserted fresh), output
bounded (``DEFAULT_CAP``).

Deliberately NOT recall: no query, no ranking, no index, no dense model, no LLM, and
no telemetry — a pure read of text already committed to the repo's own corpus. That
is the disclosure boundary the Action recipe documents: the sticky PR comment renders
only names + descriptions that already live in ``.claude/memory/`` in the same repo,
so a teammate WITHOUT Claude sees exactly what a git checkout already shows them.
Because nothing here needs the venv (PyYAML degrades to the vendored miniyaml
frontmatter parser), the CI lane runs on a bare python3 with ``PYTHONPATH=plugin``.

Surfaces: ``recall --for-diff <range> [--json] [--cap N]`` (the recall CLI dispatches
here before any query/telemetry machinery), or ``python -m memory.recall_diff``
directly. Exit 0 with empty output when nothing cites the diff — the empty norm; the
Action recipe posts no comment at all in that case.

Positioning (RATIFIED 2026-07-16): quiet dogfood on this repo first —
``.github/workflows/memory-on-diff.yml`` is the shipped recipe, same-repo PRs only by
default (a fork PR gets no write token, and the recipe guards on head repo == base
repo explicitly).
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

DEFAULT_CAP = 10  # bounded comment: the reviewer wants the top lessons, not the corpus
_MAX_CHANGED_PATHS = 400  # a pathological mega-diff stays bounded (matches capture's own cap)

# Review-order rank per row: the lesson-shaped types outrank ambient project facts.
_TYPE_RANK = {"feedback": 1, "user": 1, "project": 2, "reference": 3}


def changed_paths_for_range(range_expr: str, repo_root: Optional[str]) -> List[str]:
    """Repo-relative paths changed in ``range_expr`` (any ``git diff`` range form:
    ``A..B``, ``A...B``, a single ref against the working tree). Sorted, bounded,
    ``[]`` on any failure (bad ref, not a repo, shallow history) — never raises."""
    if not range_expr or not repo_root:
        return []
    try:
        from .provenance import run_git

        out = run_git(["diff", "--name-only", range_expr], repo_root)
        paths = sorted({ln.strip() for ln in out.splitlines() if ln.strip()})
        return paths[:_MAX_CHANGED_PATHS]
    except Exception:
        return []


def memories_for_paths(
    paths: List[str], memory_dir: str, *, repo_root: Optional[str] = None
) -> List[dict]:
    """The citation join: every memory whose ``cited_paths`` intersects ``paths``.

    Rows — ``{name, type, steer, description, paths, stale}`` — ordered pins first,
    then feedback/user, then project, then reference, then by name (deterministic).
    ``paths`` on the row lists WHICH changed files the memory cites (the reviewer's
    'why am I seeing this'). ``stale`` is ``{"changed": n}`` when the memory's cited
    code drifted after its recorded baseline (``staleness.find_stale`` — best-effort:
    a shallow clone or non-git corpus yields no flags, never an error), else ``None``.
    One corpus frontmatter pass; read-only; never raises (``[]`` on failure).
    """
    try:
        if not paths:
            return []
        changed = set(paths)
        from .build_index import _extract_steer, extract_description
        from .jit import _flatten, _memory_type
        from .provenance import _iter_memory_files, parse_frontmatter
        from .staleness import read_provenance

        stale_by_name: Dict[str, int] = {}
        if repo_root:
            try:
                from .staleness import find_stale

                for item in find_stale(memory_dir, repo_root):
                    stale_by_name[item["name"]] = len(item.get("changed_paths") or [])
            except Exception:
                stale_by_name = {}

        rows: List[tuple] = []
        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            cited = read_provenance(text)[0]
            hit = sorted(set(cited) & changed)
            if not hit:
                continue
            name = os.path.splitext(os.path.basename(path))[0]
            fm = parse_frontmatter(text)
            steer = _extract_steer(fm if isinstance(fm, dict) else {})
            mtype = _memory_type(fm) or "project"
            rank = 0 if steer == "pin" else _TYPE_RANK.get(mtype, 2)
            n_stale = stale_by_name.get(name)
            rows.append(
                (
                    rank,
                    name,
                    {
                        "name": name,
                        "type": mtype,
                        "steer": steer,
                        "description": _flatten(extract_description(text)),
                        "paths": hit,
                        "stale": {"changed": n_stale} if n_stale else None,
                    },
                )
            )
        return [row for _rank, _name, row in sorted(rows, key=lambda t: (t[0], t[1]))]
    except Exception:
        return []


def render_text(rows: List[dict], *, range_expr: str, changed_count: int) -> str:
    """The human/terminal form — one bounded line per memory; ``""`` when empty.

    The Action recipe embeds these lines verbatim inside a fenced code block, so the
    corpus text lands in the comment as QUOTED DATA (SEC-5): no markdown rendering,
    no @-mention pings, no link smuggling.
    """
    if not rows:
        return ""
    lines = [
        f"institutional memory on this diff — {len(rows)} memory(ies) across "
        f"{changed_count} changed file(s) [{range_expr}]:"
    ]
    for r in rows:
        marker = " [pin]" if r.get("steer") == "pin" else ""
        stale = r.get("stale") or {}
        flag = (
            f"  ⚠ stale — {stale['changed']} cited file(s) drifted since last verify"
            if stale
            else ""
        )
        cites = ", ".join(r.get("paths") or [])
        lines.append(f"- {r['name']} [{r['type']}]{marker} — {r['description']}  (cites: {cites}){flag}")
    return "\n".join(lines)


def run(
    range_expr: str,
    *,
    memory_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
    cap: int = DEFAULT_CAP,
    as_json: bool = False,
) -> int:
    """The whole ``--for-diff`` lane: resolve, join, render, print. Always exits 0 —
    an empty result is the norm, and a broken ref must not fail a CI job (the recipe's
    empty-check simply posts nothing)."""
    try:
        if memory_dir is None or repo_root is None:
            from .provenance import resolve_dirs

            md, rr = resolve_dirs()
            memory_dir = memory_dir or md
            repo_root = repo_root or rr
        paths = changed_paths_for_range(range_expr, repo_root)
        rows = memories_for_paths(paths, memory_dir, repo_root=repo_root) if paths else []
        total = len(rows)
        rows = rows[: max(0, cap)]
        if as_json:
            print(
                json.dumps(
                    {
                        "range": range_expr,
                        "changed_paths": len(paths),
                        "total": total,
                        "items": rows,
                    },
                    ensure_ascii=False,
                )
            )
        else:
            out = render_text(rows, range_expr=range_expr, changed_count=len(paths))
            if out:
                if total > len(rows):
                    out += f"\n… and {total - len(rows)} more (raise --cap)"
                print(out)
        return 0
    except Exception:
        return 0


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="EXT-1: memories citing the files a diff touches — the reviewer's "
        "recall. Read-only; no index, no model, no telemetry."
    )
    parser.add_argument("--range", required=True, help="git diff range (A..B, A...B, or a ref)")
    parser.add_argument("--json", action="store_true", help="machine-readable output (the Action recipe consumes this)")
    parser.add_argument("--cap", type=int, default=DEFAULT_CAP, help=f"max memories rendered (default {DEFAULT_CAP})")
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args(argv)
    return run(
        args.range,
        memory_dir=args.memory_dir,
        repo_root=args.repo_root,
        cap=args.cap,
        as_json=args.json,
    )


if __name__ == "__main__":
    raise SystemExit(main())
