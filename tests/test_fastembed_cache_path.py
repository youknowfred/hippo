"""Regression tests for the durable fastembed model-cache pin (Oct-31 ship-with-tests rule).

Bug being guarded: fastembed resolves its ONNX model cache from ``FASTEMBED_CACHE_PATH``,
defaulting to the EPHEMERAL ``$TMPDIR/fastembed_cache`` (``fastembed/common/utils.py``). On
macOS that lives under ``/var/folders``, which the OS purges on a schedule — silently wiping
the warmed ~130 MB ``bge-small-en-v1.5`` model and degrading hybrid recall to BM25-only with
NO error. The fix pins the cache to a durable, machine-shared dir everywhere the tooling loads
the model (both memory hooks + ``build_index``). This touches model-load behavior, so it ships
with these tests.

Default precedence (below an explicit ``FASTEMBED_CACHE_PATH``, which ``ensure_fastembed_cache_path``
honors): ``$CLAUDE_PLUGIN_DATA/fastembed`` when ``CLAUDE_PLUGIN_DATA`` is set (the packaged
plugin's update-surviving data dir) else ``~/Library/Caches/hippo-memory/fastembed``. The two
memory hooks run BEFORE the Python resolver and their export WINS via setdefault, so they encode
the SAME order — pinned together by the cross-language guard below.

Hermetic: env mutation is monkeypatch-scoped and the conftest autouse fixture additionally
snapshots/restores ``FASTEMBED_CACHE_PATH``. The real-``fastembed`` cases use a fake embedder or
a tmp cache dir so nothing downloads a model or writes to the user's real ``~/Library/Caches``.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from memory import build_index as B

# Repo root from this test file (tests/ -> repo root is parents[1]).
_REPO_ROOT = Path(__file__).resolve().parents[1]
# The home-cache literal both hooks must contain (the bash fallback default). The plugin-data
# branch is verified by actually expanding the hook in a subshell (test_hook_exports_match_*).
_HOOK_HOME_LITERAL = "$HOME/Library/Caches/hippo-memory/fastembed"
_HOOK_PATHS = (
    _REPO_ROOT / "plugin" / "hooks" / "memory_user_prompt.sh",
    _REPO_ROOT / "plugin" / "hooks" / "memory_session_start.sh",
)
_HOME_REL = ("Library", "Caches", "hippo-memory", "fastembed")


def _expand_hook_export(hook_path: Path, *, home: str, plugin_data) -> str:
    """Source the hook's REAL ``export FASTEMBED_CACHE_PATH=`` lines in a clean ``set -u`` bash
    subshell under a controlled env and return the resulting value.

    Exercises the committed hook lines (not a copy), so a divergence in the hook file — or a
    ``set -u`` unbound-variable break — is caught. ``env`` is replaced wholesale (clean room),
    so ``FASTEMBED_CACHE_PATH`` / ``CLAUDE_PLUGIN_DATA`` are absent unless injected here.
    """
    lines = [
        ln
        for ln in hook_path.read_text(encoding="utf-8").splitlines()
        if ln.strip().startswith("export FASTEMBED_CACHE_PATH=")
    ]
    assert lines, f"{hook_path.name} has no FASTEMBED_CACHE_PATH export line"
    script = "set -u\n" + "\n".join(lines) + '\nprintf %s "$FASTEMBED_CACHE_PATH"'
    env = {"HOME": home, "PATH": os.environ.get("PATH", "")}
    if plugin_data is not None:
        env["CLAUDE_PLUGIN_DATA"] = plugin_data
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, check=True
    )
    return proc.stdout


# --------------------------------------------------------------------------- #
# The durable path's VALUE — home-cache fallback (pure, no filesystem side effects)
# --------------------------------------------------------------------------- #
def test_durable_cache_dir_is_not_under_tempdir(monkeypatch):
    """The core invariant: the cache dir is NOT under the OS temp dir (which gets purged)."""
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)  # exercise the home fallback
    durable = B.durable_fastembed_cache_dir()
    tmp = tempfile.gettempdir()
    assert os.path.commonpath([durable, tmp]) != os.path.normpath(tmp)
    assert not durable.startswith(tmp.rstrip(os.sep) + os.sep)
    # And explicitly: not the ephemeral default fastembed would otherwise use.
    assert durable != os.path.join(tmp, "fastembed_cache")


def test_durable_cache_dir_is_absolute_expanded_home_path(monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    durable = B.durable_fastembed_cache_dir()
    assert os.path.isabs(durable)
    assert "~" not in durable  # expanduser actually ran
    assert durable == os.path.join(os.path.expanduser("~"), *_HOME_REL)


def test_durable_cache_dir_stable_across_tmpdir_churn(tmp_path, monkeypatch):
    """A temp purge / TMPDIR relocation must NOT move the durable cache location.

    This is the regression in one assertion: the durable path is independent of ``$TMPDIR``,
    so the model warmed there survives whatever happens to the system temp dir.
    """
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    before = B.durable_fastembed_cache_dir()
    new_tmp = str(tmp_path / "relocated-tmp")
    os.makedirs(new_tmp, exist_ok=True)
    monkeypatch.setattr(tempfile, "tempdir", new_tmp)  # force gettempdir() to a new location
    monkeypatch.setenv("TMPDIR", new_tmp)
    assert tempfile.gettempdir() == new_tmp  # the churn took effect
    after = B.durable_fastembed_cache_dir()
    assert after == before  # durable path did not move with TMPDIR
    assert not after.startswith(new_tmp + os.sep)  # and is not under the new temp dir either


# --------------------------------------------------------------------------- #
# Forward-looking precedence: prefer $CLAUDE_PLUGIN_DATA/fastembed when set
# --------------------------------------------------------------------------- #
def test_durable_prefers_plugin_data_when_set(monkeypatch):
    """When CLAUDE_PLUGIN_DATA is set, the cache lives in the plugin's update-surviving dir.

    This is the packaging-forward path: once the tooling ships as a plugin and the harness
    exports CLAUDE_PLUGIN_DATA, the cache self-resolves there with no further code change. A
    fixed abs path stands in for the harness-provided dir (the resolver does no filesystem I/O,
    so no real dir is needed — and unlike pytest's tmp_path it is genuinely not under $TMPDIR).
    """
    plugin_data = "/opt/claude/plugin-data"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", plugin_data)
    durable = B.durable_fastembed_cache_dir()
    assert durable == os.path.join(plugin_data, "fastembed")
    assert tempfile.gettempdir() not in durable  # still durable, not an ephemeral temp dir
    # It must NOT fall through to the home cache when plugin data is available.
    assert durable != os.path.join(os.path.expanduser("~"), *_HOME_REL)


def test_durable_ignores_empty_plugin_data(monkeypatch):
    """An empty CLAUDE_PLUGIN_DATA is treated as unset (matches bash ``${VAR:+...}``)."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", "")
    assert B.durable_fastembed_cache_dir() == os.path.join(os.path.expanduser("~"), *_HOME_REL)


