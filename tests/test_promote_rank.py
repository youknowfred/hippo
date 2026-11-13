"""OWN-4: a lesson promoted to the user tier keeps its rank in its home corpus.

v1.45.0 measured a promoted lesson ranking LOWER in the corpus it came from. Two mechanisms:

1. Dense (explicit recall and the hook): the fused index stacked each tier's description
   rows AND body-chunk rows before the next tier's, while the rankers read the matrix as
   every description row first, then every chunk row. Every user-tier description vector was
   scored as a project body chunk, and a project chunk stood in for the user-tier lesson, so
   the lesson lost its dense description lane entirely. Pinned on synthetic matrices.
2. Hook only: ``promote_memory`` wrote the user-tier copy through ``write_memory``, which
   appends a floor pointer for every ``user``/``feedback`` memory. A lesson recalled on demand
   at home became a user-tier FLOOR member on the move, and the hook collapses floor members
   to a one-line "already in floor" mention. Floor membership now travels as it was.

Hermetic: BM25-only where a corpus is built, tmp dirs, the user tier via
``HIPPO_USER_MEMORY_DIR``.
"""

from __future__ import annotations

import os
import subprocess

from memory import new_memory as N
from memory import recall as R
from memory.build_index import build_index, default_index_dir
from memory.lint_floor import floor_memory_names
from memory.recall_hook import run_recall

_FLOOR = "# Memory index\n## User\n## Working Style & Process Feedback\n"
_QUERY = "should I hand over a slate of command steps or one line?"
_LESSON = "hand-one-act"


def _mem(md, name, desc, body, mtype="feedback"):
    with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write(f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: {mtype}\n---\n\n{body}\n')


def _corpus(tmp_path, monkeypatch, *, on_floor=False):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_TRUST_ALL", "1")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", str(tmp_path / "tel"))
    user = tmp_path / "user"
    user.mkdir()
    (user / "MEMORY.md").write_text(_FLOOR, encoding="utf-8")
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", str(user))
    root = tmp_path / "proj"
    md = root / ".claude" / "memory"
    md.mkdir(parents=True)
    floor = _FLOOR
    if on_floor:
        floor += f"- [Hand One Act]({_LESSON}.md) — one guarded line\n"
    (md / "MEMORY.md").write_text(floor, encoding="utf-8")
    _mem(str(md), _LESSON, "hand the owner one guarded command line, never a slate of steps",
         "A multi step slate races him. One act, one line, guarded.")
    topics = ["deploy pipeline", "database migration", "css theme tokens", "release tagging",
              "worktree cleanup", "flaky test triage", "vendor invoices", "calendar sync"]
    for i, t in enumerate(topics):
        _mem(str(md), f"note-{i}", f"how the {t} works and its command steps",
             f"The {t} has steps; run each command.", mtype="project")
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "seed"], check=True, env=env)
    build_index(str(md), default_index_dir(str(md)))
    return str(root), str(md), str(user)


def _explicit_rank(md, root):
    names = [h["name"] for h in R.recall(_QUERY, k=5, memory_dir=md, repo_root=root)]
    return names.index(_LESSON) + 1 if _LESSON in names else None


def _hook_rows(md, root, sid):
    """The hook block's full rows (a floor-collapsed mention is not a row)."""
    block = run_recall(_QUERY, session_id=sid, memory_dir=md, repo_root=root, hook=True) or ""
    rows = [ln.strip()[2:].split(" — ")[0] for ln in block.splitlines() if ln.strip().startswith("• ")]
    return rows, block


def test_promoted_lesson_keeps_its_hook_slot_and_explicit_rank(tmp_path, monkeypatch):
    root, md, user = _corpus(tmp_path, monkeypatch)
    rank_before = _explicit_rank(md, root)
    rows_before, _ = _hook_rows(md, root, "s-before")
    assert rank_before == 1 and _LESSON in rows_before

    r = N.promote_memory(_LESSON, memory_dir=md, repo_root=root, dest_tier="user")
    assert r["promoted"], r["error"]

    assert _explicit_rank(md, root) == rank_before
    rows_after, block = _hook_rows(md, root, "s-after")
    assert _LESSON in rows_after, block  # served as a row, not collapsed to "already in floor"
    assert rows_after.index(_LESSON) == rows_before.index(_LESSON)
    assert _LESSON not in floor_memory_names(user)  # it was never on a floor


