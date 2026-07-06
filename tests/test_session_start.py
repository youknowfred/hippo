"""Tests for memory/session_start.py — the SessionStart dispatcher.

Dispatcher logic (merge / bound / suppress / isolate / JSON) is tested by stubbing the
producer set, so these tests don't depend on git timing (that's covered in test_staleness).
"""

from __future__ import annotations

import json
import os

import memory.session_start as S


def _producers(monkeypatch, producers):
    monkeypatch.setattr(S, "PRODUCERS", producers)


def test_build_context_merges_producer_blocks(monkeypatch):
    _producers(
        monkeypatch,
        [
            ("a", lambda md, repo: "ALPHA block"),
            ("b", lambda md, repo: "BETA block"),
        ],
    )
    ctx = S.build_context("md", "repo")
    assert "ALPHA block" in ctx and "BETA block" in ctx


def test_build_context_empty_when_nothing_to_say(monkeypatch):
    _producers(monkeypatch, [("a", lambda md, repo: None)])
    assert S.build_context("md", "repo") == ""


def test_producer_exception_is_isolated(monkeypatch):
    def boom(md, repo):
        raise RuntimeError("producer failed")

    _producers(monkeypatch, [("boom", boom), ("ok", lambda md, repo: "still here")])
    ctx = S.build_context("md", "repo")
    assert ctx == "still here"  # the survivor is kept, the failure swallowed


def test_output_is_bounded_under_cap(monkeypatch):
    _producers(monkeypatch, [("big", lambda md, repo: "x" * 50_000)])
    ctx = S.build_context("md", "repo", max_chars=500)
    assert len(ctx) <= 500
    assert ctx.endswith("(truncated)")


def test_staleness_producer_formats_real_output(monkeypatch):
    monkeypatch.setattr(
        S,
        "find_stale",
        lambda md, repo, diagnostics=None: [
            {"name": "m_x", "changed_paths": ["src/a.py", "src/b.py"]}
        ],
    )
    out = S.staleness_producer("md", "repo")
    assert out and "m_x" in out and "src/a.py" in out


def test_main_prints_session_start_json_when_stale(monkeypatch, capsys):
    monkeypatch.setattr(S, "resolve_dirs", lambda: ("md", "repo"))
    monkeypatch.setattr(
        S,
        "find_stale",
        lambda md, repo, diagnostics=None: [{"name": "m_x", "changed_paths": ["src/a.py"]}],
    )
    rc = S.main()
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "m_x" in data["hookSpecificOutput"]["additionalContext"]


def test_main_is_silent_when_nothing_stale(monkeypatch, capsys):
    monkeypatch.setattr(S, "resolve_dirs", lambda: ("md", "repo"))
    monkeypatch.setattr(S, "find_stale", lambda md, repo, diagnostics=None: [])
    rc = S.main()
    assert rc == 0
    assert capsys.readouterr().out.strip() == ""


def test_integrity_producer_formats_real_output(monkeypatch):
    monkeypatch.setattr(S, "find_unparseable", lambda md: ["m_broken"])
    out = S.integrity_producer("md", "repo")
    assert out and "m_broken" in out and "UNPARSEABLE" in out


def test_integrity_producer_silent_when_clean(monkeypatch):
    monkeypatch.setattr(S, "find_unparseable", lambda md: [])
    assert S.integrity_producer("md", "repo") is None


