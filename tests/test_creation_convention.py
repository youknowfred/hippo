"""Tests for the creation-convention layer — lint_floor.py (floor guard) + new_memory.py.

Hermetic: every test builds a tmp memory dir with a real-shaped MEMORY.md; nothing touches
the real ~/.claude. new_memory tests disable dense + pin CLAUDE_PROJECT_DIR to tmp.
"""

from __future__ import annotations

import os

import pytest

import memory.lint_floor as floor

# A real-shaped trimmed floor: memory pointers ONLY under User + Working-Style; the
# MEMORY.full.md restore link appears in BOTH the preamble and the "Recalled on demand" nav
# header (this is what the allow-list must tolerate without false-positiving).
_CLEAN_FLOOR = """# IC Memobot — Auto-Memory Index (durable floor)
> Always-loaded floor: the User + Working-Style memories. Full snapshot in [MEMORY.full.md](MEMORY.full.md).
## User
- [User Role](user_role.md) — solo founder.
## Working Style & Process Feedback
- [Some Feedback](feedback_x.md) — a process hook.
## Recalled on demand
> Section map (nav only); full index in [MEMORY.full.md](MEMORY.full.md):
- Active / In-Flight Work
- Infra, Git, Deploy & Railway Ops
"""


def _floor(md, body):
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write(body)


def _touch_memory(md, name):
    with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write(f"---\nname: {name}\ndescription: d\n---\nbody\n")


# --------------------------------------------------------------------------- #
# lint_floor — the floor-invariant guard
# --------------------------------------------------------------------------- #
def test_floor_clean_on_real_shaped_floor(tmp_path):
    md = str(tmp_path / "memory")
    _floor(md, _CLEAN_FLOOR)
    _touch_memory(md, "user_role")
    _touch_memory(md, "feedback_x")
    v = floor.floor_violations(md)
    assert v["rebloat"] == []  # the allow-listed MEMORY.full.md links do NOT trip the guard
    assert v["missing_targets"] == []
    assert floor.floor_producer(md, str(tmp_path)) is None  # silent when clean


def test_floor_flags_project_link_outside_floor_sections(tmp_path):
    md = str(tmp_path / "memory")
    _floor(md, _CLEAN_FLOOR + "- [Sneaky Project](project_sneaky.md) — leaked into the floor\n")
    _touch_memory(md, "user_role")
    _touch_memory(md, "feedback_x")
    v = floor.floor_violations(md)
    assert any(
        r["file"] == "project_sneaky.md" and r["section"] == "Recalled on demand"
        for r in v["rebloat"]
    )
    out = floor.floor_producer(md, str(tmp_path))
    assert out and "project_sneaky.md" in out and "re-bloat" in out.lower()
    assert len(out) <= floor._MAX_CHARS


def test_floor_guard_never_edits_memory_md(tmp_path):
    md = str(tmp_path / "memory")
    _floor(md, _CLEAN_FLOOR + "- [Leak](leak_mem.md) — re-bloat\n")
    p = os.path.join(md, "MEMORY.md")
    before = open(p, "rb").read()
    floor.floor_violations(md)
    floor.floor_producer(md, str(tmp_path))
    assert open(p, "rb").read() == before  # READ-ONLY


def test_floor_flags_missing_target(tmp_path):
    md = str(tmp_path / "memory")
    _floor(md, _CLEAN_FLOOR)
    _touch_memory(md, "user_role")  # feedback_x.md deliberately NOT created
    v = floor.floor_violations(md)
    assert any(m["file"] == "feedback_x.md" for m in v["missing_targets"])
    assert v["rebloat"] == []


def test_floor_violations_missing_file_never_raises(tmp_path):
    v = floor.floor_violations(str(tmp_path / "no_such_dir"))
    assert v == {"rebloat": [], "missing_targets": []}


def test_floor_producer_registered_in_dispatcher():
    import memory.session_start as S

    assert any(label == "floor" and fn is floor.floor_producer for label, fn in S.PRODUCERS)


# --------------------------------------------------------------------------- #
# new_memory — recall-ready creation; floor pointer only for user/feedback
# --------------------------------------------------------------------------- #
def _nm_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))  # hermetic resolve_dirs
    md = str(tmp_path / ".claude" / "memory")
    _floor(md, _CLEAN_FLOOR)
    return md


def test_new_memory_feedback_adds_pointer_and_is_recallable(tmp_path, monkeypatch):
    from memory import new_memory as NM
    from memory import recall as R

    md = _nm_env(tmp_path, monkeypatch)
    res = NM.write_memory(
        "feedback_test_unique",
        "alpha beta gamma unique feedback hook",
        "feedback",
        body="**Why:** x\n**How to apply:** y",
        memory_dir=md,
        repo_root=str(tmp_path),
    )
    assert res["created"] is True and res["error"] is None

    text = open(os.path.join(md, "feedback_test_unique.md"), encoding="utf-8").read()
    assert "name: feedback_test_unique" in text
    assert "description:" in text
    assert "type: feedback" in text

    # floor pointer added under Working-Style for a feedback memory
    assert res["floor"] == {"status": "appended", "reason": None}
    mem = open(os.path.join(md, "MEMORY.md"), encoding="utf-8").read()
    assert "(feedback_test_unique.md)" in mem

    # recallable after the in-call refresh
    names = {r["name"] for r in R.recall("alpha beta gamma unique", memory_dir=md)}
    assert "feedback_test_unique" in names


def test_new_memory_project_skips_floor_pointer(tmp_path, monkeypatch):
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    before = open(os.path.join(md, "MEMORY.md"), "rb").read()
    res = NM.write_memory(
        "project_thing_xyz",
        "some project memory description",
        "project",
        memory_dir=md,
        repo_root=str(tmp_path),
    )
    assert res["created"] is True
    assert res["floor"]["status"] == "skipped"
    assert "never floor-linked" in res["floor"]["reason"]
    after = open(os.path.join(md, "MEMORY.md"), "rb").read()
    assert before == after  # the floor is UNCHANGED for a project memory (no re-bloat)
    assert "project_thing_xyz.md" not in after.decode("utf-8")


