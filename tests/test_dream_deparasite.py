"""Tests for DRM-4 — the de-parasiting counterweight (memory/deparasite.py).

Pinned acceptance criteria (ROADMAP.dream.yaml DRM-4):
  - the pass reports per-memory out-degree, flags nodes over DREAM_MAX_OUT_DEGREE, and
    proposes which dream edges to retract (auto-reversible) vs which human structures to
    demote (gated);
  - protected hubs (floor / co-recalled / cited) are NEVER proposed for depression;
  - dedup-merge proposals use set_invalid_after + a superseded_by-style typed edge
    (non-lossy, reversible); no body is deleted; contradictions route to /hippo:resolve,
    never auto-resolved.

Plus the inv4 gradient: the pass itself is report/propose (zero memory writes); only
/dream's OWN un-aged edges may auto-retract (``--retract``), through the one byte-exact
undo path; a retracted pair never auto-re-applies (the ping-pong guard).

Hermetic: tmp corpus/telemetry/index; HIPPO_DISABLE_DENSE for deterministic BM25 where a
real apply pass runs; dream edges otherwise hand-stamped in exactly the DRM-2 on-disk
shape (block line + ledger row + undo record).
"""

from __future__ import annotations

import json
import os

import pytest

import memory.deparasite as dp
import memory.dream as dream

from .test_dream import _seed_sessions, _snapshot_md, _write_memory


@pytest.fixture(autouse=True)
def _bm25_only(monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    md = str(tmp_path / "mem")
    td = str(tmp_path / "tele")
    idx = str(tmp_path / "idx")
    os.makedirs(md)
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    return md, td, idx


def _seed_corecall(td, a, b, sessions=3):
    """Episode-buffer lines where ``a`` and ``b`` co-surface across N distinct sessions
    (the GRW-2 Hebbian tally's evidence shape)."""
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "episode_buffer.jsonl"), "a", encoding="utf-8") as fh:
        for i in range(sessions):
            fh.write(
                json.dumps(
                    {"ts": 100.0 + i, "session_id": f"cr{i}", "recalled_names": [a, b]}
                )
                + "\n"
            )


def _stamp_dream_bridge(md, source, target, edge_id, applied_at, cofire=0.9):
    """A dream-applied bridge exactly as DRM-2 leaves it: stamped [[wikilink]] line in a
    dream:links block appended to the SOURCE body + a complete ledger row whose undo
    record matches the on-disk bytes (so retraction's byte-exact undo works)."""
    line = f"[[{target}]] <!-- dream: bridge · pass=pX · edge={edge_id} · cofire={cofire:.2f} -->"
    path = os.path.join(md, f"{source}.md")
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    lead = "" if text.endswith("\n") else "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{lead}<!-- dream:links -->\n{line}\n<!-- /dream:links -->\n")
    with open(dream.apply_ledger_path(md), "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "edge_id": edge_id,
                    "pass": "pX",
                    "kind": "bridge",
                    "source": source,
                    "target": target,
                    "cofire": cofire,
                    "state": "active",
                    "applied_at_distinct_count": applied_at,
                    "undo": {
                        "file": f"{source}.md",
                        "block": {"inserted": line + "\n", "wrapper": True, "lead": lead},
                    },
                }
            )
            + "\n"
        )
    return line


def _hub_corpus(md, hub="hub-node", human_links=("t-one", "t-two")):
    """A hub with hand-authored out-links to each of ``human_links`` + those targets +
    one off-topic distractor. Vocabularies are disjoint so nothing co-fires or dedups."""
    body = "Hub notes.\n\n" + "".join(f"See [[{t}]].\n" for t in human_links)
    _write_memory(md, hub, "central hub aggregating operational pointers", body=body)
    descs = [
        "xenon thruster gimbal vibration dampener specs",
        "greenhouse irrigation drip schedule almanac",
        "marathon interval training cadence plan",
        "sourdough hydration baking ratio ledger",
    ]
    for i, t in enumerate(human_links):
        _write_memory(md, t, descs[i % len(descs)], body="Leaf.\n")
    _write_memory(md, "distractor", "quasar navigation ephemeris tables", body="x\n")


