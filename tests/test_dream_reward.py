"""Tests for DRM-5 — reward-gated reverse replay (outcome-anchored edge boosts).

Pinned acceptance criteria (ROADMAP.dream.yaml DRM-5):
  - the boost applies ONLY to edges reachable backward from a memory with a recorded
    outcome; edges with no outcome chain are untouched (asserted);
  - each boosted edge's ledger entry carries the justifying decision_chain (provenance);
  - the boost changes ranking priority / cofire ORDERING only — no body byte change, no
    new asserted claim (and θ apply-eligibility reads the raw cofire, never the boost).

Plus the round-wide firewall extension: an UN-AGED dream edge never conducts reward
(the backward walk is cut there until the edge ages in), and floor/draft memories are
never boosted.

Hermetic: tmp corpus + tmp telemetry/index dirs; HIPPO_DISABLE_DENSE pins ranking to
deterministic BM25; outcome/episode ledgers are seeded by hand with pinned timestamps
(the test_outcome.py idiom).
"""

from __future__ import annotations

import json
import os

import pytest

import memory.dream as dream
from memory import outcome as O

from .test_dream import _seed_sessions, _snapshot_md, _write_memory


@pytest.fixture(autouse=True)
def _bm25_only(monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")


@pytest.fixture
def dirs(tmp_path):
    md = str(tmp_path / "mem")
    td = str(tmp_path / "tele")
    idx = str(tmp_path / "idx")
    os.makedirs(md)
    return md, td, idx


def _seed_outcome_hit(td, name_recalled, touched_path, sid="s1"):
    """One injected-then-touched hit: episode at ts=100, cited-file touch at ts=150."""
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "episode_buffer.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"ts": 100.0, "session_id": sid, "recalled_names": [name_recalled]})
            + "\n"
        )
    with open(os.path.join(td, "outcome_events.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"ts": 150.0, "session_id": sid, "tool": "Read", "path": touched_path})
            + "\n"
        )


def _lineage_corpus(md):
    """charlie-outcome supersedes bravo-mid supersedes alpha-root — a 3-step decision
    chain whose newest node carries cited_paths (the outcome join's file signal) — plus
    an UNRELATED lineage pair (delta-note supersedes echo-note) that must stay unboosted,
    and an off-topic distractor."""
    _write_memory(
        md, "alpha-root", "quasar ramjet coolant retry policy first draft",
        body="The original retry policy.\n",
    )
    _write_memory(
        md, "bravo-mid", "quasar ramjet coolant retry policy with backoff",
        body="Refined the policy.\n",
        extra_fm='supersedes: ["alpha-root"]\n',
    )
    _write_memory(
        md, "charlie-outcome", "quasar ramjet coolant retry policy final jitter",
        body="The standing policy.\n",
        extra_fm='supersedes: ["bravo-mid"]\ncited_paths: ["src/app.py"]\n',
    )
    _write_memory(
        md, "delta-note", "krill photophore survey cadence current",
        body="Current cadence.\n",
        extra_fm='supersedes: ["echo-note"]\n',
    )
    _write_memory(md, "echo-note", "krill photophore survey cadence old", body="Old.\n")
    _write_memory(md, "zulu", "gardening almanac heirloom tomato beds", body="Unrelated.\n")


# --------------------------------------------------------------------------- #
# outcome.injection_hits — the per-memory recorded-outcome surface DRM-5 gates on
# --------------------------------------------------------------------------- #
def test_injection_hits_per_memory(dirs):
    md, td, idx = dirs
    _lineage_corpus(md)
    _seed_outcome_hit(td, "charlie-outcome", "src/app.py", sid="s1")
    # A second session injects but never touches the cited file — no hit for it.
    with open(os.path.join(td, "episode_buffer.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"ts": 200.0, "session_id": "s2", "recalled_names": ["delta-note"]})
            + "\n"
        )
    hits = O.injection_hits(md, td)
    assert hits == {"charlie-outcome": {"hits": 1, "sessions": ["s1"]}}
    # The aggregate proxy still reads the same join (denominator includes the miss-free
    # charlie only — delta-note has no cited_paths, so it carries no file signal at all).
    r = O.injection_precision(md, td)
    assert r["hits"] == 1 and r["injected_with_cites"] == 1


def test_injection_hits_empty_without_signal(dirs):
    md, td, idx = dirs
    _lineage_corpus(md)
    assert O.injection_hits(md, td) == {}


# --------------------------------------------------------------------------- #
# Reward gating: no outcome → no boost, anywhere
# --------------------------------------------------------------------------- #
def test_no_outcome_means_no_boost_anywhere(dirs):
    md, td, idx = dirs
    _lineage_corpus(md)
    _seed_sessions(td, 5)

    boosts = dream.reward_boosts(md, idx, td)
    assert boosts["outcome_memories"] == {}
    assert boosts["memories"] == {} and boosts["edges"] == []

    result = dream.discover(md, idx, td)
    assert result["status"] == "ok"
    assert result["reward"]["edges"] == []
    assert all("boost" not in c for c in result["candidates"])
    assert result["stats"]["reward_boosted_edges"] == 0
    # No boost ledger materializes for a boost-free pass (empty-norm hygiene).
    code, _ = dream.run_report_pass(md, idx, td)
    assert code == 0
    ddir = dream.dream_dir(td)
    assert not [f for f in os.listdir(ddir) if f.startswith("boosts-")]


# --------------------------------------------------------------------------- #
# The backward walk: upstream chain boosted, unrelated lineage untouched
# --------------------------------------------------------------------------- #
def test_backward_walk_boosts_upstream_chain_only(dirs):
    md, td, idx = dirs
    _lineage_corpus(md)
    _seed_sessions(td, 5)
    _seed_outcome_hit(td, "charlie-outcome", "src/app.py", sid="s1")

    boosts = dream.reward_boosts(md, idx, td)
    assert boosts["outcome_memories"] == {"charlie-outcome": 1}
    # The rewarded terminus and BOTH upstream steps carry replay priority.
    assert set(boosts["memories"]) == {"charlie-outcome", "bravo-mid", "alpha-root"}
    # Exactly the two lineage edges reachable BACKWARD from the outcome memory.
    edges = {(e["edge"]["from"], e["edge"]["relation"], e["edge"]["to"]) for e in boosts["edges"]}
    assert edges == {
        ("charlie-outcome", "supersedes", "bravo-mid"),
        ("bravo-mid", "supersedes", "alpha-root"),
    }
    # The unrelated delta→echo lineage has no outcome anchor: untouched (the assertion).
    assert "delta-note" not in boosts["memories"]
    assert "echo-note" not in boosts["memories"]
    # Every boosted-edge row carries its justifying decision chain + outcome anchor.
    for row in boosts["edges"]:
        assert row["outcome_memory"] == "charlie-outcome"
        assert row["hits"] == 1
        assert set(row["decision_chain"]) == {"charlie-outcome", "bravo-mid", "alpha-root"}


def test_boost_ledger_written_with_decision_chain_provenance(dirs):
    md, td, idx = dirs
    _lineage_corpus(md)
    _seed_sessions(td, 5)
    _seed_outcome_hit(td, "charlie-outcome", "src/app.py", sid="s1")

    code, text = dream.run_report_pass(md, idx, td)
    assert code == 0
    assert "reward (DRM-5 reverse replay)" in text
    ddir = dream.dream_dir(td)
    boost_files = [f for f in os.listdir(ddir) if f.startswith("boosts-") and f.endswith(".jsonl")]
    assert len(boost_files) == 1
    rows = [
        json.loads(line)
        for line in open(os.path.join(ddir, boost_files[0]), encoding="utf-8")
        if line.strip()
    ]
    assert len(rows) == 2
    for row in rows:
        for field in ("pass", "edge", "boost", "outcome_memory", "hits", "decision_chain"):
            assert field in row, f"boost ledger row missing {field}"
        assert row["decision_chain"], "the justifying decision_chain is the provenance"


# --------------------------------------------------------------------------- #
# Ranking-only: no body bytes, no new claim kind, θ reads raw cofire
# --------------------------------------------------------------------------- #
def test_boost_writes_no_memory_bytes_and_no_new_claim_kind(dirs):
    md, td, idx = dirs
    _lineage_corpus(md)
    _seed_sessions(td, 5)
    _seed_outcome_hit(td, "charlie-outcome", "src/app.py", sid="s1")
    before = _snapshot_md(md)

    code, _ = dream.run_report_pass(md, idx, td)
    assert code == 0
    assert _snapshot_md(md) == before, "a boost must never touch a memory file"

    result = dream.discover(md, idx, td)
    assert result["stats"]["reward_boosted_edges"] >= 1
    # No new asserted-claim kind rides in on the reward pass: the closed Tier-A taxonomy
    # is untouched, and a boost only ANNOTATES an existing candidate row.
    for c in result["candidates"]:
        assert c["kind"] in ("completion", "bridge", "refines")


def test_theta_eligibility_ignores_boost():
    # A sub-θ candidate with an enormous boost is still ineligible: the boost reorders,
    # it never widens autonomy (that would be a dated owner decision, not a weight).
    cand = {"kind": "bridge", "cofire": 0.5, "mutual": True, "boost": 100.0}
    assert not dream.apply_eligible(cand, theta=0.9)
    cand = {"kind": "refines", "cofire": 0.5, "boost": 100.0}
    assert not dream.apply_eligible(cand, theta=0.9)


def test_boosted_candidate_outranks_equal_cofire_peer(dirs):
    md, td, idx = dirs
    # Two structurally IDENTICAL latent bridges with disjoint mirrored vocabularies —
    # same doc lengths, same term counts — so their co-fire strengths are equal under
    # BM25. Only the second chain's terminus carries a recorded outcome.
    _write_memory(md, "alpha", "quasar ramjet coolant telemetry calibration lattice",
                  body="Alpha notes.\n\nSee [[bravo]] for the loop.\n")
    _write_memory(md, "bravo", "ramjet coolant loop plumbing calibration maintenance",
                  body="Bravo notes.\n\nHistory in [[charlie]].\n")
    _write_memory(md, "charlie", "quasar ramjet coolant telemetry maintenance lattice",
                  body="Charlie notes.\n")
    _write_memory(md, "delta", "krill photophore migration acoustics survey beacon",
                  body="Delta notes.\n\nSee [[echo]] for the loop.\n")
    _write_memory(md, "echo", "photophore migration relay acoustics plumbing cadence",
                  body="Echo notes.\n\nHistory in [[foxtrot]].\n")
    _write_memory(
        md, "foxtrot", "krill photophore migration acoustics cadence beacon",
        body="Foxtrot notes.\n",
        extra_fm='cited_paths: ["src/app.py"]\n',
    )
    _seed_sessions(td, 5)
    _seed_outcome_hit(td, "foxtrot", "src/app.py", sid="s1")

    result = dream.discover(md, idx, td)
    assert result["status"] == "ok"
    bridges = {
        frozenset((c["source"], c["target"])): c
        for c in result["candidates"]
        if c["kind"] == "bridge"
    }
    b1 = bridges.get(frozenset(("alpha", "charlie")))
    b2 = bridges.get(frozenset(("delta", "foxtrot")))
    assert b1 is not None and b2 is not None, "both mirrored latent bridges must surface"
    # The mirror must hold or this test can't claim ordering came from the boost.
    assert b1["cofire"] == b2["cofire"], "fixture symmetry broke — mirrored corpora drifted"
    assert "boost" not in b1
    assert b2["boost"] > 0 and b2["boost_provenance"] == ["foxtrot"]
    order = [frozenset((c["source"], c["target"])) for c in result["candidates"] if c["kind"] == "bridge"]
    assert order.index(frozenset(("delta", "foxtrot"))) < order.index(
        frozenset(("alpha", "charlie"))
    ), "equal cofire → the outcome-anchored candidate must sort first (ranking-only boost)"


def test_replay_worklist_prioritizes_outcome_anchored_traces(dirs):
    md, td, idx = dirs
    _lineage_corpus(md)
    _seed_sessions(td, 5)
    _seed_outcome_hit(td, "charlie-outcome", "src/app.py", sid="s1")
    result = dream.discover(md, idx, td)
    preview = result["stats"]["worklist_preview"]
    # The three outcome-anchored chain members lead the replay worklist (boost desc
    # before the under-connected ordering the un-boosted tail keeps).
    assert set(preview[:3]) == {"charlie-outcome", "bravo-mid", "alpha-root"}


# --------------------------------------------------------------------------- #
# Firewall extension: an un-aged dream edge never conducts reward
# --------------------------------------------------------------------------- #
def _stamp_dream_refines(md, child, parent, edge_id, applied_at):
    """A dream-applied refines edge exactly as DRM-2 leaves it: additive frontmatter on
    the child + the bracket-free stamp inside the dream:links block + a ledger row."""
    from memory.links import add_typed_relation

    path = os.path.join(md, f"{child}.md")
    assert add_typed_relation(path, "refines", parent)["changed"]
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(
            "<!-- dream:links -->\n"
            f"<!-- dream: refines {parent} · pass=pX · edge={edge_id} · cofire=0.95 -->\n"
            "<!-- /dream:links -->\n"
        )
    with open(dream.apply_ledger_path(md), "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "edge_id": edge_id,
                    "pass": "pX",
                    "kind": "refines",
                    "source": child,
                    "target": parent,
                    "cofire": 0.95,
                    "state": "active",
                    "applied_at_distinct_count": applied_at,
                }
            )
            + "\n"
        )


