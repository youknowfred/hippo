"""INV-3: the crash-fault harness — every atomic-write call site, torn once, on contract.

SEC-19/COR-17/COR-18 made the write pattern uniform (unique-tmp + ``os.replace``;
COR-16 rollbacks on the two-write chains), which makes crash-safety testable as a
CLASS instead of at whichever sites a sweep happened to probe:

  - ``_discover_atomic_call_sites`` finds every ``write_*_atomic`` caller in
    ``plugin/memory`` by AST; the set must EQUAL the ``CRASH_CONTRACT`` registration
    below — a new writer that never declared its crash class fails the lane by name.
  - the in-process fault lane tears each registered site once (the primitive raises
    at exactly that caller, mid-"write") and asserts the site's declared class:

      intact      — the target keeps its prior bytes; the operation may degrade
                    silently BY DESIGN (a documented best-effort lane).
      detected    — prior bytes kept AND the failure is loud: named in the result
                    envelope, a False return, or a propagated exception.
      rolled_back — prior bytes kept AND the chain's write #1 was restored
                    byte-exact (the COR-16 chains: demote+supersede, dedup-merge,
                    dream refines, pack-update lockfile), loudly.

  - a small subprocess lane (``@pytest.mark.slow``, deselected by default like every
    wall-clock lane) SIGKILLs pack_extract and build_index mid-write and asserts the
    recovery story: a re-run heals, or the verb refuses with its documented message.

STABILITY.md's "Crash safety" section is asserted against this registration
(``test_stability_doc_matches_the_contract``) so the published contract and the
tested one cannot drift apart.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import signal
import subprocess
import sys

import pytest

from memory import atomic as A

_MEMORY_PKG = os.path.dirname(os.path.abspath(A.__file__))
_PLUGIN_ROOT = os.path.dirname(_MEMORY_PKG)
_ATOMIC_NAMES = {"write_text_atomic", "write_json_atomic", "write_bytes_atomic"}

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


# --------------------------------------------------------------------------- #
# The registration: every atomic-write call site's declared crash class.
# (module, enclosing function) -> tuple of classes the scenarios below exercise.
# A site reached by more than one chain declares every class it must honor.
# --------------------------------------------------------------------------- #
CRASH_CONTRACT = {
    ("dream", "_apply_one"): ("detected", "rolled_back"),  # bridge write; refines chain write #2
    ("dream_generate", "_set_confidence"): ("detected",),
    ("dream_generate", "_set_cited_paths"): ("detected",),
    ("eval_recall", "draft_abstention_fixtures"): ("detected",),
    ("eval_fixtures", "_append_draft_rows"): ("detected",),  # TMB-3/4: the ONE drafts-queue append (both synthesizers route through it)
    ("eval_recall", "confirm_hard_set_row"): ("detected",),  # both writes; each byte-complete
    ("eval_recall", "write_baseline"): ("detected",),  # MSR-1 pin: error named, no torn baseline
    ("eval_recall", "write_floor_sweep"): ("detected",),  # GRF-3: error named, no torn sweep report
    ("salience_eval", "write_report"): ("detected",),  # MSR-5: error named, no torn A/B evidence
    ("init_project", "_copy_if_absent"): ("detected",),  # seed copy: raises; no partial file
    ("init_project", "init_project"): ("detected",),  # fresh .format stamp
    ("interview", "_write_state"): ("detected",),  # a lost decline is NAMED, never pretended recorded
    ("jit", "write_touch_cache"): ("detected",),  # SessionStart caller sees False, never a torn map
    ("jit", "_write_state"): ("intact",),  # session bookkeeping: silent by design; reminder still emits
    ("links", "add_typed_relation"): ("detected", "rolled_back"),  # demote+supersede write #2
    ("links", "remove_typed_relation"): ("detected", "rolled_back"),  # resolve's declaration drop; scope_both 2-file chain
    ("new_memory", "_ensure_tier_floor"): ("intact",),  # opportunistic skeleton: silent by design
    ("new_memory", "_append_floor_pointer"): ("detected",),  # floor outcome dict names the failure
    ("new_memory", "_remove_floor_pointer"): ("detected",),
    ("packs", "_write_lockfile"): ("detected", "rolled_back"),  # install: re-run adopts; update: file restored
    ("packs", "pack_update_item"): ("detected",),  # ours-replacement write
    ("promote_rule", "main"): ("detected",),
    ("provenance_format", "_write_marker_keys"): ("detected",),  # returns False
    ("provenance", "restore_file_bytes"): ("detected",),  # returns the error string
    ("provenance", "backfill_file"): ("detected",),
    ("provenance", "heal_empty_baselines"): ("detected",),  # RCH-9: named in `failed`
    ("provenance", "reverify_file"): ("detected",),
    ("registry", "register_project"): ("detected",),  # returns False
    ("registry", "deregister_project"): ("detected",),
    ("registry", "prune_dead"): ("detected",),  # RCH-11: ok=False; prior doc intact
    ("sleep", "_write_report"): ("detected",),  # report still prints; the miss is named
    ("sleep", "_write_state"): ("detected",),  # a lost run-stamp is named on stdout
    ("staleness", "set_invalid_after"): ("detected", "rolled_back"),  # dedup-merge write #2
    ("trust", "_write_registry_doc"): ("detected",),  # mark_trusted returns False
}


def _discover_atomic_call_sites():
    found = set()
    for fname in sorted(os.listdir(_MEMORY_PKG)):
        if not fname.endswith(".py") or fname == "atomic.py":
            continue
        with open(os.path.join(_MEMORY_PKG, fname), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        stack = []

        def walk(node):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                stack.append(node.name)
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.Call):
                    f = child.func
                    n = f.attr if isinstance(f, ast.Attribute) else (
                        f.id if isinstance(f, ast.Name) else None
                    )
                    if n in _ATOMIC_NAMES:
                        found.add((fname[:-3], stack[-1] if stack else "<module>"))
                walk(child)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                stack.pop()

        walk(tree)
    return found


def test_every_atomic_writer_is_registered():
    discovered = _discover_atomic_call_sites()
    unregistered = sorted(discovered - set(CRASH_CONTRACT))
    assert not unregistered, (
        f"atomic-write call site(s) with NO declared crash class: {unregistered} — "
        "register each in tests/test_crash_faults.py::CRASH_CONTRACT with a tear "
        "scenario, and keep STABILITY.md's crash-safety section honest (INV-3)"
    )
    stale = sorted(set(CRASH_CONTRACT) - discovered)
    assert not stale, f"CRASH_CONTRACT entries with no live call site (prune them): {stale}"


# --------------------------------------------------------------------------- #
# Tear machinery: the primitive raises at exactly one registered caller.
# --------------------------------------------------------------------------- #
class _Tear(OSError):
    pass


# Captured ONCE at import: a scenario that re-arms (e.g. to reach a second write via
# ``nth``) must wrap the real primitives, not its own previous wrapper.
_REAL_ATOMICS = {name: getattr(A, name) for name in _ATOMIC_NAMES}


def _arm(monkeypatch, module: str, func: str, *, nth: int = 1):
    """Patch the atomic primitives to raise when ``module.func`` is on the stack.

    Deferred imports (``from .atomic import write_text_atomic`` inside functions)
    re-resolve the module attribute per call, so patching ``memory.atomic`` reaches
    every caller. ``nth`` skips the first ``nth-1`` matching writes (a function with
    two sequential writes gets its second torn too)."""
    state = {"seen": 0}
    real = _REAL_ATOMICS  # the true primitives — re-arming must never stack wrappers

    def _match():
        # FRAME-PRECISE: the tear belongs to the NEAREST plugin/memory frame outside
        # atomic.py — the registered call site itself. Anywhere-on-stack matching would
        # also tear a chain's inner writes and its own rollback (their stacks still
        # contain the outer function), which is exactly not the contract.
        frame = sys._getframe(2)
        while frame is not None:
            code = frame.f_code
            fdir = os.path.dirname(os.path.abspath(code.co_filename))
            base = os.path.basename(code.co_filename)
            if fdir == _MEMORY_PKG and base != "atomic.py":
                mod = os.path.splitext(base)[0]
                return mod == module and code.co_name == func
            frame = frame.f_back
        return False

    def _wrap(name):
        def inner(*args, **kwargs):
            if _match():
                state["seen"] += 1
                if state["seen"] >= nth:
                    raise _Tear("fault injection: torn mid-write")
            return real[name](*args, **kwargs)

        return inner

    for name in _ATOMIC_NAMES:
        monkeypatch.setattr(A, name, _wrap(name))
    return state


def _snap(*paths):
    out = {}
    for p in paths:
        out[p] = open(p, "rb").read() if os.path.exists(p) else None
    return out


def _assert_unchanged(before):
    for p, prior in before.items():
        now = open(p, "rb").read() if os.path.exists(p) else None
        assert now == prior, f"{p} changed across a torn write — the crash contract is broken"


# --------------------------------------------------------------------------- #
# Scenario substrate
# --------------------------------------------------------------------------- #
def _git_repo(tmp_path):
    root = str(tmp_path / "repo")
    md = os.path.join(root, ".claude", "memory")
    os.makedirs(md)
    with open(os.path.join(root, "app.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(["git", "-C", root, "add", "-A"], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "seed"], check=True, env=_GIT_ENV)
    return root, md


def _mem(md, name, *, body="Body.", extra_meta="", top=""):
    path = os.path.join(md, f"{name}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            f'---\nname: {name}\ndescription: "d {name}"\n{top}metadata:\n'
            f"  type: project\n{extra_meta}---\n{body}\n"
        )
    return path


# --------------------------------------------------------------------------- #
# In-process tear scenarios — one per (site, class) in CRASH_CONTRACT.
# Each returns after asserting the universal invariant (no watched file torn or
# half-written) plus its class's loudness/rollback obligations.
# --------------------------------------------------------------------------- #
def scn_dream_apply_bridge_detected(tmp_path, monkeypatch):
    from memory.dream import _apply_one

    _root, md = _git_repo(tmp_path)
    a = _mem(md, "alpha")
    _mem(md, "beta")
    before = _snap(a)
    _arm(monkeypatch, "dream", "_apply_one")
    ok, reason, undo = _apply_one(
        md, {"kind": "bridge", "source": "alpha", "target": "beta", "cofire": 0.9}, "p1-e1", "p1"
    )
    _assert_unchanged(before)
    assert ok is False and "write failed" in reason and undo is None


def scn_dream_apply_refines_rolled_back(tmp_path, monkeypatch):
    from memory.dream import _apply_one

    _root, md = _git_repo(tmp_path)
    a = _mem(md, "alpha")
    _mem(md, "beta")
    before = _snap(a)
    # Frame-precise targeting: write #1 runs in add_typed_relation's frame (it lands,
    # then must come back out); write #2 is _apply_one's own — the one torn here.
    _arm(monkeypatch, "dream", "_apply_one")
    ok, reason, undo = _apply_one(
        md, {"kind": "refines", "source": "alpha", "target": "beta", "cofire": 0.9}, "p1-e1", "p1"
    )
    _assert_unchanged(before)  # byte-exact restore of the frontmatter edge
    assert ok is False and "rolled back" in reason and undo is None


def scn_dream_generate_confidence_detected(tmp_path, monkeypatch):
    from memory.dream_generate import _set_confidence

    _root, md = _git_repo(tmp_path)
    a = _mem(md, "alpha")
    before = _snap(a)
    _arm(monkeypatch, "dream_generate", "_set_confidence")
    r = _set_confidence(a, "verified")
    _assert_unchanged(before)
    assert r["error"]


def scn_dream_generate_cited_detected(tmp_path, monkeypatch):
    from memory.dream_generate import _set_cited_paths

    _root, md = _git_repo(tmp_path)
    a = _mem(md, "alpha")
    before = _snap(a)
    _arm(monkeypatch, "dream_generate", "_set_cited_paths")
    r = _set_cited_paths(a, ["app.py"])
    _assert_unchanged(before)
    assert r["error"]


def scn_eval_drafts_detected(tmp_path, monkeypatch):
    import memory.telemetry as T
    from memory.eval_recall import draft_abstention_fixtures

    _root, md = _git_repo(tmp_path)
    drafts = str(tmp_path / "pending" / "recall_hard_set.drafts.yaml")
    monkeypatch.setattr(
        T,
        "abstention_backlog",
        lambda *a, **k: [
            {"sample_query": "how do we deploy", "count": 3, "terms": ["deploy"]}
        ],
    )
    before = _snap(drafts)
    _arm(monkeypatch, "eval_recall", "draft_abstention_fixtures")
    with pytest.raises(_Tear):
        draft_abstention_fixtures(md, drafts_path=drafts, probe=False)
    _assert_unchanged(before)  # no torn drafts queue — absent stays absent


def scn_eval_draft_forgetting_detected(tmp_path, monkeypatch):
    from memory.eval_recall import draft_forgetting_fixtures

    _root, md = _git_repo(tmp_path)
    os.makedirs(os.path.join(md, "archive"), exist_ok=True)
    with open(os.path.join(md, "archive", "gone.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: gone\ndescription: "retired deploy retry policy"\n---\nB\n')
    drafts = str(tmp_path / "pending" / "recall_hard_set.drafts.yaml")
    before = _snap(drafts)
    _arm(monkeypatch, "eval_fixtures", "_append_draft_rows")
    with pytest.raises(_Tear):
        draft_forgetting_fixtures(md, drafts_path=drafts)
    _assert_unchanged(before)  # no torn drafts queue — absent stays absent


def scn_eval_confirm_detected(tmp_path, monkeypatch):
    from memory.eval_recall import confirm_hard_set_row

    _root, md = _git_repo(tmp_path)
    _mem(md, "alpha")
    fixture = os.path.join(md, ".audit-fixtures", "recall_hard_set.yaml")
    drafts = str(tmp_path / "drafts.yaml")
    with open(drafts, "w", encoding="utf-8") as fh:
        fh.write('- query: "q one"\n  expected: []\n')
    before = _snap(fixture, drafts)
    _arm(monkeypatch, "eval_recall", "confirm_hard_set_row")
    with pytest.raises(_Tear):
        confirm_hard_set_row("q one", ["alpha"], memory_dir=md, fixture_path=fixture, drafts_path=drafts)
    _assert_unchanged(before)  # write #1 torn: fixture never materializes, drafts intact

    # Tear the SECOND write (the drafts drain): the fixture row lands byte-complete,
    # the draft row survives — both files whole, the raise is the loud signal.
    _arm(monkeypatch, "eval_recall", "confirm_hard_set_row", nth=2)
    with pytest.raises(_Tear):
        confirm_hard_set_row("q one", ["alpha"], memory_dir=md, fixture_path=fixture, drafts_path=drafts)
    assert open(drafts, "rb").read() == before[drafts]
    from memory.eval_recall import load_hard_set

    assert [r["query"] for r in load_hard_set(fixture)] == ["q one"]  # byte-complete, parseable


def scn_salience_write_report_detected(tmp_path, monkeypatch):
    from memory.salience_eval import write_report

    path = str(tmp_path / "telemetry" / "salience_ab.json")
    doc = {"ok": True, "schema": 1, "deltas": {}}
    before = _snap(path)
    _arm(monkeypatch, "salience_eval", "write_report")
    res = write_report(doc, path)
    _assert_unchanged(before)  # no torn evidence — an absent report stays absent
    assert res.get("ok") is False and "report write failed" in res.get("error", "")


def scn_eval_write_floor_sweep_detected(tmp_path, monkeypatch):
    from memory.eval_recall import write_floor_sweep

    path = str(tmp_path / "telemetry" / "floor_sweep.json")
    doc = {"ok": True, "schema": 1, "model": "m", "recommended": 0.6}
    before = _snap(path)
    _arm(monkeypatch, "eval_recall", "write_floor_sweep")
    res = write_floor_sweep(doc, path)
    _assert_unchanged(before)  # no torn report — an absent sweep stays absent
    assert res.get("ok") is False and "floor-sweep write failed" in res.get("error", "")


def scn_eval_write_baseline_detected(tmp_path, monkeypatch):
    from memory.eval_recall import write_baseline

    path = str(tmp_path / "recall_eval_baseline.json")
    report = {
        "gates": {"self_recall@10": {"value": 1.0}},
        "by_category": {},
        "tokens": {},
        "count": 1,
        "hard_set_n": 0,
        "backend": "bm25-only",
    }
    before = _snap(path)
    _arm(monkeypatch, "eval_recall", "write_baseline")
    res = write_baseline(report, path, head="abc", fixture_fp="f", corpus_fp="c")
    _assert_unchanged(before)  # no torn pin — an absent baseline stays absent
    assert res.get("ok") is False and "baseline write failed" in res.get("error", "")


def scn_init_copy_detected(tmp_path, monkeypatch):
    from memory.init_project import _copy_if_absent

    src = str(tmp_path / "src.md")
    dst = str(tmp_path / "dst.md")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("seed\n")
    _arm(monkeypatch, "init_project", "_copy_if_absent")
    with pytest.raises(_Tear):
        _copy_if_absent(src, dst)
    assert not os.path.exists(dst)  # no partial seed to dodge the already_present guard


def scn_init_project_stamp_detected(tmp_path, monkeypatch):
    from memory.init_project import init_project

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    root, md = _git_repo(tmp_path)
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)
    fmt = os.path.join(md, ".format")
    _arm(monkeypatch, "init_project", "init_project")
    r = init_project(claude_projects_dir=str(tmp_path / "cp"))
    assert not os.path.exists(fmt)  # the stamp is whole-or-absent, never torn
    # init's loud channel is its warnings list (RCH-9): the torn stamp is named there.
    assert any("fault injection" in w for w in r.get("warnings") or []), r


def scn_links_add_typed_detected(tmp_path, monkeypatch):
    from memory.links import add_typed_relation

    _root, md = _git_repo(tmp_path)
    a = _mem(md, "alpha")
    before = _snap(a)
    _arm(monkeypatch, "links", "add_typed_relation")
    r = add_typed_relation(a, "supersedes", "beta")
    _assert_unchanged(before)
    assert r["error"]


def scn_links_add_typed_rolled_back(tmp_path, monkeypatch):
    """The COR-16 demote+supersede chain: write #2 (the successor's edge) tears and
    write #1 (the loser's invalid_after) must come back OUT, byte-exact."""
    from memory.reconsolidate import semantic_reverify

    root, md = _git_repo(tmp_path)
    old = _mem(md, "m_old")
    new = _mem(md, "m_new")
    before = _snap(old, new)
    _arm(monkeypatch, "links", "add_typed_relation")
    r = semantic_reverify(
        "m_old", "demote", md, root, telemetry_dir=str(tmp_path / "tele"), superseded_by="m_new"
    )
    _assert_unchanged(before)
    assert r["error"] and "rolled back" in r["error"]
    assert not r["invalidated"] and not r["edge_written"]


