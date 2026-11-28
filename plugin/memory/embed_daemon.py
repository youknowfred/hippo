"""EMB-1: one shared copy of the high profile's MLX embedder per user, not one per session.

HCR-2 loads Qwen3-Embedding inside each session's warm MCP server, so every session that
served one prompt on the high profile held its own copy: 4.1 GB of weights plus MLX's buffer
cache, 4.3-6.7 GB of phys_footprint per server. Measured 2026-11-27 on a 128 GB M4 Max with
26 sessions open: 5 servers had loaded it, 26.3 GB of the servers' 27.6 GB together. The
model is a pure function from text to vector; nothing in it belongs to a session, a corpus or
a worktree. So it moves here: one daemon per (user, plugin data dir, embedder code), and each
session's server swaps only its embedder object for a client of the daemon
(``embed_mlx.BACKEND``). Corpora, indexes, claims and builds stay in the sessions, exactly as
before.

  - **Where.** ``<CLAUDE_PLUGIN_DATA>/embedd/<code>.sock``, the directory 0700 and the
    socket 0600. ``<code>`` hashes this file and ``embed_mlx.py``, so a session still running
    an older hippo after an update gets its own daemon, never vectors from other embedding
    code.
  - **One daemon.** It holds an exclusive ``flock`` on ``<code>.lock`` for its whole life.
    Two spawns that race both start; the one that cannot take the lock exits.
  - **Writes nothing** but its log. Index builds are the sessions' own, under IDX-1 and
    HCR-9's per-twin build lock; the daemon only turns texts into vectors.
  - **Idle exit.** No load, embed or rerank for ``HIPPO_EMBED_DAEMON_IDLE_S`` seconds (default
    900): it unlinks its socket and exits, and the memory goes back to the machine. A ping
    is not activity.
  - **When it dies** (idle exit, crash, kill): a client's next call fails fast, the client
    marks itself not alive, ``embed_mlx.loaded`` reads None and the warm server serves lean,
    its existing degrade. The next served call asks the ``hippo-high`` thread to respawn
    the daemon and reload, off the serving path.
  - **The reranker too** (EMB-2). The warm Qwen3-Reranker (``rerank_mlx``) loads here once,
    not in every server that served an explicit ``recall`` or ran with ``HIPPO_RERANK`` on.
    HCR-4's rule that a reranker still scoring an earlier call answers "busy" at once (the
    seam keeps the fused order) now holds across sessions on the warm path, whose 0.8 s bound
    has no room to wait: the daemon never queues a scoring behind one a caller may already
    have abandoned. An explicit surface (a 15 s bound) waits up to 5 s for its turn instead.
  - ``HIPPO_EMBED_DAEMON=0`` keeps the in-process load of both.
  - **Recall never depends on it** (HCR-7, the owner's sixth ruling). A served prompt asks
    first whether the daemon holds the model (``DaemonEmbedder.ready``, 150 ms); gone, or
    not loaded, that prompt is lean. Its query then waits at most ``QUERY_WAIT_S`` and says
    so in the request (``wait_ms``): the daemon refuses at once a query it cannot finish in
    that time (``busy``) and never runs one whose wait ran out while it queued. A query the
    daemon did not answer marks the prompt's ``attempt``; ``recall_warm`` then throws the
    high attempt away whole and serves the prompt lean, and the time is not charged to the
    session's breaker. Without the mark a fault served BM25 over the high index.
  - **A build's pass is one request** (HCR-7), so the daemon cuts the same token slices the
    in-process embedder would and the rows are the same bytes (a batch changes a vector's
    last bits by itself; measured 2026-11-27). A build whose session ended stops at its
    next slice.

Wire format: one JSON request line; one JSON reply line, and after an ``embed``/``query``
reply, ``n * dim`` little-endian float32s.
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Dict, Iterator, List, Optional, Tuple

ENABLE_ENV = "HIPPO_EMBED_DAEMON"
IDLE_ENV = "HIPPO_EMBED_DAEMON_IDLE_S"
DEFAULT_IDLE_S = 900.0
DIRNAME = "embedd"
_MAX_LINE = 256 * 2**20  # a request is texts: one build pass of whole memories, at most
_CONNECT_S = 2.0
_QUERY_S = 120.0
_DOC_S = 600.0
_DOC_PER_TEXT_S = 30.0  # a build pass's reply: _DOC_S, plus this per text (it may queue behind another build)


def _env_seconds(name: str, default: float) -> float:
    try:
        return max(0.0, float((os.environ.get(name) or "").strip() or default))
    except ValueError:
        return default


# HCR-7: one served prompt's wait for its query vector. A slice of a build (at most 1,024
# padded tokens, about 0.76 s on the 4B) or other sessions' queries may be ahead of it on the
# daemon's one MLX thread; past this the prompt serves lean. recall_warm keeps HIGH_RESERVE_MS
# of its budget back for what follows. The env override is the bench's.
QUERY_WAIT_S = _env_seconds("HIPPO_EMBED_DAEMON_QUERY_WAIT_S", 0.75)
_READY_S = 0.15  # the served call's "is it there, with the model" question
_FIRST_PASS_S = 0.1  # a query pass, until one has been timed (measured: 60 ms idle, 154 ms loaded)
_SPAWN_WAIT_S = 15.0
# How long an explicit surface's scoring waits for another session's to finish. Its bound is
# rerank_mlx.EXPLICIT_TIMEOUT_SECS (15 s) and a 30-doc scoring measured 0.55-0.8 s, so a wait
# this long still lands inside it. The warm path never waits (its 0.8 s bound has no room).
_EXPLICIT_WAIT_S = 5.0
_SUN_PATH_MAX = 103  # macOS sockaddr_un.sun_path is 104 bytes with its NUL


def enabled() -> bool:
    return (os.environ.get(ENABLE_ENV) or "").strip().lower() not in ("0", "off", "false", "no")


def idle_seconds() -> float:
    try:
        v = float((os.environ.get(IDLE_ENV) or "").strip() or DEFAULT_IDLE_S)
    except ValueError:
        return DEFAULT_IDLE_S
    return v if v > 0 else DEFAULT_IDLE_S


def code_id() -> str:
    """Hash of the code that turns text into vectors or scores: this file, ``embed_mlx.py``
    and ``rerank_mlx.py``."""
    h = hashlib.sha256()
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("embed_mlx.py", "embed_daemon.py", "rerank_mlx.py"):
        try:
            with open(os.path.join(here, name), "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(name.encode())
    return h.hexdigest()[:12]


def _private_dir(path: str) -> str:
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def socket_path() -> str:
    """The daemon's socket for this data dir and this code. Under ``$TMPDIR`` (per-user on
    macOS) when the data dir's path is too long for a Unix socket."""
    base = os.environ.get("CLAUDE_PLUGIN_DATA")
    if not base:
        from .build_index import platform_cache_dir

        base = platform_cache_dir()
    path = os.path.join(base, DIRNAME, f"{code_id()}.sock")
    if len(path.encode()) > _SUN_PATH_MAX:
        tag = hashlib.sha256(base.encode()).hexdigest()[:8]
        path = os.path.join(tempfile.gettempdir(), f"hippo-{os.getuid()}-{DIRNAME}", f"{tag}-{code_id()}.sock")
    return path