def test_new_memory_refuses_overwrite(tmp_path, monkeypatch):
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    p = os.path.join(md, "existing_mem.md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("ORIGINAL CONTENT\n")
    res = NM.write_memory("existing_mem", "d", "project", memory_dir=md, repo_root=str(tmp_path))
    assert res["created"] is False
    assert "exists" in (res["error"] or "")
    assert open(p, encoding="utf-8").read() == "ORIGINAL CONTENT\n"  # untouched


def test_new_memory_rejects_invalid_type(tmp_path, monkeypatch):
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    res = NM.write_memory("x_mem", "d", "bogus", memory_dir=md, repo_root=str(tmp_path))
    assert res["created"] is False
    assert "invalid type" in (res["error"] or "")
    assert not os.path.exists(os.path.join(md, "x_mem.md"))


def test_new_memory_rejects_path_separator_name(tmp_path, monkeypatch):
    """A path-separator/empty name is rejected up front — it would otherwise write the file
    OUTSIDE memory_dir (a created-but-invisible memory)."""
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    for bad in ("../escape_attempt", "sub/dir/name", ""):
        res = NM.write_memory(bad, "desc", "project", memory_dir=md, repo_root=str(tmp_path))
        assert res["created"] is False
        assert "invalid name" in (res["error"] or "")
    # nothing escaped to the .claude level (parent of memory_dir)
    assert not os.path.exists(os.path.join(md, "..", "escape_attempt.md"))


def test_new_memory_does_not_touch_unrelated_bodies(tmp_path, monkeypatch):
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    other = os.path.join(md, "other_mem.md")
    with open(other, "w", encoding="utf-8") as fh:
        fh.write("---\nname: other_mem\ndescription: keep me\n---\nUNTOUCHED BODY\n")
    other_before = open(other, "rb").read()
    NM.write_memory("new_proj_mem", "d desc", "project", memory_dir=md, repo_root=str(tmp_path))
    assert open(other, "rb").read() == other_before  # unrelated memory body unchanged


def test_new_memory_born_staleness_tracked_in_dirty_worktree(tmp_path, monkeypatch):
    """COR-1: a memory created via write_memory in a git repo (dirty worktree — the file
    itself has no commit history) carries HEAD as source_commit at CREATION, so
    find_stale/reconsolidation/archive gating see it immediately."""
    import subprocess

    from memory import new_memory as NM
    from memory.provenance import parse_frontmatter

    from .conftest import git_commit, write_file

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    write_file(repo, "src/app.py", "x = 1\n")
    head = git_commit(repo, "init", 1_700_000_000)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)

    md = os.path.join(repo, ".claude", "memory")
    _floor(md, _CLEAN_FLOOR)
    res = NM.write_memory(
        "born_tracked",
        "a fact about src/app.py that must be staleness-tracked from birth",
        "project",
        body="src/app.py does x.",
        memory_dir=md,
        repo_root=repo,
    )
    assert res["created"] is True and res["error"] is None

    fm = parse_frontmatter(open(res["path"], encoding="utf-8").read())
    meta = fm.get("metadata") or {}
    sc = fm.get("source_commit") or meta.get("source_commit")
    assert sc == head, f"expected a HEAD baseline at creation, got {sc!r}"


# --------------------------------------------------------------------------- #
# GRA-3 — link creation at write time (recall-discovered "Related: [[...]]")
# --------------------------------------------------------------------------- #
def test_new_memory_discovers_related_links_and_linkgraph_resolves_them(tmp_path, monkeypatch):
    """write_memory on a corpus with related existing memories appends a Related: [[...]] body
    line whose targets LinkGraph actually resolves (acceptance criterion #1)."""
    from memory import new_memory as NM
    from memory.links import build_graph

    md = _nm_env(tmp_path, monkeypatch)
    # Seed two existing memories that clearly overlap the new one's topic.
    NM.write_memory(
        "railway_deploy_pipeline",
        "how the railway deploy pipeline builds and ships the app",
        "project",
        body="the pipeline runs on every push to main.",
        memory_dir=md,
        repo_root=str(tmp_path),
    )
    NM.write_memory(
        "railway_env_vars",
        "railway deploy pipeline environment variables and secrets",
        "project",
        body="secrets live in the railway dashboard.",
        memory_dir=md,
        repo_root=str(tmp_path),
    )

    res = NM.write_memory(
        "railway_rollback_procedure",
        "how to roll back the railway deploy pipeline after a bad release",
        "project",
        body="run the rollback script and re-deploy the previous build.",
        memory_dir=md,
        repo_root=str(tmp_path),
    )
    assert res["created"] is True and res["error"] is None
    assert res["related"], "expected recall to surface at least one related existing memory"

    text = open(res["path"], encoding="utf-8").read()
    assert "Related: " in text
    for r in res["related"]:
        assert f"[[{r}]]" in text

    g = build_graph(md)
    assert g is not None
    outbound = g.outbound("railway_rollback_procedure")
    # every suggested name must be a resolvable, real edge — not just literal text
    assert set(res["related"]) <= outbound


