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


# --------------------------------------------------------------------------- #
# LIF-6: recalled_stale_worklist accepts a PRECOMPUTED stale list (the SessionStart
# dispatcher's single find_stale call, shared with the staleness producer) instead of
# always re-deriving it.
# --------------------------------------------------------------------------- #
def test_worklist_accepts_a_precomputed_stale_list_and_skips_find_stale(repo, memory_dir, monkeypatch):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_alpha.md", _mem("m_alpha", ["src/foo.py"], c1))
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)

    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_alpha"])])

    calls = []
    monkeypatch.setattr(R, "find_stale", lambda *a, **k: calls.append(1) or [])

    precomputed = [{"name": "m_alpha", "changed_paths": ["src/foo.py"], "recency": 1}]
    worklist = R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, stale=precomputed)
    assert [w["name"] for w in worklist] == ["m_alpha"]
    assert calls == []  # find_stale was never called -- the precomputed list was trusted


def test_worklist_falls_back_to_find_stale_when_no_stale_given(repo, memory_dir):
    """Default behavior (the reconsolidate CLI, any standalone caller) is unchanged."""
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_alpha.md", _mem("m_alpha", ["src/foo.py"], c1))
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)

    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_alpha"])])

    worklist = R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, since=_ALL)
    assert [w["name"] for w in worklist] == ["m_alpha"]


def test_worklist_never_mutates_a_precomputed_stale_lists_items(repo, memory_dir):
    """LIF-6: `stale` may be CALLER-OWNED (session_start.RunContext.stale, shared with the
    staleness producer) — GRA-9's linked-neighbor attachment must land on the worklist's
    OWN copies, never leak an added "linked" key back into the caller's list."""
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_alpha.md", _mem("m_alpha", ["src/foo.py"], c1))
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)

    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_alpha"])])

    precomputed = [{"name": "m_alpha", "changed_paths": ["src/foo.py"], "recency": 1}]
    worklist = R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, stale=precomputed)
    assert worklist and worklist[0] is not precomputed[0]  # a distinct dict, not aliased
    assert "linked" not in precomputed[0]  # the caller's own item is untouched


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
        # CUR-1 pass-through from reverify_file: _mem's body cites NOTHING, but
        # src/foo.py is still committed at HEAD — so the citation is PRESERVED (a
        # curated entry must survive a content re-verification), reported via
        # preserved_not_derived, never dropped. Pre-CUR-1 this very passthrough
        # asserted the drop; pre-LIF-4 the renderer even called the still-present
        # file "no longer in the repo".
        "cited": ["src/foo.py"],
        "dropped_citations": [],
        "dropped_gone": [],
        "dropped_not_derived": [],
        "preserved_not_derived": ["src/foo.py"],
        "invalidated": False,  # LIF-1's chain is demote-only — graduate never touches it
        "invalid_after": None,  # GRW-7's stamped boundary — demote-with-successor only
        "edge_written": False,
        "succession_replay": None,  # TMB-5 fires on demote-with-successor only
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


def test_semantic_reverify_reports_dropped_citations_after_rename(repo, memory_dir):
    """LIF-3 pass-through: a graduate/fix re-derivation over a renamed cited file must
    surface the drop on the result (this is the one write path where a memory can
    otherwise shrink to zero citations — staleness-exempt — with nobody watching)."""
    import subprocess

    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(
        memory_dir,
        "m_a.md",
        f'---\nname: m_a\ndescription: "d"\ncited_paths: ["src/foo.py"]\nsource_commit: "{c1}"\n'
        "---\nbody cites src/foo.py\n",
    )
    subprocess.run(
        ["git", "mv", "src/foo.py", "src/foo_moved2.py"], cwd=repo, check=True, capture_output=True
    )
    git_commit(repo, "rename foo", 1_700_000_100)

    td = os.path.join(repo, "tele")
    result = R.semantic_reverify("m_a", "graduate", memory_dir, repo, telemetry_dir=td)
    assert result["error"] is None
    assert result["dropped_citations"] == ["src/foo.py"]
    assert result["cited"] == []  # dropped to ZERO — the caller can name the exemption


