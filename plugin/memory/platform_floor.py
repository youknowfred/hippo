"""PLT-2: the oldest Claude Code hippo supports, and how a hook knows which one it runs on.

hippo now leans on version-gated harness features — plugin ``userConfig`` options set from
``/config`` (CLM-2's attention switch; Claude Code 2.1.269, per the plugins reference), with
``mcp_tool`` hooks next (v1.42). An older harness would load the plugin and quietly do less,
so the floor is declared here, in the README support matrix, and checked: doctor prints the
comparison and the SessionStart integrity lane names a too-old harness.

Claude Code documents no version variable, but every hook process inherits ``AI_AGENT``
(``claude-code_2-1-289_harness`` in a terminal hook, ``…_agent`` in a Desktop Bash tool —
PLATFORM.md §6). The parse is strict; anything else reads as unknown and checks nothing.
Pure, no package imports, never raises.
"""

from __future__ import annotations

import os
import re
from typing import Optional, Tuple

MIN_CLAUDE_CODE = "2.1.269"

_AI_AGENT_RE = re.compile(r"^claude-code_(\d+)-(\d+)-(\d+)(?:_|$)")


def _parse(v: str) -> Optional[Tuple[int, int, int]]:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", (v or "").strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def claude_code_version(env: Optional[dict] = None) -> Optional[str]:
    """The running Claude Code version from ``AI_AGENT``, or ``None`` when unknown."""
    m = _AI_AGENT_RE.match(((env if env is not None else os.environ).get("AI_AGENT") or "").strip())
    return f"{m.group(1)}.{m.group(2)}.{m.group(3)}" if m else None


def harness_too_old(env: Optional[dict] = None) -> Optional[str]:
    """The running version when it is older than ``MIN_CLAUDE_CODE``, else ``None``."""
    v = claude_code_version(env)
    have, need = _parse(v or ""), _parse(MIN_CLAUDE_CODE)
    return v if have is not None and need is not None and have < need else None


def harness_floor_line(env: Optional[dict] = None) -> Optional[str]:
    """The integrity-lane line for a too-old harness, or ``None``."""
    old = harness_too_old(env)
    if not old:
        return None
    return (
        f"⚠ Claude Code {old} is older than hippo's supported floor {MIN_CLAUDE_CODE} — "
        "plugin options (the attention setting) and newer hook types may not load. Update "
        "Claude Code."
    )