# --------------------------------------------------------------------------- #
# Wire
# --------------------------------------------------------------------------- #
def _readline(f) -> dict:
    line = f.readline(_MAX_LINE + 1)
    if not line:
        raise ConnectionError("embed daemon closed the connection")
    if len(line) > _MAX_LINE:
        raise ValueError("message too large")
    return json.loads(line)


def _send(sock: socket.socket, obj: dict, payload: bytes = b"") -> None:
    sock.sendall(json.dumps(obj).encode() + b"\n" + payload)


def request(path: str, req: dict, timeout: float = _QUERY_S) -> Tuple[dict, bytes]:
    """One request; the reply header and its raw payload. Raises ``OSError`` (including
    ``ConnectionError``) when there is no daemon to answer."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(min(_CONNECT_S, timeout))
        s.connect(path)
        s.settimeout(timeout)
        _send(s, req)
        f = s.makefile("rb")
        hdr = _readline(f)
        size = int(hdr.get("n") or 0) * int(hdr.get("dim") or 0) * 4 if hdr.get("ok") else 0
        payload = f.read(size) if size else b""
        if len(payload) != size:
            raise ConnectionError("embed daemon reply cut short")
        return hdr, payload
    finally:
        s.close()


def ping(path: Optional[str] = None, timeout: float = _CONNECT_S) -> Optional[dict]:
    try:
        hdr, _ = request(path or socket_path(), {"op": "ping"}, timeout=timeout)
    except (OSError, ValueError):
        return None
    return hdr if hdr.get("ok") else None


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
SPAWN_ARGS: List[str] = []  # tests add ``--fake``


def spawn(path: str) -> None:
    """Start a daemon for ``path``, detached from this session, so it outlives the session
    that started it: the child forks the daemon off into its own session and exits at once,
    and this process reaps it (no zombie, no still-running child left behind)."""
    plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env["PYTHONPATH"] = plugin_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("OPENBLAS_NUM_THREADS", "1")  # HCR-4's pin: BLAS threads beside Metal
    _private_dir(os.path.dirname(path))
    log = open(path[: -len(".sock")] + ".log", "a")  # the daemon's stderr; nothing else writes it
    try:
        subprocess.run(
            [sys.executable, "-m", "memory.embed_daemon", "--socket", path, "--detach", *SPAWN_ARGS],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env,
            cwd=plugin_root, close_fds=True, timeout=_SPAWN_WAIT_S, check=False,
        )
    finally:
        log.close()


def ensure(path: Optional[str] = None, wait_s: float = _SPAWN_WAIT_S) -> dict:
    """The live daemon's ping reply, spawning one first when none answers."""
    path = path or socket_path()
    info = ping(path)
    if info is not None:
        return info
    spawn(path)
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        time.sleep(0.05)
        info = ping(path)
        if info is not None:
            return info
    raise RuntimeError(f"the shared embed daemon did not start (see {path[:-5]}.log)")


