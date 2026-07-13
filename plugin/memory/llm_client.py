"""Provider-agnostic standalone-LLM helper for hippo's OPT-IN enrichment surfaces.

Until this module, ZERO standalone LLM/API calls existed anywhere in ``plugin/memory/`` —
every ounce of LLM judgment ran inside a live interactive session executing a skill, and
everything headless was a heuristic in code. Two opt-in, default-OFF surfaces changed that:

  - capture-time triage (``HIPPO_CAPTURE_LLM`` — ``capture_triage.py``), and
  - dream's contradiction discovery (``HIPPO_DREAM_CONTRADICTIONS`` — ``dream.py``).

Both are PROPOSE-ONLY enrichers of existing human-reviewed queues (the pending-capture
queue, the /hippo:resolve inbox). Neither writes the corpus; the human approval gate is
untouched. This module exists so that fact stays auditable in ONE place: any future
standalone call must come through here, and the callers' propose-only posture is the
admission bar.

Design contract (every caller depends on it):
  - ``complete(prompt, *, timeout_s) -> str | None`` — ``None`` on ANY failure: missing
    API key, network error, timeout, non-2xx, malformed response, unknown provider.
    NEVER raises. Callers treat ``None`` as "skip the enrichment entirely" (fail open),
    so a dead network can never break a hook or a dream pass.
  - stdlib-only transport (``urllib``): the plugin venv pins exactly the deps the package
    imports (see plugin/requirements.txt) and this module must not grow that set.
  - ONE CONFIG FILE, layered under the env vars (owner decision 2026-07-13 — "centralize
    config" over env-var sprawl): ``~/.claude/hippo-llm.json``, the same machine-local
    dotfile family as ``hippo-trust.json`` / ``hippo-projects.json``
    (``HIPPO_LLM_CONFIG`` relocates it — hermetic tests point it at a tmp path).
    Precedence PER KEY is env var > config file > shipped default, so the file is the
    durable machine-wide setting and an env var stays the per-invocation/CI override.
    Recognized keys (all optional)::

        {
          "provider": "anthropic",            // HIPPO_LLM_PROVIDER
          "model": "claude-haiku-4-5",        // HIPPO_LLM_MODEL
          "base_url": "https://…",            // HIPPO_LLM_BASE_URL
          "api_key": "sk-ant-…",              // HIPPO_LLM_API_KEY / ANTHROPIC_API_KEY
          "capture_triage": true,              // HIPPO_CAPTURE_LLM (CAP-LLM opt-in)
          "capture_timeout_s": 6,              // HIPPO_CAPTURE_LLM_TIMEOUT
          "dream_contradictions": true,        // HIPPO_DREAM_CONTRADICTIONS (DRM-C opt-in)
          "dream_timeout_s": 10,               // HIPPO_DREAM_LLM_TIMEOUT
          "contra_max_pairs": 6,               // DREAM_CONTRA_MAX_PAIRS
          "contra_min_cofire": 0.9             // DREAM_CONTRA_MIN_COFIRE
        }

    The feature modules (``capture_triage``, ``dream``) read their keys through
    ``file_setting()`` here so the whole LLM surface has one config home. Adding a
    provider = one function + one ``_PROVIDERS`` entry; no caller changes.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

DEFAULT_PROVIDER = "anthropic"
# Small/fast tier by design: both shipped callers run inside bounded budgets (a hook's
# hard timeout; an offline dream pass a human is watching). Deliberately the ALIAS, not a
# dated snapshot (owner decision 2026-07-13, max flexibility): Anthropic aliases track the
# current snapshot of the tier, so hippo picks up model refreshes without a code change.
# Override: HIPPO_LLM_MODEL / config "model".
DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_BASE_URL = "https://api.anthropic.com"
_ANTHROPIC_VERSION = "2023-06-01"
# Response-body read cap — a misbehaving endpoint must not balloon a hook's memory.
_MAX_RESPONSE_BYTES = 1_000_000
_DEFAULT_MAX_TOKENS = 512

_CONFIG_FILENAME = "hippo-llm.json"
# The closed truthy set every hippo opt-in flag parses — shared here so the config file's
# tolerated string forms ("1", "true") mean exactly what the env vars mean.
TRUTHY = ("1", "true", "True")


def config_path() -> str:
    """``HIPPO_LLM_CONFIG`` override, else ``~/.claude/hippo-llm.json``.

    The ``hippo-trust.json`` / ``hippo-projects.json`` machine-local dotfile convention:
    one JSON file per machine-scoped concern, relocatable by env var for hermetic tests.
    """
    override = (os.environ.get("HIPPO_LLM_CONFIG") or "").strip()
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".claude", _CONFIG_FILENAME)


def file_config() -> dict:
    """The parsed config file, or ``{}`` on any trouble (absent, junk, non-dict).

    Read fresh per call — the file is tiny, the callers are one-shot processes, and a
    cache would only add a staleness bug surface. Never raises.
    """
    try:
        with open(config_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def file_setting(key: str) -> Any:
    """One key from the config file, or ``None`` — the feature modules' config seam."""
    return file_config().get(key)


