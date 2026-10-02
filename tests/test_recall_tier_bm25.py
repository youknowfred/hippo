"""PRF-6: per-prompt recall work that was recomputed on every hook call.

Field measurement 2026-10-01 (em-growth-labs, 999 project + 28 user-tier memories): a warm
recall spent ~0.3s re-counting BM25 over all 3,431 fused docs because a user tier existed,
and ~0.35s pure-Python YAML-parsing 200 frontmatters for the mid-session drift check.

- The fused index now merges each tier's PERSISTED BM25 stats (``_merge_bm25_stats``) with
  lazily assembled postings. It must equal ``compute_bm25_stats`` over the merged docs:
  values, dict order, float bits. A property test pins that over random tiers, collisions
  included.
- ``parse_frontmatter`` uses libyaml's ``CSafeLoader`` when PyYAML ships it.
- ``python -m memory.recall`` runs with cyclic GC off; in-process callers keep it on.
"""

from __future__ import annotations

import gc
import json
import os

import pytest
import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from memory import build_index as B
from memory import provenance as P
from memory import recall as R
from memory import recall_tiers as RT


def _tier(entries, chunks):
    """A LoadedIndex shaped like a loaded manifest: entries [(name, tokens)], chunks
    [(entry_index, tokens)], persisted stats JSON round-tripped as they come off disk."""
    ents = [{"name": n, "tokens": list(t), "row": None} for n, t in entries]
    chs = [{"entry": i, "tokens": list(t), "row": None} for i, t in chunks]
    stats = B.compute_bm25_stats([e["tokens"] for e in ents] + [c["tokens"] for c in chs])
    manifest = {
        "entries": ents,
        "body_chunks": chs,
        "bm25": json.loads(json.dumps(stats)),
        "dense_ready": False,
    }
    return B.LoadedIndex(manifest, None)


def _materialized(stats):
    out = dict(stats)
    out["postings"] = dict(stats["postings"].items())
    return out


def _assert_identical_to_recompute(merged):
    ref = B.compute_bm25_stats(
        [e.get("tokens") or [] for e in merged.entries]
        + [c.get("tokens") or [] for c in merged.body_chunks]
    )
    got = _materialized(merged.manifest["bm25"])
    # json.dumps compares dict ORDER and exact float reprs, not just mapping equality.
    assert json.dumps(got) == json.dumps(ref)


def _merge(*tiers):
    labels = ["project", "private", "user"]
    return R._merge_loaded_indexes([(t, f"/tier{i}", labels[i]) for i, t in enumerate(tiers)])


def test_merged_stats_equal_recompute_and_postings_stay_lazy():
    proj = _tier(
        [("deploy", ["deploy", "railway", "build"]), ("tests", ["test", "suite", "build"])],
        [(0, ["railway", "deploy", "deploy"]), (1, ["chrome", "render", "gate"])],
    )
    user = _tier([("tabs", ["tabs", "indent", "build"])], [(0, ["indent", "spaces"])])
    merged = _merge(proj, user)
    postings = merged.manifest["bm25"]["postings"]
    assert isinstance(postings, RT._MergedPostings)
    assert not postings._built  # nothing materialized until a query reads a token
    _assert_identical_to_recompute(merged)


def test_token_first_seen_in_a_lower_tier_entry_orders_before_upper_tier_chunks():
    # "render" occurs in the project's CHUNK and the user's ENTRY. Merged order puts every
    # entry before every chunk, so its first merged doc is the user entry: dict order and
    # the IDF sum order must follow that, not the project's own dict order.
    proj = _tier([("a", ["alpha"]), ("b", ["beta"])], [(0, ["render", "alpha"])])
    user = _tier([("c", ["gamma", "render"])], [(0, ["delta"])])
    _assert_identical_to_recompute(_merge(proj, user))


def test_slug_collision_recounts_only_the_losing_tier(monkeypatch):
    proj = _tier([("shared", ["deploy", "pipeline"]), ("other", ["widget"])], [(0, ["deploy"])])
    user = _tier(
        [("shared", ["deploy", "user", "copy"]), ("solo", ["tabs"])],
        [(0, ["dropped", "with", "its", "parent"]), (1, ["indent"])],
    )
    calls = []
    real = RT.compute_bm25_stats

    def spy(docs):
        calls.append(len(docs))
        return real(docs)

    monkeypatch.setattr(RT, "compute_bm25_stats", spy)
    merged = _merge(proj, user)
    assert [e["name"] for e in merged.entries] == ["shared", "other", "solo"]
    # Only the user tier's 2 kept docs (solo + its chunk) were recounted, not all 5.
    assert calls == [2]
    monkeypatch.setattr(RT, "compute_bm25_stats", real)
    _assert_identical_to_recompute(merged)


def test_tier_with_stale_persisted_stats_falls_back_to_one_recompute():
    proj = _tier([("a", ["alpha", "beta"])], [(0, ["beta"])])
    user = _tier([("b", ["gamma"])], [])
    user.manifest["bm25"]["doc_len"] = [99]  # no longer describes the loaded docs
    assert RT._persisted_bm25(user) is None
    merged = _merge(proj, user)
    assert isinstance(merged.manifest["bm25"]["postings"], dict)  # the recompute path
    _assert_identical_to_recompute(merged)


