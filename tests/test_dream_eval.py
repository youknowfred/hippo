"""Tests for memory/dream_eval.py — DRM-3: the HIPPO_DREAM snapshot-diff proof harness.

Pinned acceptance criteria (ROADMAP.dream.yaml DRM-3):
  - eval --ab HIPPO_DREAM runs evaluate() twice over one frozen snapshot; the OFF arm is
    asserted byte-identical to the pinned pre-dream result; the ON arm admits
    discovered-by:dream edges into _expand_neighbors; emits the paired per-category delta
    + a significance column on the multi-hop bucket;
  - a frozen fixture S0 exists with genuinely-latent A–B–C bridges (target edges verified
    ABSENT at baseline), where every multi-hop probe MISSES and each matched single-hop
    control HITS at OFF — committed BEFORE any /dream run;
  - the harness NEVER writes the live corpus / fixture, never flips a default
    (measure-only, CLI, off the hot path);
  - the attribution check confirms each converted probe entered via a discovered-by:dream
    edge and reports multi-hop-delta vs single-hop-control-delta;
  - the guardrail gate table is emitted; nothing passes unless multi-hop rises above the
    N≥5 noise floor WITH the control flat.
"""

from __future__ import annotations

import json
import os
import shutil

import memory.dream_eval as DE
from memory.links import LinkGraph

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "dream_ab")


def _snap(d):
    out = {}
    for root, _dirs, files in os.walk(d):
        for f in files:
            p = os.path.join(root, f)
            out[os.path.relpath(p, d)] = open(p, "rb").read()
    return out


# --------------------------------------------------------------------------- #
# The frozen fixture itself (committed before any /dream run)
# --------------------------------------------------------------------------- #
def test_fixture_s0_bridges_are_genuinely_latent():
    """The A–C target edges are ABSENT at baseline (GRA-3/GRW-2 never created them),
    while the A–B / B–C chain edges exist — the latent transitive-bridge shape."""
    g = LinkGraph(os.path.join(_FIXTURE, "S0"))
    for a, b, c in (
        ("sat-uplink-protocol", "sat-groundstation-map", "sat-firmware-rollback"),
        ("reef-coral-census", "reef-dive-logbook", "reef-sensor-buoys"),
    ):
        assert b in g.adjacency.get(a, set()), f"chain edge {a}→{b} must exist in S0"
        assert c in g.adjacency.get(b, set()), f"chain edge {b}→{c} must exist in S0"
        assert c not in g.undirected_neighbors(a), f"target edge {a}–{c} must be LATENT in S0"


def test_pinned_baseline_encodes_miss_hit_contract():
    """The committed pre-dream pin: every multi-hop probe MISSES, every matched
    single-hop control HITS — the DRM-3 fixture contract, byte-stable."""
    with open(os.path.join(_FIXTURE, "pinned_off.json"), encoding="utf-8") as fh:
        pinned = json.load(fh)
    probes = pinned["per_probe"]
    mh = [p for p in probes if p["category"] == "multi-hop"]
    ctl = [p for p in probes if p["category"] == "single-hop"]
    assert mh and ctl and len(ctl) >= len(mh)
    assert all(p["hit"] is False and p["rank"] is None for p in mh)
    assert all(p["hit"] is True for p in ctl)
    assert pinned["by_category"]["multi-hop"]["recall"] == 0.0
    assert pinned["by_category"]["single-hop"]["recall"] == 1.0
    assert pinned["backend"] == "bm25-only"  # determinism is pinned along with the ranking


# --------------------------------------------------------------------------- #
# The A/B end to end: PASS verdict, identity, attribution, measure-only
# --------------------------------------------------------------------------- #
def test_ab_end_to_end_passes_and_is_measure_only():
    fixture_before = _snap(_FIXTURE)
    code, text = DE.run_ab(fixture_dir=_FIXTURE, rebuilds=5)
    assert code == 0, text
    assert "RESULT: PASS" in text
    # The fixture (S0 + pin + probe sets) is byte-identical — measure-only.
    assert _snap(_FIXTURE) == fixture_before

    # Paired per-category delta + the significance column on the multi-hop bucket.
    assert "multi-hop" in text and "single-hop" in text
    assert "exact sign test" in text
    # Attribution: every converted probe names its enabling dream edge, via=graph.
    assert text.count("via=graph") >= 2
    assert "enabling dream edge(s):" in text and "NONE" not in text
    assert "multi-hop Δ" in text and "single-hop control Δ" in text
    # The gate table, including the two honest SKIP rows.
    for gate in (
        "off_arm_byte_identity", "rebuild_determinism", "multi_hop_rises", "control_flat",
        "attribution", "self_recall_floor", "zero_hit_to_miss",
        "abstention_non_decreasing", "mrr_control_floor", "warm_p95",
    ):
        assert f"✔ {gate}" in text, f"gate {gate} missing/failed:\n{text}"
    assert "net_token" in text and "SKIPPED" in text
    # Honest scoping is printed with the gates (release gates ≠ live-corpus safety proof).
    assert "NOT a live-corpus safety proof" in text


