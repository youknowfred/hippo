"""OUT-5's doctor line: how many memories recall withholds, and by which route they came.

Read-only and cheap (the drift delta the trust-drift line already computes, plus one
frontmatter parse per withheld file). Three classes: ``native path`` (Claude Code's own
memory writer: ``metadata`` carries ``node_type`` and ``originSessionId`` and none of
hippo's provenance), ``written by hippo elsewhere`` (hippo's provenance is present: another
machine, or a pull), and ``other``. When the corpus has not opted in to consenting a
session's own writes on this machine and the native-path class is not empty, the line
names the opt-in. It also counts the memories consented that way (``hippo trust status``
names them). No line when there is neither.
``DoctorContext`` lives in ``doctor_checks_env``.
"""

from __future__ import annotations

from typing import Dict, Optional

from .doctor_checks_env import DoctorContext


def check_withheld_origin(ctx: DoctorContext) -> Optional[Dict[str, str]]:
    try:
        from . import session_consent as SC

        from . import trust

        counts = SC.withheld_by_class(ctx.memory_dir, ctx.repo_root)
        gate_root = trust.gate_repo_root(ctx.memory_dir, ctx.repo_root)
        granted = len(trust.session_granted_stems(trust._trusted_entry(gate_root)))
        tail = (
            f"; {granted} consented without a review at bytes this machine's own session wrote "
            "(`hippo trust status` names them)" if granted else ""
        )
        if not counts or not sum(counts.values()):
            return {"status": "ok", "message": "withheld by origin: none withheld" + tail + "."} if granted else None
        total = sum(counts.values())
        parts = ", ".join(f"{counts[k]} {k}" for k in (SC.NATIVE, SC.HIPPO_ELSEWHERE, SC.OTHER))
        msg = f"withheld by origin: {total} memor{'y' if total == 1 else 'ies'} withheld ({parts}){tail}"
        if counts[SC.NATIVE] and not SC.enabled(ctx.memory_dir, ctx.repo_root):
            msg += (
                ". The native-path ones are what this machine's own Claude Code sessions wrote; "
                'to consent to those as they are written, set "consent": {"session_writes": true} '
                "in hippo.json and run `hippo trust policy --accept` (pulled or merged bytes stay "
                "withheld either way)"
            )
        return {"status": "ok", "message": msg + "."}
    except Exception as exc:
        return {"status": "warn", "message": f"withheld-origin check failed: {exc}."}
