"""Recall's tier plumbing: RUL-4 rules-plane pointers, TEA-1/TEA-3 user/private tier
fusion (in-memory merge only), the fused MEMORY.md floor + TEA-1 portable-floor
producer, and ``_ensure_index``. Decomposed out of ``recall.py`` as pure code motion;
every symbol stays importable at ``memory.recall.<name>`` via the façade's explicit
re-exports."""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from .build_index import (
    SCHEMA_VERSION,
    LoadedIndex,
    build_index,
    compute_bm25_stats,
    default_index_dir,
    extract_description,
    load_index,
)
from .lint_floor import floor_memory_names
from .provenance import (
    local_memory_dir,
    split_frontmatter,
    tier_index_dir,
    user_memory_dir,
)
from .staleness import RunContext

# --------------------------------------------------------------------------- #
# TEA-1 / TEA-3: multi-corpus fusion. The machine-local USER tier (TEA-1) and the
# in-repo gitignored PRIVATE tier (TEA-3) are recalled ALONGSIDE the project corpus so a
# person-scoped lesson learned in project A is known in project B. Each tier keeps its OWN
# persisted index — a merged manifest is NEVER written to disk (that is the no-leakage
# invariant: user/private text must never land in a project-committable file). The merge is
# purely IN MEMORY, at recall time, into ONE LoadedIndex so BM25/dense/RRF/floor/knee/graph/
# salience all run once over the combined candidate space, unchanged. Each merged entry gains
# a ``root`` (so the drift re-read / dangling check / view read open the RIGHT file) and a
# ``corpus`` origin label (so a hit is provenance-labeled). When only the project tier is
# present, the project index is returned UNCHANGED — the common case pays nothing and stays
# byte-identical to the single-corpus era.
# --------------------------------------------------------------------------- #
_PROJECT_TIER = "project"
_USER_TIER = "user"
_PRIVATE_TIER = "private"

# RUL-4: the rules-plane recall SOURCE label — not a memory tier. A governance section
# surfaced as a pointer carries corpus="rule" so both renderers label it; it is never a
# corpus entry (no import, no duplication — the rules plane stays its own authority).
_RULES_SOURCE = "rule"

# Human-facing origin markers for fused hits (format_results / recall_view). The project tier
# (and single-corpus recall, corpus=None) is unmarked so existing output stays byte-identical.
_CORPUS_MARKER = {
    _USER_TIER: " (user memory)",
    _PRIVATE_TIER: " (private memory)",
    _RULES_SOURCE: " (rule)",
}

# RUL-4: how many rules-plane pointers may APPEND to one recall (never displacing a corpus
# hit), and the QUERY-CONTAINMENT floor one must clear: |query ∩ section| / |query| over
# distinct content tokens. Containment — not BM25 — because the rules plane is routinely
# 1-5 sections, where Okapi idf mass is all-zero/negative (the degenerate-corpus case
# _bm25_dup_scores refuses to score); containment is scale-independent, deterministic, and
# reads "the section covers most of what the query asks". Conservative by construction: a
# long rambling prompt rarely clears 0.6, which is the right bias — a redundant rule
# pointer on every prompt costs more trust than a missed one.
_RULES_HIT_LIMIT = 2
_RULES_HIT_FLOOR = 0.6


def _rules_hit_floor() -> float:
    """``HIPPO_RULES_RECALL_FLOOR`` override for the rules-pointer relevance floor."""
    try:
        return float(os.environ.get("HIPPO_RULES_RECALL_FLOOR", _RULES_HIT_FLOOR))
    except Exception:
        return _RULES_HIT_FLOOR


