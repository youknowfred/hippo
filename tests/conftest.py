"""Hermetic fixtures for the agent-memory tooling tests.

Each test builds a throwaway git repo with fixture code + memory files and points the
tooling at it via explicit args / HIPPO_MEMORY_DIR. Nothing reads the real ~/.claude
memory dir, and commit times are pinned so the git-drift staleness check is deterministic.
"""

from __future__ import annotations

import os
import subprocess

import pytest


def _run(args, cwd, env=None):
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )


def write_file(repo: str, rel_path: str, content: str) -> str:
    """Write ``content`` to ``repo/rel_path`` (creating dirs); return the absolute path."""
    full = os.path.join(repo, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(content)
    return full


def git_commit(repo: str, message: str, when: int) -> str:
    """Stage everything and commit with a PINNED author/committer time (unix epoch).

    Returns the new commit sha. Pinned times make the staleness ct-comparison deterministic.
    """
    iso = f"{int(when)} +0000"
    env = {
        "GIT_AUTHOR_DATE": iso,
        "GIT_COMMITTER_DATE": iso,
        "GIT_AUTHOR_NAME": "tester",
        "GIT_AUTHOR_EMAIL": "tester@example.com",
        "GIT_COMMITTER_NAME": "tester",
        "GIT_COMMITTER_EMAIL": "tester@example.com",
    }
    _run(["git", "add", "-A"], repo)
    _run(["git", "commit", "-m", message, "--allow-empty"], repo, env)
    return _run(["git", "rev-parse", "HEAD"], repo).stdout.strip()


# --------------------------------------------------------------------------- #
# HIPPO_* env-leak guard (a hook pair, deliberately NOT a fixture)
# --------------------------------------------------------------------------- #
def _hippo_env() -> dict:
    return {k: v for k, v in os.environ.items() if k.startswith("HIPPO_")}


_HIPPO_ENV_BASELINE: dict = {}


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """Snapshot the ``HIPPO_*`` environment BEFORE this test's fixtures run."""
    _HIPPO_ENV_BASELINE.clear()
    _HIPPO_ENV_BASELINE.update(_hippo_env())


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item, nextitem):
    """Repair — then fail — any test that leaves a ``HIPPO_*`` env var changed behind it.

    Every hippo behaviour switch is an env var, so a test that writes ``os.environ``
    directly instead of going through ``monkeypatch`` silently reconfigures every test
    that runs after it in the same process. The concrete instance this guard was written
    for: the index-rebuild concurrency test toggled ``HIPPO_DISABLE_DENSE`` from a worker
    thread and exited on the disabled half of the toggle, so every later dense-path test
    in a full-suite run quietly became a bm25-only test — passing while asserting nothing
    it claimed to. That is a FALSE-GREEN class, not a flake: run alone the test proves the
    dense behaviour, run in-suite it proves nothing, and both are green.

    This is a hook pair rather than an autouse fixture because a fixture CANNOT be placed
    outside ``monkeypatch``: ``monkeypatch`` is set up before any autouse fixture that
    doesn't request it (and before one that does), so it is always finalized LAST and
    every legitimate ``monkeypatch.setenv("HIPPO_...")`` still looks like a leak from
    inside a fixture's teardown. ``pytest_runtest_setup(tryfirst)`` runs before fixture
    setup and ``pytest_runtest_teardown(trylast)`` runs after fixture finalization, so the
    comparison brackets the whole fixture stack — monkeypatch's undo included. Those two
    ordering markers are load-bearing.

    Repair runs before the report so one leaky test can't cascade into a wall of unrelated
    failures — the run still names exactly the test that leaked, and only that test.
    """
    before, after = dict(_HIPPO_ENV_BASELINE), _hippo_env()
    for key in after.keys() - before.keys():
        os.environ.pop(key, None)
    for key, value in before.items():
        if os.environ.get(key) != value:
            os.environ[key] = value
    leaked = sorted(
        f"{k}: {before.get(k)!r} -> {after.get(k)!r}"
        for k in before.keys() | after.keys()
        if before.get(k) != after.get(k)
    )
    if leaked:
        pytest.fail(
            "test leaked HIPPO_* env state into the rest of the process (repaired here, "
            "but fix it at the source — use monkeypatch.setenv/delenv, or restore in a "
            "finally): " + "; ".join(leaked),
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def _strip_ambient_plugin_env(monkeypatch):
    """Strip the harness-provided plugin env from every test.

    A developer running the suite from INSIDE a Claude Code session (or any consumer
    with hippo bootstrapped) has CLAUDE_PLUGIN_DATA / CLAUDE_PLUGIN_ROOT pointing at a
    real install — the stale-venv producer (COR-11) and any future env-keyed check
    would read REAL machine state and flip test outcomes. CLAUDE_CODE_ENTRYPOINT gets
    the same treatment: a suite run from inside a Desktop session would otherwise flip
    every session-start test onto the Desktop surface-note branch. Tests that need
    these vars set them explicitly (e.g. the hook subprocess tests pass a controlled
    env, the surface-note tests monkeypatch the entrypoint)."""
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)


