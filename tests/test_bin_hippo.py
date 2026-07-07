"""Subprocess contract tests for plugin/bin/hippo (QUA-7).

bin/hippo is the convenience launcher that wraps the stateless CLI entry points
(recall, new, build-index, staleness) and redirects the multi-step orchestrations
(init, bootstrap, doctor, audit) to their SKILL.md files instead of re-implementing
them. It resolves its python interpreter via the ONE shared hippo_resolve_py() in
plugin/hooks/_resolve_py.sh (OSP-6) -- when CLAUDE_PLUGIN_DATA is unset (or its venv
is missing/non-executable), PY falls back to a bare `python3` on PATH.

Every test here invokes the real script (bash plugin/bin/hippo ...) in a fresh
subprocess under a controlled environment -- following the same conventions as
test_hooks_contract.py: explicit env dicts (no ambient CLAUDE_PLUGIN_DATA/HOME
leaking from the developer's real machine), timeouts, and tmp dirs for every
artifact the invocation could touch.

Subprocess tests do NOT inherit conftest.py's autouse fixtures (they set up THIS
process's os.environ, not a child's) -- each test here builds its own env dict and
must set MEMOBOT_TRUST_FILE / MEMOBOT_TRUST_ALL / MEMOBOT_DISABLE_DENSE explicitly,
mirroring what conftest does for in-process tests.

Note on the fallback interpreter: on the CI hermetic lane, the `python3` bin/hippo
falls back to is setup-python's interpreter WITH the pinned deps installed (numpy,
PyYAML, etc still importable) -- only a genuinely fresh machine has NONE of them,
in which case memory/_vendor's BM25 + miniyaml fallbacks serve the same recall
contract (see test_vendor.py's bare-venv fixture for that harder claim). This file
asserts on OBSERVABLE RESULTS (exit code, stdout content) that hold under BOTH
import paths, never on which path actually served the request.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

_PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugin"))
_HIPPO_BIN = os.path.join(_PLUGIN_ROOT, "bin", "hippo")

_MEMORY_MD = """---
name: zebra_deploy_runbook
description: "How the zebra service is deployed — rollout order, canary steps, and the pager escalation path."
metadata:
  type: project
---

