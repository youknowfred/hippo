"""HOT-5: the PostToolUse fast path logs a file touch without a Python spawn when Python
would only append the outcome row — and writes exactly the row Python writes.

Each case runs the real hook script under /bin/bash (3.2 on macOS) twice from identical
starting state: once as shipped, once with HIPPO_DISABLE_TOUCH_FASTPATH=1 (always Python).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from memory.jit import refresh_touch_cache
from memory.outcome import injection_precision

_PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugin"))
_HOOK = os.path.join(_PLUGIN_ROOT, "hooks", "memory_post_tool.sh")
_SID = "sess-fast-1"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _bin(tmp_path):
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    for tool in ("cat", "printf", "sed", "tr", "head", "grep", "stat", "date", "perl", "git",
                 "basename", "dirname", "touch"):
        src = shutil.which(tool)
        if src and not (b / tool).exists():
            os.symlink(src, b / tool)
    if not (b / "python3").exists():
        os.symlink(sys.executable, b / "python3")
    return str(b)


def _project(root, *, cited="src/cited.py", mtype="feedback"):
    md = root / ".claude" / "memory"
    md.mkdir(parents=True)
    (md / "MEMORY.md").write_text("# Memory\n\n## User\n", encoding="utf-8")
    (md / "note.md").write_text(
        f'---\nname: note\ndescription: "about cited"\ntype: {mtype}\ncited_paths: ["{cited}"]\n'
        '---\nbody\n', encoding="utf-8")
    idx = root / ".claude" / ".memory-index"
    idx.mkdir(parents=True)
    assert refresh_touch_cache(str(md), str(idx))  # the real touchmap for this corpus
    td = root / ".claude" / ".memory-telemetry"
    (td / "presence").mkdir(parents=True)
    (td / "presence" / f"{_SID}.json").write_text(
        json.dumps({"session_id": _SID, "branch": "main", "head": "abc", "ts": time.time(),
                    "checked_ts": time.time(), "nudged": True}), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return td


def _touch(tmp_path, tag, tool, rel, *, fast=True, extra=None):
    root = tmp_path / tag
    if not root.exists():
        _project(root)
    data = tmp_path / f"data-{tag}"
    (data / "venv" / "bin").mkdir(parents=True, exist_ok=True)
    if not (data / "venv" / "bin" / "python").exists():
        os.symlink(sys.executable, data / "venv" / "bin" / "python")
    env = {
        "PATH": _bin(tmp_path), "HOME": str(tmp_path / "home"),
        "CLAUDE_PROJECT_DIR": str(root), "CLAUDE_PLUGIN_ROOT": _PLUGIN_ROOT,
        "CLAUDE_PLUGIN_DATA": str(data), "HIPPO_DISABLE_DENSE": "1", "HIPPO_TRUST_NONGIT": "1",
    }
    if not fast:
        env["HIPPO_DISABLE_TOUCH_FASTPATH"] = "1"
    env.update(extra or {})
    payload = {"session_id": _SID, "hook_event_name": "PostToolUse", "tool_name": tool,
               "tool_input": {"file_path": str(root / rel)}, "tool_response": {"ok": True}}
    proc = subprocess.run(["/bin/bash", _HOOK], input=json.dumps(payload), capture_output=True,
                          text=True, timeout=60, env=env)
    assert proc.returncode == 0
    td = root / ".claude" / ".memory-telemetry"
    rows = [json.loads(ln) for ln in (td / "outcome_events.jsonl").read_text().splitlines() if ln]
    spool = (td / "usage_spool.jsonl").read_text().splitlines() if (td / "usage_spool.jsonl").exists() else []
    return rows[-1], json.loads(spool[-1])["action"] if spool else None, proc.stdout


@pytest.mark.parametrize("tool,rel", [
    ("Read", "src/plain.py"),
    ("Edit", "src/plain.py"),
    ("Write", "docs/new file.md"),
    ("Read", ".claude/worktrees/wt1/src/plain.py"),
])
def test_the_fast_row_equals_the_python_row(tmp_path, tool, rel):
    fast_row, fast_how, _ = _touch(tmp_path, "a", tool, rel, fast=True)
    py_row, py_how, _ = _touch(tmp_path, "b", tool, rel, fast=False)
    assert fast_how == "fast" and py_how == "spawn"
    strip = lambda r: {k: v for k, v in r.items() if k != "ts"}  # noqa: E731
    assert strip(fast_row) == strip(py_row)
    assert isinstance(fast_row["ts"], float)


@pytest.mark.parametrize("tool,rel,why", [
    ("Read", "src/cited.py", "touchmap knows the basename (JIT reminder / cited_by)"),
    ("Edit", ".claude/memory/MEMORY.md", "the floor nag"),
    ("NotebookEdit", "nb.ipynb", "a tool the fast path never handles"),
])
def test_python_still_runs_when_it_has_work(tmp_path, tool, rel, why):
    _row, how, _ = _touch(tmp_path, "c", tool, rel)
    assert how == "spawn", why


def test_a_due_fleet_check_spawns(tmp_path):
    root = tmp_path / "d"
    td = _project(root)
    doc = td / "presence" / f"{_SID}.json"
    old = time.time() - 120
    os.utime(doc, (old, old))
    _row, how, _ = _touch(tmp_path, "d", "Read", "src/plain.py")
    assert how == "spawn"


def test_a_shared_tree_edit_that_may_owe_the_nudge_spawns(tmp_path):
    root = tmp_path / "e"
    td = _project(root)
    doc = td / "presence" / f"{_SID}.json"
    d = json.loads(doc.read_text())
    d.pop("nudged")
    doc.write_text(json.dumps(d))
    (td / "presence" / "other-session.json").write_text(json.dumps({"session_id": "other"}))
    _row, how, _ = _touch(tmp_path, "e", "Edit", "src/plain.py")
    assert how == "spawn"
    _row, how, _ = _touch(tmp_path, "e", "Edit", ".claude/worktrees/wt1/src/plain.py")
    assert how == "fast"  # a worktree path is never a shared-tree mutation


def test_kpi2_is_identical_on_a_replayed_fixture(tmp_path):
    """The same touches through both paths give the same injection precision."""
    touches = [("Read", "src/plain.py"), ("Edit", "src/cited.py"), ("Read", "src/other.py"),
               ("Edit", "src/plain.py"), ("Read", "src/cited.py")]
    results = {}
    for tag, fast in (("f", True), ("p", False)):
        root = tmp_path / tag
        td = _project(root)
        md = str(root / ".claude" / "memory")
        with open(td / "episode_buffer.jsonl", "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time() - 30, "session_id": _SID,
                                 "query_preview": "q", "recalled_names": ["note"]}) + "\n")
        for tool, rel in touches:
            _touch(tmp_path, tag, tool, rel, fast=fast)
        results[tag] = injection_precision(md, str(td))
    assert results["f"] == results["p"] and results["f"]["hits"] == 1


def _commit(root):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@e"}
    (root / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(root), "add", "f.txt"], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "c"], check=True, env=env)
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()
    branch = subprocess.run(["git", "-C", str(root), "symbolic-ref", "--short", "-q", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    return branch, head


def test_a_due_fleet_check_with_an_unmoved_head_stays_fast(tmp_path):
    root = tmp_path / "g"
    td = _project(root)
    branch, head = _commit(root)
    doc = td / "presence" / f"{_SID}.json"
    d = json.loads(doc.read_text())
    d.update(branch=branch, head=head)
    doc.write_text(json.dumps(d))
    old = time.time() - 120
    os.utime(doc, (old, old))
    _row, how, _ = _touch(tmp_path, "g", "Read", "src/plain.py")
    assert how == "fast"
    assert time.time() - os.stat(doc).st_mtime < 30  # the debounce clock was refreshed
    d.update(head="0" * 40)  # HEAD moved since the doc: the tripwire may speak
    doc.write_text(json.dumps(d))
    os.utime(doc, (old, old))
    _row, how, _ = _touch(tmp_path, "g", "Read", "src/plain.py")
    assert how == "spawn"


@pytest.mark.parametrize("cited_rows,expect", [(39, "spawn"), (40, "fast")])
def test_a_cited_path_is_fast_once_the_sessions_quota_is_spent(tmp_path, cited_rows, expect):
    root = tmp_path / f"q{cited_rows}"
    td = _project(root, mtype="project")  # no reminder candidates at all
    (td / "jit").mkdir()
    (td / "jit" / f"{_SID}.json").write_text(json.dumps(
        {"files": [], "emitted": [], "lines": 0, "cited_rows": cited_rows}))
    row, how, _ = _touch(tmp_path, f"q{cited_rows}", "Read", "src/cited.py")
    assert how == expect
    # Below the cap Python stamps the session's last cited_by row; at the cap nothing would.
    assert ("cited_by" in row) == (cited_rows < 40)


def test_the_shell_cap_matches_the_engine():
    from memory.jit import MAX_PROVENANCE_ROWS_PER_SESSION

    sh = open(os.path.join(_PLUGIN_ROOT, "hooks", "_resolve_py.sh"), encoding="utf-8").read()
    assert f"HIPPO_JIT_CITED_ROWS_CAP={MAX_PROVENANCE_ROWS_PER_SESSION}" in sh


_GNU_STAT = """#!/bin/bash
# Behaves like GNU coreutils stat for the two forms the hook tries: -c FORMAT reads a format;
# -f means --file-system, so a BSD-style '-f %m FILE' treats '%m' as a missing FILE.
if [ "$1" = "-c" ] && [ "$2" = "%Y" ]; then
  exec perl -e 'print((stat $ARGV[0])[9], "\\n")' "$3"
fi
if [ "$1" = "-f" ]; then
  echo "  File: \\"$3\\""
  echo "    ID: 1000 Namelen: 255 Type: ext2/ext3"
  echo "stat: cannot read file system information for '$2': No such file or directory" >&2
  exit 1
fi
exit 1
"""


def test_the_fast_path_works_with_gnu_stat(tmp_path):
    """Linux CI: GNU stat must be read right, or every touch silently spawns Python."""
    bindir = _bin(tmp_path)
    os.remove(os.path.join(bindir, "stat"))
    with open(os.path.join(bindir, "stat"), "w") as fh:
        fh.write(_GNU_STAT)
    os.chmod(os.path.join(bindir, "stat"), 0o755)
    _row, how, _ = _touch(tmp_path, "gnu", "Read", "src/plain.py")
    assert how == "fast"
