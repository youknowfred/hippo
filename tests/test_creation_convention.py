"""Tests for the creation-convention layer — lint_floor.py (floor guard) + new_memory.py.

Hermetic: every test builds a tmp memory dir with a real-shaped MEMORY.md; nothing touches
the real ~/.claude. new_memory tests disable dense + pin CLAUDE_PROJECT_DIR to tmp.
"""

from __future__ import annotations

import os

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
    assert res["floor_pointer_added"] is True
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
    assert res["floor_pointer_added"] is False
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
