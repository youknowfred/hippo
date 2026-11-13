"""HOT-15 (b): find_stale's git half is ONE history walk per scan, memoized per HEAD.

A cited set that fits one pathspec chunk keeps SHP-6's scoped call (tests/test_staleness.py
pins it); a bigger one is one unscoped walk, streamed and output-capped, filtered in memory.
A walk cut short — by its budget or by the cap — says it is partial and how many memories it
could not decide. Fixtures are real throwaway git repos (``conftest.repo``) written from scratch.
"""

from __future__ import annotations

import subprocess as sp
import threading

import pytest

from memory import session_start as SS
from memory import staleness as ST
from memory import staleness_scan as SC
from memory.staleness import find_stale

from .conftest import git_commit, write_file

_ALL = "2000-01-01"
# More than one 8 KB pathspec chunk of cited paths that never exist: forces the unscoped walk.
_MANY = [f"src/deep/{'d' * 60}/m_{i}.py" for i in range(400)]


def _memory(cited, source_commit):
    cp = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    return f'---\nname: A\ntype: project\ncited_paths: {cp}\nsource_commit: "{source_commit}"\n---\nbody\n'


@pytest.fixture(autouse=True)
def _fresh_memo():
    SC._SCANS.clear()
    SC._SHA_TIMES.clear()
    yield
    SC._SCANS.clear()
    SC._SHA_TIMES.clear()


def _spy(monkeypatch):
    """Every git argv: ``subprocess.run`` (the scoped call, run_git) starts its process through
    ``subprocess.Popen`` too, as the unscoped walk does, so one spy sees them all once."""
    calls = []
    real_popen = ST.subprocess.Popen

    class Popen(real_popen):
        def __init__(self, args, *a, **kw):
            calls.append(list(args))
            super().__init__(args, *a, **kw)

    monkeypatch.setattr(ST.subprocess, "Popen", Popen)
    return calls


def _logs(calls):
    return [c for c in calls if "log" in c]


def _three_memories(repo, memory_dir, many=False):
    for p in ("src/a.py", "src/b.py", "src/c.py"):
        write_file(repo, p, "1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_a.md", _memory(["src/a.py"], c1))
    write_file(memory_dir, "m_b.md", _memory(["src/b.py"], c1))
    write_file(memory_dir, "m_c.md", _memory(["src/c.py"], c1))
    if many:
        write_file(memory_dir, "m_z.md", _memory(_MANY, c1))
    write_file(repo, "src/c.py", "2\n")
    git_commit(repo, "c2", 1_700_000_100)  # oldest drift: c.py
    write_file(repo, "src/a.py", "2\n")
    git_commit(repo, "c3", 1_700_000_200)  # newest drift: a.py
    return c1


# --------------------------------------------------------------------------- #
# The two shapes: one walk each
# --------------------------------------------------------------------------- #
def test_a_small_cited_set_is_one_scoped_walk(monkeypatch, repo, memory_dir):
    _three_memories(repo, memory_dir)
    calls = _spy(monkeypatch)
    assert {s["name"] for s in find_stale(memory_dir, repo, since=_ALL)} == {"m_a", "m_c"}
    logs = _logs(calls)
    assert len(logs) == 1 and logs[0][logs[0].index("--") + 1:] == [":/src/a.py", ":/src/b.py", ":/src/c.py"]


