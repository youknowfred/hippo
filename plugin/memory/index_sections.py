"""HOT-11: the sections of a fold digest, as recall units of their own.

A corpus that declares ``fold_digests`` retires idle memories into digest files: the old
memory survives as a ``### <slug>`` section. Recall ranks a memory by its description and
the first ~1,500 characters of its body, so a section deep in a digest was unfindable.

This module turns each section into an entry of a SUB-INDEX beside the main one, in the
same index dir, never a row of the main index (its entries, body chunks, dense matrix and
BM25 statistics are untouched):

  - ``sections.json``: a manifest of the same shape as ``manifest.json`` (``entries``,
    ``body_chunks``, ``bm25``, ``dense_ready``, ``dense_file``), so recall loads it as a
    ``LoadedIndex`` and fuses it like any other tier (``recall_sections``);
  - ``sections.<id>.npy``: its dense matrix, under a name no other build uses, named by the
    manifest that was written after it (IDX-1's discipline: the last manifest to land names
    the matrix its own build wrote). Superseded matrices are retired after a grace.

One entry per ``### <slug>`` heading whose text is exactly one slug token, in a file
matching a ``fold_digests`` glob. ``name`` is the slug, ``file`` the digest, ``digest`` its
stem and ``line`` the heading's 1-based line. A slug equal to a live memory's stem is not
indexed (the file wins); a slug seen twice keeps its first occurrence in sorted (file,
position) order. Both are counted (``skipped``). Lane: always ``journal``.

A corpus with no declared digest, or none with a slug heading, gets no files here and a
main manifest byte-identical to before. Otherwise the main manifest gains one key,
``sections`` (``{"slugs": {slug: digest file}}``, sorted), so the prompt hook can tell from
the manifest it already loaded whether a prompt names a section without opening this one.

Built by ``build_index``/``refresh_index`` in the same pass, after the main manifest is in
place, incrementally by content hash and inside what is left of the same offline budget:
the main index stays dense and usable while sections catch up, and an incomplete
sub-index is simply not fused yet. Never raises into a build.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple

SECTIONS_MANIFEST = "sections.json"
_DENSE_RE = re.compile(r"^sections\.[0-9a-f]{16}\.npy$")
_RETIRE_GRACE_SECS = 300  # the main matrix's grace (build_index_dense.RETIRE_GRACE_SECS)
SECTIONS_VERSION = 1  # the sub-manifest's own shape; schema_version follows the main index

_SLUG_HEADING_RE = re.compile(r"^###[ \t]+([\w.\-]+)[ \t]*$")
_END_RE = re.compile(r"^#{1,3}[ \t]")  # the next heading of level 3 or above ends a section
_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")

# Section BODY rows, chosen by measurement on the field corpus (see the commit body): among
# the configurations whose cold full build grew by no more than 25%, the best probe recall.
#   "none"  — the section's name words + first meaningful line only (one dense row);
#   "chunk" — plus one embedded body chunk of the section's first ``_BODY_CHARS`` characters;
#   "bm25"  — plus one body chunk of the first ``_BODY_BM25_CHARS`` characters with a
#             zero dense row: BM25 sees the body, the dense lane never scores it, and the
#             build embeds nothing extra.
# Measured: none and bm25 left most probes outside the top 10 (a single dense row cannot
# compete with memories that carry three body rows); two embedded rows can, and a 400-char
# first line plus a 400-char opening chunk was the best recall under the build-time bound.
BODY_MODE = "chunk"
# The first meaningful line's bound inside the embedded ``doc_text`` (None: the injection
# cap, ``trust.inject_description``). The displayed description always takes that cap.
DOC_CHARS = 400
_BODY_CHARS = 400
_BODY_BM25_CHARS = 1500
_BODY_MIN_CHARS = 80  # build_index._BODY_CHUNK_MIN_CHARS: shorter is trivia


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def digest_stems(memory_dir: str, stems) -> List[str]:
    """The stems in ``stems`` whose file matches a declared ``fold_digests`` glob, sorted by
    filename. ``[]`` when nothing is declared."""
    from .provenance_format import read_fold_digests

    globs = read_fold_digests(memory_dir)
    if not globs:
        return []
    return sorted(
        (s for s in stems if any(fnmatch.fnmatchcase(s + ".md", g) for g in globs)),
        key=lambda s: s + ".md",
    )


def _body_start(lines: List[str]) -> int:
    """Index of the first line after a leading frontmatter block (0 when there is none)."""
    if not lines or lines[0].rstrip("\r\n") != "---":
        return 0
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            return i + 1
    return 0


def parse_sections(text: str) -> Tuple[List[Tuple[str, int, str]], str]:
    """``([(slug, 1-based heading line, section text)], text outside every section)``.

    A section runs from its ``### <slug>`` heading to the next heading of level 3 or above;
    headings inside fenced code are not headings. Pure; never raises."""
    try:
        lines = (text or "").splitlines(keepends=True)
        start = _body_start(lines)
        out: List[Tuple[str, int, List[str]]] = []
        outside: List[str] = list(lines[:start])
        cur: Optional[Tuple[str, int, List[str]]] = None
        fence = False
        for i in range(start, len(lines)):
            raw = lines[i]
            ln = raw.rstrip("\r\n")
            if _FENCE_RE.match(ln):
                fence = not fence
            elif not fence and _END_RE.match(ln):
                m = _SLUG_HEADING_RE.match(ln)
                cur = (m.group(1), i + 1, []) if m else None
                if cur is not None:
                    out.append(cur)
                    continue
            if cur is not None:
                cur[2].append(raw)
            else:
                outside.append(raw)
        return [(s, n, "".join(b)) for s, n, b in out], "".join(outside)
    except Exception:
        return [], text or ""


# --------------------------------------------------------------------------- #
# The plan: what the sub-index should hold, from texts the build already read
# --------------------------------------------------------------------------- #
class Plan:
    """The sections of one corpus snapshot: entries, body chunks, link data, counts."""

    def __init__(self) -> None:
        self.entries: List[dict] = []
        self.chunks: List[dict] = []  # {entry, hash, tokens, text, embed}
        self.digests: List[str] = []
        self.skipped = {"live_memory": 0, "duplicate": 0}
        self.texts: Dict[str, str] = {}  # stem -> section text, for links
        self.outside: Dict[str, str] = {}  # digest stem -> its text outside sections

    @property
    def record(self) -> dict:
        """The main manifest's ``sections`` key: every slug and the digest it lives in."""
        return {"slugs": {e["name"]: e["file"] for e in sorted(self.entries, key=lambda e: e["name"])}}


