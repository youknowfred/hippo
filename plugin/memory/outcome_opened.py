"""OBS-10: opened-after-recall — was an injected memory's own file opened afterwards?

The recall hook ends every block with "read the file before relying on it". KPI-2
(``outcome.injection_precision``) asks a different question: was a file the memory CITES
touched later in the session. A memory can be useful while its cited code is never
touched, and a cited file can be touched for unrelated reasons, so that number is a proxy
twice removed. This module reads the direct signal the hook already asks for: the agent
opened the memory file itself, with the Read tool, after recall named it.

Three parts, all measurement only (nothing here reaches ranking):

- ``opened_memory`` runs at record time, inside the PostToolUse spawn. It recognises a
  Read of a memory file however the path was spelled: the corpus dir, a linked worktree's
  copy of it, the harness's native ``~/.claude/projects/<slug>/memory`` symlink (which
  resolves into the corpus but sits outside the repository, so the outcome row used to be
  dropped), the private tier, and the user tier.
- ``note_open`` folds each open into today's daily rollup, so the count outlives the
  outcome ledger's rotation.
- ``opened_after_recall`` is the join over the raw ledgers: of the (session, memory) pairs
  the hook injected, how many had that memory opened later in the same session, split by
  memory type. It also counts every memory open and how many followed a recall of that
  memory, which is the same ledgers read the other way: how often recall had already named
  what the agent went looking for.

What it cannot see: a memory read through the shell (``cat``, ``grep``) or returned by the
recall MCP tool. PostToolUse is wired to the file tools only. The report says so.

Siblings never import their façade: this module imports telemetry and provenance, never
``outcome`` (which imports it).
"""

from __future__ import annotations

import fnmatch
import os
from typing import Dict, Optional, Tuple

# Files that live in a memory dir without being memories.
_NOT_MEMORIES = frozenset({"MEMORY", "CONVENTIONS", "README"})

# The kinds a report row can carry besides a frontmatter ``type``.
KIND_DIGEST = "digest"  # a declared fold digest (``fold_digests`` globs)
KIND_GONE = "gone"  # injected then, no file now (archived, folded or renamed since)
KIND_UNTYPED = "untyped"


def _stem(path: str) -> Optional[str]:
    base = os.path.basename(path or "")
    if not base.endswith(".md") or base.startswith("."):
        return None
    stem = base[:-3]
    return stem if stem and stem not in _NOT_MEMORIES else None


def _real(path: str) -> str:
    try:
        return os.path.realpath(path)
    except Exception:
        return os.path.abspath(path)


def memory_rel(memory_dir: str) -> str:
    """The corpus dir as outcome rows spell it: its last two path components
    (``.claude/memory`` for every corpus that does not override the dir)."""
    md = os.path.abspath(memory_dir)
    return os.path.join(os.path.basename(os.path.dirname(md)), os.path.basename(md))


def opened_memory(raw_path: str, tree_rel: Optional[str], memory_dir: str) -> Optional[Tuple[str, str]]:
    """``(stem, tier)`` when ``raw_path`` is a memory file of this corpus or one of its
    tiers, else ``None``. ``tier`` is ``project``, ``private`` or ``user``.

    Symlinks are resolved, so the native-memory path counts. ``tree_rel`` is the touch's
    in-tree path (a worktree touch's repo-relative tail): a linked worktree carries its
    own checkout of the corpus, and a read of that copy is a read of the same memory.
    Direct children only; an ``archive/`` subdirectory is not the live corpus. Never raises.
    """
    try:
        stem = _stem(raw_path)
        if not stem or not memory_dir:
            return None
        parent = _real(os.path.dirname(os.path.abspath(raw_path)))
        if parent == _real(memory_dir):
            return stem, "project"
        if tree_rel and os.path.dirname(tree_rel) == memory_rel(memory_dir):
            return stem, "project"
        from .provenance import local_memory_dir, user_memory_dir

        if parent == _real(local_memory_dir(memory_dir)):
            return stem, "private"
        if parent == _real(user_memory_dir()):
            return stem, "user"
        return None
    except Exception:
        return None