def scn_links_remove_typed_detected(tmp_path, monkeypatch):
    from memory.links import remove_typed_relation

    _root, md = _git_repo(tmp_path)
    a = _mem(md, "alpha", extra_meta='  contradicts: ["beta"]\n')
    _mem(md, "beta")
    before = _snap(a)
    _arm(monkeypatch, "links", "remove_typed_relation")
    r = remove_typed_relation(a, "contradicts", "beta")
    _assert_unchanged(before)
    assert r["error"]


def scn_links_remove_typed_rolled_back(tmp_path, monkeypatch):
    """INV-4's scope_both verdict on a BOTH-declare pair is a two-file drop chain:
    tearing the second file's drop must restore the first, byte-exact."""
    from memory.resolve_view import apply_resolve_verdict

    root, md = _git_repo(tmp_path)
    a = _mem(md, "alpha", extra_meta='  contradicts: ["beta"]\n')
    b = _mem(md, "beta", extra_meta='  contradicts: ["alpha"]\n')
    before = _snap(a, b)
    _arm(monkeypatch, "links", "remove_typed_relation", nth=2)
    r = apply_resolve_verdict(md, root, "scope_both", a="alpha", b="beta")
    _assert_unchanged(before)
    assert r["error"] and "rolled back" in r["error"] and not r["applied"]


