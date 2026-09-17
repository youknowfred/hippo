"""SHP-7: a session launched in a LINKED git worktree resolves the MAIN working tree's corpus.

The field bug (em-growth-labs, 2026-08-19 / 09-09 / 09-16): every session there is launched
inside ``<primary>/.claude/worktrees/<name>/``, and each worktree carries its own git-checked-
out COPY of ``.claude/memory``. ``resolve_dirs`` preferred the nested copy, so doctor reported
a healthy corpus that was the branch's stale snapshot, ``.claude/.memory-pending`` queues were
dead copies with different inodes, and captures / index builds landed in files that vanished
with the worktree while the live corpus never changed. The pins:

  - resolution from inside a linked worktree (cwd OR CLAUDE_PROJECT_DIR) targets the main
    tree's corpus — the inode of MEMORY.md matches the main tree's, not the worktree's copy;
  - ``repo_root`` moves WITH it (trust rows, the projects-dir symlink, and every derived
    sibling dir — pending/index/telemetry — key on the same tree);
  - a subdir launch inside the worktree mirrors into the main tree (SHP-2's nested-wins
    still applies there); a branch-only subdir falls back to the main tree's root;
  - ``HIPPO_CORPUS_ROOT`` pins the start and disables the redirect; an explicit
    ``HIPPO_MEMORY_DIR`` is never redirected (today's hermetic-test behavior);
  - a main tree WITHOUT a corpus keeps the worktree-local one (branch-only corpus);
  - single checkouts, submodules, and bare-repo worktrees are untouched;
  - ``launch_root`` still names the worktree (session-local git facts read there);
  - doctor names WHICH tree resolved in one line and flags a dead pending queue with seeds;
  - the hooks' bash guard passes for a linked worktree whose main tree has the corpus.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

import memory.provenance_env as PE
from memory import doctor as D
from memory import presence as PR
from memory import provenance as P
from memory import trust as TR
from memory.build_index import default_index_dir
from memory.capture_queue import default_pending_dir
from memory.telemetry_store import default_telemetry_dir

_PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugin"))
_MEM = '---\nname: zebra\ndescription: "zebra runbook"\nmetadata:\n  type: project\n---\nbody\n'


def _git(args, cwd) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _init(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    _git(["init", "-q"], path)
    _git(["config", "user.email", "t@example.com"], path)
    _git(["config", "user.name", "t"], path)


def _seed_corpus(root: str, sub: str = "") -> str:
    md = os.path.join(root, sub, ".claude", "memory") if sub else os.path.join(root, ".claude", "memory")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# floor\n")
    with open(os.path.join(md, "zebra.md"), "w", encoding="utf-8") as fh:
        fh.write(_MEM)
    return md


def _clear_caches() -> None:
    PE._GIT_ROOT_CACHE.clear()
    PE._MAIN_TREE_CACHE.clear()


@pytest.fixture(autouse=True)
def _default_env(monkeypatch):
    """The production shape: no explicit dir overrides, no harness project dir (set per test)."""
    monkeypatch.delenv("HIPPO_MEMORY_DIR", raising=False)
    monkeypatch.delenv("HIPPO_CORPUS_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _clear_caches()
    yield
    _clear_caches()


@pytest.fixture
def linked(tmp_path):
    """A main checkout with a COMMITTED corpus plus a linked worktree under
    ``.claude/worktrees/`` — the exact em-growth-labs layout. The worktree's checkout
    carries its own copy of ``.claude/memory`` (different inodes: git wrote it twice)."""
    main = str(tmp_path / "main")
    _init(main)
    md = _seed_corpus(main)
    _git(["add", "."], main)
    _git(["commit", "-q", "-m", "seed"], main)
    wt = os.path.join(main, ".claude", "worktrees", "wt-1")
    _git(["worktree", "add", "-q", "-b", "feature", wt], main)
    assert os.path.isfile(os.path.join(wt, ".claude", "memory", "MEMORY.md"))
    _clear_caches()
    return {"main": main, "wt": wt, "md": md}


def _inode(path: str) -> int:
    return os.stat(path).st_ino


# --------------------------------------------------------------------------- #
# The core pin: a worktree launch resolves the MAIN tree's corpus (same inode)
# --------------------------------------------------------------------------- #
def test_cwd_inside_linked_worktree_resolves_main_tree_corpus(linked, monkeypatch):
    monkeypatch.chdir(linked["wt"])
    memory_dir, repo_root = P.resolve_dirs()
    assert os.path.realpath(memory_dir) == os.path.realpath(linked["md"])
    assert os.path.realpath(repo_root) == os.path.realpath(linked["main"])
    live = os.path.join(memory_dir, "MEMORY.md")
    copy = os.path.join(linked["wt"], ".claude", "memory", "MEMORY.md")
    assert _inode(live) == _inode(os.path.join(linked["md"], "MEMORY.md"))
    assert _inode(live) != _inode(copy)  # the worktree's copy is a different file entirely
    info = P.resolve_corpus_start()
    assert info["tree"] == "main-tree"
    assert os.path.realpath(info["linked_worktree"]) == os.path.realpath(linked["wt"])
    assert os.path.realpath(info["main_tree"]) == os.path.realpath(linked["main"])


def test_claude_project_dir_inside_linked_worktree_redirects(linked, monkeypatch, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # cwd is irrelevant when the harness set the project dir
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", linked["wt"])
    memory_dir, repo_root = P.resolve_dirs()
    assert os.path.realpath(memory_dir) == os.path.realpath(linked["md"])
    assert os.path.realpath(repo_root) == os.path.realpath(linked["main"])


def test_every_derived_dir_moves_with_the_corpus(linked, monkeypatch):
    """A corpus in one tree with an index/queue/ledger in another is the class of lie SHP-7
    closes — the three siblings derive from memory_dir, so they land beside the LIVE corpus."""
    monkeypatch.chdir(linked["wt"])
    for var in ("HIPPO_PENDING_DIR", "HIPPO_INDEX_DIR", "HIPPO_TELEMETRY_DIR"):
        monkeypatch.delenv(var, raising=False)
    memory_dir, _ = P.resolve_dirs()
    live_claude = os.path.realpath(os.path.join(linked["main"], ".claude"))
    for derived in (default_pending_dir(memory_dir), default_index_dir(memory_dir), default_telemetry_dir(memory_dir)):
        assert os.path.realpath(os.path.dirname(derived)) == live_claude
        assert ".claude/worktrees" not in os.path.realpath(derived)


def test_launch_root_stays_the_worktree_while_repo_root_is_main(linked, monkeypatch):
    """Session-local git facts (the capture diff, the presence branch/head) read the tree the
    session WORKS in; the corpus and its identity read the main tree."""
    monkeypatch.chdir(linked["wt"])
    _md, repo_root = P.resolve_dirs()
    assert os.path.realpath(P.launch_root()) == os.path.realpath(linked["wt"])
    assert os.path.realpath(repo_root) == os.path.realpath(linked["main"])


def test_trust_keys_on_the_main_tree_from_a_worktree(linked, monkeypatch):
    """The SEC-1 'APPLY REFUSED — corpus untrusted' from a worktree was WRONG ROOT: the row is
    keyed by repo root. Trusting the main tree now trusts the corpus a worktree resolves."""
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    assert TR.mark_trusted(linked["main"], linked["md"], origin="init")
    monkeypatch.chdir(linked["wt"])
    memory_dir, repo_root = P.resolve_dirs()
    gate = TR.gate_repo_root(memory_dir, repo_root)
    assert os.path.realpath(gate) == os.path.realpath(linked["main"])
    assert TR.is_trusted(gate)
    assert not TR.is_trusted(linked["wt"])  # the worktree itself was never (and need never be) trusted


# --------------------------------------------------------------------------- #
# Subdir launches inside the worktree (SHP-2 composes with SHP-7)
# --------------------------------------------------------------------------- #
def test_subdir_launch_inside_worktree_mirrors_into_main_tree(tmp_path, monkeypatch):
    main = str(tmp_path / "main")
    _init(main)
    _seed_corpus(main)
    nested = _seed_corpus(main, os.path.join("packages", "web"))
    _git(["add", "."], main)
    _git(["commit", "-q", "-m", "seed"], main)
    wt = os.path.join(main, ".claude", "worktrees", "wt-1")
    _git(["worktree", "add", "-q", "-b", "feature", wt], main)
    _clear_caches()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", os.path.join(wt, "packages", "web"))
    memory_dir, repo_root = P.resolve_dirs()
    assert os.path.realpath(memory_dir) == os.path.realpath(nested)  # nested wins — in the MAIN tree
    assert os.path.realpath(repo_root) == os.path.realpath(main)


def test_branch_only_subdir_falls_back_to_main_tree_root(linked, monkeypatch):
    new_pkg = os.path.join(linked["wt"], "packages", "brand-new")
    os.makedirs(new_pkg)  # exists only on the branch; the main tree has no such dir
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", new_pkg)
    memory_dir, repo_root = P.resolve_dirs()
    assert os.path.realpath(memory_dir) == os.path.realpath(linked["md"])
    assert os.path.realpath(repo_root) == os.path.realpath(linked["main"])


# --------------------------------------------------------------------------- #
# Overrides and the cases that must NOT redirect
# --------------------------------------------------------------------------- #
def test_hippo_corpus_root_pins_the_start_and_disables_the_redirect(linked, monkeypatch):
    monkeypatch.chdir(linked["wt"])
    monkeypatch.setenv("HIPPO_CORPUS_ROOT", linked["wt"])
    memory_dir, repo_root = P.resolve_dirs()
    assert os.path.realpath(memory_dir) == os.path.realpath(os.path.join(linked["wt"], ".claude", "memory"))
    assert os.path.realpath(repo_root) == os.path.realpath(linked["wt"])
    info = P.resolve_corpus_start()
    assert (info["tree"], info["override"]) == ("override", "HIPPO_CORPUS_ROOT")
    assert os.path.realpath(P.launch_root()) == os.path.realpath(linked["wt"])


def test_hippo_corpus_root_can_point_a_worktree_session_anywhere(linked, monkeypatch, tmp_path):
    other = str(tmp_path / "other")
    _init(other)
    other_md = _seed_corpus(other)
    monkeypatch.chdir(linked["wt"])
    monkeypatch.setenv("HIPPO_CORPUS_ROOT", other)
    memory_dir, repo_root = P.resolve_dirs()
    assert os.path.realpath(memory_dir) == os.path.realpath(other_md)
    assert os.path.realpath(repo_root) == os.path.realpath(other)


def test_explicit_memory_dir_is_never_redirected(linked, monkeypatch, tmp_path):
    """Today's hermetic-test contract: HIPPO_MEMORY_DIR is used as-is, and repo_root stays
    the launch tree's toplevel — no worktree magic on an explicit path."""
    explicit = str(tmp_path / "explicit")
    os.makedirs(explicit)
    monkeypatch.chdir(linked["wt"])
    monkeypatch.setenv("HIPPO_MEMORY_DIR", explicit)
    memory_dir, repo_root = P.resolve_dirs()
    assert memory_dir == explicit
    assert os.path.realpath(repo_root) == os.path.realpath(linked["wt"])
    assert P.resolve_corpus_start()["override"] == "HIPPO_MEMORY_DIR"


