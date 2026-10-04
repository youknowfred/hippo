"""RWY-3: corpus read-modify-writes are compare-and-swap, and new_memory never half-writes.

The two repros came first (the QA-sweep rule): both failed on the pre-RWY-3 tree.
  - lost update: a second writer lands between a floor-pointer edit's read and its write,
    and the first writer's whole-document replace erased it;
  - partial write: new_memory created ``<name>.md`` and only then backfilled its citation
    provenance, so a crash between the two left a memory with no ``source_commit``.
"""

from __future__ import annotations

import ast
import os

import pytest

from memory import atomic as A
from memory import new_memory as NM
from memory.build_index import parse_frontmatter

_FLOOR = (
    "# Agent Memory\n\n## User\n- [Alpha](alpha.md) — a\n\n"
    "## Working Style & Process Feedback\n"
)


def _interleave_once(monkeypatch, path, edit):
    """Run ``edit(path)`` (another writer) the first time a temp file is made for ``path``'s
    directory — after the caller read the document, before its rename lands."""
    real = A.tempfile.mkstemp
    fired = {"n": 0}

    def mkstemp(*args, **kwargs):
        d = kwargs.get("dir")
        if not fired["n"] and d and os.path.samefile(d, os.path.dirname(path)):
            fired["n"] += 1
            edit(path)
        return real(*args, **kwargs)

    monkeypatch.setattr(A.tempfile, "mkstemp", mkstemp)
    return fired


def _other_writer_adds_zed(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace("- [Alpha](alpha.md) — a\n", "- [Alpha](alpha.md) — a\n- [Zed](zed.md) — z\n"))


def test_repro_interleaved_floor_writers_lose_no_pointer(tmp_path, monkeypatch):
    md = tmp_path / "memory"
    md.mkdir()
    floor = md / "MEMORY.md"
    floor.write_text(_FLOOR, encoding="utf-8")
    fired = _interleave_once(monkeypatch, str(floor), _other_writer_adds_zed)

    res = NM._append_floor_pointer(str(md), "## User", "bravo", "Bravo", "b")

    assert fired["n"] == 1 and res["status"] == "appended"
    text = floor.read_text(encoding="utf-8")
    assert "(zed.md)" in text, "the interleaved writer's pointer was lost"
    assert "(bravo.md)" in text and "(alpha.md)" in text


def test_repro_crash_between_create_and_backfill_leaves_no_half_memory(repo, monkeypatch):
    from memory import provenance

    md = os.path.join(repo, ".claude", "memory")
    os.makedirs(md)

    def crash(*_a, **_k):
        raise KeyboardInterrupt("simulated crash mid write+backfill")

    monkeypatch.setattr(provenance, "backfill_file", crash)
    with pytest.raises(KeyboardInterrupt):
        NM.write_memory("half", "a note", "project", "body", memory_dir=md, repo_root=repo,
                        no_links=True)
    path = os.path.join(md, "half.md")
    if os.path.exists(path):
        fm = parse_frontmatter(open(path, encoding="utf-8").read())
        assert (fm.get("metadata") or fm).get("source_commit"), (
            "a memory was left on disk without its citation provenance"
        )
    assert not [f for f in os.listdir(md) if f.endswith(".md")], "a half-written memory remained"


# --------------------------------------------------------------------------- #
# The registry: every corpus read-modify-write goes through compare-and-swap.
# --------------------------------------------------------------------------- #
_PKG = os.path.dirname(os.path.abspath(A.__file__))
_CAS = {"write_text_cas", "update_text_cas"}
_PLAIN = {"write_text_atomic", "write_json_atomic", "write_bytes_atomic"}

# (module, function) that read a corpus document, edit it, and write it back.
CORPUS_RMW_SITES = {
    ("new_memory_floor", "_append_floor_pointer"),  # MEMORY.md pointer insert
    ("new_memory_floor", "_remove_floor_pointer"),
    ("new_memory_floor", "_ensure_tier_floor"),  # create-only-while-absent (expected=None)
    ("provenance", "backfill_file"),  # citation provenance
    ("provenance", "reverify_file"),  # reverify verdicts
    ("provenance", "heal_empty_baselines"),
    ("provenance_format", "_write_marker_keys"),  # .format axes
    ("staleness", "set_invalid_after"),  # demote verdicts
    ("links", "add_typed_relation"),  # typed edges
    ("links", "remove_typed_relation"),
    ("dream", "_apply_one"),  # dream edges
    ("dream_apply", "_undo_one_edge"),
    ("dream_generate", "_set_confidence"),
    ("dream_generate", "_set_cited_paths"),
    ("packs", "pack_update_item"),  # three-way pack merge over local edits
    ("eval_recall", "confirm_hard_set_row"),  # the tracked hard-set fixture append
}

