"""GAT-1: the write gate's checker — does this change leave a budget over AND make it worse?

Pure: the caller hands in the BEFORE and AFTER text of one corpus file (``None`` for a file
that does not exist on that side) plus the corpus policy it already read, and gets back a
list of findings. Nothing here reads the disk, runs git or decides what to do with a
finding; the PreToolUse hook (``gate_hook``) and the commit-time verb share it, so the
write moment and the commit moment can never judge the same bytes differently.

**The delta posture.** A measure objects only when the AFTER side is over its budget and
worse than the BEFORE side. Standing debt never objects: an edit that leaves an over-budget
floor the same size, or smaller, passes; so does a typo fix on a line that already carried a
banned token. That is what makes a gate usable on a corpus that is over budget the day it is
installed (the field corpus that motivated it had 661 of 1,066 bodies matching its own
declared pattern).

**What is measured** (every definition is ``lint_floor``'s, via ``measure_floor``, so the
gate and the lint cannot drift):

- the floor (``MEMORY.md``): bytes against the warn line, lines against the line warn line
  (both the harness read window, lowered by a declared ``floor_lint`` value), lines over the
  declared ``max_line``, lines matching the declared ``banned_re``, and each declared section
  budget. A caller may add hard budgets of its own (``extra``), judged the same way;
- a memory body (any other memory file, frontmatter excluded): lines matching the declared
  ``banned_re``. No pattern declared, no body check.

Counts, not line identities: "worse" means more of something. Editing one flagged line into
another flagged line holds the count and is not an objection; the line-identity rule a
hand-built gate used would object to any edit of a debt line, which is standing debt
blocking an unrelated write. Never raises.
"""

from __future__ import annotations

from typing import Dict, List, Optional

FLOOR_NAME = "MEMORY.md"


def _measure(text: Optional[str], policy: dict) -> dict:
    from .lint_floor import measure_floor, section_bytes

    raw = None if text is None else text.encode("utf-8")
    m = measure_floor(raw, policy)
    m["sections"] = section_bytes(text or "")
    return m


def _new_lines(before_rows: List[dict], after_rows: List[dict], before: str, after: str) -> List[int]:
    """Up to three AFTER line numbers whose flagged text the BEFORE side did not carry —
    where to look, never the text itself (the reason may reach a model's context)."""
    seen = {}
    b_lines = before.split("\n")
    for r in before_rows:
        txt = b_lines[r["line"] - 1] if 0 < r["line"] <= len(b_lines) else ""
        seen[txt] = seen.get(txt, 0) + 1
    out: List[int] = []
    a_lines = after.split("\n")
    for r in after_rows:
        txt = a_lines[r["line"] - 1] if 0 < r["line"] <= len(a_lines) else ""
        if seen.get(txt, 0) > 0:
            seen[txt] -= 1
            continue
        out.append(r["line"])
        if len(out) == 3:
            break
    return out


def _finding(kind: str, what: str, before: int, after: int, budget: int, file: str, **extra) -> dict:
    row = {"kind": kind, "what": what, "before": before, "after": after, "budget": budget, "file": file}
    row.update(extra)
    return row


def judge_floor(
    before: Optional[str],
    after: Optional[str],
    policy: dict,
    extra: Optional[Dict[str, int]] = None,
) -> List[dict]:
    """Findings for one change to ``MEMORY.md``. ``policy`` is ``read_floor_lint``'s dict;
    ``extra`` adds hard budgets ``{"floor_bytes": N, "floor_lines": N}`` (GAT-2's
    ``budgets``), judged with the same posture. A deleted floor (``after`` None) objects to
    nothing."""
    out: List[dict] = []
    try:
        if after is None:
            return out
        b, a = _measure(before, policy), _measure(after, policy)
        checks = [
            ("floor_bytes_warn", "floor bytes", b["bytes"], a["bytes"], policy["warn_bytes"]),
            ("floor_lines_warn", "floor lines", b["lines"], a["lines"], policy["warn_lines"]),
        ]
        for key, label, bv, av in (
            ("floor_bytes", "floor bytes", b["bytes"], a["bytes"]),
            ("floor_lines", "floor lines", b["lines"], a["lines"]),
        ):
            cap = (extra or {}).get(key)
            if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0:
                checks.append((key, label, bv, av, cap))
        for kind, label, bv, av, cap in checks:
            if av > cap and av > bv:
                extra_info = {}
                if kind == "floor_bytes_warn" and av > policy["cap_bytes"]:
                    extra_info["past_read_limit"] = policy["cap_bytes"]
                if kind == "floor_lines_warn" and av > policy["cap_lines"]:
                    extra_info["past_read_limit"] = policy["cap_lines"]
                out.append(_finding(kind, label, bv, av, cap, FLOOR_NAME, **extra_info))
        if policy.get("max_line"):
            bl, al = len(b["longlines"]), len(a["longlines"])
            if al > bl:
                out.append(
                    _finding(
                        "floor_longlines",
                        f"floor lines over {policy['max_line']} characters",
                        bl, al, 0, FLOOR_NAME,
                        lines=_new_lines(b["longlines"], a["longlines"], before or "", after),
                    )
                )
        if policy.get("banned_re"):
            bb, ab = len(b["banned"]), len(a["banned"])
            if ab > bb:
                out.append(
                    _finding(
                        "floor_banned", "floor lines matching the banned pattern", bb, ab, 0,
                        FLOOR_NAME, lines=_new_lines(b["banned"], a["banned"], before or "", after),
                    )
                )
        for sec, cap in (policy.get("section_budgets") or {}).items():
            bv, av = b["sections"].get(sec, 0), a["sections"].get(sec, 0)
            if av > cap and av > bv:
                out.append(_finding("section_budget", f"bytes in '{sec}'", bv, av, cap, FLOOR_NAME))
    except Exception:
        return []
    return out


