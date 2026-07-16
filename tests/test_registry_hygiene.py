"""RCH-11: projects-registry hygiene — census, temp-rooted prune, per-item drop.

EXT-2's real-machine dogfood found ``~/.claude/hippo-projects.json`` carrying 13+ junk
rows left by test/scratch sessions that ran a real ``init`` (tmp-dir clones whose
corpora later vanished — including one REAL project key whose ``memory_dir`` pointed
into a dead pytest tmp dir). ``registered_projects()`` already read-time-skips dead
entries and deliberately never auto-prunes (an unmounted checkout must come back on
its own — RCH-4's docstring promise). This lane adds the deliberate, human-invoked
hygiene path:

- ``registry_census()`` — read-only classification (live / dead / temp-rooted /
  repairable) that the report CLI and the doctor check both render from.
- ``prune_dead()`` — removes ONLY the mechanically-safe class: dead entries whose
  ``memory_dir`` sits under a system temp root, which cannot be an unmounted checkout
  (temp roots do not come back after cleanup). Everything else stays, named.
- ``--drop <root>`` — the explicit per-item form for entries that need human judgment.
- an ``init`` warning when a registration pairs a temp-rooted corpus with the REAL
  machine registry (the exact leak class that produced the junk).
"""

from __future__ import annotations

import json
import os
import shutil

import pytest

from memory import registry as R


# --------------------------------------------------------------------------- #
# Helpers — all state flows through HIPPO_PROJECTS_FILE (conftest points it at a
# per-test tmp file, so nothing here can touch the runner's real registry).
# --------------------------------------------------------------------------- #
def _live_project(tmp_path, name: str):
    root = tmp_path / name
    md = root / ".claude" / "memory"
    md.mkdir(parents=True)
    assert R.register_project(str(root), str(md)) is True
    return str(root), str(md)


def _dead_volatile_project(tmp_path, name: str):
    """Registered under tmp (volatile), then deleted — the scratchpad-clone class."""
    root, md = _live_project(tmp_path, name)
    shutil.rmtree(root)
    return root, md


def _dead_nonvolatile_entry(name: str):
    """A dead entry OUTSIDE any temp root — indistinguishable from an unmounted
    checkout, so prune must never touch it. register_project never validates
    existence (registration is a statement, not a probe), so the public API works."""
    root = f"/nonexistent-rch11/{name}"
    md = f"{root}/.claude/memory"
    assert R.register_project(root, md) is True
    return os.path.realpath(root), md


def _registry_bytes() -> bytes:
    with open(R.projects_registry_path(), "rb") as fh:
        return fh.read()


def _entry(census: dict, root: str) -> dict:
    matches = [e for e in census["entries"] if e["root"] == os.path.realpath(root)]
    assert len(matches) == 1, f"expected exactly one census row for {root}"
    return matches[0]


# --------------------------------------------------------------------------- #
# _under_volatile_root — the pure classifier the safe-prune class stands on.
# --------------------------------------------------------------------------- #
def test_volatile_root_classifier():
    import tempfile

    assert R._under_volatile_root(os.path.join(tempfile.gettempdir(), "x")) is True
    assert R._under_volatile_root("/tmp/newproj.0Z3R/.claude/memory") is True
    assert R._under_volatile_root("/private/tmp/claude-501/s/clone") is True
    assert R._under_volatile_root("/var/folders/yl/abc/T/pytest-of-x/pytest-1/t0") is True
    assert R._under_volatile_root("/Users/dev/projects/real") is False
    assert R._under_volatile_root("/Volumes/backup/repo/.claude/memory") is False
    # Prefix means PATH prefix, not string prefix — /tmpfoo is not under /tmp.
    assert R._under_volatile_root("/tmpfoo/repo") is False


# --------------------------------------------------------------------------- #
# registry_census — read-only classification.
# --------------------------------------------------------------------------- #
def test_census_classifies_and_counts(tmp_path):
    live_root, _ = _live_project(tmp_path, "alive")
    dead_v_root, _ = _dead_volatile_project(tmp_path, "scratch-clone")
    dead_nv_root, _ = _dead_nonvolatile_entry("maybe-unmounted")

    c = R.registry_census()
    assert c["path"] == R.projects_registry_path()
    assert len(c["entries"]) == 3

    live = _entry(c, live_root)
    assert live["live"] is True and live["volatile"] is True  # tmp_path IS a temp root

    dead_v = _entry(c, dead_v_root)
    assert dead_v["live"] is False and dead_v["volatile"] is True
    assert dead_v["repairable"] is False

    dead_nv = _entry(c, dead_nv_root)
    assert dead_nv["live"] is False and dead_nv["volatile"] is False


