"""The one harness-envelope tag list (memory/harness_envelopes.py) and the planes that read it.

Before this list each plane kept its own copy of the tag names, and none of the copies knew
the desktop app's ``<agent-message>`` (a subagent's hand-back) or ``<cross-session-message>``
(another local session). The pins:

  - every plane derives from the one list: clean_query's block and marker regexes, the RCL-3
    rescue, the lived-in drafter (tests/test_livedin_drafts.py), and capture's previews;
  - no other module spells an envelope tag name, so a private copy cannot drift again;
  - a hook-form envelope (closed, nothing trailing, the shape the UserPromptSubmit payload
    carries) cleans to "" (its body is neither kept nor mined) and injects nothing, including
    in a session whose history the rescue could blend;
  - a real query that mentions a tag is still a query.
"""

from __future__ import annotations

import ast
import glob
import os

import pytest

from memory import build_index as B
from memory import capture as C
from memory import harness_envelopes as HE
from memory import recall as R
from memory import recall_query as RQ
from memory import telemetry as T
from memory.telemetry import default_telemetry_dir

# Hook-form envelopes. The hand-back's body shares the corpus's vocabulary, the way a real
# report about the user's own work does, so on v1.39.0 it recalled on its body.
HAND_BACK = (
    '<agent-message from="a0aee28d53630a1ba">\n[Subagent hand-back] The text below is the '
    "final report of a subagent you launched.\nThe kubernetes deployment rollout uses a helm "
    "chart canary.\n</agent-message>"
)
CROSS_SESSION = (
    '<cross-session-message from="uds:/tmp/cc-socks/23938.sock" from-name="Verify fork" '
    'from-mode="prompting">\nLane check: is anyone on the kubernetes deployment rollout '
    "strategy?\n</cross-session-message>"
)
# The output-file path is the part v1.39.0 mined back out of the stripped block.
TASK_NOTE = (
    "<task-notification>\n<task-id>abc123def</task-id>\n"
    "<output-file>/private/tmp/claude-501/-proj/sess/tasks/abc123def.output</output-file>\n"
    "<status>completed</status>\n<summary>Background command finished</summary>\n"
    "</task-notification>"
)
ENVELOPES = pytest.mark.parametrize(
    "prompt",
    [HAND_BACK, CROSS_SESSION, TASK_NOTE],
    ids=["agent-message", "cross-session-message", "task-notification"],
)
MENTIONS = "why does the drafter queue agent-message rows as hard-set queries"


def _mem(name: str, description: str) -> str:
    return f'---\nname: {name}\ndescription: "{description}"\ntype: project\n---\nbody\n'


