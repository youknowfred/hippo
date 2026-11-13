"""OUT-5: consent for the bytes a session's own file tool wrote on this machine (opt-in).

A corpus whose committed ``hippo.json`` sets ``"consent": {"session_writes": true}``, once
this machine accepted it, lets the PreToolUse hook note a write's prior state and predicted
bytes and the PostToolUse hook consent to exactly those bytes. Everything else grants
nothing: an unconsented prior, a hash mismatch, no note, a path trick, an untrusted or
legacy corpus, the key off or not accepted. With the key off the trust file is
byte-identical. Driven in process and through the real hook scripts.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

from memory import policy_consent as PC
from memory import session_consent as SC
from memory import telemetry_rollup as TRL
from memory import trust as T
from memory import trust_review as TR

from .conftest import git_commit, write_file

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

_PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugin"))
_PRE = os.path.join(_PLUGIN_ROOT, "hooks", "memory_pre_tool.sh")
_POST = os.path.join(_PLUGIN_ROOT, "hooks", "memory_post_tool.sh")

ON = {"consent": {"session_writes": True}}
SID = "sess-1"
NOTE = '---\nname: note\ndescription: "a note"\nmetadata:\n  type: project\n---\nPlain body.\n'


def _native(name="fresh", body="native body"):
    return (
        f'---\nname: {name}\ndescription: "d {name}"\nmetadata:\n  node_type: memory\n'
        f"  type: project\n  originSessionId: 0000-1111\n---\n{body}\n"
    )


@pytest.fixture
def tele(tmp_path, monkeypatch):
    td = str(tmp_path / "tele")
    monkeypatch.setenv("HIPPO_TELEMETRY_DIR", td)
    return td


def _corpus(repo, memory_dir, monkeypatch, policy=ON, *, trust=True, accept=True, legacy=False):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    write_file(memory_dir, "note.md", NOTE)
    write_file(memory_dir, "drifted.md", NOTE.replace("note", "drifted"))
    if policy is not None:
        write_file(memory_dir, "hippo.json", json.dumps(policy))
    git_commit(repo, "c1", 1_700_000_000)
    root = T.gate_repo_root(memory_dir, repo)
    if trust:
        assert T.mark_trusted(root, memory_dir=None if legacy else memory_dir, origin="init")
    if trust and accept and policy is not None:
        assert PC.acknowledge(memory_dir, repo)["ok"]
    # drifted.md changes after consent: an UNCONSENTED prior
    write_file(memory_dir, "drifted.md", NOTE.replace("note", "drifted") + "edited outside\n")
    return root


def _withheld(memory_dir, repo):
    return sorted(r["stem"] for r in TR.corpus_state(memory_dir, repo)["rows"] if r["kind"] != "removed")


def _write(path, content):
    return {"tool_name": "Write", "session_id": SID, "tool_input": {"file_path": path, "content": content}}


def _edit(path, old, new):
    return {"tool_name": "Edit", "session_id": SID,
            "tool_input": {"file_path": path, "old_string": old, "new_string": new}}


def _apply(payload):
    """Do what the tool does: the simulation is the file-tool's own semantics."""
    from memory.gate_hook import simulate

    ti = payload["tool_input"]
    path = ti["file_path"]
    before = open(path, encoding="utf-8", newline="").read() if os.path.exists(path) else None
    after = simulate(payload["tool_name"], ti, before)
    assert after is not None
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(after)


def _drive(payload, repo, memory_dir, *, between=None):
    pre = SC.note_before_write(payload, memory_dir=memory_dir, repo_root=repo, tree=repo)
    _apply(payload)
    if between:
        between()
    post = SC.grant_after_write(payload, memory_dir=memory_dir, repo_root=repo, tree=repo)
    return pre, post


def _trust_bytes(tmp_path):
    p = os.environ["HIPPO_TRUST_FILE"]
    return open(p, "rb").read() if os.path.exists(p) else None