def test_merged_postings_reads_like_the_dict_it_replaces():
    proj = _tier([("a", ["x", "y"]), ("b", ["y"])], [(0, ["z"])])
    user = _tier([("c", ["y", "w"])], [])
    stats = _merge(proj, user).manifest["bm25"]
    postings = stats["postings"]
    ref = B.compute_bm25_stats([["x", "y"], ["y"], ["y", "w"], ["z"]])["postings"]
    assert list(postings) == list(ref) and len(postings) == len(ref)
    assert postings["y"] == [[0, 1], [1, 1], [2, 1]]
    assert postings["z"] == [[3, 1]]  # the project chunk shifted past the user entry
    assert postings.get("nope", ()) == () and "nope" not in postings and "w" in postings
    with pytest.raises(KeyError):
        postings["nope"]
    with pytest.raises(TypeError):
        json.dumps(stats)  # never silently persisted half-built


_VOCAB = ["deploy", "build", "test", "render", "tabs", "gate"]  # small: forces negative IDF
_tokens = st.lists(st.sampled_from(_VOCAB), max_size=5)


@st.composite
def _tiers(draw):
    out = []
    for _ in range(draw(st.integers(min_value=2, max_value=3))):
        names = draw(st.lists(st.sampled_from(["m1", "m2", "m3", "m4", "m5"]), max_size=4, unique=True))
        entries = [(n, draw(_tokens)) for n in names]
        chunks = (
            [(draw(st.integers(0, len(entries) - 1)), draw(_tokens)) for _ in range(draw(st.integers(0, 4)))]
            if entries
            else []
        )
        out.append(_tier(entries, sorted(chunks, key=lambda c: c[0])))
    return out


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_tiers())
def test_property_merged_stats_always_equal_recompute(tiers):
    tiers = [t for t in tiers if len(t)]  # _fuse_recall_tiers never merges an empty tier
    if len(tiers) < 2:
        return
    _assert_identical_to_recompute(_merge(*tiers))


def _mem(name: str, description: str, body: str) -> str:
    return f'---\nname: {name}\ndescription: "{description}"\nmetadata:\n  type: feedback\n---\n{body}\n'


def test_fused_recall_ranks_identically_with_merged_or_recomputed_stats(tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    proj = str(tmp_path / "proj" / ".claude" / "memory")
    proj_idx = str(tmp_path / "proj" / ".claude" / ".memory-index")
    user = str(tmp_path / "usertier")
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", user)
    for d, items in (
        (proj, {
            "deploy-flow": ("railway deploy flow for the dashboard", "## Steps\n\nbuild then deploy to railway"),
            "render-gate": ("headless chrome render gate", "## Flakes\n\nthe render gate retries chrome twice"),
            "shared-slug": ("project copy about deploy gates", "deploy gates live in ci"),
        }),
        (user, {
            "shared-slug": ("user copy about deploy gates", "loses the collision"),
            "tabs-pref": ("indent with tabs", "## Why\n\ntabs render consistently in the gate output"),
        }),
    ):
        os.makedirs(d, exist_ok=True)
        for stem, (desc, body) in items.items():
            with open(os.path.join(d, f"{stem}.md"), "w", encoding="utf-8") as fh:
                fh.write(_mem(stem, desc, body))
    B.build_index(proj, proj_idx)
    B.build_index(user, B.default_index_dir(user))
    queries = ["deploy to railway", "render gate chrome", "tabs in gate output", "deploy gates"]

    def run():
        return [
            [(h["name"], h.get("corpus"), h.get("score")) for h in R.recall(q, k=5, memory_dir=proj, index_dir=proj_idx)]
            for q in queries
        ]

    merged = run()
    monkeypatch.setattr(RT, "_persisted_bm25", lambda li: None)  # force the recompute path
    assert run() == merged
    assert any(merged), "the fixture must actually recall something"


def test_frontmatter_uses_libyaml_when_available(monkeypatch):
    if not hasattr(yaml, "CSafeLoader"):
        pytest.skip("this PyYAML build ships no libyaml")
    seen = []
    real = yaml.load
    monkeypatch.setattr(yaml, "load", lambda s, Loader: seen.append(Loader) or real(s, Loader=Loader))
    assert P.parse_frontmatter("---\nname: a\n---\n") == {"name": "a"}
    assert seen == [yaml.CSafeLoader]


@pytest.mark.parametrize(
    "text",
    [
        '---\nname: a\ndescription: "quoted: colon"\nmetadata:\n  type: project\n  cited_paths: ["x.py", "y/z.js"]\n---\nbody\n',
        "---\nname: b\ndescription: plain — unicode ✓\nvalid_from: 2026-10-01\nlinks: [a, b]\n---\n",
        "---\nname: c\ndescription: >\n  folded\n  text\nmetadata:\n  edge_origin:\n    kind: dream\n---\n",
        "---\nname: [unclosed\n---\n",  # invalid YAML -> {}
        "---\n- just\n- a list\n---\n",  # not a mapping -> {}
    ],
)
def test_frontmatter_libyaml_and_pure_python_parse_identically(text, monkeypatch):
    fast = P.parse_frontmatter(text)
    monkeypatch.delattr(yaml, "CSafeLoader", raising=False)  # -> yaml.safe_load
    assert P.parse_frontmatter(text) == fast


def test_in_process_recall_main_leaves_gc_enabled(tmp_path, monkeypatch, capsys):
    # gc.disable() lives ONLY under `if __name__ == "__main__"`: the hook's one-shot process.
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    md = str(tmp_path / "m")
    os.makedirs(md)
    with open(os.path.join(md, "one.md"), "w", encoding="utf-8") as fh:
        fh.write(_mem("one", "widgets and gadgets", "body"))
    assert gc.isenabled()
    R.main(["--memory-dir", md, "--index-dir", str(tmp_path / "idx"), "widgets", "gadgets"])
    assert gc.isenabled()
