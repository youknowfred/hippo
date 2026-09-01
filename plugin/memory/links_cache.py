"""Persisted wikilink edge cache (GRA-6) — ``links.json`` in the index dir.

The cache half of the ``links`` family (module-size ratchet split, GRF-6 round;
``links.py`` remains the façade and re-exports every name here — import from
``memory.links`` unless you are inside the family). ``build_index`` writes the
fully-resolved graph to ``links.json``, keyed by a per-file STAT signature
``[st_mtime_ns, st_size]`` — deliberately NOT the manifest's ``doc_text`` hash, because
wikilinks live in BODIES and a body edit does not change ``doc_text`` (name + description
only); a hash-keyed cache would go silently stale on exactly the edits that change edges.
``links.build_graph(memory_dir, index_dir=...)`` reconstructs the graph from this cache
after ONE stat sweep (zero file reads); any mismatch/corruption falls back to the full
re-read there. ``load_edges`` is the O(1) recall-time loader (GRA-1): links.json only, no
corpus scan — recall tolerates a slightly-stale edge list the same way it tolerates a
stale index. Read-only except ``write_links_cache`` (atomic tmp + ``os.replace``, its own
write-discipline pin); never raises into a caller.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from .links_graph import LinkGraph, dream_edges_admitted, normalize_slug
from .provenance import _is_memory_filename

# links.json schema — independent of the manifest's SCHEMA_VERSION (the two files evolve
# separately; a manifest bump must not silently invalidate a perfectly good edge cache).
# v2 (GRA-4): per-file "typed" resolved-relation maps + top-level "typed_raw"/
# "typed_unresolved" — a v1 cache reads as a miss and heals with one rebuild.
# v3 (DRM-6): the typed-relation set gained "derives-from" — a v2 cache's typed maps
# predate the relation and would silently serve it as absent, so the bump forces one
# rebuild (inv5: a clean break, never a compat shim).
# v4 (COR-20): the parser stopped reading code spans/fences as link surface — a v3
# cache's adjacency was built by the OLD parser and still carries the phantom edges
# (this repo's own corpus had four), which a reader would silently keep serving. Same
# reasoning as v3: bump, force one rebuild, no shim.
# v5 (GRF-6): the payload gained the top-level "planned" map (per-stem deliberate-
# forward-reference declarations) — a v4 cache predates the key and would silently
# serve every declaration as absent, re-nagging the exact links the idiom exists to
# quiet. Same reasoning as v3/v4: bump, force one rebuild, no shim.
LINKS_SCHEMA_VERSION = 5
_LINKS_CACHE_NAME = "links.json"


# --------------------------------------------------------------------------- #
# GRA-6: persisted edge cache (links.json in the index dir)
# --------------------------------------------------------------------------- #
def write_links_cache(index_dir: str, graph: LinkGraph, sigs: Dict[str, List[int]]) -> None:
    """Persist ``graph`` (+ per-file stat ``sigs``) to ``index_dir/links.json``. Never raises.

    ``sigs`` maps stem -> ``[st_mtime_ns, st_size]`` captured by the caller AT READ TIME
    (build_index stats each file just before reading it, so a write racing the build makes
    the sig look STALE on the next check — never fresh-but-wrong). The payload carries
    everything a ``LinkGraph`` view needs — including ``raw_targets``, which lint's
    slug-mismatch check reads and which is NOT derivable from the resolved adjacency —
    so a cache hit reproduces the full graph, not a lossy subset. Written atomically
    (tmp + ``os.replace``, the manifest's COR-12 pattern) so a reader never sees a torn
    file. A stem missing from ``sigs`` gets ``[0, 0]``, which can never match a real stat
    — it degrades to a cache miss, the safe direction.
    """
    try:
        payload = {
            "schema_version": LINKS_SCHEMA_VERSION,
            # DRM-3: which admission view this cache was built under (see
            # ``dream_edges_admitted``). A reader under the OTHER view treats the cache as
            # a miss — an admitted-view cache must never serve the filtered A/B arm, nor a
            # filtered build poison the default view. Absent key (pre-DRM cache) reads as
            # True — those caches were all built admitted.
            "dream_admitted": dream_edges_admitted(),
            "files": {
                stem: {
                    "sig": list(sigs.get(stem) or (0, 0)),
                    "outbound": sorted(graph.adjacency.get(stem, ())),
                    # GRA-4: resolved typed relations, sparse ({} when the stem declares
                    # none) — recall's O(1) loader reads these, never the corpus.
                    "typed": {
                        rel: sorted(targets)
                        for rel, targets in graph.typed.get(stem, {}).items()
                    },
                }
                for stem in graph.files
            },
            "alias_to_file": dict(graph._alias_to_stem),
            "ambiguous": {a: sorted(c) for a, c in graph._ambiguous.items()},
            "unresolved": {s: list(t) for s, t in graph.unresolved.items()},
            "raw_targets": {s: list(t) for s, t in graph.raw_targets.items()},
            # GRA-4: raw + unresolved typed targets round-trip too (lint's dangling-typed
            # check reads them, and neither is derivable from the resolved map alone).
            "typed_raw": {s: {r: list(t) for r, t in m.items()} for s, m in graph.typed_raw.items()},
            "typed_unresolved": {
                s: {r: list(t) for r, t in m.items()} for s, m in graph.typed_unresolved.items()
            },
            # GRF-6: per-stem ``planned:`` declarations round-trip so the CACHED lint
            # path classifies deliberate forward references with zero file reads (v5).
            "planned": {s: list(t) for s, t in graph.planned_raw.items()},
        }
        path = os.path.join(index_dir, _LINKS_CACHE_NAME)
        tmp = path + f".tmp.{os.getpid()}"  # COR-17: unique per writer — concurrent processes must not share a tmp
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
    except Exception:
        pass  # a failed cache write must never break an index build


def _load_links_payload(index_dir: str) -> Optional[dict]:
    """Parse ``links.json`` -> payload dict, or None (missing / corrupt / wrong schema)."""
    try:
        with open(os.path.join(index_dir, _LINKS_CACHE_NAME), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict):
            return None
        if payload.get("schema_version") != LINKS_SCHEMA_VERSION:
            return None
        # DRM-3: a cache built under the other HIPPO_DREAM admission view is a MISS (the
        # safe direction — one wasted rebuild, never a wrong-view edge list served).
        if bool(payload.get("dream_admitted", True)) != dream_edges_admitted():
            return None
        if not all(
            isinstance(payload.get(k), dict)
            for k in (
                "files",
                "alias_to_file",
                "ambiguous",
                "unresolved",
                "raw_targets",
                "typed_raw",
                "typed_unresolved",
                "planned",
            )
        ):
            return None
        return payload
    except Exception:
        return None


def _stat_signatures(memory_dir: str) -> Optional[Dict[str, List[int]]]:
    """One ``os.scandir`` stat sweep -> ``{stem: [st_mtime_ns, st_size]}``; None on failure.

    NO file reads — this is the cached path's entire I/O cost. Deliberately NOT built on
    ``_iter_memory_files`` (which is the corpus READER's entry point and what the
    zero-reads test probes); the membership filter itself is still the one canonical
    ``_is_memory_filename``, so the sweep sees exactly the files the graph builder reads.
    """
    try:
        sigs: Dict[str, List[int]] = {}
        with os.scandir(memory_dir) as it:
            for entry in it:
                if not _is_memory_filename(entry.name):
                    continue
                st = entry.stat()
                sigs[entry.name[:-3]] = [st.st_mtime_ns, st.st_size]
        return sigs
    except Exception:
        return None


def links_cache_fresh(index_dir: str, sigs: Dict[str, List[int]]) -> bool:
    """True when ``links.json`` exists and its per-file sigs exactly match ``sigs``.

    Used by ``refresh_index``'s no-op short-circuit: the corpus-unchanged check compares
    ``doc_text`` hashes, which body edits do NOT perturb — so the short-circuit must
    independently verify the edge cache before skipping the rebuild, or a body-only edit
    (the exact kind that changes wikilinks) would leave a stale links.json in place forever.
    """
    payload = _load_links_payload(index_dir)
    if payload is None:
        return False
    try:
        cached = {s: list(rec.get("sig") or []) for s, rec in payload["files"].items()}
        return cached == {s: list(v) for s, v in sigs.items()}
    except Exception:
        return False


def _require_dict(value) -> dict:
    """``value`` if it is a dict, else raise — the typed-map twin of the ``outbound``
    list check in ``_graph_from_payload`` (wrong-shape corruption must read as a cache
    miss, never iterate into garbage)."""
    if not isinstance(value, dict):
        raise ValueError("typed relation map must be a dict")
    return value


def _graph_from_payload(memory_dir: str, payload: dict) -> LinkGraph:
    """Reconstruct a full ``LinkGraph`` view from a validated cache payload (no I/O).

    Raises on any malformed field — ``build_graph`` treats that as a cache miss and falls
    back to the full re-read. The reverse adjacency is re-derived here (O(E), in-memory)
    rather than persisted: it is the exact transpose of ``outbound`` and storing both would
    just create a second copy that could disagree.
    """
    files_map = payload["files"]
    g = LinkGraph.__new__(LinkGraph)
    g.memory_dir = memory_dir
    g._texts_in = None
    g.files = list(files_map.keys())
    g._alias_to_stem = {str(a): str(s) for a, s in payload["alias_to_file"].items()}
    g._ambiguous = {str(a): set(c) for a, c in payload["ambiguous"].items()}
    # Only consulted during construction's soft-alias tiering, but kept coherent anyway —
    # it is trivially derivable and a half-initialized object invites subtle breakage.
    g._stem_tier = {normalize_slug(s) for s in g.files}
    g.raw_targets = {str(s): list(t) for s, t in payload["raw_targets"].items()}
    g.unresolved = {str(s): list(t) for s, t in payload["unresolved"].items()}
    g.typed_raw = {
        str(s): {str(r): list(t) for r, t in _require_dict(m).items()}
        for s, m in payload["typed_raw"].items()
    }
    g.typed_unresolved = {
        str(s): {str(r): list(t) for r, t in _require_dict(m).items()}
        for s, m in payload["typed_unresolved"].items()
    }
    g.planned_raw = {str(s): list(t) for s, t in payload["planned"].items()}
    g.adjacency = {}
    g._inbound = {stem: set() for stem in g.files}
    g.typed = {}
    g._typed_inbound = {}
    for stem, rec in files_map.items():
        outbound = rec["outbound"]
        if not isinstance(outbound, list):
            # Valid-JSON-but-wrong-shape corruption (e.g. a hand-edited string) would
            # otherwise iterate as CHARACTERS into a garbage adjacency — raise instead,
            # which build_graph treats as a cache miss.
            raise ValueError("outbound must be a list")
        out = set(outbound)
        g.adjacency[stem] = out
        for tgt in out:
            g._inbound.setdefault(tgt, set()).add(stem)
        # GRA-4: typed edges + their transpose, re-derived exactly like _inbound above
        # (same rationale: persisting both directions invites disagreement). Wrong-shape
        # corruption raises for the same cache-miss treatment as ``outbound``.
        for rel, targets in _require_dict(rec.get("typed", {})).items():
            if not isinstance(targets, list):
                raise ValueError("typed targets must be a list")
            if not targets:
                continue
            g.typed.setdefault(stem, {})[str(rel)] = set(targets)
            for tgt in targets:
                g._typed_inbound.setdefault(tgt, {}).setdefault(str(rel), set()).add(stem)
    return g


def load_edges(index_dir: str) -> Optional[Dict[str, Dict[str, object]]]:
    """O(1)-load edge list for recall (GRA-1 + GRA-4):
    ``{stem: {"out", "in", "typed_out", "typed_in"}}``.

    Reads ``links.json`` ONLY — no corpus scan, no stat sweep. Recall-time expansion
    tolerates a slightly-stale edge list the same way it tolerates a stale index (the
    SessionStart refresh re-syncs both), and the hot path must not pay an O(N) stat sweep
    per prompt for freshness it does not need. Returns None (never raises) when the cache
    is absent/corrupt — the caller simply skips expansion.

    GRA-4: ``typed_out``/``typed_in`` are ``{relation: set(stems)}`` (both always present,
    ``{}`` when the stem carries none) — ``typed_in`` is the direction recall consumes
    ("who supersedes/contradicts THIS stem?"). Same corpus-stem filter as ``out``/``in``:
    an edge whose endpoint left the cache's stem set is dropped, so a consumer never sees
    a typed edge pointing outside the corpus snapshot the cache describes.
    """
    try:
        payload = _load_links_payload(index_dir)
        if payload is None:
            return None
        files_map = payload["files"]
        edges: Dict[str, Dict[str, object]] = {
            str(stem): {"out": set(), "in": set(), "typed_out": {}, "typed_in": {}}
            for stem in files_map
        }
        for stem, rec in files_map.items():
            outbound = rec.get("outbound")
            if not isinstance(outbound, list):
                return None  # wrong-shape corruption -> absent, same as any other corruption
            for tgt in outbound:
                if tgt in edges:  # a resolved edge always targets a corpus stem
                    edges[stem]["out"].add(tgt)
                    edges[tgt]["in"].add(stem)
            typed = rec.get("typed", {})
            if not isinstance(typed, dict):
                return None
            for rel, targets in typed.items():
                if not isinstance(targets, list):
                    return None
                for tgt in targets:
                    if tgt in edges:
                        edges[stem]["typed_out"].setdefault(rel, set()).add(tgt)
                        edges[tgt]["typed_in"].setdefault(rel, set()).add(stem)
        return edges
    except Exception:
        return None
