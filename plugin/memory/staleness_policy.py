"""VOL-1: the volatile-paths ARMING policy — derivation keeps the citation, arming asks first.

The field defect (em-growth-labs, one working day): a fully-worked 56-item reconsolidation
worklist re-flagged 23 memories within the hour, every one triggered by ONE file — the
repo's living roadmap, edited by nearly every session BY DESIGN. Those memories cite it
because their bodies delegate to it ("live status lives there, not here"), so the citation
is RIGHT for recall — JIT point-of-action, ``recall --for-diff``, the RET-6 verify-at-use
banner all should fire — and wrong only as a staleness-ARMING trigger: whole-file drift in
a churn-by-design file carries ~zero bits about memory validity. De-citing amputates both
(and doesn't stick: the next rederive faithfully re-adds what the body legitimately
mentions). The defect is architectural — whole-file drift as the arming trigger conflates
"mentions" with "depends on" — so the fix is a policy SPLIT, not an exception list.

This module is the split's one policy point. The corpus declares its churn files once, in
the committed ``.claude/memory/.format`` marker (``volatile_paths`` — read by
``provenance_format.read_volatile_paths``, next to the format/derivation axes it travels
with), and the three ARMING surfaces partition through ``split_volatile_only``:

  - ``reconsolidate.recalled_stale_worklist``'s stale lane (the worklist itself),
  - ``reconsolidate_watermark.watermark_stale_candidates`` (the [since-watermark] flag),
  - ``session_start.staleness_producer`` (the ⚠ Memory staleness note).

An item arms iff at least ONE drifted cited path is non-volatile; an armed item keeps its
FULL changed-path listing (the render stays honest — the volatile path did drift, it just
didn't arm alone). Everything else is deliberately registry-blind: ``find_stale`` detection
and ``stale.json`` (RET-5's penalty, RET-6's banner), the JIT touch map, ``--for-diff``,
derivation/rederive (AC6: byte-identical output with or without the registry), and the
deep-judgment surfaces (audit's raw stale section, archive's admission leg, publish's
preflight) — those exist to see everything. CLB-3 quoted-evidence drift also arms through
unfiltered (``fold_drift_candidates`` runs AFTER the watermark producer's filter): a
memory's own quoted span changing is span-level truth, not whole-file churn, even inside a
volatile file.

Honesty over silence (the no-silent-caps rule): every suppression is counted somewhere
visible — the producers record suppressed names into a caller-owned ``diagnostics`` dict
(``DIAG_KEY``, the ``find_stale``/``timed_out`` pattern), the SessionStart note and the
CLI/MCP worklist listings render the count, and doctor carries one ok-glyph display line
(never a warn — policy state is information, not a defect). ED-1 holds: this routes
DETECTION only; every verdict stays human, per-item. Tier-2 co-drift arming (a volatile
path counting when a non-volatile sibling also drifted) is a possible follow-up; tier-1
ships never-arm-alone, which the armed-iff-any-non-volatile rule already approximates
from the suppression side.

TYPE-1 (2026-08-17, the em-growth-labs memory×Linear audit's option A, owner-picked):
``type: project`` memories are additionally exempt from the SAME three arming surfaces.
The measurement that earned it: 70/79 worklist items were ``type: project``; 83% of all
past reverify verdicts were "graduate"; 60/66 graduated memories re-armed — on a living
repo the project-memory staleness queue meters REPO VELOCITY, not truth-risk, and the
per-item human verdict it demands is almost always "still fine". The split is the same
shape as VOL-1: ARMING only — detection, ``stale.json``, RET-5/RET-6, JIT, ``--for-diff``,
derivation, and the deep-judgment surfaces stay type-blind; suppression is counted on
every surface it happens on (``DIAG_TYPE_KEY``, the note/worklist tails). Partition order
is VOL then TYPE, so each suppressed name lands in exactly one bucket and VOL-1's counts
keep their historical meaning. An item with NO ``type`` field ARMS (fail-open: only an
explicit type ever exempts — which also keeps typeless fixtures and older corpora
byte-identical). The exempt set ships as ``{"project"}``; ``HIPPO_ARMING_EXEMPT_TYPES``
(comma-list; set EMPTY to arm everything) is the reversible override — ED-1 holds, every
verdict stays human, per-item.

Pure functions over caller-supplied data + one tiny marker read; no writes anywhere.
Sibling of ``staleness.py`` (never imports it); reads the registry via
``provenance_format`` directly (the ``staleness_evidence`` precedent). Never raises.
"""

from __future__ import annotations

import os
from typing import Iterable, List, Optional, Set, Tuple

from .provenance_format import read_volatile_paths

# The caller-owned diagnostics key suppressed names are unioned under — the same
# out-parameter pattern as find_stale's "timed_out" (a producer must never lose its
# primary return shape to carry a side-channel count).
DIAG_KEY = "volatile_suppressed"

# TYPE-1's own bucket — kept separate from DIAG_KEY so each surface can report the two
# policies' counts distinctly (and the VOL-1 numbers keep their historical meaning).
DIAG_TYPE_KEY = "type_exempt_suppressed"

# TYPE-1: the shipped arming-exempt type set and its reversible env override.
_DEFAULT_ARMING_EXEMPT_TYPES = frozenset({"project"})
_EXEMPT_TYPES_ENV = "HIPPO_ARMING_EXEMPT_TYPES"


def volatile_set(memory_dir: str) -> Set[str]:
    """The registry as a membership set — ``set()`` when undeclared/unreadable (ED-4)."""
    try:
        return set(read_volatile_paths(memory_dir))
    except Exception:
        return set()


