"""Tests for memory/eval_recall.py — the 5 merge gates.

Hermetic: a tmp corpus + tmp MEMORY.full.md/MEMORY.md + a tmp hard-set fixture are built
so the gates compute and assert their thresholds on BM25 alone (no fastembed needed). The
real-corpus run with a warm dense model is a manual merge step (see eval_recall docstring).
"""

from __future__ import annotations

import json
import os

import pytest

from memory import build_index as B
from memory import eval_recall as E

from .conftest import git_commit, write_file

# A small corpus with distinctive, low-overlap descriptions so self-recall is clean on BM25.
_CORPUS = {
    "reranker_voyage.md": "voyage rerank cross encoder primary reranker hybrid bm25 fallback circuit breaker",
    "budget_envelope.md": "phase envelope budget authority synthesis tail reservation degradation",
    "excel_header.md": "excel parser header rescue inference noncanonical column layout capped calls",
    "canvas_pdf.md": "canvas pdf export gotenberg pypdf footnote dagger marker two pass",
    "formula_graph.md": "formula graph columnar parquet csr trace dependencies reachability python",
    "image_intel.md": "image intelligence page render diagram subtype ocr extraction pipeline",
    "playbook_memory.md": "playbook strategy promotion tier semantic learning store demotion",
    "ducklake.md": "observability ducklake warehouse postgres catalog bucket data files lakehouse",
}

# Paraphrase-ish queries (still lexically reachable for BM25 in a tiny corpus).
_HARD_SET = [
    {"query": "which reranker model runs first on search candidates", "expected": ["reranker_voyage"]},
    {"query": "guaranteed time envelope for each pipeline phase", "expected": ["budget_envelope"]},
    {"query": "infer spreadsheet column headers when layout is unusual", "expected": ["excel_header"]},
    {"query": "render a document to pdf with headless chrome", "expected": ["canvas_pdf"]},
    {"query": "workbook dependency graph stored as columnar parquet", "expected": ["formula_graph"]},
    {"query": "warehouse catalog in postgres data files in a bucket", "expected": ["ducklake"]},
]


def _mem(name: str, description: str) -> str:
    return f'---\nname: {name}\ndescription: "{description}"\ntype: project\n---\nbody for {name}\n'


def _build_corpus(tmp_path):
    md = str(tmp_path / "memory")
    os.makedirs(md, exist_ok=True)
    for fname, desc in _CORPUS.items():
        with open(os.path.join(md, fname), "w", encoding="utf-8") as fh:
            fh.write(_mem(fname[:-3], desc))
    # Full index (always-loaded baseline) — deliberately large; trimmed floor — small.
    with open(os.path.join(md, "MEMORY.full.md"), "w", encoding="utf-8") as fh:
        fh.write("# Full index\n" + "\n".join(f"- [{n}]({n}) — {d}" for n, d in _CORPUS.items()) * 6)
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# Floor\n## User\n## Working Style\n(project/reference recalled on demand)\n")
    return md


def _write_hard_set(tmp_path):
    import yaml

    p = str(tmp_path / "hard_set.yaml")
    with open(p, "w", encoding="utf-8") as fh:
        yaml.safe_dump(_HARD_SET, fh)
    return p


# A graded relevance set: one single-relevant query (mirrors hard-set's binary shape) plus one
# multi-relevant query (a real cluster) so precision@k actually exercises the GRADED behavior.
_RELEVANCE_SET = [
    {"query": "which reranker model runs first on search candidates", "relevant": ["reranker_voyage"]},
    {
        "query": "things that touch document rendering and export",
        "relevant": ["canvas_pdf", "image_intel"],
    },
]


def _write_relevance_set(tmp_path):
    import yaml

    p = str(tmp_path / "relevance_set.yaml")
    with open(p, "w", encoding="utf-8") as fh:
        yaml.safe_dump(_RELEVANCE_SET, fh)
    return p


def test_self_recall_gate_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)
    sr = E.self_recall_at_k(index, k=10)
    assert sr >= E.GATE_SELF_RECALL, f"self-recall {sr} below {E.GATE_SELF_RECALL}"


def test_hard_set_recall_and_mrr_gates(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)
    hs = E.hard_set_metrics(index, _HARD_SET, k=10)
    assert hs["recall"] >= E.GATE_HARD_RECALL, hs
    assert hs["mrr"] >= E.GATE_MRR, hs


def test_token_reduction_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)
    tok = E.token_reduction(md, index, _HARD_SET, k=10)
    assert tok["net"] > 0, tok
    assert tok["full"] > tok["floor"]


def test_latency_is_recorded(tmp_path, monkeypatch):
    """Hermetic shape check ONLY: latency() returns a well-formed report. QUA-10: the
    STRICT `p95 < GATE_P95_MS` wall-clock assertion moved to
    test_latency_p95_is_under_gate (@pytest.mark.slow, below) — a real wall-clock
    measurement is environment/timing-sensitive (noisy shared CI runner, thermal
    throttling, etc.) and must never be able to flake a hermetic lane red. The eval
    CLI's OWN gate (`eval_recall --gate-cold`, dense CI lane) enforces GATE_P95_MS for
    real against the shipped corpus regardless of this test's marker."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)
    lat = E.latency(index, [h["query"] for h in _HARD_SET], k=10)
    assert lat["n"] == len(_HARD_SET)
    assert lat["p95"] >= 0.0


@pytest.mark.slow
def test_latency_p95_is_under_gate(tmp_path, monkeypatch):
    """QUA-10: the strict wall-clock half of test_latency_is_recorded, split out and
    marked slow so it's deselected from the default hermetic lane (pytest.ini addopts
    `-m "not network and not slow"`) and instead runs alongside the dense CI lane's
    network-marked tests (`-m "network or slow"`) — a machine already reserved and
    less noisy, where a real timing assertion is meaningful. See
    test_latency_is_recorded's docstring for the full rationale."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)
    lat = E.latency(index, [h["query"] for h in _HARD_SET], k=10)
    assert lat["p95"] < E.GATE_P95_MS  # warm recall is well under the latency gate


def test_cold_latency_is_reported_not_gated(tmp_path, monkeypatch):
    # Cold latency is a report-only honesty signal — a FRESH subprocess per sample, so the real
    # per-process import + model-load cost is measured (the warm gate hides it). On BM25-only it
    # is cheaper (no model) but must still record samples and never gate. Subprocess inherits the
    # parent cwd (repo root) so `memory.recall` imports.
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    cold = E.cold_latency(md, idx, [h["query"] for h in _HARD_SET], k=10, samples=2)
    assert cold["n"] == 2  # capped at the sample bound; each sample is a fresh process
    assert cold["p50"] >= 0.0 and cold["p95"] >= cold["p50"] and cold["max"] >= cold["p95"]


def test_evaluate_all_gates_pass_on_tmp_corpus(tmp_path, monkeypatch):
    """QUA-10: ``report["ok"]`` and ``recall_p95_ms``'s own ``pass`` flag are real wall-clock
    assertions (evaluate() computes both from a live ``latency()`` measurement) — split into
    test_evaluate_recall_p95_gate_passes_for_real (@pytest.mark.slow, below) so a noisy
    hermetic runner can't flake THIS test red. Everything else here (the 4 deterministic
    gates, cold_p95_ms's skip semantics, recall_p95_ms's structural shape) has no timing
    dependency and stays hermetic."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)
    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10)
    # The 4 deterministic gates (no timing jitter) are asserted True directly.
    for name in ("self_recall@10", "hard_recall@10", "mrr@10", "token_reduction"):
        assert report["gates"][name]["pass"] is True, report["gates"][name]
    # recall_p95_ms's SHAPE is checked hermetically (present, non-negative, right threshold)
    # without asserting the strictly-timed `pass` flag itself.
    p95_gate = report["gates"]["recall_p95_ms"]
    assert p95_gate["value"] >= 0.0
    assert p95_gate["threshold"] == E.GATE_P95_MS
    # cold_latency itself (the report block) stays report-only regardless of gate_cold --
    # only the derived cold_p95_ms GATE is opt-in (PRF-2).
    assert "cold_latency" in report and report["cold_latency"]["n"] >= 1
    assert report["gates"]["cold_p95_ms"]["pass"] is None  # not requested -> skipped
    assert report["gates"]["cold_p95_ms"]["skipped"] is True


@pytest.mark.slow
def test_evaluate_recall_p95_gate_passes_for_real(tmp_path, monkeypatch):
    """QUA-10: the strict wall-clock half of test_evaluate_all_gates_pass_on_tmp_corpus,
    split out and marked slow (see that test's docstring) — ``report["ok"]`` and
    ``recall_p95_ms["pass"]`` are real timing assertions and must not be able to flake the
    default hermetic lane red. Runs alongside the dense CI lane's network-marked tests
    (``-m "network or slow"``); the eval CLI's own ``--gate-cold`` run is the real merge
    gate regardless of this test's outcome."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)
    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10)
    assert report["ok"] is True, report["gates"]
    assert report["gates"]["recall_p95_ms"]["pass"] is True
    assert report["gates"]["recall_p95_ms"]["value"] < E.GATE_P95_MS


# --------------------------------------------------------------------------- #
# PRF-2 — cold_p95_ms: gate the cold path (opt-in via evaluate(gate_cold=True) /
# main's --gate-cold), report-only by default. Three states to cover:
#   1. not requested                -> skipped, reason "not requested (--gate-cold)"
#   2. requested + dense_ready      -> a REAL gate (pass = n>0 and p50 < GATE_COLD_P95_MS)
#   3. requested + bm25-only        -> skipped (cold ~= warm without dense; a hermetic
#                                      machine must never redden on this)
# cold_latency is monkeypatched throughout so these are hermetic unit tests of the GATES
# DICT SHAPE, not integration tests that spawn real subprocesses in a loop (that coverage
# lives in test_cold_latency_is_reported_not_gated above + the manual dense-lane check).
# --------------------------------------------------------------------------- #
def test_cold_p95_gate_skipped_when_not_requested(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)
    # Even a comfortably-under-budget cold sample must NOT turn into a gate unless asked.
    monkeypatch.setattr(E, "cold_latency", lambda *a, **k: {"p50": 5.0, "p95": 6.0, "max": 6.0, "n": 3})
    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10)
    g = report["gates"]["cold_p95_ms"]
    assert g["pass"] is None
    assert g["skipped"] is True
    assert g["threshold"] == E.GATE_COLD_P95_MS
    assert report["ok"] is True  # a skipped gate never drags `ok` down


