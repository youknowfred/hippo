"""MEA-6: worktree touch-path normalization at record time (EVD-2's named follow-up).

Half the machine's recent touch evidence was dark: worktree sessions record touches
main-root-relative under .claude/worktrees/, which can never match repo-relative
cited_paths (917 mappable rows at build). The pins:

  AC1  record_from_payload derives tree_path via the ONE existing _WORKTREE_PREFIX
       constant; raw `path` preserved on every row; `tree_path` present only when
       different (ED-4).
  AC2  jit.observe_touch receives the STRIPPED path — JIT-1 reminders + JIT-2 cited_by
       fire in worktree sessions (the ONE behavior delta), bounded by the existing
       session caps and killed by HIPPO_DISABLE_JIT.
  AC3  FLT-3's shared_tree exemption stays RAW-keyed — negative capability: a
       worktree-prefixed mutation NEVER renders as a shared-tree mutation post-strip;
       presence.observe_fleet's read stays raw too.
  AC4  read-side consumers prefer tree_path (the ONE join included); historical rows
       untouched; lane health reports the before/after receipt.
  AC5  semantics pinned: a worktree touch of a cited tree path COUNTS as touching the
       citation (the session-grain join hits).
"""

from __future__ import annotations

import json
import os

import pytest

from memory import jit as J
from memory import outcome as O
from memory import presence as PR
from memory import telemetry as T
from memory.build_index import build_index, default_index_dir
from memory.telemetry import default_telemetry_dir, read_outcomes


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.delenv("HIPPO_DISABLE_JIT", raising=False)
    monkeypatch.delenv("HIPPO_DISABLE_PRESENCE", raising=False)


def _cited_corpus(md):
    # type: feedback -> the memory qualifies for the JIT-1 reminders map too (JIT-2's
    # cited map is type-agnostic; the reminder lane is deliberately pin/feedback-scoped)
    with open(os.path.join(md, "alpha-notes.md"), "w", encoding="utf-8") as fh:
        fh.write(
            '---\nname: alpha-notes\ndescription: "alpha subsystem handling notes"\n'
            'metadata:\n  type: feedback\n  cited_paths: ["src/a.py"]\n---\nbody\n'
        )
    build_index(md, default_index_dir(md))
    from memory.jit import refresh_touch_cache

    refresh_touch_cache(md, default_index_dir(md))


def _payload(repo, path, tool="Edit", sid="s1"):
    return {"tool_name": tool, "tool_input": {"file_path": os.path.join(repo, path)}, "session_id": sid}


def test_worktree_row_keeps_raw_path_and_gains_tree_path(memory_dir):
    repo = os.path.dirname(os.path.dirname(memory_dir))
    _cited_corpus(memory_dir)
    td = default_telemetry_dir(memory_dir)
    wt = ".claude/worktrees/t99-x/src/a.py"
    assert O.record_from_payload(_payload(repo, wt), memory_dir=memory_dir, repo_root=repo, telemetry_dir=td)
    row = list(read_outcomes(td))[-1]
    assert row["path"] == wt                      # RAW preserved — honest record
    assert row["tree_path"] == "src/a.py"         # additive, only when different
    # an in-tree touch carries NO tree_path (ED-4: absent when identical)
    assert O.record_from_payload(_payload(repo, "src/a.py"), memory_dir=memory_dir, repo_root=repo, telemetry_dir=td)
    row2 = list(read_outcomes(td))[-1]
    assert row2["path"] == "src/a.py"
    assert "tree_path" not in row2


def test_jit_lane_lives_in_worktrees(memory_dir):
    """AC2: cited_by (JIT-2) and the first-touch reminder (JIT-1) fire on a worktree
    touch of a cited tree path."""
    repo = os.path.dirname(os.path.dirname(memory_dir))
    _cited_corpus(memory_dir)
    td = default_telemetry_dir(memory_dir)
    ctx: list = []
    ok = O.record_from_payload(
        _payload(repo, ".claude/worktrees/t99-x/src/a.py"),
        memory_dir=memory_dir, repo_root=repo, telemetry_dir=td, context_out=ctx,
    )
    assert ok
    row = list(read_outcomes(td))[-1]
    assert row.get("cited_by") == ["alpha-notes"]          # JIT-2 provenance recorded
    assert any("alpha-notes" in c for c in ctx)            # JIT-1 reminder emitted