def scn_invalid_after_resolve_keep_one_rolled_back(tmp_path, monkeypatch):
    """INV-4's keep_one chain composed end-to-end: the declaration drop lands (write
    #1), the demote tears inside semantic_reverify (write #2) — and the declaration
    must come back, byte-exact, with the refusal naming both facts."""
    from memory.resolve_view import apply_resolve_verdict

    root, md = _git_repo(tmp_path)
    loser = _mem(md, "use_x", extra_meta='  contradicts: ["use_y"]\n')
    winner = _mem(md, "use_y")
    before = _snap(loser, winner)
    _arm(monkeypatch, "staleness", "set_invalid_after")
    r = apply_resolve_verdict(md, root, "keep_one", winner="use_y", loser="use_x")
    _assert_unchanged(before)
    assert r["error"] and "rolled back" in r["error"] and not r["applied"]


def scn_tier_floor_intact(tmp_path, monkeypatch):
    from memory.new_memory import _ensure_tier_floor

    tier = str(tmp_path / "tier")
    _arm(monkeypatch, "new_memory", "_ensure_tier_floor")
    _ensure_tier_floor(tier, "user")  # swallows by design (opportunistic skeleton)
    assert not os.path.exists(os.path.join(tier, "MEMORY.md"))  # absent, never partial