# --------------------------------------------------------------------------- #
# HCR-7: one served prompt's high attempt
# --------------------------------------------------------------------------- #
class Attempt:
    """What ``recall_warm`` hands a served call: when its daemon waits must end, and whether
    its query embed went unanswered (``busy``: the daemon said at once that it could not
    answer in time, so nothing was waited for). A mutable object in a ContextVar, so the
    mark made on ``run_bounded``'s worker thread is read back by the caller."""

    __slots__ = ("deadline", "fault", "busy")

    def __init__(self, budget_s: float) -> None:
        self.deadline = time.monotonic() + max(0.0, budget_s)
        self.fault = ""
        self.busy = False


_ATTEMPT: "ContextVar[Optional[Attempt]]" = ContextVar("hippo_embed_attempt", default=None)


@contextmanager
def attempt(budget_s: float) -> Iterator[Attempt]:
    """Inside this block a query embed the daemon does not answer in time marks the returned
    ``Attempt`` instead of quietly leaving the dense lane empty: without the mark a daemon
    fault serves BM25 over the high index, which is not lean."""
    mark = Attempt(budget_s)
    token = _ATTEMPT.set(mark)
    try:
        yield mark
    finally:
        _ATTEMPT.reset(token)


class DaemonEmbedder:
    """``embed_mlx._MLXEmbedder``'s shape, served by the shared daemon. ``alive`` turns
    False when a call finds the daemon gone; ``revive`` brings one back (blocking: it may
    load the model, so only the ``hippo-high`` thread calls it)."""

    def __init__(self, model: str, dim: int, snap: str = "") -> None:
        from .embed_mlx import space_id

        self.model_id, self.dim = model, dim
        self.space = space_id(model, dim)
        self.path = socket_path()
        self.alive = False
        self.load_ms: Optional[float] = None
        self.resident_bytes: Optional[int] = None
        self.daemon_pid: Optional[int] = None
        self.revive()

    def revive(self) -> None:
        from .build_index import DenseUnavailable

        ensure(self.path)
        t0 = time.monotonic()
        hdr, _ = request(self.path, {"op": "load", "space": self.space}, timeout=_DOC_S)
        if not hdr.get("ok"):
            raise DenseUnavailable(str(hdr.get("error") or "load failed"), hdr.get("reason") or "error")
        self.load_ms = hdr.get("load_ms")
        if self.load_ms is None:
            self.load_ms = (time.monotonic() - t0) * 1000.0
        self.resident_bytes = hdr.get("resident_bytes")
        self.daemon_pid = hdr.get("pid")
        self.alive = True

    def ready(self) -> bool:
        """HCR-7: the daemon answers and holds this space's model, asked by a served call
        before it attempts high. No answer in 150 ms, or a daemon without the model (a fresh
        one another session started): not alive, so this prompt is lean and the
        ``hippo-high`` thread brings it back."""
        info = ping(self.path, timeout=_READY_S)
        if info is None or self.space not in (info.get("loaded") or []):
            self.alive = False
            return False
        return True

    def _vectors(self, op: str, texts: List[str], timeout: float, wait_ms: Optional[float] = None):
        import numpy as np

        from .build_index import DenseUnavailable
        from .embed_mlx import _GIVEN_UP, EmbedAbandoned
        from .recall_fallback import MODEL_NOT_WARMED

        given_up = _GIVEN_UP.get()
        if given_up is not None and given_up.is_set():
            raise EmbedAbandoned()
        try:
            req = {"op": op, "space": self.space, "texts": texts}
            if wait_ms is not None:
                req["wait_ms"] = wait_ms
            hdr, payload = request(self.path, req, timeout)
        except (OSError, ValueError) as exc:
            self.alive = False
            raise DenseUnavailable(f"the shared embed daemon is gone ({exc})", MODEL_NOT_WARMED) from exc
        if not hdr.get("ok"):
            raise DenseUnavailable(str(hdr.get("error") or "embed failed"), hdr.get("reason") or "error")
        return np.frombuffer(payload, dtype="<f4").reshape(int(hdr["n"]), int(hdr["dim"]))

    def embed(self, texts):
        """Documents: only an index build embeds them, on the ``hippo-high`` thread, where a
        blocking reload is allowed. A daemon lost mid-build is revived once rather than
        failing the build, which would write the twin back without its rows. HCR-7: the
        whole pass goes in ONE request, so the daemon's embedder cuts the same token slices
        the in-process one would and the rows come back the same bytes."""
        texts = list(texts)
        if not texts:
            return iter([])
        wait = _DOC_S + _DOC_PER_TEXT_S * len(texts)
        try:
            return iter(list(self._vectors("embed", texts, wait)))
        except Exception:
            if self.alive:
                raise
            self.revive()
            return iter(list(self._vectors("embed", texts, wait)))

    def query_embed(self, texts):
        """A served prompt (inside ``attempt``) waits at most ``QUERY_WAIT_S`` and what is
        left of its budget, tells the daemon so, and marks the attempt when no vector comes
        back; one fault per prompt, so a later tier does not wait again. Any other caller
        (a bench, the floor sweep) waits as before."""
        texts = list(texts)
        mark = _ATTEMPT.get()
        if mark is None:
            return iter(list(self._vectors("query", texts, _QUERY_S)))
        from .build_index import DenseUnavailable

        if mark.fault:
            raise DenseUnavailable(mark.fault, "error")
        wait = min(QUERY_WAIT_S, mark.deadline - time.monotonic())
        try:
            if wait <= 0:
                raise DenseUnavailable("no time left in this prompt's budget for the embed daemon", "error")
            return iter(list(self._vectors("query", texts, wait, wait_ms=wait * 1000.0)))
        except Exception as exc:
            mark.fault = f"{type(exc).__name__}: {exc}"[:200]
            mark.busy = getattr(exc, "reason", "") == "busy"
            raise


