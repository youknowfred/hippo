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


def _plist(la_dir, key: str, repo: str, plugin: str, python: str, log: str = "/dev/null"):
    os.makedirs(la_dir, exist_ok=True)
    cmd = f"cd {repo} && PYTHONPATH={plugin} {python} -m memory.sleep >> {log} 2>&1"
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


def _sleep_state(repo, last_run_at=None, raw: str | None = None):
    """Plant a repo's sleep-state.json where the OPS-2 join resolves it — the same
    ``<repo>/.claude/.memory-telemetry`` shape ``telemetry.default_telemetry_dir``
    derives from ``<repo>/.claude/memory``."""
    td = repo / ".claude" / ".memory-telemetry"
    td.mkdir(parents=True, exist_ok=True)
    body = raw if raw is not None else json.dumps({"last_run_at": last_run_at})
    (td / "sleep-state.json").write_text(body, encoding="utf-8")


def _iso_ago(days: float) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


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
    _sleep_state(repo, _iso_ago(0.1))  # ran "this morning" — ok means genuinely ok post-OPS-2
    _plist(la, "good", str(repo), str(plugin), str(python))
    _plist(la, "moved", "/nonexistent-hyg1-sched/repo", str(plugin), str(python))

    out = MC.scheduler_census(launch_agents_dir=la, crontab_text="")
    assert (out["ok"], out["quiet"], out["stale"], out["unparseable"]) == (1, 0, 1, 0)
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
    _sleep_state(repo, _iso_ago(0.1))
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


# --------------------------------------------------------------------------- #
# HYG-2: the remover — batch confined to the mechanically-safe class
# --------------------------------------------------------------------------- #
def _mixed_farm(tmp_path):
    """ok + dangling-temp-rooted + dangling-kept + native shapes, in one farm."""
    farm = _farm(tmp_path)
    live_target = tmp_path / "live-corpus" / ".claude" / "memory"
    live_target.mkdir(parents=True)
    ok_link = _add_symlink(farm, "-live", str(live_target))
    gone_link = _add_symlink(farm, "-gone-tmp", str(tmp_path / "gone" / ".claude" / "memory"))
    kept_link = _add_symlink(farm, "-gone-volume", "/nonexistent-hyg2-volume/.claude/memory")
    native = os.path.join(farm, "-native", "memory")
    os.makedirs(native)
    return farm, ok_link, gone_link, kept_link, native


def test_prune_dangling_removes_only_the_temp_rooted_batch(tmp_path):
    farm, ok_link, gone_link, kept_link, native = _mixed_farm(tmp_path)
    result = MC.prune_dangling(farm)
    assert [e["link"] for e in result["removed"]] == [gone_link]
    assert [e["link"] for e in result["kept_dead"]] == [kept_link]
    assert result["failed"] == []
    assert not os.path.lexists(gone_link)  # the one removal
    assert os.path.islink(ok_link) and os.path.islink(kept_link)  # untouched
    assert os.path.isdir(native)  # native-memory shape never touched
    # the harness-owned parent dir survives — only the symlink itself is hippo's
    assert os.path.isdir(os.path.dirname(gone_link))


def test_prune_dangling_is_idempotent_and_empty_norm(tmp_path):
    farm = _farm(tmp_path)
    result = MC.prune_dangling(farm)
    assert result == {"removed": [], "kept_dead": [], "failed": []}


def test_main_prune_dangling_prints_the_grain(tmp_path, monkeypatch, capsys):
    farm, _ok, gone_link, kept_link, _native = _mixed_farm(tmp_path)
    monkeypatch.setenv("HIPPO_CLAUDE_PROJECTS_DIR", farm)
    monkeypatch.setattr(MC, "_read_crontab", lambda: "")
    assert MC.main(["--prune-dangling"]) == 0
    out = capsys.readouterr().out
    assert f"removed: {gone_link}" in out
    assert "kept (target not temp-rooted — possibly an unmounted volume)" in out
    assert kept_link in out
    # second run: the batch class is drained — the empty norm names it
    assert MC.main(["--prune-dangling"]) == 0
    assert "nothing to prune" in capsys.readouterr().out


def test_main_json_cannot_ride_the_mutation(capsys):
    with pytest.raises(SystemExit) as exc:
        MC.main(["--json", "--prune-dangling"])
    assert exc.value.code == 2


