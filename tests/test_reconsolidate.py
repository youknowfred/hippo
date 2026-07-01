"""Tests for memory/reconsolidate.py — the recall-triggered reconsolidation worklist.

Hermetic: a throwaway git repo (the `repo`/`memory_dir` fixtures) + a synthesized recall-event
ledger in a tmp telemetry dir. Nothing touches the real ~/.claude memory dir or the real repo.
"""

from __future__ import annotations

import inspect
import json
import os
import time

import memory.reconsolidate as R
from memory.telemetry import read_reconsolidation_events

from .conftest import git_commit, write_file

# find_stale()'s default `since` window ("2 years ago") is WALL-CLOCK-relative, not relative
# to the pinned commit times below -- a wide fixed window (mirrors test_staleness.py's `_ALL`)
# keeps the pinned-epoch fixtures stale-detectable regardless of when the test actually runs.
_ALL = "2000-01-01"


def _mem(name, cited, source_commit):
    cp = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    sc = f'"{source_commit}"' if source_commit is not None else "null"
    return f"---\nname: {name}\ndescription: \"{name} description\"\ncited_paths: {cp}\nsource_commit: {sc}\n---\nbody for {name}\n"


def _seed_events(td, session_names):
    """session_names: ordered [(session_id, [names...]), ...] -- preserves call order so
    ledger append-order matches the intended chronological session order."""
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "recall_events.jsonl"), "w", encoding="utf-8") as fh:
        for sid, names in session_names:
            fh.write(json.dumps({"session_id": sid, "names": names, "backend": "bm25"}) + "\n")


# --------------------------------------------------------------------------- #
# recalled_stale_worklist — the intersection
# --------------------------------------------------------------------------- #
def test_worklist_is_intersection_of_recalled_and_stale(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_alpha.md", _mem("m_alpha", ["src/foo.py"], c1))
    write_file(memory_dir, "m_beta.md", _mem("m_beta", ["src/foo.py"], c1))  # also stale, never recalled
    write_file(repo, "src/foo.py", "x = 2\n")  # cited file drifts -- BOTH become stale
    git_commit(repo, "c2", 1_700_000_100)

    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_alpha"])])  # only m_alpha was ever recalled

    worklist = R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, since=_ALL)
    names = [w["name"] for w in worklist]
    assert names == ["m_alpha"]  # m_beta is stale but never recalled -> excluded


def test_worklist_excludes_recalled_but_not_stale(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_fresh.md", _mem("m_fresh", ["src/foo.py"], c1))
    git_commit(repo, "c2", 1_700_000_100)  # nothing cited changes -- m_fresh stays fresh

    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_fresh"])])

    worklist = R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td)
    assert worklist == []  # recalled but not stale -> not on the worklist


def test_worklist_excludes_stale_but_never_recalled(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_cold.md", _mem("m_cold", ["src/foo.py"], c1))
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)

    td = os.path.join(repo, "tele")
    # empty ledger -- nothing was ever recalled
    worklist = R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td)
    assert worklist == []


def test_worklist_most_recently_drifted_first(repo, memory_dir):
    write_file(repo, "src/old.py", "x = 1\n")
    write_file(repo, "src/new.py", "y = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_old_drift.md", _mem("m_old_drift", ["src/old.py"], c1))
    write_file(memory_dir, "m_new_drift.md", _mem("m_new_drift", ["src/new.py"], c1))
    write_file(repo, "src/old.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)  # old.py drifts first
    write_file(repo, "src/new.py", "y = 2\n")
    git_commit(repo, "c3", 1_700_000_200)  # new.py drifts later -- most recent

    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_old_drift", "m_new_drift"])])

    worklist = R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, since=_ALL)
    assert [w["name"] for w in worklist] == ["m_new_drift", "m_old_drift"]


def test_worklist_window_sessions_excludes_old_sessions(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_a.md", _mem("m_a", ["src/foo.py"], c1))
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)

    td = os.path.join(repo, "tele")
    # m_a was recalled only in the OLDEST session; 5 newer sessions recalled nothing relevant
    _seed_events(
        td,
        [("s1", ["m_a"]), ("s2", []), ("s3", []), ("s4", []), ("s5", []), ("s6", [])],
    )

    assert R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, window_sessions=3, since=_ALL) == []
    in_window = R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, window_sessions=10, since=_ALL)
    assert [w["name"] for w in in_window] == ["m_a"]


def test_worklist_empty_ledger_never_raises(repo, memory_dir):
    assert R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=str(repo) + "/missing") == []


def test_worklist_never_raises_on_bogus_dirs():
    assert R.recalled_stale_worklist("/no/such/memory", "/no/such/repo") == []


# --------------------------------------------------------------------------- #
# semantic_reverify — per-item write primitive (wraps provenance.reverify_file)
# --------------------------------------------------------------------------- #
def test_semantic_reverify_graduate_clears_flag_and_logs_outcome(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    body = _mem("m_a", ["src/foo.py"], c1)
    write_file(memory_dir, "m_a.md", body)
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)

    td = os.path.join(repo, "tele")
    result = R.semantic_reverify("m_a", "graduate", memory_dir, repo, telemetry_dir=td)
    assert result == {"name": "m_a", "outcome": "graduate", "cleared": True, "logged": True, "error": None}

    # the flag is genuinely cleared -- m_a no longer shows up as stale
    from memory.staleness import find_stale

    assert all(s["name"] != "m_a" for s in find_stale(memory_dir, repo, since=_ALL))

    # body is byte-identical (reverify_file's own contract) -- only frontmatter changed
    with open(os.path.join(memory_dir, "m_a.md"), encoding="utf-8") as fh:
        new_text = fh.read()
    assert new_text.split("---\n", 2)[-1] == body.split("---\n", 2)[-1]

    evs = list(read_reconsolidation_events(td))
    assert len(evs) == 1
    assert evs[0] == {"ts": evs[0]["ts"], "name": "m_a", "outcome": "graduate"}


