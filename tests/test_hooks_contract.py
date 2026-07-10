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
_PRE_COMPACT_HOOK = os.path.join(_PLUGIN_ROOT, "hooks", "memory_pre_compact.sh")
_SESSION_END_HOOK = os.path.join(_PLUGIN_ROOT, "hooks", "memory_session_end.sh")
_SUBAGENT_STOP_HOOK = os.path.join(_PLUGIN_ROOT, "hooks", "memory_subagent_stop.sh")
_POST_TOOL_HOOK = os.path.join(_PLUGIN_ROOT, "hooks", "memory_post_tool.sh")

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
    # sed/tr/head joined the genuine set with GRW-4 (the PreCompact nudge extracts the
    # payload's session_id and sanitizes the embedded command, still zero Python spawn).
    for tool in ("cat", "printf", "sed", "tr", "head"):
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
        "HIPPO_DISABLE_DENSE": "1",
        # SEC-12: this hermetic corpus is a non-git tmp dir; treat it as a trusted local
        # corpus (as a user's own hand-made project would be) so the hook injects/nudges
        # its real mechanics instead of the untrusted-corpus gate.
        "HIPPO_TRUST_NONGIT": "1",
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

    def test_emits_valid_json_without_jq(self, tmp_path):
        # INT-5: recall --stdin-json emits the hookSpecificOutput JSON itself, so jq is no longer
        # on the hook path at all — the hook must still emit valid JSON with jq ABSENT.
        stdin = json.dumps({"prompt": "zebra canary deploy escalation path"})
        proc, _, _ = _run_hook(_USER_PROMPT_HOOK, stdin, tmp_path, jq=False, venv_python=True)
        _assert_contract(proc, "UserPromptSubmit")
        assert proc.stdout.strip()

    @pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed on this machine")
    def test_emits_valid_json_with_jq_present(self, tmp_path):
        # jq present but now unused — output must be identical/valid either way.
        stdin = json.dumps({"prompt": "zebra canary deploy escalation path"})
        proc, _, _ = _run_hook(_USER_PROMPT_HOOK, stdin, tmp_path, jq=True, venv_python=True)
        _assert_contract(proc, "UserPromptSubmit")
        assert proc.stdout.strip()

    def test_valid_prompt_spawns_exactly_one_python(self, tmp_path):
        # INT-5 acceptance: a single Python spawn per prompt. The interpreter is a counting
        # wrapper that appends a line per invocation then execs the real python.
        project = _make_project(tmp_path, with_corpus=True)
        data_dir = tmp_path / "plugin-data"
        os.makedirs(data_dir, exist_ok=True)
        counter = tmp_path / "spawn-count"
        bindir = tmp_path / "bin"
        os.makedirs(bindir, exist_ok=True)
        for tool in ("cat", "printf"):
            _symlink_once(shutil.which(tool), bindir / tool)
        wrapper = f"#!/bin/sh\necho x >> '{counter}'\nexec '{sys.executable}' \"$@\"\n"
        (bindir / "python3").write_text(wrapper, encoding="utf-8")
        os.chmod(bindir / "python3", 0o755)  # no venv python → PY resolves to this wrapper
        home = tmp_path / "home"
        os.makedirs(home, exist_ok=True)
        env = {
            "PATH": str(bindir),
            "HOME": str(home),
            "CLAUDE_PROJECT_DIR": project,
            "CLAUDE_PLUGIN_ROOT": _PLUGIN_ROOT,
            "CLAUDE_PLUGIN_DATA": str(data_dir),
            "HIPPO_DISABLE_DENSE": "1",
            "HIPPO_TRUST_NONGIT": "1",  # SEC-12: hermetic non-git corpus = trusted local
        }
        stdin = json.dumps({
            "prompt": "how is the zebra service deployed with canary rollout", "session_id": "s1"
        })
        proc = subprocess.run(
            ["/bin/bash", _USER_PROMPT_HOOK],
            input=stdin, capture_output=True, text=True, timeout=60, env=env,
        )
        _assert_contract(proc, "UserPromptSubmit")
        spawns = counter.read_text().count("x") if counter.exists() else 0
        assert spawns == 1, f"expected exactly ONE python spawn per prompt (INT-5), got {spawns}"
        ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "zebra_deploy_runbook" in ctx  # and it still injected the right memory

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
# CAP-1: PreCompact capture nudge — prompt-level, no Python, no corpus writes
# --------------------------------------------------------------------------- #
class TestPreCompactHook:
    def _ctx(self, proc) -> str:
        out = proc.stdout.strip()
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""

    def test_valid_payload_nudges_capture(self, tmp_path):
        stdin = json.dumps({"hook_event_name": "PreCompact", "trigger": "auto"})
        proc, _, _ = _run_hook(_PRE_COMPACT_HOOK, stdin, tmp_path)
        _assert_contract(proc, "PreCompact")
        ctx = self._ctx(proc)
        assert "/hippo:new" in ctx, "PreCompact nudge must point at the capture verb"

    def test_manual_trigger_also_nudges(self, tmp_path):
        stdin = json.dumps({"hook_event_name": "PreCompact", "trigger": "manual"})
        proc, _, _ = _run_hook(_PRE_COMPACT_HOOK, stdin, tmp_path)
        _assert_contract(proc, "PreCompact")
        assert "/hippo:new" in self._ctx(proc)

    def test_empty_stdin(self, tmp_path):
        proc, _, _ = _run_hook(_PRE_COMPACT_HOOK, "", tmp_path)
        _assert_contract(proc, "PreCompact")

    def test_garbage_json_still_exits_zero(self, tmp_path):
        proc, _, _ = _run_hook(_PRE_COMPACT_HOOK, "{{{{not json", tmp_path)
        _assert_contract(proc, "PreCompact")

    def test_no_corpus_stays_silent(self, tmp_path):
        # A never-opted-in repo: the nudge would dead-end at a missing bootstrap/init, so the
        # hook says nothing at all (COR-10 consistency) — but still exits 0.
        proc, _, _ = _run_hook(_PRE_COMPACT_HOOK, "", tmp_path, with_corpus=False)
        _assert_contract(proc, "PreCompact")
        assert proc.stdout.strip() == "", "no corpus → no nudge output"

    def test_missing_python3_still_nudges(self, tmp_path):
        # The nudge is pure bash — no Python spawn — so it works even pre-bootstrap.
        proc, _, _ = _run_hook(_PRE_COMPACT_HOOK, "", tmp_path, python3=False)
        _assert_contract(proc, "PreCompact")
        assert "/hippo:new" in self._ctx(proc)

    def test_writes_nothing(self, tmp_path):
        proc, project, _ = _run_hook(_PRE_COMPACT_HOOK, "", tmp_path)
        _assert_contract(proc, "PreCompact")
        # Prompt-level only: the corpus that was seeded by _make_project is the only thing
        # present; the hook itself creates no index/telemetry/pending dirs.
        for rel in _tree(project):
            assert rel.startswith(".claude/memory/"), f"PreCompact hook wrote {rel}"

    # GRW-4: the nudge also routes the WHY into the decision ledger — with THIS session's id
    # baked into the command (pure-bash sed extraction; still no Python spawn, no writes).
    def test_nudge_names_the_decision_command_with_the_session_id(self, tmp_path):
        stdin = json.dumps(
            {"hook_event_name": "PreCompact", "session_id": "sess-wy-42", "trigger": "auto"}
        )
        proc, _, _ = _run_hook(_PRE_COMPACT_HOOK, stdin, tmp_path)
        _assert_contract(proc, "PreCompact")
        ctx = self._ctx(proc)
        assert "/hippo:new" in ctx, "the base capture nudge must survive the GRW-4 extension"
        assert "--add-decision" in ctx, "the WHY-capture command must be named"
        assert "--session-id 'sess-wy-42'" in ctx, "the payload's session id keys the ledger"
        assert "never inferring" in ctx, "capture-from-evidence: transcription, not synthesis"

    def test_nudge_omits_session_flag_when_payload_has_none(self, tmp_path):
        stdin = json.dumps({"hook_event_name": "PreCompact", "trigger": "manual"})
        proc, _, _ = _run_hook(_PRE_COMPACT_HOOK, stdin, tmp_path)
        _assert_contract(proc, "PreCompact")
        ctx = self._ctx(proc)
        assert "--add-decision" in ctx
        assert "--session-id" not in ctx, "no session id in the payload → no stale flag"


