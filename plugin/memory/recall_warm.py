"""HOT-6: warm recall, served by the session's own MCP server (opt-in).

A spawned recall hook pays an interpreter start, the imports and the embedding-model load on
every prompt. The session's MCP server already holds all three. After the opt-in
(``hippo setup --warm`` adds a UserPromptSubmit ``mcp_tool`` hook to the user's settings),
the harness calls this server's ``recall_hook`` tool with the prompt, the session id, the
prompt id and the cwd, and the tool answers with the same ``hookSpecificOutput`` line the
spawned hook prints (PLATFORM.md §2: an ``mcp_tool`` hook's plain text is dropped). The body
is the spawned hook's own (``recall_hook.run_recall``), so the two paths cannot drift.

The command hook stays installed: it is the fallback, and it decides. Hooks for one event run
in parallel, so the two paths settle each prompt through files in
``${CLAUDE_PLUGIN_DATA}/warm/`` (machine-local, never inside a repo), named by the harness's
own ids:

  ``<session>.server.json``   this server's heartbeat: pid, plugin version, index schema,
                              whether it takes warm calls (``accept``) and its state
                              (``idle`` / ``waiting`` / ``serving``). Replaced atomically.
  ``<session>.<prompt>.claim`` ONE per prompt, created with O_EXCL. The command hook creates
                              it with its decision: ``warm`` only for a heartbeat from a live
                              pid on the hook's own plugin version that accepts and is not
                              mid-serve, else ``spawn``. This server creates it (``spawn``)
                              only when it is not fit to serve, or when it gave up waiting.
                              Whoever arrives second reads it and obeys.
  ``<session>.lost``          the hook found a ``warm`` claim no server ever read (a server
                              that died with its pid reused, or a call the harness never
                              made): the hook spawns until this server answers again.

This server injects only on a ``warm`` claim and the hook spawns only on a ``spawn`` one;
each prompt has one claim, so exactly one path injects, in either arrival order, on the
first prompt of a session, and across a server restart (the new pid writes its own
heartbeat). Everything else degrades to the spawn path: no prompt id, a plugin-version or
index-schema mismatch (a long-lived session keeps its old server after an update), no
usable index, a server busy with another tool call, or a tripped breaker: one served call
over ``SERVE_BUDGET_MS`` (``COLD_BUDGET_MS`` for the process's first, which loads the model),
``FAIL_TRIP`` calls that raised, or ``GIVEUP_TRIP`` waits the hook never answered. The rare
one-prompt gap (the hook committed a prompt here, then this server could not serve it) is
counted as ``failed``.

Read-only: a served call reads the index on disk through an in-process cache that reloads
whenever the manifest or matrix changes (a stat on every call) and never builds one
(``recall_tiers.INDEX_LOADER``), so it writes no corpus or index file. The telemetry the
spawned hook writes (recall ledger, episode buffer, daily rollup) is written here too, keyed
by the hook's session id, after the answer goes out (``defer``/``drain``).
"""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from typing import Callable, Dict, Iterator, List, Optional, Tuple

WARM_DIRNAME = "warm"
CLAIM_WAIT_S = 1.0  # how long a fit server waits for the command hook's claim
CLAIM_POLL_S = 0.003
SERVE_BUDGET_MS = 1500.0  # one served call over this trips the session's breaker
COLD_BUDGET_MS = 4000.0  # the process's first served call also loads the embedding model
FAIL_TRIP = 2  # served calls that raised before the breaker trips
GIVEUP_TRIP = 2  # consecutive waits the command hook never answered before it trips
SWEEP_EVERY_S = 3600.0
STALE_CLAIM_S = 600.0
STALE_STATE_S = 86400.0

# The command hook's bash applies the same rule (hooks/_resolve_py.sh, hippo_warm_route).
_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")


def safe_id(value) -> Optional[str]:
    """A harness id usable in a file name — letters, digits, ``_`` and ``-``, at most 128
    characters — else None. No dots, so ``<session>.<prompt>`` splits one way only."""
    if isinstance(value, str) and _ID_RE.fullmatch(value):
        return value
    return None