# --------------------------------------------------------------------------- #
# The report: out-degree table, flagging, zero writes, legible refusals
# --------------------------------------------------------------------------- #
def test_report_flags_over_cap_and_writes_nothing(dirs, monkeypatch):
    md, td, idx = dirs
    monkeypatch.setenv("DREAM_MAX_OUT_DEGREE", "1")
    _hub_corpus(md)  # hub out-degree 2 > 1
    _seed_sessions(td, 5)
    before = _snapshot_md(md)

    report = dp.deparasite_report(md, idx, td)
    assert report["status"] == "ok"
    # Per-memory out-degree is REPORTED (the acceptance's first clause)…
    by_stem = {r[0]: r[1] for r in report["degrees"]}
    assert by_stem["hub-node"] == 2 and by_stem["t-one"] == 0
    # …and only the over-cap node is flagged.
    assert [r["stem"] for r in report["flagged"]] == ["hub-node"]
    assert report["flagged"][0]["out_degree"] == 2

    code, text = dp.run_deparasite_pass(md, idx, td)
    assert code == 0
    assert "REPORT/PROPOSE" in text and "zero memory writes" in text
    assert _snapshot_md(md) == before, "the report/propose pass must write no memory byte"
    # The pass report landed under the DERIVED dream dir (inv1), never the corpus.
    files = [f for f in os.listdir(dream.dream_dir(td)) if f.startswith("deparasite-")]
    assert len(files) == 1


def test_under_cap_corpus_flags_nothing_and_says_so(dirs):
    md, td, idx = dirs
    _hub_corpus(md)  # default cap 8 — nothing is over
    _seed_sessions(td, 5)
    code, text = dp.run_deparasite_pass(md, idx, td)
    assert code == 0
    assert "no hub over the cap" in text and "norm" in text


def test_below_soak_bar_proposes_nothing_and_says_so(dirs, monkeypatch):
    md, td, idx = dirs
    monkeypatch.setenv("DREAM_MAX_OUT_DEGREE", "1")
    _hub_corpus(md)
    _seed_sessions(td, 4)  # one short
    report = dp.deparasite_report(md, idx, td)
    assert report["status"] == "below-soak"
    assert report["flagged"] == [] and report["dedup"] == []
    code, text = dp.run_deparasite_pass(md, idx, td)
    assert code == 0 and "below the curation-soak bar" in text


# --------------------------------------------------------------------------- #
# Protected hubs are NEVER proposed for depression (the acceptance assertion)
# --------------------------------------------------------------------------- #
def test_floor_hub_protected_no_demotion_proposed(dirs, monkeypatch):
    md, td, idx = dirs
    monkeypatch.setenv("DREAM_MAX_OUT_DEGREE", "1")
    _hub_corpus(md)
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# Floor\n\n## User\n- [Hub](hub-node.md) — pinned\n")
    _seed_sessions(td, 5)
    report = dp.deparasite_report(md, idx, td)
    row = next(r for r in report["flagged"] if r["stem"] == "hub-node")
    assert row["protected"] is True and "floor" in row["protected_by"]
    assert row["demote_gated"] == [], "a protected hub is never proposed for depression"
    assert "PROTECTED" in dp.render_report(report)


def test_corecalled_hub_protected(dirs, monkeypatch):
    md, td, idx = dirs
    monkeypatch.setenv("DREAM_MAX_OUT_DEGREE", "1")
    _hub_corpus(md)
    _seed_sessions(td, 5)
    _seed_corecall(td, "hub-node", "t-one", sessions=3)
    report = dp.deparasite_report(md, idx, td)
    row = next(r for r in report["flagged"] if r["stem"] == "hub-node")
    assert row["protected"] is True and "co-recalled" in row["protected_by"]
    assert row["demote_gated"] == []


