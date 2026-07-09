"""Recall-event telemetry for the agent-memory hook (instrumentation tier).

An append-only, gitignored, LOCAL ledger of what the recall hook surfaced in the wild —
one JSON line per hook recall: timestamp, session id, the surfaced memory NAMES, the
backend that served them (``dense+bm25`` / ``dense`` / ``bm25`` / ``none``), latency, ``k``,
and a TRUNCATED query preview (privacy-conscious — never the full prompt).

Robustness contract (the UserPromptSubmit hook depends on it):
  - NEVER raises — every write is wrapped; a failure (unwritable dir, a race) degrades to a
    silent no-op, and the recall still returns its results.
  - It runs AFTER recall results are computed, so it can never delay or change a recall.
  - SIZE-BOUNDED — the ledger caps at a byte ceiling and rotates (keeps the recent tail), so
    it can never grow without bound.
  - No sensitive content — only memory names + backend + latency + a truncated query.

Markdown-in-git stays the single source of authority; this ledger is DERIVED, local,
gitignored, and append-only HISTORY (NOT rebuildable like the index — deleting it loses only
the history, nothing the corpus needs). It lives in its OWN sibling of the index
(``.claude/.memory-telemetry/``) precisely BECAUSE it is history, not a rebuildable cache.

``read_events`` is the read surface the Tier-2 soak/curation analyzer consumes.

LIF-4: beside the rotating ledgers sits ``usage_aggregates.json`` — a tiny per-memory
aggregate (first/last recalled ts, distinct-session count) updated on every
``log_recall_event`` append and NEVER rotated, so long-lived corpora keep their oldest
usage evidence after the ledger's byte-capped tail drops it. ``read_usage_aggregates``
is its read surface (soak/curation union it in; v0.5.0's RET-5 consumes it as a
ranking prior).
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Dict, Iterator, List, Optional

from .provenance import ensure_self_ignoring_dir

_TELEMETRY_DIRNAME = ".memory-telemetry"
_LEDGER_NAME = "recall_events.jsonl"
_EPISODE_LEDGER_NAME = "episode_buffer.jsonl"
_RECONSOLIDATION_LEDGER_NAME = "reconsolidation_events.jsonl"
_OUTCOME_LEDGER_NAME = "outcome_events.jsonl"  # SIG-4: PostToolUse read-signal (KPI-2)
_USAGE_AGGREGATES_NAME = "usage_aggregates.json"
_SESSION_NAME = "session"

# Tier 2: the only valid reconsolidation outcomes -- "fix" is a distinct outcome (content was
# wrong, then corrected) from "graduate"/"demote" (a verdict on the ORIGINALLY flagged
# content), see eval_recall.graduation_rate()'s docstring for why it's excluded from that ratio.
# LIF-1 adds "snooze": an explicit per-item ACK (defer, no verdict rendered) — recorded here so
# the worklist can stop re-nagging it for a bounded window; graduation_rate() ignores it (its
# counts dict only knows the three verdicts), so the accuracy ratio's denominator stays clean.
_RECONSOLIDATION_OUTCOMES = frozenset({"graduate", "fix", "demote", "snooze"})

# Privacy: store only a short prefix of the query, never the full prompt.
_QUERY_PREVIEW_CHARS = 80

_DEFAULT_MAX_BYTES = 2_000_000


def _max_bytes() -> int:
    """Byte ceiling before the ledger rotates. Env-overridable (tests use a tiny cap)."""
    try:
        return max(256, int(os.environ.get("HIPPO_TELEMETRY_MAX_BYTES") or _DEFAULT_MAX_BYTES))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_BYTES


# --------------------------------------------------------------------------- #
# Dir resolution (mirrors build_index.default_index_dir)
# --------------------------------------------------------------------------- #
def default_telemetry_dir(memory_dir: str) -> str:
    """``.claude/.memory-telemetry`` — a sibling of ``.claude/memory`` (its own gitignored dir).

    Mirrors ``build_index.default_index_dir`` so the ledger lands beside the index. It is a
    SEPARATE dir from the index because it is append-only history, not a rebuildable cache.
    ``HIPPO_TELEMETRY_DIR`` overrides (hermetic tests use this).
    """
    override = os.environ.get("HIPPO_TELEMETRY_DIR")
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(memory_dir)), _TELEMETRY_DIRNAME)


def _resolve_dir(telemetry_dir: Optional[str]) -> str:
    if telemetry_dir:
        return telemetry_dir
    # Lazy import: provenance is the package's dir oracle and never imports telemetry.
    from .provenance import resolve_dirs

    memory_dir, _ = resolve_dirs()
    return default_telemetry_dir(memory_dir)


def _ledger_path(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, _LEDGER_NAME)


def _episode_ledger_path(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, _EPISODE_LEDGER_NAME)


def _reconsolidation_ledger_path(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, _RECONSOLIDATION_LEDGER_NAME)


def _outcome_ledger_path(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, _OUTCOME_LEDGER_NAME)


def _usage_aggregates_path(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, _USAGE_AGGREGATES_NAME)


def _session_path(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, _SESSION_NAME)


# --------------------------------------------------------------------------- #
# Session token (persisted: SessionStart and UserPromptSubmit are separate processes)
#
# COR-6: when the harness hands us a concrete session_id (from the SessionStart /
# UserPromptSubmit hook payload), that id is used DIRECTLY as the telemetry key instead of
# the file-based uuid token below. The file (``<telemetry_dir>/session``) is a SHARED,
# mutable fallback — fine for a single interactive session with no harness id (tests, bare
# CLI invocations), but two concurrent harness sessions on the same project both writing to
# it would clobber each other's id. Passing an explicit ``session_id`` bypasses the file
# entirely: nothing is read or written to it, so concurrent sessions never collide.
# --------------------------------------------------------------------------- #
def mark_session(telemetry_dir: Optional[str] = None) -> Optional[str]:
    """Stamp a FRESH session id (rotates the token). Called once per SessionStart.

    Returns the new id, or None on failure. Never raises.
    """
    try:
        td = _resolve_dir(telemetry_dir)
        ensure_self_ignoring_dir(td)  # derived dir: mkdir + self-ignoring .gitignore (SEC-3)
        sid = uuid.uuid4().hex
        with open(_session_path(td), "w", encoding="utf-8") as fh:
            fh.write(sid)
        return sid
    except Exception:
        return None


def current_session_id(
    telemetry_dir: Optional[str] = None, *, session_id: Optional[str] = None
) -> Optional[str]:
    """Read the current session id, minting + persisting one if none exists.

    So recall events are grouped per Claude-Code session even if a recall fires before the
    SessionStart mark (the first read establishes the id; the next SessionStart rotates it).
    When ``session_id`` is given (a harness-provided id), it is returned DIRECTLY — the
    file-based token is neither read nor written, so concurrent sessions never share it.
    Never raises.
    """
    if session_id:
        return session_id
    try:
        td = _resolve_dir(telemetry_dir)
        sp = _session_path(td)
        if os.path.exists(sp):
            with open(sp, "r", encoding="utf-8") as fh:
                sid = fh.read().strip()
            if sid:
                return sid
        return mark_session(td)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Append (never raises, bounded, rotates)
# --------------------------------------------------------------------------- #
def _rotate_if_needed(path: str) -> None:
    """Keep the ledger under the byte ceiling by retaining only the most-recent tail.

    Keeps the last ``<= max_bytes // 2`` bytes, aligned to a line boundary (the partial
    leading line is dropped). Called AFTER the new line is appended, so the newest event is
    always retained. A failed rotation leaves the file as-is — it never breaks logging.

    Single-writer assumption: interactive SessionStart/UserPromptSubmit hooks are effectively
    serialized per session, so this read-modify-write is not cross-process locked. The
    ``os.replace`` swap keeps each rotation atomic (no structurally-corrupt file); a rare
    concurrent-writer race costs at most a dropped telemetry line, never a crash.
    """
    try:
        if os.path.getsize(path) <= _max_bytes():
            return
    except OSError:
        return
    try:
        target = max(256, _max_bytes() // 2)
        with open(path, "rb") as fh:
            data = fh.read()
        tail = data[-target:]
        nl = tail.find(b"\n")
        if nl != -1:
            tail = tail[nl + 1:]  # drop the partial leading line
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(tail)
        os.replace(tmp, path)
    except Exception:
        pass


def log_recall_event(
    results: List[dict],
    *,
    query: str,
    k: int,
    latency_ms: float,
    telemetry_dir: Optional[str] = None,
    session_id: Optional[str] = None,
) -> bool:
    """Append ONE recall event to the ledger. Fire-and-forget: NEVER raises.

    Records the surfaced memory names, the serving backend, latency, ``k``, a TRUNCATED
    query preview (never the full prompt), and — COR-8 — each result's TRUE penalized fused
    score plus its 1-based emission rank (``recall()`` now emits the real ranking signal
    instead of fabricated 1/rank noise; this ledger just persists it verbatim so threshold
    calibration and any future feedback loop, e.g. v0.5.0's RET-5 salience fusion, inherit the
    real number). ``scores``/``ranks`` are parallel arrays aligned to ``names`` — kept
    separate rather than nesting `{name, score, rank}` objects so the existing `names`-shaped
    consumers (the soak/curation analyzer) are untouched; a `results` entry missing a `score`
    (a caller-constructed dict predating this field) contributes ``None`` at that position
    rather than dropping the row, so the arrays never lose alignment with `names`.
    ``session_id``, when given (the harness-provided id), keys the event directly instead of
    the file-based token — see ``current_session_id``. Returns True on a successful append,
    else False (a write failure degrades silently — the caller's recall is unaffected).
    LIF-4: a successful append also folds the event into ``usage_aggregates.json`` (the
    rotation-surviving per-memory summary — best-effort, never affects the return value).
    """
    try:
        td = _resolve_dir(telemetry_dir)
        ensure_self_ignoring_dir(td)  # derived dir: mkdir + self-ignoring .gitignore (SEC-3)
        backend = (results[0].get("backend") if results else None) or "none"
        named = [r for r in results if r.get("name")]
        event = {
            "ts": round(time.time(), 3),
            "session_id": current_session_id(td, session_id=session_id),
            "names": [r.get("name") for r in named],
            "scores": [r.get("score") for r in named],
            "ranks": [r.get("rank") for r in named],
            "backend": backend,
            "latency_ms": round(float(latency_ms), 2),
            "k": int(k),
            "query_preview": (query or "")[:_QUERY_PREVIEW_CHARS],
        }
        path = _ledger_path(td)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        _rotate_if_needed(path)
        # LIF-4: fold this event into the rotation-surviving usage aggregates. Runs AFTER
        # the append and is itself never-raising, so an aggregate failure can neither lose
        # the ledger line nor flip this function's return value.
        _update_usage_aggregates(
            td, names=event["names"], session_id=event["session_id"], ts=event["ts"]
        )
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Read (for the Tier-2 soak / curation analyzer)
# --------------------------------------------------------------------------- #
def read_events(telemetry_dir: Optional[str] = None) -> Iterator[dict]:
    """Yield parsed recall events, skipping corrupt/partial lines. Read-only; never raises."""
    try:
        td = _resolve_dir(telemetry_dir)
        path = _ledger_path(td)
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except Exception:
        return


# --------------------------------------------------------------------------- #
# SIG-3: recall blind-spot mining — turn silent abstention into a curation backlog.
#
# RET-1 correctly injects nothing when nothing clears the floor, but that abstention was
# invisible: the corpus never learned what it keeps being ASKED and cannot answer. Every
# such event is already in the recall ledger as ``backend == "none"`` with a truncated
# query preview. This clusters the RECURRING ones into a legible backlog line. Ships the
# ``backend == "none"`` arm ONLY — sub-floor "near-miss" scores are never logged on an
# abstention (``log_recall_event`` records scores only for SURFACED named hits), so that
# sub-arm has no data. Read-only aggregation of the gitignored ledger; never raises.
#
# No time window: the ledger is a byte-bounded rotating buffer, so "recurring in the buffer"
# already means "recently", the analysis stays deterministic (no wall-clock input, so the
# doctor render is reproducible), and a captured blind spot self-clears — recall stops
# abstaining on it, so its cluster stops growing and rotates out.
# --------------------------------------------------------------------------- #
# Question words / articles / fillers stripped before clustering so "how do I X" and "what's
# the X" cluster on the CONTENT tokens (X), not the boilerplate. Intentionally small — a
# too-aggressive list would merge genuinely different questions.
_ABSTENTION_STOPWORDS = frozenset(
    {
        "the", "a", "an", "how", "do", "does", "did", "i", "we", "you", "to", "of", "in", "on",
        "for", "is", "are", "was", "were", "what", "whats", "why", "when", "where", "which",
        "who", "can", "could", "should", "would", "and", "or", "with", "my", "our", "this",
        "that", "it", "its", "be", "get", "got", "use", "using", "there", "here", "about",
    }
)
_ABSTENTION_JACCARD = 0.5   # content-token overlap for two abstained queries to share a cluster
_ABSTENTION_MIN_COUNT = 3   # a cluster is a "recurring" blind spot only at/above this many asks
_ABSTENTION_MAX_CLUSTERS = 5
_ABSTENTION_MAX_TERMS = 6


def _abstention_content_tokens(text: str) -> set:
    """Significant (non-stopword, length>=3) lowercased word tokens of a query preview."""
    toks = re.findall(r"[a-z0-9][a-z0-9_-]+", (text or "").lower())
    return {t for t in toks if len(t) >= 3 and t not in _ABSTENTION_STOPWORDS}


def abstention_backlog(
    telemetry_dir: Optional[str] = None,
    *,
    min_count: int = _ABSTENTION_MIN_COUNT,
    max_clusters: int = _ABSTENTION_MAX_CLUSTERS,
) -> List[dict]:
    """Recurring abstained-query clusters — the recall blind-spot backlog.

    Reads ``backend == "none"`` recall events, greedily clusters their query previews by
    content-token Jaccard overlap (>= ``_ABSTENTION_JACCARD``), and returns clusters asked at
    least ``min_count`` times, most-frequent first, as
    ``[{"count", "sample_query", "terms", "queries"}]``. One-off / diverse abstentions never
    reach ``min_count``, so they never surface. Read-only; never raises; ``[]`` on any failure.
    """
    try:
        td = _resolve_dir(telemetry_dir)
        clusters: List[dict] = []  # {"seed": set, "all": set, "count": int, "queries": [str]}
        for e in read_events(td):
            if e.get("backend") != "none":
                continue
            q = (e.get("query_preview") or "").strip()
            if not q:
                continue
            toks = _abstention_content_tokens(q)
            if not toks:
                continue
            best = None
            best_j = 0.0
            for c in clusters:
                union = len(toks | c["seed"])
                j = (len(toks & c["seed"]) / union) if union else 0.0
                if j > best_j:
                    best_j = j
                    best = c
            if best is not None and best_j >= _ABSTENTION_JACCARD:
                best["count"] += 1
                best["queries"].append(q)
                best["all"].update(toks)
            else:
                clusters.append({"seed": set(toks), "all": set(toks), "count": 1, "queries": [q]})
        recurring = [c for c in clusters if c["count"] >= min_count]
        # Most-asked first; tie-break by sample query for a deterministic (DOC-4) ordering.
        recurring.sort(key=lambda c: (-c["count"], c["queries"][0]))
        out: List[dict] = []
        for c in recurring[:max_clusters]:
            out.append(
                {
                    "count": c["count"],
                    "sample_query": c["queries"][0],
                    "terms": sorted(c["all"])[:_ABSTENTION_MAX_TERMS],
                    "queries": list(c["queries"]),
                }
            )
        return out
    except Exception:
        return []


# GRW-2: how many DISTINCT sessions two memories must co-surface in before the pair becomes
# an edge proposal. Deliberately HIGH so a sparse/noisy co-recall map proposes NOTHING —
# an empty result is the designed behavior on a young corpus, not a failure.
_CORECALL_MIN_SESSIONS = 3
_CORECALL_MAX_PAIRS = 20  # bounded output for the consolidate proposal turn


def co_recall_pairs(
    telemetry_dir: Optional[str] = None,
    *,
    min_sessions: int = _CORECALL_MIN_SESSIONS,
    exclude_names: Optional[set] = None,
) -> List[dict]:
    """Hebbian co-recall tally (GRW-2): memory pairs that co-surface across many sessions.

    GRA-3 links by write-time similarity, so it can never connect pairs that are semantically
    DISTANT but operationally inseparable (a bug and its unrelated-looking workaround). The
    episode buffer already records exactly that signal — which names surfaced together — and
    nothing read it. This tallies it: per session, the recalled names are UNIONED FIRST (a
    chatty single session counts ONCE, structurally), every unordered pair in that union is
    credited one distinct session, and only pairs reaching ``min_sessions`` return, as
    ``[{"pair": [a, b], "sessions": n}]`` (pair sorted, list most-sessions-first, capped at
    ``_CORECALL_MAX_PAIRS``). Below threshold → ``[]`` — the sparse map STAYS empty rather
    than proposing spurious edges. ``exclude_names`` drops names before pairing (pass
    ``lint_floor.floor_memory_names`` so always-recalled floor memories can't dominate every
    pair). Read-only over the gitignored buffer; a TALLY, never a writer — the consumer
    (the consolidate skill) proposes each edge per-item, agent-gated. Never raises.
    """
    try:
        excluded = exclude_names or set()
        by_session: Dict[str, set] = {}
        for e in read_episodes(telemetry_dir):
            sid = e.get("session_id") or ""
            names = {n for n in (e.get("recalled_names") or []) if n and n not in excluded}
            if not names:
                continue
            by_session.setdefault(str(sid), set()).update(names)
        counts: Dict[frozenset, int] = {}
        for names in by_session.values():
            ordered = sorted(names)
            for i, a in enumerate(ordered):
                for b in ordered[i + 1 :]:
                    key = frozenset((a, b))
                    counts[key] = counts.get(key, 0) + 1
        out = [
            {"pair": sorted(pair), "sessions": n}
            for pair, n in counts.items()
            if n >= min_sessions
        ]
        out.sort(key=lambda p: (-p["sessions"], p["pair"]))
        return out[:_CORECALL_MAX_PAIRS]
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# LIF-4: rotation-surviving usage aggregates — a tiny JSON sidecar of the recall ledger.
#
# The ledger is a byte-capped rotating buffer, so a long-lived corpus loses its OLDEST
# usage evidence first and genuinely-used memories drift toward "never recalled" in the
# soak/curation/archive analyzers. This file keeps the per-memory summary those analyzers
# actually need — first-recalled ts, last-recalled ts, distinct-session count — updated on
# every ``log_recall_event`` append and NEVER rotated (its size is bounded by corpus size,
# ~100 bytes per ever-recalled memory, not by history length).
#
# Distinct-session counting is APPROXIMATE by design: each record keeps only the LAST
# session id it counted, and increments when a different id shows up. Consecutive events
# from one session count once; two sessions interleaving their prompts on the same project
# can each be counted more than once. The full-precision alternative (a session-id set per
# name) would grow without bound — the opposite of this file's contract.
#
# Same robustness contract as the ledgers: NEVER raises (corrupt/missing file -> start
# fresh), fire-and-forget, no sensitive content (memory names + timestamps + one session
# id per record). Writes are atomic (tmp + os.replace, pid-suffixed so concurrent writers
# never share a tmp path) under the same single-writer assumption as ``_rotate_if_needed``
# — a rare concurrent-writer race loses at most one increment, never the file.
# --------------------------------------------------------------------------- #
_AGGREGATES_VERSION = 1


def _empty_aggregates() -> dict:
    return {
        "version": _AGGREGATES_VERSION,
        "sessions": {"count": 0, "first_ts": None, "last_session_id": None},
        "memories": {},
    }


def _num(v) -> Optional[float]:
    """``v`` as a float when it is a real number (bool excluded), else None."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _load_usage_aggregates(telemetry_dir: str) -> dict:
    """Parse ``usage_aggregates.json`` into the canonical shape. Corrupt/missing -> fresh.

    Field-level tolerant: a wrong-typed ``sessions`` block or non-dict memory record is
    replaced with a fresh value rather than raising (the never-raise discipline) or
    poisoning the rest of the file.
    """
    fresh = _empty_aggregates()
    try:
        with open(_usage_aggregates_path(telemetry_dir), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return fresh
    if not isinstance(data, dict):
        return fresh
    sess = data.get("sessions")
    if isinstance(sess, dict):
        count = sess.get("count")
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            fresh["sessions"]["count"] = count
        fresh["sessions"]["first_ts"] = _num(sess.get("first_ts"))
        lsid = sess.get("last_session_id")
        if isinstance(lsid, str) and lsid:
            fresh["sessions"]["last_session_id"] = lsid
    mems = data.get("memories")
    if isinstance(mems, dict):
        fresh["memories"] = {
            name: rec for name, rec in mems.items() if isinstance(name, str) and isinstance(rec, dict)
        }
    return fresh


def _update_usage_aggregates(
    telemetry_dir: str, *, names: List[str], session_id: Optional[str], ts: float
) -> None:
    """Fold ONE recall event into the aggregates. Fire-and-forget: NEVER raises.

    The GLOBAL ``sessions`` record advances on every event (an empty recall still counts
    as a session for the soak gate, mirroring ``soak.soak_status``); per-memory records
    advance only for the recalled ``names``. Events without a session id still stamp
    first/last timestamps but cannot advance a distinct-session count. A malformed
    per-name record self-heals to a fresh one. The write is atomic (tmp + os.replace);
    on any failure the previous file is left intact and the caller is unaffected —
    ``_rotate_if_needed`` never sees, and can never truncate, this file.
    """
    try:
        agg = _load_usage_aggregates(telemetry_dir)
        sess = agg["sessions"]
        if sess["first_ts"] is None:
            sess["first_ts"] = ts
        if session_id and session_id != sess["last_session_id"]:
            sess["count"] += 1
            sess["last_session_id"] = session_id
        for name in names or []:
            if not name or not isinstance(name, str):
                continue
            rec = agg["memories"].get(name)
            if not isinstance(rec, dict):
                rec = {}
            first_ts = _num(rec.get("first_ts"))
            count = rec.get("sessions")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                count = 0
            last_sid = rec.get("last_session_id")
            if not isinstance(last_sid, str) or not last_sid:
                last_sid = None
            if session_id and session_id != last_sid:
                count += 1
                last_sid = session_id
            agg["memories"][name] = {
                "first_ts": first_ts if first_ts is not None else ts,
                "last_ts": ts,
                "sessions": count,
                "last_session_id": last_sid,
            }
        path = _usage_aggregates_path(telemetry_dir)
        tmp = f"{path}.tmp.{os.getpid()}"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(agg, ensure_ascii=False, separators=(",", ":")))
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except Exception:
        pass


def read_usage_aggregates(telemetry_dir: Optional[str] = None) -> dict:
    """The cheap read surface over the rotation-surviving usage aggregates.

    Always returns the canonical shape (missing/corrupt file -> the empty shape, never an
    error): ``{"version", "sessions": {"count", "first_ts", "last_session_id"},
    "memories": {name: {"first_ts", "last_ts", "sessions", "last_session_id"}}}``.
    ``sessions.count`` / per-record ``sessions`` are DISTINCT-session counts (approximate —
    see the section comment above); ``first_ts``/``last_ts`` are recall-event timestamps;
    ``sessions.first_ts`` is the start of the whole observation span. ``last_session_id``
    keys are counter bookkeeping, not signal. Consumers: soak/curation/archive union this
    with the rotating ledger; RET-5 reads it as a ranking prior. Read-only; never raises.
    """
    try:
        td = _resolve_dir(telemetry_dir)
        return _load_usage_aggregates(td)
    except Exception:
        return _empty_aggregates()


# --------------------------------------------------------------------------- #
# TEA-5: committed per-user usage summary — ``.claude/memory/.usage/<user>.json``.
# The rotating recall ledger and the ``usage_aggregates.json`` above are BOTH clone-local
# and gitignored, so "never recalled" means "never in THIS clone" — a memory a teammate hits
# daily reads as archive-cold on your machine. This tier is the opt-in fix: an append-only,
# COMMITTED (NOT self-ignored — the whole point is that teammates union it) per-user summary
# under the corpus tree, tiny and merge-friendly (memory names + counts + timestamps only, NO
# session ids — those are bookkeeping and would leak into git). Curation UNIONS every user's
# summary before judging coldness. It is NOT indexed/recalled/floor-scanned: ``.usage`` is a
# subdir, and ``_iter_memory_files`` only yields ``*.md`` files (never recurses), so it is
# skipped exactly like ``archive/``.
# --------------------------------------------------------------------------- #
_USAGE_DIRNAME = ".usage"
_COMMITTED_USAGE_VERSION = 1


def committed_usage_dir(memory_dir: str) -> str:
    """``<memory_dir>/.usage`` — the committed per-user usage summaries' one canonical home."""
    return os.path.join(memory_dir, _USAGE_DIRNAME)


def read_committed_usage(memory_dir: Optional[str]) -> dict:
    """Union of EVERY committed per-user summary under ``<memory_dir>/.usage/*.json`` (TEA-5).

    Returns ``{"memories": set(names), "sessions": int}`` — the set of memory stems ANY
    teammate has recalled, and the summed distinct-session count across users. Missing dir or a
    corrupt file contributes nothing; never raises. This is what ``soak.curation_report`` unions
    into its ``recalled`` set (and ``soak_status`` into its distinct count) so a teammate's daily
    hit is never miscounted as clone-local dead weight."""
    memories: set = set()
    sessions = 0
    try:
        if not memory_dir:
            return {"memories": memories, "sessions": 0}
        usage_dir = committed_usage_dir(memory_dir)
        if not os.path.isdir(usage_dir):
            return {"memories": memories, "sessions": 0}
        for fname in sorted(os.listdir(usage_dir)):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(usage_dir, fname), "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            mems = data.get("memories")
            if isinstance(mems, dict):
                memories |= {k for k in mems if isinstance(k, str)}
            sess = data.get("sessions")
            if isinstance(sess, dict):
                c = sess.get("count")
                if isinstance(c, int) and not isinstance(c, bool) and c > 0:
                    sessions += c
    except Exception:
        pass
    return {"memories": memories, "sessions": sessions}


def write_user_usage_summary(
    memory_dir: str, user_slug: str, telemetry_dir: Optional[str] = None
) -> Optional[str]:
    """Fold THIS clone's rotation-surviving aggregates into the COMMITTED per-user summary
    ``<memory_dir>/.usage/<user_slug>.json`` (TEA-5). Returns the path written, or None on
    failure.

    Append-only union — a re-run never loses ground: ``max`` on session/per-memory counts,
    ``min`` on ``first_ts``, ``max`` on ``last_ts``. Session ids are deliberately NOT written
    (bookkeeping, and they would leak into git). The file is COMMITTED, so — unlike the
    telemetry aggregates — it is NEVER self-ignored. Agent/user-gated (never on the hot path).
    Atomic write (tmp + os.replace, pid-suffixed). Never raises."""
    try:
        if not memory_dir or not user_slug:
            return None
        agg = read_usage_aggregates(telemetry_dir)
        usage_dir = committed_usage_dir(memory_dir)
        os.makedirs(usage_dir, exist_ok=True)
        path = os.path.join(usage_dir, f"{user_slug}.json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                prior = json.load(fh)
            if not isinstance(prior, dict):
                prior = {}
        except Exception:
            prior = {}
        prior_mems = prior.get("memories") if isinstance(prior.get("memories"), dict) else {}
        prior_sess = prior.get("sessions") if isinstance(prior.get("sessions"), dict) else {}

        def _mx(a, b):
            a = a if isinstance(a, (int, float)) and not isinstance(a, bool) else None
            b = b if isinstance(b, (int, float)) and not isinstance(b, bool) else None
            vals = [v for v in (a, b) if v is not None]
            return max(vals) if vals else None

        def _mn(a, b):
            a = a if isinstance(a, (int, float)) and not isinstance(a, bool) else None
            b = b if isinstance(b, (int, float)) and not isinstance(b, bool) else None
            vals = [v for v in (a, b) if v is not None]
            return min(vals) if vals else None

        merged_mems: dict = {k: v for k, v in prior_mems.items() if isinstance(v, dict)}
        for name, rec in agg.get("memories", {}).items():
            if not isinstance(name, str) or not isinstance(rec, dict):
                continue
            old = merged_mems.get(name) or {}
            merged_mems[name] = {
                "first_ts": _mn(old.get("first_ts"), rec.get("first_ts")),
                "last_ts": _mx(old.get("last_ts"), rec.get("last_ts")),
                "sessions": int(_mx(old.get("sessions"), rec.get("sessions")) or 0),
            }
        agg_sess = agg.get("sessions", {})
        out = {
            "version": _COMMITTED_USAGE_VERSION,
            "user": user_slug,
            "sessions": {
                "count": int(_mx(prior_sess.get("count"), agg_sess.get("count")) or 0),
                "first_ts": _mn(prior_sess.get("first_ts"), agg_sess.get("first_ts")),
                "last_ts": _mx(prior_sess.get("last_ts"), agg_sess.get("last_ts")),
            },
            "memories": merged_mems,
        }
        tmp = f"{path}.tmp.{os.getpid()}"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return None
        return path
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Episode buffer (instrumentation tier) — append-only, DISTINCT from the recall
# ledger above. The recall ledger records memory NAMES surfaced per query; the episode
# buffer additionally pins the repo HEAD commit at recall time, so a future (separately
# roadmapped, NOT shipped here) autonomous-capture pass has a watermark to diff
# ``git log <head_commit>..HEAD`` against. It must start soaking now even though nothing
# reads it yet, since it cannot be backfilled retroactively.
#
# Same robustness contract as the recall ledger above: NEVER raises, fire-and-forget,
# size-bounded (reuses ``_rotate_if_needed``), no sensitive content (a truncated query
# preview only, same ``_QUERY_PREVIEW_CHARS`` budget — never the full prompt).
# --------------------------------------------------------------------------- #
def log_episode(
    recalled_names: List[str],
    *,
    query: str,
    repo_root: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    session_id: Optional[str] = None,
) -> bool:
    """Append ONE episode to the gitignored ``episode_buffer.jsonl``. Fire-and-forget.

    Records the recalled memory NAMES (not content — the buffer has nothing else to
    "replay"), a TRUNCATED query preview, the current session id, and the repo's HEAD
    commit at logging time (``None`` when it cannot be determined — e.g. not a git repo —
    never raises on that failure). ``session_id``, when given (the harness-provided id),
    keys the event directly instead of the file-based token — see ``current_session_id``.
    Returns True on a successful append, else False (a write failure degrades silently,
    mirroring ``log_recall_event``).
    """
    try:
        td = _resolve_dir(telemetry_dir)
        ensure_self_ignoring_dir(td)  # derived dir: mkdir + self-ignoring .gitignore (SEC-3)
        head_commit = None
        if repo_root:
            try:
                from .provenance import run_git

                head_commit = run_git(["rev-parse", "HEAD"], repo_root).strip() or None
            except Exception:
                head_commit = None
        event = {
            "ts": round(time.time(), 3),
            "session_id": current_session_id(td, session_id=session_id),
            "query_preview": (query or "")[:_QUERY_PREVIEW_CHARS],
            "recalled_names": [n for n in (recalled_names or []) if n],
            "head_commit": head_commit,
        }
        path = _episode_ledger_path(td)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        _rotate_if_needed(path)
        return True
    except Exception:
        return False


def read_episodes(telemetry_dir: Optional[str] = None) -> Iterator[dict]:
    """Yield parsed episode-buffer entries, skipping corrupt/partial lines. Never raises."""
    try:
        td = _resolve_dir(telemetry_dir)
        path = _episode_ledger_path(td)
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except Exception:
        return


# --------------------------------------------------------------------------- #
# SIG-4: outcome ledger (KPI-2 read-signal). A PostToolUse hook appends one event per
# file-touching tool call — {ts, session_id, tool, path} (path repo-relative). The KPI-2
# injection-precision proxy (see ``memory.outcome``) later JOINS this against the episode
# buffer's recalled_names + the corpus's cited_paths, OFF the hot path — the hook writes the
# raw signal only. Same contract as the other ledgers: NEVER raises, fire-and-forget,
# byte-bounded (``_rotate_if_needed``), gitignored. MEASUREMENT ONLY — nothing here or in the
# proxy influences ranking (that is gated on SIG-5, the salience keystone).
# --------------------------------------------------------------------------- #
def log_outcome(
    tool: str,
    path: str,
    *,
    session_id: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
) -> bool:
    """Append ONE file-touch outcome to ``outcome_events.jsonl``. Fire-and-forget; never raises."""
    try:
        td = _resolve_dir(telemetry_dir)
        ensure_self_ignoring_dir(td)  # derived dir: mkdir + self-ignoring .gitignore (SEC-3)
        event = {
            "ts": round(time.time(), 3),
            "session_id": current_session_id(td, session_id=session_id),
            "tool": tool,
            "path": path,
        }
        p = _outcome_ledger_path(td)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        _rotate_if_needed(p)
        return True
    except Exception:
        return False


def read_outcomes(telemetry_dir: Optional[str] = None) -> Iterator[dict]:
    """Yield parsed outcome-ledger entries, skipping corrupt/partial lines. Never raises."""
    try:
        td = _resolve_dir(telemetry_dir)
        path = _outcome_ledger_path(td)
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except Exception:
        return


# --------------------------------------------------------------------------- #
# Reconsolidation outcomes (immunize tier) — a THIRD, distinct ledger. Logs the verdict each
# time the memory-master agent re-grounds a recall-flagged-stale memory (graduate / fix /
# demote), feeding eval_recall.graduation_rate() (the accuracy axis of the scorecard), plus
# LIF-1's "snooze" acks (explicit per-item deferrals the worklist reads back to stop
# re-nagging). This module only LOGS the outcome; the per-item judgment + the actual
# reverify/fix/invalidate/archive action live in reconsolidate.py / the memory-master
# agent, never here.
# --------------------------------------------------------------------------- #
def record_reconsolidation_outcome(
    name: str,
    outcome: str,
    *,
    telemetry_dir: Optional[str] = None,
    invalidated: Optional[bool] = None,
) -> bool:
    """Append ONE reconsolidation outcome to the gitignored ``reconsolidation_events.jsonl``.

    ``outcome`` must be one of ``{"graduate", "fix", "demote", "snooze"}`` — an invalid
    outcome is a silent no-op (returns ``False``) rather than corrupting
    ``graduation_rate()``'s denominator with garbage (``snooze`` — LIF-1's per-item ack —
    is valid here but ignored by that ratio: an explicit deferral is not a verdict).
    ``invalidated``, when not ``None``, is stamped onto the event — LIF-1's demote chain
    passes it so the ledger is an AUDIT TRAIL of whether the verdict also closed the
    memory's validity window (``staleness.set_invalid_after``), not just that it was
    rendered. Fire-and-forget; NEVER raises; size-bounded (reuses ``_rotate_if_needed``);
    no sensitive content (only the memory name + the outcome).
    """
    if outcome not in _RECONSOLIDATION_OUTCOMES:
        return False
    try:
        td = _resolve_dir(telemetry_dir)
        ensure_self_ignoring_dir(td)  # derived dir: mkdir + self-ignoring .gitignore (SEC-3)
        event = {"ts": round(time.time(), 3), "name": name, "outcome": outcome}
        if invalidated is not None:
            event["invalidated"] = bool(invalidated)
        path = _reconsolidation_ledger_path(td)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        _rotate_if_needed(path)
        return True
    except Exception:
        return False


def read_reconsolidation_events(telemetry_dir: Optional[str] = None) -> Iterator[dict]:
    """Yield parsed reconsolidation-outcome events, skipping corrupt/partial lines. Never raises."""
    try:
        td = _resolve_dir(telemetry_dir)
        path = _reconsolidation_ledger_path(td)
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except Exception:
        return
