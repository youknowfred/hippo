"""The LLM key's reach: a run whose LLM flag is on but which can see no API key says so.

Owner ruling 2026-10-05: ``HIPPO_LLM_API_KEY`` / ``ANTHROPIC_API_KEY`` stay the one route for
scheduled and shell runs, and a missing key stops failing silently. A cron or launchd
``hippo sleep``, a shell ``hippo dream`` and a shell ``hippo capture`` never receive the
plugin options saved in /config, so a flag turned on through the environment or the legacy
file found no key and skipped its LLM pass without a word. Each of those runs now prints
one plain line naming the skip and the fix — and never a key value.

Hermetic: ``llm_client.complete`` is stubbed, the transport is bombed, and conftest strips
every ambient key, flag and plugin option.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from memory import capture as C
from memory import dream
from memory import llm_client
from memory import sleep as SL
from memory.telemetry import default_telemetry_dir, log_episode

from .conftest import git_commit, write_file

_DREAM_SKIP = "LLM contradiction check skipped"
_TRIAGE_SKIP = "LLM triage skipped"
_FAKE_KEY = "test-key-value-never-printed"

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_DREAM_CONTRA_MIN_COFIRE", "0.05")

    def _bomb(*a, **kw):  # pragma: no cover - only fires on a contract breach
        raise AssertionError("a test attempted a real network call")

    monkeypatch.setattr("urllib.request.urlopen", _bomb)


def _stub_llm(monkeypatch, reply='{"conflict": true, "reason": "opposite claims"}'):
    calls = []

    def fake(prompt, *, timeout_s, **kw):
        calls.append(prompt)
        return reply

    monkeypatch.setattr("memory.llm_client.complete", fake)
    return calls


def _lines(out, needle):
    return [ln for ln in out.splitlines() if needle in ln]


# --------------------------------------------------------------------------- #
# The sleep report — the scheduled run that never sees /config
# --------------------------------------------------------------------------- #
def _sleep_repo(tmp_path, monkeypatch):
    root = str(tmp_path / "repo")
    md = os.path.join(root, ".claude", "memory")
    os.makedirs(md)
    with open(os.path.join(root, "app.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(["git", "-C", root, "add", "-A"], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "seed"], check=True, env=_GIT_ENV)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return root, md


def _mem(md, name, description, body):
    with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write(
            f'---\nname: {name}\ndescription: "{description}"\nmetadata:\n  type: project\n'
            f"---\n\n{body}\n"
        )


def _conflicting_corpus(md):
    """Two memories making opposite current-state claims (they co-fire hard), one distractor,
    and five recall sessions so the pass clears the soak bar."""
    _mem(md, "gateway-current", "all service calls go through the quasar gateway proxy layer",
         "The gateway proxy layer is mandatory for every service call today.")
    _mem(md, "gateway-dropped", "we removed the quasar gateway proxy layer for service calls",
         "Direct service calls only now; the proxy layer is gone.")
    _mem(md, "zulu-almanac", "gardening almanac for heirloom tomato rotation beds",
         "Completely unrelated.")
    td = default_telemetry_dir(md)
    os.makedirs(td, exist_ok=True)
    with open(os.path.join(td, "recall_events.jsonl"), "w", encoding="utf-8") as fh:
        for i in range(5):
            fh.write(json.dumps({"session_id": f"s{i}", "names": [], "backend": "bm25"}) + "\n")


def _sleep(capsys):
    assert SL.main([]) == 0
    return capsys.readouterr().out


def test_sleep_names_the_skip_when_the_flag_is_on_and_no_key_is_visible(
    tmp_path, monkeypatch, capsys
):
    _root, md = _sleep_repo(tmp_path, monkeypatch)
    _conflicting_corpus(md)
    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "1")
    calls = _stub_llm(monkeypatch)
    out = _sleep(capsys)
    skip = _lines(out, _DREAM_SKIP)
    assert len(skip) == 1, out
    assert "HIPPO_LLM_API_KEY" in skip[0] and "MCP dream tool" in skip[0]
    assert calls == [], "a run that can see no key must skip the check, not attempt it"
    # It is said in the dream section of the report, where a reader looks for it.
    assert out.index("## Dream discovery") < out.index(_DREAM_SKIP)


def test_sleep_skip_line_survives_the_one_line_empty_report(tmp_path, monkeypatch, capsys):
    """An empty corpus renders a one-line "nothing to do" report — the skip must not be
    folded away with it, or a scheduled run stays silent forever."""
    _sleep_repo(tmp_path, monkeypatch)
    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "1")
    out = _sleep(capsys)
    assert len(_lines(out, _DREAM_SKIP)) == 1, out


def test_sleep_runs_the_check_when_the_env_key_is_set(tmp_path, monkeypatch, capsys):
    _root, md = _sleep_repo(tmp_path, monkeypatch)
    _conflicting_corpus(md)
    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "1")
    monkeypatch.setenv("HIPPO_LLM_API_KEY", _FAKE_KEY)
    calls = _stub_llm(monkeypatch)
    out = _sleep(capsys)
    assert calls, "with a key in the environment the check must run"
    assert "contradiction discovery" in out
    assert _DREAM_SKIP not in out
    assert _FAKE_KEY not in out


def test_sleep_flag_off_prints_no_line(tmp_path, monkeypatch, capsys):
    _root, md = _sleep_repo(tmp_path, monkeypatch)
    _conflicting_corpus(md)
    calls = _stub_llm(monkeypatch)
    out = _sleep(capsys)
    assert _DREAM_SKIP not in out
    assert calls == []


# --------------------------------------------------------------------------- #
# A shell-run dream pass
# --------------------------------------------------------------------------- #
@pytest.fixture
def dream_dirs(tmp_path):
    md = str(tmp_path / "mem")
    os.makedirs(md)
    _conflicting_corpus(md)
    return md, default_telemetry_dir(md), str(tmp_path / "idx")


def test_shell_dream_names_the_skip(dream_dirs, monkeypatch, capsys):
    md, td, idx = dream_dirs
    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "")  # the CLI flag sets it in-process
    calls = _stub_llm(monkeypatch)
    rc = dream.main(["--dry-run", "--contradictions", "--memory-dir", md, "--index-dir", idx,
                     "--telemetry-dir", td])
    assert rc == 0
    out = capsys.readouterr().out
    assert len(_lines(out, _DREAM_SKIP)) == 1, out
    assert calls == []


def test_shell_dream_apply_pass_names_the_skip(dream_dirs, monkeypatch):
    md, td, idx = dream_dirs
    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "1")
    calls = _stub_llm(monkeypatch)
    _code, text = dream.run_apply_pass(md, idx, td)
    assert len(_lines(text, _DREAM_SKIP)) == 1, text
    assert calls == []


def test_shell_dream_runs_the_check_with_the_env_key(dream_dirs, monkeypatch):
    md, td, idx = dream_dirs
    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "1")
    monkeypatch.setenv("HIPPO_LLM_API_KEY", _FAKE_KEY)
    calls = _stub_llm(monkeypatch)
    _code, text = dream.run_report_pass(md, idx, td)
    assert calls and "contradiction discovery" in text
    assert _DREAM_SKIP not in text and _FAKE_KEY not in text


# --------------------------------------------------------------------------- #
# Which routes count as "a key this run can see"
# --------------------------------------------------------------------------- #
def _legacy_file(tmp_path, monkeypatch, doc):
    path = tmp_path / "hippo-llm.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setenv("HIPPO_LLM_CONFIG", str(path))


def test_flag_from_the_legacy_file_with_its_key_moved_away_is_named(tmp_path, monkeypatch):
    """doctor tells people to move the key out of the legacy file into the plugin option; a
    schedule whose flag still lives in that file then loses its key without a word."""
    _legacy_file(tmp_path, monkeypatch, {"dream_contradictions": True, "capture_triage": True})
    line = dream.contradictions_skip_line()
    assert line and _DREAM_SKIP in line and "HIPPO_LLM_API_KEY" in line


@pytest.mark.parametrize("route", ["HIPPO_LLM_API_KEY", "ANTHROPIC_API_KEY",
                                   "CLAUDE_PLUGIN_OPTION_LLM_API_KEY", "legacy-file"])
def test_every_key_route_counts_as_visible(route, tmp_path, monkeypatch):
    doc = {"dream_contradictions": True}
    if route == "legacy-file":
        doc["api_key"] = _FAKE_KEY
    else:
        monkeypatch.setenv(route, _FAKE_KEY)
    _legacy_file(tmp_path, monkeypatch, doc)
    assert llm_client.key_visible() is True
    assert dream.contradictions_skip_line() is None


def test_an_unsubstituted_option_placeholder_is_not_a_key(monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LLM_API_KEY", "${user_config.llm_api_key}")
    assert llm_client.key_visible() is False


def test_the_skip_line_carries_no_roadmap_ids_and_no_key_value(tmp_path, monkeypatch):
    import re

    monkeypatch.setenv("HIPPO_DREAM_CONTRADICTIONS", "1")
    line = dream.contradictions_skip_line()
    assert line and not re.search(r"\b[A-Z]{2,5}-\d+\b", line)
    assert "\n" not in line


# --------------------------------------------------------------------------- #
# A shell-run capture
# --------------------------------------------------------------------------- #
def _capture_corpus(repo):
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    write_file(
        md,
        "existing-fact.md",
        '---\nname: existing-fact\ndescription: "the pipeline batches its writes at the end"\n'
        "metadata:\n  type: project\n---\nBatching is deliberate.\n",
    )
    write_file(md, "MEMORY.md", "# Memory Index\n\n## User\n")
    write_file(repo, "src/app.py", "print('v1')\n")
    git_commit(repo, "init", 1_700_000_000)
    log_episode(["existing-fact"], query="how does the pipeline batch writes", repo_root=repo,
                telemetry_dir=default_telemetry_dir(md), session_id="s-shell")
    return md


def _capture(md, repo, capsys, *extra):
    rc = C.main(["--session-id", "s-shell", "--memory-dir", md, "--repo-root", repo, *extra])
    assert rc == 0
    return capsys.readouterr()


_TRIAGE_REPLY = json.dumps({
    "name": "pipeline-write-batching",
    "type": "project",
    "description": "the pipeline batches all writes at the end of a run",
    "duplicates": [],
})


def test_shell_capture_names_the_skip(repo, monkeypatch, capsys):
    md = _capture_corpus(repo)
    monkeypatch.setenv("HIPPO_CAPTURE_LLM", "1")
    out = _capture(md, repo, capsys).out
    assert "captured →" in out
    skip = _lines(out, _TRIAGE_SKIP)
    assert len(skip) == 1, out
    assert "HIPPO_LLM_API_KEY" in skip[0]
    seeds = C.read_pending(memory_dir=md)
    assert len(seeds) == 1 and "llm_triage" not in seeds[0]


def test_shell_capture_triages_with_the_env_key(repo, monkeypatch, capsys):
    md = _capture_corpus(repo)
    monkeypatch.setenv("HIPPO_CAPTURE_LLM", "1")
    monkeypatch.setenv("HIPPO_LLM_API_KEY", _FAKE_KEY)
    calls = _stub_llm(monkeypatch, reply=_TRIAGE_REPLY)
    out = _capture(md, repo, capsys).out
    assert calls, "with a key in the environment triage must run"
    assert _TRIAGE_SKIP not in out and _FAKE_KEY not in out
    assert "llm_triage" in C.read_pending(memory_dir=md)[0]


def test_shell_capture_flag_off_prints_no_line(repo, monkeypatch, capsys):
    md = _capture_corpus(repo)
    out = _capture(md, repo, capsys).out
    assert "captured →" in out and _TRIAGE_SKIP not in out


def test_hook_capture_keeps_its_one_stderr_line_and_empty_stdout(repo, monkeypatch, capsys):
    """The wired SessionEnd hook discards stderr and has no stdout consumer: its output shape
    stays exactly one outcome line on stderr."""
    import io

    md = _capture_corpus(repo)
    monkeypatch.setenv("HIPPO_CAPTURE_LLM", "1")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": "s-shell"})))
    res = _capture(md, repo, capsys, "--from-hook")
    assert res.out == ""
    assert len(res.err.strip().splitlines()) == 1
