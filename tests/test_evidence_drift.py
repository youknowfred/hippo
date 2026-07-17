"""CLB-3: evidence fences + cited-code drift — the quoted hunk is re-verified before reuse.

Covers the marker grammar (fence info-string ``evidence: path:start-end``), the
diff-line-class-aware matcher (exact / whitespace / missing; removed lines excluded;
whitespace-only refactors deliberately NOT drift), the optional ``evidence_drift``
stale.json field (absence-emits-nothing, no schema bump), the upgraded RET-6 banner
(match level named; pre-CLB-3 text byte-identical when the field is absent), the
union into the watermark → semantic_reverify lane, the doctor coverage line, and —
load-bearing — the AST pin that recall's hot-path ``_ensure_index`` and
``build_index`` perform ZERO evidence matching (the detector lives in
``session_start``'s find_stale pipeline, full stop).
"""

from __future__ import annotations

import ast
import inspect
import json
import os

from .conftest import git_commit, write_file

from memory import staleness_evidence as SE
from memory.staleness import (
    read_evidence_drift,
    read_stale_cache,
    stale_cache_path,
    write_stale_cache,
)


def _mem_with_fence(name: str, desc: str, fence: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f'description: "{desc}"\n'
        "metadata:\n"
        "  type: project\n"
        "---\n"
        f"the fact this memory records\n\n{fence}\n"
    )


_PY_FENCE = (
    "```python evidence: src/thing.py:1-2\n"
    "def hello():\n"
    "    return 42\n"
    "```"
)
_DIFF_FENCE = (
    "```diff evidence: src/app.py:10-11\n"
    "@@ -8,3 +10,2 @@\n"
    " context line a\n"
    "-an old removed line\n"
    "+a freshly added line\n"
    "```"
)


# --------------------------------------------------------------------------- #
# Marker extraction
# --------------------------------------------------------------------------- #
def test_extract_marked_fence():
    fences = SE.extract_evidence_fences(f"prose\n\n{_PY_FENCE}\n\nmore prose\n")
    assert fences == [
        {
            "path": "src/thing.py",
            "start": 1,
            "end": 2,
            "content": "def hello():\n    return 42",
        }
    ]


def test_unmarked_and_malformed_fences_are_skipped():
    text = (
        "```python\nplain code block\n```\n\n"
        "```diff evidence: src/x.py\nno region\n```\n\n"
        "```evidence: :1-2\nno path\n```\n"
    )
    assert SE.extract_evidence_fences(text) == []


def test_tilde_fences_carry_markers_too():
    text = "~~~text evidence: docs/note.md:5-6\nquoted line\n~~~"
    fences = SE.extract_evidence_fences(text)
    assert len(fences) == 1 and fences[0]["path"] == "docs/note.md"


# --------------------------------------------------------------------------- #
# The matcher — all three classes + the false-positive case
# --------------------------------------------------------------------------- #
def test_match_exact_raw():
    assert SE.match_fence("def hello():\n    return 42", "x\ndef hello():\n    return 42\ny") == SE.MATCH_EXACT


def test_match_exact_via_diff_post_image_removed_lines_excluded():
    content = "@@ -8,3 +10,2 @@\n context line a\n-an old removed line\n+a freshly added line"
    file_text = "header\ncontext line a\na freshly added line\nfooter\n"
    # the removed line is nowhere in the tree — the post-image (context+added) matches
    assert SE.match_fence(content, file_text) == SE.MATCH_EXACT


def test_match_whitespace_level():
    content = "def hello():\n    return 42"
    file_text = "def hello():\n        return 42\n"  # re-indented only
    assert SE.match_fence(content, file_text) == SE.MATCH_WHITESPACE


def test_match_missing_when_content_gone():
    assert SE.match_fence("def hello():\n    return 42", "entirely different\n") == SE.MATCH_MISSING


def test_match_missing_when_file_unreadable():
    assert SE.match_fence("anything", "") == SE.MATCH_MISSING


# --------------------------------------------------------------------------- #
# evidence_drift_map — absence-emits-nothing; whitespace-only refactor never flags
# --------------------------------------------------------------------------- #
def test_drift_map_empty_when_no_markers(repo, memory_dir):
    write_file(repo, ".claude/memory/m-plain.md", _mem_with_fence("m-plain", "x", "```python\ncode\n```"))
    git_commit(repo, "seed", 1_700_000_000)
    assert SE.evidence_drift_map(memory_dir, repo) == {}


def test_drift_map_empty_when_evidence_matches(repo, memory_dir):
    write_file(repo, "src/thing.py", "def hello():\n    return 42\n")
    write_file(repo, ".claude/memory/m-ok.md", _mem_with_fence("m-ok", "ok", _PY_FENCE))
    git_commit(repo, "seed", 1_700_000_000)
    assert SE.evidence_drift_map(memory_dir, repo) == {}


def test_whitespace_only_refactor_is_not_drift(repo, memory_dir):
    write_file(repo, "src/thing.py", "def hello():\n            return 42\n")  # reflowed indent
    write_file(repo, ".claude/memory/m-ws.md", _mem_with_fence("m-ws", "ws", _PY_FENCE))
    git_commit(repo, "seed", 1_700_000_000)
    assert SE.evidence_drift_map(memory_dir, repo) == {}


