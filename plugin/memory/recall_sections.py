"""HOT-11: folded digest sections at recall time — extra rows in explicit recall, named rows
in the prompt hook, what a section row carries, and the graph edges a named section adds.

Sections never compete with live memories for a top-k slot:

  - explicit recall (``hippo recall``, the MCP recall tool, the inspect views, ``eval``):
    the memory ranking runs exactly as without sections; then the best ``extra_k()``
    sections, ranked among sections only by the same recall pipeline over the section
    sub-index (same lanes, dense floor and abstention gate), are APPENDED after the memory
    rows (``extra``), the way rules-plane pointers are: they hold no slot, feed no knee
    comparison and displace no memory. Each carries ``section: True``;
  - the prompt hook: only when the prompt names a section's slug, and then only the named
    sections, fused into the pool as journal-lane rows (``fuse``/``admit``). A section
    never reaches the hook unnamed, whether or not the corpus declared ``journals``.

A corpus whose manifest has no ``sections`` key pays one dict lookup. An incomplete
sub-index (rows still being embedded) is not used while the main index is dense, so recall
never drops to BM25-only to show a section.

Graph: a ``[[slug]]`` that names a section is not a dangling link and never resolves to the
whole digest. When a named section is fused, it carries its own links, and its digest keeps
only the out-links written outside its sections (``section_edges``). Explicit recall does
not expand into sections: a section appears there only as an appended row.

Leaf helper: imports ``recall_tiers``, ``index_sections`` and ``recall_rank``'s query-vector
window (HOT-15: the section pass reuses the memory pass's query embedding), never the recall
façade (the section pass is handed the ``recall`` function it runs).
"""

from __future__ import annotations

from typing import Iterable, Optional

from .recall_rank import reuse_query_vector as _reuse_query_vector
from .recall_tiers import _PROJECT_TIER, _merge_loaded_indexes


def named_sections(manifest: Optional[dict], named: Iterable[str]) -> set:
    """The section slugs in ``manifest``'s ``sections`` record that ``named`` (lowercased
    ids, ``lanes.named_ids``) names. Empty when the corpus has no sections."""
    rec = (manifest or {}).get("sections")
    slugs = rec.get("slugs") if isinstance(rec, dict) else None
    if not slugs:
        return set()
    named = set(named)
    return {s for s in slugs if s.lower() in named}


def fuse(idx, memory_dir: Optional[str], index_dir: Optional[str], lanes=None):
    """The hook's ``idx`` with the sections its prompt names fused in. ``idx`` unchanged for
    explicit recall (``lanes`` None: sections are appended rows there, ``extra``), when the
    corpus has no sections, or when the prompt names none. Never raises."""
    try:
        if lanes is None or idx is None or not (idx.manifest or {}).get("sections"):
            return idx
        want = named_sections(idx.manifest, lanes.named())
        return admit(idx, memory_dir, index_dir, want) if want else idx
    except Exception:
        return idx


# How many section rows explicit recall appends after the memory rows, chosen on the field
# corpus's probe set (see the commit body). override: HIPPO_SECTION_ROWS (0 turns them off).
_EXTRA_K = 5
_EVAL_FLAG = "_append_sections"  # an in-memory marker eval sets on the index it loaded


def extra_k() -> int:
    """``HIPPO_SECTION_ROWS`` override (an int >= 0); junk -> the default."""
    import os

    raw = os.environ.get("HIPPO_SECTION_ROWS")
    try:
        return max(0, int(raw)) if raw is not None and raw.strip() else _EXTRA_K
    except ValueError:
        return _EXTRA_K


def for_eval(idx):
    """Mark an index eval loaded so its recalls append section rows like explicit recall
    does (in memory only; nothing is written). ``idx`` itself, for chaining."""
    if idx is not None and (idx.manifest or {}).get("sections"):
        idx.manifest[_EVAL_FLAG] = True
    return idx


# HOT-15: explicit recall in a long-lived process (the MCP recall tool, eval) re-read the
# section sub-index — a 4.7 MB manifest and its matrix on the field corpus — on every call.
# It is kept per index dir under the sub-manifest's stat signature (the matrix is named by,
# and written before, the manifest that points at it, so a new matrix always comes with a
# new manifest) and the dense switch. The section pass only reads it.
_SEC_MEMO: dict = {}