def test_semantic_reverify_fix_also_clears_flag(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_a.md", _mem("m_a", ["src/foo.py"], c1))
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)

    td = os.path.join(repo, "tele")
    result = R.semantic_reverify("m_a", "fix", memory_dir, repo, telemetry_dir=td)
    assert result["cleared"] is True
    assert result["logged"] is True


def test_semantic_reverify_demote_never_clears_flag(repo, memory_dir):
    """The FM2 neutralization: a confirmed-WRONG memory must stay flagged, never silently
    re-baselined just because an outcome was logged."""
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_bad.md", _mem("m_bad", ["src/foo.py"], c1))
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)

    td = os.path.join(repo, "tele")
    result = R.semantic_reverify("m_bad", "demote", memory_dir, repo, telemetry_dir=td)
    assert result["cleared"] is False
    assert result["logged"] is True
    assert result["error"] is None

    from memory.staleness import find_stale

    assert any(s["name"] == "m_bad" for s in find_stale(memory_dir, repo, since=_ALL))  # STILL flagged

    evs = list(read_reconsolidation_events(td))
    assert evs[0]["outcome"] == "demote"


def test_semantic_reverify_invalid_outcome_writes_nothing(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_a.md", _mem("m_a", ["src/foo.py"], c1))

    td = os.path.join(repo, "tele")
    result = R.semantic_reverify("m_a", "approved", memory_dir, repo, telemetry_dir=td)
    assert result["error"] is not None
    assert result["cleared"] is False
    assert result["logged"] is False
    assert list(read_reconsolidation_events(td)) == []


def test_semantic_reverify_refuses_unparseable_frontmatter(repo, memory_dir):
    bad = "---\nname: m_bad\ndescription: contains an unquoted colon: like this\ncited_paths: [\"src/foo.py\"]\nsource_commit: \"abc\"\n---\nbody\n"
    write_file(repo, "src/foo.py", "x = 1\n")
    git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_bad.md", bad)

    td = os.path.join(repo, "tele")
    result = R.semantic_reverify("m_bad", "graduate", memory_dir, repo, telemetry_dir=td)
    assert result["error"] is not None  # propagated straight from reverify_file's own guard
    assert result["cleared"] is False
    assert result["logged"] is False  # refused write -> outcome is NOT logged either


def test_semantic_reverify_never_raises_on_bogus_dirs():
    result = R.semantic_reverify("m", "graduate", "/no/such/memory", "/no/such/repo")
    assert result["error"] is not None or result["cleared"] is False  # degrades, never raises


def test_semantic_reverify_is_single_item_only_no_bulk_path():
    """reverify_head_only_no_bulk: confirm the signature takes ONE name, not a list/batch
    parameter -- there is no way to call this across many memories in one invocation."""
    sig = inspect.signature(R.semantic_reverify)
    params = list(sig.parameters)
    assert params[0] == "name"
    assert "names" not in params and "bulk" not in params and "all" not in params


# --------------------------------------------------------------------------- #
# reconsolidation_producer — SessionStart producer (silent when empty)
# --------------------------------------------------------------------------- #
def test_producer_silent_when_worklist_empty(repo, memory_dir):
    assert R.reconsolidation_producer(memory_dir, repo) is None


def test_producer_emits_bounded_block_when_worklist_nonempty(repo, memory_dir, monkeypatch):
    # reconsolidation_producer(memory_dir, repo_root) has a FIXED 2-arg dispatcher signature
    # (no `since` passthrough), so it always uses find_stale's default wall-clock-relative
    # window -- pin commits NEAR-NOW (not a fixed historical epoch) so they land inside it.
    now = int(time.time())
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", now - 200)
    write_file(memory_dir, "m_a.md", _mem("m_a", ["src/foo.py"], c1))
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", now - 100)

    td = os.path.join(repo, "tele")
    monkeypatch.setenv("MEMOBOT_TELEMETRY_DIR", td)
    _seed_events(td, [("s1", ["m_a"])])

    out = R.reconsolidation_producer(memory_dir, repo)
    assert out is not None
    assert "m_a" in out
    assert "Reconsolidation worklist" in out


def test_producer_truncates_past_max_items(repo, memory_dir, monkeypatch):
    now = int(time.time())
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", now - 200)
    names = [f"m_{i}" for i in range(R._MAX_WORKLIST_ITEMS + 5)]
    for n in names:
        write_file(memory_dir, f"{n}.md", _mem(n, ["src/foo.py"], c1))
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", now - 100)

    td = os.path.join(repo, "tele")
    monkeypatch.setenv("MEMOBOT_TELEMETRY_DIR", td)
    _seed_events(td, [("s1", names)])

    out = R.reconsolidation_producer(memory_dir, repo)
    assert "…and 5 more." in out


def test_producer_never_raises_on_bogus_dirs():
    assert R.reconsolidation_producer("/no/such/memory", "/no/such/repo") is None


def test_producer_signature_matches_dispatcher_contract():
    sig = inspect.signature(R.reconsolidation_producer)
    assert list(sig.parameters) == ["memory_dir", "repo_root"]