def test_cold_p95_gate_requested_and_dense_ready_passes_under_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)
    B.build_index(md, idx)
    real_index = B.load_index(idx)
    monkeypatch.setattr(E, "load_index", lambda _idx_dir: _with_dense_ready(real_index, True))
    monkeypatch.setattr(E, "cold_latency", lambda *a, **k: {"p50": 5.0, "p95": 6.0, "max": 6.0, "n": 3})

    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10, gate_cold=True)
    g = report["gates"]["cold_p95_ms"]
    assert "skipped" not in g
    assert g["pass"] is True
    assert g["value"] == 6.0
    assert g["threshold"] == E.GATE_COLD_P95_MS
    assert report["ok"] is True


def test_cold_p95_gate_requested_and_dense_ready_fails_over_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)
    B.build_index(md, idx)
    real_index = B.load_index(idx)
    monkeypatch.setattr(E, "load_index", lambda _idx_dir: _with_dense_ready(real_index, True))
    # A p50 comfortably OVER the 1500ms budget -- must fail the gate and drag `ok` down.
    monkeypatch.setattr(E, "cold_latency", lambda *a, **k: {"p50": 4000.0, "p95": 4200.0, "max": 4200.0, "n": 3})

    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10, gate_cold=True)
    g = report["gates"]["cold_p95_ms"]
    assert g["pass"] is False
    assert report["ok"] is False


def test_cold_p95_gate_requested_but_no_samples_fails_not_skips(tmp_path, monkeypatch):
    """n == 0 (every subprocess sample failed/timed out) must FAIL the gate when requested,
    not silently pass -- a gate you can't measure is not a gate you can trust."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)
    B.build_index(md, idx)
    real_index = B.load_index(idx)
    monkeypatch.setattr(E, "load_index", lambda _idx_dir: _with_dense_ready(real_index, True))
    monkeypatch.setattr(E, "cold_latency", lambda *a, **k: {"p50": 0.0, "p95": 0.0, "max": 0.0, "n": 0})

    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10, gate_cold=True)
    g = report["gates"]["cold_p95_ms"]
    assert "skipped" not in g
    assert g["pass"] is False
    assert report["ok"] is False


def test_cold_p95_gate_requested_but_bm25_only_is_skipped_not_failed(tmp_path, monkeypatch):
    """Requested (--gate-cold) but the run only served bm25-only (no dense model, e.g. a
    hermetic/cache-less machine) -- must SKIP (not fail), because cold ~= warm without a
    per-process model load to amortize; gating here would redden hermetic CI for nothing."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)
    # Even a wildly-over-budget "cold" sample must not fail when bm25-only -- proves the
    # skip is unconditional on backend, not just a lucky under-budget number.
    monkeypatch.setattr(E, "cold_latency", lambda *a, **k: {"p50": 9999.0, "p95": 9999.0, "max": 9999.0, "n": 1})
    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10, gate_cold=True)
    assert report["dense_ready"] is False
    g = report["gates"]["cold_p95_ms"]
    assert g["pass"] is None
    assert g["skipped"] is True
    assert report["ok"] is True


def test_gate_cold_cli_flag_defaults_false_and_plumbs_through(tmp_path, monkeypatch, capsys):
    """CLI flag plumbing: --gate-cold absent -> gate_cold=False reaches evaluate(); the
    skip reason printed names the flag so a reader knows how to opt in."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)

    captured = {}
    real_evaluate = E.evaluate

    def _spy(*args, **kwargs):
        captured["gate_cold"] = kwargs.get("gate_cold")
        return real_evaluate(*args, **kwargs)

    monkeypatch.setattr(E, "evaluate", _spy)
    rc = E.main(["--memory-dir", md, "--index-dir", idx, "--hard-set", hs_path])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert captured["gate_cold"] is False
    assert "cold_p95_ms" in out
    assert "not requested (--gate-cold)" in out


def test_gate_cold_cli_flag_true_plumbs_through(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)

    captured = {}
    real_evaluate = E.evaluate

    def _spy(*args, **kwargs):
        captured["gate_cold"] = kwargs.get("gate_cold")
        return real_evaluate(*args, **kwargs)

    monkeypatch.setattr(E, "evaluate", _spy)
    rc = E.main(["--memory-dir", md, "--index-dir", idx, "--hard-set", hs_path, "--gate-cold"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert captured["gate_cold"] is True
    # requested but bm25-only (hermetic build above) -> skipped with the OTHER reason
    assert "bm25-only" in out and "cold ~= warm" in out


def test_main_cli_exits_nonzero_on_cold_failure_only_when_requested(tmp_path, monkeypatch, capsys):
    """RESULT honors a cold failure ONLY when --gate-cold was passed -- the same forged
    over-budget cold sample must be invisible to `ok`/exit-code without the flag, and must
    fail the build with it (dense_ready forced True so the gate actually evaluates)."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)
    B.build_index(md, idx)
    real_index = B.load_index(idx)
    monkeypatch.setattr(E, "load_index", lambda _idx_dir: _with_dense_ready(real_index, True))
    monkeypatch.setattr(E, "cold_latency", lambda *a, **k: {"p50": 5000.0, "p95": 5200.0, "max": 5200.0, "n": 3})

    rc_without = E.main(["--memory-dir", md, "--index-dir", idx, "--hard-set", hs_path])
    out_without = capsys.readouterr().out
    assert rc_without == 0, out_without
    assert "RESULT: ALL GATES PASS" in out_without

    rc_with = E.main(["--memory-dir", md, "--index-dir", idx, "--hard-set", hs_path, "--gate-cold"])
    out_with = capsys.readouterr().out
    assert rc_with == 1, out_with
    assert "RESULT: GATE FAILURE" in out_with
    assert "cold_p95_ms" in out_with


def test_load_hard_set_missing_file_is_empty():
    assert E.load_hard_set("/no/such/file.yaml") == []


def test_derive_self_query_is_nontrivial():
    entry = {"doc_text": "reranker voyage. voyage rerank cross encoder is the primary reranker"}
    q = E.derive_self_query(entry)
    assert q and "voyage" in q
    # derived from the DESCRIPTION, not the verbatim indexed doc_text
    assert q != entry["doc_text"]


# --------------------------------------------------------------------------- #
# precision_at_k (Tier 1, report-only) — graded measure, distinct from hard_set's binary recall
# --------------------------------------------------------------------------- #
def test_load_relevance_set_missing_file_is_empty():
    assert E.load_relevance_set("/no/such/file.yaml") == []


def test_load_relevance_set_coerces_single_relevant_string(tmp_path):
    import yaml

    p = str(tmp_path / "rs.yaml")
    with open(p, "w", encoding="utf-8") as fh:
        yaml.safe_dump([{"query": "q", "relevant": "solo_name"}], fh)
    rs = E.load_relevance_set(p)
    assert rs == [{"query": "q", "relevant": ["solo_name"]}]


def test_precision_at_k_empty_set_is_zero():
    assert E.precision_at_k(index=None, relevance_set=[], k=10) == {"precision": 0.0, "n": 0}


def test_precision_at_k_rewards_finding_more_of_a_cluster(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)

    query = "which reranker model runs first on search candidates"
    actual_top_k = [r["name"] for r in E.recall(query, k=10, index=index)]
    assert actual_top_k  # sanity: the query matches something in this tiny corpus

    single = [{"query": query, "relevant": [actual_top_k[0]]}]
    p_single = E.precision_at_k(index, single, k=10)
    assert p_single["n"] == 1
    assert p_single["precision"] == round(1 / 10, 4)  # exactly 1 of 10 slots is relevant

    # Treating EVERY returned slot as relevant scores a perfect 1.0 -- proves "found more of
    # the cluster" genuinely raises the score (not a coincidence of one lucky match), without
    # depending on fragile cross-document vocabulary assumptions: it uses whatever recall()
    # actually returned as ground truth, so it's deterministic regardless of corpus wording.
    # k is set to len(actual_top_k) (not 10) since precision's denominator is always k, not
    # the number of matches -- a small tmp corpus may match fewer than 10 BM25 candidates.
    n = len(actual_top_k)
    everything = [{"query": query, "relevant": actual_top_k}]
    p_everything = E.precision_at_k(index, everything, k=n)
    assert p_everything["precision"] == 1.0
    assert p_everything["precision"] > p_single["precision"]


def test_precision_at_k_is_distinct_from_hard_set_binary_recall(tmp_path, monkeypatch):
    """A query matching only 1 of 2 relevant items scores 0.5/k under precision, but would
    score a full binary HIT under hard_set_metrics' recall@k -- proving the metrics differ."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)

    rs = [{"query": "which reranker model runs first on search candidates", "relevant": ["reranker_voyage", "a_name_not_in_results"]}]
    p = E.precision_at_k(index, rs, k=10)
    assert p["precision"] == round(1 / 10, 4)  # only 1 of 2 relevant names actually found

    hs_equiv = [{"query": rs[0]["query"], "expected": rs[0]["relevant"]}]
    hs = E.hard_set_metrics(index, hs_equiv, k=10)
    assert hs["recall"] == 1.0  # binary: ANY one of the two expected counts as a full hit


# --------------------------------------------------------------------------- #
# RET-1: abstention_rate (report-only) — the mirror image of the 5 gates: does recall()
# correctly find NOTHING for a query with no right answer, rather than "does it find the
# right memory".
# --------------------------------------------------------------------------- #
_OFF_TOPIC_QUERIES = [
    "what's the ideal hydration ratio for pizza dough and how long should it ferment",
    "explain quantum entanglement and Bell's inequality in a physics lecture",
    "which celebrity just announced their engagement this week",
]


def test_load_abstention_set_missing_file_is_empty():
    assert E.load_abstention_set("/no/such/file.yaml") == []


def test_load_abstention_set_parses_bare_query_rows(tmp_path):
    import yaml

    p = str(tmp_path / "abstain.yaml")
    with open(p, "w", encoding="utf-8") as fh:
        yaml.safe_dump([{"query": "pizza dough hydration ratio"}, {"query": "quantum entanglement lecture"}], fh)
    assert E.load_abstention_set(p) == ["pizza dough hydration ratio", "quantum entanglement lecture"]


def test_load_abstention_set_tolerates_bare_string_rows(tmp_path):
    import yaml

    p = str(tmp_path / "abstain.yaml")
    with open(p, "w", encoding="utf-8") as fh:
        yaml.safe_dump(["a bare string query"], fh)
    assert E.load_abstention_set(p) == ["a bare string query"]


def test_abstention_rate_empty_set_is_zero():
    assert E.abstention_rate(index=None, abstention_set=[], k=10) == {"rate": 0.0, "n": 0}


def test_abstention_rate_measures_fraction_returning_zero_results(tmp_path, monkeypatch):
    """The headline RET-1 eval acceptance: over a set of queries that share NO token with
    the corpus (hermetic — dense disabled), recall() must abstain on every one, so
    abstention_rate == 1.0."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)

    rate = E.abstention_rate(index, _OFF_TOPIC_QUERIES, k=10)
    assert rate == {"rate": 1.0, "n": 3}


