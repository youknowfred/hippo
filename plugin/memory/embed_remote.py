"""HCR-7: the client of the machine's one MLX process (``embed_daemon``).

Before this, every session's warm server on the high profile loaded its own copy of the
embedder: 4.1 GB each, never shared (``mx.load`` copies the weights into private Metal
buffers), so ten sessions held 41 GB. Now the model lives in ONE per-user daemon and each
process that needs it holds this client instead: a few file descriptors and no MLX import.

Where it plugs in. ``embed_mlx.BACKEND`` and ``rerank_mlx.BACKEND`` are the seams
(``(model, dim, snapshot) -> embedder`` and ``(model, snapshot) -> reranker``). While they
hold their in-process defaults and ``enabled()`` is true, ``embed_mlx.high_embedder`` and
``rerank_mlx.reranker`` hand out ``RemoteEmbedder`` / ``RemoteReranker``, which have the same
shape. ``build_index`` and recall are unchanged. ``enabled()`` is ``HIPPO_EMBED_DAEMON=1`` or
``"embed_daemon": true`` in the plugin's ``model.json``; it is off by default (see
``DEFAULT_ON``), and ``HIPPO_EMBED_DAEMON=0`` keeps the models in the calling process
whatever the file says (the benches set it; the byte-identity check needs both).

The owner's four conditions (2026-11-26, sixth ruling), as built:

* **hippo spawns it and reaps it.** ``ensure`` starts ``python -m memory.embed_daemon`` the
  first time a process on high needs a model, detached, with no install step and no launchd
  entry. The daemon exits by itself when no warm server is left (``embed_daemon``).
* **A 0600 unix socket, no port.** ``paths`` names it under ``${CLAUDE_PLUGIN_DATA}/embedd/``
  by embedding space, pinned weights revision and plugin version, so two plugin versions
  never share a space.
* **Recall never depends on it.** Every call here is bounded and fails as an exception the
  dense lane and the rerank seam already absorb. A served prompt goes further: a query embed
  that fails or runs late marks the prompt's ``attempt``, and ``recall_warm`` then discards
  the high attempt whole and serves that prompt lean. The in-process embedder is never the
  fallback, so a daemon fault cannot bring back 4 GB per session.
* **high only, and doctor names its state** (``daemons``; ``recall_warm_high.doctor_meaning``).

This module imports only the standard library at load time (numpy when a vector comes back),
and never MLX.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Dict, Iterator, List, NamedTuple, Optional, Tuple

ENV = "HIPPO_EMBED_DAEMON"  # 1: the daemon. 0: the models load in the calling process
PRESET_KEY = "embed_daemon"  # the same switch in ${CLAUDE_PLUGIN_DATA}/model.json (true / false)
# Off until the owner turns it on. planning/HCR-7.evidence.2026-11-27.json: one model copy for
# ten servers, byte-identical vectors, lean on every fault with no breaker trip, one daemon
# from simultaneous starts all held; the roadmap's stamp "each server under 0.2 GB" did not
# (a warm server holds 0.38 to 0.41 GB with the daemon, and 0.31 GB on lean with no daemon).
DEFAULT_ON = False
DIRNAME = "embedd"

def _env_seconds(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name) or default))
    except ValueError:
        return default


# One served prompt's wait for its query vector. A build's document slice (at most 1,024
# padded tokens, about 0.76 s on the 4B) may be ahead of it on the daemon's one MLX thread,
# or other sessions' queries (one forward pass each); past this the prompt serves lean.
# recall_warm keeps HIGH_RESERVE_MS of its budget back for what follows. The env override is
# the bench's (it measures the wait it ships with against others).
QUERY_WAIT_S = _env_seconds("HIPPO_EMBED_DAEMON_QUERY_WAIT_S", 0.75)
HELLO_WAIT_S = 0.15  # the served call's "is it there and loaded" check
START_WAIT_S = 20.0  # spawn -> the socket answers (an interpreter start and the imports)
LOAD_WAIT_S = 180.0  # the first load of a model from a cold disk
DOC_WAIT_BASE_S = 120.0  # a build's request: this, plus the next line per text
DOC_WAIT_PER_TEXT_S = 30.0
RERANK_LOAD_WAIT_S = 60.0
MAX_FRAME = 1 << 28  # a request or response header; a build pass of whole memories is a few MB
_FIRST_BACKOFF_S = 5.0
_MAX_BACKOFF_S = 600.0
_LOG_KEEP_BYTES = 512 * 1024


class DaemonError(RuntimeError):
    """The daemon could not serve this call (absent, starting, slow, gone, or it said no)."""


def enabled() -> bool:
    """True when this process's high models are the daemon's. Needs unix sockets."""
    if not hasattr(socket, "AF_UNIX"):
        return False
    raw = (os.environ.get(ENV) or "").strip().lower()
    if raw in ("0", "false", "off", "no"):
        return False
    if raw in ("1", "true", "on", "yes"):
        return True
    try:
        from .retrieval_profile import _read_preset  # the shared model.json; never raises

        saved = _read_preset().get(PRESET_KEY)
    except Exception:
        saved = None
    return saved if isinstance(saved, bool) else DEFAULT_ON


# --------------------------------------------------------------------------- #
# Where the daemon for a space lives
# --------------------------------------------------------------------------- #
class Paths(NamedTuple):
    dir: str
    sock: Optional[str]  # None: no usable socket path on this machine (then: lean)
    lock: str
    meta: str
    log: str


def base_dir() -> str:
    """``${CLAUDE_PLUGIN_DATA}/embedd``, else the platform cache's (as the weights do)."""
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if data:
        return os.path.join(data, DIRNAME)
    from .build_index import platform_cache_dir

    return os.path.join(platform_cache_dir(), DIRNAME)


