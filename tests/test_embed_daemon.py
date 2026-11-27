"""HCR-7: one embedder per machine — the daemon, its client, and lean on any fault.

MLX is faked (``tests/embed_daemon_fake.py``), so everything here runs on Linux CI. Daemons
run two ways: on a thread of the test process, where the test can hold a forward pass and
read what the fake embedded, and as real processes (started, raced and killed) through the
same ``embed_remote.SPAWN`` seam production uses.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

import numpy as np
import pytest

from memory import build_index as B
from memory import embed_daemon as D
from memory import embed_mlx as E
from memory import embed_remote as X
from memory import recall_rank as RR
from memory import recall_warm as W
from memory import recall_warm_high as H
from memory import rerank_mlx as R

from . import embed_daemon_fake as fake
from .test_embed_mlx import (  # noqa: F401  (fixtures and the warm-call helpers)
    _FakeLean, _MEMS, _PROMPT, _SPACE, _fresh_warm_state, _heartbeat, _serve_warm, _wait_fresh, project,
)

_LAUNCHER = os.path.abspath(fake.__file__)
_PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(E.__file__)))


def _until(cond, timeout=10.0, what="the condition"):
    end = time.time() + timeout
    while time.time() < end:
        got = cond()
        if got:
            return got
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture
def daemon_env(tmp_path, monkeypatch):
    """A machine on the daemon path with MLX faked: the client seams at their defaults, the
    high model 'servable', and every daemon process this test starts killed afterwards."""
    data = str(tmp_path / "data")  # the `project` fixture's too, which makes it when it is used
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", data)
    monkeypatch.setenv(X.ENV, "1")
    monkeypatch.setattr(E, "unavailable_reason", lambda model: None)
    monkeypatch.setattr(R, "unavailable_reason", lambda model: None)
    for allow in (True, False):
        B._MODEL_CACHE[(B.DEFAULT_MODEL, allow)] = _FakeLean()
    fake.FakeEmbedder.made.clear()
    fake.FakeReranker.made.clear()
    procs: list = []

    def spawn(space):
        env = dict(os.environ)
        env["PYTHONPATH"] = _PLUGIN + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        os.makedirs(data, exist_ok=True)
        log = open(os.path.join(data, "fake-daemon.log"), "ab")
        proc = subprocess.Popen([sys.executable, _LAUNCHER, "--space", space], env=env, stderr=log,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, start_new_session=True)
        log.close()
        procs.append(proc)
        return proc

    monkeypatch.setattr(X, "SPAWN", spawn)
    monkeypatch.setattr(X, "_FIRST_BACKOFF_S", 0.0)  # these tests kill daemons seconds after a start
    E._LOADED.clear()
    H._reset_for_tests()
    X._reset_for_tests()
    yield {"data": data, "procs": procs, "spawn": spawn}
    H._reset_for_tests()
    E._LOADED.clear()
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
        proc.wait(10)
    X._reset_for_tests()


@contextmanager
def _thread_daemon(space=_SPACE, **kw):
    """A daemon on a thread of this process, serving the fakes. Yields (daemon, embedder)."""
    emb = fake.FakeEmbedder(*E.parse_space(space))
    kw.setdefault("live", lambda: 1)
    kw.setdefault("tick_s", 0.02)
    daemon = D.Daemon(space, load_embedder=lambda: emb, load_reranker=fake.FakeReranker, **kw)
    assert daemon.start()

    def run():
        try:
            daemon.serve_forever()
        finally:
            daemon.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield daemon, emb
    finally:
        if emb.gate is not None:
            emb.gate.set()
        daemon.stop()
        thread.join(10)
        assert not thread.is_alive()


def _client(space=_SPACE):
    return X.RemoteEmbedder(*E.parse_space(space))


# --------------------------------------------------------------------------- #
# The seam: who gets the daemon's client, and the knob that keeps a model in-process
# --------------------------------------------------------------------------- #
def test_the_default_backends_hand_out_the_daemons_clients_and_the_knob_turns_that_off(daemon_env, monkeypatch):
    assert E.uses_daemon() and R._backend() is X.RemoteReranker
    assert isinstance(E.high_embedder(_SPACE), X.RemoteEmbedder)  # made without touching a socket
    assert daemon_env["procs"] == []
    monkeypatch.setenv(X.ENV, "0")
    assert not E.uses_daemon() and R._backend() is R._MLXReranker
    monkeypatch.setenv(X.ENV, "1")
    monkeypatch.setattr(E, "BACKEND", fake.FakeEmbedder)  # a backend put in the seam wins
    assert not E.uses_daemon()


def test_the_benches_keep_their_models_in_process():
    code = "import os, bench; print(os.environ['HIPPO_EMBED_DAEMON'])"
    env = {k: v for k, v in os.environ.items() if k != X.ENV}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                         cwd=os.path.dirname(_PLUGIN))
    assert out.stdout.strip() == "0", out.stderr


# --------------------------------------------------------------------------- #
# Where it listens: a 0600 unix socket named by space, weights and version; no port
# --------------------------------------------------------------------------- #
def test_the_socket_is_0600_in_a_0700_dir_and_is_a_unix_socket(daemon_env):
    with _thread_daemon() as (daemon, _emb):
        p = X.paths(_SPACE)
        assert stat.S_IMODE(os.stat(p.sock).st_mode) == 0o600
        assert stat.S_ISSOCK(os.stat(p.sock).st_mode)
        assert stat.S_IMODE(os.stat(os.path.dirname(p.sock)).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(p.dir).st_mode) == 0o700
        assert daemon._listener.family == socket.AF_UNIX
        meta = json.load(open(p.meta, encoding="utf-8"))
        assert meta["pid"] == os.getpid() and meta["space"] == _SPACE and meta["socket"] == p.sock
    assert not os.path.exists(p.sock) and not os.path.exists(p.meta)  # it takes them with it


def test_neither_module_can_open_a_port():
    """No AF_INET socket, no TCP helper, no HTTP: the only address family in either file is
    AF_UNIX. (The evidence run also reads the live daemon's open files with lsof.)"""
    for mod in (D, X):
        src = open(mod.__file__, encoding="utf-8").read()
        assert "AF_UNIX" in src
        for banned in ("AF_INET", "create_server", "create_connection", "socketserver", "http.server"):
            assert banned not in src, f"{mod.__name__} mentions {banned}"


def test_a_daemon_is_named_by_space_weights_revision_plugin_version_and_data_dir(daemon_env, monkeypatch, tmp_path):
    key = X.daemon_key(_SPACE)
    assert X.daemon_key("Qwen/Qwen3-Embedding-8B") != key
    assert X.daemon_key(f"{_SPACE}@1024") != key
    assert X.daemon_key(_SPACE, str(tmp_path / "other-data")) != key
    monkeypatch.setitem(W._RUNNING, "v", "0.0.1")  # a session still on an older plugin
    assert X.daemon_key(_SPACE) != key
    monkeypatch.undo()
    spec = dict(E.HIGH_MODELS[_SPACE], revision="f" * 40)
    monkeypatch.setitem(E.HIGH_MODELS, _SPACE, spec)
    assert X.daemon_key(_SPACE) != key


def test_a_socket_path_too_long_for_sockaddr_un_moves_to_a_private_tmp_dir(monkeypatch, tmp_path):
    short = str(tmp_path / "a-plugin-data-dir-with-a-long-name" / "d")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", short)
    X._reset_for_tests()
    monkeypatch.setattr(X, "_sun_path_max", lambda: 4096)
    p = X.paths(_SPACE)
    assert p.sock == os.path.join(short, "embedd", X.daemon_key(_SPACE) + ".sock")  # the home path
    X._reset_for_tests()
    monkeypatch.setattr(X, "_sun_path_max", lambda: len(os.fsencode(p.sock)) - 1)
    monkeypatch.setattr(X.tempfile, "gettempdir", lambda: str(tmp_path))
    moved = X.paths(_SPACE)
    assert moved.sock == str(tmp_path / f"hippo-embedd-{os.getuid()}" / (X.daemon_key(_SPACE) + ".sock"))
    assert stat.S_IMODE(os.stat(os.path.dirname(moved.sock)).st_mode) == 0o700
    assert moved.lock == p.lock and moved.meta == p.meta  # only the socket moves
    X._reset_for_tests()
    os.chmod(os.path.dirname(moved.sock), 0o755)  # readable by others: not trusted
    assert X.paths(_SPACE).sock is None
    with pytest.raises(X.DaemonError):
        X.ensure(_SPACE)
    X._reset_for_tests()


# --------------------------------------------------------------------------- #
# One daemon, however many start
# --------------------------------------------------------------------------- #
def test_two_simultaneous_starts_leave_one_daemon(daemon_env):
    a, b = daemon_env["spawn"](_SPACE), daemon_env["spawn"](_SPACE)
    info = _until(lambda: X.hello(_SPACE, timeout=1.0), what="a daemon to answer")
    _until(lambda: a.poll() is not None or b.poll() is not None, what="the other start to exit")
    time.sleep(0.3)
    alive = [p for p in (a, b) if p.poll() is None]
    assert len(alive) == 1 and info["pid"] == alive[0].pid
    assert [p.returncode for p in (a, b) if p.poll() is not None] == [0]  # it lost, cleanly
    c = daemon_env["spawn"](_SPACE)  # a third, later: the winner answers, so it leaves at once
    assert c.wait(10) == 0 and X.hello(_SPACE, timeout=1.0)["pid"] == alive[0].pid


def test_two_sessions_ensuring_at_once_spawn_at_most_two_and_keep_one(daemon_env):
    out: list = []
    threads = [threading.Thread(target=lambda: out.append(X.ensure(_SPACE, load=True))) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert len(out) == 2 and out[0]["pid"] == out[1]["pid"] and out[0]["state"] == "loaded"
    _until(lambda: sum(p.poll() is None for p in daemon_env["procs"]) == 1, what="one daemon left")


def test_a_start_that_fails_is_not_retried_on_every_call(daemon_env, monkeypatch):
    calls: list = []
    monkeypatch.setattr(X, "SPAWN", lambda space: calls.append(space))  # nothing ever comes up
    monkeypatch.setattr(X, "_FIRST_BACKOFF_S", 5.0)  # the shipped spacing
    monkeypatch.setattr(X, "START_WAIT_S", 0.2)
    with pytest.raises(X.DaemonError, match="did not start"):
        X.ensure(_SPACE)
    with pytest.raises(X.DaemonError, match="next start in"):
        X.ensure(_SPACE)
    assert calls == [_SPACE]


def test_a_killed_daemons_socket_does_not_block_the_next_one(daemon_env):
    first = daemon_env["spawn"](_SPACE)
    _until(lambda: X.hello(_SPACE, timeout=1.0), what="the first daemon")
    first.kill()
    first.wait(10)
    assert os.path.exists(X.paths(_SPACE).sock) and X.hello(_SPACE) is None  # the stale socket
    info = X.ensure(_SPACE, load=True)
    assert info["pid"] != first.pid and info["state"] == "loaded"


# --------------------------------------------------------------------------- #
# The wire: the same bytes the embedder produced
# --------------------------------------------------------------------------- #
def test_vectors_cross_the_socket_bit_for_bit(daemon_env):
    docs = ["The zebra service deploys through the canary lane first.", "Llama billing cutover.", ""]
    with _thread_daemon() as (_daemon, emb):
        client = _client()
        got_docs = np.stack(list(client.embed(docs)))
        got_q = np.stack(list(client.query_embed(["how do we deploy zebra"])))
        assert list(client.embed([])) == []
    assert got_docs.dtype == np.float32 and got_docs.shape == (3, 2560)
    assert np.array_equal(got_docs, np.stack([emb._vec(d) for d in docs]))
    assert np.array_equal(got_q[0], emb._vec("how do we deploy zebra"))
    assert emb.docs == docs  # one request, the whole list, in order: the same slices as in-process


def test_an_index_built_through_the_daemon_is_the_index_built_in_process(daemon_env, project, monkeypatch):
    B.build_index(project["md"], project["idx"])
    with _thread_daemon():
        with E.using_space(_SPACE):
            via = B.build_index(project["md"], project["high"], allow_download=False)
    remote_rows = np.array(B.load_index(project["high"]).dense)
    monkeypatch.setattr(E, "BACKEND", fake.FakeEmbedder)  # in-process, the same fake
    E._LOADED.clear()
    local_dir = project["high"] + "-local"
    with E.using_space(_SPACE):
        local = B.build_index(project["md"], local_dir, allow_download=False)
    assert via["dense_ready"] and local["dense_ready"] and via["model"] == local["model"] == _SPACE
    assert np.array_equal(remote_rows, np.array(B.load_index(local_dir).dense))


def test_a_request_for_another_space_is_refused(daemon_env):
    with _thread_daemon():
        with pytest.raises(X.DaemonError, match="serves"):
            X.call(X.paths(_SPACE).sock, {"op": "embed", "space": "Qwen/Qwen3-Embedding-8B",
                                          "kind": "doc", "texts": ["x"]}, 5.0)
        with pytest.raises(X.DaemonError, match="unknown op"):
            X.call(X.paths(_SPACE).sock, {"op": "exec", "space": _SPACE}, 5.0)


# --------------------------------------------------------------------------- #
# Queries coalesce; a caller that stopped waiting is never embedded
# --------------------------------------------------------------------------- #
def _hold_first_pass(emb, client):
    """Occupy the daemon's model with one query pass the test holds open."""
    emb.gate = threading.Event()
    emb.entered.clear()
    first = threading.Thread(target=lambda: list(client.query_embed(["the pass that is running"])))
    first.start()
    assert emb.entered.wait(10)
    return first


@pytest.mark.parametrize("batch, passes", [(True, [1, 5]), (False, [1, 1, 1, 1, 1, 1])])
def test_queries_that_queue_behind_a_pass_share_the_next_one_unless_switched_off(daemon_env, batch, passes):
    with _thread_daemon(batch=batch) as (daemon, emb):
        client = _client()
        first = _hold_first_pass(emb, client)
        got: dict = {}
        waiting = [threading.Thread(target=lambda i=i: got.__setitem__(i, list(client.query_embed([f"zebra {i}"]))[0]))
                   for i in range(5)]
        for t in waiting:
            t.start()
        _until(lambda: len(daemon._queue) == 5, what="five queries to queue")
        emb.gate.set()
        for t in [first] + waiting:
            t.join(10)
        assert emb.passes == passes and X.hello(_SPACE)["batch"] is batch
        for i in range(5):  # each caller got its own vector back, whatever pass carried it
            assert np.array_equal(got[i], emb._vec(f"zebra {i}"))


def test_a_queued_query_past_its_deadline_is_not_embedded(daemon_env):
    with _thread_daemon() as (daemon, emb):
        first = _hold_first_pass(emb, _client())
        sock = X.paths(_SPACE).sock
        with pytest.raises(X.DaemonError, match="expired"):  # the caller is still on the line
            threading.Timer(0.3, emb.gate.set).start()
            X.call(sock, {"op": "embed", "space": _SPACE, "kind": "query",
                          "texts": ["nobody waits this long"], "deadline_ms": 50}, 10.0)
        first.join(10)
        assert emb.queries == ["the pass that is running"] and daemon.stats["expired"] == 1


def test_a_query_whose_caller_hung_up_is_not_embedded_and_marks_the_prompt(daemon_env):
    with _thread_daemon() as (daemon, emb):
        client = _client()
        first = _hold_first_pass(emb, client)
        with X.attempt(0.1) as tried:  # a served prompt with 100 ms left for the daemon
            with pytest.raises(B.DenseUnavailable):
                list(client.query_embed(["the prompt that gave up"]))
            t0 = time.monotonic()
            with pytest.raises(B.DenseUnavailable):  # one fault per prompt: no second wait
                list(client.query_embed(["a later tier of the same prompt"]))
            assert time.monotonic() - t0 < 0.05
        assert tried.fault
        _until(lambda: len(daemon._watch) == 1, what="the daemon to see the caller hang up")
        emb.gate.set()
        first.join(10)
        list(client.query_embed(["the next prompt"]))  # served as ever
        assert emb.queries == ["the pass that is running", "the next prompt"]
        assert daemon.stats["gone"] + daemon.stats["expired"] == 1


def test_outside_a_served_prompt_a_query_waits_the_dense_lanes_own_bound(daemon_env):
    with _thread_daemon() as (_daemon, emb):
        with E.using_space(_SPACE):
            vec = RR._query_vector("zebra canary lane")
    assert vec.shape == (2560,) and emb.queries == ["zebra canary lane"]


# --------------------------------------------------------------------------- #
# It reaps itself, and drops the model while idle
# --------------------------------------------------------------------------- #
def _write_heartbeat(pid, high=True):
    d = W.warm_dir(create=True)
    doc = {"pid": pid, "version": W.running_version(), "updated": time.time()}
    if high:
        doc["high"] = {"status": "ready", "space": _SPACE}
    with open(W.heartbeat_path(d, "sess"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def _dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_the_daemon_exits_when_no_warm_server_on_high_is_alive(daemon_env):
    _write_heartbeat(os.getpid())
    daemon = D.Daemon(_SPACE, load_embedder=lambda: fake.FakeEmbedder(*E.parse_space(_SPACE)),
                      grace_s=0.3, tick_s=0.02)
    assert daemon.start()
    thread = threading.Thread(target=lambda: (daemon.serve_forever(), daemon.close()), daemon=True)
    thread.start()
    time.sleep(0.8)
    assert thread.is_alive() and X.hello(_SPACE) is not None  # a live server on high: it stays
    _write_heartbeat(os.getpid(), high=False)  # the same live process, now on lean: not a reason
    thread.join(5)
    assert not thread.is_alive() and X.hello(_SPACE) is None
    assert not os.path.exists(X.paths(_SPACE).sock)
    again = D.Daemon(_SPACE, load_embedder=lambda: None)
    assert again.start()  # the lock went with it
    again.close()


def test_a_dead_servers_heartbeat_does_not_keep_the_daemon(daemon_env):
    _write_heartbeat(_dead_pid())
    assert D._live_servers() == 0
    _write_heartbeat(os.getpid())
    assert D._live_servers() == 1


def test_a_request_in_flight_keeps_the_daemon_past_its_grace(daemon_env):
    with _thread_daemon(live=lambda: 0, grace_s=0.2) as (daemon, emb):
        first = _hold_first_pass(emb, _client())
        time.sleep(0.6)
        assert X.hello(_SPACE) is not None  # no server alive, but someone is being served
        emb.gate.set()
        first.join(10)
        _until(lambda: X.hello(_SPACE) is None, what="the daemon to leave once idle")


def test_the_model_is_dropped_while_idle_and_reloaded_on_the_next_ask(daemon_env):
    loads: list = []

    def load():
        loads.append(1)
        time.sleep(0.2 if len(loads) > 1 else 0)  # a reload takes a moment, as a real one does
        return fake.FakeEmbedder(*E.parse_space(_SPACE))

    daemon = D.Daemon(_SPACE, load_embedder=load, live=lambda: 1, idle_s=0.25, tick_s=0.02)
    assert daemon.start()
    thread = threading.Thread(target=lambda: (daemon.serve_forever(), daemon.close()), daemon=True)
    thread.start()
    try:
        client = _client()
        assert client.ensure(load=True)["state"] == "loaded" and client.resident_bytes == 4 * 2**30
        _until(lambda: X.hello(_SPACE)["state"] == "idle", what="the idle unload")
        info = X.hello(_SPACE)
        assert info["resident_bytes"] == 0 and info["stats"]["unloads"] == 1
        assert client.ensure()["state"] == "idle"  # a server's 30 s check does not wake the model
        assert client.ready() is False  # a prompt's question does, and that prompt is lean
        _until(client.ready, what="the reload")
        assert len(loads) == 2
    finally:
        daemon.stop()
        thread.join(10)


def test_a_model_that_fails_to_load_is_reported_not_retried_in_a_loop(daemon_env):
    tries: list = []

    def boom():
        tries.append(1)
        raise RuntimeError("metal device lost")

    daemon = D.Daemon(_SPACE, load_embedder=boom, live=lambda: 1, tick_s=0.02)
    assert daemon.start()
    thread = threading.Thread(target=lambda: (daemon.serve_forever(), daemon.close()), daemon=True)
    thread.start()
    try:
        with pytest.raises(X.DaemonError, match="metal device lost"):
            X.ensure(_SPACE, load=True)
        client = _client()
        assert client.ready() is False and client.ready() is False
        with pytest.raises(B.DenseUnavailable):
            list(client.query_embed(["zebra"]))
        assert tries == [1] and X.hello(_SPACE)["state"] == "failed"
    finally:
        daemon.stop()
        thread.join(10)


# --------------------------------------------------------------------------- #
# The reranker rides the same daemon
# --------------------------------------------------------------------------- #
def test_the_rerank_seam_scores_through_the_daemon_and_keeps_the_fused_order_without_it(daemon_env, monkeypatch):
    monkeypatch.delenv("HIPPO_RERANK_TIMEOUT", raising=False)
    R._reset_for_tests()
    hits = [{"name": "llama", "description": "llama billing cutover", "corpus": "p"},
            {"name": "zebra", "description": "zebra deploy canary lane", "corpus": "p"}]
    with _thread_daemon() as (daemon, _emb):
        with R.using_reranker(R.WARM_MODEL, explicit=True):
            out = RR._cross_encoder_rerank("zebra deploy canary", hits)
        assert [h["name"] for h in out] == ["zebra", "llama"]
        assert isinstance(R.loaded(R.WARM_MODEL), X.RemoteReranker)
        assert R.status(R.WARM_MODEL)["status"] == "ready" and R.status(R.WARM_MODEL)["load_ms"] == 5.0
        assert daemon.stats["reranks"] == 1 and len(fake.FakeReranker.made) == 1
        with pytest.raises(X.DaemonError, match="not a high reranker"):
            X.call(X.paths(_SPACE).sock, {"op": "rerank", "model": "x/y", "query": "q", "docs": []}, 5.0)
    with R.using_reranker(R.WARM_MODEL, explicit=True):  # the daemon is gone
        t0 = time.monotonic()
        assert RR._cross_encoder_rerank("zebra deploy canary", hits) == hits
        assert time.monotonic() - t0 < 1.0  # it failed at once; nothing waited out a bound
    R._reset_for_tests()


# --------------------------------------------------------------------------- #
# The served path: high through the daemon, lean on any fault, no breaker trip
# --------------------------------------------------------------------------- #
def _order(data, text):
    with open(os.path.join(data, "fake-daemon-mode"), "w", encoding="utf-8") as fh:
        fh.write(text)


def _events(p):
    from memory.telemetry import default_telemetry_dir

    path = os.path.join(default_telemetry_dir(p["md"]), "recall_events.jsonl")
    if not os.path.exists(path):
        return []
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


def _serve_high(p, monkeypatch):
    """A session on high whose daemon is up and whose twin is level, proved by one served
    prompt that engaged it. Returns the session's breaker state."""
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")
    B.build_index(p["md"], p["idx"])
    assert "zebra_deploy_runbook" in _serve_warm("s1", "p1")  # lean while the daemon starts
    _wait_fresh(p)
    _until(lambda: H.status().get("status") == "ready", what="the loader to see the daemon")
    assert "zebra_deploy_runbook" in _serve_warm("s2", "p2", "zebra canary lane deploy steps")
    hb = _heartbeat("s2")["high"]
    assert hb["engaged"] is True and hb["daemon"] is True and hb["resident_bytes"] == 4 * 2**30
    return W._SESSIONS["s2"]


def _assert_served_lean_untripped(sid, pid, prompt, out):
    sess = W._SESSIONS[sid]
    assert "zebra_deploy_runbook" in out
    assert sess.tripped == "" and sess.fails == 0 and sess.last_path == "warm"
    assert _heartbeat(sid)["high"]["engaged"] is False and _heartbeat(sid)["accept"] is True


def test_a_server_on_high_serves_through_the_daemon_and_never_imports_mlx(daemon_env, project, monkeypatch):
    tried: list = []

    class _Spy:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in ("mlx", "mlx_lm"):
                tried.append(name)
            return None

    for mod in [m for m in sys.modules if m.split(".")[0] in ("mlx", "mlx_lm")]:
        monkeypatch.delitem(sys.modules, mod)
    monkeypatch.setattr(sys, "meta_path", [_Spy()] + sys.meta_path)
    _serve_high(project, monkeypatch)
    assert tried == [] and not [m for m in sys.modules if m.split(".")[0] in ("mlx", "mlx_lm")]
    assert sum(p.poll() is None for p in daemon_env["procs"]) == 1
    meaning, note = H.doctor_meaning("dense x", "high")
    assert note == "" and "the machine's one embedding daemon" in meaning
    assert f"embedding daemon loaded (pid {daemon_env['procs'][0].pid}" in meaning and "serving high" in meaning


def test_daemon_absent_from_the_start_serves_lean_and_says_why(daemon_env, project, monkeypatch):
    monkeypatch.setattr(X, "SPAWN", lambda space: None)
    monkeypatch.setattr(X, "START_WAIT_S", 0.2)
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")
    B.build_index(project["md"], project["idx"])
    out = _serve_warm("s1", "p1")
    _until(lambda: H.status().get("status") == "failed", what="the failed start to be recorded")
    out2 = _serve_warm("s1", "p2", "zebra canary lane deploy steps again please")
    assert "zebra_deploy_runbook" in out and out2 != "{}"
    assert "embedding daemon" in _heartbeat("s1")["high"]["reason"]
    assert W._SESSIONS["s1"].tripped == "" and W._SESSIONS["s1"].fails == 0
    meaning, note = H.doctor_meaning("dense x", "high")
    assert "embedding daemon not running" in meaning and "degraded to lean" in note


def test_daemon_gone_between_prompts_serves_lean_then_comes_back(daemon_env, project, monkeypatch):
    _serve_high(project, monkeypatch)
    proc = daemon_env["procs"][0]
    proc.kill()
    proc.wait(10)
    before = len(_events(project))
    out = _serve_warm("s3", "p3", "zebra canary lane deploy steps")
    _assert_served_lean_untripped("s3", "p3", _PROMPT, out)
    assert H._ST.faults == 0  # the plan's own question found it gone: no attempt, no wait
    assert len(_events(project)) == before + 1
    _until(lambda: len(daemon_env["procs"]) == 2 and (X.hello(_SPACE) or {}).get("state") == "loaded",
           what="the loader to restart the daemon")
    assert "zebra_deploy_runbook" in _serve_warm("s4", "p4", "zebra canary lane deploy steps")
    assert _heartbeat("s4")["high"]["engaged"] is True


def test_a_slow_daemon_serves_that_prompt_lean_without_tripping_the_breaker(daemon_env, project, monkeypatch):
    _serve_high(project, monkeypatch)
    monkeypatch.setattr(X, "QUERY_WAIT_S", 0.25)
    _order(daemon_env["data"], "slow 3")
    before = len(_events(project))
    t0 = time.monotonic()
    out = _serve_warm("s3", "p3", "zebra canary lane deploy steps")
    took = time.monotonic() - t0
    _assert_served_lean_untripped("s3", "p3", _PROMPT, out)
    assert took < W.SERVE_BUDGET_MS / 1000.0
    assert H._ST.faults == 1 and _heartbeat("s3")["high"]["faults"] == 1
    events = _events(project)
    assert len(events) == before + 1  # the discarded high attempt logged nothing
    assert events[-1].get("backend") != "bm25"  # lean's hybrid answer, not BM25 over the high index
    t0 = time.monotonic()  # held: the next prompt does not wait on the daemon at all
    out = _serve_warm("s4", "p4", "zebra canary lane deploy steps")
    assert time.monotonic() - t0 < 0.2 and H._ST.faults == 1
    _assert_served_lean_untripped("s4", "p4", _PROMPT, out)
    assert "served lean on a daemon fault" in H.doctor_meaning("dense x", "high")[0]


def test_a_daemon_killed_mid_call_serves_that_prompt_lean_and_high_resumes(daemon_env, project, monkeypatch):
    _serve_high(project, monkeypatch)
    monkeypatch.setattr(H, "FAULT_HOLD_S", 0.0)
    _order(daemon_env["data"], "die")
    out = _serve_warm("s3", "p3", "zebra canary lane deploy steps")
    _assert_served_lean_untripped("s3", "p3", _PROMPT, out)
    assert H._ST.faults == 1 and daemon_env["procs"][0].wait(10) == 9
    _order(daemon_env["data"], "")
    _until(lambda: (X.hello(_SPACE) or {}).get("state") == "loaded", what="the restarted daemon")
    assert "zebra_deploy_runbook" in _serve_warm("s4", "p4", "zebra canary lane deploy steps")
    assert _heartbeat("s4")["high"]["engaged"] is True and W._SESSIONS["s4"].tripped == ""


def test_time_lost_to_a_daemon_fault_is_not_charged_to_the_breaker(daemon_env, project, monkeypatch):
    _serve_high(project, monkeypatch)
    monkeypatch.setattr(X, "QUERY_WAIT_S", 0.3)
    monkeypatch.setattr(W, "SERVE_BUDGET_MS", 250.0)  # the daemon wait alone is over it
    monkeypatch.setattr(W, "HIGH_RESERVE_MS", -1000.0)
    _order(daemon_env["data"], "slow 3")
    out = _serve_warm("s3", "p3", "zebra canary lane deploy steps")
    _assert_served_lean_untripped("s3", "p3", _PROMPT, out)
    assert W._SESSIONS["s3"].last_ms > 250.0


def test_a_prompt_with_no_budget_left_for_the_daemon_is_lean_at_once(daemon_env, project, monkeypatch):
    _serve_high(project, monkeypatch)
    monkeypatch.setattr(W, "HIGH_RESERVE_MS", W.SERVE_BUDGET_MS)  # nothing left to wait with
    before = len(fake.FakeEmbedder.made)
    out = _serve_warm("s3", "p3", "zebra canary lane deploy steps")
    _assert_served_lean_untripped("s3", "p3", _PROMPT, out)
    assert H._ST.faults == 1 and len(fake.FakeEmbedder.made) == before