# --------------------------------------------------------------------------- #
# ensure_fastembed_cache_path() — pin-if-unset, respect-if-set
# --------------------------------------------------------------------------- #
def test_ensure_sets_default_when_unset(monkeypatch):
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    effective = B.ensure_fastembed_cache_path()
    assert effective == B.durable_fastembed_cache_dir()
    assert os.environ["FASTEMBED_CACHE_PATH"] == B.durable_fastembed_cache_dir()
    assert tempfile.gettempdir() not in os.environ["FASTEMBED_CACHE_PATH"]


def test_ensure_respects_explicit_override(monkeypatch, tmp_path):
    """An explicit override (the hooks' export, or a packaged plugin's data dir) is preserved."""
    custom = str(tmp_path / "my-cache")
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", custom)
    effective = B.ensure_fastembed_cache_path()
    assert effective == custom
    assert os.environ["FASTEMBED_CACHE_PATH"] == custom  # NOT clobbered to the default


def test_ensure_is_idempotent(monkeypatch):
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    first = B.ensure_fastembed_cache_path()
    second = B.ensure_fastembed_cache_path()
    assert first == second == B.durable_fastembed_cache_dir()


# --------------------------------------------------------------------------- #
# Integration: the pin actually fires inside _get_model BEFORE fastembed loads
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("allow_download", [True, False])
def test_get_model_pins_cache_before_fastembed_import(monkeypatch, allow_download):
    """``_get_model`` must pin the durable cache path before constructing the embedder.

    Proven by capturing ``FASTEMBED_CACHE_PATH`` at the moment the (faked) ``TextEmbedding``
    is constructed — i.e. exactly when fastembed would resolve its cache dir. Guards against a
    future edit that drops the ``ensure_fastembed_cache_path()`` call from the model-load path.
    Uses a fake embedder so nothing downloads or writes to the real cache dir.
    """
    fastembed = pytest.importorskip("fastembed")
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)  # deterministic home-cache target
    monkeypatch.delenv("MEMOBOT_DISABLE_DENSE", raising=False)

    captured = {}

    class _RecordingEmbedding:
        def __init__(self, *args, **kwargs):
            # Recording the env (not instantiating the real model) creates no cache dir.
            captured["cache_path"] = os.environ.get("FASTEMBED_CACHE_PATH")

    monkeypatch.setattr(fastembed, "TextEmbedding", _RecordingEmbedding)

    model = B._get_model(allow_download=allow_download)
    assert isinstance(model, _RecordingEmbedding)
    assert captured["cache_path"] == B.durable_fastembed_cache_dir()
    assert tempfile.gettempdir() not in captured["cache_path"]


