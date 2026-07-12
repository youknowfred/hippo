"""Tests for the MCP setup tools (INT-9..12) — the /hippo:* setup flows as tools.

The Claude desktop app runs plugin hooks, skills, and MCP servers but has no typed
/hippo:* command surface, so setup (init / bootstrap / doctor / the SEC-1 consent step)
is re-served here as MCP tools. These tests cover each tool against real corpora, and —
per the house rule — every trust-gate test deletes HIPPO_TRUST_ALL to exercise the REAL
gate (conftest's autouse bypass + tmp HIPPO_TRUST_FILE keep it hermetic).
"""

from __future__ import annotations

import json
import os
import re

import pytest

from memory import build_index as B
from memory import mcp_server as M
from memory import trust as T
from memory.build_index import default_index_dir
from memory.provenance import resolve_dirs


def _mem(name, desc, mtype="project", body="body"):
    return f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: {mtype}\n---\n{body}\n'


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    md = str(tmp_path / ".claude" / "memory")
    os.makedirs(md)
    with open(os.path.join(md, "deploy_runbook.md"), "w") as fh:
        fh.write(_mem("deploy_runbook", "how the web service is deployed via the canary lane",
                      body="Deploy via canary. See [[rollback_plan]]."))
    with open(os.path.join(md, "rollback_plan.md"), "w") as fh:
        fh.write(_mem("rollback_plan", "how to roll back a bad web deploy"))
    B.build_index(md, default_index_dir(md))
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    return md


def _call(tool, arguments, req_id=99):
    return M.handle_request(
        {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
         "params": {"name": tool, "arguments": arguments}}
    )


def _text(resp):
    return resp["result"]["content"][0]["text"]


def _digest_from(text):
    m = re.search(r'confirm_digest="([0-9a-f]+)"', text)
    assert m, f"no consent digest offered in: {text!r}"
    return m.group(1)


# --------------------------------------------------------------------------- #
# trust_corpus (INT-9) — the SEC-1 consent flow, digest-bound
# --------------------------------------------------------------------------- #
def test_trust_corpus_review_never_trusts_then_confirm_trusts(corpus, monkeypatch):
    """The review step is read-only and shows the injectable descriptions (SEC-5); the
    confirm step trusts, stamps the SEC-6 fingerprint, and records origin='review' (SEC-7)."""
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    gate_root = T.gate_repo_root(*resolve_dirs())

    review = _text(_call("trust_corpus", {}))
    assert "UNTRUSTED" in review
    assert "how the web service is deployed" in review  # the description recall would inject
    assert "UNTRUSTED DATA" in review  # the quoted-data framing travels with the sample
    assert not T.is_trusted(gate_root)  # review alone trusted nothing

    done = _text(_call("trust_corpus", {"confirm_digest": _digest_from(review)}))
    assert "trusted" in done
    assert T.is_trusted(gate_root)
    assert T.consented_hashes(gate_root)  # SEC-6 baseline stamped
    assert (T.trust_origin(gate_root) or {}).get("origin") == "review"  # SEC-7


def test_trust_corpus_wrong_digest_refuses(corpus, monkeypatch):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    gate_root = T.gate_repo_root(*resolve_dirs())
    text = _text(_call("trust_corpus", {"confirm_digest": "deadbeef0000"}))
    assert "REFUSED" in text
    assert not T.is_trusted(gate_root)


def test_trust_corpus_digest_goes_stale_when_content_changes(corpus, monkeypatch):
    """TOCTOU guard: consent is bound to the reviewed bytes — a corpus that changed
    between review and confirm refuses rather than trusting unreviewed content."""
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    gate_root = T.gate_repo_root(*resolve_dirs())
    stale = _digest_from(_text(_call("trust_corpus", {})))
    with open(os.path.join(corpus, "deploy_runbook.md"), "a") as fh:
        fh.write("\ninjected-after-review\n")
    text = _text(_call("trust_corpus", {"confirm_digest": stale}))
    assert "REFUSED" in text
    assert not T.is_trusted(gate_root)


def test_trust_corpus_already_trusted_clean_is_noop(corpus, monkeypatch):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    md, rr = resolve_dirs()
    gate_root = T.gate_repo_root(md, rr)
    assert T.mark_trusted(gate_root, memory_dir=md, origin="init")
    text = _text(_call("trust_corpus", {}))
    assert "nothing to do" in text


def test_trust_corpus_drift_reconsent_reviews_delta_and_preserves_origin(corpus, monkeypatch):
    """SEC-6 re-consent: the review names the withheld stems and samples exactly the
    delta; confirming re-baselines WITHOUT relabelling an init-origin corpus as foreign."""
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    md, rr = resolve_dirs()
    gate_root = T.gate_repo_root(md, rr)
    assert T.mark_trusted(gate_root, memory_dir=md, origin="init")
    with open(os.path.join(corpus, "deploy_runbook.md"), "w") as fh:
        fh.write(_mem("deploy_runbook", "deploys now go through the blue-green lane"))

    review = _text(_call("trust_corpus", {}))
    assert "WITHHOLDING" in review
    assert "deploy_runbook" in review
    assert "blue-green lane" in review     # the delta's injectable description is shown
    assert "how to roll back" not in review  # the unchanged file is NOT re-sampled — delta only

    done = _text(_call("trust_corpus", {"confirm_digest": _digest_from(review)}))
    assert "trusted" in done
    assert (T.trust_origin(gate_root) or {}).get("origin") == "init"  # preserved, not "review"
    drift = T.untrusted_changes(gate_root, md)
    assert drift["baseline"] and not drift["changed"] and not drift["added"]


def test_consent_sample_stems_filter_targets_the_delta(corpus):
    """trust.corpus_consent_sample(stems=...) rows are exactly the requested stems —
    the SEC-6 drift review must show the CHANGE, not whichever files sort first."""
    rows = T.corpus_consent_sample(corpus, stems=["rollback_plan"])
    assert [r["name"] for r in rows] == ["rollback_plan"]


def test_trust_corpus_trust_all_bypass_is_legible(corpus):
    # conftest's autouse HIPPO_TRUST_ALL=1 is still set here
    assert "HIPPO_TRUST_ALL" in _text(_call("trust_corpus", {}))


def test_trust_corpus_inapplicable_without_corpus_or_git(tmp_path, monkeypatch):
    empty = tmp_path / "nothing"
    empty.mkdir()
    monkeypatch.setenv("HIPPO_MEMORY_DIR", str(empty))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))  # hermetic non-git repo_root
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    assert "inapplicable" in _text(_call("trust_corpus", {}))
