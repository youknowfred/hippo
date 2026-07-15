"""TEA-1 — the two-tier corpus: a machine-local user tier recalled ALONGSIDE the project
corpus (two-corpus fusion), with the floor drawn from BOTH, and — the sharpest invariant of
the release — ZERO user-tier leakage into the project's git.

Hermetic: every tier is a tmp dir; BM25-only (``HIPPO_DISABLE_DENSE=1``) so the fusion math is
exercised without a model. The conftest ``_isolate_memory_tiers`` fixture points the tier env
vars at absent tmp paths, so a test that wants a user tier sets ``HIPPO_USER_MEMORY_DIR`` to a
dir it populates.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from memory import build_index as B
from memory import new_memory as N
from memory import provenance as P
from memory import recall as R

from .conftest import write_file


def _mem(name: str, description: str, body: str = "body text here") -> str:
    return f'---\nname: {name}\ndescription: "{description}"\nmetadata:\n  type: feedback\n---\n{body}\n'


def _seed(memory_dir: str, items: dict) -> None:
    os.makedirs(memory_dir, exist_ok=True)
    for stem, desc in items.items():
        with open(os.path.join(memory_dir, f"{stem}.md"), "w", encoding="utf-8") as fh:
            fh.write(_mem(stem, desc))


# --------------------------------------------------------------------------- #
# Resolvers
# --------------------------------------------------------------------------- #
def test_user_memory_dir_default_is_under_home(monkeypatch):
    monkeypatch.delenv("HIPPO_USER_MEMORY_DIR", raising=False)
    got = P.user_memory_dir()
    assert got == os.path.join(os.path.expanduser("~"), ".claude", "hippo-memory")


def test_user_memory_dir_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", str(tmp_path / "elsewhere"))
    assert P.user_memory_dir() == str(tmp_path / "elsewhere")


def test_recall_tiers_lists_project_only_when_no_user_tier(tmp_path, monkeypatch):
    # conftest points HIPPO_USER_MEMORY_DIR at an absent path -> only the project tier.
    md = str(tmp_path / "proj" / ".claude" / "memory")
    os.makedirs(md, exist_ok=True)
    tiers = R._recall_tier_dirs(md, None)
    assert [t[2] for t in tiers] == ["project"]


# --------------------------------------------------------------------------- #
# Two-corpus fusion — the core deliverable
# --------------------------------------------------------------------------- #
def test_user_tier_memory_recallable_in_project_with_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    proj = str(tmp_path / "proj" / ".claude" / "memory")
    proj_idx = str(tmp_path / "proj" / ".claude" / ".memory-index")
    user = str(tmp_path / "usertier")
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", user)

    _seed(proj, {"project-thing": "how the project build pipeline caches artifacts"})
    _seed(user, {"tabs-not-spaces": "always indent with tabs never spaces in this operator's code"})
    B.build_index(proj, proj_idx)
    B.build_index(user, B.default_index_dir(user))

    hits = R.recall("indent with tabs or spaces", k=5, memory_dir=proj, index_dir=proj_idx)
    names = {h["name"]: h for h in hits}
    assert "tabs-not-spaces" in names, "a user-tier feedback memory must be recallable in a project"
    assert names["tabs-not-spaces"]["corpus"] == "user"
    assert names["tabs-not-spaces"]["root"] == user


def test_project_hits_labeled_project_when_fused(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    proj = str(tmp_path / "proj" / ".claude" / "memory")
    proj_idx = str(tmp_path / "proj" / ".claude" / ".memory-index")
    user = str(tmp_path / "usertier")
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", user)
    _seed(proj, {"caching-layer": "the caching layer invalidates on artifact hash change"})
    _seed(user, {"unrelated-user": "operator prefers concise commit messages"})
    B.build_index(proj, proj_idx)
    B.build_index(user, B.default_index_dir(user))
    hits = R.recall("caching layer invalidation", k=5, memory_dir=proj, index_dir=proj_idx)
    top = next(h for h in hits if h["name"] == "caching-layer")
    assert top["corpus"] == "project" and top["root"] == proj


def test_single_corpus_fast_path_leaves_results_untagged(tmp_path, monkeypatch):
    # No user tier -> the merge is a strict no-op; corpus/root are present-but-None.
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    proj = str(tmp_path / "proj" / ".claude" / "memory")
    proj_idx = str(tmp_path / "proj" / ".claude" / ".memory-index")
    _seed(proj, {"only-thing": "the only memory in this project corpus about widgets"})
    B.build_index(proj, proj_idx)
    hits = R.recall("widgets", k=5, memory_dir=proj, index_dir=proj_idx)
    assert hits and hits[0]["corpus"] is None and hits[0]["root"] is None


def test_project_slug_wins_cross_tier_name_collision(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    proj = str(tmp_path / "proj" / ".claude" / "memory")
    proj_idx = str(tmp_path / "proj" / ".claude" / ".memory-index")
    user = str(tmp_path / "usertier")
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", user)
    _seed(proj, {"shared-slug": "PROJECT copy of the shared slug about deployment"})
    _seed(user, {"shared-slug": "USER copy of the shared slug about deployment"})
    B.build_index(proj, proj_idx)
    B.build_index(user, B.default_index_dir(user))
    hits = R.recall("shared slug deployment", k=5, memory_dir=proj, index_dir=proj_idx)
    shared = [h for h in hits if h["name"] == "shared-slug"]
    assert len(shared) == 1, "a colliding slug must appear once (first-wins dedup)"
    assert shared[0]["corpus"] == "project", "the project tier wins the collision"


# --------------------------------------------------------------------------- #
# THE FLAGSHIP INVARIANT: no user-tier leakage into the project's git
# --------------------------------------------------------------------------- #
def test_user_tier_write_leaves_zero_trace_in_project_git(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    proj = os.path.join(repo, ".claude", "memory")
    _seed(proj, {"project-note": "a git-native project memory teammates share"})
    write_file(repo, ".claude/memory/MEMORY.md", "# Floor\n\n## User\n\n## Working Style & Process Feedback\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)

    user = str(tmp_path / "usertier")
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", user)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)

    res = N.write_memory(
        "secret-workflow",
        "this operator's personal deploy workflow — not for teammates",
        "feedback",
        body="never share this across the team",
        tier="user",
    )
    assert res["created"] and res["error"] is None
    assert res["tier"] == "user"

    # 1. The file physically lives in the user tier, NOT the project corpus.
    assert os.path.isfile(os.path.join(user, "secret-workflow.md"))
    assert not os.path.exists(os.path.join(proj, "secret-workflow.md"))

    # 2. The floor pointer landed in the USER tier's own MEMORY.md, never the project's.
    with open(os.path.join(proj, "MEMORY.md"), encoding="utf-8") as fh:
        assert "secret-workflow" not in fh.read()
    with open(os.path.join(user, "MEMORY.md"), encoding="utf-8") as fh:
        assert "secret-workflow.md" in fh.read()

    # 3. The project working tree is still pristine — `git status` shows nothing, and a
    #    habitual `git add -A` commits nothing from the user tier.
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert status.strip() == "", f"user-tier write dirtied the project git tree: {status!r}"
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert "secret-workflow" not in tracked

    # 4. The project's own index manifest never gains the user-tier entry.
    B.build_index(proj, os.path.join(repo, ".claude", ".memory-index"))
    manifest = B.load_index(os.path.join(repo, ".claude", ".memory-index"))
    assert manifest is not None
    assert "secret-workflow" not in {e["name"] for e in manifest.entries}


def test_user_tier_index_never_written_inside_the_repo(tmp_path, monkeypatch):
    """The user tier's derived index must resolve OUTSIDE any project repo."""
    user = str(tmp_path / "home" / ".claude" / "hippo-memory")
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", user)
    idx = B.default_index_dir(user)
    # The user-tier index is a sibling of the user corpus, under the fake home — never under a repo.
    assert idx == os.path.join(str(tmp_path / "home" / ".claude"), ".memory-index")


