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
    trees). Every acceptance is a per-item tend verdict (``promote_tend``), which this
    module only NAMES, never runs.
  - REUSED MACHINERY: similarity is ``new_memory._duplicate_neighbors``' scorers and
    calibrated thresholds (dense cosine / normalized BM25, behind ``check_candidate``),
    aimed at the OTHER project's persisted index — no new similarity stack. A corpus
    with no index degrades to a NAMED note, never a crash.
  - EMPTY-NORM: most runs propose nothing and say (almost) nothing. Comparison is one
    direction per pair (the smaller side's lessons vs the other's index), so a mirrored
    match is structurally one proposal, and everything is capped (projects,
    lessons/pair, proposals).
  - SEC-5: rendered descriptions are flattened and quoted — corpus text is DATA in
    the report, never formatting or instructions.

Surfaces: ``python -m memory.promote_scan [--json]`` for a deliberate run, and the
``hippo sleep`` morning report carries a section when (and only when) there are
proposals — the designed offline home for machine-wide worklists (SLP-1).

OWN-2: per-item acceptance is now the tend queue's ``promote`` kind (``promote_tend``),
which reuses ``twins`` below for one corpus against each other trusted one. Two
corrections came with it, both in ``twins``: a same-named lesson in the other corpus is a
candidate twin (``_duplicate_neighbors`` excludes its own name, which is right inside one
corpus and hid the likeliest cross-corpus twin), and the side with fewer lessons is the
query side, so the per-pair budget covers the small corpus whole instead of the
alphabetically first sixty lessons of the large one.
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


def _lessons_of(memory_dir: str, cap: int = 200, effective: bool = False) -> List[dict]:
    """The lesson-shaped memories of ONE trusted corpus:
    ``{name, text, description, type, sha, machine_local}`` — feedback/user types only,
    not retired, name-sorted, capped. ``effective`` reads outbox copies where they exist
    (the corpus a tend pass runs in: what its verdicts wrote). Never raises; ``[]`` on
    failure."""
    out: List[dict] = []
    try:
        from .build_index import extract_description
        from .fm_access import fm_metadata
        from .jit import _flatten, _memory_type
        from .outbox_edit import iter_files
        from .provenance import _iter_memory_files, parse_frontmatter
        from .staleness import read_invalid_after, read_provenance

        for path in (iter_files if effective else _iter_memory_files)(memory_dir):
            if len(out) >= cap:
                break
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            fm = parse_frontmatter(text)
            mtype = _memory_type(fm)
            if mtype not in _LESSON_TYPES or read_invalid_after(text) is not None:
                continue  # a retired lesson does not promote (promote_memory refuses it too)
            out.append(
                {
                    "name": os.path.splitext(os.path.basename(path))[0],
                    "text": text,
                    "description": _flatten(extract_description(text)),
                    "type": mtype,
                    "sha": _short_sha(read_provenance(text)[1]),
                    "machine_local": fm_metadata(fm).get("machine_local") is True,  # OWN-1
                }
            )
    except Exception:
        return []
    return out


def index_dir_of(memory_dir: str) -> str:
    """Another corpus's OWN index dir. ``default_index_dir`` honours ``HIPPO_INDEX_DIR``,
    which names THIS process's index and would point every corpus at the same one."""
    from .derived_dirs import derived_dir

    return derived_dir(memory_dir, "index")


def _index_neighbors(index, name: str, text: str) -> Optional[List[tuple]]:
    """``[(name, score)]`` above the calibrated threshold in ONE loaded index, best first;
    ``None`` when the index is unscorable. ``_duplicate_neighbors``' scorers and thresholds,
    without its own-name exclusion: across corpora a same-named memory is a twin."""
    from .build_index import memory_doc_text
    from .new_memory import (
        _DUP_BM25_THRESHOLD,
        _DUP_COSINE_THRESHOLD,
        _DUP_NEIGHBORS_K,
        _bm25_dup_scores,
        _dense_dup_scores,
        _dup_threshold,
    )

    doc_text = memory_doc_text(name, text)
    scored, default = _dense_dup_scores(index, doc_text), _DUP_COSINE_THRESHOLD
    if scored is None:
        scored, default = _bm25_dup_scores(index, doc_text), _DUP_BM25_THRESHOLD
    if scored is None:
        return None
    threshold = _dup_threshold(default)
    hits = sorted(((i, s) for i, s in scored if s >= threshold), key=lambda p: p[1], reverse=True)
    return [(index.entries[i]["name"], round(float(s), 4)) for i, s in hits[:_DUP_NEIGHBORS_K]]


