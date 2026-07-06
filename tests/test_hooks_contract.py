"""Subprocess contract tests for the two hook scripts (QUA-2).

The plugin's most safety-critical contract lives in plugin/hooks/*.sh:

  - ALWAYS exit 0. On UserPromptSubmit, a non-zero exit BLOCKS *and ERASES* the
    user's prompt — a recall failure must degrade silently, never eat input.
  - stdout is either EMPTY or a single valid ``hookSpecificOutput`` JSON object.
  - Nothing is written outside the project's ``.claude/`` derived dirs and the
    plugin's own ``CLAUDE_PLUGIN_DATA`` dir.

Every test here invokes the real script in a fresh subprocess under a controlled
environment (tmp project, tmp CLAUDE_PLUGIN_DATA, stripped PATH) and asserts that
contract across the failure matrix: empty stdin, garbage JSON, valid prompt,
missing python3, missing jq. A deliberate exit-2 mutation in either script fails
this file loudly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

_PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugin"))
_USER_PROMPT_HOOK = os.path.join(_PLUGIN_ROOT, "hooks", "memory_user_prompt.sh")
_SESSION_START_HOOK = os.path.join(_PLUGIN_ROOT, "hooks", "memory_session_start.sh")

_MEMORY_MD = """---
name: zebra_deploy_runbook
description: "How the zebra service is deployed — rollout order, canary steps, and the pager escalation path."
metadata:
  type: project
---