def _weights_revision(space: str) -> str:
    from .embed_mlx import HIGH_MODELS, parse_space

    parsed = parse_space(space)
    return HIGH_MODELS[parsed[0]]["revision"] if parsed else ""


def daemon_key(space: str, base: Optional[str] = None) -> str:
    """16 hex characters naming one daemon: the embedding space, its pinned weights revision,
    the plugin version this code is, and the data dir it serves."""
    from .recall_warm import running_version

    base = os.path.realpath(base or base_dir())
    raw = "|".join((space, _weights_revision(space), running_version(), base))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _sun_path_max() -> int:
    return 103 if sys.platform == "darwin" else 107  # sockaddr_un.sun_path, less its NUL


def _private_tmp_dir() -> Optional[str]:
    """A 0700 dir of this user's under the temp dir, for a socket whose home path is too long
    for ``sockaddr_un``. None unless it is a real directory, owned by this user, closed to
    everyone else."""
    path = os.path.join(tempfile.gettempdir(), f"hippo-embedd-{os.getuid()}")
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        return None
    return path


_PATHS: Dict[Tuple[str, str], Paths] = {}


def paths(space: str) -> Paths:
    base = base_dir()
    hit = _PATHS.get((space, base))
    if hit is not None:
        return hit
    key = daemon_key(space, base)
    sock: Optional[str] = os.path.join(base, key + ".sock")
    if len(os.fsencode(sock)) > _sun_path_max():
        tmp = _private_tmp_dir()
        sock = os.path.join(tmp, key + ".sock") if tmp else None
        if sock is not None and len(os.fsencode(sock)) > _sun_path_max():
            sock = None
    out = Paths(base, sock, os.path.join(base, key + ".lock"),
                os.path.join(base, key + ".json"), os.path.join(base, key + ".log"))
    _PATHS[(space, base)] = out
    return out


# --------------------------------------------------------------------------- #
# The wire: one request per connection
# --------------------------------------------------------------------------- #
# A frame is a 4-byte big-endian length and that many bytes of JSON. A response header that
# carries ``nbytes`` is followed by that many bytes of little-endian float32.
def send_frame(sock: socket.socket, header: dict, payload: bytes = b"") -> None:
    raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
    sock.sendall(struct.pack(">I", len(raw)) + raw)
    if payload:
        sock.sendall(payload)