def twins(lessons_a: List[dict], index_a: str, lessons_b: List[dict], index_b: str):
    """Near-duplicate lesson pairs between two corpora -> ``([(a, b, score)], note)``.

    One direction per pair: the side with FEWER lessons is scored against the other side's
    persisted index (ties: A against B), so a mirrored match is one pair and the per-pair
    budget covers the small corpus whole. Pairs come back oriented (A side, B side)
    whichever way they were scored. ``note`` names a target index that could not be
    scored (``None`` when it was)."""
    from .build_index import load_index

    swap = len(lessons_b) < len(lessons_a)
    query, target, target_dir = (lessons_b, lessons_a, index_a) if swap else (lessons_a, lessons_b, index_b)
    index = load_index(target_dir)
    if index is None:
        return [], "duplicate check skipped: no index"
    if not index.entries:
        return [], "duplicate check skipped: empty index"
    by_name = {m["name"]: m for m in target}
    out: List[tuple] = []
    for lesson in query[:MAX_LESSONS_PER_PAIR]:
        hits = _index_neighbors(index, lesson["name"], lesson["text"])
        if hits is None:
            return out, "duplicate check skipped: unscorable index"
        for name, score in hits:
            twin = by_name.get(name)
            if twin:  # a near-dup of a project/reference note is not a LESSON twin
                out.append((twin, lesson, score) if swap else (lesson, twin, score))
    return out, None


def trusted_corpora() -> tuple:
    """``([(root, memory_dir)], untrusted_count)`` over the projects registry, root-sorted.
    SEC-1: an untrusted corpus contributes NOTHING — not even names — it is only counted."""
    from . import trust
    from .registry import registered_projects

    eligible: List[tuple] = []
    untrusted = 0
    projects = registered_projects()
    for root in sorted(projects):
        md = (projects.get(root) or {}).get("memory_dir")
        if not md:
            continue
        try:
            gate_root = trust.gate_repo_root(md, root)
            if gate_root is not None and not trust.is_trusted(gate_root):
                untrusted += 1
                continue
        except Exception:
            untrusted += 1
            continue
        eligible.append((root, md))
    return eligible, untrusted


def scan() -> dict:
    """The machine-wide sweep. Returns
    ``{projects_scanned, projects_untrusted, proposals, notes}`` — read-only, never
    raises, empty-norm shaped (``proposals`` usually ``[]``)."""
    result = {"projects_scanned": 0, "projects_untrusted": 0, "proposals": [], "notes": []}
    try:
        eligible, result["projects_untrusted"] = trusted_corpora()
        if len(eligible) > MAX_PROJECTS:
            result["notes"].append(
                f"project cap: scanning the first {MAX_PROJECTS} of {len(eligible)} trusted projects"
            )
            eligible = eligible[:MAX_PROJECTS]
        result["projects_scanned"] = len(eligible)
        if len(eligible) < 2:
            return result

        lessons: Dict[str, List[dict]] = {root: _lessons_of(md) for root, md in eligible}

        def _side(root: str, m: dict) -> dict:
            return dict({k: m[k] for k in ("name", "sha", "type", "description")}, repo=root)

        for i, (root_a, md_a) in enumerate(eligible):
            for root_b, md_b in eligible[i + 1 :]:
                if len(result["proposals"]) >= MAX_PROPOSALS:
                    result["notes"].append(f"proposal cap reached ({MAX_PROPOSALS}); rerun after draining")
                    return result
                la, lb = lessons.get(root_a) or [], lessons.get(root_b) or []
                pairs, note = twins(la, index_dir_of(md_a), lb, index_dir_of(md_b))
                if note:
                    result["notes"].append(f"{root_a if len(lb) < len(la) else root_b}: {note}")
                for a, b, score in pairs:
                    result["proposals"].append({"score": score, "sides": [_side(root_a, a), _side(root_b, b)]})
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
                    f"   decide: say \"tend memory\" (or run `hippo tend --kind promote`) inside "
                    f"{a['repo']} or {b['repo']}: per item, with both texts, written only on a yes"
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
        description="cross-project promotion mining — report-only sweep over this "
        "machine's TRUSTED registered corpora for lessons learned in >=2 projects. "
        "Each is decided per item in the maintenance queue (hippo tend); this never writes."
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