# --------------------------------------------------------------------------- #
# CAP-2: SessionEnd draft-capture pass — writes ONLY the gitignored pending queue
# --------------------------------------------------------------------------- #
class TestSessionEndHook:
    def test_empty_stdin(self, tmp_path):
        proc, _, _ = _run_hook(_SESSION_END_HOOK, "", tmp_path)
        _assert_contract(proc, "SessionEnd")

    def test_garbage_json(self, tmp_path):
        proc, _, _ = _run_hook(_SESSION_END_HOOK, "{{{{", tmp_path)
        _assert_contract(proc, "SessionEnd")

    def test_no_corpus_writes_nothing(self, tmp_path):
        proc, project, _ = _run_hook(
            _SESSION_END_HOOK,
            json.dumps({"session_id": "s", "reason": "clear"}),
            tmp_path,
            with_corpus=False,
        )
        _assert_contract(proc, "SessionEnd")
        assert _tree(project) == set()

    def test_missing_python3_degrades_silently(self, tmp_path):
        proc, _, _ = _run_hook(_SESSION_END_HOOK, "", tmp_path, python3=False)
        _assert_contract(proc, "SessionEnd")

    def test_no_episodes_leaves_corpus_untouched(self, tmp_path):
        # A valid payload but no episode buffer → no seed; the corpus stays exactly as seeded.
        stdin = json.dumps({"session_id": "sess", "reason": "clear"})
        proc, project, _ = _run_hook(_SESSION_END_HOOK, stdin, tmp_path)
        _assert_contract(proc, "SessionEnd")
        # Only the two seeded corpus files exist under .claude/memory/; nothing new landed there.
        corpus = {r for r in _tree(project) if r.startswith(".claude/memory/")}
        assert corpus == {".claude/memory/zebra_deploy_runbook.md", ".claude/memory/MEMORY.md"}

    def test_captures_pending_seed_but_never_the_corpus(self, tmp_path):
        # Pre-seed the episode buffer for a session, then fire SessionEnd for that session.
        stdin = json.dumps({"session_id": "sess-cap", "reason": "clear"})
        # Build the project first (idempotent) so we can drop an episode buffer beside it.
        proc0, project, _ = _run_hook(_SESSION_END_HOOK, "", tmp_path)  # warm the dirs
        tele = os.path.join(project, ".claude", ".memory-telemetry")
        os.makedirs(tele, exist_ok=True)
        with open(os.path.join(tele, "episode_buffer.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": 1.0, "session_id": "sess-cap", "query_preview": "how do we deploy",
                "recalled_names": ["zebra_deploy_runbook"], "head_commit": None,
            }) + "\n")
        corpus_before = {r for r in _tree(project) if r.startswith(".claude/memory/")}

        proc, _, _ = _run_hook(_SESSION_END_HOOK, stdin, tmp_path)
        _assert_contract(proc, "SessionEnd")

        # A seed landed in the gitignored pending queue …
        pending = os.path.join(project, ".claude", ".memory-pending")
        seeds = [f for f in os.listdir(pending) if f.endswith(".json")] if os.path.isdir(pending) else []
        assert seeds, "SessionEnd captured no pending seed from the episode buffer"
        assert os.path.exists(os.path.join(pending, ".gitignore"))  # self-ignoring (SEC-3)
        # … and the corpus is byte-for-byte unchanged (the approval gate held).
        corpus_after = {r for r in _tree(project) if r.startswith(".claude/memory/")}
        assert corpus_after == corpus_before, "SessionEnd hook wrote into the corpus"