def test_census_flags_repairable_root(tmp_path):
    """The corrupt-hippo shape: a REAL root whose registered memory_dir died, while a
    live corpus sits at the canonical <root>/.claude/memory — the census must name the
    re-init route rather than let a prune read as data loss."""
    root = tmp_path / "realproj"
    canonical = root / ".claude" / "memory"
    canonical.mkdir(parents=True)
    gone = tmp_path / "gone-elsewhere" / "memory"
    gone.mkdir(parents=True)
    assert R.register_project(str(root), str(gone)) is True
    shutil.rmtree(gone)

    e = _entry(R.registry_census(), str(root))
    assert e["live"] is False and e["repairable"] is True


def test_census_is_read_only_and_never_raises(tmp_path):
    _dead_volatile_project(tmp_path, "junk")
    before = _registry_bytes()
    R.registry_census()
    assert _registry_bytes() == before

    # Missing and corrupt files degrade to an empty census, never an exception —
    # the same never-raise discipline as registered_projects().
    os.remove(R.projects_registry_path())
    assert R.registry_census()["entries"] == []
    with open(R.projects_registry_path(), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert R.registry_census()["entries"] == []


# --------------------------------------------------------------------------- #
# prune_dead — removes ONLY temp-rooted dead entries.
# --------------------------------------------------------------------------- #
def test_prune_dead_removes_only_temp_rooted_dead(tmp_path):
    live_root, live_md = _live_project(tmp_path, "alive")
    dead_v_root, _ = _dead_volatile_project(tmp_path, "scratch-clone")
    dead_nv_root, _ = _dead_nonvolatile_entry("maybe-unmounted")

    r = R.prune_dead()
    assert r["ok"] is True
    assert [e["root"] for e in r["removed"]] == [dead_v_root]
    assert [e["root"] for e in r["kept_dead"]] == [dead_nv_root]

    left = json.load(open(R.projects_registry_path()))["projects"]
    assert os.path.realpath(live_root) in left  # live: untouched
    assert dead_nv_root in left  # non-volatile dead: an unmounted checkout comes back
    assert dead_v_root not in left

    # And the live entry still serves reads exactly as before.
    assert os.path.realpath(live_root) in R.registered_projects()


def test_prune_dead_is_a_noop_without_candidates(tmp_path):
    """No prunable rows -> the file is not rewritten (mtime/bytes identical), and a
    missing file stays missing — prune never manufactures registry state."""
    _live_project(tmp_path, "alive")
    _dead_nonvolatile_entry("maybe-unmounted")
    before = _registry_bytes()
    r = R.prune_dead()
    assert r["ok"] is True and r["removed"] == []
    assert _registry_bytes() == before

    os.remove(R.projects_registry_path())
    r = R.prune_dead()
    assert r["ok"] is True and r["removed"] == [] and r["kept_dead"] == []
    assert not os.path.exists(R.projects_registry_path())


def test_prune_dead_preserves_sibling_keys(tmp_path):
    """Whole-document RMW discipline (same as register/deregister): sibling keys a
    future schema adds must survive a prune rewrite."""
    _dead_volatile_project(tmp_path, "junk")
    path = R.projects_registry_path()
    doc = json.load(open(path))
    doc["schema_hint"] = {"v": 99}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)

    assert R.prune_dead()["ok"] is True
    after = json.load(open(path))
    assert after["schema_hint"] == {"v": 99}
    assert after["projects"] == {}


# --------------------------------------------------------------------------- #
# The CLI — report (default, read-only), --prune-dead, --drop, --json.
# --------------------------------------------------------------------------- #
def test_cli_report_is_read_only_and_names_the_verbs(tmp_path, capsys):
    _live_project(tmp_path, "alive")
    _dead_volatile_project(tmp_path, "scratch-clone")
    _dead_nonvolatile_entry("maybe-unmounted")
    before = _registry_bytes()

    assert R.main([]) == 0
    assert _registry_bytes() == before
    out = capsys.readouterr().out
    assert "3 entries: 1 live, 2 dead" in out
    # Every hygiene verb is named so the report is actionable without docs.
    assert "--prune-dead" in out and "--drop" in out
    # The one honesty line prune's restraint depends on: non-volatile dead entries
    # are NAMED as kept, with the per-item route.
    assert "maybe-unmounted" in out


def test_cli_report_names_the_repair_route(tmp_path, capsys):
    root = tmp_path / "realproj"
    (root / ".claude" / "memory").mkdir(parents=True)
    gone = tmp_path / "gone" / "memory"
    gone.mkdir(parents=True)
    assert R.register_project(str(root), str(gone)) is True
    shutil.rmtree(gone)

    assert R.main([]) == 0
    out = capsys.readouterr().out
    assert "/hippo:init" in out  # the re-register route rides the report


def test_cli_report_empty_norm(tmp_path, capsys):
    assert R.main([]) == 0
    out = capsys.readouterr().out
    assert "nothing registered" in out


