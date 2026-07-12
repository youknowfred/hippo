"""Tests for memory/dream.py — DRM-1: the replay harness + candidate-edge ledger.

Pinned acceptance criteria (ROADMAP.dream.yaml DRM-1):
  - the pass emits a jsonl candidate ledger under the gitignored derived dir with
    per-candidate kind/source/target/distance/cofire/query — and ZERO writes to any .md
    memory (byte-compared before/after);
  - below the soak bar / an empty-young corpus → zero candidates AND an explicit say-so;
  - floor memories never appear as an edge endpoint;
  - a candidate duplicating an existing edge is excluded (novelty filter — pre-linked pair);
  - the pass prints the co-fire-strength distribution + count-by-kind (the θ/cap
    calibration surface).

Hermetic: tmp corpus + tmp telemetry/index dirs; HIPPO_DISABLE_DENSE pins the ranking to
deterministic BM25 (dense availability must not flip a co-fire assertion between machines).
"""

from __future__ import annotations

import json
import os

import pytest

import memory.dream as dream


# --------------------------------------------------------------------------- #
# Fixture helpers
# --------------------------------------------------------------------------- #
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
    """{filename: bytes} for every file under the memory dir (the zero-writes oracle)."""
    out = {}
    for name in sorted(os.listdir(md)):
        p = os.path.join(md, name)
        if os.path.isfile(p):
            out[name] = open(p, "rb").read()
    return out


