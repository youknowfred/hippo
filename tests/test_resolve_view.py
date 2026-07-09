"""GOV-1: contradiction inbox + /hippo:resolve — a standing, drainable conflict queue.

Every unresolved `contradicts` pair appears corpus-wide (not only when both sides
co-surface in one recall); /hippo:resolve renders per-item verdicts. Corpus-mutating
verdicts are ordinary git edits (exercised here as plain file edits); ONLY the
mark-not-conflicting verdict lands in the per-clone gitignored ledger. Nothing auto-picks
a winner — the module structurally has no corpus-write path (AST-pinned below).
"""

from __future__ import annotations

import ast
import inspect
import json
import os

import memory.resolve_view as RV
import memory.session_start as S


def _write(md, name, description, contradicts=None):
    os.makedirs(md, exist_ok=True)
    lines = ["---", f"name: {name}", f'description: "{description}"']
    if contradicts:
        lines.append(f"contradicts: [{', '.join(contradicts)}]")
    lines += ["---", "", f"body of {name}", ""]
    with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _conflicted_corpus(md):
    """new-api declares it contradicts old-api; bystander carries no typed edges."""
    _write(md, "old-api", "we call the v1 endpoint directly")
    _write(md, "new-api", "we stopped calling v1 — everything goes through the gateway", ["old-api"])
    _write(md, "bystander", "an unrelated fact")


# ---- the inbox (unresolved_contradictions) ------------------------------------------------ #
def test_inbox_lists_every_pair_without_co_surfacing(memory_dir, repo):
    """The acceptance bar: a standing queue — no recall, no co-surfacing, the pair is there."""
    _conflicted_corpus(memory_dir)
    inbox = RV.unresolved_contradictions(memory_dir, repo_root=repo)
    assert inbox == [{"pair": ["new-api", "old-api"], "declared_by": ["new-api"]}]


def test_mutual_declarations_collapse_to_one_pair(memory_dir, repo):
    _write(memory_dir, "a", "claim A", ["b"])
    _write(memory_dir, "b", "claim B", ["a"])
    inbox = RV.unresolved_contradictions(memory_dir, repo_root=repo)
    assert inbox == [{"pair": ["a", "b"], "declared_by": ["a", "b"]}]


def test_corpus_edit_is_the_resolving_verdict(memory_dir, repo):
    """A corpus-mutating verdict (here: dropping the contradicts declaration, the edit every
    supersede/scope/merge recipe ends with) drains the pair with NO ledger involved."""
    _conflicted_corpus(memory_dir)
    assert len(RV.unresolved_contradictions(memory_dir, repo_root=repo)) == 1
    _write(memory_dir, "new-api", "we stopped calling v1 — everything goes through the gateway")
    assert RV.unresolved_contradictions(memory_dir, repo_root=repo) == []


