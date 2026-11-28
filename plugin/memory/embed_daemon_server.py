"""The shared embed daemon's server: ``embed_daemon``'s other half (EMB-1, EMB-2, HCR-7).

``embed_daemon`` holds the socket's address, the wire format, the clients and ``main``; this
file is the process's work once it is listening: the models it holds, whose turn it is, when
a model is dropped and when the daemon leaves. That module's docstring is the contract for
both. Split at the module-size cap; ``embed_daemon.Server`` still resolves.
"""

from __future__ import annotations

import gc
import os
import select
import socket
import sys
import threading
import time
from typing import Callable, Dict, List, Optional

from . import embed_daemon as D


class _Unavailable(RuntimeError):
    def __init__(self, msg: str, reason: str) -> None:
        super().__init__(msg)
        self.reason = reason


def _live_servers() -> int:
    """Warm servers on the high profile whose process is alive: the heartbeats under this
    data dir that carry a ``high`` record and a live pid. One not rewritten for a day does
    not count (``recall_warm._sweep`` deletes those), so a dead session's heartbeat whose pid
    another program now has keeps the daemon for a day at most, with its model dropped."""
    from .recall_warm import STALE_STATE_S, server_states

    now = time.time()
    return sum(1 for r in server_states()
               if r.get("alive") and isinstance(r.get("high"), dict)
               and now - float(r.get("updated") or 0.0) < STALE_STATE_S)


def _release_mlx_memory() -> None:
    """After the models are dropped: collect, then hand MLX's cached buffers back, on the
    MLX thread. A process that never imported MLX (the tests' fakes) has nothing to give."""
    gc.collect()
    mx = sys.modules.get("mlx.core")
    if mx is None:
        return

    def release() -> None:
        gc.collect()
        (getattr(mx, "clear_cache", None) or mx.metal.clear_cache)()

    try:
        from .embed_mlx import mlx_executor

        mlx_executor().submit(release).result(timeout=60)
    except Exception:
        pass


