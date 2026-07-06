"""Create a recall-ready memory file, right-by-construction (post-trim convention).

``write_memory(name, description, type, body)`` writes a new ``<name>.md`` whose frontmatter
carries the three fields the system depends on — ``name``, ``description`` (the recall hook),
and ``metadata.type`` — then:

  1. backfills Tier-1 citation provenance (``cited_paths`` / ``source_commit``) so the new
     memory is born staleness-tracked, and
  2. refreshes the recall index so it is immediately recallable, and
  3. appends a ``MEMORY.md`` floor pointer ONLY when ``type`` is ``user`` or ``feedback``.

``project`` / ``reference`` memories are deliberately NOT added to the floor — they are
recalled on demand (the UserPromptSubmit recall hook + the SessionStart auto-refresh index
them). This is the whole point: new memories never re-bloat the trimmed always-load.

Never silently overwrites an existing file. The floor-pointer write is the ONLY edit to
MEMORY.md; no existing memory BODY is ever modified.
"""

from __future__ import annotations

import json
import os
from typing import Optional

VALID_TYPES = ("user", "feedback", "project", "reference")

# Which floor section a pointer goes under, by type. project/reference => no floor pointer.
_FLOOR_SECTION_BY_TYPE = {
    "user": "## User",
    "feedback": "## Working Style & Process Feedback",
}


def _title_from_slug(name: str) -> str:
    return name.replace("_", " ").replace("-", " ").strip().title()


def _render_frontmatter(name: str, description: str, mtype: str, body: str) -> str:
    """Recall-ready frontmatter: top-level name + description (indexed), metadata.type.

    ``description`` is JSON-quoted so any colon/character is valid YAML (the recall index
    reads ``description`` via yaml.safe_load).
    """
    lines = [
        "---",
        f"name: {name}",
        f"description: {json.dumps(description)}",
        "metadata:",
        f"  type: {mtype}",
        "---",
        "",
        body.rstrip("\n") + "\n" if body else "",
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def _append_floor_pointer(
    memory_dir: str, section_header: str, name: str, title: str, hook: str
) -> bool:
    """Insert ``- [title](name.md) — hook`` at the END of ``section_header`` in MEMORY.md.

    Returns True if the pointer was added. Never raises. Idempotent: a pointer to the same
    ``name.md`` already present is left as-is (returns False).
    """
    path = os.path.join(memory_dir, "MEMORY.md")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except Exception:
        return False

    link = f"]({name}.md)"
    if any(link in ln for ln in lines):
        return False  # already pointed-to — don't duplicate

    # Find the section header, then the end of its block (next "## " or EOF).
    start = next((i for i, ln in enumerate(lines) if ln.strip() == section_header), None)
    if start is None:
        return False
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip().startswith("## "):
            end = j
            break
    # Insertion point = after the last non-blank line within the block.
    insert = start + 1
    for j in range(start + 1, end):
        if lines[j].strip():
            insert = j + 1

    pointer = f"- [{title}]({name}.md) — {hook}".rstrip()
    lines.insert(insert, pointer)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        return True
    except Exception:
        return False


def write_memory(
    name: str,
    description: str,
    type: str,
    body: str = "",
    *,
    memory_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
    title: Optional[str] = None,
    hook: Optional[str] = None,
) -> dict:
    """Create a recall-ready memory file. Returns a small result dict.

    - Validates ``type`` ∈ VALID_TYPES.
    - Refuses to overwrite an existing ``<name>.md`` (``created=False``, ``error`` set).
    - Backfills provenance + refreshes the recall index (best-effort; never fatal).
    - Adds a MEMORY.md floor pointer ONLY for ``user`` / ``feedback``.
    - Scans the rendered text for secret-looking patterns (SEC-2) and, on a match, populates
      ``warnings`` — WARN-not-block: the write still happens; the agent decides what to do.
    """
    result = {
        "created": False,
        "path": None,
        "floor_pointer_added": False,
        "indexed": False,
        "warnings": [],
        "error": None,
    }
    if type not in VALID_TYPES:
        result["error"] = f"invalid type {type!r} (expected one of {VALID_TYPES})"
        return result
    # Name must be a bare slug — a path separator (or "..") would write the file OUTSIDE
    # memory_dir, where neither the index nor the floor would ever find it (a silent hole).
    if not name or os.path.basename(name) != name:
        result["error"] = f"invalid name {name!r} (must be a bare slug, no path separators)"
        return result

    from .provenance import build_repo_file_index, resolve_dirs

    md, repo = resolve_dirs()
    memory_dir = memory_dir or md
    repo_root = repo_root or repo

    path = os.path.join(memory_dir, f"{name}.md")
    rendered = _render_frontmatter(name, description, type, body)
    try:
        os.makedirs(memory_dir, exist_ok=True)
        # "x" = exclusive create: atomic no-overwrite (no TOCTOU window between check + write).
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(rendered)
        result["created"] = True
        result["path"] = path
    except FileExistsError:
        result["error"] = f"{name}.md already exists — refusing to overwrite"
        return result
    except Exception as exc:
        result["error"] = f"write failed: {exc}"
        return result

    # Secret-pattern lint (SEC-2): scan the RENDERED text (frontmatter + body) for
    # secret-looking content. This WARNS, it does NOT block — the write already happened and
    # is kept; the warnings ride out on the result dict so the agent decides what to do next
    # (report-then-act, agent-gated). Never fatal — a scan failure just yields no warnings.
    try:
        from .secrets import scan_with_remediation

        result["warnings"] = scan_with_remediation(rendered)
    except Exception:
        pass

    # 1. Provenance backfill (best-effort — a new file with no code citations is fine).
    try:
        from .provenance import backfill_file

        repo_files, basename_index = build_repo_file_index(repo_root)
        backfill_file(path, repo_root, repo_files, basename_index)
    except Exception:
        pass

    # 2. Refresh the recall index so the memory is immediately recallable.
    try:
        from .build_index import refresh_index

        refresh_index(memory_dir)
        result["indexed"] = True
    except Exception:
        pass

    # 3. Floor pointer ONLY for user / feedback (project / reference are recalled on demand).
    section = _FLOOR_SECTION_BY_TYPE.get(type)
    if section is not None:
        result["floor_pointer_added"] = _append_floor_pointer(
            memory_dir, section, name, title or _title_from_slug(name), hook or description
        )
    return result


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Create a recall-ready memory file.")
    parser.add_argument("name", help="kebab/snake slug (also the filename stem)")
    parser.add_argument("description", help="one-line recall hook (indexed for recall)")
    parser.add_argument("--type", required=True, choices=VALID_TYPES)
    parser.add_argument("--body", default="", help="memory body text")
    parser.add_argument("--title", default=None, help="floor-pointer link text (user/feedback only)")
    parser.add_argument("--hook", default=None, help="floor-pointer trailing note (user/feedback only)")
    parser.add_argument("--memory-dir", default=None)
    args = parser.parse_args(argv)

    res = write_memory(
        args.name,
        args.description,
        args.type,
        body=args.body,
        memory_dir=args.memory_dir,
        title=args.title,
        hook=args.hook,
    )
    if res["error"]:
        print(f"error: {res['error']}")
        return 1
    print(f"created : {res['path']}")
    print(f"indexed : {res['indexed']}")
    print(f"floor pointer added : {res['floor_pointer_added']} (only user/feedback get one)")
    for warning in res["warnings"]:
        print(f"warning : {warning}")
    if args.type in ("project", "reference"):
        print("note    : project/reference memories are recalled on demand — NOT added to the floor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