class DaemonReranker:
    """``rerank_mlx._MLXReranker``'s shape (``score(query, docs)``), served by the shared
    daemon. ``alive`` and ``revive`` as ``DaemonEmbedder``'s."""

    def __init__(self, model: str, snap: str = "") -> None:
        self.model_id = model
        self.path = socket_path()
        self.alive = False
        self.load_ms: Optional[float] = None
        self.resident_bytes: Optional[int] = None
        self.daemon_pid: Optional[int] = None
        self.revive()

    def revive(self) -> None:
        ensure(self.path)
        t0 = time.monotonic()
        hdr, _ = request(self.path, {"op": "rerank_load", "model": self.model_id}, timeout=_DOC_S)
        if not hdr.get("ok"):
            raise RuntimeError(str(hdr.get("error") or "load failed"))
        self.load_ms = hdr.get("load_ms")
        if self.load_ms is None:
            self.load_ms = (time.monotonic() - t0) * 1000.0
        self.resident_bytes = hdr.get("resident_bytes")
        self.daemon_pid = hdr.get("pid")
        self.alive = True

    def score(self, query: str, docs: List[str]) -> List[float]:
        from .rerank_mlx import RerankUnavailable, explicit_surface

        wait = _EXPLICIT_WAIT_S if explicit_surface() else 0.0
        try:
            hdr, _ = request(self.path, {"op": "rerank", "model": self.model_id, "wait_s": wait,
                                         "query": str(query), "docs": [str(d) for d in docs]}, _QUERY_S)
        except (OSError, ValueError) as exc:
            self.alive = False
            raise RerankUnavailable(f"the shared embed daemon is gone ({exc})") from exc
        if not hdr.get("ok"):
            raise RerankUnavailable(str(hdr.get("error") or "rerank failed"))
        return [float(x) for x in hdr.get("scores") or []]


