"""Receipt checks for the deterministic doctor engine — the v2 scoreboard's durable inputs.

OBS-4's shell-measured hook wall. Read-only and display-only (``ok`` unless the read itself
fails): these lines report numbers a later gate judges, they never judge them here.
``DoctorContext`` lives in ``doctor_checks_env``.
"""

from __future__ import annotations

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