def scn_floor_append_detected(tmp_path, monkeypatch):
    from memory.new_memory import _append_floor_pointer

    _root, md = _git_repo(tmp_path)
    floor = os.path.join(md, "MEMORY.md")
    with open(floor, "w", encoding="utf-8") as fh:
        fh.write("# Memory\n\n## User\n\n## Working Style & Process Feedback\n")
    before = _snap(floor)
    _arm(monkeypatch, "new_memory", "_append_floor_pointer")
    r = _append_floor_pointer(md, "## User", "alpha", "Alpha", "hook")
    _assert_unchanged(before)
    assert r["status"] == "skipped" and "write failed" in (r["reason"] or "")


def scn_floor_remove_detected(tmp_path, monkeypatch):
    from memory.new_memory import _remove_floor_pointer

    _root, md = _git_repo(tmp_path)
    floor = os.path.join(md, "MEMORY.md")
    with open(floor, "w", encoding="utf-8") as fh:
        fh.write("# Memory\n\n## User\n- [Alpha](alpha.md) — hook\n")
    before = _snap(floor)
    _arm(monkeypatch, "new_memory", "_remove_floor_pointer")
    r = _remove_floor_pointer(md, "alpha")
    _assert_unchanged(before)
    assert r["status"] == "skipped" and "write failed" in (r["reason"] or "")


def _pack_source(tmp_path, md):
    """Extract a one-memory pack from a scratch corpus into a local source dir."""
    from memory.packs import pack_extract

    src_md = os.path.join(str(tmp_path / "srcrepo"), ".claude", "memory")
    os.makedirs(src_md)
    _mem(src_md, "lesson", body="Share me.")
    dest = str(tmp_path / "pack-src")
    r = pack_extract(["lesson"], dest, memory_dir=src_md, repo_root=str(tmp_path / "srcrepo"))
    assert not r["error"], r
    return dest


