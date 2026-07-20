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
import time
from typing import Dict, Iterator, List, Optional

from .provenance import ensure_self_ignoring_dir

# --------------------------------------------------------------------------- #
# ED5R-3 split (pure code motion): the ledger substrate (dir/paths/session/
# rotation + the two sibling-needed read iterators) lives in telemetry_store;
# the SIG-3/GRW-2 mining lives in telemetry_mining; the LIF-4/TEA-5 usage
# aggregates live in telemetry_usage. Every moved name is re-imported HERE so
# memory.telemetry.<name> keeps resolving and stays patchable for the façade's
# own call sites. Siblings never import this façade.
# --------------------------------------------------------------------------- #
from .telemetry_store import (  # noqa: F401
    _DEFAULT_MAX_BYTES,
    _EPISODE_LEDGER_NAME,
    _LEDGER_NAME,
    _OUTCOME_LEDGER_NAME,
    _RECONSOLIDATION_LEDGER_NAME,
    _SESSION_NAME,
    _TELEMETRY_DIRNAME,
    _THREAT_LEDGER_NAME,
    _USAGE_AGGREGATES_NAME,
    _episode_ledger_path,
    _ledger_path,
    _max_bytes,
    _outcome_ledger_path,
    _reconsolidation_ledger_path,
    _resolve_dir,
    _rotate_if_needed,
    _session_path,
    _threat_ledger_path,
    _usage_aggregates_path,
    current_session_id,
    default_telemetry_dir,
    mark_session,
    read_episodes,
    read_events,
)
from .telemetry_mining import (  # noqa: F401
    _ABSTENTION_JACCARD,
    _ABSTENTION_MAX_CLUSTERS,
    _ABSTENTION_MAX_TERMS,
    _ABSTENTION_MIN_COUNT,
    _ABSTENTION_STOPWORDS,
    _CORECALL_LIFT_FLOOR,
    _CORECALL_MAX_PAIRS,
    _CORECALL_MIN_SESSIONS,
    _abstention_content_tokens,
    abstention_backlog,
    co_recall_pairs,
)
from .telemetry_usage import (  # noqa: F401
    _AGGREGATES_VERSION,
    _COMMITTED_USAGE_VERSION,
    _USAGE_DIRNAME,
    _empty_aggregates,
    _load_usage_aggregates,
    _num,
    _update_usage_aggregates,
    committed_usage_dir,
    read_committed_usage,
    read_usage_aggregates,
    write_user_usage_summary,
)


# Tier 2: the only valid reconsolidation outcomes -- "fix" is a distinct outcome (content was
# wrong, then corrected) from "graduate"/"demote" (a verdict on the ORIGINALLY flagged
# content), see eval_recall.graduation_rate()'s docstring for why it's excluded from that ratio.
# LIF-1 adds "snooze": an explicit per-item ACK (defer, no verdict rendered) — recorded here so
# the worklist can stop re-nagging it for a bounded window; graduation_rate() ignores it (its
# counts dict only knows the three verdicts), so the accuracy ratio's denominator stays clean.
_RECONSOLIDATION_OUTCOMES = frozenset({"graduate", "fix", "demote", "snooze"})

# Privacy: store only a short prefix of the query, never the full prompt.
_QUERY_PREVIEW_CHARS = 80

# --------------------------------------------------------------------------- #
# MEA-4: producer-version stamps. The live-hook-lag class (a stale installed hook
# writing rows for N releases) bit twice in one week and both diagnoses were hand
# forensics — no row said which plugin version minted it. Every NEW row in the
# outcome / recall / reconsolidation ledgers carries an additive `v` (ED-4:
# unreadable manifest -> field omitted; historical rows never backfilled). The
# version is the RUNNING plugin's — CLAUDE_PLUGIN_ROOT (the launch-pinned installed
# cache under hooks) with the module's own root as the dev fallback, the
# check_plugin_version / DoctorContext.plugin_root resolution — NOT the repo tree
# being operated on; that distinction is the whole point. Provenance only: nothing
# anywhere branches on the stamp.
# --------------------------------------------------------------------------- #
_PRODUCER_VERSION_UNSET = object()
_producer_version_cache = _PRODUCER_VERSION_UNSET


