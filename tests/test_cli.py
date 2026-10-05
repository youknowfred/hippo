"""SRF-1: `hippo <verb>` is the one engine entry.

The door (bin/hippo → memory.cli) must run each verb's module exactly as
`python -m memory.<module>` did, the skills and hooks must reach the engine only through
it, and a Bash-tool shell with no plugin env must still find the plugin's venv.
"""

from __future__ import annotations

import glob
import importlib.util
import os
import re
import shutil
import subprocess
import sys

import pytest

from memory import cli
from memory.cli_verbs import CLI_VERBS, SKILL_REDIRECTS

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_PLUGIN = os.path.join(_REPO, "plugin")
_HIPPO_BIN = os.path.join(_PLUGIN, "bin", "hippo")


def test_every_verb_names_a_runnable_module():
    for row in CLI_VERBS:
        spec = importlib.util.find_spec(f"memory.{row.module}")
        assert spec is not None and spec.origin, row
        with open(spec.origin, encoding="utf-8") as fh:
            assert 'if __name__ == "__main__":' in fh.read(), f"{row.module} has no __main__ entry"


def test_verbs_are_unique_and_kebab_case():
    verbs = [r.verb for r in CLI_VERBS]
    assert len(verbs) == len(set(verbs))
    assert all(re.fullmatch(r"[a-z][a-z-]*", v) for v in verbs)
    assert not set(verbs) & set(SKILL_REDIRECTS)


def test_the_frozen_verbs_still_run_what_they_always_ran():
    frozen = {r.verb: r.module for r in CLI_VERBS if r.frozen}
    assert frozen == {
        "recall": "recall_hook",
        "new": "new_memory",
        "build-index": "build_index",
        "staleness": "staleness",
        "mcp": "mcp_server",
        "sleep": "sleep",
        "review": "review",
    }


def test_no_skill_or_hook_calls_the_engine_around_the_door():
    """The v1.42.0 gate line: 0 `-m memory.` call sites in skills and hooks."""
    sites = []
    paths = glob.glob(os.path.join(_PLUGIN, "skills", "*", "SKILL.md"))
    paths += glob.glob(os.path.join(_PLUGIN, "hooks", "*.sh"))
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if re.search(r"-m\s+memory\.", line):
                    sites.append(f"{os.path.relpath(path, _PLUGIN)}:{n}")
    assert not sites, "engine calls that bypass `hippo <verb>`:\n  " + "\n  ".join(sites)


def _env(tmp_path, **extra):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        "PATH": os.pathsep.join([os.path.dirname(sys.executable), "/usr/bin", "/bin"]),
        "HOME": str(home),
        "HIPPO_TRUST_FILE": str(tmp_path / "trust.json"),
        "HIPPO_DISABLE_DENSE": "1",
    }
    env.update(extra)
    return env


@pytest.mark.parametrize("verb", ["staleness", "inspect", "capture", "dream", "resolve"])
def test_a_verb_runs_its_module_exactly_like_dash_m(tmp_path, verb):
    module = cli.verb_table()[verb].module
    env = _env(tmp_path, PYTHONPATH=_PLUGIN)
    via_m = subprocess.run(
        [sys.executable, "-m", f"memory.{module}", "--help"],
        capture_output=True, text=True, timeout=60, env=env, cwd=str(tmp_path),
    )
    via_door = subprocess.run(
        [sys.executable, "-m", "memory.cli", verb, "--help"],
        capture_output=True, text=True, timeout=60, env=env, cwd=str(tmp_path),
    )
    assert (via_door.returncode, via_door.stdout) == (via_m.returncode, via_m.stdout)


def test_unknown_verb_prints_usage_and_exits_2(capsys):
    assert cli.main(["nope"]) == 2
    err = capsys.readouterr().err
    assert "unknown verb 'nope'" in err and "usage: hippo <verb>" in err


def test_skill_redirects_name_the_skill(capsys):
    for name in SKILL_REDIRECTS:
        assert cli.main([name, "--anything"]) == 1
        assert f"/hippo:{name}" in capsys.readouterr().err


def _fake_cache_install(tmp_path, *, sentinel: bool):
    """A copy of plugin/ laid out the way a marketplace install is, plus its data dir."""
    plugins = tmp_path / "home" / ".claude" / "plugins"
    root = plugins / "cache" / "hippo" / "hippo" / "9.9.9"
    shutil.copytree(_PLUGIN, root, ignore=shutil.ignore_patterns("__pycache__"))
    data = plugins / "data" / "hippo-hippo"
    (data / "venv" / "bin").mkdir(parents=True)
    probe = data / "venv" / "bin" / "python"
    probe.write_text(
        "#!/bin/sh\n"
        'echo "DATA=$CLAUDE_PLUGIN_DATA"\n',
        encoding="utf-8",
    )
    probe.chmod(0o755)
    if sentinel:
        (data / ".bootstrap-sentinel").write_text("1.42.0\n", encoding="utf-8")
    return root, data


def test_a_bash_tool_shell_finds_the_installed_venv(tmp_path):
    root, data = _fake_cache_install(tmp_path, sentinel=True)
    r = subprocess.run(
        ["/bin/bash", str(root / "bin" / "hippo"), "doctor"],
        capture_output=True, text=True, timeout=30, env=_env(tmp_path), cwd=str(tmp_path),
    )
    assert r.stdout.strip() == f"DATA={data}", r.stderr


def test_no_sentinel_means_no_guess(tmp_path):
    root, _data = _fake_cache_install(tmp_path, sentinel=False)
    r = subprocess.run(
        ["/bin/bash", str(root / "bin" / "hippo"), "help"],
        capture_output=True, text=True, timeout=30, env=_env(tmp_path), cwd=str(tmp_path),
    )
    assert "DATA=" not in r.stdout and "usage: hippo <verb>" in r.stdout


def test_an_exported_data_dir_wins(tmp_path):
    root, _data = _fake_cache_install(tmp_path, sentinel=True)
    other = tmp_path / "other"
    (other / "venv" / "bin").mkdir(parents=True)
    probe = other / "venv" / "bin" / "python"
    probe.write_text('#!/bin/sh\necho "OTHER=$CLAUDE_PLUGIN_DATA"\n', encoding="utf-8")
    probe.chmod(0o755)
    r = subprocess.run(
        ["/bin/bash", str(root / "bin" / "hippo"), "doctor"],
        capture_output=True, text=True, timeout=30,
        env=_env(tmp_path, CLAUDE_PLUGIN_DATA=str(other)), cwd=str(tmp_path),
    )
    assert r.stdout.strip() == f"OTHER={other}"


def test_env_prints_shell_exports_a_flow_file_can_eval(tmp_path):
    """SRF-3: `eval "$(hippo env)"` gives a supporting flow file the plugin root, data dir and
    interpreter that Claude Code only fills into SKILL.md."""
    env = _env(tmp_path, PYTHONPATH=_PLUGIN, CLAUDE_PLUGIN_DATA=str(tmp_path / "data"))
    r = subprocess.run(
        ["/bin/bash", "-c",
         f'eval "$("{sys.executable}" -m memory.cli env)"; '
         'printf "%s|%s|%s\\n" "$CLAUDE_PLUGIN_ROOT" "$CLAUDE_PLUGIN_DATA" "$PY"'],
        capture_output=True, text=True, timeout=30, env=env, cwd=str(tmp_path),
    )
    root, data, py = r.stdout.strip().split("|")
    assert root == _PLUGIN and data == str(tmp_path / "data") and py == sys.executable
