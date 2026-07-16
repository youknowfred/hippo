"""PRF-3 — the 500-memory scale lane.

The largest test corpus elsewhere is 8 memories (eval_recall) / 50 (golden_corpus) against a
north star of 500. This lane GENERATES a ~500-memory corpus (realistic description/body lengths,
BM25-only for speed — no model) and pins the scale envelope: recall latency, bounded output with
40+ real matches, and build/refresh time. Every test is ``@pytest.mark.scale`` so it is
deselected from the default hermetic run AND kept off the per-PR dense lane; CI runs it NIGHTLY
(``-m scale``). A failure NAMES the budget it broke (the assertion messages below).

Deterministic: a fixed-seed ``random.Random`` generates the corpus, so a regression is a real
budget break, never generator noise. BM25-only via ``HIPPO_DISABLE_DENSE=1``.
"""

from __future__ import annotations

import os
import random
import time

import pytest

from memory import build_index as B
from memory import eval_recall as E
from memory import recall as R

pytestmark = pytest.mark.scale

# --- Scale + budget constants (documented tripwires; a break names the budget) --------------- #
_N = 500  # north-star corpus size
_CLUSTER_TOKEN = "zephyrquux"  # a rare token injected into a cluster to force a large match set
_CLUSTER_SIZE = 45  # >40 memories share the cluster token -> bounded-output stress
# Budgets — grounded in existing repo numbers, generous enough for a shared CI runner:
_WARM_P95_MS = E.GATE_P95_MS  # 300.0 — the repo's stated warm p95 budget (eval_recall)
_BUILD_BUDGET_S = 15.0  # BM25-only build; matches the north-star refresh envelope (15s), far above actual
_REFRESH_BUDGET_S = 8.0  # an incremental refresh after touching ONE memory (mostly hash re-check)
# PRF-4: the DENSE (production) warm-p95 envelope at scale — the path the bm25-only lane above
# skips. Dense recall pays a per-query ONNX embed (~200ms) the lexical path doesn't, so its warm
# p95 sits ABOVE the bm25 budget: measured ~407ms locally at 500 memories (p50 ~215ms). This
# budget is generous headroom over that — CPU CI runners + ONNX jitter run slower than a dev
# laptop — so it catches a gross regression (a heavier model, a lost incremental-embed cache)
# without flaking. It documents that dense@scale is a DIFFERENT, higher envelope than PRF-3's 300ms.
_DENSE_WARM_P95_MS = 900.0

_WORDS = (
    "cache invalidation retry backoff idempotent migration schema index shard replica "
    "latency throughput queue worker deploy rollout canary feature flag telemetry ledger "
    "recall embedding vector cosine tokenizer fusion ranking floor staleness provenance "
    "commit rebase squash worktree monorepo symlink gitignore corpus manifest checksum "
    "timeout socket daemon subprocess offline bounded degrade fallback threshold budget"
).split()


def _sentence(rng: random.Random, lo: int, hi: int) -> str:
    return " ".join(rng.choice(_WORDS) for _ in range(rng.randint(lo, hi)))


def _build_scale_corpus(memory_dir: str, n: int = _N, seed: int = 1234) -> None:
    os.makedirs(memory_dir, exist_ok=True)
    rng = random.Random(seed)
    for i in range(n):
        name = f"scale-memory-{i:04d}"
        desc = _sentence(rng, 12, 30)  # ~80-200 chars of realistic vocabulary
        if i < _CLUSTER_SIZE:
            desc = f"{_CLUSTER_TOKEN} {desc}"  # a large shared-token match set for bounded-output
        # A multi-paragraph body so body-chunking (RET-2) is exercised at scale.
        body = "\n\n".join(_sentence(rng, 40, 90) for _ in range(rng.randint(1, 3)))
        with open(os.path.join(memory_dir, f"{name}.md"), "w", encoding="utf-8") as fh:
            fh.write(
                f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: reference\n---\n{body}\n'
            )


