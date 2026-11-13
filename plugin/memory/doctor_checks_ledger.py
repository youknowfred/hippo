"""LDG-1: doctor's ledger-join line — what the corpus declares, what the cache holds.

No line at all when ``hippo.json`` declares no ``ledgers`` (so a corpus without one renders
exactly as before). Otherwise one line: each usable ledger with the ids its cache holds and
the snapshot date, or why a declared ledger was dropped (a ``warn``). Reads the declaration
and the derived cache only, never the ledger file itself. ``DoctorContext`` lives in
``doctor_checks_env``.
"""

from __future__ import annotations

from typing import Dict, Optional

from .doctor_checks_env import DoctorContext


def check_ledger(ctx: DoctorContext) -> Optional[Dict[str, str]]:
    try:
        from .build_index import default_index_dir
        from .ledger import corpus_top, declared, load, read_declaration

        if not declared(ctx.memory_dir):
            return None
        ledgers, dropped = read_declaration(ctx.memory_dir, corpus_top(ctx.memory_dir, ctx.repo_root))
        cache = load(default_index_dir(ctx.memory_dir))
        held = {m.get("name"): m for m in (cache.ledgers if cache else [])}
        names = {d.get("name") for d in dropped}
        for d in cache.dropped if cache else []:
            if d.get("name") not in names and any(lg["name"] == d.get("name") for lg in ledgers):
                dropped.append(d)
                names.add(d.get("name"))
        parts = []
        for lg in ledgers:
            if lg["name"] in names:
                continue
            m = held.get(lg["name"])
            if m:
                parts.append(
                    f"{lg['name']} ({lg['path']}): {m.get('ids', 0)} ids in the cache, "
                    f"snapshot {m.get('as_of')}"
                )
            else:
                parts.append(
                    f"{lg['name']} ({lg['path']}): no cache yet (the next SessionStart or "
                    "hippo build-index builds it, on a trusted corpus)"
                )
        for d in dropped:
            parts.append(f"{d.get('name')} dropped: {d.get('reason')}")
        n = len(ledgers) + sum(1 for d in dropped if d.get("name") not in {lg["name"] for lg in ledgers})
        msg = f"ledger join: {n} ledger(s) declared in hippo.json — " + "; ".join(parts) + "."
        return {"status": "warn" if dropped else "ok", "message": msg}
    except Exception as exc:
        return {"status": "warn", "message": f"ledger-join check failed: {exc}."}
