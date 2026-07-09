"""RCH-3 — decision-chain replay: the typed graph walked into an ordered narrative.

Hermetic chain corpus (no index needed — ``build_graph`` falls back to a full corpus
read). Pins the acceptance criteria: an ordered supersession/refinement narrative from
the edges the corpus ALREADY stores (no new edge storage), contradicts as un-traversed
branch-point annotations, per-node chronology from the stamped ``source_commit_time``,
retirement boundaries surfaced, and ONE builder behind both surfaces (MCP tool tests
live in test_mcp_server.py; the recall_view front-end is pinned here).
"""

from __future__ import annotations

import inspect
import os

from memory import history as H
from memory import recall_view as V


def _write(md: str, name: str, description: str, *, sct=None, extra_top="", extra_meta=""):
    os.makedirs(md, exist_ok=True)
    meta = f"  type: project\n{extra_meta}"
    if sct is not None:
        meta += f"  source_commit_time: {sct}\n"
    text = (
        f'---\nname: {name}\ndescription: "{description}"\n{extra_top}'
        f"metadata:\n{meta}---\n\nbody of {name}\n"
    )
    with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write(text)


def _chain(tmp_path) -> str:
    """api-v1 (2001, retired) <- refines <- api-v1-retry (2017) <- supersedes <- api-v2
    (2023); api-alt contradicts api-v2 (a fork, not lineage)."""
    md = str(tmp_path / "mem")
    _write(md, "api-v1", "the original api approach", sct=1000000000,
           extra_meta="  invalid_after: 2026-01-01\n")
    _write(md, "api-v1-retry", "v1 with retries", sct=1500000000,
           extra_top='refines: ["api-v1"]\n')
    _write(md, "api-v2", "the v2 api approach", sct=1700000000,
           extra_top='supersedes: ["api-v1-retry"]\n')
    _write(md, "api-alt", "a competing take", sct=1600000000,
           extra_top='contradicts: ["api-v2"]\n')
    _write(md, "unrelated", "something else entirely")
    return md


# --------------------------------------------------------------------------- #
# decision_chain — the builder
# --------------------------------------------------------------------------- #
def test_chain_collects_both_directions_transitively_and_chronologically(tmp_path):
    chain = H.decision_chain("api-v1", _chain(tmp_path))
    assert chain is not None and chain["seed"] == "api-v1"
    # transitive closure from the OLD end reaches the newest successor; unrelated and
    # the contradicts-fork stay out.
    assert [n["name"] for n in chain["nodes"]] == ["api-v1", "api-v1-retry", "api-v2"]
    assert chain["edges"] == [
        {"from": "api-v1-retry", "relation": "refines", "to": "api-v1"},
        {"from": "api-v2", "relation": "supersedes", "to": "api-v1-retry"},
    ]
    # same closure from the NEW end (both directions walk)
    from_new = H.decision_chain("api-v2", _chain(tmp_path))
    assert [n["name"] for n in from_new["nodes"]] == ["api-v1", "api-v1-retry", "api-v2"]


def test_contradicts_is_a_branch_annotation_never_a_traversal(tmp_path):
    chain = H.decision_chain("api-v1", _chain(tmp_path))
    names = [n["name"] for n in chain["nodes"]]
    assert "api-alt" not in names  # a fork is not lineage
    v2 = next(n for n in chain["nodes"] if n["name"] == "api-v2")
    assert v2["contradicted_by"] == ["api-alt"]


def test_chain_surfaces_invalid_after_and_times(tmp_path):
    chain = H.decision_chain("api-v2", _chain(tmp_path))
    v1 = next(n for n in chain["nodes"] if n["name"] == "api-v1")
    assert str(v1["invalid_after"]).startswith("2026-01-01")
    assert v1["time"] == 1000000000


def test_chain_none_on_unresolvable_or_empty(tmp_path):
    md = _chain(tmp_path)
    assert H.decision_chain("ghost", md) is None
    assert H.decision_chain("x", str(tmp_path / "empty")) is None


# --------------------------------------------------------------------------- #
# render_decision_history — the one narrative both surfaces show
# --------------------------------------------------------------------------- #
def test_render_orders_and_annotates_the_narrative(tmp_path):
    text = H.render_decision_history("api-v1", _chain(tmp_path))
    lines = text.splitlines()
    assert lines[0] == "decision history for 'api-v1' (3 memories, oldest first):"
    assert lines[1] == (
        "  • chose api-v1 (2001-09) [retired — invalid_after 2026-01-01]"
    )
    assert lines[2] == "  • api-v1-retry (2017-07) — refines api-v1"
    assert lines[3] == (
        "  • api-v2 (2023-11) — supersedes api-v1-retry "
        "[branch point — contradicted by api-alt]"
    )
    # api-v1-retry was superseded, api-v1 is retired -> only api-v2 stands.
    assert lines[4] == "  standing today: api-v2"


def test_render_undated_node_is_honest(tmp_path):
    md = str(tmp_path / "mem")
    _write(md, "old", "the base decision")  # no source_commit_time
    _write(md, "new", "the replacement", sct=1700000000,
           extra_top='supersedes: ["old"]\n')
    text = H.render_decision_history("new", md)
    assert "chose old (date unknown)" in text
    assert "standing today: new" in text


def test_render_degrades_without_lineage_or_resolution(tmp_path):
    md = _chain(tmp_path)
    assert "no supersedes/refines edges touch" in H.render_decision_history("unrelated", md)
    assert "no memory resolves" in H.render_decision_history("ghost", md)


# --------------------------------------------------------------------------- #
# The recall_view front-end + the import-direction pin
# --------------------------------------------------------------------------- #
def test_recall_view_history_flag_renders_the_same_narrative(tmp_path, capsys):
    md = _chain(tmp_path)
    rc = V.main(["--history", "api-v1", "--memory-dir", md])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.rstrip("\n") == H.render_decision_history("api-v1", md)


def test_history_module_never_imports_the_server():
    # One-directional dependency: mcp_server imports history, never the reverse — so the
    # recall_view front-end can import the builder without ever pulling in the server.
    assert "mcp_server" not in inspect.getsource(H)
