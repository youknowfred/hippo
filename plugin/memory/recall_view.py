"""INT-1: /hippo:recall — the read-side verb.

``memory.recall`` is the hot-path engine: structured hits (``recall.recall``) plus the
injection string (``recall.format_results``) the SILENT ``UserPromptSubmit`` hook emits. This
module is the DELIBERATE, human-facing read entry point the ``/hippo:recall`` skill wraps —
the answer to "what do you remember about X", "list what you know here", and "why was that
injected", questions the invisible-by-design hook cannot answer.

It REUSES ``recall.recall()`` verbatim — it never forks the ranking. The same fusion, floor,
knee cutoff, graph expansion, and salience blend the hook would apply produce these hits; the
only thing added is presentation: each hit is enriched with the memory's ``type``, a staleness
flag (RET-6's ``stale_banner``), and its inbound/outbound graph neighbors, then rendered as a
human-readable listing. ``--list-by-type`` dumps the whole corpus grouped by type (a map of
what is known here), read straight off the corpus files with no query.

Read-only: it never writes the corpus, the index, or the ledgers. It does NOT route through
``recall.main()``, so a deliberate listing logs no episode/recall event (a human browsing the
corpus is not a recall the capture pass should later replay). ``main()`` never raises — it
degrades to a plain message, mirroring ``recall.py``'s own never-raise hook discipline.
"""

from __future__ import annotations

import os
from typing import List, Optional, Set, Tuple

from .build_index import default_index_dir, extract_description
from .provenance import _iter_memory_files, parse_frontmatter, resolve_dirs
from .recall import DEFAULT_K, recall

# Canonical floor-taxonomy order (mirrors new_memory.VALID_TYPES) so --list-by-type reads
# user → feedback → project → reference; any unknown type sorts alphabetically after.
_TYPE_ORDER = ("user", "feedback", "project", "reference")


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return ""


def _memory_type(text: str) -> str:
    """The memory's declared type (``metadata.type``, falling back to a top-level ``type``)."""
    fm = parse_frontmatter(text)
    md = fm.get("metadata")
    if isinstance(md, dict) and md.get("type"):
        return str(md.get("type"))
    return str(fm.get("type") or "")


def _memory_origin(text: str) -> str:
    """The promote-time origin stamp (``metadata.origin``, RCH-1) — "" when never promoted.

    Same both-schema read as ``_memory_type``. Display-only provenance: a promoted memory
    answers "where was this learned" right in the recall view.
    """
    fm = parse_frontmatter(text)
    md = fm.get("metadata")
    if isinstance(md, dict) and md.get("origin"):
        return str(md.get("origin"))
    return str(fm.get("origin") or "")