def test_fresh_project_five_writes_yields_nonzero_edge_density(tmp_path, monkeypatch):
    """Fresh-project simulation (the roadmap's literal acceptance criterion): five write_memory
    calls with overlapping topics on a corpus that starts EMPTY must yield edge count > 0."""
    from memory import new_memory as NM
    from memory.links import build_graph

    md = _nm_env(tmp_path, monkeypatch)
    topics = [
        ("onboarding_flow", "the user onboarding flow walks a new signup through setup"),
        ("onboarding_email_copy", "onboarding email copy sent during the signup flow"),
        ("signup_form_validation", "signup form validation rules for the onboarding flow"),
        ("billing_plan_tiers", "billing plan tiers offered after onboarding completes"),
        ("billing_invoice_format", "billing invoice format used for plan tier billing"),
    ]
    for name, desc in topics:
        res = NM.write_memory(
            name, desc, "project", body=f"notes about {name}.", memory_dir=md, repo_root=str(tmp_path)
        )
        assert res["created"] is True and res["error"] is None

    g = build_graph(md)
    assert g is not None
    total_edges = sum(len(v) for v in g.adjacency.values())
    assert total_edges > 0, "fresh project should reach nonzero edge density within its first five memories"


def test_new_memory_no_links_suppresses_discovery(tmp_path, monkeypatch):
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    NM.write_memory(
        "topic_a", "a memory about the deploy pipeline topic", "project", memory_dir=md, repo_root=str(tmp_path)
    )
    res = NM.write_memory(
        "topic_b",
        "another memory about the deploy pipeline topic",
        "project",
        memory_dir=md,
        repo_root=str(tmp_path),
        no_links=True,
    )
    assert res["created"] is True
    assert res["related"] == []
    text = open(res["path"], encoding="utf-8").read()
    assert "Related:" not in text


def test_new_memory_links_override_discovery(tmp_path, monkeypatch):
    """--links (explicit) OVERRIDES discovery entirely — no recall() call, the given names win
    verbatim even if they wouldn't have been the top BM25 hits."""
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    NM.write_memory(
        "totally_unrelated_topic", "something about gardening and houseplants", "project",
        memory_dir=md, repo_root=str(tmp_path),
    )
    res = NM.write_memory(
        "topic_c",
        "a memory about the deploy pipeline",
        "project",
        memory_dir=md,
        repo_root=str(tmp_path),
        links=["totally_unrelated_topic"],
    )
    assert res["created"] is True
    assert res["related"] == ["totally_unrelated_topic"]
    text = open(res["path"], encoding="utf-8").read()
    assert "[[totally_unrelated_topic]]" in text


def test_new_memory_empty_corpus_no_related_line_no_error(tmp_path, monkeypatch):
    """Empty corpus (the very first memory ever created) -> no Related line, no error."""
    from memory import new_memory as NM

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    md = str(tmp_path / ".claude" / "memory")  # deliberately NOT pre-seeded — truly empty/absent
    res = NM.write_memory(
        "first_ever_memory", "the very first memory in this corpus", "project",
        memory_dir=md, repo_root=str(tmp_path),
    )
    assert res["created"] is True and res["error"] is None
    assert res["related"] == []
    text = open(res["path"], encoding="utf-8").read()
    assert "Related:" not in text


# --------------------------------------------------------------------------- #
# LIF-2 — duplicate/conflict detection at write time (warn-only, agent-gated)
# --------------------------------------------------------------------------- #
# Three seed memories (not fewer): a 1-2 doc corpus's BM25 idf mass is degenerate
# (df==N terms floor to zero/negative idf), which the detector honestly refuses to
# score — three distinct-vocabulary docs give every term a positive idf.
_DUP_SEED = [
    ("railway_deploy_pipeline", "how the railway deploy pipeline builds and ships the app"),
    ("billing_plan_tiers", "billing plan tiers offered after onboarding completes"),
    ("signup_form_validation", "signup form validation rules for the onboarding flow"),
]


def _seed_dup_corpus(NM, md, tmp_path):
    for name, desc in _DUP_SEED:
        res = NM.write_memory(
            name, desc, "project", body=f"notes about {name}.", memory_dir=md, repo_root=str(tmp_path)
        )
        assert res["created"] is True and res["error"] is None


def test_new_memory_near_duplicate_surfaces_neighbor_and_never_rejects(tmp_path, monkeypatch):
    """AC (hermetic BM25 path): a near-duplicate description surfaces the existing twin in
    result["neighbors"] ({name, score, description}) — and creation still proceeds (the
    no-autonomous-rejection bar: agent decides, tool reports)."""
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    _seed_dup_corpus(NM, md, tmp_path)

    res = NM.write_memory(
        "deploy_pipeline_rebuild_notes",
        "how the railway deploy pipeline builds and ships the app again",
        "project",
        body="re-captured months later in nearly the same words.",
        memory_dir=md,
        repo_root=str(tmp_path),
    )
    # NEVER auto-rejects: the file exists despite the near-dupe.
    assert res["created"] is True and res["error"] is None
    assert os.path.exists(res["path"])
    # The twin surfaces, best-first, in the documented shape; the check RAN (no note).
    assert res["note"] is None
    assert res["neighbors"], "expected the near-duplicate twin to surface"
    top = res["neighbors"][0]
    assert top["name"] == "railway_deploy_pipeline"
    assert top["score"] >= NM._DUP_BM25_THRESHOLD
    assert top["description"] == "how the railway deploy pipeline builds and ships the app"


def test_new_memory_distinct_creation_yields_no_neighbors(tmp_path, monkeypatch):
    """The calibrated BM25 threshold must NOT flag distinct memories (the existing
    fixture-style corpus cross-scores at ~0.0 — see the LIF-2 calibration)."""
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    _seed_dup_corpus(NM, md, tmp_path)

    res = NM.write_memory(
        "gardening_watering_schedule",
        "watering schedule for indoor houseplants during winter months",
        "project",
        memory_dir=md,
        repo_root=str(tmp_path),
    )
    assert res["created"] is True and res["neighbors"] == [] and res["note"] is None


