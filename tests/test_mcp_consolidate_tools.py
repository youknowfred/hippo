"""Tests for the MCP consolidate-flow tools (INT-13) — /hippo:consolidate as per-item tools.

The Claude desktop app's Bash tool never inherits CLAUDE_PLUGIN_DATA, so the consolidate
skill's bash blocks can't run there; these tools re-serve the SAME engine calls the skill's
blocks run, one approval-gated step per call. Hermetic: tmp corpora, pending queues, and
telemetry ledgers via the HIPPO_* env overrides; trust-gate tests delete HIPPO_TRUST_ALL to
exercise the REAL gate (conftest's autouse bypass + tmp HIPPO_TRUST_FILE keep it hermetic).
"""

from __future__ import annotations

import json
import os
import time

import pytest

from memory import build_index as B
from memory import mcp_server as M
from memory import mcp_tools_consolidate as MC
from memory import telemetry as T
from memory.build_index import default_index_dir

from .conftest import git_commit, write_file


def _mem(name, desc, mtype="project", body="body"):
    return f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: {mtype}\n---\n{body}\n'


def _pmem(name, cited, source_commit, body=None):
    """A memory with citation provenance — the reconsolidate fixtures' shape."""
    cp = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    body = body if body is not None else f"body for {name} — see {cited[0]}."
    return (
        f'---\nname: {name}\ndescription: "{name} description"\ncited_paths: {cp}\n'
        f'source_commit: "{source_commit}"\n---\n{body}\n'
    )


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """Two unlinked memories in a tmp corpus, with the queue + ledgers pinned to tmp too."""
    md = str(tmp_path / ".claude" / "memory")
    os.makedirs(md)
    with open(os.path.join(md, "deploy_runbook.md"), "w") as fh:
        fh.write(_mem("deploy_runbook", "how the web service is deployed via the canary lane"))
    with open(os.path.join(md, "rollback_plan.md"), "w") as fh:
        fh.write(_mem("rollback_plan", "how to roll back a bad web deploy"))
    B.build_index(md, default_index_dir(md))
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))  # hermetic non-git repo_root
    monkeypatch.setenv("HIPPO_PENDING_DIR", str(tmp_path / "pending"))
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "tele"))
    return md


@pytest.fixture
def repo_corpus(repo, monkeypatch):
    """A corpus INSIDE a git repo with one drifted cited file — the reverify fixtures.

    Commit times are recent-relative (not pinned epochs) because the tool surface cannot
    pass find_stale's ``since`` override — the default wall-clock window must catch them.
    """
    now = int(time.time())
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", now - 7200)
    write_file(md, "m_alpha.md", _pmem("m_alpha", ["src/foo.py"], c1))
    write_file(md, "m_keep.md", _pmem("m_keep", ["src/foo.py"], c1))
    git_commit(repo, "add memories", now - 7000)
    write_file(repo, "src/foo.py", "x = 2\n")  # the cited file drifts
    git_commit(repo, "drift", now - 3600)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", os.path.join(repo, "tele"))
    return md


def _call(tool, arguments, req_id=99):
    return M.handle_request(
        {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
         "params": {"name": tool, "arguments": arguments}}
    )


def _text(resp):
    return resp["result"]["content"][0]["text"]


def _seed_events(td, session_names):
    """Synthesize the recall-event ledger (recently-recalled names, per session)."""
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "recall_events.jsonl"), "w", encoding="utf-8") as fh:
        for sid, names in session_names:
            fh.write(json.dumps({"session_id": sid, "names": names, "backend": "bm25"}) + "\n")


