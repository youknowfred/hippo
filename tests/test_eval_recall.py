"""Tests for memory/eval_recall.py — the 5 merge gates.

Hermetic: a tmp corpus + tmp MEMORY.full.md/MEMORY.md + a tmp hard-set fixture are built
so the gates compute and assert their thresholds on BM25 alone (no fastembed needed). The
real-corpus run with a warm dense model is a manual merge step (see eval_recall docstring).
"""

from __future__ import annotations

import json
import os

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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)
    sr = E.self_recall_at_k(index, k=10)
    assert sr >= E.GATE_SELF_RECALL, f"self-recall {sr} below {E.GATE_SELF_RECALL}"


def test_hard_set_recall_and_mrr_gates(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)
    hs = E.hard_set_metrics(index, _HARD_SET, k=10)
    assert hs["recall"] >= E.GATE_HARD_RECALL, hs
    assert hs["mrr"] >= E.GATE_MRR, hs


def test_token_reduction_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)
    tok = E.token_reduction(md, index, _HARD_SET, k=10)
    assert tok["net"] > 0, tok
    assert tok["full"] > tok["floor"]


def test_latency_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)
    lat = E.latency(index, [h["query"] for h in _HARD_SET], k=10)
    assert lat["n"] == len(_HARD_SET)
    assert lat["p95"] >= 0.0
    assert lat["p95"] < E.GATE_P95_MS  # warm recall is well under the latency gate


def test_cold_latency_is_reported_not_gated(tmp_path, monkeypatch):
    # Cold latency is a report-only honesty signal — a FRESH subprocess per sample, so the real
    # per-process import + model-load cost is measured (the warm gate hides it). On BM25-only it
    # is cheaper (no model) but must still record samples and never gate. Subprocess inherits the
    # parent cwd (repo root) so `memory.recall` imports.
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    cold = E.cold_latency(md, idx, [h["query"] for h in _HARD_SET], k=10, samples=2)
    assert cold["n"] == 2  # capped at the sample bound; each sample is a fresh process
    assert cold["p50"] >= 0.0 and cold["max"] >= cold["p50"]


def test_evaluate_all_gates_pass_on_tmp_corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)
    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10)
    assert report["ok"] is True, report["gates"]
    # every individual gate flag is true
    assert all(g["pass"] for g in report["gates"].values()), report["gates"]
    assert report["gates"]["recall_p95_ms"]["value"] < E.GATE_P95_MS
    # cold latency is reported alongside but is NOT a gate (report-only honesty signal)
    assert "cold_latency" in report and report["cold_latency"]["n"] >= 1
    assert not any("cold" in name for name in report["gates"])


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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    B.build_index(md, idx)
    index = B.load_index(idx)
    sc = E.session_token_cost(md, str(tmp_path / "missing-tele"), index, _HARD_SET, k=10)
    assert sc == {"avg_events_per_session": 0.0, "avg_session_tokens": 0.0, "n_sessions": 0}


# --------------------------------------------------------------------------- #
# evaluate() — report-only additions never touch the 5 gates
# --------------------------------------------------------------------------- #
def test_evaluate_gates_byte_unchanged_with_or_without_new_report_params(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    # The 4 deterministic gates are byte-unchanged (BM25 ranking has no timing jitter).
    # recall_p95_ms is a REAL wall-clock measurement re-timed on each evaluate() call, so its
    # exact `value` legitimately varies run-to-run -- compare its threshold/pass flag only.
    for name in ("self_recall@10", "hard_recall@10", "mrr@10", "token_reduction"):
        assert enriched["gates"][name] == baseline["gates"][name], name
    assert enriched["gates"]["recall_p95_ms"]["threshold"] == baseline["gates"]["recall_p95_ms"]["threshold"]
    assert enriched["gates"]["recall_p95_ms"]["pass"] == baseline["gates"]["recall_p95_ms"]["pass"]
    assert enriched["ok"] == baseline["ok"]
    assert "precision_at_k" in enriched and "precision_at_k" not in baseline.get("gates", {})
    assert "staleness_half_life" in enriched
    assert "session_token_cost" in enriched


def test_evaluate_report_fields_present_and_zeroed_when_omitted(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    md = _build_corpus(tmp_path)
    idx = str(tmp_path / ".memory-index")
    hs_path = _write_hard_set(tmp_path)
    report = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs_path, k=10)
    assert report["precision_at_k"] == {"precision": 0.0, "n": 0}
    assert report["staleness_half_life"] == {"median_days": 0.0, "n": 0}  # no repo_root passed
    assert report["session_token_cost"]["n_sessions"] == 0


def test_evaluate_explicit_memory_dir_stays_hermetic_no_repo_root_leak(tmp_path, monkeypatch):
    """Passing memory_dir explicitly (without repo_root) must NOT trigger an extra
    resolve_dirs() git call that resolves repo_root against the real working tree."""
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_MEMORY_DIR", md)

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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
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
    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    repo = str(tmp_path / "proj")
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    for fname, desc in _CORPUS.items():
        with open(os.path.join(md, fname), "w", encoding="utf-8") as fh:
            fh.write(_mem(fname[:-3], desc))
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# Floor\n## User\n")  # fresh-install shape: floor, no MEMORY.full.md
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    monkeypatch.setenv("MEMOBOT_MEMORY_DIR", md)

    rc = E.main(["--memory-dir", md, "--index-dir", str(tmp_path / ".memory-index")])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "ALL GATES PASS" in out
    assert "skipped" in out  # the skipped gates say so instead of rendering ❌
    assert "❌" not in out
