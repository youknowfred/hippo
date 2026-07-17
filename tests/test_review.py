"""CLB-1: the corpus review packet — op-classified diff + scoped lints + local preview.

A teammate reviewing a memory PR gets a zero-LLM packet: each touched memory
op-classified (ADD/UPDATE/SUPERSEDE/ARCHIVE/EDGE — plus an honest DELETE for the
convention-breaking hard-delete case) purely from git name-status + frontmatter
edges + archive/ moves; the shipped lints run scoped to the touched files; and —
LOCAL ONLY, never CI — recent episode-buffer previews replay against temp shadow
indexes at base-vs-head. ``--ci`` is the SEC-8 memory-diff gate half + SEN-2's
threat-lint CI leg: nonzero exit iff a GATE finding (secret / Tier-A threat)
exists on a touched file; the other lints are advisory context, never a gate
(portability warns fire on ~every project memory by design — cited paths ARE
repo coupling; and gating on unresolved contradictions would turn a
human-judgment inbox into a merge blocker, against ED-1).
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import re

from .conftest import git_commit, write_file

from memory import review as RV


def _mem(name: str, desc: str, body: str = "body text here", extra_fm: str = "") -> str:
    fm_extra = (extra_fm.rstrip("\n") + "\n") if extra_fm else ""
    return (
        "---\n"
        f"name: {name}\n"
        f'description: "{desc}"\n'
        f"{fm_extra}"
        "metadata:\n"
        "  type: project\n"
        "---\n"
        f"{body}\n"
    )


def _run(repo, memory_dir, argv):
    """Run review.run() with explicit dirs; return (exit_code, packet_text)."""
    return RV.run(argv, memory_dir=memory_dir, repo_root=repo)


# --------------------------------------------------------------------------- #
# Touched-file scoping + range forms
# --------------------------------------------------------------------------- #
def test_touched_files_scoped_to_memory_dir(repo, memory_dir):
    write_file(repo, ".claude/memory/m-one.md", _mem("m-one", "alpha fact"))
    write_file(repo, "src/code.py", "x = 1\n")
    git_commit(repo, "seed", 1_700_000_000)
    write_file(repo, ".claude/memory/m-one.md", _mem("m-one", "alpha fact v2"))
    write_file(repo, "src/code.py", "x = 2\n")
    git_commit(repo, "change both", 1_700_000_100)

    code, text = _run(repo, memory_dir, ["HEAD~1..HEAD"])
    assert code == 0
    assert "m-one" in text
    assert "src/code.py" not in text  # non-memory changes are out of scope


def test_range_with_no_memory_changes_says_so(repo, memory_dir):
    write_file(repo, ".claude/memory/m-one.md", _mem("m-one", "alpha"))
    git_commit(repo, "seed", 1_700_000_000)
    write_file(repo, "src/code.py", "x = 1\n")
    git_commit(repo, "code only", 1_700_000_100)

    code, text = _run(repo, memory_dir, ["HEAD~1..HEAD"])
    assert code == 0
    assert "no memory changes" in text


def test_single_ref_means_ref_to_head(repo, memory_dir):
    write_file(repo, ".claude/memory/m-one.md", _mem("m-one", "alpha"))
    base = git_commit(repo, "seed", 1_700_000_000)
    write_file(repo, ".claude/memory/m-two.md", _mem("m-two", "beta"))
    git_commit(repo, "add two", 1_700_000_100)

    code, text = _run(repo, memory_dir, [base])
    assert code == 0
    assert "m-two" in text and "ADD" in text


# --------------------------------------------------------------------------- #
# Op classification — derivable purely from name-status + frontmatter/edges/moves
# --------------------------------------------------------------------------- #
def test_op_add(repo, memory_dir):
    git_commit(repo, "empty seed", 1_700_000_000)
    write_file(repo, ".claude/memory/m-new.md", _mem("m-new", "brand new fact"))
    git_commit(repo, "add", 1_700_000_100)
    _code, text = _run(repo, memory_dir, ["HEAD~1..HEAD"])
    assert re.search(r"ADD\s.*m-new", text)


def test_op_update(repo, memory_dir):
    write_file(repo, ".claude/memory/m-a.md", _mem("m-a", "original"))
    git_commit(repo, "seed", 1_700_000_000)
    write_file(repo, ".claude/memory/m-a.md", _mem("m-a", "revised claim"))
    git_commit(repo, "revise", 1_700_000_100)
    _code, text = _run(repo, memory_dir, ["HEAD~1..HEAD"])
    assert re.search(r"UPDATE\s.*m-a", text)


def test_op_supersede_new_file_with_edge(repo, memory_dir):
    write_file(repo, ".claude/memory/m-old.md", _mem("m-old", "the old claim"))
    git_commit(repo, "seed", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m-new.md",
        _mem("m-new", "the corrected claim", extra_fm="supersedes: m-old"),
    )
    git_commit(repo, "supersede", 1_700_000_100)
    _code, text = _run(repo, memory_dir, ["HEAD~1..HEAD"])
    assert re.search(r"SUPERSEDE\s.*m-new", text)
    assert "m-old" in text  # the superseded target is named


def test_op_supersede_gained_edge_on_existing(repo, memory_dir):
    write_file(repo, ".claude/memory/m-old.md", _mem("m-old", "old"))
    write_file(repo, ".claude/memory/m-a.md", _mem("m-a", "will gain an edge"))
    git_commit(repo, "seed", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m-a.md",
        _mem("m-a", "will gain an edge", extra_fm="supersedes: m-old"),
    )
    git_commit(repo, "gain supersedes", 1_700_000_100)
    _code, text = _run(repo, memory_dir, ["HEAD~1..HEAD"])
    assert re.search(r"SUPERSEDE\s.*m-a", text)


def test_op_edge_only_change(repo, memory_dir):
    write_file(repo, ".claude/memory/m-b.md", _mem("m-b", "neighbor"))
    write_file(repo, ".claude/memory/m-a.md", _mem("m-a", "stable claim"))
    git_commit(repo, "seed", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m-a.md",
        _mem("m-a", "stable claim", extra_fm="refines: m-b"),
    )
    git_commit(repo, "edge only", 1_700_000_100)
    _code, text = _run(repo, memory_dir, ["HEAD~1..HEAD"])
    assert re.search(r"EDGE\s.*m-a", text)


def test_op_edge_only_wikilink_line(repo, memory_dir):
    write_file(repo, ".claude/memory/m-b.md", _mem("m-b", "neighbor"))
    write_file(repo, ".claude/memory/m-a.md", _mem("m-a", "stable", body="the body"))
    git_commit(repo, "seed", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m-a.md",
        _mem("m-a", "stable", body="the body\n\nRelated: [[m-b]]"),
    )
    git_commit(repo, "link only", 1_700_000_100)
    _code, text = _run(repo, memory_dir, ["HEAD~1..HEAD"])
    assert re.search(r"EDGE\s.*m-a", text)


def test_body_change_beats_edge_classification(repo, memory_dir):
    write_file(repo, ".claude/memory/m-b.md", _mem("m-b", "neighbor"))
    write_file(repo, ".claude/memory/m-a.md", _mem("m-a", "claim", body="old body"))
    git_commit(repo, "seed", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m-a.md",
        _mem("m-a", "claim", body="NEW body\n\nRelated: [[m-b]]"),
    )
    git_commit(repo, "edge plus body", 1_700_000_100)
    _code, text = _run(repo, memory_dir, ["HEAD~1..HEAD"])
    assert re.search(r"UPDATE\s.*m-a", text)


def test_op_archive_move(repo, memory_dir):
    write_file(repo, ".claude/memory/m-done.md", _mem("m-done", "retired"))
    git_commit(repo, "seed", 1_700_000_000)
    os.makedirs(os.path.join(memory_dir, "archive"), exist_ok=True)
    os.rename(
        os.path.join(memory_dir, "m-done.md"),
        os.path.join(memory_dir, "archive", "m-done.md"),
    )
    git_commit(repo, "archive it", 1_700_000_100)
    _code, text = _run(repo, memory_dir, ["HEAD~1..HEAD"])
    assert re.search(r"ARCHIVE\s.*m-done", text)


def test_op_delete_hard_is_named_honestly(repo, memory_dir):
    write_file(repo, ".claude/memory/m-gone.md", _mem("m-gone", "doomed"))
    git_commit(repo, "seed", 1_700_000_000)
    os.remove(os.path.join(memory_dir, "m-gone.md"))
    git_commit(repo, "hard delete", 1_700_000_100)
    _code, text = _run(repo, memory_dir, ["HEAD~1..HEAD"])
    assert re.search(r"DELETE\s.*m-gone", text)
    assert "archive/" in text  # the packet names the convention the delete skipped


def test_working_tree_mode_is_the_default(repo, memory_dir):
    write_file(repo, ".claude/memory/m-a.md", _mem("m-a", "committed"))
    git_commit(repo, "seed", 1_700_000_000)
    write_file(repo, ".claude/memory/m-a.md", _mem("m-a", "uncommitted revision"))
    write_file(repo, ".claude/memory/m-untracked.md", _mem("m-untracked", "fresh draft"))

    code, text = _run(repo, memory_dir, [])
    assert code == 0
    assert re.search(r"UPDATE\s.*m-a", text)
    assert re.search(r"ADD\s.*m-untracked", text)


# --------------------------------------------------------------------------- #
# Lints — touched-file scoped; gate vs advisory
# --------------------------------------------------------------------------- #
_FAKE_AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"  # intentional detector vector (tests are CI-scan exempt)


def test_lints_scoped_to_touched_files(repo, memory_dir):
    # Pre-existing memory carries a secret-shaped token but is NOT touched in range.
    write_file(
        repo, ".claude/memory/m-dirty.md", _mem("m-dirty", "old", body=f"key {_FAKE_AWS_KEY}")
    )
    git_commit(repo, "seed with pre-existing", 1_700_000_000)
    write_file(repo, ".claude/memory/m-clean.md", _mem("m-clean", "new clean fact"))
    git_commit(repo, "clean addition", 1_700_000_100)

    code, text = _run(repo, memory_dir, ["--ci", "HEAD~1..HEAD"])
    assert code == 0  # the untouched secret is out of scope for THIS diff's gate
    assert "m-dirty" not in text


def test_ci_exits_nonzero_on_touched_secret(repo, memory_dir):
    git_commit(repo, "empty seed", 1_700_000_000)
    write_file(
        repo, ".claude/memory/m-leak.md", _mem("m-leak", "oops", body=f"key {_FAKE_AWS_KEY}")
    )
    git_commit(repo, "leak", 1_700_000_100)
    code, text = _run(repo, memory_dir, ["--ci", "HEAD~1..HEAD"])
    assert code == 1
    assert "m-leak" in text
    assert _FAKE_AWS_KEY not in text  # the KIND is reported, never the secret itself


def test_ci_exits_nonzero_on_touched_threat(repo, memory_dir):
    git_commit(repo, "empty seed", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m-sneak.md",
        _mem("m-sneak", "looks fine", body="visible\u200btext"),  # invisible codepoint (escape form — the T10 Trojan Source lesson)
    )
    git_commit(repo, "threat", 1_700_000_100)
    code, text = _run(repo, memory_dir, ["--ci", "HEAD~1..HEAD"])
    assert code == 1
    assert "m-sneak" in text


def test_ci_exit_zero_when_clean(repo, memory_dir):
    git_commit(repo, "empty seed", 1_700_000_000)
    write_file(repo, ".claude/memory/m-fine.md", _mem("m-fine", "a clean fact"))
    git_commit(repo, "fine", 1_700_000_100)
    code, _text = _run(repo, memory_dir, ["--ci", "HEAD~1..HEAD"])
    assert code == 0


def test_ci_exit_zero_when_nothing_touched(repo, memory_dir):
    write_file(repo, ".claude/memory/m-a.md", _mem("m-a", "alpha"))
    git_commit(repo, "seed", 1_700_000_000)
    write_file(repo, "src/code.py", "x = 1\n")
    git_commit(repo, "code only", 1_700_000_100)
    code, _text = _run(repo, memory_dir, ["--ci", "HEAD~1..HEAD"])
    assert code == 0


def test_advisory_findings_never_gate(repo, memory_dir):
    git_commit(repo, "empty seed", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m-local.md",
        _mem("m-local", "machine-specific", body="lives at /Users/someone/project"),
    )
    git_commit(repo, "portability warn", 1_700_000_100)
    code, text = _run(repo, memory_dir, ["--ci", "HEAD~1..HEAD"])
    assert code == 0  # portability is advisory context, never the gate
    assert "advisory" in text and "m-local" in text


def test_dangling_edge_reported_as_advisory(repo, memory_dir):
    git_commit(repo, "empty seed", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m-dangle.md",
        _mem("m-dangle", "points nowhere", extra_fm="supersedes: no-such-memory"),
    )
    git_commit(repo, "dangling edge", 1_700_000_100)
    code, text = _run(repo, memory_dir, ["--ci", "HEAD~1..HEAD"])
    assert code == 0
    assert "no-such-memory" in text  # named, human-routed, not a gate


# --------------------------------------------------------------------------- #
# Recall-impact preview — LOCAL ONLY, never CI / HIPPO_DISABLE_DENSE=1
# --------------------------------------------------------------------------- #
def test_preview_never_runs_in_ci_mode(repo, memory_dir):
    git_commit(repo, "empty seed", 1_700_000_000)
    write_file(repo, ".claude/memory/m-a.md", _mem("m-a", "alpha"))
    git_commit(repo, "add", 1_700_000_100)
    _code, text = _run(repo, memory_dir, ["--ci", "HEAD~1..HEAD"])
    assert "recall-impact preview" not in text  # not even a skip line — CI output stays lint-only


def test_preview_skipped_when_dense_disabled(repo, memory_dir, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    git_commit(repo, "empty seed", 1_700_000_000)
    write_file(repo, ".claude/memory/m-a.md", _mem("m-a", "alpha"))
    git_commit(repo, "add", 1_700_000_100)
    _code, text = _run(repo, memory_dir, ["HEAD~1..HEAD"])
    assert "recall-impact preview skipped" in text
    assert "HIPPO_DISABLE_DENSE" in text


def test_preview_skipped_under_ci_env(repo, memory_dir, monkeypatch):
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setenv("CI", "true")
    git_commit(repo, "empty seed", 1_700_000_000)
    write_file(repo, ".claude/memory/m-a.md", _mem("m-a", "alpha"))
    git_commit(repo, "add", 1_700_000_100)
    _code, text = _run(repo, memory_dir, ["HEAD~1..HEAD"])
    assert "recall-impact preview skipped" in text


def test_preview_no_local_episodes_line(repo, memory_dir, monkeypatch, tmp_path):
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "empty-telemetry"))
    git_commit(repo, "empty seed", 1_700_000_000)
    write_file(repo, ".claude/memory/m-a.md", _mem("m-a", "alpha"))
    git_commit(repo, "add", 1_700_000_100)
    _code, text = _run(repo, memory_dir, ["HEAD~1..HEAD"])
    assert "no local episodes to replay" in text


def test_replay_engine_diffs_shadow_indexes(repo, memory_dir, tmp_path, monkeypatch):
    """The engine itself is testable under BM25 (the guard, not the engine, owns the
    dense-env policy): a memory added in range newly recalls for a matching preview."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")  # BM25 shadow indexes — hermetic
    write_file(repo, ".claude/memory/m-base.md", _mem("m-base", "an unrelated base fact"))
    base = git_commit(repo, "seed", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m-zebra.md",
        _mem("m-zebra", "zebra migration patterns in the savanna"),
    )
    head = git_commit(repo, "add zebra", 1_700_000_100)

    from memory.telemetry import log_episode

    td = str(tmp_path / "telemetry")
    assert log_episode(["m-base"], query="zebra migration patterns", telemetry_dir=td)

    report = RV.replay_previews(
        memory_dir, repo, base_ref=base, head_ref=head, telemetry_dir=td
    )
    assert report is not None
    assert any("m-zebra" in d["newly"] for d in report["deltas"])


