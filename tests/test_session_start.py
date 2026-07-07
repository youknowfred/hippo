"""Tests for memory/session_start.py — the SessionStart dispatcher.

Dispatcher logic (merge / bound / suppress / isolate / JSON) is tested by stubbing the
producer set, so these tests don't depend on git timing (that's covered in test_staleness).
The LIF-1 staleness-producer tests stub ``find_stale`` the same way but write REAL memory
files for the ``invalid_after`` read — still no git timing anywhere.
"""

from __future__ import annotations

import datetime
import json
import os

import memory.session_start as S
from memory.staleness import set_invalid_after

from .conftest import write_file


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


# --------------------------------------------------------------------------- #
# LIF-1: the staleness producer suppresses already-demoted (invalid_after) entries'
# per-item lines but keeps them COUNTED — terminal states never re-nag, and nothing
# silently disappears.
# --------------------------------------------------------------------------- #
def _iso_days_ago(days):
    return (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    ).isoformat()


def _leaf(name):
    return f'---\nname: {name}\ndescription: "{name} description"\n---\nbody\n'


def _stub_stale(monkeypatch, names):
    monkeypatch.setattr(
        S,
        "find_stale",
        lambda md, repo, diagnostics=None: [
            {"name": n, "changed_paths": ["src/a.py"]} for n in names
        ],
    )


def test_staleness_producer_suppresses_demoted_lines_but_keeps_the_count(memory_dir, monkeypatch):
    write_file(memory_dir, "m_keep.md", _leaf("m_keep"))
    gone = write_file(memory_dir, "m_gone.md", _leaf("m_gone"))
    set_invalid_after(gone, _iso_days_ago(5))  # "recent" — demoted, penalty engaged
    _stub_stale(monkeypatch, ["m_keep", "m_gone"])

    out = S.staleness_producer(memory_dir, "repo")
    assert "m_keep" in out
    assert "m_gone" not in out  # per-item line suppressed — no double-nag
    assert "1 memories cite code" in out  # header counts ACTIVE entries only
    assert "(+1 already demoted" in out  # …but the demoted one is still accounted for


def test_staleness_producer_all_demoted_collapses_to_one_honest_line(memory_dir, monkeypatch):
    for n in ("m_a", "m_b"):
        set_invalid_after(write_file(memory_dir, f"{n}.md", _leaf(n)), _iso_days_ago(5))
    _stub_stale(monkeypatch, ["m_a", "m_b"])

    out = S.staleness_producer(memory_dir, "repo")
    assert out is not None  # not silent — suppression must never look like "nothing stale"
    assert "all 2 stale memories are already demoted" in out
    assert "m_a" not in out and "m_b" not in out  # no per-item re-nag lines


def test_staleness_producer_old_invalidation_suggests_the_archive_flow(memory_dir, monkeypatch):
    """LIF-1 (D): once an invalid_after entry ages past recall's old horizon, the producer
    points at the audit skill's archive flow — report-only, naming the memory."""
    old = write_file(memory_dir, "m_old.md", _leaf("m_old"))
    set_invalid_after(old, _iso_days_ago(400))  # far past _INVALIDATION_RECENT_DAYS
    _stub_stale(monkeypatch, ["m_old"])

    out = S.staleness_producer(memory_dir, "repo")
    assert "/hippo:audit" in out and "m_old" in out  # named ONLY in the archive suggestion
    assert "old-invalidation horizon" in out


def test_staleness_producer_recent_invalidation_gets_no_archive_suggestion(memory_dir, monkeypatch):
    recent = write_file(memory_dir, "m_recent.md", _leaf("m_recent"))
    set_invalid_after(recent, _iso_days_ago(5))
    _stub_stale(monkeypatch, ["m_recent"])

    out = S.staleness_producer(memory_dir, "repo")
    assert "/hippo:audit" not in out  # still inside the penalty window — nothing to archive yet


def test_staleness_producer_unchanged_when_nothing_is_invalidated(memory_dir, monkeypatch):
    """No invalid_after anywhere -> the exact pre-LIF-1 block (no tail, full header count)."""
    write_file(memory_dir, "m_x.md", _leaf("m_x"))
    _stub_stale(monkeypatch, ["m_x"])

    out = S.staleness_producer(memory_dir, "repo")
    assert "1 memories cite code" in out and "m_x" in out
    assert "demoted" not in out and "/hippo:audit" not in out


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

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
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

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
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


# --------------------------------------------------------------------------- #
# COR-6: source-gated session rotation + harness-keyed telemetry sessions
# --------------------------------------------------------------------------- #
def _session_start_env(tmp_path, monkeypatch):
    import memory.telemetry as T

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / ".claude" / "memory")
    os.makedirs(md)
    td = str(tmp_path / ".claude" / ".memory-telemetry")
    monkeypatch.delenv("HIPPO_TELEMETRY_DIR", raising=False)
    monkeypatch.setattr(S, "resolve_dirs", lambda: (md, str(tmp_path)))
    monkeypatch.setattr(S, "build_context", lambda *a, **k: "")
    return md, td, T


def test_resume_source_does_not_rotate_session_token(tmp_path, monkeypatch):
    md, td, T = _session_start_env(tmp_path, monkeypatch)
    assert S.main(source="startup") == 0
    before = T.current_session_id(td)
    assert S.main(source="resume") == 0
    after = T.current_session_id(td)
    assert after == before


