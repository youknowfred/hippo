"""GOV-1: the contradiction inbox + /hippo:resolve — a standing, drainable conflict queue.

A ``contradicts`` typed edge deliberately demotes NEITHER side (GRA-4: "one of these is
wrong, VERIFY" — not "this one lost"), so its only surface was the hot-path annotation on a
pointer, which appears IF both sides co-surface in one recall. A live conflict ("we use X"
vs "we stopped using X") could therefore sit unresolved forever while the model keeps
getting injected both sides. This module gives every unresolved pair ONE standing queue:

- ``unresolved_contradictions`` enumerates ALL corpus-wide ``contradicts`` pairs (the
  ``LinkGraph.all_typed_edges`` primitive) minus this clone's resolved ledger — PLUS, when
  dream's opt-in DRM-C pass has run, the LLM-PROPOSED pairs from its derived verdict ledger
  (``dream.contradictions_ledger_path``): candidates no human has declared yet, marked
  ``proposed: True`` and fed into the SAME inbox so discovery gains a second source without
  gaining a second review surface. A proposal clears itself on any corpus verdict — a
  ``supersedes`` edge between the pair, either file leaving the corpus, or the pair being
  DECLARED ``contradicts`` (it then simply appears as a declared item) — and the dismiss
  ledger below suppresses it exactly like a declared pair.
- The /hippo:resolve skill walks each pair for a per-item human verdict. Every
  corpus-MUTATING verdict (keep-A-supersede-B via ``reconsolidate --reverify <loser>
  --outcome demote --superseded-by <winner>``, merge, scope-both) is an ordinary reviewable
  git commit that removes or rewrites the ``contradicts:`` declaration — markdown-in-git
  keeps the authority (inv1), and nothing here auto-picks a winner (inv4).
- ONLY the one corpus-PRESERVING verdict — "these do not actually conflict" — lands in the
  gitignored per-clone ledger below, because there is nothing for the corpus to change:
  both memories stay, the edge stays as documentation, the pair just stops nagging.

The ledger is derived, per-clone state (rebuildable by re-dismissing), keyed by the same
``sha256(realpath(repo_root))[:16]`` scheme as the SessionStart nudge counters so two
clones of the same repo never share verdicts through a common CLAUDE_PLUGIN_DATA. Nothing
in recall imports this module — zero hot-path cost (inv6).

Write posture: the module's OWN only write is the ledger. INV-4's ``apply_resolve_verdict``
(the resolve MCP tool's engine — the verb's second surface) additionally EXECUTES the
per-pair corpus verdicts, but every corpus byte it changes goes through the shared
primitives (``reconsolidate.semantic_reverify``, ``links.remove_typed_relation``) with the
COR-16 rollback discipline (``provenance.restore_file_bytes``) around its two-write chains
— never a raw write from here, and never more than ONE pair per call.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Optional, Set, Tuple

_LEDGER_PREFIX = ".resolve-ledger-"

# TMB-1: the ONE verdict taxonomy — exactly the four names the /hippo:resolve skill and
# the resolve MCP tool expose, plus the explicit no-suggestion token. The evidence card's
# prefill is expressed STRICTLY in these; there is deliberately no second vocabulary.
_VERDICT_NAMES = ("keep_one", "scope_both", "merge", "not_conflicting")
_ABSTAIN = "abstain"

# TMB-1: usage asymmetry is only evidence once the ledger has seen enough distinct
# sessions to mean anything — below this floor the column is withheld (labeled, not
# silently dropped). Same 5-session rhythm as soak.SOAK_GATE_SESSIONS /
# reconsolidate._SNOOZE_WINDOW_SESSIONS; a plain module constant, no env knob.
_EVIDENCE_USAGE_MIN_SESSIONS = 5


def _repo_key(repo_root: str) -> str:
    """Per-clone corpus key — ``_periodic_nudge_should_fire``'s exact derivation."""
    return hashlib.sha256(os.path.realpath(repo_root).encode("utf-8")).hexdigest()[:16]


def ledger_path(repo_root: str) -> Optional[str]:
    """This clone's resolved-pairs ledger path, or ``None`` when CLAUDE_PLUGIN_DATA is unset.

    ``None`` (not a cwd-relative fallback) is deliberate: without a durable per-clone home
    the ledger would either leak between corpora or vanish per-process — both worse than
    an honest "no durable ledger" the dismiss verdict reports loudly.
    """
    data_dir = os.environ.get("CLAUDE_PLUGIN_DATA") or ""
    if not data_dir:
        return None
    return os.path.join(data_dir, f"{_LEDGER_PREFIX}{_repo_key(repo_root)}")


def _canonical_pair(a: str, b: str) -> Tuple[str, str]:
    """One order-free identity per conflict — ``a ⇄ b`` and ``b ⇄ a`` are the same pair."""
    return (a, b) if a <= b else (b, a)