Deploy zebra via the canary lane first; page the on-call if step two stalls.
"""


def _make_project(tmp_path, with_corpus: bool) -> str:
    """Idempotent — some tests run the hook several times against one project."""
    project = tmp_path / "project"
    memdir = project / ".claude" / "memory"
    os.makedirs(memdir if with_corpus else project, exist_ok=True)
    if with_corpus:
        (memdir / "zebra_deploy_runbook.md").write_text(_MEMORY_MD, encoding="utf-8")
        (memdir / "MEMORY.md").write_text("# Memory Index\n\n## User\n", encoding="utf-8")
    return str(project)


def _symlink_once(src: str, dst) -> None:
    if src and not os.path.lexists(dst):
        os.symlink(src, dst)


def _make_path_dir(tmp_path, *, python3: bool, jq: bool) -> str:
    """A minimal PATH dir: coreutils the scripts genuinely need, python3/jq optional."""
    bindir = tmp_path / "bin"
    os.makedirs(bindir, exist_ok=True)
    for tool in ("cat", "printf"):
        _symlink_once(shutil.which(tool), bindir / tool)
    if python3:
        # The test venv's interpreter, exposed as `python3` — it has the pinned deps.
        _symlink_once(sys.executable, bindir / "python3")
    if jq:
        _symlink_once(shutil.which("jq"), bindir / "jq")
    return str(bindir)


def _run_hook(
    hook: str,
    stdin: str,
    tmp_path,
    *,
    with_corpus: bool = True,
    python3: bool = True,
    jq: bool = False,
    venv_python: bool = False,
    sentinel: bool = False,
    sentinel_hash: str = "",
) -> tuple[subprocess.CompletedProcess, str, str]:
    """Run one hook script in a controlled env; return (proc, project_dir, data_dir)."""
    project = _make_project(tmp_path, with_corpus)
    data_dir = str(tmp_path / "plugin-data")
    os.makedirs(data_dir, exist_ok=True)
    if venv_python:
        venv_bin = os.path.join(data_dir, "venv", "bin")
        os.makedirs(venv_bin, exist_ok=True)
        _symlink_once(sys.executable, os.path.join(venv_bin, "python"))
    if sentinel:
        # Record the REAL current requirements hash (a healthy bootstrap) unless a test
        # overrides it to simulate a post-update dep bump (COR-11).
        import hashlib

        with open(os.path.join(_PLUGIN_ROOT, "requirements.txt"), "rb") as fh:
            current = hashlib.sha256(fh.read()).hexdigest()
        with open(os.path.join(data_dir, ".bootstrap-sentinel"), "w", encoding="utf-8") as fh:
            json.dump({"requirements_hash": sentinel_hash or current, "bootstrapped_at": "test"}, fh)
    home = str(tmp_path / "home")
    os.makedirs(home, exist_ok=True)
    env = {
        "PATH": _make_path_dir(tmp_path, python3=python3, jq=jq),
        "HOME": home,
        "CLAUDE_PROJECT_DIR": project,
        "CLAUDE_PLUGIN_ROOT": _PLUGIN_ROOT,
        "CLAUDE_PLUGIN_DATA": data_dir,
        "MEMOBOT_DISABLE_DENSE": "1",
    }
    proc = subprocess.run(
        ["/bin/bash", hook],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    return proc, project, data_dir


def _assert_contract(proc: subprocess.CompletedProcess, event: str) -> None:
    """The full hook contract: exit 0, stdout empty or one valid hookSpecificOutput JSON."""
    assert proc.returncode == 0, (
        f"hook exited {proc.returncode} — this BLOCKS (and on UserPromptSubmit ERASES) "
        f"the user's prompt. stderr: {proc.stderr!r}"
    )
    out = proc.stdout.strip()
    if not out:
        return
    payload = json.loads(out)  # must be valid JSON if anything was printed
    hso = payload.get("hookSpecificOutput")
    assert isinstance(hso, dict), f"stdout is JSON but not hookSpecificOutput: {out!r}"
    assert hso.get("hookEventName") == event
    assert isinstance(hso.get("additionalContext"), str) and hso["additionalContext"]


def _tree(root: str) -> set:
    out = set()
    for dirpath, _dirnames, filenames in os.walk(root):
        for f in filenames:
            out.add(os.path.relpath(os.path.join(dirpath, f), root))
    return out


# --------------------------------------------------------------------------- #
# UserPromptSubmit hook
# --------------------------------------------------------------------------- #
class TestUserPromptHook:
    def test_empty_stdin(self, tmp_path):
        proc, _, _ = _run_hook(_USER_PROMPT_HOOK, "", tmp_path)
        _assert_contract(proc, "UserPromptSubmit")
        assert proc.stdout.strip() == ""

    def test_garbage_json(self, tmp_path):
        proc, _, _ = _run_hook(_USER_PROMPT_HOOK, "{not json at all]]", tmp_path)
        _assert_contract(proc, "UserPromptSubmit")
        assert proc.stdout.strip() == ""

    def test_valid_prompt_returns_recall_json(self, tmp_path):
        stdin = json.dumps({"prompt": "how is the zebra service deployed with canary rollout"})
        proc, _, _ = _run_hook(_USER_PROMPT_HOOK, stdin, tmp_path, venv_python=True)
        _assert_contract(proc, "UserPromptSubmit")
        out = proc.stdout.strip()
        assert out, "a matching corpus + valid prompt should inject recall context"
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "zebra_deploy_runbook" in ctx
        assert len(out) < 10_000  # harness cap

    def test_valid_prompt_no_corpus(self, tmp_path):
        stdin = json.dumps({"prompt": "how is the zebra service deployed"})
        proc, project, _ = _run_hook(
            _USER_PROMPT_HOOK, stdin, tmp_path, with_corpus=False, venv_python=True
        )
        _assert_contract(proc, "UserPromptSubmit")

    def test_missing_python3(self, tmp_path):
        stdin = json.dumps({"prompt": "anything at all here"})
        proc, _, _ = _run_hook(_USER_PROMPT_HOOK, stdin, tmp_path, python3=False)
        _assert_contract(proc, "UserPromptSubmit")
        assert proc.stdout.strip() == ""

    def test_missing_jq_falls_back_to_python_emission(self, tmp_path):
        # jq is ABSENT from PATH in this matrix by default — the python fallback
        # must still emit valid JSON (or nothing), never a partial line.
        stdin = json.dumps({"prompt": "zebra canary deploy escalation path"})
        proc, _, _ = _run_hook(_USER_PROMPT_HOOK, stdin, tmp_path, jq=False, venv_python=True)
        _assert_contract(proc, "UserPromptSubmit")
        assert proc.stdout.strip()

    @pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed on this machine")
    def test_with_jq_present(self, tmp_path):
        stdin = json.dumps({"prompt": "zebra canary deploy escalation path"})
        proc, _, _ = _run_hook(_USER_PROMPT_HOOK, stdin, tmp_path, jq=True, venv_python=True)
        _assert_contract(proc, "UserPromptSubmit")
        assert proc.stdout.strip()

    def test_writes_only_derived_dirs(self, tmp_path):
        stdin = json.dumps({"prompt": "zebra canary deploy escalation path"})
        proc, project, _ = _run_hook(_USER_PROMPT_HOOK, stdin, tmp_path, venv_python=True)
        _assert_contract(proc, "UserPromptSubmit")
        for rel in _tree(project):
            assert rel.startswith(
                (".claude/memory/", ".claude/.memory-index/", ".claude/.memory-telemetry/")
            ), f"hook wrote outside the expected dirs: {rel}"


# --------------------------------------------------------------------------- #
# SessionStart hook
# --------------------------------------------------------------------------- #
class TestSessionStartHook:
    def test_empty_stdin(self, tmp_path):
        proc, _, _ = _run_hook(
            _SESSION_START_HOOK, "", tmp_path, venv_python=True, sentinel=True
        )
        _assert_contract(proc, "SessionStart")

    def test_garbage_json(self, tmp_path):
        proc, _, _ = _run_hook(
            _SESSION_START_HOOK, "{{{{", tmp_path, venv_python=True, sentinel=True
        )
        _assert_contract(proc, "SessionStart")

    def test_valid_payload(self, tmp_path):
        stdin = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
        proc, _, _ = _run_hook(
            _SESSION_START_HOOK, stdin, tmp_path, venv_python=True, sentinel=True
        )
        _assert_contract(proc, "SessionStart")

    def test_missing_python3(self, tmp_path):
        proc, _, _ = _run_hook(_SESSION_START_HOOK, "", tmp_path, python3=False)
        _assert_contract(proc, "SessionStart")

    def test_no_corpus(self, tmp_path):
        proc, _, _ = _run_hook(
            _SESSION_START_HOOK, "", tmp_path, with_corpus=False, venv_python=True,
            sentinel=True,
        )
        _assert_contract(proc, "SessionStart")

    def test_writes_only_derived_dirs(self, tmp_path):
        proc, project, _ = _run_hook(
            _SESSION_START_HOOK, "", tmp_path, venv_python=True, sentinel=True
        )
        _assert_contract(proc, "SessionStart")
        for rel in _tree(project):
            assert rel.startswith(
                (".claude/memory/", ".claude/.memory-index/", ".claude/.memory-telemetry/")
            ), f"hook wrote outside the expected dirs: {rel}"


# --------------------------------------------------------------------------- #
# ONB-1: SessionStart bootstrap/init nudge (pre-Python branch)
# --------------------------------------------------------------------------- #
class TestSessionStartNudge:
    def _ctx(self, proc) -> str:
        out = proc.stdout.strip()
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""

    def test_venv_absent_nudges_bootstrap_exactly_one_line(self, tmp_path):
        proc, _, _ = _run_hook(_SESSION_START_HOOK, "", tmp_path, venv_python=False)
        _assert_contract(proc, "SessionStart")
        ctx = self._ctx(proc)
        assert "/hippo:bootstrap" in ctx and "not bootstrapped" in ctx
        assert "\n" not in ctx  # exactly one nudge line
        assert len(proc.stdout.strip().splitlines()) == 1  # exactly one JSON object

    def test_sentinel_absent_nudges_bootstrap_even_with_venv(self, tmp_path):
        proc, _, _ = _run_hook(
            _SESSION_START_HOOK, "", tmp_path, venv_python=True, sentinel=False
        )
        _assert_contract(proc, "SessionStart")
        assert "/hippo:bootstrap" in self._ctx(proc)

    def test_corpus_absent_nudges_init(self, tmp_path):
        proc, _, _ = _run_hook(
            _SESSION_START_HOOK, "", tmp_path, with_corpus=False, venv_python=True,
            sentinel=True,
        )
        _assert_contract(proc, "SessionStart")
        ctx = self._ctx(proc)
        assert "/hippo:init" in ctx and "/hippo:bootstrap" not in ctx

    def test_nudge_fires_once_per_n_sessions_not_spam(self, tmp_path):
        # Session 1 nudges; sessions 2..5 stay silent; session 6 nudges again.
        emitted = []
        for _ in range(6):
            proc, _, _ = _run_hook(_SESSION_START_HOOK, "", tmp_path, venv_python=False)
            _assert_contract(proc, "SessionStart")
            emitted.append(bool(proc.stdout.strip()))
        assert emitted == [True, False, False, False, False, True]

    def test_dismissal_marker_silences_permanently(self, tmp_path):
        data_dir = tmp_path / "plugin-data"
        os.makedirs(data_dir, exist_ok=True)
        (data_dir / ".nudge-dismissed").touch()
        proc, _, _ = _run_hook(_SESSION_START_HOOK, "", tmp_path, venv_python=False)
        _assert_contract(proc, "SessionStart")
        assert proc.stdout.strip() == ""

    def test_fully_provisioned_project_never_nudges(self, tmp_path):
        proc, _, _ = _run_hook(
            _SESSION_START_HOOK, "", tmp_path, venv_python=True, sentinel=True
        )
        _assert_contract(proc, "SessionStart")
        assert "/hippo:" not in self._ctx(proc)


# --------------------------------------------------------------------------- #
# COR-11: a simulated dep bump yields the re-bootstrap nudge (once per session —
# the producer lives in the once-per-session SessionStart dispatcher)
# --------------------------------------------------------------------------- #
class TestStaleVenvNudge:
    def test_dep_bump_yields_rebootstrap_nudge(self, tmp_path):
        proc, _, _ = _run_hook(
            _SESSION_START_HOOK, "", tmp_path, venv_python=True, sentinel=True,
            sentinel_hash="0" * 64,  # pre-bump hash — requirements.txt no longer matches
        )
        _assert_contract(proc, "SessionStart")
        ctx = json.loads(proc.stdout.strip())["hookSpecificOutput"]["additionalContext"]
        assert "deps changed" in ctx and "/hippo:bootstrap" in ctx

    def test_current_hash_stays_silent(self, tmp_path):
        proc, _, _ = _run_hook(
            _SESSION_START_HOOK, "", tmp_path, venv_python=True, sentinel=True
        )
        _assert_contract(proc, "SessionStart")
        assert "deps changed" not in proc.stdout