def _loaded_sections(index_dir: Optional[str]):
    import os

    from .build_index import dense_disabled
    from .index_sections import SECTIONS_MANIFEST, load_sections

    if not index_dir:
        return None
    try:
        st = os.stat(os.path.join(index_dir, SECTIONS_MANIFEST))
    except OSError:
        _SEC_MEMO.pop(index_dir, None)
        return None
    sig = (st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino, dense_disabled())
    hit = _SEC_MEMO.get(index_dir)
    if hit is not None and hit[0] == sig:
        return hit[1]
    sec = load_sections(index_dir)
    _SEC_MEMO.clear()  # one corpus per process in practice; never grows
    if sec is not None:
        _SEC_MEMO[index_dir] = (sig, sec)
    return sec


def extra(rows: list, query: str, idx, memory_dir, index_dir, *, explicit: bool,
          consented=None, recall_fn=None) -> list:
    """``rows`` with the best ``extra_k()`` section rows appended after the memory rows
    (before any rules-plane pointer), every row renumbered. Only for explicit recall (or an
    index ``for_eval`` marked) on a corpus with sections; ``rows`` itself otherwise.

    The sections are ranked by ``recall_fn`` (``recall.recall``) over the section sub-index
    alone, so the same lanes, dense floor and abstention gate decide them: a query no
    section clears adds nothing, and a query the memories abstain on stays an abstention
    (measured: answering from sections alone cost one of emgl's 20 off-topic probes).
    ``consented`` (the SEC-6 baseline) withholds a section whose
    digest drifted from it, as the memory walk does. Never raises."""
    try:
        man = (idx.manifest if idx is not None else None) or {}
        if not man.get("sections") or not (explicit or man.get(_EVAL_FLAG)) or recall_fn is None:
            return rows
        n = extra_k()
        mem = [r for r in rows if r.get("corpus") != "rule"]
        if n <= 0 or not mem:
            return rows  # extra rows ride an answer; they never turn an abstention into one
        from .build_index import default_index_dir

        if not index_dir and memory_dir:
            index_dir = default_index_dir(memory_dir)
        sec = _loaded_sections(index_dir)
        if sec is None or not len(sec):
            return rows
        if idx.dense_ready and (not sec.dense_ready or sec.model != idx.model):
            return rows  # still catching up: never rank sections BM25-only beside dense rows
        with _reuse_query_vector(query):  # HOT-15: the memory pass already embedded it
            hits = recall_fn(query, k=n, index=sec)
        if consented is not None and memory_dir:
            import os

            from .trust import file_sha256

            hits = [h for h in hits if (file_sha256(os.path.join(memory_dir, h["file"]))
                                        == consented.get(h.get("digest")))]
        if not hits:
            return rows
        for h in hits:
            h["section"] = True
        rules = [r for r in rows if r.get("corpus") == "rule"]
        out = mem + hits + rules
        for i, r in enumerate(out):
            r["rank"] = i + 1
        return out
    except Exception:
        return rows


def admit(idx, memory_dir: Optional[str], index_dir: Optional[str], slugs: Optional[Iterable[str]]):
    """THE SEAM: ``idx`` with the sections ``slugs`` fused in (every section when None).

    The prompt hook calls it with the slugs the prompt names; an issue join (GP-S4's
    ``lanes.issue_join_admissions``) reaches it the same way, because ``fuse`` reads
    ``LaneRequest.named()``, which already unions that join's admissions. Fused section
    rows are ``journal``-lane rows, so ``LaneRequest.withhold`` admits them as named
    journals on a corpus that declares ``journals``. Never raises; ``idx`` on any failure."""
    try:
        from .build_index import compute_bm25_stats, default_index_dir
        from .index_sections import load_sections

        if not index_dir and memory_dir:
            index_dir = default_index_dir(memory_dir)
        sec = load_sections(index_dir)
        if sec is None or not len(sec):
            return idx
        if idx.dense_ready and (not sec.dense_ready or sec.model != idx.model):
            return idx  # still catching up: never cost the main index its dense lane
        if slugs is not None:
            sec = _subset(sec, set(slugs), compute_bm25_stats)
            if not len(sec):
                return idx
        root = memory_dir
        merged = _merge_loaded_indexes([(idx, root, _PROJECT_TIER), (sec, root, _PROJECT_TIER)])
        if merged is None or merged is idx:
            return idx
        tagged = any("corpus" in e for e in idx.entries)
        for ne, oe in zip(merged.entries, idx.entries):  # idx's entries keep their own tags
            for key in ("root", "corpus"):
                if key in oe:
                    ne[key] = oe[key]
                else:
                    ne.pop(key, None)
        for ne in merged.entries[len(idx.entries):]:
            if not tagged:
                ne.pop("corpus", None)
        if not idx.manifest.get("fused_without_dense"):
            merged.manifest.pop("fused_without_dense", None)  # only ever set by idx itself
        for key, val in idx.manifest.items():
            merged.manifest.setdefault(key, val)
        merged.manifest["section_digest_out"] = sec.manifest.get("digest_out") or {}
        return merged
    except Exception:
        return idx


