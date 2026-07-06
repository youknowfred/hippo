"""Offline index builder for agent-memory recall (Tier 2 of the activation roadmap).

Builds a HYBRID retrieval index over the memory corpus:
  - DENSE: ``bge-small-en-v1.5`` embeddings via ``fastembed`` (ONNX, no PyTorch). The
    ~130 MB model cache is warmed HERE, offline — NEVER from a hook.
  - SPARSE: a ``rank-bm25`` index over the same tokenized text (already a repo dep).

What gets indexed per memory = its ``name`` + ``description:`` (the recall hook the files
already carry). Body-summary embedding is deferred (see the roadmap) until the
description-only index is measured.

Persistence: a gitignored, rebuildable cache at ``.claude/.memory-index/``
  - ``manifest.json`` — schema version, model, per-entry {name, file, hash, tokens, doc_text}
  - ``dense.npy``    — float32 [N, dim] L2-normalized embeddings (row i ↔ entries[i])

Markdown-in-git stays the single source of authority; this cache is derived and
deleting it loses nothing (``build_index`` regenerates it). The build is INCREMENTAL:
unchanged memories (same content hash) reuse their cached embedding row, so only
new/edited files are re-embedded.

Degrades cleanly: with ``fastembed`` absent (or ``MEMOBOT_DISABLE_DENSE=1``) it builds a
BM25-only index without error (``dense_ready=false``); recall still works on BM25 alone.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import sys
import threading
import time
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from .provenance import (
    _iter_memory_files,
    ensure_self_ignoring_dir,
    parse_frontmatter,
    resolve_dirs,
    split_frontmatter,
)

# --------------------------------------------------------------------------- #
# Config (all overridable via env so the hook/tests never hard-depend on one model)
# --------------------------------------------------------------------------- #
_INDEX_DIRNAME = ".memory-index"
# v2 (Tier 3, memory-organism-instrument-immunize): entries gained "invalid_after". Nothing
# currently reads/gates on this field (no version-mismatch check exists anywhere), so the
# bump is a marker only — an older v1 index loads fine; entry.get("invalid_after") is None
# for every pre-existing entry until the next rebuild repopulates it.
SCHEMA_VERSION = 2
DEFAULT_MODEL = os.environ.get("MEMOBOT_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
_MANIFEST_NAME = "manifest.json"
_DENSE_NAME = "dense.npy"


def default_index_dir(memory_dir: str) -> str:
    """``.claude/.memory-index`` — a sibling of ``.claude/memory`` (the gitignored cache)."""
    override = os.environ.get("MEMOBOT_INDEX_DIR")
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(memory_dir)), _INDEX_DIRNAME)


def dense_disabled() -> bool:
    """True when the dense path is explicitly suppressed (tests / forced BM25-only)."""
    return os.environ.get("MEMOBOT_DISABLE_DENSE", "").strip() not in ("", "0", "false", "False")


# --------------------------------------------------------------------------- #
# Wall-clock bound for the dense model (shared by recall's query path + the offline
# SessionStart refresh). A WARM model load from the cache is ~1-2s; a COLD/wiped cache
# makes fastembed attempt a fetch and — even with HF forced offline — sleep ~27s on retry
# before failing. That would blow a hook's timeout, so the dense attempt is bounded and
# aborts to BM25 instead of blocking. recall.py imports these.
# --------------------------------------------------------------------------- #
class DenseTimeout(Exception):
    pass


def _parse_timeout_env(name: str, default: float) -> float:
    """Parse a float timeout env var; a malformed value must NEVER crash module import."""
    try:
        return float(os.environ.get(name) or str(default))
    except (TypeError, ValueError):
        return default


# query path (per-prompt recall) — short; refresh path (SessionStart embed batch) — longer.
DENSE_QUERY_TIMEOUT_SECS = _parse_timeout_env("MEMOBOT_DENSE_TIMEOUT", 5.0)
DENSE_REFRESH_TIMEOUT_SECS = _parse_timeout_env("MEMOBOT_REFRESH_TIMEOUT", 15.0)


def _parse_int_env(name: str, default: int) -> int:
    """Parse an int env var; a malformed value must NEVER crash module import."""
    try:
        return int(os.environ.get(name) or str(default))
    except (TypeError, ValueError):
        return default


# COR-3: the offline batch embed is sliced into bounded-size chunks so a large corpus
# persists PARTIAL progress within one DENSE_REFRESH_TIMEOUT_SECS budget instead of an
# all-or-nothing attempt that discards everything on a single slow batch. Each slice's
# already-embedded hashes are cache-reused on the next call (see build_index's
# old_row_by_hash), so a 500-doc corpus converges to dense over N sessions.
DENSE_EMBED_CHUNK_SIZE = _parse_int_env("MEMOBOT_EMBED_CHUNK_SIZE", 64)


def run_bounded(fn, seconds: float):
    """Run ``fn`` with a SIGALRM wall-clock bound (main-thread/Unix only).

    Off the main thread or where SIGALRM is unavailable (Windows), runs ``fn`` directly —
    hooks always run on the main thread of a fresh Unix process, where the bound holds.
    The handler is installed BEFORE the timer is armed (so a firing alarm never hits the
    default SIGALRM action, which terminates the process).
    """
    if (
        seconds <= 0
        or threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "SIGALRM")
    ):
        return fn()

    def _handler(signum, frame):
        raise DenseTimeout()

    old = signal.signal(signal.SIGALRM, _handler)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


# --------------------------------------------------------------------------- #
# Tokenization (shared by BM25 build, recall, and self-recall query derivation)
# --------------------------------------------------------------------------- #
# Compact English stopword set. Domain terms (pdf, irr, dscr, llm, ...) are deliberately
# NOT stopped — they are the most discriminating tokens in this corpus.
_STOPWORDS = frozenset(
    """
    a an the of to in on for and or but is are was were be been being it its this that these those
    with without within into onto from by as at via per vs not no nor so than then thus too very
    can could should would may might must will shall do does did done has have had having
    if else when while where which who whom whose what why how all any each few more most other some
    such only own same about above below over under again further once here there both
    we you they i he she them our your their he's also new now use used using up out off down
    """.split()
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_]*")


def tokenize(text: str) -> List[str]:
    """Lowercase word tokens (alnum + underscore), stopwords + 1-char tokens dropped."""
    if not text:
        return []
    out: List[str] = []
    for m in _TOKEN_RE.finditer(text.lower()):
        tok = m.group(0)
        if len(tok) < 2 or tok in _STOPWORDS:
            continue
        out.append(tok)
    return out


# --------------------------------------------------------------------------- #
# Per-memory document text
# --------------------------------------------------------------------------- #
def _first_meaningful_body_line(body: str) -> str:
    for raw in (body or "").split("\n"):
        ln = raw.strip().lstrip("#").strip()
        if len(ln) >= 8 and not ln.startswith(("---", "```", "**Why", "**How")):
            return ln
    return ""


def extract_description(text: str) -> str:
    """The memory's ``description:`` (top-level or under ``metadata:``), or a body fallback.

    3 of the corpus carry no description; for them the first meaningful body line is used.
    """
    fm = parse_frontmatter(text)
    desc = ""
    if isinstance(fm, dict):
        d = fm.get("description")
        if not d:
            meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
            d = meta.get("description")
        if isinstance(d, str):
            desc = d.strip()
    if not desc:
        _, body = split_frontmatter(text)
        desc = _first_meaningful_body_line(body)
    return desc


def _name_words(name: str) -> str:
    return name.replace("_", " ").replace("-", " ")


def memory_doc_text(name: str, text: str) -> str:
    """The text indexed for one memory: ``name`` (slug words) + its ``description``.

    The name is included because the kebab/snake slug is itself a dense recall signal
    (e.g. ``density-adaptive-floor``).
    """
    return f"{_name_words(name)}. {extract_description(text)}".strip()


def entry_description(entry: dict) -> str:
    """The raw description for display. Prefers the stored field; falls back to splitting
    ``doc_text`` on the first ``. `` (the name/description boundary) for legacy indexes."""
    d = entry.get("description")
    if isinstance(d, str):
        return d
    doc = entry.get("doc_text", "")
    return doc.split(". ", 1)[1] if ". " in doc else doc


def _hash(doc_text: str) -> str:
    return hashlib.sha1(doc_text.encode("utf-8")).hexdigest()


def _extract_invalid_after(fm: dict) -> Optional[str]:
    """The memory's ``invalid_after`` (top-level or under ``metadata:``), or ``None``.

    Mirrors ``extract_description``'s exact top-level-then-``metadata:`` fallback. This is
    load-bearing: every OTHER provenance-style key in this corpus (``cited_paths``,
    ``source_commit``) nests under ``metadata:`` when present, and
    ``staleness.set_invalid_after`` follows that same convention — a top-level-only read
    here would make Tier 3's soft-invalidation PERMANENTLY inert the moment a memory's
    frontmatter uses the nested schema, not just a no-op on first ship.

    Also coerces a YAML-auto-typed ``date``/``datetime`` value (``yaml.safe_load`` parses an
    UNQUOTED ``invalid_after: 2026-06-01`` — the most natural hand-authored form — into a
    native ``datetime.date``, not a ``str``) to its ISO string, rather than silently
    discarding it. Without this, the value would also reach ``json.dump`` un-serializable
    and crash ``build_index()`` the first time anyone writes the field the natural way.
    """
    if not isinstance(fm, dict):
        return None
    ia = fm.get("invalid_after")
    if not ia:
        meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
        ia = meta.get("invalid_after")
    if isinstance(ia, str):
        return ia
    if isinstance(ia, (date, datetime)):
        return ia.isoformat()
    return None


def compute_corpus(memory_dir: str) -> List[dict]:
    """Scan the corpus -> ordered entries ``{name, file, doc_text, hash, tokens, invalid_after}``.

    Order is deterministic (sorted filenames, from ``_iter_memory_files``). Re-scanned FRESH
    on every call (every file re-read from disk) — only the dense embedding ROW is
    cache-reused, keyed by ``hash`` (= sha1 of ``doc_text``, which is name + description
    ONLY). Adding ``invalid_after`` therefore can never disturb embedding-cache reuse, and a
    metadata-only change (e.g. a fresh ``invalid_after``) is reflected on every rebuild —
    including a rebuild whose embedding rows are entirely cache-hit.
    """
    entries: List[dict] = []
    for path in _iter_memory_files(memory_dir):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except Exception:
            continue
        name = os.path.splitext(os.path.basename(path))[0]
        desc = extract_description(text)
        doc_text = f"{_name_words(name)}. {desc}".strip()
        fm = parse_frontmatter(text)
        entries.append(
            {
                "name": name,
                "file": os.path.basename(path),
                "doc_text": doc_text,
                "description": desc,  # stored separately so display never re-parses doc_text
                "hash": _hash(doc_text),
                "tokens": tokenize(doc_text),
                "invalid_after": _extract_invalid_after(fm),
            }
        )
    return entries


# --------------------------------------------------------------------------- #
# Durable fastembed model cache (closes a live silent-degradation bug)
# --------------------------------------------------------------------------- #
# fastembed resolves its ONNX model cache from FASTEMBED_CACHE_PATH, DEFAULTING to the
# EPHEMERAL ``$TMPDIR/fastembed_cache`` (fastembed/common/utils.py::define_cache_dir). On
# macOS that lives under ``/var/folders`` which the OS PURGES on a schedule — silently
# wiping the ~130 MB ``bge-small-en-v1.5`` model. Once wiped, the OFFLINE recall + SessionStart
# refresh paths (allow_download=False) cannot re-fetch it, so hybrid recall degrades to
# BM25-only with NO error. Pin the cache to a durable, machine-shared dir so the model warms
# ONCE and survives reboots / temp purges. Exporting the env var is sufficient — fastembed
# honors it with no code change on its side. The two memory hooks export the same default;
# this Python-side setdefault additionally covers a manual ``python -m memory.build_index``
# run that never passed through a hook (the warm path), so the manual re-warm and the hook
# read paths share ONE cache dir.
#
# Default precedence (below an explicit FASTEMBED_CACHE_PATH, which ``ensure_fastembed_cache_path``
# honors): ``$CLAUDE_PLUGIN_DATA/fastembed`` when CLAUDE_PLUGIN_DATA is set — the packaged
# plugin's UPDATE-surviving data dir — else a platform-conventional home cache (OSP-2). The hooks
# run BEFORE this resolver and their export WINS via setdefault, so they implement the SAME order
# (see the cross-language guard in tests/test_fastembed_cache_path.py).
def platform_cache_dir(*, subpath: str = "hippo-memory") -> str:
    """The OS-conventional user cache dir, joined with ``subpath`` — macOS vs Linux/XDG (OSP-2).

    darwin -> ``~/Library/Caches/<subpath>``; anything else (Linux, and any unrecognized
    ``sys.platform`` — Windows is out of scope per OQ-2, so no hard-fail on an odd platform
    string) -> ``$XDG_CACHE_HOME/<subpath>`` or ``~/.cache/<subpath>`` when XDG_CACHE_HOME is
    unset/empty. Absolute and ``~``-expanded. Must stay equivalent to the bash branch the memory
    hooks mirror (same ``uname``-based split; see tests/test_fastembed_cache_path.py).
    """
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Caches", subpath)
    xdg_cache_home = os.environ.get("XDG_CACHE_HOME", "")
    if xdg_cache_home:
        return os.path.join(xdg_cache_home, subpath)
    return os.path.join(os.path.expanduser("~"), ".cache", subpath)


def durable_fastembed_cache_dir() -> str:
    """A durable, machine-shared cache dir for the fastembed model — NEVER under ``$TMPDIR``.

    Prefers ``$CLAUDE_PLUGIN_DATA/fastembed`` when CLAUDE_PLUGIN_DATA is set+non-empty (the
    packaged plugin's update-surviving data dir), else ``platform_cache_dir()/fastembed`` (macOS:
    ``~/Library/Caches/hippo-memory/fastembed``; Linux: XDG-or-``~/.cache/hippo-memory/fastembed``).
    Absolute and ``~``-expanded; stable across reboots and macOS temp purges. Must stay equivalent
    to the ``FASTEMBED_CACHE_PATH`` default the memory hooks export (same precedence in bash;
    ``set -u``-safe ``:+`` / ``:-`` expansions).
    """
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    if plugin_data:  # non-empty (matches bash ${CLAUDE_PLUGIN_DATA:+...}); harness sets a clean abs path
        return os.path.join(plugin_data, "fastembed")
    return os.path.join(platform_cache_dir(), "fastembed")


def ensure_fastembed_cache_path() -> str:
    """Pin ``FASTEMBED_CACHE_PATH`` to the durable dir unless the caller already set it.

    Idempotent ``setdefault`` — it RESPECTS an explicit override (e.g. the hooks' export, or a
    future packaged plugin pointing it at its own data dir). Call this BEFORE importing /
    instantiating ``fastembed.TextEmbedding`` so every load — build, offline recall, SessionStart
    refresh — warms/reads the SAME durable cache. Returns the effective cache path.
    """
    os.environ.setdefault("FASTEMBED_CACHE_PATH", durable_fastembed_cache_dir())
    return os.environ["FASTEMBED_CACHE_PATH"]


# --------------------------------------------------------------------------- #
# Dense embedding (lazy fastembed; warms the model cache at BUILD time only)
# --------------------------------------------------------------------------- #
_MODEL_CACHE: dict = {}


def _normalize_rows(mat):
    import numpy as np

    arr = np.asarray(mat, dtype="float32")
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


def _get_model(allow_download: bool):
    """Return a cached ``fastembed.TextEmbedding`` or raise.

    With ``allow_download=False`` (the recall/hook path) HF Hub is forced OFFLINE so a
    cache miss raises immediately instead of triggering a synchronous ~130 MB download.
    The build path (``allow_download=True``) is the ONLY place a download may happen.
    """
    if dense_disabled():
        raise RuntimeError("dense disabled via MEMOBOT_DISABLE_DENSE")
    key = (DEFAULT_MODEL, bool(allow_download))
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    if not allow_download:
        # Belt: any cache miss now errors fast (no network) -> caller falls back to BM25.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    # Pin the model cache to a durable dir BEFORE the fastembed import so the model warms /
    # loads from a path that survives macOS temp purges (closes the silent BM25-degradation
    # bug). Unconditional: the BUILD path (allow_download=True) is where the model is WARMED,
    # so it must warm into the durable dir too — not just the offline read paths.
    ensure_fastembed_cache_path()
    from fastembed import TextEmbedding  # lazy: never imported at module load

    model = TextEmbedding(model_name=DEFAULT_MODEL)
    _MODEL_CACHE[key] = model
    return model


def embed_documents(texts: List[str], allow_download: bool = True):
    """L2-normalized passage embeddings as a float32 matrix [len(texts), dim]."""
    model = _get_model(allow_download=allow_download)
    vecs = list(model.embed(texts))
    return _normalize_rows(vecs)


def embed_query(text: str, allow_download: bool = False):
    """L2-normalized query embedding (1-D). Uses the model's asymmetric ``query_embed``."""
    model = _get_model(allow_download=allow_download)
    embedder = getattr(model, "query_embed", None) or model.embed
    vec = list(embedder([text]))[0]
    return _normalize_rows(vec)[0]


