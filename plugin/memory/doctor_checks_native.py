"""NAT-1's doctor line: native auto memory's settings and footprint beside this corpus.

Read-only. ``warn`` only when a setting changes where (or whether) native memory reads
``MEMORY.md``, because that decides whether hippo's always-load floor reaches context at
all; the native-write and untracked counts are information. ``DoctorContext`` lives in
``doctor_checks_env``.
"""

from __future__ import annotations

from typing import Dict

from .doctor_checks_env import DoctorContext

_MAX_NAMES = 5


def _names(names) -> str:
    shown = ", ".join(names[:_MAX_NAMES])
    more = f" (+{len(names) - _MAX_NAMES} more)" if len(names) > _MAX_NAMES else ""
    return shown + more


def check_native_auto_memory(ctx: DoctorContext) -> Dict[str, str]:
    """NAT-1: is native auto memory on, where does it read and write, and what has it left
    in the corpus that hippo never adopted."""
    try:
        from .native_memory import native_memory_state

        s = native_memory_state(ctx.memory_dir, ctx.repo_root)
        parts = []
        warn = False
        if s["enabled"]:
            parts.append(f"native auto memory ON ({s['decided_by']})")
        else:
            warn = True
            parts.append(
                f"native auto memory OFF ({s['decided_by']}) — native memory then skips MEMORY.md, "
                "so hippo's always-load floor does not reach context (see PLATFORM.md)"
            )
        if s["directory"]:
            if s["directory_is_corpus"]:
                parts.append(f"autoMemoryDirectory ({s['directory_source']}) points at this corpus")
            else:
                warn = True
                parts.append(
                    f"autoMemoryDirectory ({s['directory_source']}) = {s['directory']}, not this "
                    "corpus — native memory reads MEMORY.md and writes topic files THERE, "
                    "bypassing hippo's projects-dir symlink"
                )
        if s["stamped"]:
            line = f"{s['stamped']} corpus file(s) carry native auto memory's stamp"
            unadopted = s["stamped_without_provenance"]
            if unadopted:
                line += f", {len(unadopted)} with no hippo provenance: {_names(unadopted)}"
            parts.append(line)
        if s["untracked"]:
            parts.append(f"{len(s['untracked'])} corpus file(s) untracked by git: {_names(s['untracked'])}")
        return {"status": "warn" if warn else "ok", "message": "; ".join(parts) + "."}
    except Exception as exc:
        return {"status": "warn", "message": f"native auto-memory check failed: {exc}."}