def _read_ledger_doc(repo_root: str) -> dict:
    """The per-clone ledger's whole JSON document, ``{}`` on absence/corruption. Never raises.

    TMB-1 made the ledger two-keyed (``resolved`` + the additive ``verdicts`` log below), so
    both writers must read-modify-write the WHOLE document — a writer that rebuilt only its
    own key would silently drop the other's records.
    """
    try:
        path = ledger_path(repo_root)
        if not path or not os.path.isfile(path):
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def read_resolved(repo_root: str) -> Set[Tuple[str, str]]:
    """Pairs this clone marked not-conflicting; ``set()`` on no ledger/no data dir. Never raises."""
    try:
        pairs = _read_ledger_doc(repo_root).get("resolved")
        out: Set[Tuple[str, str]] = set()
        for p in pairs or []:
            if isinstance(p, list) and len(p) == 2 and all(isinstance(x, str) for x in p):
                out.add(_canonical_pair(p[0], p[1]))
        return out
    except Exception:
        return set()


def read_verdict_log(repo_root: str) -> List[dict]:
    """TMB-1's prefill-agreement records — ``[{"pair", "verdict", "prefill"}]`` in the order
    rendered on this clone. ``[]`` on no ledger. Derived, per-clone, rebuildable exactly like
    the dismiss records it lives beside — never an authority, never a ranking input."""
    try:
        rows = _read_ledger_doc(repo_root).get("verdicts")
        return [r for r in rows or [] if isinstance(r, dict)]
    except Exception:
        return []


def _log_verdict(repo_root: str, pair: Tuple[str, str], verdict: str, prefill: Optional[str]) -> bool:
    """Record ONE rendered verdict next to the evidence card's prefill (TMB-1).

    The additive ``verdicts`` key on the SAME per-clone ledger file the dismiss verdict
    already writes — deliberately NOT a sibling ledger, NOT a reconsolidation outcome
    (``_RECONSOLIDATION_OUTCOMES`` is untouched), and never consulted by ranking. This is
    the capture half only: whether the human's choice agreed with the card's suggestion is
    future-audit material; nothing reads it on any automated path, and there is no
    accept-prefill/auto-default anywhere (gated behind a dated owner decision in the
    roadmap). Best-effort: ``False`` (no durable home / write failure) never voids the
    verdict itself. Never raises.
    """
    try:
        path = ledger_path(repo_root)
        if path is None:
            return False
        doc = _read_ledger_doc(repo_root)
        rows = doc.get("verdicts")
        if not isinstance(rows, list):
            rows = []
        rows.append({"pair": list(pair), "verdict": verdict, "prefill": prefill})
        doc["verdicts"] = rows
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=0)
            fh.write("\n")
        return True
    except Exception:
        return False


def mark_not_conflicting(a: str, b: str, repo_root: str) -> dict:
    """Record "``a`` and ``b`` do not actually conflict" in this clone's ledger.

    The ONE verdict that lands here instead of in git — every other /hippo:resolve verdict
    mutates the corpus (an ordinary reviewable commit) and needs no ledger at all.
    Idempotent; never raises. ``{"pair", "recorded", "ledger", "error"}`` — ``recorded``
    False + ``error`` set when there is nowhere durable to write (CLAUDE_PLUGIN_DATA
    unset), so the caller can say so instead of silently forgetting the verdict.
    """
    pair = _canonical_pair(str(a).strip(), str(b).strip())
    result = {"pair": list(pair), "recorded": False, "ledger": None, "error": None}
    if not pair[0] or not pair[1]:
        result["error"] = "both memory names are required"
        return result
    if pair[0] == pair[1]:
        result["error"] = "a memory cannot conflict with itself"
        return result
    try:
        path = ledger_path(repo_root)
        if path is None:
            result["error"] = (
                "CLAUDE_PLUGIN_DATA is unset — no durable per-clone ledger to record the "
                "verdict in (the pair will keep appearing in the inbox)"
            )
            return result
        result["ledger"] = path
        resolved = read_resolved(repo_root)
        resolved.add(pair)
        # TMB-1: rewrite the WHOLE document (dismissals + the verdicts log), not just this
        # writer's key — rebuilding {"resolved": …} alone would drop the prefill records.
        doc = _read_ledger_doc(repo_root)
        doc["resolved"] = sorted(list(p) for p in resolved)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=0)
            fh.write("\n")
        result["recorded"] = True
        return result
    except Exception as exc:
        result["error"] = f"ledger write failed: {exc}"
        return result


