"""MIG-9/MIG-10/MIG-11: the index, telemetry and pending queue live in ``<project>/.hippo/``.

The Claude desktop app copies the checkout's whole ``.claude/`` (ignored files included) into
every worktree it creates, so the three dirs that used to sit beside the corpus rode into each
one. These pin where they resolve now, that an older layout is still read where it is, how
SessionStart moves it (one rename, then a fold of whatever a session still on an older hippo
writes), the telemetry stub a sleep schedule's log needs, and doctor's line.
"""

from __future__ import annotations

import json
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


# --------------------------------------------------------------------------- #
# MIG-10: the move
# --------------------------------------------------------------------------- #
def test_first_migration_renames_each_dir_byte_for_byte(tmp_path):
    md = str(_old_layout(tmp_path))
    before = {k: sorted(os.listdir(_legacy(tmp_path, k))) for k in _KINDS}
    assert D.migrate(md) == {"index": "moved", "telemetry": "moved", "pending": "moved"}
    for kind in _KINDS:
        assert not _legacy(tmp_path, kind).exists()
        assert sorted(os.listdir(tmp_path / ".hippo" / kind)) == before[kind]
    assert (tmp_path / ".hippo" / "telemetry" / "recall_events.jsonl").read_text() == '{"n": 1}\n{"n": 2}\n'
    assert (tmp_path / ".hippo" / ".gitignore").read_text() == "*\n"
    assert default_pending_dir(md) == str(tmp_path / ".hippo" / "pending")
    assert D.migrate(md) == {}  # nothing left: three stats, no writes


def test_nothing_to_move_creates_nothing(tmp_path):
    md = str(_corpus(tmp_path))
    assert D.migrate(md) == {}
    assert not (tmp_path / ".hippo").exists()
    assert D.migrate(str(tmp_path / "absent" / ".claude" / "memory")) == {}


def test_an_overridden_kind_is_left_alone(tmp_path, monkeypatch):
    md = str(_old_layout(tmp_path))
    monkeypatch.setenv("HIPPO_PENDING_DIR", str(tmp_path / "elsewhere"))
    out = D.migrate(md)
    assert "pending" not in out and _legacy(tmp_path, "pending").is_dir()


def test_a_held_lock_defers_the_move(tmp_path):
    fcntl = pytest.importorskip("fcntl")
    md = str(_old_layout(tmp_path))
    (tmp_path / ".hippo").mkdir()
    with open(tmp_path / ".hippo" / ".migrate.lock", "a") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        assert set(D.migrate(md).values()) == {"busy"}
    assert _legacy(tmp_path, "index").is_dir()
    assert D.migrate(md)["index"] == "moved"


