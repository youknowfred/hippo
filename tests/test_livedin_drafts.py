"""MEA-2: lived-in hard-set drafting — the missing FOURTH draft lane.

Joins episode ``query_preview``s to session-grain ``_injection_join`` hits (the ONE
sanctioned join, MSR-6) and queues (verbatim query, derived_expected) candidate rows
beside the abstention/forgetting/update lanes. The pins:

  AC1  drafts derive ONLY from ledger evidence — injected AND outcome-confirmed; the
       query is byte-verbatim from the episode preview (zero LLM, zero rewording,
       zero templating — the templated-fixture kill).
  AC2  volume-capped per run (module constant) + deduplicated against tracked queries
       AND queued drafts; noise filters are deterministic and tested (short /
       system-reminder / slash-command previews never draft).
  AC3  drafts land in the SEC-3 pending queue; NOTHING enters the tracked fixture
       without per-item ``confirm_hard_set_row`` — which keeps refusing absent stems
       (negative-capability: no bulk-admit path exists).
  AC4  cold path only; absence of candidates emits nothing (no file created).
"""

from __future__ import annotations

import json
import os

import pytest
import yaml

from memory import build_index as B
from memory import eval_fixtures as EF
from memory import eval_recall as E
from memory import telemetry as T
from memory.build_index import default_index_dir
from memory.eval_fixtures import default_drafts_path
from memory.telemetry import default_telemetry_dir


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.delenv("HIPPO_PENDING_DIR", raising=False)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")


def _mem(md, stem, description, cited_paths):
    cited = ", ".join(json.dumps(p) for p in cited_paths)
    with open(os.path.join(md, f"{stem}.md"), "w", encoding="utf-8") as fh:
        fh.write(
            f"---\nname: {stem}\ndescription: {json.dumps(description)}\n"
            f"metadata:\n  type: project\n  cited_paths: [{cited}]\n---\nbody\n"
        )


def _confirmed_touch(td, *, session, query, name, path):
    """One outcome-confirmed retrieval: inject (episode) then touch a cited file."""
    T.log_episode([name], query=query, telemetry_dir=td, session_id=session)
    T.log_outcome("Edit", path, session_id=session, telemetry_dir=td)


def _setup(memory_dir):
    _mem(memory_dir, "alpha-notes", "alpha subsystem rollback handling", ["src/a.py"])
    _mem(memory_dir, "beta-notes", "beta pipeline retry semantics", ["src/b.py"])
    B.build_index(memory_dir, default_index_dir(memory_dir))
    return default_telemetry_dir(memory_dir)


QUERY = "how does the alpha subsystem handle rollback again"


def test_confirmed_pairs_draft_verbatim_rows(memory_dir):
    td = _setup(memory_dir)
    _confirmed_touch(td, session="s1", query=QUERY, name="alpha-notes", path="src/a.py")
    # injected but NEVER touched -> no draft (evidence-derived only)
    T.log_episode(["beta-notes"], query="beta retry semantics deep dive", telemetry_dir=td, session_id="s2")

    summary = EF.draft_livedin_fixtures(memory_dir, telemetry_dir=td)
    assert summary["added"] == [QUERY]
    dp = default_drafts_path(memory_dir)
    text = open(dp, encoding="utf-8").read()
    # AC1: byte-verbatim query + evidence-derived expectation; judgment stays human
    assert json.dumps(QUERY) in text
    docs = [d for d in yaml.safe_load_all(open(dp, encoding="utf-8")) if d is not None]
    rows = docs[-1]
    row = next(r for r in rows if r["query"] == QUERY)
    assert row["derived_expected"] == ["alpha-notes"]
    assert row["expected"] == []
    assert row["kind"] == "lived-in"
    assert row["sessions"] == 1