def test_abstention_rate_gates_when_fixture_provided(tmp_path, monkeypatch):
    """RET-8 promotion (supersedes the pre-RET-8 report-only pin): a provided abstention
    fixture whose rate lands under GATE_ABSTENTION now FAILS the run — and omitting the
    fixture skips the gate rather than failing it, so fixtureless callers keep ok=True."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)

    p = str(tmp_path / "abstain.yaml")
    import yaml

    # Deliberately ON-topic (shares vocabulary with _CORPUS) -- recall() correctly does NOT
    # abstain, so this fixture's own rate is 0.0: under the promoted gate that is a FAIL.
    with open(p, "w", encoding="utf-8") as fh:
        yaml.safe_dump([{"query": "which reranker model runs first on search candidates"}], fh)

    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10, abstention_set_path=p)
    assert report["abstention_rate"]["rate"] == 0.0
    assert report["abstention_rate"]["n"] == 1
    assert report["gates"]["abstention_rate"]["pass"] is False
    assert report["ok"] is False  # promoted: a provided-but-failing fixture reddens the run

    without = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10)
    assert without["gates"]["abstention_rate"]["pass"] is None
    assert without["gates"]["abstention_rate"]["skipped"] is True
    assert without["ok"] is True  # absent fixture = skip, never a fail


def test_default_abstention_set_path_probes_audit_fixtures_then_tests_fixtures(tmp_path, monkeypatch):
    repo = str(tmp_path / "proj")
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)

    assert E._default_abstention_set_path() is None  # nothing anywhere yet

    tf = os.path.join(repo, "tests", "fixtures")
    os.makedirs(tf)
    repo_fixture = os.path.join(tf, "recall_abstention_set.yaml")
    with open(repo_fixture, "w", encoding="utf-8") as fh:
        fh.write("- {query: q}\n")
    assert E._default_abstention_set_path() == repo_fixture

    af = os.path.join(md, ".audit-fixtures")
    os.makedirs(af)
    audit_fixture = os.path.join(af, "recall_abstention_set.yaml")
    with open(audit_fixture, "w", encoding="utf-8") as fh:
        fh.write("- {query: q}\n")
    assert E._default_abstention_set_path() == audit_fixture  # project-local wins


def test_main_cli_prints_abstention_rate_line_when_present(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)

    ab_path = str(tmp_path / "abstain.yaml")
    import yaml

    with open(ab_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump([{"query": q} for q in _OFF_TOPIC_QUERIES], fh)

    rc = E.main(
        [
            "--memory-dir", md,
            "--index-dir", idx,
            "--hard-set", hs_path,
            "--abstention-set", ab_path,
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    # RET-8: abstention_rate renders as a tracked row in the gate table (1.0 >= 0.8 -> pass),
    # not the old report-only footer line.
    assert "✅ abstention_rate" in out
    assert "= 1.0 (threshold 0.8)" in out


# --------------------------------------------------------------------------- #
# staleness_half_life (Tier 1, report-only)
# --------------------------------------------------------------------------- #
def _mem_with_source_commit(name, source_commit):
    return f'---\nname: {name}\ndescription: "{name} description"\nsource_commit: "{source_commit}"\n---\nbody\n'


def test_staleness_half_life_is_median_of_baseline_ages(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(repo, "src/bar.py", "y = 1\n")
    c2 = git_commit(repo, "c2", 1_700_000_000 + 10 * 86400)  # 10 days after c1
    write_file(memory_dir, "m_a.md", _mem_with_source_commit("m_a", c1))
    write_file(memory_dir, "m_b.md", _mem_with_source_commit("m_b", c2))

    now = 1_700_000_000 + 30 * 86400  # 30 days after c1
    hl = E.staleness_half_life(memory_dir, repo, now=now)
    # ages: m_a = 30 days old, m_b = 20 days old -> median (sorted [20, 30]) = 25.0
    assert hl == {"median_days": 25.0, "n": 2}


def test_staleness_half_life_excludes_memories_without_source_commit(repo, memory_dir):
    write_file(repo, "src/foo.py", "x = 1\n")
    c1 = git_commit(repo, "c1", 1_700_000_000)
    write_file(memory_dir, "m_a.md", _mem_with_source_commit("m_a", c1))
    write_file(memory_dir, "m_no_baseline.md", '---\nname: m_no_baseline\ndescription: "x"\n---\nbody\n')

    hl = E.staleness_half_life(memory_dir, repo, now=1_700_000_000 + 86400)
    assert hl["n"] == 1  # only m_a has a resolvable baseline


def test_staleness_half_life_empty_corpus_returns_zero_never_raises(repo, memory_dir):
    assert E.staleness_half_life(memory_dir, repo) == {"median_days": 0.0, "n": 0}


# --------------------------------------------------------------------------- #
# session_token_cost (Tier 1, report-only)
# --------------------------------------------------------------------------- #
def _seed_recall_events(td, session_event_counts):
    """Write raw recall events into td/recall_events.jsonl -- N events per session_id."""
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "recall_events.jsonl"), "w", encoding="utf-8") as fh:
        for sid, n in session_event_counts.items():
            for _ in range(n):
                fh.write(json.dumps({"session_id": sid, "names": ["a"], "backend": "bm25"}) + "\n")


def test_session_token_cost_combines_ledger_and_token_reduction(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)

    td = str(tmp_path / "tele")
    _seed_recall_events(td, {"s1": 2, "s2": 4})  # avg 3 events/session over 2 sessions

    sc = E.session_token_cost(md, td, index, _HARD_SET, k=10)
    tok = E.token_reduction(md, index, _HARD_SET, k=10)
    assert sc["n_sessions"] == 2
    assert sc["avg_events_per_session"] == 3.0
    assert sc["avg_session_tokens"] == round(3.0 * tok["recall_avg"], 1)


def test_session_token_cost_no_sessions_returns_zeros_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)
    sc = E.session_token_cost(md, str(tmp_path / "missing-tele"), index, _HARD_SET, k=10)
    assert sc == {"avg_events_per_session": 0.0, "avg_session_tokens": 0.0, "n_sessions": 0}


# --------------------------------------------------------------------------- #
# evaluate() — the 5 core gates are untouched by the optional inputs; the RET-8
# promoted entries key strictly off whether their fixture path was provided
# --------------------------------------------------------------------------- #
def test_evaluate_core_gates_unchanged_and_promoted_gates_key_off_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)

    baseline = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10)

    rs_path = _write_relevance_set(tmp_path)
    enriched = E.evaluate(
        memory_dir=md,
        index_dir=idx,
        hard_set_path=hs_path,
        k=10,
        relevance_set_path=rs_path,
        repo_root=str(tmp_path),
        telemetry_dir=str(tmp_path / "tele"),
    )
    # The 4 deterministic CORE gates are byte-unchanged (BM25 ranking has no timing jitter).
    # recall_p95_ms is a REAL wall-clock measurement re-timed on each evaluate() call, so its
    # exact `value` legitimately varies run-to-run -- compare its threshold/pass flag only.
    for name in ("self_recall@10", "hard_recall@10", "mrr@10", "token_reduction"):
        assert enriched["gates"][name] == baseline["gates"][name], name
    assert enriched["gates"]["recall_p95_ms"]["threshold"] == baseline["gates"]["recall_p95_ms"]["threshold"]
    assert enriched["gates"]["recall_p95_ms"]["pass"] == baseline["gates"]["recall_p95_ms"]["pass"]
    # RET-8: the promoted precision gate SKIPS without a path and goes LIVE with one — its
    # value is exactly precision_at_k's (one scoring path), pass is the threshold comparison.
    assert baseline["gates"]["precision@10"]["pass"] is None
    assert baseline["gates"]["precision@10"]["skipped"] is True
    live = enriched["gates"]["precision@10"]
    assert live["value"] == enriched["precision_at_k"]["precision"]
    assert live["pass"] == (live["value"] >= E.GATE_PRECISION_AT_K)
    # comparative (QUA-10: no absolute ok assertion in a hermetic test — p95 is wall-clock):
    # a skipped entry never feeds ok; a live one does.
    assert enriched["ok"] == (baseline["ok"] and live["pass"])
    assert "staleness_half_life" in enriched
    assert "session_token_cost" in enriched


def test_evaluate_report_fields_present_and_zeroed_when_omitted(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)
    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10)
    assert report["precision_at_k"] == {"precision": 0.0, "n": 0}
    assert report["staleness_half_life"] == {"median_days": 0.0, "n": 0}  # no repo_root passed
    assert report["session_token_cost"]["n_sessions"] == 0
    assert report["abstention_rate"] == {"rate": 0.0, "n": 0}  # no abstention_set_path passed


def test_evaluate_explicit_memory_dir_stays_hermetic_no_repo_root_leak(tmp_path, monkeypatch):
    """Passing memory_dir explicitly (without repo_root) must NOT trigger an extra
    resolve_dirs() git call that resolves repo_root against the real working tree."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)
    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10)
    # repo_root was never resolved -> staleness_half_life degrades to the zeroed report,
    # never silently scanning the real ic-memobot repo's git history.
    assert report["staleness_half_life"] == {"median_days": 0.0, "n": 0}


