"""The GRO-1745 field round — COR-24 / ORC-4 / CUR-2 / MIG-2.

Found 2026-09-17 on the em-growth-labs corpus (579 memories) while a 419-memory MIG-1
re-derive was worked per item; every finding was reproduced against the shipped functions
before a line changed. One theme: a derivation that cannot be COMPLETED.

- COR-24: the COR-9 insert walk took its indent from the last indented KEY, which under a
  trailing nested mapping (hippo's own ``edge_origin:`` stamp) is the map's CHILD — the
  provenance triplet folded into the map, the damage guard refused, and the file became
  permanently un-writable. (The minimal repro sits beside COR-9's own block-list fixture
  in ``test_provenance.py``; the audit of every writer on the walk lives here.)
- ORC-4: ``resolve_citations`` resolved a DIRECTORY-QUALIFIED token by its basename
  alone — 11 of 21 such resolutions on the field corpus bound a memory to a file its claim
  was not about.
- CUR-2: a deliberate prune of a DERIVABLE path came straight back as a gain, so the
  worklist never emptied and ``--stamp-derivation`` refused forever. ``cited_paths_exclude``
  is the human-owned list every derivation honours and none writes.
- MIG-2: ``rederive`` said things that had not happened ("source_commit PRESERVED" on a
  memory that had none; "unparseable frontmatter" on a file with no frontmatter at all),
  and buried index drift between the worklist read and the write.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from memory import provenance as P

from .conftest import git_commit, write_file
from .test_provenance import _NESTED_MAP_TAIL


def _index(files):
    repo_files = set(files)
    basename_index = {}
    for f in files:
        basename_index.setdefault(f.rsplit("/", 1)[-1], []).append(f)
    return repo_files, basename_index


def _fm(path):
    with open(path, encoding="utf-8") as fh:
        return P.parse_frontmatter(fh.read())


def _memory(name, cited, body, *, extra=""):
    flow = "[" + ", ".join(f'"{c}"' for c in cited) + "]"
    return (
        f'---\nname: {name}\ndescription: "d"\nmetadata:\n  type: project\n'
        f"  cited_paths: {flow}\n{extra}"
        '  source_commit: "0000000000000000000000000000000000000000"\n'
        f"  source_commit_time: 1\n---\n{body}\n"
    )


# --------------------------------------------------------------------------- #
# COR-24 — every writer on the shared walk, against the nested-map tail
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("child_indent", ["  ", "    ", "\t"])
def test_insert_takes_the_first_level_indent_whatever_nests_below(child_indent):
    """The indent a new first-level key needs is the block's FIRST key's — a block
    mapping's first entry sets the indent every sibling shares — for any child indent."""
    i = child_indent
    fm = ["name: M", "metadata:", f"{i}type: project", f"{i}edge_origin:",
          f"{i}{i}a: dedup-review", f"{i}{i}deeper:", f"{i}{i}{i}x: 1"]
    out = P.insert_frontmatter_keys(fm, ["added: 1"])
    assert out[-1] == f"{i}added: 1" and out[:-1] == fm  # appended AFTER the nested value


def test_insert_ignores_a_comment_lines_indent():
    fm = ["metadata:", "      # a note, indented however the author liked", "  type: project"]
    assert P.insert_frontmatter_keys(fm, ["added: 1"])[-1] == "  added: 1"


