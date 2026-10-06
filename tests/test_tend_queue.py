"""TND-1: one derived, ranked maintenance queue over every maintenance source."""

from __future__ import annotations

import hashlib
import json
import os
import time

import pytest

from .conftest import git_commit, write_file
from memory import tend_queue as Q
from memory.capture_queue import default_pending_dir
from memory.telemetry import default_telemetry_dir

_MEM = """---
name: {name}
description: "{desc}"
metadata:
  type: project
  source_commit: "{sc}"
---

{body}
"""


def _mem(memory_dir, name, body="plain body", sc="abc", desc=None):
    write_file(memory_dir, f"{name}.md", _MEM.format(name=name, desc=desc or f"about {name}", sc=sc, body=body))


def _tree_hashes(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


@pytest.fixture
def corpus(repo, memory_dir):
    _mem(memory_dir, "alpha", body="links to [[nowhere-at-all]]")
    _mem(memory_dir, "beta", sc="")
    write_file(memory_dir, "MEMORY.md", "# Memory\n\n## User\n\n## Working Style & Process Feedback\n")
    git_commit(repo, "corpus", 1_700_000_000)
    pending = default_pending_dir(memory_dir)
    os.makedirs(pending, exist_ok=True)
    with open(os.path.join(pending, "capture-s1.json"), "w", encoding="utf-8") as fh:
        json.dump({"session_id": "s1", "changed_paths": ["a.py"], "query_previews": ["q"]}, fh)
    os.makedirs(default_telemetry_dir(memory_dir), exist_ok=True)
    return repo, memory_dir


def _kinds(result):
    return [e["kind"] for e in result["pending"]]


def test_every_kind_is_counted_and_entries_carry_the_contract(corpus):
    repo, md = corpus
    r = Q.build_queue(md, repo)
    assert set(r["counts"]) == set(Q.KINDS)
    assert not r["errors"], r["errors"]
    for e in r["pending"]:
        assert set(e) >= {"id", "kind", "target", "evidence", "proposed", "gate", "rank"}
        assert e["id"] == f"{e['kind']}:{e['target']}"
        assert e["gate"] == Q.GATES[e["kind"]]
    assert r["counts"]["capture"] == 1
    assert r["counts"]["link"] == 1
    assert any(e["kind"] == "baseline" and e["target"] == "beta" for e in r["pending"])


def test_entries_come_ranked_integrity_first(corpus):
    repo, md = corpus
    kinds = _kinds(Q.build_queue(md, repo))
    ranks = [Q.KINDS.index(k) for k in kinds]
    assert ranks == sorted(ranks)
    assert kinds.index("baseline") < kinds.index("capture") < kinds.index("link")


def test_trust_drift_is_queued_first(corpus, monkeypatch):
    from memory import trust

    repo, md = corpus
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    trust.mark_trusted(repo, memory_dir=md)
    _mem(md, "alpha", body="edited by hand after consent")
    r = Q.build_queue(md, repo)
    assert r["pending"][0]["kind"] == "trust" and r["pending"][0]["target"] == "alpha"


def test_building_the_queue_never_writes_the_corpus(corpus):
    repo, md = corpus
    before = _tree_hashes(md)
    Q.build_queue(md, repo)
    assert _tree_hashes(md) == before


def test_a_broken_source_is_reported_and_the_rest_still_list(corpus, monkeypatch):
    repo, md = corpus

    def boom(*_a):
        raise RuntimeError("source down")

    monkeypatch.setitem(Q.SOURCES, "link", boom)
    r = Q.build_queue(md, repo)
    assert "source down" in r["errors"]["link"]
    assert r["counts"]["link"] == 0 and r["counts"]["capture"] == 1


def _write_state(md, doc):
    with open(Q.state_path(md), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def test_an_owner_hold_resolves_a_whole_kind_with_its_reason(corpus):
    repo, md = corpus
    _write_state(md, {"hold": {"link": {"reason": "kept on purpose", "at": "2026-10-05"}}})
    r = Q.build_queue(md, repo)
    assert r["counts"]["link"] == 0
    assert [e["kind"] for e in r["held"]] == ["link"]
    assert r["held"][0]["hold"]["reason"] == "kept on purpose"


def test_snooze_hides_until_its_date(corpus):
    repo, md = corpus
    entry = next(e for e in Q.build_queue(md, repo)["pending"] if e["kind"] == "capture")
    now = time.time()
    _write_state(md, {"snooze": {entry["id"]: now + 3600}})
    assert Q.build_queue(md, repo, now=now)["counts"]["capture"] == 0
    assert Q.build_queue(md, repo, now=now + 7200)["counts"]["capture"] == 1


def test_skip_lapses_when_the_evidence_moves(corpus):
    repo, md = corpus
    entry = next(e for e in Q.build_queue(md, repo)["pending"] if e["kind"] == "link")
    _write_state(md, {"skip": {entry["id"]: Q.evidence_hash(entry)}})
    assert Q.build_queue(md, repo)["counts"]["link"] == 0
    assert Q.evidence_hash(dict(entry, evidence="something new")) != Q.evidence_hash(entry)


def test_the_cache_holds_counts_only_and_needs_an_existing_ledger_dir(corpus, tmp_path):
    repo, md = corpus
    r = Q.build_queue(md, repo)
    cached = Q.cached_counts(md)
    assert cached["counts"] == r["counts"]
    assert Q.total_pending(r) == sum(r["counts"].values())


def test_no_ledger_dir_means_no_cache_file(repo, memory_dir):
    _mem(memory_dir, "solo")
    git_commit(repo, "one", 1_700_000_000)
    Q.build_queue(memory_dir, repo)
    assert not os.path.exists(default_telemetry_dir(memory_dir))


def test_a_corpus_behind_the_extractor_with_nothing_to_change_asks_for_a_stamp(corpus, monkeypatch):
    repo, md = corpus
    monkeypatch.setattr("memory.provenance.rederive_worklist", lambda *_a: [])
    with open(os.path.join(md, ".format"), "w", encoding="utf-8") as fh:
        json.dump({"corpus_format": 5, "cite_derivation": 4}, fh)
    r = Q.build_queue(md, repo, kinds=("derivation",))
    assert [e["target"] for e in r["pending"]] == ["corpus"]


def test_a_repo_with_no_corpus_has_no_derivation_item_and_no_failed_source(repo):
    """MIG-3: the derivation source raised FileNotFoundError in a repo with no corpus (so
    every list printed "the derivation source failed"), and an empty worklist alone would
    then ask to stamp a corpus that does not exist."""
    md = os.path.join(repo, ".claude", "memory")
    r = Q.build_queue(md, repo, kinds=("derivation",), write_cache=False)
    assert r["errors"] == {}
    assert r["pending"] == []
    assert not os.path.exists(os.path.join(repo, ".claude"))


def test_a_repo_with_no_corpus_has_no_baseline_item_and_no_source_fails(repo):
    """TND-8: the baseline source iterated the missing dir, so in a repo with no corpus every
    list said "the baseline source failed". No corpus means no baseline items, and with
    MIG-3's derivation half no source fails at all."""
    md = os.path.join(repo, ".claude", "memory")
    r = Q.build_queue(md, repo, write_cache=False)
    assert r["errors"] == {}
    assert r["pending"] == []
    assert not os.path.exists(os.path.join(repo, ".claude"))


def test_an_unreadable_corpus_still_fails_the_baseline_source(repo, memory_dir):
    """TND-8 covers an ABSENT corpus only: one that exists but cannot be read is still a
    failed source, named in the list, not an empty one."""
    try:
        os.chmod(memory_dir, 0o000)
        r = Q.build_queue(memory_dir, repo, kinds=("baseline",), write_cache=False)
    finally:
        os.chmod(memory_dir, 0o755)
    assert "PermissionError" in r["errors"].get("baseline", ""), r["errors"]
