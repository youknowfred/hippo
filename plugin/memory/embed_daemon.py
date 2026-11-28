"""HCR-7: one embedder per machine — the process that holds the high profile's MLX models.

``python -m memory.embed_daemon --space <id>``, started by ``embed_remote.ensure`` and by
nothing else (no install step, no launchd entry). It serves one embedding space over one
unix socket (mode 0600, in a 0700 directory, no port) and, on the same MLX thread, the Qwen3
rerankers. See ``embed_remote`` for the client and the owner's four conditions.

* **One per space.** The process takes an exclusive ``flock`` on ``<key>.lock`` before it
  loads anything and holds it for life. Two sessions that start together spawn two
  processes; the one that does not get the lock exits as soon as the other answers.
* **One MLX thread.** Models are the in-process classes (``embed_mlx.BACKEND``,
  ``rerank_mlx.BACKEND``), so every forward pass still runs on ``embed_mlx.mlx_executor``,
  in slices of at most 1,024 padded tokens. A build's document slices and every session's
  queries interleave there at slice granularity: a query waits one slice, across sessions.
* **One query per forward pass, in arrival order.** A query embedded alone is the same
  bytes the in-process embedder produces, so the space's floor, gate and count rule carry
  over unchanged. ``HIPPO_EMBED_DAEMON_BATCH=1`` coalesces instead: the queries that queued
  while a pass ran share the next one. That is faster under a burst and NOT byte-identical
  (measured 2026-11-27 on the 4B: a padded batch moves a vector's raw components by up to
  1.1e-4, cosine to its single-pass self 0.9999998), which is why it is off.
* **A caller that stopped waiting is dropped.** Each query and rerank carries how long its
  caller will wait. One still queued past that, or whose caller closed the socket, is never
  run: the cross-socket form of ``embed_mlx.abandon_on_timeout``. A build whose session
  ended stops at its next slice.
* **A query that cannot be answered in time is refused at once.** The daemon knows how many
  queries are ahead and how long one has been taking; when that is more than the caller
  will wait it says ``busy`` instead of letting the caller wait for nothing. The prompt is
  served lean straight away, and the next one asks again.
* **It reaps itself.** No live warm server on high (``recall_warm.server_states``) and no
  request in flight for ``GRACE_S``: exit. No request for ``IDLE_S``: drop the models and
  keep listening (measured 2026-11-26: the process falls to 166 MB; a reload from a warm
  page cache takes about 0.3 s).

Nothing here is reachable from another user (the socket and its directory are the owner's
alone), and a request can only ask for vectors or scores from the pinned models.
"""

from __future__ import annotations

import argparse
import gc
import os
import select
import signal
import socket
import sys
import threading
import time
from typing import Callable, Dict, List, Optional

from . import embed_remote as remote

BATCH_ENV = "HIPPO_EMBED_DAEMON_BATCH"  # 1: queued queries share a forward pass (not byte-identical)
IDLE_ENV = "HIPPO_EMBED_DAEMON_IDLE_S"
GRACE_ENV = "HIPPO_EMBED_DAEMON_GRACE_S"
IDLE_S = 1800.0  # no request for this long: drop the models (the process stays, small)
GRACE_S = 60.0  # no live warm server on high and nothing in flight for this long: exit
LOCK_WAIT_S = 3.0  # how long a start waits for a daemon that holds the lock to answer or go
LOAD_RETRY_S = 60.0  # a model load that raised is not retried before this
_FIRST_PASS_S = 0.1  # a query pass, until one has been timed (measured: 60 ms idle, 154 ms loaded)
_REQUEST_READ_S = 10.0


def _env_seconds(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name) or default))
    except ValueError:
        return default


