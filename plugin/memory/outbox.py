"""OUT-1: the outbox — where a save lands when the corpus asks it to stay out of the working tree.

A memory is a file in a shared working tree, so on a corpus several checkouts write to, every
save is a commit someone has to land. A corpus can opt out of that per machine:
``hippo.json: "write_path": "outbox"`` sends ``new_memory``'s writes (and the floor pointer
they add) to a gitignored sibling of the corpus, ``<dirname(memory_dir)>/memory.outbox``
(``.claude/memory.outbox/`` beside ``.claude/memory/``, the same shape as the private tier's
``memory.local``). The corpus dir in the working tree is not touched, and recall fuses the
outbox like a tier, so the new memory is recallable on the next prompt.

This module is the one place that knows the layout. Rules it holds:

- **Location** comes from ``memory_dir`` only (``HIPPO_OUTBOX_DIR`` overrides it for hermetic
  tests). The policy value can never name a path: ``write_path`` is ``"outbox"`` or
  ``"direct"``; anything else behaves as ``"direct"`` and doctor names it. NOT under
  ``.hippo/``: that container holds derived, rebuildable dirs ``/hippo:remove`` offers to
  delete, and the outbox holds unshipped data. ``resolve_dirs`` sends a linked worktree to the
  main tree's corpus, so one clone has one outbox, shared by all its worktrees.
- **Writes** go here only when ``policy_consent.honoured(memory_dir, "write_path")`` is
  ``"outbox"``: the corpus asks AND this machine accepted that value (``hippo trust policy
  --accept``) AND the corpus is trusted here. A pulled, not-yet-accepted switch degrades to
  the direct write and the save says so (``held_back_line``).
- **Reads never depend on the policy.** If the outbox holds memories, recall fuses them and
  doctor counts them whatever ``hippo.json`` says now: turning the switch off must never hide
  the machine's own unshipped memories.
- **Trust.** A gitignored path can still be tracked (a force-added commit), so a pull can
  deliver a file into the outbox. Every hippo write here records the file's sha256 in the
  machine-local trust registry entry for the corpus (``entry["outbox"] = {stem: sha}``);
  recall injects an outbox memory only while its live bytes match. Anything else here is
  withheld and counted by doctor; a withheld copy that shadows a committed memory falls back
  to the committed one. A legacy fingerprint-less record, a non-git corpus and the
  ``HIPPO_TRUST_ALL`` bypass mean no per-file check, exactly as for the corpus.
- **The manifest** (``.outbox.json``) records, per file, the sha256 of the committed bytes it
  was copied from (``None`` for a new memory) and when. That is what a later ship step needs to
  tell "new" from "the committed copy moved since"; recording it from the first write means
  nothing written before that step exists is unclassifiable.

Everything on the prompt path here costs one ``isdir`` when there is no outbox. Never raises.
"""

from __future__ import annotations

import json
import os
from typing import Dict, Iterator, List, Optional, Tuple

OUTBOX_DIRNAME = "memory.outbox"
MANIFEST_NAME = ".outbox.json"
FLOOR_NAME = "MEMORY.md"
# OUT-2: the committed floor text a floor copy was made from, kept beside it so a ship can
# replay hippo's pointer adds and removes onto the floor at the base ref. Not a memory file.
FLOOR_BASE_NAME = ".floor-base"
WRITE_PATH_VALUES = ("outbox", "direct")
# The note a recalled outbox memory carries where the renderers show one.
RECALL_NOTE = "unshipped: in memory.outbox"
# What a save that landed here says (CLI and MCP).
SAVE_LINE = (
    "saved to the outbox beside the corpus, not the corpus dir: recall sees it from the next "
    "prompt; it is not in git until `hippo ship` batches the outbox into a branch"
)


def outbox_dir(memory_dir: str) -> str:
    """``<dirname(memory_dir)>/memory.outbox``; ``HIPPO_OUTBOX_DIR`` wins (hermetic tests)."""
    override = os.environ.get("HIPPO_OUTBOX_DIR")
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(memory_dir)), OUTBOX_DIRNAME)


def present(memory_dir: Optional[str]) -> Optional[str]:
    """The outbox dir when it exists, else None. One ``isdir``: the prompt path's whole cost."""
    try:
        if not memory_dir:
            return None
        d = outbox_dir(memory_dir)
        return d if os.path.isdir(d) else None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# The switch
# --------------------------------------------------------------------------- #
def declared_write_path(memory_dir: str):
    """``hippo.json``'s ``write_path`` as written (None when absent)."""
    from .policy_consent import declared

    return declared(memory_dir, "write_path")