@pytest.fixture(autouse=True)
def _bm25_only(monkeypatch):
    """Deterministic hermetic ranking: dense off, so co-fire strengths are pure BM25."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")


@pytest.fixture
def dirs(tmp_path):
    md = str(tmp_path / "mem")
    td = str(tmp_path / "tele")
    idx = str(tmp_path / "idx")
    os.makedirs(md)
    return md, td, idx


def _bridge_corpus(md):
    """A–B–C chain (alpha→bravo→charlie wikilinks) whose A/C descriptions strongly co-fire,
    with A–C absent — the canonical latent transitive bridge. Plus one off-topic memory so
    ranking has a non-co-firing distractor."""
    _write_memory(
        md, "alpha", "quasar ramjet coolant telemetry calibration for the orbital lattice",
        body="Calibration notes.\n\nSee [[bravo]] for the loop.\n",
    )
    _write_memory(
        md, "bravo", "ramjet coolant loop plumbing between calibration and maintenance",
        body="Loop plumbing.\n\nMaintenance history in [[charlie]].\n",
    )
    _write_memory(
        md, "charlie", "quasar ramjet coolant telemetry maintenance schedule orbital lattice",
        body="Maintenance schedule.\n",
    )
    _write_memory(
        md, "zulu", "gardening almanac for heirloom tomato rotation beds",
        body="Completely unrelated.\n",
    )


# --------------------------------------------------------------------------- #
# The core pass: ledger emitted, fields present, ZERO memory writes
# --------------------------------------------------------------------------- #
def test_pass_emits_ledger_and_writes_no_memory(dirs):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    before = _snapshot_md(md)

    code, text = dream.run_report_pass(md, idx, td)

    assert code == 0
    # ZERO writes to any memory file — byte-for-byte (the inv4 strongest-form assertion).
    assert _snapshot_md(md) == before
    # The ledger landed under the DERIVED dir (never the corpus dir).
    ddir = dream.dream_dir(td)
    ledgers = [f for f in os.listdir(ddir) if f.startswith("candidates-") and f.endswith(".jsonl")]
    assert len(ledgers) == 1
    rows = [
        json.loads(line)
        for line in open(os.path.join(ddir, ledgers[0]), encoding="utf-8")
        if line.strip()
    ]
    assert rows, "the latent alpha–charlie bridge should have been discovered"
    for row in rows:
        for field in ("pass", "kind", "source", "target", "distance", "cofire", "query"):
            assert field in row
    # The canonical latent edge: alpha↔charlie at graph distance 2, kind bridge.
    bridge = [r for r in rows if {r["source"], r["target"]} == {"alpha", "charlie"}]
    assert bridge and bridge[0]["kind"] == "bridge" and bridge[0]["distance"] == 2
    assert 0.0 < bridge[0]["cofire"] <= 1.0
    assert bridge[0]["query"], "the firing query is provenance — must be recorded"


def test_report_prints_distribution_and_count_by_kind(dirs):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    code, text = dream.run_report_pass(md, idx, td)
    assert code == 0
    assert "count-by-kind" in text
    assert "co-fire strength distribution" in text
    assert "θ sweep" in text
    assert "REPORT-ONLY" in text and "zero memory writes" in text
    # The θ/cap calibration knobs are named so the owner's flip decision has its handle.
    assert "DREAM_COFIRE_THETA" in text


# --------------------------------------------------------------------------- #
# Legible refusals (inv3): below soak / empty corpus say so, and emit nothing
# --------------------------------------------------------------------------- #
def test_below_soak_bar_zero_candidates_and_says_so(dirs):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 4)  # one short of the bar
    before = _snapshot_md(md)
    code, text = dream.run_report_pass(md, idx, td)
    assert code == 0  # a legible refusal is a correct outcome, not an error
    assert "below the curation-soak bar" in text
    assert "4/5" in text
    assert not os.path.isdir(dream.dream_dir(td)) or not os.listdir(dream.dream_dir(td))
    assert _snapshot_md(md) == before


def test_empty_young_corpus_says_so(dirs):
    md, td, idx = dirs
    _write_memory(md, "alpha", "quasar ramjet telemetry")
    _write_memory(md, "bravo", "ramjet coolant loop")
    _seed_sessions(td, 5)
    code, text = dream.run_report_pass(md, idx, td)
    assert code == 0
    assert "too few for latent edges" in text
    assert not os.path.isdir(dream.dream_dir(td)) or not os.listdir(dream.dream_dir(td))


def test_ok_empty_pass_states_empty_is_the_norm(dirs):
    md, td, idx = dirs
    # Three well-linked memories with disjoint vocabularies: nothing co-fires, nothing
    # latent — the healthy empty pass.
    _write_memory(md, "alpha", "quasar navigation ephemeris tables", body="See [[bravo]].\n")
    _write_memory(md, "bravo", "sourdough hydration baking ratios", body="See [[charlie]].\n")
    _write_memory(md, "charlie", "marathon interval training cadence", body="x\n")
    _seed_sessions(td, 5)
    code, text = dream.run_report_pass(md, idx, td)
    assert code == 0
    assert "empty pass" in text and "norm" in text
    # An OK pass writes the (empty) ledger — the auditable record the pass ran.
    ddir = dream.dream_dir(td)
    assert os.path.isdir(ddir) and len(os.listdir(ddir)) == 1


# --------------------------------------------------------------------------- #
# Floor exclusion — floor memories are never an edge endpoint
# --------------------------------------------------------------------------- #
def test_floor_memories_never_an_endpoint(dirs):
    md, td, idx = dirs
    _bridge_corpus(md)
    # floor-pinned-lore co-fires with the ramjet cluster AND is body-named by alpha —
    # both discovery paths would emit it were it not floor-pinned.
    _write_memory(
        md, "floor-pinned-lore",
        "quasar ramjet coolant telemetry calibration maintenance orbital lattice",
    )
    with open(os.path.join(md, "alpha.md"), "a", encoding="utf-8") as fh:
        fh.write("\nAlso consult floor-pinned-lore for the standing rules.\n")
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# Floor\n\n## User\n- [Lore](floor-pinned-lore.md) — pinned\n")
    _seed_sessions(td, 5)

    td_result = dream.discover(md, idx, td)
    assert td_result["status"] == "ok"
    for c in td_result["candidates"]:
        assert c["source"] != "floor-pinned-lore"
        assert c["target"] != "floor-pinned-lore"
    assert "floor-pinned-lore" in td_result["stats"]["floor_excluded"]


# --------------------------------------------------------------------------- #
# Novelty filter — an existing edge is never re-proposed
# --------------------------------------------------------------------------- #
def test_prelinked_pair_is_excluded(dirs):
    md, td, idx = dirs
    # delta and echo co-fire hard AND are already linked — the pair must not surface.
    _write_memory(
        md, "delta", "xenon thruster gimbal vibration dampener specs",
        body="Twin of [[echo]].\n",
    )
    _write_memory(md, "echo", "xenon thruster gimbal vibration dampener history")
    _write_memory(md, "foxtrot", "unrelated knitting pattern archive")
    _seed_sessions(td, 5)
    result = dream.discover(md, idx, td)
    assert result["status"] == "ok"
    assert not [
        c for c in result["candidates"] if {c["source"], c["target"]} == {"delta", "echo"}
    ]
    assert result["stats"]["novelty_excluded"] >= 1


# --------------------------------------------------------------------------- #
# Completion kind — the body already names the target
# --------------------------------------------------------------------------- #
def test_body_mention_yields_completion_candidate(dirs):
    md, td, idx = dirs
    _write_memory(
        md, "golf-launch-checklist", "pre launch checklist for the golf payload manifest",
        body="Before launch, re-read hotel-abort-criteria and sign off.\n",
    )
    _write_memory(
        md, "hotel-abort-criteria", "abort criteria thresholds for ascent anomalies"
    )
    _write_memory(md, "india", "greenhouse irrigation drip schedule")
    _seed_sessions(td, 5)
    result = dream.discover(md, idx, td)
    assert result["status"] == "ok"
    comp = [
        c
        for c in result["candidates"]
        if c["kind"] == "completion"
        and c["source"] == "golf-launch-checklist"
        and c["target"] == "hotel-abort-criteria"
    ]
    assert comp, "a plain-text mention of another memory's slug is the completion kind"
    assert comp[0]["signal"] == "body-mention"


def test_wikilinked_mention_is_not_a_completion(dirs):
    md, td, idx = dirs
    # The mention is ALREADY a resolved wikilink — an edge exists; nothing to complete.
    _write_memory(
        md, "golf-launch-checklist", "pre launch checklist for the golf payload manifest",
        body="Before launch, re-read [[hotel-abort-criteria]] and sign off.\n",
    )
    _write_memory(md, "hotel-abort-criteria", "abort criteria thresholds for ascent anomalies")
    _write_memory(md, "india", "greenhouse irrigation drip schedule")
    _seed_sessions(td, 5)
    result = dream.discover(md, idx, td)
    assert result["status"] == "ok"
    assert not [c for c in result["candidates"] if c["kind"] == "completion"]


def test_dangling_wikilink_fuzzy_completion(dirs):
    md, td, idx = dirs
    # [[hotel-abort-criteria]] does not resolve (the file is hotel-abort-criteria-v2) but
    # exactly one stem contains the dangling slug → completion via fuzzy resolution.
    _write_memory(
        md, "golf-launch-checklist", "pre launch checklist for the golf payload manifest",
        body="Re-read [[hotel-abort-criteria]] first.\n",
    )
    _write_memory(
        md, "hotel-abort-criteria-v2", "abort criteria thresholds for ascent anomalies"
    )
    _write_memory(md, "india", "greenhouse irrigation drip schedule")
    _seed_sessions(td, 5)
    result = dream.discover(md, idx, td)
    assert result["status"] == "ok"
    comp = [
        c
        for c in result["candidates"]
        if c["kind"] == "completion"
        and c["source"] == "golf-launch-checklist"
        and c["target"] == "hotel-abort-criteria-v2"
    ]
    assert comp
    assert comp[0]["signal"].startswith("dangling-wikilink")


# --------------------------------------------------------------------------- #
# Refines kind — slug-prefix child→parent
# --------------------------------------------------------------------------- #
def test_slug_prefix_cofire_yields_refines(dirs):
    md, td, idx = dirs
    _write_memory(
        md, "deploy-runbook", "deploy runbook for the staging cutover fleet rollout"
    )
    _write_memory(
        md, "deploy-runbook-rollback", "deploy runbook rollback drill for the staging cutover fleet"
    )
    _write_memory(md, "india", "greenhouse irrigation drip schedule")
    _seed_sessions(td, 5)
    result = dream.discover(md, idx, td)
    assert result["status"] == "ok"
    ref = [c for c in result["candidates"] if c["kind"] == "refines"]
    assert ref, "a co-firing slug-prefix pair is the refines kind"
    assert ref[0]["source"] == "deploy-runbook-rollback"  # child refines parent
    assert ref[0]["target"] == "deploy-runbook"
    assert ref[0]["cofire"] > 0


# --------------------------------------------------------------------------- #
# confidence: draft is quarantined — never source nor target (inv-DRM-firewall)
# --------------------------------------------------------------------------- #
def test_draft_confidence_excluded_as_endpoint(dirs):
    md, td, idx = dirs
    _bridge_corpus(md)
    _write_memory(
        md, "papa-draft-hypothesis",
        "quasar ramjet coolant telemetry calibration orbital lattice speculation",
        extra_fm="confidence: draft\n",
    )
    _seed_sessions(td, 5)
    result = dream.discover(md, idx, td)
    assert result["status"] == "ok"
    for c in result["candidates"]:
        assert "papa-draft-hypothesis" not in (c["source"], c["target"])
    assert "papa-draft-hypothesis" in result["stats"]["draft_excluded"]


# --------------------------------------------------------------------------- #
# The apply-eligibility bar (the calibrated θ/mutuality gate DRM-2 consumes)
# --------------------------------------------------------------------------- #
def test_apply_eligible_calibrated_bar():
    # completion: θ-exempt (text evidence).
    assert dream.apply_eligible({"kind": "completion", "cofire": 0.0}, theta=0.9)
    # refines: cofire ≥ θ.
    assert dream.apply_eligible({"kind": "refines", "cofire": 0.91}, theta=0.9)
    assert not dream.apply_eligible({"kind": "refines", "cofire": 0.89}, theta=0.9)
    # bridge: MUTUAL and cofire ≥ θ — the live-corpus calibration's separator.
    assert dream.apply_eligible({"kind": "bridge", "cofire": 0.95, "mutual": True}, theta=0.9)
    assert not dream.apply_eligible({"kind": "bridge", "cofire": 0.95, "mutual": False}, theta=0.9)
    assert not dream.apply_eligible({"kind": "bridge", "cofire": 0.85, "mutual": True}, theta=0.9)
    # Tier B/C or junk kinds never clear this gate.
    assert not dream.apply_eligible({"kind": "supersedes", "cofire": 1.0, "mutual": True}, theta=0.9)
    assert not dream.apply_eligible({"kind": "", "cofire": 1.0}, theta=0.9)


# --------------------------------------------------------------------------- #
# Aging-firewall pure functions (the DRM-2 integration rides on these)
# --------------------------------------------------------------------------- #
def test_edge_aged_in_pure_function():
    assert dream.edge_aged_in({"applied_at_distinct_count": 10}, 15, window=5) is True
    assert dream.edge_aged_in({"applied_at_distinct_count": 11}, 15, window=5) is False
    # Missing/junk provenance NEVER ages in — fail toward the firewall.
    assert dream.edge_aged_in({}, 100, window=5) is False
    assert dream.edge_aged_in({"applied_at_distinct_count": "7"}, 100, window=5) is False
    assert dream.edge_aged_in({"applied_at_distinct_count": True}, 100, window=5) is False


def test_unaged_dream_pairs_reads_ledger_states(tmp_path, monkeypatch):
    monkeypatch.setenv("DREAM_AGE_SESSIONS", "5")
    md = str(tmp_path / "mem")
    os.makedirs(md)
    rows = [
        # active + un-aged → firewalled out of the source view
        {"edge_id": "p1-e1", "source": "a", "target": "b", "state": "active",
         "applied_at_distinct_count": 8},
        # active + aged → trusted source, NOT in the un-aged set
        {"edge_id": "p1-e2", "source": "c", "target": "d", "state": "active",
         "applied_at_distinct_count": 1},
        # applied then undone → never a source, and not in the un-aged set either
        {"edge_id": "p1-e3", "source": "e", "target": "f", "state": "active",
         "applied_at_distinct_count": 9},
        {"edge_id": "p1-e3", "state": "undone"},
    ]
    with open(dream.apply_ledger_path(md), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    pairs = dream.unaged_dream_pairs(md, distinct_sessions_now=10)
    assert pairs == {frozenset(("a", "b"))}
    # The append-only merge keeps full provenance on the superseded edge.
    ledger = dream.read_apply_ledger(md)
    undone = [e for e in ledger if e["edge_id"] == "p1-e3"]
    assert undone and undone[0]["state"] == "undone" and undone[0]["source"] == "e"


# --------------------------------------------------------------------------- #
# CLI smoke — the shipped invocation shape (`python -m memory.dream --dry-run`)
# --------------------------------------------------------------------------- #
def test_cli_dry_run_smoke(dirs, capsys):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    rc = dream.main(
        ["--memory-dir", md, "--telemetry-dir", td, "--index-dir", idx, "--dry-run"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "dream pass" in out and "count-by-kind" in out


def test_cli_json_mode(dirs, capsys):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    rc = dream.main(["--memory-dir", md, "--telemetry-dir", td, "--index-dir", idx, "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["status"] == "ok"
    assert isinstance(payload["candidates"], list)