def test_replay_disclosure_names_the_preview_bound(repo, memory_dir, tmp_path):
    from memory.telemetry import _QUERY_PREVIEW_CHARS, log_episode

    write_file(repo, ".claude/memory/m-a.md", _mem("m-a", "alpha"))
    base = git_commit(repo, "seed", 1_700_000_000)
    write_file(repo, ".claude/memory/m-b.md", _mem("m-b", "beta"))
    head = git_commit(repo, "add", 1_700_000_100)
    td = str(tmp_path / "telemetry")
    assert log_episode(["m-a"], query="alpha", telemetry_dir=td)

    report = RV.replay_previews(memory_dir, repo, base_ref=base, head_ref=head, telemetry_dir=td)
    assert report is not None
    assert str(_QUERY_PREVIEW_CHARS) in report["disclosure"]


# --------------------------------------------------------------------------- #
# The identity pins: zero LLM/network in the module; no auto-approve anywhere
# --------------------------------------------------------------------------- #
_FORBIDDEN_IMPORTS = {
    "llm_client", "urllib", "urllib.request", "http", "http.client",
    "requests", "socket", "ssl",
}


def test_review_module_imports_no_llm_or_network():
    """The AST/import pin the CLB-1 acceptance names: the classifier path (the whole
    module) is zero-LLM, zero-network — op classification stays derivable purely from
    frontmatter/edges/archive-moves."""
    src = inspect.getsource(RV)
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            imported.add(mod)
            imported.update(f"{mod}.{a.name}".lstrip(".") for a in node.names)
    bad = {i for i in imported for f in _FORBIDDEN_IMPORTS if i == f or i.startswith(f + ".")}
    assert not bad, f"review.py imports LLM/network machinery: {sorted(bad)}"