def plan_sections(memory_dir: str, texts: Dict[str, str]) -> Optional[Plan]:
    """The plan for ``texts`` (``{stem: full text}``, what ``compute_corpus`` read), or None
    when the corpus declares no digest or no digest holds a slug section. Never raises."""
    try:
        from .build_index import _first_meaningful_body_line, _hash, _name_words, bm25_terms, tokenize
        from .lanes import JOURNAL
        from .staleness import read_source_commit_time
        from .trust import inject_description

        stems = set(texts)
        digests = digest_stems(memory_dir, stems)
        if not digests:
            return None
        plan = Plan()
        seen: set = set()
        for stem in digests:
            sections, outside = parse_sections(texts.get(stem) or "")
            if not sections:
                continue
            plan.digests.append(stem)
            plan.outside[stem] = outside
            sct = read_source_commit_time(texts.get(stem) or "")
            for slug, line, body in sections:
                if slug in stems:
                    plan.skipped["live_memory"] += 1
                    continue
                if slug in seen:
                    plan.skipped["duplicate"] += 1
                    continue
                seen.add(slug)
                first = _first_meaningful_body_line(body)
                desc = inject_description(first)
                lead = desc if DOC_CHARS is None else " ".join(first.split())[:DOC_CHARS]
                doc_text = f"{_name_words(slug)}. {lead}".strip()
                idx = len(plan.entries)
                plan.entries.append({
                    "name": slug,
                    "file": stem + ".md",
                    "digest": stem,
                    "line": line,
                    "doc_text": doc_text,
                    "description": desc,
                    "hash": _hash(doc_text),
                    "tokens": bm25_terms(tokenize(doc_text)),
                    "invalid_after": None,
                    "source_commit_time": sct,
                    "steer": None,
                    "confidence": None,
                    "lane": JOURNAL,
                })
                plan.texts[slug] = body
                chunk = _body_chunk(body, first)
                if chunk:
                    plan.chunks.append({
                        "entry": idx,
                        "hash": _hash(("bm25:" if BODY_MODE == "bm25" else "") + chunk),
                        "tokens": bm25_terms(tokenize(chunk)),
                        "text": chunk,
                        "embed": BODY_MODE == "chunk",
                    })
        return plan if plan.entries else None
    except Exception:
        return None