def tier_path(stem: str, tier: str) -> str:
    """The ``path`` an outcome row carries for a memory outside the repository (the user
    tier). Angle brackets keep it from ever equalling a repo-relative cited path."""
    return f"<{tier}-tier>/{stem}.md"


def row_opened(row: dict, rel_dir: str) -> Optional[str]:
    """The memory stem an outcome row opened, or ``None``. Rows written by the Python path
    carry ``opened``; rows from the bash fast path and from before this field are read
    from their path. Read rows only: an Edit is upkeep, not use."""
    if row.get("tool") != "Read":
        return None
    stem = row.get("opened")
    if isinstance(stem, str) and stem:
        return stem
    path = row.get("tree_path") or row.get("path")
    if isinstance(path, str) and os.path.dirname(path) == rel_dir:
        return _stem(path)
    return None


def _hook_served(telemetry_dir: str):
    """Yield ``(session_id, ts, names)`` for every recall the prompt hook injected."""
    from .telemetry import read_episodes

    for e in read_episodes(telemetry_dir):
        names = [n for n in (e.get("recalled_names") or []) if isinstance(n, str) and n]
        ts = e.get("ts")
        if names and isinstance(ts, (int, float)):
            yield e.get("session_id"), float(ts), names


def served_before(telemetry_dir: str, session_id: Optional[str], stem: str) -> bool:
    """True when the hook injected ``stem`` earlier in ``session_id``. One pass over the
    episode buffer; called only when a memory file is opened, which is rare. Never raises."""
    try:
        if not session_id:
            return False
        return any(sid == session_id and stem in names for sid, _ts, names in _hook_served(telemetry_dir))
    except Exception:
        return False


def note_open(telemetry_dir: str, session_id: Optional[str], stem: str) -> bool:
    """Fold one memory open into today's rollup row. Fire-and-forget; never raises."""
    try:
        from .telemetry_rollup import record_open

        return record_open(telemetry_dir, after_recall=served_before(telemetry_dir, session_id, stem))
    except Exception:
        return False


def _may_have_rotated(path: str) -> bool:
    """A ledger rotates to its newest half once it passes the byte ceiling, so a file under
    roughly half the ceiling has never rotated and still holds its first row."""
    try:
        from .telemetry_store import _max_bytes

        return os.path.getsize(path) >= int(_max_bytes() * 0.45)
    except Exception:
        return False


def _kind_of(memory_dir: str, name: str, folds: list, cache: Dict[str, str]) -> str:
    if name in cache:
        return cache[name]
    kind = KIND_GONE
    try:
        from .provenance import local_memory_dir, user_memory_dir
        from .staleness import read_memory_type

        # Recall fuses the private and user tiers into the same block, so look there too.
        for d in (memory_dir, local_memory_dir(memory_dir), user_memory_dir()):
            path = os.path.join(d, f"{name}.md")
            if not os.path.isfile(path):
                continue
            if d == memory_dir and any(fnmatch.fnmatch(f"{name}.md", pat) for pat in folds):
                kind = KIND_DIGEST
            else:
                with open(path, "r", encoding="utf-8") as fh:
                    kind = read_memory_type(fh.read()) or KIND_UNTYPED
            break
    except Exception:
        kind = KIND_GONE
    cache[name] = kind
    return kind


