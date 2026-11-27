"""IDX-1: the dense matrix a manifest is bound to — written, found, retired, checked.

COR-12 made ``dense.npy`` and ``manifest.json`` each atomic, written in that order, but
nothing ordered two CONCURRENT builds: A writes its matrix, B writes its matrix and its
manifest, A writes its manifest last, and A's entries now index B's rows. QUA-4's load
guard checks shape and row bounds, which a same-size pair passes, and every later
incremental build copied the wrong rows forward by hash. Measured on a 1,315-memory
field corpus (2026-11-03): 1,016 entries (77%) scored against another memory's vector.

The fix is by construction, not by lock: each build writes its OWN ``dense.<id>.npy`` and
its manifest names that file (``dense_file``), so whichever manifest lands last names the
matrix its own build wrote. Superseded matrices are retired after a grace (a reader that
holds the previous manifest can still open its file). A manifest with no ``dense_file`` is
the legacy shape — still written by a session running an older plugin — and still served
from ``dense.npy``; before a build REUSES such a prior's rows it spot-checks a sample of
them against a fresh embed (``prior_rows_aligned``), so a torn legacy index heals on its
next build instead of being copied forward. Leaf module: no imports from ``build_index``.
"""

from __future__ import annotations

import os
import re
import threading
import time
import uuid
from typing import Callable, Dict, List, Optional, Tuple

LEGACY_DENSE_NAME = "dense.npy"
_BOUND_RE = re.compile(r"^dense\.[0-9a-f]{16}\.npy$")
# A reader that loaded the previous manifest opens its matrix within milliseconds; builds
# on one corpus can run seconds apart. Minutes of grace cost a few MB, briefly.
RETIRE_GRACE_SECS = 300
# Rows re-embedded to vouch for an unbound prior. A torn pair is misaligned across most
# rows (the field case: 77%), so a small spread sample finds it.
VERIFY_SAMPLE = 8
_ALIGNED_COSINE = 0.99


# HCR-9: what an entry's dense row embeds. "description" is the name + description line
# (``doc_text``), the shape every index had before; "whole" is the memory's whole file as
# written, frontmatter included, cut by the embedder at its own token limit. The body-chunk
# rows sit beside either. HCR-8 measured the whole file as the largest lever on the Qwen3
# 4B (303 -> 320 of 347 held-out questions in the top 10, 336 with the MMR re-cut rescaled);
# planning/HCR-9.calibration.evidence.2026-11-27.json is the run each listed model's floor,
# gate and count rule were re-measured in. A model NOT listed here keeps description rows and
# the calibration it already has: the row text and the cosine thresholds change together or
# not at all. bge-small was measured on whole rows too and left out (the same file says why). A manifest records its mode as ``row_text`` (absent = description), and an
# entry's ``row_hash`` (sha1 over the whole file) is the reuse key for a whole row, so a
# mode change re-embeds the entry rows and nothing else.
ROW_WHOLE, ROW_DESCRIPTION = "whole", "description"
WHOLE_ROW_MODELS = frozenset({"Qwen/Qwen3-Embedding-4B"})


def row_text(space: Optional[str]) -> str:
    """The row mode of embedding space ``space`` (a model id, or ``model@dim``)."""
    return ROW_WHOLE if (space or "").split("@", 1)[0] in WHOLE_ROW_MODELS else ROW_DESCRIPTION


def row_hash(text: str) -> str:
    """The reuse key of a whole-file row. Prefixed, so it can never equal a ``doc_text`` or
    body-chunk hash of the same characters."""
    import hashlib

    return hashlib.sha1(("row:" + text).encode("utf-8")).hexdigest()


def _row_key(entry: dict, mode: str) -> Optional[str]:
    return entry.get("row_hash") if mode == ROW_WHOLE else entry.get("hash")


def row_plan(entries: List[dict], chunks: List[dict], texts: Dict[str, str], space: str,
             old_manifest: Optional[dict], old_dense) -> Tuple[str, List[str], List[str], Dict[str, int]]:
    """One build's dense work list: ``(mode, keys, docs, prior)``. ``keys`` and ``docs`` run
    entries first, then body chunks (the matrix's row order); ``prior`` maps a key to the row
    of ``old_dense`` that already holds its vector. A prior in another model, or whose entry
    rows embed the other text, contributes only what still matches: a partially-embedded
    manifest (COR-3) contributes its finished rows, so a large corpus converges across
    builds."""
    mode = row_text(space)
    keys = [_row_key(e, mode) or e["hash"] for e in entries] + [c["hash"] for c in chunks]
    docs = [(texts.get(e["name"]) if mode == ROW_WHOLE else None) or e["doc_text"] for e in entries]
    docs += [c["text"] for c in chunks]
    prior: Dict[str, int] = {}
    if old_manifest and old_dense is not None and old_manifest.get("model") == space:
        old_mode = old_manifest.get("row_text") or ROW_DESCRIPTION
        rows = [(_row_key(e, old_mode) if old_mode == mode else None, e.get("row"))
                for e in old_manifest.get("entries", [])]
        rows += [(c.get("hash"), c.get("row")) for c in old_manifest.get("body_chunks", []) or []]
        for key, row in rows:
            if key and row is not None and 0 <= row < len(old_dense):
                prior[key] = row
    return mode, keys, docs, prior