def test_kill_switch_still_kills_the_delta(memory_dir, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_JIT", "1")
    repo = os.path.dirname(os.path.dirname(memory_dir))
    _cited_corpus(memory_dir)
    td = default_telemetry_dir(memory_dir)
    ctx: list = []
    O.record_from_payload(
        _payload(repo, ".claude/worktrees/t99-x/src/a.py"),
        memory_dir=memory_dir, repo_root=repo, telemetry_dir=td, context_out=ctx,
    )
    row = list(read_outcomes(td))[-1]
    assert "cited_by" not in row
    assert ctx == []
    assert row["tree_path"] == "src/a.py"  # normalization is recording truth, not jit


def test_shared_tree_and_presence_stay_raw_keyed(memory_dir, monkeypatch):
    """AC3 negative capability: observe_fleet sees the RAW rel and shared_tree=False for
    a worktree mutation — the strip can never make worktree work look shared."""
    repo = os.path.dirname(os.path.dirname(memory_dir))
    _cited_corpus(memory_dir)
    seen = {}

    def _spy(rel, **kw):
        seen["rel"] = rel
        seen["shared_tree"] = kw.get("shared_tree")
        return None

    monkeypatch.setattr(PR, "observe_fleet", _spy)
    wt = ".claude/worktrees/t99-x/src/a.py"
    O.record_from_payload(
        _payload(repo, wt), memory_dir=memory_dir, repo_root=repo,
        telemetry_dir=default_telemetry_dir(memory_dir),
    )
    assert seen["rel"] == wt                # RAW, not stripped
    assert seen["shared_tree"] is False     # worktree mutation stays self-exempt


def test_join_counts_worktree_touch_of_cited_path(memory_dir):
    """AC5: the session-grain join hits when the touch's tree_path matches the cited
    path — a worktree touch of a cited tree path COUNTS as touching the citation."""
    repo = os.path.dirname(os.path.dirname(memory_dir))
    _cited_corpus(memory_dir)
    td = default_telemetry_dir(memory_dir)
    T.log_episode(["alpha-notes"], query="alpha handling", telemetry_dir=td, session_id="s1")
    O.record_from_payload(
        _payload(repo, ".claude/worktrees/t99-x/src/a.py"),
        memory_dir=memory_dir, repo_root=repo, telemetry_dir=td,
    )
    join = O._injection_join(memory_dir, td)
    assert join[("s1", "alpha-notes")]["hit"] is True
    # historical dark rows (raw worktree path, no tree_path) still do NOT hit
    T.log_episode(["alpha-notes"], query="alpha again", telemetry_dir=td, session_id="s2")
    T.log_outcome("Edit", ".claude/worktrees/t99-x/src/a.py", session_id="s2", telemetry_dir=td)
    join2 = O._injection_join(memory_dir, td)
    assert join2[("s2", "alpha-notes")]["hit"] is False


def test_lane_health_reports_the_before_after_receipt(memory_dir):
    repo = os.path.dirname(os.path.dirname(memory_dir))
    _cited_corpus(memory_dir)
    td = default_telemetry_dir(memory_dir)
    # one NEW normalized row + one HISTORICAL dark row
    O.record_from_payload(
        _payload(repo, ".claude/worktrees/t99-x/src/a.py"),
        memory_dir=memory_dir, repo_root=repo, telemetry_dir=td,
    )
    T.log_outcome("Edit", ".claude/worktrees/t99-y/src/a.py", session_id="s0", telemetry_dir=td)
    text = O.format_lane_health(memory_dir, td)
    assert "carry tree_path (normalized at record time" in text
    assert "1 historical row(s) would map if prefix-stripped" in text


def test_one_prefix_constant(monkeypatch):
    """inv5: the strip derives from outcome._WORKTREE_PREFIX — no second copy anywhere
    in the record path."""
    import inspect

    src = inspect.getsource(O.record_from_payload)
    assert "_WORKTREE_PREFIX" in src
    assert ".claude/worktrees/" not in src  # never a literal second copy