def proposed_contradictions(
    memory_dir: str,
    *,
    index_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
) -> List[dict]:
    """Dream-PROPOSED (DRM-C, LLM-discovered) pairs still awaiting a human verdict.

    Reads dream's derived verdict ledger and keeps only the ``conflict: true`` pairs that
    no human action has settled yet. Read-time subtraction IS the lifecycle — there is no
    second verdict machinery: a pair leaves this list when it is dismissed
    (``mark_not_conflicting`` — the same ledger as declared pairs), DECLARED (a
    ``contradicts:`` line now exists, so it surfaces as a declared item instead),
    superseded (the keep-one verdict's ``supersedes`` edge — succession has settled it),
    or when either file leaves the corpus (merge/retire). The scope-both verdict ends in
    "both stand as written", which is exactly the dismiss verdict — the skill listing says
    so. Empty is fine (the flag never ran, or everything settled). Never raises.
    """
    try:
        from .build_index import default_index_dir
        from .dream import read_contradiction_verdicts
        from .links import build_graph
        from .telemetry import default_telemetry_dir

        td = telemetry_dir or default_telemetry_dir(memory_dir)
        verdicts = read_contradiction_verdicts(td)
        live = {pair: rec for pair, rec in verdicts.items() if rec.get("conflict") is True}
        if not live:
            return []
        settled: Set[Tuple[str, str]] = set()
        graph = build_graph(memory_dir, index_dir or default_index_dir(memory_dir))
        if graph is not None:
            for rel in ("contradicts", "supersedes"):
                for src, tgt in graph.all_typed_edges(rel):
                    settled.add(_canonical_pair(src, tgt))
        resolved = read_resolved(repo_root) if repo_root else set()
        out: List[dict] = []
        for pair in sorted(live):
            a, b = pair
            if pair in settled or pair in resolved:
                continue
            if not (
                os.path.isfile(os.path.join(memory_dir, f"{a}.md"))
                and os.path.isfile(os.path.join(memory_dir, f"{b}.md"))
            ):
                continue
            rec = live[pair]
            out.append(
                {
                    "pair": [a, b],
                    "declared_by": [],
                    "proposed": True,
                    "cofire": rec.get("cofire"),
                    "reason": str(rec.get("reason") or ""),
                    "pass": rec.get("pass"),
                }
            )
        return out
    except Exception:
        return []


def unresolved_contradictions(
    memory_dir: str,
    *,
    index_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
) -> List[dict]:
    """Every live ``contradicts`` pair minus this clone's resolved ledger — the inbox.

    Sorted ``[{"pair": [a, b], "declared_by": [stems]}]`` where ``pair`` is the canonical
    (order-free) identity and ``declared_by`` names which side(s) carry the frontmatter
    declaration — the file a corpus-mutating verdict edits. The ledger is only subtracted
    when ``repo_root`` is known (it keys the per-clone file); a caller without one gets the
    full corpus-wide enumeration. DREAM-PROPOSED pairs (DRM-C — ``proposed: True``,
    ``declared_by: []``, nothing to edit yet) follow the declared items: one inbox, one
    verdict flow, one more source feeding it. Empty is fine. Never raises.
    """
    try:
        from .build_index import default_index_dir
        from .links import build_graph

        graph = build_graph(memory_dir, index_dir or default_index_dir(memory_dir))
        if graph is None:
            return []
        declared: Dict[Tuple[str, str], Set[str]] = {}
        for src, tgt in graph.all_typed_edges("contradicts"):
            declared.setdefault(_canonical_pair(src, tgt), set()).add(src)
        resolved = read_resolved(repo_root) if repo_root else set()
        out = [
            {"pair": list(pair), "declared_by": sorted(srcs)}
            for pair, srcs in sorted(declared.items())
            if pair not in resolved
        ]
        out.extend(
            proposed_contradictions(
                memory_dir,
                index_dir=index_dir,
                repo_root=repo_root,
                telemetry_dir=telemetry_dir,
            )
        )
        return out
    except Exception:
        return []


def pair_edge_state(memory_dir: str, a: str, b: str, *, repo_root: Optional[str] = None) -> dict:
    """How this pair is currently in dispute: which side(s) DECLARE the ``contradicts``
    edge in frontmatter (the files a corpus verdict edits), and whether a live DRM-C
    proposal exists for it. ``{"declared_by": [stems], "proposed": bool}``. Never raises."""
    declared_by: List[str] = []
    try:
        from .links import normalize_slug, parse_typed_relations
        from .provenance import parse_frontmatter

        for side, other in ((a, b), (b, a)):
            try:
                with open(os.path.join(memory_dir, f"{side}.md"), "r", encoding="utf-8") as fh:
                    rels = parse_typed_relations(parse_frontmatter(fh.read()))
                if normalize_slug(other) in {normalize_slug(t) for t in rels.get("contradicts", [])}:
                    declared_by.append(side)
            except Exception:
                continue
    except Exception:
        pass
    proposed = any(
        tuple(item["pair"]) == _canonical_pair(a, b)
        for item in proposed_contradictions(memory_dir, repo_root=repo_root)
    )
    return {"declared_by": declared_by, "proposed": proposed}


