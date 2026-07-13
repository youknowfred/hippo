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
in recall imports this module — zero hot-path cost (inv6). Read-only over the corpus: the
ONLY file this module ever writes is the ledger.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Optional, Set, Tuple

_LEDGER_PREFIX = ".resolve-ledger-"


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


def read_resolved(repo_root: str) -> Set[Tuple[str, str]]:
    """Pairs this clone marked not-conflicting; ``set()`` on no ledger/no data dir. Never raises."""
    try:
        path = ledger_path(repo_root)
        if not path or not os.path.isfile(path):
            return set()
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        pairs = data.get("resolved") if isinstance(data, dict) else None
        out: Set[Tuple[str, str]] = set()
        for p in pairs or []:
            if isinstance(p, list) and len(p) == 2 and all(isinstance(x, str) for x in p):
                out.add(_canonical_pair(p[0], p[1]))
        return out
    except Exception:
        return set()


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
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"resolved": sorted(list(p) for p in resolved)}, fh, indent=0)
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
