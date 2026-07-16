"""T16 JIT: point-of-action recall — the first-touch reminder lane + touch-grain evidence.

Every recall moment hippo has is prompt-shaped (SessionStart context, UserPromptSubmit
injection). The moment a feedback lesson matters most is the ACT — the first touch of the
file the lesson is about, possibly an hour after the injection fell out of the context
window. Procedural memory fires at the action; this module gives hippo that moment,
bounded and boring: no LLM, no rebuild, first-touch-only.

Two lanes share one derived file and one per-session state doc:

  JIT-1 (``observe_touch`` -> the reminder). SessionStart's ``_build_run_context`` — the
  same offline, trust-gated moment that writes ``stale.json`` — calls
  ``refresh_touch_cache``, persisting ``<index_dir>/touchmap.json``: a reverse index from
  each cited repo-relative path to the ``steer:pin``/feedback-type memories that cite it.
  The PostToolUse hook's existing single Python spawn (``memory.outcome --from-hook``)
  then calls ``observe_touch`` per file touch: ONE small derived-JSON read on the
  empty-norm path (most touches emit nothing, ever), and on the FIRST touch of a mapped
  file per session, one line — ``memory <name>: <description>`` — as hook
  additionalContext, never again that session. Restraint is the design (the 2026-07-16
  ratified default-on decision leans on it): at most ``MAX_LINES_PER_SESSION`` lines per
  session, ``MAX_LINE_CHARS`` per line, project/reference types never remind, floor-linked
  memories never remind (they are already always-loaded), a memory already surfaced this
  session (``recall_events``) never reminds (the model just saw it), and the whole lane
  dies on ``HIPPO_DISABLE_JIT`` — the kill switch that restores pre-T16 hook behavior
  byte-for-byte.

  JIT-2 (``observe_touch`` -> ``cited_by``). The same lookup sees the exact
  (memory, file, touch) coincidence session-grain joins can only approximate, so it hands
  the caller the citing memory names to record as OPTIONAL provenance on the outcome row
  (``telemetry.log_outcome(cited_by=...)``). Volume stays bounded: at most
  ``MAX_PROVENANCE_ROWS_PER_SESSION`` rows per session carry the field, at most
  ``MAX_CITED_PER_PATH`` names each. ``outcome.injection_hits(grain="touch")`` consumes
  it report-only; session grain stays the default (a sharper join can UNDER-count —
  touch grain is evidence-plus, never evidence-instead).

Hot-path contract (the measured acceptance criterion, pinned in tests/test_scale.py):
``observe_touch`` reads DERIVED files only — the touchmap, the per-session state doc,
and (on the rare emit path) the recall ledger and trust registry. Never the corpus,
never the index manifest, never a model, never a rebuild. A negative test pins the
zero-corpus-reads mechanism; the scale lane pins the per-touch budget.

SEC-1 parity: the reminder INJECTS corpus content, so the emit path re-checks corpus
trust (lazily — only when a line is about to fire). The cache is only ever WRITTEN by
the trusted SessionStart path, but trust can be revoked mid-stream; a revoked corpus
goes silent even with a stale cache on disk. The JIT-2 half keeps measuring (it writes
gitignored telemetry, injects nothing) — same posture as the outcome ledger itself.

Concurrency: per-session state is one small JSON, written atomically (COR-17 via
``atomic.write_json_atomic``) with last-writer-wins semantics. Two PostToolUse hooks
racing in one session can at worst over-emit within a touch or two — bounded by the
session caps, never a torn file. Crash classes (INV-3, tests/test_crash_faults.py):
``write_touch_cache`` is detected (returns False; SessionStart never assumes freshness);
``_write_state`` is intact (bookkeeping is silent by design — the reminder outranks it).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# The stated bounds (acceptance criteria carry these numbers; tests import them)
# --------------------------------------------------------------------------- #
TOUCH_CACHE_SCHEMA_VERSION = 1
_TOUCH_CACHE_NAME = "touchmap.json"
MAX_LINE_CHARS = 200  # hard cap per reminder line ("memory <name>: <description>")
MAX_LINES_PER_SESSION = 3  # the whole lane goes silent after this many lines
MAX_REMINDERS_PER_PATH = 8  # cache-side bound; emission is capped far lower anyway
MAX_CITED_PER_PATH = 16  # JIT-2: names recorded per outcome row
MAX_PROVENANCE_ROWS_PER_SESSION = 40  # JIT-2: rows per session carrying cited_by
MAX_STATE_FILES = 32  # per-session state docs kept before oldest-first pruning
_STATE_DIRNAME = "jit"


def jit_disabled() -> bool:
    """True when the T16 JIT lane is killed (``HIPPO_DISABLE_JIT``).

    The ratified owner decision (2026-07-16) ships the lane DEFAULT-ON with an env kill
    switch — same convention as ``build_index.dense_disabled``. Killed means byte-for-byte
    pre-T16 behavior: no reminder, no touch-grain provenance, no SessionStart cache write.
    """
    return os.environ.get("HIPPO_DISABLE_JIT", "").strip() not in ("", "0", "false", "False")


def touch_cache_path(index_dir: str) -> str:
    """``<index_dir>/touchmap.json`` — the one path the SessionStart writer and the
    PostToolUse reader must agree on (same standing as ``stale.json``: derived,
    rebuildable, gitignored)."""
    return os.path.join(index_dir, _TOUCH_CACHE_NAME)


def _flatten(text: str) -> str:
    """One display line: collapse all whitespace runs (including newlines) to single
    spaces. The reminder is contractually ONE bounded line; a multi-line description
    (a YAML folded scalar) must never smuggle extra lines into hook context."""
    return re.sub(r"\s+", " ", text or "").strip()


def _memory_type(fm) -> str:
    """The memory's ``type`` (``metadata:``-nested wins, top-level fallback — the read
    direction ``new_memory`` uses), lowercased; ``""`` when unreadable."""
    if not isinstance(fm, dict):
        return ""
    meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    val = meta.get("type") or fm.get("type")
    return val.strip().lower() if isinstance(val, str) else ""


# --------------------------------------------------------------------------- #
# The derived cache (SessionStart writes; the hook only ever reads)
# --------------------------------------------------------------------------- #
def build_touch_cache(memory_dir: str) -> dict:
    """One corpus frontmatter pass -> ``{"reminders": {path: [{name, description}]},
    "cited": {path: [names]}}``.

    ``reminders`` (JIT-1) carries ONLY ``steer:pin`` or feedback-type memories with
    cited_paths, minus floor-linked names (``lint_floor.floor_memory_names`` — the floor
    is already always-loaded context; reminding about it is pure duplication). Entries
    are pre-bounded: the stored description already fits the ``MAX_LINE_CHARS`` line so
    the hook never re-measures. Pins sort before feedback, then by name — deterministic
    emission order. ``cited`` (JIT-2) is the FULL reverse index, all types — evidence is
    not a nag, so it is not type-scoped. Runs at SessionStart, off the hot path (the
    same cost class as ``find_stale``'s own corpus pass). Never raises; empty maps on
    any failure.
    """
    reminders: Dict[str, List[tuple]] = {}
    cited: Dict[str, set] = {}
    try:
        from .build_index import _extract_steer, extract_description
        from .provenance import _iter_memory_files, parse_frontmatter
        from .staleness import read_provenance

        try:
            from .lint_floor import floor_memory_names

            floor = floor_memory_names(memory_dir)
        except Exception:
            floor = set()
        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            cited_paths = read_provenance(text)[0]
            if not cited_paths:
                continue
            name = os.path.splitext(os.path.basename(path))[0]
            for p in cited_paths:
                cited.setdefault(p, set()).add(name)
            fm = parse_frontmatter(text)
            steer = _extract_steer(fm if isinstance(fm, dict) else {})
            mtype = _memory_type(fm)
            if not (steer == "pin" or mtype == "feedback") or name in floor:
                continue
            head = f"memory {name}: "
            budget = max(0, MAX_LINE_CHARS - len(head))
            desc = _flatten(extract_description(text))
            if len(desc) > budget:
                desc = desc[: max(0, budget - 1)].rstrip() + "…"
            for p in cited_paths:
                reminders.setdefault(p, []).append((0 if steer == "pin" else 1, name, desc))
    except Exception:
        return {"reminders": {}, "cited": {}}
    return {
        "reminders": {
            p: [
                {"name": n, "description": d}
                for _rank, n, d in sorted(lst)[:MAX_REMINDERS_PER_PATH]
            ]
            for p, lst in reminders.items()
        },
        "cited": {p: sorted(names)[:MAX_CITED_PER_PATH] for p, names in cited.items()},
    }


def write_touch_cache(index_dir: str, cache: dict) -> bool:
    """Persist ``build_touch_cache``'s result to ``<index_dir>/touchmap.json``.

    Written on EVERY call, including empty maps — an honest ``{"reminders": {}}`` means
    "checked this session, nothing cites anything remind-worthy", never a skipped write
    (``write_stale_cache``'s same discipline). Atomic via ``atomic.write_json_atomic``
    (COR-17/COR-18: a reader mid-crash sees the old map or the new one, never a torn
    one). Never raises; False on any failure — the SessionStart caller must treat that
    as "cache not refreshed", never assume freshness (INV-3 crash class: detected).
    """
    try:
        os.makedirs(index_dir, exist_ok=True)
        payload = {
            "schema_version": TOUCH_CACHE_SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "reminders": (cache or {}).get("reminders") or {},
            "cited": (cache or {}).get("cited") or {},
        }
        from .atomic import write_json_atomic

        write_json_atomic(touch_cache_path(index_dir), payload)
        return True
    except Exception:
        return False


def refresh_touch_cache(memory_dir: str, index_dir: Optional[str] = None) -> bool:
    """Build + persist the touch cache (the SessionStart moment). Never raises."""
    try:
        if index_dir is None:
            from .build_index import default_index_dir

            index_dir = default_index_dir(memory_dir)
        return write_touch_cache(index_dir, build_touch_cache(memory_dir))
    except Exception:
        return False


def read_touch_cache(index_dir: str) -> Optional[dict]:
    """The reader half — ``{"reminders": {...}, "cited": {...}}``, or ``None`` when the
    cache is absent, corrupt, or schema-mismatched (all degrade to "lane silent", never
    an error, and NEVER a corpus fallback scan — the hook path stays derived-only)."""
    try:
        with open(touch_cache_path(index_dir), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict):
            return None
        if payload.get("schema_version") != TOUCH_CACHE_SCHEMA_VERSION:
            return None
        reminders = payload.get("reminders")
        cited = payload.get("cited")
        if not isinstance(reminders, dict) or not isinstance(cited, dict):
            return None
        return {"reminders": reminders, "cited": cited}
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Per-session state (first-touch bookkeeping + the session caps)
# --------------------------------------------------------------------------- #
def _state_dir(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, _STATE_DIRNAME)


def _state_path(telemetry_dir: str, session_id: Optional[str]) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id or "anon"))[:80] or "anon"
    return os.path.join(_state_dir(telemetry_dir), f"{safe}.json")


def _read_state(telemetry_dir: str, session_id: Optional[str]) -> dict:
    """This session's JIT state — fresh defaults on absence/corruption, never a raise."""
    state = {"files": [], "emitted": [], "lines": 0, "cited_rows": 0}
    try:
        with open(_state_path(telemetry_dir, session_id), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict):
            state["files"] = [str(f) for f in doc.get("files") or [] if f]
            state["emitted"] = [str(n) for n in doc.get("emitted") or [] if n]
            state["lines"] = max(0, int(doc.get("lines") or 0))
            state["cited_rows"] = max(0, int(doc.get("cited_rows") or 0))
    except Exception:
        pass
    return state


def _write_state(telemetry_dir: str, session_id: Optional[str], state: dict) -> None:
    """Persist the session state doc (atomic), then prune old sessions' docs.

    Silent by design (INV-3 crash class: intact): a lost write costs at most one
    repeated reminder later — the reminder already emitted outranks the bookkeeping.
    """
    try:
        sd = _state_dir(telemetry_dir)
        os.makedirs(sd, exist_ok=True)
        from .atomic import write_json_atomic

        write_json_atomic(_state_path(telemetry_dir, session_id), state)
        _prune_state_dir(sd)
    except Exception:
        pass


def _prune_state_dir(state_dir: str) -> None:
    """Keep the newest ``MAX_STATE_FILES`` session docs; delete the rest oldest-first.
    Opportunistic (runs only after a successful write) and never raising — sessions are
    short-lived, so the dir stays a bounded scratchpad, not an unbounded ledger."""
    try:
        entries = []
        with os.scandir(state_dir) as it:
            for e in it:
                if e.name.endswith(".json"):
                    try:
                        entries.append((e.stat().st_mtime_ns, e.path))
                    except OSError:
                        continue
        for _mtime, path in sorted(entries)[: max(0, len(entries) - MAX_STATE_FILES)]:
            try:
                os.remove(path)
            except OSError:
                pass
    except Exception:
        pass


def _recalled_names(telemetry_dir: str, session_id: Optional[str]) -> set:
    """Names already injected THIS session, from the recall ledger — the suppression
    source the acceptance criterion names: never duplicate an injection the model just
    saw. One bounded read (the ledger byte-rotates), paid only on the emit path."""
    out: set = set()
    try:
        from .telemetry import read_events

        for e in read_events(telemetry_dir):
            if e.get("session_id") == session_id:
                for n in e.get("names") or []:
                    if n:
                        out.add(n)
    except Exception:
        return out
    return out


def _corpus_trusted(memory_dir: str, repo_root: Optional[str]) -> bool:
    """SEC-1 parity for the emit path: is this corpus (still) trusted right now?

    The cache was written by a trusted SessionStart, but trust can be revoked
    mid-stream — the reminder injects corpus content, so it re-checks at fire time,
    exactly like recall's per-prompt gate. Fail-closed: a broken gate emits nothing.
    """
    try:
        from . import trust

        root = trust.gate_repo_root(memory_dir, repo_root)
        return True if root is None else trust.is_trusted(root)
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# The hook-path decision (one call per PostToolUse file touch)
# --------------------------------------------------------------------------- #
def observe_touch(
    rel_path: str,
    *,
    memory_dir: str,
    repo_root: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    index_dir: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Tuple[Optional[List[str]], Optional[str]]:
    """The whole T16 decision for ONE repo-relative file touch:
    ``(cited_by, additional_context)``.

    ``cited_by`` (JIT-2) — the memory names citing this path, for the caller to record
    on the outcome row; ``None`` on a map miss, past the per-session provenance cap, or
    with the lane killed. ``additional_context`` (JIT-1) — the bounded first-touch
    reminder line(s), or ``None`` (the empty norm: most touches, forever).

    Derived-cache reads only (the measured budget in tests/test_scale.py): the fast
    path is one JSON read + two dict lookups. State, the recall ledger, and the trust
    gate are touched only when this path is actually mapped. Never raises.
    """
    try:
        if not rel_path or jit_disabled():
            return (None, None)
        if index_dir is None:
            from .build_index import default_index_dir

            index_dir = default_index_dir(memory_dir)
        cache = read_touch_cache(index_dir)
        if not cache:
            return (None, None)
        candidates = cache["reminders"].get(rel_path) or []
        citing = cache["cited"].get(rel_path) or []
        if not candidates and not citing:
            return (None, None)  # the empty norm — no state read, no ledger read

        from .telemetry import current_session_id, default_telemetry_dir

        td = telemetry_dir or default_telemetry_dir(memory_dir)
        sid = session_id or current_session_id(td)
        state = _read_state(td, sid)
        dirty = False

        cited_by: Optional[List[str]] = None
        if citing and state["cited_rows"] < MAX_PROVENANCE_ROWS_PER_SESSION:
            cited_by = [str(n) for n in citing if n][:MAX_CITED_PER_PATH]
            if cited_by:
                state["cited_rows"] += 1
                dirty = True

        context: Optional[str] = None
        if (
            candidates
            and rel_path not in set(state["files"])
            and state["lines"] < MAX_LINES_PER_SESSION
        ):
            # Once per (file, session) — the decision itself is recorded, whatever it yields.
            state["files"].append(rel_path)
            dirty = True
            if _corpus_trusted(memory_dir, repo_root):
                emitted = set(state["emitted"])
                surfaced = None  # the recall-ledger read is paid at most once, lazily
                lines: List[str] = []
                for entry in candidates:
                    if state["lines"] >= MAX_LINES_PER_SESSION:
                        break
                    name = entry.get("name") if isinstance(entry, dict) else None
                    if not name or name in emitted:
                        continue
                    if surfaced is None:
                        surfaced = _recalled_names(td, sid)
                    emitted.add(name)
                    state["emitted"].append(name)
                    if name in surfaced:
                        continue  # the model just saw it — never duplicate an injection
                    desc = entry.get("description") or ""
                    lines.append(f"memory {name}: {desc}"[:MAX_LINE_CHARS])
                    state["lines"] += 1
                if lines:
                    context = "\n".join(lines)
        if dirty:
            _write_state(td, sid, state)
        return (cited_by, context)
    except Exception:
        return (None, None)