def test_main_tree_without_a_corpus_keeps_the_worktree_local_one(tmp_path, monkeypatch):
    """A corpus that exists only on the branch is the only corpus there is — not an error."""
    main = str(tmp_path / "main")
    _init(main)
    with open(os.path.join(main, "README"), "w", encoding="utf-8") as fh:
        fh.write("x\n")
    _git(["add", "."], main)
    _git(["commit", "-q", "-m", "init"], main)
    wt = os.path.join(main, ".claude", "worktrees", "wt-1")
    _git(["worktree", "add", "-q", "-b", "feature", wt], main)
    wt_md = _seed_corpus(wt)
    _clear_caches()
    monkeypatch.chdir(wt)
    memory_dir, repo_root = P.resolve_dirs()
    assert os.path.realpath(memory_dir) == os.path.realpath(wt_md)
    assert os.path.realpath(repo_root) == os.path.realpath(wt)
    assert P.resolve_corpus_start()["tree"] == "linked-worktree"


def test_single_checkout_is_byte_identical_to_before(linked, monkeypatch):
    monkeypatch.chdir(linked["main"])
    memory_dir, repo_root = P.resolve_dirs()
    assert os.path.realpath(memory_dir) == os.path.realpath(linked["md"])
    assert os.path.realpath(repo_root) == os.path.realpath(linked["main"])
    info = P.resolve_corpus_start()
    assert info["tree"] == "checkout" and info["linked_worktree"] is None
    assert P.main_worktree_root(linked["main"]) is None


