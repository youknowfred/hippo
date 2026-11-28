"""HCR-7: the four gaps EMB-1's daemon had against the ruling, each as the test that found it.

Written against ``main`` at d74c560 (the daemon as #260 and #261 merged it), before the port
and without sight of it, and run there first. Each failed:

  1. the first prompt after the daemon went was answered from the high index (``engaged``
     true, lean's embedder never asked);
  2. a slow daemon, and one that died mid-query, did the same;
  3. a 70-row build pass reached the model as three requests, ``[32, 32, 6]``;
  4. a query sent with a 50 ms wait was embedded anyway.

``test_embed_daemon_faults`` holds the port's own tests. These stay beside them because they
ask a different question of the same paths: not "did the event say hybrid" but "was lean's own
embedder asked, and did the big model see nothing". They also hold the one case that file does
not: a session's first prompt while the daemon is still starting. The daemon is the real
``embed_daemon.Server`` on a thread of the test process with a backend the test controls (no
MLX); the warm server is the real ``recall_warm.serve`` with its default backend.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager

import numpy as np
import pytest

from memory import build_index as B
from memory import embed_daemon as D
from memory import embed_mlx as E
from memory import recall_warm as W
from memory import recall_warm_high as H

from .test_embed_mlx import (  # noqa: F401  (fixtures and the warm-call helpers)
    _FakeLean, _FakeQwen, _PROMPT, _SPACE, _fresh_warm_state, _heartbeat, _serve_warm, _wait_fresh, fakes, project,
)

_DIM = E.HIGH_MODELS[_SPACE]["native_dim"]
_ASK = "zebra canary lane deploy steps"


class _Lean(_FakeLean):
    """Lean's stand-in, recording every text it embeds: a prompt served lean asks it."""

    asked: list = []

    def embed(self, texts):
        texts = list(texts)
        _Lean.asked.extend(texts)
        return super().embed(texts)

    query_embed = embed


class _Model(_FakeQwen):
    """The daemon's model. ``hold`` makes a query wait until ``release`` (a slow daemon, or a
    pass the test keeps open); ``die`` drops the connection with no answer, which is what a
    client of a daemon killed mid-call sees."""

    hold = False
    die = False
    entered = threading.Event()
    release = threading.Event()
    doc_calls: list = []

    def embed(self, texts):
        texts = list(texts)
        _Model.doc_calls.append(len(texts))
        return super().embed(texts)

    def query_embed(self, texts):
        _Model.entered.set()
        if _Model.die:
            raise SystemExit  # not an Exception: the handler answers nothing and closes
        if _Model.hold:
            _Model.release.wait(30)
        return super().query_embed(texts)


@pytest.fixture
def model():
    _Model.hold = _Model.die = False
    _Model.entered.clear()
    _Model.release.clear()
    _Model.doc_calls.clear()
    _Lean.asked.clear()
    yield _Model
    _Model.hold = _Model.die = False
    _Model.release.set()


@contextmanager
def _daemon(backend=_Model, idle_s=60.0):
    """The real server on a thread, at the socket this process's clients resolve."""
    path = D.socket_path()
    D._private_dir(os.path.dirname(path))
    srv = D.Server(path, backend, idle_s)
    srv.bind()
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        _Model.release.set()
        srv._idle = lambda: True
        thread.join(10)


@pytest.fixture
def high(fakes, project, model, monkeypatch):  # noqa: F811
    """A session on the high profile whose embedder is the daemon's client."""
    short = tempfile.mkdtemp(prefix="hd")  # a short data dir: the socket's main path
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", short)
    monkeypatch.delenv(D.ENABLE_ENV, raising=False)
    monkeypatch.setenv(D.IDLE_ENV, "20")  # a daemon a revive started after teardown leaves by itself
    monkeypatch.setattr(D, "SPAWN_ARGS", ["--fake"])
    monkeypatch.setattr(E, "BACKEND", E._default_backend)
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")
    for allow in (True, False):
        B._MODEL_CACHE[(B.DEFAULT_MODEL, allow)] = _Lean()
    B.build_index(project["md"], project["idx"])
    yield project
    loader = H._ST.thread
    H._reset_for_tests()
    if loader is not None:
        H._ST.wake.set()  # the loop may be about to wait on the new state's event
        loader.join(30)  # a revive in flight ends before the next test's state exists
        H._ST.wake.clear()
    info = D.ping()
    if info and info["pid"] != os.getpid():
        import signal

        os.kill(info["pid"], signal.SIGKILL)
    shutil.rmtree(short, ignore_errors=True)


def _engage(p):
    """Two served prompts: lean while the client connects and the twin is built, then high."""
    assert "zebra_deploy_runbook" in _serve_warm("s1", "p1")
    _wait_fresh(p)
    end = time.time() + 20
    while time.time() < end and H.status().get("status") != "ready":
        time.sleep(0.01)
    assert "zebra_deploy_runbook" in _serve_warm("s2", "p2", _ASK)
    assert _heartbeat("s2")["high"]["engaged"] is True
    _Lean.asked.clear()