def test_report_names_the_drain_flag_only_when_the_batch_exists(tmp_path):
    farm = _farm(tmp_path)
    _add_symlink(farm, "-gone-volume", "/nonexistent-hyg2-volume/.claude/memory")
    text = "\n".join(MC._render_symlinks(MC.symlink_farm_census(farm)))
    assert "--prune-dangling" not in text  # kept-class only: no batch to drain
    _add_symlink(farm, "-gone-tmp", str(tmp_path / "gone" / ".claude" / "memory"))
    text = "\n".join(MC._render_symlinks(MC.symlink_farm_census(farm)))
    assert "python -m memory.machine_census --prune-dangling" in text


def test_report_prints_scheduler_removal_recipe_for_stale_only(tmp_path):
    la = str(tmp_path / "LaunchAgents")
    repo = tmp_path / "repo"
    plugin = repo / "plugin"
    plugin.mkdir(parents=True)
    python = repo / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n")
    good = _plist(la, "good", str(repo), str(plugin), str(python))
    stale = _plist(la, "moved", "/nonexistent-hyg2-sched/repo", str(plugin), str(python))
    text = "\n".join(
        MC._render_scheduler(MC.scheduler_census(launch_agents_dir=la, crontab_text=""))
    )
    assert f"launchctl unload {stale} && rm {stale}" in text
    assert f"launchctl unload {good}" not in text
    assert "print-only — hippo never uninstalls system state" in text


# --------------------------------------------------------------------------- #
# HYG-2: the leak fix — the faucet, not just the drain
# --------------------------------------------------------------------------- #
def test_init_with_no_explicit_dir_lands_under_the_conftest_guard(tmp_path, monkeypatch):
    """The live offender shape (test_mcp_setup_tools.py:578,588 at 81177ba): a REAL
    init with claude_projects_dir=None. The conftest HIPPO_CLAUDE_PROJECTS_DIR guard +
    init_project's claude_projects_root() resolution must land the symlink in tmp —
    never the runner's real ~/.claude/projects."""
    from memory.init_project import init_project

    proj = tmp_path / "leaky-proj"
    (proj / ".claude" / "memory").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.delenv("HIPPO_MEMORY_DIR", raising=False)

    r = init_project()  # the leak shape: no explicit claude_projects_dir
    link = r["symlink"]["expected_path"]
    guard = os.environ["HIPPO_CLAUDE_PROJECTS_DIR"]
    assert link.startswith(guard + os.sep), (
        f"init wrote {link} — outside the HIPPO_CLAUDE_PROJECTS_DIR guard {guard}; "
        "the HYG-2 faucet fix regressed"
    )
    assert r["symlink"]["status"] in ("created", "already_correct")
    assert os.path.islink(link)


def test_conftest_guard_is_set_for_every_test():
    """The autouse fixture is the class fix — its absence would re-open the faucet."""
    assert "claude-projects-guard" in os.environ.get("HIPPO_CLAUDE_PROJECTS_DIR", "")


# --------------------------------------------------------------------------- #
# HYG-3: the ONE doctor line — warn on DEAD only; sleep inherits free
# --------------------------------------------------------------------------- #
def _quiet_scheduler(monkeypatch, stale: int = 0):
    monkeypatch.setattr(
        MC,
        "scheduler_census",
        lambda *a, **k: {"entries": [], "ok": 0, "stale": stale, "unparseable": 0},
    )


def test_doctor_registers_machine_state_before_the_pinned_last_check():
    from memory import doctor as D

    labels = [label for label, _ in D.CHECKS]
    assert labels[-1] == "stale_memobot_env"  # the pinned-last check holds
    # HYG-3 and PUB-3 both append at the same insertion point (the tier note called
    # it): machine_state landed first, subset_boundary after it, the pin still last.
    assert labels[-3:] == ["machine_state", "subset_boundary", "stale_memobot_env"]


def test_machine_state_ok_names_the_census_command(tmp_path, monkeypatch):
    from memory.doctor_checks_env import check_machine_state

    _quiet_scheduler(monkeypatch)
    r = check_machine_state(None)
    assert r["status"] == "ok"
    assert "python -m memory.machine_census" in r["message"]