def test_every_writer_on_the_shared_walk_survives_the_nested_map_tail(tmp_path):
    """The audit, as a test: every caller of the ONE insert walk (and the hand-copied
    relatives COR-9's docstring names) writes this shape without touching `edge_origin`."""
    from memory.dream_generate import _set_cited_paths, _set_confidence
    from memory.links import add_typed_relation, remove_typed_relation
    from memory.packs import _stamp_pack
    from memory.staleness import set_invalid_after

    # The nested map must be the block's LAST key for the writer under test — in the
    # shared fixture the provenance pair trails it (that is backfill's repro), so lift it.
    trailing = ('  source_commit: "0000000000000000000000000000000000000000"\n'
                "  source_commit_time: 1\n")
    tail = _NESTED_MAP_TAIL.replace(trailing, "").replace(
        "  edge_origin:\n", trailing + "  edge_origin:\n")
    assert tail.split("---\n")[1].endswith("    some-other-memory: dedup-review\n")

    def fresh(text=tail):
        path = tmp_path / "m.md"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def edge_origin(path):
        with open(path, encoding="utf-8") as fh:
            return P.parse_frontmatter(fh.read())["metadata"]["edge_origin"]

    want = {"some-other-memory": "dedup-review"}
    for write in (
        lambda p: set_invalid_after(p, "2026-09-17T00:00:00+00:00"),
        lambda p: add_typed_relation(p, "supersedes", "old-memory"),
        lambda p: _set_confidence(p, "draft"),
        lambda p: _set_cited_paths(p, ["src/other.py"]),
    ):
        path = fresh()
        res = write(path)
        assert res["error"] is None and res["changed"], res
        assert edge_origin(path) == want
    # remove_typed_relation's in-place rewrite, directly above the nested map
    path = fresh(tail.replace("  edge_origin:\n", "  refines: [a, b]\n  edge_origin:\n"))
    assert remove_typed_relation(path, "refines", "a")["changed"]
    assert edge_origin(path) == want
    for stamp in (P._stamp_last_verified(tail, "2026-09-17"),
                  P._stamp_verified_by(tail, "fred@2026-09-17"),
                  _stamp_pack(tail, "pk", "1.0.0")):
        assert P.parse_frontmatter(stamp)["metadata"]["edge_origin"] == want


def test_a_nested_maps_same_named_child_is_not_the_memorys_own_key(tmp_path):
    """COR-24, the strip side: `_PROVENANCE_KEY_RE` matches at ANY indent, so a nested
    map's child that merely shares an owned key's name was stripped as if it were the
    memory's provenance — hollowing out its parent and tripping the same permanent
    refusal. No reader resolves a key at that depth, so no writer may claim it."""
    text = _NESTED_MAP_TAIL.replace(
        "    some-other-memory: dedup-review\n",
        "    some-other-memory: dedup-review\n    source_commit: not-provenance\n",
    )
    stripped = P._strip_provenance(text)
    meta = P.parse_frontmatter(stripped)["metadata"]
    assert meta["edge_origin"]["source_commit"] == "not-provenance"
    assert "cited_paths" not in meta and "source_commit" not in meta
    lines = ["name: M", "pack_info:", "  cited_paths: [x]", "metadata:", "  cited_paths: [a]",
             "  nested:", "    cited_paths: [y]", "cited_paths: [top]"]
    assert P._own_scope_key_lines(lines, P._CITED_PATHS_KEY_RE) == [4, 7]
    assert P._has_cited_paths(["pack_info:", "  cited_paths: [x]"]) is False


# --------------------------------------------------------------------------- #
# ORC-4 — a directory-qualified token must suffix-match
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "token, tracked",
    [
        # another repo's file the memory is ABOUT -> this repo's only admin.py
        ("apps/api/routes/admin.py", "ingest/nightly_steps/admin.py"),
        # planned, never built -> an unrelated module of the same name
        ("ingest/ga4/audit.py", "ingest/impact/audit.py"),
        # an UNTRACKED handoff script -> a tracked skill script of the same name
        (".handoff/x/census.mjs", ".claude/skills/census/census.mjs"),
    ],
)
def test_a_directory_qualified_token_never_falls_back_to_a_foreign_basename(token, tracked):
    rf, bi = _index([tracked])
    assert P.resolve_citations([token], rf, bi) == []
    # ONE resolver: the receipt agrees with the derivation, by construction.
    assert P.unresolved_citations(f"see `{token}`", rf, bi) == [token]


def test_the_right_half_of_the_field_measurement_still_resolves():
    rf, bi = _index(["dash/lib/views-kit.js", "ads/write_exec.py", "x/write_exec.py",
                     "y/write_exec.py", "src/a.py"])
    # a relative import names a file by its TAIL
    assert P.resolve_citations(["../views-kit.js"], rf, bi) == ["dash/lib/views-kit.js"]
    assert P.resolve_citations(["../../lib/views-kit.js"], rf, bi) == ["dash/lib/views-kit.js"]
    # an AMBIGUOUS basename still drops, qualified or bare — suffix-disambiguation is a
    # separate, unshipped question (it quadrupled the field worklist when tried)
    assert P.resolve_citations(["ads/write_exec.py"], rf, bi) == ["ads/write_exec.py"]  # exact
    assert P.resolve_citations(["exec/../write_exec.py", "write_exec.py"], rf, bi) == []
    assert P.resolve_citations(["./src/a.py", "a.py"], rf, bi) == ["src/a.py"]  # ORC-1 intact


