"""CLM-2: the attention setting — how much SessionStart says, and which signals it mutes.

Two modes. ``full`` (the v1.41 default) runs every producer, as before. ``calm`` renders
CLM-1's budgeted digest (``session_start_calm``). On top of either, a mute list hides named
signals. Integrity signals can never be muted: a quarantined corpus, a corrupt index, a
format this plugin cannot read, a harness or venv that cannot run hippo, and native
settings that keep the floor out of context always show.

Where the setting comes from, first answer wins (SRF-4's precedence, ``settings.py``):
  1. ``HIPPO_ATTENTION`` = ``calm`` | ``full``;
  2. the plugin's ``calm_session_start`` boolean option (``userConfig``), which Claude Code
     exports to hooks as ``CLAUDE_PLUGIN_OPTION_CALM_SESSION_START`` once it has a saved
     value (PLATFORM.md §4: an unsaved default is not exported) — a saved ``true`` is
     calm, a saved ``false`` is full;
  3. the corpus policy file's ``attention`` key (``.claude/memory/hippo.json``);
  4. ``full``.
The mute list is ``HIPPO_MUTE`` (comma-separated SessionStart signal names) when set,
else ``hippo.json``'s ``mute`` (a list, or the same comma string). Never raises.
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


def _policy(memory_dir: Optional[str], key: str):
    if not memory_dir:
        return None
    try:
        from .provenance_format import read_policy_key

        return read_policy_key(memory_dir, key)
    except Exception:
        return None


def attention_source(env: Optional[dict] = None, memory_dir: Optional[str] = None) -> Tuple[str, str]:
    """``(mode, source)`` — the mode and which home decided it (``HIPPO_ATTENTION``, the
    ``calm_session_start`` option, ``hippo.json``, or ``default``)."""
    e = env if env is not None else os.environ
    raw = (e.get("HIPPO_ATTENTION") or "").strip().lower()
    if raw in (CALM, FULL):
        return raw, "HIPPO_ATTENTION"
    try:
        from .settings import option_bool

        opt = option_bool("calm_session_start", e)
    except Exception:
        opt = None
    if opt is not None:
        return (CALM if opt else FULL), "the calm_session_start option"
    pol = _policy(memory_dir, "attention")
    if isinstance(pol, str) and pol.strip().lower() in (CALM, FULL):
        return pol.strip().lower(), "hippo.json"
    return FULL, "default"


def attention_mode(env: Optional[dict] = None, memory_dir: Optional[str] = None) -> str:
    return attention_source(env, memory_dir)[0]


def muted_signals(env: Optional[dict] = None, memory_dir: Optional[str] = None) -> Tuple[Set[str], Set[str]]:
    """``(muted, refused)``: the names ``HIPPO_MUTE`` (else ``hippo.json`` ``mute``) mutes,
    and the integrity names it asked to mute and was refused."""
    e = env if env is not None else os.environ
    raw = e.get("HIPPO_MUTE")
    if raw is not None and raw.strip():
        asked = {s.strip() for s in raw.split(",") if s.strip()}
    else:
        pol = _policy(memory_dir, "mute")
        if isinstance(pol, str):
            pol = pol.split(",")
        asked = {str(s).strip() for s in pol if str(s).strip()} if isinstance(pol, list) else set()
    return asked - INTEGRITY_SIGNALS, asked & INTEGRITY_SIGNALS
