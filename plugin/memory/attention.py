"""CLM-2: the attention setting — how much SessionStart says, and which signals it mutes.

Two modes. ``full`` (the v1.41 default) runs every producer, as before. ``calm`` renders
CLM-1's budgeted digest (``session_start_calm``). On top of either, a mute list hides named
signals. Integrity signals can never be muted: a quarantined corpus, a corrupt index, a
format this plugin cannot read, a harness or venv that cannot run hippo, and native
settings that keep the floor out of context always show.

Where the setting comes from, first answer wins:
  1. ``HIPPO_ATTENTION`` = ``calm`` | ``full``;
  2. the plugin's ``calm_session_start`` boolean option (``userConfig``), which Claude Code
     exports to hooks as ``CLAUDE_PLUGIN_OPTION_CALM_SESSION_START`` once it has a saved
     value (PLATFORM.md §4: an unsaved default is not exported);
  3. ``full``.
The mute list is ``HIPPO_MUTE``, comma-separated SessionStart signal names. It moves into
``hippo.json`` with SRF-4. Never raises.
"""

from __future__ import annotations

import os
from typing import Optional, Set, Tuple

CALM = "calm"
FULL = "full"

# SessionStart producer labels that are always shown and never budgeted or muted.
INTEGRITY_SIGNALS = frozenset({
    "stale_venv",
    "harness_floor",
    "corpus_format",
    "integrity",
    "index_integrity",
    "trust_drift",
    "native_interference",
})

_TRUE = ("1", "true", "yes", "on")


def attention_mode(env: Optional[dict] = None) -> str:
    e = env if env is not None else os.environ
    raw = (e.get("HIPPO_ATTENTION") or "").strip().lower()
    if raw in (CALM, FULL):
        return raw
    if (e.get("CLAUDE_PLUGIN_OPTION_CALM_SESSION_START") or "").strip().lower() in _TRUE:
        return CALM
    return FULL


def muted_signals(env: Optional[dict] = None) -> Tuple[Set[str], Set[str]]:
    """``(muted, refused)``: the names ``HIPPO_MUTE`` mutes, and the integrity names it asked
    to mute and was refused."""
    e = env if env is not None else os.environ
    asked = {s.strip() for s in (e.get("HIPPO_MUTE") or "").split(",") if s.strip()}
    return asked - INTEGRITY_SIGNALS, asked & INTEGRITY_SIGNALS