def test_a_relative_token_must_still_pin_exactly_one_file():
    rf, bi = _index(["x.py", "pkg/x.py"])
    # `../x.py` from SOME subdir: a top-level exact hit would be a guess — under-flag.
    assert P.resolve_citations(["../x.py"], rf, bi) == []
    assert P.resolve_citations(["x.py"], rf, bi) == ["x.py"]  # a real exact path still wins


def test_a_slash_joined_pair_is_the_accepted_miss():
    """Pinned honestly: `patterns.json/vocabulary.ts` is one token naming no path. v4
    resolved its last half by accident; v5 does not — the under-flag side of the trade."""
    rf, bi = _index(["kit/patterns.json", "kit/vocabulary.ts"])
    assert P.extract_citations("the pair patterns.json/vocabulary.ts") == [
        "patterns.json/vocabulary.ts"
    ]
    assert P.resolve_citations(["patterns.json/vocabulary.ts"], rf, bi) == []


def test_legacy_basename_repoints_names_exactly_what_v4_would_have_bound():
    rf, bi = _index(["ingest/nightly_steps/admin.py", "ads/write_exec.py", "src/a.py"])
    tokens = ["apps/api/routes/admin.py", "ads/write_exec.py", "a.py", "nope/missing.py"]
    assert P.legacy_basename_repoints(tokens, rf, bi) == {
        "ingest/nightly_steps/admin.py": "apps/api/routes/admin.py"
    }


def _repoint_repo(repo):
    write_file(repo, "ingest/nightly_steps/admin.py", "a = 1\n")
    write_file(repo, "src/keep.py", "k = 1\n")
    git_commit(repo, "code", 1_700_000_000)


def test_a_stored_v4_repoint_is_lost_with_its_cause_not_preserved_forever(repo, memory_dir):
    """The migration half. CUR-1 preserves any stored path whose file exists — so without
    this a v4 corpus would KEEP every wrong re-point silently (not derivable, file
    present) and could stamp v5 while still bound to the wrong files."""
    _repoint_repo(repo)
    target = write_file(
        repo, ".claude/memory/m.md",
        _memory("m", ["src/keep.py", "ingest/nightly_steps/admin.py"],
                "cites src/keep.py; the OTHER repo's `apps/api/routes/admin.py` does auth"),
    )
    git_commit(repo, "memory", 1_700_000_001)
    rf, bi = P.build_repo_file_index(repo)

    pv = P.rederive_preview(target, repo, rf, bi)
    assert pv["lost"] == ["ingest/nightly_steps/admin.py"] and pv["kept"] == []
    assert pv["repointed"] == {"ingest/nightly_steps/admin.py": "apps/api/routes/admin.py"}
    assert pv["unresolved"] == ["apps/api/routes/admin.py"]
    (work,) = P.rederive_worklist(memory_dir, repo)
    text = "\n".join(P.rederive_worklist_lines([work]))
    assert "- loses  : ingest/nightly_steps/admin.py" in text
    assert "bound by basename alone from `apps/api/routes/admin.py`" in text

    r = P.rederive_file(target, repo, rf, bi)
    assert r["error"] is None and r["cited"] == ["src/keep.py"]
    assert r["dropped_gone"] == [] and r["dropped_not_derived"] == []  # a NAMED cause
    (rot, *_rest) = P.citation_rot_lines("m.md", r)
    assert "ORC-4" in rot and "no longer in the repo" not in rot  # never a fake deletion
    assert _fm(target)["metadata"]["cited_paths"] == ["src/keep.py"]
    assert P.rederive_worklist(memory_dir, repo) == []


def test_the_hand_pruned_memory_leaves_the_worklist(repo, memory_dir):
    """The field state: 14 memories had the wrong path pruned BY HAND, and under v4 each
    came straight back as a `+ gains` forever. Under v5 there is nothing to gain."""
    _repoint_repo(repo)
    write_file(
        repo, ".claude/memory/m.md",
        _memory("m", ["src/keep.py"], "cites src/keep.py; see `apps/api/routes/admin.py`"),
    )
    git_commit(repo, "memory", 1_700_000_001)
    assert P.rederive_worklist(memory_dir, repo) == []


def test_a_repointed_path_the_body_ALSO_names_properly_stays(repo, memory_dir):
    _repoint_repo(repo)
    target = write_file(
        repo, ".claude/memory/m.md",
        _memory("m", ["ingest/nightly_steps/admin.py"],
                "`apps/api/routes/admin.py` there, ingest/nightly_steps/admin.py here"),
    )
    git_commit(repo, "memory", 1_700_000_001)
    rf, bi = P.build_repo_file_index(repo)
    pv = P.rederive_preview(target, repo, rf, bi)
    assert pv["changed"] is False and pv["repointed"] == {}


