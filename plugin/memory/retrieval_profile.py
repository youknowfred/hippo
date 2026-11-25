"""HCR-1: the retrieval profile — one machine-level switch (``floor`` | ``lean`` | ``high``).

The high-ceiling retrieval roadmap (``planning/ROADMAP.retrieval.yaml``, ratified
2026-11-25) tiers recall by machine: ``floor`` is BM25-only (the dense lane off by
POLICY, not merely degraded on a cold cache), ``lean`` is today's default stack, and
``high`` is the big-model profile later HCR items hang real models off — under HCR-1
it still resolves to lean's models, so selecting it is safe anywhere and means
"opted in", never "faster today".

Resolution mirrors ``build_index.resolve_embed_model`` exactly — env > persisted
preset > default — and shares its preset FILE: the profile is a second key in
``${CLAUDE_PLUGIN_DATA}/model.json`` (``{"retrieval_profile": "high"}``), because
both keys answer the same question ("what does THIS machine run at recall time")
and a second file would just be a second thing to tear. An invalid value at one
level falls through to the next and is reported by doctor, never raised over: this
resolves on hook fast paths, the same contract as the model resolver.

The ``high`` OFFER (setup + doctor) is hardware-gated — arm64 macOS with >= 64 GiB
(``HIGH_MIN_BYTES``) — but an explicit ``hippo setup --profile high --yes`` is the
user's call on any machine: the roadmap gates the offer, not the hand on the switch.

Leaf module: stdlib plus ``atomic`` (COR-18 write discipline) only, no imports from
``build_index`` (build_index imports US for the floor wiring in ``dense_disabled``)."""

from __future__ import annotations

import json
import os
import platform
import sys
from typing import Dict, List, Optional, Tuple

PROFILES = ("floor", "lean", "high")
DEFAULT_PROFILE = "lean"
ENV_VAR = "HIPPO_RETRIEVAL_PROFILE"
PRESET_KEY = "retrieval_profile"
_PRESET_FILENAME = "model.json"  # build_index._MODEL_PRESET_FILENAME — shared on purpose

# The setup offer's hardware bar (EXPLORATIONS.retrieval.md §3): unified memory large
# enough that the HCR-2 models are a rounding error. 64 GiB, binary.
HIGH_MIN_BYTES = 64 * 2**30


def _preset_path() -> Optional[str]:
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    if not plugin_data:
        return None
    return os.path.join(plugin_data, _PRESET_FILENAME)


def _read_preset() -> Dict:
    """The parsed preset dict, ``{}`` on any problem — missing file/env, corrupt JSON,
    non-dict JSON. Never raises (hook fast path)."""
    path = _preset_path()
    if not path:
        return {}
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def profile_source() -> Tuple[str, str]:
    """``(profile, source)`` where source is ``env`` | ``preset`` | ``default``.

    Precedence is ``resolve_embed_model``'s verbatim: env wins unconditionally, the
    persisted preset makes the choice stick across sessions, the default is ``lean``.
    A value outside ``PROFILES`` at one level falls through to the next (doctor's
    ``check_retrieval_profile`` names the ignored value; this function stays silent)."""
    env_raw = (os.environ.get(ENV_VAR) or "").strip().lower()
    if env_raw in PROFILES:
        return env_raw, "env"
    preset_raw = _read_preset().get(PRESET_KEY)
    if isinstance(preset_raw, str) and preset_raw.strip().lower() in PROFILES:
        return preset_raw.strip().lower(), "preset"
    return DEFAULT_PROFILE, "default"


def resolve_retrieval_profile() -> str:
    """The active profile name. Cheap (one getenv + at most one small JSON read),
    never raises."""
    return profile_source()[0]


def ignored_values() -> List[Tuple[str, str]]:
    """``(level, raw)`` pairs the resolver silently skipped — an env var or preset key
    holding something outside ``PROFILES``. Doctor's input; empty on a clean machine."""
    out: List[Tuple[str, str]] = []
    env_raw = (os.environ.get(ENV_VAR) or "").strip()
    if env_raw and env_raw.lower() not in PROFILES:
        out.append(("env", env_raw))
    preset_raw = _read_preset().get(PRESET_KEY)
    if preset_raw is not None and not (
        isinstance(preset_raw, str) and preset_raw.strip().lower() in PROFILES
    ):
        out.append(("preset", str(preset_raw)))
    return out


def write_retrieval_profile(profile: str) -> str:
    """Persist ``profile`` into the preset file, keeping every other key (``embed_model``
    survives). Returns the path written. Raises ``ValueError`` on an unknown profile, a
    missing ``CLAUDE_PLUGIN_DATA``, or a preset file that EXISTS but does not parse —
    setup_cli's rule: never touch a file that does not parse."""
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r} (one of: {', '.join(PROFILES)})")
    path = _preset_path()
    if not path:
        raise ValueError("CLAUDE_PLUGIN_DATA is unset — no machine preset file to write")
    data: Dict = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                parsed = json.load(fh)
        except Exception as exc:
            raise ValueError(f"{path} does not parse ({exc}); fix or remove it first")
        if not isinstance(parsed, dict):
            raise ValueError(f"{path} is not a JSON object; fix or remove it first")
        data = parsed
    data[PRESET_KEY] = profile
    os.makedirs(os.path.dirname(path), exist_ok=True)
    from .atomic import write_json_atomic

    write_json_atomic(path, data)
    return path


def remove_retrieval_profile() -> bool:
    """Drop the profile key (back to the ``lean`` default), keeping every other key.
    True when a key was actually removed. Same parse-refusal rule as the writer."""
    path = _preset_path()
    if not path or not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as fh:
            parsed = json.load(fh)
    except Exception as exc:
        raise ValueError(f"{path} does not parse ({exc}); fix or remove it first")
    if not isinstance(parsed, dict) or PRESET_KEY not in parsed:
        return False
    parsed.pop(PRESET_KEY, None)
    from .atomic import write_json_atomic

    write_json_atomic(path, parsed)
    return True


def machine_memsize_bytes() -> Optional[int]:
    """Physical memory, or None where the probe is unavailable — None means NO offer
    (conservative), never a guess."""
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except Exception:
        return None


def qualifies_for_high() -> bool:
    """The OFFER gate: Apple Silicon macOS with >= ``HIGH_MIN_BYTES`` physical memory.
    Gates what setup/doctor suggest, never what an explicit ``--profile high`` may write."""
    if sys.platform != "darwin" or platform.machine() != "arm64":
        return False
    mem = machine_memsize_bytes()
    return mem is not None and mem >= HIGH_MIN_BYTES


def high_offer_line() -> Optional[str]:
    """The ONE offer sentence setup and doctor both print (shared here so the wording
    can never fork), or None when the machine does not qualify or is already on high."""
    if not qualifies_for_high():
        return None
    profile = resolve_retrieval_profile()
    if profile == "high":
        return None
    mem = machine_memsize_bytes() or 0
    return (
        f"this machine qualifies for the high retrieval profile "
        f"({mem // 2**30} GiB, arm64) — opt in with `hippo setup --profile high --yes` "
        f"(today high runs lean's models; the big-model tier ships next)"
    )