# --------------------------------------------------------------------------- #
# INT-3: SubagentStop capture — same gate as SessionEnd, labels the seed subagent-stop
# --------------------------------------------------------------------------- #
class TestSubagentStopHook:
    def test_empty_stdin(self, tmp_path):
        proc, _, _ = _run_hook(_SUBAGENT_STOP_HOOK, "", tmp_path)
        _assert_contract(proc, "SubagentStop")

    def test_garbage_json(self, tmp_path):
        proc, _, _ = _run_hook(_SUBAGENT_STOP_HOOK, "{{{", tmp_path)
        _assert_contract(proc, "SubagentStop")

    def test_no_corpus_writes_nothing(self, tmp_path):
        proc, project, _ = _run_hook(
            _SUBAGENT_STOP_HOOK, json.dumps({"session_id": "s"}), tmp_path, with_corpus=False
        )
        _assert_contract(proc, "SubagentStop")
        assert _tree(project) == set()

    def test_captures_to_pending_labeled_subagent_stop(self, tmp_path):
        proc0, project, _ = _run_hook(_SUBAGENT_STOP_HOOK, "", tmp_path)  # warm dirs
        tele = os.path.join(project, ".claude", ".memory-telemetry")
        os.makedirs(tele, exist_ok=True)
        with open(os.path.join(tele, "episode_buffer.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": 1.0, "session_id": "sub-1", "query_preview": "what changed",
                "recalled_names": ["zebra_deploy_runbook"], "head_commit": None,
            }) + "\n")
        corpus_before = {r for r in _tree(project) if r.startswith(".claude/memory/")}

        proc, _, _ = _run_hook(_SUBAGENT_STOP_HOOK, json.dumps({"session_id": "sub-1"}), tmp_path)
        _assert_contract(proc, "SubagentStop")

        pending = os.path.join(project, ".claude", ".memory-pending")
        seeds = [f for f in os.listdir(pending) if f.endswith(".json")] if os.path.isdir(pending) else []
        assert seeds, "SubagentStop captured no seed"
        with open(os.path.join(pending, seeds[0])) as fh:
            assert json.load(fh)["reason"] == "subagent-stop"
        # corpus untouched — same structural gate as SessionEnd
        assert {r for r in _tree(project) if r.startswith(".claude/memory/")} == corpus_before


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


