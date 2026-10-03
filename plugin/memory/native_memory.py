"""NAT-1: read-only detection of Claude Code's native auto memory beside a hippo corpus.

Native auto memory is on by default. It loads ``MEMORY.md`` from its memory directory at
session start and writes topic files into that directory, which ``/hippo:init`` symlinks
onto the reviewed corpus. So its settings decide whether hippo's floor loads at all, and
its writes land in the corpus unreviewed. hippo read none of it until now.

``native_memory_state`` reports, without writing anything:
  - whether auto memory is enabled, and what decided it: ``CLAUDE_CODE_DISABLE_AUTO_MEMORY``
    or ``autoMemoryEnabled`` in the local, project or user settings (most specific wins);
  - ``autoMemoryDirectory`` when any of those settings redirect it, and whether the target
    is this corpus;
  - corpus files carrying native auto memory's frontmatter stamp (``modified`` /
    ``originSessionId``), and how many of those carry no hippo provenance
    (``source_commit``) — the files native wrote that hippo never adopted;
  - corpus files git does not track (written, never committed).

The doctor line reads it; CLM-1's integrity lane will too. What the settings DO to the
floor is a platform fact recorded with dated receipts in ``PLATFORM.md`` (PLT-1).
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Dict, List, Optional

from .fm_access import fm_get

_SETTINGS_ENV = "HIPPO_CLAUDE_SETTINGS"  # hermetic tests point the user settings file here
_DISABLE_ENV = "CLAUDE_CODE_DISABLE_AUTO_MEMORY"
_NATIVE_STAMP_KEYS = ("modified", "originSessionId")


def user_settings_path() -> str:
    """Claude Code's user settings file: ``HIPPO_CLAUDE_SETTINGS``, else
    ``$CLAUDE_CONFIG_DIR/settings.json``, else ``~/.claude/settings.json``."""
    override = os.environ.get(_SETTINGS_ENV)
    if override:
        return override
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "settings.json")


def _read_settings(path: str) -> Dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _settings_layers(repo_root: Optional[str]) -> List[tuple]:
    """``[(label, settings)]`` most specific first: local, project, user."""
    layers = []
    if repo_root:
        layers.append(("local settings", _read_settings(os.path.join(repo_root, ".claude", "settings.local.json"))))
        layers.append(("project settings", _read_settings(os.path.join(repo_root, ".claude", "settings.json"))))
    layers.append(("user settings", _read_settings(user_settings_path())))
    return layers


def _is_native_stamped(text: str) -> tuple:
    """``(stamped, has_hippo_provenance)`` for one memory file's frontmatter."""
    from .provenance import parse_frontmatter

    fm = parse_frontmatter(text) or {}
    stamped = any(fm_get(fm, key) is not None for key in _NATIVE_STAMP_KEYS)
    return stamped, fm_get(fm, "source_commit") is not None


def _untracked(repo_root: str, memory_dir: str) -> List[str]:
    try:
        rel = os.path.relpath(memory_dir, repo_root)
        out = subprocess.run(
            ["git", "-C", repo_root, "ls-files", "--others", "--exclude-standard", "--", rel],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return []
        return sorted(
            os.path.splitext(os.path.basename(p))[0]
            for p in out.stdout.splitlines()
            if p.endswith(".md") and os.path.basename(p) not in ("MEMORY.md", "CONVENTIONS.md")
        )
    except Exception:
        return []


def native_memory_state(memory_dir: str, repo_root: Optional[str]) -> Dict:
    """The native auto-memory picture for this corpus. Read-only; never raises."""
    state: Dict = {
        "enabled": True,
        "decided_by": "default",
        "directory": None,
        "directory_source": None,
        "directory_is_corpus": None,
        "stamped": 0,
        "stamped_without_provenance": [],
        "untracked": [],
    }
    try:
        layers = _settings_layers(repo_root)
        env = (os.environ.get(_DISABLE_ENV) or "").strip().lower()
        if env and env not in ("0", "false", "no"):
            state.update(enabled=False, decided_by=f"{_DISABLE_ENV}={os.environ.get(_DISABLE_ENV)}")
        else:
            for label, doc in layers:
                if isinstance(doc.get("autoMemoryEnabled"), bool):
                    state.update(enabled=doc["autoMemoryEnabled"], decided_by=f"autoMemoryEnabled in {label}")
                    break
        for label, doc in layers:
            d = doc.get("autoMemoryDirectory")
            if isinstance(d, str) and d.strip():
                target = os.path.expanduser(d.strip())
                if not os.path.isabs(target) and repo_root:
                    target = os.path.join(repo_root, target)
                state.update(directory=target, directory_source=label)
                state["directory_is_corpus"] = os.path.realpath(target) == os.path.realpath(memory_dir)
                break
        from .provenance import _iter_memory_files

        for path in _iter_memory_files(memory_dir):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    stamped, provenanced = _is_native_stamped(fh.read())
            except Exception:
                continue
            if stamped:
                state["stamped"] += 1
                if not provenanced:
                    state["stamped_without_provenance"].append(os.path.splitext(os.path.basename(path))[0])
        state["stamped_without_provenance"].sort()
        if repo_root:
            state["untracked"] = _untracked(repo_root, memory_dir)
    except Exception:
        pass
    return state
