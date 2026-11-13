"""``hippo ship``: the outbox, batched into one commit on one new branch off the default-branch ref.

The estate lane's classification cases, written from scratch as fixtures: non-memory content
never ships; bytes the default branch holds are "same"; bytes it once held (the checkout sat
behind) are stale and never roll it back; only bytes that would ship are scanned; a credential
shape holds the file naming the file and the class, never the match; an edit ships only when
made on the default branch's current version; deletions never travel; the preview changes
nothing; the floor ships as a pointer replay; one commit on a fresh timestamped branch whose
message lists the files. Plus: HEAD, the user's index and the working tree are never touched,
a ref is never overwritten, a crash after the ref reads as in flight, and landing empties the
outbox and folds the landed bytes into the consent baseline.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from memory import build_index as B
from memory import new_memory as N
from memory import outbox as OB
from memory import policy_consent as PC
from memory import ship as S
from memory import ship_cli as SC
from memory import trust as T

from .conftest import git_commit, write_file

_FLOOR = "# Agent Memory\n\n## User\n\n## Working Style & Process Feedback\n"


def _mem(name, desc, mtype="project", body="body text"):
    return f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: {mtype}\n---\n{body}\n'


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout.strip()


def _corpus(repo, memory_dir, monkeypatch, files=None, outbox=True):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    write_file(memory_dir, "MEMORY.md", _FLOOR)
    write_file(memory_dir, "branch_naming.md", _mem("branch_naming", "how release branches are named"))
    for n, t in (files or {}).items():
        write_file(memory_dir, f"{n}.md", t)
    if outbox:
        write_file(memory_dir, "hippo.json", json.dumps({"write_path": "outbox"}))
    write_file(repo, ".gitignore", ".hippo/\n.claude/memory.local/\n.claude/memory.outbox/\n")
    git_commit(repo, "seed", 1_700_000_000)
    _origin(repo)
    assert T.mark_trusted(T.gate_repo_root(memory_dir, repo), memory_dir=memory_dir, origin="init")
    if outbox:
        assert PC.acknowledge(memory_dir, repo)["ok"]
    B.build_index(memory_dir, B.default_index_dir(memory_dir))


def _origin(repo, rev="HEAD"):
    """A fake remote-tracking default branch: refs/remotes/origin/main + origin/HEAD."""
    _git(repo, "update-ref", "refs/remotes/origin/main", rev)
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")


def _save(repo, memory_dir, name, desc, mtype="project"):
    r = N.write_memory(name, desc, mtype, f"body of {name}", memory_dir=memory_dir, repo_root=repo)
    assert r["created"] and r.get("write_path") == "outbox", r
    return r


def _edit(repo, memory_dir, name, extra="\nan edit line\n"):
    """An edit made the way hippo makes one in outbox mode: copy up, edit, record."""
    dest = OB.copy_up(memory_dir, name, repo)
    with open(dest, "a", encoding="utf-8") as fh:
        fh.write(extra)
    assert OB.record_write(memory_dir, dest, repo) is None
    return dest


def _ship(memory_dir, *args):
    return SC.main(["--memory-dir", memory_dir, *args])


def _state(repo):
    return (_git(repo, "rev-parse", "HEAD"), _git(repo, "symbolic-ref", "HEAD"),
            _git(repo, "status", "--porcelain"), _git(repo, "ls-files", "-s"))


def _branches(repo):
    return [b for b in _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/hippo").splitlines() if b]


# --------------------------------------------------------------------------- #
# 8. The plan changes nothing; with nothing new it says so and creates nothing
# --------------------------------------------------------------------------- #
def test_preview_changes_nothing_and_nothing_new_creates_nothing(repo, memory_dir, monkeypatch, capsys):
    _corpus(repo, memory_dir, monkeypatch)
    before = _state(repo)
    objects = _git(repo, "count-objects", "-v")
    assert _ship(memory_dir, "--yes") == 0
    assert "nothing to ship" in capsys.readouterr().out and _branches(repo) == []
    _save(repo, memory_dir, "fresh_note", "a fresh note")
    assert _ship(memory_dir) == 0
    out = capsys.readouterr().out
    assert "1 to ship" in out and "preview only" in out and "fresh_note.md (new)" in out
    assert _state(repo) == before and _branches(repo) == []
    assert _git(repo, "count-objects", "-v") == objects  # not even a loose object


# --------------------------------------------------------------------------- #
# 10. One commit on a fresh timestamped branch; HEAD, index and tree untouched
# --------------------------------------------------------------------------- #
def test_one_commit_on_a_fresh_branch_and_nothing_else_moves(repo, memory_dir, monkeypatch, capsys):
    _corpus(repo, memory_dir, monkeypatch)
    _save(repo, memory_dir, "fresh_note", "a fresh note")
    _save(repo, memory_dir, "tabs_pref", "tabs not spaces", mtype="feedback")
    _edit(repo, memory_dir, "branch_naming")
    before = _state(repo)
    assert _ship(memory_dir, "--yes") == 0
    out = capsys.readouterr().out
    assert _state(repo) == before
    (branch,) = _branches(repo)
    assert branch.startswith("hippo/ship-") and "Nothing was pushed" in out and "git hooks do not run" in out
    base = _git(repo, "rev-parse", "origin/main")
    assert _git(repo, "rev-parse", f"{branch}^") == base and _git(repo, "rev-list", "--count", f"{base}..{branch}") == "1"
    changed = _git(repo, "diff", "--name-status", base, branch).splitlines()
    assert sorted(changed) == sorted([
        "M\t.claude/memory/MEMORY.md", "M\t.claude/memory/branch_naming.md",
        "A\t.claude/memory/fresh_note.md", "A\t.claude/memory/tabs_pref.md",
    ])
    msg = _git(repo, "log", "-1", "--format=%B", branch)
    assert msg.startswith("Land 3 memories from the outbox") and ".claude/memory/fresh_note.md (new)" in msg
    assert "branch_naming.md (edit)" in msg and "Co-Authored" not in msg
    floor = _git(repo, "show", f"{branch}:.claude/memory/MEMORY.md")
    assert floor.count("(tabs_pref.md)") == 1 and floor.index("(tabs_pref.md)") > floor.index("## Working")
    # Shipped files stay recallable until they land: in flight, not shipped again.
    plan = S.build_plan(memory_dir, repo)
    assert [w["name"] for w in plan["waiting"]] == ["branch_naming.md", "fresh_note.md", "tabs_pref.md"]
    assert plan["floor"]["state"] == "waiting" and S.counts_line(plan).startswith("0 to ship, 3 waiting on hippo/ship-")
    assert os.path.isfile(os.path.join(OB.outbox_dir(memory_dir), "fresh_note.md"))


def test_a_named_branch_that_exists_is_never_overwritten(repo, memory_dir, monkeypatch, capsys):
    _corpus(repo, memory_dir, monkeypatch)
    _save(repo, memory_dir, "fresh_note", "a fresh note")
    _git(repo, "branch", "hippo/taken")
    tip = _git(repo, "rev-parse", "hippo/taken")
    assert _ship(memory_dir, "--yes", "--branch", "hippo/taken") == 1
    assert "already exists" in capsys.readouterr().out
    assert _git(repo, "rev-parse", "hippo/taken") == tip


# --------------------------------------------------------------------------- #
# 2 and 3: same, and stale (the checkout sat behind; never rolled back)
# --------------------------------------------------------------------------- #
def test_bytes_the_default_branch_holds_are_same_and_land(repo, memory_dir, monkeypatch, capsys):
    _corpus(repo, memory_dir, monkeypatch)
    _save(repo, memory_dir, "fresh_note", "a fresh note", mtype="feedback")
    assert _ship(memory_dir, "--yes") == 0
    (branch,) = _branches(repo)
    # Land it: the default branch and this checkout move to the ship commit.
    _git(repo, "merge", "-q", "--ff-only", branch)
    _origin(repo)
    capsys.readouterr()
    assert _ship(memory_dir, "--yes") == 0
    out = capsys.readouterr().out
    assert "cleared from the outbox" in out and "nothing to ship" in out
    d = OB.outbox_dir(memory_dir)
    assert sorted(n for n in os.listdir(d) if not n.startswith(".") or n == ".floor-base") == []
    assert OB.read_manifest(memory_dir) == {}
    entry = T._trusted_entry(T.gate_repo_root(memory_dir, repo))
    assert entry.get("outbox") == {}
    assert entry["fingerprint"]["files"]["fresh_note"] == T.file_sha256(os.path.join(memory_dir, "fresh_note.md"))
    assert OB.withheld(memory_dir, repo) == [] and _git(repo, "status", "--porcelain") == ""
    from memory import recall as R

    hits = R.recall("a fresh note", k=5, memory_dir=memory_dir, repo_root=repo)
    hit = next(h for h in hits if h["name"] == "fresh_note")  # recalled from the corpus now
    assert hit.get("root") in (None, memory_dir)


def test_landed_but_not_pulled_is_kept(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, monkeypatch)
    _save(repo, memory_dir, "fresh_note", "a fresh note")
    assert _ship(memory_dir, "--yes") == 0
    (branch,) = _branches(repo)
    _origin(repo, branch)  # the default branch has it; this checkout has not pulled
    plan = S.build_plan(memory_dir, repo)
    assert [i["name"] for i in plan["landed_unpulled"]] == ["fresh_note.md"]
    assert _ship(memory_dir, "--yes") == 0
    assert os.path.isfile(os.path.join(OB.outbox_dir(memory_dir), "fresh_note.md"))


def test_bytes_the_default_branch_once_held_are_stale_never_shipped(repo, memory_dir, monkeypatch, capsys):
    """The checkout copied up an older version; the default branch moved since."""
    _corpus(repo, memory_dir, monkeypatch)
    _edit(repo, memory_dir, "branch_naming")
    write_file(memory_dir, "branch_naming.md", _mem("branch_naming", "how release branches are named, revised"))
    git_commit(repo, "upstream edit", 1_700_000_100)
    _origin(repo)
    assert _ship(memory_dir) == 0
    out = capsys.readouterr().out
    assert "stale: branch_naming.md: the default branch changed this file too" in out
    assert "+an edit line" in out and "hippo ship --mine branch_naming" in out
    assert _ship(memory_dir, "--yes") == 0 and _branches(repo) == []
    # --mine: the copy now ships as an edit of the current version (only after a preview).
    assert _ship(memory_dir, "--mine", "branch_naming") == 0
    plan = S.build_plan(memory_dir, repo)
    assert [i["name"] for i in plan["new"]] == ["branch_naming.md"]


def test_drop_removes_a_stale_copy_and_never_touches_the_corpus(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, monkeypatch)
    _edit(repo, memory_dir, "branch_naming")
    write_file(memory_dir, "branch_naming.md", _mem("branch_naming", "moved upstream"))
    git_commit(repo, "upstream edit", 1_700_000_100)
    _origin(repo)
    committed = open(os.path.join(memory_dir, "branch_naming.md"), "rb").read()
    assert _ship(memory_dir, "--drop", "fresh_never") == 1  # not stale: refused
    assert _ship(memory_dir, "--drop", "branch_naming") == 0
    assert not os.path.exists(os.path.join(OB.outbox_dir(memory_dir), "branch_naming.md"))
    assert "branch_naming.md" not in OB.read_manifest(memory_dir)
    assert "branch_naming" not in (T._trusted_entry(T.gate_repo_root(memory_dir, repo)).get("outbox") or {})
    assert open(os.path.join(memory_dir, "branch_naming.md"), "rb").read() == committed


# --------------------------------------------------------------------------- #
# 6. An edit ships only when made on the current version; 7. deletions never travel
# --------------------------------------------------------------------------- #
def test_an_edit_made_on_the_current_version_ships(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, monkeypatch)
    _edit(repo, memory_dir, "branch_naming")
    plan = S.build_plan(memory_dir, repo)
    assert [(i["name"], i["edit"]) for i in plan["new"]] == [("branch_naming.md", True)]


def test_a_new_memory_whose_name_appeared_upstream_is_stale(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, monkeypatch)
    _save(repo, memory_dir, "fresh_note", "mine")
    write_file(memory_dir, "fresh_note.md", _mem("fresh_note", "theirs"))
    git_commit(repo, "upstream", 1_700_000_100)
    _origin(repo)
    os.remove(os.path.join(memory_dir, "fresh_note.md"))
    _git(repo, "checkout", "--", ".")
    (it,) = S.build_plan(memory_dir, repo)["stale"]
    assert it["why"] == "the default branch now has a memory of this name"


def test_deletions_never_travel(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, monkeypatch, {"gone_soon": _mem("gone_soon", "to be removed")})
    os.remove(os.path.join(memory_dir, "gone_soon.md"))  # a working-tree deletion
    _save(repo, memory_dir, "fresh_note", "a fresh note")
    assert _ship(memory_dir, "--yes") == 0
    (branch,) = _branches(repo)
    names = _git(repo, "ls-tree", "--name-only", f"{branch}:.claude/memory").splitlines()
    assert "gone_soon.md" in names and "fresh_note.md" in names
    assert "D\t" not in _git(repo, "diff", "--name-status", "origin/main", branch)


def test_a_deleted_upstream_file_is_stale(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, monkeypatch, {"gone_soon": _mem("gone_soon", "to be removed")})
    _edit(repo, memory_dir, "gone_soon")
    _git(repo, "rm", "-q", ".claude/memory/gone_soon.md")
    git_commit(repo, "upstream delete", 1_700_000_100)
    _origin(repo)
    (it,) = S.build_plan(memory_dir, repo)["stale"]
    assert it["why"] == "the default branch deleted this file"


# --------------------------------------------------------------------------- #
# 1. Non-memory content never ships
# --------------------------------------------------------------------------- #
def test_non_memory_content_in_the_outbox_is_ignored(repo, memory_dir, monkeypatch, tmp_path):
    _corpus(repo, memory_dir, monkeypatch)
    _save(repo, memory_dir, "fresh_note", "a fresh note")
    d = OB.outbox_dir(memory_dir)
    os.makedirs(os.path.join(d, "sub"))
    write_file(d, "sub/inner.md", _mem("inner", "x"))
    write_file(d, "notes.txt", "loose text")
    outside = write_file(str(tmp_path), "elsewhere.md", _mem("elsewhere", "outside"))
    os.symlink(outside, os.path.join(d, "linked.md"))
    plan = S.build_plan(memory_dir, repo)
    assert sorted((i["name"], i["why"]) for i in plan["ignored"]) == [
        ("linked.md", "a symlink"), ("notes.txt", "not a memory file"), ("sub", "a folder")]
    assert _ship(memory_dir, "--yes") == 0
    (branch,) = _branches(repo)
    assert _git(repo, "diff", "--name-only", "origin/main", branch).splitlines() == [".claude/memory/fresh_note.md"]


# --------------------------------------------------------------------------- #
# 4 and 5: only bytes that would ship are scanned; a shape holds, naming the class
# --------------------------------------------------------------------------- #
_SHAPES = {
    "aws": "AKIA" + "ABCDEFGHIJKLMNOP",
    "github": "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "github_fine": "github_pat_" + "11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz",
    "slack": "xoxb-" + "1234567890-abcdefghij",
    "openai": "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2",
    "anthropic": "sk-ant-" + "api03-abcdefghijklmnopqrstu",
    "stripe": "sk_" + "live_" + "A1b2C3d4E5f6G7h8",
    "pem": "-----BEGIN RSA " + "PRIVATE KEY-----",
    "mail": "SG." + "abcdefghijklmnop1234.qrstuvwxyzABCDEF5678",
    "bearer": "Authorization: Bearer " + "abcdefghijklmnopqrstuvwxyz012345",
    "url_password": "https://deploy:" + "hunter2secret@example.com/x",
}


def test_every_credential_shape_is_held_by_class_never_shipped(repo, memory_dir, monkeypatch, capsys):
    _corpus(repo, memory_dir, monkeypatch)
    for key, shape in _SHAPES.items():
        _save(repo, memory_dir, f"shape_{key}", f"note about {key}")
        p = os.path.join(OB.outbox_dir(memory_dir), f"shape_{key}.md")
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(f"value {shape}\n")
        assert OB.record_write(memory_dir, p, repo) is None
    for i, ph in enumerate(["https://u:${{ secrets.PW }}@h/x", "https://u:<password>@h/x",
                            "https://u:$TOKEN@h/x", "https://u:***@h/x"]):
        _save(repo, memory_dir, f"placeholder_{i}", "a placeholder")
        p = os.path.join(OB.outbox_dir(memory_dir), f"placeholder_{i}.md")
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(f"url {ph}\n")
        assert OB.record_write(memory_dir, p, repo) is None
    plan = S.build_plan(memory_dir, repo)
    held = {i["stem"]: i["classes"] for i in plan["held"]}
    assert sorted(held) == sorted(f"shape_{k}" for k in _SHAPES)
    assert sorted(i["stem"] for i in plan["new"]) == [f"placeholder_{i}" for i in range(4)]
    assert _ship(memory_dir, "--yes") == 0
    out = capsys.readouterr().out
    for shape in _SHAPES.values():
        assert shape not in out  # the class is named, never the match
    (branch,) = _branches(repo)
    from memory.secrets import scan_text

    shipped = _git(repo, "diff", "--name-only", "origin/main", branch).splitlines()
    assert all("shape_" not in f for f in shipped)
    for f in shipped:
        assert scan_text(_git(repo, "show", f"{branch}:{f}"), entropy=False) == []


def test_only_bytes_that_would_ship_are_scanned(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, monkeypatch, {"has_key": _mem("has_key", "x", body="AKIA" + "ABCDEFGHIJKLMNOP")})
    dest = OB.copy_up(memory_dir, "has_key", repo)  # same bytes as the base: same, never scanned
    calls = []
    real = S.scan_classes
    monkeypatch.setattr(S, "scan_classes", lambda t: calls.append(t) or real(t))
    plan = S.build_plan(memory_dir, repo)
    assert [i["name"] for i in plan["landed"]] == ["has_key.md"] and calls == []
    assert os.path.isfile(dest)


def test_a_withheld_outbox_file_is_held_not_laundered(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, monkeypatch)
    write_file(OB.ensure(memory_dir), "dropped.md", _mem("dropped", "pulled in"))
    (it,) = S.build_plan(memory_dir, repo)["held"]
    assert it["classes"] == ["bytes hippo did not write on this machine"]


# --------------------------------------------------------------------------- #
# 9. The floor: a replay, both sides' pointers kept
# --------------------------------------------------------------------------- #
def test_the_floor_replays_onto_a_moved_base_keeping_both_sides(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, monkeypatch)
    _save(repo, memory_dir, "mine_pref", "mine", mtype="feedback")
    floor = os.path.join(memory_dir, "MEMORY.md")
    text = open(floor).read().replace("## User\n", "## User\n- [Theirs](theirs_pref.md) — theirs\n")
    write_file(memory_dir, "MEMORY.md", text)
    write_file(memory_dir, "theirs_pref.md", _mem("theirs_pref", "theirs", mtype="user"))
    git_commit(repo, "upstream floor", 1_700_000_100)
    _origin(repo)
    plan = S.build_plan(memory_dir, repo)
    assert plan["floor"]["state"] == "new" and plan["floor"]["adds"] == 1
    assert _ship(memory_dir, "--yes") == 0
    (branch,) = _branches(repo)
    shipped = _git(repo, "show", f"{branch}:.claude/memory/MEMORY.md")
    assert "(theirs_pref.md)" in shipped and "(mine_pref.md)" in shipped
    base_lines = _git(repo, "show", "origin/main:.claude/memory/MEMORY.md").splitlines()
    assert all(ln in shipped.splitlines() for ln in base_lines)  # no lost line


def test_a_hand_edited_floor_copy_is_stale_not_replayed(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, monkeypatch)
    _save(repo, memory_dir, "mine_pref", "mine", mtype="feedback")
    with open(os.path.join(OB.outbox_dir(memory_dir), "MEMORY.md"), "a", encoding="utf-8") as fh:
        fh.write("a hand-written line\n")
    fl = S.build_plan(memory_dir, repo)["floor"]
    assert fl["state"] == "stale" and "pointer adds and removes" in fl["why"]


def test_a_floor_copy_with_no_recorded_base_is_stale(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, monkeypatch)
    _save(repo, memory_dir, "mine_pref", "mine", mtype="feedback")
    os.remove(os.path.join(OB.outbox_dir(memory_dir), OB.FLOOR_BASE_NAME))
    fl = S.build_plan(memory_dir, repo)["floor"]
    assert fl["state"] == "stale" and fl["why"] == "no recorded base text"


def test_floor_replay_skips_an_add_already_there_and_a_remove_already_gone():
    base = "# M\n\n## User\n- [A](a.md) — a\n"
    target = "# M\n\n## User\n- [B](b.md) — b\n"
    adds, removes = S.floor_changes(base, "# M\n\n## User\n- [B](b.md) — b\n")
    text, n_add, n_rm = S.replay_floor(target, adds, removes)
    assert (n_add, n_rm) == (0, 0) and text == target


def test_a_gate_finding_refuses_the_ship(repo, memory_dir, monkeypatch, capsys):
    _corpus(repo, memory_dir, monkeypatch)
    write_file(memory_dir, "hippo.json", json.dumps({"write_path": "outbox", "budgets": {"files": 2}}))
    git_commit(repo, "budget", 1_700_000_100)
    _origin(repo)
    assert T.mark_trusted(T.gate_repo_root(memory_dir, repo), memory_dir=memory_dir, origin="init")
    assert PC.acknowledge(memory_dir, repo)["ok"]
    _save(repo, memory_dir, "one_more", "one")
    _save(repo, memory_dir, "two_more", "two")
    assert _ship(memory_dir) == 0
    assert "refuses while that stands" in capsys.readouterr().out
    assert _ship(memory_dir, "--yes") == 1 and _branches(repo) == []


# --------------------------------------------------------------------------- #
# Trust, crash, base resolution
# --------------------------------------------------------------------------- #
def test_an_untrusted_corpus_ships_nothing(repo, memory_dir, monkeypatch, capsys):
    _corpus(repo, memory_dir, monkeypatch)
    _save(repo, memory_dir, "fresh_note", "a fresh note")
    assert T.untrust(T.gate_repo_root(memory_dir, repo))
    assert _ship(memory_dir, "--yes") == 1 and _branches(repo) == []
    assert "not trusted" in capsys.readouterr().out


def test_a_crash_after_the_ref_reads_as_in_flight(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, monkeypatch)
    _save(repo, memory_dir, "fresh_note", "a fresh note")
    monkeypatch.setattr(S, "stamp_shipped", lambda *a, **k: False)  # the stamp never lands
    assert _ship(memory_dir, "--yes") == 0
    (branch,) = _branches(repo)
    assert "shipped" not in OB.read_manifest(memory_dir)["fresh_note.md"]
    plan = S.build_plan(memory_dir, repo)
    assert [(w["name"], w["branch"]) for w in plan["waiting"]] == [("fresh_note.md", branch)]


def test_a_gone_branch_makes_the_file_new_again_and_an_edit_ships_afresh(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, monkeypatch)
    _save(repo, memory_dir, "fresh_note", "a fresh note")
    assert _ship(memory_dir, "--yes") == 0
    (branch,) = _branches(repo)
    _git(repo, "branch", "-D", branch)
    assert [i["name"] for i in S.build_plan(memory_dir, repo)["new"]] == ["fresh_note.md"]


def test_base_resolution_and_override(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, monkeypatch)
    top = S.corpus_location(memory_dir)[0]
    assert S.resolve_base(top)["ref"] == "origin/main"
    _git(repo, "symbolic-ref", "-d", "refs/remotes/origin/HEAD")
    _git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    local = _git(repo, "symbolic-ref", "--short", "HEAD")
    _git(repo, "config", "init.defaultBranch", local)
    assert S.resolve_base(top)["ref"] == local
    assert S.resolve_base(top, "nope-not-a-ref") is None


def test_json_output_names_classes_and_counts(repo, memory_dir, monkeypatch, capsys):
    _corpus(repo, memory_dir, monkeypatch)
    _save(repo, memory_dir, "fresh_note", "a fresh note")
    assert _ship(memory_dir, "--json") == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["counts"].startswith("1 to ship") and doc["new"][0]["name"] == "fresh_note.md"


# --------------------------------------------------------------------------- #
# 10. A promote's floor remove replays, bare pointer or link
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("line", ["- tabs_pref — tabs, never spaces", "- [Tabs](tabs_pref.md) — tabs"],
                         ids=["bare", "link"])
def test_a_promoted_memorys_floor_pointer_removal_ships(repo, memory_dir, monkeypatch, tmp_path, line):
    """The promote takes the memory out of the corpus and its pointer out of the outbox floor
    copy; the plan reads that as a pointer remove, a bare line too, though the memory is no
    longer in the corpus or the outbox (the base ref still holds it)."""
    _corpus(repo, memory_dir, monkeypatch, {"tabs_pref": _mem("tabs_pref", "tabs please", mtype="user")})
    write_file(memory_dir, "MEMORY.md", _FLOOR.replace("## User\n", f"## User\n{line}\n"))
    git_commit(repo, "floor", 1_700_000_050)
    _origin(repo)
    assert T.mark_trusted(T.gate_repo_root(memory_dir, repo), memory_dir=memory_dir, origin="init")
    assert PC.acknowledge(memory_dir, repo)["ok"]
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", str(tmp_path / "user-tier"))
    r = N.promote_memory("tabs_pref", memory_dir=memory_dir, repo_root=repo)
    assert r["promoted"] and r["floor_removed"]["status"] == "removed", r
    fl = S.build_plan(memory_dir, repo)["floor"]
    assert fl["state"] == "new", fl.get("why")
    assert (fl["adds"], fl["removes"]) == (0, 1) and fl["replayed"] == _FLOOR
    assert _ship(memory_dir, "--yes") == 0
    (branch,) = _branches(repo)
    assert _git(repo, "show", f"{branch}:.claude/memory/MEMORY.md") + "\n" == _FLOOR
