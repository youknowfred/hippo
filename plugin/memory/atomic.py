"""The one atomic-write primitive for shared mutable files (SEC-19 / COR-17 / COR-18).

Two failure classes this closes, both mapped in the 2026-07-16 QA sweep:

  - a plain truncating ``open(path, "w")`` torn by a crash — or READ mid-write by a
    concurrent process — leaves partial bytes where a whole document used to be. For
    the machine-wide trust registry that meant every consent baseline on the machine
    lost at once (and a concurrent recall's torn read meant deny-all or a silently
    disabled drift quarantine for that prompt); for the packs lockfile, every
    installed pack's three-way merge base; for an in-place corpus ``.md`` rewrite, a
    truncated source-of-truth memory.
  - a FIXED ``path + ".tmp"`` sibling name collides when two processes write the same
    target concurrently (two sessions' SessionStart hooks, a hook racing the MCP
    server): one writer's ``os.replace`` can promote the other's half-written bytes,
    and a ``finally: os.remove(tmp)`` can delete the other writer's live tmp.

``write_text_atomic`` writes to a per-call-unique tmp in the target's own directory
(same filesystem, so the ``os.replace`` is atomic) and swaps it in: every reader sees
the old document or the new one, never a torn one, and concurrent writers degrade to
last-writer-wins over WHOLE documents — the semantics every caller here already
assumed it had.

Symlink caveat (COR-18): ``os.replace`` would replace a symlink ITSELF with a regular
file, silently detaching layouts like a dotfiles-managed user tier where individual
files are links. When the target is a symlink we resolve it first and swap the real
file behind it, so the link survives and the write is still atomic at the target.
"""

from __future__ import annotations

import json
import os
import tempfile


def write_text_atomic(path: str, text: str, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` atomically (unique tmp + ``os.replace``).

    Preserves an existing target's permission bits (a ``mkstemp`` file is 0600,
    which must not tighten a shared corpus file). Raises on failure exactly like
    ``open(path, "w")`` would — callers keep their existing error handling.
    """
    real = os.path.realpath(path) if os.path.islink(path) else path
    d = os.path.dirname(os.path.abspath(real)) or "."
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(real) + ".tmp.", dir=d)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(text)
        try:
            os.chmod(tmp, os.stat(real).st_mode & 0o777)
        except OSError:
            pass  # new file: mkstemp's private mode is upgraded below instead
        else:
            os.replace(tmp, real)
            return
        os.chmod(tmp, 0o644 & ~_umask())
        os.replace(tmp, real)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


def write_json_atomic(path: str, doc, *, indent: int = 2, sort_keys: bool = False) -> None:
    """``json.dump`` + trailing newline, through ``write_text_atomic``.

    Serializes BEFORE touching the filesystem — a ``doc`` that cannot serialize
    leaves the existing file untouched (the plain ``open("w") + json.dump`` idiom
    this replaces truncated the file first and raised after).
    """
    write_text_atomic(path, json.dumps(doc, indent=indent, sort_keys=sort_keys) + "\n")