def scn_pack_lockfile_install_detected(tmp_path, monkeypatch):
    """Install's lockfile write tears: loud error, corpus file present, lockfile
    unchanged — and the DOCUMENTED recovery holds: a re-run adopts the byte-identical
    file and restores the lockfile record (INT-17's crash-window story)."""
    from memory.packs import lockfile_path, pack_install_item

    root, md = _git_repo(tmp_path)
    source = _pack_source(tmp_path, md)
    lock = lockfile_path(md)
    before = _snap(lock)
    _arm(monkeypatch, "packs", "_write_lockfile")
    r = pack_install_item(source, "lesson", memory_dir=md, repo_root=root)
    _assert_unchanged(before)  # the old lockfile is whole — never torn
    assert r["error"] and "fault injection" in r["error"]
    assert os.path.exists(os.path.join(md, "lesson.md"))  # the crash window INT-17 owns
    monkeypatch.undo()
    r2 = pack_install_item(source, "lesson", memory_dir=md, repo_root=root)
    assert r2["installed"] and r2["adopted"], r2  # re-run heals, exactly as documented


def scn_pack_lockfile_update_rolled_back(tmp_path, monkeypatch):
    """Update's lockfile going bad after the file write rolls the file write back
    (packs' COR-16 chain). The tear here hits the lockfile LOAD-then-write path by
    corrupting the lockfile between plan and apply — the documented refusal."""
    from memory.packs import lockfile_path, pack_install_item, pack_update_item

    root, md = _git_repo(tmp_path)
    source = _pack_source(tmp_path, md)
    r = pack_install_item(source, "lesson", memory_dir=md, repo_root=root)
    assert r["installed"], r
    target = os.path.join(md, "lesson.md")
    # New upstream version of the same pack.
    src2_md = os.path.join(str(tmp_path / "srcrepo2"), ".claude", "memory")
    os.makedirs(src2_md)
    _mem(src2_md, "lesson", body="Share me, v2.")
    source2 = str(tmp_path / "pack-src2")
    from memory.packs import pack_extract

    r = pack_extract(["lesson"], source2, memory_dir=src2_md, repo_root=str(tmp_path / "srcrepo2"), version="0.2.0")
    assert not r["error"], r
    before = _snap(target)
    _arm(monkeypatch, "packs", "_write_lockfile")
    r = pack_update_item(source2, "lesson", memory_dir=md, repo_root=root)
    _assert_unchanged(before)  # ours came back byte-exact
    assert r["error"] and not r["updated"]


def scn_pack_update_write_detected(tmp_path, monkeypatch):
    from memory.packs import pack_install_item, pack_update_item, pack_extract

    root, md = _git_repo(tmp_path)
    source = _pack_source(tmp_path, md)
    r = pack_install_item(source, "lesson", memory_dir=md, repo_root=root)
    assert r["installed"], r
    src2_md = os.path.join(str(tmp_path / "srcrepo2"), ".claude", "memory")
    os.makedirs(src2_md)
    _mem(src2_md, "lesson", body="Share me, v2.")
    source2 = str(tmp_path / "pack-src2")
    r = pack_extract(["lesson"], source2, memory_dir=src2_md, repo_root=str(tmp_path / "srcrepo2"), version="0.2.0")
    assert not r["error"], r
    target = os.path.join(md, "lesson.md")
    from memory.packs import lockfile_path

    before = _snap(target, lockfile_path(md))
    _arm(monkeypatch, "packs", "pack_update_item")
    r = pack_update_item(source2, "lesson", memory_dir=md, repo_root=root)
    _assert_unchanged(before)  # local edits exist nowhere else — never torn
    assert r["error"] and not r["updated"]


def scn_promote_rule_detected(tmp_path, monkeypatch, capsys):
    import memory.promote_rule as PR

    root, md = _git_repo(tmp_path)
    _mem(md, "lint", extra_meta='  cited_paths: ["app.py"]\n')
    rule = os.path.join(root, ".claude", "rules", "lint.md")
    _arm(monkeypatch, "promote_rule", "main")
    with pytest.raises(_Tear):
        PR.main(["--name", "lint", "--memory-dir", md, "--repo-root", root, "--apply"])
    assert not os.path.exists(rule)  # the rule file is whole-or-absent


def scn_marker_keys_detected(tmp_path, monkeypatch):
    # The writer's true home moved to provenance_format.py (ratchet split; the façade
    # re-export in provenance still resolves) — _arm keys on the FILE the frame lives in.
    from memory.provenance_format import _write_marker_keys, format_marker_path

    _root, md = _git_repo(tmp_path)
    with open(format_marker_path(md), "w", encoding="utf-8") as fh:
        fh.write('{"corpus_format": 4}\n')
    before = _snap(format_marker_path(md))
    _arm(monkeypatch, "provenance_format", "_write_marker_keys")
    ok = _write_marker_keys(md, cite_derivation=3)
    _assert_unchanged(before)
    assert ok is False


def scn_restore_bytes_detected(tmp_path, monkeypatch):
    from memory.provenance import restore_file_bytes

    _root, md = _git_repo(tmp_path)
    a = _mem(md, "alpha")
    before = _snap(a)
    _arm(monkeypatch, "provenance", "restore_file_bytes")
    err = restore_file_bytes(a, "original bytes\n", md)
    _assert_unchanged(before)
    assert err and "fault injection" in err  # the caller reports the PARTIAL state