class _Slot:
    """One model, loaded on demand on a thread of its own, so ``hello`` answers through a
    load. States: idle (not held), loading, loaded, failed (with the reason, retried later)."""

    def __init__(self, load: Callable[[], object]) -> None:
        self._load, self._lock = load, threading.Lock()
        self.obj: Optional[object] = None
        self.state, self.reason = "idle", ""
        self.load_ms: Optional[float] = None
        self.resident_bytes: Optional[int] = None
        self._event: Optional[threading.Event] = None
        self._failed_at = 0.0

    def kick(self) -> threading.Event:
        """Start the load unless it is loaded, loading, or failed a moment ago. The event is
        set when the attempt this call saw is over."""
        with self._lock:
            if self._event is not None:
                return self._event
            done = threading.Event()
            if self.obj is not None or (
                    self.state == "failed" and time.monotonic() - self._failed_at < LOAD_RETRY_S):
                done.set()
                return done
            self.state, self.reason, self._event = "loading", "", done
        threading.Thread(target=self._run, name="hippo-embedd-load", daemon=True).start()
        return done

    def _run(self) -> None:
        obj, reason = None, ""
        try:
            obj = self._load()
        except BaseException as exc:
            reason = f"model load failed: {exc}"
        with self._lock:
            done, self._event = self._event, None
            if obj is None:
                self.state, self.reason, self._failed_at = "failed", reason, time.monotonic()
            else:
                self.obj, self.state = obj, "loaded"
                self.load_ms = getattr(obj, "load_ms", None)
                self.resident_bytes = getattr(obj, "resident_bytes", None)
        if done is not None:
            done.set()

    def get(self, wait_s: Optional[float]):
        """The loaded model, loading it first. Raises when it failed or is not loaded in time."""
        self.kick().wait(wait_s)
        with self._lock:
            if self.obj is not None:
                return self.obj
            raise RuntimeError(self.reason or "the model is still loading")

    def drop(self) -> bool:
        with self._lock:
            if self.obj is None:
                return False
            self.obj, self.state = None, "idle"
            return True

    def info(self) -> dict:
        with self._lock:
            return {"state": self.state, "reason": self.reason,
                    "load_ms": round(self.load_ms, 1) if self.load_ms is not None else None,
                    "resident_bytes": self.resident_bytes if self.obj is not None else 0}


class _Query:
    __slots__ = ("texts", "deadline", "gone", "done", "vecs", "error")

    def __init__(self, texts: List[str], deadline: Optional[float], gone: threading.Event) -> None:
        self.texts, self.deadline, self.gone = texts, deadline, gone
        self.done = threading.Event()
        self.vecs = None
        self.error = ""


def _local_embedder(space: str):
    from . import embed_mlx as E

    model, dim = E.parse_space(space)
    why = E.unavailable_reason(model)
    if why:
        raise RuntimeError(why)
    return E.BACKEND(model, dim, E.snapshot_dir(model))


def _local_reranker(model: str):
    from . import rerank_mlx as R

    why = R.unavailable_reason(model)
    if why:
        raise RuntimeError(why)
    return R.BACKEND(model, R.snapshot_dir(model))


def _live_servers() -> int:
    """Warm servers on the high profile whose process is alive (their heartbeats)."""
    from .recall_warm import server_states

    return sum(1 for r in server_states() if r.get("alive") and isinstance(r.get("high"), dict))


def _release_mlx_memory() -> None:
    """After the models are dropped: collect, then hand MLX's cached buffers back. Runs on
    the MLX thread; a process that never imported MLX (the tests' fakes) has nothing to do."""
    gc.collect()
    mx = sys.modules.get("mlx.core")
    if mx is None:
        return
    try:
        (getattr(mx, "clear_cache", None) or mx.metal.clear_cache)()
    except Exception:
        pass


