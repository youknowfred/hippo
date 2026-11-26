"""HCR-3: the high profile's calibration is visible — the floor sweep can run inside the
resolved high embedding space, and doctor reads that space against its own table entries.

The big model is faked (``test_embed_mlx``'s stand-ins), so this runs on Linux CI. The
numbers the tables hold come from real-model runs; ``test_high_space_calibration`` pins those.
"""

from __future__ import annotations

import json
import os

import pytest

from memory import build_index as B
from memory import doctor as D
from memory import embed_mlx as E
from memory import eval_floor as F
from memory import eval_recall
from memory import recall_abstain, recall_count
from memory import recall_rank as RR
from memory import recall_warm_high as H

from .test_doctor import _ctx
from .test_embed_mlx import _MEMS, fakes, project  # noqa: F401  (fixtures)

FOUR_B = "Qwen/Qwen3-Embedding-4B"
EIGHT_B = "Qwen/Qwen3-Embedding-8B"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("HIPPO_DENSE_FLOOR", "HIPPO_COUNT_BAND", "HIPPO_COUNT_WIDE", E.MODEL_ENV, E.DIM_ENV,
                "HIPPO_RETRIEVAL_PROFILE", "HIPPO_TELEMETRY_DIR"):
        monkeypatch.delenv(var, raising=False)


def _high(monkeypatch, tmp_path, model=None, dim=None):
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "telemetry"))
    if model:
        monkeypatch.setenv(E.MODEL_ENV, model)
    if dim:
        monkeypatch.setenv(E.DIM_ENV, str(dim))


def _write_high_sweep(memory_dir, **over):
    doc = {"ok": True, "schema": F._FLOOR_SWEEP_SCHEMA, "model": FOUR_B, "configured_floor": 0.42,
           "corpus_fingerprint": "fp", "generated_at": "2026-11-25", "recommended": 0.43,
           "overlap": False, "on_n": 6, "off_n": 3, "on_min": 0.5, "off_max": 0.36,
           "safety_delta": 0.07, "leaked_off": 0, "cut_on": 0}
    doc.update(over)
    path = F.default_floor_sweep_path(memory_dir, high=True)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    return path


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #
def test_no_line_off_the_high_profile(repo, memory_dir):
    assert D.check_high_space_calibration(_ctx(memory_dir, repo)) is None
    assert ("high_space_calibration", D.check_high_space_calibration) in D.CHECKS