def body_of(text: Optional[str]) -> str:
    """A memory file's body: everything after the frontmatter block (the whole text when it
    has none)."""
    if not text:
        return ""
    from .provenance import split_frontmatter

    return split_frontmatter(text)[1]


def judge_body(before: Optional[str], after: Optional[str], policy: dict, file: str) -> List[dict]:
    """Findings for one change to a memory file's body: more lines matching the corpus's
    declared ``banned_re`` than before. No pattern declared, or the file deleted: none."""
    try:
        pattern = policy.get("banned_re")
        if not pattern or after is None:
            return []
        from .lint_floor import banned_lines

        bt, at = body_of(before), body_of(after)
        br, ar = banned_lines(bt, pattern), banned_lines(at, pattern)
        if len(ar) <= len(br):
            return []
        # Line numbers in the AFTER file, not its body: that is what an editor shows.
        offset = (after.count("\n") - at.count("\n")) if at else 0
        lines = [n + offset for n in _new_lines(br, ar, bt, at)]
        return [
            _finding(
                "body_banned", "body lines matching the banned pattern", len(br), len(ar), 0, file,
                lines=lines,
            )
        ]
    except Exception:
        return []


def judge_file(name: str, before: Optional[str], after: Optional[str], policy: dict, extra=None) -> List[dict]:
    """Dispatch on the filename: the floor, a memory file, or (anything else) nothing."""
    from .provenance import _is_memory_filename

    if name == FLOOR_NAME:
        return judge_floor(before, after, policy, extra)
    if _is_memory_filename(name):
        return judge_body(before, after, policy, name)
    return []


_REMEDY = {
    "floor_bytes_warn": "move detail out of the floor line into the memory file it links, or trim another line in the same edit",
    "floor_lines_warn": "fold pointers into fewer lines, or drop one in the same edit",
    "floor_bytes": "trim the floor in the same change, or raise the budget in hippo.json deliberately",
    "floor_lines": "drop or merge floor lines in the same change, or raise the budget in hippo.json deliberately",
    "floor_longlines": "keep a floor line to a pointer and a short hook; the detail belongs in the linked file",
    "floor_banned": "the floor names a memory and its lesson; status words, PR numbers and hashes belong in the tracker the memory cites",
    "section_budget": "fold a line out of that section before adding one",
    "body_banned": "cite the tracker id and let the tracker hold the status, instead of retyping it in the memory",
}


def describe(f: dict) -> str:
    """One plain line for a finding: what, before and after, the budget, where to look."""
    try:
        if f["budget"]:
            head = f"{f['what']} {f['before']:,} -> {f['after']:,}, over the budget of {f['budget']:,}"
        else:
            head = f"{f['what']} {f['before']:,} -> {f['after']:,}"
        if f.get("past_read_limit"):
            head += f" and past the {f['past_read_limit']:,} read limit (the tail stops loading)"
        if f.get("lines"):
            head += " (new at line " + ", ".join(str(n) for n in f["lines"]) + ")"
        remedy = _REMEDY.get(f["kind"])
        return f"{f['file']}: {head}; {remedy}." if remedy else f"{f['file']}: {head}."
    except Exception:
        return str(f.get("what") or "a budget got worse")
