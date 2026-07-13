"""DRM-C: dream's opt-in LLM contradiction discovery (memory/dream.py + resolve_view merge).

Pinned contract, per the feature's design:
  - FLAG OFF (the default): a dream pass is byte-identical to today — no LLM import fires,
    no ``contradicts`` candidates, no verdict ledger, no stats block.
  - FLAG ON: the SAME high-cofire pairs the pass already computed are judged by ONE bounded
    LLM call each; ``conflict: true`` verdicts become PROPOSE-ONLY ``kind: "contradicts"``
    candidates that land in the derived verdict ledger and feed the /hippo:resolve inbox —
    NEVER the corpus, NEVER the auto-apply path (there is no Tier-A for contradictions).
  - ANY LLM failure: the pair is simply not proposed — no crash, no partial write, retried
    on a future pass. A judged pair (either verdict) is never re-billed.

Hermetic: ``memory.llm_client.complete`` is monkeypatched everywhere; dense is disabled so
co-firing is deterministic BM25.
"""

from __future__ import annotations

import json
import os

import pytest

import memory.dream as dream
import memory.resolve_view as RV


def _write_memory(md, name, description, body="body\n", type_="project", extra_fm=""):
    os.makedirs(md, exist_ok=True)
    path = os.path.join(md, f"{name}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            f"---\nname: {name}\ndescription: \"{description}\"\n"
            f"metadata:\n  type: {type_}\n{extra_fm}---\n\n{body}"
        )
    return path


def _seed_sessions(td, n=5):
    """Raw recall events across n distinct sessions — clears the ≥5 soak bar."""
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "recall_events.jsonl"), "w", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({"session_id": f"s{i}", "names": [], "backend": "bm25"}) + "\n")