def recv_exact(sock: socket.socket, n: int, end: Optional[float] = None) -> bytes:
    chunks: List[bytes] = []
    while n > 0:
        if end is not None:
            left = end - time.monotonic()
            if left <= 0:
                raise socket.timeout("timed out")
            sock.settimeout(left)
        got = sock.recv(min(n, 1 << 20))
        if not got:
            raise ConnectionError("the connection closed mid-frame")
        chunks.append(got)
        n -= len(got)
    return b"".join(chunks)


def recv_frame(sock: socket.socket, end: Optional[float] = None) -> dict:
    (size,) = struct.unpack(">I", recv_exact(sock, 4, end))
    if size > MAX_FRAME:
        raise ConnectionError("frame too large")
    doc = json.loads(recv_exact(sock, size, end).decode("utf-8"))
    if not isinstance(doc, dict):
        raise ConnectionError("frame is not an object")
    return doc


def call(sock_path: Optional[str], header: dict, timeout: float) -> Tuple[dict, bytes]:
    """One request, one answer, inside ``timeout`` seconds end to end. Raises ``DaemonError``
    when the daemon answers with an error, and the socket's own errors otherwise."""
    if not sock_path:
        raise DaemonError("no usable socket path on this machine")
    end = time.monotonic() + timeout
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        conn.settimeout(max(0.001, timeout))
        conn.connect(sock_path)
        send_frame(conn, header)
        resp = recv_frame(conn, end)
        if not resp.get("ok"):
            raise DaemonError(str(resp.get("error") or "the daemon refused the request"))
        size = int(resp.get("nbytes") or 0)
        return resp, (recv_exact(conn, size, end) if size else b"")
    finally:
        conn.close()


def hello(space: str, *, want: bool = False, timeout: float = HELLO_WAIT_S) -> Optional[dict]:
    """The daemon's state, or None when it does not answer in time. ``want`` asks it to start
    loading the embedder if it is not loaded (the answer does not wait for the load). Never
    spawns, never raises."""
    try:
        resp, _ = call(paths(space).sock, {"op": "hello", "space": space, "want": bool(want)}, timeout)
        return resp
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Starting it
# --------------------------------------------------------------------------- #
_SPAWN_LOCK = threading.Lock()
_CHILDREN: List[Tuple[str, subprocess.Popen]] = []  # (daemon key, the process this one started)
_BACKOFF: Dict[str, Tuple[float, float, float]] = {}  # key -> (not before, next delay, spawned at)


def _spawn(space: str) -> subprocess.Popen:
    """Start the daemon for ``space``, detached from this process's session and stdio, with
    this interpreter and this plugin's code. Its stderr goes to a small log beside the socket."""
    p = paths(space)
    os.makedirs(p.dir, mode=0o700, exist_ok=True)
    try:
        if os.path.getsize(p.log) > _LOG_KEEP_BYTES:
            os.replace(p.log, p.log + ".1")
    except OSError:
        pass
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # holds memory/
    env = dict(os.environ)
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    with open(p.log, "ab") as log:
        return subprocess.Popen(
            [sys.executable, "-m", "memory.embed_daemon", "--space", space],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
            start_new_session=True, close_fds=True, cwd=p.dir, env=env,
        )


# Tests replace this with a launcher that fakes MLX: (space) -> Popen or None.
SPAWN: Callable[[str], Optional[subprocess.Popen]] = _spawn


