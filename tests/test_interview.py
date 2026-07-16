"""EXT-3: the interview loop — consolidate asks up to three grounded questions.

hippo tells, but never asks. The blind-spot backlog (SIG-3), the contradiction inbox
(GOV-1), and expiring generated drafts (DRM-6) are machine-detected GAPS with no
encode-side loop — the human never thinks to write the missing memory. The module
under test renders AT MOST three template questions per consolidate session, each
citing its source signal verbatim, each answer routing through the EXISTING per-item
write verbs (the asks step itself writes nothing to the corpus, ever), each decline
REMEMBERED in telemetry so nothing re-asks, and a "later" is a snooze. Zero LLM on
the default path — templates over existing signals.
"""

from __future__ import annotations

import json
import os

from memory import interview as IV
from memory import telemetry as T
from memory.telemetry import default_telemetry_dir

from .conftest import write_file


def _abstain(td, preview: str, n: int = 3, sid: str = "s1"):
    """n abstention events for one recurring query (backend == 'none')."""
    for _ in range(n):
        assert T.log_recall_event(
            [], query=preview, k=3, latency_ms=1.0, telemetry_dir=td, session_id=sid
        )


def _mem(md, name, desc="a note", extra=""):
    write_file(
        md,
        f"{name}.md",
        f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: project\n{extra}---\nBody.\n',
    )


def _contradiction(md, a="alpha-note", b="beta-note"):
    from memory.links import add_typed_relation

    _mem(md, a, "the deploy runs on Mondays")
    _mem(md, b, "the deploy runs on Fridays")
    r = add_typed_relation(os.path.join(md, f"{a}.md"), "contradicts", b)
    assert r.get("changed"), r
    return a, b


# --------------------------------------------------------------------------- #
# Gathering: grounded, capped, empty-norm
# --------------------------------------------------------------------------- #
def test_empty_norm_zero_questions(repo, memory_dir):
    assert IV.gather_questions(memory_dir, repo_root=repo) == []


def test_abstention_question_cites_the_evidence_verbatim(repo, memory_dir):
    td = default_telemetry_dir(memory_dir)
    _abstain(td, "how do we deploy the staging environment", n=4)
    qs = IV.gather_questions(memory_dir, repo_root=repo, telemetry_dir=td)
    assert len(qs) == 1
    q = qs[0]
    assert q["kind"] == "abstention"
    assert "how do we deploy the staging environment" in q["question"]
    assert "4" in q["question"], "the count is the evidence — cite it"
    assert "new_memory" in q["route"] or "/hippo:new" in q["route"]


def test_contradiction_question_names_both_sides(repo, memory_dir):
    a, b = _contradiction(memory_dir)
    qs = IV.gather_questions(memory_dir, repo_root=repo)
    assert len(qs) == 1
    q = qs[0]
    assert q["kind"] == "contradiction"
    assert a in q["question"] and b in q["question"]
    assert "resolve" in q["route"]


def test_expiring_draft_question_names_the_draft(repo, memory_dir, monkeypatch):
    from memory import dream_generate as DG

    def _fake_sweep(md, telemetry_dir=None):
        return {
            "rows": [], "graduate": [], "awaiting_archive": [],
            "expire": [{"stem": "auto-draft-note", "age": 9, "confidence": "draft"}],
            "alarm": {},
        }

    monkeypatch.setattr(DG, "draft_sweep_state", _fake_sweep)
    qs = IV.gather_questions(memory_dir, repo_root=repo)
    assert len(qs) == 1
    q = qs[0]
    assert q["kind"] == "draft"
    assert "auto-draft-note" in q["question"]
    assert "reconsolidate" in q["route"]


def test_cap_is_three_across_all_signals(repo, memory_dir):
    td = default_telemetry_dir(memory_dir)
    # Four distinct recurring abstention clusters + one contradiction = 5 candidates.
    _abstain(td, "how do we deploy the staging environment", n=3)
    _abstain(td, "what rotates the signing keys quarterly", n=3)
    _abstain(td, "where does the billing reconciliation job live", n=3)
    _abstain(td, "who owns the incident escalation runbook", n=3)
    _contradiction(memory_dir)
    qs = IV.gather_questions(memory_dir, repo_root=repo, telemetry_dir=td)
    assert len(qs) == IV.QUESTION_CAP == 3


def test_gathering_writes_nothing(repo, memory_dir):
    td = default_telemetry_dir(memory_dir)
    _abstain(td, "how do we deploy the staging environment", n=3)
    _contradiction(memory_dir)

    def _snap(root):
        out = {}
        for dp, _dn, fns in os.walk(root):
            for f in fns:
                p = os.path.join(dp, f)
                with open(p, "rb") as fh:
                    out[p] = fh.read()
        return out

    before = _snap(repo)
    IV.gather_questions(memory_dir, repo_root=repo, telemetry_dir=td)
    assert _snap(repo) == before, "the asks step itself must write NOTHING"


