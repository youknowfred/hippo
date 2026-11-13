"""OBS-8 — the 1,000-memory dense-under-load nightly lane.

PRF-3's scale lane is BM25-only at 500 memories and PRF-4's dense test runs one recall at a
time in-process. Neither sees what a busy machine does: several hook processes, each loading
the index and the embedding model cold and embedding its prompt, at the same moment. This
lane generates a 1,000-memory corpus from a fixed seed (PRF-3's generator, at twice the
size), builds a REAL dense index from the model cache CI restores, then starts
``_CONCURRENCY`` recall processes at once, ``_ROUNDS`` times, and measures each process's
wall (spawn to exit, as a hook pays it) for p50/p95. Correctness: every process returns, and
none degrades to BM25-only (each one asserts the dense lane ran: no ``dense_why`` reason and
a ``dense+bm25`` backend label on its top hit).

The regression rule ("fails on >25% against the trailing 7 nights") needs a store. CI keeps a
small JSON of the last nights' p95 in the actions cache (``HIPPO_DENSE_LOAD_HISTORY`` names
the file; the nightly job restores the newest copy and saves a new one). With 7 recorded
nights the run fails when its p95 exceeds 1.25x their median; before that it only records.
An absolute ceiling (``_P95_CEILING_MS``, a measured local run times a stated margin) holds
from the first night, so an evicted cache costs the trailing check, never the whole lane.
Without the env var (a local run) nothing is read or written beyond tmp.

``scale``-marked: deselected from the hermetic default and kept off the per-PR dense lane;
the nightly ``scale-dense`` job runs it (``-m scale tests/test_scale_dense_load.py``).
"""

from __future__ import annotations

import json
import os
import random
import statistics
import subprocess
import sys
import time

import pytest

from memory import build_index as B

from .test_scale import _build_scale_corpus

pytestmark = pytest.mark.scale

_N = 1000
_SEED = 8008
_CONCURRENCY = 8
_ROUNDS = 4
_TRAILING_NIGHTS = 7
_REGRESSION = 1.25
# Measured locally 2026-11-12 (Apple silicon, model cached, 8 at once, the machine busy with
# two full suites): wall p50 4,787 ms, p95 5,374 ms; build 124 s. CI's shared 4-vCPU runners
# run 8 processes on half the cores, so the ceiling is that p95 times a ~5.6x margin (30 s).
# It is a backstop for a gross regression
# (a heavier model, a lost cache, a per-process cost that grows with the corpus), not the
# trailing check, which is the tight one.
_P95_CEILING_MS = 30000.0

_WORKER = r"""
import json, sys, time
t0 = time.monotonic()
from memory import build_index as B
from memory import recall as R
idx, query = sys.argv[1], sys.argv[2]
index = B.load_index(idx)
log = {}
hits = R.recall(query, k=R.DEFAULT_K, index=index, drop_log=log)
print(json.dumps({
    "ms": round((time.monotonic() - t0) * 1000.0, 1),
    "n": len(hits),
    "backend": hits[0]["backend"] if hits else None,
    "dense_why": log.get("dense_why"),
    "dense_ready": bool(index is not None and index.dense_ready),
}))
"""


def _percentile(values, q):
    s = sorted(values)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