def _events(p):
    from memory.telemetry import default_telemetry_dir

    path = os.path.join(default_telemetry_dir(p["md"]), "recall_events.jsonl")
    if not os.path.exists(path):
        return []
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


def _assert_lean_untripped(sid, out, events_before, p):
    """The prompt was answered from the lean index by lean's own embedder, once, and the
    session's breaker is where it was."""
    sess = W._SESSIONS[sid]
    assert "zebra_deploy_runbook" in out
    assert sess.tripped == "" and sess.fails == 0 and sess.last_path == "warm"
    hb = _heartbeat(sid)
    assert hb["high"]["engaged"] is False and hb["accept"] is True
    assert _ASK in _Lean.asked, "lean's embedder was never asked: this was not a lean recall"
    assert len(_events(p)) == events_before + 1, "the prompt logged one recall event, not two or none"


# --------------------------------------------------------------------------- #
# Gap 1: the daemon is gone (its idle exit, a crash, a kill) when the prompt arrives
# --------------------------------------------------------------------------- #
def test_the_first_prompt_after_the_daemon_goes_is_served_lean(high):
    with _daemon():
        _engage(high)
    assert D.ping() is None  # gone, as after HIPPO_EMBED_DAEMON_IDLE_S without a call
    before = len(_events(high))
    asked = len(_Model.made[0].queries)
    out = _serve_warm("s3", "p3", _ASK)
    _assert_lean_untripped("s3", out, before, high)
    assert len(_Model.made[0].queries) == asked  # no attempt was made on the high index


def test_a_daemon_that_is_still_starting_serves_lean(high):
    """No daemon yet on a session's first prompt: nothing waits for the spawn or the load."""
    t0 = time.monotonic()
    out = _serve_warm("s1", "p1", _ASK)
    assert "zebra_deploy_runbook" in out and _ASK in _Lean.asked
    assert W._SESSIONS["s1"].tripped == "" and _heartbeat("s1")["high"]["engaged"] is False
    assert time.monotonic() - t0 < W.COLD_BUDGET_MS / 1000.0


# --------------------------------------------------------------------------- #
# Gap 2: the daemon is there and slow
# --------------------------------------------------------------------------- #
def test_a_slow_daemon_serves_that_prompt_lean_inside_the_budget_and_trips_nothing(high, monkeypatch):
    monkeypatch.setattr(D, "QUERY_WAIT_S", 0.25, raising=False)
    with _daemon():
        _engage(high)
        _Model.hold = True
        before = len(_events(high))
        t0 = time.monotonic()
        out = _serve_warm("s3", "p3", _ASK)
        took = time.monotonic() - t0
        _Model.release.set()
        _assert_lean_untripped("s3", out, before, high)
        assert took < W.SERVE_BUDGET_MS / 1000.0, f"the prompt waited {took:.2f} s on the daemon"
        assert W._SESSIONS["s3"].last_ms < W.SERVE_BUDGET_MS


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")  # the handler dies on purpose
def test_a_daemon_killed_mid_call_serves_that_prompt_lean(high):
    with _daemon():
        _engage(high)
        _Model.die = True
        before = len(_events(high))
        out = _serve_warm("s3", "p3", _ASK)
        _assert_lean_untripped("s3", out, before, high)
        _Model.die = False


# --------------------------------------------------------------------------- #
# Gap 3: a build pass is one request, so the model cuts the slices it cuts in-process
# --------------------------------------------------------------------------- #
def test_a_build_pass_reaches_the_model_as_one_request_in_order(high):
    texts = [f"memory {i} about the zebra deploy lane" for i in range(70)]
    with _daemon():
        emb = D.DaemonEmbedder(_SPACE, _DIM)
        rows = np.stack(list(emb.embed(texts)))
        assert _Model.doc_calls == [70], "the pass was cut into several requests"
        assert _Model.made[0].docs == texts
        assert np.array_equal(rows, np.stack([_Model.made[0]._vec(t) for t in texts]))


# --------------------------------------------------------------------------- #
# Gap 4: a deadline crosses the socket
# --------------------------------------------------------------------------- #
def _query(path, text, **more):
    return D.request(path, dict({"op": "query", "space": _SPACE, "texts": [text]}, **more), 10.0)


def test_a_queued_query_past_its_deadline_is_dropped_unrun(high):
    with _daemon() as srv:
        _Model.hold = True
        first = threading.Thread(target=_query, args=(srv.path, "the pass that is running"))
        first.start()
        assert _Model.entered.wait(10)
        threading.Timer(0.4, _Model.release.set).start()
        hdr, _ = _query(srv.path, "nobody waits this long", wait_ms=50)
        first.join(10)
        # refused at once (a pass is ahead and 50 ms cannot cover it) or dropped when its turn
        # came too late: either way it never reached the model
        assert hdr.get("ok") is False and hdr.get("reason") in ("busy", "expired")
        assert _Model.made[0].queries == ["the pass that is running"]

