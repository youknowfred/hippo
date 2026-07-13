"""INT-11: the /hippo:bootstrap flow as a kick-off-and-poll engine — venv + model warm.

Bootstrap is the ONE online step in the plugin's lifecycle (venv build + ~130MB embedding-
model download), and it lived only as bash-in-prose in the bootstrap SKILL — unreachable
from surfaces without typed /hippo:* commands. That gap is not cosmetic on the Claude
desktop app: the harness hands EACH surface its own plugin-data dir (a terminal install
gets ``~/.claude/plugins/data/<plugin>-<marketplace>``, a desktop local session gets
``<plugin>-inline``), so a terminal bootstrap's venv/model cache is simply not where a
desktop session's ``CLAUDE_PLUGIN_DATA`` points — without an in-surface bootstrap, desktop
recall would stay BM25-only forever. ``status()`` names a detected sibling-surface install
so that split is legible instead of mystifying.

Shape: a several-minute network download cannot run synchronously inside an MCP tool call,
so ``start()`` spawns a DETACHED worker (``python3 -m memory.bootstrap --worker``, its own
session, output to ``${CLAUDE_PLUGIN_DATA}/bootstrap.log``) and returns immediately;
``status()`` is the poll (sentinel state + a live-pid lock + the log tail). The worker
deliberately runs under the SYSTEM python3, never the venv python — a re-bootstrap must
not rebuild the venv out from under its own running interpreter — and it needs only the
stdlib (venv/deps/warm are subprocess steps), so it works pre-bootstrap by construction,
exactly like the rest of the pre-venv ladder.

The SKILL.md hard rules carry over: the sentinel is written LAST (a partial bootstrap is
never marked complete); a stale requirements hash re-provisions rather than skips; the
model cache is pinned durable via ``ensure_fastembed_cache_path`` (never ``$TMPDIR``);
and none of this is ever triggered from a hook — the MCP tool is agent-invoked, on an
explicit user ask, the same consent posture as typing the skill. The MCP server's own
process sets ``HF_HUB_OFFLINE``/``TRANSFORMERS_OFFLINE`` at serve(); the worker env
strips them — this download IS the sanctioned online step those pins exist to protect.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Dict, List, Optional

_SENTINEL = ".bootstrap-sentinel"
_LOCK = ".bootstrap-lock"
_LOG = "bootstrap.log"
_LOG_TAIL = 20
# The supported system-python window (matches the bootstrap SKILL + pinned deps).
_PY_WINDOW = ("3.9", "3.10", "3.11", "3.12", "3.13")
_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
_MULTILINGUAL_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_CROSS_ENCODER = "Xenova/ms-marco-MiniLM-L-6-v2"


def _data_dir() -> str:
    return os.environ.get("CLAUDE_PLUGIN_DATA") or ""


def _plugin_root() -> str:
    return os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )


def _venv_python(data_dir: str) -> str:
    return os.path.join(data_dir, "venv", "bin", "python")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _lock_path(data_dir: str) -> str:
    return os.path.join(data_dir, _LOCK)


def _running_pid(data_dir: str) -> Optional[int]:
    """The live worker pid from the lock file, or None (absent / stale / unreadable —
    a dead pid's lock is stale, not blocking; start() may overwrite it)."""
    try:
        with open(_lock_path(data_dir), encoding="utf-8") as fh:
            pid = int((json.load(fh) or {}).get("pid"))
        return pid if _pid_alive(pid) else None
    except Exception:
        return None


def _log_tail(data_dir: str, lines: int = _LOG_TAIL) -> str:
    try:
        with open(os.path.join(data_dir, _LOG), encoding="utf-8", errors="replace") as fh:
            return "\n".join(fh.read().splitlines()[-lines:])
    except Exception:
        return ""


def _sibling_installs(data_dir: str) -> List[str]:
    """Other SURFACES' already-bootstrapped data dirs for this same plugin.

    ``<plugin>-inline`` (desktop) and ``<plugin>-<marketplace>`` (terminal) are siblings
    under the same parent; a sentinel in one explains to a user why the other surface is
    downloading "again" — each surface provisions its own dir. Names only, no coupling."""
    out: List[str] = []
    try:
        parent = os.path.dirname(os.path.abspath(data_dir))
        with open(
            os.path.join(_plugin_root(), ".claude-plugin", "plugin.json"), encoding="utf-8"
        ) as fh:
            plugin_name = str(json.load(fh).get("name") or "")
        if not plugin_name:
            return out
        me = os.path.basename(os.path.abspath(data_dir))
        for entry in sorted(os.listdir(parent)):
            if entry == me or not entry.startswith(plugin_name + "-"):
                continue
            if os.path.isfile(os.path.join(parent, entry, _SENTINEL)):
                out.append(os.path.join(parent, entry))
    except Exception:
        return out
    return out


def status() -> Dict[str, object]:
    """The poll: sentinel state, live worker, log tail, sibling-surface installs.
    Read-only; never raises."""
    data = _data_dir()
    if not data:
        return {"state": "no_data_dir"}
    from .session_start import bootstrap_state

    pid = _running_pid(data)
    return {
        "state": bootstrap_state(None, None),
        "running": pid is not None,
        "pid": pid,
        "log_tail": _log_tail(data),
        "siblings": _sibling_installs(data),
    }


def _spawn(cmd: List[str], env: Dict[str, str], log_path: str, cwd: str) -> int:
    """Detach the worker (own session, log-file stdout) and return its pid.
    Module-level so tests can monkeypatch the spawn without touching the plumbing."""
    with open(log_path, "a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            cwd=cwd,
            start_new_session=True,
        )
    return proc.pid


def start(multilingual: bool = False) -> Dict[str, object]:
    """Kick off the detached bootstrap worker. Returns immediately; poll with status()."""
    data = _data_dir()
    if not data:
        return {"status": "no_data_dir"}
    from .session_start import bootstrap_state

    if _running_pid(data) is not None:
        return {"status": "already_running", "pid": _running_pid(data)}
    if bootstrap_state(None, None) == "current" and not multilingual:
        # A version-only update ("re-bootstrap: no") leaves the venv genuinely current but the
        # sentinel's plugin_version stale. Refresh just that label (offline, no rebuild) so
        # doctor's DOC-7 delta doesn't nag to run a bootstrap that only fast-paths out here
        # again — the no-op remedy that fix closes.
        restamped = _restamp_plugin_version(data, _plugin_root())
        return {"status": "already_bootstrapped", "restamped": restamped}
    try:
        os.makedirs(data, exist_ok=True)
        root = _plugin_root()
        # System python3, never the venv python: a re-bootstrap must not rebuild the venv
        # out from under its own interpreter. The worker itself is stdlib-only.
        import shutil

        worker_py = shutil.which("python3") or sys.executable
        cmd = [worker_py, "-m", "memory.bootstrap", "--worker"]
        if multilingual:
            cmd.append("--multilingual")
        env = dict(os.environ)
        env["CLAUDE_PLUGIN_DATA"] = data
        env["CLAUDE_PLUGIN_ROOT"] = root
        env["PYTHONPATH"] = root + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
        )
        # serve() pins these in-process so recall never downloads; the worker's whole job
        # is the one sanctioned download — strip them from ITS env only.
        env.pop("HF_HUB_OFFLINE", None)
        env.pop("TRANSFORMERS_OFFLINE", None)
        pid = _spawn(cmd, env, os.path.join(data, _LOG), root)
        from datetime import datetime, timezone

        with open(_lock_path(data), "w", encoding="utf-8") as fh:
            json.dump({"pid": pid, "started_at": datetime.now(timezone.utc).isoformat()}, fh)
        return {"status": "started", "pid": pid}
    except Exception as exc:
        return {"status": "spawn_failed", "error": str(exc)}


