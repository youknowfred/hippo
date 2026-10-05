"""The crash-safety subprocess kill lane, split from test_crash_faults.py (module-size cap).

A real SIGKILL mid-write, then the documented recovery. Deterministic: the child kills
itself at the exact write moment (a genuine process death; no handlers, no cleanup).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

import pytest

_PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugin"))
from .test_crash_faults import _git_repo, _mem  # noqa: E402  (the shared fixtures)


# --------------------------------------------------------------------------- #
# The subprocess kill lane (slow-marked): a real SIGKILL mid-write, then the
# documented recovery. Deterministic — the child kills ITSELF at the exact
# write moment (a genuine process death; no handlers, no cleanup).
# --------------------------------------------------------------------------- #
_KILL_CHILD = r"""
import os, signal, sys
sys.path.insert(0, {plugin_root!r})
import builtins
_real_open = builtins.open
_state = {{"writes": 0}}
def _open(path, mode="r", *a, **k):
    if any(c in mode for c in "wax") and {suffix!r} in str(path):
        _state["writes"] += 1
        if _state["writes"] >= {nth}:
            fh = _real_open(path, mode, *a, **k)
            fh.write({partial!r})  # bytes hit the disk...
            fh.flush()
            os.kill(os.getpid(), signal.SIGKILL)  # ...and the process dies mid-write
    return _real_open(path, mode, *a, **k)
builtins.open = _open
{body}
"""


def _run_child(body: str, *, suffix: str, nth: int = 1, partial: str = "{TORN") -> int:
    code = _KILL_CHILD.format(
        plugin_root=_PLUGIN_ROOT, suffix=suffix, nth=nth, partial=partial, body=body
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    return proc.returncode


@pytest.mark.slow
def test_kill9_mid_pack_extract_rerun_refuses_with_the_documented_message(tmp_path):
    """SIGKILL while extract writes a pack file: the dest holds a partial write the
    process never got to roll back (RCH-8 can't run under SIGKILL). The documented
    recovery is the REFUSAL arm: a re-run refuses the non-empty dest by name — the
    operator deletes the partial dir and re-runs clean."""
    root, md = _git_repo(tmp_path)
    _mem(md, "alpha")
    _mem(md, "beta")
    dest = str(tmp_path / "pack-out")
    body = (
        "from memory.packs import pack_extract\n"
        f"pack_extract(['alpha', 'beta'], {dest!r}, memory_dir={md!r}, repo_root={root!r})\n"
    )
    rc = _run_child(body, suffix=".md", nth=1)
    assert rc == -signal.SIGKILL
    assert os.path.isdir(dest)  # the partial dest the crash stranded

    from memory.packs import pack_extract

    r = pack_extract(["alpha", "beta"], dest, memory_dir=md, repo_root=root)
    # The documented refusal: every colliding name reported, zero files written.
    assert r["error"] and "refusing to overwrite" in r["error"], r
    assert r["extracted"] == [] and "zero files written" in r["error"]
    import shutil

    shutil.rmtree(dest)
    r2 = pack_extract(["alpha", "beta"], dest, memory_dir=md, repo_root=root)
    assert not r2["error"] and len(r2["extracted"]) == 2  # clean re-run heals


@pytest.mark.slow
def test_kill9_mid_build_index_rerun_heals(tmp_path):
    """SIGKILL while build_index writes its manifest TMP: the published manifest is
    old-or-absent (never torn — the swap never ran), and a re-run heals the index."""
    from memory.build_index import build_index, default_index_dir

    _root, md = _git_repo(tmp_path)
    _mem(md, "alpha")
    _mem(md, "beta")
    idx = default_index_dir(md)
    body = (
        "os.environ['HIPPO_DISABLE_DENSE'] = '1'\n"
        "from memory.build_index import build_index\n"
        f"build_index({md!r}, {idx!r})\n"
    )
    rc = _run_child(body, suffix="manifest.json", nth=1)  # matches the unique tmp too
    assert rc == -signal.SIGKILL

    manifest = os.path.join(idx, "manifest.json")
    if os.path.exists(manifest):  # whatever survived must parse whole — never torn
        with open(manifest, encoding="utf-8") as fh:
            json.load(fh)

    os.environ["HIPPO_DISABLE_DENSE"] = "1"
    try:
        build_index(md, idx)
    finally:
        os.environ.pop("HIPPO_DISABLE_DENSE", None)
    with open(manifest, encoding="utf-8") as fh:
        m = json.load(fh)
    assert m.get("count") == 2  # the re-run healed the index completely