class Daemon:
    """The server. ``start`` takes the lock and binds; ``serve_forever`` runs until it has
    no one left to serve; ``close`` gives the socket and the lock back. Tests pass their own
    loaders (fakes), liveness source and windows."""

    def __init__(self, space: str, *, load_embedder: Optional[Callable[[], object]] = None,
                 load_reranker: Optional[Callable[[str], object]] = None,
                 live: Optional[Callable[[], int]] = None, idle_s: Optional[float] = None,
                 grace_s: Optional[float] = None, batch: Optional[bool] = None,
                 tick_s: float = 1.0) -> None:
        self.space = space
        self.paths = remote.paths(space)
        self._emb = _Slot(load_embedder or (lambda: _local_embedder(space)))
        self._load_reranker = load_reranker or _local_reranker
        self._rerankers: Dict[str, _Slot] = {}
        self._rerank_turn: Dict[str, threading.Lock] = {}
        self._live = live or _live_servers
        self.idle_s = _env_seconds(IDLE_ENV, IDLE_S) if idle_s is None else idle_s
        self.grace_s = _env_seconds(GRACE_ENV, GRACE_S) if grace_s is None else grace_s
        raw = (os.environ.get(BATCH_ENV) or "").strip().lower()
        self.batch = (raw in ("1", "true", "on", "yes")) if batch is None else batch
        self.tick_s = tick_s
        self._lock = threading.Lock()
        self._queue: List[_Query] = []
        self._queued = threading.Condition(self._lock)
        self._watch: Dict[socket.socket, threading.Event] = {}
        self._stop = threading.Event()
        self._inflight = 0
        self._last_used = self._last_live = time.monotonic()
        self._listener: Optional[socket.socket] = None
        self._lockfh = None
        self.started = time.time()
        self.stats = {"queries": 0, "query_passes": 0, "largest_pass": 0, "expired": 0, "busy": 0,
                      "gone": 0, "doc_requests": 0, "doc_rows": 0, "reranks": 0, "unloads": 0}
        self._pass_s = _FIRST_PASS_S  # how long one query pass has been taking (a moving average)
        self._in_pass = 0  # queries in the pass that is running now

    # ----------------------------------------------------------------- #
    # Start: the lock, then the socket
    # ----------------------------------------------------------------- #
    def start(self) -> bool:
        """True when this process now serves ``space``. False when another daemon does (or
        holds the lock and never answers): the caller exits, having loaded nothing."""
        p = self.paths
        if not p.sock:
            return False
        os.makedirs(p.dir, mode=0o700, exist_ok=True)
        try:
            os.chmod(p.dir, 0o700)
        except OSError:
            pass
        import fcntl

        fh = open(p.lock, "a")
        end = time.monotonic() + LOCK_WAIT_S
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                # Held: by a daemon that serves (it answers, so this one is not needed) or by
                # one on its way out (its lock frees in a moment, and then this one serves).
                if remote.hello(self.space, timeout=0.2) is not None or time.monotonic() >= end:
                    fh.close()
                    return False
                time.sleep(0.05)
        self._lockfh = fh
        try:
            os.unlink(p.sock)  # a killed daemon's socket; the lock says no one serves on it
        except FileNotFoundError:
            pass
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        mask = os.umask(0o177)  # the socket is born 0600, never briefly wider
        try:
            listener.bind(p.sock)
        finally:
            os.umask(mask)
        os.chmod(p.sock, 0o600)
        listener.listen(64)
        listener.settimeout(self.tick_s)
        self._listener = listener
        self._record()
        return True

    def _record(self) -> None:
        """``<key>.json``: who serves this socket, for doctor (``embed_remote.daemons``). Best
        effort: without it the daemon serves all the same, and doctor still finds this space's
        daemon by asking its socket."""
        try:
            from .atomic import write_json_atomic
            from .recall_warm import running_version

            write_json_atomic(self.paths.meta, {
                "pid": os.getpid(), "space": self.space, "socket": self.paths.sock,
                "version": running_version(), "started": round(self.started, 3)})
        except Exception:
            pass

    # ----------------------------------------------------------------- #
    # The loop
    # ----------------------------------------------------------------- #
    def serve_forever(self) -> None:
        threading.Thread(target=self._coalesce, name="hippo-embedd-queries", daemon=True).start()
        threading.Thread(target=self._watch_peers, name="hippo-embedd-peers", daemon=True).start()
        last_tick = time.monotonic()
        while not self._stop.is_set():
            try:
                conn, _addr = self._listener.accept()
            except socket.timeout:
                conn = None
            except OSError:
                break
            if conn is not None:
                threading.Thread(target=self._handle, args=(conn,), name="hippo-embedd-conn",
                                 daemon=True).start()
            now = time.monotonic()
            if now - last_tick >= self.tick_s:
                last_tick = now
                if self._tick(now):
                    break

    def _tick(self, now: float) -> bool:
        """Housekeeping. True: nobody is left to serve, exit."""
        with self._lock:
            busy = self._inflight > 0
            if busy:
                self._last_used = now
            idle_for = now - self._last_used
        if not busy and idle_for >= self.idle_s:
            self.unload()
        try:
            live = self._live() > 0
        except Exception:
            live = True  # an unreadable heartbeat dir is not a reason to leave
        if busy or live:
            self._last_live = now
        return now - self._last_live >= self.grace_s

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        """Stop listening, take the socket and the record away, release the lock."""
        self._stop.set()
        with self._queued:
            self._queued.notify_all()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            for path in (self.paths.sock, self.paths.meta):
                try:
                    os.unlink(path)
                except OSError:
                    pass
            self._listener = None
        if self._lockfh is not None:
            self._lockfh.close()
            self._lockfh = None

    def unload(self) -> None:
        """Drop every model this process holds and give the memory back."""
        dropped = self._emb.drop()
        for slot in list(self._rerankers.values()):
            dropped = slot.drop() or dropped
        if not dropped:
            return
        self.stats["unloads"] += 1
        try:
            from .embed_mlx import mlx_executor

            mlx_executor().submit(_release_mlx_memory).result(timeout=30)
        except Exception:
            pass

    # ----------------------------------------------------------------- #
    # Requests
    # ----------------------------------------------------------------- #
    def info(self) -> dict:
        from .recall_warm import running_version

        out = {"ok": True, "pid": os.getpid(), "space": self.space, "version": running_version(),
               "batch": self.batch, "started": round(self.started, 3), "stats": dict(self.stats),
               "rerankers": {m: s.info() for m, s in list(self._rerankers.items())}}
        out.update(self._emb.info())
        return out

    def _handle(self, conn: socket.socket) -> None:
        gone = threading.Event()
        work = False
        try:
            conn.settimeout(_REQUEST_READ_S)
            req = remote.recv_frame(conn)
            op = req.get("op")
            if op in ("hello", "status"):
                if req.get("space") not in (None, self.space):
                    raise ValueError(f"this daemon serves {self.space}")
                if req.get("want"):
                    self._emb.kick()
                    self._touch()
                remote.send_frame(conn, self.info())
                return
            if op == "shutdown":
                remote.send_frame(conn, {"ok": True})
                self._stop.set()
                return
            work = True
            with self._lock:
                self._inflight += 1
                self._last_used = time.monotonic()
                self._watch[conn] = gone
            conn.settimeout(60.0)  # for the answer's send; the wait for the model is ours
            wait_ms = req.get("deadline_ms")
            deadline = time.monotonic() + float(wait_ms) / 1000.0 if wait_ms is not None else None
            if op == "embed":
                header, payload = self._embed(req, deadline, gone)
            elif op == "rerank":
                header, payload = self._rerank(req, deadline, gone), b""
            elif op == "load":
                header, payload = self._load(req), b""
            else:
                raise ValueError(f"unknown op {op!r}")
            if not gone.is_set():
                remote.send_frame(conn, header, payload)
        except Exception as exc:
            try:
                if not gone.is_set():
                    remote.send_frame(conn, {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]})
            except Exception:
                pass
        finally:
            if work:
                with self._lock:
                    self._inflight -= 1
                    self._last_used = time.monotonic()
                    self._watch.pop(conn, None)
            try:
                conn.close()
            except OSError:
                pass

    def _touch(self) -> None:
        with self._lock:
            self._last_used = time.monotonic()

    def _embed(self, req: dict, deadline: Optional[float], gone: threading.Event):
        import numpy as np

        if req.get("space") != self.space:
            raise ValueError(f"this daemon serves {self.space}")
        texts = req.get("texts")
        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            raise ValueError("texts must be a list of strings")
        if req.get("kind") == "query":
            item = _Query(texts, deadline, gone)
            with self._queued:
                # Admission: the passes ahead of this query (one per queued query, or one for
                # all of them when coalescing), the one running, and its own.
                # With nothing ahead it is always let in: that is how the estimate recovers.
                ahead = (1 if self.batch and self._queue else len(self._queue)) + (1 if self._in_pass else 0)
                if ahead and deadline is not None and time.monotonic() + (ahead + 1) * self._pass_s > deadline:
                    self.stats["busy"] += 1
                    raise RuntimeError(f"busy: {ahead} query pass(es) ahead at {self._pass_s * 1000:.0f} ms each")
                self._queue.append(item)
                self._queued.notify()
            self._emb.kick()
            while not item.done.wait(0.05):
                if gone.is_set() or self._stop.is_set():
                    raise RuntimeError("abandoned")
            if item.vecs is None:
                raise RuntimeError(item.error or "not embedded")
            vecs = item.vecs
        else:
            emb = self._emb.get(None)
            self.stats["doc_requests"] += 1
            from .embed_mlx import _GIVEN_UP  # the embedder drops a slice its caller left

            token = _GIVEN_UP.set(gone)
            try:
                vecs = np.stack(list(emb.embed(texts))) if texts else np.zeros((0, 0), dtype="float32")
            finally:
                _GIVEN_UP.reset(token)
            self.stats["doc_rows"] += len(texts)
        vecs = np.ascontiguousarray(vecs, dtype="<f4")
        dim = int(vecs.shape[1]) if vecs.ndim == 2 else 0
        data = vecs.tobytes()
        return {"ok": True, "n": len(texts), "dim": dim, "nbytes": len(data)}, data

    def _coalesce(self) -> None:
        """The one consumer of the query queue: one query per forward pass, or (``batch``)
        everything that queued while the last pass ran, together. A query whose caller
        stopped waiting is never run."""
        import numpy as np

        while not self._stop.is_set():
            with self._queued:
                while not self._queue and not self._stop.is_set():
                    self._queued.wait(0.5)
            if self._stop.is_set():
                break
            try:
                emb = self._emb.get(None)
            except Exception as exc:
                self._fail_queue(str(exc))
                time.sleep(0.05)
                continue
            with self._queued:
                take = len(self._queue) if self.batch else 1
                items, self._queue = self._queue[:take], self._queue[take:]
            now = time.monotonic()
            live: List[_Query] = []
            for item in items:
                if item.gone.is_set():
                    self.stats["gone"] += 1
                    item.error = "abandoned"
                    item.done.set()
                elif item.deadline is not None and now >= item.deadline:
                    self.stats["expired"] += 1
                    item.error = "expired before it reached the model"
                    item.done.set()
                else:
                    live.append(item)
            if not live:
                continue
            flat = [t for item in live for t in item.texts]
            self._in_pass, t0 = len(live), time.monotonic()
            try:
                vecs = np.stack(list(emb.query_embed(flat))) if flat else np.zeros((0, 0), dtype="float32")
            except Exception as exc:
                for item in live:
                    item.error = f"{type(exc).__name__}: {exc}"
                    item.done.set()
                continue
            finally:
                self._in_pass = 0
            took = time.monotonic() - t0  # a wait behind a build's slice is part of it
            self._pass_s = took if took < self._pass_s else 0.5 * (self._pass_s + took)
            self.stats["queries"] += len(flat)
            self.stats["query_passes"] += 1
            self.stats["largest_pass"] = max(self.stats["largest_pass"], len(flat))
            self._touch()
            at = 0
            for item in live:
                item.vecs = vecs[at: at + len(item.texts)]
                at += len(item.texts)
                item.done.set()

    def _fail_queue(self, why: str) -> None:
        with self._queued:
            items, self._queue = self._queue, []
        for item in items:
            item.error = why
            item.done.set()

    def _slot(self, model: str) -> _Slot:
        from .rerank_mlx import RERANK_MODELS

        if model not in RERANK_MODELS:
            raise ValueError(f"not a high reranker: {model!r}")
        with self._lock:
            if model not in self._rerankers:
                self._rerankers[model] = _Slot(lambda: self._load_reranker(model))
                self._rerank_turn[model] = threading.Lock()
            return self._rerankers[model]

    def _rerank(self, req: dict, deadline: Optional[float], gone: threading.Event) -> dict:
        model, query, docs = req.get("model"), req.get("query"), req.get("docs")
        if not isinstance(query, str) or not isinstance(docs, list) or not all(isinstance(d, str) for d in docs):
            raise ValueError("query must be a string and docs a list of strings")
        slot = self._slot(model)

        def left() -> Optional[float]:
            return None if deadline is None else max(0.0, deadline - time.monotonic())

        rr = slot.get(left())
        turn = self._rerank_turn[model]
        if not turn.acquire(timeout=-1 if deadline is None else left()):
            self.stats["expired"] += 1
            raise RuntimeError("expired before it reached the model")
        try:
            if gone.is_set() or (deadline is not None and time.monotonic() >= deadline):
                self.stats["gone" if gone.is_set() else "expired"] += 1
                raise RuntimeError("expired before it reached the model")
            scores = [float(x) for x in rr.score(query, docs)]
        finally:
            turn.release()
        self.stats["reranks"] += 1
        return {"ok": True, "scores": scores}

    def _load(self, req: dict) -> dict:
        """Load a model now and wait up to ``wait_ms`` for it: the embedder, or the reranker
        named by ``model``. The answer is that model's state."""
        slot = self._slot(req["model"]) if req.get("model") else self._emb
        slot.kick().wait(max(0.0, float(req.get("wait_ms") or 0.0)) / 1000.0)
        out = {"ok": True}
        out.update(slot.info())
        return out

    def _watch_peers(self) -> None:
        """A caller that gave up closes its socket. Nothing else is ever sent after the
        request, so a readable socket here means exactly that: mark the request gone."""
        while not self._stop.is_set():
            with self._lock:
                conns = list(self._watch)
            if not conns:
                time.sleep(0.02)
                continue
            try:
                readable, _, _ = select.select(conns, [], [], 0.02)
            except (OSError, ValueError):
                time.sleep(0.005)  # one of them closed under us; the next pass has the new list
                continue
            for conn in readable:
                try:
                    closed = conn.recv(1, socket.MSG_PEEK) == b""
                except (OSError, ValueError):
                    closed = True
                if closed:
                    with self._lock:
                        gone = self._watch.pop(conn, None)
                    if gone is not None:
                        gone.set()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="memory.embed_daemon", description=__doc__.split("\n\n")[0])
    ap.add_argument("--space", required=True, help="the high embedding space to serve")
    args = ap.parse_args(argv)
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")  # numpy's pool beside Metal (HCR-4)
    from .embed_mlx import parse_space

    if parse_space(args.space) is None:
        print(f"not a high embedding space: {args.space!r}", file=sys.stderr)
        return 2
    daemon = Daemon(args.space)
    if not daemon.start():
        return 0  # another daemon serves this space; this process loaded nothing
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_a: daemon.stop())
    try:
        daemon.serve_forever()
    finally:
        daemon.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
