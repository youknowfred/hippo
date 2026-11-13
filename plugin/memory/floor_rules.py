"""RUL-10: a floor line that restates a rule (read-only, informational).

The floor (``MEMORY.md``) and the always-loaded governance files (``CLAUDE.md`` and the
``.claude/rules`` files without a ``paths:`` scope) both ride every session. A floor pointer
line whose own text a rule already carries spends floor bytes saying the same thing twice,
so the line can leave the floor: the memory stays in the corpus and recall still finds it.

The check is RUL-3's write-time duplicate-of-rule check (``rules_plane.rule_dup_candidates``,
same tokenizer, same containment threshold, same minimum draft size), run over each floor
pointer line's OWN text (the link title and its description, link target dropped — never the
memory body) against the always-loaded files only. A path-scoped rule loads only when a
matching file is touched, so a floor line that repeats one still earns its place.

One change from the write-time check, measured: a rule is scored per LIST ITEM, not per
blank-line block. A governance file is mostly long bullet lists with no blank lines between
items, so a block holds hundreds of words and a ten-word floor line is "contained" in it by
chance. On the field corpora at the same 0.6 threshold, block scoring reported 12 lines of
which 6 were restatements; item scoring reported those 6 and nothing else.

Informational by contract: a finding never fails the commit gate or the write gate (both
judge ``lint_floor.measure_floor``, which this module does not touch), never counts toward
their delta posture, and never changes ``hippo lint-floor``'s exit status. It shows in
``hippo lint-floor``, in doctor's floor line, and as a tend ``floor`` item.

Acknowledging (TND-13's ``acknowledged_findings`` list): ``file`` ``MEMORY.md``, ``rule``
``restates-rule``, ``sha256`` of the floor's bytes when reviewed. Any edit to the floor
re-arms it, as for every acknowledged finding. One difference from the secret and threat
lints, on purpose: there is no consent leg. ``MEMORY.md`` sits outside the trust fingerprint
(``trust.corpus_fingerprint``), so a consent match could never hold, and the leg exists so a
pulled acknowledgement cannot hide a SECURITY finding; this one is advice about floor bytes.

Never raises; reads only.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import List, Optional

RULE_ID = "restates-rule"
FLOOR_FILE = "MEMORY.md"

# The governance files the harness loads into every session, relative to the repo root.
# ``.claude/rules/*.md`` joins only when it declares no ``paths:`` scope.
_ALWAYS_LOADED = ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")

_LINK_TARGET_RE = re.compile(r"\]\([^)]*\)")


def always_loaded_files(repo_root: str) -> List[str]:
    """Absolute paths of the governance files every session loads. Never raises."""
    out: List[str] = []
    try:
        from .rules_plane import rule_paths_globs

        root = Path(repo_root)
        for rel in _ALWAYS_LOADED:
            p = root / rel
            if p.is_file():
                out.append(str(p))
        for p in sorted(root.glob(".claude/rules/*.md")):
            try:
                if p.is_file() and not rule_paths_globs(p.read_text(encoding="utf-8")):
                    out.append(str(p))
            except Exception:
                continue
    except Exception:
        return out
    return out


def _floor_file(memory_dir: str) -> str:
    try:
        from .outbox import effective_path  # OUT-1: the floor as this machine sees it

        return effective_path(memory_dir, "MEMORY")
    except Exception:
        return os.path.join(memory_dir, FLOOR_FILE)


def line_text(raw: str) -> str:
    """A pointer line's own words: bullet, brackets and link targets dropped."""
    text = _LINK_TARGET_RE.sub("]", raw.strip())
    text = text.lstrip("-*+ ").replace("[", "").replace("]", "")
    return " ".join(text.split())


def pointer_lines(text: str, sections, is_memory=None) -> List[dict]:
    """``[{line, name, text}]`` — each memory pointer line under a floor section (the same
    walk ``lint_floor.floor_memory_names`` makes; restore links excluded). With ``is_memory``
    (``lint_floor.memory_exists``) a line with no link counts when it is a bare pointer,
    ``- name — text``, to a memory that exists: ``lint_floor.pointer_name``'s reading."""
    from .lint_floor import _ALLOWLIST, _MD_LINK_RE, pointer_name

    rows: List[dict] = []
    current = "(preamble)"
    for i, raw in enumerate(text.split("\n"), start=1):
        stripped = raw.strip()
        if stripped.startswith("## "):
            current = stripped[3:].strip()
            continue
        if stripped.startswith("# ") or current not in sections:
            continue
        linked = False
        for m in _MD_LINK_RE.finditer(raw):
            linked = True
            base = m.group(1).rsplit("/", 1)[-1]
            if base in _ALLOWLIST:
                continue
            rows.append({"line": i, "name": base[:-3], "text": line_text(raw)})
            break
        if not linked and is_memory is not None:
            name = pointer_name(raw, is_memory)
            if name:
                rows.append({"line": i, "name": name, "text": line_text(raw)})
    return rows


