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


# --------------------------------------------------------------------------- #
# RCH-11: registry hygiene. Read-time skip (above) keeps dead entries out of
# recall but the FILE accumulates them forever — scratch/test sessions that ran a
# real init leave rows whose tmp-dir corpora no longer exist. The verbs here are
# deliberate and human-invoked, never automatic: the read path's never-auto-prune
# promise (an unmounted checkout comes back on its own) stays intact.
# --------------------------------------------------------------------------- #

# Path prefixes that do not survive tmp cleanup/reboot. A dead entry under one of
# these can never be a temporarily-unmounted checkout — that is the entire honesty
# argument for prune_dead's batch class; everything else stays per-item (--drop).
_VOLATILE_ROOT_CANDIDATES = (
    "/tmp",
    "/var/tmp",
    "/var/folders",
    "/private/tmp",
    "/private/var/tmp",
    "/private/var/folders",
)


def _under_volatile_root(path: str) -> bool:
    """True when ``path`` (realpath'd) sits under a system temp root."""
    import tempfile

    real = os.path.realpath(path)
    roots = {os.path.realpath(tempfile.gettempdir())}
    for cand in _VOLATILE_ROOT_CANDIDATES:
        roots.add(os.path.realpath(cand))
    return any(real == root or real.startswith(root + os.sep) for root in roots)


def registry_census() -> dict:
    """Read-only classification of every registry entry, malformed rows included.

    Per entry: ``live`` mirrors ``registered_projects()``'s read-time criterion
    (``memory_dir`` is a directory); ``volatile`` = memory_dir under a system temp
    root; ``repairable`` = dead, but a live corpus exists at the canonical
    ``<root>/.claude/memory`` (running /hippo:init in that root re-registers it).
    A malformed row (non-dict, or no memory_dir string) reports live=False,
    volatile=False so only the explicit per-item drop can remove it. Missing or
    corrupt files degrade to an empty census. Never raises; never writes.
    """
    path = projects_registry_path()
    empty = {"path": path, "entries": [], "live": 0, "dead": 0}
    try:
        projects = _load_doc().get("projects")
        if not isinstance(projects, dict):
            return empty
        entries = []
        for root in sorted(projects):
            entry = projects[root] if isinstance(projects[root], dict) else {}
            mdir = entry.get("memory_dir")
            mdir = mdir if isinstance(mdir, str) else ""
            live = bool(mdir) and os.path.isdir(mdir)
            entries.append(
                {
                    "root": root,
                    "memory_dir": mdir,
                    "registered_at": entry.get("registered_at"),
                    "live": live,
                    "volatile": _under_volatile_root(mdir) if mdir else False,
                    "repairable": (not live)
                    and os.path.isdir(os.path.join(root, ".claude", "memory")),
                }
            )
        return {
            "path": path,
            "entries": entries,
            "live": sum(1 for e in entries if e["live"]),
            "dead": sum(1 for e in entries if not e["live"]),
        }
    except Exception:
        return empty


def prune_dead() -> dict:
    """Remove every dead entry whose ``memory_dir`` is temp-rooted; keep the rest.

    The batch is restricted to the one mechanically-safe class (see the module
    comment); every other dead entry is returned in ``kept_dead`` for the per-item
    drop. Nothing to remove -> the file is not rewritten. Whole-document
    read-modify-write preserving sibling keys, atomic replace. ``ok`` False = the
    rewrite failed loudly and the prior document is intact (crash class: detected).
    Never raises.
    """
    try:
        census = registry_census()
        removed = [e for e in census["entries"] if not e["live"] and e["volatile"]]
        kept_dead = [e for e in census["entries"] if not e["live"] and not e["volatile"]]
        if not removed:
            return {"ok": True, "removed": [], "kept_dead": kept_dead}
        path = projects_registry_path()
        doc = _load_doc()
        projects = doc.get("projects")
        if not isinstance(projects, dict):
            return {"ok": True, "removed": [], "kept_dead": kept_dead}
        gone = {e["root"] for e in removed}
        doc["projects"] = {r: e for r, e in projects.items() if r not in gone}
        from .atomic import write_json_atomic

        write_json_atomic(path, doc, sort_keys=True)  # SEC-19: same discipline as register
        return {"ok": True, "removed": removed, "kept_dead": kept_dead}
    except Exception:
        return {"ok": False, "removed": [], "kept_dead": []}