def test_compact_source_does_not_rotate_session_token(tmp_path, monkeypatch):
    md, td, T = _session_start_env(tmp_path, monkeypatch)
    assert S.main(source="startup") == 0
    before = T.current_session_id(td)
    assert S.main(source="compact") == 0
    assert T.current_session_id(td) == before


def test_startup_source_rotates_session_token(tmp_path, monkeypatch):
    md, td, T = _session_start_env(tmp_path, monkeypatch)
    assert S.main(source="startup") == 0
    before = T.current_session_id(td)
    assert S.main(source="startup") == 0
    after = T.current_session_id(td)
    assert after != before


def test_clear_source_rotates_session_token(tmp_path, monkeypatch):
    md, td, T = _session_start_env(tmp_path, monkeypatch)
    assert S.main(source="startup") == 0
    before = T.current_session_id(td)
    assert S.main(source="clear") == 0
    after = T.current_session_id(td)
    assert after != before


def test_missing_source_does_not_rotate_session_token(tmp_path, monkeypatch):
    """No hook payload at all (source=None) must not rotate — mirrors resume/compact."""
    md, td, T = _session_start_env(tmp_path, monkeypatch)
    assert S.main(source="startup") == 0
    before = T.current_session_id(td)
    assert S.main() == 0
    assert T.current_session_id(td) == before


def test_harness_session_id_bypasses_file_token_entirely(tmp_path, monkeypatch):
    """When the harness hands us a session_id, the file-based token is never touched."""
    md, td, T = _session_start_env(tmp_path, monkeypatch)
    assert S.main(source="startup", session_id="harness-sid-1") == 0
    assert not os.path.exists(T._session_path(td))


def test_explicit_source_wins_over_stdin_payload(tmp_path, monkeypatch):
    """Explicit kwargs override whatever _read_hook_payload would have parsed from stdin."""
    monkeypatch.setattr(S, "_read_hook_payload", lambda: ("resume", None))
    md, td, T = _session_start_env(tmp_path, monkeypatch)
    assert S.main(source="startup") == 0
    before = T.current_session_id(td)
    # stdin says "resume" (would NOT rotate) but the explicit source="startup" wins,
    # so a second explicit-startup call still rotates.
    assert S.main(source="startup") == 0
    assert T.current_session_id(td) != before


def test_read_hook_payload_parses_source_and_session_id(monkeypatch):
    import io

    monkeypatch.setattr(
        S.sys, "stdin", io.StringIO('{"source": "resume", "session_id": "abc123"}')
    )
    assert S._read_hook_payload() == ("resume", "abc123")


def test_read_hook_payload_empty_stdin_yields_none_none(monkeypatch):
    import io

    monkeypatch.setattr(S.sys, "stdin", io.StringIO(""))
    assert S._read_hook_payload() == (None, None)


def test_read_hook_payload_garbage_json_yields_none_none(monkeypatch):
    import io

    monkeypatch.setattr(S.sys, "stdin", io.StringIO("{not json"))
    assert S._read_hook_payload() == (None, None)


def test_read_hook_payload_tty_stdin_is_not_read(monkeypatch):
    class FakeTty:
        def isatty(self):
            return True

        def read(self):
            raise AssertionError("must not read from a tty stdin")

    monkeypatch.setattr(S.sys, "stdin", FakeTty())
    assert S._read_hook_payload() == (None, None)


# --------------------------------------------------------------------------- #
# COR-7: corpus_format producer — a corpus NEWER than the plugin must be loud;
# an OLDER one is doctor's migration path, never a per-session nag
# --------------------------------------------------------------------------- #
def test_corpus_format_producer_warns_when_corpus_is_newer(tmp_path):
    from memory.provenance import write_corpus_format

    md = str(tmp_path / "memory")
    os.makedirs(md)
    newer = S.CORPUS_FORMAT_VERSION + 1
    assert write_corpus_format(md, version=newer) is True
    out = S.corpus_format_producer(md, "repo")
    assert out is not None
    assert f"v{newer}" in out and f"v{S.CORPUS_FORMAT_VERSION}" in out  # names BOTH versions
    assert "update the hippo plugin" in out.lower()  # the one-directional remediation


def test_corpus_format_producer_silent_when_current_or_undeclared(tmp_path):
    from memory.provenance import write_corpus_format

    md = str(tmp_path / "memory")
    os.makedirs(md)
    assert S.corpus_format_producer(md, "repo") is None  # no marker == format 1 == silent
    assert write_corpus_format(md) is True  # explicit current version
    assert S.corpus_format_producer(md, "repo") is None


def test_corpus_format_producer_silent_when_corpus_is_older(tmp_path, monkeypatch):
    """Corpus older than the plugin == a migration is pending — that path is doctor-driven
    (check_format_version names it once, on demand), not a per-session alarm."""
    from memory.provenance import write_corpus_format

    md = str(tmp_path / "memory")
    os.makedirs(md)
    assert write_corpus_format(md, version=1) is True
    monkeypatch.setattr(S, "CORPUS_FORMAT_VERSION", 2)  # simulate a post-bump plugin
    assert S.corpus_format_producer(md, "repo") is None


def test_corpus_format_producer_silent_on_missing_corpus(tmp_path):
    assert S.corpus_format_producer(str(tmp_path / "does-not-exist"), "repo") is None


def test_corpus_format_producer_is_registered_exactly_once():
    labels = [label for label, _fn in S.PRODUCERS]
    assert labels.count("corpus_format") == 1
    fns = [fn for label, fn in S.PRODUCERS if label == "corpus_format"]
    assert fns == [S.corpus_format_producer]