# --------------------------------------------------------------------------- #
# SEC-3: never-opted-in projects gain ZERO hippo files
# --------------------------------------------------------------------------- #
class TestCorpusLessHygiene:
    def test_prompt_in_corpusless_repo_creates_no_files(self, tmp_path):
        stdin = json.dumps({"prompt": "a perfectly normal prompt about deploying things"})
        proc, project, _ = _run_hook(
            _USER_PROMPT_HOOK, stdin, tmp_path, with_corpus=False, venv_python=True,
            sentinel=True,
        )
        _assert_contract(proc, "UserPromptSubmit")
        assert _tree(project) == set(), (
            "a repo that never ran /hippo:init must gain zero hippo files "
            f"(got {_tree(project)})"
        )

    def test_session_start_in_corpusless_repo_creates_no_files(self, tmp_path):
        # Dismiss the ONB-1 nudge so the full Python dispatcher actually runs.
        data_dir = tmp_path / "plugin-data"
        os.makedirs(data_dir, exist_ok=True)
        (data_dir / ".nudge-dismissed").touch()
        proc, project, _ = _run_hook(
            _SESSION_START_HOOK, "", tmp_path, with_corpus=False, venv_python=True,
            sentinel=True,
        )
        _assert_contract(proc, "SessionStart")
        assert _tree(project) == set()


