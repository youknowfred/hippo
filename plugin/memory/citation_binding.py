"""FMT-2: the derived citation binding, kept beside the index instead of in frontmatter.

A memory's ``cited_paths`` are derived data: the extractor reads the body, the resolver
pins each token to one tracked file, and the result is committed into frontmatter. Every
extractor fix therefore became per-memory corpus work (about 600 rederive calls across two
derivation bumps). Here the index derives the same binding from the BODY with the current
extractor on every build, and keeps it in a sidecar in the index dir (``citation_binding.json``):

  - per memory, keyed by name, with the sha1 of the full file text it was derived from:
    ``cited_paths`` (the resolver's paths minus the memory's own authored
    ``cited_paths_exclude``), the paths that exclusion held out, the body tokens nothing
    could pin, and the evidence-anchor candidates
    (``staleness_anchors.candidate_identifiers``);
  - stamped with ``CITATION_DERIVATION_VERSION`` and ``ANCHOR_DERIVATION_VERSION``, and a
    fingerprint of the repo file list the paths were resolved against;
  - rebuilt incrementally: a memory whose text and the file list are both unchanged keeps
    its row; a changed file list re-derives every row (resolution depends on it).

What the sidecar does NOT carry: ``source_commit``, ``cited_paths_exclude`` and relations
stay authored in frontmatter, and nothing CUR-1 preserved by hand is in it (a path the body
does not name is not derivable). A missing, torn, schema- or version-mismatched sidecar
reads as absent, so every reader falls back to frontmatter, which is exactly today.

Readers PREFER it (dual mode): ``Bound.cited`` returns a memory's sidecar paths when the
sidecar is current AND its row was derived from this exact text, else the frontmatter's.
Writers still write frontmatter (``new_memory``, reverify, rederive). ``HIPPO_INDEX_BINDING=0``
is the kill switch: frontmatter only and no sidecar write, exactly the previous release.

The main manifest and the dense file are untouched: the prompt hook loads the manifest on
every prompt and must not pay for this. One ``git ls-files`` per build or refresh, never one
per memory. Never raises from a public entry point.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Optional, Tuple

from .provenance_citations import (
    _strip_relative_prefix,
    extract_citations,
    frontmatter_excluded_paths,
    resolve_citations,
)

BINDING_SCHEMA = 1
BINDING_NAME = "citation_binding.json"

# Kill switch (HOT-9's shape, outside the frozen HIPPO_DISABLE set): "0" means frontmatter
# only and no sidecar write. Unset or any other value: readers prefer the index binding.
INDEX_BINDING_ENV = "HIPPO_INDEX_BINDING"


def binding_enabled() -> bool:
    return os.environ.get(INDEX_BINDING_ENV, "").strip() != "0"


def _versions() -> Tuple[int, int]:
    from .provenance_format import CITATION_DERIVATION_VERSION
    from .staleness_anchors import ANCHOR_DERIVATION_VERSION

    return CITATION_DERIVATION_VERSION, ANCHOR_DERIVATION_VERSION


def text_hash(text: str) -> str:
    """The key a row is derived from: sha1 of the memory file's full text."""
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def binding_path(index_dir: str) -> str:
    return os.path.join(index_dir, BINDING_NAME)


# --------------------------------------------------------------------------- #
# Derivation
# --------------------------------------------------------------------------- #
def repo_file_index(repo_root: str) -> Optional[Tuple[set, Dict[str, List[str]], str]]:
    """``(repo_files, basename_index, files_sig)`` from ONE ``git ls-files`` call, or None
    when ``repo_root`` is not a git work tree (or lists nothing): with no oracle every
    citation would resolve to nothing, so no binding is better than an empty one."""
    from .provenance_env import run_git

    raw = run_git(["ls-files", "--full-name"], repo_root)
    files = sorted({f for f in raw.split("\n") if f})
    if not files:
        return None
    return file_index_from_list(files)


def file_index_from_list(files: List[str]) -> Tuple[set, Dict[str, List[str]], str]:
    """The same triple from an already-listed file set (the gate replays use ``ls-tree``)."""
    files = sorted(set(files))
    basename_index: Dict[str, List[str]] = {}
    for f in files:
        basename_index.setdefault(f.rsplit("/", 1)[-1], []).append(f)
    sig = hashlib.sha1("\n".join(files).encode("utf-8")).hexdigest()
    return set(files), basename_index, sig


