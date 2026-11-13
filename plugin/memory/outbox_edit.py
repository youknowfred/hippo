"""OUT-1, the tend half: the one seam every verdict uses to edit a memory that already exists.

The first slice sent ``new_memory``'s saves to the outbox. Every other hippo write edits a
memory that is already there: a reverify graduate, a demote, a typed edge, a citation
re-derivation, a baseline heal, a dream edge. With ``write_path: outbox`` honoured, those
must not touch the corpus dir in the working tree either, and whatever READS the item back
(the tend source that queued it, or decides it is resolved) must read what was written, or
the same item re-queues forever.

So a writer asks two questions here, and nothing else changes for it:

- ``resolve(path)`` (the read side): the outbox copy of that memory when one exists, else
  ``path`` unchanged. Cheap; never copies.
- ``edit_path(path)`` (the write side, called right before the write): the path to write.
  For a project-tier memory in a corpus whose accepted ``hippo.json`` says
  ``"write_path": "outbox"``, that is the outbox copy, made by ``outbox.copy_up`` on first
  edit. An existing copy is always the target, whatever the switch says now, because it is
  what recall serves and what the flipped readers read. The user and private tiers are never
  redirected. A committed file this machine withholds (its bytes are not the consented ones)
  is REFUSED with the reason: copying it up would launder bytes nobody consented to into
  recall, and falling back to a direct write would put the edit in the working tree. An
  outbox copy whose bytes hippo did not record writing is refused the same way.

After the write, the writer's existing consent fold (``trust.record_authored_write`` /
``carry_consent_forward``) reaches ``record_edit`` instead of the corpus baseline when the
path is an outbox copy (a three-line diversion inside ``trust._fold_file``): the manifest
row, the per-file outbox hash, and an outbox index refresh so recall serves the edit on the
next prompt. A mechanical rewrite (``before_sha`` set) carries the outbox hash forward only
when the bytes it read were the recorded ones, the same rule ``carry_consent_forward``
applies to the corpus.

Removals stay direct working-tree changes (archive, promote's source removal); ``drop_copy``
removes the outbox copy of a removed memory with its manifest row and hash record, so nothing
later resurrects it.

Readers that feed a tend kind switch to the effective view explicitly
(``outbox.iter_effective_memory_files`` / ``effective_path`` / ``effective_graph`` here).
The index build, the trust fingerprint, review, the gates and census keep reading the
committed dir. With no outbox and the switch off, every function here returns its input
unchanged after one ``isdir`` and one small policy read. Never raises.
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

from . import outbox as OB


# --------------------------------------------------------------------------- #
# Which corpus a path belongs to
# --------------------------------------------------------------------------- #
def owner_of(path: str) -> Optional[str]:
    """The corpus dir whose outbox holds ``path``, or None when ``path`` is not an outbox
    copy. Checks the candidate corpora by their own outbox location, so a dir merely named
    like an outbox is not mistaken for one."""
    try:
        d = os.path.dirname(os.path.abspath(path))
        candidates = []
        env = os.environ.get("HIPPO_MEMORY_DIR")
        if env:
            candidates.append(env)
        if os.path.basename(d) == OB.OUTBOX_DIRNAME:
            candidates.append(os.path.join(os.path.dirname(d), "memory"))
        for md in candidates:
            if os.path.abspath(OB.outbox_dir(md)) == d:
                return md
        return None
    except Exception:
        return None


def _redirectable(memory_dir: str) -> bool:
    """Project tier only: the user tier and the private tier are never redirected."""
    try:
        from .provenance_env import user_memory_dir

        md = os.path.abspath(memory_dir)
        if md == os.path.abspath(user_memory_dir()) or os.path.basename(md) == "memory.local":
            return False
        local = os.environ.get("HIPPO_LOCAL_MEMORY_DIR")
        return not (local and md == os.path.abspath(local))
    except Exception:
        return False


def _stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


# --------------------------------------------------------------------------- #
# The read side
# --------------------------------------------------------------------------- #
def resolve(path: str) -> str:
    """The outbox copy of the memory at ``path`` when there is one, else ``path``."""
    try:
        if owner_of(path):
            return path
        name = os.path.basename(path)
        md = os.path.dirname(os.path.abspath(path))
        if not OB._is_memory_filename(name) or not _redirectable(md):
            return path
        d = OB.present(md)
        if d:
            copy = os.path.join(d, name)
            if os.path.isfile(copy):
                return copy
        return path
    except Exception:
        return path


def iter_files(memory_dir: str):
    """A flipped reader's file walk: ``provenance._iter_memory_files`` exactly (its errors
    included) when there is no outbox, else the effective view."""
    if OB.present(memory_dir):
        return OB.iter_effective_memory_files(memory_dir)
    from .provenance import _iter_memory_files

    return _iter_memory_files(memory_dir)


def effective_texts(memory_dir: str) -> Dict[str, str]:
    """``{stem: text}`` over the effective view (committed files overlaid by outbox copies,
    plus outbox-only files), in file-name order. Unreadable files are skipped."""
    out: Dict[str, str] = {}
    for p in iter_files(memory_dir):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                out[_stem(p)] = fh.read()
        except Exception:
            continue
    return out


def effective_graph(memory_dir: str, index_dir: Optional[str] = None):
    """The link graph a flipped reader needs: ``links.build_graph`` unchanged when there is
    no outbox (its cache fast path included), else a graph over the effective texts (an
    outbox copy's edges replace the committed file's; outbox-only memories join). Off the
    hot path: only tend sources and verdicts call this."""
    from .links import build_graph

    if not OB.present(memory_dir):
        return build_graph(memory_dir, index_dir)
    try:
        from .links_graph import LinkGraph

        return LinkGraph(memory_dir, texts=effective_texts(memory_dir))
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# The write side
# --------------------------------------------------------------------------- #
def _copy_withheld(memory_dir: str, copy: str, repo_root: Optional[str]) -> Optional[str]:
    allowed = OB.outbox_hashes(memory_dir, repo_root)
    if allowed is None:
        return None
    if OB._sha(copy) == allowed.get(_stem(copy)):
        return None
    return (
        f"the outbox copy of {_stem(copy)} is withheld on this machine (hippo did not write "
        "its current bytes here); read it with `hippo trust review` and grant it before "
        "editing it"
    )


def _committed_withheld(memory_dir: str, path: str, repo_root: Optional[str]) -> Optional[str]:
    from . import trust as T

    consented = T.consented_hashes(T.gate_repo_root(memory_dir, repo_root))
    if consented is None:
        return None  # no per-file check here (bypass, untrusted, legacy, non-git)
    if consented.get(_stem(path)) == OB._sha(path):
        return None
    return (
        f"{_stem(path)} is withheld on this machine (its bytes changed since you consented, "
        "or it is new since then), so it cannot be copied into the outbox: review it with "
        "`hippo trust review` first. Nothing was written"
    )


def edit_path(
    path: str, repo_root: Optional[str] = None, memory_dir: Optional[str] = None
) -> Tuple[Optional[str], Optional[str]]:
    """``(path to write, refusal)``. Exactly one is None. See the module docstring."""
    try:
        name = os.path.basename(path)
        if not OB._is_memory_filename(name):
            return path, None  # a staging file, the floor: not this seam's business
        owner = owner_of(path)
        if owner:
            why = _copy_withheld(owner, path, repo_root)
            return (None, why) if why else (path, None)
        md = memory_dir or os.path.dirname(os.path.abspath(path))
        if not _redirectable(md):
            return path, None
        d = OB.present(md)
        copy = os.path.join(d, name) if d else None
        if copy and os.path.isfile(copy):
            why = _copy_withheld(md, copy, repo_root)
            return (None, why) if why else (copy, None)
        if OB.write_target(md, repo_root) != "outbox" or not os.path.isfile(path):
            return path, None  # today's write (a missing file reports itself as today)
        why = _committed_withheld(md, path, repo_root)
        if why:
            return None, why
        dest = OB.copy_up(md, name, repo_root)
        if not dest:
            return None, f"could not copy {name} into the outbox; nothing was written"
        return dest, None
    except Exception as exc:
        return None, f"could not prepare the outbox copy of {os.path.basename(path)}: {exc}"


class OutboxRefusal(RuntimeError):
    """``edit_path``'s refusal, raised for a writer whose own handler names the error."""