# --------------------------------------------------------------------------- #
# COR-10: bash-level bail on a missing .claude/memory — proves Python is never
# even SPAWNED (not just that it returns nothing), by making every interpreter
# reachable on PATH / in the venv a canary that records its own invocation.
# --------------------------------------------------------------------------- #
class TestBashLevelCorpusGuard:
    def _canary_env(self, tmp_path, *, with_corpus: bool, nudge_dismissed: bool):
        project = _make_project(tmp_path, with_corpus)
        data_dir = tmp_path / "plugin-data"
        os.makedirs(data_dir, exist_ok=True)
        if nudge_dismissed:
            (data_dir / ".nudge-dismissed").touch()

        canary = tmp_path / "python-spawned.marker"
        bindir = tmp_path / "bin"
        os.makedirs(bindir, exist_ok=True)
        for tool in ("cat", "printf"):
            _symlink_once(shutil.which(tool), bindir / tool)
        canary_script = f"#!/bin/sh\ntouch '{canary}'\nexit 0\n"
        # Any python this hook could possibly resolve to (bare python3 on PATH,
        # or the plugin-data venv) is this same canary — if the bash guard
        # doesn't fire before the FIRST python invocation, the marker appears.
        (bindir / "python3").write_text(canary_script, encoding="utf-8")
        os.chmod(bindir / "python3", 0o755)
        venv_bin = data_dir / "venv" / "bin"
        os.makedirs(venv_bin, exist_ok=True)
        (venv_bin / "python").write_text(canary_script, encoding="utf-8")
        os.chmod(venv_bin / "python", 0o755)
        (data_dir / ".bootstrap-sentinel").write_text(
            json.dumps({"requirements_hash": "irrelevant", "bootstrapped_at": "test"}),
            encoding="utf-8",
        )

        home = tmp_path / "home"
        os.makedirs(home, exist_ok=True)
        env = {
            "PATH": str(bindir),
            "HOME": str(home),
            "CLAUDE_PROJECT_DIR": project,
            "CLAUDE_PLUGIN_ROOT": _PLUGIN_ROOT,
            "CLAUDE_PLUGIN_DATA": str(data_dir),
            "HIPPO_DISABLE_DENSE": "1",
            "HIPPO_TRUST_NONGIT": "1",  # SEC-12: hermetic non-git corpus = trusted local
        }
        return project, str(data_dir), canary, env

    def test_user_prompt_hook_spawns_zero_python_in_corpusless_repo(self, tmp_path):
        project, _data_dir, canary, env = self._canary_env(
            tmp_path, with_corpus=False, nudge_dismissed=True
        )
        stdin = json.dumps({"prompt": "a perfectly normal prompt about deploying things"})
        proc = subprocess.run(
            ["/bin/bash", _USER_PROMPT_HOOK],
            input=stdin, capture_output=True, text=True, timeout=60, env=env,
        )
        _assert_contract(proc, "UserPromptSubmit")
        assert proc.stdout.strip() == ""
        assert not canary.exists(), "the hook spawned python despite no .claude/memory"
        assert _tree(project) == set()
        assert not os.path.isdir(os.path.join(project, ".claude"))

    def test_session_start_hook_spawns_zero_python_in_corpusless_repo(self, tmp_path):
        # Dismiss the ONB-1 nudge so we're proving the POST-nudge guard, not just
        # that the nudge branch itself short-circuits before Python.
        project, _data_dir, canary, env = self._canary_env(
            tmp_path, with_corpus=False, nudge_dismissed=True
        )
        proc = subprocess.run(
            ["/bin/bash", _SESSION_START_HOOK],
            input="", capture_output=True, text=True, timeout=60, env=env,
        )
        _assert_contract(proc, "SessionStart")
        assert proc.stdout.strip() == ""
        assert not canary.exists(), "the hook spawned python despite no .claude/memory"
        assert _tree(project) == set()
        assert not os.path.isdir(os.path.join(project, ".claude"))
        assert not os.path.isdir(os.path.join(project, ".claude", ".memory-index"))
        assert not os.path.isdir(os.path.join(project, ".claude", ".memory-telemetry"))

    def test_session_start_nudge_still_fires_before_guard_when_not_dismissed(self, tmp_path):
        # The bash guard must NOT silence the ONB-1 nudge — it sits AFTER it.
        project, _data_dir, canary, env = self._canary_env(
            tmp_path, with_corpus=False, nudge_dismissed=False
        )
        proc = subprocess.run(
            ["/bin/bash", _SESSION_START_HOOK],
            input="", capture_output=True, text=True, timeout=60, env=env,
        )
        _assert_contract(proc, "SessionStart")
        assert "/hippo:init" in proc.stdout
        assert not canary.exists()
        assert _tree(project) == set()


