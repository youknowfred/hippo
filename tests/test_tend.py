"""TND-2: the `tend` verb — the one queue worked one verdict at a time, every gate kept."""

from __future__ import annotations

import json
import os

import pytest

from .conftest import git_commit, write_file
from memory import tend as T
from memory import tend_queue as Q
from memory.capture_queue import default_pending_dir
from memory.telemetry import default_telemetry_dir

_MEM = """---
name: {name}
description: "about {name}"
metadata:
  type: project
  source_commit: "abc"
---

{body}
"""


@pytest.fixture
def corpus(repo, memory_dir):
    write_file(memory_dir, "alpha.md", _MEM.format(name="alpha", body="see [[ghost]]"))
    write_file(memory_dir, "beta.md", _MEM.format(name="beta", body="plain"))
    git_commit(repo, "corpus", 1_700_000_000)
    pending = default_pending_dir(memory_dir)
    os.makedirs(pending)
    for sid in ("s1", "s2"):
        with open(os.path.join(pending, f"capture-{sid}.json"), "w", encoding="utf-8") as fh:
            json.dump({"session_id": sid, "changed_paths": ["x.py"]}, fh)
    os.makedirs(default_telemetry_dir(memory_dir))
    return repo, memory_dir


def _ids(md, repo, kind=None):
    r = Q.build_queue(md, repo, kinds=(kind,) if kind else None)
    return [e["id"] for e in r["pending"]]