def _snapshot_md(md):
    out = {}
    for name in sorted(os.listdir(md)):
        p = os.path.join(md, name)
        if os.path.isfile(p):
            out[name] = open(p, "rb").read()
    return out


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """BM25-only ranking, no ambient flag/keys, an inclusive cofire bar (the bar's own
    mechanics get dedicated tests), and a bombed transport backstop."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.delenv("HIPPO_DREAM_CONTRADICTIONS", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HIPPO_LLM_API_KEY", raising=False)
    monkeypatch.setenv("DREAM_CONTRA_MIN_COFIRE", "0.05")

    def _bomb(*a, **kw):  # pragma: no cover - only fires on a contract breach
        raise AssertionError("DRM-C attempted a real network call in a test")

    monkeypatch.setattr("urllib.request.urlopen", _bomb)


@pytest.fixture
def dirs(tmp_path):
    md = str(tmp_path / "mem")
    td = str(tmp_path / "tele")
    idx = str(tmp_path / "idx")
    os.makedirs(md)
    return md, td, idx


def _conflicting_corpus(md, declare_contradicts=False, declare_supersedes=False):
    """Two memories about the SAME subject making OPPOSITE current-state claims (they
    co-fire hard under BM25 — heavy shared vocabulary — but no organic kind matches:
    no mention, no slug prefix, no 2-hop path), plus one distractor."""
    extra = ""
    if declare_contradicts:
        extra = "contradicts: [gateway-dropped]\n"
    if declare_supersedes:
        extra = "supersedes: [gateway-dropped]\n"
    _write_memory(
        md,
        "gateway-current",
        "all service calls go through the quasar gateway proxy layer",
        body="The gateway proxy layer is mandatory for every service call today.\n",
        extra_fm=extra,
    )
    _write_memory(
        md,
        "gateway-dropped",
        "we removed the quasar gateway proxy layer for service calls",
        body="Direct service calls only now; the proxy layer is gone.\n",
    )
    _write_memory(
        md,
        "zulu-almanac",
        "gardening almanac for heirloom tomato rotation beds",
        body="Completely unrelated.\n",
    )


def _mock_llm(monkeypatch, responses=None, default='{"conflict": true, "reason": "opposite claims about the gateway"}'):
    """Install a complete() double; returns the call log. ``responses`` pops in order."""
    calls = []
    queue = list(responses) if responses is not None else None

    def fake(prompt, *, timeout_s, **kw):
        calls.append({"prompt": prompt, "timeout_s": timeout_s})
        if queue is not None:
            return queue.pop(0) if queue else None
        return default

    monkeypatch.setattr("memory.llm_client.complete", fake)
    return calls


# --------------------------------------------------------------------------- #
# Flag OFF — today's pass, byte-identical, zero LLM involvement
# --------------------------------------------------------------------------- #
def test_flag_off_no_llm_no_candidates_no_ledger(dirs, monkeypatch):
    md, td, idx = dirs
    _conflicting_corpus(md)
    _seed_sessions(td)

    def _boom(*a, **kw):  # pragma: no cover
        raise AssertionError("llm_client.complete ran with the flag off")

    monkeypatch.setattr("memory.llm_client.complete", _boom)
    code, text = dream.run_report_pass(md, idx, td)
    assert code == 0
    assert "contradiction discovery" not in text
    result = dream.discover(md, idx, td)
    assert result["status"] == "ok"
    assert all(c["kind"] != "contradicts" for c in result["candidates"])
    assert "contradictions" not in result["stats"]
    assert not os.path.exists(dream.contradictions_ledger_path(td))


# --------------------------------------------------------------------------- #
# Flag ON — propose-only discovery over the pass's own high-cofire pairs
# --------------------------------------------------------------------------- #
def test_flag_on_proposes_pair_and_never_writes_corpus(dirs, monkeypatch):
    md, td, idx = dirs
    _conflicting_corpus(md)
    _seed_sessions(td)
    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "1")
    calls = _mock_llm(monkeypatch)

    before = _snapshot_md(md)
    code, text = dream.run_report_pass(md, idx, td)

    assert code == 0
    assert _snapshot_md(md) == before, "DRM-C wrote the corpus — propose-only breached"
    assert calls, "the co-firing pair should have been judged"
    # Both memories rode the verdict prompt verbatim (bounded).
    assert "gateway-current" in calls[0]["prompt"] and "gateway-dropped" in calls[0]["prompt"]
    # The report says what was judged and where proposals went.
    assert "contradiction discovery" in text and "/hippo:resolve" in text
    # The derived verdict ledger carries the proposed row.
    rows = [
        json.loads(l)
        for l in open(dream.contradictions_ledger_path(td), encoding="utf-8")
        if l.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["a"] == "gateway-current" and rows[0]["b"] == "gateway-dropped"
    assert rows[0]["conflict"] is True and rows[0]["state"] == "proposed"
    assert rows[0]["reason"] == "opposite claims about the gateway"
    # The distractor never co-fired with either side, so exactly ONE pair was judged.
    assert len(calls) == 1


def test_flag_on_candidates_ride_the_pass_ledger_as_tier_c(dirs, monkeypatch):
    md, td, idx = dirs
    _conflicting_corpus(md)
    _seed_sessions(td)
    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "1")
    _mock_llm(monkeypatch)
    result = dream.discover(md, idx, td)
    contras = [c for c in result["candidates"] if c["kind"] == "contradicts"]
    assert len(contras) == 1
    c = contras[0]
    assert {c["source"], c["target"]} == {"gateway-current", "gateway-dropped"}
    assert c["signal"] == "llm-contradiction-verdict"
    assert result["stats"]["contradictions"]["proposed"] == 1
    # Tier-C forever: never apply-eligible, even with the bar on the floor.
    assert dream.apply_eligible(c, theta=0.0) is False


def test_apply_pass_routes_contradicts_and_applies_nothing(dirs, monkeypatch):
    md, td, idx = dirs
    _conflicting_corpus(md)
    _seed_sessions(td)
    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "1")
    _mock_llm(monkeypatch)

    before = _snapshot_md(md)
    code, text = dream.run_apply_pass(md, idx, td)

    assert code == 0
    assert _snapshot_md(md) == before, "apply pass wrote the corpus for a contradicts candidate"
    assert "routed to /hippo:resolve — never auto" in text
    assert "contradiction discovery" in text
    # No Tier-A edge existed, so the committed apply ledger was never even created.
    assert not os.path.exists(dream.apply_ledger_path(md))


# --------------------------------------------------------------------------- #
# The resolve inbox — one more source, the identical verdict flow
# --------------------------------------------------------------------------- #
def test_proposal_feeds_resolve_inbox_and_dismiss_clears_it(dirs, tmp_path, monkeypatch):
    md, td, idx = dirs
    _conflicting_corpus(md)
    _seed_sessions(td)
    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "1")
    _mock_llm(monkeypatch)
    dream.run_report_pass(md, idx, td)

    repo_root = str(tmp_path)  # keys the per-clone dismiss ledger
    inbox = RV.unresolved_contradictions(md, index_dir=idx, repo_root=repo_root, telemetry_dir=td)
    assert len(inbox) == 1
    item = inbox[0]
    assert item["pair"] == ["gateway-current", "gateway-dropped"]
    assert item["proposed"] is True and item["declared_by"] == []
    assert item["reason"] == "opposite claims about the gateway"

    listing = RV.describe(md, index_dir=idx, repo_root=repo_root, telemetry_dir=td)
    assert "PROPOSED by dream --contradictions" in listing
    assert "model's rationale: opposite claims about the gateway" in listing
    assert "--dismiss" in listing  # the per-item verdict guidance rides the listing

    # The IDENTICAL dismiss verdict (mark-not-conflicting) drains a proposal too.
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    res = RV.mark_not_conflicting("gateway-current", "gateway-dropped", repo_root)
    assert res["recorded"] is True
    assert (
        RV.unresolved_contradictions(md, index_dir=idx, repo_root=repo_root, telemetry_dir=td)
        == []
    )


def test_supersedes_verdict_clears_proposal_without_any_ledger(dirs, tmp_path, monkeypatch):
    """keep-A-supersede-B — the corpus verdict's own supersedes edge settles the pair."""
    md, td, idx = dirs
    _conflicting_corpus(md)
    _seed_sessions(td)
    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "1")
    _mock_llm(monkeypatch)
    dream.run_report_pass(md, idx, td)
    repo_root = str(tmp_path)
    assert RV.proposed_contradictions(md, index_dir=idx, repo_root=repo_root, telemetry_dir=td)

    # The human renders keep-current-supersede-dropped (an ordinary corpus edit).
    _conflicting_corpus(md, declare_supersedes=True)
    assert (
        RV.proposed_contradictions(md, index_dir=idx, repo_root=repo_root, telemetry_dir=td)
        == []
    )


