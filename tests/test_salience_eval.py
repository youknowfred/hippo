"""MSR-5: the ED-2 salience-revisit A/B rig (memory/salience_eval.py).

Hermetic, bm25-lane (HIPPO_DISABLE_DENSE per test): the rig's contracts — the OFF-arm
byte-identity self-check, the usage-prior precondition (the pre-MSR-5 blind shape must
FAIL LOUDLY, never measure nothing silently), the signal-less self-check labeling, the
report shape + ED-2 footer — are all backend-independent. ED-2 is binding throughout:
nothing in these tests (or the module) flips HIPPO_SALIENCE's default.
"""

from __future__ import annotations

import json
import os

from memory import build_index as B
from memory import salience_eval as S

_CORPUS = {
    "reranker_voyage.md": "voyage rerank cross encoder is the primary reranker bm25 hybrid fallback",
    "budget_envelope.md": "phase envelope budget authority guards the synthesis tail reservation",
    "excel_header.md": "excel parser llm header rescue for non canonical column layouts",
    "canvas_pdf.md": "canvas pdf export two pass gotenberg pypdf footnote marker",
}

_HARD_SET = [
    {"query": "which reranker model runs first on search candidates", "expected": ["reranker_voyage"]},
    {"query": "guaranteed time envelope for each pipeline phase", "expected": ["budget_envelope"]},
    {"query": "infer spreadsheet column headers when layout is unusual", "expected": ["excel_header"]},
    {"query": "render a document to pdf with headless chrome", "expected": ["canvas_pdf"]},
]


def _mem(name: str, desc: str) -> str:
    return f'---\nname: {name}\ndescription: "{desc}"\ntype: project\n---\nbody for {name}\n'


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.delenv("HIPPO_SALIENCE", raising=False)
    md = str(tmp_path / "memory")
    idx = str(tmp_path / "idx")
    td = str(tmp_path / "telemetry")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    os.makedirs(md, exist_ok=True)
    for fname, desc in _CORPUS.items():
        with open(os.path.join(md, fname), "w", encoding="utf-8") as fh:
            fh.write(_mem(fname[:-3], desc))
    B.build_index(md, idx)
    import yaml

    hs = str(tmp_path / "hs.yaml")
    with open(hs, "w", encoding="utf-8") as fh:
        yaml.safe_dump(_HARD_SET, fh)
    return md, idx, td, hs


