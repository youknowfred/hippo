"""EMB-1: the shared embed daemon — N sessions, one model load.

Each session's warm MCP server used to load its own Qwen3-Embedding (4.3-6.7 GB each; 5 of
26 servers held 26.3 GB on 2026-11-27). These tests run the real daemon process with its
``--fake`` embedder (no MLX), so they run on Linux CI.
"""

from __future__ import annotations

import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import time

import numpy as np
import pytest

from memory import build_index as B
from memory import embed_daemon as D
from memory import embed_mlx as E

_SPACE = "Qwen/Qwen3-Embedding-4B"
_DIM = E.HIGH_MODELS[_SPACE]["native_dim"]
_PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(D.__file__)))


def _kill(path):
    info = D.ping(path)
    if info:
        try:
            os.kill(info["pid"], signal.SIGKILL)
        except OSError:
            pass


@pytest.fixture
def daemon_env(monkeypatch):
    """A short private data dir (the socket's main path, not the long-path fallback), the
    fake embedder, and every daemon started here killed afterwards."""
    data = tempfile.mkdtemp(prefix="hd")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", data)
    monkeypatch.delenv(D.ENABLE_ENV, raising=False)
    monkeypatch.setenv(D.IDLE_ENV, "60")
    monkeypatch.setattr(D, "SPAWN_ARGS", ["--fake"])
    path = D.socket_path()
    assert path.startswith(data)
    yield path
    _kill(path)
    shutil.rmtree(data, ignore_errors=True)