def write_target(memory_dir: str, repo_root: Optional[str] = None) -> str:
    """``"outbox"`` when this machine may act on the corpus's ``write_path: "outbox"``,
    else ``"direct"`` (today's write). Never raises."""
    try:
        from .policy_consent import honoured

        return "outbox" if honoured(memory_dir, "write_path", repo_root) == "outbox" else "direct"
    except Exception:
        return "direct"


def unknown_write_path(memory_dir: str):
    """The declared value when it is neither ``"outbox"`` nor ``"direct"``, else None."""
    try:
        value = declared_write_path(memory_dir)
    except Exception:
        return None
    if value is None or (isinstance(value, str) and value in WRITE_PATH_VALUES):
        return None
    return value


def held_back_line(memory_dir: str, repo_root: Optional[str] = None) -> Optional[str]:
    """The pending-policy line when the corpus asks for the outbox and this machine has not
    accepted it (so the save went to the working tree), else None."""
    try:
        if declared_write_path(memory_dir) != "outbox":
            return None
        if write_target(memory_dir, repo_root) == "outbox":
            return None
        from .policy_consent import pending_line

        return pending_line(memory_dir, repo_root)
    except Exception:
        return None


def ensure(memory_dir: str) -> str:
    """Create the outbox (self-ignoring: a ``.gitignore`` of ``*`` inside it) and return it."""
    from .provenance_env import ensure_self_ignoring_dir

    d = outbox_dir(memory_dir)
    ensure_self_ignoring_dir(d)
    return d


def write_redirect(memory_dir: str, repo_root: Optional[str], tier: str) -> Tuple[Optional[str], Optional[str]]:
    """``write_memory``'s one question: ``(outbox_dir or None, held_back_line or None)``.

    Project tier only; the user and private tiers are never redirected. ``(None, None)`` for a
    corpus that declares nothing, so its save is today's save."""
    if tier != "project":
        return None, None
    try:
        if write_target(memory_dir, repo_root) == "outbox":
            return ensure(memory_dir), None
        return None, held_back_line(memory_dir, repo_root)
    except Exception:
        return None, None


def name_taken(memory_dir: str, name: str) -> bool:
    """True when ``<name>.md`` exists in the corpus dir or the outbox."""
    fname = _fname(name)
    if os.path.exists(os.path.join(memory_dir, fname)):
        return True
    d = present(memory_dir)
    return bool(d) and os.path.exists(os.path.join(d, fname))


# --------------------------------------------------------------------------- #
# Layout: effective paths, the overlay iterator
# --------------------------------------------------------------------------- #
def _fname(name: str) -> str:
    return name if name.endswith(".md") else f"{name}.md"


def _is_memory_filename(name: str) -> bool:
    from .provenance import _is_memory_filename as is_mem

    return is_mem(name)


def _sha(path: str) -> Optional[str]:
    from .trust import file_sha256

    return file_sha256(path)


def effective_path(memory_dir: str, name: str) -> str:
    """The outbox copy of ``<name>.md`` (or ``MEMORY.md``) when there is one, else the
    committed path (which may not exist)."""
    fname = _fname(name)
    d = present(memory_dir)
    if d:
        p = os.path.join(d, fname)
        if os.path.isfile(p):
            return p
    return os.path.join(memory_dir, fname)


def _memory_names(d: Optional[str]) -> List[str]:
    try:
        return sorted(n for n in os.listdir(d) if _is_memory_filename(n)) if d else []
    except OSError:
        return []


def iter_effective_memory_files(memory_dir: str) -> Iterator[str]:
    """Every memory file as this machine sees it: the committed files, each overlaid by its
    outbox copy when one exists, plus the outbox-only files. Sorted by file name; the same
    membership filter as ``provenance._iter_memory_files``."""
    paths: Dict[str, str] = {n: os.path.join(memory_dir, n) for n in _memory_names(memory_dir)}
    d = present(memory_dir)
    for n in _memory_names(d):
        paths[n] = os.path.join(d, n)
    for n in sorted(paths):
        yield paths[n]


# --------------------------------------------------------------------------- #
# The manifest and copy-up
# --------------------------------------------------------------------------- #
def manifest_path(memory_dir: str) -> str:
    return os.path.join(outbox_dir(memory_dir), MANIFEST_NAME)