def test_declaring_the_edge_turns_proposal_into_declared_item(dirs, tmp_path, monkeypatch):
    md, td, idx = dirs
    _conflicting_corpus(md)
    _seed_sessions(td)
    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "1")
    _mock_llm(monkeypatch)
    dream.run_report_pass(md, idx, td)
    repo_root = str(tmp_path)

    _conflicting_corpus(md, declare_contradicts=True)  # the human ratifies the edge
    inbox = RV.unresolved_contradictions(md, index_dir=idx, repo_root=repo_root, telemetry_dir=td)
    assert len(inbox) == 1
    assert inbox[0]["declared_by"] == ["gateway-current"]
    assert not inbox[0].get("proposed"), "a declared pair must not double-list as proposed"


def test_retiring_a_side_clears_proposal(dirs, tmp_path, monkeypatch):
    md, td, idx = dirs
    _conflicting_corpus(md)
    _seed_sessions(td)
    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "1")
    _mock_llm(monkeypatch)
    dream.run_report_pass(md, idx, td)
    os.remove(os.path.join(md, "gateway-dropped.md"))  # merge/retire verdict
    assert (
        RV.proposed_contradictions(md, index_dir=idx, repo_root=str(tmp_path), telemetry_dir=td)
        == []
    )


# --------------------------------------------------------------------------- #
# Skips, fail-open, and bounds
# --------------------------------------------------------------------------- #
def test_already_declared_pair_burns_no_llm_call(dirs, monkeypatch):
    md, td, idx = dirs
    _conflicting_corpus(md, declare_contradicts=True)
    _seed_sessions(td)
    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "1")
    calls = _mock_llm(monkeypatch)
    result = dream.discover(md, idx, td)
    assert calls == [], "a pair the inbox already shows must not be re-judged"
    assert result["stats"]["contradictions"]["skipped_declared"] >= 1
    assert result["stats"]["contradictions"]["proposed"] == 0


def test_judged_pair_is_never_rebilled_either_verdict(dirs, monkeypatch):
    md, td, idx = dirs
    _conflicting_corpus(md)
    _seed_sessions(td)
    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "1")
    calls = _mock_llm(
        monkeypatch, responses=['{"conflict": false, "reason": "same fact, different words"}']
    )
    r1 = dream.discover(md, idx, td)
    assert r1["stats"]["contradictions"]["judged"] == 1
    assert r1["stats"]["contradictions"]["proposed"] == 0
    r2 = dream.discover(md, idx, td)
    assert len(calls) == 1, "a recorded verdict (even no-conflict) must not be re-billed"
    assert r2["stats"]["contradictions"]["skipped_prior_verdict"] == 1
    # A no-conflict verdict never reaches the inbox.
    assert RV.proposed_contradictions(md, index_dir=idx, telemetry_dir=td) == []


