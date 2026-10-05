"""CLM-1: the calm SessionStart digest — orientation, not a maintenance briefing.

On em-growth-labs 99.1% of SessionStarts reached the 9,000-char cap, so what the agent read
was decided by truncation order. Calm mode (CLM-2's attention setting) assembles four parts
from the producers' output instead:

  1. the integrity lane — every ``attention.INTEGRITY_SIGNALS`` block that fired, always
     shown and exempt from the budget;
  2. orientation — the fleet line, the resume card, relevant-to-work, then the portable
     floor (the user and private tiers' only always-load channel), cut at whole lines;
  3. one next-best action — the highest-priority maintenance signal that fired, as a plain
     sentence;
  4. one counted line — every other maintenance signal, by name, pointing at ``tend``
     (TND-2), the one queue that works them all.
The whole digest aims at ``CALM_BUDGET`` chars: the integrity lane is never cut, so parts 2–4
shrink to make room for it (down to ``_MIN_ROOM``). The maintenance producers still run, so their
numbers stay data for doctor; calm mode only stops reciting them. Never raises.
"""

from __future__ import annotations

from typing import List, Tuple

from .attention import INTEGRITY_SIGNALS

CALM_BUDGET = 2000
_MIN_ROOM = 400  # parts 2–4 keep at least this much however long the integrity lane runs

_ORIENTATION = ("presence", "resume_card", "relevant_to_work", "portable_floor")

# Highest priority first: (producer label, the next-best action in plain words).
_ACTIONS = (
    ("floor", "trim the MEMORY.md floor (move detail into the linked memory files)"),
    ("contradiction_inbox", "settle the conflicting memories (say \"tend memory\")"),
    ("reconsolidation", "re-check the stale memories this repo recently relied on (say \"tend memory\")"),
    ("pending_capture", "review what earlier sessions captured (say \"tend memory\")"),
    ("cite_derivation", "re-derive the corpus's citations (say \"tend memory\")"),
    ("squash_merge_heal", "re-baseline the memories a squash merge orphaned (say \"tend memory\")"),
    ("unresolvable_baseline", "re-baseline memories whose source commit is gone"),
    ("citation_rot", "fix memories that cite files that no longer exist"),
    ("rules_conflict", "check governance rules that cite disputed memories"),
    ("rules_rot", "fix stale references in CLAUDE.md or .claude/rules"),
    ("staleness", "re-verify memories whose cited code moved (say \"tend memory\")"),
    ("merge_digest", "review the near-duplicates a merge brought in"),
    ("dream_applied", "look over the links the last dream pass added"),
    ("blind_spot", "capture an answer to a question the corpus keeps missing"),
    ("link_health", "fix dangling memory links"),
)
_ACTION_BY_LABEL = dict(_ACTIONS)
_PRIORITY = {label: i for i, (label, _a) in enumerate(_ACTIONS)}

# Plain names for the counted line.
_NAMES = {
    "floor": "floor size",
    "contradiction_inbox": "conflicting memories",
    "reconsolidation": "stale recalled memories",
    "pending_capture": "pending captures",
    "cite_derivation": "citation re-derivation",
    "squash_merge_heal": "squash-merge baselines",
    "unresolvable_baseline": "lost baselines",
    "citation_rot": "missing cited files",
    "rules_conflict": "rule conflicts",
    "rules_rot": "rule rot",
    "staleness": "stale memories",
    "merge_digest": "merge duplicates",
    "dream_applied": "new dream links",
    "blind_spot": "blind spots",
    "link_health": "link health",
    "floor_change": "floor changes",
    "git_recent": "recent memories",
}


def _cut_lines(text: str, budget: int) -> str:
    """``text`` cut at a whole line so it fits ``budget`` chars ('' when nothing fits)."""
    if len(text) <= budget:
        return text
    out: List[str] = []
    used = 0
    for line in text.split("\n"):
        if used + len(line) + 1 > budget - 2:
            break
        out.append(line)
        used += len(line) + 1
    return ("\n".join(out) + "\n  …") if out else ""


def calm_digest(blocks: List[Tuple[str, str]], budget: int = CALM_BUDGET) -> str:
    """Assemble the calm digest from ``[(label, block)]`` in producer order."""
    try:
        integrity = [b for label, b in blocks if label in INTEGRITY_SIGNALS]
        orient = {label: b for label, b in blocks if label in _ORIENTATION}
        rest = [label for label, _b in blocks
                if label not in INTEGRITY_SIGNALS and label not in _ORIENTATION]
        tail: List[str] = []
        ranked = sorted(rest, key=lambda label: _PRIORITY.get(label, len(_PRIORITY)))
        if ranked:
            first = ranked[0]
            if first in _ACTION_BY_LABEL:
                tail.append(f"➡ Next: {_ACTION_BY_LABEL[first]}.")
            others = ranked[1:] if first in _ACTION_BY_LABEL else ranked
            if others:
                names = ", ".join(_NAMES.get(label, label.replace("_", " ")) for label in others)
                tail.append(
                    f"{len(others)} more item(s) queued ({names}) — say \"tend memory\" "
                    "to work through them."
                )
        # The whole digest aims at ``budget``: integrity lines are never cut, so orientation
        # gives way to them, down to a floor that keeps the action and queue lines.
        integrity_len = sum(len(b) + 2 for b in integrity)
        room = max(budget - integrity_len, _MIN_ROOM) - sum(len(t) + 2 for t in tail)
        oriented: List[str] = []
        for label in _ORIENTATION:
            block = orient.get(label)
            if not block or room <= 0:
                continue
            cut = _cut_lines(block, room)
            if cut:
                oriented.append(cut)
                room -= len(cut) + 2
        parts = integrity + oriented + tail
        return "\n\n".join(p.rstrip() for p in parts if p)
    except Exception:
        return "\n\n".join(b for _label, b in blocks if b)