def test_many_cited_paths_cost_one_unscoped_walk(monkeypatch, repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    write_file(repo, "src/uncited.py", "y = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_alpha.md", _memory(["src/foo.py"] + _MANY, c1))  # 5 old chunks
    write_file(repo, "src/foo.py", "x = 2\n")
    write_file(repo, "src/uncited.py", "y = 2\n")
    git_commit(repo, "c2", 1_700_000_100)
    calls = _spy(monkeypatch)
    stale = find_stale(memory_dir, repo, since=_ALL)
    assert [s["name"] for s in stale] == ["m_alpha"] and stale[0]["changed_paths"] == ["src/foo.py"]
    logs = _logs(calls)
    assert len(logs) == 1 and "--" not in logs[0] and "--name-only" in logs[0]
    times, cut = ST._path_change_times(repo, _ALL, ["src/foo.py"] + _MANY)
    assert times == {"src/foo.py": 1_700_000_100} and cut is False  # uncited paths never come back


def test_both_shapes_give_the_same_verdicts(repo, memory_dir):
    _three_memories(repo, memory_dir)
    small = find_stale(memory_dir, repo, since=_ALL)
    SC._SCANS.clear()
    write_file(memory_dir, "m_z.md", _memory(_MANY, "0" * 40))  # unjudgeable: no baseline resolves
    big = find_stale(memory_dir, repo, since=_ALL)
    assert big == small


# --------------------------------------------------------------------------- #
# The memo
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("many", [False, True])
def test_a_second_call_at_one_head_walks_no_history(monkeypatch, repo, memory_dir, many):
    _three_memories(repo, memory_dir, many=many)
    first = find_stale(memory_dir, repo, since=_ALL)
    calls = _spy(monkeypatch)
    again = find_stale(memory_dir, repo, since=_ALL)
    assert again == first
    assert not [c for c in calls if "log" in c or "show" in c], calls  # only `git rev-parse HEAD`


def test_a_scoped_result_is_never_served_to_another_cited_set(monkeypatch, repo, memory_dir):
    _three_memories(repo, memory_dir)
    a, _ = ST._path_change_times(repo, _ALL, ["src/a.py"])
    calls = _spy(monkeypatch)
    c, _ = ST._path_change_times(repo, _ALL, ["src/c.py"])
    assert a == {"src/a.py": 1_700_000_200} and c == {"src/c.py": 1_700_000_100}
    assert len(_logs(calls)) == 1  # a different set is walked again, not filtered from set A
    calls.clear()
    assert ST._path_change_times(repo, _ALL, ["src/c.py"])[0] == c and not _logs(calls)


def test_a_new_commit_is_seen_by_the_next_call(repo, memory_dir):
    _three_memories(repo, memory_dir)
    assert {s["name"] for s in find_stale(memory_dir, repo, since=_ALL)} == {"m_a", "m_c"}
    write_file(repo, "src/b.py", "2\n")
    git_commit(repo, "c4", 1_700_000_300)
    assert {s["name"] for s in find_stale(memory_dir, repo, since=_ALL)} == {"m_a", "m_b", "m_c"}


# --------------------------------------------------------------------------- #
# A walk cut short: by the budget (either shape) or by the output cap (unscoped)
# --------------------------------------------------------------------------- #
def _timeout_after(monkeypatch, keep_commits):
    """The scoped call raises TimeoutExpired carrying only the newest ``keep_commits`` commits
    of its real output (and a torn last line), as a walk cut short by its budget does."""
    real = sp.run

    def run(args, **kw):
        if "log" in args:
            out = real(args, **kw).stdout
            marks = [i for i in range(len(out)) if out.startswith(SC.CHANGE_MARKER, i)]
            cut = marks[keep_commits] if keep_commits < len(marks) else len(out)
            raise sp.TimeoutExpired(cmd=args, timeout=kw.get("timeout"), output=out[:cut] + "src/tor")
        return real(args, **kw)

    monkeypatch.setattr(ST.subprocess, "run", run)


def test_a_timed_out_walk_keeps_what_it_read_and_counts_the_rest(monkeypatch, repo, memory_dir):
    _three_memories(repo, memory_dir)
    _timeout_after(monkeypatch, keep_commits=1)  # only c3 (a.py) was read
    diag: dict = {}
    stale = find_stale(memory_dir, repo, since=_ALL, diagnostics=diag)
    assert [s["name"] for s in stale] == ["m_a"]  # what the walk read still counts
    assert diag == {"timed_out": True, "partial": True, "unchecked": 2}  # m_b and m_c undecided


def test_a_timed_out_walk_is_not_memoized(monkeypatch, repo, memory_dir):
    _three_memories(repo, memory_dir)
    with monkeypatch.context() as m:
        _timeout_after(m, keep_commits=0)
        diag: dict = {}
        assert find_stale(memory_dir, repo, since=_ALL, diagnostics=diag) == []
        assert diag["unchecked"] == 3
    diag = {}
    assert {s["name"] for s in find_stale(memory_dir, repo, since=_ALL, diagnostics=diag)} == {"m_a", "m_c"}
    assert diag == {"timed_out": False, "partial": False, "unchecked": 0}


def _newest_commit_bytes(repo):
    """The byte length of the unscoped log's newest commit block (marker line + its paths)."""
    out = sp.run(["git", "-C", repo, "log", f"--since={_ALL}", "--name-only",
                  f"--format={SC.CHANGE_MARKER}%ct"], capture_output=True).stdout
    second = out.index(SC.CHANGE_MARKER.encode(), 1)
    return second


def test_the_unscoped_walk_stops_at_its_output_cap(monkeypatch, repo, memory_dir):
    _three_memories(repo, memory_dir, many=True)
    monkeypatch.setattr(SC, "OUTPUT_CAP_BYTES", _newest_commit_bytes(repo) + 2)  # c3 and one more line
    diag: dict = {}
    stale = find_stale(memory_dir, repo, since=_ALL, diagnostics=diag)
    assert [s["name"] for s in stale] == ["m_a"]  # c3 (a.py) was read before the cap
    assert diag == {"timed_out": False, "partial": True, "output_capped": True, "unchecked": 3}
    assert not SC._SCANS  # a capped walk is never memoized
    monkeypatch.setattr(SC, "OUTPUT_CAP_BYTES", 64 * 1024 * 1024)
    diag = {}
    assert {s["name"] for s in find_stale(memory_dir, repo, since=_ALL, diagnostics=diag)} == {"m_a", "m_c"}
    assert diag == {"timed_out": False, "partial": False, "unchecked": 0}


def test_the_unscoped_walk_stops_at_its_budget(monkeypatch, repo, memory_dir):
    _three_memories(repo, memory_dir, many=True)
    real = ST.subprocess.Popen
    first = []

    class Stalls:
        """git that prints its newest commit, then hangs until killed."""

        def __init__(self, args, **kw):
            with real(args, stdout=sp.PIPE, stderr=sp.DEVNULL) as p:
                out = p.stdout.read()
            first.append(out[: out.index(SC.CHANGE_MARKER.encode(), 1)])
            self._killed = threading.Event()
            outer = self

            class _Out:
                def __iter__(self):
                    yield from first[0].splitlines(keepends=True)
                    outer._killed.wait(10)

                def close(self):
                    pass

            self.stdout = _Out()

        def poll(self):
            return 0 if self._killed.is_set() else None

        def kill(self):
            self._killed.set()

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(ST.subprocess, "Popen",
                        lambda args, *a, **kw: Stalls(args) if "log" in args else real(args, *a, **kw))
    monkeypatch.setattr(SC, "GIT_LOG_TIMEOUT", 0.2)
    diag: dict = {}
    stale = find_stale(memory_dir, repo, since=_ALL, diagnostics=diag)
    assert [s["name"] for s in stale] == ["m_a"]
    assert diag == {"timed_out": True, "partial": True, "unchecked": 3}
    assert not SC._SCANS


# --------------------------------------------------------------------------- #
# Commit times and the note
# --------------------------------------------------------------------------- #
def test_an_unresolvable_sha_is_asked_again(monkeypatch, repo, memory_dir):
    _three_memories(repo, memory_dir)
    write_file(memory_dir, "m_gone.md", _memory(["src/a.py"], "f" * 40))
    find_stale(memory_dir, repo, since=_ALL)
    calls = _spy(monkeypatch)
    find_stale(memory_dir, repo, since=_ALL)
    shows = [c for c in calls if "show" in c]
    assert len(shows) == 1
    asked = shows[0][shows[0].index("--ignore-missing") + 1:]
    assert asked == ["f" * 40]  # the resolvable sha's time came from memory; the missing one is asked again


@pytest.mark.parametrize("diag,why", [
    ({"timed_out": True, "partial": True, "unchecked": 7}, "timed out"),
    ({"timed_out": False, "partial": True, "output_capped": True, "unchecked": 7}, "stopped at its output cap"),
])
def test_the_session_note_says_why_and_how_many_memories_were_not_checked(monkeypatch, diag, why):
    import memory.session_start_signals as SG

    def fake(md, repo, diagnostics=None, **kw):
        diagnostics.update(diag)
        return []

    monkeypatch.setattr(SG, "find_stale", fake)
    out = SS.staleness_producer("md", "repo")
    assert why in out and "7 memories not checked" in out
