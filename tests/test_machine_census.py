"""HYG-1/HYG-2: the machine-state census + the one genuinely-new remedy.

Round 4's machine-state lifecycle tier: ``machine_census.py`` composes four classes —
projects rows (DELEGATED to ``registry.registry_census``), the ``~/.claude/projects``
memory-symlink farm (ok / dangling / dangling-temp-rooted), trust rows (REPORT-ONLY
pending Q2), and scheduler artifacts (file oracles only) — into one deterministic
report mirroring ``registry.main``'s shape. Everything here flows through hermetic
overrides (``HIPPO_CLAUDE_PROJECTS_DIR``, ``HIPPO_TRUST_FILE``, ``HIPPO_PROJECTS_FILE``,
injected LaunchAgents dir / crontab text): nothing touches the runner's real machine
state — which is itself the HYG-2 lesson (the dangling-symlink class the census reports
was 100% minted by tests that lacked exactly this isolation).
"""

from __future__ import annotations

import json
import os
import plistlib

import pytest

from memory import machine_census as MC
from memory import registry as R
from memory import trust as T


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _farm(tmp_path):
    """A hermetic claude-projects dir; returns its path (str)."""
    root = tmp_path / "claude-projects"
    root.mkdir(exist_ok=True)
    return str(root)


def _add_symlink(farm: str, name: str, target: str):
    d = os.path.join(farm, name)
    os.makedirs(d, exist_ok=True)
    os.symlink(target, os.path.join(d, "memory"))
    return os.path.join(d, "memory")


def _plist(la_dir, key: str, repo: str, plugin: str, python: str):
    os.makedirs(la_dir, exist_ok=True)
    cmd = f"cd {repo} && PYTHONPATH={plugin} {python} -m memory.sleep >> /dev/null 2>&1"
    path = os.path.join(la_dir, f"com.hippo.sleep.{key}.plist")
    with open(path, "wb") as fh:
        plistlib.dump(
            {
                "Label": f"com.hippo.sleep.{key}",
                "ProgramArguments": ["/bin/sh", "-c", cmd],
            },
            fh,
        )
    return path


# --------------------------------------------------------------------------- #
# Class (a): DELEGATION — no second projects-census path exists (the inv5 pin)
# --------------------------------------------------------------------------- #
def test_projects_class_is_registry_census_verbatim(tmp_path, monkeypatch):
    sentinel = {"path": "SENTINEL", "entries": [], "live": 0, "dead": 0}
    monkeypatch.setattr(MC, "registry_census", lambda: sentinel)
    census = MC.machine_census(
        claude_projects_dir=_farm(tmp_path), launch_agents_dir=str(tmp_path / "la"),
        crontab_text="",
    )
    assert census["projects"] is sentinel


def test_no_second_projects_census_path_in_source():
    """The census DELEGATES (inv5): machine_census.py must never read the projects
    file itself — registry.registry_census is the one projects walk."""
    import inspect

    src = inspect.getsource(MC)
    assert "registry_census" in src
    assert "projects_registry_path" not in src
    assert "hippo-projects.json" not in src


# --------------------------------------------------------------------------- #
# Class (b): the symlink farm
# --------------------------------------------------------------------------- #
def test_symlink_farm_classifies_ok_dangling_and_temp_rooted(tmp_path):
    farm = _farm(tmp_path)
    live_target = tmp_path / "live-corpus" / ".claude" / "memory"
    live_target.mkdir(parents=True)
    _add_symlink(farm, "-Users-x-live", str(live_target))
    _add_symlink(farm, "-tmp-gone", str(tmp_path / "gone" / ".claude" / "memory"))
    _add_symlink(farm, "-Volumes-gone", "/nonexistent-hyg1-volume/.claude/memory")

    out = MC.symlink_farm_census(farm)
    assert (out["ok"], out["dangling_temp_rooted"], out["dangling"]) == (1, 1, 1)
    by_status = {e["status"]: e for e in out["entries"]}
    # tmp_path sits under the pytest tmp root — a volatile root — so its dead target
    # is the mechanically-safe batch class; the /nonexistent-… one could be an
    # unmounted volume and must stay per-item.
    assert by_status["dangling-temp-rooted"]["target"].endswith("gone/.claude/memory")
    assert by_status["dangling"]["target"] == "/nonexistent-hyg1-volume/.claude/memory"