def test_machine_state_warns_on_dangling_and_names_the_drain(tmp_path, monkeypatch):
    from memory.doctor_checks_env import check_machine_state

    farm = _farm(tmp_path)
    _add_symlink(farm, "-gone", str(tmp_path / "gone" / ".claude" / "memory"))
    monkeypatch.setenv("HIPPO_CLAUDE_PROJECTS_DIR", farm)
    _quiet_scheduler(monkeypatch)
    r = check_machine_state(None)
    assert r["status"] == "warn"
    assert "1 dangling memory symlink" in r["message"]
    assert "--prune-dangling" in r["message"]  # the batch class exists, so the drain is named


def test_machine_state_warns_on_dead_trust_rows(tmp_path, monkeypatch):
    from memory.doctor_checks_env import check_machine_state

    assert T.mark_trusted("/nonexistent-hyg3-trust/repo")
    _quiet_scheduler(monkeypatch)
    r = check_machine_state(None)
    assert r["status"] == "warn"
    assert "1 dead trust row" in r["message"]
    assert "python -m memory.machine_census" in r["message"]


def test_machine_state_warns_on_stale_scheduler_artifacts(tmp_path, monkeypatch):
    from memory.doctor_checks_env import check_machine_state

    _quiet_scheduler(monkeypatch, stale=1)
    r = check_machine_state(None)
    assert r["status"] == "warn"
    assert "1 stale scheduler artifact" in r["message"]


def test_machine_state_temp_rooted_live_never_warns(tmp_path, monkeypatch):
    """Warn on DEAD only: live-but-volatile rows (this repo's normal state) and ok
    symlinks must render ok — the volatile split belongs to the census's own report."""
    from memory.doctor_checks_env import check_machine_state

    farm = _farm(tmp_path)
    live_target = tmp_path / "live" / ".claude" / "memory"
    live_target.mkdir(parents=True)
    _add_symlink(farm, "-live", str(live_target))  # ok AND volatile (pytest tmp)
    monkeypatch.setenv("HIPPO_CLAUDE_PROJECTS_DIR", farm)
    live_repo = tmp_path / "live-trust-repo"
    live_repo.mkdir()
    assert T.mark_trusted(str(live_repo))  # live AND volatile trust row
    _quiet_scheduler(monkeypatch)
    r = check_machine_state(None)
    assert r["status"] == "ok", r["message"]


# --------------------------------------------------------------------------- #
# OPS-2: the dead-man freshness join — "quiet" from the oracle that already existed
# --------------------------------------------------------------------------- #
def _ok_repo(tmp_path):
    """A repo whose scheduler artifact classifies ok (all embedded paths exist)."""
    repo = tmp_path / "repo"
    plugin = repo / "plugin"
    plugin.mkdir(parents=True)
    python = repo / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n")
    return repo, plugin, python


def test_quiet_when_no_run_was_ever_recorded(tmp_path):
    """An installed schedule with no sleep-state at all IS the dead-man alarm shape."""
    la = str(tmp_path / "LaunchAgents")
    repo, plugin, python = _ok_repo(tmp_path)
    _plist(la, "silent", str(repo), str(plugin), str(python))
    out = MC.scheduler_census(launch_agents_dir=la, crontab_text="")
    assert (out["ok"], out["quiet"], out["stale"]) == (0, 1, 0)
    e = [x for x in out["entries"] if x["status"] == "quiet"][0]
    assert e["last_run_at"] is None and e["age_days"] is None


def test_quiet_when_the_last_run_is_beyond_the_window(tmp_path):
    la = str(tmp_path / "LaunchAgents")
    repo, plugin, python = _ok_repo(tmp_path)
    stamp = _iso_ago(10)
    _sleep_state(repo, stamp)
    _plist(la, "dying", str(repo), str(plugin), str(python))
    out = MC.scheduler_census(launch_agents_dir=la, crontab_text="")
    assert out["quiet"] == 1 and out["ok"] == 0
    e = [x for x in out["entries"] if x["status"] == "quiet"][0]
    assert e["last_run_at"] == stamp
    assert e["age_days"] == 10