Deploy zebra via the canary lane first; page the on-call if step two stalls.
"""


def _symlink_once(src: str, dst) -> None:
    if src and not os.path.lexists(dst):
        os.symlink(src, dst)


def _make_path_dir(tmp_path) -> str:
    """A minimal PATH dir: coreutils + a bare python3 (the test venv's interpreter,
    exposed as `python3` -- see module docstring re: CI hermetic-lane parity)."""
    bindir = tmp_path / "bin"
    os.makedirs(bindir, exist_ok=True)
    # bin/hippo itself resolves SCRIPT_DIR via `dirname` (unlike the hooks, which
    # don't need it) -- coreutils bin/hippo genuinely calls, plus git for parity
    # with a real dev shell.
    for tool in ("cat", "printf", "dirname", "git"):
        _symlink_once(shutil.which(tool), bindir / tool)
    _symlink_once(sys.executable, bindir / "python3")
    return str(bindir)


def _make_corpus(tmp_path) -> tuple[str, str]:
    """A tmp memory dir with one seeded memory + a tmp (empty, self-building) index dir."""
    memory_dir = tmp_path / "corpus" / "memory"
    index_dir = tmp_path / "corpus" / "index"
    os.makedirs(memory_dir, exist_ok=True)
    os.makedirs(index_dir, exist_ok=True)
    (memory_dir / "zebra_deploy_runbook.md").write_text(_MEMORY_MD, encoding="utf-8")
    return str(memory_dir), str(index_dir)


def _base_env(tmp_path, *, cwd) -> dict:
    """The controlled env every test starts from: no CLAUDE_PLUGIN_DATA (forces the
    python3 fallback per the item spec), no CLAUDE_PLUGIN_ROOT ambient leakage risk
    (bin/hippo pins its own via SCRIPT_DIR regardless), hermetic trust + dense-off.
    """
    home = tmp_path / "home"
    os.makedirs(home, exist_ok=True)
    return {
        "PATH": _make_path_dir(tmp_path),
        "HOME": str(home),
        # Mirrors conftest._isolate_trust_registry -- subprocess tests don't inherit
        # the in-process autouse fixture, so the hermetic trust env must be set here
        # explicitly or the corpus (a real git-less dir) could be denied/allowed by
        # whatever the runner's real ~/.claude/hippo-trust.json happens to contain.
        "MEMOBOT_TRUST_FILE": str(tmp_path / "hippo-trust.json"),
        "MEMOBOT_TRUST_ALL": "1",
        # Mirrors the item spec: BM25-only, no dense model load / download attempt.
        "MEMOBOT_DISABLE_DENSE": "1",
    }


def _run_hippo(args, tmp_path, *, cwd, extra_env: dict | None = None):
    env = _base_env(tmp_path, cwd=cwd)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["/bin/bash", _HIPPO_BIN, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


# --------------------------------------------------------------------------- #
# No CLAUDE_PLUGIN_DATA -> python3 fallback works BM25-only
# --------------------------------------------------------------------------- #
class TestPython3FallbackRecall:
    def test_recall_bm25_only_finds_seeded_memory(self, tmp_path):
        """CLAUDE_PLUGIN_DATA is absent from _base_env entirely -- hippo_resolve_py()
        must fall back to bare `python3` on PATH (never fail, never hang), and recall
        must still serve a real BM25-backed hit for a lexically-matching query."""
        memory_dir, index_dir = _make_corpus(tmp_path)
        cwd = tmp_path / "scratch_cwd"
        os.makedirs(cwd, exist_ok=True)
        proc = _run_hippo(
            [
                "recall",
                "how is the zebra service deployed with canary rollout",
                "--memory-dir", memory_dir,
                "--index-dir", index_dir,
            ],
            tmp_path,
            cwd=cwd,
        )
        assert proc.returncode == 0, (
            f"expected exit 0, got {proc.returncode}; stderr: {proc.stderr!r}"
        )
        assert "zebra_deploy_runbook" in proc.stdout, (
            f"expected memory name in stdout; got: {proc.stdout!r} / stderr: {proc.stderr!r}"
        )

    def test_claude_plugin_data_truly_absent_from_env(self, tmp_path):
        """Belt-and-suspenders on the fixture itself: CLAUDE_PLUGIN_DATA must not be
        in the env dict we hand to the subprocess (a silent ambient leak would let
        this whole class pass for the wrong reason -- the venv path, not the
        fallback)."""
        env = _base_env(tmp_path, cwd=tmp_path)
        assert "CLAUDE_PLUGIN_DATA" not in env


# --------------------------------------------------------------------------- #
# Bogus subcommand -> exit 2 + usage on stderr
# --------------------------------------------------------------------------- #
class TestBogusSubcommand:
    def test_unknown_command_exits_2_with_usage_on_stderr(self, tmp_path):
        cwd = tmp_path / "scratch_cwd"
        os.makedirs(cwd, exist_ok=True)
        proc = _run_hippo(["not-a-real-command"], tmp_path, cwd=cwd)
        assert proc.returncode == 2, f"stdout: {proc.stdout!r} stderr: {proc.stderr!r}"
        assert "usage:" in proc.stderr
        assert proc.stdout == ""

    def test_no_subcommand_at_all_exits_2_with_usage(self, tmp_path):
        """`shift || true` means a bare invocation with zero args must also fall
        through to the usage branch, not error out on the shift itself."""
        cwd = tmp_path / "scratch_cwd"
        os.makedirs(cwd, exist_ok=True)
        proc = _run_hippo([], tmp_path, cwd=cwd)
        assert proc.returncode == 2
        assert "usage:" in proc.stderr


# --------------------------------------------------------------------------- #
# Skill-redirect commands -> exit 1 + message pointing at /hippo:<name>
# --------------------------------------------------------------------------- #
class TestSkillRedirects:
    def test_each_skill_command_redirects(self, tmp_path):
        cwd = tmp_path / "scratch_cwd"
        os.makedirs(cwd, exist_ok=True)
        for name in ("init", "bootstrap", "doctor", "audit"):
            proc = _run_hippo([name], tmp_path, cwd=cwd)
            assert proc.returncode == 1, (
                f"'{name}': expected exit 1, got {proc.returncode}; stderr: {proc.stderr!r}"
            )
            assert f"/hippo:{name}" in proc.stderr, (
                f"'{name}': expected a pointer to /hippo:{name} in stderr; "
                f"got: {proc.stderr!r}"
            )
            assert proc.stdout == ""

    def test_skill_redirect_ignores_trailing_args(self, tmp_path):
        """Skill commands are a hard redirect regardless of any extra argv --
        proves the case arm doesn't accidentally try to consume/forward them."""
        cwd = tmp_path / "scratch_cwd"
        os.makedirs(cwd, exist_ok=True)
        proc = _run_hippo(["doctor", "--verbose", "extra"], tmp_path, cwd=cwd)
        assert proc.returncode == 1
        assert "/hippo:doctor" in proc.stderr