# --- the granted routes -------------------------------------------------------------------
def test_new_file_write_is_consented_with_the_key_on(repo, memory_dir, monkeypatch, tele):
    _corpus(repo, memory_dir, monkeypatch)
    path = os.path.join(memory_dir, "fresh.md")
    assert _drive(_write(path, _native()), repo, memory_dir) == (True, True)
    assert "fresh" not in _withheld(memory_dir, repo)
    assert not os.listdir(os.path.join(tele, SC.NOTE_DIRNAME))  # the note was consumed
    entry = T._trusted_entry(T.gate_repo_root(memory_dir, repo))
    assert entry["session_granted"]["fresh"] == hashlib.sha256(_native().encode()).hexdigest()
    assert entry.get("last_session_grant_at")
    assert TR.status(memory_dir, repo)["session_granted"] == ["fresh"]
    rows = TRL.read_rollups(tele)
    assert rows[-1]["consent"] == {"session_grants": 1}
    assert TRL.summarize(rows)["session_grants"] == 1


def test_native_symlink_route_write_is_consented(repo, memory_dir, monkeypatch, tele, tmp_path):
    _corpus(repo, memory_dir, monkeypatch)
    native = tmp_path / "home" / ".claude" / "projects" / "-repo" / "memory"
    native.parent.mkdir(parents=True)
    os.symlink(memory_dir, native)
    path = str(native / "fresh.md")
    assert _drive(_write(path, _native()), repo, memory_dir) == (True, True)
    assert "fresh" not in _withheld(memory_dir, repo)


def test_new_file_stays_withheld_with_the_key_off_and_the_trust_file_is_untouched(
    repo, memory_dir, monkeypatch, tele, tmp_path
):
    _corpus(repo, memory_dir, monkeypatch, policy={})
    before = _trust_bytes(tmp_path)
    path = os.path.join(memory_dir, "fresh.md")
    assert _drive(_write(path, _native()), repo, memory_dir) == (False, False)
    assert "fresh" in _withheld(memory_dir, repo)
    assert _trust_bytes(tmp_path) == before
    assert not os.path.exists(os.path.join(tele, SC.NOTE_DIRNAME))
    assert not os.path.exists(tele) or not os.listdir(tele)  # no telemetry either


def test_declared_but_not_accepted_grants_nothing(repo, memory_dir, monkeypatch, tele, tmp_path):
    _corpus(repo, memory_dir, monkeypatch, accept=False)
    before = _trust_bytes(tmp_path)
    path = os.path.join(memory_dir, "fresh.md")
    assert _drive(_write(path, _native()), repo, memory_dir) == (False, False)
    assert "fresh" in _withheld(memory_dir, repo) and _trust_bytes(tmp_path) == before


def test_edit_of_a_consented_file_stays_consented(repo, memory_dir, monkeypatch, tele):
    _corpus(repo, memory_dir, monkeypatch)
    path = os.path.join(memory_dir, "note.md")
    assert _drive(_edit(path, "Plain body.", "Plain body, edited."), repo, memory_dir) == (True, True)
    assert "note" not in _withheld(memory_dir, repo)


def test_multiedit_of_a_consented_file_stays_consented(repo, memory_dir, monkeypatch, tele):
    _corpus(repo, memory_dir, monkeypatch)
    path = os.path.join(memory_dir, "note.md")
    payload = {"tool_name": "MultiEdit", "session_id": SID, "tool_input": {"file_path": path, "edits": [
        {"old_string": "Plain", "new_string": "Plainer"}, {"old_string": "body.", "new_string": "text."}]}}
    assert _drive(payload, repo, memory_dir) == (True, True)
    assert "note" not in _withheld(memory_dir, repo)


# --- what grants nothing ------------------------------------------------------------------
def test_edit_of_an_unconsented_file_stays_withheld(repo, memory_dir, monkeypatch, tele, tmp_path):
    _corpus(repo, memory_dir, monkeypatch)
    before = _trust_bytes(tmp_path)
    path = os.path.join(memory_dir, "drifted.md")
    pre, post = _drive(_edit(path, "edited outside", "edited by the session"), repo, memory_dir)
    assert (pre, post) == (True, False)
    note_dir = os.path.join(tele, SC.NOTE_DIRNAME)
    assert not os.listdir(note_dir)  # consumed either way
    assert "drifted" in _withheld(memory_dir, repo) and _trust_bytes(tmp_path) == before


