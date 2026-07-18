"""CUR-1 + COR-20 — citation curation survives the machines.

Split out of ``test_provenance.py`` when the module-size ratchet fired (the pin is a
deliberate ceiling, not a suggestion). Two families, one theme:

- CUR-1 (owner-ratified 2026-07-18): re-derivation preserves a LIVE citation it cannot
  re-derive from the body — hand-curated entries die only with their file. End-to-end
  over backfill --refresh, reverify, and the MIG-1 rederive worklist/apply, plus the
  renderer's ℹ keep-line.
- COR-20: the legacy split-empty ``cited_paths:\n  []`` continuation line strips WITH
  its key instead of orphaning ``[]`` onto the neighbouring scalar (`type: feedback` ->
  `type: feedback []`), which the COR-9 guard refused into a permanent rederive block.
"""

from __future__ import annotations

import os
import subprocess

from memory import provenance as P
from memory.staleness import read_provenance

from .conftest import git_commit, write_file


def _reverify_one(memory_dir, repo, name):
    repo_files, basename_index = P.build_repo_file_index(repo)
    target = os.path.join(memory_dir, f"{name}.md")
    return P.reverify_file(target, repo, repo_files, basename_index)

# --------------------------------------------------------------------------- #
# COR-20 — a flow value on its own continuation line strips WITH its key
# --------------------------------------------------------------------------- #
_SPLIT_EMPTY = (
    "---\nname: m\nmetadata:\n  node_type: memory\n  type: feedback\n"
    '  cited_paths:\n    []\n  source_commit: "abc"\n---\nbody cites `.env.example`\n'
)


def test_strip_frontmatter_keys_consumes_a_flow_value_continuation_line():
    """AC (COR-20), the primitive: stripping a bare ``cited_paths:`` whose value is a
    ``[]`` on its own continuation line (the legacy split-empty form an older hippo
    emitted) must consume the ``[]`` too. The old walk consumed only ``- item`` lines, so
    the orphaned ``[]`` folded into the PRECEDING key's scalar — ``type: feedback``
    became ``type: feedback []`` — or broke the parse outright. The COR-9 guard caught
    the damage but, by refusing, permanently blocked rederive/reverify on every memory
    carrying the shape the writer's own past self produced."""
    stripped = P.strip_frontmatter_keys(_SPLIT_EMPTY, P._PROVENANCE_KEY_RE)
    fm = P.parse_frontmatter(stripped)
    assert fm, "stripped frontmatter must still parse"
    assert fm["metadata"]["type"] == "feedback"  # NOT 'feedback []'
    assert "cited_paths" not in fm.get("metadata", {}) and "cited_paths" not in fm
    assert "[]" not in stripped


def test_rederive_gains_a_citation_through_the_legacy_split_empty_shape(repo, memory_dir):
    """AC (COR-20) end-to-end on the reported repro: a memory in the split-empty shape,
    whose body cites one resolvable path, re-derives cleanly — adjacent scalar intact,
    no guard refusal."""
    write_file(repo, ".env.example", "X=1\n")
    git_commit(repo, "code", 1_700_000_000)
    target = write_file(repo, ".claude/memory/m.md", _SPLIT_EMPTY)
    git_commit(repo, "memory", 1_700_000_001)

    repo_files, basename_index = P.build_repo_file_index(repo)
    r = P.rederive_file(target, repo, repo_files, basename_index)
    assert r["error"] is None, f"guard refusal on the legacy shape: {r['error']}"
    assert r["changed"] is True and r["cited"] == [".env.example"]
    fm = P.parse_frontmatter(open(target, encoding="utf-8").read())
    assert fm["metadata"]["type"] == "feedback"  # the adjacent scalar survived intact
    assert fm["metadata"]["cited_paths"] == [".env.example"]


def test_value_run_end_rule():
    """The rule in one place: block items at the key's indent or deeper and any non-blank
    deeper-indented line are value; a sibling key, a dedent, or a blank line ends it."""
    fm = [
        "  cited_paths:",
        "    []",          # deeper flow value  -> consumed
        "    - a.py",      # block item deeper  -> consumed
        "  - b.py",        # block item AT key indent -> consumed (YAML allows)
        "  sibling: 1",    # sibling key -> stops
    ]
    assert P._value_run_end(fm, 1, 2) == 4
    assert P._value_run_end(["    []", "", "    []"], 0, 2) == 1  # blank line ends the run



