"""Machine-wide state census (HYG-1) — read-only lifecycle report for the machine-state
classes hippo itself creates.

RCH-11 censused ONE class (the projects registry). The same rot pattern covers three
more classes with a real existence oracle, none of which had a census: the
``~/.claude/projects/<encoded>/memory`` symlink farm (the flagship — every dangling
entry on the reference machine was test-leaked), the trust registry (the consent
ledger), and installed scheduler artifacts (the SLP-2 recipes users installed by hand).
This module composes all four into one deterministic report, mirroring
``registry.main``'s shape exactly: report default, ``--json``, empty-norm one-liner.

Census discipline (the HYG workstream goal):
  - DELEGATE, never fork (inv5): class (a) is ``registry.registry_census`` — no second
    projects walk exists here. The plugin-install class is likewise delegated wholesale
    to ``bootstrap.status``/``_sibling_installs`` and has no class of its own.
  - Read-only: zero writes, zero LLM, zero network. The scheduler class uses FILE
    ORACLES only (LaunchAgents glob + ``crontab -l`` + plistlib parse of the embedded
    paths) — launchctl runtime state is deliberately out of scope (ED-3), as are
    Claude scheduled-task recipes (no local oracle).
  - ``~/.claude/projects`` is HARNESS-owned: the census enumerates freely, but the only
    remedy that may ever act there is the HYG-2 remover, and only on the ``memory``
    symlink hippo itself creates.
  - Trust rows render REPORT-ONLY pending owner decision Q2 (ED4R-1): ``untrust`` is
    named as the existing per-row route, never prescribed — it deletes the row
    INCLUDING its SEC-6 fingerprint baseline, converting an unmounted-volume row from
    "comes back trusted" into "full re-consent".

Step-zero companion (HYG-2): the dangling-symlink class is self-inflicted — tests that
ran the real init with no HOME/``claude_projects_dir`` isolation minted every dangling
entry observed on the reference machine. The conftest ``HIPPO_CLAUDE_PROJECTS_DIR``
guard ships WITH the remover; until then the census labels the pytest-minted share so
the report reads as "our own suite", not ambient rot.
"""

from __future__ import annotations

import glob
import json
import os
import re
from typing import Dict, List, Optional

# init_project.py already imports this cross-module (the shipped precedent); the census
# reuses the ONE volatile-root classifier rather than duplicating it (inv5).
from .registry import _under_volatile_root, registry_census

# Hermetic-test / relocation override for the harness-owned symlink base. Resolved HERE
# (not in provenance.py, which is size-pinned) and honored by init_project's symlink
# step — the single write path — so no test can ever mint a real ~/.claude/projects
# symlink again (the HYG-2 leak fix).
_CLAUDE_PROJECTS_ENV = "HIPPO_CLAUDE_PROJECTS_DIR"

# The SLP-2 launchd label family (sleep._print_schedule's formula).
_LAUNCH_AGENT_GLOB = "com.hippo.sleep.*.plist"


def claude_projects_root() -> str:
    """Absolute path to the harness-owned ``~/.claude/projects`` dir.

    ``HIPPO_CLAUDE_PROJECTS_DIR`` wins (hermetic tests point it at a tmp dir; the
    conftest guard sets it suite-wide); otherwise the real harness location.
    """
    override = os.environ.get(_CLAUDE_PROJECTS_ENV)
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".claude", "projects")