def _body_chunk(body: str, first: str) -> str:
    """The one body row's text for ``BODY_MODE`` ("" for none): the section's opening text,
    first line included (the description row holds only its capped head), bounded."""
    if BODY_MODE not in ("chunk", "bm25"):
        return ""
    cap = _BODY_CHARS if BODY_MODE == "chunk" else _BODY_BM25_CHARS
    rest = " ".join(body.split())[:cap].strip()
    return rest if len(rest) >= _BODY_MIN_CHARS and rest != " ".join(first.split()) else ""


def _links(plan: Plan, index_dir: str) -> Dict[str, object]:
    """Section link data off the link cache this build just wrote: each section's resolved
    out-links (memory stems, or other sections by slug), each section's in-links (memories
    whose ``[[slug]]`` resolves to no file), and each digest's own out-links (links written
    OUTSIDE its sections). A link inside a section belongs to the section, not the digest."""
    from .links_cache import _load_links_payload
    from .links_graph import normalize_slug, parse_wikilinks

    payload = _load_links_payload(index_dir) or {}
    alias = payload.get("alias_to_file") or {}
    ambiguous = payload.get("ambiguous") or {}
    by_norm = {normalize_slug(e["name"]): e["name"] for e in plan.entries}

    def resolve(target: str) -> Optional[str]:
        n = normalize_slug(target)
        if n in ambiguous:
            return None
        return alias.get(n) or by_norm.get(n)

    out: Dict[str, List[str]] = {}
    inbound: Dict[str, set] = {e["name"]: set() for e in plan.entries}
    for e in plan.entries:
        name = e["name"]
        targets = {resolve(t) for t in parse_wikilinks(plan.texts.get(name) or "")}
        out[name] = sorted(t for t in targets if t and t != name)
    digests = set(plan.digests)
    for stem, raws in (payload.get("unresolved") or {}).items():
        if stem in digests:
            continue  # a digest's own misses are its sections' links, counted above
        for t in raws or ():
            slug = by_norm.get(normalize_slug(t))
            if slug:
                inbound[slug].add(stem)
    files = set(alias.values())
    own = {}
    for stem in plan.digests:
        ts = {resolve(t) for t in parse_wikilinks(plan.outside.get(stem) or "")}
        own[stem] = sorted(t for t in ts if t and t != stem and t in files)
    return {"out": out, "in": {k: sorted(v) for k, v in inbound.items()}, "digest_out": own}


# --------------------------------------------------------------------------- #
# Files
# --------------------------------------------------------------------------- #
def _manifest_path(index_dir: str) -> str:
    return os.path.join(index_dir, SECTIONS_MANIFEST)


def read_manifest(index_dir: str) -> Optional[dict]:
    """The sub-manifest, or None (absent, unparseable, or another schema). Never raises."""
    try:
        from .build_index import SCHEMA_VERSION

        with open(_manifest_path(index_dir), "r", encoding="utf-8") as fh:
            m = json.load(fh)
        if not isinstance(m, dict) or m.get("schema_version") != SCHEMA_VERSION:
            return None
        return m
    except Exception:
        return None


