"""The MEMORY.md floor edits ``new_memory`` makes: the sorted pointer insert, its inverse, and
a non-project tier's first skeleton.

Split out of ``new_memory`` by RWY-3 as pure code motion (that module had 27 lines of
runway left), then moved onto ``atomic``'s compare-and-swap writes: MEMORY.md is the corpus's
highest-churn file, and two sessions appending pointers used to lose one of them.
``new_memory`` re-exports every name here, so existing imports and monkeypatch targets keep
resolving.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple


def _ensure_tier_floor(tier_dir: str, label: str) -> None:
    """Seed a NON-project tier's ``MEMORY.md`` with the two canonical floor sections the first
    time a memory is written there, so a ``user``/``feedback`` pointer has somewhere to land.
    Created once, minimally; never overwrites an existing floor. Never raises."""
    try:
        floor_path = os.path.join(tier_dir, "MEMORY.md")
        if os.path.exists(floor_path):
            return
        os.makedirs(tier_dir, exist_ok=True)
        from .atomic import write_text_cas

        # INV-2: a torn skeleton would pass the exists() guard above forever and block
        # every future floor append — the floor is corpus-class truth, write it whole.
        # RWY-3: expected=None — only while it is still absent, so a floor another session
        # created a moment ago is never replaced by an empty skeleton.
        write_text_cas(
            floor_path,
            f"# Agent Memory ({label} tier)\n\n"
            f"> {label.capitalize()}-tier user/feedback memories — recalled alongside the "
            "project corpus and delivered each session by the SessionStart portable-floor "
            "producer (TEA-1/TEA-3), NOT the native symlink.\n\n"
            "## User\n\n"
            "## Working Style & Process Feedback\n",
            None,
        )
    except Exception:
        pass


def _pointer_name(line: str) -> Optional[str]:
    """The memory name a floor-section ``line`` points at, or ``None`` if it isn't a pointer.

    TEA-4: reuses lint_floor's own link regex + restore-pointer allow-list so "what counts as
    a pointer to sort by" is the exact same notion the lint guard already parses — a hand-
    authored ``[MEMORY.full.md](MEMORY.full.md)`` restore link inside a floor section (rare,
    but the allow-list tolerates it) is never treated as a memory entry to sort against.
    """
    from .lint_floor import _ALLOWLIST, _MD_LINK_RE

    m = _MD_LINK_RE.search(line)
    if not m:
        return None
    base = m.group(1).rsplit("/", 1)[-1]
    if base in _ALLOWLIST:
        return None
    return base[:-3] if base.endswith(".md") else base


def _append_floor_pointer(
    memory_dir: str, section_header: str, name: str, title: str, hook: str
) -> dict:
    """Insert ``- [title](name.md) — hook`` at its SORTED position within ``section_header``.

    Returns the ``result["floor"]`` outcome dict — ``{"status", "reason"}`` (LIF-5). This used
    to return a bare bool that silently no-oped on a missing file OR a renamed header, so a
    user/feedback memory could lose its always-load pointer with no signal anywhere. Now every
    outcome is explicit; never raises; MEMORY.md stays the ONLY file this module edits:

    - ``appended`` (reason None) — the section exists; the pointer is inserted at its
      deterministic lexicographic position among the section's EXISTING pointer lines (TEA-4 —
      see the insertion-point comment below), never necessarily the block tail anymore.
    - ``created-section`` — MEMORY.md exists but ``section_header`` does not (renamed or
      deleted by hand — the floor drifted from ``assets/MEMORY.skeleton.md``). The canonical
      section is re-created at the END of MEMORY.md in the skeleton's own format (one blank
      separator line, ``## Header``, then the pointer as its first entry). Repairing beats
      skipping here: the pointer is the whole point of a user/feedback write, and the created
      section is exactly what lint_floor/floor_memory_names already parse as floor. Merging a
      RENAMED section's leftovers into the canonical one stays agent-gated (/hippo:new routes
      it) — this function never touches other sections.
    - ``skipped`` — nothing written; ``reason`` is machine-readable: ``MEMORY.md missing``
      (floor CREATION is /hippo:init's job — skeleton + starter packs; fabricating the whole
      file here would shadow that), ``pointer already present`` (idempotence — the same
      ``name.md`` is already floor-linked), or ``MEMORY.md unreadable/write failed: ...``.
    """
    from .atomic import update_text_cas

    path = os.path.join(memory_dir, "MEMORY.md")
    outcome: dict = {}

    def transform(text: str) -> Optional[str]:
        # RWY-3: re-run on fresh bytes after a CAS conflict, so it must be pure.
        new_text, out = _with_pointer(text, section_header, name, title, hook)
        outcome.clear()
        outcome.update(out)
        return new_text

    try:
        update_text_cas(path, transform)  # COR-18 + RWY-3: whole, and never a lost pointer
    except FileNotFoundError:
        return {
            "status": "skipped",
            "reason": "MEMORY.md missing — pointer NOT recorded; run /hippo:init to create "
            "the floor, then add the pointer line by hand",
        }
    except Exception as exc:
        return {"status": "skipped", "reason": f"MEMORY.md unreadable or write failed: {exc}"}
    return dict(outcome)


def _with_pointer(
    text: str, section_header: str, name: str, title: str, hook: str
) -> Tuple[Optional[str], dict]:
    """``_append_floor_pointer``'s edit as a pure function: ``(new_text or None, outcome)``."""
    lines = text.split("\n")
    link = f"]({name}.md)"
    if any(link in ln for ln in lines):
        return None, {"status": "skipped", "reason": "pointer already present"}

    pointer = f"- [{title}]({name}.md) — {hook}".rstrip()

    # Find the section header, then the end of its block (next "## " or EOF).
    start = next((i for i, ln in enumerate(lines) if ln.strip() == section_header), None)
    if start is None:
        # LIF-5: header renamed/deleted — re-create the canonical section at EOF, skeleton
        # format. (An empty-but-existing MEMORY.md gets the section with no leading blank.)
        body = "\n".join(lines).rstrip("\n")
        section = f"{section_header}\n{pointer}\n"
        return (f"{body}\n\n{section}" if body else section), {
            "status": "created-section",
            "reason": f"section not found: {section_header} — created it at the end of MEMORY.md",
        }

    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip().startswith("## "):
            end = j
            break

    # TEA-4: sorted insertion — the new pointer goes BEFORE the first existing pointer line in
    # the block whose memory name sorts lexicographically greater than this one. This is what
    # kills tail-collision merge conflicts: two clones adding DIFFERENT names to the same
    # section each touch a diff hunk at THEIR OWN name's position, not both appending to the
    # single highest-churn shared line (the section tail) — git merges the two non-overlapping
    # insertions cleanly. A fully-sorted section stays sorted (every insert lands at its exact
    # slot). An unsorted legacy section gets each new entry placed at its locally-correct spot
    # relative to whatever order already exists, WITHOUT touching or reordering any other
    # line — no bulk re-sort, per the no-bulk-autonomous-sweeps invariant. Non-pointer lines
    # (blank lines, hand-written prose) are skipped when searching but never moved.
    insert = None
    for j in range(start + 1, end):
        other = _pointer_name(lines[j])
        if other is not None and other > name:
            insert = j
            break
    if insert is None:
        # No existing pointer sorts greater than this name (a brand-new section, an
        # append-only section, or this name is the section's new last entry) — falls through
        # to the same "end of block" position the pre-TEA-4 append always used, so a freshly
        # created section's first pointer (LIF-5) and an alphabetically-last name both land
        # exactly where they always did.
        insert = start + 1
        for j in range(start + 1, end):
            if lines[j].strip():
                insert = j + 1

    lines.insert(insert, pointer)
    return "\n".join(lines), {"status": "appended", "reason": None}