def test_main_worktree_root_reads_a_relative_gitdir_pointer(linked):
    """git 2.48+'s ``--relative-paths`` writes ``gitdir: ../../../.git/worktrees/<name>``."""
    dot_git = os.path.join(linked["wt"], ".git")
    with open(dot_git, "r", encoding="utf-8") as fh:
        absolute = fh.read().strip()
    assert absolute.startswith("gitdir: ")
    rel = os.path.relpath(absolute[len("gitdir: "):], linked["wt"])
    with open(dot_git, "w", encoding="utf-8") as fh:
        fh.write(f"gitdir: {rel}\n")
    _clear_caches()
    assert os.path.realpath(P.main_worktree_root(linked["wt"])) == os.path.realpath(linked["main"])


def test_submodule_shaped_git_file_is_not_a_linked_worktree(tmp_path):
    sub = tmp_path / "super" / "sub"
    sub.mkdir(parents=True)
    (sub / ".git").write_text("gitdir: ../.git/modules/sub\n", encoding="utf-8")
    assert P.main_worktree_root(str(sub)) is None


def test_bare_repo_worktree_has_no_main_tree_and_is_left_alone(linked, tmp_path, monkeypatch):
    bare = str(tmp_path / "bare.git")
    _git(["clone", "-q", "--bare", linked["main"], bare], str(tmp_path))
    bwt = str(tmp_path / "bwt")
    _git(["worktree", "add", "-q", bwt, "feature"], bare)
    _clear_caches()
    assert P.main_worktree_root(bwt) is None  # `git worktree list` lists the bare repo first
    monkeypatch.chdir(bwt)
    memory_dir, repo_root = P.resolve_dirs()
    assert os.path.realpath(memory_dir) == os.path.realpath(os.path.join(bwt, ".claude", "memory"))
    assert os.path.realpath(repo_root) == os.path.realpath(bwt)


