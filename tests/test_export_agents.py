"""RUL-7 — /hippo:export-agents: the floor rendered as a PROPOSED AGENTS.md diff.

Hermetic: a real scratch git repo + corpus floor; BM25-only. Pins both acceptance
criteria — (1) one command emits a reviewable AGENTS.md diff from the floor and NEVER
writes (the module has zero write paths; apply is the skill's separate, explicitly
approved step), (2) the exported file is drift-checked (a cited path that moves flags
loud through the widened rules_rot / archive scan surfaces) — plus the RUL-6-shared
derivation contract: conservative collapse, the over-scoping cap, literals as the
fallback, ``**`` never emitted.
"""

from __future__ import annotations

import os
import subprocess

from memory import archive as A
from memory.export_agents import (
    BLOCK_BEGIN,
    BLOCK_END,
    describe,
    export_agents,
)
from memory.rules_plane import derive_paths_globs, rules_rot

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


# --------------------------------------------------------------------------- #
# derive_paths_globs — the RUL-6/RUL-7 shared derivation
# --------------------------------------------------------------------------- #
def test_derive_single_citation_stays_literal():
    universe = {"src/a.py", "src/b.py", "src/c.py"}
    globs, flags = derive_paths_globs(["src/a.py"], universe)
    assert globs == ["src/a.py"]
    assert flags == []


def test_derive_same_dir_same_ext_collapses_within_cap():
    universe = {"src/a.py", "src/b.py", "src/notes.md"}
    globs, flags = derive_paths_globs(["src/a.py", "src/b.py"], universe)
    assert globs == ["src/*.py"]
    assert flags == []


def test_derive_over_scope_falls_back_to_literals():
    # 2 cited among 10 same-dir .py files: 10 > 3x2 — the collapse would over-scope.
    universe = {f"src/f{i}.py" for i in range(10)}
    globs, flags = derive_paths_globs(["src/f0.py", "src/f1.py"], universe)
    assert globs == ["src/f0.py", "src/f1.py"]
    assert [f["kind"] for f in flags] == ["over_scope"]
    assert flags[0]["glob"] == "src/*.py"
    assert flags[0]["matched"] == 10 and flags[0]["cited"] == 2


def test_derive_missing_path_flagged_and_excluded():
    universe = {"src/a.py"}
    globs, flags = derive_paths_globs(["src/a.py", "src/gone.py"], universe)
    assert globs == ["src/a.py"]
    assert flags == [{"kind": "missing", "path": "src/gone.py"}]


def test_derive_no_oracle_returns_unvalidated_literals():
    globs, flags = derive_paths_globs(["src/a.py"], set())
    assert globs == ["src/a.py"]
    assert flags == [{"kind": "no_oracle"}]


def test_derive_never_emits_recursive_glob_or_crosses_directories():
    # Same extension across DIFFERENT directories must not merge into a ** glob.
    universe = {"a/x.py", "b/y.py", "a/z.py"}
    globs, _flags = derive_paths_globs(["a/x.py", "b/y.py", "a/z.py"], universe)
    assert all("**" not in g for g in globs)
    assert "a/*.py" in globs and "b/y.py" in globs


# --------------------------------------------------------------------------- #
# export_agents — fixture
# --------------------------------------------------------------------------- #
def _repo(tmp_path, monkeypatch):
    """A committed scratch repo with code files + a corpus floor."""
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo = str(tmp_path / "repo")
    os.makedirs(os.path.join(repo, "src"))
    for rel in ("src/a.py", "src/b.py", "src/c.py", "src/solo.py", "README.md"):
        with open(os.path.join(repo, rel), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "seed"], check=True, env=_GIT_ENV)
    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)
    return repo, md


def _write_memory(md: str, name: str, body: str, *, cited=None, extra_meta: str = ""):
    cited_line = ""
    if cited is not None:
        quoted = ", ".join(f'"{c}"' for c in cited)
        cited_line = f"  cited_paths: [{quoted}]\n  source_commit: abc123\n"
    with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write(
            f"---\nname: {name}\ndescription: {name} description\nmetadata:\n"
            f"  type: feedback\n{cited_line}{extra_meta}---\n\n{body}\n"
        )


def _write_floor(md: str, names):
    lines = ["# Memory", "", "## User"]
    lines += [f"- [{n}]({n}.md) — hook" for n in names[:1]]
    lines += ["", "## Working Style & Process Feedback"]
    lines += [f"- [{n}]({n}.md) — hook" for n in names[1:]]
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _agents_path(repo: str) -> str:
    return os.path.join(repo, "AGENTS.md")


def _apply(repo: str, result: dict):
    with open(_agents_path(repo), "w", encoding="utf-8") as fh:
        fh.write(result["proposed"])


