"""SIG-6: abstention → self-populating eval fixtures (KPI-4).

The draft→confirm loop: ``eval_recall.draft_abstention_fixtures`` turns recurring
``backend='none'`` abstention clusters (SIG-3's backlog) into CANDIDATE rows in a
gitignored drafts queue — ``expected`` always empty, the judgment never automated —
and ``eval_recall.confirm_hard_set_row`` is the per-item admission gate into the
tracked ``.audit-fixtures/recall_hard_set.yaml``, tagging rows ``category: abstention``
(RET-8's data-driven tag). Hermetic, BM25-only.
"""

from __future__ import annotations

import os

import pytest
import yaml

from memory import build_index as B
from memory import eval_recall as E
from memory import telemetry as T
from memory.build_index import default_index_dir
from memory.telemetry import default_telemetry_dir


@pytest.fixture(autouse=True)
def _no_ambient_pending_dir(monkeypatch):
    """The drafts queue derives from the pending dir — never an ambient override. And
    BM25-only throughout: this module tests the draft/confirm lifecycle, not embeddings
    (the dense model would also cost ~seconds per index build on a warm cache)."""
    monkeypatch.delenv("HIPPO_PENDING_DIR", raising=False)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")


def _abstain(td, query, n=3):
    """Log ``n`` abstentions (empty results -> backend='none') — one recurring cluster."""
    for _ in range(n):
        T.log_recall_event([], query=query, k=6, latency_ms=1.0, telemetry_dir=td, session_id="s")


