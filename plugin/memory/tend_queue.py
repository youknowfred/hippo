"""TND-1: one derived, ranked maintenance queue.

hippo's maintenance used to arrive as separate SessionStart producers, each with its own
verb: the capture queue, the reverify worklist, the contradiction inbox, the derivation
nag, trust drift, broken baselines, link rot, the merge digest and floor overflow. This
module derives all of them into ONE ranked list of entries. Each entry says what needs a
decision, why (the evidence), what the likely verdict is, and which per-item gate applies.

Derived, never authoritative: every entry is recomputed from the sources that already own
the state (the pending dir, the staleness/reconsolidation engine, the link graph, the trust
registry, the merge digest, the floor lint, the re-derivation worklist). Nothing here
writes the corpus. The only things written are gitignored: the last-built queue (so a
count can be read without rebuilding) and the operator's tend state (snoozes, skips, and
owner holds), both under the corpus's telemetry dir.

Entry fields:
  - ``id``        ``<kind>:<target>``, stable across rebuilds
  - ``kind``      one of KINDS
  - ``target``    the memory name, seed file, pair or file the decision is about
  - ``evidence``  one line: why it is queued
  - ``proposed``  the verdict most likely right, for the human to confirm or change
  - ``gate``      the per-item gate that applies; tend never batches past it
  - ``rank``      the kind's rank (lower first), then source order within a kind

Tend state (TND-2 writes it; this module reads it): ``snooze`` hides an entry until a date,
``skip`` hides it until its evidence changes, and ``hold`` is an owner decision with a
reason that hides a whole kind (or one target) until released. Held entries count as
resolved, so a corpus can reach an empty queue while keeping, for example, an old
citation derivation on purpose.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Callable, Dict, List, Optional, Tuple

# Rank order: integrity first (what is withheld or invisible), then decisions that block
# others (contradictions, incoming duplicates), then the steady-state queues.
KINDS: Tuple[str, ...] = (
    "trust",
    "baseline",
    "contradiction",
    "merge",
    "capture",
    "reverify",
    "link",
    "floor",
    "derivation",
)

GATES: Dict[str, str] = {
    "trust": "re-consent after reading what changed; consent is never inferred",
    "baseline": "one memory at a time: confirm it still holds, then re-baseline it",
    "contradiction": "one pair at a time: a human verdict, never an automatic winner",
    "merge": "one pair at a time: update the existing memory, supersede, or skip",
    "capture": "one seed at a time: check for duplicates first, then write or discard",
    "reverify": "one memory at a time: graduate, fix, demote or snooze",
    "link": "one link at a time: fix or remove it in the memory body",
    "floor": "one floor line at a time: trim it, or move it out of the floor",
    "derivation": "one memory at a time: read the citation diff, then apply it; stamp last",
}

_QUEUE_FILE = "tend_queue.json"
_STATE_FILE = "tend_state.json"
# Per-kind cap on rendered entries. The count stays exact; this only bounds the payload.
_MAX_PER_KIND = 200


def _entry(kind: str, target: str, evidence: str, proposed: str) -> dict:
    return {
        "id": f"{kind}:{target}",
        "kind": kind,
        "target": target,
        "evidence": evidence,
        "proposed": proposed,
        "gate": GATES[kind],
        "rank": KINDS.index(kind),
    }


def evidence_hash(entry: dict) -> str:
    """Fingerprint of what an entry claims, so a skip lapses when the evidence moves."""
    raw = f"{entry['id']}\n{entry['evidence']}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Sources — one per kind. Each is read-only and returns [] on any failure, so one
# broken source never hides the others (its failure is reported in diagnostics).
# --------------------------------------------------------------------------- #
def _src_trust(memory_dir: str, repo_root: str) -> List[dict]:
    from . import trust

    gate = trust.gate_repo_root(memory_dir, repo_root)
    if gate is None or not trust.is_trusted(gate):
        return []
    drift = trust.untrusted_changes(gate, memory_dir)
    out = [
        _entry("trust", name, "changed since you consented; recall is withholding it",
               "read the change, then re-consent")
        for name in drift.get("changed") or []
    ]
    out += [
        _entry("trust", name, "new since you consented; recall is withholding it",
               "read it, then consent")
        for name in drift.get("added") or []
    ]
    return out


def _src_baseline(memory_dir: str, repo_root: str) -> List[dict]:
    from .provenance import _iter_memory_files, parse_frontmatter
    from .staleness import unresolvable_baseline_names
    from .fm_access import fm_get

    if not os.path.isdir(memory_dir):
        return []  # TND-8: no corpus, so no baselines; an unreadable one still fails the source
    out = []
    for path in _iter_memory_files(memory_dir):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                fm = parse_frontmatter(fh.read())
        except Exception:
            continue
        sc = fm_get(fm, "source_commit") if fm else None
        if sc is not None and not str(sc).strip():
            out.append(_entry(
                "baseline", os.path.basename(path)[:-3],
                "empty staleness baseline: invisible to staleness tracking",
                "heal the baseline to the current commit",
            ))
    for name in unresolvable_baseline_names(memory_dir, repo_root):
        out.append(_entry(
            "baseline", name,
            "its baseline commit is not in this repo's history (a squash merge or rewrite)",
            "confirm it still holds, then re-baseline it",
        ))
    return out


def _src_contradiction(memory_dir: str, repo_root: str) -> List[dict]:
    from .resolve_view import unresolved_contradictions
    from .telemetry import default_telemetry_dir

    out = []
    for item in unresolved_contradictions(
        memory_dir, repo_root=repo_root, telemetry_dir=default_telemetry_dir(memory_dir)
    ):
        a, b = item["pair"]
        who = "proposed by the link pass" if item.get("proposed") else (
            "declared by " + ", ".join(item.get("declared_by") or []) or "declared")
        out.append(_entry(
            "contradiction", f"{a}|{b}", f"these two memories contradict ({who})",
            "read both; supersede one, scope both, merge, or mark them not conflicting",
        ))
    return out


def _src_merge(memory_dir: str, repo_root: str) -> List[dict]:
    from .merge_digest import incoming_duplicate_pairs
    from .telemetry import default_telemetry_dir

    pairs, _degradation, _n = incoming_duplicate_pairs(
        memory_dir, repo_root, default_telemetry_dir(memory_dir)
    )
    out = []
    for p in pairs:
        if p.get("route") == "resolve":
            proposed = "they also contradict: give the pair a verdict"
        else:
            proposed = "update the existing memory, supersede it, or skip"
        out.append(_entry(
            "merge", f"{p['incoming']}|{p['neighbor']}",
            f"merged-in memory looks like a duplicate (similarity {float(p.get('score', 0)):.2f})",
            proposed,
        ))
    return out


def _src_capture(memory_dir: str, repo_root: str) -> List[dict]:
    from .capture_queue import _seed_score, read_pending

    out = []
    for seed in read_pending(memory_dir=memory_dir):
        path = seed.get("_path") or seed.get("path") or ""
        target = os.path.basename(path) if path else str(seed.get("session_id") or "seed")
        score = _seed_score(seed)
        changed = len(seed.get("changed_paths") or [])
        queries = len(seed.get("query_previews") or seed.get("queries") or [])
        evidence = f"captured session: value {score}, {changed} changed file(s), {queries} prompt(s)"
        proposed = "discard: nothing durable" if score <= 0 else "draft what is durable, duplicate-check first"
        out.append(_entry("capture", target, evidence, proposed))
    return out


def _src_reverify(memory_dir: str, repo_root: str) -> List[dict]:
    from .reconsolidate import recalled_stale_worklist

    out = []
    for item in recalled_stale_worklist(memory_dir, repo_root):
        paths = item.get("changed_paths") or []
        shown = ", ".join(paths[:3]) + (f" (+{len(paths) - 3} more)" if len(paths) > 3 else "")
        out.append(_entry(
            "reverify", item["name"], f"cited code changed since it was verified: {shown}",
            "re-read the cited change; graduate if the claim still holds",
        ))
    return out


def _src_link(memory_dir: str, repo_root: str) -> List[dict]:
    from .lint_links import lint

    report = lint(memory_dir)
    out = []
    for d in report.get("dangling") or []:
        src = d["file"][:-3] if d["file"].endswith(".md") else d["file"]
        out.append(_entry("link", f"{src}->{d['target']}",
                          f"{src} links to [[{d['target']}]], which does not exist",
                          "point it at the right memory, or remove the link"))
    for d in report.get("ambiguous") or []:
        src = d["file"][:-3] if d["file"].endswith(".md") else d["file"]
        out.append(_entry("link", f"{src}->{d['target']}",
                          f"{src} links to [[{d['target']}]], which more than one memory claims",
                          "make the link name exactly one memory"))
    for d in report.get("typed_dangling") or []:
        src = d["file"][:-3] if d["file"].endswith(".md") else d["file"]
        out.append(_entry("link", f"{src}-{d['relation']}->{d['target']}",
                          f"{src} declares {d['relation']}: {d['target']}, which resolves to no memory",
                          "point the relation at the right memory, or remove it"))
    return out


def _src_floor(memory_dir: str, repo_root: str) -> List[dict]:
    from .lint_floor import floor_violations, over_hard_edge
    from .provenance_format import read_floor_lint

    out = []
    v = floor_violations(memory_dir)
    for item in v.get("rebloat") or []:
        out.append(_entry("floor", f"{item['file']}", f"floor pointer outside the floor sections ({item['section']})",
                          "move it into a floor section or out of MEMORY.md"))
    for item in v.get("missing_targets") or []:
        out.append(_entry("floor", item["file"], "floor pointer names a memory that does not exist",
                          "remove the pointer or restore the memory"))
    try:
        with open(os.path.join(memory_dir, "MEMORY.md"), "r", encoding="utf-8") as fh:
            edge = over_hard_edge(fh.read(), read_floor_lint(memory_dir))
    except Exception:
        edge = None
    if edge:
        out.append(_entry("floor", "MEMORY.md", edge, "trim the floor below the limit"))
    return out


def _src_derivation(memory_dir: str, repo_root: str) -> List[dict]:
    from .provenance import rederive_worklist
    from .provenance_format import CITATION_DERIVATION_VERSION, read_cite_derivation

    if not os.path.isdir(memory_dir):
        return []  # MIG-3: no corpus, so nothing to re-derive and no marker to stamp
    have = read_cite_derivation(memory_dir)
    work = rederive_worklist(memory_dir, repo_root)
    out = []
    for r in work:
        gained, lost = r.get("gained") or [], r.get("lost") or []
        out.append(_entry(
            "derivation", r["name"],
            f"citations re-derive differently ({len(gained)} gained, {len(lost)} lost)",
            "read the citation diff, then apply it",
        ))
    if not out and have < CITATION_DERIVATION_VERSION:
        out.append(_entry(
            "derivation", "corpus",
            f"citations were derived by extractor {have}; this plugin derives {CITATION_DERIVATION_VERSION} "
            "and nothing would change",
            "stamp the corpus with the current derivation",
        ))
    return out


SOURCES: Dict[str, Callable[[str, str], List[dict]]] = {
    "trust": _src_trust,
    "baseline": _src_baseline,
    "contradiction": _src_contradiction,
    "merge": _src_merge,
    "capture": _src_capture,
    "reverify": _src_reverify,
    "link": _src_link,
    "floor": _src_floor,
    "derivation": _src_derivation,
}


# --------------------------------------------------------------------------- #
# Tend state (snooze / skip / hold) — gitignored, per clone
# --------------------------------------------------------------------------- #
def _telemetry_dir(memory_dir: str) -> str:
    from .telemetry import default_telemetry_dir

    return default_telemetry_dir(memory_dir)


def state_path(memory_dir: str) -> str:
    return os.path.join(_telemetry_dir(memory_dir), _STATE_FILE)


def read_state(memory_dir: str) -> dict:
    """``{"snooze": {id: until_epoch}, "skip": {id: evidence_hash}, "hold": {key: {...}}}``.
    A hold key is a kind (``derivation``) or an entry id. Never raises."""
    empty = {"snooze": {}, "skip": {}, "hold": {}}
    try:
        with open(state_path(memory_dir), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict):
            return empty
        return {k: dict(doc.get(k) or {}) for k in empty}
    except Exception:
        return empty


def _held_by(entry: dict, state: dict) -> Optional[dict]:
    holds = state.get("hold") or {}
    return holds.get(entry["id"]) or holds.get(entry["kind"])


def _hidden(entry: dict, state: dict, now: float) -> Optional[str]:
    if _held_by(entry, state):
        return "held"
    until = (state.get("snooze") or {}).get(entry["id"])
    if isinstance(until, (int, float)) and until > now:
        return "snoozed"
    if (state.get("skip") or {}).get(entry["id"]) == evidence_hash(entry):
        return "skipped"
    return None


# --------------------------------------------------------------------------- #
# The queue
# --------------------------------------------------------------------------- #
def build_queue(
    memory_dir: str,
    repo_root: str,
    *,
    kinds: Optional[Tuple[str, ...]] = None,
    now: Optional[float] = None,
    write_cache: bool = True,
) -> dict:
    """Derive the whole queue. Returns::

        {"pending": [entries, ranked], "held": [...], "snoozed": [...], "skipped": [...],
         "counts": {kind: pending count}, "errors": {kind: message},
         "timings_ms": {kind: ms}, "built_at": epoch}

    Read-only over the corpus; writes the gitignored cache when ``write_cache``.
    """
    now = time.time() if now is None else now
    state = read_state(memory_dir)
    result = {"pending": [], "held": [], "snoozed": [], "skipped": [], "counts": {},
              "errors": {}, "timings_ms": {}, "built_at": now}
    for kind in kinds or KINDS:
        t0 = time.monotonic()
        try:
            entries = SOURCES[kind](memory_dir, repo_root)
        except Exception as exc:  # a broken source is reported, never fatal
            entries = []
            result["errors"][kind] = f"{type(exc).__name__}: {exc}"
        result["timings_ms"][kind] = int((time.monotonic() - t0) * 1000)
        pending = 0
        for e in entries:
            why = _hidden(e, state, now)
            if why is None:
                pending += 1
                if pending <= _MAX_PER_KIND:
                    result["pending"].append(e)
            else:
                if why == "held":
                    e = dict(e, hold=_held_by(e, state))
                result[why].append(e)
        result["counts"][kind] = pending
    result["pending"].sort(key=lambda e: e["rank"])  # stable: source order within a kind
    if write_cache:
        _write_cache(memory_dir, result)
    return result


def _write_cache(memory_dir: str, result: dict) -> None:
    try:
        from .atomic import write_json_atomic

        td = _telemetry_dir(memory_dir)
        if not os.path.isdir(td):
            return  # never create a ledger dir for a corpus that has none yet
        write_json_atomic(os.path.join(td, _QUEUE_FILE), {
            "built_at": result["built_at"],
            "counts": result["counts"],
            "held": sorted({e["kind"] for e in result["held"]}),
            "errors": result["errors"],
        })
    except Exception:
        pass


def cached_counts(memory_dir: str) -> Optional[dict]:
    """The last build's per-kind pending counts and build time, or None. Never raises."""
    try:
        with open(os.path.join(_telemetry_dir(memory_dir), _QUEUE_FILE), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) and isinstance(doc.get("counts"), dict) else None
    except Exception:
        return None


def total_pending(result: dict) -> int:
    return sum(int(v) for v in (result.get("counts") or {}).values())