# --------------------------------------------------------------------------- #
# Tier 3 — gates stay green with soft-invalidation ACTIVELY engaged (not just absent)
# --------------------------------------------------------------------------- #
def test_gates_unaffected_when_invalidation_marks_an_irrelevant_entry(tmp_path, monkeypatch):
    """invalid_after set on a memory NOT involved in the hard-set/self-recall queries must
    not move any of the 5 gates -- the penalty only touches the marked entry's own ranking."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)

    baseline = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10)
    assert baseline["ok"] is True

    # Mark one entry (not referenced by any hard-set query) as RECENTLY invalidated (a
    # few days old, not "old" -- "old" would drop it from display entirely, which would
    # also remove it from self_recall_at_k's check for its OWN self-derived query), then
    # force a fresh index build so build_index.compute_corpus re-ingests it.
    import datetime

    recent_ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=5)).isoformat()
    desc = _CORPUS["image_intel.md"]
    with open(os.path.join(md, "image_intel.md"), "w", encoding="utf-8") as fh:
        fh.write(
            f'---\nname: image_intel\ndescription: "{desc}"\ninvalid_after: '
            f'"{recent_ts}"\ntype: project\n---\nbody for image_intel.md\n'
        )
    B.build_index(md, idx, force=True)

    enriched = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10)
    assert enriched["ok"] is True
    for name in ("self_recall@10", "hard_recall@10", "mrr@10", "token_reduction"):
        assert enriched["gates"][name]["pass"] == baseline["gates"][name]["pass"]
        assert enriched["gates"][name]["value"] == baseline["gates"][name]["value"]


# --------------------------------------------------------------------------- #
# graduation_rate (Tier 2, report-only) — the accuracy axis
# --------------------------------------------------------------------------- #
def _seed_reconsolidation_events(td, outcomes):
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "reconsolidation_events.jsonl"), "w", encoding="utf-8") as fh:
        for name, outcome in outcomes:
            fh.write(json.dumps({"name": name, "outcome": outcome}) + "\n")


def test_graduation_rate_excludes_fix_from_ratio():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        td = os.path.join(tmp, "tele")
        _seed_reconsolidation_events(
            td,
            [("a", "graduate"), ("b", "demote"), ("c", "fix"), ("d", "graduate")],
        )
        gr = E.graduation_rate(td)
        # 2 graduate, 1 demote -> 2/3; the 1 fix is excluded from BOTH numerator and denominator
        assert gr["n"] == 3
        assert gr["rate"] == round(2 / 3, 4)
        assert gr == {"rate": round(2 / 3, 4), "n": 3, "graduate": 2, "fix": 1, "demote": 1}


def test_graduation_rate_fix_only_ledger_yields_zero_n():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        td = os.path.join(tmp, "tele")
        _seed_reconsolidation_events(td, [("a", "fix"), ("b", "fix")])
        gr = E.graduation_rate(td)
        assert gr["n"] == 0
        assert gr["rate"] == 0.0


def test_graduation_rate_empty_ledger_never_raises():
    assert E.graduation_rate("/no/such/dir") == {"rate": 0.0, "n": 0, "graduate": 0, "fix": 0, "demote": 0}


def test_graduation_rate_all_demote_is_zero():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        td = os.path.join(tmp, "tele")
        _seed_reconsolidation_events(td, [("a", "demote"), ("b", "demote")])
        gr = E.graduation_rate(td)
        assert gr["rate"] == 0.0
        assert gr["n"] == 2


def test_evaluate_includes_graduation_rate_report_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)
    td = str(tmp_path / "tele")
    _seed_reconsolidation_events(td, [("a", "graduate"), ("b", "demote")])

    baseline = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10)
    enriched = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10, telemetry_dir=td)

    assert baseline["graduation_rate"] == {"rate": 0.0, "n": 0, "graduate": 0, "fix": 0, "demote": 0}
    assert enriched["graduation_rate"] == {"rate": 0.5, "n": 2, "graduate": 1, "fix": 0, "demote": 1}
    # never a gate
    for name in ("self_recall@10", "hard_recall@10", "mrr@10", "token_reduction"):
        assert enriched["gates"][name] == baseline["gates"][name], name


def test_evaluate_graduation_rate_no_telemetry_dir_stays_hermetic(tmp_path, monkeypatch):
    """Passing memory_dir explicitly without telemetry_dir must derive the SIBLING
    telemetry dir (which won't exist under a fresh tmp corpus), not leak onto the real
    repo's reconsolidation ledger."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)
    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10)
    assert report["graduation_rate"] == {"rate": 0.0, "n": 0, "graduate": 0, "fix": 0, "demote": 0}


# --------------------------------------------------------------------------- #
# COR-2: default fixture-path resolution + fresh-corpus gate skip semantics
# --------------------------------------------------------------------------- #
def test_default_fixture_path_probes_audit_fixtures_then_tests_fixtures(tmp_path, monkeypatch):
    repo = str(tmp_path / "proj")
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)

    # Nothing anywhere -> None, so the CLI inherits evaluate()'s skip semantics
    # (the old default pointed at tests/unit/memory_tools/ — a path that exists in
    # NO repo — making gates 2-3 spuriously fail everywhere).
    assert E._default_hard_set_path() is None
    assert E._default_relevance_set_path() is None

    # Engine-repo convention: <repo>/tests/fixtures/
    tf = os.path.join(repo, "tests", "fixtures")
    os.makedirs(tf)
    repo_fixture = os.path.join(tf, "recall_hard_set.yaml")
    with open(repo_fixture, "w", encoding="utf-8") as fh:
        fh.write("- {query: q, expected: [a]}\n")
    assert E._default_hard_set_path() == repo_fixture

    # Project-local audit convention WINS over the repo fixtures.
    af = os.path.join(md, ".audit-fixtures")
    os.makedirs(af)
    audit_fixture = os.path.join(af, "recall_hard_set.yaml")
    with open(audit_fixture, "w", encoding="utf-8") as fh:
        fh.write("- {query: q, expected: [a]}\n")
    assert E._default_hard_set_path() == audit_fixture


def test_token_reduction_gate_skips_without_pretrim_snapshot(tmp_path, monkeypatch):
    """A corpus that never had an untrimmed always-load (MEMORY.full.md absent — every
    fresh install) has nothing to compare against: net == -recall_avg would spuriously
    fail the gate in EVERY fresh project. It must skip, not fail."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    os.remove(os.path.join(md, "MEMORY.full.md"))
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)
    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10)
    g = report["gates"]["token_reduction"]
    assert g["pass"] is None and g.get("skipped") is True
    assert report["ok"] is True, report["gates"]  # a skipped gate never fails the run
    # value still REPORTED (honest), just not gated
    assert "value" in g and "pct" in g


def test_main_bare_cli_is_honest_on_a_fresh_corpus(tmp_path, monkeypatch, capsys):
    """The documented merge-gate command `python -m memory.eval_recall` (no flags) must
    pass/skip honestly on a fresh corpus with no fixtures — not report spurious gate
    failures from a fossil default path."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo = str(tmp_path / "proj")
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    for fname, desc in _CORPUS.items():
        with open(os.path.join(md, fname), "w", encoding="utf-8") as fh:
            fh.write(_mem(fname[:-3], desc))
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# Floor\n## User\n")  # fresh-install shape: floor, no MEMORY.full.md
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)

    rc = E.main(["--memory-dir", md, "--index-dir", str(tmp_path / ".memory-index")])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "ALL GATES PASS" in out
    assert "skipped" in out  # the skipped gates say so instead of rendering ❌
    assert "❌" not in out


# --------------------------------------------------------------------------- #
# RET-2: body_probe — report-only metric, never a merge gate
# --------------------------------------------------------------------------- #
_DISTINCTIVE_BODY = (
    "## Error signature\n"
    "The exact failure is a zqxwyvutplaceholder timeout raised from the network layer "
    "when the retry budget is exhausted before the handshake completes successfully.\n\n"
    "## Root cause\n"
    "A misconfigured connection pool size caused exhaustion under load during peak traffic "
    "hours across every affected region consistently.\n"
)


def _mem_with_body(name: str, description: str, body: str) -> str:
    return f'---\nname: {name}\ndescription: "{description}"\ntype: project\n---\n{body}\n'


def test_derive_body_probe_query_excludes_description_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "incident.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem_with_body("incident", "a generic description here", _DISTINCTIVE_BODY))
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)

    q = E.derive_body_probe_query(index, 0)
    assert q  # a qualifying probe was derived
    desc_tokens = set(B.tokenize("a generic description here"))
    probe_tokens = set(q.split())
    assert not (probe_tokens & desc_tokens)  # every probe token is body-only
    assert "zqxwyvutplaceholder" in probe_tokens


def test_derive_body_probe_query_empty_when_no_body_chunks(tmp_path, monkeypatch):
    """A memory with no qualifying body chunks (trivial/short body) yields "" -- excluded
    from body_probe_recall_at_k's denominator, never a spurious miss."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "trivial.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem_with_body("trivial", "a generic description", "tiny"))
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)
    assert E.derive_body_probe_query(index, 0) == ""


def test_body_probe_recall_at_k_measures_parent_recall(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "incident.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem_with_body("incident", "a generic description", _DISTINCTIVE_BODY))
    with open(os.path.join(md, "other.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem_with_body("other", "an unrelated description", "unrelated filler body content here today"))
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)

    result = E.body_probe_recall_at_k(index, k=10)
    assert result["n"] >= 1
    assert result["recall"] == 1.0  # the one qualifying probe finds its own parent


def test_body_probe_recall_zero_n_when_no_body_chunks_anywhere(tmp_path, monkeypatch):
    """A corpus with NO qualifying body chunks (e.g. every body is trivial, or a pre-RET-2
    manifest with no body_chunks key at all) reports n=0, recall=0.0 -- never raises."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)  # this fixture's bodies are all "body for {name}" -- trivial
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)
    result = E.body_probe_recall_at_k(index, k=10)
    assert result == {"recall": 0.0, "n": 0}