def _producer_version() -> Optional[str]:
    """Version string of the RUNNING plugin, cached once per process; None (stamp
    omitted) when the manifest is unreadable. Never raises."""
    global _producer_version_cache
    if _producer_version_cache is not _PRODUCER_VERSION_UNSET:
        return _producer_version_cache
    v: Optional[str] = None
    try:
        root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        with open(os.path.join(root, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            raw = json.load(fh).get("version")
        v = raw if isinstance(raw, str) and raw.strip() else None
    except Exception:
        v = None
    _producer_version_cache = v
    return v



def log_recall_event(
    results: List[dict],
    *,
    query: str,
    k: int,
    latency_ms: float,
    telemetry_dir: Optional[str] = None,
    session_id: Optional[str] = None,
    drops: Optional[List[dict]] = None,
    near_miss: Optional[List[dict]] = None,
    dense_floor: Optional[float] = None,
    channel: Optional[str] = None,
    injected_chars: Optional[int] = None,
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

    MSR-4 (all three ADDITIVE — absent input emits no key, existing rows parse unchanged,
    no schema bump per ED-4):
      ``drops``       — the admission-walk cut records ``[{name, reason, score[, threshold]}]``
                        the caller collected via ``recall(..., drop_log=...)``, already
                        capped per mechanism there (this function stays a dumb appender).
      ``near_miss``   — on an ABSTENTION (``backend == "none"``), the best sub-floor
                        dense candidates ``[{name, score}]`` — the evidence RET-11's
                        BM25-floor decision and the SIG-5 revisit never had.
      ``dense_floor`` — the calibrated floor those near-miss scores missed, so the
                        margin is readable off the row without re-deriving the constant.

    MSR-3 ``channel``: which surface issued the recall — ``'hook'`` (the silent
    UserPromptSubmit path; the ONLY writer before this field existed) or ``'mcp'``
    (the recall/why tools, agent-issued mid-turn). ADDITIVE and absent-means-hook:
    a hook event writes NO key at all, so every pre-MSR-3 row and every new hook row
    parse identically (ED-4, no schema bump). Consumers that must stay hook-only
    (doctor's KPI-3 p95) filter on ``channel in (None, 'hook')``. The CLI
    (``recall_view.main``, a human browsing) deliberately stays UNLOGGED — see
    that module's docstring; agent-issued MCP is the only channel added.

    MSR-6 ``injected_chars``: the ACTUAL emitted hook-payload length (``len`` of the
    formatted block the hook printed into context) — measured at the emission point,
    never re-derived. Hook + SessionStart surfaces only: an MCP recall's return goes
    to the asking agent as a tool result, not silently into context, so MCP events
    deliberately never carry it. Additive/absence-emits-nothing; an abstention
    emitted nothing, so it writes no key rather than a fake 0.
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
        if drops:
            event["drops"] = drops
        if near_miss:
            event["near_miss"] = near_miss
        if dense_floor is not None:
            event["dense_floor"] = dense_floor
        if channel and channel != "hook":
            event["channel"] = channel
        if injected_chars is not None:
            event["injected_chars"] = int(injected_chars)
        v = _producer_version()  # MEA-4: provenance stamp, cached; omitted when unreadable
        if v:
            event["v"] = v
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
# MSR-6: the injection cost ledger — SessionStart's per-producer byte contributions.
#
# hippo's value is silent injection, so its COST was invisible: nothing recorded what
# each SessionStart producer contributed against the _MAX_CONTEXT_CHARS budget, and
# session_token_cost could only ESTIMATE. One rotating jsonl beside the recall ledger
# (same contract: never raises, byte-rotated, gitignored, SEC-3 self-ignoring) holds
# {ts, session_id, producers: {label: chars}, total, cap} per SessionStart emission.
# SESSION/PRODUCER aggregation ONLY — rows carry producer LABELS, never memory names,
# so no consumer can grow a per-memory cross-session touch table out of this file
# (the round-1 inert-recall-noise-finder kill, enforced by the MSR-6 AST pin).
# --------------------------------------------------------------------------- #
_INJECTION_LEDGER_NAME = "injection_producers.jsonl"


def _injection_ledger_path(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, _INJECTION_LEDGER_NAME)


def log_injection_producers(
    producers: Dict[str, int],
    *,
    total: int,
    cap: int,
    telemetry_dir: Optional[str] = None,
    session_id: Optional[str] = None,
) -> bool:
    """Append ONE SessionStart injection-cost row. Fire-and-forget: NEVER raises.

    ``producers`` maps producer LABEL -> emitted chars (pre-bound contributions);
    ``total`` is the final bounded payload length actually injected; ``cap`` the
    ``_MAX_CONTEXT_CHARS`` budget it was bounded against. Zero behavior change to
    injection itself — the caller measures what it already built.
    """
    try:
        if not producers:
            return False
        td = _resolve_dir(telemetry_dir)
        ensure_self_ignoring_dir(td)  # derived dir: mkdir + self-ignoring .gitignore (SEC-3)
        event = {
            "ts": round(time.time(), 3),
            "session_id": current_session_id(td, session_id=session_id),
            "producers": {str(k): int(v) for k, v in producers.items()},
            "total": int(total),
            "cap": int(cap),
        }
        path = _injection_ledger_path(td)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        _rotate_if_needed(path)
        return True
    except Exception:
        return False


def read_injection_producers(telemetry_dir: Optional[str] = None) -> Iterator[dict]:
    """Yield parsed injection-cost rows, skipping corrupt/partial lines. Never raises."""
    try:
        td = _resolve_dir(telemetry_dir)
        path = _injection_ledger_path(td)
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
# SEN-2 Tier-B: the measured-only threat ledger.
#
# Injection-imperative grammar (ignore-previous-instructions, tool-mimicry) is HELD DARK —
# it WILL false-positive on hippo's own corpus (which is about prompt injection), so
# surfacing it would degrade the human-review channel (inv3). Instead it is MEASURED here:
# each write-plane seam that finds Tier-B grammar appends one row, and doctor renders a
# single aggregate count line. A dated owner decision graduates it to Tier-A only once this
# ledger proves a near-zero FP rate. Append-only, gitignored, rotates — the eval-run-ledger
# precedent (MSR-1): registered in test_write_discipline's allowlist, not CRASH_CONTRACT
# (a torn tail is a skipped measurement, never lost corpus truth).
# --------------------------------------------------------------------------- #
def log_threat_findings(
    kinds: List[str],
    *,
    source: str,
    name: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    session_id: Optional[str] = None,
) -> bool:
    """Append ONE Tier-B threat row. Fire-and-forget: NEVER raises, NEVER surfaced.

    ``kinds`` are the ``threat_lint.scan_tier_b`` KINDS (never the payload); ``source`` is
    the seam ("write"/"capture"/"import"); ``name`` the memory/candidate stem when known.
    Absence-emits-nothing: an empty ``kinds`` writes no row (a clean scan adds no ledger
    noise), so the ledger's row count IS the Tier-B hit count for FP measurement.
    """
    try:
        if not kinds:
            return False
        td = _resolve_dir(telemetry_dir)
        ensure_self_ignoring_dir(td)  # derived dir: mkdir + self-ignoring .gitignore (SEC-3)
        event = {
            "ts": round(time.time(), 3),
            "session_id": current_session_id(td, session_id=session_id),
            "source": str(source),
            "name": str(name) if name else None,
            "kinds": [str(k) for k in kinds],
        }
        path = _threat_ledger_path(td)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        _rotate_if_needed(path)
        return True
    except Exception:
        return False


def read_threat_findings(telemetry_dir: Optional[str] = None) -> Iterator[dict]:
    """Yield parsed Tier-B threat rows, skipping corrupt/partial lines. Never raises."""
    try:
        td = _resolve_dir(telemetry_dir)
        path = _threat_ledger_path(td)
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


def threat_ledger_aggregate(telemetry_dir: Optional[str] = None) -> dict:
    """``{"rows": int, "kinds": {kind: count}}`` over the Tier-B ledger — doctor's one line.

    Aggregate only, by design: the doctor surface is a single count, never a per-memory
    listing (that would resurrect the surfaced-flag the tier deliberately holds dark). Never
    raises; empty aggregate when the ledger is absent.
    """
    rows = 0
    kinds: Dict[str, int] = {}
    for row in read_threat_findings(telemetry_dir):
        rows += 1
        for k in row.get("kinds") or []:
            kinds[str(k)] = kinds.get(str(k), 0) + 1
    return {"rows": rows, "kinds": kinds}


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




# GRW-4: the in-session decision ledger. The WHY of a session — tradeoffs the user confirmed,
# approaches they chose — cannot be re-derived from git or code and hooks can never scrape it
# (no Stop event, no transcript access, no LLM in hooks — triply impossible by design). So the
# capture moment is IN-SESSION: the AGENT records each user-confirmed decision explicitly via
# ``memory.capture --add-decision`` (prompted by the PreCompact nudge), and SessionEnd folds
# the session's entries into the capture seed. Same keying, rotation, and privacy posture as
# the episode buffer; entries are bounded so a chatty session can't bloat the ledger.
_DECISION_LEDGER_NAME = "decisions.jsonl"
_DECISION_MAX_CHARS = 400


def _decision_ledger_path(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, _DECISION_LEDGER_NAME)


def log_decision(
    text: str,
    *,
    telemetry_dir: Optional[str] = None,
    session_id: Optional[str] = None,
) -> bool:
    """Append ONE user-confirmed session decision to the gitignored ``decisions.jsonl``.

    CAPTURE-FROM-EVIDENCE, enforced at the surface: this is only ever called by the agent,
    per decision, with text the user stated or confirmed — the tooling never synthesizes an
    entry (there is no automated caller anywhere). Truncated to ``_DECISION_MAX_CHARS``,
    keyed like ``log_episode`` — the harness session id when given, else the shared file
    token. Seed matching is STRICT on the harness id, so a file-token row can never ride
    the session-proven lane; it surfaces only via capture's LABELED time-window fallback
    (``window_decisions``, WRT-3) at the drain. Fire-and-forget: True on append, False on
    any failure, never raises.
    """
    try:
        cleaned = (text or "").strip()
        if not cleaned:
            return False
        td = _resolve_dir(telemetry_dir)
        ensure_self_ignoring_dir(td)  # derived dir: mkdir + self-ignoring .gitignore (SEC-3)
        event = {
            "ts": round(time.time(), 3),
            "session_id": current_session_id(td, session_id=session_id),
            "text": cleaned[:_DECISION_MAX_CHARS],
        }
        path = _decision_ledger_path(td)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        _rotate_if_needed(path)
        return True
    except Exception:
        return False


def read_decisions(telemetry_dir: Optional[str] = None) -> Iterator[dict]:
    """Yield parsed decision-ledger entries, skipping corrupt/partial lines. Never raises."""
    try:
        td = _resolve_dir(telemetry_dir)
        path = _decision_ledger_path(td)
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
    cited_by: Optional[List[str]] = None,
    tree_path: Optional[str] = None,
) -> bool:
    """Append ONE file-touch outcome to ``outcome_events.jsonl``. Fire-and-forget; never raises.

    ``cited_by`` (JIT-2, T16) is OPTIONAL touch-grain provenance: the memory names whose
    cited_paths matched this touch AT TOUCH TIME (the jit lane's derived-map lookup — the
    exact (memory, file, touch) coincidence session-grain joins can only approximate).
    The field is SPARSE and additive: absent when nothing cites the path (most rows), so
    existing session-grain consumers read rows exactly as before. The caller bounds the
    volume (``jit.MAX_PROVENANCE_ROWS_PER_SESSION`` / ``MAX_CITED_PER_PATH``); this
    function stays a dumb appender.

    ``tree_path`` (MEA-6) is the worktree touch's in-tree normalization — the repo-relative
    tail the caller derived from ``outcome._WORKTREE_PREFIX``. Additive and sparse (ED-4):
    the raw ``path`` is always preserved; the field rides only when the two differ, and
    read-side joins prefer it when present. No schema bump — a queue-own shape read via
    ``.get()``.
    """
    try:
        td = _resolve_dir(telemetry_dir)
        ensure_self_ignoring_dir(td)  # derived dir: mkdir + self-ignoring .gitignore (SEC-3)
        event = {
            "ts": round(time.time(), 3),
            "session_id": current_session_id(td, session_id=session_id),
            "tool": tool,
            "path": path,
        }
        if cited_by:
            event["cited_by"] = [str(n) for n in cited_by if n]
        if tree_path and tree_path != path:
            event["tree_path"] = tree_path
        v = _producer_version()  # MEA-4: provenance stamp, cached; omitted when unreadable
        if v:
            event["v"] = v
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
    invalid_after: Optional[str] = None,
    superseded_by: Optional[str] = None,
    succession_replay: Optional[dict] = None,
) -> bool:
    """Append ONE reconsolidation outcome to the gitignored ``reconsolidation_events.jsonl``.

    ``outcome`` must be one of ``{"graduate", "fix", "demote", "snooze"}`` — an invalid
    outcome is a silent no-op (returns ``False``) rather than corrupting
    ``graduation_rate()``'s denominator with garbage (``snooze`` — LIF-1's per-item ack —
    is valid here but ignored by that ratio: an explicit deferral is not a verdict).
    ``invalidated``, when not ``None``, is stamped onto the event — LIF-1's demote chain
    passes it so the ledger is an AUDIT TRAIL of whether the verdict also closed the
    memory's validity window (``staleness.set_invalid_after``), not just that it was
    rendered. ``invalid_after`` and ``superseded_by`` (GRW-7) extend that trail to the
    supersession itself: the stamped validity BOUNDARY (the successor's commit date) and
    the successor's name, so a demotion is an auditable fact, not a silent score nudge.
    Fire-and-forget; NEVER raises; size-bounded (reuses ``_rotate_if_needed``);
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
        if invalid_after is not None:
            event["invalid_after"] = str(invalid_after)
        if superseded_by is not None:
            event["superseded_by"] = str(superseded_by)
        if succession_replay is not None:
            # TMB-5: replay COUNTS only ({"harvested", "pass", "fail", "inconclusive"}) —
            # an additive field on the SAME event (no new ledger, no new outcome value),
            # and the no-sensitive-content contract holds: never query text.
            event["succession_replay"] = dict(succession_replay)
        v = _producer_version()  # MEA-4: provenance stamp, cached; omitted when unreadable
        if v:
            event["v"] = v
        path = _reconsolidation_ledger_path(td)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        _rotate_if_needed(path)
        return True
    except Exception:
        return False


_ARCHIVE_REGRET_NAME = "archive_regret.jsonl"


def _archive_regret_path(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, _ARCHIVE_REGRET_NAME)


def log_archive_regret(query: str, stem: str, telemetry_dir: Optional[str] = None) -> bool:
    """TMB-3: append ONE regret event ``{ts, query, stem}`` — evidence only.

    The doctor check both writes AND reads this ledger (read-back = the dedup memory, so
    it is never a dark reservoir); NOTHING restore-shaped ever consumes it. Fire-and-
    forget; never raises; size-bounded.
    """
    try:
        td = _resolve_dir(telemetry_dir)
        ensure_self_ignoring_dir(td)
        path = _archive_regret_path(td)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {"ts": round(time.time(), 3), "query": str(query), "stem": str(stem)},
                    ensure_ascii=False,
                )
                + "\n"
            )
        _rotate_if_needed(path)
        return True
    except Exception:
        return False


def read_archive_regret(telemetry_dir: Optional[str] = None) -> Iterator[dict]:
    """Yield parsed archive-regret events, skipping corrupt/partial lines. Never raises."""
    try:
        with open(_archive_regret_path(_resolve_dir(telemetry_dir)), "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except Exception:
        return


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
