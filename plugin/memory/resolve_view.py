"""GOV-1: the contradiction inbox + /hippo:resolve — a standing, drainable conflict queue.

A ``contradicts`` typed edge deliberately demotes NEITHER side (GRA-4: "one of these is
wrong, VERIFY" — not "this one lost"), so its only surface was the hot-path annotation on a
pointer, which appears IF both sides co-surface in one recall. A live conflict ("we use X"
vs "we stopped using X") could therefore sit unresolved forever while the model keeps
getting injected both sides. This module gives every unresolved pair ONE standing queue:

- ``unresolved_contradictions`` enumerates ALL corpus-wide ``contradicts`` pairs (the
  ``LinkGraph.all_typed_edges`` primitive) minus this clone's resolved ledger.
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


def unresolved_contradictions(
    memory_dir: str, *, index_dir: Optional[str] = None, repo_root: Optional[str] = None
) -> List[dict]:
    """Every live ``contradicts`` pair minus this clone's resolved ledger — the inbox.

    Sorted ``[{"pair": [a, b], "declared_by": [stems]}]`` where ``pair`` is the canonical
    (order-free) identity and ``declared_by`` names which side(s) carry the frontmatter
    declaration — the file a corpus-mutating verdict edits. The ledger is only subtracted
    when ``repo_root`` is known (it keys the per-clone file); a caller without one gets the
    full corpus-wide enumeration. Empty is fine. Never raises.
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
        return [
            {"pair": list(pair), "declared_by": sorted(srcs)}
            for pair, srcs in sorted(declared.items())
            if pair not in resolved
        ]
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
) -> str:
    """Human-readable inbox listing for the /hippo:resolve skill. Empty is fine."""
    if memory_dir is None:
        from .provenance import resolve_dirs

        memory_dir, resolved_root = resolve_dirs()
        repo_root = repo_root or resolved_root
    inbox = unresolved_contradictions(memory_dir, index_dir=index_dir, repo_root=repo_root)
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
    for item in inbox:
        a, b = item["pair"]
        lines.append(f"  • {a} ⇄ {b}  (declared by: {', '.join(item['declared_by'])})")
        for side in (a, b):
            desc = _description_of(memory_dir, side)
            if desc:
                lines.append(f"      {side}: {desc}")
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
        print(describe(memory_dir, index_dir=args.index_dir, repo_root=repo_root))
        return 0
    except Exception as exc:  # never raise out of the CLI — mirror recall_view's discipline
        print(f"resolve view unavailable: {exc}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