# --------------------------------------------------------------------------- #
# Class (b): the memory symlink farm
# --------------------------------------------------------------------------- #
def symlink_farm_census(claude_projects_dir: Optional[str] = None) -> dict:
    """Classify every ``<projects>/<encoded>/memory`` symlink: ok / dangling /
    dangling-temp-rooted.

    Symlinks ONLY — a real ``memory`` dir or file is a native-memory shape
    (``check_native_coexistence``'s class) and is not hippo's to report here, let alone
    touch. A dangling target under a system temp root can never be an unmounted volume
    (the ``registry.prune_dead`` honesty argument), which is what makes that class —
    and only that class — mechanically safe for HYG-2's batch remover. ``pytest_leaked``
    counts dangling targets under a pytest tmp tree (``pytest-of-``): the self-inflicted
    share the module docstring names. Never raises; never writes.
    """
    root = claude_projects_dir or claude_projects_root()
    entries: List[dict] = []
    try:
        names = sorted(os.listdir(root))
    except Exception:
        names = []
    for name in names:
        link = os.path.join(root, name, "memory")
        try:
            if not os.path.islink(link):
                continue
            target = os.readlink(link)
            if not os.path.isabs(target):
                target = os.path.normpath(os.path.join(os.path.dirname(link), target))
            if os.path.isdir(os.path.realpath(link)):
                status = "ok"
            elif _under_volatile_root(target):
                status = "dangling-temp-rooted"
            else:
                status = "dangling"
            entries.append({"link": link, "target": target, "status": status})
        except Exception:
            continue
    return {
        "path": root,
        "entries": entries,
        "ok": sum(1 for e in entries if e["status"] == "ok"),
        "dangling": sum(1 for e in entries if e["status"] == "dangling"),
        "dangling_temp_rooted": sum(
            1 for e in entries if e["status"] == "dangling-temp-rooted"
        ),
        "pytest_leaked": sum(
            1 for e in entries if e["status"] != "ok" and "pytest-of-" in e["target"]
        ),
    }


# --------------------------------------------------------------------------- #
# Class (c): trust rows (the consent ledger) — REPORT-ONLY pending Q2
# --------------------------------------------------------------------------- #
def trust_census() -> dict:
    """Live/dead/temp-rooted legibility over the trust registry's rows.

    Honesty bound (carried from the vetting): ``is_trusted`` fails CLOSED and a dead
    trust row cannot inject — trust junk is inert-but-illegible. The census's value here
    is legibility plus the unmounted-volume/re-consent distinction, NOT incident
    prevention. Never raises; never writes; never prescribes ``untrust`` (Q2 pending).
    """
    # Same private-import posture as _under_volatile_root: reuse trust.py's canonical
    # reader (realpath keys, never-raise) rather than parsing the file a second way.
    from .trust import _load_registry, trust_registry_path

    path = trust_registry_path()
    rows = _load_registry()
    entries: List[dict] = []
    for root in sorted(rows):
        entry = rows[root] if isinstance(rows[root], dict) else {}
        entries.append(
            {
                "root": root,
                "live": os.path.isdir(root),
                "volatile": _under_volatile_root(root),
                "origin": entry.get("origin"),
                "fingerprinted": isinstance(entry.get("fingerprint"), dict),
            }
        )
    return {
        "path": path,
        "entries": entries,
        "live": sum(1 for e in entries if e["live"]),
        "dead": sum(1 for e in entries if not e["live"]),
    }


# --------------------------------------------------------------------------- #
# Class (d): scheduler artifacts — file oracles only (ED-3, verified 2026-07-17)
# --------------------------------------------------------------------------- #
def _parse_schedule_command(cmd: str) -> dict:
    """Extract the embedded paths from a SLP-2 recipe command line.

    The shape is ``sleep._print_schedule``'s: ``cd <repo> && PYTHONPATH=<plugin>
    <python> -m memory.sleep >> <log> 2>&1``. Missing pieces parse to None.
    """
    repo = re.search(r"\bcd\s+(\S+)\s+&&", cmd)
    plugin = re.search(r"\bPYTHONPATH=(\S+)", cmd)
    python = re.search(r"\bPYTHONPATH=\S+\s+(\S+)\s+-m\s+memory\.sleep\b", cmd)
    return {
        "repo_root": repo.group(1) if repo else None,
        "plugin_root": plugin.group(1) if plugin else None,
        "python": python.group(1) if python else None,
    }


