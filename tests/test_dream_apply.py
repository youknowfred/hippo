"""Tests for memory/dream.py — DRM-2: Tier-A auto-apply + notify-with-undo + aging firewall.

Pinned acceptance criteria (ROADMAP.dream.yaml DRM-2 / DRM-2.spec.md §7):
  - a pass auto-applies ONLY Tier-A candidates above θ, capped; supersedes→digest-gated,
    contradicts→routed to /resolve, schemas→never emitted; NO prose byte changes outside
    the dream:links block and NO frontmatter change outside the target refines list;
  - below the soak bar / a floor target → zero edges, said aloud;
  - every applied edge has a complete dream-ledger.jsonl line + an on-disk stamp; the
    doctor check fails loudly on any stamp/ledger mismatch;
  - dream --undo restores the pre-pass working tree BYTE-FOR-BYTE and rebuilds the index;
    --undo <id> reverts exactly one; both refuse-with-report on manual drift;
  - aging firewall — a not-yet-aged dream edge is provably ABSENT from the next pass's
    candidate SOURCE set and PRESENT after ≥DREAM_AGE_SESSIONS; an edge undone before
    aging never entered the source set;
  - secret-BLOCK — a candidate whose rationale would emit a token-shaped string is
    REFUSED pre-write (not merely warned);
  - _tool_dream returns the digest; dream_applied_producer surfaces not-yet-aged edges
    within budget and DROPS aged-in ones.

The shipped DEFAULT stays report-only (apply_mode_default) — asserted here so it cannot
drift without the dated owner decision the roadmap requires.
"""

from __future__ import annotations

import json
import os

import pytest

import memory.dream as dream
from memory.links import load_edges

from .test_dream import _bridge_corpus, _seed_sessions, _snapshot_md, _write_memory


def _snapshot_corpus(md):
    """The zero/undo byte oracle over MEMORY FILES. ``dream-ledger.jsonl`` is excluded by
    design: the audit ledger is append-only (an undo appends a superseding line, it never
    rewrites history), so 'restores the pre-pass working tree byte-for-byte' is a claim
    about the memories, and the ledger's state transitions are asserted separately."""
    snap = _snapshot_md(md)
    snap.pop("dream-ledger.jsonl", None)
    return snap