def _rules_source_hits(
    q_tokens: List[str],
    index_dir: Optional[str],
    repo_root: Optional[str],
    *,
    start_rank: int = 0,
) -> List[dict]:
    """RUL-4: governance sections genuinely relevant to this query, as labelled POINTERS.

    Hot-path-safe by construction: ONE small-JSON read (``rules_plane.load_rules_cache``,
    built off-path at SessionStart) + pure set arithmetic — no model, no network, no file
    scan (inv6). Relevance is QUERY CONTAINMENT (``|q ∩ section| / |q|`` over distinct
    content tokens — see ``_RULES_HIT_FLOOR``'s comment for why not BM25 at this corpus
    scale); only sections clearing ``_rules_hit_floor()`` surface, capped at
    ``_RULES_HIT_LIMIT``. Result dicts carry every conventional key with
    ``corpus="rule"``/``via="rules"`` so both renderers label them — recall only ADDS a
    pointer; always-loaded rules are never demoted, moved, or copied into the corpus.
    Never raises; ``[]`` on any failure or when the cache is absent (the doctor
    rules-source check keeps that degradation legible).
    """
    try:
        from .rules_plane import load_rules_cache

        qset = set(q_tokens)
        if not qset:
            return []
        cache = load_rules_cache(index_dir)
        if not cache:
            return []
        entries = cache.get("entries") or []
        if not entries:
            return []
        floor = _rules_hit_floor()
        scored = []
        for i, e in enumerate(entries):
            overlap = qset.intersection(e.get("tokens") or [])
            if not overlap:
                continue
            containment = len(overlap) / len(qset)
            if containment >= floor:
                scored.append((i, containment))
        scored.sort(key=lambda t: (-t[1], entries[t[0]]["file"], entries[t[0]]["title"]))
        hits: List[dict] = []
        for i, norm in scored[:_RULES_HIT_LIMIT]:
            e = entries[i]
            hits.append(
                {
                    "name": e["title"],
                    "file": e["file"],
                    "description": e.get("preview") or "",
                    "score": round(float(norm), 6),
                    "rank": start_rank + len(hits) + 1,
                    "backend": "bm25",
                    "via": "rules",
                    "note": "",
                    "stale_banner": "",
                    "salience": None,
                    "corpus": _RULES_SOURCE,
                    "root": repo_root,
                }
            )
        return hits
    except Exception:
        return []


def _extra_recall_tiers(memory_dir: str) -> List[Tuple[str, str, str]]:
    """The NON-project tiers as ``[(corpus_dir, index_dir, label)]``, in precedence order (the
    project — prepended by ``_recall_tier_dirs`` — always wins a name collision; among the
    extras, the private tier added by TEA-3 precedes the user tier). Each tier declares its OWN
    index location so a single knob (``default_index_dir``/``tier_index_dir``) is chosen once,
    consistently used by recall, refresh, and the write path. The user tier's index is its plain
    sibling (``~/.claude/.memory-index`` — unique, machine-local); TEA-3's private tier NESTS
    its index inside ``memory.local`` because its sibling would collide with the project's."""
    dirs: List[Tuple[str, str, str]] = []
    # TEA-3: the in-repo private tier — precedes the user tier (a repo-local override of a
    # portable preference wins). Its index NESTS inside memory.local because its plain sibling
    # would be the project's own ``.claude/.memory-index``; nesting keeps it distinct AND sweeps
    # it into memory.local's own self-ignoring .gitignore, so private text never reaches git.
    local = local_memory_dir(memory_dir)
    if local:
        dirs.append((local, tier_index_dir(local), _PRIVATE_TIER))
    user = user_memory_dir()
    if user:
        dirs.append((user, default_index_dir(user), _USER_TIER))
    return dirs


def _recall_tier_dirs(memory_dir: str, index_dir: Optional[str]) -> List[Tuple[str, str, str]]:
    """Ordered ``[(corpus_dir, index_dir, label)]`` for recall fusion, project FIRST.

    A non-project tier is included only when its dir EXISTS and is distinct from the project
    dir — an unconfigured machine lists only the project tier and the merge is a no-op.
    """
    project_index = index_dir or default_index_dir(memory_dir)
    tiers: List[Tuple[str, str, str]] = [(memory_dir, project_index, _PROJECT_TIER)]
    try:
        project_abs = os.path.abspath(memory_dir)
    except Exception:
        project_abs = memory_dir
    for tier_dir, tier_index, label in _extra_recall_tiers(memory_dir):
        try:
            if not tier_dir or os.path.abspath(tier_dir) == project_abs:
                continue
            if not os.path.isdir(tier_dir):
                continue
        except Exception:
            continue
        tiers.append((tier_dir, tier_index, label))
    return tiers