def test_noise_filters_are_deterministic(memory_dir):
    td = _setup(memory_dir)
    for q in (
        "/hippo:doctor please",                       # slash-command preview
        "<system-reminder>injected context</system-reminder>",  # harness envelope
        # a TRUNCATED envelope (the ledger's preview budget cuts the closing tag, so
        # clean_query's block-stripping regex never fires — the first live drain's catch)
        "<task-notification>\n<task-id>abc123def</task-id>\n<tool-use-id>toolu_01XY",
        "ok",                                          # below clean_query's content gate
    ):
        _confirmed_touch(td, session="s1", query=q, name="alpha-notes", path="src/a.py")
    summary = EF.draft_livedin_fixtures(memory_dir, telemetry_dir=td)
    assert summary["added"] == []
    assert summary["skipped_noise"] == 4
    # AC4: nothing to add -> the drafts file is never created
    assert not os.path.exists(default_drafts_path(memory_dir))


def test_dedup_against_tracked_and_queued(memory_dir):
    td = _setup(memory_dir)
    _confirmed_touch(td, session="s1", query=QUERY, name="alpha-notes", path="src/a.py")

    first = EF.draft_livedin_fixtures(memory_dir, telemetry_dir=td)
    assert first["added"] == [QUERY]
    # queued dedup: a second run adds nothing and preserves the existing row verbatim
    second = EF.draft_livedin_fixtures(memory_dir, telemetry_dir=td)
    assert second["added"] == []
    assert second["kept"] == 1

    # tracked dedup: admit the row per-item, then a fresh draft run skips it
    r = E.confirm_hard_set_row(QUERY, ["alpha-notes"], memory_dir, category="single-hop")
    assert r["ok"], r
    assert r["removed_from_drafts"] is True
    third = EF.draft_livedin_fixtures(memory_dir, telemetry_dir=td)
    assert third["added"] == []


def test_volume_cap_is_a_module_constant(memory_dir, monkeypatch):
    td = _setup(memory_dir)
    monkeypatch.setattr(EF, "_LIVEDIN_MAX_DRAFTS_PER_RUN", 2)
    for i in range(5):
        _confirmed_touch(
            td, session=f"s{i}",
            query=f"alpha rollback variant number {i} details",
            name="alpha-notes", path="src/a.py",
        )
    summary = EF.draft_livedin_fixtures(memory_dir, telemetry_dir=td)
    assert len(summary["added"]) == 2


def test_no_bulk_admit_path_exists(memory_dir):
    """AC3 (negative capability): the drafter QUEUES; the tracked fixture grows only
    through per-item confirm_hard_set_row, which keeps refusing absent stems."""
    td = _setup(memory_dir)
    _confirmed_touch(td, session="s1", query=QUERY, name="alpha-notes", path="src/a.py")
    EF.draft_livedin_fixtures(memory_dir, telemetry_dir=td)
    tracked = os.path.join(memory_dir, ".audit-fixtures", "recall_hard_set.yaml")
    assert not os.path.exists(tracked)  # drafting NEVER touched the tracked fixture
    # the admission gate still refuses a stem that does not exist in this corpus
    refused = E.confirm_hard_set_row(QUERY, ["ghost-stem"], memory_dir, category="single-hop")
    assert not refused["ok"]
    assert "ghost-stem" in str(refused.get("reason"))
    assert not os.path.exists(tracked)
    # and the drafter itself CALLS no admission path (AST pin — docstrings may
    # mention the confirm gate as guidance; calling it would be the violation)
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(EF.draft_livedin_fixtures)))
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert "_append_draft_rows" in calls         # the ONE sanctioned queue append
    assert "confirm_hard_set_row" not in calls   # admission is never called from the drafter


def test_multi_memory_query_aggregates_expected(memory_dir):
    td = _setup(memory_dir)
    q = "alpha rollback interacting with beta retries"
    T.log_episode(["alpha-notes", "beta-notes"], query=q, telemetry_dir=td, session_id="s1")
    T.log_outcome("Edit", "src/a.py", session_id="s1", telemetry_dir=td)
    T.log_outcome("Edit", "src/b.py", session_id="s1", telemetry_dir=td)
    summary = EF.draft_livedin_fixtures(memory_dir, telemetry_dir=td)
    assert summary["added"] == [q]
    docs = [d for d in yaml.safe_load_all(open(default_drafts_path(memory_dir), encoding="utf-8")) if d is not None]
    row = next(r for r in docs[-1] if r["query"] == q)
    assert row["derived_expected"] == ["alpha-notes", "beta-notes"]
