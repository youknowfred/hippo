"""TND-5: one ``Related:`` line per memory — the writer's merge, a read-only scan, a fixer.

``new_memory`` closes a new memory's body with ``Related: [[a]], [[b]]`` (GRA-3). When the
agent-written body ALREADY ended with its own ``Related:`` line, the writer used to append a
second one beneath it — on the dogfood corpus about a fifth of the memories carry two. The
convention is one line: ``merge_related_line`` adds new names to the body's existing line
(union, order kept, deduped) and appends a fresh line only when the body has none.

For files already carrying more than one line, ``scan_duplicate_related`` is a read-only
listing (``hippo lint-links`` prints it; the maintenance queue can consume it), and
``fix_duplicate_related`` merges ONE named file. Nothing here runs on a corpus by itself:
the fixer is per item and human-invoked (``hippo links --merge-related <name>``), and it
folds its own write into the trust baseline the way every hippo write primitive does.

A ``Related:`` line inside a closed fenced code block is documentation, not the body's line
(the masking rule ``markdown_code`` states for every lint).
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

_RELATED_RE = re.compile(r"^ {0,3}Related:")
_LINK_RE = re.compile(r"\[\[([^\]\n]+)\]\]")
# A "pure" line holds only wikilinks and the commas between them — merging it is lossless.
_PURE_RE = re.compile(r"^ {0,3}Related:\s*(\[\[[^\]\n]+\]\]\s*(,\s*\[\[[^\]\n]+\]\]\s*)*)?,?\s*$")
_FENCE_RE = re.compile(r"^(```+|~~~+)")


def _fenced_lines(lines: List[str]) -> List[bool]:
    """Per line: inside a CLOSED fenced block (fence lines included). An unclosed fence
    masks nothing — the conservative direction ``markdown_code`` takes."""
    mask = [False] * len(lines)
    i = 0
    while i < len(lines):
        m = _FENCE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        fence = m.group(1)
        close = next(
            (j for j in range(i + 1, len(lines)) if lines[j].startswith(fence[0] * len(fence))),
            None,
        )
        if close is None:
            i += 1
            continue
        for j in range(i, close + 1):
            mask[j] = True
        i = close + 1
    return mask


def related_line_indices(lines: List[str]) -> List[int]:
    """Indices of the body's ``Related:`` lines, fenced examples excluded."""
    mask = _fenced_lines(lines)
    return [i for i, ln in enumerate(lines) if not mask[i] and _RELATED_RE.match(ln)]


def _names(line: str) -> List[str]:
    out: List[str] = []
    for raw in _LINK_RE.findall(line):
        name = raw.split("|", 1)[0].split("#", 1)[0].strip()
        if name and name not in out:
            out.append(name)
    return out


def _with_names(line: str, extra: List[str]) -> str:
    """``line`` with ``extra`` links appended (comma-joined; nothing else changes)."""
    if not extra:
        return line
    links = ", ".join(f"[[{n}]]" for n in extra)
    head = line.rstrip()
    if _LINK_RE.search(head):
        return f"{head}, {links}"
    return f"{head} {links}" if head.endswith(":") else f"{head}, {links}"


def merge_related_line(body: str, related: List[str]) -> str:
    """The body with ``related`` merged into its ``Related:`` line — appended as a final
    ``Related: [[a]], [[b]]`` line only when the body has none.

    Names the body's Related line(s) already carry are not repeated; the rest join the LAST
    such line in the given order. Additive only: no existing text is rewritten. The result
    ends with a newline, as the append always did.
    """
    if not related:
        return body
    text = (body or "").rstrip("\n")
    lines = text.split("\n") if text else []
    idx = related_line_indices(lines)
    if not idx:
        line = "Related: " + ", ".join(f"[[{r}]]" for r in related)
        return f"{text}\n\n{line}\n" if text else f"{line}\n"
    have: List[str] = []
    for i in idx:
        have += [n for n in _names(lines[i]) if n not in have]
    extra: List[str] = []
    for r in related:
        if r and r not in have and r not in extra:
            extra.append(r)
    lines[idx[-1]] = _with_names(lines[idx[-1]], extra)
    return "\n".join(lines) + "\n"