def _wait(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


_SESSION = textwrap.dedent("""
    import sys
    from memory import embed_daemon as D
    D.SPAWN_ARGS = ["--fake"]
    emb = D.DaemonEmbedder(sys.argv[1], int(sys.argv[2]))
    v = list(emb.query_embed(["zebra canary lane"]))[0]
    print(emb.daemon_pid, float(v.sum()))
""")


def test_n_sessions_share_one_daemon_and_one_model_load(daemon_env):
    """Six session processes start at once and race to spawn: one daemon, one load."""
    env = dict(os.environ, PYTHONPATH=_PLUGIN)
    procs = [subprocess.Popen([sys.executable, "-c", _SESSION, _SPACE, str(_DIM)], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for _ in range(6)]
    outs = [p.communicate(timeout=60) for p in procs]
    assert all(p.returncode == 0 for p in procs), [o[1] for o in outs]
    pids = {o[0].split()[0] for o in outs}
    sums = {o[0].split()[1] for o in outs}
    assert len(pids) == 1, f"one daemon for every session, got {pids}"
    assert len(sums) == 1  # every session gets the same vector
    info = D.ping(daemon_env)
    assert str(info["pid"]) in pids and info["loads"] == 1 and info["loaded"] == [_SPACE]


def test_the_socket_is_private_to_its_user(daemon_env):
    D.DaemonEmbedder(_SPACE, _DIM)
    assert stat.S_IMODE(os.stat(daemon_env).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(daemon_env)).st_mode) == 0o700


def test_vectors_round_trip_and_documents_differ_from_queries(daemon_env):
    emb = D.DaemonEmbedder(_SPACE, 1024)
    assert emb.space == f"{_SPACE}@1024" and emb.resident_bytes == 1234
    docs = list(emb.embed([f"doc {i} zebra" for i in range(70)]))  # > one request chunk
    q = list(emb.query_embed(["doc 1 zebra"]))[0]
    assert len(docs) == 70 and docs[0].shape == (1024,) and docs[0].dtype == np.float32
    assert not np.array_equal(docs[1], q)
    assert list(emb.embed([])) == []


def test_an_idle_daemon_exits_and_a_served_call_brings_it_back(daemon_env, monkeypatch):
    monkeypatch.setenv(D.IDLE_ENV, "0.5")
    emb = D.DaemonEmbedder(_SPACE, _DIM)
    first = emb.daemon_pid
    E._LOADED[_SPACE] = emb
    try:
        assert _wait(lambda: not os.path.exists(daemon_env)), "the idle daemon never left"
        assert _wait(lambda: not _alive(first))
        t0 = time.monotonic()
        with pytest.raises(B.DenseUnavailable):
            list(emb.query_embed(["zebra"]))
        assert time.monotonic() - t0 < 2.0  # fails fast: the warm call serves lean instead
        assert emb.alive is False and E.loaded(_SPACE) is None
        emb.revive()
        assert emb.alive and emb.daemon_pid != first and E.loaded(_SPACE) is emb
        assert D.ping(daemon_env)["loads"] == 1
    finally:
        E._LOADED.pop(_SPACE, None)


def test_pings_do_not_keep_an_idle_daemon_alive(daemon_env, monkeypatch):
    monkeypatch.setenv(D.IDLE_ENV, "1")
    D.DaemonEmbedder(_SPACE, _DIM)
    end = time.monotonic() + 10
    while os.path.exists(daemon_env) and time.monotonic() < end:
        D.ping(daemon_env)
        time.sleep(0.1)
    assert not os.path.exists(daemon_env)


def test_a_killed_daemon_mid_build_is_revived_not_written_off(daemon_env):
    emb = D.DaemonEmbedder(_SPACE, _DIM)
    _kill(daemon_env)
    assert _wait(lambda: D.ping(daemon_env) is None)
    rows = list(emb.embed(["zebra deploy", "llama billing"]))  # a build: reloads, carries on
    assert len(rows) == 2 and emb.alive


def test_a_daemon_for_other_embedding_code_is_never_reached(daemon_env, monkeypatch):
    monkeypatch.setattr(D, "code_id", lambda: "0123456789ab")
    assert D.socket_path() != daemon_env


def test_a_long_data_dir_falls_back_to_a_short_private_socket_path(monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", "/x/" + "d" * 150)
    path = D.socket_path()
    assert len(path.encode()) <= D._SUN_PATH_MAX and path.endswith(".sock")
    assert f"hippo-{os.getuid()}-" in path


def test_off_switch_loads_in_process(monkeypatch):
    made = []
    monkeypatch.setattr(E, "_MLXEmbedder", lambda *a: made.append(a) or "in-process")
    monkeypatch.setenv(D.ENABLE_ENV, "0")
    assert E._default_backend(_SPACE, _DIM, "/snap") == "in-process" and made


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a zombie still answers kill(0); it is gone for our purposes
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
        return bool(out.stdout.strip()) and not out.stdout.strip().startswith("Z")
    except OSError:
        return True


def test_a_query_waits_for_one_document_batch_not_every_sessions_build(tmp_path):
    """Several sessions building at once take turns; a query never queues behind all of them."""
    import threading

    live, peak, done = [0], [0], []
    lock = threading.Lock()

    class Slow:
        def __init__(self, *a):
            self.load_ms = self.resident_bytes = None

        def embed(self, texts):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.15)
            with lock:
                live[0] -= 1
            done.append("doc")
            return iter([np.ones(4, dtype="float32") for _ in texts])

        def query_embed(self, texts):
            done.append("query")
            return iter([np.ones(4, dtype="float32") for _ in texts])

    path = tempfile.mktemp(prefix="hq", suffix=".sock")
    srv = D.Server(path, Slow, idle_s=60)
    orig = E.unavailable_reason
    E.unavailable_reason = lambda model: None
    try:
        srv.bind()
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        builds = [threading.Thread(target=D.request, args=(path, {"op": "embed", "space": _SPACE, "texts": ["a"]}))
                  for _ in range(4)]
        for t in builds:
            t.start()
        time.sleep(0.05)
        hdr, payload = D.request(path, {"op": "query", "space": _SPACE, "texts": ["q"]})
        for t in builds:
            t.join()
        assert hdr["ok"] and len(payload) == 16
        assert peak[0] == 1  # one document batch at a time
        assert done.index("query") < 3  # behind at most the batches already running
    finally:
        E.unavailable_reason = orig
        srv._idle = lambda: True
        try:
            os.unlink(path)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# EMB-2: the reranker in the same daemon
# --------------------------------------------------------------------------- #
from memory import rerank_mlx as R  # noqa: E402

_RR = R.WARM_MODEL

_RERANK_SESSION = textwrap.dedent("""
    import sys
    from memory import embed_daemon as D
    D.SPAWN_ARGS = ["--fake"]
    rr = D.DaemonReranker(sys.argv[1])
    print(rr.daemon_pid, rr.score("zebra canary lane", ["zebra canary", "llama billing"]))
""")


def test_n_sessions_share_one_reranker_load(daemon_env):
    env = dict(os.environ, PYTHONPATH=_PLUGIN)
    procs = [subprocess.Popen([sys.executable, "-c", _RERANK_SESSION, _RR], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for _ in range(5)]
    outs = [p.communicate(timeout=60) for p in procs]
    assert all(p.returncode == 0 for p in procs), [o[1] for o in outs]
    assert len({o[0].split(" ", 1)[0] for o in outs}) == 1
    assert len({o[0].split(" ", 1)[1] for o in outs}) == 1
    info = D.ping(daemon_env)
    assert info["rerank_loads"] == 1 and info["rerankers"] == [_RR] and info["loads"] == 0


def test_the_embedder_and_the_reranker_share_one_daemon(daemon_env):
    emb = D.DaemonEmbedder(_SPACE, _DIM)
    rr = D.DaemonReranker(_RR)
    assert emb.daemon_pid == rr.daemon_pid and rr.resident_bytes == 567
    s = rr.score("zebra canary lane", ["zebra canary lane", "llama billing"])
    assert s[0] > s[1]
    info = D.ping(daemon_env)
    assert info["loads"] == 1 and info["rerank_loads"] == 1


def test_a_reranker_still_scoring_answers_busy_to_another_session(tmp_path):
    """HCR-4's fail-fast rule, across sessions: a second scoring never queues behind one."""
    import threading

    started = threading.Event()

    class Slow:
        def __init__(self, *a):
            self.load_ms = self.resident_bytes = None

        def score(self, query, docs):
            started.set()
            time.sleep(0.4)
            return [1.0 for _ in docs]

    path = tempfile.mktemp(prefix="hr", suffix=".sock")
    srv = D.Server(path, None, idle_s=60, rerank_backend=Slow)
    orig = R.unavailable_reason
    R.unavailable_reason = lambda model: None
    try:
        srv.bind()
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        req = {"op": "rerank", "model": _RR, "query": "q", "docs": ["a", "b"]}
        first = []
        t = threading.Thread(target=lambda: first.append(D.request(path, req)))
        t.start()
        assert started.wait(5)
        t0 = time.monotonic()
        hdr, _ = D.request(path, req)
        assert hdr["ok"] is False and hdr["reason"] == "busy" and time.monotonic() - t0 < 0.3
        t.join()
        assert first[0][0] == {"ok": True, "scores": [1.0, 1.0]}

        # An explicit surface (15 s bound) waits its turn instead of losing the rerank.
        started.clear()
        t = threading.Thread(target=lambda: D.request(path, req))
        t.start()
        assert started.wait(5)
        with R.using_reranker(_RR, explicit=True):
            rr = object.__new__(D.DaemonReranker)
            rr.model_id, rr.path, rr.alive = _RR, path, True
            assert rr.score("q", ["a", "b"]) == [1.0, 1.0]
        t.join()
    finally:
        R.unavailable_reason = orig
        srv._idle = lambda: True
        try:
            os.unlink(path)
        except OSError:
            pass


def test_a_gone_daemon_keeps_the_fused_order_and_a_served_call_revives_it(daemon_env, monkeypatch):
    monkeypatch.setattr(R, "unavailable_reason", lambda model: None)
    R._reset_for_tests()
    try:
        R.reranker(_RR)  # an explicit surface's first use: loads through the daemon
        first = R.status(_RR)["daemon_pid"]
        _kill(daemon_env)
        assert _wait(lambda: D.ping(daemon_env) is None)
        t0 = time.monotonic()
        with pytest.raises(R.RerankUnavailable):
            R.loaded(_RR).score("zebra", ["zebra"])
        assert time.monotonic() - t0 < 2.0 and R.loaded(_RR) is None
        with pytest.raises(R.RerankUnavailable):
            R.score(_RR, "zebra", ["zebra"])  # the warm path: fused order, no load on the call
        R.start_loading(_RR)  # what the next served call's kick does
        assert _wait(lambda: R.loaded(_RR) is not None)
        assert R.status(_RR)["daemon_pid"] != first
        assert R.score(_RR, "zebra lane", ["zebra lane", "llama"])[0] == 1.0
    finally:
        R._reset_for_tests()


def test_off_switch_loads_the_reranker_in_process(monkeypatch):
    made = []
    monkeypatch.setattr(R, "_MLXReranker", lambda *a: made.append(a) or "in-process")
    monkeypatch.setenv(D.ENABLE_ENV, "0")
    assert R._default_backend(_RR, "/snap") == "in-process" and made