def test_write_over_an_unconsented_file_stays_withheld(repo, memory_dir, monkeypatch, tele):
    _corpus(repo, memory_dir, monkeypatch)
    path = os.path.join(memory_dir, "drifted.md")
    assert _drive(_write(path, _native("drifted")), repo, memory_dir)[1] is False
    assert "drifted" in _withheld(memory_dir, repo)


def test_a_change_between_pre_and_post_grants_nothing(repo, memory_dir, monkeypatch, tele):
    _corpus(repo, memory_dir, monkeypatch)
    path = os.path.join(memory_dir, "fresh.md")

    def race():
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("someone else wrote here\n")

    assert _drive(_write(path, _native()), repo, memory_dir, between=race) == (True, False)
    assert "fresh" in _withheld(memory_dir, repo)
    assert not os.listdir(os.path.join(tele, SC.NOTE_DIRNAME))


def test_no_pre_note_grants_nothing(repo, memory_dir, monkeypatch, tele):
    _corpus(repo, memory_dir, monkeypatch)
    path = os.path.join(memory_dir, "fresh.md")
    payload = _write(path, _native())
    _apply(payload)  # bytes that arrived by a route the pre hook never saw
    assert SC.grant_after_write(payload, memory_dir=memory_dir, repo_root=repo, tree=repo) is False
    assert "fresh" in _withheld(memory_dir, repo)


def test_a_stale_note_or_another_sessions_note_grants_nothing(repo, memory_dir, monkeypatch, tele):
    _corpus(repo, memory_dir, monkeypatch)
    path = os.path.join(memory_dir, "fresh.md")
    payload = _write(path, _native())
    assert SC.note_before_write(payload, memory_dir=memory_dir, repo_root=repo, tree=repo)
    _apply(payload)
    other = dict(payload, session_id="sess-2")
    assert SC.grant_after_write(other, memory_dir=memory_dir, repo_root=repo, tree=repo) is False
    np = SC.note_path(tele, SID, os.path.realpath(path))
    note = json.load(open(np))
    note["ts"] -= SC.NOTE_TTL_S + 5
    with open(np, "w") as fh:
        json.dump(note, fh)
    assert SC.grant_after_write(payload, memory_dir=memory_dir, repo_root=repo, tree=repo) is False
    assert not os.path.exists(np) and "fresh" in _withheld(memory_dir, repo)


def test_path_tricks_note_nothing(repo, memory_dir, monkeypatch, tele, tmp_path):
    _corpus(repo, memory_dir, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside / "target.md", os.path.join(memory_dir, "escape.md"))
    os.makedirs(os.path.join(memory_dir, "sub"))
    for path in (
        os.path.join(memory_dir, "escape.md"),  # a symlink in the memory dir pointing outside
        os.path.join(memory_dir, "sub", "..", "fresh.md"),  # a `..` segment
        os.path.join(memory_dir, "sub", "x.md"),  # nested memory/sub/x.md
        os.path.join(memory_dir, "fresh.txt"),  # not a .md
        os.path.join(memory_dir, "MEMORY.md"),  # the harness's floor, not a memory
        "relative/memory/x.md",
    ):
        payload = _write(path, _native())
        assert SC.note_before_write(payload, memory_dir=memory_dir, repo_root=repo, tree=repo) is False, path
    assert not os.path.exists(os.path.join(tele, SC.NOTE_DIRNAME))
    assert SC.resolve(os.path.join(memory_dir, "note.md"), memory_dir, repo) is not None


def test_an_untrusted_corpus_or_a_legacy_record_grants_nothing_and_creates_no_record(
    repo, memory_dir, monkeypatch, tele, tmp_path
):
    _corpus(repo, memory_dir, monkeypatch, trust=False)
    path = os.path.join(memory_dir, "fresh.md")
    assert _drive(_write(path, _native()), repo, memory_dir) == (False, False)
    assert _trust_bytes(tmp_path) is None  # no record was created
    # Even a forged note and a direct call cannot create one.
    assert T.record_session_write(memory_dir, path, repo, expect_sha=T.file_sha256(path), before_sha=None) is False
    assert _trust_bytes(tmp_path) is None