def _name_from_path(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def _load_graph(memory_dir: str, index_dir: Optional[str]):
    """A best-effort ``LinkGraph`` for neighbor lookups; ``None`` if it can't be built."""
    try:
        from .links import build_graph

        return build_graph(memory_dir, index_dir or default_index_dir(memory_dir))
    except Exception:
        return None


def _neighbors(graph, name: str) -> Tuple[List[str], List[str]]:
    """``(outbound, inbound)`` neighbor stems for ``name`` — ``([], [])`` when unknown."""
    if graph is None:
        return [], []
    try:
        return sorted(graph.outbound(name)), sorted(graph.inbound(name))
    except Exception:
        return [], []


def _cross_encoder_rerank(query: str, hits: List[dict]) -> List[dict]:
    """RCL-5: re-order ``hits`` by a local cross-encoder's joint query/description read.

    An EXPLICIT-SURFACE-ONLY precision lever (never the UserPromptSubmit hot path — no p95
    budget to protect here). T2 guard: ``corpus == "rule"`` pointers are excluded from the
    rerank and re-attached at the tail in their ORIGINAL relative order — a rule pointer has
    no query-vs-description joint signal to rerank on and must never be reordered among
    corpus hits. Reorders ONLY; never mutates a hit's own ``score``/``rank`` (COR-8: those
    stay the true fused-recall values, not a fabricated cross-encoder number on a different
    scale). Degrades to the ORIGINAL order on any failure — no cached model, fastembed
    unavailable, any exception — never downloads, never raises.
    """
    rule_hits = [h for h in hits if h.get("corpus") == "rule"]
    corpus_hits = [h for h in hits if h.get("corpus") != "rule"]
    if len(corpus_hits) < 2:
        return hits  # nothing meaningful to reorder
    try:
        from .build_index import _get_cross_encoder

        model = _get_cross_encoder(allow_download=False)
        descriptions = [h.get("description") or "" for h in corpus_hits]
        scores = list(model.rerank(query, descriptions))
        order = sorted(range(len(corpus_hits)), key=lambda i: scores[i], reverse=True)
        return [corpus_hits[i] for i in order] + rule_hits
    except Exception:
        return hits


def _abstention_receipt(
    query: str,
    memory_dir: Optional[str],
    index_dir: Optional[str],
    repo_root: Optional[str],
) -> str:
    """GOV-5: WHY nothing surfaced — the near-miss score and the floor it missed, honestly.

    The near-miss COSINES are discarded inside ``recall._dense_rank_rows`` (no ledger keeps
    them), so this recovers them with one direct similarity probe over the already-loaded
    dense matrix — the same ``index.dense @ qvec`` that function computes, minus the floor
    filter. NOT a floors-disabled ``recall()`` re-run: recall emits RRF-FUSED scores
    (~1/60 scale), which are not commensurable with the cosine floor — quoting one against
    the other would be a fabricated comparison (COR-8). Branches, in order of honesty:
    untrusted corpus (withheld ≠ sub-floor), no corpus at all, BM25-only (the match-set IS
    the floor — no cosine to quote), then the dense near-miss.
    """
    from . import trust
    from .recall import _dense_floor, _ensure_index, embed_query

    base = (
        f'No memories cleared the relevance floor for "{query}" — nothing would be '
        "injected for a prompt like this."
    )
    tail = (
        "\nAbstention is a feature (RET-1); if a memory SHOULD answer this, enrich its "
        "description (/hippo:consolidate) or pin it (steer: pin)."
    )
    try:
        gate_root = trust.gate_repo_root(memory_dir, repo_root)
        if gate_root is not None and not trust.is_trusted(gate_root):
            return (
                base + "\nReason: this corpus is UNTRUSTED (SEC-1) — recall is withheld "
                "entirely, nothing was scored at all. Run /hippo:doctor to review and "
                "trust it."
            )
    except Exception:
        pass
    index = _ensure_index(None, memory_dir or "", index_dir)
    if index is None or not len(index.entries):
        return base + "\nReason: no memory corpus/index resolves here at all."
    if not index.dense_ready:
        return (
            base + "\nReason: no memory shares a token with this query (BM25-only corpus "
            "— lexical recall's match-set IS its floor, so there is no near-miss score to "
            "report; the dense model would be needed for one)." + tail
        )
    try:
        qvec = embed_query(query)
        sims = index.dense @ qvec
        entry_rows = {e["row"]: e for e in index.entries if isinstance(e.get("row"), int)}
        best_row = max(entry_rows, key=lambda r: float(sims[r]))
        best_sim, best = float(sims[best_row]), entry_rows[best_row]
    except Exception:
        return (
            base + "\nReason: no memory shares a token with this query, and the dense "
            "model was unavailable for a similarity probe (cold cache?)." + tail
        )
    floor = _dense_floor(index.model)
    if best_sim < floor:
        return (
            base + f"\nReason: best candidate `{best['name']}` scored {best_sim:.3f}, "
            f"below the dense relevance floor {floor:.2f} — the gap is the abstention."
            + tail
        )
    return (
        base + f"\nReason: best candidate `{best['name']}` scored {best_sim:.3f} (≥ floor "
        f"{floor:.2f}) but was filtered at display time — e.g. soft-invalidated 'old' or "
        "its file was deleted mid-session." + tail
    )


def _corpora_note(res: dict) -> str:
    """The one legible sources trailer for an --all-projects run (inv3): what was
    searched, and every skipped source NAMED with why."""
    parts = [f"searched: {', '.join(res['searched']) or '(nothing)'}"]
    for key, reason in (
        ("skipped_untrusted", "untrusted — review + trust via /hippo:doctor there"),
        ("skipped_unavailable", "unavailable (no readable corpus/index)"),
    ):
        names = res.get(key) or []
        if names:
            noun = "corpus" if len(names) == 1 else "corpora"
            parts.append(f"{len(names)} {noun} skipped: {', '.join(names)} — {reason}")
    return "  (" + " · ".join(parts) + ")"


def describe(
    query: str,
    k: int = DEFAULT_K,
    *,
    memory_dir: Optional[str] = None,
    index_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
    why: bool = False,
    all_projects: bool = False,
) -> str:
    """Human-readable answer to "what do you remember about ``query``".

    Runs the SAME ``recall.recall()`` the hook would, then annotates each hit with type,
    staleness, and graph neighbors — plus, always, the typed-edge note and the steer:pin
    echo (GOV-2's legibility contract). ``why=True`` (GOV-5, the /hippo:why receipt) adds
    the winning-backend/salience breakdown per hit, relabels a rule pointer's score as the
    containment it really is, and — on abstention — replaces the generic message with the
    near-miss receipt (sub-floor score + the floor it missed). Abstention (no hit clears
    the relevance floor) is reported as such — a feature, not an error.

    ``all_projects=True`` (RCH-4) swaps the retrieval call for
    ``recall.recall_all_projects`` — the current project's tiers plus every registered,
    per-source-trust-gated local corpus — labels cross-project hits "from <repo>", and
    appends a sources trailer naming everything searched and everything skipped (inv3).
    Explicit surfaces only; the hook path never sets it.
    """
    if memory_dir is None:
        memory_dir, repo_root = resolve_dirs()
    corpora_res: Optional[dict] = None
    if all_projects:
        from .recall import recall_all_projects

        corpora_res = recall_all_projects(
            query, k, memory_dir=memory_dir, index_dir=index_dir, repo_root=repo_root
        )
        hits = corpora_res["hits"]
    else:
        hits = recall(
            query, k, memory_dir=memory_dir, index_dir=index_dir, repo_root=repo_root
        )
    if not hits:
        if why and not all_projects:
            return _abstention_receipt(query, memory_dir, index_dir, repo_root)
        message = (
            f'No memories cleared the relevance floor for "{query}" — nothing would be '
            "injected for a prompt like this. Abstention is a feature (RET-1): an unrelated "
            "or too-thin query surfaces nothing rather than padding out low-signal matches. "
            "Try /hippo:recall --list-by-type to see everything this project knows "
            "(or /hippo:recall --why for the abstention receipt)."
        )
        if corpora_res is not None:
            message += "\n" + _corpora_note(corpora_res)
        return message
    hits = _cross_encoder_rerank(query, hits)
    graph = _load_graph(memory_dir, index_dir)
    out: List[str] = [f'{len(hits)} memory match(es) for "{query}" (most relevant first):', ""]
    for h in hits:
        # recall's ``file`` is a bare basename (build_index stores os.path.basename); rejoin
        # to the corpus dir to read the memory's frontmatter for its type.
        fname = h.get("file") or ""
        name = h.get("name") or _name_from_path(fname)
        # TEA-1/TEA-3: a fused hit carries its own corpus ``root`` (project / user tier /
        # private tier); read its type from THAT dir, not the single project dir — a user-tier
        # basename joined to the project dir would read as "untyped" or collide with a same-named
        # project file. A single-corpus hit has no root and falls back to memory_dir.
        hit_root = h.get("root") or memory_dir
        score = h.get("score")
        via = h.get("via")
        corpus = h.get("corpus")
        if corpus == "rule":
            # RUL-4: a governance-plane pointer, not a memory — no frontmatter type to read
            # (h["file"] is the rule file itself) and no tier provenance to disambiguate.
            tags = ["rule — governance plane, not a memory"]
            if why and isinstance(score, (int, float)):
                # GOV-5: a rule hit's score IS query containment (|q ∩ section| / |q|) —
                # name it that, against its own floor, instead of a generic "relevance".
                from .recall import _rules_hit_floor

                tags.append(f"containment {score:.3f} ≥ floor {_rules_hit_floor():.2f}")
        else:
            hit_text = _read_text(os.path.join(hit_root, fname))
            mtype = _memory_type(hit_text) or "untyped"
            tags = [f"{mtype}"]
            if corpus and corpus != "project":
                # provenance: which corpus this hit came from — the machine-local tiers
                # render as tiers; any other label is a registered repo's basename (RCH-4).
                tags.append(
                    f"{corpus} tier" if corpus in ("user", "private") else f"from {corpus}"
                )
            # RCH-1: the promote-time origin stamp — a lifted memory names the repo (and
            # sha) it was learned in, so a cross-project hit is never mystery knowledge.
            hit_origin = _memory_origin(hit_text)
            if hit_origin:
                tags.append(f"learned in {hit_origin}")
        if isinstance(score, (int, float)) and not (why and corpus == "rule"):
            tags.append(f"relevance {score:.3f}")
        if via == "graph":
            tags.append("via 1-hop link")  # answers "why was this injected" (GRA-1 expansion)
        # GOV-7: the author's confidence tier — display-only provenance, never a ranking
        # input; absence (the default) renders nothing.
        if h.get("confidence"):
            tags.append(str(h["confidence"]))
        # GOV-2: the steer echo — a pinned hit says so (and by how much), always.
        if h.get("steer") == "pin":
            from .recall import _pin_boost

            tags.append(f"pinned ×{_pin_boost():g}")
        # GRA-4: the typed-edge annotation ("superseded by X" / "contradicts X — verify")
        # was emitted but never rendered here — the receipt's "edges traversed" answer.
        if h.get("note"):
            tags.append(h["note"])
        if why:
            backend = h.get("backend")
            if backend and corpus != "rule":
                tags.append(f"won via {backend}")  # which ranking(s) produced this hit
            sal = h.get("salience")
            if isinstance(sal, dict) and sal:
                parts = ", ".join(f"{k2} {v:+.2f}" for k2, v in sorted(sal.items()))
                tags.append(f"salience {parts}")
        if h.get("stale_banner"):
            tags.append("⚠ stale — verify before relying")
        out.append(f"  • {name}  [{' · '.join(tags)}]")
        desc = h.get("description") or ""
        if desc:
            out.append(f"      {desc}")
        outbound, inbound = _neighbors(graph, name)
        if outbound:
            out.append("      → links to: " + ", ".join(outbound))
        if inbound:
            out.append("      ← linked from: " + ", ".join(inbound))
    if corpora_res is not None:
        out.append("")
        out.append(_corpora_note(corpora_res))
    return "\n".join(out)


def list_by_type(*, memory_dir: Optional[str] = None) -> str:
    """The whole corpus grouped by type — a map of what this project knows. No query."""
    if memory_dir is None:
        memory_dir, _ = resolve_dirs()
    buckets: dict = {}
    try:
        paths = list(_iter_memory_files(memory_dir))
    except Exception:
        paths = []
    for path in paths:
        text = _read_text(path)
        mtype = _memory_type(text) or "untyped"
        buckets.setdefault(mtype, []).append((_name_from_path(path), extract_description(text)))
    if not buckets:
        return (
            "This project has no memory corpus yet (nothing under .claude/memory/). "
            "Run /hippo:init to seed one."
        )
    keys = [t for t in _TYPE_ORDER if t in buckets] + sorted(
        t for t in buckets if t not in _TYPE_ORDER
    )
    total = sum(len(v) for v in buckets.values())
    out: List[str] = [f"{total} memories across {len(keys)} type(s):"]
    for t in keys:
        items = sorted(buckets[t])
        out.append("")
        out.append(f"## {t} ({len(items)})")
        for name, desc in items:
            out.append(f"  • {name} — {desc}" if desc else f"  • {name}")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Read-side recall (INT-1): query the corpus, or list it by type."
    )
    parser.add_argument("query", nargs="*", help="what to recall (natural-language)")
    parser.add_argument("-k", type=int, default=DEFAULT_K, help="max matches to show")
    parser.add_argument(
        "--list-by-type",
        action="store_true",
        help="list the whole corpus grouped by type instead of querying",
    )
    parser.add_argument(
        "--why",
        action="store_true",
        help="GOV-5: the recall receipt — per-hit winning backend/edges/salience/steer, "
        "and on abstention the near-miss score vs the floor it missed",
    )
    parser.add_argument(
        "--history",
        default=None,
        metavar="NAME",
        help="RCH-3: replay the supersedes/refines decision chain around a memory as an "
        "ordered narrative (same builder the decision_history MCP tool renders)",
    )
    parser.add_argument(
        "--all-projects",
        action="store_true",
        help="RCH-4: search every registered local corpus alongside this project's tiers "
        "— each source trust-gated at query time, each hit labeled by source repo; "
        "explicit command only, never the hook",
    )
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--index-dir", default=None)
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args(argv)
    try:
        if args.history:
            from .history import render_decision_history

            memory_dir = args.memory_dir
            if memory_dir is None:
                memory_dir, _ = resolve_dirs()
            print(render_decision_history(args.history, memory_dir, args.index_dir))
            return 0
        if args.list_by_type:
            print(list_by_type(memory_dir=args.memory_dir))
            return 0
        query = " ".join(args.query).strip()
        if not query:
            print('usage: recall "<what to recall>"   |   recall --list-by-type')
            return 2
        print(
            describe(
                query,
                args.k,
                memory_dir=args.memory_dir,
                index_dir=args.index_dir,
                repo_root=args.repo_root,
                why=args.why,
                all_projects=args.all_projects,
            )
        )
        return 0
    except Exception as exc:  # never raise out of the CLI — mirror recall.py's discipline
        print(f"recall view unavailable: {exc}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