def _dense_file(index_dir: str, manifest: Optional[dict]) -> Optional[str]:
    name = (manifest or {}).get("dense_file")
    if not isinstance(name, str) or not _DENSE_RE.match(name):
        return None
    return os.path.join(index_dir, name)


def _load_matrix(index_dir: str, manifest: Optional[dict]):
    p = _dense_file(index_dir, manifest)
    if p is None or not os.path.exists(p):
        return None
    try:
        import numpy as np

        return np.load(p)
    except Exception:
        return None


def load_sections(index_dir: Optional[str]):
    """The sub-index as a ``LoadedIndex``, or None. Dense only when the manifest says it is
    complete and dense is not disabled; ``LoadedIndex`` re-checks the matrix shape."""
    if not index_dir:
        return None
    m = read_manifest(index_dir)
    if not m:
        return None
    from .build_index import LoadedIndex, dense_disabled

    dense = _load_matrix(index_dir, m) if (m.get("dense_ready") and not dense_disabled()) else None
    return LoadedIndex(m, dense)


def _write_matrix(index_dir: str, rows) -> str:
    """Persist ``rows`` under a fresh ``sections.<id>.npy``; returns the name."""
    import io
    import uuid

    import numpy as np

    from .atomic import write_bytes_atomic

    buf = io.BytesIO()
    np.save(buf, rows)
    name = f"sections.{uuid.uuid4().hex[:16]}.npy"
    write_bytes_atomic(os.path.join(index_dir, name), buf.getvalue())
    return name


def _write_manifest(index_dir: str, manifest: dict) -> None:
    from .atomic import write_json_atomic

    write_json_atomic(_manifest_path(index_dir), manifest, indent=None)


def _retire(index_dir: str, keep: Optional[str], now: Optional[float] = None) -> None:
    """Drop every section matrix but ``keep`` older than the grace. Never raises."""
    now = time.time() if now is None else now
    try:
        names = os.listdir(index_dir)
    except Exception:
        return
    for name in names:
        if name == keep or not _DENSE_RE.match(name):
            continue
        try:
            path = os.path.join(index_dir, name)
            if now - os.stat(path).st_mtime > _RETIRE_GRACE_SECS:
                os.remove(path)
        except Exception:
            pass


def remove_sections(index_dir: str) -> None:
    """A corpus that no longer has sections keeps no sub-index. Never raises."""
    try:
        if os.path.exists(_manifest_path(index_dir)):
            os.remove(_manifest_path(index_dir))
        _retire(index_dir, None, now=float("inf"))
    except Exception:
        pass


def _fingerprint(plan: Plan, links: dict) -> str:
    material = [
        [(e["name"], e["file"], e["line"], e["hash"], e["source_commit_time"]) for e in plan.entries],
        [(c["entry"], c["hash"]) for c in plan.chunks],
        plan.skipped,
        links,
    ]
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Build (incremental, bounded)
# --------------------------------------------------------------------------- #
def write_sections(
    index_dir: str,
    plan: Optional[Plan],
    *,
    allow_download: bool = True,
    deadline: Optional[float] = None,
    force: bool = False,
) -> Optional[dict]:
    """Bring the sub-index in line with ``plan``: remove it when there is none, no-op when
    it is current and complete, else reuse every unchanged row by hash and embed the rest
    (offline: in slices, stopping at ``deadline``). Returns the sub-manifest, or None.
    Never raises: a failed write leaves the prior sub-index as it was."""
    try:
        if plan is None:
            remove_sections(index_dir)
            return None
        from . import build_index as B

        links = _links(plan, index_dir)
        fp = _fingerprint(plan, links)
        old = None if force else read_manifest(index_dir)
        want_dense = not B.dense_disabled()
        current = old and old.get("fingerprint") == fp
        if current and (not want_dense or (old.get("dense_ready") and old.get("model") == B.DEFAULT_MODEL)):
            return old
        entries = [dict(e, links_out=links["out"][e["name"]], links_in=links["in"][e["name"]]) for e in plan.entries]
        chunks = [{k: c[k] for k in ("entry", "hash", "tokens", "text")} for c in plan.chunks]
        embed_chunk = [c["embed"] for c in plan.chunks]
        rows, dense_ready, dim = _rows(index_dir, old, entries, chunks, embed_chunk, allow_download, deadline, want_dense)
        manifest = {
            "schema_version": B.SCHEMA_VERSION,
            "sections_version": SECTIONS_VERSION,
            "model": B.DEFAULT_MODEL if rows is not None else None,
            "dense_ready": dense_ready,
            "dim": dim,
            "count": len(entries),
            "entries": entries,
            "body_chunks": chunks,
            "bm25": B.compute_bm25_stats([e["tokens"] for e in entries] + [c["tokens"] for c in chunks]),
            "digests": len(plan.digests),
            "skipped": dict(plan.skipped),
            "digest_out": links["digest_out"],
            "fingerprint": fp,
        }
        if rows is not None:
            manifest["dense_file"] = _write_matrix(index_dir, rows)
        _write_manifest(index_dir, manifest)
        _retire(index_dir, manifest.get("dense_file"))
        return manifest
    except Exception:
        return None


