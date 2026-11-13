"""HOT-15 (c): the two per-process memos on recall's hot path.

A long-lived process (the warm recall server, the MCP server) used to redo, on every prompt,
the drift check's frontmatter parse of up to 200 unchanged files and the links.json parse
plus edge build. Each is now kept under the file's own stat signature, so an unchanged file
costs a stat and an edited one is re-read exactly as before. Fixtures written from scratch.
"""

from __future__ import annotations

import json
import os

import pytest

from memory import links as L
from memory import recall_graph as RG


@pytest.fixture(autouse=True)
def _fresh():
    RG._DRIFT_MEMO.clear()
    RG._EDGES_MEMO.clear()
    yield
    RG._DRIFT_MEMO.clear()
    RG._EDGES_MEMO.clear()


def _write(path, desc):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f'---\nname: alpha\ndescription: "{desc}"\nmetadata:\n  type: project\n---\nbody\n')


def _entry(md, desc):
    from memory.build_index import _hash, memory_doc_text

    text = open(os.path.join(md, "alpha.md"), encoding="utf-8").read()
    return {"name": "alpha", "file": "alpha.md", "hash": _hash(memory_doc_text("alpha", text)),
            "description": desc, "tokens": []}


def _count_parses(monkeypatch):
    calls = []
    real = RG.memory_doc_text

    def spy(name, text):
        calls.append(name)
        return real(name, text)

    monkeypatch.setattr(RG, "memory_doc_text", spy)
    return calls


def test_an_unchanged_file_is_parsed_once_per_process(tmp_path, monkeypatch):
    md = str(tmp_path)
    _write(os.path.join(md, "alpha.md"), "Cadence of the garden watering")
    e = _entry(md, "Cadence of the garden watering")
    calls = _count_parses(monkeypatch)
    assert RG._drift_patch(e, md) is e
    assert RG._drift_patch(e, md) is e
    assert calls == ["alpha"]


def test_an_edit_is_seen_even_with_the_old_size_and_mtime(tmp_path, monkeypatch):
    md = str(tmp_path)
    p = os.path.join(md, "alpha.md")
    _write(p, "Cadence of the garden watering")
    e = _entry(md, "Cadence of the garden watering")
    assert RG._drift_patch(e, md) is e
    st = os.stat(p)
    _write(p, "Cadence of the orchard watering")  # same length
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))  # mtime put back; ctime cannot be
    calls = _count_parses(monkeypatch)
    patched = RG._drift_patch(e, md)
    assert calls == ["alpha"]
    assert patched is not e and patched["description"] == "Cadence of the orchard watering"
    assert RG._drift_patch(e, md)["description"] == "Cadence of the orchard watering"  # memo of the new text


def _links(idx, files):
    from memory.links_cache import LINKS_SCHEMA_VERSION, _LINKS_CACHE_NAME
    from memory.links_graph import dream_edges_admitted

    payload = {"schema_version": LINKS_SCHEMA_VERSION, "dream_admitted": dream_edges_admitted(),
               "files": files, "alias_to_file": {}, "ambiguous": {}, "unresolved": {}, "raw_targets": {},
               "typed_raw": {}, "typed_unresolved": {}, "planned": {}, "folded": {}, "fold_digests": []}
    tmp = os.path.join(idx, _LINKS_CACHE_NAME + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, os.path.join(idx, _LINKS_CACHE_NAME))


def test_edges_are_built_once_per_links_file(tmp_path, monkeypatch):
    idx = str(tmp_path)
    _links(idx, {"a": {"outbound": ["b"], "typed": {}}, "b": {"outbound": [], "typed": {}}})
    calls = []
    real = L.load_edges
    monkeypatch.setattr(L, "load_edges", lambda d: calls.append(d) or real(d))
    first = RG._load_hot_edges(idx)
    assert first["a"]["out"] == {"b"} and first["b"]["in"] == {"a"}
    assert RG._load_hot_edges(idx) is first
    assert len(calls) == 1
    _links(idx, {"a": {"outbound": [], "typed": {}}, "b": {"outbound": ["a"], "typed": {}}})  # a rebuild
    again = RG._load_hot_edges(idx)
    assert len(calls) == 2 and again["b"]["out"] == {"a"} and again["a"]["out"] == set()


def test_a_missing_links_file_is_never_memoized(tmp_path):
    idx = str(tmp_path)
    assert RG._load_hot_edges(idx) is None
    _links(idx, {"a": {"outbound": [], "typed": {}}})
    assert RG._load_hot_edges(idx) is not None


_DIGEST = """---
name: folded-garden
description: "Folded digest of old garden notes"
metadata:
  type: project
---
### orchard-pruning

Orchard pruning schedule for the winter apple trees

### compost-ratio

Compost ratio of browns to greens for the hot garden pile
"""


def test_explicit_recall_reads_the_section_sub_index_once_per_build(tmp_path, monkeypatch):
    from memory import build_index as B
    from memory import index_sections as S
    from memory import recall as R
    from memory import recall_sections as RS

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_TRUST_ALL", "1")
    RS._SEC_MEMO.clear()
    md, idx = str(tmp_path / "memory"), str(tmp_path / "idx")
    os.makedirs(md)
    files = {
        "compost.md": '---\nname: compost\ndescription: "Compost pile turning for the garden"\n'
                      "metadata:\n  type: project\n---\nx\n",
        "folded-garden.md": _DIGEST,
        "MEMORY.md": "# Memory Index\n",
    }
    for name, text in files.items():
        with open(os.path.join(md, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    with open(os.path.join(md, "hippo.json"), "w", encoding="utf-8") as fh:
        json.dump({"fold_digests": ["folded-*.md"]}, fh)
    B.build_index(md, idx)
    loads = []
    real = S.load_sections
    monkeypatch.setattr(S, "load_sections", lambda d: loads.append(d) or real(d))
    try:
        first = R.recall("garden compost", k=5, memory_dir=md, index_dir=idx)
        again = R.recall("garden compost", k=5, memory_dir=md, index_dir=idx)
        assert first == again and any(r.get("section") for r in first)
        assert len(loads) == 1
        with open(os.path.join(md, "folded-garden.md"), "a", encoding="utf-8") as fh:
            fh.write("\n### garden-shed\n\nThe garden shed compost bins and their hinges\n")
        B.build_index(md, idx)
        third = R.recall("garden compost shed", k=5, memory_dir=md, index_dir=idx)
        assert len(loads) == 2  # the rebuilt sub-index is read again
        assert "garden-shed" in [r["name"] for r in third if r.get("section")]
    finally:
        RS._SEC_MEMO.clear()
