"""HOT-3: per-prompt and per-session context budgets for the recall hook."""

from __future__ import annotations

from memory import build_index as B
from memory import recall as R
from memory import recall_budget as RB
from memory.telemetry import default_telemetry_dir, log_episode, read_episodes


def _rows(n, desc_len=200, score=0.03):
    return [
        {"name": f"m_{i}", "file": f"m_{i}.md", "description": "d" * desc_len,
         "score": score, "backend": "bm25", "rank": i + 1}
        for i in range(n)
    ]


def test_fit_keeps_whole_rows_and_names_the_rest():
    blocks = [(f"r{i}", [f"  • r{i} — " + "x" * 90], False) for i in range(10)]
    out, rendered = RB.fit(["HEADER"], blocks, ["  ⤷ tail"], 500)
    assert len(out) <= 500 and rendered < 10
    lines = out.splitlines()
    assert lines[0] == "HEADER" and lines[-1] == "  ⤷ tail"
    assert all(len(ln) > 90 for ln in lines[1 : 1 + rendered])  # no row cut mid-line
    assert lines[-2].startswith(f"  ⤷ {10 - rendered} more past this prompt's context budget: r{rendered}")


def test_fit_sends_a_knee_row_to_the_overflow_line():
    blocks = [("a", ["  • a"], False), ("b", ["  • b"], True), ("c", ["  • c"], False)]
    out, rendered = RB.fit(["H"], blocks, [], 2500)
    assert rendered == 2 and "  • b" not in out
    assert out.splitlines()[-1].endswith("context budget: b")


def test_prompt_rendering_stays_inside_the_default_budget():
    out = R.format_results(_rows(14))
    assert len(out) <= RB._PROMPT_BUDGET_CHARS
    rendered = [ln for ln in out.splitlines() if ln.startswith("  • ")]
    assert f"(top {len(rendered)} by hybrid recall" in out
    assert "more past this prompt's context budget" in out


def test_a_row_omits_its_file_when_it_is_just_name_dot_md():
    rows = _rows(1)
    assert "  • m_0 — d" in R.format_results(rows)
    rows[0]["file"] = "renamed.md"
    assert "  • m_0 (renamed.md) — d" in R.format_results(rows)


def test_a_weak_trailing_row_renders_as_a_name_only():
    rows = _rows(3)
    rows[2]["score"] = 0.01  # under 0.4 x the top row's 0.03
    out = R.format_results(rows)
    assert "  • m_2" not in out and out.splitlines()[-1].endswith("context budget: m_2")


def test_session_budget_arithmetic():
    eps = [{"injected_chars": 5000}, {"injected_chars": 6000}, {}, {"injected_chars": "x"}]
    assert RB.session_spent(eps) == 11000
    assert RB.prompt_budget(11000) == RB._PROMPT_BUDGET_CHARS
    assert RB.over_session_budget(12000) and RB.prompt_budget(12000) == RB._SPENT_PROMPT_CHARS


def _seed(memory_dir, session, chars):
    td = default_telemetry_dir(memory_dir)
    log_episode(["deploy_runbook"], query="deploy rollback", telemetry_dir=td,
                session_id=session, injected_chars=chars)
    for _ in range(4):  # four later turns: deploy_runbook is outside RCL-2's 3-turn window
        log_episode(["other"], query="something else", telemetry_dir=td,
                    session_id=session, injected_chars=chars)
    return td


def _hook(memory_dir, idx, repo, session):
    return ["deploy rollback steps", "--memory-dir", memory_dir, "--index-dir", idx,
            "--repo-root", repo, "--session-id", session]


def _corpus(memory_dir):
    with open(f"{memory_dir}/deploy_runbook.md", "w", encoding="utf-8") as fh:
        fh.write("---\nname: deploy_runbook\ndescription: deploy rollback steps for production "
                 "incidents\ntype: project\n---\nbody\n")


def test_a_spent_session_cools_down_every_name_it_already_showed(
    repo, memory_dir, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    idx = str(tmp_path / "idx")
    _corpus(memory_dir)
    B.build_index(memory_dir, idx)
    _seed(memory_dir, "spent", 3000)  # 15,000 chars over five turns: past the budget

    assert R.main(_hook(memory_dir, idx, repo, "spent")) == 0
    out = capsys.readouterr().out
    assert "already surfaced this thread: deploy_runbook" in out
    assert len(out.rstrip("\n")) <= RB._SPENT_PROMPT_CHARS


def test_an_unspent_session_keeps_the_three_turn_window(
    repo, memory_dir, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    idx = str(tmp_path / "idx")
    _corpus(memory_dir)
    B.build_index(memory_dir, idx)
    _seed(memory_dir, "fresh", 100)

    assert R.main(_hook(memory_dir, idx, repo, "fresh")) == 0
    out = capsys.readouterr().out
    assert "  • deploy_runbook — deploy rollback" in out
    assert "already surfaced" not in out


def test_the_hook_records_what_each_prompt_injected(repo, memory_dir, tmp_path, monkeypatch):
    import io
    import json
    import sys

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_TRUST_ALL", "1")
    idx = str(tmp_path / "idx")
    _corpus(memory_dir)
    B.build_index(memory_dir, idx)
    payload = {"prompt": "deploy rollback steps", "session_id": "s-rec"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert R.main(["--stdin-json", "--memory-dir", memory_dir, "--index-dir", idx,
                   "--repo-root", repo]) == 0
    eps = [e for e in read_episodes(default_telemetry_dir(memory_dir)) if e.get("session_id") == "s-rec"]
    assert eps and eps[-1]["injected_chars"] > 0
