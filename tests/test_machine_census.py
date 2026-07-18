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