def test_drift_map_flags_missing_evidence_with_counts_and_paths(repo, memory_dir):
    write_file(repo, "src/thing.py", "the function was deleted entirely\n")
    write_file(repo, ".claude/memory/m-rot.md", _mem_with_fence("m-rot", "rot", _PY_FENCE))
    git_commit(repo, "seed", 1_700_000_000)
    drift = SE.evidence_drift_map(memory_dir, repo)
    assert drift == {
        "m-rot": {"fences": 1, "missing": 1, "whitespace": 0, "paths": ["src/thing.py"]}
    }


def test_drift_map_flags_vanished_file(repo, memory_dir):
    write_file(repo, ".claude/memory/m-gone.md", _mem_with_fence("m-gone", "gone", _PY_FENCE))
    git_commit(repo, "seed", 1_700_000_000)  # src/thing.py never existed
    drift = SE.evidence_drift_map(memory_dir, repo)
    assert "m-gone" in drift and drift["m-gone"]["missing"] == 1


def test_fold_drift_candidates_unions_without_duplicates():
    wm = [{"name": "m-a", "changed_paths": ["x.py"], "watermark": True}]
    drift = {
        "m-a": {"fences": 1, "missing": 1, "whitespace": 0, "paths": ["x.py"]},
        "m-b": {"fences": 2, "missing": 1, "whitespace": 1, "paths": ["y.py"]},
    }
    out = SE.fold_drift_candidates(wm, drift)
    assert [i["name"] for i in out] == ["m-a", "m-b"]
    assert out[1] == {
        "name": "m-b", "changed_paths": ["y.py"], "watermark": True, "evidence": True
    }