def test_main_worktree_root_is_memoized_per_toplevel(linked, monkeypatch):
    first = P.main_worktree_root(linked["wt"])
    assert first
    # Once cached, the probe never touches the filesystem again for that toplevel.
    monkeypatch.setattr(PE.os.path, "isfile", lambda p: (_ for _ in ()).throw(AssertionError("re-probed")))
    assert P.main_worktree_root(linked["wt"]) == first


# --------------------------------------------------------------------------- #
# Doctor: WHICH tree, in one line — and the dead copies a worktree carries
# --------------------------------------------------------------------------- #
def _ctx(memory_dir: str, repo_root: str) -> D.DoctorContext:
    return D.DoctorContext(memory_dir, repo_root, plugin_data="", plugin_root="")


def test_doctor_resolution_line_names_the_main_tree_and_the_worktree(linked, monkeypatch):
    monkeypatch.chdir(linked["wt"])
    md, rr = P.resolve_dirs()
    r = D.check_corpus_resolution(_ctx(md, rr))
    assert r["status"] == "ok"
    assert "tree: MAIN working tree" in r["message"]
    assert linked["main"] in r["message"] and linked["wt"] in r["message"]
    assert "redirected from linked worktree" in r["message"]


def test_doctor_resolution_line_names_a_plain_checkout(linked, monkeypatch):
    monkeypatch.chdir(linked["main"])
    md, rr = P.resolve_dirs()
    r = D.check_corpus_resolution(_ctx(md, rr))
    assert r["status"] == "ok"
    assert "tree: this checkout" in r["message"] and "not a linked worktree" in r["message"]


