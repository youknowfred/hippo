"""A pulled ``hippo.json`` may not switch on, by itself, a setting that changes what hippo does.

``hippo.json`` is outside the consent fingerprint (memory files only), so nothing asks for
re-consent when it changes. The settings that move where writes land or let a hook refuse
one are therefore honoured on a machine only after the user accepted their value there.
"""

from __future__ import annotations

import json
import os

from memory import policy_consent as PC
from memory import trust as T
from memory import trust_cli

from .conftest import git_commit, write_file


def _corpus(repo, memory_dir, policy=None, monkeypatch=None):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    write_file(memory_dir, "m_a.md", '---\nname: m_a\ndescription: "d"\n---\nbody\n')
    if policy is not None:
        write_file(memory_dir, "hippo.json", json.dumps(policy))
    git_commit(repo, "c1", 1_700_000_000)
    return T.gate_repo_root(memory_dir, repo)


def _trust(root, memory_dir):
    assert T.mark_trusted(root, memory_dir=memory_dir, origin="init")


def test_declared_but_not_accepted_reads_as_not_declared(repo, memory_dir, monkeypatch):
    root = _corpus(repo, memory_dir, {"write_path": "outbox"}, monkeypatch)
    _trust(root, memory_dir)
    assert PC.declared(memory_dir, "write_path") == "outbox"
    assert PC.honoured(memory_dir, "write_path", repo) is None
    assert [i["key"] for i in PC.pending(memory_dir, repo)] == ["write_path"]
    assert "hippo trust policy --accept" in PC.pending_line(memory_dir, repo)


def test_accepting_turns_it_on_and_a_changed_value_turns_it_off(repo, memory_dir, monkeypatch):
    root = _corpus(repo, memory_dir, {"gate": {"write": "deny"}}, monkeypatch)
    _trust(root, memory_dir)
    res = PC.acknowledge(memory_dir, repo)
    assert res["ok"] and res["accepted"] == {"gate": {"write": "deny"}}
    assert PC.honoured(memory_dir, "gate", repo) == {"write": "deny"}
    assert PC.pending(memory_dir, repo) == [] and PC.pending_line(memory_dir, repo) is None
    # A pulled edit changes the value: held back again until accepted again.
    write_file(memory_dir, "hippo.json", json.dumps({"gate": {"write": "deny", "bodies": True}}))
    assert PC.honoured(memory_dir, "gate", repo) is None
    assert PC.pending(memory_dir, repo)[0]["acknowledged"] == {"write": "deny"}
    # Key order in the file is not a change.
    write_file(memory_dir, "hippo.json", '{"gate": {"write": "deny"}, "other": 1}')
    assert PC.honoured(memory_dir, "gate", repo) == {"write": "deny"}


def test_an_untrusted_corpus_has_nothing_to_honour_and_cannot_accept(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, {"write_path": "outbox"}, monkeypatch)
    assert PC.honoured(memory_dir, "write_path", repo) is None
    res = PC.acknowledge(memory_dir, repo)
    assert res["ok"] is False and "not trusted" in res["error"]
    assert T.is_trusted(T.gate_repo_root(memory_dir, repo)) is False


def test_a_reconsent_keeps_what_was_accepted_and_accepts_nothing_new(repo, memory_dir, monkeypatch):
    root = _corpus(repo, memory_dir, {"write_path": "outbox"}, monkeypatch)
    _trust(root, memory_dir)
    assert PC.acknowledge(memory_dir, repo)["ok"]
    write_file(memory_dir, "hippo.json", json.dumps({"write_path": "outbox", "gate": {"write": "deny"}}))
    _trust(root, memory_dir)  # drift re-consent: the whole-corpus grant again
    assert PC.honoured(memory_dir, "write_path", repo) == "outbox"
    assert PC.honoured(memory_dir, "gate", repo) is None


def test_removing_the_key_drops_the_acceptance(repo, memory_dir, monkeypatch):
    root = _corpus(repo, memory_dir, {"write_path": "outbox"}, monkeypatch)
    _trust(root, memory_dir)
    assert PC.acknowledge(memory_dir, repo)["ok"]
    write_file(memory_dir, "hippo.json", "{}")
    assert PC.acknowledge(memory_dir, repo)["ok"]
    write_file(memory_dir, "hippo.json", json.dumps({"write_path": "outbox"}))
    assert PC.honoured(memory_dir, "write_path", repo) is None


def test_the_ci_bypass_honours_without_asking(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, {"write_path": "outbox"}, monkeypatch)
    monkeypatch.setenv("HIPPO_TRUST_ALL", "1")
    assert PC.honoured(memory_dir, "write_path", repo) == "outbox"
    assert PC.pending(memory_dir, repo) == []


def test_other_keys_and_the_legacy_marker_are_untouched(repo, memory_dir, monkeypatch):
    root = _corpus(repo, memory_dir, {"volatile_paths": ["a/*"]}, monkeypatch)
    write_file(memory_dir, ".format", json.dumps({"corpus_format": 1, "write_path": "outbox"}))
    _trust(root, memory_dir)
    assert PC.honoured(memory_dir, "volatile_paths", repo) == ["a/*"]
    assert PC.declared(memory_dir, "write_path") is None  # .format is not a home for it
    assert PC.pending(memory_dir, repo) == []
    assert "nothing that needs accepting" in PC.render_policy(memory_dir, repo)
    assert PC.acknowledge(memory_dir, repo, keys=["volatile_paths"])["ok"] is False


def test_cli_shows_then_accepts(repo, memory_dir, monkeypatch, capsys):
    root = _corpus(repo, memory_dir, {"write_path": "outbox"}, monkeypatch)
    _trust(root, memory_dir)
    args = ["--memory-dir", memory_dir, "--repo-root", repo]
    assert trust_cli.main(["policy", *args]) == 0
    out = capsys.readouterr().out
    assert 'write_path = "outbox"' in out and "NOT accepted" in out
    assert PC.honoured(memory_dir, "write_path", repo) is None  # showing accepts nothing
    assert trust_cli.main(["status", *args]) == 0
    assert "policy: " in capsys.readouterr().out
    assert trust_cli.main(["policy", "--accept", *args]) == 0
    assert "accepted for this machine: write_path" in capsys.readouterr().out
    assert PC.honoured(memory_dir, "write_path", repo) == "outbox"
    assert trust_cli.main(["status", *args]) == 0
    assert "policy: " not in capsys.readouterr().out


def test_cli_accept_fails_closed_on_an_untrusted_corpus(repo, memory_dir, monkeypatch, capsys):
    _corpus(repo, memory_dir, {"gate": {"write": "deny"}}, monkeypatch)
    assert trust_cli.main(["policy", "--accept", "--memory-dir", memory_dir, "--repo-root", repo]) == 1
    assert "NOT accepted" in capsys.readouterr().out
    assert not os.path.exists(T.trust_registry_path()) or PC.honoured(memory_dir, "gate", repo) is None
