"""Machine-local project registry (RCH-4) — which corpora ``--all-projects`` may search.

Cross-project recall needs to know where the OTHER corpora live. That knowledge is
machine-local state (the same class as the SEC-1 trust registry, and deliberately the
same shape of file): ``~/.claude/hippo-projects.json``, a sibling of
``hippo-trust.json``, holding ``{"projects": {<realpath repo_root>: {"memory_dir",
"registered_at"}}}``. It is OPT-IN — populated by ``/hippo:init`` (the same step that
marks the corpus trusted) and pruned by ``/hippo:remove`` — and it is a LIST, not a
grant: every registered corpus is still trust-gated per-source at query time
(``recall.recall_all_projects``), because registration says "this corpus exists", while
``hippo-trust.json`` alone says "this corpus may inject". Keeping the two files separate
keeps that distinction legible.

Mirrors ``trust.py``'s discipline exactly: realpath keys, ``HIPPO_PROJECTS_FILE`` env
override for hermetic tests, whole-document read-modify-write that preserves sibling
keys a future schema adds, and never-raise (a corrupt/missing file reads as "nothing
registered"). Read-time self-heal: an entry whose ``memory_dir`` no longer exists is
SKIPPED (a deleted/moved project silently drops out of search) — never auto-pruned from
the file, so a temporarily unmounted checkout comes back on its own.
"""

from __future__ import annotations

import json
import os
from typing import Dict, Optional

# Hermetic-test / relocation override for the registry file path.
_PROJECTS_FILE_ENV = "HIPPO_PROJECTS_FILE"


def projects_registry_path() -> str:
    """Absolute path to the machine-local project registry JSON.

    ``HIPPO_PROJECTS_FILE`` wins (hermetic tests point it at a tmp file); otherwise the
    canonical ``~/.claude/hippo-projects.json`` — outside any repo, like the trust file.
    """
    override = os.environ.get(_PROJECTS_FILE_ENV)
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".claude", "hippo-projects.json")


def _key(repo_root: str) -> str:
    return os.path.realpath(repo_root)


def _load_doc() -> dict:
    path = projects_registry_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def registered_projects() -> Dict[str, dict]:
    """``{repo_root: {"memory_dir", "registered_at"}}`` for every LIVE registration.

    Read-time self-heal: entries whose ``memory_dir`` is not a directory are skipped
    (never auto-pruned). ``{}`` on a missing/corrupt file. Never raises.
    """
    try:
        projects = _load_doc().get("projects")
        if not isinstance(projects, dict):
            return {}
        live: Dict[str, dict] = {}
        for root, entry in projects.items():
            if not isinstance(entry, dict):
                continue
            mdir = entry.get("memory_dir")
            if isinstance(mdir, str) and os.path.isdir(mdir):
                live[root] = entry
        return live
    except Exception:
        return {}


def register_project(repo_root: str, memory_dir: str) -> bool:
    """Record a project corpus in the registry. Idempotent (re-init refreshes the entry).

    Same read-modify-write discipline as ``trust.mark_trusted``: the whole document is
    re-read so sibling keys never drop; a corrupt file degrades to a fresh document.
    True on success (or no-op), False when the write failed — the caller reports it.
    """
    try:
        path = projects_registry_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        doc = _load_doc()
        projects = doc.get("projects")
        if not isinstance(projects, dict):
            projects = {}
        from datetime import datetime, timezone

        projects[_key(repo_root)] = {
            "memory_dir": os.path.abspath(memory_dir),
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        doc["projects"] = projects
        from .atomic import write_json_atomic

        write_json_atomic(path, doc, sort_keys=True)  # SEC-19: same discipline as trust
        return True
    except Exception:
        return False


def deregister_project(repo_root: str) -> bool:
    """Drop a project from the registry (``/hippo:remove``'s offboarding step).

    True when the entry was removed OR was never there (idempotent); False only when a
    present entry could not be removed (write failure). Never raises.
    """
    try:
        path = projects_registry_path()
        doc = _load_doc()
        projects = doc.get("projects")
        if not isinstance(projects, dict) or _key(repo_root) not in projects:
            return True
        del projects[_key(repo_root)]
        doc["projects"] = projects
        from .atomic import write_json_atomic

        write_json_atomic(path, doc, sort_keys=True)  # SEC-19
        return True
    except Exception:
        return False