# --------------------------------------------------------------------------- #
# TMB-1: the per-pair evidence card — a deterministic, read-only adjudication aid.
# Git-mined on demand (inv1: no new persisted state; git history IS the birth record),
# and confined to this module's describe()/--list cold path (inv6: the SessionStart
# contradiction producer and the recall hot path never reach these git calls — pinned).
# Freshness comes from provenance.git_last_commit_with_time DIRECTLY: importing
# reconsolidate here would put the demote engine one typo away from the read-only
# listing half (the no-corpus-write AST pin's whole point).
# --------------------------------------------------------------------------- #
def _memory_rel_path(name: str, memory_dir: str, repo_root: str) -> Optional[str]:
    """``<name>.md``'s repo-relative path, or ``None`` when outside the repo."""
    try:
        rel = os.path.relpath(
            os.path.realpath(os.path.join(memory_dir, f"{name}.md")),
            os.path.realpath(repo_root),
        )
        return None if rel.startswith("..") else rel.replace(os.sep, "/")
    except Exception:
        return None


def _edge_birth_commits_ago(
    declared_by: List[str], other_of: Dict[str, str], memory_dir: str, repo_root: str
) -> Optional[int]:
    """Commits since the ``contradicts`` declaration was introduced, or ``None`` (unknown).

    Git-mined, zero persisted state: pickaxe (``-S<counterpart>``) finds the OLDEST commit
    that changed the counterpart-name's occurrence count in the declaring file — the
    commit that introduced the reference — then ``rev-list --count <sha>..HEAD`` is the
    age in commits. An approximation by design (a body mention of the counterpart that
    predates the edge line reads as the birth), honest for an evidence card. With BOTH
    sides declaring, the OLDER introduction wins (the conflict has existed since the
    first declaration). ``None`` — rendered "age unknown" — for uncommitted files,
    shallow/rewritten history, a proposal-only pair (nothing declared), or any git
    failure. Never raises.
    """
    try:
        from .provenance import run_git

        best: Optional[int] = None
        for side in declared_by or []:
            other = other_of.get(side)
            rel = _memory_rel_path(side, memory_dir, repo_root)
            if not other or not rel:
                continue
            log = run_git(
                ["log", "--reverse", "--format=%H", f"-S{other}", "--", rel], repo_root
            )
            sha = log.split("\n")[0].strip() if log.strip() else ""
            if not sha:
                continue
            count = run_git(["rev-list", "--count", f"{sha}..HEAD"], repo_root).strip()
            try:
                n = int(count)
            except ValueError:
                continue
            best = n if best is None else max(best, n)
        return best
    except Exception:
        return None


def _git_newer_side(
    a: str, b: str, memory_dir: str, repo_root: str
) -> Tuple[Optional[str], Dict[str, Optional[int]]]:
    """``(newer_side_or_None, {name: last-commit epoch})`` — the freshness leg.

    ``provenance.git_last_commit_with_time`` on both ``.md`` files (their last-edit
    moments in history) — NEVER via reconsolidate (see the section comment). ``None``
    newer-side when either file is uncommitted/outside the repo or the epochs tie —
    unknown stays unknown, never a coin flip. Never raises.
    """
    epochs: Dict[str, Optional[int]] = {a: None, b: None}
    try:
        from .provenance import git_last_commit_with_time

        for side in (a, b):
            rel = _memory_rel_path(side, memory_dir, repo_root)
            if rel is None:
                continue
            _sha, epoch = git_last_commit_with_time(rel, repo_root)
            epochs[side] = epoch
        ea, eb = epochs[a], epochs[b]
        if ea is not None and eb is not None and ea != eb:
            return (a if ea > eb else b), epochs
        return None, epochs
    except Exception:
        return None, epochs


def _drift_evidence(a: str, b: str, memory_dir: str, index_dir: Optional[str]) -> Dict[str, int]:
    """``{side: changed-file count}`` for sides in the stale cache — cited-code drift.

    Reads ``stale.json`` (the LIF-6 cache SessionStart already refreshed) — zero git
    calls, zero corpus reads. ``{}`` when the cache is absent (advisory, same posture as
    every other reader of that file). Never raises.
    """
    try:
        from .build_index import default_index_dir
        from .staleness import read_stale_cache

        cache = read_stale_cache(index_dir or default_index_dir(memory_dir))
        if cache is None:
            return {}
        out: Dict[str, int] = {}
        for side in (a, b):
            rec = cache.get(side)
            if isinstance(rec, dict):
                try:
                    n = int(rec.get("changed") or 0)
                except (TypeError, ValueError):
                    continue
                if n > 0:
                    out[side] = n
        return out
    except Exception:
        return {}