# --------------------------------------------------------------------------- #
# CUR-2 — cited_paths_exclude: a deliberate prune that HOLDS
# --------------------------------------------------------------------------- #
_GENERIC_BODY = "This is about the OTHER repo: its `plan.json` and src/keep.py here."


def _generic_repo(repo):
    write_file(repo, "tools/plan.json", "{}\n")
    write_file(repo, "src/keep.py", "k = 1\n")
    git_commit(repo, "code", 1_700_000_000)


def test_without_the_exclusion_a_hand_prune_can_never_be_stamped(repo, memory_dir):
    """The control — the defect itself. A bare generic basename unique in THIS repo, in a
    memory plainly about another one: pruned by hand, it returns as a gain every time."""
    _generic_repo(repo)
    write_file(repo, ".claude/memory/m.md", _memory("m", ["src/keep.py"], _GENERIC_BODY))
    git_commit(repo, "memory", 1_700_000_001)
    (work,) = P.rederive_worklist(memory_dir, repo)
    assert work["gained"] == ["tools/plan.json"]
    assert P.main(["--stamp-derivation", "--memory-dir", memory_dir, "--repo-root", repo]) == 1


def test_the_exclusion_makes_the_prune_stable_and_the_stamp_earnable(repo, memory_dir, capsys):
    _generic_repo(repo)
    target = write_file(
        repo, ".claude/memory/m.md",
        _memory("m", ["src/keep.py"], _GENERIC_BODY,
                extra="  cited_paths_exclude:\n    - tools/plan.json\n"),
    )
    git_commit(repo, "memory", 1_700_000_001)
    rf, bi = P.build_repo_file_index(repo)
    pv = P.rederive_preview(target, repo, rf, bi)
    assert pv["changed"] is False and pv["gained"] == []
    assert pv["excluded"] == ["tools/plan.json"]  # the receipt — visible, not silent
    assert P.rederive_worklist(memory_dir, repo) == []
    assert P.main(["--stamp-derivation", "--memory-dir", memory_dir, "--repo-root", repo]) == 0
    assert "earned" in capsys.readouterr().out
    assert P.read_cite_derivation(memory_dir) == P.CITATION_DERIVATION_VERSION


@pytest.mark.parametrize("verb", ["initial", "refresh", "rederive", "reverify"])
def test_every_derivation_honours_the_exclusion_and_none_writes_it(repo, memory_dir, verb):
    _generic_repo(repo)
    exclude = '  cited_paths_exclude: ["./tools/plan.json"]\n'  # `./` normalised like a token
    if verb == "initial":
        text = (f'---\nname: m\ndescription: "d"\nmetadata:\n  type: project\n{exclude}'
                f"---\n{_GENERIC_BODY}\n")
    else:  # the stored frontmatter still CARRIES the path the human is excluding
        text = _memory("m", ["src/keep.py", "tools/plan.json"], _GENERIC_BODY, extra=exclude)
    target = write_file(repo, ".claude/memory/m.md", text)
    git_commit(repo, "memory", 1_700_000_001)
    rf, bi = P.build_repo_file_index(repo)
    r = {
        "initial": lambda: P.backfill_file(target, repo, rf, bi),
        "refresh": lambda: P.backfill_file(target, repo, rf, bi, refresh=True),
        "rederive": lambda: P.rederive_file(target, repo, rf, bi),
        "reverify": lambda: P.reverify_file(target, repo, rf, bi),
    }[verb]()
    assert r["error"] is None, r["error"]
    assert r["cited"] == ["src/keep.py"] and r["excluded"] == ["tools/plan.json"]
    meta = _fm(target)["metadata"]
    assert meta["cited_paths"] == ["src/keep.py"]
    assert meta["cited_paths_exclude"] == ["./tools/plan.json"]  # byte-for-byte the human's
    if verb != "initial":
        # The stored path left — as a deliberate prune, never as rot.
        assert r["dropped_citations"] == ["tools/plan.json"]
        assert r["dropped_gone"] == [] and r["dropped_not_derived"] == []
        lines = P.citation_rot_lines("m.md", r)
        assert not any("⚠" in ln for ln in lines)
        assert any("ℹ excluded" in ln and "tools/plan.json" in ln for ln in lines)