def _merge_loaded_indexes(
    loadeds: List[Tuple[LoadedIndex, str, str]]
) -> Optional[LoadedIndex]:
    """Merge per-corpus ``LoadedIndex`` objects into ONE in-memory index.

    First-wins dedup by entry ``name`` (the project tier is first, so it owns any cross-tier
    slug collision). Every kept entry is tagged with its ``root`` (absolute corpus dir) and
    ``corpus`` (origin label). Dense is vstacked ONLY when every tier is dense-ready under the
    SAME model — otherwise the merged view degrades to BM25-only (a transient state healed at
    the next per-tier dense rebuild), never a half-valid matrix. BM25 stats are recomputed once
    over the unified doc space (entries then body chunks), byte-for-byte the way ``build_index``
    assembles them. Returns the single index unchanged when there is nothing to merge.
    """
    loadeds = [(li, root, label) for (li, root, label) in loadeds if li is not None]
    if not loadeds:
        return None
    if len(loadeds) == 1:
        return loadeds[0][0]  # single corpus -> unchanged (byte-identical fast path)

    models = {li.model for li, _r, _l in loadeds if li.model}
    build_dense = len(models) <= 1 and all(
        li.dense_ready and li.dense is not None for li, _r, _l in loadeds
    )

    merged_entries: List[dict] = []
    merged_chunks: List[dict] = []
    dense_vectors: List = []
    seen_names: set = set()
    model: Optional[str] = None

    for li, root, label in loadeds:
        if li.model and model is None:
            model = li.model
        remap: Dict[int, int] = {}  # this tier's entry-index -> merged entry-index
        for old_i, e in enumerate(li.entries):
            name = e.get("name")
            if name in seen_names:
                continue  # a higher-precedence tier already owns this slug
            seen_names.add(name)
            ne = dict(e)
            ne["root"] = root
            ne["corpus"] = label
            remap[old_i] = len(merged_entries)
            if build_dense:
                ne["row"] = len(dense_vectors)
                dense_vectors.append(li.dense[e.get("row")])
            else:
                ne["row"] = None
            merged_entries.append(ne)
        for c in li.body_chunks:
            parent = c.get("entry")
            if parent not in remap:
                continue  # parent entry was deduped away -> drop its chunks too
            nc = dict(c)
            nc["entry"] = remap[parent]
            if build_dense:
                nc["row"] = len(dense_vectors)
                dense_vectors.append(li.dense[c.get("row")])
            else:
                nc["row"] = None
            merged_chunks.append(nc)

    merged_dense = None
    if build_dense and dense_vectors:
        import numpy as np

        merged_dense = np.vstack(dense_vectors)

    bm25 = compute_bm25_stats(
        [e.get("tokens") or [] for e in merged_entries]
        + [c.get("tokens") or [] for c in merged_chunks]
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "model": model if merged_dense is not None else None,
        "dense_ready": merged_dense is not None,
        "dim": int(merged_dense.shape[1]) if merged_dense is not None else None,
        "count": len(merged_entries),
        "entries": merged_entries,
        "body_chunks": merged_chunks,
        "bm25": bm25,
    }
    return LoadedIndex(manifest, merged_dense)


def _fuse_recall_tiers(
    project_idx: LoadedIndex,
    memory_dir: str,
    index_dir: Optional[str],
    repo_root: Optional[str],
) -> LoadedIndex:
    """Fuse the machine-local user/private tiers into the (already loaded, already trust-gated)
    project index. Returns the project index UNCHANGED when no extra tier exists. Never raises —
    a tier that fails to load is skipped, so recall degrades to project-only, never crashes. The
    extra tiers are the current user's OWN corpora (machine-local / created locally by init), so
    they are trusted by construction and bypass the SEC-1 gate that only guards cloned project
    corpora."""
    try:
        tiers = _recall_tier_dirs(memory_dir, index_dir)
        if len(tiers) == 1:
            return project_idx
        loadeds: List[Tuple[LoadedIndex, str, str]] = [(project_idx, memory_dir, _PROJECT_TIER)]
        for tdir, tidx, label in tiers:
            if label == _PROJECT_TIER:
                continue
            li = _ensure_index(None, tdir, tidx)
            if li is not None and len(li):
                loadeds.append((li, tdir, label))
        merged = _merge_loaded_indexes(loadeds)
        return merged if merged is not None else project_idx
    except Exception:
        return project_idx