# --------------------------------------------------------------------------- #
# The worker — runs detached under system python3; stdout/stderr -> bootstrap.log.
# Each step is a small function so tests exercise the sequencing hermetically.
# --------------------------------------------------------------------------- #
def _say(msg: str) -> None:
    print(f"[bootstrap] {msg}", flush=True)


def _system_python_ok() -> Optional[bool]:
    """True/False for the 3.9–3.13 window; None when undetectable (best effort — the
    SKILL's rule: don't block on a detection quirk)."""
    try:
        out = subprocess.run(
            ["python3", "-c", 'import sys; print("%d.%d" % sys.version_info[:2])'],
            capture_output=True, text=True, timeout=30,
        )
        ver = out.stdout.strip()
        return ver in _PY_WINDOW if ver else None
    except Exception:
        return None


def _provision_venv(data: str, root: str) -> None:
    import shutil

    venv_dir = os.path.join(data, "venv")
    have_uv = shutil.which("uv") is not None
    ok = _system_python_ok()
    if have_uv:
        cmd = ["uv", "venv", venv_dir]
        if ok is False:
            # Out-of-window system python: let uv fetch a pinned supported interpreter
            # rather than limping into an opaque numpy source build.
            cmd = ["uv", "venv", "--python", "3.12", venv_dir]
        _say(f"creating venv: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
        return
    if ok is False:
        raise RuntimeError(
            "system python3 is outside hippo's supported window (3.9-3.13) and uv is not "
            "on PATH to fetch a supported interpreter. Install uv "
            "(https://docs.astral.sh/uv/) or a 3.9-3.13 python3, then re-run bootstrap."
        )
    _say("creating venv: python3 -m venv (uv not found — slower, works)")
    subprocess.run(["python3", "-m", "venv", venv_dir], check=True)


def _install_deps(data: str, root: str) -> None:
    import shutil

    req = os.path.join(root, "requirements.txt")
    py = _venv_python(data)
    if shutil.which("uv"):
        _say("installing deps (uv pip)")
        subprocess.run(["uv", "pip", "install", "-r", req, "--python", py], check=True)
    else:
        _say("installing deps (venv pip)")
        subprocess.run([py, "-m", "pip", "install", "-q", "-r", req], check=True)


def _warm_env(data: str, root: str) -> Dict[str, str]:
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = data
    env["CLAUDE_PLUGIN_ROOT"] = root
    env["PYTHONPATH"] = root
    env.pop("HF_HUB_OFFLINE", None)
    env.pop("TRANSFORMERS_OFFLINE", None)
    return env


def _warm_models(data: str, root: str, multilingual: bool) -> None:
    """The actual online step: pin the durable cache, download the embedding model.
    ``--multilingual`` persists the model preset FIRST (so a partial warm still leaves
    the choice recorded), then warms that model. Cross-encoder warm is best-effort —
    the rerank degrades to un-reranked order on a cache miss, so its failure never
    fails bootstrap (the SKILL's rule)."""
    model = _MULTILINGUAL_MODEL if multilingual else _DEFAULT_MODEL
    if multilingual:
        _say(f"writing model preset: {model}")
        with open(os.path.join(data, "model.json"), "w", encoding="utf-8") as fh:
            json.dump({"embed_model": model}, fh)
    py = _venv_python(data)
    env = _warm_env(data, root)
    _say(f"warming embedding model {model} (the one online step; ~minutes on first run)")
    subprocess.run(
        [py, "-c",
         "from memory.build_index import ensure_fastembed_cache_path; "
         "ensure_fastembed_cache_path(); "
         "from fastembed import TextEmbedding; "
         f"TextEmbedding({model!r})"],
        check=True, env=env,
    )
    _say("warming cross-encoder (best-effort)")
    try:
        subprocess.run(
            [py, "-c",
             "from memory.build_index import ensure_fastembed_cache_path; "
             "ensure_fastembed_cache_path(); "
             "from fastembed.rerank.cross_encoder import TextCrossEncoder; "
             f"TextCrossEncoder({_CROSS_ENCODER!r})"],
            check=True, env=env,
        )
    except Exception:
        _say("cross-encoder warm failed — continuing (rerank degrades gracefully)")


def _write_sentinel(data: str, root: str) -> None:
    """LAST step by hard rule: a sentinel means the venv AND the model warm both
    succeeded — a partial bootstrap marked complete would never get retried."""
    import hashlib
    from datetime import datetime, timezone

    with open(os.path.join(root, "requirements.txt"), "rb") as fh:
        req_hash = hashlib.sha256(fh.read()).hexdigest()
    version = "unknown"
    try:
        with open(os.path.join(root, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            version = str(json.load(fh).get("version") or "unknown")
    except Exception:
        pass
    with open(os.path.join(data, _SENTINEL), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "requirements_hash": req_hash,
                "bootstrapped_at": datetime.now(timezone.utc).isoformat(),
                "plugin_version": version,
            },
            fh,
        )
    _say("sentinel written — bootstrap complete")


def _restamp_plugin_version(data: str, root: str) -> bool:
    """DOC-7 companion: on an ALREADY-current bootstrap, refresh ONLY the sentinel's
    ``plugin_version`` label when a version-only ("re-bootstrap: no") update left it stale.

    ``start()``'s fast path returns before the worker — and thus before ``_write_sentinel``
    — ever runs, so a release that changes code but keeps ``requirements.txt`` byte-identical
    would otherwise leave the sentinel forever stamped with the OLD ``plugin_version``. Then
    ``doctor.check_plugin_version`` (DOC-7) nags to "run /hippo:bootstrap" — a remedy that
    hits this very fast path and returns again without touching the label: a no-op by
    construction, so the nag could never be cleared by following its own advice.

    This closes that loop with a cheap, OFFLINE metadata rewrite: no venv rebuild, no
    download, ``requirements_hash`` and ``bootstrapped_at`` preserved (the venv genuinely
    wasn't re-provisioned — only the label was wrong). It runs ONLY on the fast path, which
    implies ``bootstrap_state == "current"`` (the sentinel exists and is complete), so it can
    never mark a partial bootstrap complete. Never raises: on any error the sentinel is left
    untouched (fail toward not-breaking). Returns True iff it rewrote the label.
    """
    try:
        sentinel_path = os.path.join(data, _SENTINEL)
        if not os.path.exists(sentinel_path):
            return False
        try:
            with open(os.path.join(root, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
                current = str(json.load(fh).get("version") or "")
        except Exception:
            return False
        if not current:
            return False
        with open(sentinel_path, encoding="utf-8") as fh:
            sentinel = json.load(fh) or {}
        if sentinel.get("plugin_version") == current:
            return False
        sentinel["plugin_version"] = current
        with open(sentinel_path, "w", encoding="utf-8") as fh:
            json.dump(sentinel, fh)
        return True
    except Exception:
        return False


def _run_worker(multilingual: bool = False) -> int:
    data, root = _data_dir(), _plugin_root()
    if not data:
        _say("CLAUDE_PLUGIN_DATA is unset — nowhere to provision. Aborting.")
        return 1
    try:
        os.makedirs(data, exist_ok=True)
        _provision_venv(data, root)
        _install_deps(data, root)
        _warm_models(data, root, multilingual)
        _write_sentinel(data, root)
        if multilingual:
            _say(
                "multilingual preset active — each project's index must re-embed under "
                "the new model: re-run init (or build-index) per project."
            )
        return 0
    except Exception as exc:
        _say(f"FAILED: {exc!r} — no sentinel written; fix the cause and start again.")
        return 1
    finally:
        try:
            os.remove(_lock_path(data))
        except Exception:
            pass


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="memory.bootstrap")
    parser.add_argument("--worker", action="store_true", help="run the provisioning steps")
    parser.add_argument("--multilingual", action="store_true")
    args = parser.parse_args(argv)
    if args.worker:
        return _run_worker(multilingual=args.multilingual)
    print(json.dumps(status(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
