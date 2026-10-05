"""HOT-1: machine-generated turns never trigger recall.

Background-task notifications, subagent hand-backs, cross-session messages, CI-monitor events,
scheduled-task runs, system-reminder-only turns and `!`-bash turns all arrive on
UserPromptSubmit exactly like a typed prompt. On the field corpora they were most of the hook's traffic, and they injected:
``clean_query`` knew only three envelope tags, and RET-4's identifier mining re-read the RAW
prompt, so a stripped ``<task-notification>`` still contributed its output-file path, tool-use
id and task id as the query. The RCL-3 rescue then blended raw prompts and raw previews.

The fixtures mirror the harness's real shapes (identifiers anonymized), each in full and as
the 80-char truncated preview the ledgers hold (an unclosed tag).
"""

from __future__ import annotations

import json
import os

import pytest

from memory import build_index as B
from memory import recall as R
from memory.recall_query import clean_query, human_text, turn_class

TASK_NOTIFICATION = (
    "<task-notification>\n<task-id>w6pnh2b90</task-id>\n"
    "<tool-use-id>toolu_01KqMb6U7P21zFsy1dDNm6q3</tool-use-id>\n"
    "<output-file>/private/tmp/claude-501/-Users-dev-proj/ea28/tasks/w6pnh2b90.output</output-file>\n"
    "<status>completed</status>\n"
    "<summary>Background command \"Run the deploy rollout check\" completed (exit code 0)</summary>\n"
    "<result>{\"deploy_rollout\": \"kubernetes canary_strategy ok\"}</result>\n"
    "</task-notification>"
)
AGENT_MESSAGE = (
    '<agent-message from="a351f34eb62e9f321">\n[Subagent hand-back] The text below is the '
    "subagent's report. kubernetes deployment rollout strategy finished; helm_chart canary "
    "verified in deploy/rollout.yaml.\n</agent-message>"
)
CROSS_SESSION = (
    '<cross-session-message from="uds:/tmp/cc-socks/30920.sock" from-session="local_e1">\n'
    "Please re-run the kubernetes deployment rollout strategy review for helm_chart canary.\n"
    "</cross-session-message>"
)
CI_MONITOR = (
    '<ci-monitor-event>"Deploy rollout" checks failed on the watched pull request: the '
    "kubernetes deployment rollout helm_chart canary job is red.</ci-monitor-event>"
)
SCHEDULED_TASK = (
    '<scheduled-task name="weekday-rollout-check" file="/Users/dev/.claude/scheduled-tasks/'
    'weekday-rollout-check/SKILL.md">\nThis is an automated run of a scheduled task. Check the '
    "kubernetes deployment rollout strategy and the helm_chart canary.\n</scheduled-task>"
)
SYSTEM_REMINDER_ONLY = (
    "<system-reminder>\nThe user started your suggested background task task_b0d8d4d2 "
    '"Fix kubernetes deployment rollout strategy". Continue your current work.\n</system-reminder>'
)
BASH_INPUT = (
    "<bash-input>kubectl rollout status deploy/canary_strategy</bash-input>"
    "<bash-stdout>deployment \"canary_strategy\" successfully rolled out</bash-stdout>"
    "<bash-stderr></bash-stderr>"
)

MACHINE_TURNS = {
    "task-notification": TASK_NOTIFICATION,
    "agent-message": AGENT_MESSAGE,
    "cross-session-message": CROSS_SESSION,
    "ci-monitor-event": CI_MONITOR,
    "scheduled-task": SCHEDULED_TASK,
    "system-reminder-only": SYSTEM_REMINDER_ONLY,
    "bash-input": BASH_INPUT,
}


def _cases():
    for cls, text in MACHINE_TURNS.items():
        yield pytest.param(cls, text, id=f"{cls}-full")
        yield pytest.param(cls, text[:80], id=f"{cls}-truncated")


@pytest.mark.parametrize("cls,text", list(_cases()))
def test_every_machine_class_is_classified(cls, text):
    assert turn_class(text) == cls


@pytest.mark.parametrize("cls,text", list(_cases()))
def test_clean_query_yields_nothing_for_a_machine_turn(cls, text):
    assert clean_query(text) == ""
    assert human_text(text) == ""


@pytest.mark.parametrize(
    "text,expected",
    [
        ("how do we roll out the canary deploy", "how do we roll out the canary deploy"),
        (
            "<system-reminder>\nYou are operating in a git worktree.\n</system-reminder>\n\n"
            "how do we roll out the canary deploy",
            "how do we roll out the canary deploy",
        ),
        ('<pasted_content id="bf58">\nerror: rollout stuck\n</pasted_content id="bf58">\nwhy?', None),
        ("<command-name>/model</command-name><command-args>opus</command-args>", None),
        ("", ""),
    ],
)
def test_human_turns_stay_human(text, expected):
    assert turn_class(text) == "human"
    if expected is not None:
        assert human_text(text) == expected


def test_a_reminder_riding_a_ci_monitor_event_is_still_that_event():
    """The class is the machine envelope's, not "system-reminder-only": a reminder beside the
    event says nothing about who sent the turn."""
    prompt = "<system-reminder>\nYou are operating in a git worktree.\n</system-reminder>\n" + CI_MONITOR
    assert turn_class(prompt) == "ci-monitor-event"