def _usage_evidence(
    a: str, b: str, telemetry_dir: Optional[str]
) -> Tuple[Dict[str, int], bool]:
    """``({side: distinct-session recall count}, confident)`` — the usage-asymmetry leg.

    ``confident`` only once the aggregates have seen ``_EVIDENCE_USAGE_MIN_SESSIONS``
    distinct sessions (below the floor the card labels the column withheld rather than
    presenting two near-zero counts as signal). Read-only over the rotation-surviving
    aggregates; never raises.
    """
    counts: Dict[str, int] = {a: 0, b: 0}
    try:
        from .telemetry import read_usage_aggregates

        agg = read_usage_aggregates(telemetry_dir)
        for side in (a, b):
            rec = agg["memories"].get(side) or {}
            s = rec.get("sessions")
            if isinstance(s, int) and not isinstance(s, bool) and s >= 0:
                counts[side] = s
        total = agg["sessions"]["count"]
        confident = isinstance(total, int) and total >= _EVIDENCE_USAGE_MIN_SESSIONS
        return counts, confident
    except Exception:
        return counts, False


def _suggest_verdict(
    a: str, b: str, drift: Dict[str, int], newer: Optional[str]
) -> Tuple[str, Optional[str], str]:
    """``(suggested, winner_or_None, reason)`` — deterministic, evidence-only prefill.

    STRICTLY the resolve skill's own verdict names plus the explicit ``abstain`` (no
    second taxonomy). The ONE mechanical rule: when exactly one side cites drifted code
    and git freshness does not contradict it (the clean side is the newer edit, or
    freshness is unknown), suggest ``keep_one`` with the clean side as winner. Everything
    else — both stale, neither stale, signals disagreeing — is ``abstain``: merge /
    scope_both / not_conflicting are CONTENT judgments no drift/freshness arithmetic can
    make, so the card never fakes one. A suggestion is a prefill for the human, never
    auto-applied (accept-prefill stays gated behind a dated owner decision).
    """
    stale_sides = [s for s in (a, b) if s in drift]
    if len(stale_sides) == 1:
        loser = stale_sides[0]
        winner = b if loser == a else a
        if newer in (None, winner):
            return (
                "keep_one",
                winner,
                f"{loser} alone cites drifted code"
                + (f" and {winner} is the fresher edit" if newer == winner else ""),
            )
        return (
            _ABSTAIN,
            None,
            f"signals disagree: {loser} cites drifted code but is the fresher edit",
        )
    if len(stale_sides) == 2:
        return _ABSTAIN, None, "both sides cite drifted code — re-read both"
    return _ABSTAIN, None, "no drift asymmetry — this is a content judgment"


def pair_evidence(
    a: str,
    b: str,
    memory_dir: str,
    repo_root: Optional[str],
    *,
    index_dir: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    declared_by: Optional[List[str]] = None,
) -> dict:
    """The TMB-1 evidence card for ONE pair — deterministic, read-only, zero persisted state.

    ``{"age_commits", "newer", "epochs", "drift", "usage", "usage_confident",
    "suggested", "suggested_winner", "reason"}``. Every leg degrades to an honest
    unknown (``None``/empty) rather than guessing; with no ``repo_root`` the git legs
    are skipped entirely. Cold path only — callers are ``describe()``/``--list`` and
    the resolve MCP tool's inbox, never a SessionStart producer or the hot path.
    Never raises.
    """
    card = {
        "age_commits": None,
        "newer": None,
        "epochs": {},
        "drift": {},
        "usage": {},
        "usage_confident": False,
        "suggested": _ABSTAIN,
        "suggested_winner": None,
        "reason": "",
    }
    try:
        if declared_by is None:
            declared_by = pair_edge_state(memory_dir, a, b, repo_root=repo_root)["declared_by"]
        other_of = {a: b, b: a}
        if repo_root:
            card["age_commits"] = _edge_birth_commits_ago(
                declared_by, other_of, memory_dir, repo_root
            )
            card["newer"], card["epochs"] = _git_newer_side(a, b, memory_dir, repo_root)
        card["drift"] = _drift_evidence(a, b, memory_dir, index_dir)
        card["usage"], card["usage_confident"] = _usage_evidence(a, b, telemetry_dir)
        card["suggested"], card["suggested_winner"], card["reason"] = _suggest_verdict(
            a, b, card["drift"], card["newer"]
        )
        return card
    except Exception:
        return card


