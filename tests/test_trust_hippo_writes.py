"""Does every hippo write path keep the consent record in step with the bytes it wrote?

The consent record holds the sha256 of each memory file as consented. A hippo write that
rewrites a memory without updating it turns the file into drift by hippo's own hand:
recall then withholds a memory the user never changed, under a line asking them to
review a change they did not make.

Two families:

- **verdicts** (reverify graduate/fix/demote, through the CLI and the MCP tool): a
  per-item human review of the file, so the fold records the bytes written.
- **mechanical rewrites** (the empty-baseline heal, a citation refresh, the bulk
  backfill): not reviews, so they may not consent anything new. They carry consent
  forward only when the bytes they read were the consented ones; a file that had
  already drifted stays drifted.
"""

from __future__ import annotations

import os

import pytest

from memory import provenance as P
from memory import reconsolidate as R
from memory import trust as T

from .conftest import git_commit, write_file


def _gate(monkeypatch):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")


def _stale_memory(repo, memory_dir):
    """m_a cites src/foo.py at c1; src/foo.py then changes — m_a is stale."""
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(
        memory_dir,
        "m_a.md",
        f'---\nname: m_a\ndescription: "d"\ncited_paths: ["src/foo.py"]\nsource_commit: "{c1}"\n'
        "---\nbody cites src/foo.py\n",
    )
    write_file(repo, "src/foo.py", "x = 2\n")
    git_commit(repo, "c2", 1_700_000_100)


def _consent(repo, memory_dir):
    gate_root = T.gate_repo_root(memory_dir, repo)
    assert T.mark_trusted(gate_root, memory_dir=memory_dir, origin="init")
    assert T.untrusted_changes(gate_root, memory_dir)["changed"] == []
    return gate_root


def _drift(gate_root, memory_dir):
    d = T.untrusted_changes(gate_root, memory_dir)
    return d["changed"] + d["added"]


# --------------------------------------------------------------------------- #
# Verdicts: the reverify stamp records consent for the bytes it writes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("outcome", ["graduate", "fix", "demote"])
def test_reconsolidate_cli_reverify_verdict_keeps_the_file_consented(repo, memory_dir, monkeypatch, outcome):
    _gate(monkeypatch)
    _stale_memory(repo, memory_dir)
    gate_root = _consent(repo, memory_dir)
    before = open(os.path.join(memory_dir, "m_a.md"), encoding="utf-8").read()

    rc = R.main(["--reverify", "m_a", "--outcome", outcome, "--memory-dir", memory_dir,
                 "--repo-root", repo, "--telemetry-dir", os.path.join(repo, "tele")])

    assert rc == 0
    assert open(os.path.join(memory_dir, "m_a.md"), encoding="utf-8").read() != before  # it wrote
    assert _drift(gate_root, memory_dir) == [], "a reverify verdict left its own write withheld"


def test_mcp_reconsolidate_reverify_keeps_the_file_consented(repo, memory_dir, monkeypatch):
    from memory import mcp_server as M

    _gate(monkeypatch)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    _stale_memory(repo, memory_dir)
    gate_root = _consent(repo, memory_dir)
    before = open(os.path.join(memory_dir, "m_a.md"), encoding="utf-8").read()

    resp = M.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "reconsolidate",
                   "arguments": {"action": "reverify", "name": "m_a", "outcome": "graduate"}},
    })
    text = resp["result"]["content"][0]["text"]

    assert "refused" not in text and "withheld" not in text, text
    assert open(os.path.join(memory_dir, "m_a.md"), encoding="utf-8").read() != before
    assert _drift(gate_root, memory_dir) == []


# --------------------------------------------------------------------------- #
# Mechanical rewrites: consent carries forward, never launders drift
# --------------------------------------------------------------------------- #
def _empty_baseline_memory(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    git_commit(repo, "c1", 1_700_000_000)
    write_file(
        memory_dir, "m_e.md",
        '---\nname: m_e\ndescription: "d"\ncited_paths: ["src/foo.py"]\nsource_commit: ""\n'
        "---\nbody cites src/foo.py\n",
    )
    git_commit(repo, "memory", 1_700_000_001)


def test_heal_empty_baselines_does_not_turn_the_healed_file_into_drift(repo, memory_dir, monkeypatch):
    _gate(monkeypatch)
    _empty_baseline_memory(repo, memory_dir)
    gate_root = _consent(repo, memory_dir)

    healed, failed = P.heal_empty_baselines(memory_dir, repo)

    assert healed == ["m_e"] and not failed
    assert _drift(gate_root, memory_dir) == [], "the heal made its own write withheld"


def test_heal_keeps_an_already_drifted_file_withheld(repo, memory_dir, monkeypatch):
    _gate(monkeypatch)
    _empty_baseline_memory(repo, memory_dir)
    gate_root = _consent(repo, memory_dir)
    with open(os.path.join(memory_dir, "m_e.md"), "a", encoding="utf-8") as fh:
        fh.write("a hand edit nobody reviewed\n")

    P.heal_empty_baselines(memory_dir, repo)

    assert _drift(gate_root, memory_dir) == ["m_e"], "a mechanical write consented unreviewed bytes"


def _refreshable_memory(repo, memory_dir):
    write_file(repo, "package.json", '{"name":"x"}\n')
    write_file(repo, "src/keep.py", "k = 1\n")
    c1 = git_commit(repo, "code", 1_700_000_000)
    write_file(
        memory_dir, "m.md",
        f'---\nname: M\ncited_paths: ["src/keep.py"]\nsource_commit: "{c1}"\n'
        "---\nbody cites src/keep.py and package.json\n",
    )
    git_commit(repo, "memory", 1_700_000_001)


def test_provenance_refresh_one_does_not_turn_the_file_into_drift(repo, memory_dir, monkeypatch):
    _gate(monkeypatch)
    _refreshable_memory(repo, memory_dir)
    gate_root = _consent(repo, memory_dir)
    before = open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read()

    assert P.main(["--refresh-one", "m", "--memory-dir", memory_dir, "--repo-root", repo]) == 0

    assert open(os.path.join(memory_dir, "m.md"), encoding="utf-8").read() != before
    assert _drift(gate_root, memory_dir) == []


def test_bulk_backfill_refresh_carries_consent_only_for_consented_files(repo, memory_dir, monkeypatch):
    _gate(monkeypatch)
    _refreshable_memory(repo, memory_dir)
    write_file(
        memory_dir, "n.md",
        '---\nname: N\ncited_paths: []\nsource_commit: ""\n---\nbody cites package.json\n',
    )
    gate_root = _consent(repo, memory_dir)
    with open(os.path.join(memory_dir, "n.md"), "a", encoding="utf-8") as fh:
        fh.write("edited after consent\n")  # n is drift before the pass runs

    assert P.main(["--refresh", "--memory-dir", memory_dir, "--repo-root", repo]) == 0

    assert _drift(gate_root, memory_dir) == ["n"]


def test_carry_forward_is_a_no_op_without_a_matching_consented_hash(repo, memory_dir, monkeypatch):
    _gate(monkeypatch)
    _refreshable_memory(repo, memory_dir)
    gate_root = _consent(repo, memory_dir)
    path = os.path.join(memory_dir, "m.md")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("drift\n")
    assert T.carry_consent_forward(memory_dir, path, "0" * 64, repo) is False
    assert T.carry_consent_forward(memory_dir, path, None, repo) is False
    assert _drift(gate_root, memory_dir) == ["m"]
