"""GAT-1's doctor line: what the write gate does for this corpus, on this machine.

Read-only. ``ok`` for every settled posture (off, warns, may refuse); ``warn`` when the
corpus asks for something this machine is not doing yet (a refusal declared but not
accepted here, or not committed) or names a value hippo does not know.
"""

from __future__ import annotations

from typing import Dict

from .doctor_checks_env import DoctorContext

_WHAT = "a corpus write that leaves a budget over and makes it worse"


def check_write_gate(ctx: DoctorContext) -> Dict[str, str]:
    try:
        from .gate_hook import committed_write_mode
        from .policy_consent import declared, honoured, pending_line

        g = declared(ctx.memory_dir, "gate")
        raw = g.get("write") if isinstance(g, dict) else None
        if g is not None and not isinstance(g, dict):
            return {
                "status": "warn",
                "message": 'write gate: hippo.json sets "gate" to something other than an object, '
                'so it is ignored and the gate warns (the default). Expected {"write": "warn"}, '
                '{"write": "deny"} or {"write": "off"}.',
            }
        if raw is None:
            return {"status": "ok", "message": f"write gate: warns on {_WHAT} (the default)."}
        if raw == "off":
            return {"status": "ok", "message": "write gate: off for this corpus (hippo.json gate.write = off)."}
        if raw == "warn":
            return {"status": "ok", "message": f"write gate: warns on {_WHAT}."}
        if raw != "deny":
            return {
                "status": "warn",
                "message": f"write gate: hippo.json sets gate.write to {raw!r}, which hippo does not "
                'know, so the gate only warns. Use "warn", "deny" or "off".',
            }
        h = honoured(ctx.memory_dir, "gate", ctx.repo_root)
        if not (isinstance(h, dict) and h.get("write") == "deny"):
            held = pending_line(ctx.memory_dir, ctx.repo_root) or (
                "this machine has not accepted it, so the gate only warns here."
            )
            return {"status": "warn", "message": f"write gate: hippo.json asks it to refuse {_WHAT}, but {held}"}
        if committed_write_mode(ctx.memory_dir) != "deny":
            return {
                "status": "warn",
                "message": f"write gate: refusing {_WHAT} is accepted on this machine but not "
                "committed in hippo.json, so the gate only warns until that change is committed.",
            }
        return {
            "status": "ok",
            "message": f"write gate: may refuse {_WHAT} (committed in hippo.json, accepted on this machine).",
        }
    except Exception as exc:
        return {"status": "ok", "message": f"write gate: check skipped ({exc})."}