def _corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "memory")
    idx = str(tmp_path / ".memory-index")
    os.makedirs(md)
    with open(os.path.join(md, "kubernetes_deploy.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem("kubernetes_deploy", "kubernetes deployment rollout strategy helm chart canary"))
    B.build_index(md, idx)
    return md, idx


def _turn(capsys, prompt, md, idx, session):
    assert R.main([prompt, "--memory-dir", md, "--index-dir", idx, "--session-id", session]) == 0
    return capsys.readouterr().out


# --------------------------------------------------------------------------- #
# One list
# --------------------------------------------------------------------------- #
def test_every_plane_reads_the_one_list():
    assert RQ._KNOWN_HARNESS_TAGS is HE.HARNESS_TAGS
    assert R._KNOWN_HARNESS_TAGS is HE.HARNESS_TAGS  # the façade re-export
    for tag in ("agent-message", "cross-session-message"):
        assert tag in HE.HARNESS_ENVELOPE_TAGS
    for tag in HE.HARNESS_ENVELOPE_TAGS:
        assert RQ._ENVELOPE_BLOCK_RE.fullmatch(f'<{tag} from="x">\nbody words\n</{tag}>'), tag
        assert RQ._TAG_RE.fullmatch(f'<{tag} from="x">'), tag
        assert HE.is_envelope_preview(f'<{tag} from="x">\nbody cut mid-wo'), tag
    for tag in HE.HARNESS_WRAPPER_TAGS:
        assert not HE.is_envelope_preview(f"<{tag}>/hippo:doctor</{tag}>"), tag


def test_no_module_keeps_a_private_copy_of_an_envelope_tag():
    """A tag name spelled anywhere else in plugin/memory is a second list waiting to drift.
    Docstrings and comments may name tags; string constants may not."""
    offenders = []
    for path in sorted(glob.glob(os.path.join(os.path.dirname(HE.__file__), "*.py"))):
        if os.path.basename(path) == "harness_envelopes.py":
            continue
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and any(tag in node.value for tag in HE.HARNESS_ENVELOPE_TAGS)
            ):
                offenders.append(f"{os.path.basename(path)}:{node.lineno}: {node.value[:60]!r}")
    assert offenders == [], "envelope tag names outside harness_envelopes:\n" + "\n".join(offenders)


def test_envelope_preview_matches_the_tag_name_not_a_longer_one():
    assert HE.is_envelope_preview('  <AGENT-MESSAGE from="a1">')  # leading space, any case
    assert not HE.is_envelope_preview("<agent-messages are not envelopes>")
    assert not HE.is_envelope_preview("")
    assert not HE.is_envelope_preview(MENTIONS)
    assert not HE.is_envelope_preview("strip the <agent-message> wrapper first")


# --------------------------------------------------------------------------- #
# clean_query
# --------------------------------------------------------------------------- #
@ENVELOPES
def test_clean_query_blanks_a_hook_form_envelope(prompt):
    """Covers the mining leak too: v1.39.0 mined identifiers from the RAW prompt, so a stripped
    envelope came back as its own paths (the output file, the sender's socket address)."""
    assert R.clean_query(prompt) == ""


def test_clean_query_still_mines_a_traceback_outside_an_envelope():
    out = R.clean_query(TASK_NOTE + '\nFile "src/deploy_runner.py", line 12\nKeyError: rollout_id')
    assert "src/deploy_runner.py" in out and "rollout_id" in out
    assert "abc123def.output" not in out


def test_clean_query_keeps_real_text_beside_a_hand_back():
    out = R.clean_query(HAND_BACK + " fix the reranker circuit breaker bug")
    assert out == "fix the reranker circuit breaker bug"


def test_clean_query_passes_a_query_that_mentions_agent_message():
    assert R.clean_query(MENTIONS) == MENTIONS


# --------------------------------------------------------------------------- #
# The hook: no recall on an envelope turn, rescue or not
# --------------------------------------------------------------------------- #
@ENVELOPES
def test_an_envelope_turn_injects_nothing_even_with_history_to_blend(
    tmp_path, monkeypatch, capsys, prompt
):
    """v1.39.0 leaked two ways: clean_query passed the desktop envelopes whole, and a prompt it
    DID blank (a task notification) fell to the RCL-3 rescue, which blended the previous turns'
    previews and recalled on them."""
    md, idx = _corpus(tmp_path, monkeypatch)
    assert "kubernetes_deploy" in _turn(capsys, "kubernetes deployment rollout strategy", md, idx, "s")
    assert _turn(capsys, prompt, md, idx, "s").strip() == ""


def test_rescue_window_skips_envelope_previews(tmp_path, monkeypatch, capsys):
    """With a one-turn window, the last episode is the task notification's truncated preview;
    the rescue reaches past it to the real query instead of blending its ids."""
    md, idx = _corpus(tmp_path, monkeypatch)
    monkeypatch.setenv("HIPPO_RESCUE_TURNS", "1")
    _turn(capsys, "kubernetes deployment rollout strategy", md, idx, "s")
    _turn(capsys, TASK_NOTE, md, idx, "s")
    assert "kubernetes_deploy" in _turn(capsys, "and the other one", md, idx, "s")


def test_rescue_still_serves_a_terse_follow_up_behind_a_system_reminder(
    tmp_path, monkeypatch, capsys
):
    """The envelope-only gate reads what survives the strip, not the first tag: a reminder the
    harness prepends to a real terse follow-up leaves the follow-up, which the rescue serves."""
    md, idx = _corpus(tmp_path, monkeypatch)
    _turn(capsys, "kubernetes deployment rollout strategy", md, idx, "s")
    prompt = "<system-reminder>injected context</system-reminder> and the other one"
    assert "kubernetes_deploy" in _turn(capsys, prompt, md, idx, "s")


# --------------------------------------------------------------------------- #
# Capture: a seed's query_previews
# --------------------------------------------------------------------------- #
def test_capture_previews_leave_envelopes_out(tmp_path):
    md = str(tmp_path / "memory")
    os.makedirs(md)
    td = default_telemetry_dir(md)
    for q in ("how do we deploy", HAND_BACK, CROSS_SESSION, TASK_NOTE, MENTIONS):
        T.log_episode(["deploy_runbook"], query=q, telemetry_dir=td, session_id="sess-1")
    seed = C.gather_session_context("sess-1", telemetry_dir=td, memory_dir=md, include_hunks=False)
    assert seed["query_previews"] == ["how do we deploy", MENTIONS]
    assert seed["episode_count"] == 5  # the episodes still count; only their previews drop