# --------------------------------------------------------------------------- #
# criterion 1: a reviewable diff from the floor; never an authoritative overwrite
# --------------------------------------------------------------------------- #
def test_refuses_on_empty_floor(tmp_path, monkeypatch):
    repo, md = _repo(tmp_path, monkeypatch)
    _write_floor(md, [])
    r = export_agents(memory_dir=md, repo_root=repo)
    assert r["proposed"] is None
    assert "floor pins no memories" in r["reason"]
    assert "refused" in describe(r)


def test_basic_proposal_shape_and_never_writes(tmp_path, monkeypatch):
    repo, md = _repo(tmp_path, monkeypatch)
    _write_memory(md, "lint-first", "Run lint. See [[style-guide|the guide]].",
                  cited=["src/a.py", "src/b.py", "src/c.py"])
    _write_memory(md, "terse-commits", "Prefer terse commit messages.")
    _write_floor(md, ["terse-commits", "lint-first"])

    r = export_agents(memory_dir=md, repo_root=repo)
    assert r["proposed"] and r["changed"] and not r["exists"]
    # frontmatter: JSON-quoted derived glob union
    assert '  - "src/*.py"' in r["proposed"]
    assert r["paths_globs"] == ["src/*.py"]
    # managed block + one section per floor memory, heading = backtick stem
    assert BLOCK_BEGIN in r["proposed"] and BLOCK_END in r["proposed"]
    assert "## `lint-first`" in r["proposed"] and "## `terse-commits`" in r["proposed"]
    assert "Applies to: `src/*.py`" in r["proposed"]
    # wikilinks rewritten to backtick stems; unscoped memory carries no Applies-to
    assert "`style-guide`" in r["proposed"] and "[[" not in r["proposed"]
    sect = r["proposed"].split("## `terse-commits`")[1]
    assert "Applies to:" not in sect
    # a NEW file diffs from /dev/null
    assert r["diff"].startswith("--- /dev/null")
    # THE criterion: the module wrote nothing
    assert not os.path.exists(_agents_path(repo))
    # and the render is deterministic
    assert export_agents(memory_dir=md, repo_root=repo)["proposed"] == r["proposed"]


def test_reexport_after_apply_is_idempotent(tmp_path, monkeypatch):
    repo, md = _repo(tmp_path, monkeypatch)
    _write_memory(md, "lint-first", "Run lint.", cited=["src/a.py", "src/b.py"])
    _write_floor(md, ["lint-first"])
    _apply(repo, export_agents(memory_dir=md, repo_root=repo))
    r2 = export_agents(memory_dir=md, repo_root=repo)
    assert r2["changed"] is False and r2["diff"] == "" and r2["exists"] is True


def test_hand_content_outside_markers_survives(tmp_path, monkeypatch):
    repo, md = _repo(tmp_path, monkeypatch)
    _write_memory(md, "lint-first", "Run lint.", cited=["src/a.py"])
    _write_floor(md, ["lint-first"])
    hand = "# Our agents guide\n\nHand-maintained intro.\n"
    with open(_agents_path(repo), "w", encoding="utf-8") as fh:
        fh.write(hand)
    r = export_agents(memory_dir=md, repo_root=repo)
    # hand text precedes the appended managed block, byte-verbatim
    assert r["proposed"].split(BLOCK_BEGIN)[0].endswith("Hand-maintained intro.\n\n")
    assert r["diff"].startswith("--- AGENTS.md")
    # an afterword BEYOND the managed block also survives a re-export
    _apply(repo, r)
    with open(_agents_path(repo), "a", encoding="utf-8") as fh:
        fh.write("\nHand afterword.\n")
    r2 = export_agents(memory_dir=md, repo_root=repo)
    assert "Hand afterword." in r2["proposed"] and r2["changed"] is False


def test_foreign_frontmatter_preserved_verbatim(tmp_path, monkeypatch):
    repo, md = _repo(tmp_path, monkeypatch)
    _write_memory(md, "lint-first", "Run lint.", cited=["src/a.py", "src/b.py"])
    _write_floor(md, ["lint-first"])
    foreign = "---\nowner: platform-team \ncustom: true\n---\nBody kept.\n"
    with open(_agents_path(repo), "w", encoding="utf-8") as fh:
        fh.write(foreign)
    r = export_agents(memory_dir=md, repo_root=repo)
    assert r["frontmatter_preserved"] is True
    assert r["proposed"].startswith("---\nowner: platform-team \ncustom: true\n---\n")
    assert "hippo:agents-export — derived" not in r["proposed"]
    assert r["paths_globs"] == []  # ours NOT emitted
    assert "foreign frontmatter preserved" in describe(r)