# --------------------------------------------------------------------------- #
# Server
# --------------------------------------------------------------------------- #
class _Unavailable(RuntimeError):
    def __init__(self, msg: str, reason: str) -> None:
        super().__init__(msg)
        self.reason = reason


class Server:
    def __init__(self, path: str, backend: Callable[[str, int, str], object], idle_s: float,
                 rerank_backend: Optional[Callable[[str, str], object]] = None) -> None:
        self.path, self.backend, self.idle_s = path, backend, idle_s
        self.rerank_backend = rerank_backend
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
        self._pass_s = _FIRST_PASS_S  # how long one query pass has been taking
        self.stats = {"queries": 0, "busy": 0, "expired": 0, "doc_rows": 0, "doc_abandoned": 0}
        self._inflight = 0
        self._last = time.monotonic()
        self._sock: Optional[socket.socket] = None
        self._ino: Optional[int] = None

    def _model(self, space: str):
        from . import embed_mlx as E

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
            return emb

    def _reranker(self, model: str):
        from . import rerank_mlx as R

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
            return rr

    def _rerank(self, conn: socket.socket, op: str, req: dict) -> None:
        model = str(req.get("model") or "")
        rr = self._reranker(model)
        if op == "rerank_load":
            _send(conn, {"ok": True, "pid": os.getpid(), "load_ms": getattr(rr, "load_ms", None),
                         "resident_bytes": getattr(rr, "resident_bytes", None)})
            return
        busy = self._scoring[model]
        try:
            wait = min(max(float(req.get("wait_s") or 0.0), 0.0), _EXPLICIT_WAIT_S)
        except (TypeError, ValueError):
            wait = 0.0
        if not (busy.acquire(timeout=wait) if wait else busy.acquire(blocking=False)):
            raise _Unavailable(f"{model} is still scoring an earlier call", "busy")
        try:
            scores = [float(x) for x in rr.score(str(req.get("query") or ""),
                                                 [str(d) for d in (req.get("docs") or [])])]
        finally:
            busy.release()
        _send(conn, {"ok": True, "scores": scores})

    def _busy(self, delta: int) -> None:
        with self._lock:
            self._inflight += delta
            self._last = time.monotonic()

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(_DOC_S)
            req = _readline(conn.makefile("rb"))
            op = req.get("op")
            if op == "ping":
                _send(conn, {"ok": True, "pid": os.getpid(), "code": code_id(),
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
                _send(conn, {"ok": False, "error": f"unknown op {op!r}", "reason": "error"})
                return
            self._busy(1)
            try:
                emb = self._model(str(req.get("space") or ""))
                if op == "load":
                    _send(conn, {"ok": True, "pid": os.getpid(), "load_ms": getattr(emb, "load_ms", None),
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
                _send(conn, {"ok": True, "n": int(mat.shape[0]), "dim": int(mat.shape[1])}, mat.tobytes())
            finally:
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
                vecs = list(emb.query_embed(texts))
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
                vecs = list(emb.embed(texts))
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

    def _idle(self) -> bool:
        with self._lock:
            return self._inflight == 0 and time.monotonic() - self._last > self.idle_s

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
        _send(conn, obj)
    except OSError:
        pass


def _take_lock(path: str, wait_s: float = 5.0):
    """The daemon's lifetime lock, or None. Waits a little: a predecessor that just went
    idle releases it as it exits."""
    import fcntl

    fh = open(path[: -len(".sock")] + ".lock", "a")  # flock target; nothing is written to it
    end = time.monotonic() + wait_s
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except OSError:
            if time.monotonic() >= end:
                fh.close()
                return None
            time.sleep(0.05)


class _FakeEmbedder:
    """``--fake``: a deterministic stand-in for tests (no MLX). One-hot-ish hash vectors."""

    def __init__(self, model: str, dim: int, snap: str) -> None:
        self.dim, self.load_ms, self.resident_bytes = dim, 1.0, 1234

    def _vec(self, text: str, salt: str):
        import numpy as np

        v = np.zeros(self.dim, dtype="float32")
        for tok in text.split():
            v[int(hashlib.sha256((salt + tok).encode()).hexdigest()[:8], 16) % self.dim] += 1.0
        return v

    def embed(self, texts):
        return iter([self._vec(t, "d") for t in texts])

    def query_embed(self, texts):
        return iter([self._vec(t, "q") for t in texts])


class _FakeReranker:
    """``--fake``: scores a doc by the query words it shares (no MLX)."""

    def __init__(self, model: str, snap: str) -> None:
        self.load_ms, self.resident_bytes = 1.0, 567

    def score(self, query, docs):
        q = set(query.lower().split())
        return [len(q & set(d.lower().split())) / (len(q) or 1) for d in docs]


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="hippo-embed-daemon")
    ap.add_argument("--socket", required=True)
    ap.add_argument("--fake", action="store_true", help="test-only deterministic embedder")
    ap.add_argument("--detach", action="store_true", help="fork into a new session; the parent exits")
    args = ap.parse_args(argv)
    if args.detach:
        if os.fork() > 0:
            os._exit(0)  # only the standard library is loaded here, so forking is safe
        os.setsid()
    _private_dir(os.path.dirname(args.socket))
    lock = _take_lock(args.socket)
    if lock is None:
        return 0  # another daemon owns this socket
    if args.fake:
        from . import embed_mlx as E

        from . import rerank_mlx as R

        E.unavailable_reason = lambda model: None
        R.unavailable_reason = lambda model: None
        backend: Callable = _FakeEmbedder
        rerank_backend: Callable = _FakeReranker
    else:
        from .embed_mlx import _MLXEmbedder
        from .rerank_mlx import _MLXReranker

        backend, rerank_backend = _MLXEmbedder, _MLXReranker
    srv = Server(args.socket, backend, idle_seconds(), rerank_backend)
    srv.bind()
    sys.stderr.write(f"[hippo-embedd] pid {os.getpid()} serving {args.socket}\n")
    sys.stderr.flush()
    try:
        srv.serve_forever()
    finally:
        sys.stderr.write(f"[hippo-embedd] pid {os.getpid()} exiting (idle)\n")
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
