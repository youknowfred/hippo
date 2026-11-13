"""HOT-11: the sections of a fold digest as recall units of their own.

A ``### <slug>`` section in a declared fold digest becomes an entry of a sub-index beside the
main one; the main index's rows never change. Explicit recall fuses every section; the
prompt hook fuses only the sections a prompt names, so an unnamed section never reaches it.
Fixtures are written from scratch; BM25-only unless a test says so.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from memory import build_index as B
from memory import index_sections as S
from memory import recall as R
from memory import recall_sections as RS
from memory.recall_hook import run_recall

_DIGEST = """---
name: folded-garden
description: "Folded digest of old garden notes"
metadata:
  type: project
---
Preamble that links [[watering]].

## Members

### orchard-pruning

Orchard pruning schedule for the winter apple trees and the ladder rules

The crew prunes on Tuesdays with the tall ladder; zebra quokka marmalade is a phrase that
lives only in this section body, far below the digest's description and first chunk.
See [[compost-ratio]] and [[watering]].

### compost-ratio

Compost ratio of browns to greens for the hot pile

```
### not-a-heading-inside-a-fence
```

### watering

A live memory has this name, so the file wins and this section is not indexed.

### Two Words

Not a slug heading, so not a section.
"""

_SECOND = """---
name: folded-zz-more
description: "A second digest"
metadata:
  type: project
---
### compost-ratio

