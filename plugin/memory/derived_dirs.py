"""Where hippo's derived per-clone dirs live: ``<project>/.hippo/`` (MIG-9).

The recall index, the telemetry ledgers and the capture pending queue used to sit beside the
corpus, in ``.claude/.memory-index``, ``.claude/.memory-telemetry`` and
``.claude/.memory-pending``. The Claude desktop app lays down every worktree it creates with a
copy of the checkout's whole ``.claude/`` directory, ignored files included, so each worktree
carried all three. SHP-7 already resolves a worktree session to the main tree's dirs, so the
copies were never read: on 2026-10-20 em-growth-labs had 50 desktop worktrees carrying 1.66 GB
of them (15 MB index, 13 MB telemetry, 8 MB pending queue apiece).

MIG-9. The dirs now live in ``<project>/.hippo/index``, ``.hippo/telemetry`` and
``.hippo/pending``: beside ``.claude/``, not inside it, on a path derived from the corpus path
alone (the same rule as MIG-6's ``.hippo-snapshots/``, for the same reasons: every surface,
the bash fast paths included, computes it without the plugin data dir, and it lives and dies
with the clone, so the pending queue survives a plugin uninstall). The ``.hippo/`` container
self-ignores (SEC-3). A corpus that is not ``<project>/.claude/memory`` (a ``HIPPO_MEMORY_DIR``
elsewhere, the user tier's ``~/.claude/hippo-memory``) keeps the old sibling dirs: there is no
``.claude/`` for them to ride in. An old dir is read where it is, so an update loses neither
the index nor the queue.

Imports only ``provenance_env`` (CONTRIBUTING.md "Code layout"). Nothing here raises.
"""

from __future__ import annotations

import os
from typing import Optional

from .provenance_env import DERIVED_HOME_DIRNAME

KINDS = ("index", "telemetry", "pending")
LEGACY_DIRNAMES = {
    "index": ".memory-index",
    "telemetry": ".memory-telemetry",
    "pending": ".memory-pending",
}
def derived_home(memory_dir: str) -> Optional[str]:
    """``<project>/.hippo`` when the corpus is ``<project>/.claude/memory``; else None."""
    md = os.path.abspath(memory_dir)
    parent = os.path.dirname(md)
    if os.path.basename(md) != "memory" or os.path.basename(parent) != ".claude":
        return None
    return os.path.join(os.path.dirname(parent), DERIVED_HOME_DIRNAME)


def legacy_dir(memory_dir: str, kind: str) -> str:
    """The pre-MIG-9 location: ``.claude/.memory-<kind>``, beside the corpus."""
    return os.path.join(os.path.dirname(os.path.abspath(memory_dir)), LEGACY_DIRNAMES[kind])


def derived_dir(memory_dir: str, kind: str) -> str:
    """Where ``kind`` lives for this corpus. Callers check their own env override first.

    ``<project>/.hippo/<kind>``, unless only the old ``.claude/.memory-<kind>`` exists: that
    one is read where the data is. At most two stats; this is on the recall hot path.
    """
    legacy = legacy_dir(memory_dir, kind)
    home = derived_home(memory_dir)
    if home is None:
        return legacy
    new = os.path.join(home, kind)
    if os.path.isdir(new) or not os.path.isdir(legacy):
        return new
    return legacy