def test_llm_failure_fails_open_not_proposed_retried_next_pass(dirs, monkeypatch):
    md, td, idx = dirs
    _conflicting_corpus(md)
    _seed_sessions(td)
    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "1")
    calls = _mock_llm(monkeypatch, responses=[None, "utter junk, no json"])

    before = _snapshot_md(md)
    r1 = dream.discover(md, idx, td)
    assert r1["status"] == "ok", "an LLM failure must never break the organic pass"
    assert _snapshot_md(md) == before
    assert r1["stats"]["contradictions"]["llm_failures"] == 1
    assert r1["stats"]["contradictions"]["proposed"] == 0
    assert all(c["kind"] != "contradicts" for c in r1["candidates"])
    # NOT recorded → no ledger row, no partial write…
    assert not os.path.exists(dream.contradictions_ledger_path(td))
    # …and the next pass retries the pair (call 2: malformed → same fail-open).
    r2 = dream.discover(md, idx, td)
    assert len(calls) == 2
    assert r2["stats"]["contradictions"]["llm_failures"] == 1
    assert not os.path.exists(dream.contradictions_ledger_path(td))


def test_attempt_cap_bounds_a_dead_endpoint(dirs, monkeypatch):
    """Five same-vocabulary memories co-fire into many pairs; a dead endpoint must cost at
    most the CAP in attempts (the cap counts CALLS, not successes)."""
    md, td, idx = dirs
    for stem in ("alpha-fact", "bravo-fact", "carol-fact", "delta-fact", "echo-fact"):
        _write_memory(
            md, stem, f"quasar retrofit calibration includes the {stem.split('-')[0]} spec",
            body="Shared-vocabulary corpus for pair volume.\n",
        )
    _seed_sessions(td)
    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "1")
    monkeypatch.setenv("DREAM_CONTRA_MAX_PAIRS", "3")
    calls = _mock_llm(monkeypatch, responses=[None] * 50)
    result = dream.discover(md, idx, td)
    assert result["stats"]["contradictions"]["attempts"] == 3
    assert len(calls) == 3


def test_cofire_bar_gates_the_pool(dirs, monkeypatch):
    md, td, idx = dirs
    _conflicting_corpus(md)
    _seed_sessions(td)
    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "1")
    monkeypatch.setenv("DREAM_CONTRA_MIN_COFIRE", "1.01")  # nothing can clear it
    calls = _mock_llm(monkeypatch)
    result = dream.discover(md, idx, td)
    assert calls == []
    assert result["stats"]["contradictions"]["attempts"] == 0
    assert not os.path.exists(dream.contradictions_ledger_path(td))


def test_knob_clamps_and_flag_convention(monkeypatch):
    monkeypatch.setenv("DREAM_CONTRA_MAX_PAIRS", "99")
    assert dream.contra_max_pairs() == 12  # hard max is not overridable
    monkeypatch.setenv("DREAM_CONTRA_MAX_PAIRS", "-4")
    assert dream.contra_max_pairs() == 0
    monkeypatch.delenv("DREAM_CONTRA_MIN_COFIRE", raising=False)
    assert dream.contra_min_cofire() == dream.cofire_theta()  # θ is the default bar
    for junk in ("", "0", "yes", "TRUE "):
        monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", junk)
        assert dream.contradictions_enabled() is (junk.strip() in ("1", "true", "True"))


def test_verdict_ledger_reader_is_last_line_wins_and_junk_tolerant(dirs):
    md, td, idx = dirs
    os.makedirs(dream.dream_dir(td), exist_ok=True)
    with open(dream.contradictions_ledger_path(td), "w", encoding="utf-8") as fh:
        fh.write("not json\n")
        fh.write(json.dumps({"a": "x", "b": "y", "conflict": True, "reason": "v1"}) + "\n")
        fh.write(json.dumps({"nope": 1}) + "\n")
        fh.write(json.dumps({"a": "y", "b": "x", "conflict": False, "reason": "v2"}) + "\n")
    verdicts = dream.read_contradiction_verdicts(td)
    assert list(verdicts) == [("x", "y")]  # canonical, order-free
    assert verdicts[("x", "y")]["conflict"] is False  # the later verdict superseded
    assert verdicts[("x", "y")]["reason"] == "v2"


def test_cli_contradictions_flag_is_the_env_flag(dirs, monkeypatch, capsys):
    md, td, idx = dirs
    _conflicting_corpus(md)
    _seed_sessions(td)
    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "")  # off — the CLI flag must enable
    _mock_llm(monkeypatch)
    rc = dream.main(
        ["--dry-run", "--contradictions", "--memory-dir", md, "--index-dir", idx,
         "--telemetry-dir", td]
    )
    assert rc == 0
    assert "contradiction discovery" in capsys.readouterr().out
    assert os.path.exists(dream.contradictions_ledger_path(td))