def test_a_legacy_record_grants_nothing(repo, memory_dir, monkeypatch, tele, tmp_path):
    _corpus(repo, memory_dir, monkeypatch, legacy=True)
    before = _trust_bytes(tmp_path)
    path = os.path.join(memory_dir, "fresh.md")
    assert _drive(_write(path, _native()), repo, memory_dir) == (False, False)
    assert _trust_bytes(tmp_path) == before


def test_a_human_grant_clears_the_session_mark(repo, memory_dir, monkeypatch, tele):
    _corpus(repo, memory_dir, monkeypatch)
    path = os.path.join(memory_dir, "fresh.md")
    assert _drive(_write(path, _native()), repo, memory_dir) == (True, True)
    write_file(memory_dir, "fresh.md", _native(body="changed by hand"))
    review = TR.build_review(memory_dir, repo, ["fresh"])
    assert TR.grant(memory_dir, repo, digest=review["digest"], files=["fresh"])["ok"]
    assert TR.status(memory_dir, repo)["session_granted"] == []


# --- the real hook scripts ----------------------------------------------------------------
def _env(tmp_path, project, tele):
    data = tmp_path / "plugin-data"
    (data / "venv" / "bin").mkdir(parents=True, exist_ok=True)
    if not (data / "venv" / "bin" / "python").exists():
        os.symlink(sys.executable, data / "venv" / "bin" / "python")
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path / "home"),
        "CLAUDE_PROJECT_DIR": project, "CLAUDE_PLUGIN_ROOT": _PLUGIN_ROOT,
        "CLAUDE_PLUGIN_DATA": str(data), "HIPPO_DISABLE_DENSE": "1",
        "HIPPO_TRUST_FILE": os.environ["HIPPO_TRUST_FILE"], "HIPPO_TELEMETRY_DIR": tele,
        "HIPPO_USER_MEMORY_DIR": str(tmp_path / "user-mem"),
    }


def _hook(script, payload, env):
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(["/bin/bash", script], input=stdin, capture_output=True, text=True,
                          timeout=60, env=env)


def _hooked(payload, env):
    pre = _hook(_PRE, payload, env)
    _apply(payload)
    post = _hook(_POST, payload, env)
    for p in (pre, post):
        assert p.returncode == 0
        assert p.stdout == "" or json.loads(p.stdout)["hookSpecificOutput"]
    return pre, post


def test_hooks_consent_a_native_write_and_stay_silent(repo, memory_dir, monkeypatch, tele, tmp_path):
    _corpus(repo, memory_dir, monkeypatch)
    env = _env(tmp_path, repo, tele)
    _hooked(_write(os.path.join(memory_dir, "fresh.md"), _native()), env)
    _hooked(_edit(os.path.join(memory_dir, "note.md"), "Plain body.", "Edited body."), env)
    assert _withheld(memory_dir, repo) == ["drifted"]


def test_hooks_with_the_key_off_leave_the_trust_file_byte_identical(repo, memory_dir, monkeypatch, tele, tmp_path):
    _corpus(repo, memory_dir, monkeypatch, policy={})
    env = _env(tmp_path, repo, tele)
    before = hashlib.sha256(_trust_bytes(tmp_path)).hexdigest()
    _hooked(_write(os.path.join(memory_dir, "fresh.md"), _native()), env)
    assert hashlib.sha256(_trust_bytes(tmp_path)).hexdigest() == before
    assert "fresh" in _withheld(memory_dir, repo)
    assert not os.path.exists(os.path.join(tele, SC.NOTE_DIRNAME))


@pytest.mark.parametrize("stdin", ["", "{not json", "null", '{"tool_name": "Write"}',
                                   '{"tool_name":"Write","session_id":"s","tool_input":{"file_path":"/a/memory/x.md"}}',
                                   '{"tool_name":"Edit","session_id":"../x","tool_input":{"file_path":"/a/memory/x.md",'
                                   '"old_string":3}}'])