def scn_backfill_detected(tmp_path, monkeypatch):
    from memory.provenance import backfill_file, build_repo_file_index

    root, md = _git_repo(tmp_path)
    a = _mem(md, "alpha", body="See app.py for the shape.\n")
    repo_files, basenames = build_repo_file_index(root)
    before = _snap(a)
    _arm(monkeypatch, "provenance", "backfill_file")
    r = backfill_file(a, root, repo_files, basenames)
    _assert_unchanged(before)
    assert r["error"]


def scn_heal_baselines_detected(tmp_path, monkeypatch):
    from memory.provenance import heal_empty_baselines

    root, md = _git_repo(tmp_path)
    # heal's precondition: a PRESENT source_commit key with an EMPTY value (it never
    # invents a baseline for a memory that doesn't carry the key).
    a = _mem(md, "alpha", extra_meta='  cited_paths: ["app.py"]\n  source_commit: ""\n')
    before = _snap(a)
    _arm(monkeypatch, "provenance", "heal_empty_baselines")
    healed, failed = heal_empty_baselines(md, root)
    _assert_unchanged(before)
    assert healed == [] and "alpha" in failed  # RCH-9: the failure is named, never silent
    assert "fault injection" in failed["alpha"]


def scn_reverify_detected(tmp_path, monkeypatch):
    from memory.provenance import build_repo_file_index, reverify_file

    root, md = _git_repo(tmp_path)
    a = _mem(md, "alpha", body="See app.py.\n")
    repo_files, basenames = build_repo_file_index(root)
    before = _snap(a)
    _arm(monkeypatch, "provenance", "reverify_file")
    r = reverify_file(a, root, repo_files, basenames)
    _assert_unchanged(before)
    assert r["error"]


def scn_registry_register_detected(tmp_path, monkeypatch):
    from memory.registry import register_project, registered_projects

    root, md = _git_repo(tmp_path)
    _arm(monkeypatch, "registry", "register_project")
    assert register_project(root, md) is False
    assert registered_projects() == {}  # the registry file never materialized torn


def scn_registry_deregister_detected(tmp_path, monkeypatch):
    from memory.registry import deregister_project, register_project, registered_projects

    root, md = _git_repo(tmp_path)
    assert register_project(root, md) is True
    before = dict(registered_projects())
    _arm(monkeypatch, "registry", "deregister_project")
    assert deregister_project(root) is False
    assert registered_projects() == before  # old registry intact, loudly not-removed


def scn_registry_prune_dead_detected(tmp_path, monkeypatch):
    from memory.registry import prune_dead, projects_registry_path, register_project

    # A prunable row: registered under tmp (volatile by construction), then deleted.
    root = tmp_path / "proj"
    md = root / ".claude" / "memory"
    md.mkdir(parents=True)
    assert register_project(str(root), str(md)) is True
    shutil.rmtree(str(root))
    with open(projects_registry_path(), "rb") as fh:
        before = fh.read()
    _arm(monkeypatch, "registry", "prune_dead")
    r = prune_dead()
    assert r["ok"] is False  # RCH-11: the failed rewrite is loud, never pretended
    with open(projects_registry_path(), "rb") as fh:
        assert fh.read() == before  # prior document byte-intact, never torn


def scn_sleep_report_write_detected(tmp_path, monkeypatch, capsys):
    from memory import sleep as SL
    from memory.telemetry import default_telemetry_dir

    root, md = _git_repo(tmp_path)
    _mem(md, "alpha")
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _arm(monkeypatch, "sleep", "_write_report")
    assert SL.main([]) == 0
    out = capsys.readouterr().out
    # SLP-1's RCH-9 posture: the report still PRINTS whole, and the missed persist
    # is named — never a silent hole where the artifact should be.
    assert "NOT persisted" in out
    assert not os.path.exists(os.path.join(default_telemetry_dir(md), "sleep-report.md"))


def scn_sleep_state_write_detected(tmp_path, monkeypatch, capsys):
    from memory import sleep as SL

    root, md = _git_repo(tmp_path)
    _mem(md, "alpha")
    monkeypatch.setenv("HIPPO_MEMORY_DIR", md)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _arm(monkeypatch, "sleep", "_write_state")
    assert SL.main([]) == 0
    out = capsys.readouterr().out
    assert "could not write sleep-state.json" in out  # the lost stamp is named


def scn_invalid_after_detected(tmp_path, monkeypatch):
    from memory.staleness import set_invalid_after

    _root, md = _git_repo(tmp_path)
    a = _mem(md, "alpha")
    before = _snap(a)
    _arm(monkeypatch, "staleness", "set_invalid_after")
    r = set_invalid_after(a)
    _assert_unchanged(before)
    assert r["error"]


def scn_invalid_after_rolled_back(tmp_path, monkeypatch):
    """The dedup-merge chain (COR-16): write #2 (loser's invalid_after) tears and
    write #1 (the survivor's supersedes edge) must come back out, byte-exact."""
    from memory.deparasite import apply_dedup_merge

    _root, md = _git_repo(tmp_path)
    s = _mem(md, "survivor")
    l = _mem(md, "loser")
    before = _snap(s, l)
    _arm(monkeypatch, "staleness", "set_invalid_after")
    r = apply_dedup_merge(md, "survivor", "loser", telemetry_dir=str(tmp_path / "tele"))
    _assert_unchanged(before)
    assert r["error"] and "rolled back" in r["error"] and not r["changed"]


def scn_trust_registry_detected(tmp_path, monkeypatch):
    from memory import trust

    root, md = _git_repo(tmp_path)
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    before = _snap(os.environ["HIPPO_TRUST_FILE"])
    _arm(monkeypatch, "trust", "_write_registry_doc")
    ok = trust.mark_trusted(root, memory_dir=md)
    _assert_unchanged(before)
    assert ok is False  # the caller must not pretend the corpus got trusted


