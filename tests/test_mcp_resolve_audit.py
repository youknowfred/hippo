"""INV-4: the resolve + audit MCP tools — the two nudge-routed verbs' second surface.

Scope RATIFIED 2026-07-16: these two only. The contradiction-inbox nudge routes to
/hippo:resolve and the old-invalidation horizon to /hippo:audit — both hooks fire on
Desktop, where neither verb had a path (INT-19's honest note still described a dead
end). resolve mirrors the reconsolidate tool's per-item shape: list pairs, then ONE
verdict per call (keep_one / scope_both / merge / not_conflicting) — nothing auto-picks
a winner, and the two-write verdicts ride the COR-16 rollback discipline
(``provenance.restore_file_bytes``). audit is the read-only report/material producer;
judgment stays with the skill on both surfaces.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from memory import mcp_server as M

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _call(tool, arguments):
    resp = M.handle_request(
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
         "params": {"name": tool, "arguments": arguments}}
    )
    return resp["result"]["content"][0]["text"]


def _repo(tmp_path, monkeypatch):
    root = str(tmp_path / "repo")
    md = os.path.join(root, ".claude", "memory")
    os.makedirs(md)
    with open(os.path.join(root, "app.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(["git", "-C", root, "add", "-A"], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "seed"], check=True, env=_GIT_ENV)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    return root, md


def _mem(md, name, *, contradicts=None, body="Body."):
    edge = f"  contradicts: [{json.dumps(contradicts)}]\n" if contradicts else ""
    path = os.path.join(md, f"{name}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            f'---\nname: {name}\ndescription: "d {name}"\nmetadata:\n'
            f"  type: project\n{edge}---\n{body}\n"
        )
    return path


def _snap(*paths):
    return {p: (open(p, "rb").read() if os.path.exists(p) else None) for p in paths}


# --------------------------------------------------------------------------- #
# resolve — inbox listing
# --------------------------------------------------------------------------- #
def test_resolve_inbox_lists_pairs_and_empty_is_fine(tmp_path, monkeypatch):
    _root, md = _repo(tmp_path, monkeypatch)
    out = _call("resolve", {})
    assert "empty" in out.lower()

    _mem(md, "use_x", contradicts="stop_x")
    _mem(md, "stop_x")
    out = _call("resolve", {})
    assert "stop_x ⇄ use_x" in out or "use_x ⇄ stop_x" in out
    assert "verdict" in out  # the listing routes to the per-pair verdict shape


def test_resolve_is_gated_like_the_pack_tools(tmp_path, monkeypatch):
    _root, _md = _repo(tmp_path, monkeypatch)
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    out = _call("resolve", {})
    assert "untrusted" in out and "SEC-1" in out


def test_audit_is_gated_like_the_pack_tools(tmp_path, monkeypatch):
    _root, _md = _repo(tmp_path, monkeypatch)
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    out = _call("audit", {})
    assert "untrusted" in out and "SEC-1" in out


# --------------------------------------------------------------------------- #
# resolve — the four verdicts, one pair per call
# --------------------------------------------------------------------------- #
def test_keep_one_demotes_supersedes_and_drops_the_declaration(tmp_path, monkeypatch):
    from memory.links import parse_typed_relations
    from memory.provenance import parse_frontmatter

    _root, md = _repo(tmp_path, monkeypatch)
    winner = _mem(md, "use_y")
    loser = _mem(md, "use_x", contradicts="use_y")
    out = _call(
        "resolve",
        {"action": "verdict", "verdict": "keep_one", "winner": "use_y", "loser": "use_x"},
    )
    assert "✔" in out
    with open(winner, encoding="utf-8") as fh:
        w_rel = parse_typed_relations(parse_frontmatter(fh.read()))
    assert "use_x" in w_rel.get("supersedes", [])
    with open(loser, encoding="utf-8") as fh:
        l_fm = parse_frontmatter(fh.read())
    l_rel = parse_typed_relations(l_fm)
    assert "use_y" not in l_rel.get("contradicts", [])  # the settled edge is gone
    meta = l_fm.get("metadata") or {}
    assert l_fm.get("invalid_after") or meta.get("invalid_after")  # demote landed
    assert "empty" in _call("resolve", {}).lower()  # the inbox drained


def test_keep_one_rolls_back_the_declaration_drop_when_the_demote_fails(
    tmp_path, monkeypatch
):
    """The COR-16 discipline: write #1 (dropping the settled contradicts edge) must
    come back OUT when write #2 (the demote+supersede chain) refuses."""
    _root, md = _repo(tmp_path, monkeypatch)
    _mem(md, "use_y")
    loser = _mem(md, "use_x", contradicts="use_y")
    before = _snap(loser)

    import memory.reconsolidate as R

    def _refuse(*a, **k):
        return {"error": "injected refusal", "invalidated": False, "edge_written": False,
                "cleared": False, "invalid_after": None, "logged": False,
                "name": "use_x", "outcome": "demote"}

    monkeypatch.setattr(R, "semantic_reverify", _refuse)
    out = _call(
        "resolve",
        {"action": "verdict", "verdict": "keep_one", "winner": "use_y", "loser": "use_x"},
    )
    assert "injected refusal" in out and "rolled back" in out
    assert _snap(loser) == before  # byte-exact restore of the declaring file


