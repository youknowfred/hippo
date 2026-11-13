"""A fused recall keeps each row's "indexed @sha" source.

``build_index`` stamps the manifest with the commit the corpus was indexed at, and a rank-1
body-win snippet renders it as ``indexed @<sha>``. When a user or private tier is fused into
the project's index the merged manifest used to drop that stamp, so the mark vanished from
every project row the moment a second tier existed. Each merged entry now carries its own
tier's stamp. Fixtures are written from scratch; BM25-only.
"""

from __future__ import annotations

import os

from memory import build_index as B
from memory import recall as R

from .conftest import git_commit

_BODY = (
    "## Error signature\n"
    "The exact failure is a qzvkwplaceholder timeout raised from the network layer when the "
    "retry budget is exhausted before the handshake completes successfully.\n\n"
    "## Root cause\n"
    "A misconfigured connection pool size caused exhaustion under load during peak traffic "
    "hours across every affected region consistently.\n"
)


def _mem(name, description, body):
    return f'---\nname: {name}\ndescription: "{description}"\nmetadata:\n  type: project\n---\n{body}\n'


def _write(d, name, description, body="body text here"):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem(name, description, body))


def _fused(repo, memory_dir, tmp_path, monkeypatch, user_git=False):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    user = str(tmp_path / "usertier")
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", user)
    idx = str(tmp_path / "idx")
    _write(memory_dir, "incident", "a generic description", _BODY)
    project_head = git_commit(repo, "seed corpus", 1_700_000_000)
    _write(user, "tabs-not-spaces", "always indent with tabs never spaces in this operator's code")
    B.build_index(memory_dir, idx)
    B.build_index(user, B.default_index_dir(user))
    return idx, project_head


def test_a_project_row_keeps_its_indexed_sha_when_a_user_tier_is_fused(repo, memory_dir, tmp_path, monkeypatch):
    idx, project_head = _fused(repo, memory_dir, tmp_path, monkeypatch)
    assert B.load_index(idx).manifest["head_commit"] == project_head
    res = R.recall("qzvkwplaceholder timeout", k=5, memory_dir=memory_dir, index_dir=idx)
    assert res and res[0]["name"] == "incident" and res[0]["corpus"] == "project"
    assert res[0]["body_win"] is True
    assert res[0]["head_commit"] == project_head            # was None once a tier was fused
    assert f"indexed @{project_head[:7]}" in R.format_results(res)


def test_a_user_tier_row_carries_its_own_tiers_stamp_not_the_projects(repo, memory_dir, tmp_path, monkeypatch):
    idx, project_head = _fused(repo, memory_dir, tmp_path, monkeypatch)
    res = R.recall("indent with tabs or spaces", k=5, memory_dir=memory_dir, index_dir=idx)
    row = next(r for r in res if r["name"] == "tabs-not-spaces")
    assert row["corpus"] == "user"
    user_manifest = B.load_index(B.default_index_dir(os.environ["HIPPO_USER_MEMORY_DIR"])).manifest
    assert row["head_commit"] == user_manifest.get("head_commit") != project_head


def test_a_single_corpus_recall_is_unchanged(repo, memory_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    idx = str(tmp_path / "idx")
    _write(memory_dir, "incident", "a generic description", _BODY)
    head = git_commit(repo, "seed corpus", 1_700_000_000)
    B.build_index(memory_dir, idx)
    res = R.recall("qzvkwplaceholder timeout", k=5, memory_dir=memory_dir, index_dir=idx)
    assert res[0]["head_commit"] == head and "corpus" in res[0] and res[0]["corpus"] is None
    assert all("head_commit" not in e for e in B.load_index(idx).entries)  # no per-entry stamp on disk


def test_the_merged_index_stamps_entries_and_writes_nothing(repo, memory_dir, tmp_path, monkeypatch):
    idx, project_head = _fused(repo, memory_dir, tmp_path, monkeypatch)
    user = os.environ["HIPPO_USER_MEMORY_DIR"]
    p_li, u_li = B.load_index(idx), B.load_index(B.default_index_dir(user))
    before = open(os.path.join(idx, "manifest.json"), "rb").read()
    merged = R._merge_loaded_indexes([(p_li, memory_dir, "project"), (u_li, user, "user")])
    stamps = {e["name"]: e.get("head_commit") for e in merged.entries}
    assert stamps["incident"] == project_head
    assert stamps["tabs-not-spaces"] == u_li.manifest.get("head_commit")
    assert all("head_commit" not in e for e in p_li.entries)  # the loaded tier is not mutated
    assert open(os.path.join(idx, "manifest.json"), "rb").read() == before