def test_doctor_resolution_line_names_an_override(linked, monkeypatch):
    monkeypatch.chdir(linked["wt"])
    monkeypatch.setenv("HIPPO_CORPUS_ROOT", linked["wt"])
    md, rr = P.resolve_dirs()
    r = D.check_corpus_resolution(_ctx(md, rr))
    assert "tree: OVERRIDE via HIPPO_CORPUS_ROOT=" in r["message"]
    assert "no worktree redirect" in r["message"]


def test_doctor_worktree_copies_is_na_outside_a_redirect(linked, monkeypatch):
    monkeypatch.chdir(linked["main"])
    md, rr = P.resolve_dirs()
    r = D.check_worktree_copies(_ctx(md, rr))
    assert r["status"] == "ok" and r["message"].startswith("worktree copies: n/a")


def test_doctor_worktree_copies_names_the_snapshot_when_clean(linked, monkeypatch):
    monkeypatch.chdir(linked["wt"])
    md, rr = P.resolve_dirs()
    r = D.check_worktree_copies(_ctx(md, rr))
    assert r["status"] == "ok"
    assert "no dead derived dirs" in r["message"]
    assert "committed snapshot, not the live corpus" in r["message"]


def test_doctor_worktree_copies_warns_on_a_dead_pending_queue_with_seeds(linked, monkeypatch):
    """The 2026-08-19 shape: a 39-item queue in the worktree, same filenames, different inodes."""
    dead = os.path.join(linked["wt"], ".claude", ".memory-pending")
    os.makedirs(dead)
    for i in range(3):
        with open(os.path.join(dead, f"seed-{i}.json"), "w", encoding="utf-8") as fh:
            json.dump({"session_id": f"s{i}"}, fh)
    os.makedirs(os.path.join(linked["wt"], ".claude", ".memory-index"))
    monkeypatch.chdir(linked["wt"])
    md, rr = P.resolve_dirs()
    r = D.check_worktree_copies(_ctx(md, rr))
    assert r["status"] == "warn"
    assert "3 capture seed(s) sit in a DEAD pending queue" in r["message"]
    assert dead in r["message"]
    assert os.path.join(linked["main"], ".claude", ".memory-pending") in r["message"]  # the live queue, named
    assert "index " + os.path.join(linked["wt"], ".claude", ".memory-index") in r["message"]
    assert "2 dead derived dir(s)" in r["message"]


def test_doctor_worktree_copies_is_ok_for_empty_dead_dirs(linked, monkeypatch):
    os.makedirs(os.path.join(linked["wt"], ".claude", ".memory-telemetry"))
    monkeypatch.chdir(linked["wt"])
    md, rr = P.resolve_dirs()
    r = D.check_worktree_copies(_ctx(md, rr))
    assert r["status"] == "ok"
    assert "1 dead derived dir(s)" in r["message"] and "stale copies nothing reads" in r["message"]


def test_doctor_registers_worktree_copies_right_after_resolution():
    labels = [label for label, _ in D.CHECKS]
    assert labels.index("worktree_copies") == labels.index("resolution") + 1


def test_doctor_render_from_a_worktree_is_deterministic_and_complete(linked, monkeypatch):
    monkeypatch.chdir(linked["wt"])
    md, rr = P.resolve_dirs()
    ctx = _ctx(md, rr)
    first, second = D.render(ctx), D.render(ctx)
    assert first == second
    assert first.count("\n") + 1 == len(D.CHECKS)
    assert "tree: MAIN working tree" in first