def test_list_and_next_render_the_queue(corpus, capsys):
    repo, md = corpus
    assert T.main(["list", "--memory-dir", md, "--repo-root", repo]) == 0
    out = capsys.readouterr().out
    assert "item(s) need a decision" in out and "capture:capture-s1.json" in out
    assert T.main(["next", "--memory-dir", md, "--repo-root", repo]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Next (") and "Verdicts:" in out and "gate:" in out


def test_capture_verdicts_drain_one_seed_at_a_time(corpus):
    repo, md = corpus
    r = T.apply("capture:capture-s1.json", "discard", memory_dir=md, repo_root=repo)
    assert r["ok"], r
    assert _ids(md, repo, "capture") == ["capture:capture-s2.json"]
    r = T.apply("capture:capture-s2.json", "done", memory_dir=md, repo_root=repo)
    assert r["ok"] and _ids(md, repo, "capture") == []


def test_a_verdict_outside_the_kinds_vocabulary_is_refused(corpus):
    repo, md = corpus
    r = T.apply("capture:capture-s1.json", "graduate", memory_dir=md, repo_root=repo)
    assert not r["ok"] and "discard, done" in r["message"]
    assert os.path.exists(os.path.join(default_pending_dir(md), "capture-s1.json"))


def test_an_id_not_in_the_queue_is_refused(corpus):
    repo, md = corpus
    r = T.apply("capture:nope.json", "discard", memory_dir=md, repo_root=repo)
    assert not r["ok"] and "not in the maintenance queue" in r["message"]


def test_link_done_is_rechecked_before_it_clears(corpus, monkeypatch):
    repo, md = corpus
    counted = []
    monkeypatch.setattr(T, "_count", lambda md_, kind, verdict: counted.append((kind, verdict)))
    [link_id] = _ids(md, repo, "link")
    r = T.apply(link_id, "done", memory_dir=md, repo_root=repo)
    assert not r["ok"] and "still reported" in r["message"] and counted == []
    write_file(md, "alpha.md", _MEM.format(name="alpha", body="see [[beta]]"))
    r = T.apply(link_id, "done", memory_dir=md, repo_root=repo)
    assert r == {"ok": True, "message": f"{link_id}: fixed."}  # the hand edit is the fix
    assert counted == [("link", "done")]


def test_floor_done_answers_fixed_once_the_floor_is_edited(corpus, monkeypatch):
    repo, md = corpus
    write_file(md, "MEMORY.md", "# floor\n\n## User\n- [Gone](gone.md) — removed\n")
    [floor_id] = _ids(md, repo, "floor")
    assert not T.apply(floor_id, "done", memory_dir=md, repo_root=repo)["ok"]
    write_file(md, "MEMORY.md", "# floor\n\n## User\n")
    r = T.apply(floor_id, "graduate", memory_dir=md, repo_root=repo)
    assert not r["ok"] and "floor items take: done" in r["message"]
    assert T.apply(floor_id, "done", memory_dir=md, repo_root=repo) == {"ok": True, "message": f"{floor_id}: fixed."}
    monkeypatch.setitem(Q.SOURCES, "floor", lambda md_, rr: 1 / 0)  # a check that cannot run proves nothing
    assert not T.apply(floor_id, "done", memory_dir=md, repo_root=repo)["ok"]


def test_reverify_and_baseline_route_to_the_reverify_engine(corpus, monkeypatch):
    repo, md = corpus
    calls = []

    def fake(name, outcome, memory_dir, repo_root, **kw):
        calls.append((name, outcome, kw.get("superseded_by")))
        return {"error": None}

    monkeypatch.setattr("memory.reconsolidate.semantic_reverify", fake)
    monkeypatch.setitem(Q.SOURCES, "reverify", lambda md_, rr: [Q._entry("reverify", "beta", "moved", "graduate")])
    monkeypatch.setitem(Q.SOURCES, "baseline", lambda md_, rr: [Q._entry("baseline", "alpha", "lost", "rebaseline")])
    assert T.apply("reverify:beta", "demote", memory_dir=md, repo_root=repo, superseded_by="alpha")["ok"]
    assert T.apply("baseline:alpha", "rebaseline", memory_dir=md, repo_root=repo)["ok"]
    assert calls == [("beta", "demote", "alpha"), ("alpha", "graduate", None)]


def test_contradiction_verdicts_need_both_sides_named(corpus, monkeypatch):
    repo, md = corpus
    seen = []
    monkeypatch.setitem(Q.SOURCES, "contradiction",
                        lambda md_, rr: [Q._entry("contradiction", "alpha|beta", "conflict", "read both")])
    monkeypatch.setattr("memory.resolve_view.apply_resolve_verdict",
                        lambda md_, rr, verdict, **kw: seen.append((verdict, kw)) or {"applied": True, "error": None})
    r = T.apply("contradiction:alpha|beta", "keep_one", memory_dir=md, repo_root=repo, winner="alpha")
    assert not r["ok"] and seen == []
    r = T.apply("contradiction:alpha|beta", "keep_one", memory_dir=md, repo_root=repo, winner="alpha", loser="beta")
    assert r["ok"] and seen == [("keep_one", {"winner": "alpha", "loser": "beta"})]
    assert T.apply("contradiction:alpha|beta", "not_conflicting", memory_dir=md, repo_root=repo)["ok"]
    assert seen[-1] == ("not_conflicting", {"a": "alpha", "b": "beta"})


def test_trust_items_route_to_the_consent_gate(corpus, monkeypatch):
    repo, md = corpus
    monkeypatch.setitem(Q.SOURCES, "trust", lambda md_, rr: [Q._entry("trust", "beta", "changed", "re-consent")])
    r = T.apply("trust:beta", "grant", memory_dir=md, repo_root=repo)
    assert not r["ok"] and "re-consent is its own gate" in r["message"]


def test_stamp_is_refused_while_any_memory_still_rederives(corpus, monkeypatch):
    repo, md = corpus
    monkeypatch.setattr("memory.provenance.rederive_worklist", lambda *_a: [{"name": "alpha"}])
    monkeypatch.setitem(Q.SOURCES, "derivation", lambda md_, rr: [Q._entry("derivation", "corpus", "behind", "stamp")])
    r = T.apply("derivation:corpus", "stamp", memory_dir=md, repo_root=repo)
    assert not r["ok"] and "still re-derive" in r["message"]


def test_hold_needs_a_reason_and_counts_as_resolved(corpus, capsys):
    repo, md = corpus
    assert not T.hold("capture", "", memory_dir=md, repo_root=repo)["ok"]
    assert T.hold("capture", "owner keeps these for later", memory_dir=md, repo_root=repo)["ok"]
    r = Q.build_queue(md, repo)
    assert r["counts"]["capture"] == 0 and {e["kind"] for e in r["held"]} == {"capture"}
    assert "owner keeps these for later" in T.render_list(r)
    assert T.release("capture", memory_dir=md, repo_root=repo)["ok"]
    assert Q.build_queue(md, repo)["counts"]["capture"] == 2


def test_snooze_and_skip_hide_one_item(corpus):
    repo, md = corpus
    assert T.snooze("capture:capture-s1.json", 1, memory_dir=md, repo_root=repo)["ok"]
    assert T.skip("capture:capture-s2.json", memory_dir=md, repo_root=repo)["ok"]
    assert _ids(md, repo, "capture") == []


def test_the_dogfood_shape_drains_to_empty_using_only_tend(corpus, monkeypatch):
    """Every kind present reaches zero pending through tend verbs alone."""
    repo, md = corpus
    write_file(md, "alpha.md", _MEM.format(name="alpha", body="see [[beta]]"))  # the link fix is an edit
    for item in _ids(md, repo):
        kind = item.split(":", 1)[0]
        if kind == "capture":
            assert T.apply(item, "discard", memory_dir=md, repo_root=repo)["ok"]
        else:
            assert T.hold(kind, "kept on purpose for this test", memory_dir=md, repo_root=repo)["ok"]
    assert Q.total_pending(Q.build_queue(md, repo)) == 0


def test_the_mcp_tool_serves_the_same_engine_and_gates_on_trust(corpus, monkeypatch):
    from memory.mcp_server import handle_request

    repo, md = corpus
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)

    def call(args):
        resp = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "tend", "arguments": args}})
        return resp["result"]["content"][0]["text"]

    assert "need a decision" in call({})
    assert call({"action": "apply", "id": "capture:capture-s1.json", "verdict": "discard"}).endswith("discarded.")
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    assert "withheld" in call({"action": "list"})


def test_doctor_reports_the_queue(corpus):
    from memory.doctor_checks_env import DoctorContext
    from memory.doctor_checks_lifecycle import check_tend_queue

    repo, md = corpus
    r = check_tend_queue(DoctorContext(memory_dir=md, repo_root=repo))
    assert r["status"] == "warn" and "2 capture" in r["message"] and "tend memory" in r["message"]