# --------------------------------------------------------------------------- #
# Manifest / dense matrix IO
# --------------------------------------------------------------------------- #
def _load_manifest(index_dir: str) -> Optional[dict]:
    p = os.path.join(index_dir, _MANIFEST_NAME)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _load_dense(index_dir: str):
    p = os.path.join(index_dir, _DENSE_NAME)
    if not os.path.exists(p):
        return None
    try:
        import numpy as np

        return np.load(p)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Build (incremental)
# --------------------------------------------------------------------------- #
def build_index(
    memory_dir: Optional[str] = None,
    index_dir: Optional[str] = None,
    *,
    force: bool = False,
    allow_download: bool = True,
    preserve_on_dense_fail: bool = False,
) -> dict:
    """(Re)build the hybrid index. Returns the manifest dict. Never raises on dense failure.

    Incremental: an entry whose content ``hash`` matches the prior manifest reuses its
    cached embedding row; only new/changed memories are embedded. ``force=True`` re-embeds
    everything. With fastembed unavailable/disabled, builds BM25-only (``dense_ready``
    False) — the BM25 part is always rebuilt (cheap), so the index is never stale.

    ``allow_download=False`` (the offline SessionStart refresh) forbids a model download and
    bounds the embed so a cold cache can't hang. The batch is embedded in
    ``DENSE_EMBED_CHUNK_SIZE``-sized slices against the SAME overall
    ``DENSE_REFRESH_TIMEOUT_SECS`` wall-clock budget: once a slice completes, no NEW slice is
    started if the budget is already exhausted, but everything embedded so far is still
    persisted (row=None for anything left un-embedded). This lets a large corpus converge to
    dense across sessions (each pass reuses the previous pass's rows via ``old_row_by_hash``)
    instead of an all-or-nothing 15s attempt that discards partial progress.

    ``dense_ready`` is True only when EVERY entry ends up with a row — a partially-embedded
    corpus stays ``dense_ready=False`` (BM25-only) rather than exposing a half-filled dense
    matrix to recall, which cosine-scores placeholder rows as if they were real embeddings.
    The rows themselves (and the manifest's per-entry ``row``) are still saved so the next
    build's ``old_row_by_hash`` skips re-embedding them — "never worse than BM25" is protected
    for CURRENT recall while embedding progress is not thrown away.

    ``preserve_on_dense_fail=True`` means: if the existing index was dense and this build
    could NOT produce (fully) dense (offline embed failed or ran out of budget), leave the
    existing index untouched rather than DOWNGRADE it to BM25-only — "never worse".
    """
    if memory_dir is None:
        memory_dir, _ = resolve_dirs()
    if index_dir is None:
        index_dir = default_index_dir(memory_dir)
    ensure_self_ignoring_dir(index_dir)  # derived dir: mkdir + self-ignoring .gitignore (SEC-3)

    entries = compute_corpus(memory_dir)

    old_manifest = None if force else _load_manifest(index_dir)
    old_dense = None if force else _load_dense(index_dir)

    want_dense = not dense_disabled()
    dense_rows = None
    dense_ready = False
    if want_dense:
        try:
            import numpy as np

            # NOTE: gated on the model matching + a dense matrix being present — NOT on
            # old_manifest["dense_ready"] — so a PARTIALLY-embedded manifest (COR-3 chunked
            # embed, dense_ready=False but some entries do have a row) still contributes its
            # already-embedded rows here, letting a large corpus converge across sessions
            # instead of re-embedding from scratch every time the prior attempt fell short.
            old_row_by_hash: Dict[str, int] = {}
            if old_manifest and old_dense is not None and old_manifest.get("model") == DEFAULT_MODEL:
                for e in old_manifest.get("entries", []):
                    if "row" in e and e["row"] is not None and 0 <= e["row"] < len(old_dense):
                        old_row_by_hash[e["hash"]] = e["row"]

            to_embed_idx = [i for i, e in enumerate(entries) if e["hash"] not in old_row_by_hash]
            new_vecs_by_idx: Dict[int, "np.ndarray"] = {}
            if to_embed_idx:
                if allow_download:
                    # Online build (bootstrap/manual): one unbounded batch, as before.
                    texts = [entries[i]["doc_text"] for i in to_embed_idx]
                    vecs = embed_documents(texts, allow_download=True)
                    for pos, i in enumerate(to_embed_idx):
                        new_vecs_by_idx[i] = vecs[pos]
                else:
                    # Offline: slice into bounded chunks against ONE overall wall-clock budget
                    # so a large corpus persists whatever it manages instead of all-or-nothing.
                    deadline = time.monotonic() + DENSE_REFRESH_TIMEOUT_SECS
                    for start in range(0, len(to_embed_idx), DENSE_EMBED_CHUNK_SIZE):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break  # budget exhausted -> stop starting new slices
                        chunk_idx = to_embed_idx[start : start + DENSE_EMBED_CHUNK_SIZE]
                        texts = [entries[i]["doc_text"] for i in chunk_idx]
                        try:
                            chunk_vecs = run_bounded(
                                lambda t=texts: embed_documents(t, allow_download=False),
                                remaining,
                            )
                        except DenseTimeout:
                            break  # this slice didn't finish -> keep what's already persisted
                        for pos, i in enumerate(chunk_idx):
                            new_vecs_by_idx[i] = chunk_vecs[pos]

            dim = None
            if new_vecs_by_idx:
                dim = next(iter(new_vecs_by_idx.values())).shape[0]
            elif old_dense is not None and len(old_dense):
                dim = old_dense.shape[1]
            if dim is None:
                raise RuntimeError("could not determine embedding dim")

            rows = np.zeros((len(entries), dim), dtype="float32")
            all_embedded = True
            for i, e in enumerate(entries):
                if e["hash"] in old_row_by_hash:
                    rows[i] = old_dense[old_row_by_hash[e["hash"]]]
                    e["row"] = i
                elif i in new_vecs_by_idx:
                    rows[i] = new_vecs_by_idx[i]
                    e["row"] = i
                else:
                    e["row"] = None
                    all_embedded = False
            dense_rows = rows
            dense_ready = all_embedded
        except Exception:
            # Any dense failure (no fastembed, no cached model, offline miss, timeout) -> BM25.
            dense_rows = None
            dense_ready = False

    # Never-worse guard: don't overwrite a complete dense index with a BM25-only one just
    # because an OFFLINE embed couldn't run. Leave the last good index in place.
    if (
        preserve_on_dense_fail
        and not dense_ready
        and old_manifest is not None
        and old_manifest.get("dense_ready")
    ):
        return old_manifest

    if dense_rows is None:
        # Total dense failure (no partial progress to keep) -> every entry is row=None.
        for e in entries:
            e["row"] = None

    manifest = {
        "schema_version": SCHEMA_VERSION,
        # A partially-embedded (dense_ready=False) manifest still names the model so the
        # next build's cache-reuse check (old_manifest.get("model") == DEFAULT_MODEL) fires.
        "model": DEFAULT_MODEL if dense_rows is not None else None,
        "dense_ready": dense_ready,
        "dim": int(dense_rows.shape[1]) if dense_rows is not None else None,
        "count": len(entries),
        "entries": entries,
    }

    # COR-12: dense.npy (or its removal) is durably in place BEFORE the manifest that
    # references it is made visible — a recall racing this rebuild must never observe a
    # manifest ahead of its data (e.g. dense_ready=true with a stale/missing dense.npy).
    dense_path = os.path.join(index_dir, _DENSE_NAME)
    if dense_rows is not None:
        # Persisted even when dense_ready=False (partial progress) so the next build's
        # old_row_by_hash can resume from here instead of re-embedding from scratch.
        import numpy as np

        tmp_dense_path = dense_path + ".tmp.npy"
        try:
            np.save(tmp_dense_path, dense_rows)
            os.replace(tmp_dense_path, dense_path)
        finally:
            if os.path.exists(tmp_dense_path):
                try:
                    os.remove(tmp_dense_path)
                except Exception:
                    pass
    elif os.path.exists(dense_path):
        # Stale dense file from a prior dense build — remove so recall doesn't misread it.
        try:
            os.remove(dense_path)
        except Exception:
            pass

    manifest_path = os.path.join(index_dir, _MANIFEST_NAME)
    tmp_manifest_path = manifest_path + ".tmp"
    try:
        with open(tmp_manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
        os.replace(tmp_manifest_path, manifest_path)
    finally:
        if os.path.exists(tmp_manifest_path):
            try:
                os.remove(tmp_manifest_path)
            except Exception:
                pass
    return manifest


def refresh_index(memory_dir: Optional[str] = None, index_dir: Optional[str] = None) -> Optional[dict]:
    """Incrementally bring the index up to date with the corpus — OFFLINE, never-raises.

    For the SessionStart hook: so a memory written during one session is indexed (and thus
    recallable) by the next. Fast no-op when nothing changed AND the index isn't degraded (a
    hash check, NO model load); otherwise an offline, bounded, never-downgrade incremental
    build. Returns the manifest (or the unchanged one), or None on any failure.

    COR-3: the short-circuit must NOT fire on an unchanged-but-degraded (``dense_ready``
    False) index — that would serve BM25-only recall forever, healed only by a corpus write
    perturbing a hash. Falling through to ``build_index`` retries the offline dense embed
    (bounded, chunked, never-downgrade), so a warm model cache upgrades the index to dense on
    this SessionStart instead of waiting on the next write.
    """
    try:
        if memory_dir is None:
            memory_dir, _ = resolve_dirs()
        if index_dir is None:
            index_dir = default_index_dir(memory_dir)
        entries_now = compute_corpus(memory_dir)
        old = _load_manifest(index_dir)
        if old is not None:
            old_hashes = [e.get("hash") for e in old.get("entries", [])]
            corpus_unchanged = old_hashes == [e["hash"] for e in entries_now]
            if corpus_unchanged and (old.get("dense_ready") or dense_disabled()):
                return old  # corpus unchanged + already as good as it can be -> no-op
        return build_index(
            memory_dir, index_dir, allow_download=False, preserve_on_dense_fail=True
        )
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Load (for recall / eval)
# --------------------------------------------------------------------------- #
class LoadedIndex:
    """In-memory view of the persisted index. ``dense`` is None for a BM25-only index.

    QUA-4: ``manifest.json`` and ``dense.npy`` are read as TWO separate files (see
    ``load_index``) -- COR-12 made each individual write atomic, but a rebuild racing
    BETWEEN those two reads can still swap ``dense.npy`` out from under a reader that
    already has the OLD manifest in hand (e.g. the next build removes it, going
    BM25-only, or replaces it with a different-shape/different-entry-count matrix).
    Rather than exposing that torn pair, ``dense_ready`` is verified HERE against the
    actual loaded matrix's shape and every entry's ``row`` index -- any mismatch
    degrades to BM25-only for this read, exactly like a fully-failed dense load would.
    """

    def __init__(self, manifest: dict, dense):
        self.manifest = manifest
        self.entries: List[dict] = manifest.get("entries", [])
        dense_ready = bool(manifest.get("dense_ready")) and dense is not None
        if dense_ready and not self._dense_matches_entries(dense, self.entries):
            dense_ready = False
            dense = None
        self.dense_ready: bool = dense_ready
        self.dense = dense if self.dense_ready else None
        self.model: Optional[str] = manifest.get("model")

    @staticmethod
    def _dense_matches_entries(dense, entries: List[dict]) -> bool:
        try:
            n_rows = dense.shape[0]
        except Exception:
            return False
        if n_rows != len(entries):
            return False
        for e in entries:
            row = e.get("row")
            if row is None or not (0 <= row < n_rows):
                return False
        return True

    def __len__(self) -> int:
        return len(self.entries)


def load_index(index_dir: str) -> Optional[LoadedIndex]:
    manifest = _load_manifest(index_dir)
    if not manifest:
        return None
    dense = _load_dense(index_dir) if manifest.get("dense_ready") else None
    return LoadedIndex(manifest, dense)


# --------------------------------------------------------------------------- #
# QUA-5: on-disk corruption diagnosis (distinct from LoadedIndex's in-memory,
# already-degrades-gracefully view — this names WHAT is wrong on disk, for a
# SessionStart producer / doctor to surface, without needing a full recall).
# --------------------------------------------------------------------------- #
def check_index_integrity(index_dir: str) -> Optional[str]:
    """One-line diagnosis of on-disk index corruption, or ``None`` if nothing's wrong.

    Never raises. Distinguishes the three silent-degradation states this item closes:
      (a) ``manifest.json`` exists but isn't valid JSON (truncated/garbled) — recall already
          degrades to an empty/rebuilt index via ``_load_manifest``'s except->None, but
          nothing said so; the next ``refresh_index``/``build_index`` call rebuilds from
          scratch (``old_manifest`` is None -> full re-embed) so this self-heals, but the
          CURRENT session's recall was silently empty/BM25-only until then.
      (b) manifest claims ``dense_ready: true`` but ``dense.npy`` is missing — the LOADED view
          (``LoadedIndex``) already degrades this to BM25-only, but the ON-DISK manifest still
          wrongly claims dense_ready until the next rebuild overwrites it.
      (c) ``dense.npy`` exists but its shape doesn't match the manifest (row count != entry
          count, or column count != declared ``dim``) — ``LoadedIndex``/`_dense_rank`` already
          degrade this to BM25-only without raising, but silently.
    A missing manifest (no index built yet) is NOT corruption — returns ``None``.
    """
    try:
        manifest_path = os.path.join(index_dir, _MANIFEST_NAME)
        if not os.path.exists(manifest_path):
            return None  # nothing built yet -> not a corruption state
        manifest = _load_manifest(index_dir)
        if manifest is None:
            return (
                "index manifest is corrupt (invalid JSON) — will rebuild on next refresh"
            )
        dense_path = os.path.join(index_dir, _DENSE_NAME)
        if manifest.get("dense_ready"):
            if not os.path.exists(dense_path):
                return (
                    "index manifest claims dense embeddings exist but the data file is "
                    "missing — recall will degrade to BM25 until the next rebuild"
                )
            dense = _load_dense(index_dir)
            entries = manifest.get("entries", [])
            dim = manifest.get("dim")
            shape_ok = dense is not None and getattr(dense, "ndim", 0) == 2
            if shape_ok and dense.shape[0] != len(entries):
                shape_ok = False
            if shape_ok and dim is not None and dense.shape[1] != dim:
                shape_ok = False
            if not shape_ok:
                return (
                    "index dense.npy shape does not match the manifest (row/column count "
                    "mismatch) — recall will degrade to BM25 until the next rebuild"
                )
        return None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build the agent-memory recall index (offline).")
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--index-dir", default=None)
    parser.add_argument("--force", action="store_true", help="re-embed every memory")
    args = parser.parse_args(argv)

    memory_dir, _ = resolve_dirs()
    memory_dir = args.memory_dir or memory_dir
    index_dir = args.index_dir or default_index_dir(memory_dir)

    manifest = build_index(memory_dir, index_dir, force=args.force)
    print(f"index dir     : {index_dir}")
    print(f"memories      : {manifest['count']}")
    print(f"dense backend : {'ready (' + str(manifest['model']) + ')' if manifest['dense_ready'] else 'BM25-only (fastembed unavailable/disabled)'}")
    if manifest["dense_ready"]:
        print(f"embedding dim : {manifest['dim']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
