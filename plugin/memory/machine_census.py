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
    paths, plus OPS-2's dead-man join: each ok artifact's embedded repo resolved to
    its own gitignored ``sleep-state.json`` ``last_run_at`` — the oracle sleep.py
    already writes on every run, previously zero readers) — launchctl runtime state
    is deliberately out of scope (ED-3: widening to runtime probing would need its
    own verified probe and its own item), as are Claude scheduled-task recipes (no
    local oracle). A loaded-but-dying schedule that stopped running surfaces as
    ``quiet``; remediation stays the human's.
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

# OPS-2: the dead-man window — an otherwise-ok artifact whose repo's last recorded
# sleep run is older than this (or absent entirely) classifies as "quiet". A module
# constant, deliberately not an env knob: the 07:30 recipe is daily, so ~3 days
# separates "weekend/laptop-asleep" from "this schedule stopped running".
_QUIET_AFTER_DAYS = 3


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
    log = re.search(r">>\s+(\S+)", cmd)
    return {
        "repo_root": repo.group(1) if repo else None,
        "plugin_root": plugin.group(1) if plugin else None,
        "python": python.group(1) if python else None,
        "log": log.group(1) if log else None,
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


def _sleep_freshness(repo_root: str) -> dict:
    """OPS-2: the dead-man join for one ok artifact — FILE ORACLE only.

    Resolves the artifact's embedded repo to hippo's own gitignored telemetry
    (``<repo>/.claude/memory`` -> ``telemetry.default_telemetry_dir`` -> the
    ``sleep-state.json`` that ``sleep.py`` stamps on every run, read through
    ``sleep._read_state`` — the ONE canonical reader, same private-import posture as
    ``_under_volatile_root``). ``quiet`` when the stamp is absent, unparseable, or
    older than ``_QUIET_AFTER_DAYS``: a loaded-but-dying schedule (unloaded by hand,
    broken venv python, deps rot) is exactly the class the stale-path check cannot
    see. Pure file reads — no runtime probing of any kind, no write (the ED-3 pin
    scans this function's source for the forbidden tokens, which is why they are not
    even named here); an unexpected failure reads as fresh (a census must not cry
    wolf on its own bug). Never raises.
    """
    try:
        from datetime import datetime, timezone

        from .sleep import _read_state
        from .telemetry import default_telemetry_dir

        td = default_telemetry_dir(os.path.join(repo_root, ".claude", "memory"))
        last = _read_state(td).get("last_run_at")
        if not isinstance(last, str) or not last.strip():
            return {"quiet": True, "last_run_at": None, "age_days": None}
        try:
            stamp = datetime.fromisoformat(last)
        except ValueError:
            return {"quiet": True, "last_run_at": last, "age_days": None}
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - stamp).total_seconds() / 86400.0
        return {
            "quiet": age_days > _QUIET_AFTER_DAYS,
            "last_run_at": last,
            "age_days": max(0, int(age_days)),
        }
    except Exception:
        return {"quiet": False, "last_run_at": None, "age_days": None}


