"""HCR-7 on EMB-1's daemon: recall never depends on it.

The owner's sixth ruling (2026-11-26) let a hippo-owned embedding daemon in on four
conditions; the one these tests hold is that a daemon which is absent, slow, busy or killed
mid-call means THAT prompt is served lean, with nothing raised and no breaker trip. They also
hold the two things that keep its vectors and its queue honest: a build pass is one request
(the same token slices as in-process), and a request whose caller stopped waiting is not run.

The daemon here is ``embed_daemon.Server`` on a thread of the test process with a backend the
test controls (hold a pass, be slow, die mid-query), so it runs on Linux CI with no MLX.
"""

from __future__ import annotations

import json
import os
import socket
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
    _FakeLean, _bag, _fresh_warm_state, _heartbeat, _serve_warm, _wait_fresh, project,
)

_SPACE = "Qwen/Qwen3-Embedding-4B"
_DIM = E.HIGH_MODELS[_SPACE]["native_dim"]


def _until(cond, timeout=60.0, what="the condition"):
    end = time.time() + timeout
    while time.time() < end:
        got = cond()
        if got:
            return got
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


class _Backend:
    """The MLX embedder's shape, with the test's hands on it. ``gate`` holds a query pass
    open; ``order`` is ``slow <s>`` or ``die`` (the handler thread ends without a reply, which
    is what a killed daemon looks like from the client)."""

    made: list = []
    gate = None
    order = ""

    def __init__(self, model, dim, snap=""):
        self.model_id, self.dim = model, dim
        self.load_ms, self.resident_bytes = 7.0, 4 * 2**30
        self.queries: list = []
        self.doc_calls: list = []
        self.entered = threading.Event()
        _Backend.made.append(self)

    def _vec(self, text):
        return _bag(text, E.HIGH_MODELS[self.model_id]["native_dim"], "qwen")[: self.dim]

    def embed(self, texts):
        self.doc_calls.append(list(texts))
        out = []
        for t in texts:  # the real embedder checks between slices whether its caller left
            gone = E._GIVEN_UP.get()
            if gone is not None and gone.is_set():
                raise E.EmbedAbandoned()
            if _Backend.order.startswith("slowdocs"):
                time.sleep(float(_Backend.order.split()[1]))
            out.append(self._vec(t))
        return iter(out)

    def query_embed(self, texts):
        if _Backend.order == "die":
            raise SystemExit  # ends the handler thread: the connection closes with no reply
        if _Backend.order.startswith("slow "):
            time.sleep(float(_Backend.order.split()[1]))
        self.entered.set()
        if _Backend.gate is not None:
            _Backend.gate.wait(120)
        self.queries.extend(texts)
        return iter([self._vec(t) for t in texts])