def test_cited_hub_protected_but_retract_lane_still_open(dirs, monkeypatch):
    md, td, idx = dirs
    monkeypatch.setenv("DREAM_MAX_OUT_DEGREE", "1")
    monkeypatch.setenv("DREAM_AGE_SESSIONS", "5")
    _hub_corpus(md)
    # Another memory CITES the hub (inbound human wikilink) → protected.
    _write_memory(md, "citer", "field notes referencing the hub",
                  body="Context lives in [[hub-node]].\n")
    # And the hub carries one un-aged dream edge of its own (out-degree 3 now).
    _stamp_dream_bridge(md, "hub-node", "distractor", "pX-e1", applied_at=5)
    _seed_sessions(td, 5)

    report = dp.deparasite_report(md, idx, td)
    row = next(r for r in report["flagged"] if r["stem"] == "hub-node")
    assert row["protected"] is True and "cited" in row["protected_by"]
    # Depression of human structures: never. Retraction of /dream's OWN un-aged edge:
    # still available — it restores the pre-dream baseline, it depresses nothing human.
    assert row["demote_gated"] == []
    assert [e["edge_id"] for e in row["retract"]] == ["pX-e1"]


def test_dream_inbound_edge_confers_no_cited_protection(dirs, monkeypatch):
    md, td, idx = dirs
    monkeypatch.setenv("DREAM_MAX_OUT_DEGREE", "1")
    monkeypatch.setenv("DREAM_AGE_SESSIONS", "5")
    _hub_corpus(md)
    # The ONLY inbound edge to the hub is dream-created (stamped in citer2's body +
    # ledger-active). The counterweight must not count it as "cited" — otherwise the
    # pass it counterweighs could shield its own accretion.
    _write_memory(md, "citer2", "unrelated survey of beacon acoustics", body="Own notes.\n")
    _stamp_dream_bridge(md, "citer2", "hub-node", "pX-e9", applied_at=0)  # aged, still no shield
    _seed_sessions(td, 5)

    report = dp.deparasite_report(md, idx, td)
    row = next(r for r in report["flagged"] if r["stem"] == "hub-node")
    assert row["protected"] is False
    assert row["demote_gated"], "unprotected over-cap hub gets gated demote proposals"


# --------------------------------------------------------------------------- #
# The reversibility gradient: retract lane (Tier A) vs demote lane (gated)
# --------------------------------------------------------------------------- #
def test_lanes_split_unaged_retract_vs_aged_and_human_gated(dirs, monkeypatch):
    md, td, idx = dirs
    monkeypatch.setenv("DREAM_MAX_OUT_DEGREE", "2")
    monkeypatch.setenv("DREAM_AGE_SESSIONS", "5")
    _hub_corpus(md)  # 2 human out-links
    _stamp_dream_bridge(md, "hub-node", "distractor", "pX-e1", applied_at=5)  # un-aged
    _write_memory(md, "t-aged", "orbital beacon ledger appendix", body="x\n")
    _stamp_dream_bridge(md, "hub-node", "t-aged", "pX-e2", applied_at=0)      # aged in
    _seed_sessions(td, 5)
    before = _snapshot_md(md)

    report = dp.deparasite_report(md, idx, td)
    row = next(r for r in report["flagged"] if r["stem"] == "hub-node")
    assert row["out_degree"] == 4 and row["protected"] is False

    # Tier A: only the UN-AGED dream edge is retractable.
    assert [e["edge_id"] for e in row["retract"]] == ["pX-e1"]
    assert row["retract"][0]["sessions_to_age"] == 5

    # Gated: the aged-in dream edge (owner-action command) + the human out-links.
    gated_by_class = {}
    for d in row["demote_gated"]:
        gated_by_class.setdefault(d["class"], []).append(d)
    assert [d["edge_id"] for d in gated_by_class["aged-dream-edge"]] == ["pX-e2"]
    assert "dream --undo pX-e2" in gated_by_class["aged-dream-edge"][0]["cmd"]
    assert {d["target"] for d in gated_by_class["human-out-link"]} == {"t-one", "t-two"}

    # The report proposed all of that and WROTE NONE of it.
    assert _snapshot_md(md) == before


