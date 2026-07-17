"""TMB-4: edge-derived update / premise-resistance eval fixtures.

A deterministic synthesizer walks the supersedes chains the corpus already carries into
``category: update`` DRAFT rows — query = a VERBATIM span of the superseded memory's
file (a literal substring; no template, no paraphrase, zero LLM/network — the
fabrication-kill adjacency), gold = the live chain tip via transitive successor
resolution. Every row lands only through SIG-6's per-item ``confirm_hard_set_row``
(no bulk path), scoring is bucketed by the corpse's live GRW-7 stamp state
(unstamped/recent: successor-must-outrank-corpse; old: presence-only — recall
display-filters old corpses), and everything stays report-only: GATE_UPDATE_* does not
exist and its promotion is a dated owner decision.
"""

from __future__ import annotations

import json
import os
import socket

import pytest

import memory.build_index as B
import memory.doctor as D
import memory.eval_recall as E


def _mem(md, name, description, body="Body.", supersedes=None, invalid_after=None):
    os.makedirs(md, exist_ok=True)
    lines = ["---", f"name: {name}", f'description: "{description}"']
    if supersedes:
        lines.append(f"supersedes: [{supersedes}]")
    if invalid_after:
        lines.append(f'invalid_after: "{invalid_after}"')
    lines += ["---", "", body, ""]
    with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


_CORPSE_BODY = (
    "The deploy retry policy waits a fixed three seconds between attempts.\n\n"
    "Retries are capped at five attempts before the deploy is marked failed."
)


def _chain_corpus(md, *, corpse_invalid_after=None):
    """tip supersedes corpse; both live."""
    _mem(md, "retry-v1", "old retry policy claim", body=_CORPSE_BODY,
         invalid_after=corpse_invalid_after)
    _mem(md, "retry-v2", "retry policy uses exponential backoff with jitter",
         body="Backoff doubles per attempt with jitter.", supersedes="retry-v1")
    B.build_index(md, B.default_index_dir(md))