def read_manifest(memory_dir: str) -> Dict[str, dict]:
    """``{file name: {"base_sha256", "at"}}``; ``{}`` when absent or unreadable."""
    try:
        with open(manifest_path(memory_dir), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        files = doc.get("files") if isinstance(doc, dict) else None
        return files if isinstance(files, dict) else {}
    except Exception:
        return {}


def _update_manifest(memory_dir: str, fname: str, base_sha: Optional[str]) -> bool:
    """Record ``fname``'s base once (the first record wins: it is what the copy was made
    from). Compare-and-swap, so two sessions saving at once never lose each other's row.
    Returns False when the manifest could not be written; the caller names that."""
    from datetime import datetime, timezone

    from .atomic import CasConflict, update_text_cas, write_text_cas

    path = manifest_path(memory_dir)
    at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def transform(text: str) -> Optional[str]:
        try:
            doc = json.loads(text) if text.strip() else {}
        except ValueError:
            doc = {}  # an unreadable manifest is rebuilt rather than blocking every save
        if not isinstance(doc, dict):
            doc = {}
        files = doc.get("files") if isinstance(doc.get("files"), dict) else {}
        if fname in files:
            return None
        files = dict(files)
        files[fname] = {"base_sha256": base_sha, "at": at}
        return json.dumps({"version": 1, "files": files}, indent=2, sort_keys=True) + "\n"

    try:
        try:
            write_text_cas(path, "", None)  # create while absent; a concurrent create is fine
        except CasConflict:
            pass
        update_text_cas(path, transform)
        return True
    except Exception:
        return False


def record_write(memory_dir: str, path: str, repo_root: Optional[str] = None, base_sha: Optional[str] = None) -> Optional[str]:
    """After hippo wrote ``path`` into the outbox: its manifest row and its trust hash.

    Returns None, or one line naming what did not land (for the save's warnings)."""
    problems = []
    if not _update_manifest(memory_dir, os.path.basename(path), base_sha):
        problems.append("its row in the outbox manifest")
    if _is_memory_filename(os.path.basename(path)):
        if _record_hash(memory_dir, path, repo_root) is False:
            problems.append("its trust record (recall will withhold it on this machine)")
    if not problems:
        return None
    return "the outbox copy was written but " + " and ".join(problems) + " could not be recorded"


def copy_up(memory_dir: str, name: str, repo_root: Optional[str] = None) -> Optional[str]:
    """Copy the committed ``<name>.md`` (or ``MEMORY.md``) into the outbox unless a copy is
    already there; record its base. Returns the outbox path, or None when there is no
    committed file to copy and no copy yet.

    A memory copy inherits the committed file's consent only when the committed bytes ARE the
    consented ones: copying a withheld file up must not launder it into recall."""
    fname = _fname(name)
    dest = os.path.join(outbox_dir(memory_dir), fname)
    if os.path.isfile(dest):
        return dest
    src = os.path.join(memory_dir, fname)
    try:
        with open(src, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    import hashlib

    from .atomic import CasConflict, write_text_cas

    ensure(memory_dir)
    if fname == FLOOR_NAME:
        _record_floor_base(memory_dir, data)  # OUT-2: before the copy, so a copy never lacks it
    try:
        write_text_cas(dest, data.decode("utf-8"), None)  # never replaces a copy made a moment ago
    except CasConflict:
        return dest
    base = hashlib.sha256(data).hexdigest()
    _update_manifest(memory_dir, fname, base)
    if _is_memory_filename(fname):
        from . import trust as T

        consented = T.consented_hashes(T.gate_repo_root(memory_dir, repo_root))
        if consented is not None and consented.get(os.path.splitext(fname)[0]) == base:
            _record_hash(memory_dir, dest, repo_root)
    return dest


def _record_floor_base(memory_dir: str, data: bytes) -> None:
    """OUT-2: keep the committed floor text a floor copy is made from (create-only while
    absent). A torn write raises before the copy exists, so no copy is ever left without
    its base; a copy whose base is missing is reported stale by ship, never replayed."""
    from .atomic import CasConflict, write_text_cas

    from .atomic import content_token

    path = os.path.join(outbox_dir(memory_dir), FLOOR_BASE_NAME)
    try:
        write_text_cas(path, data.decode("utf-8"), None)
    except CasConflict:
        if not os.path.isfile(os.path.join(outbox_dir(memory_dir), FLOOR_NAME)):
            # A base no copy belongs to (a copy-up torn after it): this copy's base replaces it.
            write_text_cas(path, data.decode("utf-8"), content_token(path))


def floor_target(memory_dir: str, repo_root: Optional[str] = None) -> str:
    """Where an outbox-mode floor edit lands: the outbox, holding a copy of the committed
    ``MEMORY.md`` made on first use. With no committed floor there is nothing to copy, and the
    edit reports the same missing-floor skip a direct write does."""
    try:
        copy_up(memory_dir, FLOOR_NAME, repo_root)
    except Exception:
        pass
    return outbox_dir(memory_dir)


# --------------------------------------------------------------------------- #
# Trust: the per-file record and the gate
# --------------------------------------------------------------------------- #
_ENTRY_KEY = "outbox"  # trusted[<corpus>]["outbox"] = {stem: sha256 of the bytes hippo wrote}


def _record_hash(memory_dir: str, path: str, repo_root: Optional[str]) -> Optional[bool]:
    """Record ``path``'s live sha256 under every trust entry for this corpus (its own key and,
    from a linked worktree, the main tree's, as the corpus fold does). None when nothing needs
    recording (the gate does not apply here, or the corpus is untrusted: recall injects nothing
    from it anyway); False when a record should have been written and was not."""
    try:
        from . import trust as T

        gate_root = T.gate_repo_root(memory_dir, repo_root)
        if gate_root is None:
            return None
        keys = [T._corpus_key(gate_root)]
        try:
            from .provenance_env import main_worktree_root

            main = main_worktree_root(gate_root)
            if main and T._corpus_key(main) not in keys:
                keys.append(T._corpus_key(main))
        except Exception:
            pass
        sha = _sha(path)
        if sha is None:
            return False
        doc = T._load_registry_doc()
        trusted = doc.get("trusted")
        if not isinstance(trusted, dict):
            return None
        stem = os.path.splitext(os.path.basename(path))[0]
        touched = False
        for key in keys:
            entry = trusted.get(key)
            if not isinstance(entry, dict):
                continue
            hashes = dict(entry.get(_ENTRY_KEY)) if isinstance(entry.get(_ENTRY_KEY), dict) else {}
            hashes[stem] = sha
            entry[_ENTRY_KEY] = hashes
            touched = True
        if not touched:
            return None
        return bool(T._write_registry_doc(doc))
    except Exception:
        return False


def outbox_hashes(memory_dir: str, repo_root: Optional[str] = None) -> Optional[dict]:
    """``{stem: sha}`` recall checks outbox files against, or None for "no per-file check"
    (the bypass, a non-git corpus, a legacy fingerprint-less record), mirroring
    ``trust.consented_hashes``. An untrusted corpus reads as ``{}``. Never raises."""
    try:
        from . import trust as T

        if T.trust_all():
            return None
        gate_root = T.gate_repo_root(memory_dir, repo_root)
        if gate_root is None:
            return None
        entry = T._trusted_entry(gate_root)
        if not entry:
            return {}
        fp = entry.get("fingerprint")
        if not isinstance(fp, dict) or not isinstance(fp.get("files"), dict):
            return None
        hashes = entry.get(_ENTRY_KEY)
        return dict(hashes) if isinstance(hashes, dict) else {}
    except Exception:
        return {}


def withheld(memory_dir: str, repo_root: Optional[str] = None) -> List[str]:
    """Outbox memory stems recall withholds: bytes hippo did not write on this machine."""
    d = present(memory_dir)
    if not d:
        return []
    allowed = outbox_hashes(memory_dir, repo_root)
    if allowed is None:
        return []
    out = []
    for n in _memory_names(d):
        stem = n[:-3]
        sha = _sha(os.path.join(d, n))
        if sha is None or sha != allowed.get(stem):
            out.append(stem)
    return out


# --------------------------------------------------------------------------- #
# Recall: the outbox as a tier that shadows the committed corpus
# --------------------------------------------------------------------------- #
def recall_tier(memory_dir: str, repo_root: Optional[str] = None):
    """``(LoadedIndex, outbox_dir, "project")`` for recall fusion, or None.

    None when there is no outbox (one ``isdir``) or nothing in it passes the trust check. The
    entries are labelled as project memories, carry ``outbox: True`` (the note, and the corpus
    quarantine skipping a file this gate already checked), and ``root`` becomes the outbox once
    merged, so the drift re-read and the file a reader opens are the outbox copy. A file whose
    bytes hippo did not write here is dropped BEFORE the merge, so a committed memory it would
    have shadowed is recalled instead (subject to its own consent check)."""
    d = present(memory_dir)
    if not d:
        return None
    try:
        from .build_index import LoadedIndex, compute_bm25_stats
        from .provenance_env import tier_index_dir
        from .recall_tiers import _ensure_index

        li = _ensure_index(None, d, tier_index_dir(d))
        if li is None or not len(li):
            return None
        allowed = outbox_hashes(memory_dir, repo_root)
        keep = []
        for i, e in enumerate(li.entries):
            if allowed is not None:
                sha = _sha(os.path.join(d, e.get("file") or ""))
                if sha is None or sha != allowed.get(e.get("name")):
                    continue
            keep.append(i)
        if not keep:
            return None
        manifest = dict(li.manifest)
        if len(keep) == len(li.entries):
            manifest["entries"] = [dict(e, outbox=True) for e in li.entries]
            return LoadedIndex(manifest, li.dense), d, "project"
        remap = {old: new for new, old in enumerate(keep)}
        entries = [dict(li.entries[i], outbox=True) for i in keep]
        chunks = [dict(c, entry=remap[c["entry"]]) for c in li.body_chunks if c.get("entry") in remap]
        dense = None
        if li.dense_ready and li.dense is not None:
            rows = [e.get("row") for e in entries] + [c.get("row") for c in chunks]
            dense = li.dense[rows]
            for n, item in enumerate(entries + chunks):
                item["row"] = n
        manifest.update(
            entries=entries,
            body_chunks=chunks,
            count=len(entries),
            dense_ready=dense is not None,
            bm25=compute_bm25_stats(
                [e.get("tokens") or [] for e in entries] + [c.get("tokens") or [] for c in chunks]
            ),
        )
        return LoadedIndex(manifest, dense), d, "project"
    except Exception:
        return None


def refresh_index(memory_dir: str) -> None:
    """Keep the outbox's nested index fresh (SessionStart, beside the other tiers), so the
    hot path can fuse it WITH dense. A no-op when there is no outbox. Never raises."""
    d = present(memory_dir)
    if not d:
        return
    try:
        from .build_index import refresh_index as _refresh
        from .provenance_env import tier_index_dir

        _refresh(d, tier_index_dir(d))
    except Exception:
        pass


def duplicate_neighbors(name: str, rendered: str, memory_dir: str, k: int) -> List[dict]:
    """``new_memory``'s near-duplicate check against the outbox's own index (the committed
    index cannot see unshipped memories). ``[]`` when there is no outbox or no index."""
    d = present(memory_dir)
    if not d:
        return []
    try:
        from .new_memory import _duplicate_neighbors
        from .provenance_env import tier_index_dir

        found, _note = _duplicate_neighbors(name, rendered, d, index_dir=tier_index_dir(d))
        return list(found)[:k]
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# Status: what doctor and remove report
# --------------------------------------------------------------------------- #
def tracked_by_git(memory_dir: str, repo_root: Optional[str] = None) -> List[str]:
    """Paths under the outbox that git tracks (a force-added commit). Off the hot path."""
    d = present(memory_dir)
    if not d:
        return []
    try:
        from .provenance_env import git_root, run_git

        root = git_root(repo_root or memory_dir) or git_root(d)
        if not root:
            return []
        out = run_git(["ls-files", "--", os.path.abspath(d)], root)
        return sorted(ln for ln in out.splitlines() if ln.strip())
    except Exception:
        return []


def status(memory_dir: str, repo_root: Optional[str] = None) -> dict:
    """``{"dir", "exists", "new", "edited", "floor_pending", "tracked_by_git", "withheld"}``.

    ``new``: outbox memories with no committed file of the same name; ``edited``: copies of a
    committed memory; ``floor_pending``: the outbox holds a ``MEMORY.md`` whose bytes differ
    from the committed floor. Never raises."""
    d = outbox_dir(memory_dir)
    res = {
        "dir": d, "exists": False, "new": 0, "edited": 0, "floor_pending": False,
        "tracked_by_git": 0, "withheld": 0,
    }
    if not present(memory_dir):
        return res
    res["exists"] = True
    try:
        for n in _memory_names(d):
            if os.path.exists(os.path.join(memory_dir, n)):
                res["edited"] += 1
            else:
                res["new"] += 1
        floor = os.path.join(d, FLOOR_NAME)
        if os.path.isfile(floor):
            res["floor_pending"] = _sha(floor) != _sha(os.path.join(memory_dir, FLOOR_NAME))
        res["tracked_by_git"] = len(tracked_by_git(memory_dir, repo_root))
        res["withheld"] = len(withheld(memory_dir, repo_root))
    except Exception:
        pass
    return res