def test_the_fold_merges_what_an_older_hippo_wrote_after_the_move(tmp_path):
    """Hooks are pinned to the version a session launched with: a session open across the
    update keeps writing the old paths. The next SessionStart folds that in."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    md = str(_old_layout(tmp_path))
    D.migrate(md)
    live = tmp_path / ".hippo"
    (live / "telemetry" / "session").write_text("new-token")
    (live / "telemetry" / "rollup_today.json").write_text('{"live": true}')
    (live / "pending" / "capture-b.json").write_text('{"seed": "b-live"}')
    # ...then an older session writes the old paths again.
    for kind in _KINDS:
        _legacy(tmp_path, kind).mkdir()
        (_legacy(tmp_path, kind) / ".gitignore").write_text("*\n")
    (_legacy(tmp_path, "index") / "manifest.json").write_text("{}")
    tel = _legacy(tmp_path, "telemetry")
    (tel / "recall_events.jsonl").write_text('{"n": 3}')  # a torn tail: no newline
    (tel / "presence").mkdir()
    (tel / "presence" / "s2.json").write_text("{}")
    (tel / "session").write_text("old-token")
    (tel / ".rollup.lock").write_text("")
    (tel / "rollup_today.json").write_text('{"old": true}')
    (tel / "stale.json.tmp.123").write_text("partial")
    pend = _legacy(tmp_path, "pending")
    (pend / "capture-c.json").write_text('{"seed": "c"}')
    (pend / "capture-b.json").write_text('{"seed": "b-old"}')
    (pend / "expired").mkdir()
    (pend / "expired" / "capture-z.json").write_text('{"seed": "z"}')

    out = D.migrate(md)
    assert out == {"index": "folded", "telemetry": "partial", "pending": "partial"}
    assert not _legacy(tmp_path, "index").exists()  # a cache: the live index wins
    ledger = (live / "telemetry" / "recall_events.jsonl").read_text()
    assert ledger == '{"n": 1}\n{"n": 2}\n{"n": 3}'  # appended, nothing lost
    assert (live / "telemetry" / "presence" / "s2.json").exists()
    assert (live / "telemetry" / "session").read_text() == "new-token"
    assert (live / "telemetry" / "rollup_today.json").read_text() == '{"live": true}'
    assert sorted(os.listdir(tel)) == [".gitignore", "rollup_today.json"]  # kept for doctor
    assert _git(tmp_path, "status", "--porcelain", "--untracked-files=all").strip() == "?? .claude/memory/MEMORY.md"
    assert (live / "pending" / "capture-c.json").exists()
    assert (live / "pending" / "expired" / "capture-z.json").exists()
    assert (live / "pending" / "capture-b.json").read_text() == '{"seed": "b-live"}'
    assert (pend / "capture-b.json").read_text() == '{"seed": "b-old"}'  # a seed is never dropped
    # Once the conflicts are resolved by hand, the next fold removes the old dirs.
    os.unlink(tel / "rollup_today.json")
    os.unlink(pend / "capture-b.json")
    assert D.migrate(md) == {"telemetry": "folded", "pending": "folded"}
    assert not (tmp_path / ".claude" / ".memory-telemetry").exists()
    assert not (tmp_path / ".claude" / ".memory-pending").exists()


def test_a_ledger_fold_lands_on_a_line_boundary(tmp_path):
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_text('{"x": 1}')
    b.write_text('{"x": 2}\n')
    D._append_file(str(b), str(a))
    assert a.read_text().splitlines() == ['{"x": 1}', '{"x": 2}']


# --------------------------------------------------------------------------- #
# MIG-10: the stub a sleep schedule's `>> .../sleep.log` needs
# --------------------------------------------------------------------------- #
def test_a_telemetry_dir_with_a_sleep_log_leaves_a_stub(tmp_path):
    md = str(_old_layout(tmp_path))
    tel = _legacy(tmp_path, "telemetry")
    (tel / "sleep.log").write_text("night 1\n")
    D.migrate(md)
    assert sorted(os.listdir(tel)) == [".gitignore", D.STUB_NOTE]
    assert str(tmp_path / ".hippo" / "telemetry") in (tel / D.STUB_NOTE).read_text()
    assert (tmp_path / ".hippo" / "telemetry" / "sleep.log").read_text() == "night 1\n"
    assert default_telemetry_dir(md) == str(tmp_path / ".hippo" / "telemetry")
    # The schedule's shell redirect still has a dir to append to...
    subprocess.run(f"echo 'night 2' >> '{tel / 'sleep.log'}'", shell=True, check=True)
    # ...and the next SessionStart folds the log in and keeps the stub.
    assert D.migrate(md) == {"telemetry": "folded"}
    assert (tmp_path / ".hippo" / "telemetry" / "sleep.log").read_text() == "night 1\nnight 2\n"
    assert sorted(os.listdir(tel)) == [".gitignore", D.STUB_NOTE]
    assert D.migrate(md) == {"telemetry": "stub"}


def test_a_stub_without_a_live_dir_is_not_read(tmp_path):
    md = str(_corpus(tmp_path))
    tel = _legacy(tmp_path, "telemetry")
    tel.mkdir()
    (tel / D.STUB_NOTE).write_text("moved")
    assert default_telemetry_dir(md) == str(tmp_path / ".hippo" / "telemetry")


@pytest.mark.parametrize("layout,expect", [
    ("fresh", ".hippo/telemetry"),
    ("old", ".claude/.memory-telemetry"),
    ("both", ".hippo/telemetry"),
    ("stub", ".hippo/telemetry"),
])
def test_the_bash_helper_resolves_like_the_engine(tmp_path, layout, expect):
    md = str(_corpus(tmp_path))
    if layout in ("old", "both", "stub"):
        _legacy(tmp_path, "telemetry").mkdir()
    if layout == "both":
        (tmp_path / ".hippo" / "telemetry").mkdir(parents=True)
    if layout == "stub":
        (_legacy(tmp_path, "telemetry") / D.STUB_NOTE).write_text("moved")
    lib = os.path.join(_PLUGIN_ROOT, "hooks", "_resolve_py.sh")
    out = subprocess.run(
        ["/bin/bash", "-c", f'. "{lib}"; hippo_derived_dir "$1" telemetry; printf %s "$HIPPO_DERIVED_DIR"',
         "_", str(tmp_path)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert out == str(tmp_path / expect)
    assert out == default_telemetry_dir(md)


def test_the_census_flags_a_schedule_whose_log_dir_is_gone(tmp_path):
    from memory import machine_census as MC

    repo = tmp_path / "repo"
    repo.mkdir()
    log = repo / ".claude" / ".memory-telemetry" / "sleep.log"
    cmd = f"cd {repo} && PYTHONPATH={_PLUGIN_ROOT} /bin/sh -m memory.sleep >> {log} 2>&1"
    entry = MC._classify_schedule(cmd)
    assert entry["status"] == "stale-path" and str(log.parent) in entry["missing"]
    log.parent.mkdir(parents=True)
    assert MC._classify_schedule(cmd)["status"] == "ok"


# --------------------------------------------------------------------------- #
# SessionStart and doctor
# --------------------------------------------------------------------------- #
def test_session_start_moves_the_old_dirs_before_refreshing_the_index(tmp_path, monkeypatch):
    from memory import session_start as S

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    md = _old_layout(tmp_path)
    (md / "note.md").write_text('---\nname: note\ndescription: "a note"\ntype: project\n---\nbody\n')
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    assert S.main(source="startup", session_id="s-mig") == 0
    for kind in _KINDS:
        assert not _legacy(tmp_path, kind).exists()
    manifest = json.loads((tmp_path / ".hippo" / "index" / "manifest.json").read_text())
    assert [e["name"] for e in manifest["entries"]] == ["note"]  # the refresh wrote the live index
    assert (tmp_path / ".hippo" / "pending" / "capture-a.json").exists()
    assert ".hippo" not in _git(tmp_path, "status", "--porcelain", "--untracked-files=all")


def _doctor(md, tmp_path):
    from memory.doctor_checks_env import DoctorContext
    from memory.doctor_checks_worktree import check_derived_dirs

    return check_derived_dirs(DoctorContext(memory_dir=str(md), repo_root=str(tmp_path)))


def test_doctor_names_an_unmoved_dir_with_the_move_by_hand(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    md = _old_layout(tmp_path)
    r = _doctor(md, tmp_path)
    assert r["status"] == "warn"
    assert "index, telemetry, pending queue still in" in r["message"]
    assert f"mv {_legacy(tmp_path, 'index')} {tmp_path / '.hippo' / 'index'}" in r["message"]


def test_doctor_is_quiet_once_moved_and_names_a_stub_and_leftovers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    md = _old_layout(tmp_path)
    (_legacy(tmp_path, "telemetry") / "sleep.log").write_text("x\n")
    D.migrate(md)
    r = _doctor(md, tmp_path)
    assert r["status"] == "ok"
    assert "stays as an empty stub" in r["message"] and "--print-schedule" in r["message"]
    pend = _legacy(tmp_path, "pending")
    pend.mkdir()
    (pend / "capture-a.json").write_text("{}")  # the live queue has one by this name
    D.migrate(md)
    r = _doctor(md, tmp_path)
    assert r["status"] == "warn"
    assert f"{pend} holds 1 entry" in r["message"] and "never overwrites" in r["message"]
    os.unlink(pend / "capture-a.json")
    import shutil

    shutil.rmtree(_legacy(tmp_path, "telemetry"))
    D.migrate(md)
    r = _doctor(md, tmp_path)
    assert r == {"status": "ok", "message": f"derived dirs: in {tmp_path / '.hippo'}, outside "
                 ".claude/, so worktrees do not carry them."}


def test_doctor_totals_the_copies_old_worktrees_carry_and_its_command_deletes_only_them(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    md = _corpus(tmp_path)
    for name in ("wt-a", "wt-b"):
        claude = tmp_path / ".claude" / "worktrees" / name / ".claude"
        for kind in _KINDS:
            (claude / f".memory-{kind}").mkdir(parents=True)
            (claude / f".memory-{kind}" / "blob").write_bytes(b"x" * 200_000)
        (claude / "memory").mkdir()
        (claude / "settings.json").write_text("{}")
    (tmp_path / ".claude" / "worktrees" / "wt-c" / ".claude").mkdir(parents=True)
    r = _doctor(md, tmp_path)
    assert "2 worktree(s) under" in r["message"] and "(1.2 MB)" in r["message"]
    cmd = r["message"].split("Delete them: ", 1)[1].rstrip(".")
    subprocess.run(["/bin/sh", "-c", cmd], check=True)
    for name in ("wt-a", "wt-b"):
        claude = tmp_path / ".claude" / "worktrees" / name / ".claude"
        assert sorted(os.listdir(claude)) == ["memory", "settings.json"]
    assert "worktree(s)" not in _doctor(md, tmp_path)["message"]
