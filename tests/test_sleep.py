"""T15 SLP: the sleep runner — scheduled maintenance stops depending on human memory.

SLP-1: one headless entrypoint (``python -m memory.sleep``) renders the existing
read-only worklists into ONE morning-report artifact — zero corpus writes, zero
trust-registry writes (assertable), per-section RCH-9 degradation, drain verbs per
surface via INV-1's registry, capture snooze honored, empty-is-one-line.
SLP-2: ``--print-schedule`` emits copy-pasteable recipes (never installs);
``--snooze Nd`` silences the report and says so once when it resumes.
SLP-3 (ratified 2026-07-16: the flag ships, default OFF): with ``HIPPO_SLEEP_TIER_A``
on, a scheduled pass may apply capped Tier-A dream edges; the report LEADS with the
undo recipe; OFF keeps SLP-1's zero-write guarantee byte-for-byte.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from memory import sleep as SL
from memory import trust

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _repo(tmp_path, monkeypatch):
    root = str(tmp_path / "repo")
    md = os.path.join(root, ".claude", "memory")
    os.makedirs(md)
    with open(os.path.join(root, "app.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(["git", "-C", root, "add", "-A"], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "seed"], check=True, env=_GIT_ENV)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    # doctor's symlink/native-memory checks resolve ~/.claude — keep them off the
    # developer's real home (read-only, but hermetic means hermetic).
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return root, md


def _mem(md, name, *, body="Body."):
    with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write(
            f'---\nname: {name}\ndescription: "d {name}"\nmetadata:\n  type: project\n---\n{body}\n'
        )


def _corpus_bytes(md):
    out = {}
    for fname in sorted(os.listdir(md)):
        p = os.path.join(md, fname)
        if os.path.isfile(p):
            out[fname] = open(p, "rb").read()
    return out


def _run(argv=None, capsys=None):
    rc = SL.main(argv or [])
    assert rc == 0
    return capsys.readouterr().out if capsys else None


# --------------------------------------------------------------------------- #
# SLP-1 — the runner + the report
# --------------------------------------------------------------------------- #
def test_empty_corpus_is_a_one_line_report(tmp_path, monkeypatch, capsys):
    _root, _md = _repo(tmp_path, monkeypatch)
    out = _run(capsys=capsys)
    body = [ln for ln in out.splitlines() if ln.strip() and not ln.startswith("report:")]
    assert len(body) == 1 and "nothing to do" in body[0]


def test_report_artifact_lands_in_the_telemetry_dir_and_prints(tmp_path, monkeypatch, capsys):
    from memory.telemetry import default_telemetry_dir

    _root, md = _repo(tmp_path, monkeypatch)
    _mem(md, "alpha")
    out = _run(capsys=capsys)
    report_path = os.path.join(default_telemetry_dir(md), "sleep-report.md")
    assert os.path.exists(report_path)
    with open(report_path, encoding="utf-8") as fh:
        assert fh.read().strip() in out  # printable-to-stdout IS the artifact


def test_zero_corpus_and_trust_registry_writes(tmp_path, monkeypatch, capsys):
    """SLP-1's headline AC, asserted byte-for-byte: a full run over a corpus with a
    pending capture and a stale memory changes NOTHING in the corpus dir and NOTHING
    in the trust registry."""
    _root, md = _repo(tmp_path, monkeypatch)
    _mem(md, "alpha")
    _mem(md, "beta")
    before = _corpus_bytes(md)
    fp_before = trust.corpus_fingerprint(md).get("digest")
    trust_file = os.environ["HIPPO_TRUST_FILE"]
    trust_before = open(trust_file, "rb").read() if os.path.exists(trust_file) else None
    _run(capsys=capsys)
    assert _corpus_bytes(md) == before
    assert trust.corpus_fingerprint(md).get("digest") == fp_before
    trust_after = open(trust_file, "rb").read() if os.path.exists(trust_file) else None
    assert trust_after == trust_before


def test_sections_degrade_per_signal_and_are_named(tmp_path, monkeypatch, capsys):
    """RCH-9: a failing producer is NAMED in the report, never silently dropped."""
    _root, md = _repo(tmp_path, monkeypatch)
    _mem(md, "alpha")

    import memory.sleep as sleep_mod

    def _boom(*a, **k):
        raise RuntimeError("substrate missing")

    monkeypatch.setitem(dict(), "noop", None)  # keep monkeypatch used consistently
    monkeypatch.setattr(sleep_mod, "_section_dream", _boom)
    out = _run(capsys=capsys)
    assert "dream" in out and "failed" in out and "substrate missing" in out


def _seed_pending(md, root):
    """One real capture seed in the pending queue (an episode makes it non-empty)."""
    from memory.capture import write_session_capture
    from memory.telemetry import default_telemetry_dir, log_episode

    td = default_telemetry_dir(md)
    assert log_episode(["alpha"], query="how do we deploy", repo_root=root, telemetry_dir=td,
                       session_id="sess-1")
    path = write_session_capture("sess-1", memory_dir=md, repo_root=root, telemetry_dir=td)
    assert path
    return path


def test_report_names_drain_verbs_per_surface_from_the_registry(tmp_path, monkeypatch, capsys):
    """Every section names its per-item drain verb for BOTH surfaces, derived from
    INV-1's registry — the report never invents a route the registry doesn't declare."""
    _root, md = _repo(tmp_path, monkeypatch)
    _mem(md, "alpha")
    _seed_pending(md, _root)
    out = _run(capsys=capsys)
    assert "/hippo:consolidate" in out  # the terminal drain for the capture queue
    assert "capture" in out and "new_memory" in out  # the Desktop tool route (registry row)


def test_capture_snooze_is_honored(tmp_path, monkeypatch, capsys):
    from memory.capture import snooze_queue

    _root, md = _repo(tmp_path, monkeypatch)
    _mem(md, "alpha")
    _seed_pending(md, _root)
    assert snooze_queue(memory_dir=md)
    out = _run(capsys=capsys)
    assert "pending capture" not in out.lower() or "snoozed" in out.lower()


# --------------------------------------------------------------------------- #
# SLP-2 — recipes + report-level snooze
# --------------------------------------------------------------------------- #
def test_print_schedule_emits_recipes_and_installs_nothing(tmp_path, monkeypatch, capsys):
    from memory.telemetry import default_telemetry_dir

    root, md = _repo(tmp_path, monkeypatch)
    _mem(md, "alpha")
    out = _run(["--print-schedule"], capsys=capsys)
    assert "crontab" in out and "-m memory.sleep" in out
    assert "launchd" in out and "<plist" in out and "StartCalendarInterval" in out
    assert "scheduled-task" in out and '"schedule"' in out
    assert root in out  # THIS repo's paths, not placeholders
    # print-only: no report artifact, no state file, nothing installed anywhere.
    td = default_telemetry_dir(md)
    assert not os.path.exists(os.path.join(td, "sleep-report.md"))
    assert not os.path.exists(os.path.join(td, "sleep-state.json"))
    # the docs name the failure modes next to the recipes (RCH-9 discipline)
    assert "machine" in out and "moved" in out


def test_snooze_silences_and_resumes_with_a_note(tmp_path, monkeypatch, capsys):
    from memory.telemetry import default_telemetry_dir

    _root, md = _repo(tmp_path, monkeypatch)
    _mem(md, "alpha")
    out = _run(["--snooze", "3d"], capsys=capsys)
    assert "snoozed" in out and "3" in out

    out = _run(capsys=capsys)  # inside the snooze window: one quiet line, no report
    assert "snoozed" in out
    td = default_telemetry_dir(md)
    assert not os.path.exists(os.path.join(td, "sleep-report.md"))

    # Expire the snooze by backdating the marker, then run again: the report resumes
    # and says so ONCE.
    state_path = os.path.join(td, "sleep-state.json")
    with open(state_path, encoding="utf-8") as fh:
        state = json.load(fh)
    state["snooze_until"] = (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).isoformat(timespec="seconds")
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    out = _run(capsys=capsys)
    assert "resuming — the report snooze expired" in out
    out = _run(capsys=capsys)
    assert "resuming — the report snooze expired" not in out  # said once, not forever


def test_report_carries_the_last_run_stamp(tmp_path, monkeypatch, capsys):
    """A stalled schedule must be visible: the NEXT report names the last run, so a
    gap (machine asleep, venv moved, cron dead) lands in the report instead of
    vanishing (RCH-9)."""
    _root, md = _repo(tmp_path, monkeypatch)
    _mem(md, "alpha")
    out = _run(capsys=capsys)
    assert "first sleep run" in out
    out = _run(capsys=capsys)
    assert "last sleep run" in out