def test_reconsolidate_cli_reverify_prints_citation_rot_line(repo, memory_dir, capsys):
    """The CLI renders the drop via the ONE shared provenance.citation_rot_lines — same
    loud line as the provenance CLI, zero case called out distinctly."""
    import subprocess

    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(
        memory_dir,
        "m_a.md",
        f'---\nname: m_a\ndescription: "d"\ncited_paths: ["src/foo.py"]\nsource_commit: "{c1}"\n'
        "---\nbody cites src/foo.py\n",
    )
    subprocess.run(
        ["git", "mv", "src/foo.py", "src/foo_moved2.py"], cwd=repo, check=True, capture_output=True
    )
    git_commit(repo, "rename foo", 1_700_000_100)

    td = os.path.join(repo, "tele")
    rc = R.main(
        ["--reverify", "m_a", "--outcome", "graduate",
         "--memory-dir", memory_dir, "--repo-root", repo, "--telemetry-dir", td]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "citation rot" in out and "src/foo.py" in out
    assert "EXEMPT" in out  # drop to zero — distinct, names the staleness exemption


def test_semantic_reverify_demote_never_clears_flag(repo, memory_dir, monkeypatch):
    """The FM2 neutralization: a confirmed-WRONG memory must stay flagged, never silently
    re-baselined just because an outcome was logged."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")  # demote chains a refresh_index (LIF-1)
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


def test_semantic_reverify_demote_with_superseded_by_writes_edge_to_successor(repo, memory_dir, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")  # demote chains a refresh_index (LIF-1)
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


def test_semantic_reverify_fix_refuses_superseded_by(repo, memory_dir):
    """GRW-7 closed the fix+superseded_by combination: fix re-baselines the memory as
    CURRENT via reverify_file, and a supersede now stamps the loser's invalid_after —
    the two verdicts contradict, so the combination refuses BEFORE any write (the
    pre-GRW-7 behavior wrote the edge and stamped nothing, a silent half-supersede)."""
    _seed_pair(repo, memory_dir)
    td = os.path.join(repo, "tele")
    before_new = open(os.path.join(memory_dir, "m_new.md"), encoding="utf-8").read()
    before_old = open(os.path.join(memory_dir, "m_old.md"), encoding="utf-8").read()
    result = R.semantic_reverify(
        "m_old", "fix", memory_dir, repo, telemetry_dir=td, superseded_by="m_new"
    )
    assert result["error"] is not None and "demote" in result["error"]
    assert result["cleared"] is False and result["edge_written"] is False
    assert result["logged"] is False
    assert list(read_reconsolidation_events(td)) == []
    assert open(os.path.join(memory_dir, "m_new.md"), encoding="utf-8").read() == before_new
    assert open(os.path.join(memory_dir, "m_old.md"), encoding="utf-8").read() == before_old


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


def test_reconsolidate_cli_reverify_with_superseded_by(repo, memory_dir, capsys, monkeypatch):
    """The CLI wiring: --reverify NAME --outcome demote --superseded-by SUCCESSOR applies
    the same per-item primitive (and --outcome is required)."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")  # demote chains a refresh_index (LIF-1)
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
    before_old = open(os.path.join(memory_dir, "m_old.md"), encoding="utf-8").read()
    rc = R.main(
        [
            "--reverify", "m_old", "--outcome", "demote", "--superseded-by", "m_new",
            "--memory-dir", memory_dir, "--repo-root", repo, "--telemetry-dir", td,
            "--dry-run",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0 and "would be written" in out
    assert "invalid_after would be set" in out  # LIF-1's chain previews, byte-exact
    assert open(os.path.join(memory_dir, "m_new.md"), encoding="utf-8").read() == before
    # the demoted memory is untouched too — the chained set_invalid_after honors dry_run
    assert open(os.path.join(memory_dir, "m_old.md"), encoding="utf-8").read() == before_old


# --------------------------------------------------------------------------- #
# LIF-1: demote chains soft-invalidation (terminal); snooze acks (expiring); the
# worklist and the staleness producer stop double-nagging what's already settled
# --------------------------------------------------------------------------- #
def _append_session(td, sid, names, ts):
    """Append ONE session's recall event with an explicit ts (the snooze-expiry anchor)."""
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "recall_events.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"session_id": sid, "names": names, "backend": "bm25", "ts": ts}) + "\n")


def _seed_stale_recalled(repo, memory_dir, names, when=1_700_000_000):
    """N memories citing one drifted file, all recalled in session s1. Returns the tele dir."""
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", when)
    for n in names:
        write_file(memory_dir, f"{n}.md", _mem(n, ["src/foo.py"], c1))
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", when + 100)
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", list(names))])
    return td


def test_semantic_reverify_demote_chains_invalid_after(repo, memory_dir, monkeypatch):
    """LIF-1 (A): ONE demote verdict closes the validity window on the memory itself —
    body byte-identical, staleness flag STILL set (the chain never re-baselines), and the
    chained action lands in the ledger event for audit."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    td = _seed_stale_recalled(repo, memory_dir, ["m_bad"])
    body = open(os.path.join(memory_dir, "m_bad.md"), encoding="utf-8").read()

    result = R.semantic_reverify("m_bad", "demote", memory_dir, repo, telemetry_dir=td)
    assert result["error"] is None
    assert result["invalidated"] is True
    assert result["cleared"] is False  # the chain must never clear the staleness flag

    text = open(os.path.join(memory_dir, "m_bad.md"), encoding="utf-8").read()
    assert "invalid_after:" in text
    assert text.split("---\n", 2)[-1] == body.split("---\n", 2)[-1]  # body byte-identical

    # still visible to staleness — invalid_after is invisible to find_stale by pinned contract
    from memory.staleness import find_stale

    assert any(s["name"] == "m_bad" for s in find_stale(memory_dir, repo, since=_ALL))

    evs = list(read_reconsolidation_events(td))
    assert evs[-1]["outcome"] == "demote" and evs[-1]["invalidated"] is True


def test_demote_immediately_demotes_recall_rank_with_no_second_command(repo, memory_dir, monkeypatch):
    """The roadmap AC verbatim: demote immediately halves recall rank via the EXISTING
    pre-cut penalty, with no second command — no `staleness --invalidate`, no manual
    rebuild. Mirrors test_recall's boundary-swap pattern (nested token overlap)."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    import memory.recall as RC
    from memory.build_index import build_index, default_index_dir

    words = ["alpha", "beta", "gamma", "delta", "epsilon"]
    for i in range(1, 6):
        desc = " ".join(words[:i])
        write_file(memory_dir, f"e{i}.md", f'---\nname: e{i}\ndescription: "{desc}"\n---\nbody\n')
    idx = default_index_dir(memory_dir)
    build_index(memory_dir, idx)

    query = " ".join(words)
    k = 3
    full = [r["name"] for r in RC.recall(query, k=10, memory_dir=memory_dir, index_dir=idx)]
    boundary, successor = full[k - 1], full[k]
    before = [r["name"] for r in RC.recall(query, k=k, memory_dir=memory_dir, index_dir=idx)]
    assert boundary in before and successor not in before

    td = os.path.join(repo, "tele")
    result = R.semantic_reverify(boundary, "demote", memory_dir, repo, telemetry_dir=td)
    assert result["error"] is None and result["invalidated"] is True

    # ONE command ran. The chained invalid_after + index refresh mean the existing
    # x0.5 pre-cut penalty engages on the very next recall:
    after = [r["name"] for r in RC.recall(query, k=k, memory_dir=memory_dir, index_dir=idx)]
    assert boundary not in after  # demoted out of top-k by the penalty
    assert successor in after  # its successor takes the slot
    # soft demotion, never a hard exclude (the penalty's own contract)
    wide = [r["name"] for r in RC.recall(query, k=10, memory_dir=memory_dir, index_dir=idx)]
    assert boundary in wide


def test_worklist_excludes_items_already_carrying_invalid_after(repo, memory_dir, monkeypatch):
    """LIF-1 (C): terminal states never re-nag — a demoted (or manually invalidated)
    memory drops off the worklist while its un-demoted sibling stays."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    td = _seed_stale_recalled(repo, memory_dir, ["m_keep", "m_done"])
    got = {w["name"] for w in R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, since=_ALL)}
    assert got == {"m_keep", "m_done"}  # both nag before any verdict

    R.semantic_reverify("m_done", "demote", memory_dir, repo, telemetry_dir=td)
    names = [w["name"] for w in R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, since=_ALL)]
    assert names == ["m_keep"]

    # a manual staleness --invalidate is the SAME terminal state (any invalid_after counts)
    from memory.staleness import set_invalid_after

    set_invalid_after(os.path.join(memory_dir, "m_keep.md"))
    assert R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, since=_ALL) == []


def test_snooze_suppresses_the_next_n_worklists_then_expires(repo, memory_dir):
    """LIF-1 (C) + the roadmap AC: an acked item is absent from the next
    _SNOOZE_WINDOW_SESSIONS worklists (each produced at a new session's start, BEFORE that
    session logs recalls of its own), then re-nags — a snooze expires, only demote is
    terminal."""
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_a.md", _mem("m_a", ["src/foo.py"], c1))
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)
    td = os.path.join(repo, "tele")
    now = time.time()
    _append_session(td, "s0", ["m_a"], now - 50)
    assert [w["name"] for w in R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, since=_ALL)] == ["m_a"]

    r = R.snooze("m_a", memory_dir, telemetry_dir=td)
    assert r == {"name": "m_a", "logged": True, "error": None}

    for i in range(1, R._SNOOZE_WINDOW_SESSIONS + 1):
        # session s<i> starts: its worklist must skip m_a…
        assert R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, since=_ALL) == []
        # …then s<i> logs its own recall events (post-ack ts -> the snooze ages one session)
        _append_session(td, f"s{i}", ["m_a"], now + 10 * i)

    # _SNOOZE_WINDOW_SESSIONS new sessions have started since the ack -> re-nag
    assert [w["name"] for w in R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td, since=_ALL)] == ["m_a"]


def test_reconsolidate_cli_snooze_records_the_ack(repo, memory_dir, capsys):
    td = _seed_stale_recalled(repo, memory_dir, ["m_a"])
    rc = R.main(["--snooze", "m_a", "--memory-dir", memory_dir, "--repo-root", repo, "--telemetry-dir", td])
    out = capsys.readouterr().out
    assert rc == 0 and "ack logged" in out and str(R._SNOOZE_WINDOW_SESSIONS) in out
    evs = list(read_reconsolidation_events(td))
    assert evs[-1]["name"] == "m_a" and evs[-1]["outcome"] == "snooze"
    # the ack is ledger-only: the memory file itself is untouched (no invalid_after)
    assert "invalid_after" not in open(os.path.join(memory_dir, "m_a.md"), encoding="utf-8").read()


def test_snooze_refuses_missing_memory_and_dry_runs_cleanly(repo, memory_dir):
    td = os.path.join(repo, "tele")
    r = R.snooze("m_ghost", memory_dir, telemetry_dir=td)
    assert r["error"] is not None and r["logged"] is False
    assert list(read_reconsolidation_events(td)) == []  # a typo must not ack anything

    write_file(memory_dir, "m_real.md", _mem("m_real", ["src/foo.py"], "abc"))
    dry = R.snooze("m_real", memory_dir, telemetry_dir=td, dry_run=True)
    assert dry["error"] is None and dry["logged"] is False
    assert list(read_reconsolidation_events(td)) == []


def test_snooze_is_single_item_only_no_bulk_path():
    """Mirrors semantic_reverify's negative-capability pin: one name, no batch form."""
    sig = inspect.signature(R.snooze)
    params = list(sig.parameters)
    assert params[0] == "name"
    assert "names" not in params and "bulk" not in params and "all" not in params


def test_demoted_memory_reported_in_neither_staleness_lines_nor_worklist(repo, memory_dir, monkeypatch):
    """The AC's no-double-reporting, on LIF-1's own terms: after a demote, the memory's
    NAME appears in neither the staleness producer's per-item lines nor the worklist —
    only the aggregate '(+N already demoted)' tail accounts for it. (Full staleness ∩
    worklist single-computation is LIF-6's job, not this test's.)"""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    now = int(time.time())
    td = _seed_stale_recalled(repo, memory_dir, ["m_keep", "m_gone"], when=now - 300)
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)

    assert R.semantic_reverify("m_gone", "demote", memory_dir, repo, telemetry_dir=td)["invalidated"] is True

    import memory.session_start as S

    stale_out = S.staleness_producer(memory_dir, repo) or ""
    recon_out = R.reconsolidation_producer(memory_dir, repo) or ""
    assert "m_gone" not in stale_out and "m_gone" not in recon_out  # suppressed everywhere…
    assert "(+1 already demoted" in stale_out  # …but never silently: the count remains
    assert "m_keep" in stale_out and "m_keep" in recon_out  # the active item still nags


def test_reconsolidate_cli_demote_reports_the_chained_invalidation(repo, memory_dir, capsys, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed_pair(repo, memory_dir)
    td = os.path.join(repo, "tele")
    rc = R.main(
        ["--reverify", "m_old", "--outcome", "demote",
         "--memory-dir", memory_dir, "--repo-root", repo, "--telemetry-dir", td]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "invalid_after set" in out and "no second command" in out
    assert "invalid_after:" in open(os.path.join(memory_dir, "m_old.md"), encoding="utf-8").read()


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


# --------------------------------------------------------------------------- #
# LIF-6: reconsolidation_producer reads a precomputed worklist off ctx instead of
# re-deriving it, when the dispatcher supplies one.
# --------------------------------------------------------------------------- #
def test_producer_renders_ctx_worklist_without_recomputing():
    from memory.staleness import RunContext

    ctx = RunContext(worklist=[{"name": "m_a", "changed_paths": ["src/foo.py"]}])
    out = R.reconsolidation_producer("md", "repo", ctx)
    assert out is not None and "m_a" in out


def test_producer_with_ctx_never_calls_recalled_stale_worklist(monkeypatch):
    from memory.staleness import RunContext

    def boom(*a, **k):
        raise AssertionError("must not recompute the worklist when ctx already has one")

    monkeypatch.setattr(R, "recalled_stale_worklist", boom)
    ctx = RunContext(worklist=[{"name": "m_a", "changed_paths": ["src/foo.py"]}])
    out = R.reconsolidation_producer("md", "repo", ctx)
    assert out is not None and "m_a" in out


def test_producer_never_raises_on_bogus_dirs():
    assert R.reconsolidation_producer("/no/such/memory", "/no/such/repo") is None


def test_producer_signature_matches_dispatcher_contract():
    """LIF-6: the dispatcher now threads a shared ``RunContext`` positionally through
    EVERY producer (not just this one) -- ``ctx`` is optional (defaults to None) so the
    producer stays independently callable with the old 2-arg shape too."""
    sig = inspect.signature(R.reconsolidation_producer)
    assert list(sig.parameters) == ["memory_dir", "repo_root", "ctx"]
    assert sig.parameters["ctx"].default is None


# --------------------------------------------------------------------------- #
# GRW-5: commit-watermark re-verify — the precision lane
# --------------------------------------------------------------------------- #
def _episode_line(td, sid, ts, head_commit, names=("m",)):
    """Write one raw episode-buffer line (hermetic — no live git read at log time)."""
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "episode_buffer.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "ts": ts,
                    "session_id": sid,
                    "query_preview": "q",
                    "recalled_names": list(names),
                    "head_commit": head_commit,
                }
            )
            + "\n"
        )


