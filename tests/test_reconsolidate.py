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
# GRA-9: review-adjacent worklist — the optional 1-hop "linked" graph column
# --------------------------------------------------------------------------- #
def _mem_body(name, cited, source_commit, body):
    """``_mem()`` with a caller-supplied body — so a stale item can carry [[wikilinks]]."""
    cp = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    return f"---\nname: {name}\ndescription: \"{name} description\"\ncited_paths: {cp}\nsource_commit: \"{source_commit}\"\n---\n{body}\n"


def _leaf(name, body=""):
    """A neighbor memory with NO provenance (never stale); body carries any [[links]]."""
    return f"---\nname: {name}\ndescription: \"{name} description\"\n---\n{body}\n"


def _seed_linked_stale(repo, memory_dir, when=1_700_000_000):
    """One stale+recalled memory (m_a) with one OUTBOUND ([[m_out]]) and one INBOUND
    (m_in links [[m_a]]) untyped neighbor. Returns the telemetry dir, ledger seeded."""
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", when)
    write_file(memory_dir, "m_a.md", _mem_body("m_a", ["src/foo.py"], c1, "see [[m_out]]"))
    write_file(memory_dir, "m_out.md", _leaf("m_out", "leaf"))
    write_file(memory_dir, "m_in.md", _leaf("m_in", "points at [[m_a]]"))
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", when + 100)
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_a"])])
    return td


def test_worklist_items_carry_1hop_linked_neighbors(repo, memory_dir):
    """The GRA-9 neighborhood: inbound + outbound wikilinks AND typed edges, deduped,
    sorted — a drifted memory's 1-hop neighbors are the next most likely to be wrong."""
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_stale.md", _mem_body("m_stale", ["src/foo.py"], c1, "see [[m_out]]"))
    write_file(memory_dir, "m_out.md", _leaf("m_out"))  # outbound wikilink neighbor
    write_file(memory_dir, "m_in.md", _leaf("m_in", "points at [[m_stale]]"))  # inbound
    # typed inbound neighbor (GRA-4): m_typed declares it supersedes m_stale
    write_file(
        memory_dir,
        "m_typed.md",
        '---\nname: m_typed\ndescription: "m_typed description"\nsupersedes: ["m_stale"]\n---\nsuccessor\n',
    )
    write_file(memory_dir, "m_far.md", _leaf("m_far", "no edges at all"))
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)

    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_stale"])])

    worklist = R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, since=_ALL)
    assert [w["name"] for w in worklist] == ["m_stale"]
    assert worklist[0]["linked"] == ["m_in", "m_out", "m_typed"]  # sorted; m_far absent


def test_worklist_linked_excludes_names_already_on_the_worklist(repo, memory_dir):
    """A neighbor that is INDEPENDENTLY stale+recalled gets its own worklist line — listing
    it again as someone's neighbor would double-report it."""
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_a.md", _mem_body("m_a", ["src/foo.py"], c1, "see [[m_b]] and [[m_c]]"))
    write_file(memory_dir, "m_b.md", _mem("m_b", ["src/foo.py"], c1))  # also stale+recalled
    write_file(memory_dir, "m_c.md", _leaf("m_c"))
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)

    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_a", "m_b"])])

    worklist = R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, since=_ALL)
    by_name = {w["name"]: w for w in worklist}
    assert set(by_name) == {"m_a", "m_b"}
    assert by_name["m_a"]["linked"] == ["m_c"]  # m_b is on the worklist -> excluded
    assert by_name["m_b"]["linked"] == []  # its only neighbor (m_a) is on the worklist too


def test_worklist_linked_neighbor_cap_is_bounded(repo, memory_dir):
    """The column is capped at _MAX_LINKED_NEIGHBORS (the producer renders into
    session_start's 9000-char budget) and the capped pick is deterministic (sorted)."""
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    n = R._MAX_LINKED_NEIGHBORS + 2
    body = "hub: " + " ".join(f"[[m_n{i}]]" for i in range(n))
    write_file(memory_dir, "m_hub.md", _mem_body("m_hub", ["src/foo.py"], c1, body))
    for i in range(n):
        write_file(memory_dir, f"m_n{i}.md", _leaf(f"m_n{i}"))
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)

    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_hub"])])

    worklist = R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, since=_ALL)
    assert worklist[0]["linked"] == [f"m_n{i}" for i in range(R._MAX_LINKED_NEIGHBORS)]


def test_worklist_no_linked_column_when_graph_unavailable(repo, memory_dir, monkeypatch):
    """Graph unavailable (no cache, no corpus, any failure) -> the worklist is EXACTLY its
    pre-GRA-9 self: same items, no "linked" key anywhere."""
    td = _seed_linked_stale(repo, memory_dir)
    monkeypatch.setattr("memory.links.build_graph", lambda *a, **k: None)
    worklist = R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, since=_ALL)
    assert [w["name"] for w in worklist] == ["m_a"]
    assert all("linked" not in w for w in worklist)