def render_pair_evidence(a: str, b: str, card: dict) -> List[str]:
    """The card as its two deterministic listing lines (evidence + suggested)."""
    bits: List[str] = []
    age = card.get("age_commits")
    bits.append(
        f"age: born {age} commit(s) ago"
        if isinstance(age, int)
        else "age: unknown (uncommitted, shallow/rewritten history, or proposal-only)"
    )
    newer = card.get("newer")
    bits.append(f"git-newer: {newer}" if newer else "git-newer: unknown")
    drift = card.get("drift") or {}
    if drift:
        bits.append(
            "drift: "
            + ", ".join(f"{s} cites {n} changed file(s)" for s, n in sorted(drift.items()))
        )
    else:
        bits.append("drift: none cached")
    usage = card.get("usage") or {}
    if card.get("usage_confident"):
        bits.append(
            "usage: " + " / ".join(f"{s} {usage.get(s, 0)} session(s)" for s in sorted(usage))
        )
    else:
        bits.append(
            f"usage: withheld (fewer than {_EVIDENCE_USAGE_MIN_SESSIONS} sessions logged)"
        )
    lines = [f"      evidence: {' · '.join(bits)}"]
    suggested = card.get("suggested") or _ABSTAIN
    winner = card.get("suggested_winner")
    verdict = f"{suggested} (winner: {winner})" if winner else suggested
    reason = card.get("reason") or ""
    lines.append(
        f"      suggested: {verdict} — {reason}; a prefill for your judgment, never auto-applied"
    )
    return lines


def _drop_declarations(
    memory_dir: str, declared_by: List[str], other_of: dict, repo_root: Optional[str]
) -> Tuple[Optional[str], Dict[str, str]]:
    """Remove the pair's settled ``contradicts:`` declarations, capture-first.

    Returns ``(error_or_None, restored_bytes_by_path)`` — on a mid-chain failure the
    already-changed files are restored (COR-16) before the error returns; the captures
    are returned so a LATER chain step's failure can restore them too.
    """
    from .links import remove_typed_relation
    from .provenance import restore_file_bytes

    captured: Dict[str, str] = {}
    changed: List[str] = []
    for side in declared_by:
        path = os.path.join(memory_dir, f"{side}.md")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                captured[path] = fh.read()
        except Exception as exc:
            return f"could not read {side}.md before editing it: {exc}", {}
        r = remove_typed_relation(path, "contradicts", other_of[side])
        if r.get("error"):
            err = f"dropping the contradicts declaration on {side}.md failed: {r['error']}"
            for p in changed:  # restore the earlier drop(s), byte-exact
                undo_err = restore_file_bytes(p, captured[p], memory_dir, repo_root)
                if undo_err:
                    err += f" — AND restoring {os.path.basename(p)} failed ({undo_err}); restore it from git"
                else:
                    err += f" — {os.path.basename(p)} was rolled back"
            return err, {}
        if r.get("changed"):
            changed.append(path)
    return None, {p: captured[p] for p in changed}