def test_watermark_candidates_flag_commit_precise_hits(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_alpha.md", _mem("m_alpha", ["src/foo.py"], c1))
    write_file(memory_dir, "m_other.md", _mem("m_other", ["src/bar.py"], c1))
    td = os.path.join(repo, "tele")
    _episode_line(td, "last-sess", 100.0, c1)  # the last session started at c1
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)      # a commit since the watermark touches foo

    cands = R.watermark_stale_candidates(memory_dir, repo, telemetry_dir=td)
    assert cands == [{"name": "m_alpha", "changed_paths": ["src/foo.py"], "watermark": True}]


def test_watermark_candidates_use_most_recent_sessions_earliest_head(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(repo, "src/foo.py", "x = 2\n")
    c2 = git_commit(repo, "c2", 1_700_000_100)
    write_file(memory_dir, "m_alpha.md", _mem("m_alpha", ["src/foo.py"], c1))
    td = os.path.join(repo, "tele")
    # An OLDER session watermarked at c1; the MOST RECENT session started at c2.
    _episode_line(td, "old-sess", 50.0, c1)
    _episode_line(td, "new-sess", 200.0, c2)
    write_file(repo, "src/foo.py", "x = 3\n")
    git_commit(repo, "c3", 1_700_000_200)

    cands = R.watermark_stale_candidates(memory_dir, repo, telemetry_dir=td)
    # Diff is c2..HEAD (the LAST session's watermark), which still touches foo.
    assert [c["name"] for c in cands] == ["m_alpha"]
    # And an untouched window → nothing: simulate by rewriting the buffer to only c3.
    head = R.run_git(["rev-parse", "HEAD"], repo).strip()
    os.remove(os.path.join(td, "episode_buffer.jsonl"))
    _episode_line(td, "newest", 300.0, head)
    assert R.watermark_stale_candidates(memory_dir, repo, telemetry_dir=td) == []


def test_watermark_candidates_unreachable_sha_and_empty_buffer_are_silent(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_alpha.md", _mem("m_alpha", ["src/foo.py"], c1))
    td = os.path.join(repo, "tele")
    assert R.watermark_stale_candidates(memory_dir, repo, telemetry_dir=td) == []  # no episodes
    _episode_line(td, "s", 10.0, "a" * 40)  # squash-simulated unreachable watermark
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)
    # run_git returns "" on the unreachable range → [] honestly (GRW-6 heals, never guess).
    assert R.watermark_stale_candidates(memory_dir, repo, telemetry_dir=td) == []


def test_worklist_unions_watermark_items_after_recency(repo, memory_dir):
    """A watermark hit joins the worklist even though it was NEVER recently recalled —
    precision beats recency — while recalled∩stale items keep leading."""
    write_file(repo, "src/foo.py", "x = 1\n")
    write_file(repo, "src/bar.py", "y = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_recalled.md", _mem("m_recalled", ["src/foo.py"], c1))
    write_file(memory_dir, "m_quiet.md", _mem("m_quiet", ["src/bar.py"], c1))
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_recalled"])])  # only m_recalled was ever recalled

    precomputed = [{"name": "m_recalled", "changed_paths": ["src/foo.py"], "recency": 1}]
    wm = [{"name": "m_quiet", "changed_paths": ["src/bar.py"], "watermark": True}]
    worklist = R.recalled_stale_worklist(
        memory_dir, repo, telemetry_dir=td, stale=precomputed, watermark_stale=wm
    )
    assert [w["name"] for w in worklist] == ["m_recalled", "m_quiet"]
    assert worklist[1]["watermark"] is True
    assert worklist[1] is not wm[0], "caller-owned watermark list items are copied"
    assert "linked" not in wm[0]


def test_worklist_dedups_watermark_by_name_stale_item_wins(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_alpha.md", _mem("m_alpha", ["src/foo.py"], c1))
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["m_alpha"])])
    precomputed = [{"name": "m_alpha", "changed_paths": ["src/foo.py"], "recency": 9}]
    wm = [{"name": "m_alpha", "changed_paths": ["src/foo.py"], "watermark": True}]
    worklist = R.recalled_stale_worklist(
        memory_dir, repo, telemetry_dir=td, stale=precomputed, watermark_stale=wm
    )
    assert len(worklist) == 1
    assert worklist[0].get("recency") == 9, "the stale-derived item (richer) wins the dedup"
    assert "watermark" not in worklist[0]