def test_the_8b_reads_as_calibrated(repo, memory_dir, monkeypatch, tmp_path):
    _high(monkeypatch, tmp_path, model=EIGHT_B)
    r = D.check_high_space_calibration(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and f"[{EIGHT_B}]" in r["message"]
    assert f"floor {RR._DENSE_FLOOR_BY_MODEL[EIGHT_B]}" in r["message"] and "row-count band" in r["message"]
    _high(monkeypatch, tmp_path, model=EIGHT_B, dim=2048)
    r = D.check_high_space_calibration(_ctx(memory_dir, repo))
    assert r["status"] == "warn" and f"[{EIGHT_B}@2048]" in r["message"] and "NOT CALIBRATED" in r["message"]
    assert EIGHT_B in r["message"].split("Calibrated high space(s):")[1]


def test_a_calibrated_space_without_a_sweep_names_its_numbers(repo, memory_dir, monkeypatch, tmp_path):
    _high(monkeypatch, tmp_path)
    r = D.check_high_space_calibration(_ctx(memory_dir, repo))
    gate = recall_abstain._COSINE_BY_MODEL[FOUR_B]
    assert r["status"] == "ok"
    assert f"[{FOUR_B}]" in r["message"]
    assert f"floor {RR._DENSE_FLOOR_BY_MODEL[FOUR_B]}" in r["message"]
    assert f"gate {gate['strong']}/{gate['support']}" in r["message"]
    assert "hippo eval --floor-sweep" in r["message"] and "\n" not in r["message"]


def test_a_truncated_width_is_warned_by_name(repo, memory_dir, monkeypatch, tmp_path):
    _high(monkeypatch, tmp_path, dim=1024)
    r = D.check_high_space_calibration(_ctx(memory_dir, repo))
    assert r["status"] == "warn"
    assert f"[{FOUR_B}@1024]" in r["message"] and "NOT CALIBRATED" in r["message"]
    assert "1024-wide truncation" in r["message"]
    assert f"default dense floor {RR._DENSE_FLOOR_DEFAULT}" in r["message"]
    assert "no corroboration gate" in r["message"]
    assert FOUR_B in r["message"].split("Calibrated high space(s):")[1]  # the full width is offered
    assert E.DIM_ENV in r["message"] and "\n" not in r["message"]
    # the bench kept the full width, so the advice is to drop the truncation, not to pick a width
    assert f"the full width is the measured choice (unset {E.DIM_ENV}" in r["message"]
    assert E.MODEL_ENV not in r["message"]


def test_a_model_with_no_entries_is_warned_by_name(repo, memory_dir, monkeypatch, tmp_path):
    # Whatever the tables hold for the 8B today, the mechanism is: no floor or gate entry, warn.
    for table in (RR._DENSE_FLOOR_BY_MODEL, recall_abstain._COSINE_BY_MODEL, recall_count._COUNT_BY_MODEL):
        monkeypatch.delitem(table, EIGHT_B, raising=False)
    _high(monkeypatch, tmp_path, model=EIGHT_B)
    r = D.check_high_space_calibration(_ctx(memory_dir, repo))
    assert r["status"] == "warn"
    assert f"[{EIGHT_B}]" in r["message"] and "NOT CALIBRATED" in r["message"]
    assert "has not been measured" in r["message"]
    assert f"choose one with {E.MODEL_ENV} / {E.DIM_ENV}" in r["message"]
    assert "the full width is the measured choice" not in r["message"]
    assert "up to k rows" in r["message"]


def test_a_floor_without_a_gate_still_warns(repo, memory_dir, monkeypatch, tmp_path):
    monkeypatch.setitem(RR._DENSE_FLOOR_BY_MODEL, EIGHT_B, 0.4)
    monkeypatch.delitem(recall_abstain._COSINE_BY_MODEL, EIGHT_B, raising=False)
    _high(monkeypatch, tmp_path, model=EIGHT_B)
    r = D.check_high_space_calibration(_ctx(memory_dir, repo))
    assert r["status"] == "warn" and "NOT CALIBRATED — no corroboration gate" in r["message"]
    assert "default dense floor" not in r["message"]


def test_a_sweep_from_another_space_is_stale_not_compared(repo, memory_dir, monkeypatch, tmp_path):
    _high(monkeypatch, tmp_path)
    _write_high_sweep(memory_dir, model=f"{FOUR_B}@1024")
    r = D.check_high_space_calibration(_ctx(memory_dir, repo))
    assert r["status"] == "warn" and "STALE" in r["message"]
    assert f"{FOUR_B}@1024" in r["message"] and "hippo eval --floor-sweep" in r["message"]


def test_a_high_sweep_is_compared_with_its_own_floor(repo, memory_dir, monkeypatch, tmp_path):
    _high(monkeypatch, tmp_path)
    # the floor sits on the low side of a clean gap on purpose: not the midpoint, still ok
    _write_high_sweep(memory_dir, recommended=0.473, off_max=0.3284, on_min=0.6177)
    r = D.check_high_space_calibration(_ctx(memory_dir, repo))
    assert r["status"] == "ok", r
    assert "configured 0.42 sits inside this corpus's clean gap" in r["message"]
    assert "0.3284" in r["message"] and "0.6177" in r["message"] and "0.473" in r["message"]
    assert "STALE" not in r["message"]
    # a probe above the floor: the floor is outside the gap, so both numbers and the remedy
    _write_high_sweep(memory_dir, recommended=0.5, off_max=0.45, on_min=0.55)
    r = D.check_high_space_calibration(_ctx(memory_dir, repo))
    assert r["status"] == "warn" and "configured 0.42 vs sweep-recommended 0.5" in r["message"]
    # no clean gap at all: never the in-gap reading
    _write_high_sweep(memory_dir, recommended=0.43, off_max=0.3, on_min=0.6, overlap=True)
    r = D.check_high_space_calibration(_ctx(memory_dir, repo))
    assert "configured 0.42 ≈ recommended 0.43" in r["message"] and "clean gap" not in r["message"]


def test_the_lean_line_keeps_comparing_with_the_midpoint(repo, memory_dir, monkeypatch, tmp_path):
    from .test_doctor import _write_sweep

    _write_sweep(memory_dir, monkeypatch, tmp_path, model="BAAI/bge-small-en-v1.5", recommended=0.7,
                 off_max=0.5, on_min=0.9)
    r = D.check_floor_calibration(_ctx(memory_dir, repo))
    assert r["status"] == "warn" and "configured 0.6 vs sweep-recommended 0.7" in r["message"]


def test_the_lean_line_never_reads_the_high_report(repo, memory_dir, monkeypatch, tmp_path):
    _high(monkeypatch, tmp_path)
    _write_high_sweep(memory_dir)
    r = D.check_floor_calibration(_ctx(memory_dir, repo))
    assert r["status"] == "ok" and "no sweep recorded" in r["message"]


# --------------------------------------------------------------------------- #
# the sweep inside the high space
# --------------------------------------------------------------------------- #
def _fixtures(tmp_path):
    hard = tmp_path / "hard.yaml"
    hard.write_text(
        '- query: "how is the zebra service deployed"\n  expected: [zebra_deploy_runbook]\n'
        '- query: "llama billing cutover checklist"\n  expected: [llama_billing_cutover]\n',
        encoding="utf-8",
    )
    off = tmp_path / "off.yaml"
    off.write_text('- query: "classic french onion soup"\n- query: "tune a ukulele"\n', encoding="utf-8")
    return str(hard), str(off)


def test_off_the_high_profile_nothing_loads_and_nothing_is_written(project, fakes, tmp_path):  # noqa: F811
    hard, off = _fixtures(tmp_path)
    assert F.high_floor_sweep(project["md"], project["idx"], hard, off) is None
    assert F.high_sweep_lines(None) == []
    assert not E._LOADED


def test_an_unservable_high_model_is_named_not_swept(project, fakes, tmp_path, monkeypatch):  # noqa: F811
    _high(monkeypatch, tmp_path)
    monkeypatch.setattr(E, "unavailable_reason", lambda model: "weights not downloaded")
    hard, off = _fixtures(tmp_path)
    doc = F.high_floor_sweep(project["md"], project["idx"], hard, off)
    assert doc == {"ok": False, "space": FOUR_B, "error": "weights not downloaded"}
    (line,) = F.high_sweep_lines(doc)
    assert FOUR_B in line and "not run" in line and "weights not downloaded" in line


def test_the_sweep_runs_in_the_high_space_and_keeps_its_own_report(project, fakes, tmp_path, monkeypatch):  # noqa: F811
    _high(monkeypatch, tmp_path)
    hard, off = _fixtures(tmp_path)
    md, idx, td = project["md"], project["idx"], str(tmp_path / "telemetry")
    B.build_index(md, idx)
    lean = F.floor_sweep(md, idx, hard, off, telemetry_dir=td)
    assert lean["ok"] and lean["model"] == B.DEFAULT_MODEL

    doc = F.high_floor_sweep(md, idx, hard, off, telemetry_dir=td)
    assert doc["ok"] is True, doc
    assert doc["model"] == doc["space"] == FOUR_B
    assert doc["configured_floor"] == RR._dense_floor(FOUR_B)
    assert doc["on_n"] == 2 and doc["off_n"] == 2
    # built by the same build, in the space, beside the lean index
    high = B.load_index(H.high_index_dir(idx))
    assert high is not None and high.model == FOUR_B and H.fresh(B.load_index(idx), high, FOUR_B)
    assert doc["corpus_fingerprint"] == F.corpus_fingerprint(high)
    # the queries were embedded by the high model, with its instruction-aware query side
    assert any("zebra" in q for emb in fakes_made() for q in emb.queries)
    # two reports, neither overwrites the other
    assert doc["path"] == F.default_floor_sweep_path(md, td, high=True) != lean["path"]
    assert F.read_floor_sweep(md, td)["model"] == B.DEFAULT_MODEL
    assert F.read_floor_sweep(md, td, high=True)["model"] == FOUR_B
    assert E.active_space() is None  # the space does not leak out of the sweep

    lines = F.high_sweep_lines(doc)
    assert lines[0].startswith(f"floor sweep [{FOUR_B}]: recommended ") and doc["path"] in lines[1]
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    r = D.check_high_space_calibration(_ctx(md, project["project"]))
    assert "STALE" not in r["message"] and "recommended" in r["message"]


def fakes_made():
    from .test_embed_mlx import _FakeQwen

    return _FakeQwen.made


def test_a_high_index_in_another_space_is_refused(project, fakes, tmp_path, monkeypatch):  # noqa: F811
    hard, off = _fixtures(tmp_path)
    md, idx = project["md"], project["idx"]
    B.build_index(md, idx)
    assert H.refresh_tier(md, idx, f"{FOUR_B}@1024")
    doc = F.floor_sweep(md, idx, hard, off, write=False, space=FOUR_B)
    assert doc["ok"] is False and f"{FOUR_B}@1024" in doc["error"] and FOUR_B in doc["error"]


def test_the_cli_adds_the_high_line_on_a_high_machine(project, fakes, tmp_path, monkeypatch, capsys):  # noqa: F811
    hard, off = _fixtures(tmp_path)
    md, idx, td = project["md"], project["idx"], str(tmp_path / "telemetry")
    B.build_index(md, idx)
    argv = ["--memory-dir", md, "--index-dir", idx, "--floor-sweep", "--hard-set", hard,
            "--abstention-set", off, "--telemetry-dir", td]
    assert eval_recall.main(argv) == 0
    lean_only = capsys.readouterr().out
    assert f"[{B.DEFAULT_MODEL}]" in lean_only and FOUR_B not in lean_only
    assert not os.path.exists(F.default_floor_sweep_path(md, td, high=True))

    _high(monkeypatch, tmp_path)
    assert eval_recall.main(argv) == 0
    out = capsys.readouterr().out
    assert f"floor sweep [{B.DEFAULT_MODEL}]" in out and f"floor sweep [{FOUR_B}]: recommended" in out
    assert os.path.exists(F.default_floor_sweep_path(md, td, high=True))