def as_bool(value: Any) -> bool:
    """A config value as an opt-in boolean: JSON ``true`` or a TRUTHY string; junk = off."""
    if value is True:
        return True
    return isinstance(value, str) and value.strip() in TRUTHY


def provider_name() -> str:
    """Provider key — env ``HIPPO_LLM_PROVIDER`` > config ``provider`` > ``anthropic``."""
    env = (os.environ.get("HIPPO_LLM_PROVIDER") or "").strip()
    if env:
        return env
    cfg = file_setting("provider")
    if isinstance(cfg, str) and cfg.strip():
        return cfg.strip()
    return DEFAULT_PROVIDER


def model_name() -> str:
    """Model id — env ``HIPPO_LLM_MODEL`` > config ``model`` > the shipped alias."""
    env = (os.environ.get("HIPPO_LLM_MODEL") or "").strip()
    if env:
        return env
    cfg = file_setting("model")
    if isinstance(cfg, str) and cfg.strip():
        return cfg.strip()
    return DEFAULT_MODEL


def _base_url() -> str:
    """Endpoint root — env ``HIPPO_LLM_BASE_URL`` > config ``base_url`` > Anthropic."""
    env = (os.environ.get("HIPPO_LLM_BASE_URL") or "").strip()
    if env:
        return env.rstrip("/")
    cfg = file_setting("base_url")
    if isinstance(cfg, str) and cfg.strip():
        return cfg.strip().rstrip("/")
    return DEFAULT_BASE_URL


def _api_key() -> Optional[str]:
    """The key to send: ``HIPPO_LLM_API_KEY`` > ``ANTHROPIC_API_KEY`` > config ``api_key``.

    Both env vars outrank the file so a shell/CI key always wins; the file slot exists for
    a machine that keeps a hippo-scoped key out of every shell profile. (A dotfile key is
    a convenience, not a vault — same posture as the conventional ``~/.netrc``.)
    """
    for var in ("HIPPO_LLM_API_KEY", "ANTHROPIC_API_KEY"):
        key = (os.environ.get(var) or "").strip()
        if key:
            return key
    cfg = file_setting("api_key")
    if isinstance(cfg, str) and cfg.strip():
        return cfg.strip()
    return None


def _complete_anthropic(
    prompt: str, *, timeout_s: float, max_tokens: int, system: Optional[str]
) -> Optional[str]:
    """One Anthropic Messages API call via stdlib urllib. ``None`` on any failure.

    The import is function-local so merely importing this module (e.g. from a flag-off
    code path) never pays urllib/ssl setup. No retries by design: every caller is a
    bounded, best-effort enrichment where a second attempt would double the latency for
    marginal value — fail open instead.
    """
    key = _api_key()
    if not key:
        return None
    import urllib.request

    body = {
        "model": model_name(),
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system
    req = urllib.request.Request(
        _base_url() + "/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": _ANTHROPIC_VERSION,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read(_MAX_RESPONSE_BYTES)
        data = json.loads(raw.decode("utf-8", errors="replace"))
        parts = [
            blk.get("text", "")
            for blk in (data.get("content") or [])
            if isinstance(blk, dict) and blk.get("type") == "text"
        ]
        text = "".join(parts).strip()
        return text or None
    except Exception:
        return None


_PROVIDERS = {"anthropic": _complete_anthropic}


def complete(
    prompt: str,
    *,
    timeout_s: float,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    system: Optional[str] = None,
) -> Optional[str]:
    """ONE bounded completion from the configured provider, or ``None``. Never raises.

    ``None`` covers every failure class identically — no key, unknown provider, network
    error, timeout, junk response, empty prompt — because every caller's correct reaction
    is the same: skip the enrichment and proceed exactly as if the flag were off.
    """
    try:
        if not prompt or not prompt.strip() or timeout_s <= 0:
            return None
        fn = _PROVIDERS.get(provider_name())
        if fn is None:
            return None
        return fn(prompt, timeout_s=timeout_s, max_tokens=max_tokens, system=system)
    except Exception:
        return None


def extract_json(text: Optional[str]) -> Optional[dict]:
    """The FIRST JSON object embedded in ``text``, or ``None``. Never raises.

    Models asked for "ONLY a JSON object" still routinely wrap it in prose or a code
    fence; both shipped callers need the same defensive parse, so it lives here. Scans
    for each ``{`` and attempts a ``raw_decode`` from it — the first complete object
    wins; a decode that yields a non-dict (e.g. a stray ``{}``-free literal) is skipped.
    """
    if not text:
        return None
    try:
        decoder = json.JSONDecoder()
        idx = text.find("{")
        while idx != -1:
            try:
                obj, _end = decoder.raw_decode(text, idx)
                if isinstance(obj, dict):
                    return obj
            except ValueError:
                pass
            idx = text.find("{", idx + 1)
        return None
    except Exception:
        return None