# --------------------------------------------------------------------------- #
# Presence stays per-WORKING-TREE although linked worktrees now share one telemetry dir
# --------------------------------------------------------------------------- #
def test_presence_docs_carry_their_tree_and_other_trees_are_not_this_tree(linked, monkeypatch):
    monkeypatch.delenv("HIPPO_DISABLE_PRESENCE", raising=False)
    monkeypatch.delenv("HIPPO_TELEMETRY_DIR", raising=False)
    monkeypatch.chdir(linked["wt"])
    md, _rr = P.resolve_dirs()
    td = default_telemetry_dir(md)
    assert os.path.realpath(os.path.dirname(td)) == os.path.realpath(os.path.join(linked["main"], ".claude"))
    PR.write_presence(md, linked["wt"], session_id="wt-session")
    own = PR._read_doc(PR._presence_path(td, "wt-session"))
    assert own["tree"] == os.path.realpath(linked["wt"])
    assert own["branch"] == "feature"  # the LAUNCH tree's branch, not the main tree's
    # A main-tree session's doc lands in the SAME dir (the corpus's telemetry moved together)...
    PR.write_presence(md, linked["main"], session_id="main-session")
    # ...but is not "this working tree" from the worktree's point of view, while a legacy
    # (unstamped) doc still counts as same-tree, exactly as before SHP-7.
    PR._write_doc(td, "legacy-session", {"session_id": "legacy-session", "branch": "old", "head": "abc", "ts": 1e12})
    others = PR._fresh_others(td, "wt-session", os.path.realpath(linked["wt"]))
    assert [d["branch"] for d in others] == ["old"]
    main_branch = _git(["symbolic-ref", "--short", "HEAD"], linked["main"]).strip()
    unscoped = PR._fresh_others(td, "wt-session")  # no tree given: every fresh doc, as before
    assert sorted(d["branch"] for d in unscoped) == sorted(["old", main_branch])


# --------------------------------------------------------------------------- #
# The hooks' bash guard (COR-10) passes for a worktree whose MAIN tree has the corpus
# --------------------------------------------------------------------------- #
def _probe(fn: str, cwd: str) -> int:
    script = f'. "{_PLUGIN_ROOT}/hooks/_resolve_py.sh"; {fn}'
    return subprocess.run(["bash", "-c", script], cwd=cwd, capture_output=True).returncode


def test_shell_guard_passes_in_a_linked_worktree_without_its_own_copy(tmp_path):
    """An UNCOMMITTED main-tree corpus: the worktree's checkout has no .claude/memory at all,
    so the old ``[ -d .claude/memory ]`` guard bailed before Python ever resolved the main
    tree. The probe now consults the main tree — one `git rev-parse`, only for this shape."""
    main = str(tmp_path / "main")
    _init(main)
    with open(os.path.join(main, "README"), "w", encoding="utf-8") as fh:
        fh.write("x\n")
    _git(["add", "."], main)
    _git(["commit", "-q", "-m", "init"], main)
    wt = os.path.join(main, ".claude", "worktrees", "wt-1")
    _git(["worktree", "add", "-q", "-b", "feature", wt], main)
    assert _probe("hippo_corpus_present", wt) != 0  # nothing anywhere yet
    assert _probe("hippo_floor_present", wt) != 0
    _seed_corpus(main)  # main tree gets a corpus, uncommitted — the worktree still lacks a copy
    assert not os.path.isdir(os.path.join(wt, ".claude", "memory"))
    assert _probe("hippo_corpus_present", wt) == 0
    assert _probe("hippo_floor_present", wt) == 0
    assert _probe("hippo_corpus_present", main) == 0


def test_shell_guard_still_bails_in_a_plain_checkout_without_a_corpus(tmp_path):
    plain = str(tmp_path / "plain")
    _init(plain)
    assert _probe("hippo_corpus_present", plain) != 0
    assert _probe("hippo_main_tree", plain) != 0
    not_git = str(tmp_path / "not-git")
    os.makedirs(not_git)
    assert _probe("hippo_corpus_present", not_git) != 0