def test_fresh_and_within_window_stay_ok(tmp_path):
    """The healthy case AND the boundary: a 2-day-old stamp (weekend gap) is not quiet."""
    la = str(tmp_path / "LaunchAgents")
    repo, plugin, python = _ok_repo(tmp_path)
    _sleep_state(repo, _iso_ago(2))
    _plist(la, "healthy", str(repo), str(plugin), str(python))
    out = MC.scheduler_census(launch_agents_dir=la, crontab_text="")
    assert (out["ok"], out["quiet"]) == (1, 0)
    assert out["entries"][0]["status"] == "ok"
    assert "last_run_at" not in out["entries"][0]  # the fields ride only the quiet flip


def test_unparseable_stamp_is_quiet(tmp_path):
    la = str(tmp_path / "LaunchAgents")
    repo, plugin, python = _ok_repo(tmp_path)
    _sleep_state(repo, "not-a-timestamp")
    _plist(la, "garbled", str(repo), str(plugin), str(python))
    out = MC.scheduler_census(launch_agents_dir=la, crontab_text="")
    e = [x for x in out["entries"] if x["status"] == "quiet"][0]
    assert e["last_run_at"] == "not-a-timestamp" and e["age_days"] is None


def test_stale_path_classification_is_unchanged_by_the_join(tmp_path):
    """AC: the join runs only over otherwise-ok artifacts — a stale-path entry stays
    stale-path even when its (gone) repo obviously has no oracle."""
    la = str(tmp_path / "LaunchAgents")
    repo, plugin, python = _ok_repo(tmp_path)
    _plist(la, "moved", "/nonexistent-ops2-sched/repo", str(plugin), str(python))
    out = MC.scheduler_census(launch_agents_dir=la, crontab_text="")
    assert (out["quiet"], out["stale"]) == (0, 1)


def test_ok_without_a_parsed_repo_root_stays_ok(tmp_path):
    """No cd-<repo> in the command -> no oracle to resolve -> never quiet (honest:
    the join claims nothing it cannot read)."""
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    cron = f"30 7 * * * PYTHONPATH={plugin} python3 -m memory.sleep >> /dev/null 2>&1\n"
    out = MC.scheduler_census(launch_agents_dir=str(tmp_path / "la"), crontab_text=cron)
    assert (out["ok"], out["quiet"]) == (1, 0)


def test_absence_of_any_schedule_emits_nothing(tmp_path):
    """AC: no artifacts -> no quiet, no entries, and the render's empty one-liner."""
    out = MC.scheduler_census(launch_agents_dir=str(tmp_path / "la"), crontab_text="")
    assert out["entries"] == [] and out["quiet"] == 0
    assert MC._render_scheduler(out) == [
        "scheduler artifacts: none installed (launchd glob + crontab)."
    ]


def test_render_names_the_age_and_the_log_path(tmp_path):
    la = str(tmp_path / "LaunchAgents")
    repo, plugin, python = _ok_repo(tmp_path)
    stamp = _iso_ago(10)
    _sleep_state(repo, stamp)
    log = str(tmp_path / "logs" / "hippo-sleep.log")
    path = _plist(la, "dying", str(repo), str(plugin), str(python), log=log)
    text = "\n".join(
        MC._render_scheduler(MC.scheduler_census(launch_agents_dir=la, crontab_text=""))
    )
    assert "1 quiet" in text
    assert f"quiet [launchd]: {path}" in text
    assert f"last recorded sleep run 10d ago ({stamp})" in text
    assert f"check the log: {log}" in text
    assert f"dead-man window: {MC._QUIET_AFTER_DAYS}d" in text
    # no recipe, no prescription — remediation stays the human's (ED4R-3 posture)
    assert f"launchctl unload {path}" not in text


def test_render_quiet_without_any_recorded_run(tmp_path):
    la = str(tmp_path / "LaunchAgents")
    repo, plugin, python = _ok_repo(tmp_path)
    _plist(la, "silent", str(repo), str(plugin), str(python))
    text = "\n".join(
        MC._render_scheduler(MC.scheduler_census(launch_agents_dir=la, crontab_text=""))
    )
    assert "no recorded sleep run" in text


def test_check_machine_state_gains_the_quiet_count(monkeypatch):
    from memory.doctor_checks_env import check_machine_state

    monkeypatch.setattr(
        MC,
        "scheduler_census",
        lambda *a, **k: {"entries": [], "ok": 0, "stale": 0, "unparseable": 0, "quiet": 2},
    )
    r = check_machine_state(None)
    assert r["status"] == "warn"
    assert "2 quiet scheduler artifacts (no recent sleep run)" in r["message"]
    assert "python -m memory.machine_census" in r["message"]


