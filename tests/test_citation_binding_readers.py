"""FMT-2 slice 2: readers prefer the index's citation binding; writers still write frontmatter.

The accessor (``citation_binding.Bound.cited``) serves a memory's sidecar paths only when
the sidecar is current AND its row was derived from this exact text; otherwise the
frontmatter's. ``HIPPO_INDEX_BINDING=0`` is frontmatter only, exactly the prior release.
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from .conftest import git_commit, write_file
from memory import build_index as BI
from memory import citation_binding as CB
from memory import staleness as S

WINDOW = "40 years ago"
T0 = 1_700_000_000


def _mem(memory_dir, name, body, *, cited, sc):
    text = (f'---\nname: {name}\ndescription: "{name}"\nmetadata:\n  type: project\n'
            f'  cited_paths: {json.dumps(cited)}\n  source_commit: "{sc}"\n---\n{body}\n')
    return write_file(memory_dir, f"{name}.md", text)


def _tree(root):
    out = {}
    for d, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            out[p] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return out


@pytest.fixture
def drifted(repo, memory_dir, monkeypatch):
    """`moved` names b.py in its body but its (older) frontmatter cites a.py; a.py then
    changes. `fresh` names a.py in its body with an EMPTY frontmatter list."""
    monkeypatch.setenv("HIPPO_DISABLE", "dense")
    write_file(repo, "a.py", "x = 1\n")
    write_file(repo, "b.py", "y = 1\n")
    base = git_commit(repo, "seed", T0)
    _mem(memory_dir, "moved", "The handler lives in `b.py` now.", cited=["a.py"], sc=base)
    _mem(memory_dir, "fresh", "Watch `a.py`.", cited=[], sc=base)
    git_commit(repo, "memories", T0 + 10)
    write_file(repo, "a.py", "x = 2\n")
    git_commit(repo, "drift", T0 + 100)
    return repo, memory_dir, os.path.join(repo, ".hippo", "index")


def _stale(md, repo):
    return sorted(d["name"] for d in S.find_stale(md, repo, since=WINDOW))


def test_without_a_sidecar_staleness_reads_frontmatter(drifted):
    repo, md, _idx = drifted
    assert _stale(md, repo) == ["moved"]


def test_staleness_prefers_the_current_index_binding(drifted):
    repo, md, idx = drifted
    BI.build_index(md, idx)
    assert _stale(md, repo) == ["fresh"]


def test_the_kill_switch_is_frontmatter_only_and_writes_no_sidecar(drifted, monkeypatch):
    repo, md, idx = drifted
    monkeypatch.setenv("HIPPO_INDEX_BINDING", "0")
    BI.build_index(md, idx)
    assert not os.path.exists(CB.binding_path(idx))
    assert _stale(md, repo) == ["moved"]
    monkeypatch.delenv("HIPPO_INDEX_BINDING")
    BI.build_index(md, idx)
    monkeypatch.setenv("HIPPO_INDEX_BINDING", "0")
    assert _stale(md, repo) == ["moved"]  # a sidecar on disk is ignored too


def test_a_text_edited_since_the_refresh_falls_back_to_frontmatter(drifted):
    repo, md, idx = drifted
    BI.build_index(md, idx)
    with open(os.path.join(md, "moved.md"), "a", encoding="utf-8") as fh:
        fh.write("An edit the index has not seen.\n")
    assert _stale(md, repo) == ["fresh", "moved"]


def test_readers_write_nothing_to_the_corpus(drifted):
    repo, md, idx = drifted
    BI.build_index(md, idx)
    before = _tree(md)
    S.find_stale(md, repo, since=WINDOW)
    from memory.jit import build_touch_cache
    from memory.recall_diff import memories_for_paths

    build_touch_cache(md)
    memories_for_paths(["a.py"], md)
    assert _tree(md) == before


def test_touchmap_for_diff_and_relevant_to_work_use_the_binding(drifted):
    repo, md, idx = drifted
    BI.build_index(md, idx)
    from memory.jit import build_touch_cache
    from memory.recall_diff import memories_for_paths
    from memory.session_start_signals import _corpus_cited_union

    cache = build_touch_cache(md)
    assert cache["cited"] == {"a.py": ["fresh"], "b.py": ["moved"]}
    assert [r["name"] for r in memories_for_paths(["a.py"], md)] == ["fresh"]
    assert _corpus_cited_union(md) == {"a.py", "b.py"}


def test_outcome_join_uses_the_binding(drifted):
    _repo, md, idx = drifted
    BI.build_index(md, idx)
    from memory.outcome import _cited_paths_of

    cache = {}
    assert _cited_paths_of(md, "moved", cache) == {"b.py"}
    assert _cited_paths_of(md, "fresh", cache) == {"a.py"}


# --------------------------------------------------------------------------- #
# The derivation worklist, the nudge and doctor
# --------------------------------------------------------------------------- #
@pytest.fixture
def behind(drifted):
    repo, md, idx = drifted
    write_file(md, ".format", json.dumps({"corpus_format": 5, "cite_derivation": 4}))
    return repo, md, idx


def test_tend_derivation_stops_listing_index_bound_memories(behind):
    from memory.tend_queue import _src_derivation

    repo, md, idx = behind
    listed = sorted(e["id"] for e in _src_derivation(md, repo))
    assert len(listed) == 2 and all("moved" in i or "fresh" in i for i in listed)
    BI.build_index(md, idx)
    assert _src_derivation(md, repo) == []  # and no stamp entry: the stamp would be refused
    with open(os.path.join(md, "moved.md"), "a", encoding="utf-8") as fh:
        fh.write("Edited after the refresh.\n")
    ids = [e["id"] for e in _src_derivation(md, repo)]
    assert len(ids) == 1 and "moved" in ids[0]  # its row no longer matches its text


def test_the_nudge_and_doctor_go_quiet_when_every_memory_is_index_bound(behind):
    from memory.doctor_checks_corpus import check_format_version
    from memory.doctor_checks_env import DoctorContext
    from memory.session_start_health import cite_derivation_producer

    repo, md, idx = behind
    ctx = DoctorContext(memory_dir=md, repo_root=repo)
    assert cite_derivation_producer(md, repo) is not None
    assert check_format_version(ctx)["status"] == "warn"
    BI.build_index(md, idx)
    assert cite_derivation_producer(md, repo) is None
    r = check_format_version(ctx)
    assert "the index derives v6 for every memory" in r["message"]
    assert "Work it through the maintenance queue" not in r["message"]


def test_the_rederive_verb_and_stamp_still_see_frontmatter(behind):
    """Writers are unchanged: the explicit rederive worklist still lists both (it is the
    verb that brings frontmatter current), so the stamp stays earned, not claimed."""
    from memory.provenance import rederive_worklist

    repo, md, idx = behind
    BI.build_index(md, idx)
    assert sorted(w["name"] for w in rederive_worklist(md, repo)) == ["fresh", "moved"]


# --------------------------------------------------------------------------- #
# The review packet
# --------------------------------------------------------------------------- #
def test_review_lines_render_the_derived_cites(drifted):
    repo, md, _idx = drifted
    texts = {n: open(os.path.join(md, f"{n}.md"), encoding="utf-8").read() for n in ("moved", "fresh")}
    lines = CB.review_lines(texts, repo)
    assert lines[0].startswith("derived citations")
    assert "- `fresh`: a.py (frontmatter lacks a.py)" in lines
    assert "- `moved`: b.py (frontmatter lacks b.py; frontmatter also lists a.py)" in lines
    head = CB.review_lines(texts, repo, ref="HEAD")
    assert head == lines


def test_review_packet_carries_the_section(drifted, monkeypatch):
    from memory import review

    repo, md, _idx = drifted
    with open(os.path.join(md, "moved.md"), "a", encoding="utf-8") as fh:
        fh.write("Also see `a.py`.\n")
    monkeypatch.setenv("CI", "1")
    _code, packet = review.run([], memory_dir=md, repo_root=repo)
    assert "derived citations (what each touched memory binds to, from its body):" in packet
    assert "- `moved`: b.py, a.py" in packet
