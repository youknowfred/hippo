"""Tests for memory/dream_generate.py — DRM-6: the quarantined generative payload.

Pinned acceptance criteria (ROADMAP.dream.yaml DRM-6):
  - schema/hypothesis memories are created ONLY at confidence:draft, down-weighted in
    recall (proven in test_recall — draft below equivalent verified), and excluded from
    abstention-sensitive answering (draft-only result sets collapse to abstention);
  - a draft unconfirmed past DREAM_DRAFT_HORIZON auto-proposes for archive; graduation
    to verified requires an EXTERNAL evidence/outcome event, NOT a second human glance
    (asserted — no self-graduation path);
  - confidence is wired into ranking and derives-from is a typed relation with a version
    bump (asserted in test_recall / test_links);
  - a graduation_rate / draft-reject alarm reports proposal signal-to-noise; the
    prospective-recall metric counts abstain→hit flips over a frozen backlog;
  - /dream NEVER reads a confidence:draft or un-aged discovered-by:dream item as SOURCE
    (the firewall extends to generative output — node-level, not just endpoint-level).

Plus the DRM-2 discipline carried over: flag default OFF (DREAM-KILL-1), per-pass cap,
hard secret BLOCK, complete ledger provenance + on-disk stamps (doctor-reconciled),
byte-exact undo with refuse-on-drift, the undone/archived ping-pong guard, and NEVER an
autonomous commit.

Hermetic: tmp corpus + tmp telemetry/index dirs; HIPPO_DISABLE_DENSE pins ranking to
deterministic BM25; outcome/episode ledgers seeded by hand (the test_outcome idiom).
"""

from __future__ import annotations

import json
import os
import re

import pytest

import memory.dream as dream
import memory.dream_generate as dg
from memory.build_index import _extract_confidence, load_index
from memory.provenance import parse_frontmatter

from .test_dream import _seed_sessions, _snapshot_md, _write_memory