def test_the_exclusion_reads_in_both_schemas_and_as_a_bare_string():
    assert P.frontmatter_excluded_paths({"cited_paths_exclude": ["a.py", "a.py", 3, " "]}) == ["a.py"]
    assert P.frontmatter_excluded_paths({"metadata": {"cited_paths_exclude": "b.py"}}) == ["b.py"]
    assert P.frontmatter_excluded_paths({"metadata": {"cited_paths_exclude": {"x": 1}}}) == []
    assert P.frontmatter_excluded_paths({}) == []


def test_the_damage_guard_treats_the_exclusion_as_a_key_no_writer_owns():
    """CUR-2 is human-owned: a rewrite that alters or drops it is DAMAGE for every writer."""
    before = _memory("m", ["a.py"], "b", extra="  cited_paths_exclude: [x.py]\n")
    for after in (before.replace("[x.py]", "[x.py, y.py]"),
                  before.replace("  cited_paths_exclude: [x.py]\n", "")):
        for owned in (P._PROVENANCE_OWNED, {"invalid_after"}, {"supersedes"}, {"confidence"}):
            assert "cited_paths_exclude" in P._frontmatter_damage(before, after, owned)
    # ...and the owned-key regexes never mistake it for `cited_paths` itself.
    assert P._strip_provenance(before).count("cited_paths_exclude: [x.py]") == 1
    assert "cited_paths_exclude" not in P._PROVENANCE_OWNED


def test_an_exclusion_that_empties_cited_paths_says_so(repo, memory_dir):
    _generic_repo(repo)
    target = write_file(
        repo, ".claude/memory/m.md",
        _memory("m", ["tools/plan.json"], "about the other repo's `plan.json`",
                extra="  cited_paths_exclude: [tools/plan.json]\n"),
    )
    git_commit(repo, "memory", 1_700_000_001)
    rf, bi = P.build_repo_file_index(repo)
    r = P.rederive_file(target, repo, rf, bi)
    (line,) = P.citation_rot_lines("m.md", r)
    assert "ℹ excluded" in line and "EXEMPT from staleness" in line


def test_the_keep_line_and_the_worklist_name_the_exclusion():
    res = {"cited": ["a.py", "Dockerfile"], "dropped_citations": [],
           "preserved_not_derived": ["Dockerfile"]}
    (keep,) = P.citation_rot_lines("m.md", res)
    assert "cited_paths_exclude" in keep  # the old advice only worked for NOT-derivable paths
    assert "cited_paths_exclude" in P.REDERIVE_EXCLUDE_HINT
    lines = P.rederive_worklist_lines([{
        "name": "m", "error": None, "gained": ["b.py"], "lost": [], "kept": [],
        "excluded": ["tools/plan.json"], "repointed": {}, "unresolved": []}])
    assert any("⊘ excludes: tools/plan.json" in ln for ln in lines)


def test_pack_extraction_strips_the_exclusion_with_the_citations(repo, memory_dir, tmp_path):
    """The list names THIS repo's paths — as project-local as the citations it prunes."""
    from memory import packs

    _generic_repo(repo)
    write_file(
        repo, ".claude/memory/m.md",
        _memory("m", ["src/keep.py"], "a portable lesson",
                extra="  cited_paths_exclude:\n    - tools/plan.json\n"),
    )
    git_commit(repo, "memory", 1_700_000_001)
    dest = str(tmp_path / "out-pack")
    res = packs.pack_extract(["m"], dest, memory_dir=memory_dir, repo_root=repo)
    assert not res.get("error") and not res.get("invalid"), res
    with open(os.path.join(dest, "m.md"), encoding="utf-8") as fh:
        shipped = fh.read()
    assert "cited_paths_exclude" not in shipped and "tools/plan.json" not in shipped
    assert P.parse_frontmatter(shipped)["metadata"]["type"] == "project"