# --------------------------------------------------------------------------- #
# COR-6: SessionStart source-awareness + harness-keyed telemetry sessions, exercised
# through the REAL bash hook script (not just memory.session_start directly).
# --------------------------------------------------------------------------- #
def _session_token(project: str) -> str:
    path = os.path.join(project, ".claude", ".memory-telemetry", "session")
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read().strip()


class TestSessionStartSourceAwareness:
    def test_compaction_mid_session_does_not_rotate_token(self, tmp_path):
        stdin_startup = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
        proc, project, _ = _run_hook(
            _SESSION_START_HOOK, stdin_startup, tmp_path, venv_python=True, sentinel=True,
        )
        _assert_contract(proc, "SessionStart")
        before = _session_token(project)

        stdin_compact = json.dumps({"hook_event_name": "SessionStart", "source": "compact"})
        proc, _, _ = _run_hook(
            _SESSION_START_HOOK, stdin_compact, tmp_path, venv_python=True, sentinel=True,
        )
        _assert_contract(proc, "SessionStart")
        after = _session_token(project)
        assert after == before, "compaction mid-session must not increment distinct-session count"

    def test_resume_does_not_rotate_token(self, tmp_path):
        stdin_startup = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
        proc, project, _ = _run_hook(
            _SESSION_START_HOOK, stdin_startup, tmp_path, venv_python=True, sentinel=True,
        )
        _assert_contract(proc, "SessionStart")
        before = _session_token(project)

        stdin_resume = json.dumps({"hook_event_name": "SessionStart", "source": "resume"})
        proc, _, _ = _run_hook(
            _SESSION_START_HOOK, stdin_resume, tmp_path, venv_python=True, sentinel=True,
        )
        _assert_contract(proc, "SessionStart")
        assert _session_token(project) == before

    def test_clear_rotates_token(self, tmp_path):
        stdin_startup = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
        proc, project, _ = _run_hook(
            _SESSION_START_HOOK, stdin_startup, tmp_path, venv_python=True, sentinel=True,
        )
        _assert_contract(proc, "SessionStart")
        before = _session_token(project)

        stdin_clear = json.dumps({"hook_event_name": "SessionStart", "source": "clear"})
        proc, _, _ = _run_hook(
            _SESSION_START_HOOK, stdin_clear, tmp_path, venv_python=True, sentinel=True,
        )
        _assert_contract(proc, "SessionStart")
        assert _session_token(project) != before

    def test_harness_session_id_never_touches_file_token(self, tmp_path):
        stdin = json.dumps(
            {"hook_event_name": "SessionStart", "source": "startup", "session_id": "harness-1"}
        )
        proc, project, _ = _run_hook(
            _SESSION_START_HOOK, stdin, tmp_path, venv_python=True, sentinel=True,
        )
        _assert_contract(proc, "SessionStart")
        assert not os.path.exists(
            os.path.join(project, ".claude", ".memory-telemetry", "session")
        )