def _classify_schedule(cmd: str) -> dict:
    parsed = _parse_schedule_command(cmd)
    if not any(parsed.values()):
        return {"status": "unparseable", "missing": []}
    missing = []
    for key in ("repo_root", "plugin_root"):
        p = parsed[key]
        if p and not os.path.isdir(p):
            missing.append(p)
    py = parsed["python"]
    if py and "/" in py and not os.path.exists(py):
        missing.append(py)
    return {"status": "stale-path" if missing else "ok", "missing": missing, **parsed}


def scheduler_census(
    launch_agents_dir: Optional[str] = None, crontab_text: Optional[str] = None
) -> dict:
    """Census installed SLP-2 artifacts by FILE ORACLE only.

    launchd: glob ``~/Library/LaunchAgents/com.hippo.sleep.*.plist`` + plistlib parse of
    the embedded command; cron: ``crontab -l`` lines invoking ``-m memory.sleep``.
    ``stale-path`` = an embedded repo/plugin/venv path no longer exists — the documented
    silent-07:30 failure class (the run dies before hippo starts, so only an on-demand
    surface can report it). No launchctl runtime probing (ED-3: widening to runtime
    state would need its own verified probe). ``crontab_text`` injects for hermetic
    tests. Never raises; never writes.
    """
    entries: List[dict] = []
    la_dir = launch_agents_dir or os.path.join(
        os.path.expanduser("~"), "Library", "LaunchAgents"
    )
    for path in sorted(glob.glob(os.path.join(la_dir, _LAUNCH_AGENT_GLOB))):
        entry: Dict[str, object] = {"kind": "launchd", "path": path}
        try:
            import plistlib

            with open(path, "rb") as fh:
                doc = plistlib.load(fh)
            args = doc.get("ProgramArguments") or []
            cmd = args[-1] if args and isinstance(args[-1], str) else ""
            entry.update(_classify_schedule(cmd))
            entry["label"] = doc.get("Label")
        except Exception:
            entry.update({"status": "unparseable", "missing": []})
        entries.append(entry)

    if crontab_text is None:
        crontab_text = _read_crontab()
    for line in crontab_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "-m memory.sleep" not in line:
            continue
        entry = {"kind": "cron", "path": "crontab", "line": line}
        entry.update(_classify_schedule(line))
        entries.append(entry)

    return {
        "entries": entries,
        "ok": sum(1 for e in entries if e["status"] == "ok"),
        "stale": sum(1 for e in entries if e["status"] == "stale-path"),
        "unparseable": sum(1 for e in entries if e["status"] == "unparseable"),
    }


def _read_crontab() -> str:
    """``crontab -l`` output, or "" (no crontab / no binary / any failure). A file
    oracle in spirit: reads the installed table, touches nothing."""
    try:
        import subprocess

        out = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=10
        )
        return out.stdout if out.returncode == 0 else ""
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# The composed census + report
# --------------------------------------------------------------------------- #
def machine_census(
    claude_projects_dir: Optional[str] = None,
    launch_agents_dir: Optional[str] = None,
    crontab_text: Optional[str] = None,
) -> dict:
    """All four classes, composed. Read-only; never raises."""
    return {
        "projects": registry_census(),
        "symlinks": symlink_farm_census(claude_projects_dir),
        "trust": trust_census(),
        "scheduler": scheduler_census(launch_agents_dir, crontab_text),
    }