@pytest.fixture()
def scale_index(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _build_scale_corpus(md)
    manifest = B.build_index(md, idx)
    assert manifest["count"] == _N
    assert manifest["dense_ready"] is False  # BM25-only lane
    return md, idx, B.load_index(idx)


def test_recall_latency_under_warm_gate_at_scale(scale_index):
    _md, _idx, index = scale_index
    rng = random.Random(99)
    queries = [_sentence(rng, 4, 8) for _ in range(30)] + [_CLUSTER_TOKEN]
    lat = E.latency(index, queries, k=10)
    assert lat["n"] > 0
    assert lat["p95"] < _WARM_P95_MS, (
        f"PRF-3 recall latency regressed: warm p95 {lat['p95']:.1f}ms >= budget {_WARM_P95_MS}ms "
        f"at {_N} memories"
    )


@pytest.fixture()
def scale_index_dense(tmp_path, monkeypatch):
    """PRF-4: the PRODUCTION dense+bm25 path at 500 memories — the envelope PRF-3's bm25-only
    ``scale_index`` fixture deliberately skips. Builds a REAL dense index (500 fastembed embeds,
    tens of seconds), so it SKIPS cleanly wherever the model is unavailable (no fastembed, a cold
    cache, or ``HIPPO_DISABLE_DENSE``) rather than silently degrading to the bm25 number this lane
    already measures. Its green nightly/dense CI run is a separate wiring step (the nightly scale
    job is bm25-only today); this pins the test + budget and is verified by a local ``-m scale`` run."""
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    pytest.importorskip("fastembed")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _build_scale_corpus(md)
    manifest = B.build_index(md, idx)
    assert manifest["count"] == _N
    if not manifest.get("dense_ready"):
        pytest.skip("dense model unavailable (cold fastembed cache) — PRF-4 needs the real model")
    return md, idx, B.load_index(idx)


@pytest.mark.timeout(900)  # 500 real fastembed embeds at build — heavier than the bm25 lane
def test_dense_recall_latency_under_warm_gate_at_scale(scale_index_dense):
    """PRF-4: warm p95 of the DENSE production path at 500 memories — the number PRF-3's
    bm25-only lane cannot see. Dense latency is dominated by the per-query ONNX embed (near
    corpus-size-independent), so this pins the real hot-path envelope, which sits ABOVE the
    lexical 300ms budget."""
    _md, _idx, index = scale_index_dense
    assert index.dense_ready, "PRF-4 must measure the dense path, not a bm25-only fallback"
    rng = random.Random(99)
    queries = [_sentence(rng, 4, 8) for _ in range(30)] + [_CLUSTER_TOKEN]
    lat = E.latency(index, queries, k=10)
    assert lat["n"] > 0
    assert lat["p95"] < _DENSE_WARM_P95_MS, (
        f"PRF-4 dense recall latency regressed: warm p95 {lat['p95']:.1f}ms >= budget "
        f"{_DENSE_WARM_P95_MS}ms at {_N} memories (production dense+bm25 path; ~407ms baseline)"
    )


def test_bounded_output_with_large_match_set(scale_index):
    _md, _idx, index = scale_index
    # The cluster token matches 45 memories; recall must still return at most DEFAULT_K...
    hits = R.recall(_CLUSTER_TOKEN, k=R.DEFAULT_K, index=index)
    assert 0 < len(hits) <= R.DEFAULT_K, (
        f"PRF-3 bounded output regressed: recall returned {len(hits)} > DEFAULT_K={R.DEFAULT_K} "
        f"for a {_CLUSTER_SIZE}-memory match set"
    )
    # ...and the injected block stays under the harness cap regardless of match-set size.
    out = R.format_results(hits)
    assert len(out) <= R._MAX_RECALL_CHARS, (
        f"PRF-3 bounded output regressed: injection block {len(out)} chars > cap "
        f"{R._MAX_RECALL_CHARS}"
    )


def test_build_and_refresh_time_at_scale(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _build_scale_corpus(md)

    t0 = time.monotonic()
    manifest = B.build_index(md, idx, force=True)
    build_s = time.monotonic() - t0
    assert manifest["count"] == _N
    assert build_s < _BUILD_BUDGET_S, (
        f"PRF-3 build time regressed: full BM25 build of {_N} memories took {build_s:.2f}s "
        f">= budget {_BUILD_BUDGET_S}s"
    )

    # Touch ONE memory, then time an incremental refresh (should be dominated by hash re-checks).
    victim = os.path.join(md, "scale-memory-0000.md")
    with open(victim, "a", encoding="utf-8") as fh:
        fh.write("\n\nappended paragraph forcing a re-index of exactly one memory.\n")
    t0 = time.monotonic()
    B.refresh_index(md, idx)
    refresh_s = time.monotonic() - t0
    assert refresh_s < _REFRESH_BUDGET_S, (
        f"PRF-3 refresh time regressed: incremental refresh after a 1-memory edit took "
        f"{refresh_s:.2f}s >= budget {_REFRESH_BUDGET_S}s at {_N} memories"
    )


# --------------------------------------------------------------------------- #
# JIT-1 at scale: the PostToolUse first-touch lane on the same 500-memory corpus.
# The acceptance criterion is a STATED budget for the hook-side decision — derived-
# cache reads only (no corpus scan, no index rebuild, no model), so the per-touch
# cost must stay flat and small even when 500 memories (60 of them cited) exist.
# --------------------------------------------------------------------------- #
_JIT_CITED = 60          # memories carrying cited_paths (40 remind-eligible, 20 project-only)
_JIT_TOUCHES = 200       # mixed touch sequence: ~75% uncited (the empty norm), ~25% cited
_JIT_OBSERVE_P95_MS = 50.0   # per-touch decision budget (the hook's incremental overhead)
_JIT_CACHE_BUILD_BUDGET_S = 5.0  # one frontmatter pass over 500 files at SessionStart


def _jit_scale_corpus(tmp_path):
    from memory import jit as J

    md = str(tmp_path / "memory")
    _build_scale_corpus(md)
    # Rewrite a slice of the corpus to carry citations: 40 remind-eligible (feedback /
    # steer:pin) + 20 plain-project (JIT-2's full reverse index only).
    for i in range(_JIT_CITED):
        name = f"scale-memory-{i:04d}"
        if i % 3 == 2:
            head = "metadata:\n  type: project\n"
        elif i % 2 == 0:
            head = "metadata:\n  type: feedback\n"
        else:
            head = "metadata:\n  type: project\n  steer: pin\n"
        with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
            fh.write(
                f'---\nname: {name}\ndescription: "lesson {i} about module {i}"\n'
                f'{head}  cited_paths: ["src/module_{i:04d}.py"]\n---\nBody.\n'
            )
    idx = str(tmp_path / ".memory-index")
    tele = str(tmp_path / ".memory-telemetry")
    t0 = time.monotonic()
    assert J.refresh_touch_cache(md, idx) is True
    build_s = time.monotonic() - t0
    return md, idx, tele, build_s


def test_jit_touch_decision_under_budget_at_scale(tmp_path):
    from memory import jit as J

    md, idx, tele, build_s = _jit_scale_corpus(tmp_path)
    assert build_s < _JIT_CACHE_BUILD_BUDGET_S, (
        f"JIT-1 touchmap build regressed: {build_s:.2f}s >= budget "
        f"{_JIT_CACHE_BUILD_BUDGET_S}s at {_N} memories (one SessionStart frontmatter pass)"
    )
    rng = random.Random(7)
    touches = []
    for i in range(_JIT_TOUCHES):
        if i % 4 == 0:
            touches.append(f"src/module_{rng.randrange(_JIT_CITED):04d}.py")  # cited
        else:
            touches.append(f"src/uncited_{i:04d}.py")  # the empty norm
    times = []
    root = str(tmp_path)
    for rel in touches:
        t0 = time.perf_counter()
        J.observe_touch(
            rel, memory_dir=md, repo_root=root, telemetry_dir=tele,
            index_dir=idx, session_id="scale-sess",
        )
        times.append((time.perf_counter() - t0) * 1000.0)
    times.sort()
    p95 = times[int(0.95 * len(times))]
    assert p95 < _JIT_OBSERVE_P95_MS, (
        f"JIT-1 hook overhead regressed: per-touch decision p95 {p95:.2f}ms >= budget "
        f"{_JIT_OBSERVE_P95_MS}ms at {_N} memories / {_JIT_CITED} cited "
        f"(derived-cache reads only — a corpus scan or rebuild snuck onto the hook path?)"
    )