def test_mining_reads_only_the_human_text():
    """RET-4 mines fences and tracebacks the HUMAN wrote, never an envelope's paths/ids."""
    prompt = TASK_NOTIFICATION + "\nwhy does `deploy_rollout.retry_budget` keep tripping"
    q = clean_query(prompt)
    assert "deploy_rollout.retry_budget" in q
    assert "toolu_01KqMb6U7P21zFsy1dDNm6q3" not in q
    assert "w6pnh2b90" not in q and "private/tmp" not in q


# --------------------------------------------------------------------------- #
# End to end through the hook entry, with session history so the RCL-3 rescue is armed
# --------------------------------------------------------------------------- #
_CORPUS = {
    "kubernetes_deploy.md": "kubernetes deployment rollout strategy helm chart canary",
    "task_output_files.md": "background task output file tool use id status completed summary result",
}


def _write_corpus(md):
    os.makedirs(md, exist_ok=True)
    for fname, desc in _CORPUS.items():
        with open(os.path.join(md, fname), "w", encoding="utf-8") as fh:
            fh.write(f"---\nname: {fname[:-3]}\ndescription: {desc}\nmetadata:\n  type: project\n---\n{desc}\n")


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    td = tmp_path / ".memory-telemetry"
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(td))
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    _write_corpus(md)
    B.build_index(md, idx)
    return md, idx, str(td)


def _rows(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _hook(prompt, md, idx, session, capsys, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": prompt, "session_id": session})))
    assert R.main(["--stdin-json", "--memory-dir", md, "--index-dir", idx]) == 0
    return capsys.readouterr().out


@pytest.mark.parametrize("cls,text", list(_cases()))
def test_machine_turn_injects_nothing_even_with_rescue_armed(cls, text, corpus, capsys, monkeypatch):
    md, idx, td = corpus
    first = _hook("kubernetes deployment rollout strategy", md, idx, "sess-1", capsys, monkeypatch)
    assert "kubernetes_deploy" in first  # the session has a substantive prior turn to blend
    recall_rows = len(_rows(os.path.join(td, "recall_events.jsonl")))
    episodes = len(_rows(os.path.join(td, "episode_buffer.jsonl")))

    assert _hook(text, md, idx, "sess-1", capsys, monkeypatch).strip() == ""
    # A machine turn is not a recall attempt: no ledger row, no episode to crowd the
    # RCL-2 cooldown window or seed the capture drafter.
    assert len(_rows(os.path.join(td, "recall_events.jsonl"))) == recall_rows
    assert len(_rows(os.path.join(td, "episode_buffer.jsonl"))) == episodes


def test_the_daily_rollup_counts_a_ci_monitor_event_as_its_own_class(corpus, capsys, monkeypatch):
    from memory import telemetry_rollup as TR

    md, idx, td = corpus
    _hook("kubernetes deployment rollout strategy", md, idx, "sess-5", capsys, monkeypatch)
    _hook(CI_MONITOR, md, idx, "sess-5", capsys, monkeypatch)
    [row] = TR.read_rollups(td)
    assert row["hook"]["trigger"] == {"human": 1, "ci-monitor-event": 1}
    assert row["hook"]["injected_prompts"] == 1  # the human turn's; the event injected nothing


def test_human_turn_with_a_reminder_still_recalls(corpus, capsys, monkeypatch):
    md, idx, _ = corpus
    prompt = "<system-reminder>\nYou are operating in a git worktree.\n</system-reminder>\nkubernetes rollout strategy"
    assert "kubernetes_deploy" in _hook(prompt, md, idx, "sess-2", capsys, monkeypatch)


def test_ledger_and_episodes_store_the_human_text_not_the_envelope(corpus, capsys, monkeypatch):
    md, idx, td = corpus
    prompt = "<system-reminder>\nYou are operating in a git worktree.\n</system-reminder>\nkubernetes rollout strategy"
    _hook(prompt, md, idx, "sess-3", capsys, monkeypatch)
    rec = _rows(os.path.join(td, "recall_events.jsonl"))[-1]
    ep = _rows(os.path.join(td, "episode_buffer.jsonl"))[-1]
    assert rec["query_preview"] == ep["query_preview"] == "kubernetes rollout strategy"


def test_rescue_blends_cleaned_previews_only(corpus, capsys, monkeypatch):
    """A legacy (pre-HOT-1) episode whose preview is a raw, truncated envelope must not leak
    its contents into a terse follow-up's blended query."""
    md, idx, td = corpus
    from memory.telemetry import log_episode

    log_episode([], query="kubernetes deployment rollout strategy", repo_root=None, telemetry_dir=td, session_id="sess-4")
    log_episode([], query=SYSTEM_REMINDER_ONLY[:80], repo_root=None, telemetry_dir=td, session_id="sess-4")
    log_episode([], query=TASK_NOTIFICATION[:80], repo_root=None, telemetry_dir=td, session_id="sess-4")

    seen = {}
    real = R.recall

    def spy(query, **kw):
        seen["query"] = query
        return real(query, **kw)

    monkeypatch.setattr("memory.recall.recall", spy)
    out = _hook("and the other one", md, idx, "sess-4", capsys, monkeypatch)
    assert "kubernetes_deploy" in out  # the rescue still works off the human preview
    q = seen["query"]
    assert "kubernetes" in q
    for leaked in ("task_b0d8d4d2", "w6pnh2b90", "toolu", "system-reminder", "task-notification"):
        assert leaked not in q, (leaked, q)