def test_unaged_dream_lineage_edge_conducts_no_reward(dirs, monkeypatch):
    md, td, idx = dirs
    monkeypatch.setenv("DREAM_AGE_SESSIONS", "5")
    _write_memory(
        md, "golf-policy", "xenon gimbal damping policy standing",
        body="Standing policy.\n",
        extra_fm='cited_paths: ["src/gimbal.py"]\n',
    )
    _write_memory(md, "golf-policy-history", "xenon gimbal damping policy history",
                  body="History.\n")
    _write_memory(md, "india", "greenhouse irrigation drip schedule", body="x\n")
    # The ONLY lineage edge is dream-applied and NOT yet aged (applied at 5, now 5).
    _stamp_dream_refines(md, "golf-policy", "golf-policy-history", "pX-e1", applied_at=5)
    _seed_sessions(td, 5)
    _seed_outcome_hit(td, "golf-policy", "src/gimbal.py", sid="s1")

    boosts = dream.reward_boosts(
        md, idx, td, unaged_pairs=dream.unaged_dream_pairs(md, 5)
    )
    # The outcome memory itself is boosted (it IS the recorded outcome)…
    assert "golf-policy" in boosts["memories"]
    # …but the un-aged dream edge conducts nothing upstream: the walk is cut there.
    assert "golf-policy-history" not in boosts["memories"]
    assert boosts["edges"] == []

    # Once aged in (5 → 10 distinct sessions), the same edge conducts reward.
    _seed_sessions(td, 10)
    boosts = dream.reward_boosts(
        md, idx, td, unaged_pairs=dream.unaged_dream_pairs(md, 10)
    )
    assert "golf-policy-history" in boosts["memories"]
    assert len(boosts["edges"]) == 1


