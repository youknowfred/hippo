"""The outbox, the tend half: every verdict that edits an existing memory edits its outbox copy.

With ``write_path: outbox`` accepted, a verdict must leave the working tree alone (``git
status`` stays empty), write the outbox copy with a manifest row whose base is the committed
bytes and a trust hash matching what it wrote, and the source that queued the item must stop
reporting it (its reader flipped with its writer), and recall must serve the edited copy.
Off, every writer is byte-identical to today. A withheld committed file is refused, never
laundered. Removals stay direct and take the outbox copy with them. The shadow a copy makes
costs the project tier no BM25 recount on the prompt path.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from memory import build_index as B
from memory import outbox as OB
from memory import outbox_edit as OE
from memory import policy_consent as PC
from memory import recall as R
from memory import recall_tiers as RT
from memory import tend as TD
from memory import tend_queue as Q
from memory import trust as T

from .conftest import git_commit, write_file

_FLOOR = "# Agent Memory\n\n## User\n\n## Working Style & Process Feedback\n"
_NOW = int(time.time())


def _mem(name, desc, mtype="project", body="body text", extra=""):
    return f'---\nname: {name}\ndescription: "{desc}"\n{extra}metadata:\n  type: {mtype}\n---\n{body}\n'


def _porcelain(repo):
    return subprocess.run(
        ["git", "-C", repo, "status", "--porcelain"], capture_output=True, text=True, check=True
    ).stdout


def _sha(path):
    return T.file_sha256(path)


def _corpus(repo, memory_dir, monkeypatch, files=None, outbox=True, accept=True):
    """A committed, trusted corpus (fingerprinted, the real gates on), the outbox policy
    declared and accepted when asked. ``files`` maps name -> text; plus a cited source file."""
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    write_file(repo, "src/app.py", "def main():\n    return 1\n")
    write_file(memory_dir, "MEMORY.md", _FLOOR)
    write_file(memory_dir, "branch_naming.md", _mem("branch_naming", "how release branches are named"))
    for name, text in (files or {}).items():
        write_file(memory_dir, f"{name}.md", text)
    if outbox:
        write_file(memory_dir, "hippo.json", json.dumps({"write_path": "outbox"}))
    write_file(repo, ".gitignore", ".hippo/\n.claude/memory.local/\n.claude/memory.outbox/\n")
    git_commit(repo, "seed", _NOW - 5000)
    root = T.gate_repo_root(memory_dir, repo)
    assert T.mark_trusted(root, memory_dir=memory_dir, origin="init")
    if outbox and accept:
        assert PC.acknowledge(memory_dir, repo)["ok"]
    B.build_index(memory_dir, B.default_index_dir(memory_dir))
    return root


def _assert_landed(repo, memory_dir, name, committed_bytes):
    """(i) the working tree is clean, (ii) the outbox holds the edited copy with a manifest
    row whose base is the committed bytes and a trust hash matching it."""
    assert _porcelain(repo) == ""
    copy = os.path.join(OB.outbox_dir(memory_dir), f"{name}.md")
    assert os.path.isfile(copy)
    assert open(copy, "rb").read() != committed_bytes
    assert open(os.path.join(memory_dir, f"{name}.md"), "rb").read() == committed_bytes
    import hashlib

    assert OB.read_manifest(memory_dir)[f"{name}.md"]["base_sha256"] == hashlib.sha256(committed_bytes).hexdigest()
    entry = T._trusted_entry(T.gate_repo_root(memory_dir, repo))
    assert entry["outbox"][name] == _sha(copy)
    assert OB.withheld(memory_dir, repo) == []
    return copy


def _served(repo, memory_dir, query, name):
    """(iv) recall serves the edited copy (the outbox root), not the committed one."""
    hits = R.recall(query, k=5, memory_dir=memory_dir, repo_root=repo)
    hit = next((h for h in hits if h["name"] == name), None)
    assert hit is not None, [h["name"] for h in hits]
    assert hit["root"] == OB.outbox_dir(memory_dir)
    return hit


def _ids(memory_dir, repo, kind):
    return [e["id"] for e in Q.build_queue(memory_dir, repo, kinds=(kind,), write_cache=False)["pending"]]


_EMPTY_BASELINE = _mem(
    "cache_rule", "the app cache rule for main", body="main lives in src/app.py",
    extra='cited_paths: ["src/app.py"]\nsource_commit: ""\n',
)


# --------------------------------------------------------------------------- #
# baseline -> rebaseline (reverify_file through semantic_reverify graduate)
# --------------------------------------------------------------------------- #
def test_baseline_rebaseline_edits_the_outbox_copy(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, monkeypatch, {"cache_rule": _EMPTY_BASELINE})
    before = open(os.path.join(memory_dir, "cache_rule.md"), "rb").read()
    assert "baseline:cache_rule" in _ids(memory_dir, repo, "baseline")
    r = TD.apply("baseline:cache_rule", "rebaseline", memory_dir=memory_dir, repo_root=repo)
    assert r["ok"], r
    copy = _assert_landed(repo, memory_dir, "cache_rule", before)
    assert 'source_commit: ""' not in open(copy).read()
    assert "baseline:cache_rule" not in _ids(memory_dir, repo, "baseline")  # (iii)
    _served(repo, memory_dir, "app cache rule for main", "cache_rule")


def test_heal_empty_baselines_reads_and_writes_the_effective_view(repo, memory_dir, monkeypatch):
    from memory.provenance import heal_empty_baselines

    _corpus(repo, memory_dir, monkeypatch, {"cache_rule": _EMPTY_BASELINE})
    before = open(os.path.join(memory_dir, "cache_rule.md"), "rb").read()
    healed, failed = heal_empty_baselines(memory_dir, repo)
    assert healed == ["cache_rule"] and failed == {}
    _assert_landed(repo, memory_dir, "cache_rule", before)
    assert heal_empty_baselines(memory_dir, repo) == ([], {})  # the copy is healed: nothing re-queues
    assert "baseline:cache_rule" not in _ids(memory_dir, repo, "baseline")


# --------------------------------------------------------------------------- #
# reverify -> graduate / demote (reverify_file, set_invalid_after)
# --------------------------------------------------------------------------- #
def _stale_corpus(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, monkeypatch)
    from memory.provenance import git_head

    head = git_head(repo)
    write_file(memory_dir, "app_entry.md", _mem(
        "app_entry", "the app entry point returns one", mtype="reference", body="see src/app.py",
        extra=f'cited_paths: ["src/app.py"]\nsource_commit: "{head}"\n',
    ))
    git_commit(repo, "memory", _NOW - 4000)
    write_file(repo, "src/app.py", "def main():\n    return 2\n")
    git_commit(repo, "code moved", _NOW - 3000)
    root = T.gate_repo_root(memory_dir, repo)
    assert T.mark_trusted(root, memory_dir=memory_dir, origin="init")
    assert PC.acknowledge(memory_dir, repo)["ok"]
    B.build_index(memory_dir, B.default_index_dir(memory_dir))
    from memory import reconsolidate as RC

    monkeypatch.setattr(RC, "_recently_recalled_names", lambda *a, **k: {"app_entry"})
    return open(os.path.join(memory_dir, "app_entry.md"), "rb").read()


def test_reverify_graduate_edits_the_copy_and_the_item_leaves(repo, memory_dir, monkeypatch):
    before = _stale_corpus(repo, memory_dir, monkeypatch)
    assert "reverify:app_entry" in _ids(memory_dir, repo, "reverify")
    r = TD.apply("reverify:app_entry", "graduate", memory_dir=memory_dir, repo_root=repo)
    assert r["ok"], r
    _assert_landed(repo, memory_dir, "app_entry", before)
    assert "reverify:app_entry" not in _ids(memory_dir, repo, "reverify")
    _served(repo, memory_dir, "app entry point returns one", "app_entry")


def test_reverify_demote_edits_the_copy_and_the_item_leaves(repo, memory_dir, monkeypatch):
    before = _stale_corpus(repo, memory_dir, monkeypatch)
    r = TD.apply("reverify:app_entry", "demote", memory_dir=memory_dir, repo_root=repo)
    assert r["ok"], r
    copy = _assert_landed(repo, memory_dir, "app_entry", before)
    assert "invalid_after" in open(copy).read()
    assert "reverify:app_entry" not in _ids(memory_dir, repo, "reverify")


# --------------------------------------------------------------------------- #
# contradiction -> keep_one (remove_typed_relation, set_invalid_after, add_typed_relation)
# --------------------------------------------------------------------------- #
def _pair():
    return {
        "old_way": _mem("old_way", "deploys go out on mondays", extra="contradicts: [new_way]\n"),
        "new_way": _mem("new_way", "deploys go out on thursdays"),
    }


def test_contradiction_keep_one_edits_both_copies_and_the_pair_leaves(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, monkeypatch, _pair())
    old_b = open(os.path.join(memory_dir, "old_way.md"), "rb").read()
    new_b = open(os.path.join(memory_dir, "new_way.md"), "rb").read()
    assert "contradiction:new_way|old_way" in _ids(memory_dir, repo, "contradiction")
    r = TD.apply("contradiction:new_way|old_way", "keep_one", memory_dir=memory_dir, repo_root=repo,
                 winner="new_way", loser="old_way")
    assert r["ok"], r
    loser = _assert_landed(repo, memory_dir, "old_way", old_b)
    winner = _assert_landed(repo, memory_dir, "new_way", new_b)
    assert "contradicts" not in open(loser).read() and "invalid_after" in open(loser).read()
    assert "supersedes" in open(winner).read()
    assert _ids(memory_dir, repo, "contradiction") == []
    _served(repo, memory_dir, "deploys go out on thursdays", "new_way")


def test_a_refused_second_write_rolls_the_first_back_into_the_copy(repo, memory_dir, monkeypatch):
    """demote+supersede: the successor is withheld (hand-edited after consent), so the
    supersedes edge is refused; the loser's invalid_after (write #1, in its copy) comes back
    out of the copy, and the committed files are untouched."""
    from memory.reconsolidate import semantic_reverify

    _corpus(repo, memory_dir, monkeypatch, _pair())
    loser_b = open(os.path.join(memory_dir, "old_way.md"), "rb").read()
    with open(os.path.join(memory_dir, "new_way.md"), "a", encoding="utf-8") as fh:
        fh.write("hand edit\n")
    r = semantic_reverify("old_way", "demote", memory_dir, repo, superseded_by="new_way")
    assert r["error"] and "withheld" in r["error"] and "rolled back" in r["error"]
    copy = os.path.join(OB.outbox_dir(memory_dir), "old_way.md")
    assert open(copy, "rb").read() == loser_b  # restored where the edit landed
    assert open(os.path.join(memory_dir, "old_way.md"), "rb").read() == loser_b
    assert not os.path.exists(os.path.join(OB.outbox_dir(memory_dir), "new_way.md"))


# --------------------------------------------------------------------------- #
# derivation -> apply (rederive_file / backfill_file)
# --------------------------------------------------------------------------- #
def test_derivation_apply_edits_the_copy_and_the_item_leaves(repo, memory_dir, monkeypatch):
    from memory.provenance import git_head

    _corpus(repo, memory_dir, monkeypatch)
    write_file(memory_dir, "entry_point.md", _mem(
        "entry_point", "where the entry point lives", body="it is in src/app.py",
        extra=f'cited_paths: []\nsource_commit: "{git_head(repo)}"\n',
    ))
    git_commit(repo, "memory", _NOW - 4000)
    assert T.mark_trusted(T.gate_repo_root(memory_dir, repo), memory_dir=memory_dir, origin="init")
    assert PC.acknowledge(memory_dir, repo)["ok"]
    before = open(os.path.join(memory_dir, "entry_point.md"), "rb").read()
    assert "derivation:entry_point" in _ids(memory_dir, repo, "derivation")
    r = TD.apply("derivation:entry_point", "apply", memory_dir=memory_dir, repo_root=repo)
    assert r["ok"], r
    copy = _assert_landed(repo, memory_dir, "entry_point", before)
    assert "src/app.py" in open(copy).read().split("---")[1]
    assert "derivation:entry_point" not in _ids(memory_dir, repo, "derivation")
    _served(repo, memory_dir, "where the entry point lives", "entry_point")


# --------------------------------------------------------------------------- #
# link and floor: the readers see the outbox (their verdict is a re-check)
# --------------------------------------------------------------------------- #
def test_link_source_resolves_outbox_memories_and_reads_copies(repo, memory_dir, monkeypatch):
    from memory.links import remove_typed_relation
    from memory.new_memory import write_memory

    files = {"pointer": _mem("pointer", "points at things", body="see [[fresh_note]]",
                             extra="refines: [nowhere_at_all]\n")}
    _corpus(repo, memory_dir, monkeypatch, files)
    ids = _ids(memory_dir, repo, "link")
    assert "link:pointer->fresh_note" in ids and "link:pointer-refines->nowhere_at_all" in ids
    assert write_memory("fresh_note", "a fresh unshipped note", "project", "x",
                        memory_dir=memory_dir, repo_root=repo)["write_path"] == "outbox"
    assert "link:pointer->fresh_note" not in _ids(memory_dir, repo, "link")
    before = open(os.path.join(memory_dir, "pointer.md"), "rb").read()
    r = remove_typed_relation(os.path.join(memory_dir, "pointer.md"), "refines", "nowhere_at_all")
    assert r["changed"] and not r["error"]
    _assert_landed(repo, memory_dir, "pointer", before)
    r = TD.apply("link:pointer-refines->nowhere_at_all", "done", memory_dir=memory_dir, repo_root=repo)
    assert r["ok"] and r["message"].endswith("fixed.")  # already gone from the queue: re-checked
    assert _ids(memory_dir, repo, "link") == []


def test_floor_source_reads_the_outbox_floor_and_outbox_targets(repo, memory_dir, monkeypatch):
    from memory.new_memory import write_memory

    _corpus(repo, memory_dir, monkeypatch)
    res = write_memory("tabs_rule", "tabs not spaces", "feedback", "x", memory_dir=memory_dir, repo_root=repo)
    assert res["floor"]["status"] == "appended"
    # The pointer lives in the outbox floor copy and its target only in the outbox: neither
    # is a floor problem on this machine.
    assert _ids(memory_dir, repo, "floor") == []


# --------------------------------------------------------------------------- #
# The other writers in the table, called directly
# --------------------------------------------------------------------------- #
def test_fix_duplicate_related_edits_the_copy(repo, memory_dir, monkeypatch):
    from memory.related_lines import fix_duplicate_related, scan_duplicate_related

    body = "text\n\nRelated: [[branch_naming]]\n\nRelated: [[branch_naming]]"
    _corpus(repo, memory_dir, monkeypatch, {"twice": _mem("twice", "two related lines", body=body)})
    before = open(os.path.join(memory_dir, "twice.md"), "rb").read()
    assert [r["name"] for r in scan_duplicate_related(memory_dir)] == ["twice"]
    res = fix_duplicate_related(os.path.join(memory_dir, "twice.md"), memory_dir=memory_dir)
    assert res["fixed"] and not res["error"]
    _assert_landed(repo, memory_dir, "twice", before)
    assert scan_duplicate_related(memory_dir) == []


def test_dream_edge_apply_and_undo_edit_the_copy(repo, memory_dir, monkeypatch):
    from memory import dream as D
    from memory.dream_apply import _undo_one_edge
    from memory.dream_discover import _corpus_texts

    _corpus(repo, memory_dir, monkeypatch, {"target_one": _mem("target_one", "a target")})
    before = open(os.path.join(memory_dir, "branch_naming.md"), "rb").read()
    cand = {"kind": "bridge", "source": "branch_naming", "target": "target_one", "cofire": 0.5, "query": "q"}
    ok, why, undo = D._apply_one(memory_dir, cand, "e1", "p1")
    assert ok, why
    T.record_authored_write(memory_dir, OE.resolve(os.path.join(memory_dir, "branch_naming.md")), repo)
    copy = _assert_landed(repo, memory_dir, "branch_naming", before)
    assert "[[target_one]]" in _corpus_texts(memory_dir)["branch_naming"]  # discovery sees it
    ok, why = _undo_one_edge(memory_dir, {"undo": undo})
    assert ok, why
    assert open(copy, "rb").read() == before and _porcelain(repo) == ""


def test_dream_generate_setters_edit_the_copy(repo, memory_dir, monkeypatch):
    from memory.dream_generate import _set_cited_paths, _set_confidence

    _corpus(repo, memory_dir, monkeypatch)
    path = os.path.join(memory_dir, "branch_naming.md")
    before = open(path, "rb").read()
    assert _set_confidence(path, "verified")["changed"]
    copy = _assert_landed(repo, memory_dir, "branch_naming", before)
    assert _set_cited_paths(path, ["src/app.py"])["changed"]
    _assert_landed(repo, memory_dir, "branch_naming", before)
    assert "src/app.py" in open(copy).read()


def test_dedup_merge_edits_both_copies(repo, memory_dir, monkeypatch):
    from memory.deparasite import apply_dedup_merge

    files = {"keep_me": _mem("keep_me", "the survivor"), "fold_me": _mem("fold_me", "the loser")}
    _corpus(repo, memory_dir, monkeypatch, files)
    kb = open(os.path.join(memory_dir, "keep_me.md"), "rb").read()
    fb = open(os.path.join(memory_dir, "fold_me.md"), "rb").read()
    res = apply_dedup_merge(memory_dir, "keep_me", "fold_me")
    assert not res.get("error"), res
    _assert_landed(repo, memory_dir, "keep_me", kb)
    _assert_landed(repo, memory_dir, "fold_me", fb)


def test_a_pack_never_writes_under_an_outbox_copy(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, monkeypatch)
    assert OE.pack_refusal(memory_dir, "branch_naming") is None
    from memory.staleness import set_invalid_after

    set_invalid_after(os.path.join(memory_dir, "branch_naming.md"))
    assert "outbox" in OE.pack_refusal(memory_dir, "branch_naming")


# --------------------------------------------------------------------------- #
# Off is today; withheld is refused; tiers are never redirected
# --------------------------------------------------------------------------- #
def test_with_outbox_off_every_writer_is_byte_identical_to_today(repo, memory_dir, monkeypatch):
    """The same verdicts with no policy: the seam is a no-op (same bytes, same results as
    with the seam patched out), the corpus file is written, and no outbox appears."""
    def run(identity):
        if identity:
            monkeypatch.setattr(OE, "resolve", lambda p: p)
            monkeypatch.setattr(OE, "edit_path", lambda p, *a, **k: (p, None))
        from memory.staleness import set_invalid_after
        from memory.links import add_typed_relation

        a = set_invalid_after(os.path.join(memory_dir, "old_way.md"), "2026-01-01T00:00:00+00:00")
        b = add_typed_relation(os.path.join(memory_dir, "new_way.md"), "supersedes", "old_way")
        files = {n: open(os.path.join(memory_dir, f"{n}.md"), "rb").read() for n in ("old_way", "new_way")}
        return a, b, files

    _corpus(repo, memory_dir, monkeypatch, _pair(), outbox=False)
    got = run(False)
    assert not os.path.exists(OB.outbox_dir(memory_dir))
    assert "old_way.md" in _porcelain(repo) and "new_way.md" in _porcelain(repo)
    subprocess.run(["git", "-C", repo, "checkout", "--", "."], check=True)
    want = run(True)  # the same calls with the seam patched out: today's code path
    assert got == want


def test_declared_but_not_accepted_is_a_direct_write(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, monkeypatch, _pair(), accept=False)
    from memory.staleness import set_invalid_after

    assert set_invalid_after(os.path.join(memory_dir, "old_way.md"))["changed"]
    assert "old_way.md" in _porcelain(repo)
    assert not os.path.exists(OB.outbox_dir(memory_dir))


def test_a_withheld_committed_file_is_refused_never_laundered(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, monkeypatch, {"cache_rule": _EMPTY_BASELINE})
    path = os.path.join(memory_dir, "cache_rule.md")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("an edit nobody consented to\n")
    edited = open(path, "rb").read()
    r = TD.apply("baseline:cache_rule", "rebaseline", memory_dir=memory_dir, repo_root=repo)
    assert r["ok"] is False and "withheld" in r["message"] and "hippo trust review" in r["message"]
    assert open(path, "rb").read() == edited  # no direct fallback
    assert not os.path.exists(os.path.join(OB.outbox_dir(memory_dir), "cache_rule.md"))


def test_a_hand_edited_outbox_copy_is_refused(repo, memory_dir, monkeypatch):
    from memory.staleness import set_invalid_after

    _corpus(repo, memory_dir, monkeypatch, _pair())
    set_invalid_after(os.path.join(memory_dir, "old_way.md"), "2026-01-01T00:00:00+00:00")
    copy = os.path.join(OB.outbox_dir(memory_dir), "old_way.md")
    with open(copy, "a", encoding="utf-8") as fh:
        fh.write("dropped in\n")
    r = set_invalid_after(os.path.join(memory_dir, "old_way.md"), "2026-02-01T00:00:00+00:00")
    assert r["error"] and "withheld" in r["error"]


def test_user_and_private_tier_edits_are_never_redirected(repo, memory_dir, monkeypatch, tmp_path):
    _corpus(repo, memory_dir, monkeypatch)
    priv = str(tmp_path / "priv")
    monkeypatch.setenv("HIPPO_LOCAL_MEMORY_DIR", priv)
    p = write_file(priv, "mine.md", _mem("mine", "private one"))
    assert OE.edit_path(p, repo) == (p, None)
    user = str(tmp_path / "absent-user-tier")
    u = write_file(user, "me.md", _mem("me", "user one"))
    assert OE.edit_path(u, repo) == (u, None)


# --------------------------------------------------------------------------- #
# Removals stay direct, and take the outbox copy with them
# --------------------------------------------------------------------------- #
def test_archive_is_a_direct_change_and_drops_the_copy(repo, memory_dir, monkeypatch):
    from memory.archive import archive_memory
    from memory.staleness import set_invalid_after

    _corpus(repo, memory_dir, monkeypatch, _pair())
    set_invalid_after(os.path.join(memory_dir, "old_way.md"))
    assert "old_way.md" in OB.read_manifest(memory_dir)
    r = archive_memory("old_way", memory_dir, repo)
    assert r["moved"] and not r["error"]
    assert "old_way" in _porcelain(repo)  # the move is a working-tree change, as today
    assert not os.path.exists(os.path.join(OB.outbox_dir(memory_dir), "old_way.md"))
    assert "old_way.md" not in OB.read_manifest(memory_dir)
    assert "old_way" not in (T._trusted_entry(T.gate_repo_root(memory_dir, repo)).get("outbox") or {})
    assert "working-tree change" in OE.removal_line(memory_dir, repo)


def test_promote_removes_directly_drops_the_copy_and_edits_the_outbox_floor(repo, memory_dir, monkeypatch, tmp_path):
    from memory.new_memory import promote_memory
    from memory.staleness import set_invalid_after

    floor = _FLOOR.replace("## User\n", "## User\n- [Tabs](tabs_pref.md) — tabs\n")
    _corpus(repo, memory_dir, monkeypatch, {"tabs_pref": _mem("tabs_pref", "tabs please", mtype="user")})
    write_file(memory_dir, "MEMORY.md", floor)
    git_commit(repo, "floor", _NOW - 4500)
    assert T.mark_trusted(T.gate_repo_root(memory_dir, repo), memory_dir=memory_dir, origin="init")
    assert PC.acknowledge(memory_dir, repo)["ok"]
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", str(tmp_path / "user-tier"))
    set_invalid_after(os.path.join(memory_dir, "branch_naming.md"))  # some other copy exists
    r = promote_memory("tabs_pref", memory_dir=memory_dir, repo_root=repo)
    assert r["promoted"], r
    assert r["floor_removed"]["status"] == "removed"
    assert "(tabs_pref.md)" not in open(os.path.join(OB.outbox_dir(memory_dir), "MEMORY.md")).read()
    assert open(os.path.join(memory_dir, "MEMORY.md")).read() == floor  # committed floor untouched
    assert any("working-tree change" in w for w in r["warnings"])


# --------------------------------------------------------------------------- #
# The small gaps the first slice left
# --------------------------------------------------------------------------- #
def test_check_candidate_sees_outbox_names(repo, memory_dir, monkeypatch):
    from memory.new_memory import check_candidate, write_memory

    _corpus(repo, memory_dir, monkeypatch)
    write_memory("unshipped_one", "an unshipped note", "project", "x", memory_dir=memory_dir, repo_root=repo)
    res = check_candidate("unshipped_one", "an unshipped note", "project", memory_dir=memory_dir, repo_root=repo)
    assert res["route"] != "add" and any(n.get("collision") for n in res["neighbors"])


def test_explicit_link_to_an_outbox_memory_is_not_unresolvable(repo, memory_dir, monkeypatch):
    from memory.new_memory import write_memory

    _corpus(repo, memory_dir, monkeypatch)
    write_memory("first_one", "first unshipped", "project", "x", memory_dir=memory_dir, repo_root=repo)
    res = write_memory("second_one", "second unshipped", "project", "y", memory_dir=memory_dir,
                       repo_root=repo, links=["first_one"])
    assert not any("first_one" in w for w in res.get("warnings") or [])


def test_remove_floor_pointer_takes_a_floor_dir(repo, memory_dir, monkeypatch):
    from memory.new_memory_floor import _remove_floor_pointer

    _corpus(repo, memory_dir, monkeypatch)
    d = OB.floor_target(memory_dir, repo)
    with open(os.path.join(d, "MEMORY.md"), "a", encoding="utf-8") as fh:
        fh.write("- [X](x_one.md) — x\n")
    assert _remove_floor_pointer(memory_dir, "x_one", floor_dir=d)["status"] == "removed"
    assert _porcelain(repo) == ""


def test_trust_review_shows_and_grants_a_withheld_outbox_file(repo, memory_dir, monkeypatch):
    from memory import trust_review as TR
    from memory.doctor_checks_env import DoctorContext
    from memory.doctor_checks_outbox import check_outbox

    _corpus(repo, memory_dir, monkeypatch)
    d = OB.ensure(memory_dir)
    write_file(d, "dropped.md", _mem("dropped", "a file pulled into the outbox"))
    assert OB.withheld(memory_dir, repo) == ["dropped"]
    msg = check_outbox(DoctorContext(memory_dir, repo))["message"]
    assert "hippo trust review" in msg  # the remedy doctor names ...
    assert "trust:outbox:dropped" in _ids(memory_dir, repo, "trust")
    rv = TR.build_review(memory_dir, repo)
    item = next(i for i in rv["items"] if i["stem"] == "outbox:dropped")
    assert item["kind"] == "outbox" and "a file pulled into the outbox" in item["body"]
    fp_before = dict(T._trusted_entry(T.gate_repo_root(memory_dir, repo))["fingerprint"]["files"])
    res = TR.grant(memory_dir, repo, digest=rv["digest"], files=["outbox:dropped"])  # ... works
    assert res["ok"], res
    entry = T._trusted_entry(T.gate_repo_root(memory_dir, repo))
    assert entry["outbox"]["dropped"] == _sha(os.path.join(d, "dropped.md"))
    assert entry["fingerprint"]["files"] == fp_before  # the corpus baseline did not move
    assert OB.withheld(memory_dir, repo) == []


def test_census_counts_withheld_outbox_files(repo, memory_dir, monkeypatch):
    from memory import machine_census as MC

    _corpus(repo, memory_dir, monkeypatch)
    write_file(OB.ensure(memory_dir), "dropped.md", _mem("dropped", "pulled in"))
    rows = [e for e in MC.trust_census()["entries"] if e.get("withholding")]
    assert rows and rows[0]["withholding"]["outbox"] == 1
    assert "1 outbox" in "\n".join(MC._render_trust(MC.trust_census()))


# --------------------------------------------------------------------------- #
# The shadow: no per-prompt recount of the project tier
# --------------------------------------------------------------------------- #
def test_a_shadowing_copy_costs_the_project_tier_no_recount(repo, memory_dir, monkeypatch):
    from memory.staleness import set_invalid_after

    _corpus(repo, memory_dir, monkeypatch, _pair())
    set_invalid_after(os.path.join(memory_dir, "old_way.md"))
    OB.refresh_index(memory_dir)

    def boom(*a, **k):
        raise AssertionError("the project tier was recounted on the prompt path")

    monkeypatch.setattr(RT, "compute_bm25_stats", boom)
    hits = R.recall("deploys go out on mondays", k=5, memory_dir=memory_dir, repo_root=repo)
    hit = next(h for h in hits if h["name"] == "old_way")
    assert hit["root"] == OB.outbox_dir(memory_dir)


def _tier(entries, chunks):
    ents = [{"name": n, "tokens": list(t), "row": None} for n, t in entries]
    chs = [{"entry": i, "tokens": list(t), "row": None} for i, t in chunks]
    stats = B.compute_bm25_stats([e["tokens"] for e in ents] + [c["tokens"] for c in chs])
    return B.LoadedIndex({"entries": ents, "body_chunks": chs, "bm25": json.loads(json.dumps(stats)),
                          "dense_ready": False}, None)


_VOCAB = [f"w{i}" for i in range(12)]
_toks = st.lists(st.sampled_from(_VOCAB), min_size=0, max_size=8)


@st.composite
def _shadowed(draw):
    n = draw(st.integers(2, 7))
    names = [f"m{i}" for i in range(n)]
    proj = [(nm, draw(_toks)) for nm in names]
    pchunks = sorted([(draw(st.integers(0, n - 1)), draw(_toks)) for _ in range(draw(st.integers(0, 6)))])
    shadow = draw(st.lists(st.sampled_from(names), min_size=1, max_size=n, unique=True))
    extra = [f"x{i}" for i in range(draw(st.integers(0, 2)))]
    out = [(nm, draw(_toks)) for nm in shadow + extra]
    ochunks = sorted([(draw(st.integers(0, len(out) - 1)), draw(_toks)) for _ in range(draw(st.integers(0, 3)))])
    return _tier(out, ochunks), _tier(proj, pchunks)


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_shadowed())
def test_property_masked_project_stats_equal_a_recount(tiers):
    outbox, proj = tiers
    merged = RT._merge_loaded_indexes([(outbox, "/outbox", "project"), (proj, "/project", "project")])
    ref = B.compute_bm25_stats(
        [e.get("tokens") or [] for e in merged.entries] + [c.get("tokens") or [] for c in merged.body_chunks]
    )
    got = dict(merged.manifest["bm25"])
    got["postings"] = dict(got["postings"].items())
    assert json.dumps(got) == json.dumps(ref)  # values, dict order and float bits