def test_no_auto_approve_or_auto_post_code_path():
    """Grep-verifiable (the acceptance's own word): auto-approve is REMOVED outright —
    hippo's review-gated-writes identity pillar — and even auto-POSTING the packet
    (the one future trust-spine-gated candidate) does not exist. The human merges."""
    src = inspect.getsource(RV).lower()
    for token in ("auto_approve", "auto-approve", "auto_merge", "auto-merge",
                  "auto_post", "auto-post", "gh pr", "pulls/"):
        assert token not in src, f"review.py contains a {token!r} code path"
    skill = os.path.join(
        os.path.dirname(__file__), "..", "plugin", "skills", "review", "SKILL.md"
    )
    with open(skill, encoding="utf-8") as fh:
        skill_text = fh.read().lower()
    for token in ("auto-approve", "auto_approve"):
        # The skill may STATE the removal in prose; it must never instruct one.
        for line in skill_text.splitlines():
            if token in line:
                assert "no " in line or "never" in line or "removed" in line, (
                    f"review SKILL.md names {token!r} outside a removal statement: {line!r}"
                )


def test_packet_renders_markdown_header(repo, memory_dir):
    git_commit(repo, "empty seed", 1_700_000_000)
    write_file(repo, ".claude/memory/m-a.md", _mem("m-a", "alpha"))
    git_commit(repo, "add", 1_700_000_100)
    _code, text = _run(repo, memory_dir, ["HEAD~1..HEAD"])
    assert text.startswith("## memory review")
    assert "```" not in text.split("\n")[0]


def test_main_parses_argv_and_returns_exit_code(repo, memory_dir, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_MEMORY_DIR", memory_dir)
    monkeypatch.chdir(repo)
    git_commit(repo, "empty seed", 1_700_000_000)
    write_file(repo, ".claude/memory/m-a.md", _mem("m-a", "alpha"))
    git_commit(repo, "add", 1_700_000_100)
    rc = RV.main(["--ci", "HEAD~1..HEAD"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "m-a" in out
