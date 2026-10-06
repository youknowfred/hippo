"""SEC-21 — a gated surface in a git repo with NO corpus says so, not "untrusted".

``trust.gate_repo_root`` keys on the git root whether or not a corpus exists, so in a git repo
nobody ran setup in, every SEC-1-gated MCP tool and resource answered "this project's memory
corpus is untrusted … review and trust it", with nothing there to review. The owner's call
(option b): keep the gate, but check for an absent corpus first and give doctor's no-corpus
line. Every trust-gate test here deletes HIPPO_TRUST_ALL to exercise the REAL gate.
"""

from __future__ import annotations

import os

import pytest

from memory.mcp_server import handle_request

# Arguments that get each tool past its own argument checks to the gate.
_ARGS = {
    "abstention_fixtures": {"action": "list"},
    "blast_radius": {"path": "a.py"},
    "capture": {"action": "list"},
    "decision_history": {"name": "x"},
    "heal_baselines": {"dry_run": True},
    "new_memory": {"name": "x", "description": "d", "type": "project", "body": "b", "dry_run": True},
    "pack_extract": {"names": ["x"]},
    "pack_install_plan": {"source": "nowhere"},
    "recall": {"query": "deploy"},
    "secrets_scan": {"text": "hello"},
    "tend": {"action": "list"},
    "traverse": {"name": "x"},
    "why": {"query": "deploy"},
}
# Setup, consent and machine tools: their job in a repo with no corpus is not a gated refusal.
_NOT_GATED = {"bootstrap", "build_index", "doctor", "init", "recall_hook", "setup", "trust",
              "trust_corpus", "untrust"}
# The surfaces that refused as "untrusted" before SEC-21 (the reported repro).
_GATED = {"abstention_fixtures", "audit", "blast_radius", "co_recall_proposals",
          "decision_history", "dream", "heal_baselines", "interview", "new_memory",
          "pack_extract", "pack_install_plan", "pack_update_plan", "reconsolidate", "rederive",
          "resolve", "review", "tend", "traverse", "why"}


def _rpc(method, params):
    return handle_request({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})


def _tool(name, args):
    return _rpc("tools/call", {"name": name, "arguments": args})["result"]["content"][0]["text"]


def _resources():
    out = {}
    for res in _rpc("resources/list", {})["result"]["resources"]:
        out[res["uri"]] = _rpc("resources/read", {"uri": res["uri"]})["result"]["contents"][0]["text"]
    return out


@pytest.fixture
def untrusted(repo, monkeypatch):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    monkeypatch.delenv("HIPPO_MEMORY_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    return repo


def test_no_gated_surface_calls_a_missing_corpus_untrusted(untrusted):
    repo = untrusted
    names = sorted(t["name"] for t in _rpc("tools/list", {})["result"]["tools"])
    no_corpus = set()
    for name in names:
        if name in _NOT_GATED:
            continue
        text = _tool(name, _ARGS.get(name, {}))
        assert "untrusted" not in text.lower(), f"{name}: {text[:300]}"
        if "no corpus at" in text:
            assert "/hippo:setup" in text, f"{name}: {text[:300]}"
            no_corpus.add(name)
    assert _GATED <= no_corpus, sorted(_GATED - no_corpus)
    staged = _tool("dream", {"action": "generate", "stage": True})
    assert "untrusted" not in staged.lower() and "no corpus at" in staged, staged
    resources = _resources()
    assert resources
    for uri, text in resources.items():
        assert "untrusted" not in text.lower() and "WITHHELD" not in text, f"{uri}: {text[:300]}"
        assert "no corpus at" in text and "/hippo:setup" in text, f"{uri}: {text[:300]}"
    assert not os.path.exists(os.path.join(repo, ".claude"))  # every answer wrote nothing


def test_an_untrusted_corpus_that_exists_is_still_withheld(untrusted, memory_dir):
    """The gate itself is unchanged: a corpus that EXISTS and is untrusted still refuses as
    untrusted on every surface SEC-21 touched, never as "no corpus"."""
    with open(os.path.join(memory_dir, "m.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: m\ndescription: "deploy notes"\nmetadata:\n  type: project\n---\nbody\n')
    for name in sorted(_GATED):
        text = _tool(name, _ARGS.get(name, {}))
        assert "untrusted" in text.lower() and "no corpus at" not in text, f"{name}: {text[:300]}"
    for uri, text in _resources().items():
        assert "untrusted" in text.lower() and "no corpus at" not in text, f"{uri}: {text[:300]}"