def test_evaluate_includes_body_probe_report_only(tmp_path, monkeypatch):
    """body_probe is threaded through evaluate()'s report but NEVER feeds `ok` / a gate."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "incident.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem_with_body("incident", "a generic description", _DISTINCTIVE_BODY))
    idx = str(tmp_path / ".memory-index")
    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=None, k=10)
    assert "body_probe" in report
    assert set(report["body_probe"].keys()) == {"recall", "n"}
    assert "body_probe" not in report["gates"]  # never a 6th gate


def test_main_cli_prints_body_probe_line_when_present(tmp_path, monkeypatch, capsys):
    """Mirrors test_main_bare_cli_is_honest_on_a_fresh_corpus's env setup — the DEFAULT
    (no --hard-set/--relevance-set flags) CLI path calls _default_hard_set_path(), which
    resolves via the ambient resolve_dirs(); pointing CLAUDE_PROJECT_DIR/HIPPO_MEMORY_DIR
    at this tmp project (with no .audit-fixtures dir) makes that resolve to "no fixture
    found" -- fresh-install shape -- rather than accidentally discovering THIS repo's own
    tests/fixtures/recall_hard_set.yaml."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo = str(tmp_path / "proj")
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    with open(os.path.join(md, "incident.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem_with_body("incident", "a generic description", _DISTINCTIVE_BODY))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)

    rc = E.main(["--memory-dir", md, "--index-dir", str(tmp_path / ".memory-index")])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "body_probe" in out
    assert "report-only" in out


# --------------------------------------------------------------------------- #
# RET-7: serving-backend recording + fixture-provenance mismatch detection
#
# The point of this cluster: a BM25-only pass must never be mistakable for verified hybrid
# (dense+bm25) recall health -- both in the report dict (``report["backend"]``) and in the
# two printed lines a human/CI log actually skims (the gate-header line and the RESULT
# line). Every test below runs BM25-only (HIPPO_DISABLE_DENSE=1, per this suite's
# hermeticity requirement -- no fastembed model on disk in CI's hermetic lane) EXCEPT the
# ones that specifically assert the mismatch does NOT fire on an honest bm25-only fixture
# or one with no header at all, which is exactly the case this whole suite already runs in.
# --------------------------------------------------------------------------- #
def test_evaluate_reports_bm25_only_backend_when_dense_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)
    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10)
    assert report["dense_ready"] is False
    assert report["backend"] == "bm25-only"
    assert report["backend_mismatch"] is False  # no header on this fixture -> never fires


def test_backend_field_tracks_dense_ready_directly(tmp_path, monkeypatch):
    """``backend`` must be DERIVED from ``index.dense_ready`` (the same torn-pair-verified
    signal build_index already exposes, COR-3) rather than re-derived independently --
    forcing dense_ready True on a hermetic (no real model) index proves the field really
    reads the index's own flag rather than e.g. always defaulting to bm25-only."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)
    assert index.dense_ready is False  # sanity: hermetic build is BM25-only

    # Monkeypatch dense_ready True on the loaded index object itself (no real dense.npy
    # needed) to prove evaluate()'s backend label is READ off index.dense_ready, not
    # independently re-derived from env/config.
    monkeypatch.setattr(E, "load_index", lambda _idx_dir: _with_dense_ready(index, True))
    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=None, k=10)
    assert report["backend"] == "dense+bm25"


def _with_dense_ready(index, value):
    import copy

    forged = copy.copy(index)
    forged.dense_ready = value
    return forged


def test_main_cli_result_line_labels_bm25_only_backend(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)
    rc = E.main(["--memory-dir", md, "--index-dir", idx, "--hard-set", hs_path])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "backend=bm25-only" in out  # gate-header line
    assert "RESULT: ALL GATES PASS" in out
    assert "[backend=bm25-only — dense path unverified]" in out  # RESULT line


def test_main_cli_result_line_labels_dense_bm25_backend(tmp_path, monkeypatch, capsys):
    """Same CLI path, but with a FORGED dense_ready index (no real fastembed model needed)
    -- proves the "dense+bm25" branch of the RESULT-line label renders correctly too, not
    just the bm25-only branch every other test in this file exercises."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)
    B.build_index(md, idx)
    real_index = B.load_index(idx)
    monkeypatch.setattr(E, "load_index", lambda _idx_dir: _with_dense_ready(real_index, True))

    rc = E.main(["--memory-dir", md, "--index-dir", idx, "--hard-set", hs_path])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "backend=dense+bm25" in out
    assert "RESULT: ALL GATES PASS" in out
    assert "[backend=dense+bm25]" in out
    assert "dense path unverified" not in out


# --------------------------------------------------------------------------- #
# RET-7: fixture provenance metadata header — optional, backward-compatible
# --------------------------------------------------------------------------- #
def test_load_hard_set_metadata_empty_for_bare_list_fixture(tmp_path):
    """The pre-existing bare-list schema (no header at all) is a fully valid fixture with
    NO metadata -- this is the backward-compat guarantee: every fixture written before
    RET-7 keeps loading exactly as before, just with an empty metadata dict available."""
    hs_path = _write_hard_set(tmp_path)  # plain yaml.safe_dump of a list -- no header
    assert E.load_hard_set_metadata(hs_path) == {}
    # rows themselves are unaffected (RET-8 adds only the defaulted category tag)
    assert E.load_hard_set(hs_path) == [{**r, "category": "single-hop"} for r in _HARD_SET]


def test_load_hard_set_metadata_parses_leading_header_doc(tmp_path):
    p = str(tmp_path / "hard_set_with_header.yaml")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(
            "generated_with_backend: dense+bm25\n"
            "generated_at: '2026-07-06'\n"
            "---\n"
            "- query: q1\n"
            "  expected: [a]\n"
        )
    meta = E.load_hard_set_metadata(p)
    assert meta["generated_with_backend"] == "dense+bm25"
    assert meta["generated_at"] == "2026-07-06"
    # rows still load correctly from the SECOND document
    rows = E.load_hard_set(p)
    assert rows == [{"query": "q1", "expected": ["a"], "category": "single-hop"}]


def test_load_hard_set_metadata_missing_file_is_empty():
    assert E.load_hard_set_metadata("/no/such/file.yaml") == {}


def test_load_hard_set_metadata_bm25_only_header_is_not_dense_claim(tmp_path):
    """A fixture honestly generated bm25-only (or with any other backend value) is a
    perfectly valid input -- the metadata loader is a plain passthrough, not a validator;
    it's ``evaluate()``'s mismatch check that treats ONLY the literal 'dense+bm25' value
    as a claim worth cross-checking."""
    p = str(tmp_path / "hard_set_bm25.yaml")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("generated_with_backend: bm25-only\n---\n- query: q1\n  expected: [a]\n")
    meta = E.load_hard_set_metadata(p)
    assert meta["generated_with_backend"] == "bm25-only"


# --------------------------------------------------------------------------- #
# RET-7: backend_mismatch fires ONLY on dense-generated fixture + bm25-served run
# --------------------------------------------------------------------------- #
def _write_hard_set_with_header(tmp_path, backend_claim):
    import yaml

    p = str(tmp_path / "hard_set_hdr.yaml")
    header = f"generated_with_backend: {backend_claim}\ngenerated_at: '2026-07-06'\n"
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(header)
        fh.write("---\n")
        yaml.safe_dump(_HARD_SET, fh)
    return p


def test_backend_mismatch_fires_when_dense_generated_fixture_served_bm25_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set_with_header(tmp_path, "dense+bm25")
    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10)
    assert report["backend"] == "bm25-only"
    assert report["backend_mismatch"] is True


def test_backend_mismatch_does_not_fire_when_fixture_claims_bm25_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set_with_header(tmp_path, "bm25-only")
    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10)
    assert report["backend_mismatch"] is False


def test_backend_mismatch_does_not_fire_when_fixture_has_no_header(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)  # bare-list, no header at all
    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10)
    assert report["backend_mismatch"] is False