def test_symlink_farm_skips_non_symlink_shapes(tmp_path):
    """A real ``memory`` dir (native-memory shape) or file is not hippo's to report."""
    farm = _farm(tmp_path)
    native = os.path.join(farm, "-native-project", "memory")
    os.makedirs(native)  # a real dir, not a symlink
    plain = os.path.join(farm, "-plain-file")
    os.makedirs(plain)
    with open(os.path.join(plain, "memory"), "w", encoding="utf-8") as fh:
        fh.write("not a symlink")
    os.makedirs(os.path.join(farm, "-no-memory-entry"))

    out = MC.symlink_farm_census(farm)
    assert out["entries"] == []


def test_symlink_farm_counts_the_pytest_leak_and_report_labels_it(tmp_path):
    farm = _farm(tmp_path)
    _add_symlink(
        farm,
        "-leaked",
        str(tmp_path / "pytest-of-tester" / "pytest-1" / "proj" / ".claude" / "memory"),
    )
    out = MC.symlink_farm_census(farm)
    assert out["pytest_leaked"] == 1
    text = "\n".join(MC._render_symlinks(out))
    assert "self-inflicted test leak" in text and "HYG-2" in text


def test_symlink_farm_missing_root_is_empty(tmp_path):
    out = MC.symlink_farm_census(str(tmp_path / "does-not-exist"))
    assert out["entries"] == [] and out["ok"] == 0


def test_claude_projects_root_honors_the_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_CLAUDE_PROJECTS_DIR", str(tmp_path / "override"))
    assert MC.claude_projects_root() == str(tmp_path / "override")


# --------------------------------------------------------------------------- #
# Class (c): trust rows — REPORT-ONLY pending Q2
# --------------------------------------------------------------------------- #
def _trust_rows(tmp_path):
    live_root = tmp_path / "live-repo"
    (live_root / ".claude" / "memory").mkdir(parents=True)
    assert T.mark_trusted(str(live_root), memory_dir=str(live_root / ".claude" / "memory"), origin="init")
    dead_volatile = str(tmp_path / "gone-repo")
    assert T.mark_trusted(dead_volatile, origin="review")
    dead_kept = "/nonexistent-hyg1-trust/repo"
    assert T.mark_trusted(dead_kept)
    return str(live_root), dead_volatile, dead_kept


def test_trust_census_classifies_rows(tmp_path):
    live_root, dead_volatile, dead_kept = _trust_rows(tmp_path)
    out = MC.trust_census()
    assert (out["live"], out["dead"]) == (1, 2)
    rows = {e["root"]: e for e in out["entries"]}
    live = rows[os.path.realpath(live_root)]
    assert live["live"] and live["fingerprinted"] and live["origin"] == "init"
    gone = rows[os.path.realpath(dead_volatile)]
    assert not gone["live"] and gone["volatile"] and not gone["fingerprinted"]
    kept = rows[os.path.realpath(dead_kept)]
    assert not kept["live"] and not kept["volatile"]


def test_trust_report_presents_q2_and_never_prescribes(tmp_path):
    _trust_rows(tmp_path)
    text = "\n".join(MC._render_trust(MC.trust_census()))
    assert "REPORT-ONLY pending owner decision Q2" in text
    assert "SEC-6 fingerprint baseline" in text
    assert "legacy — no baseline" in text and "possibly unmounted" in text


# --------------------------------------------------------------------------- #
# Class (d): scheduler artifacts — file oracles only
# --------------------------------------------------------------------------- #
def test_scheduler_census_launchd_ok_and_stale(tmp_path):
    la = str(tmp_path / "LaunchAgents")
    repo = tmp_path / "repo"
    plugin = repo / "plugin"
    plugin.mkdir(parents=True)
    python = repo / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n")
    _plist(la, "good", str(repo), str(plugin), str(python))
    _plist(la, "moved", "/nonexistent-hyg1-sched/repo", str(plugin), str(python))

    out = MC.scheduler_census(launch_agents_dir=la, crontab_text="")
    assert (out["ok"], out["stale"], out["unparseable"]) == (1, 1, 0)
    stale = [e for e in out["entries"] if e["status"] == "stale-path"][0]
    assert stale["missing"] == ["/nonexistent-hyg1-sched/repo"]
    assert stale["kind"] == "launchd"