def opened_after_recall(memory_dir: str, telemetry_dir: Optional[str] = None) -> dict:
    """The join. Returns::

        {"pairs", "opened", "rate", "sessions", "by_kind": {kind: {"pairs", "opened"}},
         "opens", "opens_after_recall", "window_days"}

    A pair is one (session, memory) the hook injected; it counts as opened when that
    memory's file was Read in the same session at or after its first injection. The two
    ledgers rotate independently, so only the window both cover is judged: once the outcome
    ledger has rotated, a pair injected before its first row could not have been seen
    being opened, and counting it as unopened would understate the rate. The same holds
    for an open that predates a rotated episode buffer. ``rate`` is ``None`` with no
    pairs. Never raises.
    """
    empty = {
        "pairs": 0, "opened": 0, "rate": None, "sessions": 0, "by_kind": {},
        "opens": 0, "opens_after_recall": 0, "window_days": 0.0,
    }
    try:
        from .provenance import read_fold_digests
        from .telemetry import default_telemetry_dir, read_outcomes
        from .telemetry_store import _episode_ledger_path, _outcome_ledger_path

        td = telemetry_dir or default_telemetry_dir(memory_dir)
        rel_dir = memory_rel(memory_dir)
        outcomes = [o for o in read_outcomes(td) if isinstance(o.get("ts"), (int, float))]
        served = list(_hook_served(td))
        if not outcomes or not served:
            return empty
        first_out = min(o["ts"] for o in outcomes)
        first_served = min(ts for _s, ts, _n in served)
        pairs_from = first_out if _may_have_rotated(_outcome_ledger_path(td)) else 0.0
        opens_from = first_served if _may_have_rotated(_episode_ledger_path(td)) else 0.0
        start = max(min(first_out, first_served), pairs_from, opens_from)
        end = max(max(o["ts"] for o in outcomes), max(ts for _s, ts, _n in served))
        injected: Dict[tuple, float] = {}  # (session, memory) -> first injection in the window
        for sid, ts, names in served:
            if ts < pairs_from:
                continue
            for name in names:
                key = (sid, name)
                if key not in injected or ts < injected[key]:
                    injected[key] = ts
        opens: Dict[object, list] = {}  # session -> [(ts, stem)]
        for o in outcomes:
            if o["ts"] < opens_from:
                continue
            stem = row_opened(o, rel_dir)
            if stem:
                opens.setdefault(o.get("session_id"), []).append((float(o["ts"]), stem))
        folds = read_fold_digests(memory_dir)
        cache: Dict[str, str] = {}
        by_kind: Dict[str, Dict[str, int]] = {}
        hits = 0
        for (sid, name), inject_ts in injected.items():
            row = by_kind.setdefault(_kind_of(memory_dir, name, folds, cache), {"pairs": 0, "opened": 0})
            row["pairs"] += 1
            if any(stem == name and ts >= inject_ts for ts, stem in opens.get(sid, [])):
                row["opened"] += 1
                hits += 1
        n_opens = sum(len(v) for v in opens.values())
        led = sum(
            1
            for sid, rows in opens.items()
            for ts, stem in rows
            if injected.get((sid, stem), float("inf")) <= ts
        )
        return {
            "pairs": len(injected),
            "opened": hits,
            "rate": (hits / len(injected)) if injected else None,
            "sessions": len({sid for sid, _ in injected}),
            "by_kind": by_kind,
            "opens": n_opens,
            "opens_after_recall": led,
            "window_days": round(max(0.0, end - start) / 86400.0, 1),
        }
    except Exception:
        return empty


def format_opened_report(memory_dir: str, telemetry_dir: Optional[str] = None) -> str:
    """The doctor line: the rate, its split by kind, and what it cannot see."""
    r = opened_after_recall(memory_dir, telemetry_dir)
    if not r["pairs"]:
        return (
            "opened after recall: no signal yet (needs a session where the recall hook "
            "injected memories and the outcome ledger was recording)."
        )
    kinds = sorted(r["by_kind"].items(), key=lambda kv: (-kv[1]["pairs"], kv[0]))
    split = ", ".join(f"{kind} {row['opened']}/{row['pairs']}" for kind, row in kinds)
    return (
        f"opened after recall: {r['opened']} of {r['pairs']} injected memories "
        f"({r['rate'] * 100:.2f}%) were opened later in the same session, over "
        f"{r['sessions']} session(s) and {r['window_days']} day(s) of ledger — by kind: {split}; "
        f"{r['opens']} memory file open(s) in all, {r['opens_after_recall']} after a recall "
        "had named that memory. Counts Read-tool opens only, not shell reads. Measurement only."
    )