def _subset(sec, keep: set, compute_bm25_stats):
    """A ``LoadedIndex`` of only the sections named in ``keep`` (and their body chunks)."""
    from .build_index import LoadedIndex

    remap = {}
    entries = []
    rows = []
    for i, e in enumerate(sec.entries):
        if e.get("name") in keep:
            remap[i] = len(entries)
            ne = dict(e)
            if sec.dense is not None:
                rows.append(sec.dense[e["row"]])
                ne["row"] = len(rows) - 1
            entries.append(ne)
    chunks = []
    for c in sec.body_chunks:
        if c.get("entry") in remap:
            nc = dict(c, entry=remap[c["entry"]])
            if sec.dense is not None:
                rows.append(sec.dense[c["row"]])
                nc["row"] = len(rows) - 1
            chunks.append(nc)
    manifest = dict(sec.manifest, entries=entries, body_chunks=chunks, count=len(entries))
    manifest["bm25"] = compute_bm25_stats([e.get("tokens") or [] for e in entries] + [c.get("tokens") or [] for c in chunks])
    dense = None
    if sec.dense is not None and rows:
        import numpy as np

        dense = np.vstack(rows)
    else:
        manifest["dense_ready"] = False
    return LoadedIndex(manifest, dense)


def section_edges(edges: Optional[dict], idx) -> Optional[dict]:
    """``edges`` (``links_cache.load_edges``) with the fused sections in it: each section a
    node with its own out- and in-links, and each digest left with only the out-links
    written outside its sections. ``edges`` unchanged when no section is fused. Copies
    every record it changes; never raises."""
    try:
        if not edges or idx is None:
            return edges
        secs = [e for e in idx.entries if e.get("digest")]
        if not secs:
            return edges
        out = dict(edges)

        def rec(name):
            r = out.get(name)
            if r is None:
                return None
            if r is edges.get(name):
                r = {"out": set(r.get("out") or ()), "in": set(r.get("in") or ()),
                     "typed_out": r.get("typed_out") or {}, "typed_in": r.get("typed_in") or {}}
                out[name] = r
            return r

        for digest, own in ((idx.manifest or {}).get("section_digest_out") or {}).items():
            d = rec(digest)
            if d is None:
                continue
            keep = set(own)
            for t in d["out"] - keep:
                tr = rec(t)
                if tr is not None:
                    tr["in"].discard(digest)
            d["out"] = d["out"] & keep
        for e in secs:
            out.setdefault(e["name"], {"out": set(), "in": set(), "typed_out": {}, "typed_in": {}})
        for e in secs:
            name = e["name"]
            for t in e.get("links_out") or ():
                if t in out and t != name:
                    rec(name)["out"].add(t)
                    rec(t)["in"].add(name)
            for s in e.get("links_in") or ():
                if s in out and s != name:
                    rec(name)["in"].add(s)
                    rec(s)["out"].add(name)
        return out
    except Exception:
        return edges


def result_keys(entry: dict) -> dict:
    """The additive keys a section row carries on a recall result: its digest's stem and
    the heading's line. ``{}`` for every other row, so their dicts are unchanged."""
    if not entry.get("digest"):
        return {}
    return {"digest": entry["digest"], "line": entry.get("line"), "section": True}


def where(row: dict) -> str:
    """``file:line`` for a section row, the bare file otherwise."""
    line = row.get("line")
    return f"{row.get('file')}:{line}" if row.get("digest") and line else str(row.get("file"))