def _remove_floor_pointer(memory_dir: str, name: str) -> dict:
    """Drop ``name``'s floor pointer line from MEMORY.md — ``_append_floor_pointer``'s inverse.

    RCH-1: when /hippo:promote lifts a user/feedback memory OUT of the project corpus, the
    project floor's pointer to it would dangle (the .md is gone); this removes exactly that
    line. What counts as "the pointer" is ``_pointer_name`` — the same notion the TEA-4
    sorted insert and lint_floor parse — so a prose line that merely mentions ``name.md``
    is never touched. Returns the same explicit outcome-dict contract as append, never
    raises, and MEMORY.md stays the only file this module edits:

    - ``removed`` (reason None) — the pointer line(s) were dropped.
    - ``skipped`` — nothing written; ``reason`` names why: ``MEMORY.md missing``,
      ``pointer not present`` (idempotence — safe to call for never-floor-linked types),
      or ``MEMORY.md unreadable/write failed: ...``.
    """
    from .atomic import update_text_cas

    path = os.path.join(memory_dir, "MEMORY.md")
    removed = {"any": False}

    def transform(text: str) -> Optional[str]:
        lines = text.split("\n")
        kept = [ln for ln in lines if _pointer_name(ln) != name]
        removed["any"] = len(kept) != len(lines)
        return "\n".join(kept) if removed["any"] else None

    try:
        update_text_cas(path, transform)  # COR-18 + RWY-3
    except FileNotFoundError:
        return {"status": "skipped", "reason": "MEMORY.md missing"}
    except Exception as exc:
        return {"status": "skipped", "reason": f"MEMORY.md unreadable or write failed: {exc}"}
    if not removed["any"]:
        return {"status": "skipped", "reason": "pointer not present"}
    return {"status": "removed", "reason": None}
