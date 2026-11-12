"""OWN-1's doctor line: the user tier's git posture, from local state only.

Not a repository / inside another repository / N uncommitted changes / clean / behind its
upstream as of the owner's last fetch (no fetch here: hippo makes no network call). No
upstream is a normal state. ``warn`` only for what needs the owner: new upstream commits, a
machine-local memory git already tracks, or one the ignore list does not name yet. A machine
with no user tier gets no line at all, so its doctor output is unchanged (``run_checks``
skips a check that returns None). Off every hook path.
"""

from __future__ import annotations

from typing import Dict, Optional

from .doctor_checks_env import DoctorContext


def check_user_tier(ctx: DoctorContext) -> Optional[Dict[str, str]]:
    """OWN-1: the user tier's git home, in one line; None when this machine has no user tier."""
    try:
        from .provenance_env import user_memory_dir
        from .user_tier_git import posture, posture_line

        p = posture(user_memory_dir())
        if p is None:
            return None
        status, message = posture_line(p)
        return {"status": status, "message": message}
    except Exception as exc:
        return {"status": "warn", "message": f"user-tier check failed: {exc}."}