@pytest.fixture(autouse=True)
def _bm25_only_low_theta(monkeypatch):
    """Deterministic hermetic ranking; a low θ so the small fixture clusters clear the
    bar (the calibrated θ value itself is pinned by test_dream)."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("DREAM_COFIRE_THETA", "0.50")


@pytest.fixture
def dirs(tmp_path):
    md = str(tmp_path / "mem")
    td = str(tmp_path / "tele")
    idx = str(tmp_path / "idx")
    os.makedirs(md)
    return md, td, idx


def _snapshot_corpus(md):
    snap = _snapshot_md(md)
    snap.pop("dream-ledger.jsonl", None)
    return snap


def _cluster_corpus(md, cited=True):
    """Three UNLINKED siblings sharing strong description vocabulary (mutual co-fire)
    plus one distractor — the canonical schema cluster."""
    extra = '  cited_paths: ["src/app.py"]\n' if cited else ""
    _write_memory(
        md, "ramjet-calibration",
        "quasar ramjet coolant telemetry calibration for the orbital lattice",
        extra_fm=extra,
    )
    _write_memory(
        md, "ramjet-maintenance",
        "quasar ramjet coolant telemetry maintenance schedule orbital lattice",
        extra_fm='  cited_paths: ["src/maint.py"]\n' if cited else "",
    )
    _write_memory(
        md, "ramjet-alerts",
        "quasar ramjet coolant telemetry alerting thresholds orbital lattice",
        extra_fm=extra,
    )
    _write_memory(
        md, "zulu", "gardening almanac for heirloom tomato rotation beds",
    )


def _stage_one(md, td, idx):
    """Run a staged generative pass over the cluster corpus; return the staged rows."""
    _seed_sessions(td, 5)
    code, text = dg.run_generative_pass(md, idx, td, stage=True)
    assert code == 0, text
    rows = dream.generated_rows(md)
    assert rows, text
    return rows, text


# --------------------------------------------------------------------------- #
# The flag: default OFF (DREAM-KILL-1 — P3 ships behind a flag)
# --------------------------------------------------------------------------- #
def test_generative_flag_default_off(monkeypatch):
    monkeypatch.delenv("HIPPO_DREAM_GENERATIVE", raising=False)
    assert dg.generative_enabled() is False
    monkeypatch.setenv("HIPPO_DREAM_GENERATIVE", "1")
    assert dg.generative_enabled() is True
    monkeypatch.setenv("HIPPO_DREAM_GENERATIVE", "yes-please")  # junk stays OFF
    assert dg.generative_enabled() is False


def test_bare_apply_pass_stages_nothing_with_flag_off(dirs, monkeypatch):
    """The shipped default: an apply pass runs the edge backbone only — zero generated
    files, no generative digest section."""
    md, td, idx = dirs
    monkeypatch.delenv("HIPPO_DREAM_GENERATIVE", raising=False)
    _cluster_corpus(md)
    _seed_sessions(td, 5)
    files_before = set(os.listdir(md))
    code, digest = dream.run_apply_pass(md, idx, td)
    assert code == 0
    new_files = set(os.listdir(md)) - files_before - {"dream-ledger.jsonl"}
    assert not new_files, f"flag-off pass created files: {new_files}"
    assert "generative pass" not in digest


# --------------------------------------------------------------------------- #
# Clustering the co-firing sets
# --------------------------------------------------------------------------- #
def _pair(a, b, cofire=0.95, mutual=True, distance=None, query="q"):
    return {"a": a, "b": b, "cofire": cofire, "mutual": mutual, "distance": distance, "query": query, "seed": a}


def test_cluster_cofire_components_and_hypothesis_pairs():
    pairs = [
        # a-b-c: a mutual triangle above θ → one schema cluster
        _pair("a", "b"), _pair("b", "c"), _pair("a", "c"),
        # d-e: strong + mutual + DISCONNECTED → hypothesis pair
        _pair("d", "e", distance=None),
        # f-g: strong but ONE-WAY → neither
        _pair("f", "g", mutual=False),
        # h-i: mutual but sub-θ → neither
        _pair("h", "i", cofire=0.30),
        # j-k: mutual + strong but graph-CLOSE (a bridge's job, not a hypothesis)
        _pair("j", "k", distance=2),
    ]
    out = dg.cluster_cofire(pairs, theta=0.5)
    assert out["clusters"] == [["a", "b", "c"]]
    # d-e only: one-way (f-g), sub-θ (h-i), and graph-CLOSE (j-k, a bridge's job) all out
    assert [(p["a"], p["b"]) for p in out["hypothesis_pairs"]] == [("d", "e")]
    assert all(p["distance"] is None for p in out["hypothesis_pairs"])
    # a two-member component is a pair, not a pattern — no cluster
    out2 = dg.cluster_cofire([_pair("x", "y", distance=3)], theta=0.5)
    assert out2["clusters"] == []


def test_cluster_members_never_double_as_hypotheses():
    pairs = [
        _pair("a", "b"), _pair("b", "c"), _pair("a", "c", distance=None),
    ]
    out = dg.cluster_cofire(pairs, theta=0.5)
    assert out["clusters"] == [["a", "b", "c"]]
    assert out["hypothesis_pairs"] == []  # the schema claims first


def test_oversized_cluster_is_a_theta_signal_never_a_proposal():
    """The 2026-07-12 live-corpus finding, pinned: a mega-component (the whole release
    family fused at θ=0.90) is θ under-discriminating, not a pattern — routed to
    ``oversized`` (report-only), never staged, and its members are not hypotheses."""
    members = [f"m{i}" for i in range(9)]
    chain = [_pair(members[i], members[i + 1], distance=None) for i in range(8)]
    out = dg.cluster_cofire(chain, theta=0.5)
    assert out["clusters"] == []
    assert out["oversized"] == [sorted(members)]
    assert out["hypothesis_pairs"] == []  # connected members are not "distant"

    props = dg.propose_generative({"pairs": chain}, texts={})
    assert props["schemas"] == [] and props["oversized"] == [sorted(members)]
    # the bound never sinks below the min bar, and env junk stays on the default
    assert dg.schema_max_cluster() >= dg.schema_min_cluster()


# --------------------------------------------------------------------------- #
# Proposal synthesis (deterministic, mechanical)
# --------------------------------------------------------------------------- #
def test_propose_generative_schema_shape(dirs):
    md, td, idx = dirs
    _cluster_corpus(md)
    _seed_sessions(td, 5)
    result = dream.discover(md, idx, td)
    assert result["status"] == "ok"
    assert result["pairs"], "the pair surface must be exposed for clustering"
    for p in result["pairs"]:
        assert set(p) >= {"a", "b", "cofire", "mutual", "distance", "query"}

    texts = {s: open(os.path.join(md, f"{s}.md"), encoding="utf-8").read() for s in
             ("ramjet-calibration", "ramjet-maintenance", "ramjet-alerts", "zulu")}
    props = dg.propose_generative(result, texts)
    assert len(props["schemas"]) == 1
    schema = props["schemas"][0]
    assert schema["kind"] == "schema"
    assert schema["name"].startswith("schema-")
    assert schema["children"] == ["ramjet-alerts", "ramjet-calibration", "ramjet-maintenance"]
    assert len(schema["description"]) <= 200
    for child in schema["children"]:
        assert f"[[{child}]]" in schema["body"]
    assert "Dream-drafted" in schema["body"]  # the quarantine banner
    assert "src/app.py" in schema["body"] and "src/maint.py" in schema["body"]
    assert set(schema["cited_paths"]) == {"src/app.py", "src/maint.py"}
    assert "confidence" not in schema  # the tier is staging's call, never proposal data
    assert schema["cofire"] >= 0.5


def test_proposal_name_collision_gets_suffix(dirs):
    md, td, idx = dirs
    _cluster_corpus(md)
    _seed_sessions(td, 5)
    result = dream.discover(md, idx, td)
    texts = {s: open(os.path.join(md, f"{s}.md"), encoding="utf-8").read() for s in
             ("ramjet-calibration", "ramjet-maintenance", "ramjet-alerts", "zulu")}
    props = dg.propose_generative(result, texts)
    base = props["schemas"][0]["name"]
    texts[base] = "---\nname: %s\ndescription: \"occupied\"\n---\nbody\n" % base
    props2 = dg.propose_generative(result, texts)
    assert props2["schemas"][0]["name"] == f"{base}-2"


# --------------------------------------------------------------------------- #
# Staging: draft-only, capped, stamped, ledgered, trust-folded, recallable
# --------------------------------------------------------------------------- #
def test_stage_creates_draft_with_full_provenance(dirs):
    md, td, idx = dirs
    _cluster_corpus(md)
    rows, text = _stage_one(md, td, idx)
    assert len(rows) == 1
    row = rows[0]
    stem = row["memory"]
    assert row["kind"] == "schema"
    assert row["state"] == "active"
    assert row["confidence"] == "draft"
    assert row["children"] == ["ramjet-alerts", "ramjet-calibration", "ramjet-maintenance"]
    assert row["derives_from"] == row["children"]
    assert isinstance(row.get("applied_at_distinct_count"), int)
    assert row.get("undo", {}).get("created") is True and row["undo"].get("sha256")

    path = os.path.join(md, f"{stem}.md")
    body = open(path, encoding="utf-8").read()
    fm = parse_frontmatter(body)
    # created ONLY at confidence:draft
    assert _extract_confidence(fm) == "draft"
    # derives-from typed edges up from each child, on the PARENT (zero child bytes touched)
    from memory.links import parse_typed_relations

    assert parse_typed_relations(fm).get("derives-from") == row["children"]
    # the on-disk stamp reconciles with the ledger (doctor's contract)
    assert f"edge={row['edge_id']}" in body and "<!-- dream: generated schema" in body
    # cited_paths: the union of the children's — the draft's outcome/graduation surface
    meta = fm.get("metadata") or {}
    cited = fm.get("cited_paths") or meta.get("cited_paths")
    assert set(cited) == {"src/app.py", "src/maint.py"}
    # origin provenance
    assert (meta.get("origin") or fm.get("origin") or "").startswith("dream:")
    # immediately indexed (recallable, down-weighted — the quarantine is ranking, not absence)
    entries = {e["name"] for e in load_index(idx).entries}
    assert stem in entries
    # children untouched byte-for-byte
    for child in row["children"]:
        assert "derives-from" not in open(os.path.join(md, f"{child}.md"), encoding="utf-8").read()


def test_stage_is_draft_even_when_proposal_tampered(dirs):
    """created ONLY at draft: the staging path hard-codes the tier — a proposal claiming
    confidence:verified still lands as draft."""
    md, td, idx = dirs
    _cluster_corpus(md)
    _seed_sessions(td, 5)
    result = dream.discover(md, idx, td)
    texts = {s: open(os.path.join(md, f"{s}.md"), encoding="utf-8").read()
             for s in ("ramjet-calibration", "ramjet-maintenance", "ramjet-alerts", "zulu")}
    props = dg.propose_generative(result, texts)
    props["schemas"][0]["confidence"] = "verified"  # tampered
    out = dg.stage_generated(md, props, pass_id="ptest", telemetry_dir=td, index_dir=idx)
    assert out["staged"], out["refused"]
    stem = out["staged"][0]["memory"]
    fm = parse_frontmatter(open(os.path.join(md, f"{stem}.md"), encoding="utf-8").read())
    assert _extract_confidence(fm) == "draft"


def test_stage_cap_is_enforced(dirs, monkeypatch):
    md, td, idx = dirs
    _cluster_corpus(md)
    # A second, disjoint cluster so two proposals exist
    _write_memory(md, "grill-searing", "cast iron grill searing temperature charts for brisket smoke")
    _write_memory(md, "grill-resting", "cast iron grill resting temperature charts for brisket smoke")
    _write_memory(md, "grill-rubs", "cast iron grill spice rubs temperature charts brisket smoke")
    monkeypatch.setenv("DREAM_MAX_GENERATE_PER_PASS", "1")
    _seed_sessions(td, 5)
    code, text = dg.run_generative_pass(md, idx, td, stage=True)
    assert code == 0
    assert len(dream.generated_rows(md)) == 1, text
    # the hard max is not overridable
    monkeypatch.setenv("DREAM_MAX_GENERATE_PER_PASS", "99")
    assert dg.max_generate_per_pass() == 5


def test_stage_secret_block_refuses_pre_write(dirs):
    """The ratified dream-write-path deviation: a token-shaped string anywhere in the
    generated bytes REFUSES the staging (not merely warns) — no file, no ledger row."""
    md, td, idx = dirs
    fake_token = "ghp_" + "a1B2" * 10  # a GitHub-token-shaped test vector (not a secret)
    _write_memory(
        md, "ramjet-calibration",
        f"quasar ramjet coolant telemetry calibration rotate {fake_token} lattice",
    )
    _write_memory(
        md, "ramjet-maintenance",
        f"quasar ramjet coolant telemetry maintenance rotate {fake_token} lattice",
    )
    _write_memory(
        md, "ramjet-alerts",
        f"quasar ramjet coolant telemetry alerting rotate {fake_token} lattice",
    )
    _write_memory(md, "zulu", "gardening almanac for heirloom tomato rotation beds")
    _seed_sessions(td, 5)
    files_before = set(os.listdir(md))
    code, text = dg.run_generative_pass(md, idx, td, stage=True)
    assert code == 0
    assert "secret lint BLOCK" in text
    assert dream.generated_rows(md) == []
    assert set(os.listdir(md)) == files_before


def test_staged_cluster_never_restaged_and_undone_never_auto_restaged(dirs):
    md, td, idx = dirs
    _cluster_corpus(md)
    rows, _ = _stage_one(md, td, idx)
    edge_id = rows[0]["edge_id"]

    # While the draft lives: the same cluster is refused as a duplicate.
    code, text = dg.run_generative_pass(md, idx, td, stage=True)
    assert "already staged" in text
    assert len(dream.generated_rows(md)) == 1

    # After an undo: the standing verdict holds — never auto re-staged (ping-pong guard).
    code, text = dream.undo_edges(md, idx, edge_id=edge_id)
    assert code == 0, text
    code, text = dg.run_generative_pass(md, idx, td, stage=True)
    assert "never auto re-staged" in text
    active = [r for r in dream.generated_rows(md) if r["state"] == "active"]
    assert active == []


# --------------------------------------------------------------------------- #
# The firewall extends to generative output (inv-DRM-firewall)
# --------------------------------------------------------------------------- #
def test_staged_draft_is_never_replay_source(dirs):
    md, td, idx = dirs
    _cluster_corpus(md)
    rows, _ = _stage_one(md, td, idx)
    stem = rows[0]["memory"]

    result = dream.discover(md, idx, td)
    assert result["status"] == "ok"
    assert stem in result["stats"]["draft_excluded"]
    assert stem not in result["stats"]["worklist_preview"]
    for p in result["pairs"]:
        assert stem not in (p["a"], p["b"])
    for c in result["candidates"]:
        assert stem not in (c["source"], c["target"])


def test_draft_node_never_manufactures_bridge_distance(dirs):
    """Node-level firewall: two memories connected ONLY through a draft parent's own
    wikilinks must NOT read as a 2-hop bridge — the dream-cites-a-dream tower, node form."""
    md, td, idx = dirs
    _write_memory(
        md, "alpha-notes", "quasar ramjet coolant telemetry calibration orbital lattice",
    )
    _write_memory(
        md, "charlie-notes", "quasar ramjet coolant telemetry maintenance orbital lattice",
    )
    _write_memory(md, "zulu", "gardening almanac for heirloom tomato rotation beds")
    # A hand-planted draft "parent" linking both (simulating a staged schema)
    _write_memory(
        md, "schema-fake-parent", "an abstraction over alpha and charlie",
        body="[[alpha-notes]] [[charlie-notes]]\n",
        extra_fm="  confidence: draft\n",
    )
    _seed_sessions(td, 5)
    result = dream.discover(md, idx, td)
    assert result["status"] == "ok"
    bridges = [c for c in result["candidates"] if c["kind"] == "bridge"]
    assert not any(
        {c["source"], c["target"]} == {"alpha-notes", "charlie-notes"} for c in bridges
    ), "a draft node's wikilinks manufactured a bridge distance"
    # the co-fired pair reads as DISCONNECTED through the firewalled view
    pair = next(
        (p for p in result["pairs"] if {p["a"], p["b"]} == {"alpha-notes", "charlie-notes"}),
        None,
    )
    assert pair is not None and pair["distance"] is None


def test_graduated_but_unaged_generated_memory_stays_firewalled(dirs):
    md, td, idx = dirs
    _cluster_corpus(md)
    rows, _ = _stage_one(md, td, idx)
    stem, edge_id = rows[0]["memory"], rows[0]["edge_id"]

    # Graduate by hand-appending the superseding ledger line (state stays active).
    with open(dream.apply_ledger_path(md), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"edge_id": edge_id, "state": "active", "confidence": "verified"}) + "\n")
    dg._set_confidence(os.path.join(md, f"{stem}.md"), "verified")

    # 5 distinct sessions total, staged at 5 → age 0 < DREAM_AGE_SESSIONS → firewalled.
    assert stem in dream.unaged_generated_stems(md, 5)
    result = dream.discover(md, idx, td)
    assert stem in result["stats"]["unaged_generated_firewalled"]
    for p in result["pairs"]:
        assert stem not in (p["a"], p["b"])

    # Once aged (>= window since staging), it becomes legitimate source material.
    assert stem not in dream.unaged_generated_stems(md, 10 + rows[0]["applied_at_distinct_count"])


# --------------------------------------------------------------------------- #
# Decay: external-evidence graduation + horizon expiry + archive proposal
# --------------------------------------------------------------------------- #
def _seed_outcome_hit(td, name_recalled, touched_path, sid="s9"):
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "episode_buffer.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": 100.0, "session_id": sid, "recalled_names": [name_recalled]}) + "\n")
    with open(os.path.join(td, "outcome_events.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": 150.0, "session_id": sid, "tool": "Read", "path": touched_path}) + "\n")


def test_graduation_requires_external_evidence(dirs):
    """No evidence → no graduation, ever (not even past the horizon — it expires
    instead). Evidence (the recorded outcome join) → draft→verified, ledgered."""
    md, td, idx = dirs
    _cluster_corpus(md)
    rows, _ = _stage_one(md, td, idx)
    stem = rows[0]["memory"]
    path = os.path.join(md, f"{stem}.md")

    # Sweep with NO outcome signal: still draft (no self-graduation path).
    code, text = dg.sweep_drafts(md, td, idx)
    assert code == 0
    assert "GRADUATED" not in text
    assert _extract_confidence(parse_frontmatter(open(path, encoding="utf-8").read())) == "draft"

    # An injected-then-cited-file-touched session — the EXTERNAL outcome event.
    _seed_outcome_hit(td, stem, "src/app.py")
    code, text = dg.sweep_drafts(md, td, idx)
    assert code == 0
    assert "GRADUATED" in text and stem in text
    assert _extract_confidence(parse_frontmatter(open(path, encoding="utf-8").read())) == "verified"
    row = next(r for r in dream.generated_rows(md) if r["memory"] == stem)
    assert row["state"] == "active" and row["confidence"] == "verified"
    assert row.get("evidence", {}).get("hits") == 1
    # the graduated memory does NOT expire later (the horizon governs drafts only)
    code, text = dg.sweep_drafts(md, td, idx)
    assert "EXPIRED" not in text


def test_no_self_graduation_path_exists():
    """The module-source pin: exactly ONE call site writes a confidence value, and it is
    the evidence-gated sweep branch — the generative pass cannot grade its own output."""
    import inspect

    src = inspect.getsource(dg)
    calls = re.findall(r"_set_confidence\(", src)
    # one definition + one call (the graduation branch); write_memory staging passes the
    # literal "draft" (asserted by the tampered-proposal test).
    assert len(calls) == 2, "a second _set_confidence caller would be a self-graduation path"
    assert 'confidence="draft"' in src  # staging hard-codes the tier


def test_draft_past_horizon_expires_and_proposes_archive(dirs, monkeypatch):
    md, td, idx = dirs
    _cluster_corpus(md)
    rows, _ = _stage_one(md, td, idx)  # staged at distinct_sessions=5
    stem = rows[0]["memory"]
    path = os.path.join(md, f"{stem}.md")
    monkeypatch.setenv("DREAM_DRAFT_HORIZON", "3")
    _seed_sessions(td, 9)  # distinct sessions now 9 → age 4 ≥ horizon 3

    code, text = dg.sweep_drafts(md, td, idx)
    assert code == 0
    assert "EXPIRED" in text and f"--archive-draft {stem}" in text
    fm = parse_frontmatter(open(path, encoding="utf-8").read())
    meta = fm.get("metadata") or {}
    assert fm.get("invalid_after") or meta.get("invalid_after"), "expiry must close the validity window"
    row = next(r for r in dream.generated_rows(md) if r["memory"] == stem)
    assert row.get("expired") is True and row["state"] == "active"
    assert _extract_confidence(fm) == "draft"  # expiry never grades; it decays

    # A second sweep reports the pending archive without re-expiring.
    code, text2 = dg.sweep_drafts(md, td, idx)
    assert "awaits its per-item archive" in text2 or "--archive-draft" in text2
    assert "EXPIRED" not in text2.replace("expired-awaiting-archive", "").replace(
        "expired earlier", ""
    )


def test_archive_draft_executes_one_proposal(dirs, monkeypatch):
    md, td, idx = dirs
    _cluster_corpus(md)
    rows, _ = _stage_one(md, td, idx)
    stem = rows[0]["memory"]
    monkeypatch.setenv("DREAM_DRAFT_HORIZON", "1")
    _seed_sessions(td, 9)
    dg.sweep_drafts(md, td, idx)

    # refuses non-generated names (human memories use the audit archive flow)
    res = dg.archive_draft(md, "ramjet-calibration")
    assert res["archived"] is False and res["error"]

    res = dg.archive_draft(md, stem)
    assert res["archived"] is True, res
    assert not os.path.isfile(os.path.join(md, f"{stem}.md"))
    assert os.path.isfile(os.path.join(md, "archive", f"{stem}.md"))
    row = next(r for r in dream.generated_rows(md) if r["memory"] == stem)
    assert row["state"] == "archived"

    # stamp/ledger reconciliation stays green through the whole lifecycle
    from memory.doctor import DoctorContext, check_dream_ledger

    ctx = DoctorContext(memory_dir=md, repo_root=os.path.dirname(md))
    assert check_dream_ledger(ctx)["status"] == "ok"


# --------------------------------------------------------------------------- #
# Undo: whole-file removal, byte-exact, refuse-on-drift
# --------------------------------------------------------------------------- #
def test_undo_removes_staged_file_byte_clean(dirs):
    md, td, idx = dirs
    _cluster_corpus(md)
    before = _snapshot_corpus(md)
    rows, _ = _stage_one(md, td, idx)
    code, text = dream.undo_edges(md, idx, edge_id=rows[0]["edge_id"])
    assert code == 0, text
    assert "removed" in text
    assert _snapshot_corpus(md) == before
    row = next(r for r in dream.generated_rows(md) if r["edge_id"] == rows[0]["edge_id"])
    assert row["state"] == "undone"


def test_undo_refuses_on_drift_and_after_graduation(dirs):
    md, td, idx = dirs
    _cluster_corpus(md)
    rows, _ = _stage_one(md, td, idx)
    stem, edge_id = rows[0]["memory"], rows[0]["edge_id"]
    path = os.path.join(md, f"{stem}.md")

    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\nhand-added insight worth keeping\n")
    code, text = dream.undo_edges(md, idx, edge_id=edge_id)
    assert code == 1 and "refusing" in text
    assert os.path.isfile(path)

    # graduation is drift too — evidence-earned content refuses the mechanical undo
    md2 = os.path.join(os.path.dirname(md), "mem2")
    td2 = os.path.join(os.path.dirname(md), "tele2")
    idx2 = os.path.join(os.path.dirname(md), "idx2")
    os.makedirs(md2)
    _cluster_corpus(md2)
    rows2, _ = _stage_one(md2, td2, idx2)
    _seed_outcome_hit(td2, rows2[0]["memory"], "src/app.py")
    dg.sweep_drafts(md2, td2, idx2)
    code, text = dream.undo_edges(md2, idx2, edge_id=rows2[0]["edge_id"])
    assert code == 1 and "refusing" in text
    assert os.path.isfile(os.path.join(md2, f"{rows2[0]['memory']}.md"))


# --------------------------------------------------------------------------- #
# The alarm: graduation_rate / draft-reject signal-to-noise
# --------------------------------------------------------------------------- #
def _row(state="active", confidence="draft", expired=False):
    r = {"kind": "schema", "state": state, "confidence": confidence}
    if expired:
        r["expired"] = True
    return r


def test_alarm_stats_and_hallucination_alarm():
    quiet = dg._alarm_stats([_row(), _row(confidence="verified")])
    assert quiet["staged"] == 2 and quiet["graduated"] == 1 and quiet["pending"] == 1
    assert quiet["alarm"] is False  # decided=1 < the alarm bar

    noisy = dg._alarm_stats(
        [_row(expired=True), _row(state="archived"), _row(state="undone"), _row()]
    )
    assert noisy["decided"] == 3 and noisy["graduation_rate"] == 0.0
    assert noisy["alarm"] is True

    healthy = dg._alarm_stats(
        [_row(confidence="verified"), _row(confidence="verified"), _row(expired=True)]
    )
    assert healthy["graduation_rate"] == round(2 / 3, 3)
    assert healthy["alarm"] is False


def test_sweep_report_carries_the_alarm(dirs, monkeypatch):
    md, td, idx = dirs
    _cluster_corpus(md)
    _write_memory(md, "grill-searing", "cast iron grill searing temperature charts for brisket smoke")
    _write_memory(md, "grill-resting", "cast iron grill resting temperature charts for brisket smoke")
    _write_memory(md, "grill-rubs", "cast iron grill spice rubs temperature charts brisket smoke")
    _write_memory(md, "sail-rigging", "carbon sailboat rigging tension tables for regatta trim")
    _write_memory(md, "sail-ballast", "carbon sailboat ballast tension tables for regatta trim")
    _write_memory(md, "sail-halyard", "carbon sailboat halyard tension tables for regatta trim")
    _seed_sessions(td, 5)
    code, text = dg.run_generative_pass(md, idx, td, stage=True)  # stages 2 (default cap)
    assert len(dream.generated_rows(md)) == 2, text
    monkeypatch.setenv("DREAM_DRAFT_HORIZON", "1")
    _seed_sessions(td, 9)
    code, text = dg.sweep_drafts(md, td, idx)
    # 2 expired, 0 graduated → decided 2 (below the alarm bar of 3) — rates reported quiet
    assert "graduation_rate=0.0" in text
    assert "HALLUCINATION ALARM" not in text
    # A third cluster stages on the next pass (the first two are expired → refused, not
    # re-staged) and then AGES past the horizon and expires too → decided 3 → the alarm.
    code, text = dg.run_generative_pass(md, idx, td, stage=True)
    assert len(dream.generated_rows(md)) == 3, text
    _seed_sessions(td, 11)
    code, text = dg.sweep_drafts(md, td, idx)
    assert "HALLUCINATION ALARM" in text


# --------------------------------------------------------------------------- #
# Prospective recall: the frozen backlog + abstain→hit flips
# --------------------------------------------------------------------------- #
def _seed_abstentions(td, query, n=3):
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "recall_events.jsonl"), "a", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(
                json.dumps(
                    {"session_id": f"a{i}", "backend": "none", "query_preview": query, "names": []}
                )
                + "\n"
            )


def test_prospective_recall_counts_flips_with_attribution(dirs):
    md, td, idx = dirs
    _cluster_corpus(md)
    _seed_sessions(td, 5)
    # A recurring abstention whose vocabulary matches ONLY the future schema draft
    # (its framing/name tokens), so the flip must ride the draft + its children.
    _seed_abstentions(td, "schema pattern cluster for the coolant family")
    # And one that will keep abstaining.
    _seed_abstentions(td, "underwater basket weaving certification")

    code, text = dg.run_generative_pass(md, idx, td, stage=True)
    rows = dream.generated_rows(md)
    assert rows, text

    metric = dg.prospective_recall(md, td, idx)
    assert metric["clusters"] == 2
    assert metric["frozen_at_ts"]
    per = {r["query"]: r for r in metric["per_query"]}
    flip = per["schema pattern cluster for the coolant family"]
    assert flip["hit"] is True and flip["via_dream"] is True
    assert any(h == rows[0]["memory"] for h in flip["hits"]) or set(flip["hits"]) & set(
        rows[0]["children"]
    )
    still = per["underwater basket weaving certification"]
    assert still["hit"] is False and still["via_dream"] is False
    assert metric["flips"] == 1 and metric["flips_via_dream"] == 1
    # the render is legible
    out = dg.render_prospective(metric)
    assert "abstain→hit" in out and "via-dream" in out


def test_frozen_backlog_freezes_once(dirs):
    md, td, idx = dirs
    _cluster_corpus(md)
    _seed_abstentions(td, "schema pattern cluster for the coolant family")
    first = dg.freeze_abstention_backlog(td)
    assert first["clusters"]
    _seed_abstentions(td, "a brand new abstention cluster after freezing")
    second = dg.freeze_abstention_backlog(td)
    assert second == first, "frozen means frozen — the baseline predates the staging"


def test_prospective_without_baseline_reports_itself(dirs):
    md, td, idx = dirs
    _cluster_corpus(md)
    metric = dg.prospective_recall(md, td, idx)
    assert "no frozen abstention baseline" in (metric.get("note") or "")


# --------------------------------------------------------------------------- #
# The draft answers-alone guard, end to end through the staged artifact
# --------------------------------------------------------------------------- #
def test_staged_draft_never_answers_alone_but_flips_with_children(dirs):
    from memory.recall import recall

    md, td, idx = dirs
    _cluster_corpus(md)
    rows, _ = _stage_one(md, td, idx)
    stem = rows[0]["memory"]

    # A query matching ONLY the draft's framing vocabulary: the draft seeds expansion,
    # its verified children ride in — answered, WITH non-draft support.
    res = recall("schema pattern cluster coolant family", k=6, memory_dir=md, index_dir=idx)
    names = [r["name"] for r in res]
    assert stem in names
    assert set(names) & set(rows[0]["children"]), "children must accompany the draft"

    # Sever the draft's children links: the draft alone must NOT answer (collapse to []).
    path = os.path.join(md, f"{stem}.md")
    text = open(path, encoding="utf-8").read()
    for child in rows[0]["children"]:
        text = text.replace(f"[[{child}]]", child)
    open(path, "w", encoding="utf-8").write(text)
    from memory.build_index import build_index

    build_index(md, idx)
    # A query in the draft's OWN framing vocabulary only (no child token overlap):
    # the draft is the sole match → the guard collapses the answer to abstention.
    res2 = recall("dream drafted unconfirmed shared pattern", k=6, memory_dir=md, index_dir=idx)
    assert [r for r in res2 if r.get("name") == stem] == [], "a draft must never answer alone"


# --------------------------------------------------------------------------- #
# Apply-pass integration + MCP smoke
# --------------------------------------------------------------------------- #
def test_apply_pass_with_flag_on_stages_and_flag_off_sweeps(dirs, monkeypatch):
    md, td, idx = dirs
    _cluster_corpus(md)
    _seed_sessions(td, 5)
    monkeypatch.setenv("HIPPO_DREAM_GENERATIVE", "1")
    code, digest = dream.run_apply_pass(md, idx, td)
    assert code == 0
    assert "generative pass" in digest and "STAGED" in digest
    assert len(dream.generated_rows(md)) >= 1

    # Flag off afterwards: the decay sweep still rides every apply pass (the self-decay
    # path never depends on the flag staying on).
    monkeypatch.delenv("HIPPO_DREAM_GENERATIVE", raising=False)
    code, digest = dream.run_apply_pass(md, idx, td)
    assert code == 0
    assert "dream drafts (generative tier)" in digest


def test_mcp_dream_generative_actions_smoke(dirs, monkeypatch):
    import memory.mcp_server as M

    md, td, idx = dirs
    _cluster_corpus(md)
    monkeypatch.setattr("memory.provenance.resolve_dirs", lambda: (md, os.path.dirname(md)))

    out = M._tool_dream({"action": "generate"})  # below soak → legible refusal
    assert "no proposals" in out or "proposal" in out
    out = M._tool_dream({"action": "sweep_drafts"})
    assert "dream drafts" in out
    out = M._tool_dream({"action": "prospective"})
    assert "prospective recall" in out
    out = M._tool_dream({"action": "archive_draft"})
    assert "required" in out


# --------------------------------------------------------------------------- #
# QA sweep 2026-07-16 — COR-14: the two generate-side frontmatter writers join the
# COR-9 discipline (shared insert walk + a damage check at the write site).
# --------------------------------------------------------------------------- #
def test_generate_writers_survive_non_two_space_metadata(tmp_path):
    """The insert path used to hard-code a 2-space indent, so a metadata block whose
    children indent otherwise (a hand-reformatted draft) was rewritten into a document
    that no longer parses — silently, with changed=True and no error. The writers must
    either produce a still-parsing file or refuse; never manufacture a wreck."""
    text = (
        "---\n"
        "name: schema-draft\n"
        "description: a dream-generated schema draft\n"
        "metadata:\n"
        "    type: project\n"
        "    origin: dream:p1\n"
        "---\n"
        "\n"
        "Body stays.\n"
    )

    p1 = str(tmp_path / "conf.md")
    with open(p1, "w", encoding="utf-8") as fh:
        fh.write(text)
    r = dg._set_confidence(p1, "verified")
    after = open(p1, encoding="utf-8").read()
    fm = parse_frontmatter(after)
    assert fm and fm.get("name") == "schema-draft", (
        f"_set_confidence corrupted the file (changed={r['changed']}, error={r['error']}):\n{after}"
    )
    if r["changed"]:
        meta = fm.get("metadata") or {}
        assert (fm.get("confidence") or meta.get("confidence")) == "verified"
        assert (meta or fm).get("type") == "project"  # keys it does not own survive
    assert after.endswith("Body stays.\n")  # body byte-identical

    p2 = str(tmp_path / "cited.md")
    with open(p2, "w", encoding="utf-8") as fh:
        fh.write(text)
    r2 = dg._set_cited_paths(p2, ["src/a.py"])
    after2 = open(p2, encoding="utf-8").read()
    fm2 = parse_frontmatter(after2)
    assert fm2 and fm2.get("name") == "schema-draft", (
        f"_set_cited_paths corrupted the file (changed={r2['changed']}, error={r2['error']}):\n{after2}"
    )
    if r2["changed"]:
        meta2 = fm2.get("metadata") or {}
        assert (fm2.get("cited_paths") or meta2.get("cited_paths")) == ["src/a.py"]
