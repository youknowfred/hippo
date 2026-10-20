"""MIG-9: the index, telemetry and pending queue live in ``<project>/.hippo/``.

The Claude desktop app copies the checkout's whole ``.claude/`` (ignored files included) into
every worktree it creates, so the three dirs that used to sit beside the corpus rode into each
one. These pin where they resolve now, and that an older layout is still read where it is.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from memory import derived_dirs as D
from memory.build_index import default_index_dir
from memory.capture_queue import default_pending_dir
from memory.telemetry import default_telemetry_dir

_PLUGIN_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin")
_KINDS = ("index", "telemetry", "pending")


@pytest.fixture(autouse=True)
def _no_overrides(monkeypatch):
    for var in ("HIPPO_INDEX_DIR", "HIPPO_TELEMETRY_DIR", "HIPPO_PENDING_DIR", "HIPPO_MEMORY_DIR"):
        monkeypatch.delenv(var, raising=False)


def _corpus(root):
    md = root / ".claude" / "memory"
    md.mkdir(parents=True)
    (md / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
    return md


def _legacy(root, kind):
    return root / ".claude" / f".memory-{kind}"


def _old_layout(root):
    """What an older hippo left: the three dirs beside the corpus, self-ignoring."""
    md = _corpus(root)
    for kind in _KINDS:
        d = _legacy(root, kind)
        d.mkdir()
        (d / ".gitignore").write_text("*\n")
    (_legacy(root, "index") / "manifest.json").write_text('{"entries": []}')
    tel = _legacy(root, "telemetry")
    (tel / "recall_events.jsonl").write_text('{"n": 1}\n{"n": 2}\n')
    (tel / "presence").mkdir()
    (tel / "presence" / "s1.json").write_text("{}")
    (_legacy(root, "pending") / "capture-a.json").write_text('{"seed": "a"}')
    return md


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


# --------------------------------------------------------------------------- #
# MIG-9: where they resolve
# --------------------------------------------------------------------------- #
def test_a_project_corpus_resolves_its_derived_dirs_in_dot_hippo(tmp_path):
    md = str(_corpus(tmp_path))
    home = tmp_path / ".hippo"
    assert default_index_dir(md) == str(home / "index")
    assert default_telemetry_dir(md) == str(home / "telemetry")
    assert default_pending_dir(md) == str(home / "pending")
    assert not home.exists()  # resolving never writes: it is on the recall hot path


def test_an_unmoved_old_dir_is_read_where_it_is(tmp_path):
    md = str(_old_layout(tmp_path))
    assert default_index_dir(md) == str(_legacy(tmp_path, "index"))
    assert default_telemetry_dir(md) == str(_legacy(tmp_path, "telemetry"))
    assert default_pending_dir(md) == str(_legacy(tmp_path, "pending"))


def test_once_the_live_dir_exists_an_old_one_is_not_read(tmp_path):
    md = str(_old_layout(tmp_path))
    (tmp_path / ".hippo" / "index").mkdir(parents=True)
    assert default_index_dir(md) == str(tmp_path / ".hippo" / "index")
    assert default_pending_dir(md) == str(_legacy(tmp_path, "pending"))  # each kind on its own


def test_a_corpus_outside_dot_claude_keeps_its_sibling_dirs(tmp_path, monkeypatch):
    """No ``.claude/`` to ride in: a HIPPO_MEMORY_DIR elsewhere and the user tier's
    ``~/.claude/hippo-memory`` keep the old siblings (never a stray ``~/.hippo``)."""
    elsewhere = tmp_path / "corpus" / "memory"
    elsewhere.mkdir(parents=True)
    assert default_index_dir(str(elsewhere)) == str(tmp_path / "corpus" / ".memory-index")
    user = tmp_path / "home" / ".claude" / "hippo-memory"
    user.mkdir(parents=True)
    assert default_index_dir(str(user)) == str(tmp_path / "home" / ".claude" / ".memory-index")
    assert D.derived_home(str(user)) is None


def test_env_overrides_still_win(tmp_path, monkeypatch):
    md = str(_corpus(tmp_path))
    monkeypatch.setenv("HIPPO_INDEX_DIR", str(tmp_path / "x"))
    assert default_index_dir(md) == str(tmp_path / "x")


def test_the_container_self_ignores_so_git_status_stays_clean(tmp_path):
    from memory.provenance import ensure_self_ignoring_dir

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    md = str(_corpus(tmp_path))
    td = default_telemetry_dir(md)
    ensure_self_ignoring_dir(td)
    (tmp_path / ".hippo" / "stray.txt").write_text("x")  # dropped beside the payload dirs
    assert (tmp_path / ".hippo" / ".gitignore").read_text() == "*\n"
    assert (tmp_path / ".hippo" / "telemetry" / ".gitignore").read_text() == "*\n"
    assert ".hippo" not in _git(tmp_path, "status", "--porcelain", "--untracked-files=all")


def test_the_pending_queue_counts_into_its_sibling_telemetry(tmp_path):
    from memory.capture_queue import _queue_telemetry_dir

    (tmp_path / ".hippo" / "telemetry").mkdir(parents=True)
    pd = str(tmp_path / ".hippo" / "pending")
    assert _queue_telemetry_dir(pd, None, None) == str(tmp_path / ".hippo" / "telemetry")


@pytest.mark.parametrize("layout,expect", [
    ("fresh", ".hippo/telemetry"),
    ("old", ".claude/.memory-telemetry"),
    ("both", ".hippo/telemetry"),
])
def test_the_bash_helper_resolves_like_the_engine(tmp_path, layout, expect):
    md = str(_corpus(tmp_path))
    if layout in ("old", "both"):
        _legacy(tmp_path, "telemetry").mkdir()
    if layout == "both":
        (tmp_path / ".hippo" / "telemetry").mkdir(parents=True)
    lib = os.path.join(_PLUGIN_ROOT, "hooks", "_resolve_py.sh")
    out = subprocess.run(
        ["/bin/bash", "-c", f'. "{lib}"; hippo_derived_dir "$1" telemetry; printf %s "$HIPPO_DERIVED_DIR"',
         "_", str(tmp_path)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert out == str(tmp_path / expect)
    assert out == default_telemetry_dir(md)
