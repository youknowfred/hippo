"""EXT-2: cross-project promotion mining — report-only; promote stays per-item (T17).

TEA-1 gave lessons a machine-local user tier and ``/hippo:promote`` lifts ONE memory
with an origin stamp — but finding WHICH lessons deserve promotion was human hunch.
The projects registry (RCH-4) already knows every corpus on this machine; this sweep
reads the SEC-1-TRUSTED ones, mines their lesson-shaped memories (types ``feedback`` /
``user``) for near-duplicates appearing in >=2 projects, and renders per-item
proposals. A lesson learned twice is a lesson about the person, not the project.

Discipline (each line is an acceptance criterion):
  - TRUSTED-ONLY, fail-quiet: an untrusted corpus contributes NOTHING — not even
    memory names — it is skipped and counted (``projects_untrusted``). The gate is the
    same ``gate_repo_root``/``is_trusted`` pair recall's --all-projects lane uses.
  - REPORT-ONLY: the sweep never writes anything anywhere (a test pins byte-identical
    trees). Every acceptance routes through the EXISTING ``/hippo:promote`` flow —
    per-item, agent-gated, origin-stamped — which this module only NAMES, never runs.
  - REUSED MACHINERY: similarity is ``new_memory._duplicate_neighbors`` verbatim (the
    calibrated dense-cosine / normalized-BM25 thresholds behind ``check_candidate``),
    aimed at the OTHER project's persisted index — no new similarity stack. A corpus
    with no index degrades to a NAMED note, never a crash.
  - EMPTY-NORM: most runs propose nothing and say (almost) nothing. Comparison is one
    direction per ordered pair (A<B: A's lessons vs B's index), so a mirrored match
    is structurally one proposal, and everything is capped (projects, lessons/pair,
    proposals).
  - SEC-5: rendered descriptions are flattened and quoted — corpus text is DATA in
    the report, never formatting or instructions.

Surfaces: ``python -m memory.promote_scan [--json]`` for a deliberate run, and the
``hippo sleep`` morning report carries a section when (and only when) there are
proposals — the designed offline home for machine-wide worklists (SLP-1).
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

MAX_PROJECTS = 16  # ordered pairs grow quadratically; a machine with more gets the cap named
MAX_LESSONS_PER_PAIR = 60  # per (A,B) comparison budget — lesson-shaped types are few in practice
MAX_PROPOSALS = 20
_LESSON_TYPES = frozenset({"feedback", "user"})
_SHORT_SHA = 7


def _short_sha(value: Optional[str]) -> str:
    return (value or "").strip()[:_SHORT_SHA] or "unanchored"


def _lessons_of(memory_dir: str, cap: int = 200) -> List[dict]:
    """The lesson-shaped memories of ONE trusted corpus:
    ``{name, text, description, type, sha}`` — feedback/user types only, name-sorted,
    capped. Never raises; ``[]`` on failure."""
    out: List[dict] = []
    try:
        from .build_index import extract_description
        from .jit import _flatten, _memory_type
        from .provenance import _iter_memory_files, parse_frontmatter
        from .staleness import read_provenance

        for path in _iter_memory_files(memory_dir):
            if len(out) >= cap:
                break
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            fm = parse_frontmatter(text)
            mtype = _memory_type(fm)
            if mtype not in _LESSON_TYPES:
                continue
            out.append(
                {
                    "name": os.path.splitext(os.path.basename(path))[0],
                    "text": text,
                    "description": _flatten(extract_description(text)),
                    "type": mtype,
                    "sha": _short_sha(read_provenance(text)[1]),
                }
            )
    except Exception:
        return []
    return out


def scan() -> dict:
    """The machine-wide sweep. Returns
    ``{projects_scanned, projects_untrusted, proposals, notes}`` — read-only, never
    raises, empty-norm shaped (``proposals`` usually ``[]``)."""
    result = {"projects_scanned": 0, "projects_untrusted": 0, "proposals": [], "notes": []}
    try:
        from . import trust
        from .new_memory import _duplicate_neighbors
        from .registry import registered_projects

        eligible: List[tuple] = []  # (root, memory_dir)
        projects = registered_projects()
        for root in sorted(projects):
            md = (projects.get(root) or {}).get("memory_dir")
            if not md:
                continue
            try:
                gate_root = trust.gate_repo_root(md, root)
                if gate_root is not None and not trust.is_trusted(gate_root):
                    # SEC-1: an untrusted corpus contributes NOTHING — not even names.
                    result["projects_untrusted"] += 1
                    continue
            except Exception:
                result["projects_untrusted"] += 1
                continue
            eligible.append((root, md))
        if len(eligible) > MAX_PROJECTS:
            result["notes"].append(
                f"project cap: scanning the first {MAX_PROJECTS} of {len(eligible)} trusted projects"
            )
            eligible = eligible[:MAX_PROJECTS]
        result["projects_scanned"] = len(eligible)
        if len(eligible) < 2:
            return result

        lessons: Dict[str, List[dict]] = {root: _lessons_of(md) for root, md in eligible}

        from .build_index import default_index_dir

        for i, (root_a, _md_a) in enumerate(eligible):
            for root_b, md_b in eligible[i + 1 :]:
                if len(result["proposals"]) >= MAX_PROPOSALS:
                    result["notes"].append(f"proposal cap reached ({MAX_PROPOSALS}); rerun after draining")
                    return result
                by_name_b = {m["name"]: m for m in lessons.get(root_b) or []}
                noted_skip = False
                for lesson in (lessons.get(root_a) or [])[:MAX_LESSONS_PER_PAIR]:
                    neighbors, note = _duplicate_neighbors(
                        lesson["name"], lesson["text"], md_b, default_index_dir(md_b)
                    )
                    if note and not noted_skip:
                        result["notes"].append(f"{root_b}: {note}")
                        noted_skip = True
                    if note:
                        break  # the whole target corpus is unscorable — next pair
                    for n in neighbors:
                        twin = by_name_b.get(n.get("name"))
                        if not twin:
                            continue  # a near-dup of a project/reference note is not a LESSON twin
                        result["proposals"].append(
                            {
                                "score": n.get("score"),
                                "sides": [
                                    {
                                        "repo": root_a,
                                        "name": lesson["name"],
                                        "sha": lesson["sha"],
                                        "type": lesson["type"],
                                        "description": lesson["description"],
                                    },
                                    {
                                        "repo": root_b,
                                        "name": twin["name"],
                                        "sha": twin["sha"],
                                        "type": twin["type"],
                                        "description": twin["description"],
                                    },
                                ],
                            }
                        )
        return result
    except Exception:
        return result


def render_report(result: dict) -> str:
    """The human report — ``""`` when there is truly nothing to say (the empty norm).

    Proposals route through the EXISTING per-item flow by NAME: ``/hippo:promote`` run
    in the side's own repo (terminal verb). Descriptions render flattened inside
    quotes — data, not markup (SEC-5).
    """
    try:
        proposals = result.get("proposals") or []
        lines: List[str] = []
        if proposals:
            lines.append(
                f"cross-project promotion candidates — {len(proposals)} lesson(s) learned in "
                "more than one trusted project (report-only; accept per item):"
            )
            for i, p in enumerate(proposals, 1):
                a, b = p["sides"]
                score = p.get("score")
                score_s = f" (similarity {score:.2f})" if isinstance(score, (int, float)) else ""
                lines.append(
                    f"{i}. {a['name']} [{a['type']}] ({a['repo']}@{a['sha']}) ↔ "
                    f"{b['name']} [{b['type']}] ({b['repo']}@{b['sha']}){score_s}"
                )
                lines.append(f"   a: \"{a['description']}\"")
                lines.append(f"   b: \"{b['description']}\"")
                lines.append(
                    f"   accept: run /hippo:promote {a['name']} inside {a['repo']} "
                    f"(or its twin in {b['repo']}) — per-item, origin-stamped, terminal"
                )
        untrusted = result.get("projects_untrusted") or 0
        if untrusted:
            lines.append(
                f"{untrusted} untrusted project(s) skipped — an untrusted corpus contributes "
                "nothing, not even names (consent via /hippo:doctor in that repo)."
            )
        for note in result.get("notes") or []:
            lines.append(f"note: {note}")
        return "\n".join(lines)
    except Exception:
        return ""


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="EXT-2: cross-project promotion mining — report-only sweep over this "
        "machine's TRUSTED registered corpora for lessons learned in >=2 projects. "
        "Acceptance routes through /hippo:promote (per item); this never writes."
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    try:
        result = scan()
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
            return 0
        text = render_report(result)
        if text:
            print(text)
        else:
            print(
                "cross-project promotion mining: nothing to propose "
                f"({result.get('projects_scanned', 0)} trusted project(s) scanned)."
            )
        return 0
    except Exception:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