def _seed_usage(td: str, names, sessions: int = 12) -> None:
    os.makedirs(td, exist_ok=True)
    doc = {
        "version": 1,
        "sessions": {"count": sessions, "first_ts": 1.0, "last_session_id": "s"},
        "memories": {
            n: {"first_ts": 1.0, "last_ts": 2.0, "sessions": max(1, sessions // 2), "last_session_id": "s"}
            for n in names
        },
    }
    with open(os.path.join(td, "usage_aggregates.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


# --------------------------------------------------------------------------- #
# the rig's core contracts
# --------------------------------------------------------------------------- #
def test_signal_less_corpus_is_a_labeled_self_check(tmp_path, monkeypatch):
    md, idx, td, hs = _setup(tmp_path, monkeypatch)
    report = S.run_ab(memory_dir=md, index_dir=idx, hard_set_path=hs, telemetry_dir=td)
    assert report["ok"] is True
    assert report["identical_arms"] is True
    assert "NOT a finding" in report["identical_arms_note"]
    assert "byte-identical" in report["off_arm_self_check"]
    assert report["signal"] == {"usage_boosted_n": 0, "staleness_penalized_n": 0}
    # ED-2 is stamped on the evidence itself, dated decision language included.
    assert "owner-decided-OFF" in report["ed2"] and "2026-07-09" in report["ed2"]
    # ... and the run leaked nothing: the flag is still unset after the rig.
    assert os.environ.get("HIPPO_SALIENCE") is None


def test_report_persisted_to_gitignored_dir_and_readable(tmp_path, monkeypatch):
    md, idx, td, hs = _setup(tmp_path, monkeypatch)
    report = S.run_ab(memory_dir=md, index_dir=idx, hard_set_path=hs, telemetry_dir=td)
    assert report["path"] == S.default_report_path(md, td)
    persisted = S.read_report(md, td)
    assert persisted is not None and persisted["deltas"] == report["deltas"]
    assert persisted["condition"]["backend"] == "bm25-only"


def test_usage_signal_visible_to_the_on_arm(tmp_path, monkeypatch):
    """The MSR-5 point: with a lived-in usage ledger, the rig SEES the prior (the
    threaded memory_dir) — usage_boosted_n reflects usage_aggregates content."""
    md, idx, td, hs = _setup(tmp_path, monkeypatch)
    _seed_usage(td, ["reranker_voyage", "budget_envelope"])
    report = S.run_ab(memory_dir=md, index_dir=idx, hard_set_path=hs, telemetry_dir=td)
    assert report["ok"] is True
    assert report["signal"]["usage_boosted_n"] == 2
    assert set(report["deltas"]) == {"single-hop"}
    assert report["off_arm_self_check"].startswith("pass")


def test_usage_blind_shape_fails_loudly(tmp_path, monkeypatch):
    """The pre-MSR-5 vacuous shape must never return a report: non-empty
    usage_aggregates + an empty boost map is a structured ERROR, not a
    byte-identical 'finding' that salience is inert."""
    from memory import recall as R

    md, idx, td, hs = _setup(tmp_path, monkeypatch)
    _seed_usage(td, ["reranker_voyage"])
    monkeypatch.setattr(R, "_usage_boost_map", lambda memory_dir: {})
    report = S.run_ab(memory_dir=md, index_dir=idx, hard_set_path=hs, telemetry_dir=td)
    assert report["ok"] is False
    assert "precondition violated" in report["error"]
    assert S.read_report(md, td) is None  # nothing recorded on a violated precondition


def test_off_arm_leak_detected(tmp_path, monkeypatch):
    """If the two flag-off runs bracketing the ON arm differ, the rig must refuse to
    attribute the delta to salience — structured error, nothing recorded."""
    from memory import eval_recall as E

    md, idx, td, hs = _setup(tmp_path, monkeypatch)
    calls = {"n": 0}
    real = E.evaluate

    def flaky(*a, **kw):
        calls["n"] += 1
        rep = real(*a, **kw)
        if calls["n"] == 3:  # the second OFF arm: perturb one deterministic metric
            rep = dict(rep)
            rep["count"] = (rep.get("count") or 0) + 1
        return rep

    monkeypatch.setattr(E, "evaluate", flaky)
    report = S.run_ab(memory_dir=md, index_dir=idx, hard_set_path=hs, telemetry_dir=td)
    assert report["ok"] is False and "self-check FAILED" in report["error"]
    assert S.read_report(md, td) is None


def test_committed_usage_coverage_column_when_present(tmp_path, monkeypatch):
    """The absorbed team-soak lane: committed .usage/<user>.json summaries surface as
    ONE coverage column, explicitly labeled never-a-ranking-input."""
    md, idx, td, hs = _setup(tmp_path, monkeypatch)
    usage_dir = os.path.join(md, ".usage")
    os.makedirs(usage_dir, exist_ok=True)
    with open(os.path.join(usage_dir, "fred.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {"memories": {"reranker_voyage": {}}, "sessions": {"count": 4}}, fh
        )
    report = S.run_ab(memory_dir=md, index_dir=idx, hard_set_path=hs, telemetry_dir=td)
    cov = report["committed_usage_coverage"]
    assert cov["covered_stems"] == 1 and cov["summed_sessions"] == 4
    assert "never a ranking input" in cov["label"]


def test_cli_dispatch_via_eval_ab_flag(tmp_path, monkeypatch, capsys):
    from memory import eval_recall as E

    md, idx, td, hs = _setup(tmp_path, monkeypatch)
    rc = E.main(
        [
            "--memory-dir", md, "--index-dir", idx, "--hard-set", hs,
            "--telemetry-dir", td, "--ab", "HIPPO_SALIENCE",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "salience A/B [HIPPO_SALIENCE]" in out
    assert "measures only" in out and "owner-decided-OFF" in out
    assert "evidence recorded:" in out


def test_cli_unknown_ab_flag_still_exits_2(capsys):
    from memory import eval_recall as E

    assert E.main(["--ab", "HIPPO_NONSENSE"]) == 2
    assert "whitelist" in capsys.readouterr().out


def test_no_autonomous_execution_path():
    """Negative-capability pin (the roadmap AC): the rig is CLI/skill-invoked only —
    no hook-path or write-path module may import salience_eval, and salience_eval
    itself must never import the hook entrypoints (no cycle, no side door)."""
    import ast

    plugin_dir = os.path.join(os.path.dirname(__file__), "..", "plugin", "memory")
    hook_path_modules = (
        "recall.py", "session_start.py", "capture.py", "build_index.py",
        "telemetry.py", "new_memory.py", "mcp_server.py",
        # decomposition siblings of the hook-path façades above (recall.py,
        # mcp_server.py) — moved code stays under the same pin
        "recall_query.py", "recall_rank.py", "recall_graph.py",
        "recall_salience.py", "recall_tiers.py",
        "session_start_health.py", "session_start_signals.py",
        "mcp_schemas.py", "mcp_tools_core.py", "mcp_tools_setup.py",
        "mcp_tools_consolidate.py", "mcp_tools_packs.py", "mcp_resources.py",
    )
    for fname in hook_path_modules:
        p = os.path.abspath(os.path.join(plugin_dir, fname))
        with open(p, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any("salience_eval" in n for n in names), (
                f"{fname} imports salience_eval — the MSR-5 rig must stay "
                "explicit-CLI/skill-invoked only (no autonomous execution path)"
            )


def test_off_arm_matches_pinned_production_result(tmp_path, monkeypatch):
    """The AC's pinned-production identity: the rig's OFF arm IS the production
    evaluate() result — same by_category, so every delta is measured against exactly
    what production serves (the internal OFF/OFF bracket already pins full
    deterministic-view identity across the flag flip)."""
    from memory import eval_recall as E

    md, idx, td, hs = _setup(tmp_path, monkeypatch)
    production = E.evaluate(memory_dir=md, index_dir=idx, hard_set_path=hs, telemetry_dir=td)
    report = S.run_ab(memory_dir=md, index_dir=idx, hard_set_path=hs, telemetry_dir=td)
    assert report["off_by_category"] == production["by_category"]
