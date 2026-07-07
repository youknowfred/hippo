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
plugin's update-surviving data dir) else a PLATFORM-CONVENTIONAL home cache (OSP-2):
``~/Library/Caches/hippo-memory/fastembed`` on macOS, ``${XDG_CACHE_HOME:-~/.cache}/hippo-memory/
fastembed`` on Linux. The two memory hooks run BEFORE the Python resolver and their export WINS
via setdefault, so they encode the SAME order — pinned together by the cross-language guard below,
parametrized per platform so the suite does not pin the macOS layout as correct everywhere.

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
_HOOK_PATHS = (
    _REPO_ROOT / "plugin" / "hooks" / "memory_user_prompt.sh",
    _REPO_ROOT / "plugin" / "hooks" / "memory_session_start.sh",
)


def _expected_home_cache(*, uname: str, home: str, xdg_cache_home) -> str:
    """The per-platform expected home-cache fallback (mirrors platform_cache_dir())."""
    if uname == "Darwin":
        return os.path.join(home, "Library", "Caches", "hippo-memory", "fastembed")
    base = xdg_cache_home if xdg_cache_home else os.path.join(home, ".cache")
    return os.path.join(base, "hippo-memory", "fastembed")


def _expand_hook_export(hook_path: Path, *, home: str, plugin_data, uname: str, xdg_cache_home=None) -> str:
    """Source the hook's REAL ``export FASTEMBED_CACHE_PATH=`` lines in a clean ``set -u`` bash
    subshell under a controlled env and return the resulting value.

    Exercises the committed hook lines (not a copy), so a divergence in the hook file — or a
    ``set -u`` unbound-variable break — is caught. ``env`` is replaced wholesale (clean room),
    so ``FASTEMBED_CACHE_PATH`` / ``CLAUDE_PLUGIN_DATA`` / ``XDG_CACHE_HOME`` are absent unless
    injected here. ``uname`` fakes ``$(uname)`` via a shim ``uname`` shadowing PATH so the SAME
    Darwin/Linux branch in the hook is exercised regardless of the host running the test.
    """
    all_lines = hook_path.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(all_lines) if ln.strip().startswith("export FASTEMBED_CACHE_PATH="))
    end = next(i for i, ln in enumerate(all_lines) if i > start and ln.strip() == "fi")
    lines = all_lines[start : end + 1]
    assert any("FASTEMBED_CACHE_PATH" in ln for ln in lines), (
        f"{hook_path.name} has no FASTEMBED_CACHE_PATH export line"
    )
    fake_uname_dir = Path(tempfile.mkdtemp(prefix="fake-uname-"))
    fake_uname = fake_uname_dir / "uname"
    fake_uname.write_text(f'#!/bin/sh\nprintf %s "{uname}"\n', encoding="utf-8")
    fake_uname.chmod(0o755)
    script = "set -u\n" + "\n".join(lines) + '\nprintf %s "$FASTEMBED_CACHE_PATH"'
    env = {"HOME": home, "PATH": f"{fake_uname_dir}:{os.environ.get('PATH', '')}"}
    if plugin_data is not None:
        env["CLAUDE_PLUGIN_DATA"] = plugin_data
    if xdg_cache_home is not None:
        env["XDG_CACHE_HOME"] = xdg_cache_home
    try:
        proc = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, env=env, check=True
        )
    finally:
        fake_uname.unlink(missing_ok=True)
        fake_uname_dir.rmdir()
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
    assert durable == os.path.join(B.platform_cache_dir(), "fastembed")


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
    assert durable != os.path.join(B.platform_cache_dir(), "fastembed")