A later duplicate of a slug: the first occurrence in sorted file order wins.
"""


def _mem(name, desc, body="", kind="project"):
    return f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: {kind}\n---\n{desc}\n{body}\n'


def _write(md, policy, extra=None):
    os.makedirs(md, exist_ok=True)
    files = {
        "watering.md": _mem("watering", "Watering cadence for the vegetable beds in summer"),
        "planting.md": _mem("planting", "Planting calendar for spring seedlings", "Related: [[orchard-pruning]]"),
        "billing.md": _mem("billing", "Billing ledger cutover checklist for invoices"),
        "folded-garden.md": _DIGEST,
        "folded-zz-more.md": _SECOND,
        "MEMORY.md": "# Memory Index\n\n## User\n",
    }
    files.update(extra or {})
    for name, text in files.items():
        with open(os.path.join(md, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    if policy is not None:
        with open(os.path.join(md, "hippo.json"), "w", encoding="utf-8") as fh:
            json.dump(policy, fh)


_UNDECLARED = {"fold_digests": ["folded-*.md"]}
_DECLARED = {"fold_digests": ["folded-*.md"], "journals": ["*-round.md"]}


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "tel"))
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", str(tmp_path / "no-user"))
    monkeypatch.setenv("HIPPO_LOCAL_MEMORY_DIR", str(tmp_path / "no-local"))
    monkeypatch.setenv("HIPPO_TRUST_ALL", "1")

    def make(policy=_UNDECLARED, extra=None, sub="c"):
        md = str(tmp_path / sub / "memory")
        idx = str(tmp_path / sub / "idx")
        _write(md, policy, extra)
        B.build_index(md, idx)
        return md, idx

    return make


def _section_files(idx):
    return sorted(n for n in os.listdir(idx) if n.startswith("sections."))


# --------------------------------------------------------------------------- #
# Parsing and the plan
# --------------------------------------------------------------------------- #
def test_parse_sections_reads_slug_headings_with_their_lines():
    secs, outside = S.parse_sections(_DIGEST)
    names = [s for s, _l, _b in secs]
    assert names == ["orchard-pruning", "compost-ratio", "watering"]
    lines = _DIGEST.splitlines()
    for slug, line, body in secs:
        assert lines[line - 1] == f"### {slug}"
    # A fenced "###" is body text, and a non-slug heading ends the section before it.
    assert "not-a-heading-inside-a-fence" in secs[1][2]
    assert "Not a slug heading" not in secs[2][2]
    assert "Preamble that links [[watering]]" in outside and "## Members" in outside


def test_live_stem_and_duplicate_are_skipped_and_counted(corpus):
    md, idx = corpus()
    sm = S.read_manifest(idx)
    assert [e["name"] for e in sm["entries"]] == ["orchard-pruning", "compost-ratio"]
    assert sm["skipped"] == {"live_memory": 1, "duplicate": 1}
    first = sm["entries"][1]
    assert first["file"] == "folded-garden.md"  # the earlier file in sorted order wins
    assert all(e["lane"] == "journal" for e in sm["entries"])
    assert sm["entries"][0]["description"].startswith("Orchard pruning schedule")


def test_main_manifest_gains_only_the_slug_record(corpus):
    md, idx = corpus()
    m = B._load_manifest(idx)
    assert m["sections"] == {"slugs": {"compost-ratio": "folded-garden.md", "orchard-pruning": "folded-garden.md"}}
    names = {e["name"] for e in m["entries"]}
    assert "orchard-pruning" not in names and m["count"] == len(m["entries"]) == 5


def test_no_digest_section_means_no_files_and_no_key(corpus):
    plain = {"folded-garden.md": _mem("folded-garden", "A digest with no sections yet"), "folded-zz-more.md": _mem("folded-zz-more", "x y z")}
    for policy, sub in ((None, "a"), (_UNDECLARED, "b")):
        md, idx = corpus(policy, extra=plain, sub=sub)
        assert _section_files(idx) == []
        assert "sections" not in B._load_manifest(idx)
        assert S.plan_sections(md, _texts(md)) is None


# --------------------------------------------------------------------------- #
# Explicit recall
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("query", ["zebra quokka marmalade", "winter apple pruning ladder", "billing invoices"])
def test_explicit_recall_appends_sections_after_unchanged_memory_rows(corpus, monkeypatch, tmp_path, query):
    md, idx = corpus()
    rows = R.recall(query, memory_dir=md, index_dir=idx)
    md2, idx2 = corpus(sub="bare")
    _strip_sections(idx2)
    bare = R.recall(query, memory_dir=md2, index_dir=idx2)
    mem = [r for r in rows if not r.get("section")]
    assert len(mem) == len(bare) and all(
        {k: v for k, v in a.items() if k not in ("root",)} == {k: v for k, v in b.items() if k not in ("root",)}
        for a, b in zip(mem, bare)
    )  # the memory ranking is exactly what it is without sections
    secs = [r for r in rows if r.get("section")]
    assert rows == mem + secs and [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    assert len(secs) <= RS.extra_k()
    if query != "billing invoices":
        top = secs[0]
        assert top["name"] == "orchard-pruning" and top["file"] == "folded-garden.md"
        assert _DIGEST.splitlines()[top["line"] - 1] == "### orchard-pruning" and top["digest"] == "folded-garden"
        assert f"  • orchard-pruning (folded-garden.md:{top['line']}) — Orchard pruning" in R.format_results(rows)


def test_section_rows_can_be_switched_off(corpus, monkeypatch):
    md, idx = corpus()
    idx0 = B.load_index(idx)
    # Sections ride an answer; an abstention (no memory row) stays one.
    assert RS.extra([], "zebra quokka marmalade", idx0, md, idx, explicit=True, recall_fn=R.recall) == []
    rule = {"name": "r", "corpus": "rule", "rank": 1}
    assert RS.extra([rule], "zebra quokka marmalade", idx0, md, idx, explicit=True, recall_fn=R.recall) == [rule]
    monkeypatch.setenv("HIPPO_SECTION_ROWS", "0")
    assert not any(r.get("section") for r in R.recall("zebra quokka marmalade", memory_dir=md, index_dir=idx))


def test_recall_view_names_the_section_and_its_line(corpus):
    from memory.recall_view import describe

    md, idx = corpus()
    hits: list = []
    text = describe("zebra quokka marmalade", memory_dir=md, index_dir=idx, collect=hits)
    assert "folded section, folded-garden.md line" in text
    sec = next(h for h in hits if h["name"] == "orchard-pruning")
    assert sec["file"] == "folded-garden.md" and isinstance(sec["line"], int)


def test_eval_resolves_a_folded_gold_stem_to_its_section(corpus, tmp_path):
    from memory.eval_metrics import hard_set_resolvability
    from memory.eval_recall import evaluate

    md, idx = corpus()
    row = {"query": "q", "expected": ["orchard-pruning"]}
    assert list(hard_set_resolvability(B.load_index(idx), [row]).values())[0]["resolvable_n"] == 1
    hs = tmp_path / "hard.yaml"
    hs.write_text("- query: winter apple tree pruning ladder\n  expected: [orchard-pruning]\n", encoding="utf-8")
    rep = evaluate(memory_dir=md, index_dir=idx, hard_set_path=str(hs))
    assert rep["gates"]["hard_recall@10"]["value"] == 1.0


# --------------------------------------------------------------------------- #
# The prompt hook: named sections only
# --------------------------------------------------------------------------- #
def _strip_sections(idx):
    for n in _section_files(idx):
        os.remove(os.path.join(idx, n))
    p = os.path.join(idx, "manifest.json")
    with open(p, encoding="utf-8") as fh:
        m = json.load(fh)
    m.pop("sections")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(m, fh)


@pytest.mark.parametrize("policy", [_UNDECLARED, _DECLARED])
def test_hook_never_shows_an_unnamed_section_and_is_unchanged(corpus, policy, monkeypatch, tmp_path):
    md, idx = corpus(policy)
    prompts = ["winter apple tree pruning ladder schedule", "zebra quokka marmalade", "compost browns greens ratio"]
    with_sections = [run_recall(p, session_id="s1", memory_dir=md, index_dir=idx, hook=True) for p in prompts]
    md2, idx2 = corpus(policy, sub="bare")
    _strip_sections(idx2)
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "tel2"))
    without = [run_recall(p, session_id="s1", memory_dir=md2, index_dir=idx2, hook=True) for p in prompts]
    assert with_sections == without
    for block in with_sections:
        assert "  • orchard-pruning" not in block and "  • compost-ratio" not in block


@pytest.mark.parametrize("policy", [_UNDECLARED, _DECLARED])
def test_hook_shows_a_section_the_prompt_names_and_only_that_one(corpus, policy):
    md, idx = corpus(policy)
    block = run_recall("what did orchard-pruning say about the compost pile", session_id="s2", memory_dir=md, index_dir=idx, hook=True)
    assert "  • orchard-pruning (folded-garden.md:" in block
    assert "compost-ratio" not in block  # unnamed, though it matches the words


def test_admit_is_the_seam_for_named_slugs(corpus):
    md, idx = corpus()
    idx0 = B.load_index(idx)
    fused = RS.admit(idx0, md, idx, ["compost-ratio"])
    names = [e["name"] for e in fused.entries]
    assert names[: len(idx0.entries)] == [e["name"] for e in idx0.entries]
    assert names[len(idx0.entries):] == ["compost-ratio"]
    assert fused.manifest["lanes"] == idx0.manifest["lanes"]
    assert "corpus" not in fused.entries[0] and "root" not in fused.entries[0]
    assert RS.named_sections(idx0.manifest, {"orchard-pruning", "other"}) == {"orchard-pruning"}
    assert RS.fuse(idx0, md, idx, _Lanes(set())) is idx0  # names nothing: unchanged, no read


class _Lanes:
    def __init__(self, names):
        self._n = names

    def named(self):
        return self._n


# --------------------------------------------------------------------------- #
# Graph: a [[slug]] resolves to the section, and section links belong to it
# --------------------------------------------------------------------------- #
def test_a_wikilink_to_a_folded_slug_never_puts_a_section_in_a_memory_slot(corpus):
    md, idx = corpus()
    rows = R.recall("planting calendar for spring seedlings", memory_dir=md, index_dir=idx)
    for r in rows:
        if r["name"] in ("orchard-pruning", "compost-ratio"):
            assert r.get("section") is True  # only ever an appended row
    assert not any(r.get("via") == "graph" and r["name"] == "folded-garden" for r in rows)


def test_a_named_section_expands_from_its_own_links_in_the_hook(corpus):
    md, idx = corpus()
    block = run_recall("what does orchard-pruning say", session_id="s9", memory_dir=md, index_dir=idx, hook=True)
    assert "  • orchard-pruning (folded-garden.md:" in block


def test_digest_keeps_only_the_links_written_outside_its_sections(corpus):
    from memory.links import load_edges

    md, idx = corpus()
    edges = load_edges(idx)
    assert edges["folded-garden"]["out"] == {"watering"}  # resolved from the whole digest today
    fused = RS.admit(B.load_index(idx), md, idx, None)
    e2 = RS.section_edges(edges, fused)
    assert e2["folded-garden"]["out"] == {"watering"}  # its preamble link, not a section's
    assert e2["orchard-pruning"]["out"] == {"compost-ratio", "watering"}
    assert e2["orchard-pruning"]["in"] == {"planting"}
    assert "orchard-pruning" in e2["planting"]["out"] and "orchard-pruning" not in edges["planting"]["out"]
    assert edges["folded-garden"]["out"] == {"watering"}  # the loaded edges are never mutated


def test_section_links_leave_a_digest_with_no_extra_degree(corpus):
    from memory.links import load_edges

    extra = {"folded-garden.md": _DIGEST.replace("Preamble that links [[watering]].", "Preamble.")}
    md, idx = corpus(extra=extra)
    edges = load_edges(idx)
    assert edges["folded-garden"]["out"] == {"watering"}  # from inside a section
    e2 = RS.section_edges(edges, RS.admit(B.load_index(idx), md, idx, None))
    assert e2["folded-garden"]["out"] == set() and "folded-garden" not in e2["watering"]["in"]
    assert e2["folded-garden"]["in"] == edges["folded-garden"]["in"]


# --------------------------------------------------------------------------- #
# Refresh, incremental
# --------------------------------------------------------------------------- #
def test_refresh_is_a_no_op_when_nothing_changed(corpus):
    md, idx = corpus()
    p = os.path.join(idx, S.SECTIONS_MANIFEST)
    before = (os.stat(p).st_mtime_ns, open(p, "rb").read())
    B.refresh_index(md, idx)
    assert (os.stat(p).st_mtime_ns, open(p, "rb").read()) == before


def test_a_section_edit_reaches_the_sub_index_without_a_main_rebuild(corpus):
    md, idx = corpus()
    main = os.path.join(idx, "manifest.json")
    main_before = open(main, "rb").read()
    dp = os.path.join(md, "folded-garden.md")
    text = open(dp, encoding="utf-8").read().replace("Compost ratio of browns", "Compost ratio of dry browns")
    with open(dp, "w", encoding="utf-8") as fh:
        fh.write(text)
    B.refresh_index(md, idx)
    sm = S.read_manifest(idx)
    assert any("dry browns" in e["description"] for e in sm["entries"])
    assert open(main, "rb").read() == main_before


def test_a_corpus_that_loses_its_sections_loses_the_sub_index(corpus):
    md, idx = corpus()
    assert _section_files(idx)
    for f in ("folded-garden.md", "folded-zz-more.md"):
        os.remove(os.path.join(md, f))
    B.refresh_index(md, idx)
    assert _section_files(idx) == []
    assert "sections" not in B._load_manifest(idx)


def test_a_section_entry_is_never_drift_patched(corpus):
    from memory.recall_graph import _drift_patch

    md, idx = corpus()
    e = S.read_manifest(idx)["entries"][0]
    assert _drift_patch(e, md) is e


# --------------------------------------------------------------------------- #
# Dense: rows by hash, and an incomplete sub-index is not fused
# --------------------------------------------------------------------------- #
def _fake_embed(calls):
    def embed_documents(texts, allow_download=True):
        calls.append(len(texts))
        out = np.zeros((len(texts), 8), dtype="float32")
        for i, t in enumerate(texts):
            out[i, hash(t) % 8] = 1.0
        return out

    return embed_documents


def test_section_rows_are_reused_by_hash_shortest_first(tmp_path, monkeypatch):
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.delenv("HIPPO_DISABLE", raising=False)
    calls: list = []
    monkeypatch.setattr(B, "embed_documents", _fake_embed(calls))
    md, idx = str(tmp_path / "memory"), str(tmp_path / "idx")
    _write(md, _UNDECLARED)
    B.build_index(md, idx)
    sm = S.read_manifest(idx)
    assert sm["dense_ready"] is True and sm["dense_file"] in os.listdir(idx)
    sec = S.load_sections(idx)
    assert sec.dense_ready and sec.dense.shape[0] == len(sec.entries) + len(sec.body_chunks)
    for c in sec.body_chunks:  # an embedded opening chunk: a real row
        assert sec.dense[c["row"]].any()
    calls.clear()
    dp = os.path.join(md, "folded-garden.md")
    text = open(dp, encoding="utf-8").read().replace("Compost ratio of browns", "Compost ratio of dry browns")
    with open(dp, "w", encoding="utf-8") as fh:
        fh.write(text)
    B.build_index(md, idx)
    # The edited section's two rows (its line and its opening chunk) in one slice; the main
    # rows and the unchanged section came back by hash.
    assert calls == [2]


def test_bm25_body_rows_take_a_zero_row_and_are_never_embedded(tmp_path, monkeypatch):
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.delenv("HIPPO_DISABLE", raising=False)
    monkeypatch.setattr(S, "BODY_MODE", "bm25")
    calls: list = []
    monkeypatch.setattr(B, "embed_documents", _fake_embed(calls))
    md, idx = str(tmp_path / "memory"), str(tmp_path / "idx")
    _write(md, _UNDECLARED)
    B.build_index(md, idx)
    sec = S.load_sections(idx)
    assert sec.dense_ready and sec.body_chunks
    for c in sec.body_chunks:
        assert not sec.dense[c["row"]].any()


def test_an_incomplete_sub_index_is_not_fused_into_a_dense_main(tmp_path, monkeypatch):
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.delenv("HIPPO_DISABLE", raising=False)
    monkeypatch.setattr(B, "embed_documents", _fake_embed([]))
    md, idx = str(tmp_path / "memory"), str(tmp_path / "idx")
    _write(md, _UNDECLARED)
    m = B.build_index(md, idx)
    S.write_sections(idx, S.plan_sections(md, _texts(md)), allow_download=False, deadline=0.0, force=True)
    sm = S.read_manifest(idx)
    assert m["dense_ready"] is True and sm["dense_ready"] is False
    main = B.load_index(idx)
    assert main.dense_ready
    assert RS.admit(main, md, idx, None) is main  # still catching up: the main index keeps dense
    S.write_sections(idx, S.plan_sections(md, _texts(md)), allow_download=False)
    fused = RS.admit(main, md, idx, None)
    assert fused is not main and fused.dense_ready


def _texts(md):
    out = {}
    for n in os.listdir(md):
        if n.endswith(".md") and n != "MEMORY.md":
            out[n[:-3]] = open(os.path.join(md, n), encoding="utf-8").read()
    return out


# --------------------------------------------------------------------------- #
# Doctor
# --------------------------------------------------------------------------- #
def test_doctor_lane_line_counts_sections_and_skips(corpus, monkeypatch):
    from memory.doctor_checks_env import DoctorContext
    from memory.doctor_checks_lanes import check_recall_lanes

    md, idx = corpus()
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx)
    msg = check_recall_lanes(DoctorContext(md, None))["message"]
    assert "Folded sections: 2 indexed from 2 digests (BM25 only)" in msg
    assert "1 not indexed because a live memory has the name, 1 duplicate name" in msg
    md2, idx2 = corpus(None, extra={"folded-garden.md": _mem("folded-garden", "d"), "folded-zz-more.md": _mem("folded-zz-more", "d")}, sub="none")
    monkeypatch.setenv("HIPPO_INDEX_DIR", idx2)
    assert "Folded sections" not in check_recall_lanes(DoctorContext(md2, None))["message"]