def test_worklist_watermark_items_respect_invalid_after_and_snooze(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    body = _mem("m_dead", ["src/foo.py"], c1)
    body = body.replace("---\nbody", 'invalid_after: "2026-01-01T00:00:00+00:00"\n---\nbody')
    write_file(memory_dir, "m_dead.md", body)
    td = os.path.join(repo, "tele")
    _seed_events(td, [("s1", ["something_else"])])
    wm = [{"name": "m_dead", "changed_paths": ["src/foo.py"], "watermark": True}]
    worklist = R.recalled_stale_worklist(
        memory_dir, repo, telemetry_dir=td, stale=[], watermark_stale=wm
    )
    assert worklist == [], "LIF-1 exclusions apply to the union — terminal items never re-nag"


def test_worklist_without_watermark_param_is_unchanged(repo, memory_dir):
    """No watermark_stale → byte-identical pre-GRW-5 behavior (empty recency → [])."""
    td = os.path.join(repo, "tele")
    assert R.recalled_stale_worklist(memory_dir, repo, telemetry_dir=td) == []


def test_producer_tags_watermark_items(repo, memory_dir):
    from memory.staleness import RunContext

    ctx = RunContext(
        stale=[],
        stale_diagnostics={},
        worklist=[
            {"name": "m_recalled", "changed_paths": ["src/foo.py"]},
            {"name": "m_precise", "changed_paths": ["src/bar.py"], "watermark": True},
        ],
        changed_paths=[],
    )
    out = R.reconsolidation_producer(memory_dir, repo, ctx)
    assert out is not None
    assert "m_precise [since-watermark]" in out
    assert "m_recalled:" in out and "m_recalled [since-watermark]" not in out
    assert "commits landed since your last session" in out  # the only-when-present legend


# --------------------------------------------------------------------------- #
# GRW-7: a supersede stamps the loser's invalid_after at the SUCCESSOR's commit date
# --------------------------------------------------------------------------- #
def test_demote_superseded_by_stamps_the_successors_commit_date(repo, memory_dir, monkeypatch):
    """The succession moment — not verdict-render time — is the validity boundary. The
    successor here was committed at a PINNED epoch, so the loser's invalid_after equals
    that exact instant, read back through the SHIPPED read_invalid_after (no new field,
    no schema bump — the one canonical name, inv5)."""
    import datetime as _dt

    from memory.staleness import read_invalid_after

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed_pair(repo, memory_dir)  # c2 (epoch 1_700_000_100) commits m_new.md via add -A
    td = os.path.join(repo, "tele")

    result = R.semantic_reverify(
        "m_old", "demote", memory_dir, repo, telemetry_dir=td, superseded_by="m_new"
    )
    assert result["error"] is None and result["invalidated"] is True

    expected = _dt.datetime.fromtimestamp(1_700_000_100, _dt.timezone.utc).isoformat()
    text = open(os.path.join(memory_dir, "m_old.md"), encoding="utf-8").read()
    assert read_invalid_after(text) == expected, (
        "the loser's validity window closes at the SUCCESSOR's commit date"
    )
    assert result["invalid_after"] == expected
    # …and the body stayed byte-identical (the stamp is frontmatter-only).
    assert text.split("---\n", 2)[-1].startswith("body for m_old")


def test_demote_superseded_by_uncommitted_successor_falls_back_to_now(repo, memory_dir, monkeypatch):
    import datetime as _dt

    from memory.staleness import read_invalid_after

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_old.md", _mem("m_old", ["src/foo.py"], c1))
    git_commit(repo, "commit m_old", 1_700_000_050)
    # The successor is written but NEVER committed — just drafted this session.
    write_file(memory_dir, "m_new.md", _mem("m_new", ["src/foo.py"], None))
    td = os.path.join(repo, "tele")

    before = _dt.datetime.now(_dt.timezone.utc)
    result = R.semantic_reverify(
        "m_old", "demote", memory_dir, repo, telemetry_dir=td, superseded_by="m_new"
    )
    assert result["error"] is None and result["invalidated"] is True
    stamped = read_invalid_after(open(os.path.join(memory_dir, "m_old.md"), encoding="utf-8").read())
    parsed = _dt.datetime.fromisoformat(stamped)
    assert parsed >= before - _dt.timedelta(seconds=5), "uncommitted successor → now-UTC fallback"


def test_demote_superseded_by_ledger_event_is_the_audit_trail(repo, memory_dir, monkeypatch):
    import datetime as _dt

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed_pair(repo, memory_dir)
    td = os.path.join(repo, "tele")
    R.semantic_reverify("m_old", "demote", memory_dir, repo, telemetry_dir=td, superseded_by="m_new")

    events = list(read_reconsolidation_events(td))
    assert len(events) == 1
    ev = events[0]
    assert ev["name"] == "m_old" and ev["outcome"] == "demote" and ev["invalidated"] is True
    assert ev["superseded_by"] == "m_new"
    assert ev["invalid_after"] == _dt.datetime.fromtimestamp(1_700_000_100, _dt.timezone.utc).isoformat()


def test_plain_demote_still_stamps_now_and_logs_no_successor_fields(repo, memory_dir, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed_pair(repo, memory_dir)
    td = os.path.join(repo, "tele")
    result = R.semantic_reverify("m_old", "demote", memory_dir, repo, telemetry_dir=td)
    assert result["error"] is None and result["invalidated"] is True
    ev = list(read_reconsolidation_events(td))[0]
    assert "superseded_by" not in ev, "no successor named → no successor fields fabricated"
    # invalid_after IS recorded (the stamp happened — now-UTC); the boundary is auditable.
    assert "invalid_after" in ev


def test_cli_demote_superseded_by_prints_the_boundary(repo, memory_dir, capsys, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed_pair(repo, memory_dir)
    td = os.path.join(repo, "tele")
    rc = R.main(
        [
            "--reverify", "m_old", "--outcome", "demote", "--superseded-by", "m_new",
            "--memory-dir", memory_dir, "--repo-root", repo, "--telemetry-dir", td,
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "the successor's commit date" in out, "the stamped boundary is legible at the CLI"


# --------------------------------------------------------------------------- #
# QA sweep 2026-07-16 — COR-16: the demote+superseded_by two-write chain rolls back.
# --------------------------------------------------------------------------- #
def test_semantic_reverify_rolls_back_invalidation_when_edge_write_fails(repo, memory_dir, monkeypatch):
    """WRITE #1 (loser's invalid_after) used to persist when WRITE #2 (successor's
    supersedes edge) failed — the MCP wrapper then rendered 'refused' while the memory
    had in fact been soft-invalidated. The verdict must be all-or-nothing."""
    import memory.links as links

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _seed_pair(repo, memory_dir)
    td = os.path.join(repo, "tele")
    old_before = open(os.path.join(memory_dir, "m_old.md"), encoding="utf-8").read()
    new_before = open(os.path.join(memory_dir, "m_new.md"), encoding="utf-8").read()

    def failing(path, *a, **k):
        return {"path": path, "changed": False, "error": "disk full"}

    monkeypatch.setattr(links, "add_typed_relation", failing)
    result = R.semantic_reverify(
        "m_old", "demote", memory_dir, repo, telemetry_dir=td, superseded_by="m_new"
    )
    assert result["error"] is not None and "disk full" in result["error"]
    assert result["invalidated"] is False, "a refused verdict must not report a live write"
    assert result["logged"] is False
    assert open(os.path.join(memory_dir, "m_old.md"), encoding="utf-8").read() == old_before, (
        "m_old's invalid_after must be rolled back when the successor edge fails"
    )
    assert open(os.path.join(memory_dir, "m_new.md"), encoding="utf-8").read() == new_before


def test_worklist_nudge_names_a_runnable_verb_on_both_surfaces(repo, memory_dir, monkeypatch):
    """INT-18 (DOC-16's lesson, again): the nudge said `provenance --reverify <name>` —
    not runnable as written (no such command; the real form is python -m
    memory.provenance), the WRONG verb (the cross-surface reconsolidation path is the
    reconsolidate tool / /hippo:consolidate), and with no /hippo: token the Desktop
    surface note never attached: a Desktop user got a worklist with a terminal-only,
    mistyped command and no working alternative."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    now = int(time.time())
    td = _seed_stale_recalled(repo, memory_dir, ["m_keep"], when=now - 300)
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    out = R.reconsolidation_producer(memory_dir, repo) or ""
    assert out, "worklist expected"
    assert "`provenance --reverify" not in out, "names a command that does not exist"
    assert "reconsolidate" in out and "tool" in out, "must name the cross-surface verb"
    assert "/hippo:" in out, (
        "must carry a /hippo: token so the Desktop surface note attaches"
    )
