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
  - the provider/model are CONFIG POINTS, not hard-coded in callers:
      ``HIPPO_LLM_PROVIDER``  — provider key (default ``anthropic``; the only one shipped)
      ``HIPPO_LLM_MODEL``     — model id (default a small/fast Haiku-tier model)
      ``HIPPO_LLM_BASE_URL``  — endpoint root (default https://api.anthropic.com), the
                                escape hatch for any Anthropic-compatible proxy/gateway
      ``HIPPO_LLM_API_KEY``   — hippo-scoped key override; falls back to the conventional
                                ``ANTHROPIC_API_KEY``
    Adding a provider = one function + one ``_PROVIDERS`` entry; no caller changes.
"""

from __future__ import annotations

import json
import os
from typing import Optional

DEFAULT_PROVIDER = "anthropic"
# Small/fast tier by design: both shipped callers run inside bounded budgets (a hook's
# hard timeout; an offline dream pass a human is watching). Override: HIPPO_LLM_MODEL.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_BASE_URL = "https://api.anthropic.com"
_ANTHROPIC_VERSION = "2023-06-01"
# Response-body read cap — a misbehaving endpoint must not balloon a hook's memory.
_MAX_RESPONSE_BYTES = 1_000_000
_DEFAULT_MAX_TOKENS = 512


def provider_name() -> str:
    """``HIPPO_LLM_PROVIDER`` (trimmed), default ``anthropic``."""
    return (os.environ.get("HIPPO_LLM_PROVIDER") or DEFAULT_PROVIDER).strip() or DEFAULT_PROVIDER


def model_name() -> str:
    """``HIPPO_LLM_MODEL`` (trimmed), default the shipped small/fast model."""
    return (os.environ.get("HIPPO_LLM_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _api_key() -> Optional[str]:
    """The key to send: ``HIPPO_LLM_API_KEY`` (hippo-scoped) else ``ANTHROPIC_API_KEY``."""
    for var in ("HIPPO_LLM_API_KEY", "ANTHROPIC_API_KEY"):
        key = (os.environ.get(var) or "").strip()
        if key:
            return key
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
    base = (os.environ.get("HIPPO_LLM_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    req = urllib.request.Request(
        base + "/v1/messages",
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
