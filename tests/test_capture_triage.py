"""CAP-LLM: opt-in capture-time triage (memory/capture_triage.py + the capture.py seam).

The three pinned behaviors, per the feature's contract:
  - FLAG OFF (the default): the seed is exactly today's heuristic-only seed — no
    ``llm_triage`` field, and the triage path is never even invoked.
  - FLAG ON, working model: the seed gains ``llm_triage`` (suggested type/name, draft
    description, the model's duplicate flags filtered to REAL memory names, and the
    drain's own ``check_candidate`` dup verdict) — while the corpus stays byte-identical:
    triage annotates the pending queue, never the corpus (the approval-gate extension).
  - FLAG ON, ANY failure (no key, junk response, a raising client): fail OPEN — the seed
    is written heuristic-only and the CLI/hook path still exits 0.

Hermetic: ``memory.llm_client.complete`` is monkeypatched (or key-less so it returns None
by contract); no test touches a network. Dense is disabled for deterministic BM25 scoring.
"""

from __future__ import annotations

import ast
import inspect
import json
import os

import pytest

from memory import capture as C
from memory import capture_triage as CT
from memory import telemetry as T
from memory.telemetry import default_telemetry_dir

from .conftest import git_commit, write_file


@pytest.fixture(autouse=True)
def _hermetic_llm_env(monkeypatch):
    """No ambient keys (a dev's real ANTHROPIC_API_KEY must never fire a live call), no
    ambient flag, deterministic BM25-only scoring, and a bombed transport as the backstop."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HIPPO_LLM_API_KEY", raising=False)
    monkeypatch.delenv("HIPPO_CAPTURE_LLM", raising=False)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")

    def _bomb(*a, **kw):  # pragma: no cover - only fires on a contract breach
        raise AssertionError("capture triage attempted a real network call in a test")

    monkeypatch.setattr("urllib.request.urlopen", _bomb)


def _corpus(repo):
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    write_file(
        md,
        "existing-fact.md",
        '---\nname: existing-fact\ndescription: "the pipeline batches its writes at the end"\n'
        "metadata:\n  type: project\n---\nBatching is deliberate.\n",
    )
    # Distinct distractors so the index has enough docs for an HONEST BM25 dup ratio —
    # a 1-doc corpus's idf mass is degenerate and _bm25_dup_scores rightly refuses it.
    for name, desc in (
        ("alpha-note", "quasar telescope lens cleaning schedule"),
        ("bravo-note", "kubernetes ingress retry policy for the payments cluster"),
        ("carol-note", "the marketing site uses a static build on fridays"),
    ):
        write_file(
            md,
            f"{name}.md",
            f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: project\n---\nbody\n',
        )
    write_file(md, "MEMORY.md", "# Memory Index\n\n## User\n")
    write_file(repo, "src/app.py", "print('v1')\n")
    git_commit(repo, "init", 1_700_000_000)
    return md


def _episode(md, repo, sid, names=("existing-fact",), query="how does the pipeline batch writes"):
    T.log_episode(
        list(names),
        query=query,
        repo_root=repo,
        telemetry_dir=default_telemetry_dir(md),
        session_id=sid,
    )


def _corpus_snapshot(md):
    snap = {}
    for dirpath, _dn, files in os.walk(md):
        for f in files:
            p = os.path.join(dirpath, f)
            with open(p, "rb") as fh:
                snap[os.path.relpath(p, md)] = fh.read()
    return snap


def _read_seed(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _good_llm_json(**over):
    obj = {
        "name": "pipeline-write-batching",
        "type": "project",
        "description": "the pipeline batches all writes at the end of a run, never per-item",
        "duplicates": [],
    }
    obj.update(over)
    return json.dumps(obj)


# --------------------------------------------------------------------------- #
# Flag OFF — today's behavior, untouched and un-invoked
# --------------------------------------------------------------------------- #
def test_flag_off_seed_has_no_triage_and_triage_never_runs(repo, monkeypatch):
    md = _corpus(repo)
    _episode(md, repo, "s-off")

    def _boom(*a, **kw):  # pragma: no cover - the assertion is that this never fires
        raise AssertionError("enrich_seed ran with the flag off")

    monkeypatch.setattr(CT, "enrich_seed", _boom)
    path = C.write_session_capture("s-off", memory_dir=md, repo_root=repo)
    assert path is not None
    seed = _read_seed(path)
    assert "llm_triage" not in seed
    assert seed["schema"] == 2  # the queue shape a drain must understand is unchanged


# --------------------------------------------------------------------------- #
# Flag ON + working model — suggestions land on the seed; the corpus stays untouched
# --------------------------------------------------------------------------- #
def test_flag_on_annotates_seed_and_corpus_stays_byte_identical(repo, monkeypatch):
    md = _corpus(repo)
    from memory.build_index import build_index, default_index_dir

    build_index(md, default_index_dir(md), allow_download=False)
    _episode(md, repo, "s-on")
    write_file(repo, "src/app.py", "print('v2')  # batch at end\n")

    monkeypatch.setenv("HIPPO_CAPTURE_LLM", "1")
    prompts = []

    def _fake_complete(prompt, *, timeout_s, **kw):
        prompts.append(prompt)
        return _good_llm_json(duplicates=["existing-fact", "invented-nonsense"])

    monkeypatch.setattr("memory.llm_client.complete", _fake_complete)

    before = _corpus_snapshot(md)
    path = C.write_session_capture("s-on", memory_dir=md, repo_root=repo)
    assert path is not None
    # The approval-gate extension: triage ON, corpus byte-identical.
    assert _corpus_snapshot(md) == before

    tri = _read_seed(path)["llm_triage"]
    assert tri["suggested_type"] == "project"
    assert tri["suggested_name"] == "pipeline-write-batching"
    assert tri["draft_description"].startswith("the pipeline batches")
    # The model's dup flags are intersected with names it was actually shown.
    assert tri["llm_duplicate_flags"] == ["existing-fact"]
    # The drain's own calibrated machinery ran (CAP-3 check_candidate — same thresholds).
    assert tri["dup_check"]["route"] in ("add", "review")
    assert tri["model"]
    assert tri["secret_flagged"] is False
    # The model saw the session evidence and the neighbor shortlist.
    assert len(prompts) == 1
    assert "existing-fact" in prompts[0]
    assert "how does the pipeline batch writes" in prompts[0]
    # And the listing surfaces the suggestion for the drain reviewer.
    listing = C._format_listing([_read_seed(path) | {"_path": path}])
    assert "triage (LLM suggestion" in listing
    assert "pipeline-write-batching" in listing


def test_flag_on_llm_dup_second_opinion_rides_beside_index_check(repo, monkeypatch):
    """The LLM's flags are a SECOND opinion — the calibrated index check still runs and
    reports independently (here: a near-duplicate description routes to review)."""
    md = _corpus(repo)
    from memory.build_index import build_index, default_index_dir

    build_index(md, default_index_dir(md), allow_download=False)
    _episode(md, repo, "s-dup")
    monkeypatch.setenv("HIPPO_CAPTURE_LLM", "1")
    monkeypatch.setattr(
        "memory.llm_client.complete",
        lambda prompt, *, timeout_s, **kw: _good_llm_json(
            description="the pipeline batches its writes at the end",
            duplicates=["existing-fact"],
        ),
    )
    path = C.write_session_capture("s-dup", memory_dir=md, repo_root=repo)
    tri = _read_seed(path)["llm_triage"]
    assert tri["llm_duplicate_flags"] == ["existing-fact"]
    assert tri["dup_check"]["route"] == "review"
    assert any(n["name"] == "existing-fact" for n in tri["dup_check"]["neighbors"])


# --------------------------------------------------------------------------- #
# Flag ON + ANY failure — fail open, exit 0
# --------------------------------------------------------------------------- #
def test_flag_on_without_api_key_fails_open_end_to_end(repo, monkeypatch, capsys):
    """The real no-key path (no mocking of complete): llm_client returns None by contract,
    the seed is heuristic-only, and the CLI (the hook's entry) exits 0."""
    md = _corpus(repo)
    _episode(md, repo, "s-nokey")
    monkeypatch.setenv("HIPPO_CAPTURE_LLM", "1")
    rc = C.main(["--session-id", "s-nokey", "--memory-dir", md, "--repo-root", repo])
    assert rc == 0
    out = capsys.readouterr().out
    assert "captured →" in out
    seeds = C.read_pending(memory_dir=md)
    assert len(seeds) == 1 and "llm_triage" not in seeds[0]


def test_flag_on_llm_none_and_malformed_and_raising_all_fail_open(repo, monkeypatch):
    md = _corpus(repo)
    monkeypatch.setenv("HIPPO_CAPTURE_LLM", "1")
    cases = [
        lambda prompt, *, timeout_s, **kw: None,  # timeout/network/no-key class
        lambda prompt, *, timeout_s, **kw: "no json in this response",  # malformed
        lambda prompt, *, timeout_s, **kw: '{"type": "project"}',  # parseable, no description
    ]
    for i, fake in enumerate(cases):
        _episode(md, repo, f"s-fail{i}")
        monkeypatch.setattr("memory.llm_client.complete", fake)
        path = C.write_session_capture(f"s-fail{i}", memory_dir=md, repo_root=repo)
        assert path is not None, f"case {i}: seed must still be written"
        assert "llm_triage" not in _read_seed(path), f"case {i}: must fail open"

    # A raising client breaches llm_client's contract — enrich_seed still contains it.
    def _raises(prompt, *, timeout_s, **kw):
        raise RuntimeError("client contract breach")

    _episode(md, repo, "s-raise")
    monkeypatch.setattr("memory.llm_client.complete", _raises)
    path = C.write_session_capture("s-raise", memory_dir=md, repo_root=repo)
    assert path is not None and "llm_triage" not in _read_seed(path)

    # And a raising TRIAGE module is contained by capture's own belt.
    def _boom(*a, **kw):
        raise RuntimeError("triage exploded")

    _episode(md, repo, "s-boom")
    monkeypatch.setattr(CT, "enrich_seed", _boom)
    path = C.write_session_capture("s-boom", memory_dir=md, repo_root=repo)
    assert path is not None and "llm_triage" not in _read_seed(path)


def test_invalid_type_degrades_fieldwise_not_wholesale(repo, monkeypatch):
    md = _corpus(repo)
    _episode(md, repo, "s-type")
    monkeypatch.setenv("HIPPO_CAPTURE_LLM", "1")
    monkeypatch.setattr(
        "memory.llm_client.complete",
        lambda prompt, *, timeout_s, **kw: _good_llm_json(type="banana"),
    )
    tri = _read_seed(C.write_session_capture("s-type", memory_dir=md, repo_root=repo))["llm_triage"]
    assert tri["suggested_type"] is None  # invalid type never propagates
    assert tri["draft_description"]  # ...but the useful fields survive


# --------------------------------------------------------------------------- #
# Secret discipline — flagged hunks never leave the machine; output is linted
# --------------------------------------------------------------------------- #
def test_secret_flagged_hunks_are_never_sent(monkeypatch, tmp_path):
    md = str(tmp_path / "mem")
    os.makedirs(md)
    prompts = []
    monkeypatch.setattr(
        "memory.llm_client.complete",
        lambda prompt, *, timeout_s, **kw: prompts.append(prompt) or _good_llm_json(),
    )
    seed = {
        "query_previews": ["rotate the deploy webhook"],
        "changed_paths": ["deploy/hook.sh"],
        "recalled_names": [],
        "decisions": [],
        "diff_hunks": "+SLACK=https://hooks.slack.com/services/T0000/B0000/SECRETSECRETSECRET00",
        "hunks_secret_flagged": True,
    }
    assert CT.enrich_seed(seed, md) is not None
    assert len(prompts) == 1
    assert "hooks.slack.com" not in prompts[0]


def test_unflagged_secret_in_prompt_drops_hunks_via_lint_belt(monkeypatch, tmp_path):
    """Even if capture's own flag missed it, the assembled prompt is re-scanned and the
    hunk excerpt dropped before any bytes leave the machine."""
    from memory.secrets import scan_text

    hunk = "+URL=https://hooks.slack.com/services/T1111/B2222/ABCDEFGHIJKLMNOPQRSTUVWX"
    assert scan_text(hunk), "fixture must use a pattern secrets.py actually recognizes"
    md = str(tmp_path / "mem")
    os.makedirs(md)
    prompts = []
    monkeypatch.setattr(
        "memory.llm_client.complete",
        lambda prompt, *, timeout_s, **kw: prompts.append(prompt) or _good_llm_json(),
    )
    seed = {
        "query_previews": ["wire the deploy webhook"],
        "changed_paths": ["deploy/hook.sh"],
        "recalled_names": [],
        "decisions": [],
        "diff_hunks": hunk,
        "hunks_secret_flagged": False,  # capture missed it; the belt must not
    }
    assert CT.enrich_seed(seed, md) is not None
    assert "hooks.slack.com" not in prompts[0]


def test_secret_in_llm_output_is_flagged_for_the_drain(monkeypatch, tmp_path):
    md = str(tmp_path / "mem")
    os.makedirs(md)
    monkeypatch.setattr(
        "memory.llm_client.complete",
        lambda prompt, *, timeout_s, **kw: _good_llm_json(
            description="webhook is https://hooks.slack.com/services/T1/B2/ABCDEFGHIJKLMNOPQRST"
        ),
    )
    tri = CT.enrich_seed({"query_previews": ["q"], "recalled_names": []}, md)
    assert tri is not None and tri["secret_flagged"] is True


# --------------------------------------------------------------------------- #
# Structural pins — the triage seam can never reach the corpus writer
# --------------------------------------------------------------------------- #
def test_triage_module_never_calls_the_corpus_writer():
    """capture_triage legitimately imports new_memory for its DRY-RUN pieces
    (check_candidate / VALID_TYPES) — the pin is that it can never CALL the writer."""
    tree = ast.parse(inspect.getsource(CT))
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute):
                called.add(fn.attr)
            elif isinstance(fn, ast.Name):
                called.add(fn.id)
    assert "write_memory" not in called, "triage must never call the corpus writer"
    assert not hasattr(CT, "write_memory")


def test_capture_timeout_stays_inside_the_hook_budget(monkeypatch):
    """hooks.json gives SessionEnd/SubagentStop 30s; the LLM slice is clamped well under."""
    assert CT.llm_timeout_s() == pytest.approx(6.0)
    monkeypatch.setenv("HIPPO_CAPTURE_LLM_TIMEOUT", "2.5")
    assert CT.llm_timeout_s() == pytest.approx(2.5)
    monkeypatch.setenv("HIPPO_CAPTURE_LLM_TIMEOUT", "9999")
    assert CT.llm_timeout_s() == pytest.approx(20.0)  # clamped: never the whole hook budget
    monkeypatch.setenv("HIPPO_CAPTURE_LLM_TIMEOUT", "junk")
    assert CT.llm_timeout_s() == pytest.approx(6.0)


def test_flag_parse_matches_house_convention(monkeypatch):
    for junk in ("", "0", "yes", "TRUE ", "on"):
        monkeypatch.setenv("HIPPO_CAPTURE_LLM", junk)
        assert CT.triage_enabled() is (junk.strip() in ("1", "true", "True"))