def test_check_machine_state_tolerates_the_pre_quiet_census_shape(monkeypatch):
    """The .get() discipline (ED-4's own-shape read): a census dict without the quiet
    key — the pre-OPS-2 shape the older tests still monkeypatch — reads as 0."""
    from memory.doctor_checks_env import check_machine_state

    _quiet_scheduler(monkeypatch)  # the legacy helper: no "quiet" key at all
    r = check_machine_state(None)
    assert r["status"] == "ok", r["message"]
    assert "stale/quiet scheduler artifacts" in r["message"]


def test_census_path_never_probes_launchctl_or_grows_subprocess(tmp_path):
    """ED-3 negative-capability pin: the ONLY subprocess site in machine_census is
    _read_crontab (the grandfathered crontab -l file-oracle-in-spirit), and the
    freshness join goes through the two canonical file resolvers — no launchctl
    anywhere but the print-only removal recipe."""
    import ast
    import inspect

    src = inspect.getsource(MC)
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Attribute)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == "subprocess"
                    and node.name != "_read_crontab"
                ):
                    offenders.append(node.name)
    assert not offenders, f"subprocess use outside _read_crontab: {offenders}"
    join_src = inspect.getsource(MC._sleep_freshness)
    assert "subprocess" not in join_src and "launchctl" not in join_src
    # the join resolves ONLY through hippo's own canonical readers (AC: cross-repo
    # reads touch only hippo's own gitignored telemetry)
    assert "default_telemetry_dir" in join_src and "_read_state" in join_src
    assert "open(" not in join_src


# --------------------------------------------------------------------------- #
# BND-2: trust drift joins the census — WITHHOLDING rows (report-only)
# --------------------------------------------------------------------------- #
def _fingerprinted_corpus(tmp_path, name="drift-repo"):
    """A live trusted+fingerprinted corpus with one consented memory; returns
    ``(root_str, memory_dir_path)``. Drift is then minted by editing/adding files
    AFTER the consent — the exact shape the live exhibits had."""
    root = tmp_path / name
    md = root / ".claude" / "memory"
    md.mkdir(parents=True)
    (md / "consented-fact.md").write_text(
        "---\nname: consented-fact\n---\nbody\n", encoding="utf-8"
    )
    assert T.mark_trusted(str(root), memory_dir=str(md), origin="review")
    return str(root), md


def test_trust_census_reports_withholding_for_drifted_live_rows(tmp_path, monkeypatch):
    """AC: the alloy twin — authored-but-unfolded stems render as withheld with the
    re-consent route named. 0 changed / 3 added mirrors the live exhibit that sat
    machine-invisible 4-5 days."""
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)  # the conftest CI bypass suppresses quarantine
    root, md = _fingerprinted_corpus(tmp_path)
    for stem in ("httpx-twin", "icns-twin", "p7-twin"):
        (md / f"{stem}.md").write_text(f"---\nname: {stem}\n---\nnew\n", encoding="utf-8")
    out = MC.trust_census()
    row = {e["root"]: e for e in out["entries"]}[os.path.realpath(root)]
    assert row["withholding"] == {"changed": 0, "added": 3}
    assert out["withholding"] == 1
    text = "\n".join(MC._render_trust(out))
    assert (
        "WITHHOLDING 0 changed / 3 added — re-consent in that project "
        "(trust_corpus / doctor)" in text
    )


def test_trust_census_counts_changed_and_added_separately(tmp_path, monkeypatch):
    """The hippo-shape twin: an edited consented file counts changed, a new file
    counts added — the two overloads of drift stay legible as separate numbers."""
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    root, md = _fingerprinted_corpus(tmp_path)
    (md / "consented-fact.md").write_text(
        "---\nname: consented-fact\n---\nedited\n", encoding="utf-8"
    )
    (md / "new-stem.md").write_text("---\nname: new-stem\n---\nnew\n", encoding="utf-8")
    row = {e["root"]: e for e in MC.trust_census()["entries"]}[os.path.realpath(root)]
    assert row["withholding"] == {"changed": 1, "added": 1}