def derive_row(text: str, repo_files: set, basename_index: Dict[str, List[str]]) -> dict:
    """One memory's binding from its text. Pure. ``cited_paths`` is the ONE resolver's
    output (``provenance_citations.resolve_citations``, the extractor rederive uses) minus
    the authored exclusion; the frontmatter's own ``cited_paths`` are never consulted."""
    from .provenance import parse_frontmatter, split_frontmatter
    from .staleness_anchors import candidate_identifiers

    _fm_lines, body = split_frontmatter(text)
    fm = parse_frontmatter(text) or {}
    tokens = extract_citations(body)
    resolved = resolve_citations(tokens, repo_files, basename_index)
    excluded = set(frontmatter_excluded_paths(fm))
    unresolved = [t for t in tokens if not resolve_citations([t], repo_files, basename_index)]
    return {
        "hash": text_hash(text),
        "cited_paths": [p for p in resolved if p not in excluded],
        "excluded": [p for p in resolved if p in excluded],
        "unresolved": unresolved,
        "anchors": candidate_identifiers(text),
    }


def build_doc(
    texts: Dict[str, str],
    file_index: Tuple[set, Dict[str, List[str]], str],
    prior: Optional[dict] = None,
) -> Tuple[dict, int]:
    """``(doc, rows_derived)``: the sidecar for ``texts`` ({name: full text}), reusing a
    prior row whose hash matches when the prior was derived against the same file list at
    the same derivation versions. Pure."""
    repo_files, basename_index, sig = file_index
    cite_v, anchor_v = _versions()
    reuse = {}
    if prior and prior.get("files_sig") == sig:
        reuse = prior.get("memories") or {}
    rows: Dict[str, dict] = {}
    derived = 0
    for name in sorted(texts):
        text = texts[name]
        old = reuse.get(name)
        if isinstance(old, dict) and old.get("hash") == text_hash(text):
            rows[name] = old
            continue
        rows[name] = derive_row(text, repo_files, basename_index)
        derived += 1
    doc = {
        "schema": BINDING_SCHEMA,
        "cite_derivation": cite_v,
        "anchor_derivation": anchor_v,
        "files_sig": sig,
        "memories": rows,
    }
    return doc, derived