# --------------------------------------------------------------------------- #
# Hygiene: the launcher writes nothing outside the passed dirs
# --------------------------------------------------------------------------- #
class TestCwdHygiene:
    def _tree(self, root) -> set:
        out = set()
        for dirpath, _dirnames, filenames in os.walk(root):
            for f in filenames:
                out.add(os.path.relpath(os.path.join(dirpath, f), root))
        return out

    def test_recall_writes_nothing_to_scratch_cwd(self, tmp_path):
        memory_dir, index_dir = _make_corpus(tmp_path)
        cwd = tmp_path / "scratch_cwd"
        os.makedirs(cwd, exist_ok=True)
        before = self._tree(cwd)
        proc = _run_hippo(
            [
                "recall",
                "how is the zebra service deployed with canary rollout",
                "--memory-dir", memory_dir,
                "--index-dir", index_dir,
            ],
            tmp_path,
            cwd=cwd,
        )
        assert proc.returncode == 0, proc.stderr
        after = self._tree(cwd)
        assert after == before == set(), (
            f"recall wrote stray files into the scratch cwd: {after - before}"
        )

    def test_bogus_command_writes_nothing_to_scratch_cwd(self, tmp_path):
        cwd = tmp_path / "scratch_cwd"
        os.makedirs(cwd, exist_ok=True)
        _run_hippo(["nope"], tmp_path, cwd=cwd)
        assert self._tree(cwd) == set()

    def test_skill_redirect_writes_nothing_to_scratch_cwd(self, tmp_path):
        cwd = tmp_path / "scratch_cwd"
        os.makedirs(cwd, exist_ok=True)
        _run_hippo(["bootstrap"], tmp_path, cwd=cwd)
        assert self._tree(cwd) == set()

    def test_recall_confines_writes_to_the_passed_index_dir(self, tmp_path):
        """The index dir IS allowed to gain files (that's its job) -- assert any
        derived-cache writes land there, never in the memory dir (source of truth,
        markdown-in-git) or the scratch cwd."""
        memory_dir, index_dir = _make_corpus(tmp_path)
        memory_before = self._tree(memory_dir)
        cwd = tmp_path / "scratch_cwd"
        os.makedirs(cwd, exist_ok=True)
        proc = _run_hippo(
            [
                "recall",
                "how is the zebra service deployed with canary rollout",
                "--memory-dir", memory_dir,
                "--index-dir", index_dir,
            ],
            tmp_path,
            cwd=cwd,
        )
        assert proc.returncode == 0, proc.stderr
        assert self._tree(memory_dir) == memory_before, "recall must never write to --memory-dir"
        assert self._tree(cwd) == set()


# --------------------------------------------------------------------------- #
# Sanity: query text alone (no corpus match) still exits 0 -- recall() never raises
# --------------------------------------------------------------------------- #
class TestRecallNeverRaises:
    def test_recall_with_empty_memory_dir_exits_0(self, tmp_path):
        empty_memory = tmp_path / "empty_memory"
        empty_index = tmp_path / "empty_index"
        os.makedirs(empty_memory, exist_ok=True)
        os.makedirs(empty_index, exist_ok=True)
        cwd = tmp_path / "scratch_cwd"
        os.makedirs(cwd, exist_ok=True)
        proc = _run_hippo(
            [
                "recall",
                "anything at all",
                "--memory-dir", str(empty_memory),
                "--index-dir", str(empty_index),
            ],
            tmp_path,
            cwd=cwd,
        )
        assert proc.returncode == 0, proc.stderr