def test_new_memory_index_absent_stays_warning_free_and_note_carrying(tmp_path, monkeypatch):
    """AC: index-absent creation yields NO neighbor warning but a machine-readable note —
    degradation must be legible, never silent (and never an implicit build or download)."""
    from memory import new_memory as NM

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    # Case 1: the very first memory ever (no corpus dir, no index).
    md = str(tmp_path / ".claude" / "memory")
    res = NM.write_memory(
        "first_ever_memory", "the very first memory in this corpus", "project",
        memory_dir=md, repo_root=str(tmp_path),
    )
    assert res["created"] is True and res["error"] is None
    assert res["neighbors"] == []
    assert res["note"] == "duplicate check skipped: no index"

    # Case 2: a corpus with files but no index yet, and --no-links suppressing the GRA-3
    # recall() (whose implicit build would otherwise have created one) — same legible note.
    md2 = str(tmp_path / "elsewhere" / ".claude" / "memory")
    os.makedirs(md2)
    _touch_memory(md2, "pre_existing")
    res2 = NM.write_memory(
        "second_memory", "another memory description", "project",
        memory_dir=md2, repo_root=str(tmp_path / "elsewhere"), no_links=True,
    )
    assert res2["created"] is True
    assert res2["neighbors"] == []
    assert res2["note"] == "duplicate check skipped: no index"


def test_new_memory_dense_path_scores_cosine_with_fake_embeddings(tmp_path, monkeypatch):
    """Dense path (hermetic fake embedder, house pattern): with a dense-ready index the
    detector scores COSINE via embed_query against the persisted rows — pinned by matching
    the fake embedder's own dot product exactly, with the BM25 scorer boobytrapped to prove
    the fallback is never consulted."""
    import zlib

    import numpy as np

    from memory import build_index as B
    from memory import new_memory as NM
    from memory import recall as R

    def _vec(text: str):
        v = np.zeros(16, dtype="float32")
        for tok in B.tokenize(text):
            v[zlib.crc32(tok.encode("utf-8")) % 16] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def emb_docs(texts, allow_download=True):
        return np.vstack([_vec(t) for t in texts]).astype("float32")

    def emb_query(text, allow_download=False):
        return _vec(text)

    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", emb_docs)
    monkeypatch.setattr(B, "embed_query", emb_query)
    monkeypatch.setattr(R, "embed_query", emb_query)  # GRA-3's recall() shares the index
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    md = str(tmp_path / ".claude" / "memory")
    _floor(md, _CLEAN_FLOOR)

    _seed_dup_corpus(NM, md, tmp_path)
    assert B.load_index(B.default_index_dir(md)).dense_ready is True

    def boom(*a, **k):
        raise AssertionError("BM25 fallback must not be consulted on the dense path")

    monkeypatch.setattr(R, "_bm25_score_via_postings", boom)

    res = NM.write_memory(
        "deploy_pipeline_rebuild_notes",
        "how the railway deploy pipeline builds and ships the app again",
        "project",
        body="re-captured months later in nearly the same words.",
        memory_dir=md,
        repo_root=str(tmp_path),
        no_links=True,  # recall()'s BM25 half would trip the boom above; dense check is the probe
    )
    assert res["created"] is True and res["error"] is None
    assert res["note"] is None
    assert res["neighbors"] and res["neighbors"][0]["name"] == "railway_deploy_pipeline"

    # The reported score IS the fake embedder's cosine (query doc_text vs twin doc_text) —
    # proving the dense path scored, not the normalized-BM25 fallback.
    twin_text = open(os.path.join(md, "railway_deploy_pipeline.md"), encoding="utf-8").read()
    new_text = open(res["path"], encoding="utf-8").read()
    expected = float(
        np.dot(
            _vec(B.memory_doc_text("deploy_pipeline_rebuild_notes", new_text)),
            _vec(B.memory_doc_text("railway_deploy_pipeline", twin_text)),
        )
    )
    assert res["neighbors"][0]["score"] == pytest.approx(expected, abs=1e-3)
    assert res["neighbors"][0]["score"] >= NM._DUP_COSINE_THRESHOLD


def test_new_memory_dup_threshold_env_override(tmp_path, monkeypatch):
    """HIPPO_DUP_THRESHOLD overrides the calibrated default — raising it above the twin's
    score suppresses the warning (created either way; warn-only)."""
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    _seed_dup_corpus(NM, md, tmp_path)
    monkeypatch.setenv("HIPPO_DUP_THRESHOLD", "0.999")

    res = NM.write_memory(
        "deploy_pipeline_rebuild_notes",
        "how the railway deploy pipeline builds and ships the app again",
        "project",
        memory_dir=md,
        repo_root=str(tmp_path),
    )
    assert res["created"] is True
    assert res["neighbors"] == []


