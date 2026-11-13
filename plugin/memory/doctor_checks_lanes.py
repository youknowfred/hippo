"""HOT-7: doctor's recall-lanes line — K14's instrument, report-only (V2Q-15).

What share of the memories the prompt hook injected were behavior memories (``type: user``
or ``feedback``) and journals (files matching ``fold_digests`` or ``journals``), over the
daily rollups' window, beside each lane's share of the corpus as the index labelled it, and
whether the lanes act here (a ``journals`` declaration in ``hippo.json``). ``ok`` always:
the K14 target is judged by a later gate once 14 days of the counters exist, never here.
``DoctorContext`` lives in ``doctor_checks_env``.
"""

from __future__ import annotations

from typing import Dict

from .doctor_checks_env import DoctorContext

_WINDOW_DAYS = 30


def _pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:.1f}%" if d else "n/a"


def check_recall_lanes(ctx: DoctorContext) -> Dict[str, str]:
    try:
        from .build_index import _load_manifest, default_index_dir
        from .lanes import BEHAVIOR, FACT, JOURNAL, LANES, active
        from .telemetry import default_telemetry_dir
        from .telemetry_rollup import read_rollups, summarize

        manifest = _load_manifest(default_index_dir(ctx.memory_dir)) or {}
        state = (
            "active (the corpus declares journals)"
            if active(manifest)
            else "not active (no journals declaration in hippo.json)"
        )
        corpus = {lane: 0 for lane in LANES}
        for e in manifest.get("entries") or []:
            lane = e.get("lane")
            corpus[lane if lane in corpus else FACT] += 1
        total = sum(corpus.values())
        parts = [f"recall lanes {state}."]
        if total:
            parts.append(
                f"Corpus ({total} memories): behavior {_pct(corpus[BEHAVIOR], total)}, "
                f"fact {_pct(corpus[FACT], total)}, journal {_pct(corpus[JOURNAL], total)}."
            )
        else:
            parts.append("Corpus shares: no current index yet (the next SessionStart builds it).")
        k = summarize(read_rollups(default_telemetry_dir(ctx.memory_dir), days=_WINDOW_DAYS))
        inj = {lane: int(k["injected_by_lane"].get(lane, 0)) for lane in LANES}
        n = sum(inj.values())
        if n:
            rend = {lane: int(k["rendered_by_lane"].get(lane, 0)) for lane in LANES}
            r = sum(rend.values())
            parts.append(
                f"Hook-injected ({_WINDOW_DAYS} days of rollups, {n} memories): behavior "
                f"{_pct(inj[BEHAVIOR], n)}, journal {_pct(inj[JOURNAL], n)}; rendered in full "
                f"({r}): behavior {_pct(rend[BEHAVIOR], r)}, journal {_pct(rend[JOURNAL], r)}."
            )
        else:
            parts.append("Hook-injected by lane: nothing counted yet (recorded from this release on).")
        return {"status": "ok", "message": " ".join(parts)}
    except Exception as exc:
        return {"status": "warn", "message": f"recall-lanes check failed: {exc}."}