def test_off_arm_identity_drift_fails_loudly(tmp_path):
    """A pinned baseline that no longer matches the OFF arm is a loud FAIL — the
    regression tripwire (either ranking changed or the stamp filter is leaking)."""
    work = str(tmp_path / "fixture")
    shutil.copytree(_FIXTURE, work)
    pin_path = os.path.join(work, "pinned_off.json")
    pinned = json.load(open(pin_path, encoding="utf-8"))
    pinned["self_recall"] = 0.123  # tamper
    with open(pin_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(pinned, sort_keys=True, indent=1, ensure_ascii=False) + "\n")

    code, text = DE.run_ab(fixture_dir=work, rebuilds=1)
    assert code == 1
    assert "RESULT: FAIL" in text
    assert "✘ off_arm_byte_identity" in text
    assert "byte-identity FAILED" in text


def test_missing_pin_refuses_with_authoring_pointer(tmp_path):
    work = str(tmp_path / "fixture")
    shutil.copytree(_FIXTURE, work)
    os.remove(os.path.join(work, "pinned_off.json"))
    code, text = DE.run_ab(fixture_dir=work, rebuilds=1)
    assert code == 1 and "--pin" in text


def test_pin_mode_writes_the_baseline(tmp_path):
    work = str(tmp_path / "fixture")
    shutil.copytree(_FIXTURE, work)
    os.remove(os.path.join(work, "pinned_off.json"))
    code, text = DE.run_ab(fixture_dir=work, pin=True, rebuilds=1)
    assert code == 0 and "pinned the PRE-DREAM OFF baseline" in text
    # The freshly-pinned baseline matches the committed one byte-for-byte — the committed
    # pin IS reproducible from the pristine fixture (pre-dream provenance).
    fresh = open(os.path.join(work, "pinned_off.json"), "rb").read()
    committed = open(os.path.join(_FIXTURE, "pinned_off.json"), "rb").read()
    assert fresh == committed


# --------------------------------------------------------------------------- #
# The --ab whitelist front door on eval_recall
# --------------------------------------------------------------------------- #
def test_eval_cli_ab_whitelist_refuses_unknown_flags(capsys):
    """(HIPPO_SALIENCE was this test's refusal example until MSR-5 shipped its rig —
    it now dispatches to memory.salience_eval; see tests/test_salience_eval.py.)"""
    from memory.eval_recall import main as eval_main

    rc = eval_main(["--ab", "HIPPO_NONSENSE"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "whitelist" in out and "HIPPO_DREAM" in out and "HIPPO_SALIENCE" in out


def test_eval_cli_ab_dispatches_to_dream_harness(capsys):
    from memory.eval_recall import main as eval_main

    rc = eval_main(["--ab", "HIPPO_DREAM", "--fixture-dir", _FIXTURE, "--rebuilds", "1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "eval --ab HIPPO_DREAM" in out and "RESULT: PASS" in out


# --------------------------------------------------------------------------- #
# Live mode: stamped-edge measurement over a COPY, never the live tree
# --------------------------------------------------------------------------- #
def test_live_mode_measures_a_copy_and_never_writes(tmp_path, monkeypatch):
    import memory.dream as dream

    from .test_dream import _seed_sessions
    from .test_dream_apply import _snapshot_corpus

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("DREAM_COFIRE_THETA", "0.10")
    md = str(tmp_path / "mem")
    td = str(tmp_path / "tele")
    idx = str(tmp_path / "idx")
    shutil.copytree(os.path.join(_FIXTURE, "S0"), md)
    _seed_sessions(td, 5)
    code, _ = dream.run_apply_pass(md, idx, td)
    assert code == 0
    before = _snapshot_corpus(md)

    code, text = DE.run_ab(live_memory_dir=md, rebuilds=1)
    assert code == 0, text
    assert "--live" in text and "self_recall" in text
    assert "hit→miss regressions under admission: 0" in text
    assert _snapshot_corpus(md) == before, "live mode must never write the corpus"


# --------------------------------------------------------------------------- #
# The significance primitive
# --------------------------------------------------------------------------- #
def test_sign_test_exact_values():
    assert DE.sign_test_p(0, 0) is None
    assert DE.sign_test_p(2, 0) == 0.25          # P[X≥2 | n=2, p=.5]
    assert DE.sign_test_p(3, 0) == 0.125
    assert DE.sign_test_p(1, 1) == 0.75          # P[X≥1 | n=2]
    assert DE.sign_test_p(5, 0) == round(1 / 32, 6)