def _rows(index_dir, old, entries, chunks, embed_chunk, allow_download, deadline, want_dense):
    """``(matrix or None, dense_ready, dim)``; sets every entry's and chunk's ``row``. A
    BM25-only body chunk gets a zero row (never above the dense floor, never embedded)."""
    from . import build_index as B

    for d in entries + chunks:
        d["row"] = None
    if not want_dense:
        return None, False, None
    try:
        import numpy as np

        prior: Dict[str, int] = {}
        old_dense = _load_matrix(index_dir, old) if old else None
        if old and old_dense is not None and old.get("model") == B.DEFAULT_MODEL:
            for d in (old.get("entries") or []) + (old.get("body_chunks") or []):
                r = d.get("row")
                if isinstance(r, int) and 0 <= r < len(old_dense) and d.get("hash"):
                    prior.setdefault(d["hash"], r)
        docs = entries + chunks
        texts = [e["doc_text"] for e in entries] + [c["text"] for c in chunks]
        zero = [False] * len(entries) + [not x for x in embed_chunk]
        # Shortest first: a slice pads to its longest row, so length order embeds the same
        # rows for a fraction of the padding (each row's vector does not depend on its slice).
        todo = sorted((i for i, d in enumerate(docs) if not zero[i] and d["hash"] not in prior), key=lambda i: len(texts[i]))
        new: Dict[int, object] = {}
        if todo:
            if allow_download:
                vecs = B.embed_documents([texts[i] for i in todo], allow_download=True)
                new = {i: vecs[p] for p, i in enumerate(todo)}
            else:
                end = deadline if deadline is not None else time.monotonic() + B.DENSE_REFRESH_TIMEOUT_SECS
                for s in range(0, len(todo), B.DENSE_EMBED_CHUNK_SIZE):
                    left = end - time.monotonic()
                    if left <= 0:
                        break
                    part = todo[s : s + B.DENSE_EMBED_CHUNK_SIZE]
                    try:
                        vecs = B.run_bounded(
                            lambda t=[texts[i] for i in part]: B.embed_documents(t, allow_download=False), left
                        )
                    except B.DenseTimeout:
                        break
                    new.update({i: vecs[p] for p, i in enumerate(part)})
        if new:
            dim = int(next(iter(new.values())).shape[0])
        elif old_dense is not None and len(old_dense):
            dim = int(old_dense.shape[1])
        else:
            return None, False, None
        rows = np.zeros((len(docs), dim), dtype="float32")
        complete = True
        for i, d in enumerate(docs):
            if zero[i]:
                d["row"] = i
            elif d["hash"] in prior:
                rows[i] = old_dense[prior[d["hash"]]]
                d["row"] = i
            elif i in new:
                rows[i] = new[i]
                d["row"] = i
            else:
                complete = False
        return rows, complete, dim
    except Exception:
        return None, False, None
