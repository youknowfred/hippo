"""SRF-4: where hippo's settings come from — one resolver, one precedence, every old spelling.

hippo's switches grew in three places: two dozen ``HIPPO_*`` environment variables, policy
keys inside the corpus's ``.format`` version marker, and a plaintext machine file
(``~/.claude/hippo-llm.json``) holding the LLM opt-ins and an API key. v1.42 consolidates
them into three homes:

  - **corpus policy** — the committed ``.claude/memory/hippo.json`` (JSON, so a bare
    pre-bootstrap ``python3`` reads it): ``volatile_paths``, ``floor_lint``,
    ``fold_digests``, ``attention``, ``mute``. ``provenance_format.read_policy_key``
    reads it first and falls back to ``.format`` (both read through v1.43; the format
    axes ``corpus_format``/``cite_derivation`` stay in ``.format``).
  - **machine settings** — the plugin's ``userConfig`` options (``plugin.json``):
    ``calm_session_start``, ``capture_llm``, ``dream_contradictions``,
    ``dream_generative``, ``llm_model`` and the ``sensitive`` ``llm_api_key``, which
    Claude Code keeps in the OS keychain. Claude Code exports an option to hooks as
    ``CLAUDE_PLUGIN_OPTION_<KEY>``; ``plugin.json`` wires the same names into the MCP
    server's env through ``${user_config.<key>}``. Neither reaches a process the Bash
    tool runs (PLATFORM.md §4) — so an LLM path must run in a hook or the MCP server.
  - **the frozen environment set** (≤12 names): the directory overrides,
    ``HIPPO_DISABLE=<comma list>``, ``HIPPO_TRUST_ALL`` and ``HIPPO_TRUST_NONGIT``.

PRECEDENCE, first answer wins, for every setting that has more than one home:

  1. an environment variable (the per-shell / CI override);
  2. the plugin option (``userConfig``);
  3. the corpus policy file ``hippo.json``;
  4. the legacy home (``.format`` policy keys, ``~/.claude/hippo-llm.json``, an old env
     name) — read through v1.43, removed at v2.0.

An option value that is empty, or still the literal ``${user_config.<key>}`` (a Claude Code
too old to substitute it), reads as UNSET and falls through. Every old name keeps working
unchanged; ``legacy_names_in_use`` lists the ones in use with their new spelling (doctor
prints them, and SessionStart counts each once per session through the usage rollup).

Stdlib only and 3.9-syntax-clean: the hook fast paths and the pre-bootstrap interpreter
import it. Never raises.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Mapping, Optional, Set, Tuple

# --------------------------------------------------------------------------- #
# HIPPO_DISABLE — one comma list for every kill switch
# --------------------------------------------------------------------------- #
# feature -> (legacy env name, how that name was parsed). The legacy parse is kept EXACTLY,
# so an old spelling behaves byte-for-byte as before:
#   "notfalsy" — on unless "", "0", "false" or "False" (dense, jit, presence, touch-fastpath)
#   "nonempty" — on for any non-blank value, "0" included (floor-nag)
#   "truthy"   — on only for 1/true/yes/on, case-insensitive (abstain-gate)
DISABLE_FEATURES: Dict[str, Tuple[str, str]] = {
    "dense": ("HIPPO_DISABLE_DENSE", "notfalsy"),
    "jit": ("HIPPO_DISABLE_JIT", "notfalsy"),
    "presence": ("HIPPO_DISABLE_PRESENCE", "notfalsy"),
    "floor-nag": ("HIPPO_DISABLE_FLOOR_NAG", "nonempty"),
    "abstain-gate": ("HIPPO_DISABLE_ABSTAIN_GATE", "truthy"),
    "touch-fastpath": ("HIPPO_DISABLE_TOUCH_FASTPATH", "notfalsy"),
}


def _legacy_on(raw: Optional[str], style: str) -> bool:
    v = (raw or "").strip()
    if style == "nonempty":
        return bool(v)
    if style == "truthy":
        return v.lower() in ("1", "true", "yes", "on")
    return v not in ("", "0", "false", "False")


def _norm_feature(token: str) -> str:
    return re.sub(r"[\s_]+", "-", token.strip().lower())


def disable_list(env: Optional[Mapping[str, str]] = None) -> Tuple[Set[str], Set[str]]:
    """``(known, unknown)`` feature names ``HIPPO_DISABLE`` lists. Comma- or space-separated,
    case-insensitive, ``_`` and ``-`` alike (``floor_nag`` = ``floor-nag``)."""
    e = os.environ if env is None else env
    raw = e.get("HIPPO_DISABLE") or ""
    names = {_norm_feature(t) for t in re.split(r"[,\s]+", raw) if t.strip()}
    return names & set(DISABLE_FEATURES), names - set(DISABLE_FEATURES)


def disabled(feature: str, env: Optional[Mapping[str, str]] = None) -> bool:
    """True when ``feature`` is switched off: ``HIPPO_DISABLE`` lists it, OR its old
    variable says so under that variable's own historical parse. The bash twin is
    ``hippo_disabled`` in ``hooks/_resolve_py.sh``."""
    try:
        e = os.environ if env is None else env
        legacy, style = DISABLE_FEATURES[feature]
        if _legacy_on(e.get(legacy), style):
            return True
        return feature in disable_list(e)[0]
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Plugin options (userConfig)
# --------------------------------------------------------------------------- #
OPTION_PREFIX = "CLAUDE_PLUGIN_OPTION_"
_UNSUBSTITUTED = re.compile(r"\$\{\s*user_config\.[^}]*\}")
_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


def plugin_option(key: str, env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The saved value of plugin option ``key``, or ``None`` when unset.

    Hooks receive ``CLAUDE_PLUGIN_OPTION_<KEY>`` from Claude Code; the MCP server receives
    the same name through ``plugin.json``'s ``${user_config.<key>}`` wiring. Empty, or the
    literal placeholder left by a Claude Code that does not substitute it, is unset."""
    e = os.environ if env is None else env
    raw = e.get(OPTION_PREFIX + key.upper())
    if raw is None:
        return None
    val = str(raw).strip()
    if not val or _UNSUBSTITUTED.search(val):
        return None
    return val