def apply_resolve_verdict(
    memory_dir: str,
    repo_root: Optional[str],
    verdict: str,
    *,
    winner: Optional[str] = None,
    loser: Optional[str] = None,
    a: Optional[str] = None,
    b: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
    prefill: Optional[str] = None,
) -> dict:
    """Execute ONE per-pair human verdict — the /hippo:resolve skill's Step 2, as an
    engine call (the resolve tool's write half). Per-item by construction: one pair,
    one verdict, per call; nothing here ever auto-picks a winner.

      - ``keep_one`` (winner=, loser=): drop the settled ``contradicts`` declaration(s),
        then ``semantic_reverify(loser, "demote", superseded_by=winner)`` — the shipped
        demote+supersede chain. Two-write COR-16 discipline: a refused demote rolls the
        declaration drop back out byte-exact.
      - ``merge`` (winner=survivor, loser=): the SAME chain, rendered AFTER the agent
        folded the loser's unique content into the survivor (the demote-in-place merge
        ending — the supersedes pointer stays queryable, nothing is deleted).
      - ``scope_both`` (a=, b=): rendered AFTER the agent edited both bodies to name
        their scopes; drops the declaration(s) (multi-file drops roll back on a partial
        failure). A proposal-only pair has nothing to drop — the verdict lands in the
        dismiss ledger instead (the listing's own guidance for scoped proposals).
      - ``not_conflicting`` (a=, b=): the ONE corpus-preserving verdict — the per-clone
        ledger via ``mark_not_conflicting``; files and edge stay untouched.

    ``prefill`` (TMB-1, optional): the evidence card's suggested verdict as the CALLER saw
    it when rendering this judgment — one of the four verdict names or ``"abstain"``
    (anything else records as ``None``, honest over guessed). Each of the four paths, on
    success, appends one ``{pair, verdict, prefill}`` record to the per-clone ledger's
    additive ``verdicts`` key (``_log_verdict`` — the same file as the dismiss records,
    no sibling ledger, ``_RECONSOLIDATION_OUTCOMES`` untouched), so prefill-vs-choice
    agreement is auditable later. Capture only: nothing accepts a prefill on the human's
    behalf.

    Returns ``{"verdict", "pair", "applied", "error", "detail", "prefill"}``. Never raises.
    """
    valid_prefills = set(_VERDICT_NAMES) | {_ABSTAIN}
    prefill = prefill if prefill in valid_prefills else None
    result = {
        "verdict": verdict,
        "pair": None,
        "applied": False,
        "error": None,
        "detail": [],
        "prefill": prefill,
    }
    try:
        if verdict in ("keep_one", "merge"):
            first, second = (winner or "").strip(), (loser or "").strip()
            label = ("winner", "loser")
        else:
            first, second = (a or "").strip(), (b or "").strip()
            label = ("a", "b")
        if not first or not second:
            result["error"] = f"verdict {verdict!r} needs both {label[0]}= and {label[1]}="
            return result
        if first == second:
            result["error"] = "a memory cannot conflict with itself"
            return result
        result["pair"] = sorted((first, second))

        if verdict == "not_conflicting":
            r = mark_not_conflicting(first, second, repo_root or "")
            if not r["recorded"]:
                result["error"] = r["error"]
                return result
            result["applied"] = True
            result["detail"].append(f"ledger: {r['ledger']}")
            _log_verdict(repo_root or "", tuple(result["pair"]), verdict, prefill)
            return result

        for side in (first, second):
            if not os.path.isfile(os.path.join(memory_dir, f"{side}.md")):
                result["error"] = f"memory not found: {side}.md"
                return result
        state = pair_edge_state(memory_dir, first, second, repo_root=repo_root)
        if not state["declared_by"] and not state["proposed"]:
            result["error"] = (
                "no contradicts edge is declared between these two (and no dream "
                "proposal is live) — nothing to resolve; declare the edge or pick the "
                "right pair from action='inbox'"
            )
            return result
        other_of = {first: second, second: first}

        if verdict == "scope_both":
            if not state["declared_by"]:
                # Proposal-only: nothing declared to drop — scoping ends in a dismiss.
                r = mark_not_conflicting(first, second, repo_root or "")
                if not r["recorded"]:
                    result["error"] = r["error"]
                    return result
                result["applied"] = True
                result["detail"].append(
                    "proposal-only pair: recorded in the dismiss ledger (both stand as scoped)"
                )
                _log_verdict(repo_root or "", tuple(result["pair"]), verdict, prefill)
                return result
            err, _captures = _drop_declarations(
                memory_dir, state["declared_by"], other_of, repo_root
            )
            if err:
                result["error"] = err
                return result
            result["applied"] = True
            result["detail"].append(
                "contradicts declaration dropped on: " + ", ".join(state["declared_by"])
            )
            _log_verdict(repo_root or "", tuple(result["pair"]), verdict, prefill)
            return result

        # keep_one / merge — the two-write chain.
        err, captures = _drop_declarations(memory_dir, state["declared_by"], other_of, repo_root)
        if err:
            result["error"] = err
            return result
        from .reconsolidate import semantic_reverify

        rv = semantic_reverify(
            second, "demote", memory_dir, repo_root, telemetry_dir=telemetry_dir,
            superseded_by=first,
        )
        if rv.get("error"):
            # COR-16: the declaration drop (write #1) must come back out when the
            # demote+supersede chain (write #2, itself internally rolled back) refuses.
            from .provenance import restore_file_bytes

            result["error"] = f"demote+supersede refused: {rv['error']}"
            for path, original in captures.items():
                undo_err = restore_file_bytes(path, original, memory_dir, repo_root)
                if undo_err:
                    result["error"] += (
                        f" — AND restoring {os.path.basename(path)} failed ({undo_err}): "
                        "its contradicts declaration is gone without the supersede; "
                        "restore it from git"
                    )
                else:
                    result["error"] += (
                        f" — the declaration drop on {os.path.basename(path)} was rolled back"
                    )
            return result
        result["applied"] = True
        if state["declared_by"]:
            result["detail"].append(
                "contradicts declaration dropped on: " + ", ".join(state["declared_by"])
            )
        result["detail"].append(
            f"{second} demoted (invalid_after {rv.get('invalid_after') or 'set'}); "
            f"{first} now supersedes it"
        )
        _log_verdict(repo_root or "", tuple(result["pair"]), verdict, prefill)
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result


def _description_of(memory_dir: str, name: str) -> str:
    """The memory's one-line description, or ``""`` — display-only, never raises."""
    try:
        from .build_index import extract_description

        path = os.path.join(memory_dir, f"{name}.md")
        with open(path, "r", encoding="utf-8") as fh:
            return extract_description(fh.read()).strip()
    except Exception:
        return ""