def test_scheduler_census_unparseable_plist(tmp_path):
    la = tmp_path / "LaunchAgents"
    la.mkdir()
    (la / "com.hippo.sleep.bad.plist").write_text("not a plist at all")
    out = MC.scheduler_census(launch_agents_dir=str(la), crontab_text="")
    assert out["unparseable"] == 1


def test_scheduler_census_reads_cron_lines(tmp_path):
    repo = tmp_path / "repo"
    plugin = repo / "plugin"
    plugin.mkdir(parents=True)
    cron = (
        "# a comment\n"
        f"30 7 * * 1-5 cd {repo} && PYTHONPATH={plugin} python3 -m memory.sleep >> /dev/null 2>&1\n"
        "15 * * * * /usr/bin/true\n"
    )
    out = MC.scheduler_census(launch_agents_dir=str(tmp_path / "la"), crontab_text=cron)
    assert len(out["entries"]) == 1 and out["entries"][0]["kind"] == "cron"
    assert out["entries"][0]["status"] == "ok"


def test_scheduler_census_empty_when_nothing_installed(tmp_path):
    out = MC.scheduler_census(launch_agents_dir=str(tmp_path / "la"), crontab_text="")
    assert out["entries"] == []


# --------------------------------------------------------------------------- #
# The composed report
# --------------------------------------------------------------------------- #
def _hermetic_census(tmp_path, **kw):
    return MC.machine_census(
        claude_projects_dir=kw.pop("claude_projects_dir", _farm(tmp_path)),
        launch_agents_dir=kw.pop("launch_agents_dir", str(tmp_path / "la")),
        crontab_text=kw.pop("crontab_text", ""),
    )


def test_report_empty_norm_one_liner(tmp_path):
    text = MC.render_report(_hermetic_census(tmp_path))
    assert text.startswith("machine census: nothing to report")
    assert "\n" not in text.strip() or text.count("\n") == 0


def test_report_is_deterministic_and_covers_all_classes(tmp_path):
    farm = _farm(tmp_path)
    _add_symlink(farm, "-x-gone", str(tmp_path / "gone" / ".claude" / "memory"))
    _trust_rows(tmp_path)
    root = tmp_path / "reg-live"
    md = root / ".claude" / "memory"
    md.mkdir(parents=True)
    assert R.register_project(str(root), str(md))

    census = MC.machine_census(
        claude_projects_dir=farm,
        launch_agents_dir=str(tmp_path / "la"),
        crontab_text="",
    )
    first = MC.render_report(census)
    second = MC.render_report(
        MC.machine_census(
            claude_projects_dir=farm,
            launch_agents_dir=str(tmp_path / "la"),
            crontab_text="",
        )
    )
    assert first == second
    # every class section present; the projects section is registry's own rendering
    assert "projects registry:" in first
    assert "memory symlinks:" in first
    assert "trust registry (consent ledger):" in first
    assert "scheduler artifacts:" in first


def test_census_is_read_only(tmp_path):
    """Zero writes: the farm's shape and targets are byte-identical after a census."""
    farm = _farm(tmp_path)
    _add_symlink(farm, "-x-gone", str(tmp_path / "gone" / ".claude" / "memory"))

    def snapshot():
        out = []
        for dirpath, dirnames, filenames in sorted(os.walk(farm)):
            for n in sorted(dirnames + filenames):
                p = os.path.join(dirpath, n)
                out.append((p, os.readlink(p) if os.path.islink(p) else None))
        return out

    before = snapshot()
    MC.render_report(_hermetic_census(tmp_path, claude_projects_dir=farm))
    assert snapshot() == before


# --------------------------------------------------------------------------- #
# CLI (mirrors registry.main: report default, --json report-only)
# --------------------------------------------------------------------------- #
def _hermetic_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HIPPO_CLAUDE_PROJECTS_DIR", str(tmp_path / "claude-projects"))
    monkeypatch.setattr(MC, "_read_crontab", lambda: "")


def test_main_default_prints_report(tmp_path, monkeypatch, capsys):
    _hermetic_env(tmp_path, monkeypatch)
    assert MC.main([]) == 0
    out = capsys.readouterr().out
    assert "machine census: nothing to report" in out


def test_main_json_emits_all_four_classes(tmp_path, monkeypatch, capsys):
    _hermetic_env(tmp_path, monkeypatch)
    assert MC.main(["--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert set(doc) == {"projects", "symlinks", "trust", "scheduler"}


def test_main_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        MC.main(["--help"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("usage:")
