"""HCR-7's open call (3), ruled 2026-11-27: how long the shared embed daemon lives.

The owner's eleventh ruling ("(b) Reap + model drop", idle window "1,800 s") replaced EMB-1's
single rule (exit after 900 s with no request). Now:

  - the daemon stays while a warm server on the high profile is alive, and leaves a grace
    (60 s) after the last one is gone and nothing is in flight;
  - a model nobody asked for in the idle window is dropped, and the process keeps listening;
  - a served prompt still never waits on a load: with the model dropped that prompt is lean,
    and the load starts behind it.

Process tests run the real daemon with its ``--fake`` models; the others run
``embed_daemon.Server`` on a thread with a backend the test controls. No MLX, so they run on
Linux CI.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager

import numpy as np
import pytest

from memory import embed_daemon as D
from memory import embed_mlx as E
from memory import recall_warm as W
from memory import recall_warm_high as H
from memory import rerank_mlx as R

from .test_embed_daemon_faults import (  # noqa: F401  (fixtures and the served-prompt helpers)
    _lean_untripped, _serve_high, _until, daemon,
)
from .test_embed_mlx import _fresh_warm_state, _heartbeat, _serve_warm, project  # noqa: F401

_SPACE = "Qwen/Qwen3-Embedding-4B"
_DIM = E.HIGH_MODELS[_SPACE]["native_dim"]
_RR = R.WARM_MODEL
_PROMPT = "zebra canary lane deploy steps"


def _wait(pred, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _beat(sid, pid, high=True, age_s=0.0):
    """A warm server's heartbeat, as ``recall_warm._publish`` leaves it."""
    doc = {"pid": pid, "updated": time.time() - age_s}
    if high:
        doc["high"] = {"status": "ready", "space": _SPACE}
    with open(W.heartbeat_path(W.warm_dir(create=True), sid), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


@pytest.fixture
def lifetime_env(monkeypatch):
    """A short private data dir, the fake models, long windows unless the test shortens one,
    and every daemon started here killed afterwards. Yields the socket path."""
    data = tempfile.mkdtemp(prefix="hl")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", data)
    monkeypatch.delenv(D.ENABLE_ENV, raising=False)
    monkeypatch.setenv(D.IDLE_ENV, "60")
    monkeypatch.setenv(D.GRACE_ENV, "60")
    monkeypatch.setattr(D, "SPAWN_ARGS", ["--fake"])
    path = D.socket_path()
    assert path.startswith(data)
    yield path
    info = D.ping(path)
    if info:
        try:
            os.kill(info["pid"], signal.SIGKILL)
        except OSError:
            pass
    shutil.rmtree(data, ignore_errors=True)


class _Slow:
    """The embedder's shape; its load and its passes take as long as the test says."""

    load_s = 0.0
    pass_s = 0.0

    def __init__(self, *a):
        time.sleep(_Slow.load_s)
        self.load_ms = self.resident_bytes = None

    def embed(self, texts):
        time.sleep(_Slow.pass_s)
        gone = E._GIVEN_UP.get()  # the real embedder checks between slices whether its caller left
        if gone is not None and gone.is_set():
            raise E.EmbedAbandoned()
        return iter([np.ones(4, dtype="float32") for _ in texts])

    query_embed = embed

    def score(self, query, docs):  # the reranker's shape too
        time.sleep(_Slow.pass_s)
        return [1.0 for _ in docs]


@contextmanager
def _server(**kw):
    """``embed_daemon.Server`` on a thread, with ``_Slow`` as both of its models."""
    _Slow.load_s = _Slow.pass_s = 0.0
    path = tempfile.mktemp(prefix="hl", suffix=".sock")
    srv = D.Server(path, _Slow, kw.pop("idle_s", 60.0), _Slow, **kw)
    orig = E.unavailable_reason, R.unavailable_reason
    E.unavailable_reason = R.unavailable_reason = lambda model: None
    srv.bind()
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        E.unavailable_reason, R.unavailable_reason = orig
        srv._idle = lambda: True
        thread.join(10)
        try:
            os.unlink(path)
        except OSError:
            pass


def _loaded(path):
    return (D.ping(path) or {}).get("loaded")


# --------------------------------------------------------------------------- #
# The windows
# --------------------------------------------------------------------------- #
def test_the_shipped_windows_are_the_ruled_ones(monkeypatch):
    monkeypatch.delenv(D.IDLE_ENV, raising=False)
    monkeypatch.delenv(D.GRACE_ENV, raising=False)
    assert D.idle_seconds() == D.DEFAULT_IDLE_S == 1800.0  # the eleventh ruling's "1,800 s"
    assert D.grace_seconds() == D.DEFAULT_GRACE_S == 60.0
    monkeypatch.setenv(D.GRACE_ENV, "junk")
    assert D.grace_seconds() == 60.0
    monkeypatch.setenv(D.GRACE_ENV, "5")
    assert D.grace_seconds() == 5.0


# --------------------------------------------------------------------------- #
# Who counts as a live warm server on high
# --------------------------------------------------------------------------- #
def test_only_a_live_server_on_high_with_a_current_heartbeat_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    assert D._live_servers() == 0  # no warm dir at all
    _beat("dead", _dead_pid())
    _beat("lean", os.getpid(), high=False)  # alive, not on the high profile
    _beat("old", os.getpid(), age_s=W.STALE_STATE_S + 60)  # recall_warm's sweep would delete it
    assert D._live_servers() == 0
    _beat("live", os.getpid())
    assert D._live_servers() == 1


# --------------------------------------------------------------------------- #
# Staying and leaving
# --------------------------------------------------------------------------- #
def test_the_daemon_stays_while_a_warm_server_on_high_is_alive_and_leaves_after_it(lifetime_env, monkeypatch):
    monkeypatch.setenv(D.GRACE_ENV, "1")
    server = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        _beat("s1", server.pid)
        pid = D.ensure(lifetime_env)["pid"]
        time.sleep(3.5)  # three graces and more with nothing asked of it
        assert (D.ping(lifetime_env) or {}).get("pid") == pid, "it left under a live server"
    finally:
        server.kill()
        server.wait()
    assert _wait(lambda: not os.path.exists(lifetime_env)), "it outlived the last server"
    assert D.ping(lifetime_env) is None


def test_a_dead_servers_heartbeat_does_not_keep_the_daemon(lifetime_env, monkeypatch):
    monkeypatch.setenv(D.GRACE_ENV, "1")
    _beat("gone", _dead_pid())
    D.ensure(lifetime_env)
    assert _wait(lambda: not os.path.exists(lifetime_env)), "a dead session's heartbeat kept it"


def test_an_explicit_recall_brings_back_a_daemon_that_left_and_still_reranks(lifetime_env, monkeypatch):
    """With no warm server on high (warm recall off, the CLI) the daemon leaves a grace after
    each request. An explicit surface has 15 s and may load, so it asks once more instead of
    losing the rerank of every recall that comes later than the grace."""
    monkeypatch.setenv(D.GRACE_ENV, "0.5")
    rr = D.DaemonReranker(_RR)
    first = rr.daemon_pid
    assert _wait(lambda: not os.path.exists(lifetime_env)), "the daemon nobody uses never left"
    with R.using_reranker(_RR, explicit=True):
        scores = rr.score("zebra canary lane", ["zebra canary lane", "llama billing"])
    assert scores[0] > scores[1] and rr.alive and rr.daemon_pid != first
    assert _wait(lambda: not os.path.exists(lifetime_env))
    with pytest.raises(R.RerankUnavailable):  # the warm path has no room for a load: fused order, as before
        rr.score("zebra", ["zebra"])
    assert rr.alive is False


def test_a_request_in_flight_keeps_the_daemon_past_its_grace():
    servers = [1]  # a live server until the pass is running, so a slow start cannot end the daemon first
    with _server(grace_s=0.3, live=lambda: servers[0]) as srv:
        _Slow.pass_s = 2.0
        got: list = []
        t = threading.Thread(target=lambda: got.append(
            D.request(srv.path, {"op": "embed", "space": _SPACE, "texts": ["a build's pass"]})))
        t.start()
        assert _wait(lambda: srv._inflight == 1, 5)
        servers[0] = 0  # the session that asked is the only one, and it has no heartbeat any more
        time.sleep(1.2)  # four graces into the pass
        assert D.ping(srv.path) is not None
        t.join(10)
        assert got and got[0][0]["ok"] and len(got[0][1]) == 16
        assert _wait(lambda: D.ping(srv.path, timeout=0.3) is None), "it stayed with nobody to serve"


def test_a_live_check_that_raises_is_not_a_reason_to_leave():
    def unreadable():
        raise OSError("the heartbeat directory cannot be read")

    with _server(grace_s=0.2, live=unreadable) as srv:
        time.sleep(1.5)
        assert D.ping(srv.path) is not None


# --------------------------------------------------------------------------- #
# The model drop
# --------------------------------------------------------------------------- #
def test_an_idle_daemon_drops_its_models_and_keeps_listening(lifetime_env, monkeypatch):
    monkeypatch.setenv(D.IDLE_ENV, "0.5")
    _beat("s1", os.getpid())  # a live warm server on high: the process stays
    emb = D.DaemonEmbedder(_SPACE, _DIM)
    D.DaemonReranker(_RR)
    pid = emb.daemon_pid
    assert _wait(lambda: _loaded(lifetime_env) == [] and D.ping(lifetime_env)["rerankers"] == []), \
        "the idle models were never dropped"
    info = D.ping(lifetime_env)
    assert info["pid"] == pid and info["stats"]["unloads"] == 2  # the embedder and the reranker
    assert emb.ready() is False and emb.alive is False  # what a served prompt asks: this one is lean
    emb.revive()  # the hippo-high thread's part
    info = D.ping(lifetime_env)
    assert info["pid"] == pid and info["loads"] == 2 and emb.alive
    assert len(list(emb.query_embed(["zebra"]))) == 1


def test_pings_do_not_keep_the_model(lifetime_env, monkeypatch):
    monkeypatch.setenv(D.IDLE_ENV, "1")
    _beat("s1", os.getpid())
    D.DaemonEmbedder(_SPACE, _DIM)
    end = time.monotonic() + 10
    while _loaded(lifetime_env) and time.monotonic() < end:  # doctor, and every prompt's ready()
        time.sleep(0.1)
    assert _loaded(lifetime_env) == []


def test_each_model_is_dropped_on_its_own_idle_time():
    """Two sessions on different models share the daemon. The one nobody asks for goes, however
    busy the other keeps the daemon: one idle clock for all would hold both for good."""
    with _server(idle_s=1.0, live=lambda: 1) as srv:
        assert D.request(srv.path, {"op": "load", "space": _SPACE})[0]["ok"]
        assert D.request(srv.path, {"op": "rerank_load", "model": _RR})[0]["ok"]
        end = time.monotonic() + 3.0
        while time.monotonic() < end:  # the embedder is asked all along; the reranker never again
            assert D.request(srv.path, {"op": "query", "space": _SPACE, "texts": ["q"]})[0]["ok"]
            time.sleep(0.1)
        info = D.ping(srv.path)
        assert info["loaded"] == [_SPACE] and info["rerankers"] == []
        assert info["loads"] == 1  # the busy model was never dropped and loaded again


def test_a_model_is_never_dropped_under_a_request():
    with _server(idle_s=0.2, live=lambda: 1) as srv:
        assert D.request(srv.path, {"op": "load", "space": _SPACE})[0]["ok"]
        _Slow.pass_s = 1.2
        t = threading.Thread(target=D.request, args=(srv.path, {"op": "embed", "space": _SPACE, "texts": ["a"]}))
        t.start()
        time.sleep(0.8)  # four idle windows into the pass
        assert _loaded(srv.path) == [_SPACE]
        t.join(10)
        assert _wait(lambda: _loaded(srv.path) == [])
        assert D.ping(srv.path)["stats"]["unloads"] >= 1


# --------------------------------------------------------------------------- #
# A daemon whose model keeps failing leaves, so the next call starts a fresh one
# --------------------------------------------------------------------------- #
class _Broken(_Slow):
    """Loads, then fails every pass the way MLX did on 2026-11-27 (Metal's compiler service
    gone for that one process: six queries of six failed, with the model held and ``ready``
    saying yes)."""

    fail = True

    def embed(self, texts):
        if _Broken.fail:
            raise RuntimeError("[metal::Device] Unable to build metal library from source")
        return iter([np.ones(4, dtype="float32") for _ in texts])

    query_embed = embed


def test_a_daemon_whose_model_fails_every_call_leaves_under_a_live_server(monkeypatch):
    monkeypatch.setattr(_Slow, "embed", _Broken.embed)
    monkeypatch.setattr(_Slow, "query_embed", _Broken.embed)
    _Broken.fail = True
    with _server(grace_s=600.0, live=lambda: 1) as srv:  # a live warm server on high: it would stay for days
        ask = {"op": "query", "space": _SPACE, "texts": ["q"]}
        for _ in range(D._BROKEN_AFTER - 1):
            hdr, _ = D.request(srv.path, ask)
            assert hdr["ok"] is False and hdr["reason"] == "error"
        _Broken.fail = False  # one pass that works: the count starts over
        assert D.request(srv.path, ask)[0]["ok"]
        _Broken.fail = True
        for _ in range(D._BROKEN_AFTER - 1):
            assert D.request(srv.path, ask)[0]["ok"] is False
        assert D.ping(srv.path) is not None, "it left before the model had failed often enough in a row"
        assert D.request(srv.path, ask)[0]["ok"] is False
        assert _wait(lambda: D.ping(srv.path, timeout=0.3) is None), "a daemon that cannot embed stayed"


def test_a_caller_that_stopped_waiting_is_not_a_model_failure():
    with _server(live=lambda: 1) as srv:
        assert D.request(srv.path, {"op": "load", "space": _SPACE})[0]["ok"]
        _Slow.pass_s = 0.4
        for _ in range(D._BROKEN_AFTER + 1):  # each caller waits 50 ms and has gone by the pass's end
            hdr, _ = D.request(srv.path, {"op": "query", "space": _SPACE, "texts": ["q"], "wait_ms": 50.0})
            assert hdr["ok"] is False and hdr["reason"] == "expired"
        _Slow.pass_s = 0.0
        assert D.request(srv.path, {"op": "query", "space": _SPACE, "texts": ["q"]})[0]["ok"]
        assert D.ping(srv.path)["stats"]["expired"] == D._BROKEN_AFTER + 1


# --------------------------------------------------------------------------- #
# A served prompt never waits on a load
# --------------------------------------------------------------------------- #
def test_a_served_query_for_a_model_the_daemon_does_not_hold_is_refused_at_once_and_the_load_starts():
    with _server(live=lambda: 1) as srv:
        _Slow.load_s = 1.0
        served = {"op": "query", "space": _SPACE, "texts": ["q"], "wait_ms": 750.0}
        t0 = time.monotonic()
        hdr, _ = D.request(srv.path, served)
        assert hdr["ok"] is False and hdr["reason"] == "busy" and time.monotonic() - t0 < 0.5
        assert _wait(lambda: _loaded(srv.path) == [_SPACE]), "the refused query did not start the load"
        hdr, payload = D.request(srv.path, served)
        assert hdr["ok"] and len(payload) == 16 and D.ping(srv.path)["loads"] == 1
        # A caller with no wait of its own (a bench, the floor sweep) still gets its vector.
        assert srv.unload() is True
        hdr, _ = D.request(srv.path, {"op": "query", "space": _SPACE, "texts": ["q"]})
        assert hdr["ok"] and D.ping(srv.path)["loads"] == 2


def test_a_warm_rerank_for_a_reranker_the_daemon_does_not_hold_answers_busy_and_the_load_starts():
    with _server(live=lambda: 1) as srv:
        _Slow.load_s = 1.0
        warm = {"op": "rerank", "model": _RR, "query": "q", "docs": ["a", "b"]}
        t0 = time.monotonic()
        hdr, _ = D.request(srv.path, warm)
        assert hdr["ok"] is False and hdr["reason"] == "busy" and time.monotonic() - t0 < 0.5
        assert _wait(lambda: D.ping(srv.path)["rerankers"] == [_RR])
        assert D.request(srv.path, warm)[0] == {"ok": True, "scores": [1.0, 1.0]}
        # An explicit surface (a 15 s bound) waits for the load instead of losing the rerank.
        assert srv.unload() is True
        hdr, _ = D.request(srv.path, dict(warm, wait_s=5.0))
        assert hdr == {"ok": True, "scores": [1.0, 1.0]} and D.ping(srv.path)["rerank_loads"] == 2


def test_the_first_prompt_after_the_model_was_dropped_is_lean_and_high_comes_back(daemon, project, monkeypatch):
    _serve_high(project, monkeypatch)
    assert daemon.unload() is True
    assert _loaded(daemon.path) == [] and E.loaded(_SPACE) is not None  # nobody has noticed yet
    out = _serve_warm("s3", "p3", _PROMPT)
    _lean_untripped("s3", out)
    assert H._ST.faults == 0 and H._ST.busy == 0  # found before the attempt: nothing was waited for
    _until(lambda: _loaded(daemon.path) == [_SPACE], what="the hippo-high thread to reload it")
    _until(lambda: H.status().get("status") == "ready", what="the loader")
    assert "zebra_deploy_runbook" in _serve_warm("s4", "p4", _PROMPT)
    assert _heartbeat("s4")["high"]["engaged"] is True
    assert D.ping(daemon.path)["pid"] == os.getpid() and D.ping(daemon.path)["loads"] == 2


def test_a_model_dropped_between_the_question_and_the_query_serves_lean_with_no_hold(daemon, project, monkeypatch):
    _serve_high(project, monkeypatch)
    assert daemon.unload() is True
    monkeypatch.setattr(D.DaemonEmbedder, "ready", lambda self: True)  # asked a moment before the drop
    t0 = time.monotonic()
    out = _serve_warm("s3", "p3", _PROMPT)
    assert time.monotonic() - t0 < 1.0
    _lean_untripped("s3", out)
    assert H._ST.busy == 1 and H._ST.faults == 0 and H._ST.hold_until == 0.0  # refused at once: no wait, no hold
    _until(lambda: _loaded(daemon.path) == [_SPACE], what="the load the refused query started")
    assert "zebra_deploy_runbook" in _serve_warm("s4", "p4", _PROMPT)
    assert _heartbeat("s4")["high"]["engaged"] is True


def test_doctor_says_when_the_daemon_dropped_its_model(daemon):
    D.DaemonEmbedder(_SPACE, _DIM)
    assert f"holding {_SPACE}" in H._daemon_state()
    assert daemon.unload() is True
    state = H._daemon_state()
    assert f"embed daemon running (pid {os.getpid()})" in state and "dropped its model" in state
    assert "served lean" in state