class Server:
    def __init__(self, path: str, backend: Callable[[str, int, str], object], idle_s: float,
                 rerank_backend: Optional[Callable[[str, str], object]] = None,
                 grace_s: Optional[float] = None, live: Optional[Callable[[], int]] = None) -> None:
        self.path, self.backend, self.idle_s = path, backend, idle_s
        self.rerank_backend = rerank_backend
        self.grace_s = D.grace_seconds() if grace_s is None else grace_s
        self._live = live or _live_servers  # tests pass their own count
        self._last_live = self._live_read = time.monotonic()
        self._loading: set = set()  # models a refused request started loading
        self._used: Dict[str, float] = {}  # space or reranker -> when it was last asked for
        self._failed = 0  # model passes that raised, in a row
        self._broken = False
        self.models: Dict[str, object] = {}
        self.rerankers: Dict[str, object] = {}
        self.loads = 0
        self.rerank_loads = 0
        self._scoring: Dict[str, threading.Lock] = {}  # model -> held while it scores
        self._load_lock = threading.Lock()
        # One document request at a time. The embedder feeds its one MLX thread slice by
        # slice, so a query queues behind at most one slice of ONE build, as it did inside a
        # single session's server. Measured without it: four sessions building at once held a
        # query for up to 4.3 s, past the warm path's 1.5 s budget.
        self._doc_lock = threading.Lock()
        self._lock = threading.Lock()
        # HCR-7: queries take turns too, so one whose caller's wait ran out while it queued
        # is never run, and a query that cannot be finished in time is refused at once.
        self._query_turn = threading.Lock()
        self._queries = 0  # let in and not finished: waiting for the turn, or embedding
        self._pass_s = D._FIRST_PASS_S  # how long one query pass has been taking
        self.stats = {"queries": 0, "busy": 0, "expired": 0, "doc_rows": 0, "doc_abandoned": 0,
                      "unloads": 0}
        self._inflight = 0
        self._last = time.monotonic()
        self._sock: Optional[socket.socket] = None
        self._ino: Optional[int] = None

    def _model(self, space: str):
        from . import embed_mlx as E

        self._used[space] = time.monotonic()
        with self._load_lock:
            hit = self.models.get(space)
            if hit is not None:
                return hit
            parsed = E.parse_space(space)
            if parsed is None:
                raise _Unavailable(f"unknown embedding space {space!r}", "model_mismatch")
            model, dim = parsed
            why = E.unavailable_reason(model)
            if why:
                raise _Unavailable(why, "model_not_warmed")
            emb = self.backend(model, dim, E.snapshot_dir(model))
            self.models[space] = emb
            self.loads += 1
            self._used[space] = time.monotonic()  # its idle clock starts once it is held
            return emb

    def _reranker(self, model: str):
        from . import rerank_mlx as R

        self._used[model] = time.monotonic()
        with self._load_lock:
            hit = self.rerankers.get(model)
            if hit is not None:
                return hit
            if model not in R.RERANK_MODELS or self.rerank_backend is None:
                raise _Unavailable(f"not a high reranker: {model!r}", "error")
            why = R.unavailable_reason(model)
            if why:
                raise _Unavailable(why, "model_not_warmed")
            rr = self.rerank_backend(model, R.snapshot_dir(model))
            self.rerankers[model] = rr
            self._scoring[model] = threading.Lock()
            self.rerank_loads += 1
            self._used[model] = time.monotonic()
            return rr

    def _load_behind(self, key: str, load: Callable[[], object]) -> None:
        """Start a load nobody waits for: a served call was just refused because the model is
        not held. One at a time per model, counted as in flight so no drop runs under it."""
        with self._lock:
            if key in self._loading:
                return
            self._loading.add(key)

        def run() -> None:
            self._busy(1)
            try:
                load()
            except Exception:
                pass  # the next request for it says why
            finally:
                self._busy(-1)
                with self._lock:
                    self._loading.discard(key)

        threading.Thread(target=run, name="hippo-embedd-load", daemon=True).start()

    def _rerank(self, conn: socket.socket, op: str, req: dict) -> None:
        from . import rerank_mlx as R

        model = str(req.get("model") or "")
        try:
            wait = min(max(float(req.get("wait_s") or 0.0), 0.0), D._EXPLICIT_WAIT_S)
        except (TypeError, ValueError):
            wait = 0.0
        if (op == "rerank" and not wait and model not in self.rerankers
                and model in R.RERANK_MODELS and self.rerank_backend is not None):
            # The warm path (a 0.8 s bound) has no room for a load: the fused order stands
            # for this prompt, and the reranker is back for the next.
            self._load_behind(model, lambda: self._reranker(model))
            raise _Unavailable(f"{model} is not loaded here (dropped while idle); loading it now", "busy")
        rr = self._reranker(model)
        if op == "rerank_load":
            D._send(conn, {"ok": True, "pid": os.getpid(), "load_ms": getattr(rr, "load_ms", None),
                         "resident_bytes": getattr(rr, "resident_bytes", None)})
            return
        busy = self._scoring[model]
        if not (busy.acquire(timeout=wait) if wait else busy.acquire(blocking=False)):
            raise _Unavailable(f"{model} is still scoring an earlier call", "busy")
        try:
            scores = [float(x) for x in self._pass(lambda: rr.score(
                str(req.get("query") or ""), [str(d) for d in (req.get("docs") or [])]))]
        finally:
            self._used[model] = time.monotonic()
            busy.release()
        D._send(conn, {"ok": True, "scores": scores})

    def _pass(self, run: Callable[[], list]) -> list:
        """One model pass. ``D._BROKEN_AFTER`` in a row that raise mark the daemon broken, and
        the accept loop ends it; a pass that works starts the count over. A caller that
        stopped waiting (``EmbedAbandoned``) says nothing about the model."""
        from .embed_mlx import EmbedAbandoned

        try:
            out = run()
        except EmbedAbandoned:
            raise
        except Exception as exc:
            with self._lock:
                self._failed += 1
                broken = self._failed >= D._BROKEN_AFTER
            if broken and not self._broken:
                self._broken = True
                sys.stderr.write(f"[hippo-embedd] pid {os.getpid()}: the model failed {D._BROKEN_AFTER} passes "
                                 f"in a row ({type(exc).__name__}: {str(exc)[:200]}); leaving\n")
                sys.stderr.flush()
            raise
        with self._lock:
            self._failed = 0
        return out

    def _busy(self, delta: int) -> None:
        with self._lock:
            self._inflight += delta
            self._last = time.monotonic()

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(D._DOC_S)
            req = D._readline(conn.makefile("rb"))
            op = req.get("op")
            if op == "ping":
                D._send(conn, {"ok": True, "pid": os.getpid(), "code": D.code_id(),
                             "loaded": sorted(self.models), "loads": self.loads,
                             "rerankers": sorted(self.rerankers), "rerank_loads": self.rerank_loads,
                             "stats": dict(self.stats), "pass_ms": round(self._pass_s * 1000.0, 1)})
                return
            if op in ("rerank_load", "rerank"):
                self._busy(1)
                try:
                    self._rerank(conn, op, req)
                finally:
                    self._busy(-1)
                return
            if op not in ("load", "embed", "query"):
                D._send(conn, {"ok": False, "error": f"unknown op {op!r}", "reason": "error"})
                return
            space = str(req.get("space") or "")
            self._busy(1)
            try:
                if op == "query" and req.get("wait_ms") is not None and space not in self.models:
                    from .embed_mlx import parse_space

                    if parse_space(space) is not None:
                        # HCR-7: a served prompt never waits on a load. Its ready() said the
                        # model was here; it was dropped since. Lean for this one, at once.
                        self.stats["busy"] += 1
                        self._load_behind(space, lambda: self._model(space))
                        raise _Unavailable(
                            f"busy: {space} is not loaded here (dropped while idle); loading it now", "busy")
                emb = self._model(space)
                if op == "load":
                    D._send(conn, {"ok": True, "pid": os.getpid(), "load_ms": getattr(emb, "load_ms", None),
                                 "resident_bytes": getattr(emb, "resident_bytes", None)})
                    return
                import numpy as np

                texts = [str(t) for t in (req.get("texts") or [])]
                if op == "query":
                    vecs = self._query(emb, texts, req.get("wait_ms"))
                else:
                    vecs = self._documents(conn, emb, texts)
                    if vecs is None:
                        return  # the session that asked is gone; nobody reads a reply
                mat = (np.stack(vecs) if vecs else np.zeros((0, 1))).astype("<f4")
                D._send(conn, {"ok": True, "n": int(mat.shape[0]), "dim": int(mat.shape[1])}, mat.tobytes())
            finally:
                if space in self.models:
                    self._used[space] = time.monotonic()  # a long pass ends: its clock starts here
                self._busy(-1)
        except _Unavailable as exc:
            _try_send(conn, {"ok": False, "error": str(exc), "reason": exc.reason})
        except Exception as exc:  # one bad request never takes the daemon down
            _try_send(conn, {"ok": False, "error": f"{type(exc).__name__}: {exc}", "reason": "error"})
        finally:
            conn.close()

    def _query(self, emb, texts: List[str], wait_ms) -> list:
        """HCR-7: one query pass at a time, in turn. ``wait_ms`` is how long the caller will
        wait (a served prompt; None waits as long as it takes). Refused at once (``busy``)
        when the passes ahead, at what a pass has been taking, would outlast it; with nothing
        ahead it is always let in, which is how that estimate recovers. Not run
        (``expired``) when the wait ran out while it queued, for the turn or behind a build's
        slice on the MLX thread."""
        from .embed_mlx import _GIVEN_UP, EmbedAbandoned

        try:
            deadline = None if wait_ms is None else time.monotonic() + max(0.0, float(wait_ms)) / 1000.0
        except (TypeError, ValueError):
            deadline = None
        with self._lock:
            ahead = self._queries
            if ahead and deadline is not None and time.monotonic() + (ahead + 1) * self._pass_s > deadline:
                self.stats["busy"] += 1
                raise _Unavailable(f"busy: {ahead} query pass(es) ahead at {self._pass_s * 1000:.0f} ms each", "busy")
            self._queries += 1
        try:
            left = -1 if deadline is None else max(0.0, deadline - time.monotonic())
            if not (self._query_turn.acquire(timeout=left) if left else self._query_turn.acquire(blocking=False)):
                self.stats["expired"] += 1
                raise _Unavailable("expired before it reached the model", "expired")
            gone = threading.Event()
            timer = None
            if deadline is not None:
                timer = threading.Timer(max(0.0, deadline - time.monotonic()), gone.set)
                timer.daemon = True
                timer.start()
            token = _GIVEN_UP.set(gone)  # the embedder drops a slice whose caller stopped waiting
            t0 = time.monotonic()
            try:
                if gone.is_set():
                    raise EmbedAbandoned()
                vecs = self._pass(lambda: list(emb.query_embed(texts)))
            except EmbedAbandoned:
                self.stats["expired"] += 1
                raise _Unavailable("expired before it reached the model", "expired") from None
            finally:
                _GIVEN_UP.reset(token)
                if timer is not None:
                    timer.cancel()
                self._query_turn.release()
            took = time.monotonic() - t0  # a wait behind a build's slice is part of it
            self._pass_s = took if took < self._pass_s else 0.5 * (self._pass_s + took)
            self.stats["queries"] += len(texts)
            return vecs
        finally:
            with self._lock:
                self._queries -= 1

    def _documents(self, conn: socket.socket, emb, texts: List[str]) -> Optional[list]:
        """One build pass (HCR-7: the whole pass, one request). None when the session that
        sent it hung up: nothing is sent after the request, so a readable socket means
        exactly that, and the embedder stops at its next slice instead of finishing a pass
        nobody will read."""
        from .embed_mlx import _GIVEN_UP, EmbedAbandoned

        gone, done = threading.Event(), threading.Event()

        def watch() -> None:
            while not done.wait(0.5):
                try:
                    readable, _, _ = select.select([conn], [], [], 0)
                    if readable and conn.recv(1, socket.MSG_PEEK) == b"":
                        gone.set()
                        return
                except (OSError, ValueError):
                    gone.set()
                    return

        threading.Thread(target=watch, daemon=True).start()
        try:
            while not self._doc_lock.acquire(timeout=0.5):
                if gone.is_set():
                    self.stats["doc_abandoned"] += 1
                    return None
            token = _GIVEN_UP.set(gone)
            try:
                vecs = self._pass(lambda: list(emb.embed(texts)))
            except EmbedAbandoned:
                self.stats["doc_abandoned"] += 1
                return None
            finally:
                _GIVEN_UP.reset(token)
                self._doc_lock.release()
            self.stats["doc_rows"] += len(texts)
            return vecs
        finally:
            done.set()

    def unload(self, idle_s: Optional[float] = None) -> bool:
        """Drop the models this process holds (every one, or with ``idle_s`` those nobody
        asked for in that long) and give the memory back; it keeps listening. Never under a
        request: one in flight holds the model it was handed. True if anything was dropped."""
        with self._lock:
            if self._inflight:
                return False
            now = time.monotonic()  # read under the lock: a request that ended is counted
            gone = [k for k in list(self.models) + list(self.rerankers)
                    if idle_s is None or now - self._used.get(k, 0.0) > idle_s]
            if not gone:
                return False
            for k in gone:
                self.models.pop(k, None)
                self.rerankers.pop(k, None)
            self.stats["unloads"] += len(gone)
        threading.Thread(target=_release_mlx_memory, name="hippo-embedd-release", daemon=True).start()
        return True

    def _idle(self) -> bool:
        """The accept loop's housekeeping, about twice a second. Drops each model nobody has
        asked for in ``idle_s``. True when nobody is left to serve: nothing in flight and no
        live warm server on high for ``grace_s``, or the model keeps failing (``_pass``)."""
        if self._broken:
            return True
        now = time.monotonic()
        with self._lock:
            busy = self._inflight > 0
            quiet = now - self._last
        if not busy:
            self.unload(self.idle_s)
        if now - self._live_read >= min(D._LIVE_EVERY_S, self.grace_s / 2.0):
            self._live_read = now
            try:
                live = self._live() > 0
            except Exception:
                live = True  # heartbeats that cannot be read are not a reason to leave
            if live:
                self._last_live = now
        return not busy and quiet > self.grace_s and now - self._last_live > self.grace_s

    def bind(self) -> None:
        if os.path.exists(self.path):
            os.unlink(self.path)  # stale: we hold the lock, so no live daemon owns it
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            s.bind(self.path)
        finally:
            os.umask(old)
        os.chmod(self.path, 0o600)
        s.listen(64)
        s.settimeout(0.5)
        self._sock, self._ino = s, os.stat(self.path).st_ino

    def serve_forever(self) -> None:
        assert self._sock is not None
        try:
            while not self._idle():
                try:
                    conn, _ = self._sock.accept()
                except socket.timeout:
                    continue
                threading.Thread(target=self._handle, args=(conn,), daemon=True).start()
        finally:
            self.close()

    def close(self) -> None:
        try:
            if self._ino is not None and os.stat(self.path).st_ino == self._ino:
                os.unlink(self.path)  # first, so a new client spawns a successor
        except OSError:
            pass
        if self._sock is not None:
            self._sock.close()


def _try_send(conn: socket.socket, obj: dict) -> None:
    try:
        D._send(conn, obj)
    except OSError:
        pass