def describe(
    memory_dir: Optional[str] = None,
    *,
    index_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
    telemetry_dir: Optional[str] = None,
) -> str:
    """Human-readable inbox listing for the /hippo:resolve skill. Empty is fine."""
    if memory_dir is None:
        from .provenance import resolve_dirs

        memory_dir, resolved_root = resolve_dirs()
        repo_root = repo_root or resolved_root
    inbox = unresolved_contradictions(
        memory_dir, index_dir=index_dir, repo_root=repo_root, telemetry_dir=telemetry_dir
    )
    if not inbox:
        return (
            "Contradiction inbox is empty — no unresolved `contradicts` pairs in this "
            "corpus (pairs this clone marked not-conflicting stay dismissed)."
        )
    lines = [
        f"{len(inbox)} unresolved contradiction pair(s) — render ONE verdict per pair "
        "(nothing auto-picks a winner):",
        "",
    ]
    any_proposed = False
    for item in inbox:
        a, b = item["pair"]
        if item.get("proposed"):
            any_proposed = True
            cof = item.get("cofire")
            cof_s = f", cofire {cof:.2f}" if isinstance(cof, (int, float)) else ""
            lines.append(f"  • {a} ⇄ {b}  (PROPOSED by dream --contradictions{cof_s} — no edge declared yet)")
            if item.get("reason"):
                lines.append(f"      model's rationale: {item['reason']}")
        else:
            lines.append(f"  • {a} ⇄ {b}  (declared by: {', '.join(item['declared_by'])})")
        for side in (a, b):
            desc = _description_of(memory_dir, side)
            if desc:
                lines.append(f"      {side}: {desc}")
        # TMB-1: the evidence card — git-mined here in the cold listing, never at
        # SessionStart (the producer renders the inbox without these lines, pinned).
        card = pair_evidence(
            a, b, memory_dir, repo_root,
            index_dir=index_dir, telemetry_dir=telemetry_dir,
            declared_by=item.get("declared_by"),
        )
        lines.extend(render_pair_evidence(a, b, card))
    if any_proposed:
        lines.extend(
            [
                "",
                "PROPOSED pairs are LLM candidates, not declarations — read both files and "
                "render the same verdicts:",
                "  · genuine conflict, one side wins → the usual keep-A-supersede-B flow (the "
                "supersedes edge clears the proposal);",
                "  · genuine conflict worth keeping visible → declare it: add `contradicts: "
                "[<other>]` to one side's frontmatter (it becomes an ordinary declared pair);",
                "  · merged/retired a side → the proposal clears itself;",
                "  · scoped both, or not actually a conflict → `--dismiss <a> <b>` (both stand "
                "as written; the proposal stops appearing on this clone).",
            ]
        )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Contradiction inbox (GOV-1): list unresolved contradicts pairs, or "
        "record the one corpus-preserving verdict (mark a pair not-conflicting)."
    )
    parser.add_argument(
        "--list", action="store_true", help="list the unresolved inbox (the default action)"
    )
    parser.add_argument(
        "--dismiss",
        nargs=2,
        metavar=("NAME_A", "NAME_B"),
        default=None,
        help="mark the pair not-conflicting in this clone's ledger (the ONLY verdict that "
        "does not edit the corpus)",
    )
    parser.add_argument(
        "--prefill",
        choices=sorted(_VERDICT_NAMES) + [_ABSTAIN],
        default=None,
        help="TMB-1 (with --dismiss): the evidence card's suggested verdict as you saw it, "
        "recorded next to your choice in the per-clone ledger — capture only, never "
        "auto-applied",
    )
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--index-dir", default=None)
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--telemetry-dir", default=None)
    args = parser.parse_args(argv)
    try:
        memory_dir, repo_root = args.memory_dir, args.repo_root
        if memory_dir is None or repo_root is None:
            from .provenance import resolve_dirs

            md, rr = resolve_dirs()
            memory_dir = memory_dir or md
            repo_root = repo_root or rr
        if args.dismiss:
            res = mark_not_conflicting(args.dismiss[0], args.dismiss[1], repo_root)
            if res["recorded"]:
                # TMB-1: the CLI dismiss is the not_conflicting verdict path — record the
                # prefill next to the choice like the engine's other three paths do.
                _log_verdict(repo_root, tuple(res["pair"]), "not_conflicting", args.prefill)
                print(
                    f"recorded : {res['pair'][0]} ⇄ {res['pair'][1]} marked not-conflicting "
                    "(per-clone ledger; the corpus and the edge are untouched)"
                )
            else:
                print(f"error    : {res['error']}")
            return 0
        print(
            describe(
                memory_dir,
                index_dir=args.index_dir,
                repo_root=repo_root,
                telemetry_dir=args.telemetry_dir,
            )
        )
        return 0
    except Exception as exc:  # never raise out of the CLI — mirror recall_view's discipline
        print(f"resolve view unavailable: {exc}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