def test_hooks_exit_zero_and_print_nothing_on_a_malformed_payload(repo, memory_dir, monkeypatch, tele, tmp_path, stdin):
    _corpus(repo, memory_dir, monkeypatch)
    env = _env(tmp_path, repo, tele)
    for script in (_PRE, _POST):
        p = _hook(script, stdin, env)
        assert p.returncode == 0 and p.stdout == ""


def test_linked_worktree_through_the_native_symlink_consents_on_the_primary(
    repo, memory_dir, monkeypatch, tele, tmp_path
):
    _corpus(repo, memory_dir, monkeypatch)
    wt = str(tmp_path / "wt")
    subprocess.run(["git", "-C", repo, "worktree", "add", "-q", "-b", "side", wt], check=True,
                   capture_output=True)
    native = tmp_path / "home" / ".claude" / "projects" / "-repo" / "memory"
    native.parent.mkdir(parents=True)
    os.symlink(memory_dir, native)  # hippo's setup wires the native dir into the PRIMARY
    env = _env(tmp_path, wt, tele)
    _hooked(_write(str(native / "fresh.md"), _native()), env)
    assert os.path.exists(os.path.join(memory_dir, "fresh.md"))
    assert "fresh" not in _withheld(memory_dir, repo)


def test_pulled_bytes_stay_withheld(repo, memory_dir, monkeypatch, tele, tmp_path):
    _corpus(repo, memory_dir, monkeypatch)
    other = str(tmp_path / "clone")
    subprocess.run(["git", "clone", "-q", repo, other], check=True, capture_output=True)
    write_file(os.path.join(other, ".claude", "memory"), "pulled.md", _native("pulled"))
    git_commit(other, "upstream", 1_700_000_100)
    subprocess.run(["git", "-C", repo, "pull", "-q", "--no-rebase", other, "HEAD"], check=True,
                   capture_output=True, env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
                                             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"})
    env = _env(tmp_path, repo, tele)
    # a session Read of it afterwards, and a PostToolUse for it with no pre-note, grant nothing
    path = os.path.join(memory_dir, "pulled.md")
    _hook(_POST, {"tool_name": "Write", "session_id": SID, "tool_input": {"file_path": path, "content": _native("pulled")}}, env)
    assert "pulled" in _withheld(memory_dir, repo)


# --- doctor ---------------------------------------------------------------------------------
def _doctor(repo, memory_dir):
    from memory.doctor_checks_consent import check_withheld_origin
    from memory.doctor_checks_env import DoctorContext

    return check_withheld_origin(DoctorContext(memory_dir=memory_dir, repo_root=repo))


def test_doctor_classes_and_the_opt_in(repo, memory_dir, monkeypatch, tele):
    _corpus(repo, memory_dir, monkeypatch, policy={})
    write_file(memory_dir, "nat.md", _native("nat"))
    write_file(memory_dir, "hip.md", _native("hip").replace("  originSessionId", '  cited_paths: ["a.py"]\n  originSessionId'))
    r = _doctor(repo, memory_dir)
    assert r["status"] == "ok"
    assert "3 memories withheld (1 native path, 1 written by hippo elsewhere, 1 other)" in r["message"]
    assert '"consent": {"session_writes": true}' in r["message"] and "hippo trust policy --accept" in r["message"]
    assert "\n" not in r["message"]


def test_doctor_says_nothing_with_nothing_withheld_and_drops_the_opt_in_when_on(repo, memory_dir, monkeypatch, tele):
    _corpus(repo, memory_dir, monkeypatch)
    os.remove(os.path.join(memory_dir, "drifted.md"))
    assert _doctor(repo, memory_dir) is None
    write_file(memory_dir, "nat.md", _native("nat"))
    r = _doctor(repo, memory_dir)
    assert "1 native path" in r["message"] and "session_writes" not in r["message"]


def test_doctor_lists_the_check():
    from memory import doctor as D

    labels = [label for label, _ in D.CHECKS]
    assert labels.index("withheld_origin") == labels.index("outbox") + 1


def test_the_key_is_consent_sensitive():
    assert "consent" in PC.SENSITIVE_KEYS
