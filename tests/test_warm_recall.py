"""HOT-6: warm recall served by the session's own MCP server — the release-gate lines.

The served path (``memory.recall_warm``, the ``recall_hook`` MCP tool) and the command
hook's bash decider (``hippo_warm_route`` in ``hooks/_resolve_py.sh``) settle every prompt
through one O_EXCL claim file per prompt, so exactly one of them injects. Every decider run
here is the REAL bash function under ``/bin/bash`` with the hook tests' minimal PATH
(cat/printf/sed/tr/head + python3), against a real corpus and a real index.

Gate lines covered: session_id routing, the version-mismatch fallback (both directions), a
server killed mid-session and its restart, zero writes on the served path, and the parallel
race in both arrival orders (first prompt of a session included).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time

import pytest

from memory import build_index as B
from memory import mcp_server as M
from memory import recall as R
from memory import recall_warm as W
from memory import telemetry_rollup as TR

_PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugin"))
_RESOLVE = os.path.join(_PLUGIN_ROOT, "hooks", "_resolve_py.sh")
_USER_PROMPT_HOOK = os.path.join(_PLUGIN_ROOT, "hooks", "memory_user_prompt.sh")

_MEMS = {
    "zebra_deploy_runbook": "How the zebra service is deployed — canary lane first, then the pager path.",
    "llama_billing_cutover": "Llama billing cutover checklist — ledger freeze, invoice replay, rollback.",
}
_PROMPT = "how do we deploy the zebra service canary lane"
_SHIPPED_CLAIM_WAIT_S = W.CLAIM_WAIT_S


@pytest.fixture(autouse=True)
def _fresh_server_state():
    W._SESSIONS.clear()
    W._CACHE._rows.clear()
    W._DEFERRED.clear()
    W._STATE.update(busy=False, last_sweep=time.time(), served=False)  # no sweep unless a test asks
    yield
    W._SESSIONS.clear()
    W._CACHE._rows.clear()
    W._DEFERRED.clear()
    W._STATE.update(busy=False, last_sweep=0.0, served=False)


def _write_mem(md, name, desc):
    with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write(f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: project\n---\n{desc}\n')


@pytest.fixture
def warm(tmp_path, monkeypatch):
    """A project with a corpus and a built index; this process is "the server"."""
    project = str(tmp_path / "project")
    md = os.path.join(project, ".claude", "memory")
    os.makedirs(md)
    for name, desc in _MEMS.items():
        _write_mem(md, name, desc)
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# Memory Index\n\n## User\n")
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", project)
    data = str(tmp_path / "data")
    os.makedirs(data)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", data)
    config = str(tmp_path / "claude-config")  # installed_plugins.json lives here (absent)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", config)
    idx = B.default_index_dir(md)
    B.build_index(md, idx)
    os.makedirs(os.path.join(project, ".claude", ".memory-telemetry"), exist_ok=True)
    return {
        "project": project, "md": md, "idx": idx, "data": data, "config": config,
        "warm": os.path.join(data, "warm"), "tmp": tmp_path,
        "td": os.path.join(project, ".claude", ".memory-telemetry"),
    }


def _minimal_path(tmp_path) -> str:
    bindir = tmp_path / "minbin"
    os.makedirs(bindir, exist_ok=True)
    for tool in ("cat", "printf", "sed", "tr", "head"):
        dst = bindir / tool
        if not os.path.lexists(dst):
            os.symlink(shutil.which(tool), dst)
    if not os.path.lexists(bindir / "python3"):
        os.symlink(sys.executable, bindir / "python3")
    return str(bindir)


def _payload(sid, pid, prompt=_PROMPT, project=""):
    return json.dumps({
        "session_id": sid, "prompt_id": pid, "transcript_path": "/x/t.jsonl", "cwd": project,
        "hook_event_name": "UserPromptSubmit", "prompt": prompt,
    })


def _decide(w, sid, pid, *, plugin_root=_PLUGIN_ROOT, prompt=_PROMPT) -> str:
    """Run the hook's bash decider; "warm" (rc 0: the server serves) or "spawn" (rc 1)."""
    env = {
        "PATH": _minimal_path(w["tmp"]),
        "HOME": str(w["tmp"]),
        "CLAUDE_PLUGIN_DATA": w["data"],
        "CLAUDE_PLUGIN_ROOT": plugin_root,
        "CLAUDE_PROJECT_DIR": w["project"],
    }
    proc = subprocess.run(
        ["/bin/bash", "-c", '. "$1"; hippo_warm_route "$2"', "decider", _RESOLVE,
         _payload(sid, pid, prompt, w["project"])],
        env=env, cwd=w["project"], capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode in (0, 1), proc.stderr
    assert proc.stdout == "", "the decider must print nothing: stdout is hook output"
    return "warm" if proc.returncode == 0 else "spawn"


def _serve(sid, pid, prompt=_PROMPT, **extra) -> str:
    args = {"session_id": sid, "prompt_id": pid, "prompt": prompt, "cwd": os.environ["CLAUDE_PROJECT_DIR"]}
    args.update(extra)
    out = W.serve(args)
    W.drain()
    return out


def _serve_in_thread(sid, pid, prompt=_PROMPT):
    box = {}

    def run():
        box["out"] = W.serve({"session_id": sid, "prompt_id": pid, "prompt": prompt})

    t = threading.Thread(target=run)
    t.start()
    return t, box


def _wait_waiting(w, sid, timeout=5.0):
    path = W.heartbeat_path(w["warm"], sid)
    end = time.time() + timeout
    while time.time() < end:
        try:
            with open(path, encoding="utf-8") as fh:
                if json.load(fh).get("state") == "waiting":
                    return
        except Exception:
            pass
        time.sleep(0.002)
    raise AssertionError("the server never reached its waiting state")


def _heartbeat(w, sid):
    with open(W.heartbeat_path(w["warm"], sid), encoding="utf-8") as fh:
        return json.load(fh)


def _injected(out: str) -> bool:
    return out != "{}" and "additionalContext" in json.loads(out).get("hookSpecificOutput", {})


def _prime(w, sid):
    """One S-first prompt so the session has a healthy heartbeat from this live process.

    The decider answers here, so it runs under the shipped claim window. A bash start can
    take tens of ms on a loaded runner; a test that shortens the window primes first."""
    assert W.CLAIM_WAIT_S == _SHIPPED_CLAIM_WAIT_S, "prime before shortening CLAIM_WAIT_S"
    t, box = _serve_in_thread(sid, "prime")
    _wait_waiting(w, sid)
    assert _decide(w, sid, "prime") == "warm"
    t.join(10)
    W.drain()
    assert _injected(box["out"])


def _rows(td, name):
    path = os.path.join(td, name)
    if not os.path.exists(path):
        return []
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


# --------------------------------------------------------------------------- #
# The parallel race: exactly one injector per prompt, in both arrival orders
# --------------------------------------------------------------------------- #
def test_first_prompt_hook_first_spawns_and_server_stands_down(warm):
    os.makedirs(warm["warm"])
    assert _decide(warm, "s1", "p1") == "spawn"  # no heartbeat yet: nothing to hand over
    out = _serve("s1", "p1")
    assert out == "{}"
    assert _heartbeat(warm, "s1")["last_path"] == "spawn"


def test_first_prompt_server_first_serves_warm(warm):
    t, box = _serve_in_thread("s1", "p1")
    _wait_waiting(warm, "s1")
    assert _decide(warm, "s1", "p1") == "warm"
    t.join(10)
    W.drain()
    assert _injected(box["out"])
    assert "zebra_deploy_runbook" in box["out"]


def test_later_prompt_hook_first_hands_over_to_the_live_server(warm):
    _prime(warm, "s1")
    assert _decide(warm, "s1", "p2") == "warm"  # hook first: the heartbeat is healthy
    out = _serve("s1", "p2")
    assert _injected(out)
    assert not os.path.exists(W.claim_path(warm["warm"], "s1", "p2")), "a read claim is removed"


def test_many_prompts_in_random_order_always_have_exactly_one_injector(warm):
    import random

    rng = random.Random(6)
    for i in range(12):
        pid = f"p{i}"
        prompt = f"{_PROMPT} take {i}"
        if rng.random() < 0.5:
            hook = _decide(warm, "s1", pid, prompt=prompt)
            out = _serve("s1", pid, prompt)
        else:
            t, box = _serve_in_thread("s1", pid, prompt)
            time.sleep(rng.random() * 0.02)
            hook = _decide(warm, "s1", pid, prompt=prompt)
            t.join(10)
            W.drain()
            out = box["out"]
        injectors = int(hook == "spawn") + int(_injected(out))
        assert injectors == 1, f"prompt {i}: hook={hook} served={_injected(out)}"


def test_server_that_gave_up_waiting_leaves_spawn_for_a_late_hook(warm, monkeypatch):
    _prime(warm, "s1")
    monkeypatch.setattr(W, "CLAIM_WAIT_S", 0.05)
    assert _serve("s1", "p2") == "{}"  # the hook never answered in time
    assert open(W.claim_path(warm["warm"], "s1", "p2")).read() == "spawn"
    assert _decide(warm, "s1", "p2") == "spawn"  # the late hook obeys it: no gap


def test_a_hook_that_keeps_not_answering_trips_the_breaker(warm, monkeypatch):
    _prime(warm, "s1")
    monkeypatch.setattr(W, "CLAIM_WAIT_S", 0.02)
    for i in range(W.GIVEUP_TRIP):
        _serve("s1", f"g{i}")
    hb = _heartbeat(warm, "s1")
    assert hb["accept"] is False and "stopped answering" in hb["tripped"]
    t0 = time.time()
    assert _serve("s1", "after") == "{}"
    assert time.time() - t0 < 0.5, "a tripped server answers at once, it never waits"


# --------------------------------------------------------------------------- #
# Session routing
# --------------------------------------------------------------------------- #
def test_two_sessions_never_cross(warm, monkeypatch):
    _prime(warm, "sessA")
    _prime(warm, "sessB")
    monkeypatch.setattr(W, "CLAIM_WAIT_S", 0.05)
    assert _decide(warm, "sessA", "same") == "warm"
    # Session B's server call for the same prompt id must not see A's claim.
    assert _serve("sessB", "same") == "{}"
    assert os.path.exists(W.claim_path(warm["warm"], "sessA", "same"))
    out = _serve("sessA", "same")
    assert _injected(out)
    assert {f for f in os.listdir(warm["warm"]) if f.endswith(".server.json")} == {
        "sessA.server.json", "sessB.server.json"
    }


def test_served_telemetry_is_keyed_by_the_hooks_session_id(warm):
    _prime(warm, "sessA")
    assert _decide(warm, "sessA", "p2") == "warm"
    assert _injected(_serve("sessA", "p2"))
    events = _rows(warm["td"], "recall_events.jsonl")
    episodes = _rows(warm["td"], "episode_buffer.jsonl")
    assert events and {e.get("session_id") for e in events} == {"sessA"}
    assert episodes and {e.get("session_id") for e in episodes} == {"sessA"}
    surface = TR.read_rollups(warm["td"])[-1]["surface"]
    assert surface.get("hook:user_prompt:warm") == 2
    assert "hook:user_prompt:spawn" not in surface


def test_ids_that_are_not_file_safe_never_handshake(warm):
    assert _serve("../evil", "p1") == "{}"
    assert _serve("s1", "${prompt_id}") == "{}"  # an unsubstituted placeholder
    assert not os.path.isdir(warm["warm"]) or not os.listdir(warm["warm"])


# --------------------------------------------------------------------------- #
# Version handshake
# --------------------------------------------------------------------------- #
def _fake_heartbeat(w, sid, *, pid, version, accept=True, state="idle"):
    os.makedirs(w["warm"], exist_ok=True)
    doc = {"pid": pid, "version": version, "accept": accept, "state": state}
    with open(W.heartbeat_path(w["warm"], sid), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(doc) + "\n")


def test_a_heartbeat_from_another_version_makes_the_hook_spawn(warm):
    _fake_heartbeat(warm, "s1", pid=os.getpid(), version="0.0.1")  # a live, old server
    assert _decide(warm, "s1", "p1") == "spawn"
    assert open(W.claim_path(warm["warm"], "s1", "p1")).read() == "spawn"


def test_a_server_mid_serve_or_not_accepting_makes_the_hook_spawn(warm):
    mine = W.running_version()
    _fake_heartbeat(warm, "s1", pid=os.getpid(), version=mine, state="serving")
    assert _decide(warm, "s1", "p1") == "spawn"  # busy with an earlier prompt: don't queue
    _fake_heartbeat(warm, "s1", pid=os.getpid(), version=mine, accept=False)
    assert _decide(warm, "s1", "p2") == "spawn"
    _fake_heartbeat(warm, "s1", pid=0, version=mine)  # `kill -0 0` would signal the group
    assert _decide(warm, "s1", "p2b") == "spawn"
    _fake_heartbeat(warm, "s1", pid=os.getpid(), version=mine)
    assert _decide(warm, "s1", "p3") == "warm"  # the same heartbeat, healthy: hand over


def test_the_hook_compares_against_its_own_plugin_version(warm, tmp_path):
    fake_root = tmp_path / "other-root"
    os.makedirs(fake_root / ".claude-plugin")
    (fake_root / ".claude-plugin" / "plugin.json").write_text('{"name": "hippo", "version": "9.9.9"}\n')
    _prime(warm, "s1")  # a real heartbeat at this tree's version
    assert _decide(warm, "s1", "p2", plugin_root=str(fake_root)) == "spawn"
    assert _decide(warm, "s1", "p3") == "warm"


def test_server_seeing_another_installed_version_stands_down_and_says_why(warm):
    reg = os.path.join(warm["config"], "plugins")
    os.makedirs(reg)
    with open(os.path.join(reg, "installed_plugins.json"), "w", encoding="utf-8") as fh:
        json.dump({"version": 2, "plugins": {"hippo@hippo": [{"scope": "user", "version": "9.9.9"}]}}, fh)
    out = _serve("s1", "p1")
    assert out == "{}"
    hb = _heartbeat(warm, "s1")
    assert hb["accept"] is False and "installed" in hb["unfit"]
    assert open(W.claim_path(warm["warm"], "s1", "p1")).read() == "spawn"
    assert _decide(warm, "s1", "p1") == "spawn"  # the late hook reads the server's claim
    assert _decide(warm, "s1", "p2") == "spawn"  # and the next prompt reads the heartbeat


def test_a_server_ahead_of_the_install_still_serves(warm):
    """A --plugin-dir dev load runs newer code than the install; the hook (same root) agrees."""
    reg = os.path.join(warm["config"], "plugins")
    os.makedirs(reg)
    with open(os.path.join(reg, "installed_plugins.json"), "w", encoding="utf-8") as fh:
        json.dump({"version": 2, "plugins": {"hippo@hippo": [{"scope": "user", "version": "0.0.1"}]}}, fh)
    _prime(warm, "s1")
    assert _heartbeat(warm, "s1")["accept"] is True


def test_index_from_another_schema_is_not_served(warm):
    manifest = os.path.join(warm["idx"], "manifest.json")
    doc = json.load(open(manifest, encoding="utf-8"))
    doc["schema_version"] = B.SCHEMA_VERSION + 1
    with open(manifest, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    assert _serve("s1", "p1") == "{}"
    assert "no usable index" in _heartbeat(warm, "s1")["unfit"]


# --------------------------------------------------------------------------- #
# A server killed mid-session, and its restart
# --------------------------------------------------------------------------- #
def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_heartbeat_with_a_dead_pid_makes_the_hook_spawn(warm):
    _prime(warm, "s1")
    hb = _heartbeat(warm, "s1")
    hb["pid"] = _dead_pid()
    with open(W.heartbeat_path(warm["warm"], "s1"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(hb) + "\n")
    assert _decide(warm, "s1", "p2") == "spawn"


def test_a_restarted_server_re_handshakes(warm):
    _fake_heartbeat(warm, "s1", pid=_dead_pid(), version=W.running_version())
    assert _decide(warm, "s1", "p1") == "spawn"
    assert _serve("s1", "p1") == "{}"  # the new server obeys the claim the hook made
    assert _heartbeat(warm, "s1")["pid"] == os.getpid()
    assert _decide(warm, "s1", "p2") == "warm"
    assert _injected(_serve("s1", "p2"))


def test_a_warm_claim_no_server_read_is_lost_and_the_hook_spawns_until_it_answers(warm):
    _prime(warm, "s1")
    assert _decide(warm, "s1", "p2") == "warm"  # ...but the server is never called
    assert _decide(warm, "s1", "p3") == "spawn"
    assert open(W.claim_path(warm["warm"], "s1", "p2")).read() == "lost"
    assert os.path.exists(W.lost_path(warm["warm"], "s1"))
    spool = _rows(warm["td"], "usage_spool.jsonl")
    assert [r["action"] for r in spool] == ["failed"]
    assert _decide(warm, "s1", "p4") == "spawn"  # still no answer: keep spawning
    assert _serve("s1", "p3") == "{}"  # the server is back: it obeys, clears the marker
    assert not os.path.exists(W.lost_path(warm["warm"], "s1"))
    assert _decide(warm, "s1", "p5") == "warm"


# --------------------------------------------------------------------------- #
# Zero writes on the served path
# --------------------------------------------------------------------------- #
def _tree_hashes(*dirs):
    out = {}
    for d in dirs:
        for root, _dirs, files in os.walk(d):
            for f in files:
                p = os.path.join(root, f)
                with open(p, "rb") as fh:
                    out[p] = (hashlib.sha256(fh.read()).hexdigest(), os.stat(p).st_mtime_ns)
    return out


def test_the_served_path_writes_nothing_to_the_corpus_or_the_index(warm):
    _prime(warm, "s1")
    before = _tree_hashes(warm["md"], warm["idx"])
    assert _decide(warm, "s1", "p2") == "warm"
    assert _injected(_serve("s1", "p2"))
    assert _tree_hashes(warm["md"], warm["idx"]) == before


def test_no_index_means_no_serve_and_no_build(warm):
    shutil.rmtree(warm["idx"])
    before = _tree_hashes(warm["md"])
    assert _serve("s1", "p1") == "{}"
    assert not os.path.exists(warm["idx"]), "the served path must never build an index"
    assert _tree_hashes(warm["md"]) == before
    assert _decide(warm, "s1", "p2") == "spawn"  # the spawned hook builds it, as before


def test_an_unindexed_user_tier_is_read_never_built(warm, monkeypatch):
    """The spawned hook builds a missing tier index on the fly; the served path must not."""
    tier = str(warm["tmp"] / "user-tier")
    os.makedirs(tier)
    _write_mem(tier, "zebra_personal_note", "My own zebra deploy canary habit.")
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", tier)
    before = _tree_hashes(tier)
    _prime(warm, "s1")
    assert _tree_hashes(tier) == before
    # The user tier's index is its plain sibling dir; the spawned path would create it here.
    assert not os.path.exists(B.default_index_dir(tier)), "no tier index was built"


def test_a_rebuilt_index_is_picked_up_on_the_next_call(warm):
    _prime(warm, "s1")
    _write_mem(warm["md"], "quokka_pager_rota", "Quokka pager rota — who holds the quokka pager each week.")
    time.sleep(0.01)
    B.build_index(warm["md"], warm["idx"])
    assert _decide(warm, "s1", "p2") == "warm"
    out = _serve("s1", "p2", "who holds the quokka pager rota")
    assert "quokka_pager_rota" in out


# --------------------------------------------------------------------------- #
# Same behavior as the spawned hook
# --------------------------------------------------------------------------- #
def _spawned(warm, sid, prompt, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": prompt, "session_id": sid})))
    assert R.main(["--stdin-json"]) == 0
    return capsys.readouterr().out.strip()


def test_served_output_equals_the_spawned_hooks_prompt_after_prompt(warm, monkeypatch, capsys):
    spawned = [_spawned(warm, "spawn-session", _PROMPT, monkeypatch, capsys) for _ in range(2)]
    served = []
    for i in range(2):
        t, box = _serve_in_thread("warm-session", f"p{i}")
        _wait_waiting(warm, "warm-session")
        assert _decide(warm, "warm-session", f"p{i}") == "warm"
        t.join(10)
        W.drain()
        served.append(box["out"])
    assert served == spawned  # same block, same cooldown collapse on the repeat
    assert spawned[0] != spawned[1], "the repeat collapses: the comparison is not vacuous"


def test_a_machine_turn_is_not_served_either(warm):
    _prime(warm, "s1")
    assert _decide(warm, "s1", "p2") == "warm"
    out = _serve("s1", "p2", "<task-notification>zebra deploy canary finished</task-notification>")
    assert out == "{}"


# --------------------------------------------------------------------------- #
# The breaker and the busy mark
# --------------------------------------------------------------------------- #
def test_served_calls_that_raise_trip_the_breaker(warm, monkeypatch):
    from memory import recall_hook

    _prime(warm, "s1")

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(recall_hook, "run_recall", boom)
    for i in range(W.FAIL_TRIP):
        assert _decide(warm, "s1", f"f{i}") == "warm"
        assert _serve("s1", f"f{i}") == "{}"
    hb = _heartbeat(warm, "s1")
    assert hb["accept"] is False and "failed" in hb["tripped"]
    assert _decide(warm, "s1", "next") == "spawn"
    surface = TR.read_rollups(warm["td"])[-1]["surface"]
    assert surface.get("hook:user_prompt:failed") == W.FAIL_TRIP


def test_one_slow_served_call_trips_the_breaker(warm, monkeypatch):
    _prime(warm, "s1")
    monkeypatch.setattr(W, "SERVE_BUDGET_MS", 0.0)
    assert _decide(warm, "s1", "p2") == "warm"
    assert _injected(_serve("s1", "p2"))  # this one still answers
    assert "budget" in _heartbeat(warm, "s1")["tripped"]
    assert _decide(warm, "s1", "p3") == "spawn"


def test_the_first_served_call_may_load_the_model_without_tripping(warm, monkeypatch):
    monkeypatch.setattr(W, "SERVE_BUDGET_MS", 0.0)
    _prime(warm, "s1")  # the process's first served call: the cold budget applies
    assert _heartbeat(warm, "s1")["tripped"] == ""
    assert _decide(warm, "s1", "p2") == "warm"
    assert _injected(_serve("s1", "p2"))
    assert "budget" in _heartbeat(warm, "s1")["tripped"]


def test_another_tool_call_marks_the_server_busy(warm, monkeypatch):
    _prime(warm, "s1")
    seen = {}

    def probe(args):
        seen["accept"] = _heartbeat(warm, "s1")["accept"]
        seen["hook"] = _decide(warm, "s1", "during")
        return "ok"

    monkeypatch.setitem(M._DISPATCH, "recall", probe)
    resp = M.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": "recall", "arguments": {"query": "x"}}})
    assert resp["result"]["content"][0]["text"] == "ok"
    assert seen == {"accept": False, "hook": "spawn"}
    assert _heartbeat(warm, "s1")["accept"] is True
    assert _decide(warm, "s1", "after") == "warm"


# --------------------------------------------------------------------------- #
# The MCP surface and the stdio loop
# --------------------------------------------------------------------------- #
def test_recall_hook_over_stdio_answers_then_writes_telemetry(warm, monkeypatch):
    _prime(warm, "s1")
    assert _decide(warm, "s1", "p2") == "warm"
    before = len(_rows(warm["td"], "recall_events.jsonl"))
    calls = []
    monkeypatch.setattr(M, "_note_tool_use", lambda tool, args: calls.append(tool))
    req = {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "recall_hook",
           "arguments": {"session_id": "s1", "prompt_id": "p2", "prompt": _PROMPT, "cwd": warm["project"]}}}
    stdout = io.StringIO()
    M.serve(stdin=io.StringIO(json.dumps(req) + "\n"), stdout=stdout)
    resp = json.loads(stdout.getvalue())
    text = resp["result"]["content"][0]["text"]
    assert json.loads(text)["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert len(_rows(warm["td"], "recall_events.jsonl")) == before + 1  # drained after the answer
    assert calls == [], "the harness's hook call is not counted as a tool use"


def test_sweep_drops_stale_files_and_keeps_live_ones(warm):
    os.makedirs(warm["warm"])
    old = time.time() - W.STALE_STATE_S - 60
    paths = {name: os.path.join(warm["warm"], name) for name in (
        "gone.server.json", "gone.lost", "gone.p1.claim", "fresh.server.json", "fresh.p9.claim",
    )}
    for name, p in paths.items():
        open(p, "w").write("x")
        if name.startswith("gone"):
            os.utime(p, (old, old))
    W._STATE["last_sweep"] = 0.0
    W._sweep(warm["warm"])
    assert sorted(os.listdir(warm["warm"])) == ["fresh.p9.claim", "fresh.server.json"]


# --------------------------------------------------------------------------- #
# The real hook script: warm skips the spawn, everything else spawns as before
# --------------------------------------------------------------------------- #
def _hook_env(warm, canary):
    venv_bin = os.path.join(warm["data"], "venv", "bin")
    os.makedirs(venv_bin, exist_ok=True)
    script = os.path.join(venv_bin, "python")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(f"#!/bin/bash\nprintf spawned >> {canary}\n")
    os.chmod(script, 0o755)
    return {
        "PATH": _minimal_path(warm["tmp"]),
        "HOME": str(warm["tmp"]),
        "CLAUDE_PROJECT_DIR": warm["project"],
        "CLAUDE_PLUGIN_ROOT": _PLUGIN_ROOT,
        "CLAUDE_PLUGIN_DATA": warm["data"],
    }


def test_the_hook_script_skips_its_spawn_only_when_the_server_serves(warm):
    canary = str(warm["tmp"] / "canary")
    env = _hook_env(warm, canary)
    _prime(warm, "s1")

    def run(pid):
        return subprocess.run(["/bin/bash", _USER_PROMPT_HOOK], input=_payload("s1", pid, project=warm["project"]),
                              capture_output=True, text=True, timeout=60, env=env)

    proc = run("p2")
    assert proc.returncode == 0 and proc.stdout == ""
    assert not os.path.exists(canary), "a warm prompt must not spawn Python"
    assert _injected(_serve("s1", "p2"))
    hb = _heartbeat(warm, "s1")
    hb["pid"] = _dead_pid()
    with open(W.heartbeat_path(warm["warm"], "s1"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(hb) + "\n")
    proc = run("p3")
    assert proc.returncode == 0
    assert open(canary).read() == "spawned", "with the server gone the hook spawns as before"