def test_floor_and_draft_memories_never_boosted(dirs):
    md, td, idx = dirs
    _write_memory(
        md, "hotel-current", "orbital docking checklist current revision",
        body="Current.\n",
        extra_fm='supersedes: ["hotel-floor-old", "hotel-draft-old"]\ncited_paths: ["src/dock.py"]\n',
    )
    _write_memory(md, "hotel-floor-old", "orbital docking checklist floor pinned original",
                  body="Old.\n")
    _write_memory(md, "hotel-draft-old", "orbital docking checklist speculative variant",
                  body="Draft.\n", extra_fm="confidence: draft\n")
    _write_memory(md, "india", "greenhouse irrigation drip schedule", body="x\n")
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# Floor\n\n## User\n- [Old](hotel-floor-old.md) — pinned\n")
    _seed_sessions(td, 5)
    _seed_outcome_hit(td, "hotel-current", "src/dock.py", sid="s1")

    result = dream.discover(md, idx, td)
    boosts = result["reward"]
    assert "hotel-current" in boosts["memories"]
    # The round-wide endpoint rule extends to reward: floor + draft are never boosted.
    assert "hotel-floor-old" not in boosts["memories"]
    assert "hotel-draft-old" not in boosts["memories"]
    for row in boosts["edges"]:
        assert row["edge"]["to"] not in ("hotel-floor-old", "hotel-draft-old")