def test_worklist_survives_a_raising_graph(repo, memory_dir, monkeypatch):
    """Even an (impossible-by-contract) raising build_graph must never cost the caller the
    worklist itself — degrade to no column, not to []."""
    td = _seed_linked_stale(repo, memory_dir)

    def _boom(*a, **k):
        raise RuntimeError("graph exploded")

    monkeypatch.setattr("memory.links.build_graph", _boom)
    worklist = R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, since=_ALL)
    assert [w["name"] for w in worklist] == ["m_a"]
    assert all("linked" not in w for w in worklist)


def test_worklist_routes_through_cache_aware_graph_api(repo, memory_dir, monkeypatch):
    """GRA-9 uses the ONE canonical graph entry point WITH its persisted-cache fast path —
    build_graph(memory_dir, index_dir=default_index_dir(...)), never a bare corpus re-read."""
    td = _seed_linked_stale(repo, memory_dir)
    seen = {}

    def _record(md, index_dir=None):
        seen.update(memory_dir=md, index_dir=index_dir)
        return None

    monkeypatch.setattr("memory.links.build_graph", _record)
    R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, since=_ALL)

    from memory.build_index import default_index_dir

    assert seen == {"memory_dir": memory_dir, "index_dir": default_index_dir(memory_dir)}


def test_worklist_neighborhood_column_is_report_only(repo, memory_dir):
    """The roadmap AC's 'no autonomous writes introduced': building the annotated worklist
    leaves every corpus byte identical and creates nothing on disk (not even an index dir)."""
    td = _seed_linked_stale(repo, memory_dir)

    def _snapshot():
        return {
            f: open(os.path.join(memory_dir, f), encoding="utf-8").read()
            for f in sorted(os.listdir(memory_dir))
        }

    from memory.build_index import default_index_dir

    before = _snapshot()
    worklist = R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, since=_ALL)
    assert worklist and worklist[0]["linked"] == ["m_in", "m_out"]
    assert _snapshot() == before
    assert not os.path.exists(default_index_dir(memory_dir))  # read-side never creates it


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
    assert result == {
        "name": "m_a",
        "outcome": "graduate",
        "cleared": True,
        "edge_written": False,
        "logged": True,
        "error": None,
    }

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
# GRA-4: superseded_by — demote/fix optionally record the supersedes edge
# --------------------------------------------------------------------------- #
def _seed_pair(repo, memory_dir):
    """Two memories citing the same drifted file: m_old (the wrong one) + m_new (its
    successor). Returns m_new's original text for body-preservation asserts."""
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_old.md", _mem("m_old", ["src/foo.py"], c1))
    new_text = _mem("m_new", ["src/foo.py"], c1)
    write_file(memory_dir, "m_new.md", new_text)
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)
    return new_text


def test_semantic_reverify_demote_with_superseded_by_writes_edge_to_successor(repo, memory_dir):
    new_text = _seed_pair(repo, memory_dir)
    td = os.path.join(repo, "tele")

    result = R.semantic_reverify(
        "m_old", "demote", memory_dir, repo, telemetry_dir=td, superseded_by="m_new"
    )
    assert result["error"] is None
    assert result["edge_written"] is True
    assert result["cleared"] is False  # demote NEVER clears the staleness flag (unchanged)
    assert result["logged"] is True

    # the edge lands on the SUCCESSOR's frontmatter, additively; its body is byte-identical
    text = open(os.path.join(memory_dir, "m_new.md"), encoding="utf-8").read()
    assert 'supersedes: ["m_old"]' in text
    assert text.split("---\n", 2)[-1] == new_text.split("---\n", 2)[-1]
    # the DEMOTED memory's file is untouched (the edge lives on the successor)
    assert "supersedes" not in open(os.path.join(memory_dir, "m_old.md"), encoding="utf-8").read()

    # idempotent: re-running the same verdict re-logs but does not duplicate the edge
    again = R.semantic_reverify(
        "m_old", "demote", memory_dir, repo, telemetry_dir=td, superseded_by="m_new"
    )
    assert again["error"] is None and again["edge_written"] is False
    assert open(os.path.join(memory_dir, "m_new.md"), encoding="utf-8").read() == text


def test_semantic_reverify_fix_with_superseded_by_also_clears_flag(repo, memory_dir):
    _seed_pair(repo, memory_dir)
    td = os.path.join(repo, "tele")
    result = R.semantic_reverify(
        "m_old", "fix", memory_dir, repo, telemetry_dir=td, superseded_by="m_new"
    )
    assert result["error"] is None
    assert result["cleared"] is True  # fix still routes through reverify_file
    assert result["edge_written"] is True