class TestConcurrentSessionAttribution:
    def test_two_concurrent_harness_sessions_log_distinct_stable_ids(self, tmp_path):
        stdin = json.dumps(
            {"prompt": "how is the zebra service deployed with canary rollout",
             "session_id": "session-A"}
        )
        proc, project, _ = _run_hook(_USER_PROMPT_HOOK, stdin, tmp_path, venv_python=True)
        _assert_contract(proc, "UserPromptSubmit")

        stdin_b = json.dumps(
            {"prompt": "how is the zebra service deployed with canary rollout",
             "session_id": "session-B"}
        )
        env_data_dir = os.path.join(str(tmp_path), "plugin-data")
        proc_b = subprocess.run(
            ["/bin/bash", _USER_PROMPT_HOOK],
            input=stdin_b,
            capture_output=True,
            text=True,
            timeout=60,
            env={
                "PATH": _make_path_dir(tmp_path, python3=True, jq=False),
                "HOME": str(tmp_path / "home"),
                "CLAUDE_PROJECT_DIR": project,
                "CLAUDE_PLUGIN_ROOT": _PLUGIN_ROOT,
                "CLAUDE_PLUGIN_DATA": env_data_dir,
                "HIPPO_DISABLE_DENSE": "1",
                "HIPPO_TRUST_NONGIT": "1",  # SEC-12: hermetic non-git corpus = trusted local
            },
        )
        _assert_contract(proc_b, "UserPromptSubmit")

        ledger = os.path.join(project, ".claude", ".memory-telemetry", "recall_events.jsonl")
        with open(ledger, "r", encoding="utf-8") as fh:
            events = [json.loads(line) for line in fh if line.strip()]
        session_ids = [e.get("session_id") for e in events]
        assert "session-A" in session_ids
        assert "session-B" in session_ids
        assert not os.path.exists(
            os.path.join(project, ".claude", ".memory-telemetry", "session")
        )


# --------------------------------------------------------------------------- #
# SIG-4: PostToolUse read-signal — records file touches into the gitignored outcome ledger
# (KPI-2), never the corpus, always exit 0.
# --------------------------------------------------------------------------- #
class TestPostToolUseHook:
    def test_empty_stdin(self, tmp_path):
        proc, _, _ = _run_hook(_POST_TOOL_HOOK, "", tmp_path, venv_python=True)
        _assert_contract(proc, "PostToolUse")
        assert proc.stdout.strip() == ""  # a read-signal hook emits no context

    def test_garbage_json(self, tmp_path):
        proc, _, _ = _run_hook(_POST_TOOL_HOOK, "{not json]]", tmp_path, venv_python=True)
        _assert_contract(proc, "PostToolUse")

    def test_no_corpus_writes_nothing(self, tmp_path):
        stdin = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "x.py"}})
        proc, project, _ = _run_hook(
            _POST_TOOL_HOOK, stdin, tmp_path, with_corpus=False, venv_python=True
        )
        _assert_contract(proc, "PostToolUse")
        assert _tree(project) == set()

    def test_missing_python3_degrades_silently(self, tmp_path):
        proc, _, _ = _run_hook(_POST_TOOL_HOOK, "", tmp_path, python3=False)
        _assert_contract(proc, "PostToolUse")

    def test_records_file_touch_into_outcome_ledger(self, tmp_path):
        project = _make_project(tmp_path, with_corpus=True)
        stdin = json.dumps(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": os.path.join(project, "src", "app.py")},
                "session_id": "sess-touch",
            }
        )
        proc, project, _ = _run_hook(_POST_TOOL_HOOK, stdin, tmp_path, venv_python=True)
        _assert_contract(proc, "PostToolUse")
        ledger = os.path.join(project, ".claude", ".memory-telemetry", "outcome_events.jsonl")
        assert os.path.exists(ledger), "PostToolUse hook logged no outcome event"
        with open(ledger, "r", encoding="utf-8") as fh:
            events = [json.loads(line) for line in fh if line.strip()]
        assert events and events[0]["tool"] == "Read"
        assert events[0]["path"].endswith("app.py")
        assert events[0]["session_id"] == "sess-touch"

    def test_writes_only_derived_dirs(self, tmp_path):
        project = _make_project(tmp_path, with_corpus=True)
        stdin = json.dumps(
            {"tool_name": "Edit", "tool_input": {"file_path": os.path.join(project, "a.py")}, "session_id": "s"}
        )
        proc, project, _ = _run_hook(_POST_TOOL_HOOK, stdin, tmp_path, venv_python=True)
        _assert_contract(proc, "PostToolUse")
        for rel in _tree(project):
            assert rel.startswith(
                (".claude/memory/", ".claude/.memory-index/", ".claude/.memory-telemetry/")
            ), f"PostToolUse hook wrote outside the expected dirs: {rel}"