def test_scope_both_drops_the_declaration_only_after_the_agent_scoped_bodies(
    tmp_path, monkeypatch
):
    from memory.links import parse_typed_relations
    from memory.provenance import parse_frontmatter

    _root, md = _repo(tmp_path, monkeypatch)
    a = _mem(md, "backend_x", contradicts="frontend_y")
    _mem(md, "frontend_y")
    out = _call(
        "resolve",
        {"action": "verdict", "verdict": "scope_both", "a": "backend_x", "b": "frontend_y"},
    )
    assert "✔" in out and "scope" in out.lower()
    with open(a, encoding="utf-8") as fh:
        rel = parse_typed_relations(parse_frontmatter(fh.read()))
    assert "frontend_y" not in rel.get("contradicts", [])
    assert "empty" in _call("resolve", {}).lower()


def test_merge_verdict_supersedes_after_the_fold(tmp_path, monkeypatch):
    from memory.links import parse_typed_relations
    from memory.provenance import parse_frontmatter

    _root, md = _repo(tmp_path, monkeypatch)
    survivor = _mem(md, "fact_full", contradicts="fact_half")
    _mem(md, "fact_half")
    out = _call(
        "resolve",
        {"action": "verdict", "verdict": "merge", "winner": "fact_full", "loser": "fact_half"},
    )
    assert "✔" in out
    with open(survivor, encoding="utf-8") as fh:
        fm = parse_frontmatter(fh.read())
    rel = parse_typed_relations(fm)
    assert "fact_half" in rel.get("supersedes", [])
    assert "fact_half" not in rel.get("contradicts", [])


def test_not_conflicting_lands_in_the_ledger_and_keeps_the_edge(tmp_path, monkeypatch):
    from memory.links import parse_typed_relations
    from memory.provenance import parse_frontmatter

    _root, md = _repo(tmp_path, monkeypatch)
    a = _mem(md, "left", contradicts="right")
    _mem(md, "right")
    before = _snap(a)
    out = _call(
        "resolve",
        {"action": "verdict", "verdict": "not_conflicting", "a": "left", "b": "right"},
    )
    assert "✔" in out and "ledger" in out
    assert _snap(a) == before  # the ONE corpus-preserving verdict: files untouched
    with open(a, encoding="utf-8") as fh:
        rel = parse_typed_relations(parse_frontmatter(fh.read()))
    assert "right" in rel.get("contradicts", [])  # the edge stays as documentation
    assert "empty" in _call("resolve", {}).lower()  # but the inbox stops nagging


def test_verdict_refuses_a_pair_with_no_edge(tmp_path, monkeypatch):
    _root, md = _repo(tmp_path, monkeypatch)
    _mem(md, "one")
    _mem(md, "two")
    out = _call(
        "resolve",
        {"action": "verdict", "verdict": "keep_one", "winner": "one", "loser": "two"},
    )
    assert "refused" in out.lower() or "no contradicts" in out.lower()


# --------------------------------------------------------------------------- #
# audit — read-only report material
# --------------------------------------------------------------------------- #
def test_audit_returns_material_and_writes_nothing(tmp_path, monkeypatch):
    from memory import trust

    _root, md = _repo(tmp_path, monkeypatch)
    _mem(md, "alpha")
    _mem(md, "beta")
    fp_before = trust.corpus_fingerprint(md).get("digest")
    listing_before = sorted(os.listdir(md))
    out = _call("audit", {"skip_eval": True})
    material = json.loads(out[out.index("{"):])
    assert material["corpus_size"] == 2
    assert "stale" in material and "worklist" in material and "joins" in material
    assert sorted(os.listdir(md)) == listing_before  # zero corpus writes
    assert trust.corpus_fingerprint(md).get("digest") == fp_before
    assert "read-only" in out.split("{")[0].lower()  # the header says what this is