def _mem(md, stem, description):
    path = os.path.join(md, f"{stem}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f'---\nname: {stem}\ndescription: "{description}"\ntype: project\n---\nbody\n')
    return path


def _corpus_with_index(md):
    _mem(md, "api_key_rotation", "rotate api keys vault quarterly rotation procedure")
    _mem(md, "deploy_rollback", "roll back a bad deploy via the release pipeline")
    B.build_index(md, default_index_dir(md))


QUERY = "how do I rotate the api keys"


# ---- draft_abstention_fixtures ------------------------------------------------------------- #
def test_recurring_clusters_become_draft_rows(memory_dir):
    _corpus_with_index(memory_dir)
    _abstain(default_telemetry_dir(memory_dir), QUERY)

    out = E.draft_abstention_fixtures(memory_dir)
    dp = E.default_drafts_path(memory_dir)
    assert out["path"] == dp and out["added"] == [QUERY] and out["clusters"] == 1
    meta, rows = E._load_fixture_docs(dp)
    assert meta.get("draft") is True
    assert "confirm_hard_set_row" in meta.get("note", "")
    assert len(rows) == 1
    row = rows[0]
    assert row["query"] == QUERY and row["count"] == 3
    assert {"rotate", "api", "keys"} <= set(row["terms"])


def test_drafts_queue_lands_in_selfignoring_pending_dir(memory_dir):
    """Raw ledger queries must never be a `git add .` away from a commit (SEC-3)."""
    _abstain(default_telemetry_dir(memory_dir), QUERY)
    E.draft_abstention_fixtures(memory_dir, probe=False)
    dp = E.default_drafts_path(memory_dir)
    pending = os.path.dirname(dp)
    assert os.path.basename(pending) == ".memory-pending"
    with open(os.path.join(pending, ".gitignore"), "r", encoding="utf-8") as fh:
        assert fh.read().strip() == "*"


def test_draft_rows_probe_current_hits_and_record_backend(memory_dir):
    """current_hits = what recall surfaces NOW — judgment material, never a verdict."""
    _corpus_with_index(memory_dir)
    _abstain(default_telemetry_dir(memory_dir), QUERY)

    E.draft_abstention_fixtures(memory_dir)
    meta, rows = E._load_fixture_docs(E.default_drafts_path(memory_dir))
    assert "api_key_rotation" in rows[0]["current_hits"]
    assert meta.get("generated_with_backend") == "bm25-only"  # honest: no dense in tests


def test_draft_never_fills_expected(memory_dir):
    """The judgment is deliberately not automated — a draft row NEVER proposes expected."""
    _corpus_with_index(memory_dir)
    _abstain(default_telemetry_dir(memory_dir), QUERY)

    E.draft_abstention_fixtures(memory_dir)
    _meta, rows = E._load_fixture_docs(E.default_drafts_path(memory_dir))
    assert all(r["expected"] == [] for r in rows)
    # And an unfilled draft row is not even loadable as a fixture row.
    assert E.load_hard_set(E.default_drafts_path(memory_dir)) == []


def test_probe_false_skips_recall_and_backend_claim(memory_dir):
    _corpus_with_index(memory_dir)
    _abstain(default_telemetry_dir(memory_dir), QUERY)

    E.draft_abstention_fixtures(memory_dir, probe=False)
    meta, rows = E._load_fixture_docs(E.default_drafts_path(memory_dir))
    assert rows[0]["current_hits"] == []
    assert "generated_with_backend" not in meta


def test_redraft_appends_only_and_preserves_agent_judgments(memory_dir):
    """Re-running the drafter must never clobber an agent-filled ``expected`` awaiting
    confirmation: existing bytes are preserved verbatim; new clusters only append."""
    _corpus_with_index(memory_dir)
    td = default_telemetry_dir(memory_dir)
    _abstain(td, QUERY)
    E.draft_abstention_fixtures(memory_dir)

    dp = E.default_drafts_path(memory_dir)
    with open(dp, "r", encoding="utf-8") as fh:
        text = fh.read()
    edited = text.replace("expected: []", 'expected: ["api_key_rotation"]', 1)
    with open(dp, "w", encoding="utf-8") as fh:
        fh.write(edited)

    _abstain(td, "database migration ordering steps")
    out = E.draft_abstention_fixtures(memory_dir)
    assert out["added"] == ["database migration ordering steps"] and out["kept"] == 1
    with open(dp, "r", encoding="utf-8") as fh:
        after = fh.read()
    assert after.startswith(edited)  # byte-verbatim prefix — the filled judgment survived
    _meta, rows = E._load_fixture_docs(dp)
    assert [r["query"] for r in rows] == [QUERY, "database migration ordering steps"]
    assert rows[0]["expected"] == ["api_key_rotation"]


def test_cluster_already_tracked_is_skipped(memory_dir):
    """A confirmed row's loop is CLOSED — its cluster must not re-draft forever."""
    _abstain(default_telemetry_dir(memory_dir), QUERY)
    fp = E._project_fixture_path(memory_dir)
    os.makedirs(os.path.dirname(fp))
    with open(fp, "w", encoding="utf-8") as fh:
        yaml.safe_dump(
            [{"query": QUERY, "expected": ["api_key_rotation"], "category": "abstention"}], fh
        )

    out = E.draft_abstention_fixtures(memory_dir, probe=False)
    assert out["skipped_tracked"] == [QUERY] and out["added"] == []
    assert not os.path.exists(E.default_drafts_path(memory_dir))  # nothing to write


def test_no_recurring_backlog_writes_nothing(memory_dir):
    _abstain(default_telemetry_dir(memory_dir), "one off question", n=1)
    out = E.draft_abstention_fixtures(memory_dir, probe=False)
    assert out["clusters"] == 0 and out["added"] == []
    assert not os.path.exists(E.default_drafts_path(memory_dir))


def test_drafts_never_consumed_by_default_fixture_probe(memory_dir, repo, monkeypatch):
    """The drafts filename is not a canonical fixture name — eval defaults never see it."""
    _abstain(default_telemetry_dir(memory_dir), QUERY)
    E.draft_abstention_fixtures(memory_dir, probe=False)

    monkeypatch.chdir(repo)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", memory_dir)
    assert E._default_fixture_path("recall_hard_set.yaml") is None


def test_broken_drafts_file_refuses_append(memory_dir):
    _abstain(default_telemetry_dir(memory_dir), QUERY)
    dp = E.default_drafts_path(memory_dir)
    os.makedirs(os.path.dirname(dp))
    with open(dp, "w", encoding="utf-8") as fh:
        fh.write("{broken yaml: [unclosed\n")
    before = open(dp, encoding="utf-8").read()

    out = E.draft_abstention_fixtures(memory_dir, probe=False)
    assert "error" in out and out["added"] == []
    assert open(dp, encoding="utf-8").read() == before  # never buried the breakage


def test_drafter_explicit_memory_dir_is_hermetic(memory_dir, monkeypatch):
    """An explicit memory_dir must never re-resolve ambient state (the RET-8 CLI lesson)."""

    def _boom():
        raise AssertionError("resolve_dirs() called despite explicit memory_dir")

    monkeypatch.setattr(E, "resolve_dirs", _boom)
    _abstain(default_telemetry_dir(memory_dir), QUERY)
    out = E.draft_abstention_fixtures(memory_dir, probe=False)
    assert out["added"] == [QUERY]
    r = E.confirm_hard_set_row(QUERY, ["ghost"], memory_dir)
    assert r["ok"] is False  # validated hermetically too (missing stem, see below)


# ---- confirm_hard_set_row ------------------------------------------------------------------ #
def test_confirm_appends_tracked_row_tagged_abstention(memory_dir):
    _corpus_with_index(memory_dir)
    r = E.confirm_hard_set_row(QUERY, ["api_key_rotation"], memory_dir)
    assert r["ok"] is True and r["category"] == "abstention"

    rows = E.load_hard_set(E._project_fixture_path(memory_dir))
    assert rows == [{"query": QUERY, "expected": ["api_key_rotation"], "category": "abstention"}]
    # The RET-8 by_category bucketing sees the tag (delegates to the one scoring path).
    idx = B.load_index(default_index_dir(memory_dir))
    buckets = E.hard_set_metrics_by_category(idx, rows)
    assert list(buckets) == ["abstention"] and buckets["abstention"]["n"] == 1


def test_confirm_category_is_data_driven(memory_dir):
    """RET-8: unknown tags form their own bucket — SIG-6 admission takes any category."""
    _mem(memory_dir, "m1", "alpha beta gamma")
    r = E.confirm_hard_set_row("some q", ["m1"], memory_dir, category="team-hot")
    assert r["ok"] and E.load_hard_set(E._project_fixture_path(memory_dir))[0]["category"] == "team-hot"


def test_confirm_refuses_fabricated_stem(memory_dir):
    """Never fabricate a memory to make a fixture pass — the killed demand-gap-auto-draft."""
    r = E.confirm_hard_set_row(QUERY, ["ghost_memory"], memory_dir)
    assert r["ok"] is False and "never fabricate" in r["reason"]
    assert not os.path.exists(E._project_fixture_path(memory_dir))


def test_confirm_refuses_empty_expected(memory_dir):
    for bad in ([], [""], None):
        r = E.confirm_hard_set_row(QUERY, bad, memory_dir)
        assert r["ok"] is False and "capture gap" in r["reason"]


def test_confirm_refuses_duplicate_query(memory_dir):
    _mem(memory_dir, "m1", "alpha beta gamma")
    assert E.confirm_hard_set_row(QUERY, ["m1"], memory_dir)["ok"] is True
    r = E.confirm_hard_set_row(QUERY, ["m1"], memory_dir)
    assert r["ok"] is False and "already" in r["reason"]


def test_confirm_normalizes_md_suffix_and_refuses_paths(memory_dir):
    _mem(memory_dir, "m1", "alpha beta gamma")
    r = E.confirm_hard_set_row("q one", ["m1.md"], memory_dir)
    assert r["ok"] is True and r["expected"] == ["m1"]
    r2 = E.confirm_hard_set_row("q two", ["../m1"], memory_dir)
    assert r2["ok"] is False and "bare memory stems" in r2["reason"]


def test_confirm_preserves_existing_fixture_bytes(memory_dir):
    """Admission is a textual APPEND — a hand-curated fixture (comments and all) is never
    regenerated (the RUL-7 propose-a-diff discipline applied to fixtures)."""
    _mem(memory_dir, "m1", "alpha beta gamma")
    fp = E._project_fixture_path(memory_dir)
    os.makedirs(os.path.dirname(fp))
    original = (
        "generated_with_backend: bm25-only\n"
        "generated_at: 2026-07-01\n"
        "---\n"
        "# hand-curated rows — comment must survive\n"
        '- query: "existing paraphrase query"\n'
        "  expected: [m1]\n"
    )
    with open(fp, "w", encoding="utf-8") as fh:
        fh.write(original)

    r = E.confirm_hard_set_row(QUERY, ["m1"], memory_dir)
    assert r["ok"] is True
    text = open(fp, encoding="utf-8").read()
    assert text.startswith(original)
    rows = E.load_hard_set(fp)
    assert [row["query"] for row in rows] == ["existing paraphrase query", QUERY]
    assert rows[0]["category"] == "single-hop" and rows[1]["category"] == "abstention"
    # Header survives too: the fixture's provenance is untouched by admission.
    assert E.load_hard_set_metadata(fp).get("generated_with_backend") == "bm25-only"


def test_confirm_creates_fixture_with_minimal_honest_header(memory_dir):
    """A confirm-created fixture claims NO backend (rows come from traffic, not synthesis),
    so RET-7's backend_mismatch can never fire against it."""
    _mem(memory_dir, "m1", "alpha beta gamma")
    E.confirm_hard_set_row(QUERY, ["m1"], memory_dir)
    meta = E.load_hard_set_metadata(E._project_fixture_path(memory_dir))
    assert "generated_at" in meta and "generated_with_backend" not in meta


def test_confirm_refuses_broken_tracked_fixture(memory_dir):
    _mem(memory_dir, "m1", "alpha beta gamma")
    fp = E._project_fixture_path(memory_dir)
    os.makedirs(os.path.dirname(fp))
    with open(fp, "w", encoding="utf-8") as fh:
        fh.write("{broken yaml: [unclosed\n")
    r = E.confirm_hard_set_row(QUERY, ["m1"], memory_dir)
    assert r["ok"] is False and "not parseable" in r["reason"]


def test_confirm_drains_the_draft_row(memory_dir):
    _corpus_with_index(memory_dir)
    td = default_telemetry_dir(memory_dir)
    _abstain(td, QUERY)
    _abstain(td, "database migration ordering steps")
    E.draft_abstention_fixtures(memory_dir)

    r = E.confirm_hard_set_row(QUERY, ["api_key_rotation"], memory_dir)
    assert r["ok"] is True and r["removed_from_drafts"] is True
    meta, rows = E._load_fixture_docs(E.default_drafts_path(memory_dir))
    assert [row["query"] for row in rows] == ["database migration ordering steps"]
    assert meta.get("draft") is True  # the queue header survives the drain


# ---- the loop, end to end ------------------------------------------------------------------ #
def test_gap_closing_loop_end_to_end(memory_dir):
    """Abstention → draft → capture closes the gap → per-item confirm → the tracked eval's
    abstention category measures it (KPI-4 grows from real traffic)."""
    td = default_telemetry_dir(memory_dir)
    _mem(memory_dir, "deploy_rollback", "roll back a bad deploy via the release pipeline")
    B.build_index(memory_dir, default_index_dir(memory_dir))
    _abstain(td, QUERY)

    E.draft_abstention_fixtures(memory_dir)
    _meta, rows = E._load_fixture_docs(E.default_drafts_path(memory_dir))
    assert rows[0]["expected"] == []  # gap still open: no judgment, no fabrication

    # The gap closes the ONLY legitimate way: a real memory is captured (consolidate).
    _mem(memory_dir, "api_key_rotation", "rotate api keys vault quarterly rotation procedure")
    B.build_index(memory_dir, default_index_dir(memory_dir))
    r = E.confirm_hard_set_row(QUERY, ["api_key_rotation"], memory_dir)
    assert r["ok"] is True

    report = E.evaluate(
        memory_dir=memory_dir, hard_set_path=E._project_fixture_path(memory_dir)
    )
    cat = report["by_category"]["abstention"]
    assert cat["n"] == 1 and cat["recall"] == 1.0  # the closed gap is now measured