def fused_floor_names(memory_dir: str, index_dir: Optional[str] = None) -> set:
    """The floor drawn from BOTH corpora (TEA-1): the union of every recall tier's MEMORY.md
    floor pointers (project + user tier + private tier). Recall's display-layer dedup subtracts
    this so a floor-pinned memory — whichever tier it lives in — is never re-injected on demand.
    Never raises: a tier whose floor can't be read contributes the empty set."""
    names: set = set()
    try:
        for tdir, _tidx, _label in _recall_tier_dirs(memory_dir, index_dir):
            try:
                names |= floor_memory_names(tdir)
            except Exception:
                continue
    except Exception:
        return floor_memory_names(memory_dir) if memory_dir else set()
    return names


# --- Floor-from-both delivery (TEA-1) ------------------------------------------------ #
# The project floor reaches context NATIVELY (the harness always-loads the symlinked
# MEMORY.md and its linked bodies). The machine-local user tier and the in-repo private tier
# have NO native always-load channel, so this SessionStart producer injects THEIR floor
# (user/feedback) memories each session — bounded — so the floor is genuinely "drawn from
# BOTH" corpora. Silent when no extra tier has a floor; degrades to silence for a teammate
# who lacks a private file (a pointer with no target simply contributes nothing).
_PORTABLE_FLOOR_MAX_ITEMS = 20
_PORTABLE_FLOOR_MAX_CHARS = 3000
_PORTABLE_FLOOR_BODY_CHARS = 500


def portable_floor_producer(
    memory_dir: str, repo_root: str, ctx: Optional["RunContext"] = None
) -> Optional[str]:
    """SessionStart producer: the always-on floor of the user tier (+ private tier). Never
    raises. ``ctx`` (LIF-6's shared per-run ``RunContext``) is unused — declared only so every
    producer in ``PRODUCERS`` shares ONE call shape."""
    try:
        blocks: List[str] = []
        for tdir, _tidx, label in _recall_tier_dirs(memory_dir, None):
            if label == _PROJECT_TIER:
                continue  # the project floor is delivered natively (INT-4) — never re-inject it
            for name in sorted(floor_memory_names(tdir)):
                if len(blocks) >= _PORTABLE_FLOOR_MAX_ITEMS:
                    break
                try:
                    with open(os.path.join(tdir, f"{name}.md"), "r", encoding="utf-8") as fh:
                        text = fh.read()
                except Exception:
                    continue  # floor pointer whose target is absent (teammate lacks it) -> skip
                desc = (extract_description(text) or "").replace("\n", " ").strip()
                _fm, body = split_frontmatter(text)
                body = (body or "").strip()
                if len(body) > _PORTABLE_FLOOR_BODY_CHARS:
                    body = body[: _PORTABLE_FLOOR_BODY_CHARS - 1].rstrip() + "…"
                line = f"  • {name} ({label} tier)"
                if desc:
                    line += f" — {desc}"
                blocks.append(line + (f"\n      {body}" if body else ""))
        if not blocks:
            return None
        out = (
            "🧠 Portable memory (always-on across projects — user & private tiers):\n"
            + "\n".join(blocks)
        )
        if len(out) > _PORTABLE_FLOOR_MAX_CHARS:
            out = out[: _PORTABLE_FLOOR_MAX_CHARS - 16].rstrip() + "\n…(truncated)"
        return out
    except Exception:
        return None


def _ensure_index(
    index: Optional[LoadedIndex], memory_dir: str, index_dir: Optional[str]
) -> Optional[LoadedIndex]:
    if index is not None:
        return index
    # Never-opted-in guard (SEC-3): a project with no .claude/memory corpus must gain
    # ZERO derived files — without this, the implicit build below mkdir-p's the index
    # dir (creating .claude/ itself) in every repo the user merely opens.
    if not memory_dir or not os.path.isdir(memory_dir):
        return None
    index_dir = index_dir or default_index_dir(memory_dir)
    loaded = load_index(index_dir)
    if loaded is not None:
        return loaded
    # No persisted index yet: build an in-memory BM25 view WITHOUT touching the dense model
    # (a hook must never block on indexing). Disable dense for this implicit build.
    prev = os.environ.get("HIPPO_DISABLE_DENSE")
    os.environ["HIPPO_DISABLE_DENSE"] = "1"
    try:
        build_index(memory_dir, index_dir)
        return load_index(index_dir)
    except Exception:
        return None
    finally:
        if prev is None:
            os.environ.pop("HIPPO_DISABLE_DENSE", None)
        else:
            os.environ["HIPPO_DISABLE_DENSE"] = prev
