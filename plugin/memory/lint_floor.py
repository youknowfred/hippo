"""Floor-invariant guard for MEMORY.md (read-only).

The post-trim durable floor (``MEMORY.md``) is the ONLY always-loaded memory context, so it
must stay lean. The invariant: memory pointers (``](file.md)`` links) may appear ONLY under
the two floor sections —

    ## User
    ## Working Style & Process Feedback

A project/reference ``](file.md)`` link anywhere else (e.g. under "Recalled on demand", or in
the preamble) is **re-bloat** — it re-grows the trimmed always-load. This guard flags it.

Two restore-pointer links are ALLOW-LISTED everywhere (they are not memory entries):
``MEMORY.full.md`` and ``MEMORY.md`` (the pre-trim snapshot + self references). Without the
allow-list the guard would false-positive on the real floor, which carries a
``[MEMORY.full.md](MEMORY.full.md)`` link in both the preamble and the "Recalled on demand"
nav header.

Also flags floor **link rot** — a User/Working-Style pointer whose target file is missing.

READ-ONLY: never edits MEMORY.md (or any memory). Never raises. Its one-line summary is the
SessionStart ``floor`` producer.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

from .staleness import RunContext

# Sections under which memory pointers ARE allowed (the always-loaded floor).
_FLOOR_SECTIONS = ("User", "Working Style & Process Feedback")

# Restore/snapshot pointers — not memory entries; allowed in any section.
_ALLOWLIST = ("MEMORY.full.md", "MEMORY.md")

# A markdown link whose target is an .md file: [text](target.md)
_MD_LINK_RE = re.compile(r"\]\(([^)]+\.md)\)")

_MAX_ITEMS = 20
_MAX_CHARS = 1500


def _floor_path(memory_dir: str) -> str:
    return os.path.join(memory_dir, "MEMORY.md")


def floor_violations(memory_dir: str) -> Dict[str, List[dict]]:
    """Return ``{"rebloat": [...], "missing_targets": [...]}`` for MEMORY.md.

    - ``rebloat`` — non-allow-listed ``](file.md)`` links found OUTSIDE the two floor sections
      (each ``{file, section}``; section is ``"(preamble)"`` before the first header).
    - ``missing_targets`` — floor-section pointers whose target file is absent from memory_dir.

    READ-ONLY; never raises (returns empty sets on any failure).
    """
    result: Dict[str, List[dict]] = {"rebloat": [], "missing_targets": []}
    try:
        with open(_floor_path(memory_dir), "r", encoding="utf-8") as fh:
            text = fh.read()
    except Exception:
        return result

    current = "(preamble)"
    for raw in text.split("\n"):
        line = raw.rstrip("\n")
        stripped = line.strip()
        if stripped.startswith("## "):
            current = stripped[3:].strip()
            continue
        if stripped.startswith("# "):  # the H1 title — not a section
            continue
        for m in _MD_LINK_RE.finditer(line):
            target = m.group(1)
            base = target.rsplit("/", 1)[-1]
            if base in _ALLOWLIST:
                continue
            if current in _FLOOR_SECTIONS:
                # A genuine floor pointer — check the target file exists (link rot).
                if not os.path.exists(os.path.join(memory_dir, target)):
                    result["missing_targets"].append({"file": base, "section": current})
            else:
                # A memory link outside the floor sections — re-bloat.
                result["rebloat"].append({"file": base, "section": current})
    return result


def floor_memory_names(memory_dir: str) -> set:
    """Slug names (no ``.md``) of the memory pointers pinned in the MEMORY.md floor.

    These are the User + Working-Style memories ALREADY always-loaded in full, so the recall
    DISPLAY layer (``recall.main``) drops them from per-prompt results — re-surfacing a memory
    the agent already has wastes a top-k slot + injects redundant tokens — and tops off from
    the fused tail. The COMPLEMENT of ``floor_violations``: the same section walk, but it
    COLLECTS the in-floor pointers instead of flagging the out-of-floor ones. ``MEMORY.full.md``
    / ``MEMORY.md`` restore links are excluded (not memory entries). Read-only; never raises;
    empty set on any failure.
    """
    names: set = set()
    try:
        with open(_floor_path(memory_dir), "r", encoding="utf-8") as fh:
            text = fh.read()
    except Exception:
        return names
    current = "(preamble)"
    for raw in text.split("\n"):
        stripped = raw.strip()
        if stripped.startswith("## "):
            current = stripped[3:].strip()
            continue
        if stripped.startswith("# "):  # the H1 title — not a section
            continue
        if current not in _FLOOR_SECTIONS:
            continue
        for m in _MD_LINK_RE.finditer(raw):
            base = m.group(1).rsplit("/", 1)[-1]
            if base in _ALLOWLIST:
                continue
            names.add(base[:-3] if base.endswith(".md") else base)
    return names


def floor_producer(
    memory_dir: str, repo_root: str, ctx: Optional[RunContext] = None
) -> "str | None":
    """SessionStart producer: SILENT when the floor invariant holds; one bounded block when not.

    Lists project/reference links that re-bloat the floor (and any floor link rot). READ-ONLY;
    never raises. ``ctx`` (LIF-6's shared per-run ``RunContext``) is unused here — declared
    only so every producer in ``PRODUCERS`` shares ONE call shape.
    """
    try:
        v = floor_violations(memory_dir)
        rebloat = v.get("rebloat", [])
        missing = v.get("missing_targets", [])
        if not rebloat and not missing:
            return None
        lines: List[str] = []
        if rebloat:
            lines.append(
                f"⚠ Memory floor re-bloat — {len(rebloat)} project/reference link(s) found "
                "OUTSIDE the User + Working-Style sections of MEMORY.md (the always-loaded floor "
                "must stay lean; these belong in on-demand recall, not the floor):"
            )
            for item in rebloat[:_MAX_ITEMS]:
                lines.append(f"  • {item['file']} (under '{item['section']}')")
            if len(rebloat) > _MAX_ITEMS:
                lines.append(f"  …and {len(rebloat) - _MAX_ITEMS} more.")
        if missing:
            lines.append(
                f"⚠ Memory floor link rot — {len(missing)} floor pointer(s) target a missing file:"
            )
            for item in missing[:_MAX_ITEMS]:
                lines.append(f"  • {item['file']} (under '{item['section']}')")
            if len(missing) > _MAX_ITEMS:
                lines.append(f"  …and {len(missing) - _MAX_ITEMS} more.")
        out = "\n".join(lines)
        if len(out) > _MAX_CHARS:
            out = out[: _MAX_CHARS - 16].rstrip() + "\n…(truncated)"
        return out
    except Exception:
        return None


def main(argv=None) -> int:
    import argparse

    from .provenance import resolve_dirs

    parser = argparse.ArgumentParser(description="Lint the MEMORY.md floor invariant (read-only).")
    parser.add_argument("--memory-dir", default=None)
    args = parser.parse_args(argv)

    memory_dir, _ = resolve_dirs()
    memory_dir = args.memory_dir or memory_dir

    v = floor_violations(memory_dir)
    rebloat, missing = v["rebloat"], v["missing_targets"]
    if not rebloat and not missing:
        print("MEMORY.md floor invariant holds ✅ (memory links only under User + Working-Style)")
        return 0
    if rebloat:
        print(f"floor re-bloat ({len(rebloat)}): project/reference links outside the floor sections")
        for item in rebloat:
            print(f"  • {item['file']} (under '{item['section']}')")
    if missing:
        print(f"floor link rot ({len(missing)}): floor pointers to missing files")
        for item in missing:
            print(f"  • {item['file']} (under '{item['section']}')")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