def test_semantic_reverify_graduate_refuses_superseded_by(repo, memory_dir):
    """A memory just confirmed CORRECT cannot simultaneously be superseded — refused
    BEFORE any write, and the refusal is not logged (mirrors the unparseable guard)."""
    _seed_pair(repo, memory_dir)
    td = os.path.join(repo, "tele")
    before = open(os.path.join(memory_dir, "m_new.md"), encoding="utf-8").read()
    result = R.semantic_reverify(
        "m_old", "graduate", memory_dir, repo, telemetry_dir=td, superseded_by="m_new"
    )
    assert result["error"] is not None and "superseded_by" in result["error"]
    assert result["cleared"] is False and result["edge_written"] is False
    assert result["logged"] is False
    assert list(read_reconsolidation_events(td)) == []
    assert open(os.path.join(memory_dir, "m_new.md"), encoding="utf-8").read() == before


def test_semantic_reverify_superseded_by_missing_successor_refuses(repo, memory_dir):
    _seed_pair(repo, memory_dir)
    td = os.path.join(repo, "tele")
    result = R.semantic_reverify(
        "m_old", "demote", memory_dir, repo, telemetry_dir=td, superseded_by="m_ghost"
    )
    assert result["error"] is not None and "m_ghost" in result["error"]
    assert result["edge_written"] is False and result["logged"] is False


def test_reconsolidate_cli_reverify_with_superseded_by(repo, memory_dir, capsys):
    """The CLI wiring: --reverify NAME --outcome demote --superseded-by SUCCESSOR applies
    the same per-item primitive (and --outcome is required)."""
    _seed_pair(repo, memory_dir)
    td = os.path.join(repo, "tele")

    rc = R.main(["--reverify", "m_old", "--memory-dir", memory_dir, "--repo-root", repo])
    assert rc == 1
    assert "--outcome is required" in capsys.readouterr().out

    rc = R.main(
        [
            "--reverify", "m_old", "--outcome", "demote", "--superseded-by", "m_new",
            "--memory-dir", memory_dir, "--repo-root", repo, "--telemetry-dir", td,
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "outcome=demote" in out and "supersedes edge written to m_new" in out
    assert 'supersedes: ["m_old"]' in open(os.path.join(memory_dir, "m_new.md"), encoding="utf-8").read()


def test_reconsolidate_cli_reverify_dry_run_writes_nothing(repo, memory_dir, capsys):
    _seed_pair(repo, memory_dir)
    td = os.path.join(repo, "tele")
    before = open(os.path.join(memory_dir, "m_new.md"), encoding="utf-8").read()
    rc = R.main(
        [
            "--reverify", "m_old", "--outcome", "demote", "--superseded-by", "m_new",
            "--memory-dir", memory_dir, "--repo-root", repo, "--telemetry-dir", td,
            "--dry-run",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0 and "would be written" in out
    assert open(os.path.join(memory_dir, "m_new.md"), encoding="utf-8").read() == before


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
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
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
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    _seed_events(td, [("s1", names)])

    out = R.reconsolidation_producer(memory_dir, repo)
    assert "…and 5 more." in out


def test_producer_renders_the_plus_n_linked_form(repo, memory_dir, monkeypatch):
    """GRA-9's exact SessionStart render — `X (+2 linked: Y, Z)` — plus its one-line
    legend, which appears ONLY when an annotation does."""
    now = int(time.time())
    td = _seed_linked_stale(repo, memory_dir, when=now - 200)
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)

    out = R.reconsolidation_producer(memory_dir, repo)
    assert "• m_a (+2 linked: m_in, m_out): src/foo.py" in out
    assert "review-adjacent" in out  # the legend explains what (+N linked: …) means


def test_producer_line_and_header_unchanged_when_graph_unavailable(repo, memory_dir, monkeypatch):
    """Degradation is invisible: no column -> the pre-GRA-9 line AND header, verbatim."""
    now = int(time.time())
    td = _seed_linked_stale(repo, memory_dir, when=now - 200)
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    monkeypatch.setattr("memory.links.build_graph", lambda *a, **k: None)

    out = R.reconsolidation_producer(memory_dir, repo)
    assert "• m_a: src/foo.py" in out
    assert "linked" not in out and "review-adjacent" not in out


def test_reconsolidate_cli_worklist_renders_linked(repo, memory_dir, capsys):
    """The CLI worklist describes the same neighborhoods the producer does."""
    now = int(time.time())
    td = _seed_linked_stale(repo, memory_dir, when=now - 200)

    rc = R.main(["--memory-dir", memory_dir, "--repo-root", repo, "--telemetry-dir", td])
    out = capsys.readouterr().out
    assert rc == 0
    assert "• m_a (+2 linked: m_in, m_out): src/foo.py" in out


def test_producer_never_raises_on_bogus_dirs():
    assert R.reconsolidation_producer("/no/such/memory", "/no/such/repo") is None


def test_producer_signature_matches_dispatcher_contract():
    sig = inspect.signature(R.reconsolidation_producer)
    assert list(sig.parameters) == ["memory_dir", "repo_root"]