def test_backend_mismatch_does_not_fire_when_dense_claimed_and_actually_served(tmp_path, monkeypatch):
    """A dense-generated fixture served by an ACTUALLY-dense-ready run is exactly the
    healthy case this whole mechanism exists to distinguish from the mismatch above --
    must never false-positive here. Forges dense_ready on the loaded index (no real
    fastembed model needed) so this stays hermetic."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set_with_header(tmp_path, "dense+bm25")
    B.build_index(md, idx)
    real_index = B.load_index(idx)
    monkeypatch.setattr(E, "load_index", lambda _idx_dir: _with_dense_ready(real_index, True))
    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10)
    assert report["backend"] == "dense+bm25"
    assert report["backend_mismatch"] is False


def test_main_cli_prints_loud_warning_on_backend_mismatch(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set_with_header(tmp_path, "dense+bm25")
    rc = E.main(["--memory-dir", md, "--index-dir", idx, "--hard-set", hs_path])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "BACKEND MISMATCH" in out
    assert "[FIXTURE/BACKEND MISMATCH]" in out  # appended to the RESULT line too


def test_main_cli_no_mismatch_warning_when_backends_agree(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)  # no header -> no claim -> no mismatch possible
    rc = E.main(["--memory-dir", md, "--index-dir", idx, "--hard-set", hs_path])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "BACKEND MISMATCH" not in out
    assert "FIXTURE/BACKEND MISMATCH" not in out


# --------------------------------------------------------------------------- #
# RET-7: _default_fixture_path probe order is unaffected by the metadata-header change
# (COR-2's probe order is load-bearing -- re-pin it here since this item touches the same
# loader functions COR-2's own test already covers, to catch any regression in this item).
# --------------------------------------------------------------------------- #
def test_default_fixture_path_probe_order_unaffected_by_ret7(tmp_path, monkeypatch):
    repo = str(tmp_path / "proj2")
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)

    assert E._default_hard_set_path() is None

    tf = os.path.join(repo, "tests", "fixtures")
    os.makedirs(tf)
    repo_fixture = os.path.join(tf, "recall_hard_set.yaml")
    with open(repo_fixture, "w", encoding="utf-8") as fh:
        # this candidate carries a header now -- still discoverable via the same probe order
        fh.write("generated_with_backend: dense+bm25\n---\n- {query: q, expected: [a]}\n")
    assert E._default_hard_set_path() == repo_fixture
    assert E.load_hard_set_metadata(repo_fixture)["generated_with_backend"] == "dense+bm25"

    af = os.path.join(md, ".audit-fixtures")
    os.makedirs(af)
    audit_fixture = os.path.join(af, "recall_hard_set.yaml")
    with open(audit_fixture, "w", encoding="utf-8") as fh:
        fh.write("- {query: q, expected: [a]}\n")  # no header at all -- still wins by path
    assert E._default_hard_set_path() == audit_fixture
    assert E.load_hard_set_metadata(audit_fixture) == {}


# --------------------------------------------------------------------------- #
# QUA-6, leg 1 — exact-value gate-constant pins.
#
# Every OTHER test in this module (and the tmp corpora above) is deliberately engineered to
# score ~1.0 on all 5 gates, which means a threshold EDIT (someone loosens GATE_HARD_RECALL
# from 0.80 to 0.60 "to make CI green again") passes every existing test silently -- nothing
# here notices the merge bar moved. Pinning the exact literal values makes any such edit a
# visible, deliberate two-place change: the constant in eval_recall.py AND this pin, both
# touched in the same diff, both reviewable. A one-sided edit (constant only) is a red build.
# --------------------------------------------------------------------------- #
def test_gate_constants_are_pinned_exact_values():
    assert E.GATE_SELF_RECALL == 0.90
    assert E.GATE_HARD_RECALL == 0.80
    assert E.GATE_MRR == 0.60
    assert E.GATE_P95_MS == 300.0
    # PRF-2: cold_p95_ms's budget -- see GATE_COLD_P95_MS's docstring for why 1500ms (the
    # honest fresh-subprocess-per-sample number the warm p95 gate above understates ~10x).
    assert E.GATE_COLD_P95_MS == 1500.0


def test_token_reduction_gate_is_net_greater_than_zero(tmp_path, monkeypatch):
    """Pin the token-reduction gate's THRESHOLD EXPRESSION itself (net > 0), not just that a
    particular tmp corpus happens to satisfy it -- ``test_token_reduction_gate`` above already
    covers the latter. A future rewrite of ``token_reduction`` that silently changes what
    "pass" means (e.g. net >= 0, or a nonzero pct instead of net) should fail this pin even if
    every engineered-to-pass corpus in the suite still scores comfortably positive."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)
    tok = E.token_reduction(md, index, _HARD_SET, k=10)
    # The gate dict's "pass" flag is exactly `net > 0` (see evaluate()'s `gates["token_reduction"]`
    # construction) -- reproduce that exact boolean expression here so an edit to `>=` (which
    # would let a net of exactly 0 -- no reduction at all -- pass) fails this pin.
    assert (tok["net"] > 0) is True
    hs_path = _write_hard_set(tmp_path)
    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10)
    assert report["gates"]["token_reduction"]["pass"] == (tok["net"] > 0)


# --------------------------------------------------------------------------- #
# QUA-6, leg 2 — golden-corpus dense-ranking regression tripwire.
#
# tests/golden_corpus/ ships a checked-in, deterministic, human-readable ~50-memory
# hippo-native corpus (git workflows, debugging, deployments, testing, i18n, perf,
# architecture decisions, tooling...) plus a ~15-query cross-vocabulary paraphrase hard-set.
# Unlike every OTHER corpus in this file (engineered with near-zero lexical overlap so BM25
# alone trivially clears the gates), the golden corpus is realistic -- paraphrases sit close
# enough in vocabulary that BM25 mostly finds them too, so this is a genuine ranking-QUALITY
# probe (via MRR) rather than a pure recall/miss probe.
#
# Actuals measured on 2026-07-06 against this exact corpus + hard-set (recorded here AND in
# the commit body per the roadmap item's instructions):
#   dense (fastembed model warm)  : recall@10 = 1.0000, mrr@10 = 0.9352, self_recall@10 = 1.0000
#   bm25-only (HIPPO_DISABLE_DENSE=1): recall@10 = 1.0000, mrr@10 = 0.9120, self_recall@10 = 1.0000
#
# Band floors are set a small margin (0.05) below each measured actual -- tight enough to
# catch a real regression (a model bump, a fusion-weight change, a soft-invalidation
# threshold drift that demotes correct hits), loose enough to absorb ordinary run-to-run
# jitter (tie-breaking among equal-score candidates, floating-point summation order).
#
# RET-1 re-measured (same day, after adding the dense floor/knee cutoff/hard-skip): dense
# recall@10 = 1.0000, mrr@10 = 0.9306 (a small, expected shift -- the knee cutoff's
# "primary-relevance" comparison can reorder ties near the tail differently than the
# pre-RET-1 raw fused score did); bm25-only recall@10 = 1.0000, mrr@10 = 0.9120 (BYTE
# unchanged -- the floor/knee only touch the DENSE ranking, and this run is dense-disabled).
# Both stay comfortably inside the bands below -- no floor/threshold edit needed.
# --------------------------------------------------------------------------- #
_GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "golden_corpus")
_GOLDEN_MEMORY_DIR = os.path.join(_GOLDEN_DIR, "memory")
_GOLDEN_HARD_SET_PATH = os.path.join(_GOLDEN_DIR, "hard_set.yaml")

# Dense floors (measured actual - 0.05; see docstring above for the measured actuals).
_GOLDEN_DENSE_RECALL_FLOOR = 0.95
_GOLDEN_DENSE_MRR_FLOOR = 0.8852
_GOLDEN_DENSE_SELF_RECALL_FLOOR = 0.95

# BM25-only floors -- deliberately LOWER than the dense floors: this is the hermetic
# companion's own measured band, not a relaxed version of the dense one (see docstring).
_GOLDEN_BM25_RECALL_FLOOR = 0.95
_GOLDEN_BM25_MRR_FLOOR = 0.862
_GOLDEN_BM25_SELF_RECALL_FLOOR = 0.95


def test_golden_corpus_hard_set_loads_and_targets_exist():
    """Hermetic sanity check independent of any index build: the fixture parses, has the
    documented ~15-ish query count, and every expected stem actually ships in the corpus --
    catches a typo'd stem silently zeroing out that query's contribution to recall/MRR."""
    hard_set = E.load_hard_set(_GOLDEN_HARD_SET_PATH)
    assert 12 <= len(hard_set) <= 20
    stems = {
        fn[:-3] for fn in os.listdir(_GOLDEN_MEMORY_DIR) if fn.endswith(".md")
    }
    assert 45 <= len(stems) <= 55  # "~50" per the roadmap item
    for item in hard_set:
        for expected in item["expected"]:
            assert expected in stems, f"hard-set expects {expected!r}, not in golden corpus"


