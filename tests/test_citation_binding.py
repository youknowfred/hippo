"""FMT-2 slice 1: the index derives each memory's citation binding into a sidecar.

The sidecar (``citation_binding.json`` in the index dir) is derived from the BODY with the current
extractor, keyed by name with the content hash it came from, stamped with both derivation
versions, rebuilt incrementally, and invisible to the main manifest. Nothing reads it for
staleness yet; doctor and ``hippo migrate --check`` report it.
"""

from __future__ import annotations

import json
import os

import pytest

from .conftest import git_commit, write_file
from memory import build_index as BI
from memory import citation_binding as IB


def _mem(memory_dir, name, body, *, cited=None, exclude=None, desc=None):
    meta = "  type: project\n"
    if cited is not None:
        meta += f"  cited_paths: {json.dumps(cited)}\n"
    if exclude is not None:
        meta += f"  cited_paths_exclude: {json.dumps(exclude)}\n"
    meta += '  source_commit: "abc"\n'
    text = f'---\nname: {name}\ndescription: "{desc or name}"\nmetadata:\n{meta}---\n{body}\n'
    return write_file(memory_dir, f"{name}.md", text)


@pytest.fixture
def corpus(repo, memory_dir, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE", "dense")
    write_file(repo, "src/app.py", "def handle_request():\n    pass\n")
    write_file(repo, "src/util.py", "x = 1\n")
    write_file(repo, "lib/util.py", "y = 2\n")
    write_file(repo, "docs/plan.json", "{}\n")
    _mem(memory_dir, "alpha", "Lives in `src/app.py` (`handle_request`).", cited=["src/app.py"])
    _mem(memory_dir, "beta", "Mentions util.py (ambiguous) and docs/plan.json.",
         cited=["docs/plan.json", "src/util.py"], exclude=["docs/plan.json"])
    _mem(memory_dir, "gamma", "No code here.")
    git_commit(repo, "seed", 1_700_000_000)
    return repo, memory_dir, os.path.join(repo, ".hippo", "index")


def _doc(index_dir):
    with open(os.path.join(index_dir, IB.BINDING_NAME), encoding="utf-8") as fh:
        return json.load(fh)


def test_build_writes_the_body_derived_binding(corpus):
    _repo, md, idx = corpus
    BI.build_index(md, idx)
    doc = _doc(idx)
    from memory.provenance_format import CITATION_DERIVATION_VERSION
    from memory.staleness_anchors import ANCHOR_DERIVATION_VERSION

    assert doc["cite_derivation"] == CITATION_DERIVATION_VERSION
    assert doc["anchor_derivation"] == ANCHOR_DERIVATION_VERSION
    rows = doc["memories"]
    assert rows["alpha"]["cited_paths"] == ["src/app.py"]
    assert "handle_request" in rows["alpha"]["anchors"]
    # the exclusion holds the path out; the ambiguous bare basename never resolves
    assert rows["beta"]["cited_paths"] == []
    assert rows["beta"]["excluded"] == ["docs/plan.json"]
    assert rows["beta"]["unresolved"] == ["util.py"]
    assert rows["gamma"]["cited_paths"] == []
    with open(os.path.join(md, "alpha.md"), encoding="utf-8") as fh:
        assert rows["alpha"]["hash"] == IB.text_hash(fh.read())


def test_the_manifest_never_carries_it(corpus, monkeypatch):
    _repo, md, idx = corpus
    BI.build_index(md, idx)
    with_binding = open(os.path.join(idx, "manifest.json"), "rb").read()
    os.remove(os.path.join(idx, "manifest.json"))
    os.remove(os.path.join(idx, IB.BINDING_NAME))
    monkeypatch.setattr(IB, "write_binding", lambda *a, **k: None)
    BI.build_index(md, idx)
    assert open(os.path.join(idx, "manifest.json"), "rb").read() == with_binding
    assert not os.path.exists(os.path.join(idx, IB.BINDING_NAME))


def test_incremental_rederives_only_changed_text(corpus, monkeypatch):
    _repo, md, idx = corpus
    BI.build_index(md, idx)
    calls = []
    real = IB.derive_row
    monkeypatch.setattr(IB, "derive_row", lambda t, f, b: calls.append(t) or real(t, f, b))
    BI.build_index(md, idx)
    assert calls == []
    _mem(md, "gamma", "Now names `src/app.py`.")
    BI.build_index(md, idx)
    assert len(calls) == 1
    assert _doc(idx)["memories"]["gamma"]["cited_paths"] == ["src/app.py"]


def test_a_changed_file_list_rederives(corpus):
    repo, md, idx = corpus
    _mem(md, "gamma", "Will name `new/thing.py` before it exists.")
    git_commit(repo, "gamma", 1_700_000_100)
    BI.build_index(md, idx)
    assert _doc(idx)["memories"]["gamma"]["cited_paths"] == []
    write_file(repo, "new/thing.py", "z = 3\n")
    git_commit(repo, "thing", 1_700_000_200)
    BI.refresh_index(md, idx)  # corpus unchanged: the no-op path still refreshes the sidecar
    assert _doc(idx)["memories"]["gamma"]["cited_paths"] == ["new/thing.py"]


def test_refresh_noop_path_picks_up_a_frontmatter_only_exclusion(corpus):
    _repo, md, idx = corpus
    BI.build_index(md, idx)
    _mem(md, "alpha", "Lives in `src/app.py` (`handle_request`).", cited=["src/app.py"],
         exclude=["src/app.py"])
    BI.refresh_index(md, idx)
    row = _doc(idx)["memories"]["alpha"]
    assert row["cited_paths"] == [] and row["excluded"] == ["src/app.py"]


@pytest.mark.parametrize("damage", ["missing", "torn", "schema", "derivation", "anchors"])
def test_missing_torn_or_mismatched_reads_as_absent(corpus, damage):
    _repo, md, idx = corpus
    BI.build_index(md, idx)
    path = os.path.join(idx, IB.BINDING_NAME)
    doc = _doc(idx)
    if damage == "missing":
        os.remove(path)
    elif damage == "torn":
        open(path, "w").write(json.dumps(doc)[:40])
    else:
        key = {"schema": "schema", "derivation": "cite_derivation", "anchors": "anchor_derivation"}[damage]
        doc[key] = doc[key] - 1
        open(path, "w").write(json.dumps(doc))
    assert IB.read_binding(idx) is None
    want = "behind" if damage in ("derivation", "anchors") else "absent"
    assert IB.binding_state(idx)[0] == want
    BI.build_index(md, idx)  # the next build heals it
    assert IB.read_binding(idx) is not None


def test_row_for_needs_the_exact_text(corpus):
    _repo, md, idx = corpus
    BI.build_index(md, idx)
    doc = IB.read_binding(idx)
    text = open(os.path.join(md, "alpha.md"), encoding="utf-8").read()
    assert IB.row_for(doc, "alpha", text)["cited_paths"] == ["src/app.py"]
    assert IB.row_for(doc, "alpha", text + " ") is None
    assert IB.row_for(doc, "nobody", text) is None


def test_no_git_work_tree_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE", "dense")
    md = str(tmp_path / "loose" / ".claude" / "memory")
    _mem(md, "alpha", "Lives in `src/app.py`.")
    idx = str(tmp_path / "idx")
    BI.build_index(md, idx)
    assert os.path.exists(os.path.join(idx, "manifest.json"))
    assert not os.path.exists(os.path.join(idx, IB.BINDING_NAME))


def test_one_git_call_per_build(corpus, monkeypatch):
    _repo, md, idx = corpus
    from memory import provenance_env

    seen = []
    real = provenance_env.run_git
    monkeypatch.setattr(provenance_env, "run_git", lambda a, r: seen.append(a[0]) or real(a, r))
    BI.build_index(md, idx)
    assert seen.count("ls-files") == 1


def test_compare_counts_and_doctor_line(corpus):
    from memory.doctor_checks_env import DoctorContext

    repo, md, idx = corpus
    ctx = DoctorContext(memory_dir=md, repo_root=repo)
    assert "not built yet" in IB.check_citation_binding(ctx)["message"]
    BI.build_index(md, idx)
    c = IB.compare(md)
    # beta: frontmatter still carries src/util.py (a hand-kept path the body does not pin)
    assert (c["state"], c["memories"], c["derived"], c["disagree"]) == ("current", 3, 3, 1)
    assert c["disagree_names"] == ["beta"]
    msg = IB.check_citation_binding(ctx)
    assert msg["status"] == "ok"
    assert msg["message"] == ("citation binding in the index: current for 3 of 3 memories; "
                              "frontmatter cited_paths differ on 1.")
    doc = _doc(idx)
    doc["cite_derivation"] -= 1
    open(os.path.join(idx, IB.BINDING_NAME), "w").write(json.dumps(doc))
    assert "older extractor" in IB.check_citation_binding(ctx)["message"]


def test_doctor_registers_the_check():
    from memory.doctor import CHECKS

    labels = [label for label, _fn in CHECKS]
    assert "citation_binding" in labels


def test_classify_names_each_mechanism(corpus):
    _repo, md, idx = corpus
    BI.build_index(md, idx)
    doc = IB.read_binding(idx)
    beta = open(os.path.join(md, "beta.md"), encoding="utf-8").read()
    row = IB.row_for(doc, "beta", beta)
    assert IB.classify_path("docs/plan.json", beta, row) == "exclusion"
    assert IB.classify_path("src/util.py", beta, row) == "resolution difference"
    alpha = open(os.path.join(md, "alpha.md"), encoding="utf-8").read()
    arow = IB.row_for(doc, "alpha", alpha)
    assert IB.classify_path("src/app.py", alpha, arow) == "body names it, frontmatter lacks it"
    assert IB.classify_path("lib/util.py", alpha, arow) == "frontmatter has it, body no longer names it"


def test_migrate_check_reports_the_same_count_and_writes_nothing(corpus):
    from memory import migrate as MG

    from .test_migrate import _hash_tree

    repo, md, idx = corpus
    r = MG.check(md)
    assert r["citation_binding"]["state"] == "absent"
    assert "bindings the index already derives: none yet" in MG.render(r)
    BI.build_index(md, idx)
    before = _hash_tree(repo)
    r = MG.check(md)
    assert _hash_tree(repo) == before
    assert r["citation_binding"]["derived"] == 3 and r["citation_binding"]["disagree"] == 1
    assert ("bindings the index already derives: 3 of 3 (1 differ from frontmatter cited_paths)"
            in MG.render(r))