def test_new_memory_cli_prints_neighbor_block_and_note(tmp_path, monkeypatch, capsys):
    """main() renders the neighbor warning legibly (twin + similarity + the four-way
    decision routed to /hippo:new) and, when the check can't run, the machine-readable note."""
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    _seed_dup_corpus(NM, md, tmp_path)
    capsys.readouterr()  # drop seed noise

    rc = NM.main(
        [
            "deploy_pipeline_rebuild_notes",
            "how the railway deploy pipeline builds and ships the app again",
            "--type", "project",
            "--memory-dir", md,
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "near-duplicate" in out
    assert "railway_deploy_pipeline" in out
    assert "add (keep both) / update-existing / supersede / skip" in out
    assert "/hippo:new" in out

    # Index-absent invocation prints the note line instead of a warning.
    md2 = str(tmp_path / "fresh" / ".claude" / "memory")
    rc2 = NM.main(["lone_memory", "a lone description", "--type", "project", "--memory-dir", md2, "--no-links"])
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "near-duplicate" not in out2
    assert "duplicate check skipped: no index" in out2


def test_related_line_lands_before_provenance_backfill_ordering(tmp_path, monkeypatch):
    """The Related: line must land BEFORE provenance backfill runs, so cited_paths/staleness
    computation sees the SAME rendered text that ends up on disk (no post-hoc drift)."""
    import subprocess

    from memory import new_memory as NM
    from memory.provenance import parse_frontmatter

    from .conftest import git_commit, write_file

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    write_file(repo, "src/app.py", "x = 1\n")
    head = git_commit(repo, "init", 1_700_000_000)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)

    md = os.path.join(repo, ".claude", "memory")
    _floor(md, _CLEAN_FLOOR)
    NM.write_memory(
        "existing_topic", "an existing memory about src/app.py behavior", "project",
        body="src/app.py does x.", memory_dir=md, repo_root=repo,
    )
    res = NM.write_memory(
        "new_topic",
        "a new memory also about src/app.py behavior",
        "project",
        body="src/app.py does x, confirmed again.",
        memory_dir=md,
        repo_root=repo,
    )
    assert res["created"] is True and res["error"] is None

    text = open(res["path"], encoding="utf-8").read()
    fm = parse_frontmatter(text)
    meta = fm.get("metadata") or {}
    sc = fm.get("source_commit") or meta.get("source_commit")
    cited = fm.get("cited_paths") or meta.get("cited_paths") or []
    # Provenance backfill ran against the text that ALREADY includes the Related: line (if any
    # was discovered) — i.e. the persisted frontmatter is not stale/pre-Related.
    assert sc == head
    assert "src/app.py" in cited


# --------------------------------------------------------------------------- #
# LIF-5 — floor-pointer robustness when MEMORY.md diverges from the skeleton
# (the append used to silently no-op on a missing file / renamed header — the
#  outcome is now explicit: appended / created-section / skipped-with-reason)
# --------------------------------------------------------------------------- #
def test_floor_happy_path_append_is_byte_stable(tmp_path, monkeypatch):
    """AC (LIF-5, updated for TEA-4 sorted insertion): the happy path is pinned byte-for-byte.
    ``zz_byte_stable`` sorts lexicographically AFTER the section's only existing pointer
    (``feedback_x``), so its deterministic sorted position IS the block tail — this pins the
    "no existing pointer sorts greater" fallback, same pointer format, no incidental reflow
    anywhere else. Mid-section sorted insertion is covered separately (TEA-4 tests below)."""
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    res = NM.write_memory(
        "zz_byte_stable",
        "the hook text",
        "feedback",
        memory_dir=md,
        repo_root=str(tmp_path),
        title="Zz Byte Stable",
        hook="the hook text",
        no_links=True,
    )
    assert res["created"] is True
    assert res["floor"] == {"status": "appended", "reason": None}
    expected = _CLEAN_FLOOR.replace(
        "- [Some Feedback](feedback_x.md) — a process hook.\n",
        "- [Some Feedback](feedback_x.md) — a process hook.\n"
        "- [Zz Byte Stable](zz_byte_stable.md) — the hook text\n",
    )
    assert open(os.path.join(md, "MEMORY.md"), encoding="utf-8").read() == expected


@pytest.mark.parametrize("mtype", ["user", "feedback"])
def test_renamed_section_recreates_canonical_section_and_lint_stays_green(
    tmp_path, monkeypatch, mtype
):
    """AC (LIF-5): a corpus whose canonical section header was hand-renamed away still gets
    its always-load pointer — in a freshly re-created canonical section at the end of
    MEMORY.md (skeleton format) — and lint_floor stays green on the result."""
    from memory import new_memory as NM

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    md = str(tmp_path / ".claude" / "memory")
    canonical = NM._FLOOR_SECTION_BY_TYPE[mtype]
    other = next(h for t, h in NM._FLOOR_SECTION_BY_TYPE.items() if t != mtype)
    # THIS type's section was renamed away (drift); the other type's section is intact.
    _floor(
        md,
        "# Proj — Agent Memory Index (durable floor)\n"
        "> Always-loaded floor preamble.\n"
        f"{other}\n"
        "- [Kept Pointer](kept_ptr.md) — survives under the intact section.\n"
        f"{canonical.replace('## ', '## Renamed ')}\n"
        "> hand-renamed: no canonical header for this type anymore, no pointers left here.\n"
        "## Recalled on demand\n"
        "> nav only.\n",
    )
    _touch_memory(md, "kept_ptr")

    name, desc = f"{mtype}_lif5_repair", "a memory whose floor section header was renamed away"
    res = NM.write_memory(name, desc, mtype, memory_dir=md, repo_root=str(tmp_path))
    assert res["created"] is True and res["error"] is None
    assert res["floor"]["status"] == "created-section"
    assert f"section not found: {canonical}" in res["floor"]["reason"]

    # Skeleton format at EOF: one separating blank line, the canonical header (exactly once),
    # then the pointer as the section's first entry.
    mem = open(os.path.join(md, "MEMORY.md"), encoding="utf-8").read()
    pointer = f"- [{NM._title_from_slug(name)}]({name}.md) — {desc}"
    assert mem.endswith(f"\n\n{canonical}\n{pointer}\n")
    assert mem.count(f"\n{canonical}\n") == 1

    # lint_floor is green on the result AND parses the pointer as a genuine floor entry.
    import memory.lint_floor as lint

    assert lint.floor_violations(md) == {"rebloat": [], "missing_targets": []}
    assert name in lint.floor_memory_names(md)

    # The re-created section is genuinely canonical: the NEXT write of the same type is a
    # plain append into it (the one-time repair does not repeat).
    name2 = f"{mtype}_lif5_second"
    res2 = NM.write_memory(
        name2, "another memory of the same type", mtype, memory_dir=md, repo_root=str(tmp_path)
    )
    assert res2["floor"] == {"status": "appended", "reason": None}
    mem2 = open(os.path.join(md, "MEMORY.md"), encoding="utf-8").read()
    assert mem2.index(f"({name}.md)") < mem2.index(f"({name2}.md)")
    assert mem2.count(f"\n{canonical}\n") == 1


def test_missing_memory_md_is_loud_skip_never_fabricated(tmp_path, monkeypatch):
    """AC (LIF-5): MEMORY.md absent -> the memory is still created + indexed, but the floor
    outcome is a loud machine-readable skip that names the fix — and the file is NOT
    fabricated (floor CREATION is /hippo:init's job: skeleton + starter packs)."""
    from memory import new_memory as NM

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    md = str(tmp_path / ".claude" / "memory")  # dir created by the write; MEMORY.md never seeded
    res = NM.write_memory(
        "orphaned_feedback",
        "feedback with nowhere to floor-point",
        "feedback",
        memory_dir=md,
        repo_root=str(tmp_path),
    )
    assert res["created"] is True and res["error"] is None
    assert res["floor"]["status"] == "skipped"
    assert res["floor"]["reason"].startswith("MEMORY.md missing")
    assert "/hippo:init" in res["floor"]["reason"]  # names the fix, not just the failure
    assert not os.path.exists(os.path.join(md, "MEMORY.md"))  # NOT fabricated


def test_floor_pointer_already_present_skips_with_reason_and_no_edit(tmp_path, monkeypatch):
    """Idempotence stays, but is now NAMED: a floor that already links <name>.md (e.g. a
    hand-restored floor) yields skipped/'pointer already present' and zero bytes change."""
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    _floor(
        md,
        _CLEAN_FLOOR.replace(
            "- [Some Feedback](feedback_x.md) — a process hook.\n",
            "- [Some Feedback](feedback_x.md) — a process hook.\n"
            "- [Hand Added](hand_added.md) — restored by hand.\n",
        ),
    )
    before = open(os.path.join(md, "MEMORY.md"), "rb").read()
    res = NM.write_memory(
        "hand_added",
        "a memory the floor already points to",
        "feedback",
        memory_dir=md,
        repo_root=str(tmp_path),
    )
    assert res["created"] is True
    assert res["floor"] == {"status": "skipped", "reason": "pointer already present"}
    assert open(os.path.join(md, "MEMORY.md"), "rb").read() == before  # untouched


def test_new_memory_cli_prints_floor_outcome_when_not_plain_appended(tmp_path, monkeypatch, capsys):
    """AC (LIF-5): main() surfaces the machine-readable floor reason — missing MEMORY.md and
    created-section both print loudly; the plain append prints a one-word status."""
    from memory import new_memory as NM

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    # Missing MEMORY.md -> loud skip, fix named.
    md = str(tmp_path / "a" / ".claude" / "memory")
    rc = NM.main(["orphan_fb", "desc text", "--type", "feedback", "--memory-dir", md, "--no-links"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "floor   : skipped — MEMORY.md missing" in out
    assert "/hippo:init" in out

    # Renamed section -> created-section with the reason, merge guidance routed to the agent.
    md2 = str(tmp_path / "b" / ".claude" / "memory")
    _floor(md2, "# Floor\n## User\n- [User Role](user_role.md) — role.\n## Renamed Feedback\n")
    rc2 = NM.main(["repaired_fb", "desc text", "--type", "feedback", "--memory-dir", md2, "--no-links"])
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "floor   : created-section — section not found: ## Working Style & Process Feedback" in out2
    assert "/hippo:new" in out2

    # Plain append (into the just-repaired canonical section) -> quiet one-word status.
    rc3 = NM.main(["second_fb", "another desc", "--type", "feedback", "--memory-dir", md2, "--no-links"])
    out3 = capsys.readouterr().out
    assert rc3 == 0
    assert "floor   : appended" in out3
    assert "section not found" not in out3


def test_canonical_floor_sections_match_skeleton_and_lint():
    """Three-way drift pin (LIF-5): the headers write_memory re-creates must be byte-identical
    to the shipped skeleton's floor sections AND to the sections lint_floor treats as floor —
    otherwise a 'repaired' section could itself be flagged (or unparsed) downstream."""
    from memory import new_memory as NM

    assets = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugin", "assets"))
    with open(os.path.join(assets, "MEMORY.skeleton.md"), encoding="utf-8") as fh:
        skeleton_lines = {ln.strip() for ln in fh.read().split("\n")}
    for header in NM._FLOOR_SECTION_BY_TYPE.values():
        assert header in skeleton_lines
    assert {h[3:] for h in NM._FLOOR_SECTION_BY_TYPE.values()} == set(floor._FLOOR_SECTIONS)


# --------------------------------------------------------------------------- #
# TEA-4 — sorted floor-pointer insertion (kills tail-collision merge conflicts)
#
# DECISION (see the commit body for full rationale): sorted insertion, not generated
# floor sections. New pointers land at their deterministic lexicographic position among
# the section's EXISTING pointer lines instead of always appending at the section tail —
# the single highest-churn shared line every concurrent user/feedback write used to touch.
# --------------------------------------------------------------------------- #
def test_sorted_insertion_lands_before_first_greater_pointer(tmp_path):
    """AC #1 (deterministic insertion position): inserting into an ALREADY-sorted section
    lands the new pointer immediately before the first existing pointer whose name sorts
    greater — a mid-section insert, not a tail append — and the result is byte-pinned."""
    from memory import new_memory as NM

    md = str(tmp_path / "memory")
    _floor(
        md,
        "# Proj — Agent Memory Index (durable floor)\n"
        "## User\n"
        "- [Alpha](alpha_note.md) — a.\n"
        "- [Mango](mango_note.md) — m.\n"
        "- [Zulu](zulu_note.md) — z.\n"
        "## Working Style & Process Feedback\n"
        "## Recalled on demand\n",
    )
    res = NM._append_floor_pointer(md, "## User", "charlie_note", "Charlie", "c.")
    assert res == {"status": "appended", "reason": None}
    mem = open(os.path.join(md, "MEMORY.md"), encoding="utf-8").read()
    expected = (
        "# Proj — Agent Memory Index (durable floor)\n"
        "## User\n"
        "- [Alpha](alpha_note.md) — a.\n"
        "- [Charlie](charlie_note.md) — c.\n"
        "- [Mango](mango_note.md) — m.\n"
        "- [Zulu](zulu_note.md) — z.\n"
        "## Working Style & Process Feedback\n"
        "## Recalled on demand\n"
    )
    assert mem == expected

    # A name sorting BEFORE every existing pointer lands right after the header (first slot).
    res2 = NM._append_floor_pointer(md, "## User", "aardvark_note", "Aardvark", "aa.")
    assert res2 == {"status": "appended", "reason": None}
    mem2 = open(os.path.join(md, "MEMORY.md"), encoding="utf-8").read()
    assert mem2.index("(aardvark_note.md)") < mem2.index("(alpha_note.md)")
    assert mem2.split("## User\n", 1)[1].split("\n")[0] == "- [Aardvark](aardvark_note.md) — aa."


def test_sorted_insertion_on_unsorted_legacy_section_places_locally_no_bulk_resort(tmp_path):
    """AC #1 (legacy-section behavior, spec'd explicitly): an UNSORTED legacy section gets
    the new entry placed at its locally-correct spot — before the first pointer it scans
    past whose name sorts greater — WITHOUT reordering any pre-existing line (no bulk
    re-sort, per the no-bulk-autonomous-sweeps invariant)."""
    from memory import new_memory as NM

    md = str(tmp_path / "memory")
    # Deliberately NOT alphabetical: zulu, alpha, mango (hand-authored corpora drift like this).
    _floor(
        md,
        "# Proj — Agent Memory Index (durable floor)\n"
        "## User\n"
        "- [Zulu](zulu_note.md) — z.\n"
        "- [Alpha](alpha_note.md) — a.\n"
        "- [Mango](mango_note.md) — m.\n"
        "## Working Style & Process Feedback\n",
    )
    res = NM._append_floor_pointer(md, "## User", "bravo_note", "Bravo", "b.")
    assert res == {"status": "appended", "reason": None}
    mem = open(os.path.join(md, "MEMORY.md"), encoding="utf-8").read()
    # "bravo" sorts greater than nothing it hasn't already passed except "zulu" (the FIRST
    # pointer scanned that sorts greater than "bravo") -> lands before zulu, i.e. first slot.
    # alpha/mango — untouched, unmoved, in their original (still-unsorted) relative order.
    expected = (
        "# Proj — Agent Memory Index (durable floor)\n"
        "## User\n"
        "- [Bravo](bravo_note.md) — b.\n"
        "- [Zulu](zulu_note.md) — z.\n"
        "- [Alpha](alpha_note.md) — a.\n"
        "- [Mango](mango_note.md) — m.\n"
        "## Working Style & Process Feedback\n"
    )
    assert mem == expected


def test_sorted_section_stays_sorted_across_n_inserts(tmp_path):
    """AC #3: seeding an empty section and inserting N names in SHUFFLED (non-sorted) order,
    one write at a time (the real corpus-growth pattern), leaves the section fully sorted."""
    from memory import new_memory as NM

    md = str(tmp_path / "memory")
    _floor(
        md,
        "# Proj — Agent Memory Index (durable floor)\n"
        "## User\n"
        "## Working Style & Process Feedback\n"
        "## Recalled on demand\n",
    )
    names = [
        "mango_note", "aardvark_note", "zulu_note", "charlie_note", "bravo_note",
        "delta_note", "yankee_note", "echo_note", "quebec_note", "foxtrot_note",
    ]
    for n in names:
        res = NM._append_floor_pointer(md, "## User", n, NM._title_from_slug(n), "hook")
        assert res == {"status": "appended", "reason": None}

    mem = open(os.path.join(md, "MEMORY.md"), encoding="utf-8").read()
    section = mem.split("## User\n", 1)[1].split("## Working Style", 1)[0]
    lines = [ln for ln in section.split("\n") if ln.strip()]
    got_names = [NM._pointer_name(ln) for ln in lines]
    assert got_names == sorted(names)  # fully sorted, no gaps, nothing dropped/duplicated


def test_sorted_insertion_merges_cleanly_across_two_clones(tmp_path):
    """AC #2 — the item's whole point, proven with a REAL git merge: two independent clones
    of a fixture repo each add a DIFFERENT memory pointer to the SAME floor section (via the
    exact function ``write_memory`` calls in production), commit, and merge. Because sorted
    insertion places the two new lines at DIFFERENT positions (one before, one after the
    shared pre-existing pointer) rather than both appending to the section's tail line, git's
    three-way merge resolves with NO conflict and BOTH pointers survive."""
    import subprocess

    from memory import new_memory as NM
    from .conftest import git_commit

    def _run(args, cwd, check=True):
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=check)

    def _init_repo(path):
        os.makedirs(path, exist_ok=True)
        _run(["git", "init", "-q", "-b", "main", path], cwd=str(tmp_path))
        _run(["git", "config", "user.email", "tester@example.com"], cwd=path)
        _run(["git", "config", "user.name", "tester"], cwd=path)

    # Seed an origin repo with a real-shaped floor: ONE existing Working-Style pointer.
    origin_bare = str(tmp_path / "origin.git")
    _run(["git", "init", "-q", "--bare", "-b", "main", origin_bare], cwd=str(tmp_path))

    seed = str(tmp_path / "seed")
    _init_repo(seed)
    seed_md = os.path.join(seed, ".claude", "memory")
    _floor(seed_md, _CLEAN_FLOOR)
    git_commit(seed, "seed floor", 1_700_000_000)
    _run(["git", "remote", "add", "origin", origin_bare], cwd=seed)
    _run(["git", "push", "-q", "origin", "HEAD:main"], cwd=seed)

    # Two independent clones — teammate A and teammate B.
    clone_a = str(tmp_path / "clone_a")
    clone_b = str(tmp_path / "clone_b")
    _run(["git", "clone", "-q", origin_bare, clone_a], cwd=str(tmp_path))
    _run(["git", "clone", "-q", origin_bare, clone_b], cwd=str(tmp_path))
    for c in (clone_a, clone_b):
        _run(["git", "config", "user.email", "tester@example.com"], cwd=c)
        _run(["git", "config", "user.name", "tester"], cwd=c)

    # A adds "alpha_note" (sorts BEFORE the existing feedback_x) — B adds "zulu_note" (sorts
    # AFTER it) — different names, different sections positions, same section+file.
    md_a = os.path.join(clone_a, ".claude", "memory")
    res_a = NM._append_floor_pointer(
        md_a, "## Working Style & Process Feedback", "alpha_note", "Alpha Note", "hook a"
    )
    assert res_a == {"status": "appended", "reason": None}
    git_commit(clone_a, "add alpha_note floor pointer", 1_700_000_100)

    md_b = os.path.join(clone_b, ".claude", "memory")
    res_b = NM._append_floor_pointer(
        md_b, "## Working Style & Process Feedback", "zulu_note", "Zulu Note", "hook b"
    )
    assert res_b == {"status": "appended", "reason": None}
    git_commit(clone_b, "add zulu_note floor pointer", 1_700_000_200)

    # A pushes first; B fetches + merges (a real 3-way git merge, not a rebase).
    _run(["git", "push", "-q", "origin", "HEAD:main"], cwd=clone_a)
    _run(["git", "fetch", "-q", "origin"], cwd=clone_b)
    merge = _run(["git", "merge", "--no-edit", "origin/main"], cwd=clone_b, check=False)
    assert merge.returncode == 0, (
        f"expected a clean automatic merge, got a conflict:\nstdout={merge.stdout}\n"
        f"stderr={merge.stderr}"
    )
    assert "CONFLICT" not in merge.stdout
    assert not _run(["git", "diff", "--name-only", "--diff-filter=U"], cwd=clone_b).stdout.strip()

    merged = open(os.path.join(md_b, "MEMORY.md"), encoding="utf-8").read()
    assert "<<<<<<<" not in merged and "=======" not in merged and ">>>>>>>" not in merged
    assert "(alpha_note.md)" in merged
    assert "(zulu_note.md)" in merged
    assert "(feedback_x.md)" in merged  # the pre-existing pointer survives untouched

    section = merged.split("## Working Style & Process Feedback\n", 1)[1].split("## ", 1)[0]
    lines = [ln for ln in section.split("\n") if ln.strip()]
    assert [NM._pointer_name(ln) for ln in lines] == ["alpha_note", "feedback_x", "zulu_note"]


# --------------------------------------------------------------------------- #
# CAP-3 — write-time decisioning for captured candidates (check-FIRST dry run)
# --------------------------------------------------------------------------- #
def _corpus_files(md):
    return {f for f in os.listdir(md) if f.endswith(".md")}


def test_check_candidate_routes_duplicate_to_review(tmp_path, monkeypatch):
    """A captured candidate that near-duplicates an existing memory routes to 'review' with the
    twin named — and NO file is created (the check is a dry run; the drain decides)."""
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    _seed_dup_corpus(NM, md, tmp_path)
    before = _corpus_files(md)

    decision = NM.check_candidate(
        "deploy_pipeline_recap",
        "how the railway deploy pipeline builds and ships the app again",
        "project",
        body="re-captured months later.",
        memory_dir=md,
    )
    assert decision["route"] == "review"
    assert decision["note"] is None
    assert decision["neighbors"] and decision["neighbors"][0]["name"] == "railway_deploy_pipeline"
    # Dry run: nothing was written — the candidate did NOT become a file.
    assert _corpus_files(md) == before
    assert not os.path.exists(os.path.join(md, "deploy_pipeline_recap.md"))


def test_check_candidate_routes_novel_to_add(tmp_path, monkeypatch):
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    _seed_dup_corpus(NM, md, tmp_path)
    before = _corpus_files(md)

    decision = NM.check_candidate(
        "kubernetes_pod_autoscaler_tuning",
        "tuning the horizontal pod autoscaler cpu targets for latency",
        "project",
        memory_dir=md,
    )
    assert decision["route"] == "add"
    assert decision["neighbors"] == []
    assert _corpus_files(md) == before  # still writes nothing


def test_check_candidate_empty_corpus_notes_skip_and_defaults_to_add(tmp_path, monkeypatch):
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)  # floor only, no indexed memories
    decision = NM.check_candidate("first_ever", "the first captured fact", "project", memory_dir=md)
    assert decision["route"] == "add"
    assert decision["note"]  # legible reason the check couldn't score (no index)


def test_check_flag_cli_is_dry_run(tmp_path, monkeypatch, capsys):
    from memory import new_memory as NM

    md = _nm_env(tmp_path, monkeypatch)
    _seed_dup_corpus(NM, md, tmp_path)
    before = _corpus_files(md)
    rc = NM.main([
        "deploy_pipeline_recap",
        "how the railway deploy pipeline builds and ships the app again",
        "--type", "project", "--memory-dir", md, "--check",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "route   : review" in out
    assert "railway_deploy_pipeline" in out
    assert _corpus_files(md) == before  # --check never writes
    assert not os.path.exists(os.path.join(md, "deploy_pipeline_recap.md"))