def scn_interview_state_detected(tmp_path, monkeypatch):
    from memory.interview import STATE_NAME, respond

    tele = str(tmp_path / "tele")
    os.makedirs(tele, exist_ok=True)
    _arm(monkeypatch, "interview", "_write_state")
    r = respond("abstain:cafecafecafe", "decline", telemetry_dir=tele)
    assert r["ok"] is False and "write failed" in r["error"]  # a lost decline is NAMED
    assert not os.path.exists(os.path.join(tele, STATE_NAME))  # absent, never partial


def scn_jit_touch_cache_detected(tmp_path, monkeypatch):
    from memory import jit

    _root, md = _git_repo(tmp_path)
    idx = str(tmp_path / "idx")
    assert jit.refresh_touch_cache(md, idx) is True  # a healthy prior cache on disk
    before = _snap(jit.touch_cache_path(idx))
    _arm(monkeypatch, "jit", "write_touch_cache")
    ok = jit.refresh_touch_cache(md, idx)
    _assert_unchanged(before)
    assert ok is False  # the SessionStart caller must see the miss, never assume freshness


def scn_jit_state_intact(tmp_path, monkeypatch):
    from memory import jit

    root, md = _git_repo(tmp_path)
    _mem(md, "lesson", extra_meta='  steer: pin\n  cited_paths: ["app.py"]\n')
    idx = str(tmp_path / "idx")
    tele = str(tmp_path / "tele")
    assert jit.refresh_touch_cache(md, idx) is True
    _arm(monkeypatch, "jit", "_write_state")
    _cited, ctx = jit.observe_touch(
        "app.py", memory_dir=md, repo_root=root, telemetry_dir=tele,
        index_dir=idx, session_id="s",
    )
    assert ctx and "lesson" in ctx  # the reminder is worth more than the bookkeeping
    state_dir = os.path.join(tele, "jit")
    leftovers = [f for f in os.listdir(state_dir)] if os.path.isdir(state_dir) else []
    assert not [f for f in leftovers if f.endswith(".json")], "state absent, never partial"


_SCENARIOS = [
    (("dream", "_apply_one"), "detected", scn_dream_apply_bridge_detected),
    (("dream", "_apply_one"), "rolled_back", scn_dream_apply_refines_rolled_back),
    (("dream_generate", "_set_confidence"), "detected", scn_dream_generate_confidence_detected),
    (("dream_generate", "_set_cited_paths"), "detected", scn_dream_generate_cited_detected),
    (("eval_recall", "draft_abstention_fixtures"), "detected", scn_eval_drafts_detected),
    (("eval_fixtures", "_append_draft_rows"), "detected", scn_eval_draft_forgetting_detected),
    (("eval_recall", "confirm_hard_set_row"), "detected", scn_eval_confirm_detected),
    (("eval_recall", "write_baseline"), "detected", scn_eval_write_baseline_detected),
    (("eval_recall", "write_floor_sweep"), "detected", scn_eval_write_floor_sweep_detected),
    (("salience_eval", "write_report"), "detected", scn_salience_write_report_detected),
    (("init_project", "_copy_if_absent"), "detected", scn_init_copy_detected),
    (("init_project", "init_project"), "detected", scn_init_project_stamp_detected),
    (("interview", "_write_state"), "detected", scn_interview_state_detected),
    (("jit", "write_touch_cache"), "detected", scn_jit_touch_cache_detected),
    (("jit", "_write_state"), "intact", scn_jit_state_intact),
    (("links", "add_typed_relation"), "detected", scn_links_add_typed_detected),
    (("links", "add_typed_relation"), "rolled_back", scn_links_add_typed_rolled_back),
    (("links", "remove_typed_relation"), "detected", scn_links_remove_typed_detected),
    (("links", "remove_typed_relation"), "rolled_back", scn_links_remove_typed_rolled_back),
    (("staleness", "set_invalid_after"), "rolled_back", scn_invalid_after_resolve_keep_one_rolled_back),
    (("new_memory", "_ensure_tier_floor"), "intact", scn_tier_floor_intact),
    (("new_memory", "_append_floor_pointer"), "detected", scn_floor_append_detected),
    (("new_memory", "_remove_floor_pointer"), "detected", scn_floor_remove_detected),
    (("packs", "_write_lockfile"), "detected", scn_pack_lockfile_install_detected),
    (("packs", "_write_lockfile"), "rolled_back", scn_pack_lockfile_update_rolled_back),
    (("packs", "pack_update_item"), "detected", scn_pack_update_write_detected),
    (("promote_rule", "main"), "detected", scn_promote_rule_detected),
    (("provenance_format", "_write_marker_keys"), "detected", scn_marker_keys_detected),
    (("provenance", "restore_file_bytes"), "detected", scn_restore_bytes_detected),
    (("provenance", "backfill_file"), "detected", scn_backfill_detected),
    (("provenance", "heal_empty_baselines"), "detected", scn_heal_baselines_detected),
    (("provenance", "reverify_file"), "detected", scn_reverify_detected),
    (("registry", "register_project"), "detected", scn_registry_register_detected),
    (("registry", "deregister_project"), "detected", scn_registry_deregister_detected),
    (("registry", "prune_dead"), "detected", scn_registry_prune_dead_detected),
    (("sleep", "_write_report"), "detected", scn_sleep_report_write_detected),
    (("sleep", "_write_state"), "detected", scn_sleep_state_write_detected),
    (("staleness", "set_invalid_after"), "detected", scn_invalid_after_detected),
    (("staleness", "set_invalid_after"), "rolled_back", scn_invalid_after_rolled_back),
    (("trust", "_write_registry_doc"), "detected", scn_trust_registry_detected),
]


def test_every_declared_class_has_a_scenario():
    covered = {(site, cls) for site, cls, _fn in _SCENARIOS}
    declared = {(site, cls) for site, classes in CRASH_CONTRACT.items() for cls in classes}
    missing = sorted(declared - covered)
    assert not missing, f"declared crash classes with no tear scenario: {missing}"
    phantom = sorted(covered - declared)
    assert not phantom, f"scenarios for undeclared classes (declare them): {phantom}"


