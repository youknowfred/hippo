"""Receipt checks for the deterministic doctor engine — the v2 scoreboard's durable inputs.

OBS-1's 30-day KPIs from the rotation-proof daily rollups, OBS-2's per-verb surface usage
from the same rows, OBS-4's shell-measured hook wall, and HOT-6's warm-recall paths. Read-only and display-only (``ok`` unless the read itself
fails): these lines report numbers a later gate judges, they never judge them here.
``DoctorContext`` lives in ``doctor_checks_env``.
"""

from __future__ import annotations

import os
from typing import Dict, List

from .doctor_checks_env import DoctorContext


def _median(values: List[float]) -> float:
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _p95(values: List[float]) -> float:
    """Nearest-rank p95, the same definition KPI-3's latency line uses."""
    s = sorted(values)
    n = len(s)
    return s[max(1, min(n, (95 * n + 99) // 100)) - 1]


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_hook_wall(ctx: DoctorContext) -> Dict[str, str]:
    """OBS-4: the recall hook's shell-measured wall against its in-process ``latency_ms``.

    ``latency_ms`` is timed inside Python around recall itself, so it never counted the
    interpreter start and imports every prompt pays. Hook rows from v1.40.0 on carry
    ``wall_ms`` from a shell stamp taken at hook start; the gap between the two is the cost
    the KPI-3 line could not see. Hook channel only; ``ok`` always.
    """
    try:
        from .telemetry import default_telemetry_dir, read_events

        td = default_telemetry_dir(ctx.memory_dir)
        rows = [
            (float(e["wall_ms"]), float(e["latency_ms"]))
            for e in read_events(td)
            if e.get("channel") in (None, "hook") and _num(e.get("wall_ms")) and _num(e.get("latency_ms"))
        ]
        if not rows:
            return {
                "status": "ok",
                "message": "hook wall: no shell-stamped recalls yet (rows carry wall_ms from "
                "v1.40.0 on, on a host whose bash can stamp milliseconds).",
            }
        walls = [w for w, _ in rows]
        logged = [lat for _, lat in rows]
        gaps = [w - lat for w, lat in rows]
        return {
            "status": "ok",
            "message": f"hook wall p50 {_median(walls):.0f}ms / p95 {_p95(walls):.0f}ms vs logged "
            f"p50 {_median(logged):.0f}ms / p95 {_p95(logged):.0f}ms over {len(rows)} stamped "
            f"recall(s) — the logged latency misses a median {_median(gaps):.0f}ms "
            "(interpreter start + imports).",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"hook wall check failed: {exc}."}


def check_kpi_rollups(ctx: DoctorContext) -> Dict[str, str]:
    """OBS-1: the last 30 days of this corpus's hook and SessionStart, from the daily rollups
    (which survive ledger rotation). Percentiles read as bucket upper bounds. ``ok`` always."""
    try:
        from .telemetry import default_telemetry_dir
        from .telemetry_rollup import read_rollups, summarize

        k = summarize(read_rollups(default_telemetry_dir(ctx.memory_dir), days=30))
        if not k["days"]:
            return {
                "status": "ok",
                "message": "30-day KPIs: no daily rollups yet (recorded from v1.40.0 on; "
                "one row per active day).",
            }
        parts = [f"30-day KPIs ({k['days']} day(s) since {k['first']}):"]
        if k["prompts"]:
            human = k["trigger"].get("human", 0)
            parts.append(
                f"{k['prompts']} prompt(s), {human} human / {k['machine_prompts']} machine "
                f"({k['machine_injected_chars']} chars injected on machine turns)"
            )
            if k["ran_recall"]:
                parts.append(f"abstained {k['abstained']}/{k['ran_recall']}")
            if k["injected_prompts"]:
                parts.append(
                    f"{k['chars_per_injected_prompt']:.0f} chars per injecting prompt, "
                    f"per-session p50 ≤{k['session_chars_p50']} / p95 ≤{k['session_chars_p95']}"
                )
            if k["wall_p95"]:
                parts.append(f"hook wall p50 ≤{k['wall_p50']}ms / p95 ≤{k['wall_p95']}ms")
        if k["ss_runs"]:
            ss = f"SessionStart median ≤{k['ss_chars_p50']} chars, {k['ss_at_cap']}/{k['ss_runs']} at the cap"
            if k["ss_dropped"]:
                top = sorted(k["ss_dropped"].items(), key=lambda kv: (-kv[1], kv[0]))[:3]
                ss += ", most dropped: " + ", ".join(f"{name} ({n})" for name, n in top)
            parts.append(ss)
        return {"status": "ok", "message": " ".join(parts[:1]) + " " + "; ".join(parts[1:]) + "."}
    except Exception as exc:
        return {"status": "warn", "message": f"30-day KPI check failed: {exc}."}


_USAGE_LINE_MAX = 14


def check_surface_usage(ctx: DoctorContext) -> Dict[str, str]:
    """OBS-2: 30-day use counts per surface and verb — every MCP tool call, ``hippo <verb>``,
    skill preflight and hook spawn — from the daily rollups. This is the count the v2
    deprecation windows read before any name is removed. ``ok`` always."""
    try:
        from .telemetry import default_telemetry_dir
        from .telemetry_rollup import read_rollups, summarize

        k = summarize(read_rollups(default_telemetry_dir(ctx.memory_dir), days=30))
        usage = k["surface"]
        if not usage:
            return {
                "status": "ok",
                "message": "surface usage (30 days): nothing counted yet (recorded from v1.40.0 on).",
            }
        ranked = sorted(usage.items(), key=lambda kv: (-kv[1], kv[0]))
        shown = ", ".join(f"{name} {n}" for name, n in ranked[:_USAGE_LINE_MAX])
        more = f" (+{len(ranked) - _USAGE_LINE_MAX} more)" if len(ranked) > _USAGE_LINE_MAX else ""
        clients = ", ".join(f"{c} {n}" for c, n in sorted(k["client"].items(), key=lambda kv: (-kv[1], kv[0])))
        return {
            "status": "ok",
            "message": f"surface usage (30 days, {sum(usage.values())} uses): {shown}{more}"
            + (f"; clients: {clients}" if clients else "")
            + ".",
        }
    except Exception as exc:
        return {"status": "warn", "message": f"surface usage check failed: {exc}."}


def check_capture_queue(ctx: DoctorContext) -> Dict[str, str]:
    """TND-5: the capture queue's size, its expired shelf, and 30 days of inflow and drain
    (captured / folded / restored in; expired / discarded / drafted out) from the daily
    rollups. Read-only; ``ok`` always — a deep queue is the SessionStart nudge's job."""
    try:
        from .capture_queue import default_pending_dir, expired_count, pending_count
        from .telemetry import default_telemetry_dir
        from .telemetry_rollup import QUEUE_EVENTS, read_rollups, summarize

        pd = default_pending_dir(ctx.memory_dir)
        n, n_exp = pending_count(pd), expired_count(pd)
        msg = f"capture queue: {n} pending, {n_exp} expired"
        if n_exp:
            msg += " (kept, never deleted; `hippo capture --restore --all` brings them back)"
        flow = summarize(read_rollups(default_telemetry_dir(ctx.memory_dir), days=30))["queue"]
        if flow:
            msg += "; 30 days: " + ", ".join(f"{flow.get(ev, 0)} {ev}" for ev in QUEUE_EVENTS)
        return {"status": "ok", "message": msg + "."}
    except Exception as exc:
        return {"status": "warn", "message": f"capture queue check failed: {exc}."}
def check_warm_recall(ctx: DoctorContext) -> Dict[str, str]:
    """HOT-6: whether the opt-in warm-recall hook is configured (local, project or user
    settings), and how this corpus's prompts were served over 30 days — warm (the session's
    MCP server), spawn (a fresh process) or failed — with each path's wall. ``ok`` unless the
    configured entry is not this version's."""
    try:
        from .setup_cli import format_path_counts, path_counts, warm_state

        st = warm_state(ctx.repo_root)
        counts = path_counts(ctx.memory_dir)
        if not st["configured"]:
            from .recall_warm import warm_dir

            msg = "warm recall: off (`hippo setup --warm` previews turning it on)"
            if counts["warm"] or counts["failed"]:
                msg += "; " + format_path_counts(counts)
            d = warm_dir()
            if d and os.listdir(d):
                return {
                    "status": "warn",
                    "message": msg + "; its handshake files are still here, so each prompt's "
                    "hook keeps writing one — `hippo setup --warm --off --yes` clears them.",
                }
            return {"status": "ok", "message": msg + "."}
        msg = f"warm recall: on (the hook is in {st['file']})"
        status = "ok"
        if not st["current"]:
            status = "warn"
            msg += " but differs from this version's entry — `hippo setup --warm --yes` updates it"
        return {"status": status, "message": msg + "; " + format_path_counts(counts) + "."}
    except Exception as exc:
        return {"status": "warn", "message": f"warm recall check failed: {exc}."}