def test_golden_corpus_bm25_only_recall_and_mrr_within_band(tmp_path, monkeypatch):
    """HERMETIC companion (no fastembed, no network): gives the hermetic CI lanes real golden
    signal instead of only the network-marked dense test below. Its floors are its OWN
    measured band (not the dense test's floors relaxed) -- see the module docstring."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    idx = str(tmp_path / ".memory-index")
    manifest = B.build_index(_GOLDEN_MEMORY_DIR, idx)
    assert manifest["dense_ready"] is False  # confirms this run is genuinely BM25-only

    index = B.load_index(idx)
    hard_set = E.load_hard_set(_GOLDEN_HARD_SET_PATH)

    hs = E.hard_set_metrics(index, hard_set, k=10)
    assert hs["recall"] >= _GOLDEN_BM25_RECALL_FLOOR, hs
    assert hs["mrr"] >= _GOLDEN_BM25_MRR_FLOOR, hs

    sr = E.self_recall_at_k(index, k=10)
    assert sr >= _GOLDEN_BM25_SELF_RECALL_FLOOR, sr


@pytest.mark.network
def test_golden_corpus_dense_recall_and_mrr_within_band(tmp_path, monkeypatch, tmp_path_factory):
    """The dense-ranking regression tripwire: builds a REAL dense index (fastembed model,
    hybrid RRF fusion) over the golden corpus and asserts hard-set recall@10 / MRR@10 stay
    within a band a small margin below the measured actuals -- a model bump, a fusion-weight
    change, or a soft-invalidation threshold drift that quietly demotes correct hits would
    trip this even though it might not touch recall@10 (which mostly stays 1.0 on a small
    corpus) -- MRR is the more sensitive of the two signals here (see module docstring).

    Network-marked (deselected by default) per the QUA-3 pattern in test_build_index.py: a
    cold model cache would download the ~130MB fastembed ONNX model. CI's dense lane restores
    a cached model via actions/cache and opts in with `-m network`; this machine has a warm
    cache already.
    """
    pytest.importorskip("fastembed")
    # QUA-3 pattern: honor a caller-provided FASTEMBED_CACHE_PATH (CI's dense lane points this
    # at the actions-restored cache) else a session-scoped tmp dir -- NEVER the user's real
    # home cache.
    cache = os.environ.get("FASTEMBED_CACHE_PATH") or str(
        tmp_path_factory.getbasetemp() / "fastembed-cache"
    )
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", cache)
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)

    idx = str(tmp_path / ".memory-index")
    manifest = B.build_index(_GOLDEN_MEMORY_DIR, idx)
    assert manifest["dense_ready"] is True  # confirms this run is genuinely dense+bm25

    index = B.load_index(idx)
    hard_set = E.load_hard_set(_GOLDEN_HARD_SET_PATH)

    hs = E.hard_set_metrics(index, hard_set, k=10)
    assert hs["recall"] >= _GOLDEN_DENSE_RECALL_FLOOR, hs
    assert hs["mrr"] >= _GOLDEN_DENSE_MRR_FLOOR, hs

    sr = E.self_recall_at_k(index, k=10)
    assert sr >= _GOLDEN_DENSE_SELF_RECALL_FLOOR, sr


# --------------------------------------------------------------------------- #
# RET-1: golden-corpus abstention companion (report-only, never a merge gate) — proves the
# dense floor/knee/hard-skip trio abstains on genuinely off-topic queries against a REALISTIC
# (not artificially zero-overlap) hippo-native corpus, not just the tiny engineered ones
# elsewhere in this file.
# --------------------------------------------------------------------------- #
_GOLDEN_ABSTENTION_SET_PATH = os.path.join(_GOLDEN_DIR, "abstention_set.yaml")


def test_golden_corpus_abstention_set_loads():
    abstention_set = E.load_abstention_set(_GOLDEN_ABSTENTION_SET_PATH)
    assert 4 <= len(abstention_set) <= 10  # "~6" per the roadmap item


@pytest.mark.network
def test_golden_corpus_dense_abstention_rate_high(tmp_path, monkeypatch, tmp_path_factory):
    """With the REAL fastembed model, the calibrated dense floor must abstain on MOST of the
    golden abstention set's clearly off-topic queries -- report-only (never a merge gate),
    but this is the metric that PROVES the floor actually does something on a realistic
    corpus, not just the tiny hand-engineered ones."""
    pytest.importorskip("fastembed")
    cache = os.environ.get("FASTEMBED_CACHE_PATH") or str(
        tmp_path_factory.getbasetemp() / "fastembed-cache"
    )
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", cache)
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)

    idx = str(tmp_path / ".memory-index")
    manifest = B.build_index(_GOLDEN_MEMORY_DIR, idx)
    assert manifest["dense_ready"] is True

    index = B.load_index(idx)
    abstention_set = E.load_abstention_set(_GOLDEN_ABSTENTION_SET_PATH)
    rate = E.abstention_rate(index, abstention_set, k=10)
    # Measured 2026-07-06: 1/6 (0.1667) -- LOWER than the pack-corpus companion's 2/6,
    # because this ~50-memory realistic corpus gives common English words in the off-topic
    # probes (e.g. "week", "history", "just") more chances at a coincidental BM25
    # token-overlap hit than the smaller shipped-pack corpus does (see the RET-1 commit
    # body's before/after table for the full breakdown). This is NOT a floor failure -- it
    # is BM25's match-set filter (deliberately unfloored per the roadmap: "BM25's match-set
    # filter already IS its floor") admitting a query on a single common-word overlap. The
    # bound here is a REGRESSION TRIPWIRE (this run must not get WORSE than measured), not a
    # target -- a future BM25/tokenization change that pushes this to 0.0 (dense's floor
    # alone can't compensate for BM25 finding SOMETHING) should fail this test.
    assert rate["rate"] >= 0.15, rate


# --------------------------------------------------------------------------- #
# RET-8: category-tagged eval — loader tags, per-category buckets, the promoted
# gates' three-way skip/fail/pass split, the index_dir edge thread, and the
# CLI's per-category + hermeticity behavior
# --------------------------------------------------------------------------- #
def test_load_hard_set_category_default_and_passthrough(tmp_path):
    p = str(tmp_path / "cats.yaml")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(
            "- query: q1\n  expected: [a]\n"
            "- query: q2\n  expected: [b]\n  category: multi-hop\n"
            "- query: q3\n  expected: [c]\n  category: somebody-elses-tag\n"
        )
    rows = E.load_hard_set(p)
    assert [r["category"] for r in rows] == ["single-hop", "multi-hop", "somebody-elses-tag"]
    assert "single-hop" in E.CATEGORIES and "abstention" in E.CATEGORIES


def test_by_category_buckets_delegate_to_the_one_scoring_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)
    tagged = [
        {**_HARD_SET[0], "category": "update"},
        {**_HARD_SET[1], "category": "update"},
        {**_HARD_SET[2]},  # untagged -> single-hop
    ]
    by_cat = E.hard_set_metrics_by_category(index, tagged, k=10)
    assert sorted(by_cat) == ["single-hop", "update"]
    assert by_cat["update"]["n"] == 2 and by_cat["single-hop"]["n"] == 1
    # delegation: each bucket's numbers ARE hard_set_metrics on that subset
    assert by_cat["update"] == E.hard_set_metrics(index, tagged[:2], k=10)
    assert sum(m["n"] for m in by_cat.values()) == len(tagged)


def _build_linked_corpus(tmp_path):
    """A BM25 corpus where `hidden_gem` shares ZERO vocabulary with the seed query and is
    reachable ONLY via the seed's [[wikilink]] — the exact case GRA-1 exists for."""
    md = str(tmp_path / "memory")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "seed_topic.md"), "w", encoding="utf-8") as fh:
        fh.write(
            '---\nname: seed_topic\ndescription: "vendor cascade timeout recurrence '
            'diagnosis discipline"\ntype: project\n---\nSee [[hidden_gem]] for the deeper rule.\n'
        )
    with open(os.path.join(md, "hidden_gem.md"), "w", encoding="utf-8") as fh:
        fh.write(
            '---\nname: hidden_gem\ndescription: "quiet unrelated lexicon entirely '
            'disjoint wording"\ntype: project\n---\nbody\n'
        )
    with open(os.path.join(md, "bystander.md"), "w", encoding="utf-8") as fh:
        fh.write(
            '---\nname: bystander\ndescription: "vendor cascade adjacent chatter '
            'noise"\ntype: project\n---\nbody\n'
        )
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# Floor\n## User\n## Working Style\n")
    return md


def test_multi_hop_category_measures_graph_expansion_via_index_dir(tmp_path, monkeypatch):
    """The RET-8 substrate correction pinned end-to-end: WITH index_dir threaded the eval
    sees GRA-1 expansion (the linked, zero-overlap neighbor lands in the top-k and the
    multi-hop bucket scores 1.0); WITHOUT it (the pre-RET-8 bare-index shape) the same
    fixture scores 0.0 — the eval was structurally edge-blind."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_linked_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)
    hard = [{"query": "vendor cascade timeout recurrence", "expected": ["hidden_gem"],
             "category": "multi-hop"}]
    with_edges = E.hard_set_metrics_by_category(index, hard, k=10, index_dir=idx)
    assert with_edges["multi-hop"]["recall"] == 1.0, with_edges
    without = E.hard_set_metrics_by_category(index, hard, k=10)
    assert without["multi-hop"]["recall"] == 0.0, without


def test_temporal_category_measures_supersession_via_index_dir(tmp_path, monkeypatch):
    """A temporal case: the query matches the SUPERSEDED memory's vocabulary at least as
    well as its successor's, and the GRA-4 typed-edge penalty (loaded via the same
    index_dir thread) is what puts the successor ABOVE the loser."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "deploy_old.md"), "w", encoding="utf-8") as fh:
        fh.write(
            '---\nname: deploy_old\ndescription: "deploy release pipeline makefile '
            'runbook steps"\ntype: project\n---\nbody\n'
        )
    with open(os.path.join(md, "deploy_new.md"), "w", encoding="utf-8") as fh:
        fh.write(
            '---\nname: deploy_new\ndescription: "deploy release pipeline current '
            'runbook"\ntype: project\nsupersedes: [deploy_old]\n---\nbody\n'
        )
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# Floor\n## User\n## Working Style\n")
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)
    hard = [{"query": "deploy release pipeline runbook", "expected": ["deploy_new"],
             "category": "temporal"}]
    by_cat = E.hard_set_metrics_by_category(index, hard, k=10, index_dir=idx)
    assert by_cat["temporal"]["recall"] == 1.0
    # the mechanism, not luck: with the typed edge loaded the successor OUTRANKS the loser
    from memory.recall import recall

    ranked = [r["name"] for r in recall(hard[0]["query"], k=10, index=index, index_dir=idx)]
    assert ranked.index("deploy_new") < ranked.index("deploy_old"), ranked


def test_precision_gate_provided_but_empty_fixture_fails_loud(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)
    empty = str(tmp_path / "empty_relevance.yaml")
    with open(empty, "w", encoding="utf-8") as fh:
        fh.write("[]\n")
    report = E.evaluate(
        memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10, relevance_set_path=empty
    )
    assert report["gates"]["precision@10"]["pass"] is False
    assert report["ok"] is False