def _split_frontmatter(text: str) -> Tuple[str, str]:
    """``(frontmatter_with_fences, body)``; ``("", text)`` when there is none."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[: end + 5], text[end + 5:]
    return "", text


def merged_text(text: str) -> Tuple[Optional[str], Optional[str]]:
    """``(new_text, refusal)`` for one memory file's text.

    ``(None, None)`` when the body has at most one ``Related:`` line (nothing to do).
    Otherwise the lines merge into ONE: the union of their links, in order of first
    appearance. A line carrying prose besides its links (an annotation) is the one kept,
    with the other lines' new links added to its end; with two or more annotated lines the
    merge cannot be lossless, so it refuses and names why. The other lines are removed, and
    a blank line left doubled by a removal goes with it. Pure; never raises.
    """
    try:
        fm, body = _split_frontmatter(text)
        lines = body.split("\n")
        idx = related_line_indices(lines)
        if len(idx) < 2:
            return None, None
        annotated = [i for i in idx if not _PURE_RE.match(lines[i])]
        if len(annotated) > 1:
            return None, (
                f"{len(annotated)} of its Related lines carry notes besides their links — "
                "merge them by hand"
            )
        union: List[str] = []
        for i in idx:
            union += [n for n in _names(lines[i]) if n not in union]
        host = annotated[0] if annotated else idx[-1]
        if annotated:
            lines[host] = _with_names(lines[host], [n for n in union if n not in _names(lines[host])])
        else:
            lines[host] = "Related: " + ", ".join(f"[[{n}]]" for n in union)
        drop = set(i for i in idx if i != host)
        out: List[str] = []
        for i, ln in enumerate(lines):
            if i in drop:
                # The removed line's leading blank goes too when a blank (or the end of the
                # file) follows it — otherwise the merge would leave a doubled gap.
                after = lines[i + 1] if i + 1 < len(lines) else ""
                if out and out[-1].strip() == "" and after.strip() == "":
                    out.pop()
                continue
            out.append(ln)
        return fm + "\n".join(out), None
    except Exception as exc:
        return None, f"could not merge: {exc}"


def scan_duplicate_related(memory_dir: str) -> List[Dict]:
    """READ-ONLY: every memory whose body carries more than one ``Related:`` line.

    ``[{"name", "path", "count", "lines"}]`` sorted by name, ``lines`` being 1-based line
    numbers in the file. Writes nothing. Never raises (an unreadable file is skipped).
    """
    out: List[Dict] = []
    try:
        from .provenance import _iter_memory_files

        paths = list(_iter_memory_files(memory_dir))
    except Exception:
        return out
    for path in sorted(paths):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except Exception:
            continue
        fm, body = _split_frontmatter(text)
        lines = body.split("\n")
        idx = related_line_indices(lines)
        if len(idx) < 2:
            continue
        offset = fm.count("\n")
        out.append({
            "name": os.path.splitext(os.path.basename(path))[0],
            "path": path,
            "count": len(idx),
            "lines": [offset + i + 1 for i in idx],
        })
    out.sort(key=lambda r: r["name"])
    return out


def fix_duplicate_related(path: str, *, memory_dir: Optional[str] = None) -> Dict:
    """Merge ONE memory file's ``Related:`` lines into one (see ``merged_text``).

    Per item and human-invoked; never part of an automatic pass. A compare-and-swap write:
    a file that changed between the read and the write is left alone and the conflict is
    named. On success the new bytes join the trust baseline (authorship is consent, the
    ``add_typed_relation`` posture). Returns ``{"path", "fixed", "error", "note"}``; never
    raises.
    """
    res: Dict = {"path": path, "fixed": False, "error": None, "note": None}
    try:
        from .atomic import read_text_cas, write_text_cas

        text, token = read_text_cas(path)
        new, refusal = merged_text(text)
        if refusal:
            res["error"] = refusal
            return res
        if new is None or new == text:
            return res
        write_text_cas(path, new, token)
        res["fixed"] = True
    except Exception as exc:
        res["error"] = f"merge failed, file unchanged: {exc}"
        return res
    try:
        from .trust import record_authored_write_disclosing

        res["note"] = record_authored_write_disclosing(memory_dir or os.path.dirname(path), path)
    except Exception:
        pass
    return res