def _start(space: str) -> None:
    """Spawn, unless a start was tried a moment ago: a daemon that dies on start is retried
    at 5 s, 10 s, 20 s ... 10 min, never once per prompt. One that lived five minutes and
    then went away (killed, a crash) is restarted at once. A start this process already made
    that is still coming up is that start: the caller waits for it."""
    key = paths(space).meta
    now = time.monotonic()
    with _SPAWN_LOCK:
        for item in list(_CHILDREN):  # reap the ones that exited (a start another one won)
            if item[1].poll() is not None:
                _CHILDREN.remove(item)
        not_before, delay, spawned = _BACKOFF.get(key, (0.0, _FIRST_BACKOFF_S, -1e9))
        if now - spawned < START_WAIT_S and any(k == key for k, _child in _CHILDREN):
            return  # still coming up
        if now < not_before:
            raise DaemonError(f"the embedding daemon is not running (next start in {not_before - now:.0f} s)")
        if now - spawned > 300.0:
            delay = _FIRST_BACKOFF_S
        _BACKOFF[key] = (now + delay, min(delay * 2, _MAX_BACKOFF_S), now)
        child = SPAWN(space)
        if child is not None:
            _CHILDREN.append((key, child))


def ensure(space: str, *, load: bool = False, load_if_started: bool = False,
           wait_s: float = LOAD_WAIT_S) -> dict:
    """The daemon's ``hello`` once it answers, starting it first when it does not. ``load``
    also asks for the embedder and waits up to ``wait_s`` for it; ``load_if_started`` does
    that only when this call had to start the daemon (a warm server whose daemon went away
    wants the model back before its next prompt). Raises ``DaemonError``. Called off the
    serving path (a loader thread, a build, a bench)."""
    if not paths(space).sock:
        raise DaemonError("no usable socket path on this machine")
    info = hello(space, want=load, timeout=1.0)
    if info is None:
        _start(space)
        load = load or load_if_started
        end = time.monotonic() + START_WAIT_S
        while info is None and time.monotonic() < end:
            time.sleep(0.05)
            info = hello(space, want=load, timeout=1.0)
        if info is None:
            raise DaemonError(f"the embedding daemon did not start (see {paths(space).log})")
    end = time.monotonic() + wait_s
    while load and info.get("state") != "loaded":
        if info.get("state") == "failed":
            raise DaemonError(str(info.get("reason") or "model load failed"))
        if time.monotonic() >= end:
            raise DaemonError("the embedding daemon is still loading the model")
        time.sleep(0.05)
        info = hello(space, want=True, timeout=1.0)
        if info is None:
            raise DaemonError("the embedding daemon stopped answering while loading")
    return info


# --------------------------------------------------------------------------- #
# One served prompt's high attempt
# --------------------------------------------------------------------------- #
class Attempt:
    """What ``recall_warm`` hands a served call: when its daemon waits must end, and whether
    one of them failed (``busy``: the daemon said at once that it could not answer in time,
    so nothing was waited for). A mutable object in a ContextVar, so the mark made on
    ``run_bounded``'s worker thread is read back by the caller."""

    __slots__ = ("deadline", "fault", "busy")

    def __init__(self, budget_s: float) -> None:
        self.deadline = time.monotonic() + max(0.0, budget_s)
        self.fault = ""
        self.busy = False


_ATTEMPT: "ContextVar[Optional[Attempt]]" = ContextVar("hippo_embed_attempt", default=None)


@contextmanager
def attempt(budget_s: float) -> Iterator[Attempt]:
    """Inside this block a query embed that the daemon cannot answer in time marks the
    returned ``Attempt`` instead of quietly leaving the dense lane empty: without the mark a
    daemon fault would serve BM25 over the high index, which is not lean."""
    mark = Attempt(budget_s)
    token = _ATTEMPT.set(mark)
    try:
        yield mark
    finally:
        _ATTEMPT.reset(token)


def _unavailable(why: str):
    from .build_index import DenseUnavailable

    return DenseUnavailable(f"embedding daemon: {why}", "dense_error")