# --------------------------------------------------------------------------- #
# stale.json — optional field, no bump, separate reader
# --------------------------------------------------------------------------- #
def test_evidence_drift_field_round_trips(tmp_path):
    index_dir = str(tmp_path / "idx")
    stale = [{"name": "m-a", "changed_paths": ["a.py", "b.py"], "source_commit": "abcdef1234567"}]
    drift = {"m-rot": {"fences": 2, "missing": 1, "whitespace": 1, "paths": ["x.py"]}}
    assert write_stale_cache(index_dir, stale, evidence_drift=drift)
    assert read_evidence_drift(index_dir) == {
        "m-rot": {"fences": 2, "missing": 1, "whitespace": 1}  # counts only — paths stay off-disk
    }
    # the RET-5 reader is untouched by the new field (penalty population can't widen)
    assert read_stale_cache(index_dir) == {"m-a": {"changed": 2, "sha": "abcdef1"}}
    with open(stale_cache_path(index_dir), encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["schema_version"] == 1  # additive field, NO bump


def test_evidence_drift_field_absent_when_empty(tmp_path):
    index_dir = str(tmp_path / "idx")
    assert write_stale_cache(index_dir, [], evidence_drift={})
    with open(stale_cache_path(index_dir), encoding="utf-8") as fh:
        payload = json.load(fh)
    assert "evidence_drift" not in payload  # absence-emits-nothing
    assert read_evidence_drift(index_dir) == {}


def test_read_evidence_drift_absent_cache_is_empty(tmp_path):
    assert read_evidence_drift(str(tmp_path / "never-written")) == {}


# --------------------------------------------------------------------------- #
# RET-6 banner upgrade — match level named; base text untouched otherwise
# --------------------------------------------------------------------------- #
def test_banner_upgraded_for_evidence_drifted_name(tmp_path):
    from memory.recall_salience import _stale_banner_map

    index_dir = str(tmp_path / "idx")
    stale = [{"name": "m-both", "changed_paths": ["a.py"], "source_commit": "abcdef1234567"}]
    drift = {
        "m-both": {"fences": 2, "missing": 1, "whitespace": 1, "paths": ["a.py"]},
        "m-evidence-only": {"fences": 1, "missing": 1, "whitespace": 0, "paths": ["b.py"]},
    }
    assert write_stale_cache(index_dir, stale, evidence_drift=drift)
    banners = _stale_banner_map(index_dir)
    assert banners["m-both"] == (
        "anchored to abcdef1; 1 cited files changed since — verify before relying; "
        "quoted evidence drift: 1 of 2 marked hunk(s) no longer match the tree, "
        "1 more match only whitespace-normalized — verify before reuse"
    )
    assert banners["m-evidence-only"] == (
        "quoted evidence drift: 1 of 1 marked hunk(s) no longer match the tree — "
        "verify before reuse"
    )


def test_banner_byte_identical_without_evidence_field(tmp_path):
    from memory.recall_salience import _stale_banner_map

    index_dir = str(tmp_path / "idx")
    stale = [{"name": "m-a", "changed_paths": ["a.py", "b.py"], "source_commit": "abcdef1234567"}]
    assert write_stale_cache(index_dir, stale)
    assert _stale_banner_map(index_dir) == {
        "m-a": "anchored to abcdef1; 2 cited files changed since — verify before relying"
    }


# --------------------------------------------------------------------------- #
# Pipeline placement — session_start's find_stale pass, and ONLY there
# --------------------------------------------------------------------------- #
def test_build_run_context_folds_drift_into_worklist_and_cache(repo, memory_dir):
    from memory.build_index import default_index_dir
    from memory.session_start import _build_run_context

    write_file(repo, ".claude/memory/m-rot.md", _mem_with_fence("m-rot", "rot", _PY_FENCE))
    git_commit(repo, "seed", 1_700_000_000)  # src/thing.py never existed -> drifted

    ctx = _build_run_context(memory_dir, repo)
    assert any(item["name"] == "m-rot" and item.get("evidence") for item in ctx.worklist)
    assert read_evidence_drift(default_index_dir(memory_dir)) == {
        "m-rot": {"fences": 1, "missing": 1, "whitespace": 0}
    }


_EVIDENCE_SURFACES = {
    "evidence_drift_map",
    "extract_evidence_fences",
    "match_fence",
    "fold_drift_candidates",
    "read_evidence_drift",
}


def _function_node(module_src: str, func: str) -> ast.AST:
    tree = ast.parse(module_src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            return node
    raise AssertionError(f"pin target {func} no longer exists — re-point this pin")


def _called_names(node: ast.AST) -> set:
    out = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            f = child.func
            if isinstance(f, ast.Attribute):
                out.add(f.attr)
            elif isinstance(f, ast.Name):
                out.add(f.id)
    return out


def test_hot_path_performs_zero_evidence_matching():
    """The CLB-3 AST pin: ``_ensure_index`` (recall's hot-path build slot) and
    ``build_index``/``compute_corpus`` (in-build hooks it reaches) never touch an
    evidence surface — matching lives in session_start's find_stale pipeline,
    which the positive half below proves. A pass here means a fresh-index recall
    can never pay a git/filesystem evidence scan."""
    from memory import build_index as BI
    from memory import recall_tiers as RT

    flagged = []
    for module, func in (
        (RT, "_ensure_index"),
        (BI, "build_index"),
        (BI, "compute_corpus"),
    ):
        bad = _called_names(_function_node(inspect.getsource(module), func)) & _EVIDENCE_SURFACES
        if bad:
            flagged.append(f"{module.__name__}.{func} calls {sorted(bad)}")
    assert not flagged, (
        "evidence matching reached the hot path — CLB-3 pins it to session_start's "
        "find_stale pipeline:\n  " + "\n  ".join(flagged)
    )


def test_find_stale_pipeline_is_the_one_evidence_caller():
    from memory import session_start as S

    called = _called_names(_function_node(inspect.getsource(S), "_build_run_context"))
    assert "evidence_drift_map" in called and "fold_drift_candidates" in called


def test_new_memory_diff_post_image_delegates_to_evidence_module():
    """One implementation: the write ticket's post-image oracle IS the drift
    detector's (a fork here would let the two disagree about what HEAD contains)."""
    from memory import new_memory as NM

    content = "@@ -1,2 +1,2 @@\n context\n-gone\n+fresh"
    assert NM._diff_post_image(content) == SE._diff_post_image(content) == "context\nfresh"


# --------------------------------------------------------------------------- #
# Doctor — coverage + drift, one deterministic line
# --------------------------------------------------------------------------- #
def test_doctor_evidence_fences_ok_and_counts(repo, memory_dir):
    from memory.doctor import CHECKS
    from memory.doctor_checks_corpus import check_evidence_fences
    from memory.doctor_checks_env import DoctorContext

    write_file(repo, "src/thing.py", "def hello():\n    return 42\n")
    write_file(repo, ".claude/memory/m-ok.md", _mem_with_fence("m-ok", "ok", _PY_FENCE))
    write_file(repo, ".claude/memory/m-plain.md", _mem_with_fence("m-plain", "p", "```python\ncode\n```"))
    git_commit(repo, "seed", 1_700_000_000)

    r = check_evidence_fences(DoctorContext(memory_dir, repo))
    assert r["status"] == "ok"
    assert "1 memory(ies) carry evidence-marked fences" in r["message"]
    assert "1 with unmarked code fences" in r["message"]
    assert "\n" not in r["message"]
    labels = [label for label, _fn in CHECKS]
    assert labels.count("evidence_fences") == 1
    assert labels[-1] == "stale_memobot_env"  # the pinned-last check still trails


def test_doctor_evidence_fences_warns_on_drift(repo, memory_dir):
    from memory.doctor_checks_corpus import check_evidence_fences
    from memory.doctor_checks_env import DoctorContext

    write_file(repo, ".claude/memory/m-rot.md", _mem_with_fence("m-rot", "rot", _PY_FENCE))
    git_commit(repo, "seed", 1_700_000_000)
    r = check_evidence_fences(DoctorContext(memory_dir, repo))
    assert r["status"] == "warn"
    assert "m-rot" in r["message"] and "DRIFTED" in r["message"]
    assert check_evidence_fences(DoctorContext(memory_dir, repo)) == r  # deterministic