def scheduler_census(
    launch_agents_dir: Optional[str] = None, crontab_text: Optional[str] = None
) -> dict:
    """Census installed SLP-2 artifacts by FILE ORACLE only.

    launchd: glob ``~/Library/LaunchAgents/com.hippo.sleep.*.plist`` + plistlib parse of
    the embedded command; cron: ``crontab -l`` lines invoking ``-m memory.sleep``.
    ``stale-path`` = an embedded repo/plugin/venv path no longer exists — the documented
    silent-07:30 failure class (the run dies before hippo starts, so only an on-demand
    surface can report it). ``quiet`` (OPS-2) = the artifact looks ok but its repo's
    ``sleep-state.json`` ``last_run_at`` is absent or older than ``_QUIET_AFTER_DAYS``
    — the loaded-but-dying complement stale-path cannot see (``_sleep_freshness``).
    No launchctl runtime probing (ED-3: widening to runtime state would need its own
    verified probe). ``crontab_text`` injects for hermetic tests. Never raises; never
    writes.
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

    # OPS-2: the dead-man join — only otherwise-ok artifacts with a resolvable
    # embedded repo are checked (stale-path/unparseable classification unchanged;
    # an ok artifact whose command has no cd-<repo> keeps ok — no oracle to read).
    for entry in entries:
        if entry.get("status") == "ok" and entry.get("repo_root"):
            fresh = _sleep_freshness(str(entry["repo_root"]))
            if fresh["quiet"]:
                entry["status"] = "quiet"
                entry["last_run_at"] = fresh["last_run_at"]
                entry["age_days"] = fresh["age_days"]

    return {
        "entries": entries,
        "ok": sum(1 for e in entries if e["status"] == "ok"),
        "quiet": sum(1 for e in entries if e["status"] == "quiet"),
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
# HYG-2: the one genuinely-new write — the dangling-symlink remover
# --------------------------------------------------------------------------- #
def prune_dangling(claude_projects_dir: Optional[str] = None) -> dict:
    """Remove every dangling memory symlink whose TARGET is temp-rooted; keep the rest.

    The batch is confined to the one mechanically-safe class (``registry.prune_dead``'s
    honesty grain, mirrored exactly): islink AND target gone AND target under a system
    temp root — a dangling target anywhere else could be an unmounted volume and stays
    per-item (``kept_dead``, named for a deliberate hand removal). Never touches
    non-symlink shapes (a real ``memory`` dir/file is native-memory state and never
    even enumerates); only ever removes the ``memory`` symlink hippo itself creates,
    never its harness-owned parent dir. The removal is a bare unlink and claims no
    more — no registry document is rewritten, so SEC-19's atomic-whole-document
    discipline is not implicated. Never raises.
    """
    farm = symlink_farm_census(claude_projects_dir)
    removed: List[dict] = []
    kept_dead = [e for e in farm["entries"] if e["status"] == "dangling"]
    failed: List[dict] = []
    for e in farm["entries"]:
        if e["status"] != "dangling-temp-rooted":
            continue
        try:
            if not os.path.islink(e["link"]):  # re-verify at act time, not census time
                continue
            os.remove(e["link"])
            removed.append(e)
        except Exception as exc:
            failed.append({**e, "error": str(exc)})
    return {"removed": removed, "kept_dead": kept_dead, "failed": failed}


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
    if farm["dangling_temp_rooted"]:
        lines.append(
            "drain the "
            + _n(farm["dangling_temp_rooted"], "temp-rooted entry", "temp-rooted entries")
            + ": python -m memory.machine_census --prune-dangling"
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
        f"{census['ok']} ok, {census.get('quiet', 0)} quiet, {census['stale']} stale-path, "
        f"{census['unparseable']} unparseable"
    ]
    for e in entries:
        where = e["path"] if e["kind"] == "launchd" else f"crontab: {e.get('line', '')}"
        if e["status"] == "ok":
            lines.append(f"  ok [{e['kind']}]: {where}")
        elif e["status"] == "quiet":
            # OPS-2: name the age and the log to check — no recipe, no probe, no
            # editorializing; a quiet schedule's diagnosis (unloaded? broken venv?
            # deps rot?) is the human's, from the log the artifact already writes.
            last, age = e.get("last_run_at"), e.get("age_days")
            if last and age is not None:
                seen = f"last recorded sleep run {age}d ago ({last})"
            elif last:
                seen = f"last recorded sleep run unparseable ({last})"
            else:
                seen = "no recorded sleep run"
            log = e.get("log") or "the log path in the artifact's command"
            lines.append(
                f"  quiet [{e['kind']}]: {where} — artifact looks ok but {seen} "
                f"(dead-man window: {_QUIET_AFTER_DAYS}d); check the log: {log}"
            )
        elif e["status"] == "stale-path":
            gone = ", ".join(e.get("missing", []))
            lines.append(
                f"  stale-path [{e['kind']}]: {where} — embedded path gone: {gone} "
                "(the silent-07:30 class: the run dies before hippo starts)"
            )
            # SLP-2's print-only posture: hippo never uninstalls system state — the
            # recipe is printed for the human to run, exactly like install was.
            if e["kind"] == "launchd":
                lines.append(
                    f"    recipe (print-only — hippo never uninstalls system state): "
                    f"launchctl unload {e['path']} && rm {e['path']}"
                )
            else:
                lines.append(
                    "    recipe (print-only): crontab -e and delete the stale line, "
                    "then re-run the sleep --print-schedule recipe if wanted"
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
        "--prune-dangling",
        action="store_true",
        help="remove dangling memory symlinks whose TARGET sits under a system temp "
        "root (each removal printed); dangling targets elsewhere are kept and named "
        "— they could be unmounted volumes",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the census as JSON (report mode only)"
    )
    args = parser.parse_args(argv)

    if args.json and args.prune_dangling:
        parser.error("--json shapes the report only; it cannot ride a mutation")

    if args.prune_dangling:
        result = prune_dangling()
        for e in result["removed"]:
            print(f"removed: {e['link']} -> {e['target']}")
        for e in result["kept_dead"]:
            print(
                f"kept (target not temp-rooted — possibly an unmounted volume): "
                f"{e['link']} -> {e['target']} — remove deliberately by hand"
            )
        for e in result["failed"]:
            print(f"remove FAILED: {e['link']} ({e['error']})")
        if not result["removed"] and not result["failed"]:
            print("nothing to prune — no dangling temp-rooted memory symlinks.")
        return 1 if result["failed"] else 0

    census = machine_census()
    print(json.dumps(census, indent=2) if args.json else render_report(census))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