def _acknowledgement(memory_dir: str, sha: Optional[str]) -> tuple:
    """``(declared, matches, stale)`` for this floor's ``restates-rule`` entries."""
    try:
        from .lint_ack import read_acknowledgements

        declared, entries = read_acknowledgements(memory_dir)
        mine = [e for e in entries if e["file"] == FLOOR_FILE and e["rule"] == RULE_ID]
        matches = bool(sha) and any(e["sha256"] == sha for e in mine)
        return declared, matches, bool(mine) and not matches
    except Exception:
        return False, False, False


def floor_restatements(memory_dir: str, repo_root: str) -> dict:
    """Every floor pointer line an always-loaded rule already carries.

    ``{"rows": [{line, name, file, score, preview}], "acknowledged": n, "stale": bool,
    "declared": bool, "sha256": str|None, "files": n}`` — ``rows`` are what still warns;
    ``acknowledged`` counts the lines a matching acknowledgement covers; ``stale`` says one
    exists for an older floor. Never raises; empty on any failure.
    """
    out = {"rows": [], "acknowledged": 0, "stale": False, "declared": False,
           "sha256": None, "files": 0}
    try:
        files = always_loaded_files(repo_root)
        out["files"] = len(files)
        if not files:
            return out
        from .lint_floor import _floor_sections, memory_exists
        from .rules_plane import rule_dup_candidates

        with open(_floor_file(memory_dir), "rb") as fh:
            raw = fh.read()
        out["sha256"] = hashlib.sha256(raw).hexdigest()
        rows: List[dict] = []
        for p in pointer_lines(raw.decode("utf-8", "replace"), _floor_sections(memory_dir),
                               memory_exists(memory_dir)):
            cands = rule_dup_candidates(p["text"], "", repo_root, files=files, items=True)
            if cands:
                c = cands[0]
                rows.append({"line": p["line"], "name": p["name"], "file": c["file"],
                             "score": c["score"], "preview": c["preview"]})
        if not rows:
            return out
        declared, acked, stale = _acknowledgement(memory_dir, out["sha256"])
        out["declared"], out["stale"] = declared, stale
        if acked:
            out["acknowledged"] = len(rows)
        else:
            out["rows"] = rows
        return out
    except Exception:
        return out


def render_lines(rep: dict) -> List[str]:
    """``hippo lint-floor``'s block: ``[]`` when there is nothing to say."""
    rows = rep.get("rows") or []
    lines: List[str] = []
    if rows:
        lines.append(
            f"floor lines that restate a rule ({len(rows)}, informational): an always-loaded "
            "rule already says this, so the line can leave the floor (recall still finds the memory)"
        )
        for r in rows:
            lines.append(f"  • L{r['line']} {r['name']} — {r['file']} (overlap {r['score']:.2f}): {r['preview']}")
        if rep.get("stale"):
            lines.append(f"  The {RULE_ID} acknowledgement for {FLOOR_FILE} no longer matches: the floor changed since it was reviewed.")
        lines.append(
            f"  To keep them, acknowledge {FLOOR_FILE} with rule {RULE_ID} and sha256 "
            f"{rep.get('sha256')} in hippo.json acknowledged_findings."
        )
    elif rep.get("acknowledged"):
        lines.append(f"{rep['acknowledged']} floor line(s) restate a rule, acknowledged (not warned).")
    return lines


def repo_root_of(memory_dir: str) -> Optional[str]:
    """The repo a ``<repo>/.claude/memory`` corpus sits in; None for any other layout."""
    d = os.path.abspath(memory_dir)
    parent = os.path.dirname(d)
    if os.path.basename(d) == "memory" and os.path.basename(parent) == ".claude":
        return os.path.dirname(parent)
    return None


def print_report(memory_dir: str, repo_root: Optional[str]) -> None:
    """``hippo lint-floor``'s RUL-10 block (``repo_root`` None: the corpus's own repo)."""
    root = repo_root or repo_root_of(memory_dir)
    if root:
        for line in render_lines(floor_restatements(memory_dir, root)):
            print(line)


def doctor_sentence(rep: dict) -> str:
    """The sentence doctor's floor line appends ('' when there is nothing to say)."""
    n = len(rep.get("rows") or [])
    if n:
        return (f" {n} floor line(s) restate an always-loaded rule and could leave the floor "
                "(informational; `hippo lint-floor` lists them).")
    if rep.get("acknowledged"):
        return f" {rep['acknowledged']} floor line(s) restating a rule are acknowledged."
    return ""


def tend_entries(memory_dir: str, repo_root: str) -> List[dict]:
    """One tend ``floor`` item per restating line. Never raises."""
    try:
        from .tend_queue import _entry

        return [
            _entry(
                "floor", f"rule:{r['name']}.md",
                f"its floor line restates {r['file']}, which loads every session "
                f"(overlap {r['score']:.2f}): {r['preview']}",
                "remove the line from the floor (recall still finds the memory), or keep it "
                f"by acknowledging {RULE_ID} in hippo.json (`hippo lint-floor` prints the hash)",
            )
            for r in floor_restatements(memory_dir, repo_root)["rows"]
        ]
    except Exception:
        return []