def test_corrupt_managed_block_refuses(tmp_path, monkeypatch):
    repo, md = _repo(tmp_path, monkeypatch)
    _write_memory(md, "lint-first", "Run lint.", cited=["src/a.py"])
    _write_floor(md, ["lint-first"])
    with open(_agents_path(repo), "w", encoding="utf-8") as fh:
        fh.write(f"intro\n{BLOCK_BEGIN}\nstale\n")  # begin, no end
    r = export_agents(memory_dir=md, repo_root=repo)
    assert r["proposed"] is None and "corrupt managed block" in r["reason"]


def test_retired_and_unreadable_floor_memories_are_skipped(tmp_path, monkeypatch):
    repo, md = _repo(tmp_path, monkeypatch)
    _write_memory(md, "old-rule", "Old.", cited=["src/a.py"],
                  extra_meta="  invalid_after: 2020-01-01\n")
    _write_memory(md, "live-rule", "Live.")
    _write_floor(md, ["live-rule", "old-rule", "ghost-pointer"])
    r = export_agents(memory_dir=md, repo_root=repo)
    reasons = {s["name"]: s["reason"] for s in r["skipped"]}
    assert "retired" in reasons["old-rule"]
    assert "without a readable file" in reasons["ghost-pointer"]
    assert "## `old-rule`" not in r["proposed"] and "## `live-rule`" in r["proposed"]


def test_all_floor_memories_skipped_refuses(tmp_path, monkeypatch):
    repo, md = _repo(tmp_path, monkeypatch)
    _write_memory(md, "old-rule", "Old.", extra_meta="  invalid_after: 2020-01-01\n")
    _write_floor(md, ["old-rule"])
    r = export_agents(memory_dir=md, repo_root=repo)
    assert r["proposed"] is None and "no exportable floor memory" in r["reason"]


def test_over_scope_surfaces_in_result_and_describe(tmp_path, monkeypatch):
    repo, md = _repo(tmp_path, monkeypatch)
    # 10 extra .py files make src/ dense enough that citing 2 would over-scope
    for i in range(10):
        with open(os.path.join(repo, "src", f"extra{i}.py"), "w", encoding="utf-8") as fh:
            fh.write("y = 1\n")
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "more"], check=True, env=_GIT_ENV)
    _write_memory(md, "lint-first", "Run lint.", cited=["src/a.py", "src/b.py"])
    _write_floor(md, ["lint-first"])
    r = export_agents(memory_dir=md, repo_root=repo)
    item = r["items"][0]
    assert item["globs"] == ["src/a.py", "src/b.py"]
    assert any(f["kind"] == "over_scope" for f in item["flags"])
    assert "over-scope" in describe(r)


# --------------------------------------------------------------------------- #
# criterion 2: the exported file is drift-checked (cited_paths moved -> loud flag)
# --------------------------------------------------------------------------- #
def test_dead_frontmatter_glob_flags_after_cited_files_leave(tmp_path, monkeypatch):
    repo, md = _repo(tmp_path, monkeypatch)
    _write_memory(md, "lint-first", "Run lint.", cited=["src/a.py", "src/b.py", "src/c.py"])
    _write_floor(md, ["lint-first"])
    _apply(repo, export_agents(memory_dir=md, repo_root=repo))
    assert rules_rot(repo)["dead_path_globs"] == []  # alive at export time
    subprocess.run(
        ["git", "-C", repo, "rm", "-q", "src/a.py", "src/b.py", "src/c.py", "src/solo.py"],
        check=True,
    )
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "rm"], check=True, env=_GIT_ENV)
    dead = rules_rot(repo)["dead_path_globs"]
    assert {"file": "AGENTS.md", "glob": "src/*.py"} in dead


def test_moved_literal_ref_flags_via_code_ref_rot(tmp_path, monkeypatch):
    repo, md = _repo(tmp_path, monkeypatch)
    # a single citation stays a literal path — the exact per-file drift signal
    _write_memory(md, "solo-rule", "Keep solo tidy.", cited=["src/solo.py"])
    _write_floor(md, ["solo-rule"])
    _apply(repo, export_agents(memory_dir=md, repo_root=repo))
    assert rules_rot(repo)["code_ref_rot"] == []
    subprocess.run(["git", "-C", repo, "mv", "src/solo.py", "src/renamed.py"], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "mv"], check=True, env=_GIT_ENV)
    rot = rules_rot(repo)["code_ref_rot"]
    assert {"file": "AGENTS.md", "ref": "src/solo.py", "kind": "path"} in rot


def test_agents_md_citations_archive_protect_exported_memories(tmp_path, monkeypatch):
    repo, md = _repo(tmp_path, monkeypatch)
    _write_memory(md, "lint-first", "Run lint.", cited=["src/a.py"])
    _write_floor(md, ["lint-first"])
    _apply(repo, export_agents(memory_dir=md, repo_root=repo))
    assert "AGENTS.md" in A._SCAN_TARGETS
    cited = A._cited_by_claude_md_names(repo, {"lint-first", "uncited-memory"})
    assert "lint-first" in cited and "uncited-memory" not in cited