# --------------------------------------------------------------------------- #
# Responding: declines persist (telemetry, not corpus); later = snooze
# --------------------------------------------------------------------------- #
def test_decline_never_re_renders(repo, memory_dir):
    td = default_telemetry_dir(memory_dir)
    _abstain(td, "how do we deploy the staging environment", n=3)
    qs = IV.gather_questions(memory_dir, repo_root=repo, telemetry_dir=td)
    qid = qs[0]["qid"]
    r = IV.respond(qid, "decline", telemetry_dir=td)
    assert r["ok"] is True
    assert IV.gather_questions(memory_dir, repo_root=repo, telemetry_dir=td) == []
    # The decline lives in TELEMETRY, never the corpus.
    assert os.path.exists(os.path.join(td, IV.STATE_NAME))
    assert os.listdir(memory_dir) == [] or all(
        not f.startswith("interview") for f in os.listdir(memory_dir)
    )


def test_later_is_a_snooze_that_expires(repo, memory_dir):
    td = default_telemetry_dir(memory_dir)
    _abstain(td, "how do we deploy the staging environment", n=3)
    qs = IV.gather_questions(memory_dir, repo_root=repo, telemetry_dir=td)
    qid = qs[0]["qid"]
    assert IV.respond(qid, "later", telemetry_dir=td)["ok"] is True
    assert IV.gather_questions(memory_dir, repo_root=repo, telemetry_dir=td) == []
    # Expire the snooze by hand — the question returns (a decline would not).
    path = os.path.join(td, IV.STATE_NAME)
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    doc["snoozed"][qid] = "2020-01-01T00:00:00+00:00"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    assert len(IV.gather_questions(memory_dir, repo_root=repo, telemetry_dir=td)) == 1


def test_respond_refuses_junk(repo, memory_dir):
    td = default_telemetry_dir(memory_dir)
    assert IV.respond("abstain:deadbeef", "shrug", telemetry_dir=td)["ok"] is False
    assert IV.respond("not-a-qid", "decline", telemetry_dir=td)["ok"] is False


def test_decline_of_a_contradiction_is_scoped_to_the_pair(repo, memory_dir):
    a, b = _contradiction(memory_dir)
    td = default_telemetry_dir(memory_dir)
    qs = IV.gather_questions(memory_dir, repo_root=repo, telemetry_dir=td)
    assert IV.respond(qs[0]["qid"], "decline", telemetry_dir=td)["ok"] is True
    assert IV.gather_questions(memory_dir, repo_root=repo, telemetry_dir=td) == []
    # A DIFFERENT pair still asks (the decline was per-question, not per-kind).
    _contradiction(memory_dir, a="gamma-note", b="delta-note")
    qs2 = IV.gather_questions(memory_dir, repo_root=repo, telemetry_dir=td)
    assert len(qs2) == 1 and "gamma-note" in qs2[0]["question"]


# --------------------------------------------------------------------------- #
# The MCP tool (consolidate's Desktop rail gains the asks step)
# --------------------------------------------------------------------------- #
def _call(tool, arguments):
    from memory import mcp_server as M

    resp = M.handle_request(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": tool, "arguments": arguments}}
    )
    return resp["result"]["content"][0]["text"]


def test_tool_lists_questions_and_records_a_decline(repo, memory_dir, monkeypatch):
    monkeypatch.setenv("HIPPO_MEMORY_DIR", memory_dir)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    td = default_telemetry_dir(memory_dir)
    _abstain(td, "how do we deploy the staging environment", n=3)
    out = _call("interview", {})
    assert "how do we deploy the staging environment" in out
    assert "abstain:" in out  # the qid is the handle respond needs
    qid = next(tok for tok in out.split() if tok.startswith("abstain:")).strip("():,")
    out2 = _call("interview", {"action": "respond", "qid": qid, "outcome": "decline"})
    assert "decline" in out2
    assert "no questions" in _call("interview", {}).lower()


def test_tool_empty_norm_says_so(repo, memory_dir, monkeypatch):
    monkeypatch.setenv("HIPPO_MEMORY_DIR", memory_dir)
    assert "no questions" in _call("interview", {}).lower()


def test_tool_refuses_untrusted_corpus(repo, memory_dir, monkeypatch):
    monkeypatch.setenv("HIPPO_MEMORY_DIR", memory_dir)
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    _mem(memory_dir, "any-note")  # a real corpus, untrusted
    out = _call("interview", {})
    assert "not trusted" in out.lower() or "untrusted" in out.lower()