def test_retract_executes_only_unaged_dream_edges(dirs, monkeypatch):
    md, td, idx = dirs
    monkeypatch.setenv("DREAM_MAX_OUT_DEGREE", "2")
    monkeypatch.setenv("DREAM_AGE_SESSIONS", "5")
    _hub_corpus(md)
    pre_stamp = _snapshot_md(md)
    line1 = _stamp_dream_bridge(md, "hub-node", "distractor", "pX-e1", applied_at=5)
    _write_memory(md, "t-aged", "orbital beacon ledger appendix", body="x\n")
    line2 = _stamp_dream_bridge(md, "hub-node", "t-aged", "pX-e2", applied_at=0)
    _seed_sessions(td, 5)

    code, text = dp.run_deparasite_pass(md, idx, td, retract=True)
    assert code == 0
    assert "reverted 1 edge(s)" in text

    hub_text = open(os.path.join(md, "hub-node.md"), encoding="utf-8").read()
    assert line1 not in hub_text, "the un-aged edge's stamp must be retracted byte-exactly"
    assert line2 in hub_text, "the aged-in edge is GATED — --retract must not touch it"
    # Human links untouched.
    assert "[[t-one]]" in hub_text and "[[t-two]]" in hub_text
    # Ledger: pX-e1 undone with the deparasite annotation; pX-e2 still active.
    states = {e["edge_id"]: e for e in dream.read_apply_ledger(md)}
    assert states["pX-e1"]["state"] == "undone"
    assert states["pX-e1"]["retracted_by"] == "deparasite"
    assert "DREAM_MAX_OUT_DEGREE" in states["pX-e1"]["retract_reason"]
    assert states["pX-e2"]["state"] == "active"
    # And t-one/t-two/distractor files are byte-identical to before any stamping.
    after = _snapshot_md(md)
    for fname in ("t-one.md", "t-two.md", "distractor.md"):
        assert after[fname] == pre_stamp[fname]


def test_retracted_pair_never_auto_reapplies(dirs, monkeypatch):
    """The ping-pong guard: retract (or any undo) is a standing verdict — the next apply
    pass must refuse the same pair, not silently re-add it."""
    md, td, idx = dirs
    monkeypatch.setenv("DREAM_COFIRE_THETA", "0.10")
    monkeypatch.setenv("DREAM_MAX_OUT_DEGREE", "1")
    # A real 4-node chain so the real apply loop discovers a genuine latent bridge.
    _write_memory(md, "alpha", "quasar ramjet coolant telemetry calibration lattice",
                  body="Notes.\n\nSee [[bravo]].\n")
    _write_memory(md, "bravo", "ramjet coolant loop plumbing calibration maintenance",
                  body="Loop.\n\nHistory in [[charlie]].\n")
    _write_memory(md, "charlie", "quasar ramjet coolant telemetry maintenance lattice",
                  body="Schedule.\n\nDrills in [[zulu]].\n")
    _write_memory(md, "zulu", "gardening almanac heirloom tomato beds", body="x\n")
    _seed_sessions(td, 5)

    code, _ = dream.run_apply_pass(md, idx, td)
    assert code == 0
    active = [e for e in dream.read_apply_ledger(md) if e["state"] == "active"]
    assert len(active) == 1
    pair = frozenset((active[0]["source"], active[0]["target"]))
    assert pair == frozenset(("alpha", "charlie"))

    # The stamped endpoint is now over the cap → deparasite retracts its un-aged edge.
    code, text = dp.run_deparasite_pass(md, idx, td, retract=True)
    assert code == 0 and "reverted 1 edge(s)" in text
    assert not [e for e in dream.read_apply_ledger(md) if e["state"] == "active"]

    # Next apply pass: the same candidate re-surfaces but is REFUSED, not re-applied.
    code, digest = dream.run_apply_pass(md, idx, td)
    assert code == 0
    assert "never auto re-applied" in digest
    assert not [e for e in dream.read_apply_ledger(md) if e["state"] == "active"]


# --------------------------------------------------------------------------- #
# Dedup-merge: non-lossy proposals, protection, contradiction routing
# --------------------------------------------------------------------------- #
_DUP_DESC = "orbital docking checklist primary revision sequence appendix ledger"