def test_cli_json_report(tmp_path, capsys):
    _dead_volatile_project(tmp_path, "junk")
    assert R.main(["--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["path"] == R.projects_registry_path()
    assert len(doc["entries"]) == 1 and doc["entries"][0]["live"] is False


def test_cli_prune_dead_prints_each_removal(tmp_path, capsys):
    dead_v_root, _ = _dead_volatile_project(tmp_path, "scratch-clone")
    dead_nv_root, _ = _dead_nonvolatile_entry("maybe-unmounted")

    assert R.main(["--prune-dead"]) == 0
    out = capsys.readouterr().out
    assert dead_v_root in out and "removed" in out
    assert dead_nv_root in out and "kept" in out  # named, with the --drop route

    # Second run: nothing left to prune, and it says so.
    assert R.main(["--prune-dead"]) == 0
    assert "nothing to prune" in capsys.readouterr().out


def test_cli_drop_is_per_item_and_honest_about_absence(tmp_path, capsys):
    live_root, _ = _live_project(tmp_path, "alive")
    other_root, _ = _live_project(tmp_path, "other")

    assert R.main(["--drop", live_root]) == 0
    out = capsys.readouterr().out
    assert "dropped" in out and live_root in out
    left = json.load(open(R.projects_registry_path()))["projects"]
    assert os.path.realpath(live_root) not in left
    assert os.path.realpath(other_root) in left  # exactly one entry went

    assert R.main(["--drop", live_root]) == 0
    assert "was not registered" in capsys.readouterr().out


def test_cli_json_only_shapes_the_report(tmp_path, capsys):
    """--json with an action flag is refused loudly — a machine consumer must never
    mistake a mutation's chatter for the census document."""
    with pytest.raises(SystemExit):
        R.main(["--json", "--prune-dead"])


# --------------------------------------------------------------------------- #
# The doctor check — machine-level, warn-only, names the count and the verbs.
# --------------------------------------------------------------------------- #
def _doctor_ctx(tmp_path):
    from memory.doctor import DoctorContext

    md = tmp_path / "doctor-corpus"
    md.mkdir(parents=True, exist_ok=True)
    return DoctorContext(str(md), str(tmp_path))


def test_doctor_check_warns_with_count_and_commands(tmp_path):
    from memory.doctor import check_projects_registry

    _live_project(tmp_path, "alive")
    _dead_volatile_project(tmp_path, "junk-a")
    _dead_volatile_project(tmp_path, "junk-b")

    r = check_projects_registry(_doctor_ctx(tmp_path))
    assert r["status"] == "warn"
    assert "2 dead" in r["message"]
    assert "python -m memory.registry" in r["message"]
    assert "--prune-dead" in r["message"]


def test_doctor_check_ok_when_clean_or_empty(tmp_path):
    from memory.doctor import check_projects_registry

    r = check_projects_registry(_doctor_ctx(tmp_path))
    assert r["status"] == "ok" and "nothing registered" in r["message"]

    _live_project(tmp_path, "alive")
    r = check_projects_registry(_doctor_ctx(tmp_path))
    assert r["status"] == "ok" and "none dead" in r["message"]


def test_doctor_check_names_the_repair_route(tmp_path):
    from memory.doctor import check_projects_registry

    root = tmp_path / "realproj"
    (root / ".claude" / "memory").mkdir(parents=True)
    gone = tmp_path / "gone" / "memory"
    gone.mkdir(parents=True)
    assert R.register_project(str(root), str(gone)) is True
    shutil.rmtree(gone)

    r = check_projects_registry(_doctor_ctx(tmp_path))
    assert r["status"] == "warn"
    assert "/hippo:init" in r["message"]


def test_doctor_check_is_registered():
    from memory import doctor as D

    assert "projects_registry" in [label for label, _ in D.CHECKS]


# --------------------------------------------------------------------------- #
# Prevention — init warns when a temp-rooted corpus registers into the REAL
# machine registry (the exact class that produced the junk rows).
# --------------------------------------------------------------------------- #
def _run_init(tmp_path, monkeypatch):
    from memory.init_project import init_project

    proj = tmp_path / "proj"
    md = proj / ".claude" / "memory"
    md.mkdir(parents=True)
    monkeypatch.chdir(proj)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", str(md))
    return init_project(claude_projects_dir=str(tmp_path / "claude-projects"))


def test_init_warns_on_volatile_registration_into_real_registry(tmp_path, monkeypatch):
    # Simulate "no hermetic override": the registry resolves via HOME, which we point
    # into the sandbox so the warning path runs against a REAL-shaped (but fake) file.
    monkeypatch.delenv("HIPPO_PROJECTS_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    result = _run_init(tmp_path, monkeypatch)
    assert result["registered"] is True
    assert any("temp root" in w and "HIPPO_PROJECTS_FILE" in w for w in result["warnings"])


def test_init_stays_quiet_when_registry_is_isolated(tmp_path, monkeypatch):
    # conftest already sets HIPPO_PROJECTS_FILE -> the registration is hermetic and
    # the warning would be pure noise. The registry file itself is ephemeral here.
    result = _run_init(tmp_path, monkeypatch)
    assert result["registered"] is True
    assert not any("temp root" in w for w in result["warnings"])