def test_main_refreshes_index_for_a_new_memory(tmp_path, monkeypatch):
    """The dispatcher brings the recall index up to date so a memory written during the last
    session is indexed by this one (the SessionStart auto-refresh side effect)."""
    import os

    from memory import build_index as B

    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    md = str(tmp_path / ".claude" / "memory")
    os.makedirs(md)
    with open(os.path.join(md, "a.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: a\ndescription: "alpha"\ntype: project\n---\nbody\n')
    B.build_index(md, B.default_index_dir(md))
    assert {e["name"] for e in B.load_index(B.default_index_dir(md)).entries} == {"a"}

    # A new memory is written; SessionStart should index it on the next start.
    with open(os.path.join(md, "b.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: b\ndescription: "beta new"\ntype: project\n---\nbody\n')

    monkeypatch.setattr(S, "resolve_dirs", lambda: (md, str(tmp_path)))
    monkeypatch.setattr(S, "build_context", lambda *a, **k: "")  # isolate the refresh side effect
    assert S.main() == 0
    assert {e["name"] for e in B.load_index(B.default_index_dir(md)).entries} == {"a", "b"}


# --------------------------------------------------------------------------- #
# reconsolidation producer wiring (Tier 2) — ONE dispatcher, never a parallel hook entry
# --------------------------------------------------------------------------- #
def test_reconsolidation_producer_is_registered_exactly_once():
    from memory.reconsolidate import reconsolidation_producer

    labels = [label for label, _fn in S.PRODUCERS]
    assert labels.count("reconsolidation") == 1
    fns = [fn for label, fn in S.PRODUCERS if label == "reconsolidation"]
    assert fns == [reconsolidation_producer]  # the SAME function, not a re-implementation


def test_reconsolidation_producer_registered_after_staleness():
    labels = [label for label, _fn in S.PRODUCERS]
    # the recall-filtered subset is grouped right after the full staleness signal
    assert labels.index("reconsolidation") == labels.index("staleness") + 1


def test_reconsolidation_silent_when_stubbed_empty(monkeypatch):
    from memory.reconsolidate import reconsolidation_producer

    monkeypatch.setattr("memory.reconsolidate.recalled_stale_worklist", lambda *a, **k: [])
    assert reconsolidation_producer("md", "repo") is None


def test_main_heals_empty_baselines_side_effect(tmp_path, monkeypatch, capsys):
    """COR-1: SessionStart heals residual source_commit:"" baselines to HEAD (covers
    hand-authored/pre-COR-1 memories) as a side effect, before the index refresh."""
    import subprocess

    from memory.staleness import read_provenance

    from .conftest import git_commit, write_file

    monkeypatch.setenv("MEMOBOT_DISABLE_DENSE", "1")
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    write_file(repo, "src/x.py", "x = 1\n")
    head = git_commit(repo, "init", 1_700_000_000)
    md = os.path.join(repo, ".claude", "memory")
    path = write_file(
        repo,
        ".claude/memory/residual.md",
        '---\nname: residual\ndescription: "left empty by a pre-COR-1 backfill"\n'
        'cited_paths: ["src/x.py"]\nsource_commit: ""\n---\nbody\n',
    )
    monkeypatch.setattr(S, "resolve_dirs", lambda: (md, repo))

    assert S.main() == 0
    _, sc = read_provenance(open(path, encoding="utf-8").read())
    assert sc == head


# --------------------------------------------------------------------------- #
# COR-11: stale-venv detection (requirements hash vs bootstrap sentinel)
# --------------------------------------------------------------------------- #
def _plugin_env(tmp_path, monkeypatch, *, req_text: str, sentinel_hash):
    import hashlib
    import json as _json

    data_dir = tmp_path / "plugin-data"
    plugin_root = tmp_path / "plugin-root"
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(plugin_root, exist_ok=True)
    (plugin_root / "requirements.txt").write_text(req_text, encoding="utf-8")
    if sentinel_hash == "current":
        sentinel_hash = hashlib.sha256(req_text.encode()).hexdigest()
    if sentinel_hash is not None:
        (data_dir / ".bootstrap-sentinel").write_text(
            _json.dumps({"requirements_hash": sentinel_hash}), encoding="utf-8"
        )
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data_dir))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))


def test_stale_venv_producer_nudges_on_dep_bump(tmp_path, monkeypatch):
    _plugin_env(tmp_path, monkeypatch, req_text="numpy>=2\n", sentinel_hash="0" * 64)
    out = S.stale_venv_producer("md", "repo")
    assert out and "/hippo:bootstrap" in out


def test_stale_venv_producer_silent_when_hash_current(tmp_path, monkeypatch):
    _plugin_env(tmp_path, monkeypatch, req_text="numpy>=2\n", sentinel_hash="current")
    assert S.stale_venv_producer("md", "repo") is None


def test_stale_venv_producer_silent_when_not_bootstrapped(tmp_path, monkeypatch):
    # No sentinel — ONB-1's pre-Python nudge owns that state; this producer stays out.
    _plugin_env(tmp_path, monkeypatch, req_text="numpy>=2\n", sentinel_hash=None)
    assert S.stale_venv_producer("md", "repo") is None


def test_stale_venv_producer_silent_without_plugin_data_env(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    assert S.stale_venv_producer("md", "repo") is None


def test_stale_venv_producer_registered_first():
    assert S.PRODUCERS[0][0] == "stale_venv"
    assert S.PRODUCERS[0][1] is S.stale_venv_producer


# --------------------------------------------------------------------------- #
# SHP-3 — unresolvable_baseline_producer (squash-merge / shallow-clone legibility)
# --------------------------------------------------------------------------- #
def test_unresolvable_baseline_producer_formats_real_output(monkeypatch):
    monkeypatch.setattr(S, "count_unresolvable_baselines", lambda md, repo: 3)
    out = S.unresolvable_baseline_producer("md", "repo")
    assert out and "3 memories" in out and "squash-merge" in out


def test_unresolvable_baseline_producer_silent_when_zero(monkeypatch):
    monkeypatch.setattr(S, "count_unresolvable_baselines", lambda md, repo: 0)
    assert S.unresolvable_baseline_producer("md", "repo") is None


def test_unresolvable_baseline_producer_is_registered():
    labels = [label for label, _fn in S.PRODUCERS]
    assert labels.count("unresolvable_baseline") == 1
    fns = [fn for label, fn in S.PRODUCERS if label == "unresolvable_baseline"]
    assert fns == [S.unresolvable_baseline_producer]