def _dup_corpus(md):
    _write_memory(md, "dock-checklist-one", _DUP_DESC,
                  body="The richer, longer surviving body.\nMore detail.\n")
    _write_memory(md, "dock-checklist-two", _DUP_DESC, body="Thin twin.\n")
    _write_memory(md, "distractor", "quasar navigation ephemeris tables", body="x\n")


def test_dedup_proposal_is_non_lossy_and_report_writes_nothing(dirs):
    md, td, idx = dirs
    _dup_corpus(md)
    _seed_sessions(td, 5)
    before = _snapshot_md(md)

    report = dp.deparasite_report(md, idx, td)
    assert report["status"] == "ok"
    props = [d for d in report["dedup"] if d["route"] == "merge-proposal"]
    assert len(props) == 1
    p = props[0]
    assert p["similarity"] >= report["dedup_jaccard"]
    # Longer/richer file suggested as survivor (deterministic heuristic).
    assert p["survivor"] == "dock-checklist-one" and p["loser"] == "dock-checklist-two"
    # The proposal names the exact non-lossy chain + the per-item command.
    assert "set_invalid_after" in p["proposal"]["chain"]
    assert "supersedes" in p["proposal"]["chain"]
    assert "--dedup-merge dock-checklist-one dock-checklist-two" in p["proposal"]["command"]
    assert _snapshot_md(md) == before, "proposals are proposals — zero writes"


def test_dedup_protected_loser_flips_survivor_and_both_protected_skips(dirs):
    md, td, idx = dirs
    _dup_corpus(md)
    # The thin twin is CITED by another memory → protected → it must be the survivor
    # even though it is shorter (a protected memory is never the loser).
    _write_memory(md, "citer", "field notes referencing the thin twin",
                  body="See [[dock-checklist-two]].\n")
    _seed_sessions(td, 5)
    report = dp.deparasite_report(md, idx, td)
    p = next(d for d in report["dedup"] if d["route"] == "merge-proposal")
    assert p["survivor"] == "dock-checklist-two" and p["loser"] == "dock-checklist-one"

    # Both protected → no proposal at all (skipped, counted).
    _write_memory(md, "citer2", "field notes referencing the rich twin",
                  body="See [[dock-checklist-one]].\n")
    report = dp.deparasite_report(md, idx, td)
    assert not [d for d in report["dedup"] if d["route"] == "merge-proposal"]
    assert report["stats"]["dedup_skipped_both_protected"] == 1


def test_dedup_floor_memory_never_scanned(dirs):
    md, td, idx = dirs
    _dup_corpus(md)
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# Floor\n\n## User\n- [Two](dock-checklist-two.md) — pinned\n")
    _seed_sessions(td, 5)
    report = dp.deparasite_report(md, idx, td)
    assert report["dedup"] == [], "floor memories are outside every counterweight lane"
    assert "dock-checklist-two" in report["stats"]["floor_excluded"]


def test_dedup_contradiction_routes_to_resolve_never_merged(dirs):
    md, td, idx = dirs
    _write_memory(md, "dock-checklist-one", _DUP_DESC, body="Longer body here.\n",
                  extra_fm='contradicts: ["dock-checklist-two"]\n')
    _write_memory(md, "dock-checklist-two", _DUP_DESC, body="Thin twin.\n")
    _write_memory(md, "distractor", "quasar navigation ephemeris tables", body="x\n")
    _seed_sessions(td, 5)

    report = dp.deparasite_report(md, idx, td)
    routed = [d for d in report["dedup"] if d["route"] == "resolve"]
    assert len(routed) == 1 and "resolve" in routed[0]["note"]
    assert not [d for d in report["dedup"] if d["route"] == "merge-proposal"]
    # The per-item executor holds the same line.
    res = dp.apply_dedup_merge(md, "dock-checklist-one", "dock-checklist-two",
                               telemetry_dir=td, index_dir=idx)
    assert res["error"] and "contradicts" in res["error"]
    assert res["changed"] is False


