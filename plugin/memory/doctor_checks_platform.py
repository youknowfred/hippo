"""Doctor checks about the platform hippo runs on: which plugin version Claude Code has
installed versus the one this process is actually running (FMT-3), and the running Claude
Code version against hippo's declared floor (PLT-2).

Read-only; every check returns ``{"status", "message"}`` and never raises.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

from .doctor_checks_env import DoctorContext


def _semver(v: Optional[str]) -> Optional[Tuple[int, ...]]:
    try:
        return tuple(int(p) for p in str(v).strip().lstrip("v").split("-")[0].split("."))
    except Exception:
        return None


def _running_version(ctx: DoctorContext) -> Optional[str]:
    try:
        with open(os.path.join(ctx.plugin_root, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            return json.load(fh).get("version")
    except Exception:
        return None


def installed_plugins_path() -> str:
    """Claude Code's plugin registry: ``<config dir>/plugins/installed_plugins.json``."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "plugins", "installed_plugins.json")


def installed_versions(path: Optional[str] = None) -> List[str]:
    """Every version Claude Code records for a ``hippo@<marketplace>`` install; ``[]`` when
    the registry is missing, unreadable, or has no hippo entry."""
    try:
        with open(path or installed_plugins_path(), encoding="utf-8") as fh:
            doc = json.load(fh)
        out: List[str] = []
        for key, rows in (doc.get("plugins") or {}).items():
            if str(key).split("@", 1)[0] != "hippo":
                continue
            for row in rows if isinstance(rows, list) else [rows]:
                v = row.get("version") if isinstance(row, dict) else None
                if v and v not in out:
                    out.append(str(v))
        return out
    except Exception:
        return []


def check_installed_version(ctx: DoctorContext) -> Dict[str, str]:
    """FMT-3: the version Claude Code INSTALLED vs the version this process RUNS.

    After ``claude plugin update`` the registry moves at once, but every session started
    before it keeps the old hooks and MCP server until it restarts (field installs lagged
    about three minors). A mismatch names which side is behind."""
    try:
        running = _running_version(ctx)
        installed = installed_versions()
        if not running:
            return {"status": "warn", "message": "installed-vs-running: this plugin's version is unreadable."}
        if not installed:
            return {
                "status": "ok",
                "message": f"installed-vs-running: v{running} running; no marketplace install of "
                "hippo is recorded here (a --plugin-dir or dev load), so nothing to compare.",
            }
        if running in installed:
            return {"status": "ok", "message": f"installed-vs-running: v{running} running = installed."}
        newest = max(installed, key=lambda v: _semver(v) or ())
        behind = (_semver(running) or ()) < (_semver(newest) or ())
        tail = (
            "this session started before the update and still runs the old hooks and MCP "
            "server — restart Claude Code sessions to load it"
            if behind
            else "this process is ahead of the install (a --plugin-dir or dev load)"
        )
        return {
            "status": "warn",
            "message": f"installed-vs-running: Claude Code has hippo v{newest} installed but this "
            f"process runs v{running} — {tail}.",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"installed-vs-running check failed: {exc}."}


def check_claude_code_version(ctx: DoctorContext) -> Dict[str, str]:
    """PLT-2: the running Claude Code version against ``MIN_CLAUDE_CODE``."""
    try:
        from .platform_floor import MIN_CLAUDE_CODE, claude_code_version, harness_too_old

        v = claude_code_version()
        if v is None:
            return {
                "status": "ok",
                "message": f"Claude Code version: unknown here (no AI_AGENT in this process) — "
                f"hippo supports {MIN_CLAUDE_CODE} or newer.",
            }
        if harness_too_old():
            return {
                "status": "warn",
                "message": f"Claude Code {v} is older than hippo's floor {MIN_CLAUDE_CODE} — "
                "plugin options and newer hook types may not load; update Claude Code.",
            }
        return {"status": "ok", "message": f"Claude Code {v} (floor {MIN_CLAUDE_CODE})."}
    except Exception as exc:
        return {"status": "warn", "message": f"Claude Code version check failed: {exc}."}


def check_attention(ctx: DoctorContext) -> Dict[str, str]:
    """CLM-2: which SessionStart attention mode applies here, and what is muted."""
    try:
        from .attention import attention_mode, muted_signals

        mode = attention_mode()
        muted, refused = muted_signals()
        msg = f"attention: {mode}"
        msg += (
            " (the short digest)" if mode == "calm"
            else " (set HIPPO_ATTENTION=calm or the plugin's calm_session_start option for the short digest)"
        )
        if muted:
            msg += f"; muted: {', '.join(sorted(muted))}"
        if refused:
            msg += f"; NOT muted (integrity signals always show): {', '.join(sorted(refused))}"
        return {"status": "warn" if refused else "ok", "message": msg + "."}
    except Exception as exc:
        return {"status": "warn", "message": f"attention check failed: {exc}."}


_WIRE = "mcp__plugin_hippo_hippo__"


def check_mcp_allowlist(ctx) -> Dict[str, str]:
    """SRF-2: permission rules that name a hippo tool deprecated in v1.42. Read-only.

    The old names keep working through v1.43 and are removed in v2.0; a rule naming only
    the old id would then stop matching, so each one is named with the id to add.
    """
    try:
        from .mcp_schemas_v2 import DEPRECATED
        from .native_memory import _settings_layers

        found = []
        for label, settings in _settings_layers(ctx.repo_root):
            perms = settings.get("permissions") if isinstance(settings, dict) else None
            rules = []
            if isinstance(perms, dict):
                for key in ("allow", "ask", "deny"):
                    if isinstance(perms.get(key), list):
                        rules += [r for r in perms[key] if isinstance(r, str)]
            for rule in rules:
                if rule.startswith(_WIRE) and rule[len(_WIRE):] in DEPRECATED:
                    old = rule[len(_WIRE):]
                    found.append(f"{rule} → {_WIRE}{DEPRECATED[old][0]} ({label})")
        if not found:
            return {"status": "ok", "message": "no permission rule names a deprecated hippo tool."}
        return {
            "status": "warn",
            "message": (
                f"{len(found)} permission rule(s) name hippo tools that are removed in v2.0 "
                f"(they work until then): {'; '.join(sorted(set(found)))}. Add the new id beside "
                "each; nothing here edits your settings."
            ),
        }
    except Exception as exc:
        return {"status": "warn", "message": f"permission-rule check failed: {exc}."}