def split_volatile_only(
    items: Iterable[dict], volatile: Set[str]
) -> Tuple[List[dict], List[dict]]:
    """Partition stale/watermark-shaped items into ``(armed, suppressed)``.

    ``suppressed`` = items whose EVERY drifted cited path is registry-listed (and that
    have at least one — an empty ``changed_paths`` is not "volatile-only", it's a
    different degradation and stays armed for its own surface to explain). Items pass
    through untouched either way — armed entries keep their full ``changed_paths`` so
    the listing stays honest (AC5), and order is preserved on both sides. With an empty
    registry this is the identity split, so every caller's no-registry path stays
    byte-identical by construction. Never raises.
    """
    armed: List[dict] = []
    suppressed: List[dict] = []
    try:
        for item in items:
            changed = item.get("changed_paths") or []
            if volatile and changed and all(p in volatile for p in changed):
                suppressed.append(item)
            else:
                armed.append(item)
    except Exception:
        return list(items), []
    return armed, suppressed


def arming_exempt_types() -> frozenset:
    """The TYPE-1 exempt set — ``{"project"}`` shipped, ``HIPPO_ARMING_EXEMPT_TYPES``
    overriding (comma-list, case-insensitive; set EMPTY to arm every type). Never raises.
    """
    try:
        raw = os.environ.get(_EXEMPT_TYPES_ENV)
        if raw is None:
            return _DEFAULT_ARMING_EXEMPT_TYPES
        return frozenset(t.strip().lower() for t in raw.split(",") if t.strip())
    except Exception:
        return _DEFAULT_ARMING_EXEMPT_TYPES


def split_type_exempt(
    items: Iterable[dict], exempt: frozenset
) -> Tuple[List[dict], List[dict]]:
    """Partition stale/watermark-shaped items into ``(armed, suppressed)`` by ``type``.

    ``suppressed`` = items whose carried ``type`` field (read at detection time,
    ``staleness.read_memory_type``) is in ``exempt``. An item with NO usable type ARMS —
    fail-open, so only an explicit type ever exempts. Items pass through untouched, order
    preserved on both sides; an empty ``exempt`` is the identity split, so the override's
    arm-everything setting is byte-identical to pre-TYPE-1 behavior. Never raises.
    """
    armed: List[dict] = []
    suppressed: List[dict] = []
    try:
        for item in items:
            t = item.get("type")
            if exempt and isinstance(t, str) and t in exempt:
                suppressed.append(item)
            else:
                armed.append(item)
    except Exception:
        return list(items), []
    return armed, suppressed


def note_suppressed(diagnostics: Optional[dict], names: Iterable[str], key: str = DIAG_KEY) -> None:
    """Union ``names`` into ``diagnostics[key]`` (sorted, deduped). No-op on ``None``.

    One merge implementation so the stale lane and the watermark lane can both report
    into the SAME caller-owned dict without either clobbering the other — and (TYPE-1)
    so both policies share it, each under its own key. Never raises.
    """
    if diagnostics is None:
        return
    try:
        have = diagnostics.get(key) or []
        diagnostics[key] = sorted(set(have) | set(names))
    except Exception:
        return


def suppressed_count_note(count: int) -> str:
    """The worklist listings' one-line honesty tail (CLI + MCP render it verbatim)."""
    return (
        f"({count} memor{'y' if count == 1 else 'ies'} policy-suppressed: only "
        "volatile-path drift — churn-by-design files declared in .format volatile_paths)"
    )


def stale_note_tail(count: int) -> str:
    """The SessionStart staleness note's suppressed-count tail line (armed items exist)."""
    return (
        f"  (+{count} whose only drift is volatile-path — policy-suppressed from arming; "
        "see .format volatile_paths)"
    )


def stale_note_all_suppressed(count: int) -> str:
    """The whole-note replacement when EVERY stale memory is policy-suppressed.

    Calm on purpose (ℹ, not ⚠): the treadmill this policy retires was 23 red per-item
    lines; the honest residue is one auditable sentence, not a standing alarm.
    """
    return (
        f"ℹ Memory staleness — {count} stale memor{'y' if count == 1 else 'ies'} whose "
        "only drift is volatile-path (policy-suppressed; see .format volatile_paths); "
        "nothing to verify."
    )


def type_exempt_count_note(count: int) -> str:
    """The worklist listings' TYPE-1 honesty tail (CLI + MCP render it verbatim)."""
    return (
        f"({count} memor{'y' if count == 1 else 'ies'} arming-exempt by type — project-"
        "memory staleness meters repo velocity, not truth-risk (TYPE-1); detection/recall "
        f"unaffected; override via {_EXEMPT_TYPES_ENV})"
    )


def type_note_tail(count: int) -> str:
    """The SessionStart staleness note's TYPE-1 suppressed-count tail (armed items exist)."""
    return (
        f"  (+{count} type-exempt from arming — TYPE-1 policy on project memories; "
        f"detection unaffected; override via {_EXEMPT_TYPES_ENV})"
    )


def type_note_all_suppressed(count: int) -> str:
    """The whole-note replacement when EVERY stale memory is TYPE-1-exempt.

    Same calm register as ``stale_note_all_suppressed`` — policy state, not an alarm.
    """
    return (
        f"ℹ Memory staleness — {count} stale memor{'y' if count == 1 else 'ies'}, all "
        "arming-exempt by type (TYPE-1: project-memory staleness meters repo velocity, "
        f"not truth-risk); detection/recall unaffected; override via {_EXEMPT_TYPES_ENV}."
    )
