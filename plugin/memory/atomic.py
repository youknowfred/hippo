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

import hashlib
import json
import os
import tempfile
from typing import Callable, Optional, Tuple


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


def write_bytes_atomic(path: str, data: bytes) -> None:
    """``write_text_atomic`` for bytes — the byte-faithful copy path (INV-2).

    Exists for writers that must not round-trip an encoding (seeding a corpus file
    byte-identical to its packaged source). Same unique-tmp + ``os.replace`` + symlink
    + permission discipline; raises exactly like ``open(path, "wb")`` would.
    """
    real = os.path.realpath(path) if os.path.islink(path) else path
    d = os.path.dirname(os.path.abspath(real)) or "."
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(real) + ".tmp.", dir=d)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
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


def write_json_atomic(path: str, doc, *, indent: int = 2, sort_keys: bool = False) -> None:
    """``json.dump`` + trailing newline, through ``write_text_atomic``.

    Serializes BEFORE touching the filesystem — a ``doc`` that cannot serialize
    leaves the existing file untouched (the plain ``open("w") + json.dump`` idiom
    this replaces truncated the file first and raised after).
    """
    write_text_atomic(path, json.dumps(doc, indent=indent, sort_keys=sort_keys) + "\n")


# --------------------------------------------------------------------------- #
# RWY-3: compare-and-swap for corpus read-modify-writes.
#
# write_text_atomic makes every write whole, but two writers that each read, edit and
# replace the same document still lose one edit: the later replace carries the bytes it
# read, not the other writer's (a floor pointer, a verdict stamp, a typed edge). The CAS
# write takes the token of the bytes the caller read and swaps its document in only if the
# target still holds them. The check runs after the temp file is written, immediately
# before the rename, so the window is the rename itself. No locks, no coordinator.
# --------------------------------------------------------------------------- #
class CasConflict(OSError):
    """The target changed between the caller's read and its write."""


def _real(path: str) -> str:
    return os.path.realpath(path) if os.path.islink(path) else path


def content_token(path: str) -> Optional[str]:
    """sha256 of ``path``'s bytes, or ``None`` when it does not exist (the "absent" token)."""
    try:
        with open(_real(path), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except FileNotFoundError:
        return None


def read_text_cas(path: str, encoding: str = "utf-8") -> Tuple[str, str]:
    """``(text, token)`` for one read. Raises like ``open(path)`` (missing file included)."""
    with open(_real(path), "rb") as fh:
        data = fh.read()
    return data.decode(encoding), hashlib.sha256(data).hexdigest()


def write_text_cas(path: str, text: str, expected: Optional[str], encoding: str = "utf-8") -> None:
    """Write ``text`` only if ``path`` still holds the bytes whose token is ``expected``
    (``None``: only if it still does not exist). Raises ``CasConflict`` otherwise, leaving
    the target untouched. Same unique-tmp, permission and symlink discipline as
    ``write_text_atomic``."""
    real = _real(path)
    d = os.path.dirname(os.path.abspath(real)) or "."
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(real) + ".tmp.", dir=d)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(text)
        try:
            os.chmod(tmp, os.stat(real).st_mode & 0o777)
        except OSError:
            os.chmod(tmp, 0o644 & ~_umask())
        if content_token(real) != expected:
            raise CasConflict(f"{os.path.basename(real)} changed since it was read")
        os.replace(tmp, real)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def update_text_cas(
    path: str, transform: Callable[[str], Optional[str]], *, retries: int = 8, encoding: str = "utf-8"
) -> str:
    """Read ``path``, apply ``transform``, CAS-write the result; on a conflict re-read and
    re-apply (``transform`` must be a pure function of the text). ``transform`` returning
    ``None`` or the text unchanged writes nothing. Returns the final text. A missing file
    raises ``FileNotFoundError``; ``retries`` conflicts in a row raise ``CasConflict``."""
    for _ in range(max(1, retries)):
        text, token = read_text_cas(path, encoding)
        new = transform(text)
        if new is None or new == text:
            return text
        try:
            write_text_cas(path, new, token, encoding)
            return new
        except CasConflict:
            continue
    raise CasConflict(f"{os.path.basename(path)} kept changing; gave up after {retries} tries")