def edit_or_raise(path: str, repo_root: Optional[str] = None, memory_dir: Optional[str] = None) -> str:
    """``edit_path`` for a writer whose ``except`` already turns an exception into its
    ``error``: the path to write, or ``OutboxRefusal`` with the reason."""
    target, why = edit_path(path, repo_root, memory_dir)
    if why or not target:
        raise OutboxRefusal(why or "no path to write")
    return target


def record_edit(path: str, repo_root: Optional[str] = None, before_sha: Optional[str] = None) -> bool:
    """After hippo edited the outbox copy at ``path``: its manifest row, its outbox hash,
    and an outbox index refresh. Reached from ``trust._fold_file``. ``before_sha`` set
    (a mechanical rewrite) records only when the bytes read were the recorded ones. True
    when the copy is recorded (recall will serve it)."""
    md = owner_of(path)
    if not md or not OB._is_memory_filename(os.path.basename(path)):
        return False
    try:
        if before_sha is not None:
            allowed = OB.outbox_hashes(md, repo_root)
            if allowed is not None and allowed.get(_stem(path)) != before_sha:
                return False  # the copy had already drifted: it stays withheld
        note = OB.record_write(md, path, repo_root)
        OB.refresh_index(md)
        return note is None
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Removals
# --------------------------------------------------------------------------- #
def _drop_manifest_row(memory_dir: str, fname: str) -> bool:
    """Remove ``fname``'s row from the outbox manifest (compare-and-swap)."""
    import json

    from .atomic import update_text_cas

    path = OB.manifest_path(memory_dir)
    if not os.path.isfile(path):
        return True

    def transform(text: str) -> Optional[str]:
        try:
            doc = json.loads(text) if text.strip() else {}
        except ValueError:
            return None
        files = doc.get("files") if isinstance(doc, dict) else None
        if not isinstance(files, dict) or fname not in files:
            return None
        files = {k: v for k, v in files.items() if k != fname}
        return json.dumps({"version": 1, "files": files}, indent=2, sort_keys=True) + "\n"

    try:
        update_text_cas(path, transform)
        return True
    except Exception:
        return False


