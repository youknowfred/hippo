"""GAT-1: the PreToolUse write gate, in process and as the real hook script.

The decision matrix (ruling V2Q-22, option b): a worsening corpus write gets a warning by
default; it is refused only when the corpus's COMMITTED hippo.json asks for that AND this
machine accepted the value. The script always exits 0, prints nothing or one
hookSpecificOutput object, and starts no interpreter for a write outside a corpus.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

from memory import gate_hook as H
from memory import policy_consent as PC
from memory import trust as T

from .conftest import git_commit, write_file

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

_PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugin"))
_HOOK = os.path.join(_PLUGIN_ROOT, "hooks", "memory_pre_tool.sh")

LINT = {"floor_lint": {"banned_re": r"DONE-NOW|#\d{3,}", "max_line": 100}}
FLOOR = "# Memory\n\n## User\n- [Prefers short](prefers-short.md) — keep it tight\n"
NOTE = '---\nname: note\ndescription: "a note"\ntype: project\n---\nPlain body.\n'


def _corpus(repo, memory_dir, policy=None, commit=True):
    write_file(memory_dir, "MEMORY.md", FLOOR)
    write_file(memory_dir, "note.md", NOTE)
    write_file(memory_dir, "hippo.json", json.dumps({**LINT, **(policy or {})}))
    if commit:
        git_commit(repo, "c1", 1_700_000_000)


def _write(path, content):
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": content}}


def _edit(path, old, new, replace_all=False):
    return {
        "tool_name": "Edit",
        "tool_input": {"file_path": path, "old_string": old, "new_string": new, "replace_all": replace_all},
    }


def _eval(payload, repo, memory_dir):
    return H.evaluate(payload, memory_dir=memory_dir, repo_root=repo)


def _decision(out):
    if out is None:
        return "pass"
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    return "deny" if hso.get("permissionDecision") == "deny" else "warn"


def _accept(repo, memory_dir, monkeypatch):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    root = T.gate_repo_root(memory_dir, repo)
    assert T.mark_trusted(root, memory_dir=memory_dir, origin="init")
    assert PC.acknowledge(memory_dir, repo)["ok"]


# --- simulating the tool -----------------------------------------------------------------
def test_simulate_write_edit_multiedit():
    assert H.simulate("Write", {"content": "new"}, "old") == "new"
    assert H.simulate("Edit", {"old_string": "a", "new_string": "b"}, "a a") == "b a"
    assert H.simulate("Edit", {"old_string": "a", "new_string": "b", "replace_all": True}, "a a") == "b b"
    edits = [{"old_string": "a", "new_string": "b"}, {"old_string": "b b", "new_string": "c"}]
    assert H.simulate("MultiEdit", {"edits": edits}, "a b") == "c"


def test_simulate_says_nothing_when_the_tool_will_refuse():
    assert H.simulate("Edit", {"old_string": "zz", "new_string": "b"}, "a") is None
    assert H.simulate("Edit", {"old_string": "a", "new_string": "b"}, None) is None
    assert H.simulate("Edit", {"old_string": "", "new_string": "b"}, None) == "b"
    assert H.simulate("MultiEdit", {"edits": []}, "a") is None
    assert H.simulate("Write", {"content": 3}, "a") is None


# --- the decision matrix ------------------------------------------------------------------
def test_a_worsening_floor_write_warns_by_default(repo, memory_dir):
    _corpus(repo, memory_dir)
    floor = os.path.join(memory_dir, "MEMORY.md")
    out = _eval(_write(floor, FLOOR + "- [X](x.md) — DONE-NOW\n"), repo, memory_dir)
    assert _decision(out) == "warn"
    text = out["hookSpecificOutput"]["additionalContext"]
    assert "MEMORY.md" in text and "0 -> 1" in text and "still goes ahead" in text


def test_an_improving_or_neutral_write_is_silent(repo, memory_dir):
    _corpus(repo, memory_dir)
    floor = os.path.join(memory_dir, "MEMORY.md")
    assert _eval(_write(floor, FLOOR + "- [Y](y.md) — clean hook\n"), repo, memory_dir) is None
    assert _eval(_edit(floor, "keep it tight", "short"), repo, memory_dir) is None


def test_a_body_gaining_a_banned_token_warns(repo, memory_dir):
    _corpus(repo, memory_dir)
    note = os.path.join(memory_dir, "note.md")
    out = _eval(_edit(note, "Plain body.", "Plain body. Closed in #4521."), repo, memory_dir)
    assert _decision(out) == "warn" and "note.md" in out["hookSpecificOutput"]["additionalContext"]


def test_writes_outside_the_corpus_are_silent(repo, memory_dir, tmp_path):
    _corpus(repo, memory_dir)
    for path in (os.path.join(repo, "src", "a.md"), str(tmp_path / "memory" / "a.md"),
                 os.path.join(memory_dir, "archive", "a.md"), os.path.join(memory_dir, "x.txt")):
        assert _eval(_write(path, "DONE-NOW #4521\n"), repo, memory_dir) is None
    assert _eval({"tool_name": "Read", "tool_input": {"file_path": os.path.join(memory_dir, "MEMORY.md")}},
                 repo, memory_dir) is None
    assert _eval("garbage", repo, memory_dir) is None


def test_the_native_memory_symlink_counts_as_the_corpus(repo, memory_dir, tmp_path):
    _corpus(repo, memory_dir)
    native = tmp_path / "projects" / "-enc-key"
    native.mkdir(parents=True)
    os.symlink(memory_dir, native / "memory")
    out = _eval(_write(str(native / "memory" / "MEMORY.md"), FLOOR + "- DONE-NOW\n"), repo, memory_dir)
    assert _decision(out) == "warn"


def test_a_linked_worktrees_own_copy_is_judged_under_its_own_policy(repo, memory_dir, tmp_path, monkeypatch):
    _corpus(repo, memory_dir)
    tree = tmp_path / "wt"
    wt_md = tree / ".claude" / "memory"
    wt_md.mkdir(parents=True)
    (wt_md / "MEMORY.md").write_text(FLOOR, encoding="utf-8")
    (wt_md / "hippo.json").write_text(json.dumps({"floor_lint": {"banned_re": "ZEBRA"}}), encoding="utf-8")
    import memory.provenance as P

    monkeypatch.setattr(P, "launch_root", lambda: str(tree))
    floor = str(wt_md / "MEMORY.md")
    assert _decision(_eval(_write(floor, FLOOR + "- ZEBRA\n"), repo, memory_dir)) == "warn"
    assert _eval(_write(floor, FLOOR + "- DONE-NOW\n"), repo, memory_dir) is None  # not its pattern
    # A .claude/memory outside the session's tree is not this session's corpus.
    monkeypatch.setattr(P, "launch_root", lambda: str(tmp_path / "elsewhere"))
    assert _eval(_write(floor, FLOOR + "- ZEBRA\n"), repo, memory_dir) is None


def test_declared_deny_but_not_accepted_only_warns(repo, memory_dir, monkeypatch):
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    _corpus(repo, memory_dir, {"gate": {"write": "deny"}})
    T.mark_trusted(T.gate_repo_root(memory_dir, repo), memory_dir=memory_dir, origin="init")
    out = _eval(_write(os.path.join(memory_dir, "MEMORY.md"), FLOOR + "- DONE-NOW\n"), repo, memory_dir)
    assert _decision(out) == "warn"


def test_committed_and_accepted_deny_refuses_with_the_numbers(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, {"gate": {"write": "deny"}})
    _accept(repo, memory_dir, monkeypatch)
    out = _eval(_write(os.path.join(memory_dir, "MEMORY.md"), FLOOR + "- DONE-NOW\n"), repo, memory_dir)
    assert _decision(out) == "deny"
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "refused" in reason and "0 -> 1" in reason and "Rewrite" in reason
    assert "additionalContext" not in out["hookSpecificOutput"]
    # Standing debt still passes under deny.
    assert _eval(_edit(os.path.join(memory_dir, "MEMORY.md"), "tight", "tighter"), repo, memory_dir) is None


def test_an_uncommitted_deny_cannot_turn_refusal_on(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, {"gate": {"write": "warn"}})
    write_file(memory_dir, "hippo.json", json.dumps({**LINT, "gate": {"write": "deny"}}))
    _accept(repo, memory_dir, monkeypatch)  # accepted the working-tree value
    out = _eval(_write(os.path.join(memory_dir, "MEMORY.md"), FLOOR + "- DONE-NOW\n"), repo, memory_dir)
    assert _decision(out) == "warn"


def test_a_pulled_change_of_value_drops_back_to_warn(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, {"gate": {"write": "deny"}})
    _accept(repo, memory_dir, monkeypatch)
    write_file(memory_dir, "hippo.json", json.dumps({**LINT, "gate": {"write": "deny", "x": 1}}))
    git_commit(repo, "pulled", 1_700_000_100)
    out = _eval(_write(os.path.join(memory_dir, "MEMORY.md"), FLOOR + "- DONE-NOW\n"), repo, memory_dir)
    assert _decision(out) == "warn"


def test_off_silences_and_unknown_values_warn(repo, memory_dir):
    _corpus(repo, memory_dir, {"gate": {"write": "off"}})
    floor = os.path.join(memory_dir, "MEMORY.md")
    assert _eval(_write(floor, FLOOR + "- DONE-NOW\n"), repo, memory_dir) is None
    write_file(memory_dir, "hippo.json", json.dumps({**LINT, "gate": {"write": "block"}}))
    assert _decision(_eval(_write(floor, FLOOR + "- DONE-NOW\n"), repo, memory_dir)) == "warn"


def test_the_ci_bypass_honours_a_committed_deny(repo, memory_dir, monkeypatch):
    monkeypatch.setenv("HIPPO_TRUST_ALL", "1")
    _corpus(repo, memory_dir, {"gate": {"write": "deny"}})
    out = _eval(_write(os.path.join(memory_dir, "MEMORY.md"), FLOOR + "- DONE-NOW\n"), repo, memory_dir)
    assert _decision(out) == "deny"


def test_the_git_call_runs_only_when_a_denial_is_about_to_happen(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir, {"gate": {"write": "deny"}})
    calls = []
    real = H.committed_write_mode
    monkeypatch.setattr(H, "committed_write_mode", lambda d: calls.append(d) or real(d))
    floor = os.path.join(memory_dir, "MEMORY.md")
    assert _eval(_write(floor, FLOOR + "- clean\n"), repo, memory_dir) is None
    assert calls == []
    _eval(_write(floor, FLOOR + "- DONE-NOW\n"), repo, memory_dir)
    assert len(calls) == 1


# --- doctor -------------------------------------------------------------------------------
def _doctor(repo, memory_dir):
    from memory.doctor_checks_env import DoctorContext
    from memory.doctor_checks_gate import check_write_gate

    return check_write_gate(DoctorContext(memory_dir=memory_dir, repo_root=repo, plugin_root=_PLUGIN_ROOT))


def test_doctor_states_the_posture(repo, memory_dir, monkeypatch):
    _corpus(repo, memory_dir)
    r = _doctor(repo, memory_dir)
    assert r["status"] == "ok" and "warns" in r["message"] and "default" in r["message"]
    write_file(memory_dir, "hippo.json", json.dumps({"gate": {"write": "off"}}))
    assert "off" in _doctor(repo, memory_dir)["message"]
    write_file(memory_dir, "hippo.json", json.dumps({"gate": {"write": "nope"}}))
    r = _doctor(repo, memory_dir)
    assert r["status"] == "warn" and "'nope'" in r["message"]
    monkeypatch.delenv("HIPPO_TRUST_ALL", raising=False)
    write_file(memory_dir, "hippo.json", json.dumps({"gate": {"write": "deny"}}))
    T.mark_trusted(T.gate_repo_root(memory_dir, repo), memory_dir=memory_dir, origin="init")
    r = _doctor(repo, memory_dir)
    assert r["status"] == "warn" and "hippo trust policy --accept" in r["message"]
    assert PC.acknowledge(memory_dir, repo)["ok"]
    r = _doctor(repo, memory_dir)
    assert r["status"] == "warn" and "not committed" in r["message"]
    git_commit(repo, "deny", 1_700_000_200)
    r = _doctor(repo, memory_dir)
    assert r["status"] == "ok" and "may refuse" in r["message"]


# --- the real script ----------------------------------------------------------------------
def _bin(tmp_path, *, spy=None):
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    for tool in ("git", "cat", "printf", "date"):
        src = shutil.which(tool)
        if src and not (b / tool).exists():
            os.symlink(src, b / tool)
    if spy and not (b / "python3").exists():
        # A python3 that records being started: the fast path must never reach it.
        (b / "python3").write_text(f"#!/bin/sh\necho started >> {spy}\nexit 0\n")
        os.chmod(b / "python3", 0o755)
    return str(b)


def _run(tmp_path, repo, stdin, *, venv=True, spy=None, extra=None):
    data = tmp_path / "plugin-data"
    if venv:
        (data / "venv" / "bin").mkdir(parents=True, exist_ok=True)
        if not (data / "venv" / "bin" / "python").exists():
            os.symlink(sys.executable, data / "venv" / "bin" / "python")
    env = {
        "PATH": _bin(tmp_path, spy=spy), "HOME": str(tmp_path / "home"),
        "CLAUDE_PROJECT_DIR": repo, "CLAUDE_PLUGIN_ROOT": _PLUGIN_ROOT,
        "CLAUDE_PLUGIN_DATA": str(data) if venv else "",
        "HIPPO_DISABLE_DENSE": "1", "HIPPO_TRUST_FILE": str(tmp_path / "trust.json"),
        "HIPPO_TRUST_ALL": "1",
    }
    env.update(extra or {})
    return subprocess.run(["/bin/bash", _HOOK], input=stdin, capture_output=True, text=True,
                          timeout=60, env=env)


@pytest.mark.parametrize("stdin", ["", "{not json", "null", '{"tool_name": "Write"}',
                                   '{"tool_name":"Write","tool_input":{"file_path":"/a/memory/x.md"}}'])
def test_script_exits_zero_and_prints_nothing_on_garbage(tmp_path, repo, memory_dir, stdin):
    _corpus(repo, memory_dir)
    p = _run(tmp_path, repo, stdin)
    assert p.returncode == 0 and p.stdout == ""


def test_script_fast_path_starts_no_interpreter(tmp_path, repo, memory_dir):
    _corpus(repo, memory_dir)
    spy = tmp_path / "spy.txt"
    for path in (os.path.join(repo, "src", "app.py"), os.path.join(repo, "docs", "memory", "deep", "x.md"),
                 os.path.join(repo, "notes.md")):
        p = _run(tmp_path, repo, json.dumps(_write(path, "DONE-NOW " * 10)), venv=False, spy=spy)
        assert p.returncode == 0 and p.stdout == ""
    p = _run(tmp_path, repo, json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls /x/memory/"}}),
             venv=False, spy=spy)
    assert p.returncode == 0 and not spy.exists()
    # The corpus write does reach Python (the spy proves the fast path let it through).
    _run(tmp_path, repo, json.dumps(_write(os.path.join(memory_dir, "MEMORY.md"), "x")), venv=False, spy=spy)
    assert spy.exists()


def test_script_warns_on_a_worsening_floor_write(tmp_path, repo, memory_dir):
    _corpus(repo, memory_dir)
    p = _run(tmp_path, repo, json.dumps(_write(os.path.join(memory_dir, "MEMORY.md"), FLOOR + "- DONE-NOW\n")))
    assert p.returncode == 0
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in out["hookSpecificOutput"]
    assert "0 -> 1" in out["hookSpecificOutput"]["additionalContext"]
    spool = os.path.join(repo, ".hippo", "telemetry", "usage_spool.jsonl")
    assert not os.path.exists(spool) or '"pre_tool"' in open(spool).read()


def test_script_denies_where_committed_policy_asks(tmp_path, repo, memory_dir):
    _corpus(repo, memory_dir, {"gate": {"write": "deny"}})
    os.makedirs(os.path.join(repo, ".hippo", "telemetry"))
    p = _run(tmp_path, repo, json.dumps(_write(os.path.join(memory_dir, "MEMORY.md"), FLOOR + "- DONE-NOW\n")))
    assert p.returncode == 0
    assert json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    rows = [json.loads(x) for x in open(os.path.join(repo, ".hippo", "telemetry", "usage_spool.jsonl"))]
    assert rows[-1]["verb"] == "pre_tool" and rows[-1]["action"] == "deny"


def test_script_is_silent_without_a_corpus(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    p = _run(tmp_path, str(bare), json.dumps(_write(str(bare / ".claude" / "memory" / "MEMORY.md"), "DONE-NOW")))
    assert p.returncode == 0 and p.stdout == ""


def test_hooks_json_registers_the_gate_with_a_matcher():
    with open(os.path.join(_PLUGIN_ROOT, "hooks", "hooks.json")) as fh:
        hooks = json.load(fh)["hooks"]
    [entry] = hooks["PreToolUse"]
    assert entry["matcher"] == "Write|Edit|MultiEdit"
    assert entry["hooks"][0]["command"].endswith("/hooks/memory_pre_tool.sh")
    assert os.access(_HOOK, os.X_OK)
