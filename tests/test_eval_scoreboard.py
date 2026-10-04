"""OBS-5: the per-corpus field scoreboard reads the MSR-1 run ledger and pin, never more."""

from __future__ import annotations

import json
import os

from memory import eval_scoreboard
from memory.eval_recall import main as eval_main


def _corpus(tmp_path, name="proj"):
    md = tmp_path / name / ".claude" / "memory"
    md.mkdir(parents=True)
    return str(md)


def _report(recall=0.75, mrr=0.5, hard_n=8, multi_n=2, abst_rate=0.5, abst_n=4):
    return {
        "count": 40,
        "backend": "dense+bm25",
        "hard_set_n": hard_n,
        "by_category": {"single-hop": {"n": hard_n - multi_n}, "multi-hop": {"n": multi_n}},
        "gates": {
            "self_recall@10": {"value": 0.95},
            "hard_recall@10": {"value": recall},
            "mrr@10": {"value": mrr},
        },
        "abstention_rate": {"rate": abst_rate, "n": abst_n},
    }


def _append_run(md, report, corpus_fp="c1", fixture_fp="f1", ts=1_790_000_000.0):
    td = os.path.join(os.path.dirname(md), ".memory-telemetry")
    os.makedirs(td, exist_ok=True)
    row = {"ts": ts, "corpus_fingerprint": corpus_fp, "fixture_fingerprint": fixture_fp,
           "report": report}
    with open(os.path.join(td, "eval_runs.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _pin(md, recall, corpus_fp="c1", fixture_fp="f1"):
    path = os.path.join(md, ".audit-fixtures", "recall_eval_baseline.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc = {"schema": 1, "corpus_fingerprint": corpus_fp, "fixture_fingerprint": fixture_fp,
           "generated_at": "2026-10-03", "metrics": {"gates": {"hard_recall@10": recall}}}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def test_no_run_and_no_pin_says_so(tmp_path):
    md = _corpus(tmp_path)
    table = eval_scoreboard.scoreboard([("proj", md)])
    assert "| proj |" in table
    assert "no run persisted" in table and "no pin" in table


def test_newest_run_vs_comparable_pin_prints_the_delta(tmp_path):
    md = _corpus(tmp_path)
    _append_run(md, _report(recall=0.5), ts=1.0)
    _append_run(md, _report(recall=0.75), ts=2.0)  # newest row wins
    _pin(md, 0.70)
    row = eval_scoreboard.corpus_row("proj", md)
    assert row["recall"] == 0.75 and row["delta"] == 0.05
    assert row["abstained"] == (2, 4) and row["multi_hop_n"] == 2
    line = eval_scoreboard.render_table([row]).splitlines()[-1]
    assert "| 8 (2) |" in line and "| 2/4 |" in line and line.endswith("| +0.050 |")


def test_fingerprint_mismatch_is_not_drift(tmp_path):
    md = _corpus(tmp_path)
    _append_run(md, _report(recall=0.75), corpus_fp="c2")
    _pin(md, 0.70, corpus_fp="c1")
    row = eval_scoreboard.corpus_row("proj", md)
    assert "delta" not in row and row["comparable"] is False
    assert eval_scoreboard.render_table([row]).endswith("| n/c |")


def test_a_corpus_without_a_hard_set_renders_no_recall(tmp_path):
    md = _corpus(tmp_path)
    _append_run(md, _report(hard_n=0, multi_n=0))
    line = eval_scoreboard.render_table([eval_scoreboard.corpus_row("proj", md)]).splitlines()[-1]
    assert "| none |" in line and "| — | — | 0.950 |" in line


def test_each_corpus_reads_its_own_telemetry_dir_despite_an_override(tmp_path, monkeypatch):
    a, b = _corpus(tmp_path, "a"), _corpus(tmp_path, "b")
    _append_run(a, _report(recall=0.25))
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", os.path.join(os.path.dirname(a), ".memory-telemetry"))
    rows = [eval_scoreboard.corpus_row(n, md) for n, md in (("a", a), ("b", b))]
    assert rows[0]["recall"] == 0.25 and rows[1]["has_run"] is False


def test_table_carries_no_query_text_or_path(tmp_path):
    md = _corpus(tmp_path)
    report = _report()
    report["miss_autopsy"] = [{"query": "SECRET-QUERY-TEXT", "name": "some-memory"}]
    _append_run(md, report)
    table = eval_scoreboard.scoreboard([("proj", md)])
    assert "SECRET-QUERY-TEXT" not in table and "some-memory" not in table
    assert str(tmp_path) not in table


def test_cli_scoreboard_for_one_named_corpus(tmp_path, capsys):
    md = _corpus(tmp_path)
    _append_run(md, _report())
    assert eval_main(["--scoreboard", "--memory-dir", f"field={md}"]) == 0
    out = capsys.readouterr().out
    assert "| field | 40 | dense+bm25 |" in out
