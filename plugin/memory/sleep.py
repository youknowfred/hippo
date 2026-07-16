"""SLP-1/2/3: the sleep runner — hippo's maintenance loops get a cadence.

The biomimetic story says consolidate/dream are sleep — but sleep only ever happened
when the human remembered to invoke it, so queues deepened exactly on the projects
that need memory most. This module is ONE headless entrypoint (``python -m
memory.sleep``, or ``hippo sleep``) that runs the existing READ-ONLY producers
off-session and renders ONE morning-report artifact:

  - doctor's deterministic health report (DOC-4, verbatim)
  - the CAP-2 pending-capture triage listing (the consolidate drain's own view;
    honors the queue snooze)
  - the LIF-1 reconsolidation worklist (the SessionStart producer, verbatim — the
    report REUSES producer functions, never forks their text, so the same item can
    never nag in two dialects)
  - dream discovery (the DRM-1 report pass — or, ONLY under the SLP-3 opt-in flag,
    the capped Tier-A apply pass with ``origin=sleep:<ts>`` ledger provenance and an
    undo-first report)
  - link health (the GRA lint producer)

Every section names its per-item drain verb PER SURFACE via INV-1's surface registry
(``memory.surfaces``) — the report is a proposal queue the human drains in their next
interactive session; it never drains anything itself.

Write posture (SLP-1's headline, asserted by test): ZERO corpus writes and ZERO
trust-registry writes — the corpus fingerprint is byte-identical across a run. The
runner writes only the derived telemetry dir: the report artifact, its own small
state file (last-run stamp + snooze), and whatever gitignored ledgers the read-only
producers already maintain. With the SLP-3 flag OFF (the default) that guarantee
holds byte-for-byte even when dream has an eligible edge.

Degradation is per-section and RCH-9-loud: a failed section is NAMED in the report
with its error, never dropped; an empty everything renders a one-line "nothing to
do" report (not silence, not noise). Headless from a bare venv: no
``CLAUDE_PLUGIN_DATA`` required (the state/report live in the corpus-derived
telemetry dir, not plugin data).

Scheduling (SLP-2) is EXPLICIT-INSTALL ONLY, mirroring bootstrap's consent style:
``--print-schedule`` prints copy-pasteable launchd/cron/scheduled-task recipes for
THIS machine's interpreter and repo — hippo never writes system state; the human
owns the install. Failure modes and where they surface:

  - machine asleep / runner never fired: nothing crashes — the NEXT report's
    "last sleep run" line shows the gap (a stall is visible, never silent);
  - venv or repo moved/deleted: the scheduled command itself fails before hippo
    starts (cron mails stderr; launchd logs to the StandardErrorPath in the recipe);
    re-run ``--print-schedule`` and reinstall the printed line;
  - a section's substrate missing (no index, no telemetry yet): that section
    reports its own degradation inline and the rest of the report still renders.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

_REPORT_NAME = "sleep-report.md"
_STATE_NAME = "sleep-state.json"

# SLP-3 (owner-ratified 2026-07-16): the ONE autonomy question, as an explicit opt-in
# env flag, default OFF. OFF = report-only dream discovery (zero writes). ON = the
# existing DRM-2 Tier-A apply contract (cap, θ, aging firewall, ping-pong guard —
# unchanged), stamped origin=sleep:<ts>, undo-first in the report.
_TIER_A_FLAG = "HIPPO_SLEEP_TIER_A"


def _flag_on(name: str) -> bool:
    return (os.environ.get(name) or "").strip() not in ("", "0", "false", "False")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# State (last-run stamp + report snooze) — derived, per-clone, telemetry-dir.
# --------------------------------------------------------------------------- #
def _state_path(telemetry_dir: str) -> str:
    return os.path.join(telemetry_dir, _STATE_NAME)


def _read_state(telemetry_dir: str) -> dict:
    try:
        with open(_state_path(telemetry_dir), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_state(telemetry_dir: str, state: dict) -> Optional[str]:
    """Persist the runner's state. Returns an error string on failure (RCH-9: the
    caller prints it — a lost last-run stamp must not vanish silently)."""
    try:
        from .atomic import write_json_atomic
        from .provenance import ensure_self_ignoring_dir

        ensure_self_ignoring_dir(telemetry_dir)
        write_json_atomic(_state_path(telemetry_dir), state)
        return None
    except Exception as exc:
        return f"could not write {_STATE_NAME}: {exc}"


def _write_report(telemetry_dir: str, text: str) -> Tuple[Optional[str], Optional[str]]:
    """Write the report artifact. Returns ``(path, error)`` — exactly one is set."""
    try:
        from .atomic import write_text_atomic
        from .provenance import ensure_self_ignoring_dir

        ensure_self_ignoring_dir(telemetry_dir)
        path = os.path.join(telemetry_dir, _REPORT_NAME)
        write_text_atomic(path, text)
        return path, None
    except Exception as exc:
        return None, f"could not write {_REPORT_NAME}: {exc}"


# --------------------------------------------------------------------------- #
# Drain lines — every section routes to its per-item verb, per surface, from the
# INV-1 registry (this module is the registry's designed OFFLINE consumer; the
# hot path still never reads it).
# --------------------------------------------------------------------------- #
def _drain_line(verb: str) -> str:
    from .surfaces import verb_map

    row = verb_map().get(verb)
    if row is None:
        return f"drain: /hippo:{verb} (terminal)"
    if row.desktop == "tool":
        desktop = f"the {row.mcp_tools[0]} tool"
    elif row.desktop == "skill_tools":
        desktop = (
            f"the {verb} skill driving the {', '.join(row.mcp_tools)} tools (per item)"
        )
    else:
        desktop = "terminal-only for now"
    return f"drain: /hippo:{verb} (terminal) · Desktop: {desktop}"


# --------------------------------------------------------------------------- #
# Sections — each returns Optional[str] (None = nothing to report). Every one is
# an EXISTING read-only producer; the runner adds routing, never new analysis.
# --------------------------------------------------------------------------- #
def _section_doctor(memory_dir: str, repo_root: str) -> Optional[str]:
    from .doctor import DoctorContext, render

    return render(DoctorContext(memory_dir, repo_root))


def _section_pending(memory_dir: str, repo_root: str) -> Optional[str]:
    from .capture import _format_listing, default_pending_dir, queue_snoozed, read_pending

    pd = default_pending_dir(memory_dir)
    seeds = read_pending(pd)
    if not seeds:
        return None
    if queue_snoozed(pd, memory_dir=memory_dir):
        # CAP-6: the queue snooze quiets THIS surface too — same snooze, both dialects.
        return (
            f"{len(seeds)} seed(s) queued, nudge snoozed — seeds kept; the listing "
            "returns when the snooze expires."
        )
    return _format_listing(seeds)


def _section_reconsolidation(memory_dir: str, repo_root: str) -> Optional[str]:
    from .reconsolidate import reconsolidation_producer

    return reconsolidation_producer(memory_dir, repo_root, None)


def _section_dream(memory_dir: str, repo_root: str) -> Optional[str]:
    """Dream discovery — report-only by default; the SLP-3 apply lane lives in
    ``_run_report`` (it must lead the report, not sit inside a section). Dream's own
    empty norms (no candidates / below-soak / empty corpus) read as nothing-to-report
    here — an empty discovery must not stop the report being one line."""
    from . import dream

    _code, text = dream.run_report_pass(memory_dir)
    first = (text or "").split("\n", 1)[0]
    if "— no candidates:" in first or re.search(r"\b0 candidate", first):
        return None
    return text


def _section_links(memory_dir: str, repo_root: str) -> Optional[str]:
    from .lint_links import lint_links_producer

    return lint_links_producer(memory_dir, repo_root, None)


def _section_promote_scan(memory_dir: str, repo_root: str) -> Optional[str]:
    """EXT-2: cross-project promotion candidates — the machine-wide, trusted-only,
    report-only sweep (``promote_scan.scan``). Section rendered ONLY when there are
    actual proposals; the sweep's diagnostics (untrusted counts, index notes) belong
    to a deliberate ``python -m memory.promote_scan`` run, not the morning report's
    empty norm."""
    from .promote_scan import render_report, scan

    result = scan()
    if not result.get("proposals"):
        return None
    return render_report(result)


# (key, title, drain verb, producer ATTR NAME — resolved at call time so a test can
# monkeypatch a section producer on the module and the runner sees it)
_SECTIONS = (
    ("doctor", "Plumbing (doctor)", "doctor", "_section_doctor"),
    ("pending_captures", "Pending captures (CAP-2 triage)", "consolidate", "_section_pending"),
    ("reconsolidation", "Reconsolidation worklist (LIF-1)", "consolidate", "_section_reconsolidation"),
    ("dream", "Dream discovery (DRM-1)", "dream", "_section_dream"),
    ("link_health", "Link health (GRA)", "consolidate", "_section_links"),
    ("promotion_mining", "Cross-project promotion candidates (EXT-2)", "promote", "_section_promote_scan"),
)


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #
def _tier_a_pass(memory_dir: str) -> Tuple[Optional[str], Optional[str]]:
    """SLP-3: the opt-in overnight apply. ``(lead_line, section_text)`` — the lead is
    the report's FIRST line (undo-first, the owner-ratified posture) and is only set
    when edges actually applied; both are None when the flag is off (the default)."""
    if not _flag_on(_TIER_A_FLAG):
        return None, None
    from . import dream

    origin = f"sleep:{_iso(_now())}"
    _code, text = dream.run_apply_pass(memory_dir, origin=origin)
    first = (text or "").split("\n", 1)[0]
    m = re.search(r"applied (\d+) edge", first)
    applied = int(m.group(1)) if m else 0
    if applied > 0:
        lead = (
            f"{first} — undo: the dream tool action='undo' "
            f"(terminal: `--undo` on `python -m memory.dream`)"
        )
        return lead, text
    return None, text


def _run_report(memory_dir: str, repo_root: str, telemetry_dir: str) -> Tuple[str, List[str]]:
    """Assemble the morning report. Returns ``(report_text, stdout_warnings)``."""
    state = _read_state(telemetry_dir)
    now = _now()
    warnings: List[str] = []
    this_module = sys.modules[__name__]

    last = state.get("last_run_at")
    stamp = f"last sleep run: {last}" if last else "first sleep run on this clone"
    resume_note = None
    if state.pop("resume_note_pending", None):
        resume_note = (
            f"(resuming — the report snooze expired; it ran until {state.get('snooze_until')})"
        )
        state.pop("snooze_until", None)
    header = [f"# hippo sleep report — {_iso(now)}", stamp]
    if resume_note:
        header.append(resume_note)

    # SLP-3 first: when it applied anything, the report's FIRST line is the undo.
    lead: Optional[str] = None
    dream_override: Optional[str] = None
    try:
        lead, dream_override = _tier_a_pass(memory_dir)
    except Exception as exc:
        dream_override = f"⚠ section tier_a failed: {type(exc).__name__}: {exc}"

    blocks: List[str] = []
    for key, title, verb, producer_name in _SECTIONS:
        if key == "dream" and dream_override is not None:
            # The flag-on pass already ran dream (apply mode); don't run it twice.
            blocks.append(f"## {title}\n{dream_override.rstrip()}\n{_drain_line(verb)}")
            continue
        try:
            text = getattr(this_module, producer_name)(memory_dir, repo_root)
        except Exception as exc:
            # RCH-9: a failed section is part of the report, never a silent hole.
            text = f"⚠ section {key} failed: {type(exc).__name__}: {exc}"
        if not text:
            continue
        blocks.append(f"## {title}\n{text.rstrip()}\n{_drain_line(verb)}")

    state["last_run_at"] = _iso(now)
    err = _write_state(telemetry_dir, state)
    if err:
        warnings.append(f"⚠ {err}")

    # The empty norm: when nothing is QUEUED (every non-plumbing section empty and no
    # overnight applies), the report is ONE line — with the plumbing state folded in
    # rather than dropped (RCH-9: doctor's warnings are named, they just don't get a
    # section when there is no maintenance to do).
    non_doctor = [b for b in blocks if not b.startswith("## Plumbing")]
    if lead is None and dream_override is None and not non_doctor:
        doctor_block = next((b for b in blocks if b.startswith("## Plumbing")), None)
        doctor_clean = bool(doctor_block) and (
            "✘" not in doctor_block and "⚠" not in doctor_block
        )
        plumbing = (
            "plumbing clean"
            if doctor_clean or doctor_block is None
            else "plumbing has warnings — run /hippo:doctor"
        )
        # Still ONE line — but the run stamp (a stalled schedule must stay visible)
        # and the once-only resume note fold into it rather than being dropped.
        one = (
            f"hippo sleep report {_iso(now)}: nothing to do — queues empty; {plumbing} "
            f"({stamp}{'; ' + resume_note if resume_note else ''})"
        )
        return one + "\n", warnings

    head = ([lead] if lead else []) + header
    return "\n".join(head) + "\n\n" + "\n\n".join(blocks) + "\n", warnings


# --------------------------------------------------------------------------- #
# SLP-2 — scheduler recipes (print-only, never installed by hippo)
# --------------------------------------------------------------------------- #
def _print_schedule(memory_dir: str, repo_root: str, telemetry_dir: str) -> str:
    from xml.sax.saxutils import escape

    data = os.environ.get("CLAUDE_PLUGIN_DATA") or ""
    venv_py = os.path.join(data, "venv", "bin", "python")
    py = venv_py if data and os.access(venv_py, os.X_OK) else sys.executable
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
    log = os.path.join(telemetry_dir, "sleep.log")
    cmd = f"cd {repo_root} && PYTHONPATH={plugin_root} {py} -m memory.sleep"
    key = re.sub(r"[^a-z0-9]+", "-", os.path.basename(repo_root).lower()).strip("-") or "repo"
    # The shell line rides inside XML <string> elements: `&&`/`>>` (and any &, <, >
    # in the user's paths) MUST be XML-escaped or the plist does not parse — plutil
    # refused the very first dogfood install over a raw `&&`.
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.hippo.sleep.{key}</string>
  <key>ProgramArguments</key><array>
    <string>/bin/sh</string><string>-c</string>
    <string>{escape(f"{cmd} >> {log} 2>&1")}</string>
  </array>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>7</integer><key>Minute</key><integer>30</integer>
  </dict>
  <key>StandardErrorPath</key><string>{escape(log)}</string>
</dict></plist>"""
    task = json.dumps(
        {
            "name": f"hippo sleep report ({os.path.basename(repo_root)})",
            "schedule": "30 7 * * 1-5",
            "command": cmd,
        },
        indent=2,
    )
    return f"""hippo sleep — scheduler recipes (PRINT-ONLY: hippo never installs system state;
copy the one you want and install it yourself — the explicit-install posture is the point).

## crontab (crontab -e, weekday mornings 07:30)
30 7 * * 1-5 {cmd} >> {log} 2>&1

## launchd (macOS): save as ~/Library/LaunchAgents/com.hippo.sleep.{key}.plist, then
## launchctl load ~/Library/LaunchAgents/com.hippo.sleep.{key}.plist
{plist}

## Claude scheduled-task (paste into your scheduler of choice)
{task}

Failure modes — where each one surfaces (nothing vanishes silently):
- machine asleep / run skipped: the NEXT report's "last sleep run" line shows the gap.
- venv moved or repo moved: the command above fails before hippo starts (cron mails
  stderr; launchd writes {log}) — re-run --print-schedule and reinstall the new line.
- report going stale (nobody reads it): it is one markdown file at
  {os.path.join(telemetry_dir, _REPORT_NAME)} — snooze it honestly
  (`hippo sleep --snooze 7d`) instead of letting it rot; it says so once when it resumes.
"""


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_snooze(raw: str) -> Optional[int]:
    m = re.fullmatch(r"(\d+)d?", raw.strip())
    return int(m.group(1)) if m else None


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="The sleep runner (SLP-1/2/3): render the read-only maintenance "
        "worklists into one morning report. Zero corpus writes; the report is a "
        "proposal queue you drain per item in your next interactive session."
    )
    parser.add_argument(
        "--print-schedule",
        action="store_true",
        help="print copy-pasteable launchd/cron/scheduled-task recipes for THIS "
        "machine and repo — prints only, never installs (SLP-2)",
    )
    parser.add_argument(
        "--snooze",
        default=None,
        metavar="Nd",
        help="silence the sleep report for N days (e.g. 7d; 0d clears) — it says so "
        "once when it resumes (SLP-2)",
    )
    parser.add_argument("--memory-dir", default=None)
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args(argv)
    try:
        from .provenance import resolve_dirs
        from .telemetry import default_telemetry_dir

        memory_dir, repo_root = args.memory_dir, args.repo_root
        if memory_dir is None or repo_root is None:
            md, rr = resolve_dirs()
            memory_dir = memory_dir or md
            repo_root = repo_root or rr
        td = default_telemetry_dir(memory_dir)

        if args.print_schedule:
            print(_print_schedule(memory_dir, repo_root, td))
            return 0

        if args.snooze is not None:
            days = _parse_snooze(args.snooze)
            if days is None:
                print("sleep: --snooze takes Nd (e.g. 7d, or 0d to clear)")
                return 2
            state = _read_state(td)
            if days == 0:
                state.pop("snooze_until", None)
                state.pop("resume_note_pending", None)
                err = _write_state(td, state)
                print("sleep report snooze cleared." + (f" ⚠ {err}" if err else ""))
                return 0
            until = _iso(_now() + timedelta(days=days))
            state["snooze_until"] = until
            state["resume_note_pending"] = True
            err = _write_state(td, state)
            print(
                f"sleep report snoozed for {days} day(s) (until {until}) — it will say "
                "so once when it resumes." + (f" ⚠ {err}" if err else "")
            )
            return 0

        # A live snooze: one quiet line, no report render, no artifact churn.
        state = _read_state(td)
        until = state.get("snooze_until")
        if until:
            try:
                active = _now() < datetime.fromisoformat(until)
            except Exception:
                active = False
            if active:
                print(f"sleep report snoozed until {until} — `hippo sleep --snooze 0d` to clear.")
                return 0

        report, warnings = _run_report(memory_dir, repo_root, td)
        path, err = _write_report(td, report)
        print(report.rstrip())
        if path:
            print(f"report: {path}")
        if err:
            warnings.append(f"⚠ {err} — the report above was NOT persisted")
        for w in warnings:
            print(w)
        return 0
    except Exception as exc:
        # A total failure is loud and non-zero — cron/launchd surface it (RCH-9).
        print(f"sleep runner failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
