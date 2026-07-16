"""EXT-3: the interview loop — consolidate asks up to three grounded questions (T17).

hippo tells, but never asks. Three machine-detected GAP signals already exist with no
encode-side loop — the human never thinks to write the missing memory:

  - the recall blind-spot backlog (SIG-3, ``telemetry.abstention_backlog``): queries
    the corpus kept being asked and could not answer;
  - the contradiction inbox (GOV-1, ``resolve_view.unresolved_contradictions``):
    declared conflicts nobody has ruled on;
  - expiring generated drafts (DRM-6, ``dream_generate.draft_sweep_state``): claims
    approaching their decay horizon with no graduation evidence.

``gather_questions`` renders AT MOST ``QUESTION_CAP`` template questions per
consolidate session — each citing its source signal VERBATIM (the count and the query
preview, the pair names, the draft stem), each carrying a ``route`` that names the
EXISTING per-item write verb an acceptance goes through (new_memory check-first /
resolve / reconsolidate). The asks step itself writes NOTHING to the corpus, ever.

``respond`` remembers a ``decline`` forever and a ``later`` for ``_SNOOZE_DAYS`` — in
TELEMETRY (``interview-state.json``), never the corpus — so nothing re-asks. Zero LLM
on the default path: templates over existing signals (the optional LLM lane, if ever
wanted, rides the existing hippo-llm.json opt-in and is NOT part of this module).

Annoyance is the failure mode of every elicitation system; the hard cap, the decline
memory, and the empty-norm posture (zero questions when the queues are empty) are the
design. Crash class (INV-3): ``_write_state`` is detected — a failed decline write is
returned as an error, never silently pretended recorded.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

QUESTION_CAP = 3
STATE_NAME = "interview-state.json"
_SNOOZE_DAYS = 7
_QID_PREFIXES = ("abstain:", "contra:", "draft:")
_OUTCOMES = ("decline", "later")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _state_path(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, STATE_NAME)


def _read_state(telemetry_dir: str) -> Dict[str, dict]:
    """``{"declined": {qid: iso}, "snoozed": {qid: until_iso}}`` — fresh empty maps on
    absence/corruption, never a raise."""
    state = {"declined": {}, "snoozed": {}}
    try:
        with open(_state_path(telemetry_dir), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict):
            for key in ("declined", "snoozed"):
                val = doc.get(key)
                if isinstance(val, dict):
                    state[key] = {str(k): str(v) for k, v in val.items()}
    except Exception:
        pass
    return state


def _write_state(telemetry_dir: str, state: dict) -> Optional[str]:
    """Persist the decline/snooze memory (atomic). Returns an error string on failure —
    the caller REPORTS it (INV-3 crash class: detected), never pretends the decline was
    recorded."""
    try:
        from .provenance import ensure_self_ignoring_dir

        ensure_self_ignoring_dir(telemetry_dir)
        from .atomic import write_json_atomic

        write_json_atomic(_state_path(telemetry_dir), state)
        return None
    except Exception as exc:
        return f"decline/snooze write failed: {type(exc).__name__}: {exc}"


def _suppressed(state: dict, qid: str) -> bool:
    """Declined forever; snoozed while the stamp is in the future (an unparseable stamp
    reads as expired — re-asking beats silently never asking again)."""
    if qid in state.get("declined", {}):
        return True
    until = state.get("snoozed", {}).get(qid)
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > _now()
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Gathering — each source individually guarded; templates cite evidence verbatim
# --------------------------------------------------------------------------- #
def _abstention_questions(telemetry_dir: str) -> List[dict]:
    try:
        from .telemetry import abstention_backlog

        out = []
        for c in abstention_backlog(telemetry_dir):
            terms = " ".join(sorted(c.get("terms") or []))
            qid = "abstain:" + hashlib.sha1(terms.encode("utf-8")).hexdigest()[:12]
            count = int(c.get("count") or 0)
            sample = str(c.get("sample_query") or "").strip()
            out.append(
                {
                    "qid": qid,
                    "kind": "abstention",
                    "question": (
                        f'recall abstained {count}× on "{sample}" — the corpus could not '
                        "answer it. Want to write that memory down?"
                    ),
                    "route": (
                        "accept: author it from real knowledge (never fabricate), via the "
                        "new_memory tool with check:true first — /hippo:new in a terminal"
                    ),
                    "evidence": {"count": count, "sample_query": sample},
                }
            )
        return out
    except Exception:
        return []


def _contradiction_questions(
    memory_dir: str, repo_root: Optional[str], telemetry_dir: str
) -> List[dict]:
    try:
        from .resolve_view import unresolved_contradictions

        out = []
        for item in unresolved_contradictions(
            memory_dir, repo_root=repo_root, telemetry_dir=telemetry_dir
        ):
            a, b = item["pair"]
            qid = "contra:" + "|".join(sorted((str(a), str(b))))
            out.append(
                {
                    "qid": qid,
                    "kind": "contradiction",
                    "question": (
                        f"'{a}' and '{b}' declare contradicts and no verdict is recorded — "
                        "resolve the pair now?"
                    ),
                    "route": (
                        "accept: /hippo:resolve (terminal) or the resolve tool "
                        "(action='inbox', then ONE action='verdict' per pair)"
                    ),
                    "evidence": {"pair": [a, b]},
                }
            )
        return out
    except Exception:
        return []


def _draft_questions(memory_dir: str, telemetry_dir: str) -> List[dict]:
    try:
        from . import dream_generate

        sweep = dream_generate.draft_sweep_state(memory_dir, telemetry_dir)
        out = []
        for info in list(sweep.get("expire") or []) + list(sweep.get("awaiting_archive") or []):
            stem = info.get("stem")
            if not stem:
                continue
            age = info.get("age")
            out.append(
                {
                    "qid": f"draft:{stem}",
                    "kind": "draft",
                    "question": (
                        f"generated draft '{stem}' is at its decay horizon "
                        f"({age} session(s) old, no graduation evidence) — verify it now, "
                        "or let it decay?"
                    ),
                    "route": (
                        "accept: verify the claim against reality, then reconsolidate "
                        "action='reverify' outcome='graduate'; letting it decay needs nothing"
                    ),
                    "evidence": {"stem": stem, "age": age},
                }
            )
        return out
    except Exception:
        return []


def gather_questions(
    memory_dir: str,
    *,
    repo_root: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    cap: int = QUESTION_CAP,
) -> List[dict]:
    """At most ``cap`` grounded questions — ``{qid, kind, question, route, evidence}``.

    Order: recurring abstentions (most-asked first — the flagship gap), then the
    contradiction inbox, then expiring drafts. Declined/snoozed questions never render.
    Read-only; never raises; ``[]`` is the designed norm.
    """
    try:
        from .telemetry import default_telemetry_dir

        td = telemetry_dir or default_telemetry_dir(memory_dir)
        state = _read_state(td)
        candidates = (
            _abstention_questions(td)
            + _contradiction_questions(memory_dir, repo_root, td)
            + _draft_questions(memory_dir, td)
        )
        return [q for q in candidates if not _suppressed(state, q["qid"])][: max(0, cap)]
    except Exception:
        return []


def respond(qid: str, outcome: str, *, telemetry_dir: Optional[str] = None) -> dict:
    """Record ONE human response to one question: ``decline`` (never re-ask) or
    ``later`` (snooze ``_SNOOZE_DAYS`` days). Telemetry-only — the corpus is untouched;
    an ACCEPTED answer is never recorded here at all (the agent routes it through the
    existing write verbs, and the underlying signal clears itself). Never raises."""
    try:
        if outcome not in _OUTCOMES:
            return {"ok": False, "error": f"outcome must be one of {'/'.join(_OUTCOMES)}"}
        if not isinstance(qid, str) or not qid.startswith(_QID_PREFIXES):
            return {"ok": False, "error": "unknown qid (expected abstain:/contra:/draft: …)"}
        if telemetry_dir is None:
            from .provenance import resolve_dirs
            from .telemetry import default_telemetry_dir

            telemetry_dir = default_telemetry_dir(resolve_dirs()[0])
        state = _read_state(telemetry_dir)
        now = _now()
        if outcome == "decline":
            state["declined"][qid] = now.isoformat()
            status = f"decline recorded — {qid} will never re-render"
        else:
            until = now + timedelta(days=_SNOOZE_DAYS)
            state["snoozed"][qid] = until.isoformat()
            status = f"snoozed — {qid} returns after {until.date().isoformat()}"
        err = _write_state(telemetry_dir, state)
        if err:
            return {"ok": False, "error": err}
        return {"ok": True, "status": status}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def render_questions(questions: List[dict]) -> str:
    """The human form the consolidate skill / MCP tool present. Empty-norm honest."""
    if not questions:
        return (
            "interview: no questions — the blind-spot backlog, contradiction inbox, and "
            "draft horizon are all clear (the designed norm)."
        )
    lines = [
        f"interview — {len(questions)} grounded question(s) this session (cap {QUESTION_CAP}; "
        "each cites its evidence; a decline is remembered, 'later' snoozes):"
    ]
    for i, q in enumerate(questions, 1):
        lines.append(f"{i}. [{q['kind']}] {q['question']}")
        lines.append(f"   {q['route']}")
        lines.append(
            f"   qid: {q['qid']} — decline/later via the interview tool action='respond'"
        )
    return "\n".join(lines)