# ---- the ledger (mark-not-conflicting, the ONE corpus-preserving verdict) ------------------ #
def test_dismiss_drains_pair_and_persists_per_clone(memory_dir, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    _conflicted_corpus(memory_dir)
    res = RV.mark_not_conflicting("new-api", "old-api", repo)
    assert res["recorded"] is True and res["error"] is None
    assert res["pair"] == ["new-api", "old-api"]  # canonical order-free identity
    assert RV.unresolved_contradictions(memory_dir, repo_root=repo) == []
    # the ledger is a plain per-clone JSON under CLAUDE_PLUGIN_DATA, keyed by repo
    with open(res["ledger"], "r", encoding="utf-8") as fh:
        assert json.load(fh) == {"resolved": [["new-api", "old-api"]]}
    # the corpus itself is untouched — the edge is still declared in the file
    with open(os.path.join(memory_dir, "new-api.md"), "r", encoding="utf-8") as fh:
        assert "contradicts: [old-api]" in fh.read()


def test_dismiss_is_order_free_and_idempotent(memory_dir, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    _conflicted_corpus(memory_dir)
    RV.mark_not_conflicting("old-api", "new-api", repo)  # reversed order
    RV.mark_not_conflicting("new-api", "old-api", repo)  # repeat
    assert RV.read_resolved(repo) == {("new-api", "old-api")}
    assert RV.unresolved_contradictions(memory_dir, repo_root=repo) == []


def test_dismiss_scopes_to_its_own_pair(memory_dir, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    _conflicted_corpus(memory_dir)
    _write(memory_dir, "left", "left claim", ["right"])
    _write(memory_dir, "right", "right claim")
    RV.mark_not_conflicting("new-api", "old-api", repo)
    inbox = RV.unresolved_contradictions(memory_dir, repo_root=repo)
    assert inbox == [{"pair": ["left", "right"], "declared_by": ["left"]}]


def test_dismiss_without_plugin_data_is_a_loud_no(memory_dir, repo):
    """No durable per-clone home -> the verdict is REFUSED legibly (inv3), never silently
    forgotten; the pair stays in the inbox."""
    _conflicted_corpus(memory_dir)
    res = RV.mark_not_conflicting("new-api", "old-api", repo)
    assert res["recorded"] is False
    assert "CLAUDE_PLUGIN_DATA" in res["error"]
    assert len(RV.unresolved_contradictions(memory_dir, repo_root=repo)) == 1


def test_ledger_is_keyed_per_clone(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    clone_a = str(tmp_path / "clone-a")
    clone_b = str(tmp_path / "clone-b")
    os.makedirs(clone_a), os.makedirs(clone_b)
    assert RV.ledger_path(clone_a) != RV.ledger_path(clone_b)
    RV.mark_not_conflicting("x", "y", clone_a)
    assert RV.read_resolved(clone_a) == {("x", "y")}
    assert RV.read_resolved(clone_b) == set()  # a dismissal never leaks across clones


def test_dismiss_rejects_self_and_empty():
    assert RV.mark_not_conflicting("a", "a", ".")["error"]
    assert RV.mark_not_conflicting("", "b", ".")["error"]


# ---- nothing auto-resolves: the module has no corpus-write path (structural pin) ----------- #
def test_module_has_no_corpus_write_path():
    """inv4 pinned structurally: resolve_view imports no corpus writer and calls none of the
    corpus-mutating primitives — every such verdict routes through ordinary agent edits/
    reconsolidate in the SKILL, outside this module."""
    tree = ast.parse(inspect.getsource(RV))
    imported, called = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute):
                called.add(fn.attr)
            elif isinstance(fn, ast.Name):
                called.add(fn.id)
    assert not any("new_memory" in m or "reconsolidate" in m for m in imported)
    for writer in ("write_memory", "semantic_reverify", "add_typed_relation", "set_invalid_after"):
        assert writer not in called


# ---- the SessionStart producer ------------------------------------------------------------- #
def test_producer_lists_pairs_and_routes_to_resolve(memory_dir, repo):
    _conflicted_corpus(memory_dir)
    out = S.contradiction_inbox_producer(memory_dir, repo)
    assert out is not None
    assert out.startswith("⚖ Contradiction inbox — 1 unresolved")
    assert "/hippo:resolve" in out and "nothing auto-picks a winner" in out
    assert "new-api ⇄ old-api" in out


def test_producer_silent_when_inbox_empty(memory_dir, repo):
    _write(memory_dir, "solo", "no conflicts here")
    assert S.contradiction_inbox_producer(memory_dir, repo) is None


def test_producer_silent_after_dismissal(memory_dir, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    _conflicted_corpus(memory_dir)
    RV.mark_not_conflicting("new-api", "old-api", repo)
    assert S.contradiction_inbox_producer(memory_dir, repo) is None


def test_producer_counts_radar_pairs_but_does_not_reprint_them(memory_dir, repo, monkeypatch):
    """T2 boundary: a pair the rules_conflict producer already surfaced this session is
    counted in the header but not re-printed (no double nag)."""
    import memory.rules_plane as RP

    _conflicted_corpus(memory_dir)
    _write(memory_dir, "left", "left claim", ["right"])
    _write(memory_dir, "right", "right claim")
    monkeypatch.setattr(
        RP,
        "conflict_radar",
        lambda md, rr, **kw: {
            "authority_gaps": [],
            "edge_conflicts": [
                {"name": "old-api", "relation": "contradicts", "by": "new-api", "cited_by": ["CLAUDE.md"]}
            ],
            "gate_met": True,
            "distinct_sessions": 5,
        },
    )
    out = S.contradiction_inbox_producer(memory_dir, repo)
    assert out is not None
    assert "2 unresolved contradiction pair(s)" in out  # counted...
    assert "new-api ⇄ old-api" not in out  # ...but not re-printed
    assert "left ⇄ right" in out
    assert "1 pair(s) already shown by the rule↔memory conflict radar above" in out


def test_producer_wired_into_producers():
    assert any(label == "contradiction_inbox" for label, _fn in S.PRODUCERS)


# ---- the CLI (/hippo:resolve's surface) ---------------------------------------------------- #
def test_cli_list_names_pairs_and_descriptions(memory_dir, repo, capsys):
    _conflicted_corpus(memory_dir)
    rc = RV.main(["--list", "--memory-dir", memory_dir, "--repo-root", repo])
    out = capsys.readouterr().out
    assert rc == 0
    assert "1 unresolved contradiction pair(s)" in out
    assert "new-api ⇄ old-api" in out and "(declared by: new-api)" in out
    assert "we call the v1 endpoint directly" in out  # both sides' descriptions shown
    assert "everything goes through the gateway" in out


def test_cli_dismiss_then_list_is_empty(memory_dir, repo, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    _conflicted_corpus(memory_dir)
    rc = RV.main(["--dismiss", "new-api", "old-api", "--memory-dir", memory_dir, "--repo-root", repo])
    assert rc == 0
    assert "recorded : new-api ⇄ old-api" in capsys.readouterr().out
    RV.main(["--list", "--memory-dir", memory_dir, "--repo-root", repo])
    assert "inbox is empty" in capsys.readouterr().out


def test_cli_dismiss_without_plugin_data_prints_error(memory_dir, repo, capsys):
    _conflicted_corpus(memory_dir)
    rc = RV.main(["--dismiss", "new-api", "old-api", "--memory-dir", memory_dir, "--repo-root", repo])
    assert rc == 0
    assert "error    : CLAUDE_PLUGIN_DATA is unset" in capsys.readouterr().out


def test_bogus_dirs_never_raise(tmp_path):
    bogus = str(tmp_path / "nope")
    assert RV.unresolved_contradictions(bogus, repo_root=bogus) == []
    assert S.contradiction_inbox_producer(bogus, bogus) is None
    assert RV.main(["--list", "--memory-dir", bogus, "--repo-root", bogus]) == 0
