"""SRF-4: the settings check — every legacy name in use, with its new spelling.

v1.42 moved hippo's settings into three homes (``settings.py``): the corpus policy file
``.claude/memory/hippo.json``, the plugin's ``userConfig`` options, and a frozen env set of
twelve names. Every old spelling keeps working through v1.43; this check is where a human
sees which ones are still in use and what each one is called now. It also names a
plaintext API key in the legacy ``hippo-llm.json`` — the key's new home is a ``sensitive``
plugin option Claude Code keeps in the system keychain — and any ``HIPPO_DISABLE`` entry
that names no feature. When that file holds the key or turns an LLM pass on, the line also
says what moving it costs a schedule: the plugin options never reach a cron or launchd
``hippo sleep`` (or a shell run), so a schedule that relies on the file needs the env pair
before v2.0 (owner ruling 2026-10-05). Never a key value. Read-only and deterministic (fixed
order, no timestamps).
"""

from __future__ import annotations

import os
from typing import Dict

from .doctor_checks_env import DoctorContext


def _key_file_label() -> str:
    override = (os.environ.get("HIPPO_LLM_CONFIG") or "").strip()
    return override or "~/.claude/hippo-llm.json"


def check_settings(ctx: DoctorContext) -> Dict[str, str]:
    """``ok`` when nothing legacy is in use; ``warn`` naming each old spelling and its new
    one, a plaintext key file, and unknown ``HIPPO_DISABLE`` entries."""
    try:
        from .llm_client import as_bool
        from .provenance_format import read_policy_file
        from .settings import DISABLE_FEATURES, disable_list, legacy_llm_file, legacy_names_in_use

        seen = legacy_names_in_use(memory_dir=ctx.memory_dir)
        _known, unknown = disable_list()
        llm_doc = legacy_llm_file()
        key = llm_doc.get("api_key")
        plaintext_key = isinstance(key, str) and bool(key.strip())
        llm_pass_on = any(as_bool(llm_doc.get(k)) for k in ("dream_contradictions", "capture_triage"))
        parts = []
        if seen:
            parts.append(
                f"{len(seen)} legacy setting name(s) in use, still read until v2.0 — "
                + "; ".join(f"{old} → {new}" for _surface, old, new in seen)
            )
        if plaintext_key:
            parts.append(
                f"{_key_file_label()} holds an LLM API key in plain text — move it to the "
                "hippo plugin's LLM API key option (set it from /plugin; Claude Code keeps it "
                "in your system keychain), then delete the key from that file"
            )
        if plaintext_key or llm_pass_on:
            parts.append(
                "hippo's plugin options (in /config) never reach a scheduled or shell run "
                "(`hippo sleep` from cron or launchd, a terminal `hippo dream`): a schedule "
                f"that relies on {_key_file_label()} needs HIPPO_DREAM_CONTRADICTIONS=1 and "
                "HIPPO_LLM_API_KEY in its own environment before v2.0 stops reading the file"
            )
        if unknown:
            parts.append(
                f"HIPPO_DISABLE names unknown feature(s): {', '.join(sorted(unknown))} "
                f"(known: {', '.join(DISABLE_FEATURES)})"
            )
        if parts:
            return {"status": "warn", "message": "settings: " + "; ".join(parts) + "."}
        policy = sorted(read_policy_file(ctx.memory_dir)) if ctx.memory_dir else []
        tail = (
            f"; corpus policy in hippo.json ({', '.join(policy)})" if policy
            else "; no corpus policy file (.claude/memory/hippo.json is optional)"
        )
        return {"status": "ok", "message": "settings: no legacy names in use" + tail + "."}
    except Exception as exc:
        return {"status": "warn", "message": f"settings check failed: {exc}."}
