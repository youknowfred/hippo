"""FMT-1: ``hippo migrate --check`` — what moving a corpus to format 6 will touch. Read-only.

Format 6 (the v2.0 migration) makes the frontmatter nested-only, moves derived citation
fields out of committed frontmatter and into the index, renames ``derives-from`` to
``derives_from``, and moves policy keys from ``.format`` to ``hippo.json``. Before any of
that can be previewed or applied, each corpus needs an honest inventory, and this is it:

  - the format marker: absent (an implicit format 1), unreadable, newer than this plugin,
    or its declared version
  - citation derivation: the version the corpus declares against the one this plugin uses
  - flat files: memories carrying a key hippo nests under ``metadata:`` at the top level
  - legacy keys: spellings format 6 renames (``derives-from``)
  - derived fields: memories whose committed frontmatter carries ``cited_paths``, which
    format 6 derives in the index instead
  - policy keys still in ``.format`` that move to ``hippo.json``
  - files with no frontmatter, or frontmatter that does not parse

It opens files for reading only; ``tests/test_migrate.py`` hashes the whole tree before and
after to hold that. doctor runs the same check. Applying arrives with format 6.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Dict, List, Optional

# Keys hippo writes under ``metadata:``. A memory carrying one of these at the top level is
# the flat shape format 6 retires.
NESTED_KEYS = (
    "type", "cited_paths", "cited_paths_exclude", "source_commit", "source_commit_time",
    "last_verified", "verified_by", "invalid_after", "supersedes", "contradicts", "refines",
    "derives-from", "derives_from", "confidence", "steer", "origin", "pack", "pack_version",
    "edge_origin",
)
LEGACY_KEYS: Dict[str, str] = {"derives-from": "derives_from"}
DERIVED_KEYS = ("cited_paths",)
# Keys the marker is for. Anything else in ``.format`` is policy that moves to hippo.json.
MARKER_KEYS = ("corpus_format", "cite_derivation")
TARGET_FORMAT = 6

_MAX_NAMES = 12


def _names(paths: List[str]) -> List[str]:
    return sorted(os.path.basename(p)[:-3] for p in paths)


def check(memory_dir: str) -> dict:
    """The read-only inventory of one corpus. Never raises; ``error`` names a failure."""
    from .fm_access import fm_metadata
    from .provenance import _iter_memory_files, parse_frontmatter, split_frontmatter
    from .provenance_format import (
        CITATION_DERIVATION_VERSION,
        CORPUS_FORMAT_VERSION,
        _read_marker,
        marker_state,
        read_cite_derivation,
    )

    report: dict = {
        "memory_dir": memory_dir,
        "files": 0,
        "marker": None,
        "plugin_format": CORPUS_FORMAT_VERSION,
        "target_format": TARGET_FORMAT,
        "cite_derivation": None,
        "plugin_derivation": CITATION_DERIVATION_VERSION,
        "flat": [],
        "legacy_keys": {},
        "derived_fields": [],
        "policy_keys": [],
        "no_frontmatter": [],
        "unparseable": [],
        "error": None,
    }
    try:
        if not os.path.isdir(memory_dir):
            report["error"] = "no corpus here"
            return report
        report["marker"] = marker_state(memory_dir)
        report["cite_derivation"] = read_cite_derivation(memory_dir)
        report["policy_keys"] = sorted(k for k in _read_marker(memory_dir) if k not in MARKER_KEYS)
        flat, derived, nofm, bad = [], [], [], []
        legacy: Dict[str, List[str]] = {}
        for path in _iter_memory_files(memory_dir):
            report["files"] += 1
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                bad.append(path)
                continue
            fm_lines, _body = split_frontmatter(text)
            if fm_lines is None:
                nofm.append(path)
                continue
            fm = parse_frontmatter(text)
            if not fm:
                bad.append(path)
                continue
            meta = fm_metadata(fm)
            if any(k in fm for k in NESTED_KEYS):
                flat.append(path)
            for old in LEGACY_KEYS:
                if old in fm or old in meta:
                    legacy.setdefault(old, []).append(path)
            if any(k in fm or k in meta for k in DERIVED_KEYS):
                derived.append(path)
        report["flat"] = _names(flat)
        report["derived_fields"] = _names(derived)
        report["no_frontmatter"] = _names(nofm)
        report["unparseable"] = _names(bad)
        report["legacy_keys"] = {k: _names(v) for k, v in legacy.items()}
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    return report


def _list(names: List[str]) -> str:
    shown = ", ".join(names[:_MAX_NAMES])
    return shown + (f" (+{len(names) - _MAX_NAMES} more)" if len(names) > _MAX_NAMES else "")


def render(report: dict, label: Optional[str] = None) -> str:
    head = label or report["memory_dir"]
    if report.get("error") and not report.get("files"):
        return f"{head}: {report['error']}"
    m = report["marker"] or {}
    state = m.get("state")
    if state == "absent":
        marker = "no format marker (read as format 1)"
    elif state == "unreadable":
        marker = f"format marker unreadable ({m.get('error')})"
    elif state == "newer":
        marker = f"format marker declares {m.get('declared')}, newer than this plugin reads"
    else:
        marker = f"format {m.get('declared')}"
    lines = [f"{head}: {report['files']} memories, {marker}; format {report['target_format']} "
             "is the next migration."]
    deriv, want = report["cite_derivation"], report["plugin_derivation"]
    lines.append(f"  citations: derived by extractor {deriv}"
                 + ("" if deriv >= want else f"; this plugin derives {want}"))
    if report["flat"]:
        lines.append(f"  flat frontmatter: {len(report['flat'])} file(s) carry hippo keys at the "
                     f"top level instead of under metadata: ({_list(report['flat'])})")
    for old, names in sorted(report["legacy_keys"].items()):
        lines.append(f"  legacy key `{old}` (becomes `{LEGACY_KEYS[old]}`): {len(names)} file(s) "
                     f"({_list(names)})")
    if report["derived_fields"]:
        lines.append(f"  derived citation fields in frontmatter: {len(report['derived_fields'])} "
                     "file(s); format 6 derives them in the index instead")
    if report["policy_keys"]:
        lines.append(f"  policy keys in .format that move to hippo.json: "
                     f"{', '.join(report['policy_keys'])}")
    if report["no_frontmatter"]:
        lines.append(f"  no frontmatter: {_list(report['no_frontmatter'])}")
    if report["unparseable"]:
        lines.append(f"  frontmatter does not parse: {_list(report['unparseable'])}")
    if report.get("error"):
        lines.append(f"  ⚠ check stopped early: {report['error']}")
    lines.append("  (read-only: nothing was written)")
    return "\n".join(lines)


def summary_line(report: dict) -> str:
    """One line for doctor."""
    bits = []
    if report["flat"]:
        bits.append(f"{len(report['flat'])} flat")
    n_legacy = sum(len(v) for v in report["legacy_keys"].values())
    if n_legacy:
        bits.append(f"{n_legacy} legacy key(s)")
    if report["policy_keys"]:
        bits.append(f"{len(report['policy_keys'])} policy key(s) to move")
    if report["cite_derivation"] < report["plugin_derivation"]:
        bits.append(f"derivation {report['cite_derivation']} of {report['plugin_derivation']}")
    if report["unparseable"] or report["no_frontmatter"]:
        bits.append(f"{len(report['unparseable']) + len(report['no_frontmatter'])} unreadable")
    found = "; ".join(bits) if bits else "nothing to migrate beyond the marker"
    return (f"format {report['target_format']} readiness over {report['files']} memories: {found} "
            "(`hippo migrate --check` lists them; read-only).")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="hippo migrate", description=__doc__.split("\n\n")[0])
    p.add_argument("--check", action="store_true", help="report what format 6 would touch (read-only)")
    p.add_argument("--all-projects", action="store_true", help="check every registered corpus")
    p.add_argument("--json", action="store_true")
    p.add_argument("--memory-dir", default=None)
    args = p.parse_args(argv)
    if not args.check:
        print("hippo migrate: only --check exists in this release (read-only). "
              "Applying arrives with corpus format 6.")
        return 2
    if args.all_projects:
        from .registry import registered_projects

        targets = [(root, entry["memory_dir"]) for root, entry in sorted(registered_projects().items())]
        if not targets:
            print("no registered corpora on this machine.")
            return 0
    else:
        if args.memory_dir:
            md = args.memory_dir
        else:
            from .provenance import resolve_dirs

            md, _root = resolve_dirs()
        targets = [(None, md)]
    reports = [(root, check(md)) for root, md in targets]
    if args.json:
        print(json.dumps([r for _root, r in reports], indent=2, default=str))
    else:
        print("\n\n".join(render(r, label=os.path.basename(root) if root else None)
                          for root, r in reports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