@pytest.fixture(autouse=True)
def _bm25_only_and_permissive_theta(monkeypatch):
    """Deterministic ranking + a low θ: these tests exercise the apply MECHANICS; the
    calibrated θ value itself is pinned by test_dream.test_apply_eligible_calibrated_bar."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("DREAM_COFIRE_THETA", "0.10")


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    md = str(tmp_path / "mem")
    td = str(tmp_path / "tele")
    idx = str(tmp_path / "idx")
    os.makedirs(md)
    # Home every derived artifact the apply path touches under the tmp tree.
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    return md, td, idx


def _apply(md, idx, td, **kw):
    return dream.run_apply_pass(md, idx, td, **kw)


# --------------------------------------------------------------------------- #
# The shipped default is Tier-A auto-apply — flipped by the DATED owner decision
# 2026-07-12 (ROADMAP.dream.yaml owner_decisions item 5) consuming the DRM-1
# calibration. Changing it again requires a new dated entry there.
# --------------------------------------------------------------------------- #
def test_shipped_default_is_auto_apply(monkeypatch):
    monkeypatch.delenv("HIPPO_DREAM_APPLY", raising=False)
    assert dream.apply_mode_default() is True
    monkeypatch.setenv("HIPPO_DREAM_APPLY", "0")
    assert dream.apply_mode_default() is False  # the explicit report-only opt-out
    monkeypatch.setenv("HIPPO_DREAM_APPLY", "1")
    assert dream.apply_mode_default() is True


# --------------------------------------------------------------------------- #
# Apply: Tier-A only, above θ, capped; additive bytes only
# --------------------------------------------------------------------------- #
def test_apply_writes_stamped_edge_ledger_and_only_additive_bytes(dirs):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    before = _snapshot_corpus(md)

    code, digest = _apply(md, idx, td)
    assert code == 0
    assert "applied" in digest and "uncommitted, live in recall" in digest

    after = _snapshot_corpus(md)
    changed = [f for f in after if after[f] != before.get(f)]
    assert changed, "an eligible bridge existed — something must have been applied"
    # NO prose byte changes outside the dream:links block: every changed file's new bytes
    # are exactly its old bytes + one appended, delimited block (bridges/completions).
    for fname in changed:
        old, new = before[fname], after[fname]
        assert new.startswith(old), f"{fname}: prose above the block was perturbed"
        tail = new[len(old):].decode("utf-8")
        assert tail.startswith("<!-- dream:links -->\n") or tail.startswith("\n<!-- dream:links -->\n")
        assert tail.rstrip("\n").endswith("<!-- /dream:links -->")
        assert "edge=" in tail and "pass=" in tail  # the grep-able stamp

    # Every applied edge has a COMPLETE ledger line.
    ledger = dream.read_apply_ledger(md)
    assert ledger
    for e in ledger:
        for field in (
            "edge_id", "pass", "kind", "source", "target", "cofire", "derives_from",
            "applied_at_distinct_count", "state", "undo",
        ):
            assert field in e, f"ledger line missing {field}"
        assert e["state"] == "active"
        assert e["derives_from"] == [e["source"], e["target"]]

    # The edge is LIVE in recall's graph: the index rebuild refreshed links.json.
    edges = load_edges(idx)
    assert edges is not None
    srcs = {(e["source"], e["target"]) for e in ledger if e["kind"] in ("bridge", "completion")}
    for src, tgt in srcs:
        assert tgt in edges[src]["out"], "applied edge must be in the rebuilt edge cache"


def test_apply_cap_bounds_the_pass(dirs, monkeypatch):
    md, td, idx = dirs
    _bridge_corpus(md)
    # A second, disjoint latent bridge so ≥2 candidates are eligible.
    _write_memory(md, "delta", "krill photophore migration acoustics baltic survey",
                  body="See [[echo-node]].\n")
    _write_memory(md, "echo-node", "photophore acoustics relay between krill surveys",
                  body="See [[foxtrot-node]].\n")
    _write_memory(md, "foxtrot-node", "krill photophore migration acoustics survey ledger")
    _seed_sessions(td, 5)
    monkeypatch.setenv("DREAM_MAX_APPLY_PER_PASS", "1")
    code, digest = _apply(md, idx, td)
    assert code == 0
    assert len([e for e in dream.read_apply_ledger(md) if e["state"] == "active"]) == 1


def test_hard_max_cap_is_nine(monkeypatch):
    monkeypatch.setenv("DREAM_MAX_APPLY_PER_PASS", "50")
    assert dream.max_apply_per_pass() == 9


def test_refines_apply_touches_only_refines_frontmatter(dirs, monkeypatch):
    md, td, idx = dirs
    _write_memory(md, "deploy-runbook", "deploy runbook for the staging cutover fleet rollout",
                  body="The parent runbook.\n")
    _write_memory(md, "deploy-runbook-rollback",
                  "deploy runbook rollback drill for the staging cutover fleet",
                  body="The child drill.\n")
    _write_memory(md, "india", "greenhouse irrigation drip schedule")
    _seed_sessions(td, 5)
    before = _snapshot_corpus(md)
    code, digest = _apply(md, idx, td)
    assert code == 0

    child = os.path.join(md, "deploy-runbook-rollback.md")
    text = open(child, encoding="utf-8").read()
    assert 'refines: ["deploy-runbook"]' in text  # additive frontmatter, canonical flow list
    # The body prose between the frontmatter and the appended block is untouched.
    assert "The child drill.\n" in text
    assert "<!-- dream: refines deploy-runbook" in text  # the stamp (bracket-free comment)
    # No OTHER file changed at all.
    after = _snapshot_corpus(md)
    unchanged = [f for f in after if f != "deploy-runbook-rollback.md"]
    for f in unchanged:
        assert after[f] == before[f]
    # And no frontmatter key other than refines was added/removed in the child.
    fm_before = before["deploy-runbook-rollback.md"].decode().split("---")[1]
    fm_after = after["deploy-runbook-rollback.md"].decode().split("---")[1]
    assert fm_after.replace('  refines: ["deploy-runbook"]\n', "") == fm_before


# --------------------------------------------------------------------------- #
# Tier routing: supersedes → gated, contradicts → /resolve, junk kinds → refused
# --------------------------------------------------------------------------- #
def _stub_discover(monkeypatch, md, candidates):
    """Feed the apply loop a crafted candidate stream (the seam for Tier-C routing and
    secret-BLOCK tests — the report generator itself never emits these kinds)."""
    def fake(memory_dir, index_dir=None, telemetry_dir=None, **kw):
        return {
            "status": "ok",
            "reason": "",
            "pass_id": "ptest",
            "candidates": candidates,
            "stats": {},
            "soak": {"distinct_sessions": 7, "gate_met": True},
        }
    monkeypatch.setattr(dream, "discover", fake)


def test_supersedes_gated_contradicts_routed_never_applied(dirs, monkeypatch):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    before = _snapshot_md(md)
    _stub_discover(monkeypatch, md, [
        {"kind": "supersedes", "source": "alpha", "target": "charlie", "cofire": 0.99,
         "query": "q", "mutual": True, "signal": "x", "distance": 2},
        {"kind": "contradicts", "source": "bravo", "target": "charlie", "cofire": 0.99,
         "query": "q", "mutual": True, "signal": "x", "distance": 2},
        {"kind": "schema", "source": "alpha", "target": "bravo", "cofire": 0.99,
         "query": "q", "mutual": True, "signal": "x", "distance": 2},
    ])
    code, digest = _apply(md, idx, td)
    assert code == 0
    assert "supersedes candidate(s) GATED" in digest
    assert "routed to /hippo:resolve" in digest
    assert _snapshot_md(md) == before, "Tier-B/C kinds must never touch the working tree"
    assert dream.read_apply_ledger(md) == []


# --------------------------------------------------------------------------- #
# Legible refusals: below soak / untrusted corpus
# --------------------------------------------------------------------------- #
def test_apply_below_soak_bar_applies_zero_and_says_so(dirs):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 3)
    before = _snapshot_md(md)
    code, digest = _apply(md, idx, td)
    assert code == 0
    assert "below the curation-soak bar" in digest
    assert _snapshot_md(md) == before
    assert dream.read_apply_ledger(md) == []


def test_apply_refuses_on_untrusted_corpus(dirs, monkeypatch):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    # Drop the suite-wide trust bypass: the corpus lives outside any trusted registry.
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    monkeypatch.setattr("memory.trust.gate_repo_root", lambda *a, **k: "/fake/root")
    monkeypatch.setattr("memory.trust.is_trusted", lambda *a, **k: False)
    before = _snapshot_md(md)
    code, digest = _apply(md, idx, td)
    assert code == 1
    assert "APPLY REFUSED" in digest and "untrusted" in digest
    assert _snapshot_md(md) == before


# --------------------------------------------------------------------------- #
# Secret lint: HARD BLOCK on the dream write path (ratified deviation)
# --------------------------------------------------------------------------- #
def test_secret_shaped_rationale_is_refused_pre_write(dirs, monkeypatch):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    before = _snapshot_md(md)
    fake_token = "ghp_" + "a1B2" * 10  # a GitHub-token-shaped test vector (not a secret)
    _stub_discover(monkeypatch, md, [
        {"kind": "completion", "source": "alpha", "target": "charlie", "cofire": 0.99,
         "query": f"rotate {fake_token} now", "mutual": True, "signal": "body-mention",
         "distance": 2},
    ])
    code, digest = _apply(md, idx, td)
    assert code == 0
    assert "secret lint BLOCK" in digest and "refused" in digest
    # REFUSED pre-write: nothing on disk, nothing in the ledger — not merely warned.
    assert _snapshot_md(md) == before
    assert dream.read_apply_ledger(md) == []


# --------------------------------------------------------------------------- #
# Undo: byte-for-byte, one-edge, refuse-on-drift, index rebuild
# --------------------------------------------------------------------------- #
def test_undo_restores_pre_pass_tree_byte_for_byte(dirs):
    md, td, idx = dirs
    _bridge_corpus(md)
    _write_memory(md, "deploy-runbook", "deploy runbook for the staging cutover fleet rollout")
    _write_memory(md, "deploy-runbook-rollback",
                  "deploy runbook rollback drill for the staging cutover fleet")
    _seed_sessions(td, 5)
    before = _snapshot_corpus(md)
    code, _ = _apply(md, idx, td)
    assert code == 0
    applied = [e for e in dream.read_apply_ledger(md) if e["state"] == "active"]
    assert applied
    assert _snapshot_corpus(md) != before

    code, report = dream.undo_edges(md, idx)
    assert code == 0
    assert f"reverted {len(applied)} edge(s)" in report
    assert _snapshot_corpus(md) == before, "undo must restore the pre-pass tree BYTE-FOR-BYTE"
    # Ledger is append-only: the history is intact, the current state is undone.
    assert all(e["state"] == "undone" for e in dream.read_apply_ledger(md))
    # The index rebuild dropped the edges from the live cache.
    edges = load_edges(idx)
    for e in applied:
        if e["kind"] in ("bridge", "completion"):
            assert e["target"] not in edges[e["source"]]["out"]


def test_undo_single_edge_reverts_exactly_one(dirs, monkeypatch):
    md, td, idx = dirs
    _bridge_corpus(md)
    _write_memory(md, "delta", "krill photophore migration acoustics baltic survey",
                  body="See [[echo-node]].\n")
    _write_memory(md, "echo-node", "photophore acoustics relay between krill surveys",
                  body="See [[foxtrot-node]].\n")
    _write_memory(md, "foxtrot-node", "krill photophore migration acoustics survey ledger")
    _seed_sessions(td, 5)
    code, _ = _apply(md, idx, td)
    assert code == 0
    active = [e for e in dream.read_apply_ledger(md) if e["state"] == "active"]
    assert len(active) >= 2
    victim = active[0]

    code, report = dream.undo_edges(md, idx, edge_id=victim["edge_id"])
    assert code == 0 and victim["edge_id"] in report
    now = dream.read_apply_ledger(md)
    assert [e["edge_id"] for e in now if e["state"] == "undone"] == [victim["edge_id"]]
    assert len([e for e in now if e["state"] == "active"]) == len(active) - 1


def test_undo_refuses_on_manual_drift(dirs):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    code, _ = _apply(md, idx, td)
    assert code == 0
    active = [e for e in dream.read_apply_ledger(md) if e["state"] == "active"]
    edge = active[0]
    path = os.path.join(md, edge["undo"]["file"])
    text = open(path, encoding="utf-8").read()
    drifted = text.replace(f"edge={edge['edge_id']}", f"edge={edge['edge_id']}-EDITED")
    assert drifted != text
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(drifted)

    code, report = dream.undo_edges(md, idx, edge_id=edge["edge_id"])
    assert code == 1
    assert "drift" in report and "refusing" in report.lower() or "refus" in report
    # The hand-edited file is untouched, and the edge is still ledger-active.
    assert open(path, encoding="utf-8").read() == drifted
    assert any(
        e["edge_id"] == edge["edge_id"] and e["state"] == "active"
        for e in dream.read_apply_ledger(md)
    )


def test_undo_with_nothing_to_do_says_so(dirs):
    md, td, idx = dirs
    _bridge_corpus(md)
    code, report = dream.undo_edges(md, idx)
    assert code == 0 and "no active dream edges" in report


# --------------------------------------------------------------------------- #
# The aging firewall (inv-DRM-firewall) — the keystone guardrail, end to end
# --------------------------------------------------------------------------- #
def _firewall_corpus(md):
    """alpha–bravo–charlie chain plus delta linked FROM charlie; alpha co-fires with both
    charlie (the latent bridge) and delta (reachable only THROUGH a dream edge later)."""
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
        body="Maintenance schedule.\n\nDrills tracked in [[delta]].\n",
    )
    _write_memory(
        md, "delta", "quasar ramjet coolant telemetry drill roster orbital lattice",
        body="Drill roster.\n",
    )
    _write_memory(md, "zulu", "gardening almanac for heirloom tomato rotation beds")


def test_aging_firewall_end_to_end(dirs, monkeypatch):
    md, td, idx = dirs
    _firewall_corpus(md)
    _seed_sessions(td, 5)
    monkeypatch.setenv("DREAM_AGE_SESSIONS", "5")
    monkeypatch.setenv("DREAM_MAX_APPLY_PER_PASS", "1")  # apply ONLY the top bridge (alpha–charlie)

    # Pass 1 at 5 sessions: the alpha–charlie bridge applies (distance 2 via bravo).
    code, digest = _apply(md, idx, td)
    assert code == 0
    active = [e for e in dream.read_apply_ledger(md) if e["state"] == "active"]
    assert len(active) == 1
    edge = active[0]
    assert {edge["source"], edge["target"]} == {"alpha", "charlie"}
    assert edge["applied_at_distinct_count"] == 5

    # NEXT pass, still within the window (5 sessions later would age it; we're at +0):
    # the un-aged alpha–charlie edge is ABSENT from the source graph, so alpha–delta is
    # NOT at distance 2 (it is 3 via bravo–charlie) — no bridge through the dream edge.
    result = dream.discover(md, idx, td)
    assert result["status"] == "ok"
    assert result["stats"]["unaged_dream_pairs_firewalled"] == 1
    assert not [
        c for c in result["candidates"] if {c["source"], c["target"]} == {"alpha", "delta"}
    ], "a not-yet-aged dream edge must be invisible to the next pass's source set"

    # After ≥DREAM_AGE_SESSIONS distinct sessions, the edge ages in and JOINS the source
    # set: alpha–delta is now distance 2 through it → the bridge candidate appears.
    _seed_sessions(td, 10)
    result = dream.discover(md, idx, td)
    assert result["status"] == "ok"
    assert result["stats"]["unaged_dream_pairs_firewalled"] == 0
    assert [
        c for c in result["candidates"] if {c["source"], c["target"]} == {"alpha", "delta"}
    ], "an aged-in dream edge is trusted source (implicit ratification by non-undo)"


def test_edge_undone_before_aging_never_enters_source_set(dirs, monkeypatch):
    md, td, idx = dirs
    _firewall_corpus(md)
    _seed_sessions(td, 5)
    monkeypatch.setenv("DREAM_AGE_SESSIONS", "5")
    monkeypatch.setenv("DREAM_MAX_APPLY_PER_PASS", "1")
    code, _ = _apply(md, idx, td)
    assert code == 0
    code, _ = dream.undo_edges(md, idx)
    assert code == 0
    # Sessions roll past the window; the UNDONE edge still never becomes source.
    _seed_sessions(td, 10)
    result = dream.discover(md, idx, td)
    assert result["status"] == "ok"
    assert not [
        c for c in result["candidates"] if {c["source"], c["target"]} == {"alpha", "delta"}
    ], "an edge undone before aging must never enter the source set"


# --------------------------------------------------------------------------- #
# Doctor reconciliation: stamps ↔ ledger, loud on mismatch
# --------------------------------------------------------------------------- #
def test_doctor_dream_ledger_reconciles_and_fails_loudly(dirs, repo):
    md, td, idx = dirs
    from memory.doctor import DoctorContext, check_dream_ledger

    _bridge_corpus(md)
    _seed_sessions(td, 5)
    ctx = DoctorContext(md, repo)

    # Nothing applied → quiet ok.
    assert check_dream_ledger(ctx)["status"] == "ok"

    code, _ = _apply(md, idx, td)
    assert code == 0
    res = check_dream_ledger(ctx)
    assert res["status"] == "ok" and "reconcile" in res["message"]

    # Hand-delete the stamped line (instead of dream --undo) → active ledger edge with no
    # stamp → FAIL, loudly.
    edge = [e for e in dream.read_apply_ledger(md) if e["state"] == "active"][0]
    path = os.path.join(md, edge["undo"]["file"])
    text = open(path, encoding="utf-8").read()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace(edge["undo"]["block"]["inserted"], "", 1))
    res = check_dream_ledger(ctx)
    assert res["status"] == "fail" and "MISMATCH" in res["message"]
    assert edge["edge_id"] in res["message"]


# --------------------------------------------------------------------------- #
# Notify surfaces: _tool_dream digest + the SessionStart producer
# --------------------------------------------------------------------------- #
def test_tool_dream_returns_digest(dirs, monkeypatch):
    md, td, idx = dirs
    import memory.mcp_server as M

    _bridge_corpus(md)
    _seed_sessions(td, 5)
    monkeypatch.setattr("memory.provenance.resolve_dirs", lambda: (md, os.path.dirname(md)))

    # apply: false is the explicit report-only override (zero writes).
    before = _snapshot_corpus(md)
    out = M._tool_dream({"action": "pass", "apply": False})
    assert "dream pass" in out and "REPORT-ONLY" in out
    assert _snapshot_corpus(md) == before

    # A bare pass follows the shipped default: auto-apply (owner flip 2026-07-12).
    out = M._tool_dream({"action": "pass"})
    assert "applied" in out

    out = M._tool_dream({"action": "log"})
    assert "dream --log" in out

    out = M._tool_dream({"action": "undo"})
    assert "reverted" in out


def test_dream_applied_producer_surfaces_unaged_and_drops_aged(dirs, monkeypatch):
    md, td, idx = dirs
    os.makedirs(os.path.join(md), exist_ok=True)
    _seed_sessions(td, 8)
    monkeypatch.setenv("DREAM_AGE_SESSIONS", "5")
    rows = [
        {"edge_id": "p1-e1", "pass": "p1", "kind": "bridge", "source": "a", "target": "b",
         "cofire": 0.9, "state": "active", "applied_at_distinct_count": 6,
         "undo": {"file": "a.md", "block": {"inserted": "x\n"}}},
        {"edge_id": "p1-e2", "pass": "p1", "kind": "bridge", "source": "c", "target": "d",
         "cofire": 0.9, "state": "active", "applied_at_distinct_count": 1,  # aged in (8-1 ≥ 5)
         "undo": {"file": "c.md", "block": {"inserted": "y\n"}}},
        {"edge_id": "p1-e3", "pass": "p1", "kind": "bridge", "source": "e", "target": "f",
         "cofire": 0.9, "state": "active", "applied_at_distinct_count": 6,
         "undo": {"file": "e.md", "block": {"inserted": "z\n"}}},
        {"edge_id": "p1-e3", "state": "undone"},
    ]
    with open(dream.apply_ledger_path(md), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    out = dream.dream_applied_producer(md, os.path.dirname(md))
    assert out is not None
    assert "p1-e1" in out            # un-aged → surfaced with the undo handle
    assert "p1-e2" not in out        # aged in → dropped (now trusted)
    assert "p1-e3" not in out        # undone → gone
    assert "--undo" in out

    # All aged → silent (None), like every quiet-by-default producer.
    with open(dream.apply_ledger_path(md), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rows[1]) + "\n")
    assert dream.dream_applied_producer(md, os.path.dirname(md)) is None


def test_dream_applied_producer_registered_in_session_start():
    import memory.session_start as S

    labels = [label for label, _fn in S.PRODUCERS]
    assert labels.count("dream_applied") == 1
    fns = [fn for label, fn in S.PRODUCERS if label == "dream_applied"]
    assert fns == [dream.dream_applied_producer]


# --------------------------------------------------------------------------- #
# CLI surface for the DRM-2 verbs
# --------------------------------------------------------------------------- #
def test_cli_apply_undo_log_roundtrip(dirs, capsys):
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    before = _snapshot_corpus(md)

    rc = dream.main(["--memory-dir", md, "--telemetry-dir", td, "--index-dir", idx, "--apply"])
    assert rc == 0
    assert "applied" in capsys.readouterr().out
    assert _snapshot_corpus(md) != before

    rc = dream.main(["--memory-dir", md, "--log"])
    assert rc == 0 and "dream --log" in capsys.readouterr().out

    rc = dream.main(["--memory-dir", md, "--index-dir", idx, "--undo"])
    assert rc == 0
    assert "reverted" in capsys.readouterr().out
    assert _snapshot_corpus(md) == before


# --------------------------------------------------------------------------- #
# SEC-6 fold: applied/undone files re-enter the consent baseline (DRM-6 hardening)
# --------------------------------------------------------------------------- #
def test_apply_and_undo_fold_written_files_into_trust_baseline(dirs, monkeypatch):
    """A fingerprinted corpus quarantines new-since-consent bytes out of recall — so the
    apply pass must fold every stamped file (and undo must re-fold the restoration), or
    'live in recall immediately' silently breaks on any SEC-6-baselined corpus."""
    md, td, idx = dirs
    _bridge_corpus(md)
    _seed_sessions(td, 5)
    calls = []
    monkeypatch.setattr(
        "memory.trust.record_authored_write",
        lambda memory_dir, path, repo_root=None: (calls.append(os.path.basename(path)), True)[1],
    )

    code, digest = dream.run_apply_pass(md, idx, td)
    assert code == 0 and "applied" in digest
    applied_files = {
        e["source"] + ".md"
        for e in dream.read_apply_ledger(md)
        if e.get("state") == "active"
    }
    assert applied_files, "the bridge corpus must apply at least one edge"
    assert applied_files <= set(calls), f"unfolded applied files: {applied_files - set(calls)}"

    calls.clear()
    code, text = dream.undo_edges(md, idx)
    assert code == 0, text
    assert applied_files <= set(calls), f"unfolded restored files: {applied_files - set(calls)}"


# --------------------------------------------------------------------------- #
# QA sweep 2026-07-16 — COR-16: the refines two-write chain honors its own
# per-edge-atomicity contract.
# --------------------------------------------------------------------------- #
def test_apply_refines_rolls_back_frontmatter_edge_when_stamp_write_fails(dirs, monkeypatch):
    """The refines kind writes twice (frontmatter edge, then the dream:links stamp).
    A failure on write #2 used to strand the frontmatter edge with NO ledger row: undo
    could not see it and the idempotency guard refused every retry — a permanent,
    untracked edge on a pass that reported 'refused'. The file must come back
    byte-identical."""
    import builtins

    md, td, idx = dirs
    _write_memory(md, "narrow-lesson", "the narrow one", body="Body n.\n")
    _write_memory(md, "broad-lesson", "the broad one", body="Body b.\n")
    before = open(os.path.join(md, "narrow-lesson.md"), encoding="utf-8").read()

    cand = {"kind": "refines", "source": "narrow-lesson", "target": "broad-lesson",
            "score": 0.9}
    real_open = builtins.open
    w_count = {"n": 0}

    def failing_open(path, mode="r", *a, **k):
        if str(path).endswith("narrow-lesson.md") and "w" in str(mode):
            w_count["n"] += 1
            if w_count["n"] == 2:  # write #1 = the typed edge; write #2 = the stamp;
                raise OSError(28, "No space left on device")  # write #3 = the rollback
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", failing_open)
    ok, reason, undo = dream._apply_one(md, cand, "edge-test-1", "pass-test-1")
    monkeypatch.undo()

    assert ok is False and undo is None
    assert "rolled back" in reason
    after = open(os.path.join(md, "narrow-lesson.md"), encoding="utf-8").read()
    assert after == before, (
        "a refused refines apply must leave the source byte-identical — found a "
        "stranded frontmatter edge"
    )