# --------------------------------------------------------------------------- #
# The embedder and the reranker, as the seams expect them
# --------------------------------------------------------------------------- #
class RemoteEmbedder:
    """``embed_mlx._MLXEmbedder``'s shape over the socket. Making one touches nothing: the
    warm server's loader thread calls ``ensure`` (off the serving path), a served call asks
    ``ready`` first, and a build just embeds."""

    def __init__(self, model: str, dim: int, snap: str = "") -> None:
        from .embed_mlx import space_id

        self.model_id, self.dim = model, dim
        self.space = space_id(model, dim)
        self.load_ms: Optional[float] = None
        self.resident_bytes: Optional[int] = None

    def _note(self, info: dict) -> dict:
        if info.get("load_ms") is not None:
            self.load_ms = info["load_ms"]
        if info.get("resident_bytes") is not None:
            self.resident_bytes = info["resident_bytes"]
        return info

    def ensure(self, *, load: bool = False, wait_s: float = LOAD_WAIT_S) -> dict:
        return self._note(ensure(self.space, load=load, load_if_started=True, wait_s=wait_s))

    def ready(self) -> bool:
        """The daemon answers and holds the model. Not loaded (just started, or it dropped
        the model while idle) asks it to load and says no: this prompt serves lean, the next
        one finds it loaded."""
        info = hello(self.space, want=True)
        return bool(info) and self._note(info).get("state") == "loaded"

    def _vectors(self, kind: str, texts: List[str], wait: float, deadline_ms: Optional[float],
                 *, may_start: bool = False):
        import numpy as np

        if not texts:
            return np.zeros((0, self.dim), dtype="float32")
        req = {"op": "embed", "space": self.space, "kind": kind, "texts": texts, "deadline_ms": deadline_ms}
        try:
            resp, data = call(paths(self.space).sock, req, wait)
        except (OSError, DaemonError) as exc:
            # Off the serving path (a build, a bench, the floor sweep) nobody else keeps the
            # daemon up: when it is not there, start it and ask once more. A served prompt
            # never does this; it serves lean and its loader thread does the starting.
            if not may_start or isinstance(exc, socket.timeout):
                raise
            self.ensure(load=True)
            resp, data = call(paths(self.space).sock, req, wait)
        n, dim = int(resp.get("n") or 0), int(resp.get("dim") or 0)
        if n != len(texts) or dim != self.dim or len(data) != n * dim * 4:
            raise DaemonError("the daemon's answer does not match the request")
        return np.frombuffer(data, dtype="<f4").reshape(n, dim)

    def embed(self, texts):
        """Documents (an index build's pass), in ONE request, so the daemon cuts the same
        token slices the in-process embedder would and the rows come back the same bytes."""
        texts = list(texts)
        wait = DOC_WAIT_BASE_S + DOC_WAIT_PER_TEXT_S * len(texts)
        try:
            return iter(self._vectors("doc", texts, wait, None, may_start=True))
        except Exception as exc:
            raise _unavailable(f"{type(exc).__name__}: {exc}") from exc

    def query_embed(self, texts):
        texts = list(texts)
        mark = _ATTEMPT.get()
        if mark is not None:
            if mark.fault:  # one fault per prompt: later tiers do not wait again
                raise _unavailable(mark.fault)
            wait = min(QUERY_WAIT_S, mark.deadline - time.monotonic())
        else:  # a bench, the floor sweep: the dense lane's own bound
            from .build_index import DENSE_QUERY_TIMEOUT_SECS

            wait = DENSE_QUERY_TIMEOUT_SECS if DENSE_QUERY_TIMEOUT_SECS > 0 else LOAD_WAIT_S
        try:
            if wait <= 0:
                raise DaemonError("no time left in this prompt's budget")
            return iter(self._vectors("query", texts, wait, wait * 1000.0, may_start=mark is None))
        except Exception as exc:
            why = f"{type(exc).__name__}: {exc}"[:200]
            if mark is not None:
                mark.fault = why
                mark.busy = isinstance(exc, DaemonError) and "busy:" in str(exc)
            raise _unavailable(why) from exc