# --------------------------------------------------------------------------- #
# CUR-1 — the renderer's keep-line
# --------------------------------------------------------------------------- #
def test_keep_line_renders_alone_when_nothing_dropped():
    """CUR-1: a preserved-only result gets exactly one ℹ line — informational, never ⚠."""
    res = {
        "cited": ["src/keep.py", "Dockerfile"],
        "dropped_citations": [],
        "preserved_not_derived": ["Dockerfile"],
    }
    (line,) = P.citation_rot_lines("m.md", res)
    assert "kept" in line and "Dockerfile" in line and "m.md" in line
    assert "⚠" not in line and "citation rot" not in line


def test_keep_line_rides_after_a_real_gone_drop():
    """CUR-1 × LIF-3: a drop and a keep in one result render as rot line THEN keep line —
    neither event hides the other."""
    res = {
        "cited": ["src/keep.py", "Dockerfile"],
        "dropped_citations": ["src/gone.py"],
        "dropped_gone": ["src/gone.py"],
        "dropped_not_derived": [],
        "preserved_not_derived": ["Dockerfile"],
    }
    rot, keep = P.citation_rot_lines("m.md", res)
    assert "citation rot" in rot and "src/gone.py" in rot
    assert "kept" in keep and "Dockerfile" in keep


def test_no_lines_when_nothing_dropped_and_nothing_preserved():
    assert P.citation_rot_lines("m.md", {"cited": ["a.py"], "dropped_citations": []}) == []



# --------------------------------------------------------------------------- #
# CUR-1 — preservation end-to-end (refresh / reverify / rederive)
# --------------------------------------------------------------------------- #

def test_refresh_preserves_a_live_not_derivable_citation_end_to_end(repo, memory_dir):
    """AC (CUR-1, owner-ratified 2026-07-18) on the live producer: a stored citation the
    extractor cannot derive from the body (`Dockerfile` — bare, unmarked prose) but whose
    file still EXISTS is PRESERVED, not dropped. This deliberately reverses LIF-4's old
    drop-and-report: the em-growth-labs corpus carried hand-curated citations
    (Dockerfile, .dockerignore) that every re-derivation clobbered — the exact paths a
    human had just restored. A citation now dies only with its file."""
    write_file(repo, "Dockerfile", "FROM scratch\n")
    write_file(repo, "src/keep.py", "k = 1\n")
    git_commit(repo, "code", 1_700_000_000)
    target = write_file(
        repo,
        ".claude/memory/m.md",
        '---\nname: M\ncited_paths: ["Dockerfile", "src/keep.py"]\nsource_commit: "abc"\n'
        "---\nbody cites src/keep.py and the Dockerfile\n",
    )
    git_commit(repo, "memory", 1_700_000_001)

    repo_files, basename_index = P.build_repo_file_index(repo)
    res = P.backfill_file(target, repo, repo_files, basename_index, refresh=True)
    assert res["error"] is None
    assert res["dropped_citations"] == [], "Dockerfile is alive — nothing may drop"
    assert res["preserved_not_derived"] == ["Dockerfile"]
    assert set(res["cited"]) == {"src/keep.py", "Dockerfile"}
    # The write really carries the preserved path (not just the result dict).
    cited_after, _ = read_provenance(open(target, encoding="utf-8").read())
    assert "Dockerfile" in cited_after
    # And the renderer says KEPT (informational), never rot.
    (line,) = P.citation_rot_lines("m.md", res)
    assert "kept" in line and "Dockerfile" in line
    assert "citation rot" not in line and "no longer in the repo" not in line


def test_refresh_preserves_a_citation_never_in_the_body_at_all(repo, memory_dir):
    """The `.dockerignore` case: a purely hand-added citation (the body never mentions the
    file in ANY form) survives re-derivation as long as the file exists."""
    write_file(repo, ".dockerignore", "node_modules\n")
    write_file(repo, "src/keep.py", "k = 1\n")
    git_commit(repo, "code", 1_700_000_000)
    target = write_file(
        repo,
        ".claude/memory/m.md",
        '---\nname: M\ncited_paths: [".dockerignore", "src/keep.py"]\nsource_commit: "abc"\n'
        "---\nbody cites src/keep.py only\n",
    )
    git_commit(repo, "memory", 1_700_000_001)

    repo_files, basename_index = P.build_repo_file_index(repo)
    res = P.backfill_file(target, repo, repo_files, basename_index, refresh=True)
    assert res["error"] is None
    assert res["preserved_not_derived"] == [".dockerignore"]
    assert set(res["cited"]) == {"src/keep.py", ".dockerignore"}


