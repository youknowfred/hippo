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


@pytest.fixture(autouse=True)
def _strip_ambient_plugin_env(monkeypatch):
    """Strip the harness-provided plugin env from every test.

    A developer running the suite from INSIDE a Claude Code session (or any consumer
    with hippo bootstrapped) has CLAUDE_PLUGIN_DATA / CLAUDE_PLUGIN_ROOT pointing at a
    real install — the stale-venv producer (COR-11) and any future env-keyed check
    would read REAL machine state and flip test outcomes. Tests that need these vars
    set them explicitly (e.g. the hook subprocess tests pass a controlled env)."""
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)


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
    """
    monkeypatch.setenv("HIPPO_TRUST_FILE", str(tmp_path / "hippo-trust.json"))
    monkeypatch.setenv("HIPPO_TRUST_ALL", "1")


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