def _unique_tmp(path: str) -> str:
    # COR-17 keyed temp names on the pid alone; two builds in one process (threads) shared it.
    return f"{path}.tmp.{os.getpid()}.{threading.get_ident()}.npy"


def dense_path(index_dir: str, manifest: Optional[dict]) -> Optional[str]:
    """The matrix ``manifest`` is bound to; ``dense.npy`` for the legacy shape; None when the
    manifest names a file outside the bound shape (never followed: it is read from disk)."""
    name = (manifest or {}).get("dense_file")
    if name is None:
        return os.path.join(index_dir, LEGACY_DENSE_NAME)
    if not isinstance(name, str) or not _BOUND_RE.match(name):
        return None
    return os.path.join(index_dir, name)


def load_dense(index_dir: str, manifest: Optional[dict]):
    p = dense_path(index_dir, manifest)
    if p is None or not os.path.exists(p):
        return None
    try:
        import numpy as np

        return np.load(p)
    except Exception:
        return None


def write_dense(index_dir: str, rows) -> str:
    """Persist ``rows`` under a name no other build uses; returns the ``dense_file`` name."""
    import numpy as np

    name = f"dense.{uuid.uuid4().hex[:16]}.npy"
    path = os.path.join(index_dir, name)
    tmp = _unique_tmp(path)
    try:
        np.save(tmp, rows)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
    return name


def retire_dense(index_dir: str, keep: Optional[str], now: Optional[float] = None) -> None:
    """After this build's manifest is in place: drop the legacy ``dense.npy`` (an older
    plugin's reader must degrade to BM25, never pair it with a bound manifest) and every
    bound matrix other than ``keep`` older than the grace. Never raises."""
    now = time.time() if now is None else now
    try:
        names = os.listdir(index_dir)
    except Exception:
        return
    for name in names:
        if name == keep:
            continue
        path = os.path.join(index_dir, name)
        try:
            if name == LEGACY_DENSE_NAME:
                os.remove(path)
            elif _BOUND_RE.match(name) and now - os.stat(path).st_mtime > RETIRE_GRACE_SECS:
                os.remove(path)
        except Exception:
            pass


def _sample(manifest: dict, n: int) -> list:
    """``(row, text)`` pairs whose text the manifest itself holds. A whole-row manifest does
    not store the files its entry rows embed, so its body chunks stand in: a torn pair is
    misaligned across the matrix, chunk rows included."""
    if (manifest.get("row_text") or ROW_DESCRIPTION) == ROW_WHOLE:
        rows = [(c["row"], c["text"]) for c in manifest.get("body_chunks") or []
                if c.get("row") is not None and c.get("text")]
    else:
        rows = [(e["row"], e["doc_text"]) for e in manifest.get("entries") or []
                if e.get("row") is not None and e.get("doc_text")]
    if len(rows) <= n:
        return rows
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)]


def prior_rows_aligned(manifest: dict, dense, embed: Callable) -> Optional[bool]:
    """Does a sample of ``manifest``'s entries hold its own vector in ``dense``?

    True: aligned. False: torn — at least one sampled row is another text's vector. None:
    the check could not run (no model). ``embed(texts) -> [n, dim]``."""
    sample = _sample(manifest, VERIFY_SAMPLE)
    if not sample:
        return True
    try:
        vecs = embed([text for _row, text in sample])
        for (row, _text), v in zip(sample, vecs):
            if not 0 <= row < len(dense) or float(dense[row] @ v) < _ALIGNED_COSINE:
                return False
        return True
    except Exception:
        return None


def alignment_line(manifest: dict, dense, embed: Callable) -> Optional[str]:
    """Doctor's one line for a misaligned index, or None (aligned or not checkable)."""
    if prior_rows_aligned(manifest, dense, embed) is not False:
        return None
    return (
        "index is misaligned: sampled entries are scored against another memory's vector "
        "(a torn manifest/dense pair from concurrent builds) — dense recall is wrong until "
        "the index is rebuilt: `hippo build-index --force` (on Desktop, the setup tool's "
        "build_index action: a build never reuses a torn index's rows)"
    )
