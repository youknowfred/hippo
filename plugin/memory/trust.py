"""Machine-local trust registry for memory corpora (SEC-1 — foreign-corpus gate).

The recall hook auto-executes in whatever project it lands in: clone any repo that
carries a ``.claude/memory/`` corpus and — absent this gate — its memories inject into
your context on EVERY prompt, an unreviewed prompt-injection channel that needs zero user
action. This module is the gate. Recall (and the SessionStart producers) inject ONLY from
corpora the current user has explicitly marked trusted; a freshly-cloned foreign corpus
injects nothing until the user reviews it (via ``/hippo:doctor``) and consents.

Design constraints this module is shaped by (all load-bearing):
  - The registry lives OUTSIDE any project's own git tree (``~/.claude/hippo-trust.json``),
    so a foreign repo can't ship a "trust me" marker committed into itself. The key is the
    corpus's REAL absolute ``repo_root`` (``os.path.realpath``) — the same folder-trust
    shape the harness itself uses. One canonical file, one canonical key.
  - The GATE CHECK (``is_trusted``) is a cheap file-exists + small-JSON-read — NO git, NO
    network, NO LLM — so the UserPromptSubmit hot path can call it synchronously without
    violating the pure-retrieval invariant.
  - The one-time CONSENT step cannot live here or in any hook (hooks are non-interactive,
    exit-0, and must never block a prompt). Consent is agent-driven: ``/hippo:doctor`` shows
    the memory COUNT + a SAMPLE of names (never bodies) and, on the user's yes, calls
    ``mark_trusted``. ``/hippo:init`` marks a corpus trusted the moment the user creates it
    (or explicitly re-runs init against it) — running a hippo command against a corpus IS
    the review.
  - CI override: ``MEMOBOT_TRUST_ALL=1`` bypasses the gate entirely (matches the codebase's
    ``MEMOBOT_`` env convention). ``MEMOBOT_TRUST_FILE`` relocates the registry (hermetic
    tests point it at a tmp path so the real ``~/.claude`` is never touched).

Fail posture: this is a SECURITY gate, so it fails CLOSED — an unresolvable ``repo_root``
that IS a real corpus, or an unreadable/corrupt registry, denies rather than injects. The
one exception is a corpus that is not inside a git repo at all: the clone-injection attack
is a git-clone, a non-git corpus is one the user created locally by hand, and gating it
would break every hermetic (non-git tmp) recall call — so ``gate_repo_root`` returns None
there and the caller treats "no resolvable git root" as "gate inapplicable, proceed".
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

from .provenance import git_root

# CI/automation bypass — set to any non-empty value to skip the gate entirely.
_TRUST_ALL_ENV = "MEMOBOT_TRUST_ALL"
# Hermetic-test / relocation override for the registry file path.
_TRUST_FILE_ENV = "MEMOBOT_TRUST_FILE"


def trust_all() -> bool:
    """True when the CI/automation override (``MEMOBOT_TRUST_ALL``) is set non-empty."""
    return bool(os.environ.get(_TRUST_ALL_ENV))


def trust_registry_path() -> str:
    """Absolute path to the machine-local trust registry JSON.

    ``MEMOBOT_TRUST_FILE`` wins (hermetic tests point it at a tmp file); otherwise the
    canonical ``~/.claude/hippo-trust.json`` — deliberately OUTSIDE any project repo so a
    foreign corpus can never commit its own trust marker.
    """
    override = os.environ.get(_TRUST_FILE_ENV)
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".claude", "hippo-trust.json")


def _corpus_key(repo_root: str) -> str:
    """Canonical registry key for a corpus: its real (symlink-resolved) absolute repo root."""
    return os.path.realpath(repo_root)


def _load_registry() -> dict:
    """Read the registry into a dict of ``{repo_root: metadata}``; ``{}`` on any problem.

    Never raises — a missing file (the common case: nobody has trusted anything yet) or an
    unreadable/corrupt one both yield ``{}``, which in a fail-closed gate means "nothing is
    trusted". Only the ``"trusted"`` sub-map is returned so the on-disk schema can grow.
    """
    path = trust_registry_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {}
        trusted = data.get("trusted")
        return trusted if isinstance(trusted, dict) else {}
    except Exception:
        return {}


def is_trusted(repo_root: Optional[str]) -> bool:
    """True when ``repo_root``'s corpus is trusted — or when the CI override is set.

    Cheap by contract (a stat + a small JSON read): the UserPromptSubmit hot path calls this
    synchronously. A falsy ``repo_root`` is "not trusted" (fail closed). Never raises.
    """
    if trust_all():
        return True
    if not repo_root:
        return False
    return _corpus_key(repo_root) in _load_registry()


def mark_trusted(repo_root: str) -> bool:
    """Record ``repo_root`` as trusted in the machine-local registry. Idempotent.

    Called by ``/hippo:init`` when a corpus is created (or init is re-run against it) and by
    ``/hippo:doctor`` after the user reviews the count+sample and consents. Creates the
    ``~/.claude`` dir + registry file if absent, preserving any existing entries. Returns
    True on a successful write (or an already-present no-op), False if the write failed —
    the caller surfaces a failure rather than pretending the corpus is now trusted.
    """
    try:
        key = _corpus_key(repo_root)
        path = trust_registry_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # Re-read the WHOLE file (not just the trusted sub-map) so we never drop sibling keys
        # a future schema adds; degrade a corrupt/non-dict file to a fresh document.
        doc: dict = {}
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    doc = loaded
            except Exception:
                doc = {}
        trusted = doc.get("trusted")
        if not isinstance(trusted, dict):
            trusted = {}
        from datetime import datetime, timezone

        trusted[key] = {"trusted_at": datetime.now(timezone.utc).isoformat()}
        doc["trusted"] = trusted
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
        return True
    except Exception:
        return False


def gate_repo_root(memory_dir: Optional[str], repo_root: Optional[str] = None) -> Optional[str]:
    """Resolve the git ``repo_root`` the trust gate keys on, or None if the gate is inapplicable.

    ALWAYS resolves through ``git_root`` — never trusts a passed-in path blind. ``resolve_dirs``
    returns ``git_root(start) or start``, so a caller's ``repo_root`` can be a NON-git fallback
    dir; keying the gate on that would wrongly deny an ordinary non-git project. So this asks
    git for the toplevel of the best available start dir (the supplied ``repo_root`` if any,
    else ``memory_dir``) and returns None when there is none. That None is deliberate and
    load-bearing: the clone-injection attack is a git clone, a corpus with no git root is one
    the user created locally by hand, and gating it would break every hermetic (non-git tmp)
    recall path — so the caller treats None as "gate inapplicable, proceed". Never raises.
    """
    try:
        start = repo_root or memory_dir
        if not start:
            return None
        return git_root(start)
    except Exception:
        return None


def corpus_sample(memory_dir: str, limit: int = 8) -> List[str]:
    """Up to ``limit`` memory NAMES (not bodies) from ``memory_dir``, for the consent prompt.

    The trust prompt must show WHAT would be injected before the user consents — the count
    plus a representative sample of names. Names only: the whole point of the gate is that an
    untrusted corpus's CONTENT never reaches the context, so the review shows filenames, not
    the bodies a malicious corpus might weaponize. Never raises; [] on any problem.
    """
    try:
        from .provenance import _iter_memory_files

        names = [
            os.path.splitext(os.path.basename(p))[0]
            for p in _iter_memory_files(memory_dir)
        ]
        return names[:limit]
    except Exception:
        return []


def corpus_count(memory_dir: str) -> int:
    """Total count of memory files in ``memory_dir`` (excludes MEMORY.md floor). 0 on failure."""
    try:
        from .provenance import _iter_memory_files

        return sum(1 for _ in _iter_memory_files(memory_dir))
    except Exception:
        return 0