@pytest.fixture(autouse=True)
def _isolate_trust_registry(tmp_path, monkeypatch):
    """Keep the SEC-1 trust gate hermetic + open by default across the whole suite.

    Two independent guards:
      - ``HIPPO_TRUST_FILE`` -> a per-test tmp path, so ``memory.trust`` NEVER reads or
        writes the real ``~/.claude/hippo-trust.json`` on the runner's machine (mark_trusted
        creates dirs/files — it must land in tmp, not the developer's home).
      - ``HIPPO_TRUST_ALL=1`` -> the gate is bypassed by default, so the many existing
        recall tests that build a corpus INSIDE a git ``repo`` fixture (which would otherwise
        resolve a real repo_root and be denied by the empty tmp registry) keep passing without
        each having to opt in. The dedicated trust tests (test_trust.py) delete this var to
        exercise the real deny/allow gate.

    ``HIPPO_PROJECTS_FILE`` (RCH-4) gets the same tmp-path treatment: ``memory.registry``
    must never read the runner's real ``~/.claude/hippo-projects.json`` — an
    ``--all-projects`` test would otherwise search REAL corpora on the developer's machine.
    """
    monkeypatch.setenv("HIPPO_TRUST_FILE", str(tmp_path / "hippo-trust.json"))
    monkeypatch.setenv("HIPPO_TRUST_ALL", "1")
    monkeypatch.setenv("HIPPO_PROJECTS_FILE", str(tmp_path / "hippo-projects.json"))


@pytest.fixture(autouse=True)
def _isolate_claude_projects_dir(tmp_path, monkeypatch):
    """HYG-2: keep the harness-owned ``~/.claude/projects`` symlink base hermetic.

    Every dangling ``~/.claude/projects/<encoded>/memory`` symlink on the reference
    machine (19 of 25 on 2026-07-17) was minted by tests that ran the REAL init with no
    isolation — the targets are dead pytest tmp trees. ``init_project`` resolves its
    symlink base through ``machine_census.claude_projects_root()``, which honors this
    override, so every farm write lands in tmp and dies with the test. Tests exercising
    the farm point the var (or the explicit ``claude_projects_dir`` args) at a dir they
    control."""
    monkeypatch.setenv("HIPPO_CLAUDE_PROJECTS_DIR", str(tmp_path / "claude-projects-guard"))


@pytest.fixture(autouse=True)
def _isolate_memory_tiers(tmp_path, monkeypatch):
    """Keep TEA-1/TEA-3 multi-corpus fusion hermetic across the whole suite.

    Point ``HIPPO_USER_MEMORY_DIR`` (TEA-1 user tier) and ``HIPPO_LOCAL_MEMORY_DIR`` (TEA-3
    private tier) at per-test tmp paths that do NOT exist, so recall fusion is a strict no-op
    unless a test deliberately creates one — a developer or CI runner with a real
    ``~/.claude/hippo-memory`` on disk can never bleed its memories into an unrelated test's
    recall results. A test exercising a tier overrides these to a dir it populates."""
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", str(tmp_path / "absent-user-tier"))
    monkeypatch.setenv("HIPPO_LOCAL_MEMORY_DIR", str(tmp_path / "absent-private-tier"))


@pytest.fixture(autouse=True)
def _isolate_llm_config(tmp_path, monkeypatch):
    """Keep the CAP-LLM/DRM-C standalone-LLM surfaces hermetic + OFF across the whole suite.

    ``HIPPO_LLM_CONFIG`` -> a per-test tmp path that does NOT exist, so ``memory.llm_client``
    never reads the developer's real ``~/.claude/hippo-llm.json`` — a machine-wide
    ``capture_triage: true`` (or a stored ``api_key``) would otherwise flip flag-off tests
    into LIVE-API territory from inside the test suite. The opt-in env flags and ambient
    API keys get the same strip, so only a test that explicitly sets them ever reaches the
    (mocked) LLM path. Tests exercising the config layer point ``HIPPO_LLM_CONFIG`` at a
    file they wrote themselves."""
    monkeypatch.setenv("HIPPO_LLM_CONFIG", str(tmp_path / "absent-llm-config.json"))
    monkeypatch.delenv("HIPPO_CAPTURE_LLM", raising=False)
    monkeypatch.delenv("HIPPO_DREAM_CONTRADICTIONS", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HIPPO_LLM_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _isolate_recall_global_state():
    """Keep the recall/index tests hermetic against PROCESS-GLOBAL side effects.

    ``build_index._get_model`` sets ``HF_HUB_OFFLINE`` / ``TRANSFORMERS_OFFLINE`` /
    ``FASTEMBED_CACHE_PATH`` via ``os.environ.setdefault`` and memoizes the loaded embedder in
    the module-global ``_MODEL_CACHE``. That is correct for the one-shot hook process, but inside
    a long-lived pytest process it LEAKS: once any test (incl. the real-``fastembed`` ones — the
    dep is installed) trips the load path, every later test inherits ``HF_HUB_OFFLINE=1``, the
    pinned cache path, and a populated cache, making the dense-backed results order-dependent (a
    "passes alone, fails in the suite" flake). Snapshot + restore those env keys and clear the
    model cache around every test so test ORDER can never change an outcome. Lazy + guarded so
    it's a no-op when the (in-flight) recall modules aren't importable.
    """
    leaked_keys = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "FASTEMBED_CACHE_PATH")
    saved = {k: os.environ.get(k) for k in leaked_keys}
    try:
        from memory import build_index as _bi

        _bi._MODEL_CACHE.clear()
    except Exception:
        _bi = None
    try:
        yield
    finally:
        if _bi is not None:
            try:
                _bi._MODEL_CACHE.clear()
            except Exception:
                pass
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture
def repo(tmp_path):
    r = str(tmp_path / "repo")
    os.makedirs(r)
    _run(["git", "init", "-q"], r)
    _run(["git", "config", "user.email", "tester@example.com"], r)
    _run(["git", "config", "user.name", "tester"], r)
    return r


@pytest.fixture
def memory_dir(repo):
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    return md