# --------------------------------------------------------------------------- #
# Floor drawn from BOTH
# --------------------------------------------------------------------------- #
def test_fused_floor_names_unions_project_and_user(tmp_path, monkeypatch):
    proj = str(tmp_path / "proj" / ".claude" / "memory")
    user = str(tmp_path / "usertier")
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", user)
    os.makedirs(proj, exist_ok=True)
    os.makedirs(user, exist_ok=True)
    write_file(
        str(tmp_path / "proj" / ".claude" / "memory"),
        "MEMORY.md",
        "# F\n\n## User\n- [P](proj_user.md) — x\n\n## Working Style & Process Feedback\n",
    )
    with open(os.path.join(user, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# F\n\n## User\n- [U](user_pref.md) — y\n\n## Working Style & Process Feedback\n")
    names = R.fused_floor_names(proj)
    assert names == {"proj_user", "user_pref"}


def test_portable_floor_producer_injects_user_floor(tmp_path, monkeypatch):
    proj = str(tmp_path / "proj" / ".claude" / "memory")
    user = str(tmp_path / "usertier")
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", user)
    os.makedirs(proj, exist_ok=True)
    _seed(user, {"user-pref": "operator writes tests in an outside-in style"})
    with open(os.path.join(user, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write(
            "# F\n\n## User\n\n## Working Style & Process Feedback\n"
            "- [Pref](user-pref.md) — outside-in tests\n"
        )
    out = R.portable_floor_producer(proj, proj)
    assert out is not None
    assert "user-pref" in out and "user tier" in out
    assert "outside-in style" in out  # the body is delivered, not just the pointer


def test_portable_floor_producer_silent_without_user_tier(tmp_path, monkeypatch):
    # conftest's absent HIPPO_USER_MEMORY_DIR -> no tier -> silent (None), never a crash.
    proj = str(tmp_path / "proj" / ".claude" / "memory")
    os.makedirs(proj, exist_ok=True)
    assert R.portable_floor_producer(proj, proj) is None


def test_portable_floor_producer_registered_after_native_floor():
    from memory import session_start as SS

    labels = [label for label, _fn in SS.PRODUCERS]
    assert "portable_floor" in labels
    # native floor lint precedes the portable-tier delivery
    assert labels.index("floor") < labels.index("portable_floor")


# --------------------------------------------------------------------------- #
# Write routing
# --------------------------------------------------------------------------- #
def test_write_memory_project_tier_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    proj = str(tmp_path / "proj" / ".claude" / "memory")
    os.makedirs(proj, exist_ok=True)
    write_file(str(tmp_path / "proj" / ".claude" / "memory"), "MEMORY.md",
               "# F\n\n## User\n\n## Working Style & Process Feedback\n")
    res = N.write_memory("proj-fact", "a project decision", "feedback", memory_dir=proj)
    assert res["created"] and res["tier"] == "project"
    assert os.path.isfile(os.path.join(proj, "proj-fact.md"))


def test_write_memory_invalid_tier_rejected(tmp_path):
    res = N.write_memory("x", "y", "feedback", memory_dir=str(tmp_path), tier="bogus")
    assert res["created"] is False and "invalid tier" in (res["error"] or "")


# --------------------------------------------------------------------------- #
# Dense fusion (hermetic — a deterministic fake embedder, no model download)
# --------------------------------------------------------------------------- #
def _fake_embedder(dim: int = 16):
    import zlib

    import numpy as np

    def _vec(text: str):
        v = np.zeros(dim, dtype="float32")
        for tok in B.tokenize(text):
            v[zlib.crc32(tok.encode("utf-8")) % dim] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_documents(texts, allow_download=True):
        import numpy as np

        return np.vstack([_vec(t) for t in texts]).astype("float32")

    def embed_query(text, allow_download=False):
        return _vec(text)

    return embed_documents, embed_query


def test_merge_vstacks_dense_when_every_tier_dense(tmp_path, monkeypatch):
    emb_docs, emb_query = _fake_embedder(16)
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    monkeypatch.setattr(B, "embed_documents", emb_docs)
    monkeypatch.setattr(R, "embed_query", emb_query)
    proj = str(tmp_path / "proj" / ".claude" / "memory")
    proj_idx = str(tmp_path / "proj" / ".claude" / ".memory-index")
    user = str(tmp_path / "usertier")
    _seed(proj, {"proj-a": "project alpha about columnar storage layout"})
    _seed(user, {"user-b": "user beta about the operator preferred editor keybindings"})
    B.build_index(proj, proj_idx)
    B.build_index(user, B.default_index_dir(user))

    p_li, u_li = B.load_index(proj_idx), B.load_index(B.default_index_dir(user))
    assert p_li.dense_ready and u_li.dense_ready
    merged = R._merge_loaded_indexes([(p_li, proj, "project"), (u_li, user, "user")])
    assert merged.dense_ready is True
    expected_rows = (
        len(p_li.entries) + len(p_li.body_chunks) + len(u_li.entries) + len(u_li.body_chunks)
    )
    assert merged.dense.shape[0] == expected_rows
    assert {e["corpus"] for e in merged.entries} == {"project", "user"}


def test_merge_degrades_to_bm25_when_a_tier_lacks_dense(tmp_path, monkeypatch):
    emb_docs, emb_query = _fake_embedder(16)
    monkeypatch.setattr(B, "embed_documents", emb_docs)
    monkeypatch.setattr(R, "embed_query", emb_query)
    proj = str(tmp_path / "proj" / ".claude" / "memory")
    proj_idx = str(tmp_path / "proj" / ".claude" / ".memory-index")
    user = str(tmp_path / "usertier")
    _seed(proj, {"proj-a": "project alpha about columnar storage layout"})
    _seed(user, {"user-b": "user beta about editor keybindings"})
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    B.build_index(proj, proj_idx)  # dense
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    B.build_index(user, B.default_index_dir(user))  # BM25-only

    # PRF-4: load_index now honours HIPPO_DISABLE_DENSE, which is still set from building the
    # user tier above. This test's subject is a tier that LACKS dense on disk, not the flag —
    # so clear it before loading, or the project tier degrades too and the asymmetry under
    # test disappears. (Before PRF-4 the flag was ignored here, which is the bug: it
    # suppressed dense scoring while leaving dense MMR reranking live on a dense index.)
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    p_li, u_li = B.load_index(proj_idx), B.load_index(B.default_index_dir(user))
    assert p_li.dense_ready and not u_li.dense_ready
    merged = R._merge_loaded_indexes([(p_li, proj, "project"), (u_li, user, "user")])
    assert merged.dense_ready is False and merged.dense is None
    # BM25 still fuses both corpora correctly.
    assert {e["name"] for e in merged.entries} == {"proj-a", "user-b"}


# --------------------------------------------------------------------------- #
# PRF-4 — load_index honours dense_disabled()
# --------------------------------------------------------------------------- #
def test_disable_dense_forces_bm25_only_on_an_already_dense_index(tmp_path, monkeypatch):
    """AC (PRF-4): HIPPO_DISABLE_DENSE=1 must mean BM25-only, as its own docstring, the
    README, CONTRIBUTING and STABILITY.md all say.

    It was enforced at exactly ONE boundary — _get_model raising — which stops every consumer
    that must EMBED a query. recall._mmr_rerank reads the STORED matrix and needs no model,
    so it walked past the only enforcement point and kept reranking for diversity on a dense
    index, measurably changing result order. Its guard is `dense is None`, which load_index
    decided without ever consulting the flag."""
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    md = str(tmp_path / "proj" / ".claude" / "memory")
    idx = str(tmp_path / "proj" / ".claude" / ".memory-index")
    _seed(md, {"a": "alpha about columnar storage", "b": "beta about editor keybindings"})
    B.build_index(md, idx)  # a genuinely dense index on disk

    live = B.load_index(idx)
    assert live.dense_ready and live.dense is not None  # dense IS on disk

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    gated = B.load_index(idx)
    assert gated.dense is None, "the flag did not reach the stored matrix"
    assert gated.dense_ready is False, "a dense-ready index under a BM25-only flag"