@pytest.fixture
def daemon(tmp_path, monkeypatch):
    """This machine's embed daemon, on a thread: ``embed_daemon.ensure`` finds it answering,
    so nothing is spawned. Yields the ``Server``."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    monkeypatch.delenv(D.ENABLE_ENV, raising=False)
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)  # CI's hermetic lane exports it
    monkeypatch.setattr(D, "SPAWN_ARGS", ["--fake"])
    monkeypatch.setenv(D.IDLE_ENV, "20")  # a fake daemon the hippo-high thread revives late does not linger
    monkeypatch.setattr(E, "unavailable_reason", lambda model: None)
    for allow in (True, False):
        B._MODEL_CACHE[(B.DEFAULT_MODEL, allow)] = _FakeLean()
    _Backend.made.clear()
    _Backend.gate, _Backend.order = None, ""
    E._LOADED.clear()
    H._reset_for_tests()
    path = D.socket_path()
    D._private_dir(os.path.dirname(path))
    srv = D.Server(path, _Backend, idle_s=600.0)
    srv.bind()
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    H._reset_for_tests()
    E._LOADED.clear()
    if _Backend.gate is not None:
        _Backend.gate.set()
    _stop(srv, thread)
    info = D.ping(path, timeout=0.5)  # a daemon the hippo-high thread started after a fault
    if info and info.get("pid") != os.getpid():
        try:
            os.kill(info["pid"], 9)
        except OSError:
            pass
    try:
        os.unlink(path)
    except OSError:
        pass


def _stop(srv, thread=None):
    """End the thread daemon the way it ends itself: idle."""
    srv.idle_s, srv._last = 0.0, 0.0
    if thread is not None:
        thread.join(30)


def _client():
    return D.DaemonEmbedder(_SPACE, _DIM)


@contextmanager
def _held_pass(srv):
    """One query pass in the daemon that the test holds open (another session's)."""
    _Backend.gate = threading.Event()
    other = threading.Thread(target=lambda: D.request(
        srv.path, {"op": "query", "space": _SPACE, "texts": ["the pass that is running"]}, 60.0))
    other.start()
    _until(lambda: _Backend.made and _Backend.made[0].entered.is_set(), what="the held pass to start")
    try:
        yield
    finally:
        _Backend.gate.set()
        other.join(60)
        _Backend.gate = None


# --------------------------------------------------------------------------- #
# The daemon: turns, waits, refusals, one-request builds
# --------------------------------------------------------------------------- #
def test_a_build_pass_is_one_request_so_the_slices_are_the_in_process_ones(daemon):
    texts = [f"memory {i} about the zebra canary lane" for i in range(70)]  # past the old 32-text chunk
    client = _client()
    rows = np.stack(list(client.embed(texts)))
    backend = _Backend.made[0]
    assert backend.doc_calls == [texts]  # one call, the whole pass, in order
    assert rows.dtype == np.float32 and np.array_equal(rows, np.stack([backend._vec(t) for t in texts]))
    assert list(client.embed([])) == [] and D.ping(daemon.path)["stats"]["doc_rows"] == 70


def test_a_build_whose_session_hung_up_stops_instead_of_finishing(daemon):
    _client()  # loads the model
    _Backend.order = "slowdocs 0.2"
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.connect(daemon.path)
    D._send(conn, {"op": "embed", "space": _SPACE, "texts": [f"row {i}" for i in range(50)]})
    time.sleep(0.3)
    conn.close()  # the session ended mid-pass
    _until(lambda: D.ping(daemon.path)["stats"]["doc_abandoned"] == 1, what="the daemon to drop the pass")
    assert D.ping(daemon.path)["stats"]["doc_rows"] == 0
    _Backend.order = ""
    assert len(list(_client().embed(["the next build is served"]))) == 1  # the doc turn was given back


def test_a_query_whose_wait_ran_out_in_the_queue_is_not_run(daemon):
    client = _client()
    with _held_pass(daemon):
        daemon._pass_s = 0.001  # passes have been quick, so it is let in; then this one is not
        hdr, _ = D.request(daemon.path, {"op": "query", "space": _SPACE, "texts": ["nobody waits this long"],
                                         "wait_ms": 80}, 30.0)
        assert hdr["ok"] is False and hdr["reason"] == "expired"
    assert len(list(client.query_embed(["the next prompt"]))) == 1
    assert _Backend.made[0].queries == ["the pass that is running", "the next prompt"]
    assert D.ping(daemon.path)["stats"]["expired"] == 1


def test_a_query_that_cannot_make_its_wait_is_refused_at_once(daemon):
    client = _client()
    with _held_pass(daemon):
        daemon._pass_s = 5.0  # a pass has been taking five seconds
        t0 = time.monotonic()
        with D.attempt(2.0) as tried:
            with pytest.raises(B.DenseUnavailable, match="busy"):
                list(client.query_embed(["one pass ahead, two seconds to wait"]))
            calls: list = []
            real = D.request
            D.request = lambda *a, **kw: calls.append(a) or real(*a, **kw)
            try:
                with pytest.raises(B.DenseUnavailable):  # one fault per prompt: a later tier does not ask
                    list(client.query_embed(["a later tier of the same prompt"]))
            finally:
                D.request = real
        assert tried.busy and "busy" in tried.fault and calls == [] and time.monotonic() - t0 < 1.5
        assert client.alive is True  # refused, not gone
    with D.attempt(5.0) as tried:  # nothing ahead: let in whatever the estimate says
        assert len(list(client.query_embed(["the queue is empty"]))) == 1
    assert not tried.fault and daemon._pass_s < 5.0 and D.ping(daemon.path)["stats"]["busy"] == 1


def test_ready_says_no_for_a_daemon_that_is_gone_or_has_no_model(daemon):
    client = _client()
    assert client.ready() is True
    daemon.models.clear()  # a fresh daemon another session started: up, without this model
    assert client.ready() is False and client.alive is False and E.loaded(_SPACE) is None
    client.revive()
    assert client.ready() is True
    _stop(daemon)
    _until(lambda: D.ping(daemon.path, timeout=0.3) is None, what="the daemon to leave")
    assert client.ready() is False and client.alive is False


def test_outside_a_served_prompt_a_query_waits_as_before(daemon):
    client = _client()
    _Backend.order = "slow 1.0"  # past QUERY_WAIT_S: a bench or the floor sweep still gets its vector
    assert np.array_equal(list(client.query_embed(["zebra canary"]))[0], _Backend.made[0]._vec("zebra canary"))


# --------------------------------------------------------------------------- #
# The served path: lean on any fault, no breaker trip
# --------------------------------------------------------------------------- #
def _events(p):
    from memory.telemetry import default_telemetry_dir

    path = os.path.join(default_telemetry_dir(p["md"]), "recall_events.jsonl")
    if not os.path.exists(path):
        return []
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


def _serve_high(p, monkeypatch):
    """A session on high whose daemon holds the model and whose twin is level, proved by one
    served prompt that engaged it."""
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")
    B.build_index(p["md"], p["idx"])
    assert "zebra_deploy_runbook" in _serve_warm("s1", "p1")  # lean while the model loads
    _wait_fresh(p)
    _until(lambda: H.status().get("status") == "ready", what="the loader")
    assert "zebra_deploy_runbook" in _serve_warm("s2", "p2", "zebra canary lane deploy steps")
    hb = _heartbeat("s2")["high"]
    assert hb["engaged"] is True and hb["daemon_pid"] == os.getpid()


def _lean_untripped(sid, out):
    sess = W._SESSIONS[sid]
    assert "zebra_deploy_runbook" in out
    assert sess.tripped == "" and sess.fails == 0 and sess.last_path == "warm"
    assert _heartbeat(sid)["high"]["engaged"] is False and _heartbeat(sid)["accept"] is True


def test_the_first_prompt_after_the_daemon_went_is_lean_not_bm25_over_high(daemon, project, monkeypatch):
    _serve_high(project, monkeypatch)
    _stop(daemon)  # its idle exit: nobody has noticed yet, the client still reads alive
    _until(lambda: D.ping(daemon.path, timeout=0.3) is None, what="the daemon to leave")
    assert E.loaded(_SPACE) is not None
    before = len(_events(project))
    out = _serve_warm("s3", "p3", "zebra canary lane deploy steps")
    _lean_untripped("s3", out)
    events = _events(project)
    assert len(events) == before + 1 and events[-1].get("backend") != "bm25"  # lean's hybrid answer
    assert H._ST.faults == 0  # found before the attempt: nothing was waited for
    _until(lambda: (D.ping(daemon.path) or {}).get("loaded"), what="the hippo-high thread to revive it")


def test_a_slow_daemon_serves_that_prompt_lean_without_tripping_the_breaker(daemon, project, monkeypatch):
    _serve_high(project, monkeypatch)
    monkeypatch.setattr(D, "QUERY_WAIT_S", 0.25)
    _Backend.order = "slow 3"
    before = len(_events(project))
    out = _serve_warm("s3", "p3", "zebra canary lane deploy steps")
    _lean_untripped("s3", out)
    assert H._ST.faults == 1 and _heartbeat("s3")["high"]["faults"] == 1
    events = _events(project)
    assert len(events) == before + 1  # the discarded high attempt logged nothing
    assert events[-1].get("backend") != "bm25"  # not BM25 over the high index
    asked: list = []  # held: the next prompt does not ask the daemon for a vector
    real = D.request
    monkeypatch.setattr(D, "request", lambda *a, **kw: asked.append(a[1].get("op")) or real(*a, **kw))
    out = _serve_warm("s4", "p4", "zebra canary lane deploy steps")
    assert "query" not in asked and H._ST.faults == 1
    monkeypatch.setattr(D, "request", real)
    _lean_untripped("s4", out)
    assert "served lean because the embed daemon did not answer" in H.doctor_meaning("dense x", "high")[0]


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")  # the handler dies on purpose
def test_a_daemon_that_dies_mid_query_serves_that_prompt_lean_and_high_resumes(daemon, project, monkeypatch):
    _serve_high(project, monkeypatch)
    monkeypatch.setattr(H, "FAULT_HOLD_S", 0.0)
    _Backend.order = "die"
    out = _serve_warm("s3", "p3", "zebra canary lane deploy steps")
    _lean_untripped("s3", out)
    assert H._ST.faults == 1 and "closed the connection" in H._ST.fault
    _Backend.order = ""
    _until(lambda: E.loaded(_SPACE) is not None and H.status().get("status") == "ready", what="the revive")
    assert "zebra_deploy_runbook" in _serve_warm("s4", "p4", "zebra canary lane deploy steps")
    assert _heartbeat("s4")["high"]["engaged"] is True and W._SESSIONS["s4"].tripped == ""


def test_a_busy_daemon_serves_that_prompt_lean_at_once_and_holds_nothing(daemon, project, monkeypatch):
    _serve_high(project, monkeypatch)
    with _held_pass(daemon):
        daemon._pass_s = 5.0
        before = len(_events(project))
        out = _serve_warm("s3", "p3", "zebra canary lane deploy steps")
        _lean_untripped("s3", out)
        assert H._ST.busy == 1 and H._ST.faults == 0 and H._ST.hold_until <= time.monotonic()
        assert len(_events(project)) == before + 1
    assert "zebra_deploy_runbook" in _serve_warm("s4", "p4", "zebra canary lane deploy steps")
    assert _heartbeat("s4")["high"]["engaged"] is True  # the very next prompt is high again


def test_time_lost_to_the_daemon_is_not_charged_to_the_breaker(daemon, project, monkeypatch):
    _serve_high(project, monkeypatch)
    monkeypatch.setattr(D, "QUERY_WAIT_S", 1.2)
    monkeypatch.setattr(W, "SERVE_BUDGET_MS", 1000.0)  # the daemon wait alone is over it
    monkeypatch.setattr(W, "HIGH_RESERVE_MS", -5000.0)
    _Backend.order = "slow 6"
    out = _serve_warm("s3", "p3", "zebra canary lane deploy steps")
    _lean_untripped("s3", out)
    assert W._SESSIONS["s3"].last_ms > 1000.0


def test_a_prompt_with_no_budget_left_for_the_daemon_is_lean_at_once(daemon, project, monkeypatch):
    _serve_high(project, monkeypatch)
    monkeypatch.setattr(W, "HIGH_RESERVE_MS", W.SERVE_BUDGET_MS)  # nothing left to wait with
    asked = len(_Backend.made[0].queries)
    out = _serve_warm("s3", "p3", "zebra canary lane deploy steps")
    _lean_untripped("s3", out)
    assert H._ST.faults == 1 and len(_Backend.made[0].queries) == asked


def test_a_fault_holds_high_off_a_little_then_longer_and_a_served_prompt_resets_it():
    H._reset_for_tests()
    H.note_fault("timed out")
    first = H._ST.hold_until - time.monotonic()
    H.note_fault("timed out")
    H.note_fault("timed out")
    assert 3.0 < first <= H.FAULT_HOLD_S and H._ST.hold_s == 4 * H.FAULT_HOLD_S and H._ST.faults == 3
    for _ in range(12):
        H.note_fault("timed out")
    assert H._ST.hold_s == H.FAULT_HOLD_MAX_S  # capped
    held = H._ST.hold_until
    H.note_fault("busy: 6 ahead", busy=True)  # refused at once: no wait was paid, nothing is held
    assert H._ST.hold_until == held and H._ST.busy == 1 and H._ST.faults == 15
    H._ST.engaged = True
    H.note_served()
    H.note_fault("timed out")
    assert H._ST.hold_s == H.FAULT_HOLD_S  # short again after a prompt that went through
    H._reset_for_tests()


def test_the_benches_keep_their_models_in_process():
    """Every evidence file before HCR-7 was read on the in-process embedder; a bench says so
    itself when it wants the daemon."""
    import subprocess
    import sys

    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(D.__file__))))
    env = {k: v for k, v in os.environ.items() if k != D.ENABLE_ENV}
    out = subprocess.run([sys.executable, "-c", "import os, bench; print(os.environ['HIPPO_EMBED_DAEMON'])"],
                         env=env, capture_output=True, text=True, cwd=repo)
    assert out.stdout.strip() == "0", out.stderr