def test_apply_dedup_merge_executor_additive_frontmatter_only(dirs):
    md, td, idx = dirs
    _dup_corpus(md)
    _seed_sessions(td, 5)

    def _body(fname):
        text = open(os.path.join(md, fname), encoding="utf-8").read()
        return text.split("---", 2)[2]

    bodies_before = {f: _body(f) for f in ("dock-checklist-one.md", "dock-checklist-two.md")}

    res = dp.apply_dedup_merge(md, "dock-checklist-one", "dock-checklist-two",
                               telemetry_dir=td, index_dir=idx)
    assert res["error"] is None and res["changed"] is True

    survivor = open(os.path.join(md, "dock-checklist-one.md"), encoding="utf-8").read()
    loser = open(os.path.join(md, "dock-checklist-two.md"), encoding="utf-8").read()
    assert 'supersedes: ["dock-checklist-two"]' in survivor
    assert "invalid_after:" in loser
    # NON-LOSSY: both files still on disk, both bodies byte-identical.
    assert os.path.isfile(os.path.join(md, "dock-checklist-two.md"))
    for f, before in bodies_before.items():
        assert _body(f) == before, f"{f}: a dedup-merge must never touch a body byte"


def test_apply_dedup_merge_refuses_protected_loser_and_dry_run_writes_nothing(dirs):
    md, td, idx = dirs
    _dup_corpus(md)
    _write_memory(md, "citer", "field notes referencing the thin twin",
                  body="See [[dock-checklist-two]].\n")
    _seed_sessions(td, 5)
    before = _snapshot_md(md)

    res = dp.apply_dedup_merge(md, "dock-checklist-one", "dock-checklist-two",
                               telemetry_dir=td, index_dir=idx)
    assert res["error"] and "protected" in res["error"]
    assert _snapshot_md(md) == before

    res = dp.apply_dedup_merge(md, "dock-checklist-two", "dock-checklist-one",
                               telemetry_dir=td, index_dir=idx, dry_run=True)
    assert res["error"] is None and res["changed"] is True
    assert _snapshot_md(md) == before, "dry_run must write nothing"

    res = dp.apply_dedup_merge(md, "dock-checklist-one", "nope-does-not-exist",
                               telemetry_dir=td, index_dir=idx)
    assert res["error"] and "no memory file" in res["error"]


# --------------------------------------------------------------------------- #
# Surfaces: CLI + MCP
# --------------------------------------------------------------------------- #
def test_cli_deparasite_and_retract_flags(dirs, monkeypatch, capsys):
    md, td, idx = dirs
    monkeypatch.setenv("DREAM_MAX_OUT_DEGREE", "1")
    _hub_corpus(md)
    _seed_sessions(td, 5)

    rc = dream.main(["--memory-dir", md, "--telemetry-dir", td, "--index-dir", idx,
                     "--deparasite"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "de-parasite pass" in out and "REPORT/PROPOSE" in out

    rc = dream.main(["--memory-dir", md, "--retract"])
    out = capsys.readouterr().out
    assert rc == 1 and "--deparasite" in out


def test_cli_dedup_merge_per_item(dirs, capsys):
    md, td, idx = dirs
    _dup_corpus(md)
    _seed_sessions(td, 5)
    rc = dream.main(["--memory-dir", md, "--telemetry-dir", td, "--index-dir", idx,
                     "--dedup-merge", "dock-checklist-one", "dock-checklist-two"])
    out = capsys.readouterr().out
    assert rc == 0 and "dedup-merge applied" in out and "non-lossy" in out
    text = open(os.path.join(md, "dock-checklist-one.md"), encoding="utf-8").read()
    assert 'supersedes: ["dock-checklist-two"]' in text


def test_mcp_deparasite_action(dirs, monkeypatch):
    md, td, idx = dirs
    import memory.mcp_server as M

    monkeypatch.setenv("DREAM_MAX_OUT_DEGREE", "1")
    _hub_corpus(md)
    _seed_sessions(td, 5)
    monkeypatch.setattr("memory.provenance.resolve_dirs", lambda: (md, os.path.dirname(md)))

    before = _snapshot_md(md)
    out = M._tool_dream({"action": "deparasite"})
    assert "de-parasite pass" in out and "REPORT/PROPOSE" in out
    assert _snapshot_md(md) == before

    out = M._tool_dream({"action": "dedup_merge", "survivor": "x"})
    assert "required" in out