# --------------------------------------------------------------------------- #
# MIG-2 — rederive says what happened
# --------------------------------------------------------------------------- #
def test_rederive_one_never_claims_a_preservation_that_did_not_happen(repo, memory_dir):
    write_file(repo, "src/keep.py", "k = 1\n")
    head = git_commit(repo, "code", 1_700_000_000)
    bare = write_file(repo, ".claude/memory/bare.md",
                      '---\nname: bare\ndescription: "d"\n---\ncites src/keep.py\n')
    partial = write_file(repo, ".claude/memory/partial.md",
                         '---\nname: partial\ndescription: "d"\ncited_paths: []\n---\n'
                         "cites src/keep.py\n")
    full = write_file(repo, ".claude/memory/full.md", _memory("full", [], "cites src/keep.py"))
    mem_commit = git_commit(repo, "memory", 1_700_000_001)
    assert head != mem_commit
    rf, bi = P.build_repo_file_index(repo)

    r = P.rederive_file(bare, repo, rf, bi)
    assert r["baseline"] == "initial" and r["changed"]
    text = "\n".join(P.rederive_one_lines("bare.md", r))
    assert "PRESERVED" not in text and "NO provenance" in text and mem_commit[:9] in text

    r = P.rederive_file(partial, repo, rf, bi)
    assert r["baseline"] == "assigned"
    text = "\n".join(P.rederive_one_lines("partial.md", r))
    assert "PRESERVED" not in text and "NO source_commit" in text

    r = P.rederive_file(full, repo, rf, bi)
    assert r["baseline"] == "preserved"
    text = "\n".join(P.rederive_one_lines("full.md", r))
    assert "source_commit PRESERVED" in text and "folded into the consent baseline" in text
    assert _fm(full)["metadata"]["source_commit"] == "0" * 40


def test_index_drift_between_the_worklist_read_and_the_write_is_loud(repo, memory_dir):
    """A sibling session stages a file between the operator's worklist read and the write:
    what gets written is no longer the diff that was reviewed. `gained:` is computed at
    call time against the STORED frontmatter, so the extra path is named, not buried."""
    write_file(repo, "src/keep.py", "k = 1\n")
    write_file(repo, "src/first.py", "f = 1\n")
    git_commit(repo, "code", 1_700_000_000)
    target = write_file(repo, ".claude/memory/m.md",
                        _memory("m", ["src/keep.py"],
                                "cites src/keep.py, src/first.py and src/late.py"))
    git_commit(repo, "memory", 1_700_000_001)
    (reviewed,) = P.rederive_worklist(memory_dir, repo)
    assert reviewed["gained"] == ["src/first.py"]  # what the operator approved

    write_file(repo, "src/late.py", "l = 1\n")  # ...then a sibling stages this
    subprocess.run(["git", "add", "src/late.py"], cwd=repo, check=True)
    rf, bi = P.build_repo_file_index(repo)
    r = P.rederive_file(target, repo, rf, bi)
    assert r["gained"] == ["src/first.py", "src/late.py"] and r["lost"] == []
    gained_line = P.rederive_one_lines("m.md", r)[1]
    assert "gained: src/first.py, src/late.py" in gained_line and "lost: (none)" in gained_line
    assert "index moved since your worklist read" in gained_line


def test_no_frontmatter_is_not_reported_as_a_yaml_error(repo, memory_dir):
    write_file(repo, "src/keep.py", "k = 1\n")
    git_commit(repo, "code", 1_700_000_000)
    write_file(repo, ".claude/memory/plain.md", "# just notes\n\ncites src/keep.py\n")
    write_file(repo, ".claude/memory/broken.md",
               "---\nname: broken\ndescription: a: b: c\n---\ncites src/keep.py\n")
    git_commit(repo, "memory", 1_700_000_001)
    errors = {w["name"]: w["error"] for w in P.rederive_worklist(memory_dir, repo)}
    assert errors["plain"].startswith("no frontmatter") and "YAML" not in errors["plain"]
    assert errors["broken"].startswith("unparseable frontmatter")


def test_the_mcp_tool_and_the_cli_share_the_one_rendering(repo, memory_dir, monkeypatch, capsys):
    import memory.mcp_tools_consolidate as T

    write_file(repo, "src/keep.py", "k = 1\n")
    git_commit(repo, "code", 1_700_000_000)
    write_file(repo, ".claude/memory/bare.md",
               '---\nname: bare\ndescription: "d"\n---\ncites src/keep.py\n')
    git_commit(repo, "memory", 1_700_000_001)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", memory_dir)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    dry = T._tool_rederive({"action": "one", "name": "bare", "dry_run": True})
    assert "would re-derive bare.md" in dry and "gained: src/keep.py" in dry
    assert P.main(["--rederive-one", "bare", "--dry-run",
                   "--memory-dir", memory_dir, "--repo-root", repo]) == 0
    assert capsys.readouterr().out.strip() == dry.strip()
    work = T._tool_rederive({"action": "worklist"})
    assert "cited_paths_exclude" in work  # the hint rides the worklist on both surfaces