def _n(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _render_symlinks(farm: dict) -> List[str]:
    entries = farm["entries"]
    if not entries:
        return [f"memory symlinks: none under {farm['path']}."]
    lines = [
        f"memory symlinks: {farm['path']} "
        f"({_n(len(entries), 'entry', 'entries')}: {farm['ok']} ok, "
        f"{farm['dangling_temp_rooted']} dangling temp-rooted, "
        f"{farm['dangling']} dangling kept)"
    ]
    for e in entries:
        if e["status"] == "ok":
            continue
        note = (
            " [temp-rooted — the mechanically-safe batch class]"
            if e["status"] == "dangling-temp-rooted"
            else " [kept — possibly an unmounted volume; per-item only]"
        )
        lines.append(f"  dangling{note}: {e['link']} -> {e['target']}")
    if farm["pytest_leaked"]:
        lines.append(
            f"  NB: {farm['pytest_leaked']} of the dangling targets are pytest tmp trees — "
            "self-inflicted test leak (the HYG-2 conftest isolation is the faucet fix)."
        )
    return lines


def _render_trust(census: dict) -> List[str]:
    entries = census["entries"]
    if not entries:
        return [f"trust registry (consent ledger): no rows ({census['path']})."]
    lines = [
        f"trust registry (consent ledger): {census['path']} "
        f"({_n(len(entries), 'row', 'rows')}: {census['live']} live, {census['dead']} dead)"
    ]
    for e in entries:
        tags = [str(e["origin"] or "origin unset")]
        tags.append("fingerprinted" if e["fingerprinted"] else "legacy — no baseline")
        if e["live"]:
            state = "live [temp-rooted]" if e["volatile"] else "live"
        else:
            state = "dead [temp-rooted]" if e["volatile"] else "dead [possibly unmounted]"
        lines.append(f"  {state} [{', '.join(tags)}]: {e['root']}")
    lines.append(
        "  rows are REPORT-ONLY pending owner decision Q2 — the per-row route is the "
        "`untrust` tool, which also deletes the row's SEC-6 fingerprint baseline "
        "(an unmounted-volume row would go from 'comes back trusted' to full re-consent)."
    )
    return lines


def _render_scheduler(census: dict) -> List[str]:
    entries = census["entries"]
    if not entries:
        return ["scheduler artifacts: none installed (launchd glob + crontab)."]
    lines = [
        f"scheduler artifacts (file oracles): {_n(len(entries), 'entry', 'entries')}: "
        f"{census['ok']} ok, {census['stale']} stale-path, "
        f"{census['unparseable']} unparseable"
    ]
    for e in entries:
        where = e["path"] if e["kind"] == "launchd" else f"crontab: {e.get('line', '')}"
        if e["status"] == "ok":
            lines.append(f"  ok [{e['kind']}]: {where}")
        elif e["status"] == "stale-path":
            gone = ", ".join(e.get("missing", []))
            lines.append(
                f"  stale-path [{e['kind']}]: {where} — embedded path gone: {gone} "
                "(the silent-07:30 class: the run dies before hippo starts)"
            )
        else:
            lines.append(f"  unparseable [{e['kind']}]: {where}")
    return lines


def render_report(census: dict) -> str:
    """Deterministic full report; empty-norm one-liner when every class is empty."""
    empty = (
        not census["projects"]["entries"]
        and not census["symlinks"]["entries"]
        and not census["trust"]["entries"]
        and not census["scheduler"]["entries"]
    )
    if empty:
        return (
            "machine census: nothing to report — no registry entries, no memory "
            "symlinks, no trust rows, no scheduler artifacts."
        )
    # Class (a) delegates its RENDERING too — registry's own report, verbatim, so the
    # two surfaces can never drift (the same private-import credit as above).
    from .registry import _render_report as _render_projects

    sections = [
        "machine census — the machine-state classes hippo creates (read-only):",
        _render_projects(census["projects"]),
        "\n".join(_render_symlinks(census["symlinks"])),
        "\n".join(_render_trust(census["trust"])),
        "\n".join(_render_scheduler(census["scheduler"])),
    ]
    return "\n\n".join(sections)


def main(argv=None) -> int:
    """Census CLI: report (default, read-only) / ``--json``."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m memory.machine_census",
        description=(
            "Machine-state census (HYG-1): classify every machine-state class hippo "
            "creates — projects-registry rows, ~/.claude/projects memory symlinks, "
            "trust rows, installed scheduler artifacts. Read-only."
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the census as JSON (report mode only)"
    )
    args = parser.parse_args(argv)

    census = machine_census()
    print(json.dumps(census, indent=2) if args.json else render_report(census))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
