"""RUL-7 — /hippo:export-agents: the floor rendered as a PROPOSED AGENTS.md diff.

Teams run multiple coding agents (Codex/Cursor/Copilot/…), each reading ``AGENTS.md`` —
the Linux-Foundation cross-tool rules file — which is always-loaded, unranked, and
hand-drifting. hippo already treats it as a first-class READ surface (rules_plane's
``GOV_GLOBS``). This module is the one write-SHAPED bridge the roadmap allows: render the
project floor (the curated always-load core pinned in the corpus ``MEMORY.md``) as a
complete proposed ``AGENTS.md``, diff it against what exists, and STOP. Nothing here
writes the file — the skill shows the diff and a human applies it (inv1: the corpus stays
the one authority; inv4: per-decision, agent-gated). One shot, no auto-sync cadence.

Shape of the proposal:

- YAML frontmatter carrying ``paths:`` globs derived from the floor memories'
  ``cited_paths`` (``rules_plane.derive_paths_globs`` — the derivation RUL-6 will share,
  over-scoping cap included), every glob JSON-quoted because a bare leading ``*`` is a
  YAML alias (the RCH-2 premise-correction lesson). Emitted only when the existing file's
  frontmatter is ours or absent — a FOREIGN frontmatter is preserved byte-verbatim.
- A marker-delimited managed block: one ``## `stem` `` section per floor memory — an
  ``Applies to:`` line from the derived globs, then the body verbatim with
  ``[[wikilinks]]`` rewritten to backtick stems. Content OUTSIDE the markers is preserved
  byte-verbatim: propose a diff, never regenerate someone's hand-maintained file.

Why the PROJECT floor only: ``AGENTS.md`` is repo-committed, and the user/private tiers
must never enter the project's git history (the no-git-leak invariant).
``portable_floor_producer`` stays the cross-tier CONTEXT channel; this file is the repo's
own. Retired floor memories (``invalid_after``) are skipped with a reason, mirroring
promote's refusal — a demoted lesson does not fan out to other tools.

The drift-check story (the second acceptance criterion) is deliberately all-reuse: the
section headings' backtick stems make every exported memory a governance citation
(``archive._SCAN_TARGETS`` now includes ``AGENTS.md``, so exported memories are
archive-protected; ``conflict_radar`` sees authority gaps), code-extension refs in the
``Applies to:`` lines rot-check via ``rules_plane.rules_rot``'s code-ref leg (its
``GOV_GLOBS`` always covered ``AGENTS.md``), and the frontmatter globs dead-glob-check
via the widened ``rules_plane._rule_scoped_files`` — all surfacing loud in the existing
doctor + SessionStart channels. A cited path moves → the exported file flags, with zero
new reporting surface.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from typing import Dict, List, Optional, Tuple

from .links import _WIKILINK_RE
from .lint_floor import floor_memory_names
from .provenance import build_repo_file_index
from .rules_plane import _repo_paths_for_globs, derive_paths_globs
from .staleness import read_invalid_after, read_provenance

_AGENTS_BASENAME = "AGENTS.md"

# The managed-block fences. Exact-match constants: the splice point is FOUND, never
# guessed — a begin without an end is a refusal, not a heuristic repair.
BLOCK_BEGIN = "<!-- hippo:agents-export:begin -->"
BLOCK_END = "<!-- hippo:agents-export:end -->"

# The ours-marker inside YAML frontmatter (a YAML comment — inert to parsers). Its
# presence means the frontmatter is regenerated on re-export; its absence from an
# existing frontmatter means FOREIGN: preserved verbatim, our ``paths:`` not emitted.
_FM_MARKER = "# hippo:agents-export — derived from memory cited_paths; regenerated on re-export"

_PREAMBLE = (
    "Exported from the hippo memory floor by the export-agents skill. The hippo corpus\n"
    "is the authority: edit the memories and re-export — hand-edits inside this block\n"
    "are replaced by the next export. Content outside the markers is never touched."
)


def _split_raw_frontmatter(text: str) -> Tuple[str, str]:
    """``(frontmatter, rest)`` with the frontmatter INCLUSIVE of both ``---`` fences and
    the trailing newline; ``("", text)`` when the file has none (or the fence never
    closes — never guess a splice point). Byte-exact, so a foreign frontmatter can be
    preserved verbatim."""
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end == -1:
        return "", text
    return text[: end + 5], text[end + 5 :]


def _render_body(text: str) -> str:
    """The memory body with ``[[wikilinks]]`` rewritten to backtick stems (``|display``
    and ``#anchor`` dropped): AGENTS.md consumers don't resolve wikilinks, and a backtick
    stem is exactly the citation shape the governance scanners already track."""
    _fm, body = _split_raw_frontmatter(text)

    def _sub(m: "re.Match") -> str:
        target = m.group(1).split("|")[0].split("#")[0].strip()
        return f"`{target}`" if target else ""

    return _WIKILINK_RE.sub(_sub, body).strip()


def _render_frontmatter(paths_globs: List[str]) -> str:
    """Our frontmatter block: the ours-marker + the JSON-quoted ``paths:`` union.
    JSON-quoting is load-bearing — an unquoted ``- *.py`` starts a YAML alias."""
    lines = ["---", _FM_MARKER, "paths:"]
    lines.extend(f"  - {json.dumps(g)}" for g in paths_globs)
    lines.append("---")
    return "\n".join(lines) + "\n"


def _render_block(items: List[dict]) -> str:
    """The managed block: preamble + one section per floor memory. The ``## `stem` ``
    heading is what makes the export a governance CITATION of the memory (archive
    protection + conflict radar); the ``Applies to:`` backtick globs are what the
    code-ref rot leg checks."""
    parts = [BLOCK_BEGIN, "", "# Agent rules — hippo floor export", "", _PREAMBLE]
    for it in items:
        parts.extend(["", f"## `{it['name']}`"])
        if it["globs"]:
            parts.extend(["", "Applies to: " + ", ".join(f"`{g}`" for g in it["globs"])])
        if it["body"]:
            parts.extend(["", it["body"]])
    parts.extend(["", BLOCK_END])
    return "\n".join(parts) + "\n"


def _splice(
    existing: Optional[str], fm_block: str, managed: str
) -> Tuple[Optional[str], bool, Optional[str]]:
    """Assemble the proposed file: ``(proposed, frontmatter_preserved, refusal_reason)``.

    No existing file → our frontmatter + the managed block. Existing file → its foreign
    frontmatter (if any) and everything outside the markers survive byte-verbatim; only
    our own frontmatter and the span between the markers regenerate.
    """
    if existing is None:
        return fm_block + managed, False, None
    fm_raw, rest = _split_raw_frontmatter(existing)
    if fm_raw and "hippo:agents-export" not in fm_raw:
        head, preserved = fm_raw, True
    else:
        head, preserved = fm_block, False
    if BLOCK_BEGIN in rest:
        b = rest.index(BLOCK_BEGIN)
        e = rest.find(BLOCK_END, b)
        if e == -1:
            return (
                None,
                False,
                "corrupt managed block in AGENTS.md (begin marker without end) — "
                "repair the file by hand, then re-run",
            )
        body = rest[:b] + managed.rstrip("\n") + rest[e + len(BLOCK_END) :]
    else:
        base = rest.rstrip("\n")
        body = (base + "\n\n" if base else "") + managed
    return head + body, preserved, None


def _diff(existing: Optional[str], proposed: str) -> str:
    """Unified diff, current → proposed; ``""`` when they are identical."""
    a = existing.splitlines(keepends=True) if existing is not None else []
    return "".join(
        difflib.unified_diff(
            a,
            proposed.splitlines(keepends=True),
            fromfile=_AGENTS_BASENAME if existing is not None else "/dev/null",
            tofile=f"{_AGENTS_BASENAME} (proposed)",
        )
    )


def export_agents(*, memory_dir: str, repo_root: str) -> dict:
    """Render the project floor as a complete proposed ``AGENTS.md`` + unified diff.

    READ-ONLY: never writes ``AGENTS.md`` (or anything else) — the caller shows the diff
    and a human decides. Refusals return ``{"proposed": None, "reason": ...}`` with zero
    side effects. On success::

        {
          "proposed": str,              # the complete proposed file
          "diff": str,                  # unified diff current → proposed ("" = no change)
          "changed": bool,
          "exists": bool,               # AGENTS.md already present
          "items":   [{"name", "cited_paths", "globs", "flags"}],
          "skipped": [{"name", "reason"}],   # unreadable pointers, retired memories
          "paths_globs": [...],         # the frontmatter union actually emitted
          "frontmatter_preserved": bool,  # foreign frontmatter kept verbatim (ours dropped)
          "bytes": int,
        }
    """
    names = sorted(floor_memory_names(memory_dir))
    if not names:
        return {
            "proposed": None,
            "reason": "the MEMORY.md floor pins no memories — nothing to export",
        }
    repo_files, _basenames = build_repo_file_index(repo_root)
    universe = _repo_paths_for_globs(repo_root, repo_files) if repo_files else set()
    items: List[dict] = []
    skipped: List[Dict[str, str]] = []
    for name in names:
        try:
            with open(os.path.join(memory_dir, f"{name}.md"), "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            skipped.append({"name": name, "reason": "floor pointer without a readable file"})
            continue
        boundary = read_invalid_after(text)
        if boundary is not None:
            skipped.append({"name": name, "reason": f"retired (invalid_after {boundary})"})
            continue
        cited, _sc = read_provenance(text)
        globs, flags = derive_paths_globs(cited, universe)
        items.append(
            {"name": name, "cited_paths": cited, "globs": globs, "flags": flags,
             "body": _render_body(text)}
        )
    if not items:
        detail = "; ".join(f"{s['name']} — {s['reason']}" for s in skipped)
        return {
            "proposed": None,
            "reason": f"no exportable floor memory (all skipped: {detail})",
            "skipped": skipped,
        }
    paths_globs = sorted({g for it in items for g in it["globs"]})
    fm_block = _render_frontmatter(paths_globs) if paths_globs else ""
    agents_path = os.path.join(repo_root, _AGENTS_BASENAME)
    existing: Optional[str] = None
    if os.path.isfile(agents_path):
        with open(agents_path, "r", encoding="utf-8") as fh:
            existing = fh.read()
    proposed, fm_preserved, reason = _splice(existing, fm_block, _render_block(items))
    if proposed is None:
        return {"proposed": None, "reason": reason, "skipped": skipped}
    diff = _diff(existing, proposed)
    return {
        "proposed": proposed,
        "diff": diff,
        "changed": bool(diff),
        "exists": existing is not None,
        "items": [
            {"name": it["name"], "cited_paths": it["cited_paths"], "globs": it["globs"],
             "flags": it["flags"]}
            for it in items
        ],
        "skipped": skipped,
        "paths_globs": paths_globs if (fm_block and not fm_preserved) else [],
        "frontmatter_preserved": fm_preserved,
        "bytes": len(proposed.encode("utf-8")),
    }


def describe(result: dict) -> str:
    """Human-readable render of an ``export_agents`` result: header, per-item notes,
    every skip and derivation flag NAMED (inv3 — count what the machinery decided),
    then the reviewable diff."""
    if result.get("proposed") is None:
        return f"✘ export-agents refused: {result.get('reason', 'unknown')}"
    lines: List[str] = []
    state = (
        "no changes — AGENTS.md already matches the floor"
        if not result["changed"]
        else ("update to existing AGENTS.md" if result["exists"] else "new AGENTS.md")
    )
    lines.append(
        f"Proposed AGENTS.md ({len(result['items'])} section(s), {result['bytes']} bytes) "
        f"— {state}. Nothing written."
    )
    for it in result["items"]:
        scope = ", ".join(it["globs"]) if it["globs"] else "unscoped (no cited_paths)"
        lines.append(f"  • {it['name']} — {scope}")
        for f in it["flags"]:
            if f["kind"] == "over_scope":
                lines.append(
                    f"      ⚑ over-scope: {f['glob']} matches {f['matched']} files for "
                    f"{f['cited']} cited — kept as literal paths"
                )
            elif f["kind"] == "missing":
                lines.append(f"      ⚑ cited path missing from tree: {f['path']} (excluded)")
            elif f["kind"] == "no_oracle":
                lines.append("      ⚑ no git oracle — globs unvalidated literals")
    for s in result["skipped"]:
        lines.append(f"  ◦ skipped {s['name']} — {s['reason']}")
    if result["frontmatter_preserved"]:
        lines.append(
            "  ⚑ existing foreign frontmatter preserved verbatim — paths: globs NOT "
            "emitted (the dead-glob drift leg won't cover this file; the per-section "
            "Applies-to refs still rot-check)"
        )
    if result["changed"]:
        lines.append("")
        lines.append(result["diff"].rstrip("\n"))
    return "\n".join(lines)