@pytest.mark.parametrize(
    "site,cls,scenario",
    _SCENARIOS,
    ids=[f"{m}.{f}-{cls}" for (m, f), cls, _ in _SCENARIOS],
)
def test_tear_once(site, cls, scenario, tmp_path, monkeypatch, capsys):
    import inspect

    kwargs = {"tmp_path": tmp_path, "monkeypatch": monkeypatch}
    if "capsys" in inspect.signature(scenario).parameters:
        kwargs["capsys"] = capsys
    scenario(**kwargs)


# --------------------------------------------------------------------------- #
# The subprocess kill lane (slow-marked): a real SIGKILL mid-write, then the
# documented recovery. Deterministic — the child kills ITSELF at the exact
# write moment (a genuine process death; no handlers, no cleanup).
# --------------------------------------------------------------------------- #
_KILL_CHILD = r"""
import os, signal, sys
sys.path.insert(0, {plugin_root!r})
import builtins
_real_open = builtins.open
_state = {{"writes": 0}}
def _open(path, mode="r", *a, **k):
    if any(c in mode for c in "wax") and {suffix!r} in str(path):
        _state["writes"] += 1
        if _state["writes"] >= {nth}:
            fh = _real_open(path, mode, *a, **k)
            fh.write({partial!r})  # bytes hit the disk...
            fh.flush()
            os.kill(os.getpid(), signal.SIGKILL)  # ...and the process dies mid-write
    return _real_open(path, mode, *a, **k)
builtins.open = _open
{body}
"""


def _run_child(body: str, *, suffix: str, nth: int = 1, partial: str = "{TORN") -> int:
    code = _KILL_CHILD.format(
        plugin_root=_PLUGIN_ROOT, suffix=suffix, nth=nth, partial=partial, body=body
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    return proc.returncode


@pytest.mark.slow
def test_kill9_mid_pack_extract_rerun_refuses_with_the_documented_message(tmp_path):
    """SIGKILL while extract writes a pack file: the dest holds a partial write the
    process never got to roll back (RCH-8 can't run under SIGKILL). The documented
    recovery is the REFUSAL arm: a re-run refuses the non-empty dest by name — the
    operator deletes the partial dir and re-runs clean."""
    root, md = _git_repo(tmp_path)
    _mem(md, "alpha")
    _mem(md, "beta")
    dest = str(tmp_path / "pack-out")
    body = (
        "from memory.packs import pack_extract\n"
        f"pack_extract(['alpha', 'beta'], {dest!r}, memory_dir={md!r}, repo_root={root!r})\n"
    )
    rc = _run_child(body, suffix=".md", nth=1)
    assert rc == -signal.SIGKILL
    assert os.path.isdir(dest)  # the partial dest the crash stranded

    from memory.packs import pack_extract

    r = pack_extract(["alpha", "beta"], dest, memory_dir=md, repo_root=root)
    # The documented refusal: every colliding name reported, zero files written.
    assert r["error"] and "refusing to overwrite" in r["error"], r
    assert r["extracted"] == [] and "zero files written" in r["error"]
    import shutil

    shutil.rmtree(dest)
    r2 = pack_extract(["alpha", "beta"], dest, memory_dir=md, repo_root=root)
    assert not r2["error"] and len(r2["extracted"]) == 2  # clean re-run heals


@pytest.mark.slow
def test_kill9_mid_build_index_rerun_heals(tmp_path):
    """SIGKILL while build_index writes its manifest TMP: the published manifest is
    old-or-absent (never torn — the swap never ran), and a re-run heals the index."""
    from memory.build_index import build_index, default_index_dir

    _root, md = _git_repo(tmp_path)
    _mem(md, "alpha")
    _mem(md, "beta")
    idx = default_index_dir(md)
    body = (
        "os.environ['HIPPO_DISABLE_DENSE'] = '1'\n"
        "from memory.build_index import build_index\n"
        f"build_index({md!r}, {idx!r})\n"
    )
    rc = _run_child(body, suffix="manifest.json", nth=1)  # matches the unique tmp too
    assert rc == -signal.SIGKILL

    manifest = os.path.join(idx, "manifest.json")
    if os.path.exists(manifest):  # whatever survived must parse whole — never torn
        with open(manifest, encoding="utf-8") as fh:
            json.load(fh)

    os.environ["HIPPO_DISABLE_DENSE"] = "1"
    try:
        build_index(md, idx)
    finally:
        os.environ.pop("HIPPO_DISABLE_DENSE", None)
    with open(manifest, encoding="utf-8") as fh:
        m = json.load(fh)
    assert m.get("count") == 2  # the re-run healed the index completely


# --------------------------------------------------------------------------- #
# STABILITY.md parity: the published contract names what the lane enforces.
# --------------------------------------------------------------------------- #
_DOC_CHAINS = (
    "demote+supersede",
    "dedup-merge",
    "dream refines",
    "pack-update lockfile",
)


def test_stability_doc_matches_the_contract():
    with open(os.path.join(_PLUGIN_ROOT, "..", "STABILITY.md"), encoding="utf-8") as fh:
        doc = fh.read()
    assert "## Crash safety" in doc, "STABILITY.md lost its crash-safety contract section"
    section = doc.split("## Crash safety", 1)[1]
    for cls in sorted({c for classes in CRASH_CONTRACT.values() for c in classes}):
        assert cls.replace("_", "-") in section, (
            f"STABILITY.md's crash-safety section never names the {cls!r} class the "
            "fault lane enforces — docs and tests must not drift (INV-3)"
        )
    for chain in _DOC_CHAINS:
        assert chain in section, (
            f"STABILITY.md's crash-safety section lost the {chain!r} rollback chain"
        )
    assert "tests/test_crash_faults.py" in section, (
        "the contract section must point at the enforcing lane"
    )