def test_preserved_citation_still_drops_when_its_file_is_gone(repo, memory_dir):
    """CUR-1's boundary: preservation is for LIVE files only. A curated citation whose
    file is deleted drops exactly as before — as `gone`, loudly."""
    write_file(repo, "Dockerfile", "FROM scratch\n")
    write_file(repo, "src/keep.py", "k = 1\n")
    git_commit(repo, "code", 1_700_000_000)
    target = write_file(
        repo,
        ".claude/memory/m.md",
        '---\nname: M\ncited_paths: ["Dockerfile", "src/keep.py"]\nsource_commit: "abc"\n'
        "---\nbody cites src/keep.py and the Dockerfile\n",
    )
    subprocess.run(["git", "rm", "-q", "Dockerfile"], cwd=repo, check=True, capture_output=True)
    git_commit(repo, "drop docker", 1_700_000_100)

    repo_files, basename_index = P.build_repo_file_index(repo)
    res = P.backfill_file(target, repo, repo_files, basename_index, refresh=True)
    assert res["error"] is None
    assert res["dropped_citations"] == ["Dockerfile"]
    assert res["dropped_gone"] == ["Dockerfile"]
    assert res["preserved_not_derived"] == []
    (line,) = P.citation_rot_lines("m.md", res)
    assert "no longer in the repo" in line


def test_reverify_preserves_live_not_derivable_citations_too(repo, memory_dir):
    """A human confirming CONTENT must not lose curated citations as a side effect."""
    write_file(repo, "Dockerfile", "FROM scratch\n")
    write_file(repo, "src/keep.py", "k = 1\n")
    git_commit(repo, "code", 1_700_000_000)
    write_file(
        repo,
        ".claude/memory/m.md",
        '---\nname: M\ncited_paths: ["Dockerfile", "src/keep.py"]\nsource_commit: "abc"\n'
        "---\nbody cites src/keep.py and the Dockerfile\n",
    )
    git_commit(repo, "memory", 1_700_000_001)

    res = _reverify_one(memory_dir, repo, "m")
    assert res["error"] is None
    assert res["dropped_citations"] == []
    assert res["preserved_not_derived"] == ["Dockerfile"]
    assert set(res["cited"]) == {"src/keep.py", "Dockerfile"}


def test_rederive_preserves_and_worklist_omits_a_preservation_only_memory(repo, memory_dir):
    """CUR-1 × MIG-1: a memory whose only difference under the current extractor is a
    kept-but-not-derivable citation does NOT appear on the worklist (nothing would
    change), so the derivation stamp can be EARNED on a curated corpus. A memory that
    also GAINS a path still appears — with the kept set attributed."""
    write_file(repo, "Dockerfile", "FROM scratch\n")
    write_file(repo, "src/keep.py", "k = 1\n")
    write_file(repo, "src/new.py", "n = 1\n")
    git_commit(repo, "code", 1_700_000_000)
    # stable: citations already = derived ∪ curated → no diff under preservation
    write_file(
        repo,
        ".claude/memory/stable.md",
        '---\nname: stable\ncited_paths: ["Dockerfile", "src/keep.py"]\nsource_commit: "abc"\n'
        "---\nbody cites src/keep.py and the Dockerfile\n",
    )
    # gains: body now cites src/new.py that frontmatter lacks; Dockerfile is curated
    write_file(
        repo,
        ".claude/memory/gains.md",
        '---\nname: gains\ncited_paths: ["Dockerfile"]\nsource_commit: "abc"\n'
        "---\nbody cites src/new.py and the Dockerfile\n",
    )
    git_commit(repo, "memories", 1_700_000_001)

    work = P.rederive_worklist(memory_dir, repo)
    names = [w["name"] for w in work]
    assert "stable" not in names, "preservation-only diff must not block the stamp"
    assert names == ["gains"]
    (w,) = work
    assert w["gained"] == ["src/new.py"]
    assert w["lost"] == []
    assert w["kept"] == ["Dockerfile"]

    # apply: the write preserves the curated path alongside the gain
    repo_files, basename_index = P.build_repo_file_index(repo)
    r = P.rederive_file(os.path.join(memory_dir, "gains.md"), repo, repo_files, basename_index)
    assert r["error"] is None and r["changed"] is True
    assert set(r["cited"]) == {"src/new.py", "Dockerfile"}
    assert r["preserved_not_derived"] == ["Dockerfile"]
    # and now the worklist is empty — the stamp is earnable
    assert P.rederive_worklist(memory_dir, repo) == []
