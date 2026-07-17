"""SEN-5 — incident-response verbs: untrust <repo> + blast-radius <name>.

After discovering a bad/poisoned memory a user had NO recourse — only mark_trusted existed,
and nothing showed what a suspect memory touched. SEN-5 adds the two verbs. The pins:

  AC1  untrust removes exactly that repo's entry, is idempotent, preserves siblings; the
       trust -> recall(injected) -> untrust -> recall(withheld) round-trip touches zero
       on-disk index/cache files.
  AC2  untrust ships zero cache-invalidation code (revocation is by-gate).
  AC3  blast-radius writes nothing, reads only episode_buffer / links.json / gov_citations /
       .archive_journal.jsonl, and states its coverage limits IN the report.
  AC4  no 'quarantine' verb ships.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.conftest import git_commit, write_file


# --------------------------------------------------------------------------- #
# AC1 + AC2: untrust — revocation by-gate, sibling-safe, idempotent, zero cache touch
# --------------------------------------------------------------------------- #


def _seed_corpus(repo, memory_dir):
    write_file(repo, ".claude/memory/fact.md",
               "---\nname: fact\ndescription: \"a trusted fact about deploys\"\nmetadata:\n  type: project\n---\nbody\n")
    git_commit(repo, "seed", 1_700_000_000)


def test_untrust_round_trip_injects_then_withholds(repo, memory_dir, monkeypatch):
    from memory import trust
    from memory.build_index import build_index
    from memory.recall import recall

    # exercise the REAL gate (conftest sets HIPPO_TRUST_ALL suite-wide — clear it here)
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    _seed_corpus(repo, memory_dir)
    idx = os.path.join(repo, ".claude", ".memory-index")
    build_index(memory_dir, idx)

    trust.mark_trusted(repo, memory_dir=memory_dir, origin="review")
    injected = recall("deploys", k=5, memory_dir=memory_dir, index_dir=idx, repo_root=repo)
    assert any(r["name"] == "fact" for r in injected)  # trusted -> injects

    # snapshot the derived cache dir before untrust to prove untrust touches none of it
    before = {p: os.path.getmtime(os.path.join(idx, p)) for p in os.listdir(idx)}

    assert trust.untrust(repo) is True
    withheld = recall("deploys", k=5, memory_dir=memory_dir, index_dir=idx, repo_root=repo)
    assert withheld == []  # untrusted -> the gate denies the whole corpus

    after = {p: os.path.getmtime(os.path.join(idx, p)) for p in os.listdir(idx)}
    assert before == after  # zero on-disk index/cache files touched (revocation is by-gate)


def test_untrust_is_idempotent_and_sibling_safe(repo, memory_dir, monkeypatch):
    from memory import trust

    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    _seed_corpus(repo, memory_dir)
    other = str(memory_dir) + "-sibling-repo"
    trust.mark_trusted(repo, memory_dir=memory_dir, origin="review")
    trust.mark_trusted(other, origin="review")  # a sibling entry that must survive

    assert trust.is_trusted(repo) is True
    assert trust.untrust(repo) is True
    assert trust.is_trusted(repo) is False
    # idempotent: untrusting again is a successful no-op
    assert trust.untrust(repo) is True
    # sibling preserved
    assert trust.is_trusted(other) is True


def test_untrust_ships_no_cache_invalidation_code():
    """AC2 source pin: untrust must not reach for any index/cache writer — revocation is
    by-gate (is_trusted re-reads live), so there is nothing to invalidate."""
    import inspect

    from memory import trust

    src = inspect.getsource(trust.untrust)
    for banned in ("build_index", "refresh_index", "os.remove", "rmtree", "shutil", "unlink",
                   "write_stale_cache", "default_index_dir"):
        assert banned not in src, f"untrust reached for cache machinery: {banned}"


# --------------------------------------------------------------------------- #
# AC3: blast-radius — read-only join + coverage banner
# --------------------------------------------------------------------------- #


def test_blast_radius_writes_nothing_and_states_coverage(repo, memory_dir):
    from memory import blast_radius as BR

    _seed_corpus(repo, memory_dir)
    idx = os.path.join(repo, ".claude", ".memory-index")
    tel = os.path.join(repo, ".claude", ".memory-telemetry")
    before = _tree_snapshot(memory_dir)
    rep = BR.blast_radius("fact", memory_dir=memory_dir, repo_root=repo)
    after = _tree_snapshot(memory_dir)
    assert before == after  # pure read — wrote nothing to the corpus
    # coverage banner names BOTH blind spots explicitly (inv3)
    assert "rotate" in rep["coverage"].lower()
    assert "mcp" in rep["coverage"].lower()
    # the schema is the four-trace join
    assert set(rep) == {"name", "recalled_sessions", "recall_events", "links",
                        "gov_citations", "archive_journal", "coverage"}


def test_blast_radius_joins_episode_and_link_traces(repo, memory_dir, monkeypatch):
    from memory import blast_radius as BR
    from memory import telemetry as T
    from memory.build_index import build_index
    from memory.links import add_typed_relation

    # two memories with a typed edge between them
    write_file(repo, ".claude/memory/a.md",
               "---\nname: a\ndescription: \"memory a\"\nmetadata:\n  type: project\n---\nbody a\n")
    write_file(repo, ".claude/memory/b.md",
               "---\nname: b\ndescription: \"memory b\"\nmetadata:\n  type: project\n---\nbody b\n")
    git_commit(repo, "two", 1_700_000_000)
    idx = os.path.join(repo, ".claude", ".memory-index")
    build_index(memory_dir, idx)
    add_typed_relation(os.path.join(memory_dir, "b.md"), "supersedes", "a")
    build_index(memory_dir, idx)

    # an episode that recalled 'a'
    tel = os.path.join(repo, ".claude", ".memory-telemetry")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", tel)
    T.log_episode(["a"], query="what is a", repo_root=repo, telemetry_dir=tel, session_id="sess-1")

    rep = BR.blast_radius("a", memory_dir=memory_dir, repo_root=repo, index_dir=idx, telemetry_dir=tel)
    assert "sess-1" in rep["recalled_sessions"]
    assert rep["recall_events"] == 1
    # 'a' is superseded BY 'b' — that inbound typed edge is in the report (typed_in)
    assert "b" in rep["links"]["typed_in"].get("supersedes", [])


def test_blast_radius_reads_archive_journal(repo, memory_dir):
    from memory import blast_radius as BR

    arch = os.path.join(memory_dir, "archive")
    os.makedirs(arch)
    with open(os.path.join(arch, ".archive_journal.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"name": "gone.md", "from": "x", "to": "y", "method": "os.rename"}) + "\n")
    rep = BR.blast_radius("gone", memory_dir=memory_dir, repo_root=repo)
    assert len(rep["archive_journal"]) == 1
    assert rep["archive_journal"][0]["name"] == "gone.md"


# --------------------------------------------------------------------------- #
# AC4 + surface: no quarantine verb; the two tools are wired on both surfaces
# --------------------------------------------------------------------------- #


def test_no_quarantine_verb_ships():
    from memory import mcp_server as M
    from memory import surfaces as S

    assert "quarantine" not in M._DISPATCH
    assert "quarantine" not in S.VERBLESS_TOOLS
    assert not any(v.verb == "quarantine" for v in S.VERBS)


def test_incident_tools_registered_on_both_surfaces():
    from memory import mcp_server as M
    from memory import surfaces as S

    for tool in ("untrust", "blast_radius"):
        assert tool in M._DISPATCH
        assert tool in S.VERBLESS_TOOLS
    # the registry parity lint's own invariant: every dispatched tool is claimed
    assert S.claimed_tools() == set(M._DISPATCH)


def test_untrust_tool_end_to_end(repo, memory_dir, monkeypatch):
    from memory import mcp_server as M

    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    _seed_corpus(repo, memory_dir)
    from memory import trust
    trust.mark_trusted(repo, memory_dir=memory_dir, origin="review")
    out = M._DISPATCH["untrust"]({"repo_root": repo})
    assert "no longer trusted" in out and "by-gate" in out
    assert trust.is_trusted(repo) is False


def _tree_snapshot(root):
    snap = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            snap[p] = os.path.getmtime(p)
    return snap