def _seed(pending_dir, name="capture-s1.json", **over):
    """One fabricated schema-2 capture seed in the pending queue."""
    os.makedirs(pending_dir, exist_ok=True)
    seed = {
        "schema": 2, "kind": "session-capture", "session_id": "s1",
        "head_commit": "a" * 40, "head": "b" * 40,
        "changed_paths": ["src/app.py"], "recalled_names": ["deploy_runbook"],
        "query_previews": ["how do we deploy?"], "episode_count": 3,
        "earliest_ts": 1_700_000_000, "captured_at": 1_700_000_001.0,
        "diff_hunks": "+x = 1", "hunks_secret_flagged": False,
        "decisions": ["ship via the canary lane"],
        "salience": {"score": 5, "trivial": False},
    }
    seed.update(over)
    path = os.path.join(pending_dir, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(seed, fh)
    return path


# --------------------------------------------------------------------------- #
# capture — the CAP-2 queue verbs (list / discard / snooze / add_decision)
# --------------------------------------------------------------------------- #
def test_capture_list_empty_queue(corpus):
    assert "No pending captures" in _text(_call("capture", {}))


def test_capture_list_shows_provenance_queue_dir_and_drain_recipe(corpus, tmp_path):
    _seed(str(tmp_path / "pending"))
    text = _text(_call("capture", {"action": "list"}))
    assert "session=s1" in text and "src/app.py" in text
    assert "ship via the canary lane" in text                   # the GRW-4 decision travels
    assert "queue dir:" in text and str(tmp_path / "pending") in text
    assert "check:true" in text and "action='discard'" in text  # the per-item drain recipe
    assert "in bulk" in text


def test_capture_list_flagged_hunks_map_the_gate_to_the_secrets_scan_tool(corpus, tmp_path):
    _seed(str(tmp_path / "pending"), hunks_secret_flagged=True)
    text = _text(_call("capture", {"action": "list"}))
    assert "scan_with_remediation" in text  # the CLI listing's own warning still travels
    assert "secrets_scan tool" in text      # ...plus the mapping for THIS surface


def test_capture_discard_by_filename_drains_the_seed(corpus, tmp_path):
    path = _seed(str(tmp_path / "pending"))
    done = _text(_call("capture", {"action": "discard", "path": "capture-s1.json"}))
    assert "discarded" in done and not os.path.exists(path)


def test_capture_discard_is_contained_to_the_pending_queue(corpus, tmp_path):
    """A model-invoked remove must never reach outside the queue (the CLI trusts a
    human-typed path; this surface must not) — nor touch the queue's own dotfile state."""
    _seed(str(tmp_path / "pending"))
    outside = tmp_path / "victim.json"
    outside.write_text("{}")
    assert "REFUSED" in _text(_call("capture", {"action": "discard", "path": str(outside)}))
    assert outside.exists()
    assert "REFUSED" in _text(_call("capture", {"action": "discard", "path": "../victim.json"}))
    assert outside.exists()
    assert "REFUSED" in _text(
        _call("capture", {"action": "discard", "path": ".capture-snooze.json"})
    )
    assert "required" in _text(_call("capture", {"action": "discard"}))


def test_capture_snooze_defers_the_nudge(corpus):
    from memory.capture import queue_snoozed

    assert "snoozed" in _text(_call("capture", {"action": "snooze"}))
    assert queue_snoozed(memory_dir=corpus)


def test_capture_add_decision_lands_in_the_ledger(corpus, tmp_path):
    from memory.telemetry import read_decisions

    text = _text(_call("capture", {"action": "add_decision", "text": "we chose the canary lane"}))
    assert "decision recorded" in text
    assert any(
        d.get("text") == "we chose the canary lane"
        for d in read_decisions(str(tmp_path / "tele"))
    )


def test_capture_add_decision_requires_text(corpus):
    assert "required" in _text(_call("capture", {"action": "add_decision"}))


def test_capture_add_decision_reply_is_honest_about_attribution(corpus, tmp_path):
    """WRT-3: this surface never receives the harness session id, so the row is keyed on
    the shared file token and strict seed-matching can NEVER reach it. The old reply
    promised exactly that seed-riding — a literal falsehood for every row ever recorded
    here (the ledger's only two rows, 07-13, died that way). Wording pinned."""
    text = _text(_call("capture", {"action": "add_decision", "text": "why we chose X"}))
    assert text == (
        "decision recorded unattributed — this MCP surface receives no harness session "
        "id, so the row cannot ride the session-proven decisions list; it will surface "
        "LABELED as a window-matched decision at the drain of the session whose episode "
        "span covers it"
    )
    assert "will ride this session's capture seed" not in text


# --------------------------------------------------------------------------- #
# secrets_scan — the GRW-1 hard gate as a primitive
# --------------------------------------------------------------------------- #
def test_secrets_scan_clean_text_is_safe_to_fence():
    text = _text(_call("secrets_scan", {"text": "x = compute_total(rows)\nreturn x\n"}))
    assert "clean" in text and "safe to fence" in text


def test_secrets_scan_flags_a_secret_and_refuses_the_fence():
    text = _text(_call("secrets_scan", {"text": 'aws_key = "AKIAIOSFODNN7EXAMPLE"'}))
    assert "HARD GATE" in text and "do NOT fence" in text


def test_secrets_scan_requires_text():
    assert "required" in _text(_call("secrets_scan", {}))


# --------------------------------------------------------------------------- #
# new_memory check:true — the CAP-3 dry-run on the existing write tool
# --------------------------------------------------------------------------- #
def test_new_memory_check_writes_nothing_then_real_write_works(corpus):
    args = {
        "name": "canary_gate",
        "description": "the canary gate promotes after five clean minutes",
        "type": "project",
    }
    text = _text(_call("new_memory", {**args, "check": True}))
    assert "route =" in text and "nothing was written" in text
    assert "baseline:" in text
    assert not os.path.exists(os.path.join(corpus, "canary_gate.md"))
    # The real write path is unchanged: the same args without check create the file.
    done = _text(_call("new_memory", args))
    assert "created:" in done
    assert os.path.exists(os.path.join(corpus, "canary_gate.md"))


def test_new_memory_check_is_trust_gated_and_leaks_nothing(corpus, monkeypatch):
    """SEC-1/SEC-13: the dry-run READS the corpus's descriptions (dup neighbors) — an
    untrusted corpus refuses the check exactly as it refuses the write, with no leak."""
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    text = _text(_call("new_memory", {
        "name": "x", "description": "how the web service is deployed", "type": "project",
        "check": True,
    }))
    assert "REFUSED" in text
    assert "canary lane" not in text  # no injectable description escapes the gate


# --------------------------------------------------------------------------- #
# reconsolidate — the LIF-1 worklist + the ONE per-item verdict gate
# --------------------------------------------------------------------------- #
def test_reconsolidate_worklist_lists_recalled_stale_only(repo_corpus, repo):
    _seed_events(os.path.join(repo, "tele"), [("s1", ["m_alpha"])])
    text = _text(_call("reconsolidate", {}))
    assert "m_alpha" in text and "src/foo.py" in text
    assert "reverify" in text  # the per-item next step is named
    assert "m_keep" not in text  # stale but never recalled -> not on the worklist


def test_reconsolidate_worklist_empty_is_legible(repo_corpus):
    assert "No recently-recalled memory is currently stale." in _text(
        _call("reconsolidate", {"action": "worklist"})
    )


def test_reconsolidate_reverify_graduate_clears_staleness(repo_corpus):
    text = _text(_call("reconsolidate", {
        "action": "reverify", "name": "m_alpha", "outcome": "graduate",
    }))
    assert "outcome=graduate" in text
    assert "staleness flag cleared" in text and "logged" in text


def test_reconsolidate_reverify_demote_chains_invalid_after(repo_corpus):
    text = _text(_call("reconsolidate", {
        "action": "reverify", "name": "m_alpha", "outcome": "demote",
    }))
    assert "outcome=demote" in text
    assert "staleness flag unchanged" in text  # demote must never re-baseline (FM2)
    assert "invalid_after set" in text and "pre-cut penalty" in text
    with open(os.path.join(repo_corpus, "m_alpha.md"), encoding="utf-8") as fh:
        assert "invalid_after:" in fh.read()


def test_reconsolidate_reverify_demote_with_successor_writes_the_edge(repo_corpus):
    text = _text(_call("reconsolidate", {
        "action": "reverify", "name": "m_alpha", "outcome": "demote",
        "superseded_by": "m_keep",
    }))
    assert "supersedes edge written to m_keep" in text
    with open(os.path.join(repo_corpus, "m_keep.md"), encoding="utf-8") as fh:
        assert "supersedes" in fh.read()


def test_reconsolidate_reverify_snooze_is_a_deferral(repo_corpus, repo):
    from memory.telemetry import read_reconsolidation_events

    text = _text(_call("reconsolidate", {
        "action": "reverify", "name": "m_alpha", "outcome": "snooze",
    }))
    assert "ack logged" in text and "deferral" in text
    evs = list(read_reconsolidation_events(os.path.join(repo, "tele")))
    assert evs and evs[-1]["name"] == "m_alpha" and evs[-1]["outcome"] == "snooze"


def test_reconsolidate_reverify_refuses_graduate_plus_successor(repo_corpus):
    """The engine's own coherence rule travels: a memory just confirmed CURRENT cannot
    simultaneously be superseded — the refusal reaches this surface verbatim."""
    text = _text(_call("reconsolidate", {
        "action": "reverify", "name": "m_alpha", "outcome": "graduate",
        "superseded_by": "m_keep",
    }))
    assert "refused" in text


def test_reconsolidate_reverify_requires_name_and_outcome(repo_corpus):
    assert "required" in _text(_call("reconsolidate", {"action": "reverify"}))


def test_reconsolidate_worklist_names_the_brief_action(repo_corpus, repo):
    _seed_events(os.path.join(repo, "tele"), [("s1", ["m_alpha"])])
    text = _text(_call("reconsolidate", {}))
    assert "action='brief'" in text  # EVD-1: the evidence step is named on the worklist


def test_reconsolidate_brief_renders_evidence(repo_corpus, repo):
    _seed_events(os.path.join(repo, "tele"), [("s1", ["m_alpha"])])
    text = _text(_call("reconsolidate", {"action": "brief", "name": "m_alpha"}))
    assert "m_alpha — baseline" in text and "diffstat:" in text
    assert "verdict (yours" in text  # evidence only — the verdict stays human (LIF-1)


def test_reconsolidate_brief_requires_name(repo_corpus):
    assert "required" in _text(_call("reconsolidate", {"action": "brief"}))


def test_reconsolidate_brief_unknown_name_is_legible(repo_corpus):
    assert "nothing to brief" in _text(
        _call("reconsolidate", {"action": "brief", "name": "no_such"})
    )


def test_reconsolidate_untrusted_is_withheld(corpus, monkeypatch):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    text = _text(_call("reconsolidate", {}))
    assert "withheld" in text and "trust_corpus" in text


# --------------------------------------------------------------------------- #
# build_index — Step 3, the graph/staleness refresh
# --------------------------------------------------------------------------- #
def test_build_index_tool_refreshes_and_reports_counts(corpus):
    text = _text(_call("build_index", {}))
    assert "index refreshed" in text and "2 memories" in text
    assert "links.json" in text


def test_build_index_tool_persists_a_new_wikilink_edge(corpus):
    """The consolidate Step 4 follow-up, end to end: append a wikilink (the per-item
    agent edit), refresh through the tool, and the persisted graph carries the edge."""
    from memory.links import build_graph

    with open(os.path.join(corpus, "rollback_plan.md"), "a", encoding="utf-8") as fh:
        fh.write("\nRelated: [[deploy_runbook]]\n")
    assert "index refreshed" in _text(_call("build_index", {}))
    graph = build_graph(corpus, default_index_dir(corpus))
    assert "deploy_runbook" in graph.outbound("rollback_plan")


def test_build_index_tool_prefers_the_fresh_interpreter(corpus, tmp_path, monkeypatch):
    """When a fresher venv python exists, the FULL -m memory.build_index runs there (the
    same stale-interpreter discipline as doctor/init) — its own report comes back."""
    import sys

    shim = tmp_path / "venvish" / "python"
    os.makedirs(shim.parent)
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    os.chmod(shim, 0o755)
    monkeypatch.setattr(MC, "_fresh_python", lambda: str(shim))
    text = _text(_call("build_index", {}))
    assert "index dir" in text and "memories" in text  # the CLI's own report lines


def test_build_index_tool_without_a_corpus_names_init(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_MEMORY_DIR", str(tmp_path / "nope"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    assert "init" in _text(_call("build_index", {}))


# --------------------------------------------------------------------------- #
# co_recall_proposals — GRW-2, Step 4 (read-only)
# --------------------------------------------------------------------------- #
def _episodes(td, sessions):
    for sid, names in sessions:
        T.log_episode(names, query=f"query in {sid}", telemetry_dir=td, session_id=sid)


def test_co_recall_proposals_surfaces_a_recurring_pair(corpus, tmp_path):
    td = str(tmp_path / "tele")
    _episodes(td, [
        ("s1", ["deploy_runbook", "rollback_plan"]),
        ("s2", ["deploy_runbook", "rollback_plan"]),
        ("s3", ["deploy_runbook", "rollback_plan"]),
        # MEA-3: a wider session universe so the pair's co-occurrence beats independence
        # (members in 3/6 sessions each, together in all 3 -> lift 2.0). Without these,
        # both members exist ONLY in shared sessions and lift is exactly 1.0 — a perfect
        # confound the null model now correctly refuses to propose.
        ("s4", ["other_note"]),
        ("s5", ["other_note"]),
        ("s6", ["other_note"]),
    ])
    text = _text(_call("co_recall_proposals", {}))
    assert "deploy_runbook <-> rollback_plan" in text
    assert "3 distinct sessions" in text
    assert "lift 2.0" in text
    assert "ONE side's body" in text and "build_index" in text  # the per-item recipe travels


def test_co_recall_proposals_drops_already_linked_pairs(corpus, tmp_path):
    with open(os.path.join(corpus, "deploy_runbook.md"), "a", encoding="utf-8") as fh:
        fh.write("\nRelated: [[rollback_plan]]\n")
    _episodes(str(tmp_path / "tele"), [
        ("s1", ["deploy_runbook", "rollback_plan"]),
        ("s2", ["deploy_runbook", "rollback_plan"]),
        ("s3", ["deploy_runbook", "rollback_plan"]),
    ])
    assert "no co-recall pairs above threshold" in _text(_call("co_recall_proposals", {}))


def test_co_recall_proposals_below_threshold_is_empty_by_design(corpus, tmp_path):
    _episodes(str(tmp_path / "tele"), [
        ("s1", ["deploy_runbook", "rollback_plan"]),
        ("s2", ["deploy_runbook", "rollback_plan"]),  # 2 sessions < the threshold
    ])
    assert "no co-recall pairs above threshold" in _text(_call("co_recall_proposals", {}))


def test_co_recall_proposals_untrusted_is_withheld(corpus, monkeypatch):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    text = _text(_call("co_recall_proposals", {}))
    assert "withheld" in text and "trust_corpus" in text


# --------------------------------------------------------------------------- #
# abstention_fixtures — SIG-6, Step 5 (draft + per-item confirm)
# --------------------------------------------------------------------------- #
def _abstain(td, query, sid="s"):
    T.log_recall_event([], query=query, k=6, latency_ms=1.0, telemetry_dir=td, session_id=sid)


def test_abstention_fixtures_draft_then_confirm_end_to_end(corpus, tmp_path):
    td = str(tmp_path / "tele")
    for q in ("how do I roll back a deploy", "roll back a deploy safely", "deploy roll back steps"):
        _abstain(td, q)

    drafted = _text(_call("abstention_fixtures", {"action": "draft"}))
    assert "per-item confirm" in drafted
    assert "roll" in drafted  # the recurring cluster's terms/query made it into the drafts

    confirmed = json.loads(_text(_call("abstention_fixtures", {
        "action": "confirm",
        "query": "how do I roll back a deploy",
        "expected": ["rollback_plan"],
    })))
    assert confirmed.get("ok") is True
    fixture = os.path.join(corpus, ".audit-fixtures", "recall_hard_set.yaml")
    with open(fixture, encoding="utf-8") as fh:
        text = fh.read()
    assert "rollback_plan" in text and "abstention" in text


def test_abstention_fixtures_confirm_refuses_a_fabricated_stem(corpus):
    refused = json.loads(_text(_call("abstention_fixtures", {
        "action": "confirm", "query": "some recurring gap", "expected": ["ghost_memory"],
    })))
    assert refused.get("ok") is False


def test_abstention_fixtures_confirm_requires_query_and_stems(corpus):
    assert "required" in _text(_call("abstention_fixtures", {"action": "confirm"}))


def test_abstention_fixtures_untrusted_is_withheld(corpus, monkeypatch):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    text = _text(_call("abstention_fixtures", {}))
    assert "withheld" in text and "trust_corpus" in text


# --------------------------------------------------------------------------- #
# The surface mappings — doctor's footer, the skill's preflight, the SessionStart note.
# The three places that TELL an agent this flow exists on this surface must all name
# the real tools (the exact rot v1.10.1 fixed for the setup tools).
# --------------------------------------------------------------------------- #
_FLOW_TOOLS = (
    "capture", "secrets_scan", "reconsolidate", "build_index",
    "co_recall_proposals", "abstention_fixtures",
)


def test_doctor_footer_maps_consolidate_to_the_flow_tools(corpus, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    text = _text(_call("doctor", {}))
    assert "/hippo:consolidate" in text
    for tool in _FLOW_TOOLS:
        assert tool in text, f"doctor's MCP-surface mapping no longer names {tool}"


def test_consolidate_skill_preflight_maps_every_flow_tool():
    """The skill's guard must route Desktop to the tools (not claim no path exists), and
    every tool it names must be one the server actually serves."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "plugin", "skills", "consolidate", "SKILL.md"
    )
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert "no Desktop-safe MCP-tool equivalent" not in text  # the pre-INT-13 claim is gone
    for tool in _FLOW_TOOLS:
        assert tool in text, f"consolidate SKILL.md no longer names the {tool} tool"
        assert tool in M._DISPATCH, f"SKILL.md names {tool} but the server does not serve it"
    assert "check:true" in text or "check: true" in text  # the CAP-3 dry-run flag


def test_desktop_surface_note_maps_consolidate_to_the_flow_tools():
    from memory.session_start import _DESKTOP_SURFACE_NOTE as note

    for tool in _FLOW_TOOLS:
        assert tool in note, f"the Desktop surface note no longer names {tool}"


def test_capture_list_names_corrupt_seed_files(corpus, tmp_path):
    """RCH-9: a corrupt seed silently vanished from the listing — while the
    SessionStart nudge (a bare file count) still counted it, so the queue said '2
    pending' and the drain showed one. The listing must name what it cannot read."""
    pd = str(tmp_path / "pending")
    _seed(pd)
    with open(os.path.join(pd, "capture-broken.json"), "w", encoding="utf-8") as fh:
        fh.write("{ definitely not json")
    text = _text(_call("capture", {"action": "list"}))
    assert "session=s1" in text
    assert "capture-broken.json" in text, "the unreadable seed must be named"
    assert "corrupt" in text.lower()