def _queries(md, n, seed):
    """On-topic prompts (a slice of real descriptions), so the dense lane has rows over the
    floor to rank and a BM25-only fallback is visible in the backend label."""
    rng = random.Random(seed)
    names = sorted(f for f in os.listdir(md) if f.endswith(".md"))
    out = []
    for fname in rng.sample(names, n):
        with open(os.path.join(md, fname), encoding="utf-8") as fh:
            desc = fh.read().split('description: "', 1)[1].split('"', 1)[0]
        words = desc.split()
        out.append(" ".join(words[: max(4, len(words) // 2)]))
    return out


def _round(idx, queries, env):
    starts, procs = [], []
    for q in queries:
        starts.append(time.monotonic())
        procs.append(subprocess.Popen(
            [sys.executable, "-c", _WORKER, idx, q], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ))
    out = []
    for t0, p in zip(starts, procs):
        stdout, stderr = p.communicate(timeout=600)
        wall = (time.monotonic() - t0) * 1000.0
        assert p.returncode == 0, f"OBS-8: a recall process failed ({p.returncode}): {stderr[-2000:]}"
        row = json.loads(stdout.strip().splitlines()[-1])
        row["wall_ms"] = round(wall, 1)
        out.append(row)
    return out


def _history_check(path, p95):
    """Compare tonight's p95 with the trailing nights' median; append it. Returns a note."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        nights = [float(x["p95_ms"]) for x in doc.get("nights", []) if "p95_ms" in x]
    except (OSError, ValueError, TypeError, AttributeError):
        doc, nights = {}, []
    trailing = nights[-_TRAILING_NIGHTS:]
    verdict = None
    if len(trailing) >= _TRAILING_NIGHTS:
        base = statistics.median(trailing)
        verdict = (p95 <= base * _REGRESSION, base)
    entry = {"p95_ms": round(p95, 1), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "sha": os.environ.get("GITHUB_SHA") or None}
    kept = (doc.get("nights") or [])[-(_TRAILING_NIGHTS * 4):] + [entry]
    if verdict is None or verdict[0]:  # a regressing night is not recorded as the new normal
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "lane": "obs-8-dense-load", "nights": kept}, fh, indent=1)
    return verdict, len(trailing)


@pytest.mark.timeout(3600)  # ~1,000 descriptions + body chunks embedded for real at build
def test_dense_recall_under_load_at_1000_memories(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.delenv("HIPPO_DISABLE", raising=False)
    pytest.importorskip("fastembed")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / "index")
    _build_scale_corpus(md, n=_N, seed=_SEED)
    t0 = time.monotonic()
    manifest = B.build_index(md, idx)
    build_s = time.monotonic() - t0
    assert manifest["count"] == _N
    if not manifest.get("dense_ready"):
        if os.environ.get("HIPPO_DENSE_LOAD_REQUIRE") == "1":  # the nightly job: a skip is a hole
            pytest.fail("OBS-8: the dense index did not build (cold model cache?) — the lane measured nothing")
        pytest.skip("dense model unavailable — OBS-8 needs the real model")

    plugin = os.path.dirname(os.path.dirname(os.path.abspath(B.__file__)))
    env = {**os.environ, "PYTHONPATH": plugin, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
           "HIPPO_TELEMETRY_DIR": str(tmp_path / "telemetry"), "HOME": str(tmp_path / "home")}
    env.pop("HIPPO_DISABLE_DENSE", None)
    env.pop("HIPPO_DISABLE", None)
    rows = []
    for r in range(_ROUNDS):
        rows += _round(idx, _queries(md, _CONCURRENCY, seed=r), env)

    assert len(rows) == _CONCURRENCY * _ROUNDS
    bm25_only = [r for r in rows if r["dense_why"] or r["backend"] != "dense+bm25" or not r["dense_ready"]]
    assert not bm25_only, f"OBS-8: {len(bm25_only)} of {len(rows)} recalls degraded off the dense lane: {bm25_only[:3]}"
    assert all(r["n"] > 0 for r in rows), "OBS-8: an on-topic recall returned nothing"

    walls = [r["wall_ms"] for r in rows]
    p50, p95 = _percentile(walls, 0.50), _percentile(walls, 0.95)
    with capsys.disabled():
        print(f"\nOBS-8: {_N} memories, build {build_s:.1f}s; {_ROUNDS}x{_CONCURRENCY} concurrent "
              f"recall processes: wall p50 {p50:.0f} ms, p95 {p95:.0f} ms")
    assert p95 < _P95_CEILING_MS, (
        f"OBS-8 dense-under-load p95 {p95:.0f} ms >= the absolute ceiling {_P95_CEILING_MS:.0f} ms "
        f"({_CONCURRENCY} concurrent recalls at {_N} memories)"
    )
    path = os.environ.get("HIPPO_DENSE_LOAD_HISTORY")
    if path:
        verdict, nights = _history_check(path, p95)
        with capsys.disabled():
            print(f"OBS-8: {nights} trailing night(s) on record"
                  + ("" if verdict is None else f"; their median p95 {verdict[1]:.0f} ms"))
        if verdict is not None:
            ok, base = verdict
            assert ok, (
                f"OBS-8 dense-under-load p95 regressed: {p95:.0f} ms > {_REGRESSION:.2f}x the trailing "
                f"{_TRAILING_NIGHTS} nights' median {base:.0f} ms"
            )
