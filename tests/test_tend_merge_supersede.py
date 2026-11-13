"""TND-9 follow-up: a merge item's ``supersede`` verdict clears its pair.

The other merge verdicts (``updated``, ``distinct``) record a skip keyed by the entry's
evidence hash; ``supersede`` demoted the loser and returned without it, so the same pair
came back on the next pass. These tests pin that a supersede clears the pair, that a
later move of the pair's evidence re-arms it, that a refused supersede records nothing,
and that the outbox path (``write_path: outbox`` accepted) behaves the same while the
demote lands on the outbox copy.
"""

from __future__ import annotations

import json
import os
import subprocess

from memory import build_index as B
from memory import outbox as OB
from memory import policy_consent as PC
from memory import tend as T
from memory import tend_queue as Q
from memory import trust as TR
from memory.telemetry import log_episode

from .conftest import git_commit, write_file

_DUP = "zebra migration patterns in the savanna during seasonal drought cycles"


def _mem(name: str, desc: str) -> str:
    return f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: project\n---\nbody\n'


def _seed(repo, memory_dir, tmp_path, monkeypatch, outbox=False):
    td = str(tmp_path / "telemetry")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    write_file(repo, ".claude/memory/m-local.md", _mem("m-local", _DUP))
    for i in range(5):
        write_file(repo, f".claude/memory/filler-{i}.md",
                   _mem(f"filler-{i}", f"distinct topic {i} about build tooling caches and release step {i}"))
    if outbox:
        monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
        write_file(memory_dir, "hippo.json", json.dumps({"write_path": "outbox"}))
        write_file(repo, ".gitignore", ".hippo/\n.claude/memory.local/\n.claude/memory.outbox/\n")
    git_commit(repo, "seed corpus", 1_700_000_000)
    assert log_episode(["m-local"], query="zebra", repo_root=repo, telemetry_dir=td)
    write_file(repo, ".claude/memory/m-in.md", _mem("m-in", _DUP))
    git_commit(repo, "merge teammate memories (#7)", 1_700_000_100)
    if outbox:
        assert TR.mark_trusted(TR.gate_repo_root(memory_dir, repo), memory_dir=memory_dir, origin="init")
        assert PC.acknowledge(memory_dir, repo)["ok"]
    B.build_index(memory_dir, B.default_index_dir(memory_dir))


def _built(memory_dir, repo):
    return Q.build_queue(memory_dir, repo, kinds=("merge",), write_cache=False)


def _pending(memory_dir, repo):
    return [e["id"] for e in _built(memory_dir, repo)["pending"]]


_ID = "merge:m-in|m-local"


def test_supersede_clears_the_pair_and_moved_evidence_rearms_it(repo, memory_dir, tmp_path, monkeypatch):
    _seed(repo, memory_dir, tmp_path, monkeypatch)
    assert _pending(memory_dir, repo) == [_ID]
    r = T.apply(_ID, "supersede", memory_dir=memory_dir, repo_root=repo, winner="m-in")
    assert r["ok"], r
    built = _built(memory_dir, repo)
    assert [e["id"] for e in built["pending"]] == []
    assert [e["id"] for e in built["skipped"]] == [_ID]
    # the verdict's own edits (the winner's edge, the loser's window) do not re-arm it
    B.build_index(memory_dir, B.default_index_dir(memory_dir))
    assert _pending(memory_dir, repo) == []
    # The skip is keyed by the evidence hash: when the pair's evidence moves (here the
    # incoming memory is reworded, so its similarity score moves), the pair is back.
    write_file(memory_dir, "m-in.md", _mem("m-in", _DUP + " and the river crossings"))
    B.build_index(memory_dir, B.default_index_dir(memory_dir))
    assert _pending(memory_dir, repo) == [_ID]


def test_refused_supersede_records_nothing(repo, memory_dir, tmp_path, monkeypatch):
    _seed(repo, memory_dir, tmp_path, monkeypatch)
    r = T.apply(_ID, "supersede", memory_dir=memory_dir, repo_root=repo, winner="nobody")
    assert not r["ok"]
    assert _pending(memory_dir, repo) == [_ID]
    # a demote that fails must not hide the pair either
    from memory import reconsolidate

    monkeypatch.setattr(reconsolidate, "semantic_reverify", lambda *a, **k: {"error": "boom"})
    r = T.apply(_ID, "supersede", memory_dir=memory_dir, repo_root=repo, winner="m-in")
    assert not r["ok"], r
    assert _ID not in Q.read_state(memory_dir)["skip"]
    assert _pending(memory_dir, repo) == [_ID]


def test_supersede_in_outbox_mode_clears_the_pair_and_edits_the_copy(repo, memory_dir, tmp_path, monkeypatch):
    _seed(repo, memory_dir, tmp_path, monkeypatch, outbox=True)
    committed = open(os.path.join(memory_dir, "m-local.md"), "rb").read()
    assert _pending(memory_dir, repo) == [_ID]
    r = T.apply(_ID, "supersede", memory_dir=memory_dir, repo_root=repo, winner="m-in")
    assert r["ok"], r
    assert _pending(memory_dir, repo) == []
    assert [e["id"] for e in _built(memory_dir, repo)["skipped"]] == [_ID]
    status = subprocess.run(["git", "-C", repo, "status", "--porcelain"],
                            capture_output=True, text=True, check=True).stdout
    assert status == ""
    assert open(os.path.join(memory_dir, "m-local.md"), "rb").read() == committed
    # the demote lands on the outbox copies: the loser's validity window closes, and the
    # winner carries the supersedes edge
    with open(os.path.join(OB.outbox_dir(memory_dir), "m-local.md"), encoding="utf-8") as fh:
        assert "invalid_after:" in fh.read()
    with open(os.path.join(OB.outbox_dir(memory_dir), "m-in.md"), encoding="utf-8") as fh:
        winner = fh.read()
    assert "supersedes" in winner and "m-local" in winner