# --------------------------------------------------------------------------- #
# the synthesizer — verbatim spans, chain tips, fail-closed
# --------------------------------------------------------------------------- #
def test_draft_queries_are_literal_substrings_zero_network(memory_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")

    def _no_net(*a, **k):
        raise AssertionError("the synthesizer must never touch the network")

    monkeypatch.setattr(socket, "socket", _no_net)
    _chain_corpus(memory_dir)
    dp = str(tmp_path / "drafts.yaml")
    s = E.draft_update_fixtures(memory_dir, drafts_path=dp)
    assert s["chains"] == 1 and len(s["added"]) == 2  # update + premise-resistance
    corpse_text = open(os.path.join(memory_dir, "retry-v1.md"), encoding="utf-8").read()
    _meta, rows = E._load_fixture_docs(dp)
    assert len(rows) == 2
    for row in rows:
        assert row["query"] in corpse_text  # THE pin: a literal verbatim span, no paraphrase
        assert row["superseded"] == "retry-v1"
        assert row["derived_expected"] == ["retry-v2"]
        assert row["expected"] == []  # drafts land nothing; confirm is per-item
    assert {r["kind"] for r in rows} == {"update", "premise-resistance"}


def test_synthesizer_resolves_the_transitive_chain_tip(memory_dir, monkeypatch, tmp_path):
    """v3 supersedes v2 supersedes v1 -> gold for v1's rows is v3, the LIVE tip."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _mem(memory_dir, "policy-v1", "v1 claim", body=_CORPSE_BODY)
    _mem(memory_dir, "policy-v2", "v2 claim",
         body="An intermediate claim that was itself replaced later on.", supersedes="policy-v1")
    _mem(memory_dir, "policy-v3", "v3 claim",
         body="The final claim standing today.", supersedes="policy-v2")
    B.build_index(memory_dir, B.default_index_dir(memory_dir))
    dp = str(tmp_path / "drafts.yaml")
    E.draft_update_fixtures(memory_dir, drafts_path=dp)
    _meta, rows = E._load_fixture_docs(dp)
    by_corpse = {}
    for r in rows:
        by_corpse.setdefault(r["superseded"], set()).add(r["derived_expected"][0])
    assert by_corpse["policy-v1"] == {"policy-v3"}  # tip, not the intermediate
    assert by_corpse["policy-v2"] == {"policy-v3"}


def test_synthesizer_fails_closed_never_paraphrases(memory_dir, monkeypatch, tmp_path):
    """No qualifying span -> the row is SKIPPED (named), never templated around."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _mem(memory_dir, "terse-v1", "old", body="short.")  # nothing >= the span floor
    _mem(memory_dir, "terse-v2", "new", body="A replacement claim with enough words here.",
         supersedes="terse-v1")
    B.build_index(memory_dir, B.default_index_dir(memory_dir))
    dp = str(tmp_path / "drafts.yaml")
    s = E.draft_update_fixtures(memory_dir, drafts_path=dp)
    assert s["added"] == []
    assert any("no qualifying verbatim span" in x for x in s["skipped"])
    assert not os.path.exists(dp)  # nothing to write -> nothing written


def test_rows_are_tagged_update_never_temporal(memory_dir, monkeypatch, tmp_path):
    """The confirm leg: rows land category:update; 'temporal' stays zero rows."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _chain_corpus(memory_dir)
    fp = os.path.join(memory_dir, ".audit-fixtures", "recall_hard_set.yaml")
    dp = str(tmp_path / "drafts.yaml")
    E.draft_update_fixtures(memory_dir, drafts_path=dp)
    _meta, rows = E._load_fixture_docs(dp)
    r = E.confirm_hard_set_row(
        rows[0]["query"], rows[0]["derived_expected"], memory_dir=memory_dir,
        fixture_path=fp, drafts_path=dp, category="update", superseded=rows[0]["superseded"],
    )
    assert r["ok"] is True and r["category"] == "update" and r["superseded"] == "retry-v1"
    loaded = E.load_hard_set(fp)
    assert [row["category"] for row in loaded] == ["update"]
    assert loaded[0]["superseded"] == "retry-v1"  # rides through the loader
    assert not any(row["category"] == "temporal" for row in loaded)
    # a vanished corpse refuses (fail closed — re-draft, don't confirm stale)
    r = E.confirm_hard_set_row(
        "another span of text entirely", ["retry-v2"], memory_dir=memory_dir,
        fixture_path=fp, category="update", superseded="never-existed",
    )
    assert r["ok"] is False and "not live in this corpus" in r["reason"]


def test_zero_live_fixtures_until_each_row_confirmed_no_bulk(memory_dir, monkeypatch, tmp_path):
    """The AC verbatim: an N-row drafts file produces ZERO live fixtures until each row
    is confirmed per item; no bulk-confirm path exists."""
    import inspect

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _chain_corpus(memory_dir)
    fp = os.path.join(memory_dir, ".audit-fixtures", "recall_hard_set.yaml")
    dp = str(tmp_path / "drafts.yaml")
    E.draft_update_fixtures(memory_dir, drafts_path=dp)
    _meta, rows = E._load_fixture_docs(dp)
    assert len(rows) == 2 and not os.path.exists(fp)  # N drafts, zero live fixtures
    E.confirm_hard_set_row(rows[0]["query"], rows[0]["derived_expected"],
                           memory_dir=memory_dir, fixture_path=fp, drafts_path=dp,
                           category="update", superseded=rows[0]["superseded"])
    assert len(E.load_hard_set(fp)) == 1  # exactly the one confirmed row
    _meta, left = E._load_fixture_docs(dp)
    assert len(left) == 1  # its draft drained; the other awaits its own judgment
    # no bulk path: confirm takes ONE query and no list-of-rows/batch parameter
    params = set(inspect.signature(E.confirm_hard_set_row).parameters)
    assert "rows" not in params and "bulk" not in params and "batch" not in params


# --------------------------------------------------------------------------- #
# the two scoring paths (stamp-state buckets)
# --------------------------------------------------------------------------- #
def _scored_corpus(md, *, corpse_invalid_after=None):
    _chain_corpus(md, corpse_invalid_after=corpse_invalid_after)
    idx_dir = B.default_index_dir(md)
    idx = B.load_index(idx_dir)
    span = "The deploy retry policy waits a fixed three seconds between attempts."
    rows = [{"query": span, "expected": ["retry-v2"], "category": "update", "superseded": "retry-v1"}]
    return idx, idx_dir, rows


def test_scoring_unstamped_requires_successor_to_outrank_corpse(memory_dir, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    idx, idx_dir, rows = _scored_corpus(memory_dir)  # unstamped corpse
    m = E.update_category_metrics(idx, rows, memory_dir, k=10, index_dir=idx_dir)
    assert m["n"] == 1 and m["outrank"]["n"] == 1 and m["presence"]["n"] == 0
    # the query is the CORPSE's own words and no supersedes penalty is stamped into the
    # index edge yet… but links.json carries the edge, so the corpse is halved: still,
    # BM25 on its own text is strong — this asserts the PATH, not a fixed verdict:
    assert m["outrank"]["pass"] + m["outrank_failures"] == 1


def test_scoring_old_stamped_is_presence_only(memory_dir, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    idx, idx_dir, rows = _scored_corpus(
        memory_dir, corpse_invalid_after="2020-01-01T00:00:00+00:00"
    )
    m = E.update_category_metrics(idx, rows, memory_dir, k=10, index_dir=idx_dir)
    assert m["outrank"]["n"] == 0 and m["presence"]["n"] == 1
    # old-stamped corpse is display-filtered; the tip ranking at all is the whole test
    assert m["presence"]["pass"] == 1 and m["outrank_failures"] == 0


def test_report_only_wiring_and_no_gate_update_constant(memory_dir, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    _chain_corpus(memory_dir)
    fp = os.path.join(memory_dir, ".audit-fixtures", "recall_hard_set.yaml")
    idx_dir = B.default_index_dir(memory_dir)
    # no update rows -> no report key (absence-emits-nothing)
    rep = E.evaluate(memory_dir, idx_dir, None)
    assert "update_knowledge" not in rep
    span = "The deploy retry policy waits a fixed three seconds between attempts."
    r = E.confirm_hard_set_row(span, ["retry-v2"], memory_dir=memory_dir,
                               fixture_path=fp, category="update", superseded="retry-v1")
    assert r["ok"] is True
    rep = E.evaluate(memory_dir, idx_dir, fp)
    u = rep["update_knowledge"]
    assert u["n"] == 1 and u["outrank"]["n"] == 1
    assert "update" in rep["by_category"]  # the presence line rides RET-8 as usual
    # report-only: no gate consumes it, and no GATE_UPDATE_* constant exists to auto-flip
    assert not any("update" in g for g in rep["gates"])
    assert not any(n.startswith("GATE_UPDATE") for n in dir(E)), (
        "GATE_UPDATE_* must not exist — promotion is a dated owner decision (TMB-4)"
    )


def test_doctor_update_eval_reads_the_persisted_run(memory_dir, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    td = str(tmp_path / "tele")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    ctx = D.DoctorContext(memory_dir, repo)
    r = D.check_update_eval(ctx)
    assert r["status"] == "ok" and "no persisted eval runs" in r["message"]
    # persist a run whose report carries update_knowledge with failures
    report = {"update_knowledge": {"n": 2, "outrank": {"n": 2, "pass": 1},
                                   "presence": {"n": 0, "pass": 0}, "outrank_failures": 1}}
    assert E.append_run_ledger(report, memory_dir, telemetry_dir=td) is not None
    r = D.check_update_eval(ctx)
    assert r["status"] == "warn"
    assert "1 outrank failure(s) across 2 update row(s)" in r["message"]
    assert "\n" not in r["message"]
    # and a clean latest run reads ok
    report["update_knowledge"]["outrank_failures"] = 0
    E.append_run_ledger(report, memory_dir, telemetry_dir=td)
    r = D.check_update_eval(ctx)
    assert r["status"] == "ok" and "0 outrank failures" in r["message"]


def test_doctor_update_eval_registered_before_trailing_env_check():
    labels = [label for label, _fn in D.CHECKS]
    assert "update_eval" in labels
    assert labels[-1] == "stale_memobot_env"