# Plain atomic writers that are NOT corpus read-modify-writes, each with why.
NON_CORPUS_SITES = {
    ("eval_fixtures", "_append_draft_rows"): "gitignored drafts queue",
    ("eval_recall", "draft_abstention_fixtures"): "gitignored drafts queue",
    ("eval_recall", "confirm_hard_set_row"): "its drafts-queue drop (the fixture write is CAS)",
    ("eval_recall", "write_baseline"): "whole-document pin, written from one run",
    ("eval_floor", "write_floor_sweep"): "derived report",
    ("salience_eval", "write_report"): "derived report",
    ("outcome_prior_eval", "write_report"): "derived report",
    ("init_project", "_copy_if_absent"): "seed copy of a packaged file",
    ("init_project", "init_project"): "fresh .format stamp at init",
    ("interview", "_write_state"): "telemetry bookkeeping",
    ("jit", "write_touch_cache"): "derived cache",
    ("jit", "_write_state"): "telemetry bookkeeping",
    ("lint_floor", "observe_floor_edit"): "session sentinel",
    ("packs", "_write_lockfile"): "pack lockfile, rebuilt whole by the pack verbs",
    ("presence", "_write_doc"): "per-session presence doc",
    ("promote_rule", "main"): "writes a new rule file",
    ("provenance", "restore_file_bytes"): "restores snapshot bytes (a rollback, not an edit)",
    ("registry", "register_project"): "machine registry, outside the corpus",
    ("registry", "deregister_project"): "machine registry, outside the corpus",
    ("registry", "prune_dead"): "machine registry, outside the corpus",
    ("sleep", "_write_state"): "telemetry bookkeeping",
    ("sleep", "_write_report"): "derived report",
    ("telemetry_rollup", "_update"): "telemetry, held under flock",
    ("telemetry_rollup", "_append_finalized"): "telemetry, held under flock",
    ("telemetry_rollup", "_drain_spool"): "telemetry, held under flock",
    ("trust", "_write_registry_doc"): "machine trust registry, outside the corpus",
}


def _calls_by_function():
    out = {}
    for fname in sorted(os.listdir(_PKG)):
        if not fname.endswith(".py") or fname == "atomic.py":
            continue
        tree = ast.parse(open(os.path.join(_PKG, fname), encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names = set()
                for c in ast.walk(node):
                    if isinstance(c, ast.Call):
                        f = c.func
                        names.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None))
                out.setdefault((fname[:-3], node.name), set()).update(names)
    return out


def test_every_corpus_rmw_site_uses_compare_and_swap():
    calls = _calls_by_function()
    missing = sorted(site for site in CORPUS_RMW_SITES if not (calls.get(site, set()) & _CAS))
    assert not missing, f"corpus read-modify-write site(s) not on CAS: {missing}"


def test_every_plain_atomic_writer_is_classified():
    calls = _calls_by_function()
    plain = {site for site, names in calls.items() if names & _PLAIN}
    unclassified = sorted(plain - set(NON_CORPUS_SITES))
    assert not unclassified, (
        f"plain (last-writer-wins) atomic write(s) not classified: {unclassified} — a corpus "
        "read-modify-write goes through atomic.write_text_cas/update_text_cas (RWY-3); any "
        "other writer is listed in NON_CORPUS_SITES with why"
    )
    stale = sorted(set(NON_CORPUS_SITES) - plain)
    assert not stale, f"NON_CORPUS_SITES entries with no plain atomic write left: {stale}"


def test_cas_write_refuses_a_target_that_changed(tmp_path):
    p = tmp_path / "doc.md"
    p.write_text("one\n", encoding="utf-8")
    _text, token = A.read_text_cas(str(p))
    p.write_text("two\n", encoding="utf-8")
    with pytest.raises(A.CasConflict):
        A.write_text_cas(str(p), "three\n", token)
    assert p.read_text(encoding="utf-8") == "two\n"
    assert not [f for f in os.listdir(tmp_path) if ".tmp." in f]  # no temp left behind


def test_cas_create_only_while_absent(tmp_path):
    p = tmp_path / "new.md"
    A.write_text_cas(str(p), "first\n", None)
    with pytest.raises(A.CasConflict):
        A.write_text_cas(str(p), "second\n", None)
    assert p.read_text(encoding="utf-8") == "first\n"