def _tier(names, dim_offset, n_chunks=2):
    """A dense-ready LoadedIndex: one description row per name, then ``n_chunks`` body-chunk
    rows per name, every vector a distinct basis direction (so each row is identifiable)."""
    import numpy as np

    from memory.build_index import DEFAULT_MODEL, LoadedIndex

    entries, chunks, vecs = [], [], []
    for i, n in enumerate(names):
        entries.append({"name": n, "file": f"{n}.md", "tokens": [n], "row": i})
    for i, n in enumerate(names):
        for j in range(n_chunks):
            chunks.append({"entry": i, "hash": f"{n}{j}", "tokens": [f"{n}body"], "row": len(names) + len(chunks)})
    dim = 64
    for k in range(len(entries) + len(chunks)):
        v = np.zeros(dim, dtype="float32")
        v[dim_offset + k] = 1.0
        vecs.append(v)
    manifest = {"entries": entries, "body_chunks": chunks, "dense_ready": True, "model": DEFAULT_MODEL,
                "dim": dim, "count": len(entries)}
    return LoadedIndex(manifest, np.vstack(vecs))


def test_fused_dense_rows_are_every_description_then_every_chunk():
    """The rankers read the fused matrix as descriptions ``0..N-1`` then chunks ``N..``
    (``_dense_rank``: ``row < len(entries)``). Before the fix each tier's chunks sat between
    its entries and the next tier's entries, so a second tier's description rows were read
    as chunks of the first."""
    from memory.recall_tiers import _merge_loaded_indexes

    proj = _tier(["a", "b", "c"], 0)
    user = _tier(["lesson"], 20)
    m = _merge_loaded_indexes([(proj, "/p", "project"), (user, "/u", "user")])
    assert m.dense_ready
    n = len(m.entries)
    src = {("project", e["name"]): proj.dense[e["row"]] for e in proj.entries}
    src[("user", "lesson")] = user.dense[0]
    for i, e in enumerate(m.entries):
        assert e["row"] == i
        assert (m.dense[i] == src[(e["corpus"], e["name"])]).all()
    srcs = [(proj, c) for c in proj.body_chunks] + [(user, c) for c in user.body_chunks]
    for j, c in enumerate(m.body_chunks):
        assert c["row"] == n + j
        li, oc = srcs[j]
        assert (m.dense[n + j] == li.dense[oc["row"]]).all()


def test_a_user_tier_lesson_wins_the_dense_description_lane_after_fusion(monkeypatch):
    from memory import recall_rank as RR
    from memory.recall_tiers import _merge_loaded_indexes

    proj = _tier(["a", "b", "c"], 0)
    user = _tier(["lesson"], 20)
    m = _merge_loaded_indexes([(proj, "/p", "project"), (user, "/u", "user")])
    monkeypatch.setattr(RR, "embed_query", lambda q, allow_download=False: user.dense[0])
    lesson = next(i for i, e in enumerate(m.entries) if e["name"] == "lesson")
    assert RR._dense_rank("q", m)[:1] == [lesson]
    assert lesson not in RR._dense_rank_body("q", m)  # its description is not a body chunk


def test_a_floor_lesson_stays_on_the_floor_in_the_user_tier(tmp_path, monkeypatch):
    root, md, user = _corpus(tmp_path, monkeypatch, on_floor=True)
    r = N.promote_memory(_LESSON, memory_dir=md, repo_root=root, dest_tier="user")
    assert r["promoted"], r["error"]
    assert r["floor_removed"]["status"] == "removed"
    assert _LESSON in floor_memory_names(user)  # always-loaded before, always-loaded after
    assert _LESSON not in floor_memory_names(md)