def option_bool(key: str, env: Optional[Mapping[str, str]] = None) -> Optional[bool]:
    """A boolean plugin option: True / False when saved, ``None`` when unset or junk."""
    v = plugin_option(key, env)
    if v is None:
        return None
    low = v.lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    return None


def env_bool(name: str, env: Optional[Mapping[str, str]] = None) -> Optional[bool]:
    """A SET env flag decides entirely (closed truthy set on, anything else off); an unset
    or blank one is ``None`` — the "a set env var decides" convention of the opt-in flags."""
    e = os.environ if env is None else env
    raw = e.get(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip() in ("1", "true", "True")


def opt_in(env_name: str, option: str, legacy_value=None, env: Optional[Mapping[str, str]] = None) -> bool:
    """An opt-in flag through the precedence: env > plugin option > legacy value > off."""
    v = env_bool(env_name, env)
    if v is not None:
        return v
    v = option_bool(option, env)
    if v is not None:
        return v
    if legacy_value is True:
        return True
    return isinstance(legacy_value, str) and legacy_value.strip() in ("1", "true", "True")


def env_first(names: Tuple[str, ...], env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The first of ``names`` set to a non-blank value (new spelling first, then old)."""
    e = os.environ if env is None else env
    for n in names:
        raw = e.get(n)
        if raw is not None and raw.strip():
            return raw.strip()
    return None


# --------------------------------------------------------------------------- #
# Legacy names — old spelling -> where it lives now
# --------------------------------------------------------------------------- #
LEGACY_ENV: Dict[str, str] = {
    "HIPPO_DISABLE_DENSE": "HIPPO_DISABLE=dense",
    "HIPPO_DISABLE_JIT": "HIPPO_DISABLE=jit",
    "HIPPO_DISABLE_PRESENCE": "HIPPO_DISABLE=presence",
    "HIPPO_DISABLE_FLOOR_NAG": "HIPPO_DISABLE=floor-nag",
    "HIPPO_DISABLE_ABSTAIN_GATE": "HIPPO_DISABLE=abstain-gate",
    "HIPPO_DISABLE_TOUCH_FASTPATH": "HIPPO_DISABLE=touch-fastpath",
    "DREAM_CONTRA_MAX_PAIRS": "HIPPO_DREAM_CONTRA_MAX_PAIRS",
    "DREAM_CONTRA_MIN_COFIRE": "HIPPO_DREAM_CONTRA_MIN_COFIRE",
}

# ``.format`` policy keys -> hippo.json (same key name).
POLICY_KEYS = ("volatile_paths", "floor_lint", "fold_digests", "attention", "mute")
FORMAT_AXES = ("corpus_format", "cite_derivation")

# ``~/.claude/hippo-llm.json`` key -> its new home.
LEGACY_LLM_FILE_KEYS: Dict[str, str] = {
    "api_key": "the plugin's llm_api_key option (kept in the OS keychain)",
    "model": "the plugin's llm_model option",
    "capture_triage": "the plugin's capture_llm option",
    "dream_contradictions": "the plugin's dream_contradictions option",
    "provider": "HIPPO_LLM_PROVIDER",
    "base_url": "HIPPO_LLM_BASE_URL",
    "capture_timeout_s": "HIPPO_CAPTURE_LLM_TIMEOUT",
    "dream_timeout_s": "HIPPO_DREAM_LLM_TIMEOUT",
    "contra_max_pairs": "HIPPO_DREAM_CONTRA_MAX_PAIRS",
    "contra_min_cofire": "HIPPO_DREAM_CONTRA_MIN_COFIRE",
}


def llm_config_path(env: Optional[Mapping[str, str]] = None) -> str:
    """The legacy plaintext LLM file: ``HIPPO_LLM_CONFIG`` or ``~/.claude/hippo-llm.json``."""
    e = os.environ if env is None else env
    override = (e.get("HIPPO_LLM_CONFIG") or "").strip()
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".claude", "hippo-llm.json")


def legacy_llm_file(env: Optional[Mapping[str, str]] = None) -> dict:
    """The legacy file's parsed dict; ``{}`` when absent or junk. Never raises."""
    try:
        with open(llm_config_path(env), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def legacy_names_in_use(
    env: Optional[Mapping[str, str]] = None, memory_dir: Optional[str] = None
) -> List[Tuple[str, str, str]]:
    """Every legacy name in use: ``[(surface, old, new)]`` with surface ``env`` or
    ``config``, in a fixed order (env names sorted, then ``.format`` keys, then the LLM
    file's keys). Read-only; never raises."""
    out: List[Tuple[str, str, str]] = []
    try:
        e = os.environ if env is None else env
        for old in sorted(LEGACY_ENV):
            raw = e.get(old)
            if raw is not None and raw.strip():
                out.append(("env", old, LEGACY_ENV[old]))
        if memory_dir:
            from .provenance_format import legacy_policy_keys

            for key in legacy_policy_keys(memory_dir):
                out.append(("config", f".format {key}", f"hippo.json {key}"))
        doc = legacy_llm_file(e)
        for key in sorted(doc):
            new = LEGACY_LLM_FILE_KEYS.get(key)
            if new:
                out.append(("config", f"hippo-llm.json {key}", new))
    except Exception:
        return out
    return out


def usage_verb(old: str) -> str:
    """The usage-rollup verb for a legacy name (``.format volatile_paths`` ->
    ``format.volatile_paths``) — no spaces or colons, which the rollup key reserves."""
    v = old.strip().lstrip(".").replace(" ", ".").replace(":", ".")
    return v.replace("hippo-llm.json.", "hippo-llm.")