def _n(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _repair_note(entry: dict) -> str:
    if not entry["repairable"]:
        return ""
    canonical = os.path.join(entry["root"], ".claude", "memory")
    return f" (a live corpus exists at {canonical} — re-run /hippo:init there to re-register)"


def _render_report(census: dict) -> str:
    entries = census["entries"]
    if not entries:
        return (
            f"projects registry: nothing registered ({census['path']}) — "
            "populated by /hippo:init."
        )
    lines = [
        f"projects registry: {census['path']} "
        f"({_n(len(entries), 'entry', 'entries')}: {census['live']} live, {census['dead']} dead)"
    ]
    prunable = 0
    for e in entries:
        if e["live"]:
            note = " [temp-rooted — will not survive tmp cleanup]" if e["volatile"] else ""
            lines.append(f"  live{note}: {e['root']}")
        elif e["volatile"]:
            prunable += 1
            lines.append(
                f"  dead [temp-rooted — prunable]: {e['root']} -> {e['memory_dir']}"
                + _repair_note(e)
            )
        else:
            lines.append(
                f"  dead [kept — possibly an unmounted checkout]: {e['root']} -> "
                f"{e['memory_dir']}" + _repair_note(e)
            )
    if prunable:
        lines.append(
            f"prune the {_n(prunable, 'temp-rooted dead entry', 'temp-rooted dead entries')}: "
            "python -m memory.registry --prune-dead"
        )
    lines.append("drop any single entry: python -m memory.registry --drop <root>")
    return "\n".join(lines)


def main(argv=None) -> int:
    """Hygiene CLI: report (default, read-only) / ``--prune-dead`` / ``--drop <root>``."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m memory.registry",
        description=(
            "Projects-registry hygiene (RCH-11): report every entry's live/dead state, "
            "prune the temp-rooted dead ones, or drop one named entry."
        ),
    )
    parser.add_argument(
        "--prune-dead",
        action="store_true",
        help="remove dead entries under system temp roots (each printed); all other "
        "dead entries are kept and named",
    )
    parser.add_argument(
        "--drop",
        metavar="ROOT",
        help="deregister exactly this project root (the per-item form for entries "
        "that need human judgment)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the census as JSON (report mode only)"
    )
    args = parser.parse_args(argv)

    if args.json and (args.prune_dead or args.drop):
        parser.error("--json shapes the report only; it cannot ride a mutation")

    if args.drop:
        key = _key(args.drop)
        projects = _load_doc().get("projects")
        if not isinstance(projects, dict) or key not in projects:
            print(f"{key} was not registered — nothing to drop.")
            return 0
        if not deregister_project(args.drop):
            print(f"drop FAILED — registry unchanged; {key} is still registered.")
            return 1
        print(f"dropped: {key}")
        return 0

    if args.prune_dead:
        result = prune_dead()
        if not result["ok"]:
            print("prune FAILED — registry unchanged.")
            return 1
        for e in result["removed"]:
            print(f"removed: {e['root']} -> {e['memory_dir']}" + _repair_note(e))
        for e in result["kept_dead"]:
            print(
                f"kept (not temp-rooted — possibly an unmounted checkout): {e['root']} — "
                f"drop deliberately: python -m memory.registry --drop {e['root']}"
            )
        if not result["removed"]:
            print("nothing to prune — no dead entries under system temp roots.")
        return 0

    census = registry_census()
    print(json.dumps(census, indent=2) if args.json else _render_report(census))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