def warm_dir(create: bool = False) -> Optional[str]:
    """``${CLAUDE_PLUGIN_DATA}/warm`` when it exists (or ``create`` made it), else None."""
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if not data:
        return None
    path = os.path.join(data, WARM_DIRNAME)
    if create:
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            return None
    return path if os.path.isdir(path) else None


def heartbeat_path(d: str, sid: str) -> str:
    return os.path.join(d, f"{sid}.server.json")


def claim_path(d: str, sid: str, prompt_id: str) -> str:
    return os.path.join(d, f"{sid}.{prompt_id}.claim")


def lost_path(d: str, sid: str) -> str:
    return os.path.join(d, f"{sid}.lost")


_RUNNING: Dict[str, str] = {}


def running_version() -> str:
    """The plugin version of the code THIS process loaded (its own plugin.json, found from
    this file — not the env, which a long-lived session can leave pointing elsewhere)."""
    if "v" not in _RUNNING:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        try:
            with open(os.path.join(root, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
                _RUNNING["v"] = str(json.load(fh).get("version") or "")
        except Exception:
            _RUNNING["v"] = ""
    return _RUNNING["v"]


_INSTALLED: Dict[str, object] = {"key": None, "versions": []}


def installed_mismatch() -> Optional[str]:
    """Why this server must not serve because Claude Code installed a NEWER hippo than the
    code this process loaded — the long-lived-session skew (FMT-3's registry reader, re-read
    only when the file changes) — else None. No registry, no hippo entry in it, or a server
    ahead of the install (a ``--plugin-dir`` dev load) is not a mismatch: the command hook's
    own version check still catches a hook and a server that disagree."""
    try:
        from .doctor_checks_platform import _semver, installed_plugins_path, installed_versions

        path = installed_plugins_path()
        try:
            st = os.stat(path)
        except OSError:
            return None
        key = (path, st.st_mtime_ns, st.st_size)
        if _INSTALLED["key"] != key:
            _INSTALLED["key"] = key
            _INSTALLED["versions"] = installed_versions(path)
        versions = list(_INSTALLED["versions"] or [])
        mine = running_version()
        if versions and mine and mine not in versions:
            newest = max(versions, key=lambda v: _semver(v) or ())
            if (_semver(mine) or ()) < (_semver(newest) or ()):
                return f"hippo v{newest} is installed but this server runs v{mine}; restart the session"
    except Exception:
        return None
    return None


# --------------------------------------------------------------------------- #
# The read-only index source
# --------------------------------------------------------------------------- #
class _IndexCache:
    """``index_dir -> LoadedIndex``, keyed on the manifest's and matrix's stat signature, so
    every call notices a rebuild (or a vanished index) and nothing is ever built here."""

    def __init__(self) -> None:
        self._rows: Dict[str, Tuple[tuple, object]] = {}

    @staticmethod
    def _sig(index_dir: str) -> Optional[tuple]:
        from .build_index import _DENSE_NAME, _MANIFEST_NAME, dense_disabled

        try:
            m = os.stat(os.path.join(index_dir, _MANIFEST_NAME))
        except OSError:
            return None
        try:
            d = os.stat(os.path.join(index_dir, _DENSE_NAME))
            dense = (d.st_mtime_ns, d.st_size, d.st_ino)
        except OSError:
            dense = None
        return (m.st_mtime_ns, m.st_size, m.st_ino, dense, dense_disabled())

    def load(self, index_dir: str):
        from .build_index import load_index

        sig = self._sig(index_dir)
        if sig is None:
            self._rows.pop(index_dir, None)
            return None
        hit = self._rows.get(index_dir)
        if hit is not None and hit[0] == sig:
            return hit[1]
        loaded = load_index(index_dir)
        self._rows[index_dir] = (sig, loaded)
        return loaded


_CACHE = _IndexCache()


@contextmanager
def read_only_index() -> Iterator[None]:
    """Inside this block, recall reads indexes through the cache and never builds one."""
    from .recall_tiers import INDEX_LOADER

    token = INDEX_LOADER.set(_CACHE.load)
    try:
        yield
    finally:
        INDEX_LOADER.reset(token)


# --------------------------------------------------------------------------- #
# Where the session's corpus is — resolved exactly as the command hook does
# --------------------------------------------------------------------------- #
def _project_dir(cwd) -> str:
    """The dir the command hook ``cd``s into: ``CLAUDE_PROJECT_DIR``, else the git toplevel
    of the hook's cwd, else that cwd, else this process's own."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return env
    if isinstance(cwd, str) and os.path.isabs(cwd) and os.path.isdir(cwd):
        from .provenance_env import git_root

        return git_root(cwd) or cwd
    return os.getcwd()


def _corpus_present(project: str) -> bool:
    """The command hook's bail-out test (``hippo_corpus_present``): a corpus here, or in the
    main tree of a linked worktree. Where it fails, the hook bails without a claim, so this
    server must not wait for one."""
    if os.path.isdir(os.path.join(project, ".claude", "memory")):
        return True
    if os.path.isfile(os.path.join(project, ".git")):
        try:
            from .provenance_env import git_root, main_worktree_root

            main = main_worktree_root(git_root(project) or project)
            return bool(main) and os.path.isdir(os.path.join(main, ".claude", "memory"))
        except Exception:
            return False
    return False


@contextmanager
def _launch_dir(project: str) -> Iterator[None]:
    """Resolve the corpus from ``project`` when the harness gave this server no
    ``CLAUDE_PROJECT_DIR`` (it normally does, so this is a no-op)."""
    if os.environ.get("CLAUDE_PROJECT_DIR"):
        yield
        return
    os.environ["CLAUDE_PROJECT_DIR"] = project
    try:
        yield
    finally:
        os.environ.pop("CLAUDE_PROJECT_DIR", None)


def _corpus_dirs(project: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        from .provenance import resolve_dirs

        with _launch_dir(project):
            return resolve_dirs()
    except Exception:
        return None, None


def _unfit(project: str) -> str:
    """Why this server must not serve this session's prompts right now, or ""."""
    mismatch = installed_mismatch()
    if mismatch:
        return mismatch
    memory_dir, _repo = _corpus_dirs(project)
    if not memory_dir or not os.path.isdir(memory_dir):
        return "no corpus resolved"
    from .build_index import default_index_dir

    if _CACHE.load(default_index_dir(memory_dir)) is None:
        return "no usable index (missing, or written by another hippo version)"
    return ""


# --------------------------------------------------------------------------- #
# Per-session state, the heartbeat and the claims
# --------------------------------------------------------------------------- #
class _Session:
    def __init__(self) -> None:
        self.tripped = ""  # why the breaker tripped; sticky for this process
        self.unfit = ""  # why the last call could not serve; re-checked every call
        self.fails = 0
        self.giveups = 0
        self.served = 0
        self.last_path = ""
        self.last_ms: Optional[float] = None
        self.prompt = ""


_SESSIONS: Dict[str, _Session] = {}
_STATE = {"busy": False, "last_sweep": 0.0, "served": False}
_DEFERRED: List[Callable[[], None]] = []


def defer(fn: Callable[[], None]) -> None:
    """Queue work for after the answer is written (``mcp_server.serve`` drains it)."""
    _DEFERRED.append(fn)


def drain() -> None:
    """Run the queued after-answer work. Never raises."""
    while _DEFERRED:
        fn = _DEFERRED.pop(0)
        try:
            fn()
        except Exception:
            pass


def _accepts(sess: _Session) -> bool:
    return not sess.tripped and not sess.unfit


def _publish(d: str, sid: str, sess: _Session, *, state: str, accept: bool) -> None:
    """Replace this session's heartbeat. The keys the hook reads come first, on one line.
    Best effort: an unwritten heartbeat leaves the previous one, never a torn one."""
    from .atomic import write_json_atomic
    from .build_index import SCHEMA_VERSION

    doc = {
        "pid": os.getpid(),
        "version": running_version(),
        "accept": bool(accept and not _STATE["busy"]),
        "state": state,
        "prompt": sess.prompt,
        "schema": SCHEMA_VERSION,
        "tripped": sess.tripped,
        "unfit": sess.unfit,
        "fails": sess.fails,
        "served": sess.served,
        "last_path": sess.last_path,
        "last_ms": round(sess.last_ms, 1) if sess.last_ms is not None else None,
        "updated": round(time.time(), 3),
    }
    try:
        write_json_atomic(heartbeat_path(d, sid), doc, indent=None)
    except Exception:
        pass


def _read_claim(path: str) -> Optional[str]:
    """The claim's verdict, "" while it is being written, None when there is none."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except FileNotFoundError:
        return None
    except OSError:
        return ""


def _create_claim(path: str, verdict: str) -> bool:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except OSError:
        return False
    try:
        os.write(fd, verdict.encode("ascii"))
    finally:
        os.close(fd)
    return True


def _read_settled(path: str, wait_s: float = 0.2) -> str:
    """Read a claim another process just created, giving its write a moment to land."""
    deadline = time.monotonic() + wait_s
    while True:
        got = _read_claim(path)
        if got or got is None or time.monotonic() >= deadline:
            return got or ""
        time.sleep(0.002)


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _note(project: str, action: str) -> None:
    """Count one ``hook:user_prompt:<action>`` use under the spawned hook's own gates (the
    corpus exists and is trusted). Never raises."""
    try:
        from . import trust
        from .telemetry import default_telemetry_dir
        from .telemetry_rollup import record_usage

        memory_dir, repo_root = _corpus_dirs(project)
        if not memory_dir or not os.path.isdir(memory_dir):
            return
        gate = trust.gate_repo_root(memory_dir, repo_root)
        if gate is not None and not trust.is_trusted(gate):
            return
        record_usage(default_telemetry_dir(memory_dir), surface="hook", verb="user_prompt", action=action)
    except Exception:
        pass


def _sweep(d: str, now: Optional[float] = None) -> None:
    """Delete stale handshake files, at most once an hour per process: claims older than
    ten minutes, heartbeats and lost-markers a day old (never this process's own)."""
    now = time.time() if now is None else now
    if now - _STATE["last_sweep"] < SWEEP_EVERY_S:
        return
    _STATE["last_sweep"] = now
    try:
        names = os.listdir(d)
    except OSError:
        return
    for name in names:
        path = os.path.join(d, name)
        try:
            age = now - os.stat(path).st_mtime
        except OSError:
            continue
        sid = name.split(".", 1)[0]
        if name.endswith(".claim") or ".tmp." in name:
            stale = age > STALE_CLAIM_S
        elif name.endswith((".server.json", ".lost")):
            stale = age > STALE_STATE_S and sid not in _SESSIONS
        else:
            continue
        if stale:
            _unlink(path)


def mark_busy(busy: bool) -> None:
    """While this server runs another tool call, its heartbeats stop accepting: a prompt
    arriving then would queue behind that call, so the hook spawns instead."""
    if _STATE["busy"] == busy:
        return
    _STATE["busy"] = busy
    if not _SESSIONS:
        return  # never served a warm call: nothing to tell
    d = warm_dir()
    if d is None:
        return
    for sid, sess in list(_SESSIONS.items()):
        if os.path.exists(heartbeat_path(d, sid)):
            _publish(d, sid, sess, state="idle", accept=_accepts(sess))


# --------------------------------------------------------------------------- #
# The tool
# --------------------------------------------------------------------------- #
def serve(args: dict) -> str:
    """The ``recall_hook`` MCP tool: the hook envelope as text when this server serves the
    prompt, ``{}`` when the command hook's spawn does (or nothing should inject)."""
    t_call = time.time()
    sid = safe_id(args.get("session_id"))
    prompt_id = safe_id(args.get("prompt_id"))
    if not sid or not prompt_id:
        return "{}"  # the hook needs both ids to hand a prompt over, so it spawns
    project = _project_dir(args.get("cwd"))
    if not _corpus_present(project):
        return "{}"  # the hook bails here too: nothing recalls, nothing waits
    d = warm_dir(create=True)
    if d is None:
        return "{}"
    sess = _SESSIONS.setdefault(sid, _Session())
    sess.prompt = prompt_id
    _unlink(lost_path(d, sid))  # this server is answering again
    defer(lambda: _sweep(d))
    cp = claim_path(d, sid, prompt_id)

    sess.unfit = "" if sess.tripped else _unfit(project)
    if not _accepts(sess):
        _publish(d, sid, sess, state="idle", accept=False)
        if _create_claim(cp, "spawn"):
            sess.last_path = "spawn"
            return "{}"  # the hook, whenever it comes, reads "spawn" and spawns
        got = _read_settled(cp)
        _unlink(cp)
        if got == "warm":  # committed here before the hook could see why not
            return _failed(d, sid, sess, project)
        sess.last_path = "spawn"
        return "{}"

    got = _read_claim(cp)
    if not got:
        _publish(d, sid, sess, state="waiting", accept=True)
        deadline = time.monotonic() + CLAIM_WAIT_S
        while not got and time.monotonic() < deadline:
            time.sleep(CLAIM_POLL_S)
            got = _read_claim(cp)
        if not got:
            if _create_claim(cp, "spawn"):  # kept: a late hook must still read it
                sess.giveups += 1
                if sess.giveups >= GIVEUP_TRIP:
                    sess.tripped = "the command hook stopped answering"
                sess.last_path = ""
                _publish(d, sid, sess, state="idle", accept=_accepts(sess))
                return "{}"
            got = _read_settled(cp)
    sess.giveups = 0
    try:
        t_claim = os.stat(cp).st_mtime
    except OSError:
        t_claim = t_call
    _unlink(cp)  # read; no one needs it again
    if got != "warm":
        sess.last_path = "spawn"
        _publish(d, sid, sess, state="idle", accept=_accepts(sess))
        return "{}"
    return _serve_warm(d, sid, sess, project, args, min(t_call, t_claim))


def _failed(d: str, sid: str, sess: _Session, project: str) -> str:
    sess.last_path = "failed"
    defer(lambda: _note(project, "failed"))
    _publish(d, sid, sess, state="idle", accept=_accepts(sess))
    return "{}"


def _serve_warm(d: str, sid: str, sess: _Session, project: str, args: dict, t_ref: float) -> str:
    from .recall_hook import hook_envelope, run_recall

    _publish(d, sid, sess, state="serving", accept=True)
    prompt = args.get("prompt")
    raw_query = prompt.strip() if isinstance(prompt, str) else ""
    try:
        with read_only_index(), _launch_dir(project):
            out = run_recall(
                raw_query,
                session_id=sid,
                hook=True,
                path="warm",
                wall_ms=lambda: (time.time() - t_ref) * 1000.0,
                defer=defer,
            )
    except Exception:
        sess.fails += 1
        if sess.fails >= FAIL_TRIP:
            sess.tripped = f"{sess.fails} served recalls failed"
        return _failed(d, sid, sess, project)
    elapsed = (time.time() - t_ref) * 1000.0
    budget = SERVE_BUDGET_MS if _STATE["served"] else COLD_BUDGET_MS
    _STATE["served"] = True
    sess.served += 1
    sess.last_ms = elapsed
    sess.last_path = "warm"
    if elapsed > budget:
        sess.tripped = f"a served recall took {elapsed:.0f} ms (budget {budget:.0f} ms)"
    sess.unfit = "" if sess.tripped else _unfit(project)  # published for the next prompt
    _publish(d, sid, sess, state="idle", accept=_accepts(sess))
    return hook_envelope(out) if out else "{}"


# --------------------------------------------------------------------------- #
# Read-only views for `hippo setup --warm --status` and doctor
# --------------------------------------------------------------------------- #
def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except Exception:
        return False


def server_states(d: Optional[str] = None) -> List[dict]:
    """Every session heartbeat on this machine, newest first, each with ``session`` and
    ``alive`` added. Read-only; never raises."""
    d = d or warm_dir()
    if not d:
        return []
    out: List[dict] = []
    try:
        names = os.listdir(d)
    except OSError:
        return []
    for name in names:
        if not name.endswith(".server.json"):
            continue
        try:
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        doc["session"] = name[: -len(".server.json")]
        doc["alive"] = _alive(doc.get("pid"))
        out.append(doc)
    out.sort(key=lambda r: -float(r.get("updated") or 0))
    return out