def test_durable_ignores_empty_plugin_data(monkeypatch):
    """An empty CLAUDE_PLUGIN_DATA is treated as unset (matches bash ``${VAR:+...}``)."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", "")
    assert B.durable_fastembed_cache_dir() == os.path.join(B.platform_cache_dir(), "fastembed")


# --------------------------------------------------------------------------- #
# platform_cache_dir() — per-platform branch (OSP-2): darwin vs XDG/Linux default
# --------------------------------------------------------------------------- #
def test_platform_cache_dir_darwin(monkeypatch):
    monkeypatch.setattr(B.sys, "platform", "darwin")
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    fake_home = "/Users/somebody"
    monkeypatch.setenv("HOME", fake_home)
    assert B.platform_cache_dir() == os.path.join(fake_home, "Library", "Caches", "hippo-memory")


def test_platform_cache_dir_linux_uses_xdg_cache_home(monkeypatch):
    monkeypatch.setattr(B.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", "/home/somebody/.cache-custom")
    assert B.platform_cache_dir() == "/home/somebody/.cache-custom/hippo-memory"


def test_platform_cache_dir_linux_defaults_to_dot_cache_when_xdg_unset(monkeypatch):
    monkeypatch.setattr(B.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    fake_home = "/home/somebody"
    monkeypatch.setenv("HOME", fake_home)
    assert B.platform_cache_dir() == os.path.join(fake_home, ".cache", "hippo-memory")


def test_platform_cache_dir_linux_ignores_empty_xdg_cache_home(monkeypatch):
    """An empty XDG_CACHE_HOME is treated as unset (matches bash ``${VAR:-...}``)."""
    monkeypatch.setattr(B.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", "")
    fake_home = "/home/somebody"
    monkeypatch.setenv("HOME", fake_home)
    assert B.platform_cache_dir() == os.path.join(fake_home, ".cache", "hippo-memory")


def test_platform_cache_dir_unrecognized_platform_falls_back_to_xdg_convention(monkeypatch):
    """An unrecognized (non-darwin) platform string does not hard-fail — Windows is out of
    scope per OQ-2, so any non-darwin platform is treated as the XDG/Linux convention."""
    monkeypatch.setattr(B.sys, "platform", "freebsd13")
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    fake_home = "/home/somebody"
    monkeypatch.setenv("HOME", fake_home)
    assert B.platform_cache_dir() == os.path.join(fake_home, ".cache", "hippo-memory")


@pytest.mark.parametrize(
    "sys_platform, xdg_cache_home",
    [("darwin", None), ("linux", None), ("linux", "/home/somebody/.cache-custom")],
)
def test_durable_fastembed_cache_dir_per_platform(monkeypatch, sys_platform, xdg_cache_home):
    """``durable_fastembed_cache_dir`` follows ``platform_cache_dir`` under EVERY platform branch —
    not just the host's real platform (the regression this item fixes: the fallback used to be
    pinned to the macOS literal regardless of ``sys.platform``)."""
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.setattr(B.sys, "platform", sys_platform)
    fake_home = "/Users/somebody" if sys_platform == "darwin" else "/home/somebody"
    monkeypatch.setenv("HOME", fake_home)
    if xdg_cache_home is None:
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_CACHE_HOME", xdg_cache_home)
    assert B.durable_fastembed_cache_dir() == os.path.join(B.platform_cache_dir(), "fastembed")


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
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)

    captured = {}

    class _RecordingEmbedding:
        def __init__(self, *args, **kwargs):
            # Recording the env (not instantiating the real model) creates no cache dir.
            captured["cache_path"] = os.environ.get("FASTEMBED_CACHE_PATH")

    monkeypatch.setattr(fastembed, "TextEmbedding", _RecordingEmbedding)
    # OSP-4 added an offline-path pre-check (``_fastembed_model_cached``) that raises BEFORE
    # constructing the embedder on a cold cache — orthogonal to what THIS test verifies (the
    # cache-pin fires before the embedder is built). Neutralize it so both parametrizations
    # reach the construction; the cold-cache bail has its own coverage in test_build_index.py.
    monkeypatch.setattr(B, "_fastembed_model_cached", lambda cache_dir: True)

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
    """Each hook must encode BOTH precedence levels AND branch on platform (a fast, bash-free
    structural check) — not just contain the macOS literal unconditionally."""
    for hook in _HOOK_PATHS:
        text = hook.read_text(encoding="utf-8")
        assert "FASTEMBED_CACHE_PATH" in text, f"{hook.name} does not pin FASTEMBED_CACHE_PATH"
        assert "CLAUDE_PLUGIN_DATA" in text, f"{hook.name} missing the plugin-data precedence"
        assert "Darwin" in text, f"{hook.name} missing the darwin/linux platform branch"
        assert "Library/Caches/hippo-memory/fastembed" in text, f"{hook.name} missing the macOS home-cache fallback"
        assert "XDG_CACHE_HOME" in text, f"{hook.name} missing the Linux/XDG home-cache fallback"


@pytest.mark.parametrize(
    "uname, xdg_cache_home",
    [("Darwin", None), ("Linux", None), ("Linux", "/home/somebody/.cache-custom")],
)
@pytest.mark.parametrize("plugin_data", [None, "/opt/claude/plugin-data"])
def test_hook_exports_match_python_resolver(monkeypatch, plugin_data, uname, xdg_cache_home):
    """Both hooks' REAL export lines must resolve to the SAME dir as ``durable_fastembed_cache_dir``
    under the SAME simulated platform (OSP-2: this must hold for both Darwin and Linux, not just
    the host's real platform).

    Divergence here is the one way this fix silently breaks: the hook export runs first and WINS
    over Python's setdefault, so if the hook picked a different dir than a manual ``build_index``
    warms, recall would read a cold cache. Verified by expanding the committed hook lines in a
    subshell — under BOTH precedence branches (CLAUDE_PLUGIN_DATA unset vs set) and BOTH platform
    branches (Darwin vs Linux, with/without XDG_CACHE_HOME).
    """
    fake_home = "/Users/somebody" if uname == "Darwin" else "/home/somebody"
    monkeypatch.setenv("HOME", fake_home)
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    monkeypatch.setattr(B.sys, "platform", "darwin" if uname == "Darwin" else "linux")
    if plugin_data is None:
        monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", plugin_data)
    if xdg_cache_home is None:
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_CACHE_HOME", xdg_cache_home)

    expected = B.durable_fastembed_cache_dir()  # Python resolver under this exact env
    # Sanity: the Python value reflects the intended precedence + platform branch.
    if plugin_data is None:
        assert expected == os.path.join(
            _expected_home_cache(uname=uname, home=fake_home, xdg_cache_home=xdg_cache_home)
        )
    else:
        assert expected == os.path.join(plugin_data, "fastembed")

    for hook in _HOOK_PATHS:
        got = _expand_hook_export(
            hook, home=fake_home, plugin_data=plugin_data, uname=uname, xdg_cache_home=xdg_cache_home
        )
        assert os.path.normpath(got) == os.path.normpath(expected), (
            f"{hook.name} diverged from the Python resolver (CLAUDE_PLUGIN_DATA={plugin_data!r}, "
            f"uname={uname!r}, XDG_CACHE_HOME={xdg_cache_home!r}): hook={got!r} python={expected!r}"
        )