# --------------------------------------------------------------------------- #
# Read / write
# --------------------------------------------------------------------------- #
def _read_raw(index_dir: str) -> Optional[dict]:
    try:
        with open(binding_path(index_dir), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception:
        return None
    if not isinstance(doc, dict) or doc.get("schema") != BINDING_SCHEMA:
        return None
    if not isinstance(doc.get("memories"), dict):
        return None
    return doc


def binding_state(index_dir: str) -> Tuple[str, Optional[dict]]:
    """``("absent" | "behind" | "current", doc)``. Missing, torn or another schema is
    absent; a sidecar derived by another extractor or anchor definition is behind (doc
    returned for the report, but ``read_binding`` will not serve it)."""
    doc = _read_raw(index_dir)
    if doc is None:
        return "absent", None
    if (doc.get("cite_derivation"), doc.get("anchor_derivation")) != _versions():
        return "behind", doc
    return "current", doc


def read_binding(index_dir: str) -> Optional[dict]:
    """The sidecar when it is current, else None (absent, torn, or version-mismatched all
    read as absent). Never raises."""
    try:
        state, doc = binding_state(index_dir)
        return doc if state == "current" else None
    except Exception:
        return None


def row_for(doc: Optional[dict], name: str, text: str) -> Optional[dict]:
    """``name``'s row when it was derived from exactly ``text``, else None."""
    if not doc:
        return None
    row = (doc.get("memories") or {}).get(name)
    if isinstance(row, dict) and row.get("hash") == text_hash(text):
        return row
    return None


def write_binding(
    index_dir: str,
    memory_dir: str,
    texts: Dict[str, str],
    *,
    repo_root: Optional[str] = None,
    file_index: Optional[Tuple[set, Dict[str, List[str]], str]] = None,
) -> Optional[dict]:
    """Bring the sidecar up to date with ``texts`` (the texts the index build already read,
    so no extra file reads). Writes only when something changed. Returns the doc, or None
    when there is no git work tree to resolve against or anything failed (the sidecar then
    stays as it was; a reader treats a stale one row by row via ``row_for``). Never raises."""
    try:
        if not binding_enabled():
            return None
        if file_index is None:
            if repo_root is None:
                from .provenance_env import git_root

                repo_root = git_root(memory_dir)
            if not repo_root:
                return None
            file_index = repo_file_index(repo_root)
            if file_index is None:
                return None
        prior = read_binding(index_dir)
        doc, derived = build_doc(texts, file_index, prior)
        if prior is not None and derived == 0 and set(prior["memories"]) == set(doc["memories"]):
            return prior
        _write_doc(index_dir, doc)
        return doc
    except Exception:
        return None


def _write_doc(index_dir: str, doc: dict) -> None:
    from .atomic import write_json_atomic

    os.makedirs(index_dir, exist_ok=True)
    write_json_atomic(binding_path(index_dir), doc, indent=None, sort_keys=True)


# --------------------------------------------------------------------------- #
# The reader accessor: the sidecar's paths when current for this text, else frontmatter's
# --------------------------------------------------------------------------- #
def index_dir_for(memory_dir: str) -> str:
    """``build_index.default_index_dir`` without importing the index builder."""
    override = os.environ.get("HIPPO_INDEX_DIR")
    if override:
        return override
    from .derived_dirs import derived_dir

    return derived_dir(memory_dir, "index")


class Bound:
    """One sidecar read for a corpus pass. ``cited(name, text, fallback)`` is THE accessor
    every ``cited_paths`` reader goes through."""

    def __init__(self, memory_dir: Optional[str] = None, index_dir: Optional[str] = None):
        self.doc = None
        try:
            if binding_enabled() and (index_dir or memory_dir):
                self.doc = read_binding(index_dir or index_dir_for(memory_dir))
        except Exception:
            self.doc = None

    def row(self, name: str, text: str) -> Optional[dict]:
        try:
            return row_for(self.doc, name, text)
        except Exception:
            return None

    def cited(self, name: str, text: str, fallback: Optional[List[str]] = None) -> List[str]:
        """The sidecar's ``cited_paths`` when its row was derived from exactly ``text`` at
        the current derivation versions; else ``fallback`` (the frontmatter's, read here
        when not given)."""
        row = self.row(name, text)
        if row is not None:
            return list(row.get("cited_paths") or [])
        if fallback is None:
            fallback = frontmatter_cited(text)
        return list(fallback)


def bound(memory_dir: Optional[str], index_dir: Optional[str] = None) -> Bound:
    return Bound(memory_dir, index_dir)


def drop_index_bound(memory_dir: str, work: List[dict]) -> Tuple[List[dict], int]:
    """``(still_listed, dropped)``: the re-derivation worklist without the memories whose
    index binding is current (readers already use the current extractor's binding for
    them, so the per-memory rederive buys nothing). What stays is what the index cannot
    derive: an error, no row, or a row derived from other text (an outbox copy, an edit
    since the last refresh). Never raises; the input unchanged on any failure."""
    try:
        b = bound(memory_dir)
        if b.doc is None:
            return list(work), 0
        from .outbox import effective_path

        keep: List[dict] = []
        for w in work:
            name = w.get("name") or ""
            ok = False
            if not w.get("error"):
                try:
                    with open(effective_path(memory_dir, name), "r", encoding="utf-8") as fh:
                        ok = b.row(name, fh.read()) is not None
                except Exception:
                    ok = False
            if not ok:
                keep.append(w)
        return keep, len(work) - len(keep)
    except Exception:
        return list(work), 0


def all_index_bound(memory_dir: str) -> bool:
    """True when the binding is on and EVERY memory file has a current row (text read and
    hashed, no YAML parse): the citation-derivation nudge has nothing left to ask for."""
    try:
        b = bound(memory_dir)
        if b.doc is None:
            return False
        from .outbox_edit import iter_files

        seen = False
        for path in iter_files(memory_dir):
            seen = True
            with open(path, "r", encoding="utf-8") as fh:
                if b.row(os.path.basename(path)[:-3], fh.read()) is None:
                    return False
        return seen
    except Exception:
        return False


def review_lines(texts: Dict[str, str], repo_root: str, ref: Optional[str] = None) -> List[str]:
    """The review packet's section: what each touched memory binds to, derived from its
    body at ``ref`` (the working tree when None) with the current extractor, beside what its
    frontmatter lists. Never raises."""
    try:
        if not texts:
            return []
        from .provenance_env import run_git

        if ref:
            raw = run_git(["ls-tree", "-r", "--name-only", "--full-tree", ref], repo_root)
            files = [f for f in raw.split("\n") if f]
            fi = file_index_from_list(files) if files else None
        else:
            fi = repo_file_index(repo_root)
        if fi is None:
            return []
        lines = ["derived citations (what each touched memory binds to, from its body):"]
        for stem in sorted(texts):
            text = texts[stem]
            row = derive_row(text, fi[0], fi[1])
            cited, fm = row["cited_paths"], frontmatter_cited(text)
            shown = ", ".join(cited) if cited else "(none)"
            extra = []
            lacks = [p for p in cited if p not in fm]
            only = [p for p in fm if p not in cited]
            if lacks:
                extra.append(f"frontmatter lacks {', '.join(lacks)}")
            if only:
                extra.append(f"frontmatter also lists {', '.join(only)}")
            if row["excluded"]:
                extra.append(f"excluded by the memory: {', '.join(row['excluded'])}")
            tail = f" ({'; '.join(extra)})" if extra else ""
            lines.append(f"- `{stem}`: {shown}{tail}")
        return lines
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# Comparison: frontmatter against the sidecar (doctor, migrate --check, the gate)
# --------------------------------------------------------------------------- #
def frontmatter_cited(text: str) -> List[str]:
    from .provenance import parse_frontmatter
    from .provenance_citations import _frontmatter_cited_paths

    return _frontmatter_cited_paths(parse_frontmatter(text) or {})


def compare(memory_dir: str, index_dir: Optional[str] = None) -> dict:
    """How the sidecar's binding stands against frontmatter, over the committed files.

    ``{"state", "memories", "derived", "disagree", "disagree_names"}``: ``derived`` counts
    memories with a row derived from their current text (the bindings the index already
    derives); ``disagree`` those among them whose frontmatter ``cited_paths`` differ as a
    set from the row's. Read-only; never raises."""
    out = {"state": "absent", "memories": 0, "derived": 0, "disagree": 0, "disagree_names": []}
    try:
        from .provenance import _iter_memory_files

        if index_dir is None:
            from .build_index import default_index_dir

            index_dir = default_index_dir(memory_dir)
        state, doc = binding_state(index_dir)
        out["state"] = state
        if not os.path.isdir(memory_dir):
            return out
        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            out["memories"] += 1
            name = os.path.basename(path)[:-3]
            row = row_for(doc, name, text) if state == "current" else None
            if row is None:
                continue
            out["derived"] += 1
            if sorted(set(frontmatter_cited(text))) != sorted(set(row["cited_paths"])):
                out["disagree"] += 1
                out["disagree_names"].append(name)
    except Exception:
        pass
    return out


def classify_path(path: str, text: str, row: dict) -> str:
    """Why ONE path is in exactly one of (frontmatter, sidecar) for this memory: the
    mechanism names the FMT-2 parity report uses."""
    if path in row.get("cited_paths", []):
        return "body names it, frontmatter lacks it"
    if path in row.get("excluded", []):
        return "exclusion"
    from .provenance import split_frontmatter

    _fm, body = split_frontmatter(text)
    base = path.rsplit("/", 1)[-1]
    for tok in extract_citations(body):
        if _strip_relative_prefix(tok).rsplit("/", 1)[-1] == base:
            return "resolution difference"
    return "frontmatter has it, body no longer names it"


# --------------------------------------------------------------------------- #
# Doctor (one line) and migrate --check's count
# --------------------------------------------------------------------------- #
def summary(memory_dir: str, index_dir: Optional[str] = None) -> str:
    if not binding_enabled():
        return (f"citation binding in the index: off ({INDEX_BINDING_ENV}=0); readers use "
                "frontmatter cited_paths.")
    c = compare(memory_dir, index_dir)
    if c["state"] == "absent":
        return ("citation binding in the index: not built yet (the next index refresh "
                "derives it; until then frontmatter cited_paths are the binding).")
    if c["state"] == "behind":
        return ("citation binding in the index: derived by an older extractor; the next "
                "index refresh re-derives it.")
    tail = (f"; frontmatter cited_paths differ on {c['disagree']}" if c["disagree"]
            else "; frontmatter agrees on every one")
    return (f"citation binding in the index: current for {c['derived']} of {c['memories']} "
            f"memories, and staleness reads it for those{tail}.")


def check_citation_binding(ctx) -> Dict[str, str]:
    """Doctor's CHECKS entry: the sidecar's state, always ``ok`` (derived data; a memory
    without a current row falls back to its frontmatter)."""
    try:
        return {"status": "ok", "message": summary(ctx.memory_dir)}
    except Exception as exc:
        return {"status": "ok", "message": f"citation binding in the index: check skipped ({exc})."}
