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
  5. inserts a ``MEMORY.md`` floor pointer ONLY when ``type`` is ``user`` or ``feedback``, at
     its deterministic SORTED position within the section rather than the tail (TEA-4 — kills
     concurrent-append merge conflicts on the section's highest-churn line; see
     ``_append_floor_pointer``), and reports the floor OUTCOME explicitly (LIF-5): a
     renamed/deleted canonical section is re-created rather than silently no-oped; a missing
     MEMORY.md is a loud machine-readable skip (floor creation is init's job, never faked here).

``project`` / ``reference`` memories are deliberately NOT added to the floor — they are
recalled on demand (the UserPromptSubmit recall hook + the SessionStart auto-refresh index
them). This is the whole point: new memories never re-bloat the trimmed always-load.

Never silently overwrites an existing file. The floor-pointer write (including re-creating a
drifted-away canonical floor section, LIF-5) is the ONLY edit to MEMORY.md; no existing memory
BODY is ever modified — link discovery only ever touches the file being CREATED (the
no-bulk-autonomous-sweeps invariant: this is a single-item write, not an edit to any
pre-existing memory).
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

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

# TEA-1/TEA-3: which corpus a memory is written to. "project" (default) = the git-native in-repo
# corpus teammates share; "user" = the machine-local user tier that follows the person across
# every project (TEA-1); "private" = the gitignored in-repo ``memory.local`` tier, recalled on
# THIS clone only (TEA-3). A non-project tier keeps its OWN floor file, so a user/feedback
# pointer written there never enters the shared, git-tracked project MEMORY.md — the no-leakage
# invariant enforced at the write seam.
_VALID_TIERS = ("project", "user", "private")

# GOV-7: the author's trust dial — a CLOSED enum, optional (absence = today's default).
# Display-only downstream: build_index carries it in the manifest, format_results/
# recall_view render it, recall's scoring path never reads it (AST-pinned in tests).
_VALID_CONFIDENCE = ("draft", "verified", "authoritative")


def _ensure_tier_floor(tier_dir: str, label: str) -> None:
    """Seed a NON-project tier's ``MEMORY.md`` with the two canonical floor sections the first
    time a memory is written there, so a ``user``/``feedback`` pointer has somewhere to land.
    Created once, minimally; never overwrites an existing floor. Never raises."""
    try:
        floor_path = os.path.join(tier_dir, "MEMORY.md")
        if os.path.exists(floor_path):
            return
        os.makedirs(tier_dir, exist_ok=True)
        from .atomic import write_text_atomic

        # INV-2: a torn skeleton would pass the exists() guard above forever and block
        # every future floor append — the floor is corpus-class truth, write it whole.
        write_text_atomic(
            floor_path,
            f"# Agent Memory ({label} tier)\n\n"
            f"> {label.capitalize()}-tier user/feedback memories — recalled alongside the "
            "project corpus and delivered each session by the SessionStart portable-floor "
            "producer (TEA-1/TEA-3), NOT the native symlink.\n\n"
            "## User\n\n"
            "## Working Style & Process Feedback\n",
        )
    except Exception:
        pass


def _title_from_slug(name: str) -> str:
    return name.replace("_", " ").replace("-", " ").strip().title()


def _pointer_name(line: str) -> Optional[str]:
    """The memory name a floor-section ``line`` points at, or ``None`` if it isn't a pointer.

    TEA-4: reuses lint_floor's own link regex + restore-pointer allow-list so "what counts as
    a pointer to sort by" is the exact same notion the lint guard already parses — a hand-
    authored ``[MEMORY.full.md](MEMORY.full.md)`` restore link inside a floor section (rare,
    but the allow-list tolerates it) is never treated as a memory entry to sort against.
    """
    from .lint_floor import _ALLOWLIST, _MD_LINK_RE

    m = _MD_LINK_RE.search(line)
    if not m:
        return None
    base = m.group(1).rsplit("/", 1)[-1]
    if base in _ALLOWLIST:
        return None
    return base[:-3] if base.endswith(".md") else base


def _unresolvable_link_warnings(related: List[str], memory_dir: str) -> List[str]:
    """RCH-10: name every explicit link target that resolves to no memory in THIS corpus.

    Returns ``[]`` (the empty norm) when everything resolves, so an ordinary write stays
    silent. Cross-tier targets get their own sentence because they are the common cause
    and the ONLY one where the target genuinely exists: ``user_role`` and
    ``hippo-machine-setup`` are real user-tier memories, but ``build_graph`` is
    per-corpus, so a project→user-tier edge can never resolve — the honest form is a
    prose/backtick reference, not a wikilink. Matching uses ``normalize_slug``, the same
    equivalence ``LinkGraph.resolve`` applies, so a legitimate ``[[User Role]]``-style
    spelling never trips it. Never raises: a lookup failure yields no warning rather
    than a false alarm.
    """
    try:
        from .links import normalize_slug
        from .provenance import _iter_memory_files

        corpus = {
            normalize_slug(os.path.splitext(os.path.basename(p))[0])
            for p in _iter_memory_files(memory_dir)
        }
        unresolved = [ln for ln in related if normalize_slug(ln) not in corpus]
        if not unresolved:
            return []

        # Which of them exist in ANOTHER tier? (the cross-tier case, named precisely)
        elsewhere: Dict[str, str] = {}
        try:
            from .recall import _extra_recall_tiers

            for tier_dir, _tier_index, label in _extra_recall_tiers(memory_dir):
                if not os.path.isdir(tier_dir):
                    continue
                stems = {
                    normalize_slug(os.path.splitext(os.path.basename(p))[0])
                    for p in _iter_memory_files(tier_dir)
                }
                for ln in unresolved:
                    if ln not in elsewhere and normalize_slug(ln) in stems:
                        elsewhere[ln] = label
        except Exception:
            elsewhere = {}

        out: List[str] = []
        cross = [ln for ln in unresolved if ln in elsewhere]
        unknown = [ln for ln in unresolved if ln not in elsewhere]
        if cross:
            shown = ", ".join(f"{ln} ({elsewhere[ln]} tier)" for ln in cross[:6])
            out.append(
                f"⚠ link target(s) live in another TIER, not this corpus: {shown}. The link "
                "graph is per-corpus, so a project→other-tier [[wikilink]] can never resolve "
                "— it reads as dangling in the link lint forever. Reference them in prose "
                "with backticks instead (the memory still recalls: tiers fuse at recall time, "
                "they just do not share a graph)."
            )
        if unknown:
            shown = ", ".join(unknown[:6])
            more = f" (+{len(unknown) - 6} more)" if len(unknown) > 6 else ""
            out.append(
                f"⚠ link target(s) resolve to no memory in this corpus: {shown}{more} — the "
                "[[wikilink]] was still written (a forward reference to a memory you plan to "
                "write is legitimate), but until that memory exists the link lint reports it "
                "as dangling. Fix the name, write the target, or drop the link."
            )
        return out
    except Exception:
        return []


def _discover_links(
    name: str,
    description: str,
    memory_dir: str,
    repo_root: Optional[str],
    k: int,
    index_dir: Optional[str] = None,
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
        hits = recall(
            query, k=k + 1, memory_dir=memory_dir, index_dir=index_dir, repo_root=repo_root
        )
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

    from .build_index import bm25_terms, tokenize
    from .recall import _bm25_score_via_postings

    stats = index.manifest.get("bm25")
    if not isinstance(stats, dict):
        return None
    try:
        k1, b, avgdl, idf = stats["k1"], stats["b"], stats["avgdl"], stats["idf"]
        if not avgdl:
            return None
        # RET-12: the manifest's postings are stemmed (build_index.bm25_terms) -- stem the
        # query side the same way, same rationale as recall._bm25_rank.
        q_tokens = bm25_terms(tokenize(doc_text))
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


def _duplicate_neighbors(name: str, rendered: str, memory_dir: str, index_dir: Optional[str] = None):
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

        index = load_index(index_dir or default_index_dir(memory_dir))
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


def committed_duplicate_neighbors(name: str, memory_dir: str, index_dir: Optional[str] = None):
    """Committed-vs-committed near-duplicate check (GRW-3) -> ``(neighbors, note)``.

    The write-time dup detector aimed at a memory ALREADY in the corpus: reads
    ``<name>.md``'s on-disk text and scores it against the persisted index with the SAME
    calibrated thresholds write-time dedup trusts (dense cosine ``_DUP_COSINE_THRESHOLD``,
    normalized-BM25 ``_DUP_BM25_THRESHOLD`` — each a genuine [0,1]-ish similarity). This is
    the scale a "near-duplicate" claim is calibrated in; ``recall()``'s fused scores are RRF
    rank aggregates (~1/60 per contributing ranking, COR-8) and must NEVER be compared to
    these thresholds — which is exactly why the audit skill's densification pass reports its
    fused scores UNTHRESHOLDED while the merge tier calls THIS instead. The dup checker's
    own-name exclusion keeps the memory from matching its own index row, so a committed
    memory can be scored against the very index that contains it. PUBLIC on purpose: the
    audit skill's merge sweep imports it, and skills never couple to underscore-private
    helpers. Same warn-only posture as the write-time check: report, never act.
    """
    try:
        with open(os.path.join(memory_dir, f"{name}.md"), "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return [], "duplicate check skipped: memory file unreadable"
    return _duplicate_neighbors(name, text, memory_dir, index_dir)


# --------------------------------------------------------------------------- #
# SEN-1: the write ticket — a deterministic pre-write verifier riding the dry-run
# battery. Three checks the reviewer used to do by eye (or a skill prescribed as
# PROCEDURE — the consolidate secret gate was SKILL.md text, not code): the secret
# lint over the rendered candidate, fenced-hunk fidelity vs a FRESH git HEAD, and
# the archive-shadow name collision. Every check is WARN-ONLY inside
# check_candidate's never-raise / no-autonomous-rejection contract: findings inform
# the approval prompt; they never flip a route, never block a write, never persist
# anywhere (no new frontmatter field, no ledger). The artifact is a "write ticket"
# (a gate stamp) — deliberately NOT named after GOV-5's shipped recall glass-box
# artifact (inv5: one concept, one word; SEN-1 renamed off it, and a test pins that
# this module never uses that word).
# --------------------------------------------------------------------------- #

# Fenced blocks checked / cited paths fetched per ticket. The caps keep the verifier
# subprocess-bounded (one `git show` per cited path, never per block×path); the
# fidelity dict's note says when a cap truncated coverage, never a silent narrowing.
_TICKET_MAX_BLOCKS = 8
_TICKET_MAX_PATHS = 12
# A fenced block shorter than this (stripped) is too weak a claim to verify — a
# `pytest -q` command block would "mismatch" every cited file and cry wolf. High
# precision over recall, the same doctrine secrets._PATTERNS states.
_TICKET_MIN_BLOCK_CHARS = 24
_TICKET_PREVIEW_CHARS = 40


def _fenced_blocks(body: str) -> List[str]:
    """The CONTENT of each fenced code block in ``body`` (fence lines stripped).

    Reuses ``markdown_code.FENCED_CODE_RE`` — COR-20's fence parser, moved to its own
    leaf module at COR-21 — so "what counts as a fenced block" is the exact notion every
    other lint uses, never a second drifting regex.
    """
    from .markdown_code import FENCED_CODE_RE

    out: List[str] = []
    for m in FENCED_CODE_RE.finditer(body or ""):
        lines = m.group(0).split("\n")
        out.append("\n".join(lines[1:-1]))
    return out


def _diff_post_image(content: str) -> Optional[str]:
    """The post-image of a unified-diff-shaped block, or None when it isn't one.

    Moved to ``staleness_evidence`` (CLB-3 owns diff-aware evidence matching);
    this delegates so the write ticket and the drift detector can never disagree
    about what a fresh tree can contain — one concept, one implementation.
    """
    from .staleness_evidence import _diff_post_image as _impl

    return _impl(content)


def _fence_fidelity(body: str, repo_root: Optional[str]) -> dict:
    """Byte-fidelity of fenced blocks vs the files the body cites, at a FRESH HEAD.

    The baseline is ``git_head`` fetched AT VERIFY TIME — never parsed out of a
    rationale string, which records HEAD at *proposal* time and can be stale or
    attacker-worded. A block "matches" when its exact bytes (or, for a diff-shaped
    block, its post-image) appear contiguously in some cited file at HEAD; the cited
    set is ``cited_paths_for_body`` — provenance backfill's own resolver, so the
    fidelity oracle and the citation oracle can never disagree. Unverifiable states
    (no HEAD, nothing cited, unreadable files) are NOTES, not warnings — an honest
    "could not check" is not a finding. Never raises.
    """
    fid: dict = {"head": None, "blocks": 0, "checked": 0, "matched": 0, "mismatched": [], "note": None}
    try:
        blocks = _fenced_blocks(body)
        fid["blocks"] = len(blocks)
        if not blocks:
            return fid
        if not repo_root:
            fid["note"] = "unverifiable: no repo root, so no git HEAD to compare against"
            return fid
        from .provenance import build_repo_file_index, cited_paths_for_body, git_head, run_git

        head = git_head(repo_root)
        if not head:
            fid["note"] = "unverifiable: no git HEAD at verify time (non-git or unborn repo)"
            return fid
        fid["head"] = head
        repo_files, basename_index = build_repo_file_index(repo_root)
        cited = cited_paths_for_body(body, repo_files, basename_index)
        if not cited:
            fid["note"] = "unverifiable: the body cites no path that resolves in the repo"
            return fid
        texts: List[str] = []
        for p in cited[:_TICKET_MAX_PATHS]:
            t = run_git(["show", f"{head}:{p}"], repo_root)
            if t:
                texts.append(t)
        if not texts:
            fid["note"] = f"unverifiable: no cited file is readable at HEAD {head[:12]}"
            return fid
        checkable = [b for b in blocks if len(b.strip()) >= _TICKET_MIN_BLOCK_CHARS]
        skipped_small = len(blocks) - len(checkable)
        for idx, content in enumerate(checkable[:_TICKET_MAX_BLOCKS]):
            candidates = [content.strip("\n")]
            post = _diff_post_image(content)
            if post:
                candidates.append(post)
            if any(c and c in t for c in candidates for t in texts):
                fid["matched"] += 1
            else:
                preview = " ".join(content.strip().split())[:_TICKET_PREVIEW_CHARS]
                fid["mismatched"].append({"index": idx + 1, "preview": preview})
            fid["checked"] += 1
        notes = []
        if skipped_small:
            notes.append(
                f"{skipped_small} block(s) under {_TICKET_MIN_BLOCK_CHARS} chars skipped (too small to verify)"
            )
        if len(checkable) > _TICKET_MAX_BLOCKS:
            notes.append(f"only the first {_TICKET_MAX_BLOCKS} of {len(checkable)} block(s) checked")
        if len(cited) > _TICKET_MAX_PATHS:
            notes.append(f"only the first {_TICKET_MAX_PATHS} of {len(cited)} cited path(s) compared")
        fid["note"] = "; ".join(notes) or None
        return fid
    except Exception:
        fid["note"] = fid["note"] or "unverifiable: fidelity check failed"
        return fid


def _archive_shadow(name: str, memory_dir: Optional[str]) -> dict:
    """Does ``name`` collide with an existing ``archive/<name>.md`` stem?

    The write-side twin of GRA-5's archive guard (which protects the OTHER direction:
    archiving a stem others still link to). A candidate re-using a retired stem's name
    would give one name two lives across live+archive — the archive-shadow blind spot.
    ``collides`` is True/False, or None when the probe itself failed (stated, not
    silently False — the GRA-5 fail-closed posture, minus the refusal: this surface
    is warn-only). Never raises.
    """
    try:
        from .archive import _ARCHIVE_SUBDIR

        p = os.path.join(memory_dir or "", _ARCHIVE_SUBDIR, f"{name}.md")
        if os.path.isfile(p):
            return {"collides": True, "path": p}
        return {"collides": False, "path": None}
    except Exception:
        return {"collides": None, "path": None}


def build_write_ticket(
    name: str, rendered: str, body: str, memory_dir: Optional[str], repo_root: Optional[str]
) -> dict:
    """Assemble the SEN-1 write ticket over one candidate. Warn-only; never raises.

    ``{"secret_warnings", "fence_fidelity", "archive_shadow", "warnings"}`` —
    ``warnings`` is the flattened human-readable warn set (secret KINDS + remediation,
    one fidelity line when blocks mismatched, one archive-shadow line on collision)
    so surfaces that just print lines need no ticket-specific logic. The structured
    fields feed ``render_write_ticket`` at the approval prompt.
    """
    ticket: dict = {
        "secret_warnings": [],
        "threat_warnings": [],
        "fence_fidelity": {"head": None, "blocks": 0, "checked": 0, "matched": 0, "mismatched": [], "note": None},
        "archive_shadow": {"collides": None, "path": None},
        "warnings": [],
    }
    try:
        from .secrets import scan_with_remediation

        ticket["secret_warnings"] = scan_with_remediation(rendered)
    except Exception:
        ticket["secret_warnings"] = []
    # SEN-2: Tier-A threat lint joins the ticket (SEN-1 is the CONTAINER SEN-2 lands inside).
    # SURFACED classes only — Tier-B imperative grammar is measured to the dark ledger by the
    # write-plane caller (write_memory), never on this pure/side-effect-free ticket path (so a
    # dry-run check never double-counts a write's Tier-B measurement).
    # Scan the UNESCAPED description + body — the surface that actually injects/recalls. The
    # rendered frontmatter json-escapes the description (an invisible codepoint becomes the
    # ASCII `\uXXXX`), but parse_frontmatter unescapes it back to the real byte before
    # inject_description injects it, so scanning `rendered` would MISS a description-embedded
    # invisible payload that still reaches context. (The body is written raw, so its bytes are
    # already real.)
    try:
        from .provenance import parse_frontmatter
        from .threat_lint import scan_tier_a

        try:
            desc_value = str(parse_frontmatter(rendered).get("description") or "")
        except Exception:
            desc_value = ""
        ticket["threat_warnings"] = scan_tier_a(f"{desc_value}\n{body}")
    except Exception:
        ticket["threat_warnings"] = []
    ticket["fence_fidelity"] = _fence_fidelity(body, repo_root)
    ticket["archive_shadow"] = _archive_shadow(name, memory_dir)

    warns: List[str] = list(ticket["secret_warnings"])
    if ticket["threat_warnings"]:
        warns.append(
            "⚠ threat lint (SEN-2 Tier-A): " + "; ".join(ticket["threat_warnings"])
            + " — a poisoning payload (invisible/confusable/exfil/HTML-comment) in the memory "
            "text; inspect and scrub before this is committed and re-injected on every recall."
        )
    fid = ticket["fence_fidelity"]
    if fid.get("mismatched"):
        shown = "; ".join(f"block #{m['index']} (“{m['preview']}…”)" for m in fid["mismatched"][:3])
        warns.append(
            f"⚠ hunk fidelity: {len(fid['mismatched'])} of {fid['checked']} fenced block(s) "
            f"match no cited file at HEAD {(fid.get('head') or '')[:12]} — {shown}. Quoted "
            "evidence may be paraphrased or stale; re-quote from the live file or drop the fence."
        )
    shadow = ticket["archive_shadow"]
    if shadow.get("collides"):
        warns.append(
            f"⚠ archive shadow: archive/{name}.md already exists — writing this name gives a "
            "retired stem a second life (links and journal history would straddle live+archive). "
            "Pick a new name, or deliberately restore the archived memory instead."
        )
    ticket["warnings"] = warns
    return ticket


def render_write_ticket(ticket: Optional[dict]) -> str:
    """The verbatim-printable write-ticket block for the approval prompt. Never raises.

    One line per check, ✓/⚠ prefixed, plus the unverifiable note when a check could
    not run — rendered at the SAME step as the dup/rules-echo warnings so the human
    approving the write sees the whole gate stamp in one place.
    """
    if not isinstance(ticket, dict):
        return ""
    try:
        from .secrets import REMEDIATION

        lines = ["write ticket (deterministic pre-write verifier — warn-only; you route):"]
        kinds = [w for w in ticket.get("secret_warnings") or [] if w != REMEDIATION]
        if kinds:
            lines.append(f"  ⚠ secret lint   : {'; '.join(kinds)}")
        else:
            lines.append("  ✓ secret lint   : clean")
        threats = ticket.get("threat_warnings") or []
        if threats:
            lines.append(f"  ⚠ threat lint   : {'; '.join(threats)}")
        else:
            lines.append("  ✓ threat lint   : clean")
        fid = ticket.get("fence_fidelity") or {}
        if not fid.get("blocks"):
            lines.append("  ✓ hunk fidelity : no fenced blocks to verify")
        elif fid.get("checked"):
            mark = "⚠" if fid.get("mismatched") else "✓"
            head = (fid.get("head") or "")[:12]
            line = (
                f"  {mark} hunk fidelity : {fid.get('matched', 0)}/{fid.get('checked', 0)} "
                f"fenced block(s) verbatim in a cited file at HEAD {head}"
            )
            if fid.get("note"):
                line += f" ({fid['note']})"
            lines.append(line)
        else:
            lines.append(f"  ✓ hunk fidelity : skipped — {fid.get('note') or 'nothing checkable'}")
        shadow = ticket.get("archive_shadow") or {}
        if shadow.get("collides"):
            lines.append("  ⚠ archive shadow: collides with a retired stem — see the warning above")
        elif shadow.get("collides") is None:
            lines.append("  ✓ archive shadow: unverifiable (archive probe failed) — stated, not assumed clear")
        else:
            lines.append("  ✓ archive shadow: clear")
        return "\n".join(lines)
    except Exception:
        return ""


def check_candidate(
    name: str,
    description: str,
    type: str,
    body: str = "",
    *,
    memory_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
) -> dict:
    """CAP-3: DRY-RUN write-time decisioning for a captured candidate — writes NOTHING.

    Renders the candidate (name + description + type) and scores it against the EXISTING
    corpus with LIF-2's exact near-duplicate machinery (``_duplicate_neighbors``), so an agent
    draining the CAP-2 pending queue routes each candidate to add / update-existing / supersede
    / skip BEFORE any file is created — the check-FIRST counterpart to ``write_memory``'s
    write-then-warn. A duplicate captured candidate therefore never becomes a new file at all
    (the acceptance bar: approving a duplicate routes to update/supersede, not a new file).

    Returns ``{"route": "add"|"review", "neighbors": [{name, score, description}],
    "rule_neighbors": [{file, score, preview}], "note"}``: ``route == "review"`` means at
    least one near-duplicate/conflict cleared the threshold and the agent must choose
    update-existing / supersede / skip (naming the target); ``"add"`` means the candidate is
    novel (or the check could not run — ``note`` says which). ``rule_neighbors`` (RUL-3)
    carries governance blocks the candidate RESTATES — route those to "link, don't copy"
    (they flag but do not flip the route: a rules-plane echo is a wording decision, not an
    add/supersede fork). It never writes the corpus (no file, no index refresh, no floor
    edit) and never raises — it reuses the same warn-only, no-autonomous-rejection contract
    as LIF-2.
    """
    try:
        if memory_dir is None:
            from .provenance import resolve_dirs

            memory_dir, repo = resolve_dirs()
            repo_root = repo_root or repo
        rendered = _render_frontmatter(name, description, type, body)
        neighbors, note = _duplicate_neighbors(name, rendered, memory_dir)
        rule_neighbors: List[dict] = []
        if repo_root:
            try:
                from .rules_plane import rule_dup_candidates

                rule_neighbors = rule_dup_candidates(description, body, repo_root)
            except Exception:
                rule_neighbors = []
        # GOV-3: the HONEST git baseline a proposal can carry — HEAD at proposal time.
        # source_commit does NOT exist yet (this is a dry run; provenance backfill only
        # happens on the real write), so "as of HEAD <sha>" is the evidence a reviewer can
        # actually anchor to. None on a non-git corpus — an honest absence, never a fake.
        baseline = None
        if repo_root:
            try:
                from .provenance import git_head

                baseline = git_head(repo_root)
            except Exception:
                baseline = None
        # SEN-1: the write ticket joins the dry-run battery. Warn-only by contract —
        # the route above was already decided by the dup check alone, and no ticket
        # finding ever flips it (no autonomous rejection; the approving human routes).
        ticket = build_write_ticket(name, rendered, body, memory_dir, repo_root)
        return {
            "route": "review" if neighbors else "add",
            "neighbors": neighbors,
            "rule_neighbors": rule_neighbors,
            "baseline": baseline,
            "note": note,
            "ticket": ticket,
        }
    except Exception as exc:
        return {
            "route": "add",
            "neighbors": [],
            "rule_neighbors": [],
            "baseline": None,
            "note": f"candidate check skipped: {exc}",
            "ticket": build_write_ticket(name, "", body, memory_dir, repo_root),
        }


def _render_frontmatter(
    name: str,
    description: str,
    mtype: str,
    body: str,
    confidence: Optional[str] = None,
    origin: Optional[str] = None,
) -> str:
    """Recall-ready frontmatter: top-level name + description (indexed), metadata.type.

    ``description`` is JSON-quoted so any colon/character is valid YAML (the recall index
    reads ``description`` via yaml.safe_load). ``confidence`` (GOV-7, optional) nests under
    ``metadata:`` like every other provenance-style key — absence emits nothing (today's
    default), keeping an unset memory byte-identical to before the field existed.
    ``origin`` (RCH-1, optional) is the promote-time provenance stamp
    (``"<repo>@<sha>"`` — where the lesson was learned); same nest-under-``metadata:``,
    absence-emits-nothing contract, JSON-quoted like ``description`` so any repo name
    stays valid YAML. Display-only (recall_view renders it) — never a ranking input.
    """
    lines = [
        "---",
        f"name: {name}",
        f"description: {json.dumps(description)}",
        "metadata:",
        f"  type: {mtype}",
        *([f"  confidence: {confidence}"] if confidence else []),
        *([f"  origin: {json.dumps(origin)}"] if origin else []),
        "---",
        "",
        body.rstrip("\n") + "\n" if body else "",
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def _append_rationale(body: str, rationale: Optional[str]) -> str:
    """Append a final ``Rationale: <evidence>`` body line (GOV-3) — the git-committed WHY.

    Additive only — never touches any existing body text, just appends one trailing line
    (``_append_related_line``'s exact discipline). Newlines in the evidence collapse to
    spaces so the fence stays one legible line.
    """
    if not rationale or not str(rationale).strip():
        return body
    line = "Rationale: " + " ".join(str(rationale).split())
    body = (body or "").rstrip("\n")
    return f"{body}\n\n{line}\n" if body else f"{line}\n"


def _append_floor_pointer(
    memory_dir: str, section_header: str, name: str, title: str, hook: str
) -> dict:
    """Insert ``- [title](name.md) — hook`` at its SORTED position within ``section_header``.

    Returns the ``result["floor"]`` outcome dict — ``{"status", "reason"}`` (LIF-5). This used
    to return a bare bool that silently no-oped on a missing file OR a renamed header, so a
    user/feedback memory could lose its always-load pointer with no signal anywhere. Now every
    outcome is explicit; never raises; MEMORY.md stays the ONLY file this module edits:

    - ``appended`` (reason None) — the section exists; the pointer is inserted at its
      deterministic lexicographic position among the section's EXISTING pointer lines (TEA-4 —
      see the insertion-point comment below), never necessarily the block tail anymore.
    - ``created-section`` — MEMORY.md exists but ``section_header`` does not (renamed or
      deleted by hand — the floor drifted from ``assets/MEMORY.skeleton.md``). The canonical
      section is re-created at the END of MEMORY.md in the skeleton's own format (one blank
      separator line, ``## Header``, then the pointer as its first entry). Repairing beats
      skipping here: the pointer is the whole point of a user/feedback write, and the created
      section is exactly what lint_floor/floor_memory_names already parse as floor. Merging a
      RENAMED section's leftovers into the canonical one stays agent-gated (/hippo:new routes
      it) — this function never touches other sections.
    - ``skipped`` — nothing written; ``reason`` is machine-readable: ``MEMORY.md missing``
      (floor CREATION is /hippo:init's job — skeleton + starter packs; fabricating the whole
      file here would shadow that), ``pointer already present`` (idempotence — the same
      ``name.md`` is already floor-linked), or ``MEMORY.md unreadable/write failed: ...``.
    """
    path = os.path.join(memory_dir, "MEMORY.md")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except FileNotFoundError:
        return {
            "status": "skipped",
            "reason": "MEMORY.md missing — pointer NOT recorded; run /hippo:init to create "
            "the floor, then add the pointer line by hand",
        }
    except Exception as exc:
        return {"status": "skipped", "reason": f"MEMORY.md unreadable: {exc}"}

    link = f"]({name}.md)"
    if any(link in ln for ln in lines):
        return {"status": "skipped", "reason": "pointer already present"}

    pointer = f"- [{title}]({name}.md) — {hook}".rstrip()

    # Find the section header, then the end of its block (next "## " or EOF).
    start = next((i for i, ln in enumerate(lines) if ln.strip() == section_header), None)
    if start is None:
        # LIF-5: header renamed/deleted — re-create the canonical section at EOF, skeleton
        # format. (An empty-but-existing MEMORY.md gets the section with no leading blank.)
        text = "\n".join(lines).rstrip("\n")
        section = f"{section_header}\n{pointer}\n"
        new_text = f"{text}\n\n{section}" if text else section
        try:
            from .atomic import write_text_atomic

            write_text_atomic(path, new_text)  # COR-18: MEMORY.md is source of truth
        except Exception as exc:
            return {"status": "skipped", "reason": f"MEMORY.md write failed: {exc}"}
        return {
            "status": "created-section",
            "reason": f"section not found: {section_header} — created it at the end of MEMORY.md",
        }

    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip().startswith("## "):
            end = j
            break

    # TEA-4: sorted insertion — the new pointer goes BEFORE the first existing pointer line in
    # the block whose memory name sorts lexicographically greater than this one. This is what
    # kills tail-collision merge conflicts: two clones adding DIFFERENT names to the same
    # section each touch a diff hunk at THEIR OWN name's position, not both appending to the
    # single highest-churn shared line (the section tail) — git merges the two non-overlapping
    # insertions cleanly. A fully-sorted section stays sorted (every insert lands at its exact
    # slot). An unsorted legacy section gets each new entry placed at its locally-correct spot
    # relative to whatever order already exists, WITHOUT touching or reordering any other
    # line — no bulk re-sort, per the no-bulk-autonomous-sweeps invariant. Non-pointer lines
    # (blank lines, hand-written prose) are skipped when searching but never moved.
    insert = None
    for j in range(start + 1, end):
        other = _pointer_name(lines[j])
        if other is not None and other > name:
            insert = j
            break
    if insert is None:
        # No existing pointer sorts greater than this name (a brand-new section, an
        # append-only section, or this name is the section's new last entry) — falls through
        # to the same "end of block" position the pre-TEA-4 append always used, so a freshly
        # created section's first pointer (LIF-5) and an alphabetically-last name both land
        # exactly where they always did.
        insert = start + 1
        for j in range(start + 1, end):
            if lines[j].strip():
                insert = j + 1

    lines.insert(insert, pointer)
    try:
        from .atomic import write_text_atomic

        write_text_atomic(path, "\n".join(lines))  # COR-18
    except Exception as exc:
        return {"status": "skipped", "reason": f"MEMORY.md write failed: {exc}"}
    return {"status": "appended", "reason": None}


def _remove_floor_pointer(memory_dir: str, name: str) -> dict:
    """Drop ``name``'s floor pointer line from MEMORY.md — ``_append_floor_pointer``'s inverse.

    RCH-1: when /hippo:promote lifts a user/feedback memory OUT of the project corpus, the
    project floor's pointer to it would dangle (the .md is gone); this removes exactly that
    line. What counts as "the pointer" is ``_pointer_name`` — the same notion the TEA-4
    sorted insert and lint_floor parse — so a prose line that merely mentions ``name.md``
    is never touched. Returns the same explicit outcome-dict contract as append, never
    raises, and MEMORY.md stays the only file this module edits:

    - ``removed`` (reason None) — the pointer line(s) were dropped.
    - ``skipped`` — nothing written; ``reason`` names why: ``MEMORY.md missing``,
      ``pointer not present`` (idempotence — safe to call for never-floor-linked types),
      or ``MEMORY.md unreadable/write failed: ...``.
    """
    path = os.path.join(memory_dir, "MEMORY.md")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except FileNotFoundError:
        return {"status": "skipped", "reason": "MEMORY.md missing"}
    except Exception as exc:
        return {"status": "skipped", "reason": f"MEMORY.md unreadable: {exc}"}
    kept = [ln for ln in lines if _pointer_name(ln) != name]
    if len(kept) == len(lines):
        return {"status": "skipped", "reason": "pointer not present"}
    try:
        from .atomic import write_text_atomic

        write_text_atomic(path, "\n".join(kept))  # COR-18
    except Exception as exc:
        return {"status": "skipped", "reason": f"MEMORY.md write failed: {exc}"}
    return {"status": "removed", "reason": None}


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
    tier: str = "project",
    rationale: Optional[str] = None,
    confidence: Optional[str] = None,
    origin: Optional[str] = None,
) -> dict:
    """Create a recall-ready memory file. Returns a small result dict.

    - ``origin`` (RCH-1): the promote-time provenance stamp (``"<repo>@<sha>"``), rendered
      as ``metadata.origin``. Display-only; absence emits nothing (byte-identical default).

    - Validates ``type`` ∈ VALID_TYPES.
    - Refuses to overwrite an existing ``<name>.md`` (``created=False``, ``error`` set).
    - ``rationale`` (GOV-3): fences the proposal's evidence trail ("from session <sid>;
      replaces <neighbor> (similarity 0.9x); as of HEAD <sha>") into the BODY as one
      trailing ``Rationale:`` line — the WHY becomes git-committed with the memory, not a
      one-time drain display. Additive-append discipline (``_append_rationale``), applied
      before the Related line so that stays the final body line.
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
    - Detects restated GOVERNANCE rules (RUL-3): ``result["rule_neighbors"]`` carries
      ``[{file, score, preview}]`` for CLAUDE.md/.claude/rules/AGENTS.md blocks the draft
      restates — "link, don't copy". Same warn-only contract; stops two-plane drift at birth.
    - Backfills provenance + refreshes the recall index (best-effort; never fatal).
    - Adds a MEMORY.md floor pointer ONLY for ``user`` / ``feedback`` and reports the outcome
      explicitly (LIF-5): ``result["floor"]`` is ``{"status": appended|created-section|skipped,
      "reason"}`` — a renamed/deleted canonical section is re-created at the end of MEMORY.md
      (never a silent no-op), a missing MEMORY.md is a loud machine-readable skip (floor
      creation is /hippo:init's job), and ``project`` / ``reference`` report a skipped floor
      by design (recalled on demand). ``None`` only when creation failed before the floor step.
    - Scans the rendered text for secret-looking patterns (SEC-2) and, on a match, populates
      ``warnings`` — WARN-not-block: the write still happens; the agent decides what to do.
    """
    result = {
        "created": False,
        "path": None,
        "tier": "project",
        "floor": None,
        "indexed": False,
        "related": [],
        "neighbors": [],
        "rule_neighbors": [],
        "note": None,
        "warnings": [],
        "ticket": None,
        "error": None,
    }
    if type not in VALID_TYPES:
        result["error"] = f"invalid type {type!r} (expected one of {VALID_TYPES})"
        return result
    tier = (tier or "project").lower()
    if tier not in _VALID_TIERS:
        result["error"] = f"invalid tier {tier!r} (expected one of {_VALID_TIERS})"
        return result
    result["tier"] = tier
    if confidence is not None and confidence not in _VALID_CONFIDENCE:
        result["error"] = (
            f"invalid confidence {confidence!r} (expected one of {_VALID_CONFIDENCE})"
        )
        return result
    # Name must be a bare slug — a path separator (or "..") would write the file OUTSIDE
    # memory_dir, where neither the index nor the floor would ever find it (a silent hole).
    if not name or os.path.basename(name) != name:
        result["error"] = f"invalid name {name!r} (must be a bare slug, no path separators)"
        return result

    from .provenance import (
        build_repo_file_index,
        ensure_self_ignoring_dir,
        local_memory_dir,
        resolve_dirs,
        tier_index_dir,
        user_memory_dir,
    )

    md, repo = resolve_dirs()
    repo_root = repo_root or repo
    # TEA-1/TEA-3: a non-project write goes to a SECOND corpus recalled alongside the project.
    #  - ``user`` (TEA-1): the machine-local user tier, recalled across every project. Its index
    #    is the tier's plain sibling (``default_index_dir``) — recall/refresh/dedup all resolve it
    #    identically with no plumbing.
    #  - ``private`` (TEA-3): the in-repo, gitignored ``memory.local`` sibling. Its index NESTS
    #    inside the tier (its plain sibling would be the project's own ``.claude/.memory-index``),
    #    so that nested ``tier_index`` must be threaded through discovery/dedup/refresh below.
    # A non-project write's floor pointer lands in the tier's OWN MEMORY.md, never the shared
    # project one (the no-leakage invariant). An explicit ``memory_dir`` (tests) always wins.
    tier_index: Optional[str] = None
    if memory_dir is None:
        if tier == "user":
            memory_dir = user_memory_dir()
        elif tier == "private":
            memory_dir = local_memory_dir(md)
        else:
            memory_dir = md
    if tier == "private":
        tier_index = tier_index_dir(memory_dir)
    if tier != "project":
        if tier == "private":
            # SEC-3: drop a self-ignoring ``.gitignore`` (``*``) so the whole private tier —
            # memories, floor, and nested index — is invisible to ``git status`` and can never
            # be committed, independent of init's gitignore patch, while staying recallable.
            ensure_self_ignoring_dir(memory_dir)
        _ensure_tier_floor(memory_dir, tier)
        if tier == "user":
            # Machine-local, outside any repo: no git provenance to backfill against.
            repo_root = memory_dir

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
        related = _discover_links(
            name, description, memory_dir, repo_root, _LINK_DISCOVERY_K, index_dir=tier_index
        )
    result["related"] = related
    # GOV-3: rationale BEFORE the Related line, so Related stays the final body line (the
    # additive-append convention both writers share).
    body = _append_rationale(body, rationale)
    body = _append_related_line(body, related)

    path = os.path.join(memory_dir, f"{name}.md")
    rendered = _render_frontmatter(name, description, type, body, confidence, origin)

    # --- LIF-2: duplicate/conflict detection, BEFORE the write + index refresh. ---
    # Ordering is load-bearing twice over: (a) it must score against the PRE-refresh
    # persisted index — after refresh_index below, the new memory would be IN the index and
    # match itself at ~1.0; (b) it runs on the exact ``rendered`` text (via memory_doc_text)
    # so the doc_text scored here is byte-identical to what the next build indexes. Warn-only:
    # whatever comes back, the exclusive-create below proceeds unconditionally.
    result["neighbors"], result["note"] = _duplicate_neighbors(
        name, rendered, memory_dir, index_dir=tier_index
    )

    # --- RUL-3: rules-plane dedup, the PREVENTIVE leg of LIF-2. A draft that restates a
    # rule already in CLAUDE.md/.claude/rules/AGENTS.md starts two-plane drift the moment it
    # lands; warn "link, don't copy" alongside the corpus-neighbor warning. Same warn-only
    # contract — the exclusive-create below proceeds unconditionally. Never fatal.
    try:
        from .rules_plane import rule_dup_candidates

        result["rule_neighbors"] = rule_dup_candidates(description, body, repo_root)
    except Exception:
        result["rule_neighbors"] = []

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

    # SEN-1: the write ticket — the secret lint (SEC-2) plus fenced-hunk fidelity and
    # archive-shadow, assembled over the RENDERED text exactly as the check-first dry run
    # does, so the two surfaces can never disagree about a candidate. This WARNS, it does
    # NOT block — the write already happened and is kept; the flattened warn lines ride
    # out on ``warnings`` (report-then-act, agent-gated) and the structured ticket on
    # ``ticket``. Never fatal — a broken checker just yields degraded ticket fields.
    ticket = build_write_ticket(name, rendered, body, memory_dir, repo_root)
    result["ticket"] = ticket
    result["warnings"] = list(ticket["warnings"])

    # SEN-2 Tier-B: measure imperative-grammar in the just-written text to the DARK ledger —
    # never surfaced, never a HOLD, never a ticket field (inv3). This is the real write plane
    # (not the dry run), off the hot path; a dated owner decision graduates it on a
    # ledger-measured near-zero FP rate. Best-effort; never fatal.
    try:
        from .threat_lint import scan_tier_b

        tier_b = scan_tier_b(rendered)
        if tier_b:
            from .telemetry import log_threat_findings

            log_threat_findings(tier_b, source="write", name=name)
    except Exception:
        pass

    # SEN-3: ungrounded-prescription lint — an agent-voiced "the user always wants X" with no
    # captured evidence and no --rationale is the synthesized-prescription shape that amplifies
    # sycophancy. WARN-ONLY (never blocks, never routes, never ranks — it stays OUT of
    # check_candidate so the confidence-never-ranking pin holds). Grounding = the --rationale
    # param OR a fenced hunk in the body overlapping the claim; only a span grounded in
    # NEITHER is surfaced. Best-effort; never fatal.
    try:
        from .prescription_lint import find_ungrounded

        ungrounded = find_ungrounded(f"{description}\n{body}", rationale=rationale)
        if ungrounded:
            result["warnings"] = (result.get("warnings") or []) + [
                "⚠ ungrounded prescription (SEN-3): this memory asserts user intent — "
                f"\"{'; '.join(ungrounded)}\" — grounded in neither the captured evidence nor "
                "a --rationale. Transcribe what the diff/decision shows, or cite the WHY; a "
                "synthesized standing preference amplifies sycophancy on every recall."
            ]
    except Exception:
        pass

    # 1. Provenance backfill (best-effort — a new file with no code citations is fine).
    try:
        from .provenance import backfill_file

        repo_files, basename_index = build_repo_file_index(repo_root)
        bf = backfill_file(path, repo_root, repo_files, basename_index)
        # DRV-1: this return used to be discarded, which made ONE outcome indistinguishable
        # from "cites no code": the body names real files, none resolve, and the memory is
        # born with `cited_paths: []` — staleness-EXEMPT, the worst rot state, silently. The
        # common cause is benign and invisible (the oracle is `git ls-files`, so a file
        # written but not yet `git add`ed does not exist to it), which is exactly why it
        # needs saying out loud at write time rather than being discovered months later.
        unresolved = bf.get("extracted_but_unresolved") or []
        if unresolved and not bf.get("cited"):
            shown = ", ".join(unresolved[:6])
            more = f" (+{len(unresolved) - 6} more)" if len(unresolved) > 6 else ""
            result["warnings"] = (result.get("warnings") or []) + [
                f"⚠ this memory's body cites {len(unresolved)} path(s) that resolve to "
                f"nothing ({shown}{more}), so it was born with cited_paths: [] — EXEMPT "
                "from staleness tracking. Usually the file is not `git add`ed yet (the "
                "citation oracle is the git index, not the filesystem); it can also be a "
                "wrong path, or a bare basename that matches several files. Fix the path or "
                "commit the file, then re-run provenance --refresh-one on this memory."
            ]
    except Exception:
        pass

    # RCH-10: an EXPLICIT links list is AUTHORITATIVE — but authoritative is not the
    # same as correct. A target that resolves to nothing mints a dangling edge the
    # corpus then carries forever, surfaced only whenever someone next runs the link
    # lint. Reproduced live on this repo (2026-07-16): a links=["user_role"] write
    # succeeded clean and the dangle was found hours later in a sleep report.
    # WARN, never block (RCH-9's discipline: name it, don't swallow it — and don't
    # refuse it either, since a forward reference to a memory you intend to write next
    # is legitimate). Discovery-path links are exempt by construction: _discover_links
    # only ever returns corpus members, so warning there would be hippo blaming the
    # user for its own pick.
    if links is not None and result["related"]:
        result["warnings"] = (result.get("warnings") or []) + _unresolvable_link_warnings(
            result["related"], memory_dir
        )

    # SEC-6: authorship is consent — fold the just-written (and just-backfilled: the
    # LAST mutation of the file in this call) bytes into the trusted-corpus consent
    # baseline, so the author's own check-first, agent-gated write never quarantines
    # itself. Project tier only: the user/private tiers are the user's own machine
    # state and are never trust-gated. Best-effort (a legacy fingerprint-less record
    # is a no-op by design); never fatal.
    if tier == "project":
        try:
            from .trust import record_authored_write_disclosing

            # BND-3: an anomalous fold failure (quarantine-active corpus, fold False)
            # is disclosed at the write moment via the existing warnings channel —
            # the one time a human is present to act. Designed no-ops stay silent.
            note = record_authored_write_disclosing(memory_dir, path, repo_root)
            if note:
                result["warnings"] = (result.get("warnings") or []) + [note]
        except Exception:
            pass

    # 2. Refresh the recall index so the memory is immediately recallable.
    try:
        from .build_index import refresh_index

        # tier_index is None for project/user (their index is the plain sibling); for the
        # private tier it is the NESTED index, so a private write's index never lands in the
        # project's ``.claude/.memory-index`` cache (no leakage).
        refresh_index(memory_dir, tier_index)
        result["indexed"] = True
    except Exception:
        pass

    # 3. Floor pointer ONLY for user / feedback (project / reference are recalled on demand).
    # LIF-5: the outcome is ALWAYS explicit on the result — appended / created-section /
    # skipped-with-reason — never a silent no-op the agent can't see.
    section = _FLOOR_SECTION_BY_TYPE.get(type)
    if section is None:
        result["floor"] = {
            "status": "skipped",
            "reason": f"type '{type}' is never floor-linked — recalled on demand",
        }
    else:
        result["floor"] = _append_floor_pointer(
            memory_dir, section, name, title or _title_from_slug(name), hook or description
        )
    return result


def promote_memory(
    name: str,
    *,
    memory_dir: Optional[str] = None,
    repo_root: Optional[str] = None,
    dest_tier: str = "user",
    new_name: Optional[str] = None,
    allow_consequential: bool = False,
    force: bool = False,
) -> dict:
    """Lift ONE project memory into the user (or private) tier, with provenance (RCH-1).

    The move, in order — every guard runs BEFORE anything is written, so a refusal is
    always a zero-filesystem-change event:

    1. Read ``<name>.md`` from the project corpus and parse its parts (description, type,
       confidence, verbatim body).
    2. REFUSE a retired memory (``invalid_after`` set) — a demoted/superseded lesson does
       not get a second life in another tier; resolve its lifecycle first.
    3. GRA-5 inbound guard (``archive``'s exact discipline): other project memories still
       linking here would dangle when the project file goes — refuse unless ``force``;
       an unbuildable graph fails CLOSED.
    4. Portability lint (RCH-6): ``severity=="confirm"`` findings (consequential defaults)
       REFUSE unless ``allow_consequential=True`` — the skill sets it only after each
       finding got an explicit per-item yes. ``"warn"`` findings (repo coupling) ride out
       on ``result["findings"]`` for the agent to strip/rewrite; they never block, because
       the destination write is still reviewable markdown.
    5. ORIGIN STAMP: ``"<repo>@<sha>"`` — the memory's own ``source_commit`` when stamped,
       else the source repo's HEAD; just the repo basename when there is no git at all.
    6. Write via ``write_memory(..., tier=dest_tier, origin=..., no_links=True)`` — the
       body carries over VERBATIM (no re-discovery of Related links against the new
       corpus), and the re-render is itself the provenance strip: ``cited_paths`` /
       ``source_commit`` / ``source_commit_time`` / ``steer`` / ``last_verified`` are
       project-scoped bookkeeping and deliberately do not survive the lift. A name
       collision in the destination tier rides ``write_memory``'s exclusive-create
       refusal — the caller renames via ``new_name``; the project file is NOT touched
       (never silently shadow, inv5).
    7. Only after the destination write succeeded: remove the project file (``git rm``,
       falling back to a plain remove for an untracked file — the content already lives
       in the destination tier, so the move is never lossy), drop its project floor
       pointer (``_remove_floor_pointer``), and refresh the project index.

    ``dest_tier`` is ``"user"`` (machine-local, recalled across every project) or
    ``"private"`` (this repo's gitignored sibling — decoupled from the shared corpus
    without cross-project spread). Per-item by design: no list/bulk parameter (inv4).
    Never raises.
    """
    result = {
        "promoted": False,
        "name": name,
        "dest_name": new_name or name,
        "tier": dest_tier,
        "origin": None,
        "from": None,
        "to": None,
        "findings": [],
        "referrers": [],
        "refused": False,
        "floor_removed": None,
        "project_indexed": False,
        "warnings": [],
        "error": None,
    }
    try:
        from .portability import scan_portability
        from .provenance import (
            local_memory_dir,
            parse_frontmatter,
            resolve_dirs,
            split_frontmatter,
            user_memory_dir,
        )
        from .staleness import read_invalid_after, read_provenance

        dest_tier = (dest_tier or "").lower()
        if dest_tier not in ("user", "private"):
            result["error"] = (
                f"invalid dest_tier {dest_tier!r} (expected 'user' or 'private' — "
                "promotion lifts OUT of the project corpus)"
            )
            return result
        md, repo = resolve_dirs()
        memory_dir = memory_dir or md
        repo_root = repo_root or repo
        src = os.path.join(memory_dir, f"{name}.md")
        result["from"] = src
        if not os.path.isfile(src):
            result["error"] = f"not found: {name}.md"
            return result
        with open(src, "r", encoding="utf-8") as fh:
            text = fh.read()

        fm = parse_frontmatter(text)
        meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
        description = fm.get("description")
        if not isinstance(description, str) or not description.strip():
            result["error"] = "no description in frontmatter — not a recall-ready memory"
            return result
        mtype = (meta or {}).get("type") or fm.get("type")
        if mtype not in VALID_TYPES:
            result["error"] = (
                f"invalid or missing type {mtype!r} (expected one of {VALID_TYPES})"
            )
            return result
        confidence = (meta or {}).get("confidence")
        if confidence not in _VALID_CONFIDENCE:
            confidence = None

        boundary = read_invalid_after(text)
        if boundary is not None:
            result["refused"] = True
            result["error"] = (
                f"retired (invalid_after {boundary}) — a demoted/superseded memory does "
                "not promote; resolve its lifecycle first"
            )
            return result

        # GRA-5: the project-side removal below would dangle every inbound link, exactly
        # like an archive move — same guard, same fail-closed posture, same force escape.
        from .archive import _inbound_referrers

        referrers = _inbound_referrers(name, memory_dir)
        if referrers is None:
            if not force:
                result["refused"] = True
                result["error"] = (
                    "could not build the link graph, so inbound referrers are "
                    "unverifiable — refusing (fail closed); re-run with force=True to "
                    "promote anyway"
                )
                return result
            referrers = []
        result["referrers"] = referrers
        if referrers and not force:
            result["refused"] = True
            result["error"] = (
                f"{len(referrers)} inbound referrer(s) still link here: "
                f"{', '.join(referrers)}. Rewrite those references first, or re-run "
                "with force=True to promote anyway"
            )
            return result

        cited, sc = read_provenance(text)
        findings = scan_portability(text, cited_paths=cited)
        result["findings"] = findings
        confirmables = [f for f in findings if f.get("severity") == "confirm"]
        if confirmables and not allow_consequential:
            result["refused"] = True
            result["error"] = (
                f"{len(confirmables)} consequential-default finding(s) require an "
                "individual yes before this memory can spread beyond its repo — re-run "
                "with allow_consequential=True after each has been explicitly confirmed"
            )
            return result

        from .provenance import git_head

        sha = sc or git_head(repo_root)
        repo_name = os.path.basename(os.path.abspath(repo_root))
        origin = f"{repo_name}@{sha}" if sha else repo_name
        result["origin"] = origin

        _, body = split_frontmatter(text)
        body = body.lstrip("\n")

        dest_dir = user_memory_dir() if dest_tier == "user" else local_memory_dir(memory_dir)
        write = write_memory(
            new_name or name,
            description,
            mtype,
            body,
            memory_dir=dest_dir,
            repo_root=repo_root,
            no_links=True,
            tier=dest_tier,
            confidence=confidence,
            origin=origin,
        )
        result["warnings"] = write.get("warnings") or []
        if write.get("error"):
            err = write["error"]
            if "already exists" in err:
                err += (
                    " in the destination tier — pass new_name= to promote under a "
                    "different name (the project file was NOT touched)"
                )
            result["error"] = err
            return result
        result["to"] = write.get("path")

        # Destination write is durable — now (and only now) the project side moves out.
        import subprocess

        proc = subprocess.run(
            ["git", "-C", repo_root, "rm", "-q", "--", src],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode != 0:
            # git rm refuses untracked/ignored paths — the content already lives in the
            # destination tier, so a plain remove loses nothing.
            os.remove(src)
        result["floor_removed"] = _remove_floor_pointer(memory_dir, name)
        try:
            from .build_index import refresh_index

            refresh_index(memory_dir)
            result["project_indexed"] = True
        except Exception:
            pass
        result["promoted"] = True
    except Exception as exc:
        result["error"] = result["error"] or f"promote failed: {exc}"
    return result


def promote_candidates(memory_dir: Optional[str] = None) -> List[dict]:
    """DRY-RUN promote-candidate listing (RCH-1 extension) — listing only, never a lift.

    A candidate is a user/feedback-type project memory (the floor-linked, working-style
    class — the kind that transfers) that is repo-coupling-FREE (zero ``repo_coupling``
    portability findings) and not retired. ``consequential`` counts its
    confirm-severity findings so the agent knows a lift will need per-item confirmation.
    Sorted by name; ``[]`` on any failure; the lift itself stays per-item
    (``promote_memory``), never batch.
    """
    out: List[dict] = []
    try:
        from .portability import scan_portability
        from .provenance import _iter_memory_files, parse_frontmatter, resolve_dirs
        from .staleness import read_invalid_after, read_provenance

        if memory_dir is None:
            memory_dir, _ = resolve_dirs()
        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            fm = parse_frontmatter(text)
            meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
            mtype = (meta or {}).get("type") or fm.get("type")
            if mtype not in ("user", "feedback"):
                continue
            if read_invalid_after(text) is not None:
                continue
            cited, _ = read_provenance(text)
            findings = scan_portability(text, cited_paths=cited)
            if any(f.get("kind") == "repo_coupling" for f in findings):
                continue
            out.append(
                {
                    "name": os.path.splitext(os.path.basename(path))[0],
                    "type": mtype,
                    "consequential": sum(
                        1 for f in findings if f.get("severity") == "confirm"
                    ),
                }
            )
        out.sort(key=lambda d: d["name"])
    except Exception:
        return out
    return out


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Create a recall-ready memory file.")
    parser.add_argument("name", help="kebab/snake slug (also the filename stem)")
    parser.add_argument("description", help="one-line recall hook (indexed for recall)")
    parser.add_argument("--type", required=True, choices=VALID_TYPES)
    parser.add_argument(
        "--tier",
        default="project",
        choices=_VALID_TIERS,
        help="TEA-1: 'project' (default, git-native in-repo) or 'user' (machine-local user "
        "tier, recalled across every project; its floor pointer lands in the user tier's own "
        "MEMORY.md, never the shared project one)",
    )
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
    parser.add_argument(
        "--check",
        action="store_true",
        help="CAP-3 DRY RUN: score this candidate against the corpus for near-dupes and print "
        "the route (add / review) WITHOUT writing anything — used when draining captured candidates",
    )
    parser.add_argument(
        "--rationale",
        default=None,
        help="GOV-3: evidence trail fenced into the body as a trailing 'Rationale:' line "
        "(e.g. 'from session <sid>; replaces <neighbor> (similarity 0.9x); as of HEAD <sha>') "
        "— the git-committed WHY behind a consolidation-approved write",
    )
    parser.add_argument(
        "--confidence",
        default=None,
        choices=_VALID_CONFIDENCE,
        help="GOV-7: the author's trust dial (draft | verified | authoritative) — rendered "
        "at inject and in /hippo:recall, never a ranking input; omit for today's default",
    )
    args = parser.parse_args(argv)

    # CAP-3: dry-run decisioning — check a captured candidate BEFORE it can become a file, so a
    # duplicate routes to update/supersede instead of re-bloating the corpus. Writes nothing.
    if args.check:
        # RUL-3: with an explicit --memory-dir, check_candidate skips its own resolve — so
        # resolve the governance root here the same way the write path does, or the rules
        # dedup leg would silently never run on the CLI dry-run surface.
        check_repo_root = None
        if args.memory_dir:
            try:
                from .provenance import resolve_dirs

                _, check_repo_root = resolve_dirs()
            except Exception:
                check_repo_root = None
        decision = check_candidate(
            args.name,
            args.description,
            args.type,
            body=args.body,
            memory_dir=args.memory_dir,
            repo_root=check_repo_root,
        )
        print(f"route   : {decision['route']}")
        # GOV-3: the proposal-time git baseline — the honest anchor a reviewer can check
        # out ("as of HEAD <sha>"). source_commit exists only after the real write.
        if decision.get("baseline"):
            print(f"baseline: as of HEAD {decision['baseline'][:12]}")
        else:
            print("baseline: no git HEAD at proposal time (non-git corpus)")
        if decision["neighbors"]:
            print("neighbors (decide update-existing / supersede / skip — NAME the target):")
            for n in decision["neighbors"]:
                desc = n["description"].replace("\n", " ").strip()
                if len(desc) > 220:
                    desc = desc[:217].rstrip() + "…"
                print(f"  • {n['name']} (similarity {n['score']:.2f}) — {desc}")
        elif decision["route"] == "add":
            print("  → no near-duplicate cleared the threshold: safe to add as a new memory.")
        # RUL-3: rules-plane echoes flag but never flip the route — a wording decision.
        if decision.get("rule_neighbors"):
            print("warning : restates the governance plane — link, don't copy:")
            for r in decision["rule_neighbors"]:
                print(f"  • {r['file']} (overlap {r['score']:.2f}) — \"{r['preview']}\"")
        # SEN-1: the write ticket renders verbatim at this same approval-prompt step —
        # the gate stamp the approving human reads alongside the dup/rules-echo block.
        ticket_block = render_write_ticket(decision.get("ticket"))
        if ticket_block:
            print(ticket_block)
        if decision["note"]:
            print(f"note    : {decision['note']}")
        return 0

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
        tier=args.tier,
        rationale=args.rationale,
        confidence=args.confidence,
    )
    if res["error"]:
        print(f"error: {res['error']}")
        return 1
    print(f"created : {res['path']}")
    if res.get("tier") and res["tier"] != "project":
        print(f"tier    : {res['tier']} (recalled across every project; not in this repo's git)")
    print(f"indexed : {res['indexed']}")
    # LIF-5: the floor outcome is always printed; anything but a plain append carries its
    # machine-readable reason (never silence — /hippo:new tells the agent to surface it).
    floor = res["floor"] or {}
    if floor.get("status") == "appended":
        print("floor   : appended (only user/feedback get a floor pointer)")
    elif floor.get("status"):
        print(f"floor   : {floor['status']} — {floor.get('reason')}")
        if floor["status"] == "created-section":
            print(
                "  merge   : MEMORY.md drifted from the skeleton — if the old section was "
                "RENAMED (not deleted), fold its pointers into the re-created canonical "
                "section — see /hippo:new"
            )
    if res["related"]:
        print(f"related : {', '.join(res['related'])} (curate this — keep/trim/replace, see /hippo:new)")
    # BND-3 (+RCH-10): the result's warnings channel now prints on the CLI too — the
    # MCP reply already rendered it; the CLI silently dropped it.
    for w in res.get("warnings") or []:
        print(f"warning : {w}")
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
    # RUL-3: the preventive rules-plane warning. Same warn-only posture — the file is already
    # written; the agent decides whether to reference the rule instead of restating it.
    if res["rule_neighbors"]:
        n_rules = len(res["rule_neighbors"])
        noun = "a governance rule" if n_rules == 1 else f"{n_rules} governance rules"
        print(f"warning : restates {noun} — link, don't copy:")
        for r in res["rule_neighbors"]:
            print(f"  • {r['file']} (overlap {r['score']:.2f}) — \"{r['preview']}\"")
        print(
            "  decide  : cite the rule file (rules stay in the rules plane) / keep both if "
            "genuinely distinct — see /hippo:new"
        )
    if res["note"]:
        print(f"note    : {res['note']}")
    for warning in res["warnings"]:
        print(f"warning : {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
