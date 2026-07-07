"""Create a recall-ready memory file, right-by-construction (post-trim convention).

``write_memory(name, description, type, body)`` writes a new ``<name>.md`` whose frontmatter
carries the three fields the system depends on — ``name``, ``description`` (the recall hook),
and ``metadata.type`` — then:

  1. discovers related EXISTING memories via ``recall()`` and appends a "Related: [[a]], [[b]]"
     line to the body (GRA-3 — see ``_discover_links``), BEFORE rendering/writing, so the link
     line lands in the file at birth; then
  2. scores the new memory's ``doc_text`` against the persisted index for near-duplicate /
     conflicting EXISTING memories (LIF-2 — see ``_duplicate_neighbors``); anything above the
     similarity threshold rides out on ``result["neighbors"]`` — WARN-ONLY, the write always
     proceeds and the AGENT decides add / update-existing / supersede / skip (``/hippo:new``
     documents the decision flow); then
  3. backfills Tier-1 citation provenance (``cited_paths`` / ``source_commit``) so the new
     memory is born staleness-tracked, and
  4. refreshes the recall index so it is immediately recallable, and
  5. appends a ``MEMORY.md`` floor pointer ONLY when ``type`` is ``user`` or ``feedback``.

``project`` / ``reference`` memories are deliberately NOT added to the floor — they are
recalled on demand (the UserPromptSubmit recall hook + the SessionStart auto-refresh index
them). This is the whole point: new memories never re-bloat the trimmed always-load.

Never silently overwrites an existing file. The floor-pointer write is the ONLY edit to
MEMORY.md; no existing memory BODY is ever modified — link discovery only ever touches the
file being CREATED (the no-bulk-autonomous-sweeps invariant: this is a single-item write, not
an edit to any pre-existing memory).
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

VALID_TYPES = ("user", "feedback", "project", "reference")

# GRA-3: how many related memories new_memory suggests via recall() at write time.
_LINK_DISCOVERY_K = 3

# --------------------------------------------------------------------------- #
# LIF-2: duplicate/conflict detection at write time — thresholds + result cap.
# --------------------------------------------------------------------------- #
# How many above-threshold neighbors ride out on result["neighbors"] (best first). A near-dupe
# has ONE twin in practice; 3 leaves room for the multi-way-drift case without ever flooding
# the CLI warning block.
_DUP_NEIGHBORS_K = 3
# DENSE path: cosine similarity of the new memory's doc_text (embedded via embed_query,
# exactly the vector recall would score this memory's own recall queries with) against the
# persisted description rows. Calibrated on tests/golden_corpus with the real warm
# bge-small-en-v1.5 (full numbers in the LIF-2 commit body): 10 near-duplicate probes
# (lightly-reworded descriptions under new slugs) scored [0.9050, 0.9640] against their
# twins while the corpus's 50 deliberately-DISTINCT memories cross-scored at most 0.7554
# leave-one-out against each other (p95 0.7431) — 0.80 sits inside that gap, biased low
# (warn-only means a borderline false neighbor costs one line of agent judgment; a missed
# real dupe costs a permanently split recall signal).
_DUP_COSINE_THRESHOLD = 0.80
# BM25 fallback: the raw Okapi score is corpus- and length-scaled (no fixed unit), so it is
# NORMALIZED to [~0, ~1] by the query's own self-score (the score a hypothetical doc
# containing exactly the query's tokens would get — see _bm25_dup_scores) before
# thresholding. Calibrated the same way (same commit body): the golden-corpus near-duplicate
# probes normalized to [0.6014, 1.2360] vs a distinct-pair ceiling of 0.3494 (the two
# hermetic fixture corpora's cross-pair ceilings are 0.0 — zero shared content tokens) —
# 0.45 splits the measured gap, again biased below the midpoint for the same
# warn-only asymmetry as the cosine threshold.
_DUP_BM25_THRESHOLD = 0.45

# Which floor section a pointer goes under, by type. project/reference => no floor pointer.
_FLOOR_SECTION_BY_TYPE = {
    "user": "## User",
    "feedback": "## Working Style & Process Feedback",
}


def _title_from_slug(name: str) -> str:
    return name.replace("_", " ").replace("-", " ").strip().title()


def _discover_links(
    name: str, description: str, memory_dir: str, repo_root: Optional[str], k: int
) -> List[str]:
    """Top-``k`` EXISTING memory names related to the new memory, via in-process ``recall()``.

    GRA-3: a snap-in project starts at zero graph edges because no code path ever creates one —
    hand-authored wikilinks accrete over months in a mature corpus, but a fresh install never
    gets there. Running ``recall()`` against the query the new memory would itself be indexed
    under (``"<name words>. <description>"`` — the exact ``doc_text`` shape ``build_index``
    uses, so this asks "what would compete with THIS memory for recall rank?"), BEFORE the file
    exists, surfaces the closest existing neighbors to seed as wikilinks at birth.

    BM25-only by construction: this reuses ``recall()``'s own ``_ensure_index`` implicit-build
    path, which explicitly disables dense for exactly this reason (a write-time call must not
    block on loading a dense model — the hot-path invariant recall's implicit index build
    already honors). Skipped entirely (returns ``[]``) when the corpus is empty/unbuilt/errors —
    never raises, never blocks the write. The new memory's OWN name is excluded defensively
    (it cannot appear in a pre-write index, but a stale/pre-seeded index could theoretically
    carry a same-named stub — belt and suspenders against ever linking a memory to itself).
    """
    try:
        from .recall import recall

        query = f"{name.replace('_', ' ').replace('-', ' ')}. {description}".strip(". ").strip()
        if not query:
            return []
        hits = recall(query, k=k + 1, memory_dir=memory_dir, repo_root=repo_root)
        return [h["name"] for h in hits if h.get("name") != name][:k]
    except Exception:
        return []


def _append_related_line(body: str, related: List[str]) -> str:
    """Append a final ``Related: [[a]], [[b]]`` body line naming ``related`` memory names.

    Additive only — never touches any existing body text, just appends one trailing line (the
    same additive-write discipline provenance backfill and the floor pointer already follow).
    """
    if not related:
        return body
    line = "Related: " + ", ".join(f"[[{r}]]" for r in related)
    body = (body or "").rstrip("\n")
    return f"{body}\n\n{line}\n" if body else f"{line}\n"


def _dup_threshold(default: float) -> float:
    """``HIPPO_DUP_THRESHOLD`` override; malformed/absent -> ``default``. Never raises.

    One env var for BOTH backends (recall's ``_knee_ratio``-style parse): the dense cosine
    and the normalized BM25 ratio are each ~[0, 1] similarity scales, so a single override
    tunes whichever path actually scored this write — no second env var to remember.
    """
    raw = os.environ.get("HIPPO_DUP_THRESHOLD")
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _dense_dup_scores(index, doc_text: str):
    """``[(entry_index, cosine)]`` over DESCRIPTION rows, or ``None`` when dense can't run.

    LIF-2's dense path is deliberately the same machinery recall's ``_dense_rank_rows``
    uses — ``embed_query`` under ``run_bounded`` (offline, never a download; a cold/wiped
    model cache aborts within ``DENSE_QUERY_TIMEOUT_SECS`` instead of blocking the write)
    against the ALREADY-persisted ``dense.npy`` — but with the raw SCORES kept (dup
    detection thresholds on similarity itself, not rank) and no relevance floor (0.80-dupe
    territory is far above RET-1's 0.60 floor anyway). Same preconditions as recall: dense
    disabled/not-ready -> ``None``; a manifest embedded under a DIFFERENT model than the
    currently configured one -> ``None`` (COR-8 — cosine across two embedding spaces is
    noise, not similarity). Description rows only — a body chunk can legitimately overlap a
    neighbor's body without the memories being duplicates of each other.
    """
    from .build_index import DEFAULT_MODEL, DENSE_QUERY_TIMEOUT_SECS, dense_disabled, embed_query, run_bounded

    if dense_disabled() or not index.dense_ready or index.dense is None:
        return None
    if index.model and index.model != DEFAULT_MODEL:
        return None
    try:
        qvec = run_bounded(lambda: embed_query(doc_text, allow_download=False), DENSE_QUERY_TIMEOUT_SECS)
        sims = index.dense @ qvec  # rows are L2-normalized -> dot == cosine
        return [(i, float(sims[e["row"]])) for i, e in enumerate(index.entries)]
    except Exception:  # incl. DenseTimeout -> BM25 fallback, never block/crash the write
        return None


def _bm25_dup_scores(index, doc_text: str):
    """``[(entry_index, normalized bm25)]`` from the manifest's PRF-1 stats, or ``None``.

    The fallback when dense is unavailable/disabled. Reuses recall's postings-walk scorer
    (``_bm25_score_via_postings`` — the exact per-term Okapi formula query time uses)
    against the manifest's precomputed stats; entry doc indices are 0..N-1 in the unified
    PRF-1 doc space, so no ``doc_offset`` translation is needed. Raw BM25 has no fixed
    unit (it grows with query length, corpus size, and idf mass), so each entry's score is
    divided by the query's SELF-SCORE — what a document containing exactly the query's own
    tokens would score, term formula identical, with tokens the corpus has never seen
    assigned the df=0 Okapi idf (``ln((N+0.5)/0.5)``, the same formula
    ``compute_bm25_stats`` applies at df>0). Counting unseen tokens at full idf weight is
    load-bearing: without it, a memory sharing only its few corpus-known tokens with some
    entry would normalize to ~1.0 (false dupe) because its genuinely-novel vocabulary
    contributed nothing to the denominator. ``None`` when the stats are absent (a manifest
    predating PRF-1 can't reach here — the schema gate rebuilds it — so this is pure
    defense) or degenerate (a 1-2 doc corpus's idf mass can be all-zero/negative — no
    honest ratio exists, and saying so beats fabricating one); ``[]`` when the doc_text
    tokenizes to nothing (the check RAN, nothing can match).
    """
    import math

    from .build_index import tokenize
    from .recall import _bm25_score_via_postings

    stats = index.manifest.get("bm25")
    if not isinstance(stats, dict):
        return None
    try:
        k1, b, avgdl, idf = stats["k1"], stats["b"], stats["avgdl"], stats["idf"]
        if not avgdl:
            return None
        q_tokens = tokenize(doc_text)
        if not q_tokens:
            return []
        freqs = {}
        for tok in q_tokens:
            freqs[tok] = freqs.get(tok, 0) + 1
        novel_idf = math.log(len(stats["doc_len"]) + 0.5) - math.log(0.5)
        denom_norm = k1 * (1 - b + b * len(q_tokens) / avgdl)
        self_score = sum(
            idf.get(tok, novel_idf) * (tf * (k1 + 1)) / (tf + denom_norm)
            for tok, tf in freqs.items()
        )
        if self_score <= 0:
            return None
        qset = set(q_tokens)
        matched = [i for i, e in enumerate(index.entries) if qset.intersection(e.get("tokens") or [])]
        scores = _bm25_score_via_postings(q_tokens, stats, matched)
        return [(i, scores[i] / self_score) for i in matched]
    except Exception:
        return None


def _duplicate_neighbors(name: str, rendered: str, memory_dir: str):
    """Top-``_DUP_NEIGHBORS_K`` above-threshold EXISTING neighbors -> ``(neighbors, note)``.

    LIF-2: creation used to refuse only exact filename collisions while an embedding index
    sat right there — months-old corpora accumulate near-dupes (splitting recall hits) and
    contradictions with no detection anywhere. This scores the new memory's ``doc_text``
    (via ``memory_doc_text`` on the SAME rendered text that lands on disk — byte-identical
    to what the next index build would derive) against the PERSISTED index: dense cosine
    when available, normalized BM25 otherwise (each with its own calibrated threshold —
    see the module constants). Neighbors are ``{name, score, description}``, best first.

    WARN-ONLY by contract: the caller writes the file regardless — the roadmap's
    no-autonomous-rejection acceptance bar — and the AGENT routes the decision
    (add / update-existing / supersede / skip, per ``/hippo:new``).

    Degradation is legible, never fatal: no index / empty index / unscorable stats each
    yield ``([], "duplicate check skipped: <reason>")`` — a machine-readable note the
    result dict carries so a silent no-warning write is distinguishable from a genuinely
    clean one (the every-silent-fallback-gains-a-signal invariant). Never raises, never
    downloads, never builds an index of its own (the GRA-3 discovery pass usually just
    built the BM25 one in-process; when it didn't — ``--links``/``--no-links``, or a
    first-ever memory — the note says so instead).
    """
    try:
        from .build_index import default_index_dir, entry_description, load_index, memory_doc_text

        index = load_index(default_index_dir(memory_dir))
        if index is None:
            return [], "duplicate check skipped: no index"
        if not index.entries:
            return [], "duplicate check skipped: empty index"
        doc_text = memory_doc_text(name, rendered)
        scored = _dense_dup_scores(index, doc_text)
        default_threshold = _DUP_COSINE_THRESHOLD
        if scored is None:
            scored = _bm25_dup_scores(index, doc_text)
            default_threshold = _DUP_BM25_THRESHOLD
        if scored is None:
            return [], "duplicate check skipped: unscorable index"
        threshold = _dup_threshold(default_threshold)
        # Own-name exclusion mirrors _discover_links: a pre-write index cannot contain the
        # new memory, but a stale index carrying a same-named stub must never self-match.
        hits = sorted(
            ((i, s) for i, s in scored if s >= threshold and index.entries[i].get("name") != name),
            key=lambda pair: pair[1],
            reverse=True,
        )
        neighbors = [
            {
                "name": index.entries[i]["name"],
                "score": round(float(s), 4),
                "description": entry_description(index.entries[i]).strip(),
            }
            for i, s in hits[:_DUP_NEIGHBORS_K]
        ]
        return neighbors, None
    except Exception:
        return [], "duplicate check skipped: error"


def _render_frontmatter(name: str, description: str, mtype: str, body: str) -> str:
    """Recall-ready frontmatter: top-level name + description (indexed), metadata.type.

    ``description`` is JSON-quoted so any colon/character is valid YAML (the recall index
    reads ``description`` via yaml.safe_load).
    """
    lines = [
        "---",
        f"name: {name}",
        f"description: {json.dumps(description)}",
        "metadata:",
        f"  type: {mtype}",
        "---",
        "",
        body.rstrip("\n") + "\n" if body else "",
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def _append_floor_pointer(
    memory_dir: str, section_header: str, name: str, title: str, hook: str
) -> bool:
    """Insert ``- [title](name.md) — hook`` at the END of ``section_header`` in MEMORY.md.

    Returns True if the pointer was added. Never raises. Idempotent: a pointer to the same
    ``name.md`` already present is left as-is (returns False).
    """
    path = os.path.join(memory_dir, "MEMORY.md")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except Exception:
        return False

    link = f"]({name}.md)"
    if any(link in ln for ln in lines):
        return False  # already pointed-to — don't duplicate

    # Find the section header, then the end of its block (next "## " or EOF).
    start = next((i for i, ln in enumerate(lines) if ln.strip() == section_header), None)
    if start is None:
        return False
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip().startswith("## "):
            end = j
            break
    # Insertion point = after the last non-blank line within the block.
    insert = start + 1
    for j in range(start + 1, end):
        if lines[j].strip():
            insert = j + 1

    pointer = f"- [{title}]({name}.md) — {hook}".rstrip()
    lines.insert(insert, pointer)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        return True
    except Exception:
        return False


def write_memory(
    name: str,
    description: str,
    type: str,
    body: str = "",
    *,
    memory_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
    title: Optional[str] = None,
    hook: Optional[str] = None,
    links: Optional[List[str]] = None,
    no_links: bool = False,
) -> dict:
    """Create a recall-ready memory file. Returns a small result dict.

    - Validates ``type`` ∈ VALID_TYPES.
    - Refuses to overwrite an existing ``<name>.md`` (``created=False``, ``error`` set).
    - Discovers related EXISTING memories (GRA-3) and appends a "Related: [[a]], [[b]]" body
      line BEFORE rendering — ``links`` (explicit names) OVERRIDES discovery entirely;
      ``no_links=True`` suppresses it entirely (neither discovery nor an explicit ``links``
      list is applied). ``result["related"]`` reports whatever list was actually used (``[]``
      when suppressed/empty-corpus/no hits).
    - Detects near-duplicate/conflicting EXISTING memories (LIF-2): ``result["neighbors"]``
      carries ``[{name, score, description}]`` above the calibrated threshold (dense cosine
      when the persisted index is dense, normalized BM25 otherwise; ``HIPPO_DUP_THRESHOLD``
      overrides). WARN-ONLY — creation NEVER auto-rejects on a neighbor; the agent decides
      add / update-existing / supersede / skip (see ``/hippo:new``). When the check cannot
      run at all (no index yet), ``result["note"]`` names why — no silent no-warning path.
    - Backfills provenance + refreshes the recall index (best-effort; never fatal).
    - Adds a MEMORY.md floor pointer ONLY for ``user`` / ``feedback``.
    - Scans the rendered text for secret-looking patterns (SEC-2) and, on a match, populates
      ``warnings`` — WARN-not-block: the write still happens; the agent decides what to do.
    """
    result = {
        "created": False,
        "path": None,
        "floor_pointer_added": False,
        "indexed": False,
        "related": [],
        "neighbors": [],
        "note": None,
        "warnings": [],
        "error": None,
    }
    if type not in VALID_TYPES:
        result["error"] = f"invalid type {type!r} (expected one of {VALID_TYPES})"
        return result
    # Name must be a bare slug — a path separator (or "..") would write the file OUTSIDE
    # memory_dir, where neither the index nor the floor would ever find it (a silent hole).
    if not name or os.path.basename(name) != name:
        result["error"] = f"invalid name {name!r} (must be a bare slug, no path separators)"
        return result

    from .provenance import build_repo_file_index, resolve_dirs

    md, repo = resolve_dirs()
    memory_dir = memory_dir or md
    repo_root = repo_root or repo

    # --- GRA-3: link discovery, BEFORE rendering (so it lands in the body at birth). ---
    # This runs against the EXISTING corpus index only — the new file does not exist on disk
    # yet, so recall() cannot possibly self-match. ``--links`` (explicit) OVERRIDES discovery
    # outright (no recall() call at all — an agent-supplied list is authoritative);
    # ``--no-links`` suppresses BOTH paths. Ordering matters here: this must happen before the
    # provenance backfill / index refresh below so cited_paths/staleness computation sees the
    # SAME rendered text that lands on disk, not a version missing its Related line.
    related: List[str] = []
    if no_links:
        related = []
    elif links is not None:
        related = [ln for ln in links if ln and ln != name]
    else:
        related = _discover_links(name, description, memory_dir, repo_root, _LINK_DISCOVERY_K)
    result["related"] = related
    body = _append_related_line(body, related)

    path = os.path.join(memory_dir, f"{name}.md")
    rendered = _render_frontmatter(name, description, type, body)

    # --- LIF-2: duplicate/conflict detection, BEFORE the write + index refresh. ---
    # Ordering is load-bearing twice over: (a) it must score against the PRE-refresh
    # persisted index — after refresh_index below, the new memory would be IN the index and
    # match itself at ~1.0; (b) it runs on the exact ``rendered`` text (via memory_doc_text)
    # so the doc_text scored here is byte-identical to what the next build indexes. Warn-only:
    # whatever comes back, the exclusive-create below proceeds unconditionally.
    result["neighbors"], result["note"] = _duplicate_neighbors(name, rendered, memory_dir)

    try:
        os.makedirs(memory_dir, exist_ok=True)
        # "x" = exclusive create: atomic no-overwrite (no TOCTOU window between check + write).
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(rendered)
        result["created"] = True
        result["path"] = path
    except FileExistsError:
        result["error"] = f"{name}.md already exists — refusing to overwrite"
        return result
    except Exception as exc:
        result["error"] = f"write failed: {exc}"
        return result

    # Secret-pattern lint (SEC-2): scan the RENDERED text (frontmatter + body) for
    # secret-looking content. This WARNS, it does NOT block — the write already happened and
    # is kept; the warnings ride out on the result dict so the agent decides what to do next
    # (report-then-act, agent-gated). Never fatal — a scan failure just yields no warnings.
    try:
        from .secrets import scan_with_remediation

        result["warnings"] = scan_with_remediation(rendered)
    except Exception:
        pass

    # 1. Provenance backfill (best-effort — a new file with no code citations is fine).
    try:
        from .provenance import backfill_file

        repo_files, basename_index = build_repo_file_index(repo_root)
        backfill_file(path, repo_root, repo_files, basename_index)
    except Exception:
        pass

    # 2. Refresh the recall index so the memory is immediately recallable.
    try:
        from .build_index import refresh_index

        refresh_index(memory_dir)
        result["indexed"] = True
    except Exception:
        pass

    # 3. Floor pointer ONLY for user / feedback (project / reference are recalled on demand).
    section = _FLOOR_SECTION_BY_TYPE.get(type)
    if section is not None:
        result["floor_pointer_added"] = _append_floor_pointer(
            memory_dir, section, name, title or _title_from_slug(name), hook or description
        )
    return result


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Create a recall-ready memory file.")
    parser.add_argument("name", help="kebab/snake slug (also the filename stem)")
    parser.add_argument("description", help="one-line recall hook (indexed for recall)")
    parser.add_argument("--type", required=True, choices=VALID_TYPES)
    parser.add_argument("--body", default="", help="memory body text")
    parser.add_argument("--title", default=None, help="floor-pointer link text (user/feedback only)")
    parser.add_argument("--hook", default=None, help="floor-pointer trailing note (user/feedback only)")
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument(
        "--links",
        default=None,
        help="comma-separated existing memory names — OVERRIDES recall-based link discovery (GRA-3)",
    )
    parser.add_argument(
        "--no-links",
        action="store_true",
        help="suppress the Related: [[...]] line entirely (no discovery, no --links)",
    )
    args = parser.parse_args(argv)

    links_arg = None
    if args.links is not None:
        links_arg = [ln.strip() for ln in args.links.split(",") if ln.strip()]

    res = write_memory(
        args.name,
        args.description,
        args.type,
        body=args.body,
        memory_dir=args.memory_dir,
        title=args.title,
        hook=args.hook,
        links=links_arg,
        no_links=args.no_links,
    )
    if res["error"]:
        print(f"error: {res['error']}")
        return 1
    print(f"created : {res['path']}")
    print(f"indexed : {res['indexed']}")
    print(f"floor pointer added : {res['floor_pointer_added']} (only user/feedback get one)")
    if res["related"]:
        print(f"related : {', '.join(res['related'])} (curate this — keep/trim/replace, see /hippo:new)")
    # LIF-2: the neighbor warning block. Bounded (<= _DUP_NEIGHBORS_K lines, descriptions
    # truncated at format_results' 220-char display convention) and decision-routing — the
    # tool reports, the agent decides; nothing here rejects or edits anything.
    if res["neighbors"]:
        n_hits = len(res["neighbors"])
        noun = "memory looks" if n_hits == 1 else "memories look"
        print(f"warning : {n_hits} existing {noun} near-duplicate/conflicting:")
        for n in res["neighbors"]:
            desc = n["description"].replace("\n", " ").strip()
            if len(desc) > 220:
                desc = desc[:217].rstrip() + "…"
            print(f"  • {n['name']} (similarity {n['score']:.2f}) — {desc}")
        print("  decide  : add (keep both) / update-existing / supersede / skip — see /hippo:new")
    if res["note"]:
        print(f"note    : {res['note']}")
    for warning in res["warnings"]:
        print(f"warning : {warning}")
    if args.type in ("project", "reference"):
        print("note    : project/reference memories are recalled on demand — NOT added to the floor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