def _drop_hash(memory_dir: str, stem: str, repo_root: Optional[str]) -> bool:
    try:
        from . import trust as T
        from .provenance_env import main_worktree_root

        gate_root = T.gate_repo_root(memory_dir, repo_root)
        if gate_root is None:
            return True
        keys = {T._corpus_key(gate_root)}
        main = main_worktree_root(gate_root)
        if main:
            keys.add(T._corpus_key(main))
        doc = T._load_registry_doc()
        trusted = doc.get("trusted")
        if not isinstance(trusted, dict):
            return True
        touched = False
        for key in keys:
            entry = trusted.get(key)
            hashes = entry.get(OB._ENTRY_KEY) if isinstance(entry, dict) else None
            if isinstance(hashes, dict) and stem in hashes:
                entry[OB._ENTRY_KEY] = {k: v for k, v in hashes.items() if k != stem}
                touched = True
        return bool(T._write_registry_doc(doc)) if touched else True
    except Exception:
        return False


def drop_copy(memory_dir: str, name: str, repo_root: Optional[str] = None) -> Optional[str]:
    """A removed memory's outbox copy goes too: the file, its manifest row and its hash
    record, so a later ship cannot resurrect it. None when nothing was there or all of it
    went; else one line naming what stayed."""
    fname = OB._fname(name)
    d = OB.present(memory_dir)
    copy = os.path.join(d, fname) if d else None
    if not copy or not os.path.isfile(copy):
        return None
    problems = []
    try:
        os.remove(copy)
    except OSError as exc:
        problems.append(f"the copy itself ({exc})")
    if not _drop_manifest_row(memory_dir, fname):
        problems.append("its manifest row")
    if not _drop_hash(memory_dir, fname[:-3], repo_root):
        problems.append("its trust record")
    OB.refresh_index(memory_dir)
    if not problems:
        return None
    return f"the outbox copy of {fname[:-3]} could not be fully removed: " + ", ".join(problems)


def removal_line(memory_dir: str, repo_root: Optional[str] = None) -> Optional[str]:
    """What a removal says in outbox mode: removals never travel through the outbox."""
    if OB.write_target(memory_dir, repo_root) != "outbox":
        return None
    return (
        "removals do not go through the outbox: this one is a working-tree change in the "
        "corpus dir, to commit yourself"
    )


def floor_dir_for_edit(memory_dir: str, repo_root: Optional[str] = None) -> Optional[str]:
    """Where a floor edit on an existing pointer lands: the outbox (its floor copy) when
    outbox mode is honoured or a floor copy already exists, else None (the corpus dir)."""
    d = OB.present(memory_dir)
    if d and os.path.isfile(os.path.join(d, OB.FLOOR_NAME)):
        return d
    if OB.write_target(memory_dir, repo_root) == "outbox":
        return OB.floor_target(memory_dir, repo_root)
    return None


def pack_refusal(memory_dir: str, name: str) -> Optional[str]:
    """Packs keep writing the corpus dir directly: an install adds a file AND a lockfile
    row there, so routing the memory into the outbox would split one install across the
    working tree and the outbox. What a pack must never do is write a committed file that
    an outbox copy shadows (recall would keep serving the copy, and the edit would vanish
    from view). The refusal line, or None."""
    d = OB.present(memory_dir)
    if d and os.path.isfile(os.path.join(d, OB._fname(name))):
        return (
            f"{OB._fname(name)} has an unshipped copy in the outbox; a pack writes the corpus "
            "dir directly, under it, so land that copy in the corpus dir or remove it first"
        )
    return None
