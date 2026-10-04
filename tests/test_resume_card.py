"""SIG-2: the 'where was I' resume card — task continuity from the episode buffer.

Replays the most-recent session in THIS clone's (gitignored) episode buffer into a legible
orientation block: what you were working on, which memories you leaned on, and which cited
files changed since. Gated to substantive threads; strict caps; labelled clone-local. Uses a
real git repo + real logged episodes (test_capture convention).
"""

from __future__ import annotations

import os

import memory.session_start as S
from memory import telemetry as T
from memory.telemetry import default_telemetry_dir

from .conftest import git_commit, write_file


def _mem(md, name, cited, sc, desc="a note"):
    cp = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    write_file(
        md,
        f"{name}.md",
        f'---\nname: {name}\ndescription: "{desc}"\ntype: project\n'
        f'cited_paths: {cp}\nsource_commit: "{sc}"\n---\nbody\n',
    )


def _episode(md, repo, session_id, names, query):
    """Log one real episode for ``session_id`` — pins the current HEAD as its watermark."""
    T.log_episode(
        names, query=query, repo_root=repo, telemetry_dir=default_telemetry_dir(md), session_id=session_id
    )


def _setup(repo):
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md, exist_ok=True)
    write_file(repo, "src/app.py", "print('v1')\n")
    sc = git_commit(repo, "init", 1_700_000_000)
    return md, sc


def test_substantive_session_shows_resume_card(repo):
    md, sc = _setup(repo)
    _mem(md, "app-note", ["src/app.py"], sc, "how the entrypoint works")
    # a prior session: leaned on app-note, asked about the entrypoint (watermark = current HEAD)
    _episode(md, repo, "prev", ["app-note"], "how does the app entrypoint boot")
    # code moved on since that session's HEAD
    write_file(repo, "src/app.py", "print('v2')\n")
    git_commit(repo, "change app", 1_700_000_100)

    out = S.resume_card_producer(md, repo)
    assert out is not None
    assert out.startswith("🧭")
    assert "how does the app entrypoint boot" in out  # theme
    assert "app-note" in out  # relied-on memory
    assert "src/app.py" in out  # changed cited file since the watermark
    assert "this clone" in out  # clone-local label


def test_trivial_session_is_silent(repo):
    """One throwaway query, no recall -> below the substantive gate -> nothing."""
    md, _sc = _setup(repo)
    _episode(md, repo, "prev", [], "hi")
    assert S.resume_card_producer(md, repo) is None


def test_two_distinct_queries_is_substantive(repo):
    """>= _MIN_RESUME_THEMES distinct queries counts as substantive even with no recall."""
    md, _sc = _setup(repo)
    _episode(md, repo, "prev", [], "how does recall fusion weight body chunks")
    _episode(md, repo, "prev", [], "where is the trust gate applied")
    out = S.resume_card_producer(md, repo)
    assert out is not None
    assert "recall fusion" in out and "trust gate" in out


def test_cold_start_empty_buffer_is_silent(repo):
    """No episodes ever logged -> cold start -> nothing to resume."""
    md, _sc = _setup(repo)
    assert S.resume_card_producer(md, repo) is None


def test_only_changed_cited_files_appear(repo):
    """A changed file NOT cited by any memory is not listed; a cited one that changed is."""
    md, sc = _setup(repo)
    write_file(repo, "src/other.py", "y = 1\n")
    git_commit(repo, "add other", 1_700_000_050)
    _mem(md, "app-note", ["src/app.py"], sc)  # cites app.py only
    _episode(md, repo, "prev", ["app-note"], "the app entrypoint")
    # change BOTH files after the watermark; only the cited one should surface
    write_file(repo, "src/app.py", "print('v2')\n")
    write_file(repo, "src/other.py", "y = 2\n")
    git_commit(repo, "change both", 1_700_000_100)

    out = S.resume_card_producer(md, repo)
    assert out is not None
    assert "src/app.py" in out
    assert "src/other.py" not in out


def test_picks_the_most_recent_session(repo):
    md, sc = _setup(repo)
    _mem(md, "app-note", ["src/app.py"], sc)
    _episode(md, repo, "older", ["app-note"], "OLD session topic")
    git_commit(repo, "advance head", 1_700_000_100)  # takes real time -> later ts, distinct watermark
    _episode(md, repo, "newer", ["app-note"], "NEW session topic")

    out = S.resume_card_producer(md, repo)
    assert out is not None
    assert "NEW session topic" in out
    assert "OLD session topic" not in out


def test_wired_into_producers_and_flows_through_build_context(repo):
    md, sc = _setup(repo)
    _mem(md, "app-note", ["src/app.py"], sc, "entrypoint details")
    _episode(md, repo, "prev", ["app-note"], "the app entrypoint boot sequence")
    ctx = S.build_context(md, repo)
    assert "🧭 Where you left off" in ctx
    assert any(label == "resume_card" for label, _fn in S.PRODUCERS)


def test_bogus_dir_never_raises(tmp_path):
    bogus = str(tmp_path / "nope")
    assert S.resume_card_producer(bogus, bogus) is None


def test_themes_carry_no_markup(repo):
    """CLM-3: a pasted block's wrapper and any leftover tag never reach the card."""
    md, _sc = _setup(repo)
    _episode(md, repo, "prev", ["app-note"], '<pasted_content id="a091">\nCreating pull request for x')
    _episode(md, repo, "prev", ["app-note"], "check the <b>bold</b> idea and the docs")
    out = S.resume_card_producer(md, repo)
    assert out is not None and "<" not in out
    assert "Creating pull request for x" in out and "check the bold idea and the docs" in out


def test_leaned_on_lists_the_three_memories_served_most(repo):
    """CLM-3: served names from the recall ledger, at most three, never "+N more"."""
    md, sc = _setup(repo)
    names = [f"m{i}" for i in range(8)]
    for n in names:
        _mem(md, n, ["src/app.py"], sc)
    td = default_telemetry_dir(md)
    _episode(md, repo, "prev", names, "how does the app boot")
    for served in (["m5", "m2"], ["m5", "m7"], ["m5", "m2", "m1"]):
        results = [{"name": n, "backend": "bm25", "score": 0.1, "rank": i + 1} for i, n in enumerate(served)]
        T.log_recall_event(results, query="q", k=10, latency_ms=1.0, telemetry_dir=td, session_id="prev")
    out = S.resume_card_producer(md, repo)
    line = next(ln for ln in out.splitlines() if "you leaned on" in ln)
    assert line.endswith("you leaned on: m5, m2, m7") or line.endswith("you leaned on: m5, m2, m1")
    assert "more" not in line