def test_fastembed_resolver_honors_pinned_env(monkeypatch, tmp_path):
    """fastembed's own ``define_cache_dir`` honors ``FASTEMBED_CACHE_PATH`` — no fastembed patch.

    Validates the load-bearing assumption that exporting the env var is sufficient (no code
    change on fastembed's side). Pinned to a tmp dir so the resolver's ``mkdir`` doesn't touch
    the user's real ``~/Library/Caches``.
    """
    pytest.importorskip("fastembed")
    from fastembed.common.utils import define_cache_dir

    pinned = str(tmp_path / "pinned-cache")
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", pinned)
    resolved = define_cache_dir()  # cache_dir=None -> reads the env var
    assert str(resolved) == pinned  # fastembed honored the pin (no fastembed code change needed)


# --------------------------------------------------------------------------- #
# Cross-language guard: the hooks' export resolves to the SAME dir as the Python pin
# --------------------------------------------------------------------------- #
def test_hooks_reference_plugin_data_and_home_cache():
    """Each hook must encode BOTH precedence levels (a fast, bash-free structural check)."""
    for hook in _HOOK_PATHS:
        text = hook.read_text(encoding="utf-8")
        assert "FASTEMBED_CACHE_PATH" in text, f"{hook.name} does not pin FASTEMBED_CACHE_PATH"
        assert "CLAUDE_PLUGIN_DATA" in text, f"{hook.name} missing the plugin-data precedence"
        assert _HOOK_HOME_LITERAL in text, f"{hook.name} missing the home-cache fallback literal"


@pytest.mark.parametrize("plugin_data", [None, "/opt/claude/plugin-data"])
def test_hook_exports_match_python_resolver(monkeypatch, plugin_data):
    """Both hooks' REAL export lines must resolve to the SAME dir as ``durable_fastembed_cache_dir``.

    Divergence here is the one way this fix silently breaks: the hook export runs first and WINS
    over Python's setdefault, so if the hook picked a different dir than a manual ``build_index``
    warms, recall would read a cold cache. Verified by expanding the committed hook lines in a
    subshell — under BOTH precedence branches (CLAUDE_PLUGIN_DATA unset vs set).
    """
    fake_home = "/Users/somebody"
    monkeypatch.setenv("HOME", fake_home)
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    if plugin_data is None:
        monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", plugin_data)

    expected = B.durable_fastembed_cache_dir()  # Python resolver under this exact env
    # Sanity: the Python value reflects the intended precedence branch.
    if plugin_data is None:
        assert expected == os.path.join(fake_home, *_HOME_REL)
    else:
        assert expected == os.path.join(plugin_data, "fastembed")

    for hook in _HOOK_PATHS:
        got = _expand_hook_export(hook, home=fake_home, plugin_data=plugin_data)
        assert os.path.normpath(got) == os.path.normpath(expected), (
            f"{hook.name} diverged from the Python resolver (CLAUDE_PLUGIN_DATA={plugin_data!r}): "
            f"hook={got!r} python={expected!r}"
        )