def test_trust_census_zero_drift_renders_byte_identical(tmp_path, monkeypatch):
    """AC byte-identity pin: zero-drift fleets render exactly as today — no
    ``withholding`` key on any row (ED-4 absence-emits-nothing), summary count 0,
    no WITHHOLDING line. Composes with the untouched pre-BND-2 render tests above,
    which pin the row lines' exact vocabulary. The CI bypass is cleared so the
    clean-row path is proven live, not vacuously suppressed."""
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    _trust_rows(tmp_path)  # one live+fingerprinted (clean) row + two dead rows
    out = MC.trust_census()
    assert out["withholding"] == 0
    assert all("withholding" not in e for e in out["entries"])
    text = "\n".join(MC._render_trust(out))
    assert "WITHHOLDING" not in text


def test_trust_census_skips_dead_and_legacy_rows(tmp_path, monkeypatch):
    """AC: rows without a fingerprint or not live are skipped — their existing
    states already say why. A drifty legacy corpus mints NO withholding row."""
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    _trust_rows(tmp_path)
    legacy = tmp_path / "legacy-repo"
    lmd = legacy / ".claude" / "memory"
    lmd.mkdir(parents=True)
    assert T.mark_trusted(str(legacy))  # no memory_dir -> fingerprint-less record
    (lmd / "anything.md").write_text("---\nname: anything\n---\nx\n", encoding="utf-8")
    out = MC.trust_census()
    assert out["withholding"] == 0
    assert all("withholding" not in e for e in out["entries"])


def test_census_drift_uses_the_one_shipped_detector_only():
    """inv5 negative-capability pin (AC): drift is computed ONLY via
    ``trust.untrusted_changes`` — a second fingerprint differ in the census would be
    a defect. The forbidden names are the primitives a re-implementation would need
    (same token-scan pattern as the projects-census delegation pin)."""
    import inspect

    src = inspect.getsource(MC)
    assert "untrusted_changes" in src
    for forbidden in ("corpus_fingerprint", "consented_hashes", "file_sha256", "hashlib"):
        assert forbidden not in src, f"second-differ primitive in census source: {forbidden}"


def test_census_withholding_is_report_only():
    """AC: no re-consent, no untrust, no write of any kind from the census path —
    the remedy text names the in-project route and the human runs it."""
    import inspect

    src = inspect.getsource(MC)
    assert "mark_trusted" not in src
    assert "untrust(" not in src  # the verb CALL; untrusted_changes is the detector read
    assert "_write_registry" not in src
    assert "re-consent in that project (trust_corpus / doctor)" in src


def test_check_machine_state_gains_the_withholding_clause(monkeypatch):
    """AC: doctor's machine-state one-liner carries the withholding count — plural
    and singular forms — and still names the census command."""
    from memory.doctor_checks_env import check_machine_state

    _quiet_scheduler(monkeypatch)
    monkeypatch.setattr(
        MC, "trust_census", lambda: {"entries": [], "live": 2, "dead": 0, "withholding": 2}
    )
    r = check_machine_state(None)
    assert r["status"] == "warn"
    assert "2 withholding corpora (trust drift — re-consent in that project)" in r["message"]
    assert "python -m memory.machine_census" in r["message"]
    monkeypatch.setattr(
        MC, "trust_census", lambda: {"entries": [], "live": 1, "dead": 0, "withholding": 1}
    )
    r = check_machine_state(None)
    assert "1 withholding corpus (trust drift — re-consent in that project)" in r["message"]


def test_check_machine_state_tolerates_the_pre_withholding_census_shape(monkeypatch):
    """ED-4 own-shape read: a trust census without the ``withholding`` key — the
    pre-BND-2 shape older tests still monkeypatch — reads as 0 and the ok message
    stays byte-identical (the zero-drift doctor pin)."""
    from memory.doctor_checks_env import check_machine_state

    _quiet_scheduler(monkeypatch)
    monkeypatch.setattr(MC, "trust_census", lambda: {"entries": [], "live": 1, "dead": 0})
    r = check_machine_state(None)
    assert r["status"] == "ok", r["message"]
    assert (
        "machine state: no dead trust rows, dangling memory symlinks, "
        "or stale/quiet scheduler artifacts" in r["message"]
    )