def test_main_cli_prints_per_category_lines(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    import yaml

    p = str(tmp_path / "cats.yaml")
    rows = [dict(_HARD_SET[0]), {**_HARD_SET[1], "category": "update"}]
    with open(p, "w", encoding="utf-8") as fh:
        yaml.safe_dump(rows, fh)
    rc = E.main(["--memory-dir", md, "--index-dir", idx, "--hard-set", p])
    out = capsys.readouterr().out
    assert rc == 0
    assert "category single-hop " in out and "category update " in out
    assert "n=1 (RET-8)" in out


def test_main_cli_explicit_memory_dir_never_inherits_ambient_fixtures(tmp_path, monkeypatch):
    """The RET-8 CLI hermeticity guard: an explicit --memory-dir must not be judged by the
    AMBIENT corpus's default fixtures (this repo's tests/fixtures — which is exactly what
    happens in this test's cwd) — those gates skip instead."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")

    captured = {}
    real_evaluate = E.evaluate

    def _spy(*args, **kwargs):
        captured.update(kwargs)
        return real_evaluate(*args, **kwargs)

    monkeypatch.setattr(E, "evaluate", _spy)
    rc = E.main(["--memory-dir", md, "--index-dir", idx])
    assert rc == 0
    assert captured["hard_set_path"] is None
    assert captured["relevance_set_path"] is None
    assert captured["abstention_set_path"] is None


# --------------------------------------------------------------------------- #
# MSR-1: run ledger + fingerprint-keyed baseline diff + pass^k determinism probe.
# All report-only: gate constants byte-unchanged (pinned above), default rendering
# untouched, baseline drift NEVER fails a run, no new CI-failing check.
# --------------------------------------------------------------------------- #
def _msr1_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)
    return md, idx, hs_path


def test_json_prints_one_parseable_report_line(tmp_path, monkeypatch, capsys):
    md, idx, hs_path = _msr1_setup(tmp_path, monkeypatch)
    rc = E.main(["--memory-dir", md, "--index-dir", idx, "--hard-set", hs_path, "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    report = json.loads(out.splitlines()[0])
    assert "gates" in report and "by_category" in report
    # The human table is fully replaced — no gate glyph lines after the JSON.
    assert "RESULT:" not in out


def test_default_render_carries_no_msr1_lines(tmp_path, monkeypatch, capsys):
    """Additive-only: a run without the new flags prints exactly the pre-MSR-1 surface."""
    md, idx, hs_path = _msr1_setup(tmp_path, monkeypatch)
    rc = E.main(["--memory-dir", md, "--index-dir", idx, "--hard-set", hs_path])
    out = capsys.readouterr().out
    assert rc == 0
    assert "run ledger" not in out and "baseline" not in out and "pass^" not in out


def test_out_appends_fingerprinted_run_ledger(tmp_path, monkeypatch, capsys):
    md, idx, hs_path = _msr1_setup(tmp_path, monkeypatch)
    ledger = str(tmp_path / "telemetry" / "eval_runs.jsonl")
    for _ in range(2):
        rc = E.main(
            ["--memory-dir", md, "--index-dir", idx, "--hard-set", hs_path, "--out", ledger]
        )
        assert rc == 0
    capsys.readouterr()
    rows = [json.loads(ln) for ln in open(ledger, encoding="utf-8") if ln.strip()]
    assert len(rows) == 2  # append-only: the second run never clobbers the first
    for row in rows:
        assert row["fixture_fingerprint"] and row["corpus_fingerprint"]
        assert row["report"]["gates"]["self_recall@10"]["value"] == 1.0
    # SEC-3: the ledger dir self-ignores — raw run rows are never a `git add .` away.
    assert os.path.exists(os.path.join(os.path.dirname(ledger), ".gitignore"))


def test_out_default_path_is_the_sibling_telemetry_dir(tmp_path, monkeypatch, capsys):
    md, idx, hs_path = _msr1_setup(tmp_path, monkeypatch)
    rc = E.main(["--memory-dir", md, "--index-dir", idx, "--hard-set", hs_path, "--out"])
    assert rc == 0
    capsys.readouterr()
    expected = os.path.join(os.path.dirname(md), ".memory-telemetry", "eval_runs.jsonl")
    assert os.path.exists(expected)


def test_deterministic_view_excludes_exactly_the_volatile_keys():
    report = {
        "ok": True,
        "gates": {
            "self_recall@10": {"value": 1.0},
            "recall_p95_ms": {"value": 3.2},
            "cold_p95_ms": {"value": 900.0},
        },
        "latency": {"p95": 3.2},
        "cold_latency": {"p95": 900.0},
        "staleness_half_life": {"median_days": 4.2, "n": 3},
        "by_category": {"single-hop": {"recall": 1.0, "mrr": 1.0, "n": 6}},
        "count": 8,
    }
    view = E.deterministic_view(report)
    assert set(view) == {"gates", "by_category", "count"}
    assert set(view["gates"]) == {"self_recall@10"}
    # The caller's report is never mutated (the run ledger serializes it AFTER this).
    assert "latency" in report and "recall_p95_ms" in report["gates"]


def test_canonical_view_is_latency_blind_but_metric_sensitive():
    a = {"gates": {"mrr@10": {"value": 0.9}}, "latency": {"p95": 1.0}}
    b = {"gates": {"mrr@10": {"value": 0.9}}, "latency": {"p95": 99.0}}
    c = {"gates": {"mrr@10": {"value": 0.8}}, "latency": {"p95": 1.0}}
    ca = E.canonical_json(E.deterministic_view(a))
    assert ca == E.canonical_json(E.deterministic_view(b))  # latency never breaks identity
    assert ca != E.canonical_json(E.deterministic_view(c))  # a metric delta always does


def test_corpus_fingerprint_reuses_the_refresh_compare_fields(tmp_path, monkeypatch):
    md, idx, _hs = _msr1_setup(tmp_path, monkeypatch)
    B.build_index(md, idx)
    fp1 = E.corpus_fingerprint(B.load_index(idx))
    fp2 = E.corpus_fingerprint(B.load_index(idx))
    assert fp1 == fp2  # same index -> same identity
    # A content edit (entry hash moves — the first refresh_index compare field) re-keys it.
    with open(os.path.join(md, "ducklake.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem("ducklake", "observability warehouse catalog rewritten entirely"))
    B.build_index(md, idx)
    assert E.corpus_fingerprint(B.load_index(idx)) != fp1


def test_fixture_fingerprint_distinguishes_absent_from_present(tmp_path):
    p = str(tmp_path / "f.yaml")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("- query: q\n  expected: [x]\n")
    assert E.fixture_fingerprint(p, None) != E.fixture_fingerprint(None, p)
    assert E.fixture_fingerprint(p) == E.fixture_fingerprint(p)


def test_write_baseline_then_no_drift(tmp_path, monkeypatch, capsys):
    md, idx, hs_path = _msr1_setup(tmp_path, monkeypatch)
    args = ["--memory-dir", md, "--index-dir", idx, "--hard-set", hs_path]
    assert E.main(args + ["--write-baseline"]) == 0
    pinned = os.path.join(md, ".audit-fixtures", "recall_eval_baseline.json")
    assert os.path.exists(pinned)
    capsys.readouterr()
    rc = E.main(args + ["--baseline"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "no drift" in out
    assert "report-only" in out  # the deferred-ratchet note prints every time


def test_baseline_fingerprint_mismatch_skips_loudly(tmp_path, monkeypatch, capsys):
    md, idx, hs_path = _msr1_setup(tmp_path, monkeypatch)
    args = ["--memory-dir", md, "--index-dir", idx, "--hard-set", hs_path]
    assert E.main(args + ["--write-baseline"]) == 0
    # A byte change that alters NO row (a YAML comment): metrics identical, key moved —
    # exactly the "different inputs, not drift" case the skip exists for.
    with open(hs_path, "a", encoding="utf-8") as fh:
        fh.write("# pinned by a reviewer\n")
    capsys.readouterr()
    rc = E.main(args + ["--baseline"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "SKIPPED" in out and "fixture_fingerprint" in out
    assert "gate " not in out  # no per-metric lines on an incomparable pair


def test_baseline_drift_reports_but_never_fails(tmp_path, monkeypatch, capsys):
    md, idx, hs_path = _msr1_setup(tmp_path, monkeypatch)
    args = ["--memory-dir", md, "--index-dir", idx, "--hard-set", hs_path]
    assert E.main(args + ["--write-baseline"]) == 0
    pinned = os.path.join(md, ".audit-fixtures", "recall_eval_baseline.json")
    doc = json.load(open(pinned, encoding="utf-8"))
    doc["metrics"]["gates"]["mrr@10"] = 0.25
    doc["metrics"]["by_category"]["single-hop"]["recall"] = 0.0
    json.dump(doc, open(pinned, "w", encoding="utf-8"))
    capsys.readouterr()
    rc = E.main(args + ["--baseline"])
    out = capsys.readouterr().out
    assert rc == 0  # drift is report-only, never an exit-code change
    assert "gate mrr@10: 0.25 -> " in out
    assert "category single-hop" in out


def test_baseline_low_n_categories_are_marked(tmp_path, monkeypatch, capsys):
    md, idx, _hs = _msr1_setup(tmp_path, monkeypatch)
    import yaml

    hs_path = str(tmp_path / "hs_low_n.yaml")
    with open(hs_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(
            [
                {
                    "query": "which reranker model runs first on search candidates",
                    "expected": ["reranker_voyage"],
                    "category": "multi-hop",
                }
            ],
            fh,
        )
    args = ["--memory-dir", md, "--index-dir", idx, "--hard-set", hs_path]
    assert E.main(args + ["--write-baseline"]) == 0
    pinned = os.path.join(md, ".audit-fixtures", "recall_eval_baseline.json")
    doc = json.load(open(pinned, encoding="utf-8"))
    doc["metrics"]["by_category"]["multi-hop"]["mrr"] = 0.1
    json.dump(doc, open(pinned, "w", encoding="utf-8"))
    capsys.readouterr()
    E.main(args + ["--baseline"])
    out = capsys.readouterr().out
    assert "[low n — report-only]" in out  # n=1 multi-hop can't gate anything


def test_baseline_explicit_path_missing_fails_loud(tmp_path, monkeypatch, capsys):
    md, idx, hs_path = _msr1_setup(tmp_path, monkeypatch)
    rc = E.main(
        ["--memory-dir", md, "--index-dir", idx, "--hard-set", hs_path]
        + ["--baseline", str(tmp_path / "nope.json")]
    )
    out = capsys.readouterr().out
    assert rc == 1  # provided-but-unreadable is the loud-fail arm (RET-8's split)
    assert "FAILED to read" in out


def test_baseline_default_absent_skips_with_note(tmp_path, monkeypatch, capsys):
    md, idx, hs_path = _msr1_setup(tmp_path, monkeypatch)
    rc = E.main(["--memory-dir", md, "--index-dir", idx, "--hard-set", hs_path, "--baseline"])
    out = capsys.readouterr().out
    assert rc == 0  # skip-if-absent mirrors the hard-set gates
    assert "none found" in out and "--write-baseline" in out


def test_repeat_probe_passes_on_the_hermetic_lane(tmp_path, monkeypatch, capsys):
    """pass^2 in real fresh processes — the determinism claim, exercised end-to-end."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    import yaml

    hs_path = str(tmp_path / "hs_small.yaml")
    with open(hs_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(
            [
                {
                    "query": "which reranker model runs first on search candidates",
                    "expected": ["reranker_voyage"],
                }
            ],
            fh,
        )
    rc = E.main(
        ["--memory-dir", md, "--index-dir", idx, "--hard-set", hs_path, "--repeat", "2"]
    )
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "pass^2" in out


def test_repeat_needs_at_least_two_runs(capsys):
    assert E.main(["--repeat", "1"]) == 2
    assert "--repeat needs k >= 2" in capsys.readouterr().out