class RemoteReranker:
    """``rerank_mlx._MLXReranker``'s shape over the socket. Making one asks the daemon to
    load the model (best effort); ``score`` is bounded by the seam's own bound."""

    def __init__(self, model: str, snap: str = "") -> None:
        self.model_id = model
        self.load_ms: Optional[float] = None
        self.resident_bytes: Optional[int] = None
        try:
            self._prime()
        except Exception:
            pass  # score() says why when it matters; the seam keeps the fused order

    @staticmethod
    def _space() -> str:
        from .embed_mlx import resolve_high_space

        return resolve_high_space()

    def _prime(self) -> None:
        space = self._space()
        ensure(space)  # the daemon, not the embedder: a reranker alone does not need 4 GB
        wait_ms = RERANK_LOAD_WAIT_S * 1000.0
        resp, _ = call(paths(space).sock, {"op": "load", "model": self.model_id, "wait_ms": wait_ms},
                       RERANK_LOAD_WAIT_S + 5.0)
        self.load_ms, self.resident_bytes = resp.get("load_ms"), resp.get("resident_bytes")

    def score(self, query: str, docs: List[str]) -> List[float]:
        from .recall_rank import _rerank_timeout_secs
        from .rerank_mlx import RerankUnavailable, bound_secs

        wait = bound_secs(self.model_id, _rerank_timeout_secs())
        mark = _ATTEMPT.get()
        if mark is not None:
            wait = min(wait, mark.deadline - time.monotonic())
        if wait <= 0:
            raise RerankUnavailable("no time left in this prompt's budget")
        req = {"op": "rerank", "model": self.model_id, "query": query, "docs": list(docs),
               "deadline_ms": wait * 1000.0}
        try:
            resp, _ = call(paths(self._space()).sock, req, wait)
        except Exception as exc:
            raise RerankUnavailable(f"embedding daemon: {type(exc).__name__}: {exc}") from exc
        return [float(x) for x in resp.get("scores") or []]


# --------------------------------------------------------------------------- #
# Doctor's view
# --------------------------------------------------------------------------- #
def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except Exception:
        return False


def daemons() -> List[dict]:
    """Every daemon record under ``embedd/`` (one per space and plugin version that ever
    started here), each with ``alive`` and, when it answers, its live ``state``. Read-only;
    never spawns, never raises."""
    out: List[dict] = []
    try:
        names = sorted(os.listdir(base_dir()))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(base_dir(), name), encoding="utf-8") as fh:
                rec = json.load(fh)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        rec["alive"] = _pid_alive(rec.get("pid"))
        if rec["alive"]:
            try:
                live, _ = call(rec.get("socket"), {"op": "hello", "space": rec.get("space")}, 0.5)
                rec.update({k: live.get(k) for k in ("state", "reason", "load_ms", "resident_bytes", "batch")})
            except Exception:
                rec["state"] = "not answering"
        out.append(rec)
    return out


def _mine(space: str) -> Optional[dict]:
    """This space's daemon on this plugin version, asked directly (a daemon whose record
    was never written still answers here)."""
    info = hello(space, timeout=0.5)
    return dict(info, alive=True) if info else None


def describe(space: str) -> str:
    """One clause for doctor: this space's daemon and what it is doing."""
    mine = [r for r in daemons() if r.get("space") == space and r.get("alive")]
    own = _mine(space)
    if own is not None and own.get("pid") not in [r.get("pid") for r in mine]:
        mine.insert(0, own)
    if not mine:
        return "embedding daemon not running (the next warm prompt on high starts it)"
    r = mine[0]
    bit = f"embedding daemon {r.get('state') or 'up'} (pid {r.get('pid')}"
    if r.get("state") == "loaded" and r.get("resident_bytes"):
        bit += f", {r['resident_bytes'] / 2**30:.1f} GiB, the one copy on this machine"
    elif r.get("state") == "idle":
        bit += ", model dropped while idle; the next prompt reloads it"
    elif r.get("state") == "failed" and r.get("reason"):
        bit += f", {r['reason']}"
    if len(mine) > 1:
        bit += f"; {len(mine)} daemons, one per plugin version in use"
    return bit + ")"


def _reset_for_tests() -> None:
    _PATHS.clear()
    _BACKOFF.clear()
    with _SPAWN_LOCK:
        for _key, child in list(_CHILDREN):
            child.poll()
        _CHILDREN.clear()
